"""Run the Lean `forshor_emit` binary or the mirror, and prove that they agree.

    bundle = emit_bundle(k)              # REAL-LEAN if the binary exists, else REFERENCE-MIRROR
    emit_bundle(k, require_real=True)    # raises if only the mirror is available
    crosscheck(k)                        # parsed-JSON equality real vs mirror, per section
    phases_crosscheck(k, m, phi)         # forshor_emit phases == phase_emit == mirror, byte-for-byte

Exit-code contract (both binaries): 0 complete, 2 REFUSED (stdout empty), 3 INTERNAL CHECK FAILED.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional

from . import paths, schemas
from .mirrors import mirror_bundle, mirror_section
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
        """Write to ``k<k>.forshor.json``, or to a sidecar when the write would destroy data.

        These bundles are expensive and curated. One full-suite run overwrote all of the set. For
        their recovery, we had to dig the values out of `web/pipeline-data.js`, because git did
        not have them either. Thus, a save never silently replaces a bundle that a *different*
        producer wrote. There are two guards, in this order:

          * The mirror never overwrites REAL-LEAN (kept from v1).
          * A REAL-LEAN bundle from a different emitter build does not overwrite a bundle that is
            already on disk. The v1 and v2 emitters disagree on section names, so this is exactly
            the cutover case. If the v1 numbers were lost, the comparison would have no
            before-side.

        Neither guard raises an error. The bundle goes next to the incumbent, under a name that
        shows its origin. `notes` records the displacement, so that the caller can report it.
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
                # `prev_fp is None` counts as different, and this is deliberate. Each bundle on
                # disk from before 2026-09-10 is older than the fingerprints, and those are exactly
                # the v1-emitter bundles that this guard protects. If "no fingerprint" meant
                # "no objection", the guard would do nothing on exactly its intended targets.
                # The first version of the guard had this defect.
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


#: Default table sizes for each arity. The figures need only the affine tails (found by m ≈ 5) and the
#: recursion ladders (E6 covers w ≤ w_max). Larger k is much slower in the Lean solver (k = 7 took more
#: than 600 s at 24/96 on the target machine, 2026-09-02), so the defaults decrease with k. To change
#: them, use m_max/w_max.
def default_opts(k: int) -> tuple:
    return (24, 96) if k <= 5 else (16, 64)


DEFAULT_TIMEOUT = 3600.0

#: The Toom-Cook operation schedule to emit. THIS CHOICE CHANGES THE COST, and the v2 drop
#: changed its default. Thus, this module names it explicitly and does not leave it to the emitter.
#:
#:   "standard"  Shor.standardLoweringSetup. ProgConsumesPtsSafe and returns-to-start are
#:               THEOREMS (genOpsWithProduct_ProgConsumesPtsSafe, genOpsWithProduct_-
#:               returns_to_original). Integer evaluation points 0, -1, 1, -2, 2, ...
#:   "generate"  Table_Generation.generate. The provenance of the emitter says: "checked at run
#:               time only, no correctness theorem". It uses fractional evaluation points.
#:
#: The default schedule of the v1 emitter is BIT-IDENTICAL to the v2 `generate` at each arity that
#: we have a bundle for (k = 2..7, for ops and for points). Thus, each cost number that this pipeline
#: published before the cutover was computed on the unproved table. The proved table is more
#: expensive, and the difference increases quickly with k. Adder-class ops, v1 default -> "standard":
#:
#:     k      2      3      4      5      6      7
#:     ops    2->4   12->16 22->56 38->92 58->216 82->372
#:     x      2.00   1.33   2.55   2.42   3.72    4.54
#:
#: We use the proved table as the default and keep the cheap one as an explicit opt-in. This follows
#: the standing rule that Lean's definitions are the default and alternatives are toggles. No theorem
#: covers a number quoted from "generate", and the number must say so.
DEFAULT_TABLE = "standard"
TABLES = ("standard", "generate")


def _table_flags(table: Optional[str]) -> List[str]:
    tb = table or DEFAULT_TABLE
    if tb not in TABLES:
        raise EmitError(f"unknown Toom-Cook table {tb!r}; expected one of {TABLES}")
    return ["--table", tb]


def _run(binary: Path, args: List[str], timeout: float = DEFAULT_TIMEOUT) -> str:
    t0 = time.time()
    try:
        proc = subprocess.run([str(binary), *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
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
    """The Lean bundle, or None if the search finds no binary. Raises EmitError on refusal/timeout."""
    b = paths.forshor_emit_binary()
    if b is None:
        return None
    dm, dw = default_opts(k)
    m_max, w_max = m_max or dm, w_max or dw
    tb = table or DEFAULT_TABLE
    t0 = time.time()
    doc = json.loads(_run(b, ["bundle", str(k), "--m-max", str(m_max), "--w-max", str(w_max),
                              *_table_flags(tb)], timeout=timeout))
    schemas.expect(doc, schemas.FORSHOR_EMIT, where=f"forshor_emit bundle {k}")
    note = f"emitted by the Lean binary in {time.time() - t0:.1f} s, --table {tb}"
    if tb == "generate":
        note += " (NO correctness theorem: Table_Generation.generate is run-time checked only)"
    return Bundle(k, doc, Provenance.REAL_LEAN, b, notes=[note])


def cached_real_bundle(k: int, m_max: Optional[int] = None, w_max: Optional[int] = None,
                       table: Optional[str] = None) -> Optional[Bundle]:
    """A previously saved REAL-LEAN bundle with the same table sizes AND the same Toom-Cook
    table (fixtures/lean/k<k>.forshor.json). The table is part of the cache key, because the
    two tables disagree by up to 4.5x in adder count. If one table is used for the other, the
    result silently shows the cost of the wrong schedule."""
    # Look at the fingerprinted sidecar FIRST, then at the plain name. When the save guard does
    # not overwrite an incumbent, the bundle that it just emitted goes to
    # `k<k>.forshor.<fp>.json`. If the cache looked only at the plain name, it would never find
    # it. Thus, each call would emit again, with the false belief that it had a cache. This
    # really occurred: it changed one test run into a 28-minute loop of bundle emissions.
    fp = emitter_fingerprint()
    candidates = []
    if fp:
        candidates.append(paths.LEAN_ARTIFACTS / f"k{k}.forshor.{fp.split(':', 1)[-1][:12]}.json")
    candidates.append(paths.LEAN_ARTIFACTS / f"k{k}.forshor.json")
    dm, dw = default_opts(k)
    want = {"m_max": m_max or dm, "w_max": w_max or dw}

    def usable(doc: Dict[str, Any]) -> bool:
        # Compare only the keys that we asked for. The v2 emitter also gives `check_cramer` in
        # `opts`. Thus, the v1 equality of the whole dict rejected each bundle that it had just
        # written, and it emitted again on each call. A cache that never hits looks like "the
        # emitter is slow" and not like a bug.
        opts = doc.get("opts") or {}
        if (doc.get("_provenance") != Provenance.REAL_LEAN.value
                or doc.get("schema") != schemas.FORSHOR_EMIT
                or any(opts.get(key) != val for key, val in want.items())):
            return False
        # A v1 bundle has no `table` key. It was emitted on what v2 calls `generate`.
        return (doc.get("table") or "generate") == (table or DEFAULT_TABLE)

    # Validation, not only existence, decides which candidate wins. If the function selects the
    # first file that EXISTS and then rejects it, it loses the good bundle under the other name.
    # The sidecar and the plain name often have different `opts`, so that version of this
    # function never hit the cache.
    for p in candidates:
        if not p.is_file():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if usable(doc):
            clean = {kk: v for kk, v in doc.items() if not str(kk).startswith("_")}
            return Bundle(k, clean, Provenance.REAL_LEAN, paths.forshor_emit_binary(),
                          notes=[f"REAL-LEAN bundle reused from {p.name} (use --refresh to re-emit)"])
    return None


def emit_bundle(k: int, m_max: Optional[int] = None, w_max: Optional[int] = None, *, require_real: bool = False,
                crosscheck_if_real: bool = True, refresh: bool = False, timeout: float = DEFAULT_TIMEOUT,
                table: Optional[str] = None) -> Bundle:
    """REAL-LEAN when a saved real bundle exists (unless refresh), or when the binary runs and agrees with
    the mirror. Otherwise, the mirror, and `notes` records the reason (timeout / refusal / no binary).
    The decision is for each k: a slow arity falls back alone, and the others stay REAL-LEAN."""
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
        # ADVISORY, not a gate. Under v1, the mirror was a faithful reimplementation, and each
        # disagreement meant that one of the two was wrong. Thus, an error was correct. Under v2,
        # this is not true. The mirror reimplements a bundle shape that the emitter no longer
        # produces, and the shared sections are the sections that change least. If all of the
        # emission failed on that, a stale Python file would block each REAL-LEAN bundle. We trust
        # the mirror less, so the mirror is the side that yields.
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
#: ACCEPTED, each with the reason why it is not a defect. All other differences are binding.
#: The key is the prefix of the diff path, and the value is the reason why the difference is allowed.
#:
#: This explicit list is the important point. We do not widen the comparison until nothing fails.
#: The list is a short, clear record of each place where the two emitters genuinely disagree,
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
    """The reason why `diff` is accepted, or None if it is binding."""
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
    """The real bundle against the mirror, section by section, with an attribution for each difference.

    `attribute=True` also emits the bundle on `baseline_table` (default `generate`, the table of
    the frozen mirror). This separates the differences that the table explains from the
    differences that it does not explain. This costs one more emission, so it is opt-in. Callers
    must assert on `unexplained`, not on `mismatches`. Refer to the note in the body.
    """
    dm, dw = default_opts(k)
    m_max, w_max = m_max or dm, w_max or dw
    real = real or cached_real_bundle(k, m_max, w_max) or real_bundle(k, m_max, w_max)
    if real is None:
        return {"available": False, "k": k, "mismatches": [], "sections": {}}
    mirror = strip_private(mirror_bundle(k, m_max, w_max))
    result: Dict[str, Any] = {"available": True, "k": k, "sections": {}, "mismatches": [],
                              "only_real": [], "only_mirror": []}
    # The v2 bundle and the Python mirror no longer have the same set of sections: v2 removed
    # `modmul_plan` and added `shor_plan` and `template`. A comparison of the union would report
    # each such section as a mismatch and give no useful data. The function compares only the
    # sections that the TWO sides have. It reports the other sections by name, as a fact about
    # coverage and not as a failure.
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

    # ---- classify each disagreement, and do not only count it ----------------------------------
    #
    # A bare "mismatches" list made this check useless after the 2026-09-18 cutover, and it would
    # have stayed useless in each direction. The mirror for k <= 7 is a FROZEN SNAPSHOT of the
    # 2026-08-27 emitter (`mirrors/schedule.raw_table` reads
    # `schedules/frozen_reference/k{k}.lean-emitted.toomsched.json`). Thus, this is a regression
    # check against v1 output, and v1 differs from v2 in ways that we selected. An assert of
    # equality asserts that the cutover did not occur. A deletion of the assert removes the one
    # check that would find real drift. Thus, the function gives an attribution for each difference:
    #
    #   shape     -- a key exists on one side only. The v1 and v2 schemas differ by design
    #                (v2 added `check1_inverse`/`check2`, removed `cites`/`k`/`A_k`, and changed
    #                the name `affine_sym` -> `affine_tail`). This is not a numeric disagreement.
    #   table     -- a VALUE differs, and the difference stops when the real bundle is emitted on
    #                `generate`, the table of the frozen baseline. The cause is the deliberate
    #                move to the proved `standard` table (section 8.19.1). This is MEASURED
    #                here, not assumed: `attribute=True` emits the comparison bundle.
    #   unexplained -- a value differs on the SAME table. This is the binding set. It is expected
    #                to be empty, and a caller must assert on it.
    #
    # A check at k=2 showed this: all three `schedule` value differences (`census/addScaled: 4 != 2`,
    # `ops` length 7 vs 5, `points[1]/kind`) go away on `generate`. Thus, with the same table, the
    # schedule sections agree exactly between the 2026-08-27 and 2026-09-18 emitters.
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
    # Of the differences that the table does not explain, separate the differences that we examined
    # and accepted (each with its reason, in KNOWN_V1_V2_DIFFERENCES) from the differences that
    # nobody has explained. The second list is the important one.
    result["known_differences"] = {d: _is_known_difference(d) for d in result["unexplained"]
                                   if _is_known_difference(d)}
    result["binding"] = [d for d in result["unexplained"] if not _is_known_difference(d)]
    return result


def phases_real(k: int, m: int, phi: Fraction, which: str = "forshor_emit") -> Optional[List[str]]:
    """The flat phase list from a binary (`forshor_emit phases` or `phase_emit`), or None if there is no binary."""
    b = paths.forshor_emit_binary() if which == "forshor_emit" else paths.phase_emit_binary()
    if b is None:
        return None
    args = (["phases"] if which == "forshor_emit" else []) + [str(k), str(m), f"{phi.numerator}/{phi.denominator}"]
    return _run(b, args).strip().splitlines()


def phases_from_coeff_poly(k: int, m: int, phi: Fraction = Fraction(1, 8),
                           bundle: Optional["Bundle"] = None) -> Optional[List[str]]:
    """`c_l(2^m) * phi`, evaluated from the `coeff_poly` rows of the BUNDLE itself.

    This is the reference for the check of the `phases` command. It is a real cross-check and
    not a restatement. `Bundle.lean:buildPhases` and the coefficient table are two different
    paths through Lean. Thus, if they agree, the phase list and the interpolation coefficients
    describe the same polynomial. It is also correct for the table by construction, because it
    reads the table that the bundle was emitted on.
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
    """`forshor_emit phases` against the coefficient rows of the bundle itself, and (as an advisory) the mirror.

    The BINDING leg was tied again on 2026-09-22. Before, it compared against
    `mirrors.coeff_poly.phases`, the Python mirror, which computes the coefficients of the
    `generate` table. The 2026-09-18 drop changed the default to `standard` (section 8.19.1).
    Thus, the mirror now disagrees with the emitter by construction, on each k and m. This is a
    stale reference, not a defect. A check at k=2 m=3 gave `-63/8, 7/2, 9/2` from the emitter,
    and the values of the old table from the mirror.

    Thus, the binding comparison is against `phases_from_coeff_poly`. That function reads the
    coefficient rows from the same bundle, so it follows the table that the bundle was emitted on.
    The function still reports the mirror, as an advisory signal of table drift and not as a gate.
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
    """A fingerprint of the `forshor_emit` build that must have made the cached IR documents. This is the
    Lake `<binary>.hash` when it is next to the executable, else the sha256 of the size and the first
    megabyte of the executable. None if there is no binary (then the cache is trusted, and the report
    says so)."""
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
    """The cached IR document at `p`, if it is usable. It must have the correct schema and REAL-LEAN
    provenance. When the emitter binary is present, it must also have the SAME build fingerprint
    (`_emitter`) as that binary. A document from an older build (or from before fingerprints existed,
    2026-09-10) is stale, and it is emitted again. The reason is that cached twiddle/QFT documents
    from 2026-09-02 were older than the zero-extension fix of 2026-09-03. They disagreed with the
    current emitter by up to 3x in leaf count."""
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
# The 2026-09-18 drop replaced all of the emitter. It has these commands:
#
#     shor <k> <a> <N> <m>            the whole program, flat            forshor.lowgate/v2
#     bundle <k> [--table ...]        the per-k tables                   forshor.emit/v1
#     <section> <k>                   one bundle section
#     phases <k> <m> <phi>            the flat phase list                (text)
#     pp  <k> <n> <phi> [--flat]      signed phase product      plan/v1, or lowgate/v2 flat
#     cpp <k> <n> <phi> [--flat]      controlled ditto
#     qft <k> <w> [--flat]            the Cooley-Tukey QFT
#
# It has no other commands. Five v1 subcommands have no counterpart. They stay below as functions
# that raise one explicit EmitError, which names the capability and the ask. The reason is that
# about 100 call sites already treat EmitError as "report SKIP, not certified", and we want that
# behaviour.

#: What the v2 emitter cannot build, why, and which ask requests it. All of the text is in one
#: table, so the SKIP reason that a notebook prints is the same sentence that the collaborator got.
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
    """Raise the one EmitError that explains a capability that the v2 emitter does not have."""
    what, ask, why = RETIRED[name]
    args = ", ".join(f"{key}={val!r}" for key, val in kwargs.items() if val is not None)
    where = f" ({args})" if args else ""
    raise EmitError(
        f"forshor_emit has no `{name}` subcommand: {what} is not emittable by the "
        f"2026-09-18 drop{where}. {why} Tracked as ask {ask} in "
        f"FORSHOR_09182026_LEAN_ASKS.2026-09-18.md. This step reports SKIP -- it is not "
        f"certified, and no Python mirror stands in for it by design.")


def _emit_doc(cache: Path, schema: str, args: List[str], where: str, timeout: float,
              refresh: bool, missing: str) -> Dict[str, Any]:
    """Get one document from the cache, or emit it: the shared body of each command below."""
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
    """(schema, cli flags, filename tag) for the flat view or the annotated view."""
    if flat:
        return schemas.FORSHOR_LOWGATE_V2, ["--flat"], "flat"
    return schemas.FORSHOR_PLAN, [], "plan"


# ---------------------------------------------------------------- the whole program
def shor(k: int, a: int, N: int, m: Optional[int] = None, timeout: float = DEFAULT_TIMEOUT,
         refresh: bool = False, *, eta: Optional[Fraction] = None) -> Dict[str, Any]:
    """`forshor_emit shor k a N m` -> the whole Shor program, flat, as `forshor.lowgate/v2`.

    This is the headline object of the v2 drop, and it has no v1 counterpart. Before, the program
    existed only as pieces that were emitted separately (modexp/orderfinding descriptors), and the
    Python side had to compose them. It has `meta.layout`, which names the physical index
    ranges of the x / data / work / scratch registers and the flag qubit, and
    `output_register`. Thus, the code reads the measured register from the document and does not
    assume it.

    About `m`: it is the approximation LEVEL, not a bit count, and the certified success bound at
    small `m` is zero. `forshor_qualtran.precision` converts between `m`, eta and the bound. A
    figure emitted at `m = 0` (the example in the README) is a real circuit that nothing
    certifies. Refer to :func:`forshor_qualtran.precision.certificate`.

    To name the precision that you actually want, pass `eta=` and not `m`. The function resolves
    the level through `precision.m_of_eta` (Lean's `referencePrecision m = 1/(m+3)`). It rounds to
    the LEAST level that is at least as precise as the request. This is the one place where the
    `eta=` / `delta=` arguments of the retired v1 commands continue. Before, the emitter got them
    as a rational. Now this side resolves them, against the same schedule that Lean proves results
    about.
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

    The default is the annotated view (`forshor.plan/v1`). The plan view keeps the recursion tree,
    the operand registers of each leaf and the leaf angles, and the structural checks read these.
    Pass `flat=True` to get the lowered `forshor.lowgate/v2` circuit that the backends compile.

    This replaces the v1 `lower` / :func:`lower_rec`, with equal widths only (ask L5)."""
    schema, flags, tag = _view(flat)
    cache = paths.LEAN_ARTIFACTS / f"pp_k{k}_n{n}_{phi.numerator}_{phi.denominator}.{tag}.json"
    return _emit_doc(cache, schema,
                     ["pp", str(k), str(n), f"{phi.numerator}/{phi.denominator}", *flags],
                     f"forshor_emit pp {k} {n}", timeout, refresh,
                     "the signed phase product")


def cpp(k: int, n: int, phi: Fraction = Fraction(1, 8), *, flat: bool = False,
        timeout: float = DEFAULT_TIMEOUT, refresh: bool = False) -> Dict[str, Any]:
    """`forshor_emit cpp k n phi [--flat]` -> the CONTROLLED signed phase product.

    New in v2: v1 could not emit the controlled form standalone. Thus, its cost came from the
    uncontrolled form plus a control-cost rule. Now the emitter emits it, so a check of that rule
    is possible, and the rule is not an assumption."""
    schema, flags, tag = _view(flat)
    cache = paths.LEAN_ARTIFACTS / f"cpp_k{k}_n{n}_{phi.numerator}_{phi.denominator}.{tag}.json"
    return _emit_doc(cache, schema,
                     ["cpp", str(k), str(n), f"{phi.numerator}/{phi.denominator}", *flags],
                     f"forshor_emit cpp {k} {n}", timeout, refresh,
                     "the controlled signed phase product")


def qft(k: int, w: int, *, flat: bool = False, timeout: float = DEFAULT_TIMEOUT,
        refresh: bool = False) -> Dict[str, Any]:
    """`forshor_emit qft k w [--flat]` -> the balanced Cooley-Tukey QFT on width `w`.

    NOTE the removed argument: v1 took a `phi` that scaled the base angle of the twiddle. v2 sets
    it to `qftPhi = 2*pi/2^w`. This is the only value that the plain QFT uses, and the only value
    that this code ever gave to v1. :func:`lower_qft` still accepts `phi` and rejects all values
    except 1. Thus, a stale call site fails with a sentence, and it does not silently emit a
    different circuit."""
    schema, flags, tag = _view(flat)
    cache = paths.LEAN_ARTIFACTS / f"qft_k{k}_w{w}.{tag}.v2.json"
    return _emit_doc(cache, schema, ["qft", str(k), str(w), *flags],
                     f"forshor_emit qft {k} {w}", timeout, refresh, "the QFT circuit")


# ---------------------------------------------------------------- v1 names, still callable
def lower_rec(k: int, n: int, phi: Fraction = Fraction(1, 8), timeout: float = DEFAULT_TIMEOUT,
              refresh: bool = False) -> Dict[str, Any]:
    """Deprecated alias for ``pp(..., flat=True)``.

    It gives the flat view, not the annotated view. The v1 `lower` returned a lowered circuit, and
    its callers index into `circuit`, so the flat view is the shape that they mean. They get the
    schema `forshor.lowgate/v2`, not `shor.lowgate.ir/v1`. Thus, a caller that checks the old
    string will fail loudly here, and this is the intention."""
    return pp(k, n, phi, flat=True, timeout=timeout, refresh=refresh)


def lower_qft(k: int, w: int, phi: Fraction = Fraction(1, 1), timeout: float = DEFAULT_TIMEOUT,
              refresh: bool = False) -> Dict[str, Any]:
    """Deprecated alias for ``qft(..., flat=True)``. It rejects all `phi` values except 1 (refer to :func:`qft`)."""
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
