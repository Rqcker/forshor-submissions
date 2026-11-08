"""Run the Lean `forshor_emit` binary, or the mirror — and prove they agree.

    bundle = emit_bundle(k)              # REAL-LEAN if the binary exists, else REFERENCE-MIRROR
    emit_bundle(k, require_real=True)    # raises if only the mirror is available
    crosscheck(k)                        # parsed-JSON equality real vs mirror, per section
    phases_crosscheck(k, m, phi)         # forshor_emit phases == phase_emit == mirror, byte-for-byte

Exit-code contract (both binaries): 0 complete, 2 REFUSED (stdout empty), 3 INTERNAL CHECK FAILED.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Tuple

from . import paths, schemas
from .mirrors import mirror_bundle
from .mirrors.bundle import SECTIONS, strip_private
from .mirrors.coeff_poly import phases as mirror_phases
from .provenance import Provenance


class EmitError(RuntimeError):
    def __init__(self, msg: str, returncode: int = -1, stderr: str = ""):
        super().__init__(msg)
        self.returncode, self.stderr = returncode, stderr


@dataclass
class Bundle:
    k: int
    doc: Dict[str, Any]
    provenance: Provenance
    binary: Optional[Path] = None
    notes: List[str] = field(default_factory=list)

    def __getitem__(self, section: str) -> Dict[str, Any]:
        return self.doc[section]

    @property
    def certifies(self) -> bool:
        return self.provenance.certifies

    def save(self, directory: Path = paths.LEAN_ARTIFACTS, *, allow_overwrite: bool = False) -> Path:
        """Write to ``k<k>.forshor.json``, or to a sidecar when that would destroy something.

        These bundles are expensive and curated: a full-suite run once overwrote the whole set,
        and recovering them meant digging the values out of `web/pipeline-data.js` because git
        did not have them either. So a save never silently replaces a bundle that a *different*
        producer wrote. Two guards, in order:

          * the mirror never overwrites REAL-LEAN (kept from v1);
          * a REAL-LEAN bundle from a different emitter build does not overwrite one already
            on disk -- the v1 and v2 emitters disagree on section names, so this is exactly the
            cutover case, and losing the v1 numbers would erase the only before-side of the
            comparison.

        Neither raises. The bundle lands beside the incumbent under a name that says where it
        came from, and the displacement is recorded in `notes` so the caller can report it.
        `allow_overwrite=True` (what `--refresh` passes) takes the direct path.
        """
        directory.mkdir(parents=True, exist_ok=True)
        out = directory / f"k{self.k}.forshor.json"
        fp = emitter_fingerprint(self.binary)
        if out.is_file() and not allow_overwrite:
            try:
                prev = json.loads(out.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
            prev_prov, prev_fp = prev.get("_provenance"), prev.get("_emitter")
            if not self.certifies and prev_prov == Provenance.REAL_LEAN.value:
                out = directory / f"k{self.k}.forshor.mirror.json"
                self.notes.append(f"kept the REAL-LEAN bundle; wrote the mirror to {out.name}")
            elif prev_prov == Provenance.REAL_LEAN.value and fp and prev_fp != fp:
                # `prev_fp is None` counts as different, and deliberately so: every bundle on
                # disk before 2026-09-10 predates fingerprinting, and those are precisely the
                # v1-emitter bundles this guard exists to protect. Reading "no fingerprint" as
                # "no objection" would make the guard inert on exactly its intended targets --
                # which is what it did when first written.
                tag = fp.split(":", 1)[-1][:12]
                out = directory / f"k{self.k}.forshor.{tag}.json"
                self.notes.append(
                    f"k{self.k}.forshor.json was emitted by a different build "
                    f"({prev_fp or 'un-fingerprinted, i.e. pre-2026-09-10'}); kept it and "
                    f"wrote this one to {out.name}. Pass allow_overwrite=True (or --refresh) "
                    f"to replace it.")
        payload = dict(self.doc)
        payload["_provenance"] = self.provenance.value
        payload["_notes"] = list(self.notes)
        if fp is not None:
            payload["_emitter"] = fp
        out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        return out


#: Default table sizes per arity. The figures need only the affine tails (found by m ≈ 5) and the
#: recursion ladders (E6 covers w ≤ w_max); larger k is much slower in the Lean solver (k = 7 exceeded
#: 600 s at 24/96 on the target machine, 2026-09-02), so the defaults shrink with k. Override with m_max/w_max.
def default_opts(k: int) -> tuple:
    return (24, 96) if k <= 5 else (16, 64)


DEFAULT_TIMEOUT = 3600.0

#: Which Toom-Cook operation schedule to emit. THIS IS A COST-BEARING CHOICE, and the v2 drop
#: changed its default, so it is named explicitly here rather than left to the emitter.
#:
#:   "standard"  Shor.standardLoweringSetup. ProgConsumesPtsSafe and returns-to-start are
#:               THEOREMS (genOpsWithProduct_ProgConsumesPtsSafe, genOpsWithProduct_-
#:               returns_to_original). Integer evaluation points 0, -1, 1, -2, 2, ...
#:   "generate"  Table_Generation.generate. The emitter's own provenance says: "checked at run
#:               time only, no correctness theorem". Uses fractional evaluation points.
#:
#: The v1 emitter's default schedule is BIT-IDENTICAL to v2's `generate` at every arity we hold
#: a bundle for (k = 2..7, ops and points both). So every cost number this pipeline published
#: before the cutover was computed on the unproved table. The proved one is dearer, and sharply
#: so as k grows -- adder-class ops, v1 default -> "standard":
#:
#:     k      2      3      4      5      6      7
#:     ops    2->4   12->16 22->56 38->92 58->216 82->372
#:     x      2.00   1.33   2.55   2.42   3.72    4.54
#:
#: We default to the proved table and keep the cheap one as an explicit opt-in, per the
#: standing rule that Lean's definitions are the default and alternatives are toggles. A number
#: quoted from "generate" is a number no theorem covers, and must say so.
DEFAULT_TABLE = "standard"
TABLES = ("standard", "generate")


_TABLE_FLAG_SUPPORT: Dict[str, bool] = {}


def _binary_has_table_flag(binary: Path) -> bool:
    """Whether this `forshor_emit` accepts `--table` (asked of the binary itself, once). The 2026-10-14
    ForShor-main emitter has only the proved `standard` table and no such flag."""
    key = str(binary)
    if key not in _TABLE_FLAG_SUPPORT:
        try:
            r = subprocess.run([key], capture_output=True, text=True, timeout=60)
            _TABLE_FLAG_SUPPORT[key] = "--table" in (r.stdout or "") + (r.stderr or "")
        except Exception:
            _TABLE_FLAG_SUPPORT[key] = True               # unknown: keep the old behaviour
    return _TABLE_FLAG_SUPPORT[key]


def _table_flags(table: Optional[str], binary: Optional[Path] = None) -> List[str]:
    tb = table or DEFAULT_TABLE
    if tb not in TABLES:
        raise EmitError(f"unknown Toom-Cook table {tb!r}; expected one of {TABLES}")
    if binary is not None and not _binary_has_table_flag(binary):
        if tb != "standard":
            raise EmitError(f"this forshor_emit has no `--table`: only the proved `standard` table, not {tb!r}")
        return []
    return ["--table", tb]


def _launch(binary: Path, args: List[str]) -> Tuple[List[str], Optional[Path]]:
    """The command line and cwd for one emitter call. The 2026-10-14 ForShor-main emitter extracts its template at
    run time (`importModules`), so it must run inside the tree's Lake environment (pinned toolchain, LEAN_PATH):
    bare, it fails with "unknown module prefix 'FastMultiplication'". Trees without a lakefile run bare."""
    tree = binary.resolve().parent.parent.parent.parent
    lake = shutil.which("lake") or str(Path.home() / ".elan" / "bin" / "lake")
    if (tree / "lakefile.lean").is_file() and Path(lake).is_file():
        return [lake, "env", str(binary), *args], tree
    return [str(binary), *args], None


def _run(binary: Path, args: List[str], timeout: float = DEFAULT_TIMEOUT) -> str:
    time.time()
    cmd, cwd = _launch(binary, args)
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise EmitError(f"{binary.name} {' '.join(args)}: TIMEOUT after {timeout:.0f} s", returncode=-9)
    if proc.returncode != 0:
        tag = {2: "REFUSED", 3: "INTERNAL CHECK FAILED"}.get(proc.returncode, f"rc={proc.returncode}")
        raise EmitError(f"{binary.name} {' '.join(args)}: {tag}: {proc.stderr.strip()}",
                        proc.returncode, proc.stderr)
    if proc.returncode == 0 and not proc.stdout.strip():
        raise EmitError(f"{binary.name} {' '.join(args)}: empty stdout with exit 0")
    return proc.stdout


def real_bundle(k: int, m_max: Optional[int] = None, w_max: Optional[int] = None,
                timeout: float = DEFAULT_TIMEOUT, table: Optional[str] = None) -> Optional[Bundle]:
    """The Lean bundle, or None if no binary is discoverable. Raises EmitError on refusal/timeout."""
    b = paths.forshor_emit_binary()
    if b is None:
        return None
    dm, dw = default_opts(k)
    m_max, w_max = m_max or dm, w_max or dw
    tb = table or DEFAULT_TABLE
    t0 = time.time()
    args = ["bundle", str(k), "--m-max", str(m_max), "--w-max", str(w_max), *_table_flags(tb, b)]
    no_template = False
    try:
        out = _run(b, args, timeout=timeout)
    except EmitError as exc:
        # The 2026-10-14 ForShor-main emitter checks the extracted template at widths up to its own
        # minimum (233 at k = 5, 521 at k = 6) and refuses a smaller `--w-max`. The bundle's other
        # sections do not need it, so retry without the template and say so.
        if "raise --w-max" not in str(exc):
            raise
        no_template = True
        out = _run(b, args + ["--no-template"], timeout=timeout)
    doc = json.loads(out)
    schemas.expect(doc, schemas.FORSHOR_EMIT, where=f"forshor_emit bundle {k}")
    from .bundle_compat import complete
    derived = complete(doc)
    if derived:
        doc["_derived"] = derived
    note = f"emitted by the Lean binary in {time.time() - t0:.1f} s, table {tb}"
    if derived:
        note += f" (derived from the emitted ops, not emitted: {', '.join(derived)})"
    if no_template:
        note += f" (--no-template: the template's own checks need --w-max above {w_max})"
    if tb == "generate":
        note += " (NO correctness theorem: Table_Generation.generate is run-time checked only)"
    return Bundle(k, doc, Provenance.REAL_LEAN, b, notes=[note])


def cached_real_bundle(k: int, m_max: Optional[int] = None, w_max: Optional[int] = None,
                       table: Optional[str] = None) -> Optional[Bundle]:
    """A previously saved REAL-LEAN bundle with the same table sizes AND the same Toom-Cook
    table (artifacts/lean/k<k>.forshor.json). The table is part of the cache key because the
    two disagree by up to 4.5x in adder count -- reusing one for the other silently reports the
    wrong schedule's cost."""
    # Look at the fingerprinted sidecar FIRST, then the plain name. When the save guard
    # declines to overwrite an incumbent, the bundle it just emitted lands at
    # `k<k>.forshor.<fp>.json` -- and if the cache only ever looked at the plain name it would
    # never find it, so every single call would re-emit while believing it had a cache. That is
    # not hypothetical: it turned one test run into a 28-minute bundle-emission loop.
    fp = emitter_fingerprint()
    candidates = []
    if fp:
        candidates.append(paths.LEAN_ARTIFACTS / f"k{k}.forshor.{fp.split(':', 1)[-1][:12]}.json")
    candidates.append(paths.LEAN_ARTIFACTS / f"k{k}.forshor.json")
    dm, dw = default_opts(k)
    want = {"m_max": m_max or dm, "w_max": w_max or dw}

    def usable(doc: Dict[str, Any]) -> bool:
        # Compare only the keys WE asked for. The v2 emitter reports `check_cramer` in `opts`
        # as well, so the v1 whole-dict equality rejected every bundle it had just written and
        # re-emitted on every call -- a cache that never hits, which reads as "the emitter is
        # slow" rather than as a bug.
        opts = doc.get("opts") or {}
        if (doc.get("_provenance") != Provenance.REAL_LEAN.value
                or doc.get("schema") != schemas.FORSHOR_EMIT
                or any(opts.get(key) != val for key, val in want.items())):
            return False
        # A v1 bundle carries no `table` key at all; it was emitted on what v2 calls `generate`.
        return (doc.get("table") or "generate") == (table or DEFAULT_TABLE)

    # Validation decides which candidate wins, not mere existence. Picking the first file that
    # EXISTS and then rejecting it loses the perfectly good bundle sitting under the other
    # name -- the sidecar and the plain name routinely hold different `opts`, so that version
    # of this function never hit the cache at all.
    for p in candidates:
        if not p.is_file():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if usable(doc):
            clean = {kk: v for kk, v in doc.items() if not str(kk).startswith("_")}
            from .bundle_compat import complete
            complete(clean)                   # a bundle cached from a ForShor-main emitter lacks the derived fields
            return Bundle(k, clean, Provenance.REAL_LEAN, paths.forshor_emit_binary(),
                          notes=[f"REAL-LEAN bundle reused from {p.name} (use --refresh to re-emit)"])
    return None


def emit_bundle(k: int, m_max: Optional[int] = None, w_max: Optional[int] = None, *, require_real: bool = False,
                crosscheck_if_real: bool = True, refresh: bool = False, timeout: float = DEFAULT_TIMEOUT,
                table: Optional[str] = None) -> Bundle:
    """REAL-LEAN when a saved real bundle exists (unless refresh) or the binary runs and agrees with the
    mirror; otherwise the mirror, with the reason recorded in `notes` (timeout / refusal / no binary).
    Per-k: a slow arity falls back alone; the others stay REAL-LEAN."""
    dm, dw = default_opts(k)
    m_max, w_max = m_max or dm, w_max or dw
    if not refresh:
        cached = cached_real_bundle(k, m_max, w_max, table)
        if cached is not None:
            return cached
    reason = None
    try:
        real = real_bundle(k, m_max, w_max, timeout=timeout, table=table)
    except EmitError as exc:
        real, reason = None, str(exc)
    if real is None:
        reason = reason or "no forshor_emit binary (set ONECLICK_FORSHOR_EMIT or lake build forshor_emit)"
        if require_real:
            raise EmitError(f"k={k}: {reason}")
        return Bundle(k, strip_private(mirror_bundle(k, m_max, w_max)), Provenance.REFERENCE_MIRROR,
                      notes=[f"REFERENCE-MIRROR (not Lean-certified): {reason}"])
    if crosscheck_if_real:
        # ADVISORY, not a gate. Under v1 the mirror was a faithful reimplementation and any
        # disagreement meant one of the two was wrong, so raising was right. Under v2 it is
        # neither: the mirror reimplements a bundle shape the emitter no longer produces, and
        # the sections it still shares are the ones least likely to move. Failing the whole
        # emission on that would block every REAL-LEAN bundle behind a stale Python file --
        # and the mirror is the side we trust less, so it is the side that yields.
        try:
            diff = crosscheck(k, m_max, w_max, real=real)
        except Exception as exc:                       # a broken mirror must not block Lean
            real.notes.append(f"crosscheck skipped: the mirror raised {type(exc).__name__}: {exc}")
        else:
            if diff["mismatches"]:
                real.notes.append(
                    f"ADVISORY: real != mirror in {diff['mismatches']} -- the Lean bundle is "
                    f"authoritative and is what was saved; the mirror is stale for these "
                    f"sections. First difference: {diff['sections'][diff['mismatches'][0]]['diff'][:1]}")
            elif diff["compared"]:
                real.notes.append(f"real == mirror (parsed JSON) on {diff['compared']}")
            if diff["only_real"] or diff["only_mirror"]:
                real.notes.append(
                    f"not comparable: only in Lean {diff['only_real']}, "
                    f"only in mirror {diff['only_mirror']}")
    real.save(allow_overwrite=refresh)
    return real


#: Differences between the real v2 bundle and the frozen v1 mirror that are UNDERSTOOD and
#: ACCEPTED, each with the reason it is not a defect. Anything outside this list is binding.
#: Keyed by the diff path prefix; the value is why it is allowed.
#:
#: Keeping this as an explicit list, rather than widening the comparison until nothing fails, is
#: the point: it is a short, readable record of every place the two emitters genuinely disagree,
#: and a new disagreement cannot hide behind it.
KNOWN_V1_V2_DIFFERENCES = {
    "coeff_poly/degenerate_m":
        "v2 is CORRECT at every k; the v1 baseline was not. Lean's `degenerateMs` "
        "(Emit/Symbolic/CoeffPoly.lean:123) flags every m whose `2^m` equals an INTEGER "
        "interpolation point, scanning `List.range (mMaxCheck + 1)` -- which includes m = 0. "
        "Recomputed directly from each side's OWN points for k = 2..7, the v2 emitter matches its "
        "own points exactly in all six cases, while the v1 baseline omits m = 0 in all six "
        "(at k = 2 the standard points are {0, -1, 1} and `2^0 = 1` is among them, so m = 0 is "
        "degenerate; v1 reported `[]`). That is a uniform off-by-one in v1, fixed in v2. "
        "The apparent extra gap at k = 7 -- v2 `[0,1,2]` against v1 `[1,2,3]` -- is NOT a missing "
        "entry: the two tables have different point sets. `standard` uses consecutive points "
        "{0, +-1, ..., +-6}, which contain no 8, so 2^3 is correctly not flagged; `generate` uses "
        "{0, 1, -1, 2, -2, 4, -4, 8}, which does. Both lists are right for their own table. "
        "The field is marked 'Advisory only' in Lean and m = 0 is a zero-width limb, so nothing "
        "downstream moves. Verified 2026-09-22; no ask is needed.",
}


def _is_known_difference(diff: str) -> Optional[str]:
    """The reason `diff` is accepted, or None if it is binding."""
    for path, why in KNOWN_V1_V2_DIFFERENCES.items():
        if path in diff:
            return why
    return None


def _diff_paths(a: Any, b: Any, prefix: str = "") -> List[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(f"{prefix}/{key} (missing on {'mirror' if key not in b else 'real'})")
            else:
                out += _diff_paths(a[key], b[key], f"{prefix}/{key}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{prefix} (len {len(a)} vs {len(b)})"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _diff_paths(x, y, f"{prefix}[{i}]")
        return out[:20]
    return [] if a == b else [f"{prefix}: {a!r} != {b!r}"]


def crosscheck(k: int, m_max: Optional[int] = None, w_max: Optional[int] = None,
               real: Optional[Bundle] = None, *, attribute: bool = False,
               baseline_table: Optional[str] = None) -> Dict[str, Any]:
    """The real bundle against the mirror, section by section, with each difference attributed.

    `attribute=True` additionally emits the bundle on `baseline_table` (default `generate`,
    the table the frozen mirror was made with) to separate differences the table explains
    from differences it does not. That costs one more emission, so it is opt-in. Callers
    should assert on `unexplained`, not on `mismatches` -- see the note in the body.
    """
    dm, dw = default_opts(k)
    m_max, w_max = m_max or dm, w_max or dw
    real = real or cached_real_bundle(k, m_max, w_max) or real_bundle(k, m_max, w_max)
    if real is None:
        return {"available": False, "k": k, "mismatches": [], "sections": {}}
    mirror = strip_private(mirror_bundle(k, m_max, w_max))
    result: Dict[str, Any] = {"available": True, "k": k, "sections": {}, "mismatches": [],
                              "only_real": [], "only_mirror": []}
    # The v2 bundle and the Python mirror no longer carry the same section set: v2 dropped
    # `modmul_plan` and added `shor_plan` and `template`. Comparing the union would report
    # every such section as a mismatch and say nothing. Only the sections BOTH sides have are
    # compared; the rest are reported by name, as a fact about coverage rather than a failure.
    real_sections = {s for s in real.doc if not str(s).startswith("_")}
    mirror_sections = set(mirror)
    for s in sorted(SECTIONS):
        if s not in real_sections:
            result["only_mirror"].append(s)
            continue
        if s not in mirror_sections:
            result["only_real"].append(s)
            continue
        d = _diff_paths(real.doc.get(s), mirror.get(s), s)
        result["sections"][s] = {"equal": not d, "diff": d}
        if d:
            result["mismatches"].append(s)
    result["only_real"] += sorted(real_sections - mirror_sections - set(SECTIONS))
    result["compared"] = sorted(result["sections"])

    # ---- classify what disagreed, instead of only counting it ----------------------------------
    #
    # A bare "mismatches" list made this check useless after the 2026-09-18 cutover, and it would
    # have stayed useless in either direction: the mirror for k <= 7 is a FROZEN SNAPSHOT of the
    # 2026-08-27 emitter (`mirrors/schedule.raw_table` reads
    # `schedules/frozen_reference/k{k}.lean-emitted.toomsched.json`), so this is a regression check
    # against v1 output, and v1 differs from v2 in ways we chose. Asserting equality asserts the
    # cutover did not happen; deleting the assert throws away the one check that would catch real
    # drift. So each difference is attributed instead:
    #
    #   shape     -- a key exists on one side only. The v1 and v2 schemas differ by design
    #                (v2 added `check1_inverse`/`check2`, dropped `cites`/`k`/`A_k`, renamed
    #                `affine_sym` -> `affine_tail`). Not a numeric disagreement at all.
    #   table     -- a VALUE differs, and stops differing when the real bundle is emitted on
    #                `generate`, the table the frozen baseline was made with. Attributable to the
    #                deliberate move to the proved `standard` table (section 8.19.1), and MEASURED
    #                here rather than assumed: `attribute=True` emits the comparison bundle.
    #   unexplained -- a value differs on the SAME table. This is the binding set; it should be
    #                empty, and it is what a caller should assert on.
    #
    # Checked at k=2: all three `schedule` value differences (`census/addScaled: 4 != 2`,
    # `ops` length 7 vs 5, `points[1]/kind`) vanish on `generate`, so the schedule sections agree
    # exactly between the 2026-08-27 and 2026-09-18 emitters given the same table.
    shape, value = [], []
    for s, v in result["sections"].items():
        for d in (v["diff"] or []):
            (shape if "missing on" in d else value).append(f"{s}: {d}")
    result["shape_differences"] = shape
    result["value_differences"] = value
    result["table_attributable"] = []
    result["unexplained"] = list(value)
    result["attributed"] = False
    if attribute and value:
        baseline_tb = baseline_table or "generate"
        try:
            same_tb = real_bundle(k, m_max, w_max, table=baseline_tb)
        except Exception:
            same_tb = None
        if same_tb is not None:
            still = set()
            for s in result["sections"]:
                if s in same_tb.doc and s in mirror:
                    for d in _diff_paths(same_tb.doc.get(s), mirror.get(s), s):
                        if "missing on" not in d:
                            still.add(f"{s}: {d}")
            result["attributed"] = True
            result["baseline_table"] = baseline_tb
            result["table_attributable"] = [d for d in value if d not in still]
            result["unexplained"] = [d for d in value if d in still]
    # Of what the table does not explain, separate the differences we have run down and accepted
    # (each with its reason, in KNOWN_V1_V2_DIFFERENCES) from the ones nobody has explained. The
    # second list is the one that means something.
    result["known_differences"] = {d: _is_known_difference(d) for d in result["unexplained"]
                                   if _is_known_difference(d)}
    result["binding"] = [d for d in result["unexplained"] if not _is_known_difference(d)]
    return result


def phases_real(k: int, m: int, phi: Fraction, which: str = "forshor_emit") -> Optional[List[str]]:
    """Flat phase list from a binary (`forshor_emit phases` or `phase_emit`), or None if absent."""
    b = paths.forshor_emit_binary() if which == "forshor_emit" else paths.phase_emit_binary()
    if b is None:
        return None
    args = (["phases"] if which == "forshor_emit" else []) + [str(k), str(m), f"{phi.numerator}/{phi.denominator}"]
    return _run(b, args).strip().splitlines()


def phases_from_coeff_poly(k: int, m: int, phi: Fraction = Fraction(1, 8),
                           bundle: Optional["Bundle"] = None) -> Optional[List[str]]:
    """`c_l(2^m) * phi` evaluated from the BUNDLE's own `coeff_poly` rows.

    This is the reference the `phases` command is checked against. It is a real cross-check and
    not a restatement: `Bundle.lean:buildPhases` and the coefficient table are two different
    paths through Lean, so agreeing means the phase list and the interpolation coefficients
    describe the same polynomial. It is also table-correct by construction -- it reads whichever
    table the bundle was emitted on.
    """
    from . import bundle_compat
    b = bundle if bundle is not None else cached_real_bundle(k) or real_bundle(k)
    if b is None:
        return None
    radix = 2 ** m
    out = []
    for poly in bundle_compat.coeff_polys(b.doc):
        val = sum(c * radix ** i for i, c in enumerate(poly)) * phi
        out.append(f"{val.numerator}/{val.denominator}")
    return out


def phases_crosscheck(k: int, m: int, phi: Fraction = Fraction(1, 8)) -> Dict[str, Any]:
    """`forshor_emit phases` against the bundle's own coefficient rows, and (advisorily) the mirror.

    BINDING leg retied 2026-09-22. It used to compare against `mirrors.coeff_poly.phases`, the
    Python mirror -- which computes the `generate` table's coefficients. The 2026-09-18 drop
    moved the default to `standard` (section 8.19.1), so the mirror now disagrees with the
    emitter by construction, on every k and m. That is a stale reference, not a defect: checked
    at k=2 m=3, the emitter gives `-63/8, 7/2, 9/2` and the mirror the old table's values.

    The binding comparison is therefore against `phases_from_coeff_poly`, which reads the
    coefficient rows out of the same bundle and so follows whichever table it was emitted on.
    The mirror is still reported, as an advisory signal of table drift rather than a gate.
    """
    ref = phases_from_coeff_poly(k, m, phi)
    out: Dict[str, Any] = {"k": k, "m": m, "phi": f"{phi.numerator}/{phi.denominator}",
                           "reference": ref, "reference_source": "bundle coeff_poly rows"}
    for which in ("forshor_emit", "phase_emit"):
        lines = phases_real(k, m, phi, which)
        out[which] = lines
        out[f"{which}_equal"] = (lines == ref) if (lines is not None and ref is not None) else None
    try:
        mirror = [f"{x.numerator}/{x.denominator}" for x in mirror_phases(k, m, phi)]
    except Exception as exc:
        mirror = None
        out["mirror_error"] = f"{type(exc).__name__}: {exc}"
    out["mirror"] = mirror
    out["mirror_equal_advisory"] = (mirror == ref) if (mirror is not None and ref is not None) else None
    if out["mirror_equal_advisory"] is False:
        out["mirror_note"] = ("ADVISORY: the Python mirror computes the `generate` table's "
                              "coefficients; this bundle is on `standard`. Expected since the "
                              "2026-09-18 cutover -- not a gate.")
    return out


# ---------------------------------------------------------------- IR cache freshness (item 5 / item 8, 2026-09-10)
def emitter_fingerprint(binary: Optional[Path] = None) -> Optional[str]:
    """A fingerprint of the `forshor_emit` build the cached IR documents must have come from: Lake's own
    `<binary>.hash` when it sits next to the executable, else sha256 of the executable's size and first
    megabyte. None without a binary (the cache is then taken on trust and reported as such)."""
    b = binary if binary is not None else paths.forshor_emit_binary()
    if b is None:
        return None
    h = b.with_name(b.name + ".hash")
    if h.is_file():
        text = h.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return f"lake:{text}"
    import hashlib
    d = hashlib.sha256()
    d.update(str(b.stat().st_size).encode())
    with b.open("rb") as f:
        d.update(f.read(1 << 20))
    return f"sha256:{d.hexdigest()[:16]}"


def _cached_doc(p: Path, schema: str, refresh: bool) -> Optional[Dict[str, Any]]:
    """The cached IR document at `p` if it is usable: right schema, REAL-LEAN provenance and — when the
    emitter binary is present — the SAME build fingerprint (`_emitter`) as that binary. A document written
    by an older build (or before fingerprints existed, 2026-09-10) is treated as stale and re-emitted:
    cached twiddle/QFT documents from 2026-09-02 were found to predate the zero-extension fix of
    2026-09-03 and disagreed with the current emitter by up to 3x in leaf count."""
    if refresh or not p.is_file():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("schema") != schema or doc.get("_provenance") != Provenance.REAL_LEAN.value:
        return None
    fp = emitter_fingerprint()
    if fp is not None and doc.get("_emitter") != fp:
        return None
    return doc


def _store_doc(p: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    doc["_provenance"] = Provenance.REAL_LEAN.value
    fp = emitter_fingerprint()
    if fp is not None:
        doc["_emitter"] = fp
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


# ================================================================ the v2 command table
# The 2026-09-18 drop replaced the emitter wholesale. What it offers:
#
#     shor <k> <a> <N> <m>            the whole program, flat            forshor.lowgate/v2
#     bundle <k> [--table ...]        the per-k tables                   forshor.emit/v1
#     <section> <k>                   one bundle section
#     phases <k> <m> <phi>            the flat phase list                (text)
#     pp  <k> <n> <phi> [--flat]      signed phase product      plan/v1, or lowgate/v2 flat
#     cpp <k> <n> <phi> [--flat]      controlled ditto
#     qft <k> <w> [--flat]            the Cooley-Tukey QFT
#
# and nothing else. Five v1 subcommands have no counterpart; they are kept below as functions
# that raise one explicit EmitError naming the capability and the ask, because ~100 call sites
# already treat EmitError as "report SKIP, not certified" and that is the behaviour we want.

#: What the v2 emitter cannot build, why, and which ask asks for it. Keeping the wording in one
#: table means the SKIP reason a notebook prints is the same sentence the collaborator got.
RETIRED: Dict[str, tuple] = {
    "twiddle": (
        "a phase product on UNEQUAL operand widths",
        "L5",
        "`ppRegisters` forces |x| = |z| = n, but the QFT twiddle has left != right at every "
        "Cooley-Tukey split. Asked for as `pp <k> <nx> <nz> <phi>` or a dedicated `twiddle`.",
    ),
    "cmodmul": (
        "a standalone controlled modular multiply (CmodMulInPlaceCore, U1..U5)",
        "L6",
        "Reachable today only inside a whole `shor` document, which is not equivalent: the "
        "layout and hence the physical qubit indices differ, and at a 2048-relevant n the "
        "whole-program document is not a practical extraction target.",
    ),
    "modexp": (
        "the modular-exponentiation DESCRIPTOR (tbits, multiplier schedule, per-step eta)",
        "L7",
        "`shor` flattens the composition, and flattening erases exactly the structure this "
        "check is about. Re-deriving `modExpIdealSteps` in Python is the mirror their own "
        "Emit/README design principle 1 rejects, so we do not.",
    ),
    "orderfinding": (
        "the order-finding DESCRIPTOR (L, embedded modexp, embedded IQFT on width L)",
        "L7",
        "Same flattening argument as modexp. Without it we cannot check that the composition "
        "we build in Qualtran is the composition ForShor proves about.",
    ),
    "multiply": (
        "the Toom-Cook Fourier multiply descriptor (alg1Step2FourierMultiplier)",
        "L21",
        "The v2 `template` section carries symbolic templates for phase_product, "
        "controlled_phase_product, qft and shor -- but not for the KMY multiply. No v1 "
        "counterpart and, until L21, no ask either.",
    ),
}


def _retired(name: str, **kwargs) -> NoReturn:
    """Raise the one EmitError that explains a capability the v2 emitter does not have."""
    what, ask, why = RETIRED[name]
    args = ", ".join(f"{key}={val!r}" for key, val in kwargs.items() if val is not None)
    where = f" ({args})" if args else ""
    raise EmitError(
        f"forshor_emit has no `{name}` subcommand: {what} is not emittable by the "
        f"ForShor-main emitter{where}. {why} This step reports SKIP -- it is not "
        f"certified, and no Python mirror stands in for it by design.")


def _emit_doc(cache: Path, schema: str, args: List[str], where: str, timeout: float,
              refresh: bool, missing: str) -> Dict[str, Any]:
    """Cache-or-emit one document: the shared body of every command below."""
    cached = _cached_doc(cache, schema, refresh)
    if cached is not None:
        return cached
    b = paths.forshor_emit_binary()
    if b is None:
        raise EmitError(f"forshor_emit binary not found: {missing} is emitted by Lean only "
                        f"-- no mirror exists. Build it with `lake build forshor_emit` in the "
                        f"build clone ({paths.FORSHOR_BUILD_TREE}), or set "
                        f"ONECLICK_FORSHOR_EMIT.")
    doc = json.loads(_run(b, args, timeout=timeout))
    schemas.expect(doc, schema, where=where)
    return _store_doc(cache, doc)


def _view(flat: bool) -> tuple:
    """(schema, cli flags, filename tag) for the flat vs annotated view."""
    if flat:
        return schemas.FORSHOR_LOWGATE_V2, ["--flat"], "flat"
    return schemas.FORSHOR_PLAN, [], "plan"


# ---------------------------------------------------------------- the whole program
def shor(k: int, a: int, N: int, m: Optional[int] = None, timeout: float = DEFAULT_TIMEOUT,
         refresh: bool = False, *, eta: Optional[Fraction] = None) -> Dict[str, Any]:
    """`forshor_emit shor k a N m` -> the whole Shor program, flat, as `forshor.lowgate/v2`.

    This is the v2 drop's headline object and has no v1 counterpart: previously the program
    existed only as separately-emitted pieces (modexp/orderfinding descriptors) that had to be
    composed on the Python side. It carries `meta.layout`, which names the physical index
    ranges of the x / data / work / scratch registers and the flag qubit, and
    `output_register` -- so the measured register is read off the document rather than assumed.

    On `m`: it is the approximation LEVEL, not a bit count, and the certified success bound at
    small `m` is zero. `forshor_qualtran.precision` converts between `m`, eta and the bound; a
    figure emitted at `m = 0` (the README's own example) is a real circuit certified by
    nothing. See :func:`forshor_qualtran.precision.certificate`.

    Pass `eta=` instead of `m` to name the precision you actually want; the level is resolved
    through `precision.m_of_eta` (Lean's `referencePrecision m = 1/(m+3)`), rounding to the
    LEAST level at least as precise as asked. This is the one place the retired v1 commands'
    `eta=` / `delta=` arguments survive: they used to be passed to the emitter as a rational
    and are now resolved on this side, against the same schedule Lean proves about.
    """
    if (m is None) == (eta is None):
        raise EmitError("shor: give exactly one of m (the approximation LEVEL) or eta (the "
                        "precision); they are different quantities related by "
                        "referencePrecision m = 1/(m+3).")
    if eta is not None:
        from . import precision
        m = precision.m_of_eta(eta)
    cache = paths.LEAN_ARTIFACTS / f"shor_k{k}_a{a}_N{N}_m{m}.v2.json"
    return _emit_doc(cache, schemas.FORSHOR_LOWGATE_V2,
                     ["shor", str(k), str(a), str(N), str(m)],
                     f"forshor_emit shor {k} {a} {N} {m}", timeout, refresh,
                     "the whole Shor program")


# ---------------------------------------------------------------- the phase products
def pp(k: int, n: int, phi: Fraction = Fraction(1, 8), *, flat: bool = False,
       timeout: float = DEFAULT_TIMEOUT, refresh: bool = False) -> Dict[str, Any]:
    """`forshor_emit pp k n phi [--flat]` -> the signed phase product on width-`n` operands.

    Annotated (`forshor.plan/v1`) by default: the plan view keeps the recursion tree, the
    per-leaf operand registers and the leaf angles, which is what the structural checks read.
    Pass `flat=True` for the lowered `forshor.lowgate/v2` circuit that the backends compile.

    Replaces v1's `lower` / :func:`lower_rec`, with equal widths only (ask L5)."""
    schema, flags, tag = _view(flat)
    cache = paths.LEAN_ARTIFACTS / f"pp_k{k}_n{n}_{phi.numerator}_{phi.denominator}.{tag}.json"
    return _emit_doc(cache, schema,
                     ["pp", str(k), str(n), f"{phi.numerator}/{phi.denominator}", *flags],
                     f"forshor_emit pp {k} {n}", timeout, refresh,
                     "the signed phase product")


def cpp(k: int, n: int, phi: Fraction = Fraction(1, 8), *, flat: bool = False,
        timeout: float = DEFAULT_TIMEOUT, refresh: bool = False) -> Dict[str, Any]:
    """`forshor_emit cpp k n phi [--flat]` -> the CONTROLLED signed phase product.

    New in v2: v1 had no way to emit the controlled form standalone, so its cost had to be
    inferred from the uncontrolled one plus a control-cost rule. Now it is emitted, and that
    rule is checkable rather than assumed."""
    schema, flags, tag = _view(flat)
    cache = paths.LEAN_ARTIFACTS / f"cpp_k{k}_n{n}_{phi.numerator}_{phi.denominator}.{tag}.json"
    return _emit_doc(cache, schema,
                     ["cpp", str(k), str(n), f"{phi.numerator}/{phi.denominator}", *flags],
                     f"forshor_emit cpp {k} {n}", timeout, refresh,
                     "the controlled signed phase product")


def qft(k: int, w: int, *, flat: bool = False, timeout: float = DEFAULT_TIMEOUT,
        refresh: bool = False) -> Dict[str, Any]:
    """`forshor_emit qft k w [--flat]` -> the balanced Cooley-Tukey QFT on width `w`.

    NOTE the dropped argument: v1 took a `phi` that scaled the twiddle base angle. v2 fixes it
    at `qftPhi = 2*pi/2^w`, which is the only value the plain QFT uses and the only one v1 was
    ever called with here. :func:`lower_qft` still accepts `phi` and rejects anything but 1, so
    a stale call site fails with a sentence instead of silently emitting a different circuit."""
    schema, flags, tag = _view(flat)
    cache = paths.LEAN_ARTIFACTS / f"qft_k{k}_w{w}.{tag}.v2.json"
    return _emit_doc(cache, schema, ["qft", str(k), str(w), *flags],
                     f"forshor_emit qft {k} {w}", timeout, refresh, "the QFT circuit")


# ---------------------------------------------------------------- v1 names, still callable
def lower_rec(k: int, n: int, phi: Fraction = Fraction(1, 8), timeout: float = DEFAULT_TIMEOUT,
              refresh: bool = False) -> Dict[str, Any]:
    """Deprecated alias for ``pp(..., flat=True)``.

    Flat, not annotated: v1's `lower` returned a lowered circuit and its callers index into
    `circuit`, so the flat view is the shape they mean. The schema they get is
    `forshor.lowgate/v2`, not `shor.lowgate.ir/v1` -- a caller that checks the old string will
    fail loudly here, which is the point."""
    return pp(k, n, phi, flat=True, timeout=timeout, refresh=refresh)


def lower_qft(k: int, w: int, phi: Fraction = Fraction(1, 1), timeout: float = DEFAULT_TIMEOUT,
              refresh: bool = False) -> Dict[str, Any]:
    """Deprecated alias for ``qft(..., flat=True)``. Rejects any `phi` but 1 (see :func:`qft`)."""
    if Fraction(phi) != 1:
        raise EmitError(
            f"qft: the v2 emitter fixes the twiddle base angle at qftPhi = 2*pi/2^w and takes "
            f"no phi; refusing phi={phi}. v1 accepted it, so this call would previously have "
            f"emitted a DIFFERENT circuit and is not a no-op to drop. Call qft(k, w) for the "
            f"plain QFT.")
    return qft(k, w, flat=True, timeout=timeout, refresh=refresh)


# ---------------------------------------------------------------- no v2 counterpart (SKIP)
def lower_twiddle(k: int, left: int, right: int, phi: Fraction = Fraction(1, 2),
                  timeout: float = DEFAULT_TIMEOUT, refresh: bool = False) -> Dict[str, Any]:
    _retired("twiddle", k=k, left=left, right=right)


def lower_cmodmul(k: int, n: int, c: int, N: int, eta: float = 1e-3,
                  timeout: float = DEFAULT_TIMEOUT, refresh: bool = False) -> Dict[str, Any]:
    _retired("cmodmul", k=k, n=n, c=c, N=N)


def lower_modexp(k: int, n: int, a: int, N: int, tbits: int, delta: float = 1e-2,
                 timeout: float = DEFAULT_TIMEOUT, refresh: bool = False) -> Dict[str, Any]:
    _retired("modexp", k=k, n=n, a=a, N=N, tbits=tbits)


def lower_orderfinding(k: int, n: int, a: int, N: int, L: int, delta: float = 1e-2,
                       timeout: float = DEFAULT_TIMEOUT,
                       refresh: bool = False) -> Dict[str, Any]:
    _retired("orderfinding", k=k, n=n, a=a, N=N, L=L)


def lower_multiply(k: int, n: int, c: int, w: int, timeout: float = DEFAULT_TIMEOUT,
                   refresh: bool = False) -> Dict[str, Any]:
    _retired("multiply", k=k, n=n, c=c, w=w)
