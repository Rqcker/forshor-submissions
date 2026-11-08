r"""Run the Lean `shor_emit` binary (Stage 2 emitter) and read its `shor.ir/v2` documents.

    schema()                       the constructor table the Lean side derives from `Shor.LowGate`
    pp(k, n, phi)                  signed phase product on n-bit operands
    qft(k, w)                      QFT on a w-qubit register
    cmodmul(k, n, m, c, N)         controlled modular multiply core
    shor(k, n, m, L, a, N)         order finding (the quantum part of Shor)
    family(kind, k)                the Stage 4 family document (census recurrence + width rule + anchors)

Every document is the PROVED lowering evaluated directly (`Emit/Cli/Main.lean`), serialised by the
verified codec. `ir_v2.check_schema` runs on first use: if the Lean constructor table and this
package's handlers disagree, nothing is read.

Binary discovery: `ONECLICK_SHOR_EMIT`, else `<FORSHOR_TREE>/.lake/build/bin/shor_emit[.exe]`, else
a `leanforshor_copy` tree checked out beside `oneclick_pipeline` (override: `ONECLICK_FORSHOR_UNIFIED`)
— `FORSHOR_TREE` may have resolved to an older tree without a built `shor_emit`. There is deliberately
NO mirror: a document either came from the binary or does not exist.

Exit-code contract: 0 ok · 1 usage · 2 REFUSED (a decidable static precondition failed).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ir_v2, paths

ARTIFACTS_V2: Path = paths.ARTIFACTS / "lean_v2"
DEFAULT_TIMEOUT = 3600.0


def _unified_tree_candidates() -> List[Path]:
    """`leanforshor_copy` trees to probe in addition to `paths.FORSHOR_TREE` (which may have
    resolved to an older tree without a built `shor_emit`). Override: ONECLICK_FORSHOR_UNIFIED.

    Probes both a portable sibling-of-`oneclick_pipeline` checkout (`PIPE_ROOT.parent`, matching
    `env_paths.py`'s own convention for `leanforshor`/`leanforshor_copy`) AND a sibling-of-the-drive-
    root checkout (`PIPE_ROOT.anchor`, e.g. `C:\\leanforshor_copy` next to `C:\\leanforshor` on a
    machine where the vault and the Lean trees are not nested under one another) -- found missing on
    the benchmark machine 2026-09-11: neither `paths.FORSHOR_TREE` nor the old sibling guess resolved to the
    built `leanforshor_copy` tree there, only the drive-root one did."""
    override = os.environ.get("ONECLICK_FORSHOR_UNIFIED")
    if override:
        return [Path(override)]
    return [paths.PIPE_ROOT.parent / "leanforshor_copy", Path(paths.PIPE_ROOT.anchor) / "leanforshor_copy"]


class Emit2Error(RuntimeError):
    def __init__(self, msg: str, returncode: int = -1, stderr: str = ""):
        super().__init__(msg)
        self.returncode, self.stderr = returncode, stderr


def binary() -> Optional[Path]:
    cand = os.environ.get("ONECLICK_SHOR_EMIT")
    if cand:
        p = Path(cand)
        return p if p.is_file() else None
    for tree in (paths.FORSHOR_TREE, *_unified_tree_candidates()):
        for suffix in ("", ".exe"):
            p = tree / ".lake" / "build" / "bin" / f"shor_emit{suffix}"
            if p.is_file():
                return p
    return None


def _binary_stamp() -> str:
    """Path + mtime + size of the binary: a rebuilt binary at the same path invalidates the cache."""
    b = binary()
    if b is None:
        return ""
    st = b.stat()
    return f"{b}|{int(st.st_mtime)}|{st.st_size}"


def _run(args: List[str], timeout: float = DEFAULT_TIMEOUT) -> str:
    b = binary()
    if b is None:
        raise Emit2Error("shor_emit binary not found (build it: `lake build shor_emit` in the unified tree)")
    try:
        proc = subprocess.run([str(b), *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Emit2Error(f"shor_emit {' '.join(args)}: TIMEOUT after {timeout:.0f} s", returncode=-9)
    if proc.returncode != 0:
        tag = {1: "USAGE", 2: "REFUSED"}.get(proc.returncode, f"rc={proc.returncode}")
        raise Emit2Error(f"shor_emit {' '.join(args)}: {tag}: {proc.stderr.strip()}", proc.returncode, proc.stderr)
    if not proc.stdout.strip():
        raise Emit2Error(f"shor_emit {' '.join(args)}: empty stdout with exit 0")
    return proc.stdout


_schema_checked: Optional[Dict[str, Any]] = None


def schema(refresh: bool = False) -> Dict[str, Any]:
    """The Lean-derived schema; checked against `ir_v2.HANDLERS` (raises on any mismatch)."""
    global _schema_checked
    if _schema_checked is not None and not refresh:
        return _schema_checked
    doc = json.loads(_run(["schema"]))
    ir_v2.check_schema(doc)
    ARTIFACTS_V2.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS_V2 / "lowgate.schema.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    _schema_checked = doc
    return doc


def _cached(name: str, args: List[str], refresh: bool, timeout: float) -> Dict[str, Any]:
    schema()                      # schema/handler agreement is a precondition for reading anything
    ARTIFACTS_V2.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS_V2 / f"{name}.v2.json"
    if out.is_file() and not refresh:
        doc = json.loads(out.read_text(encoding="utf-8"))
        if doc.get("schema") == ir_v2.SCHEMA and doc.get("_binary") == _binary_stamp():
            return doc
    t0 = time.time()
    doc = json.loads(_run(args, timeout=timeout))
    doc["_binary"] = _binary_stamp()
    doc["_emitted_in_s"] = round(time.time() - t0, 3)
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def pp(k: int, n: int, phi: str = "1/8", *, refresh: bool = False, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    tag = phi.replace("/", "_").replace("-", "m")
    return _cached(f"pp_k{k}_n{n}_{tag}", ["pp", str(k), str(n), phi], refresh, timeout)


def qft(k: int, w: int, *, refresh: bool = False, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    return _cached(f"qft_k{k}_w{w}", ["qft", str(k), str(w)], refresh, timeout)


def cmodmul(k: int, n: int, m: int, c: int, N: int, *, refresh: bool = False,
            timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    return _cached(f"cmodmul_k{k}_n{n}_m{m}_c{c}_N{N}", ["cmodmul", str(k), str(n), str(m), str(c), str(N)],
                   refresh, timeout)


def shor(k: int, n: int, m: int, L: int, a: int, N: int, *, refresh: bool = False,
         timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    return _cached(f"shor_k{k}_n{n}_m{m}_L{L}_a{a}_N{N}",
                   ["shor", str(k), str(n), str(m), str(L), str(a), str(N)], refresh, timeout)


FAMILY_SCHEMA = "shor.ir/v2-family"


def family(kind: str, k: int, *, refresh: bool = False, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """The Stage 4 FAMILY document of `kind` in {pp, qft, cmodmul, shor}: the census recurrence over
    the size parameters, the width rule (Lean-computed tables + the proved periodic law), and the
    Lean-checked census anchors. Read it with `forshor_qualtran.family`."""
    if kind not in ("pp", "qft", "cmodmul", "shor"):
        raise ValueError(f"unknown family kind {kind!r}")
    schema()
    ARTIFACTS_V2.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS_V2 / f"family_{kind}_k{k}.v2.json"
    if out.is_file() and not refresh:
        doc = json.loads(out.read_text(encoding="utf-8"))
        if doc.get("schema") == FAMILY_SCHEMA and doc.get("_binary") == _binary_stamp():
            return doc
    t0 = time.time()
    doc = json.loads(_run(["family", kind, str(k)], timeout=timeout))
    if doc.get("schema") != FAMILY_SCHEMA:
        raise Emit2Error(f"family {kind} {k}: expected {FAMILY_SCHEMA}, got {doc.get('schema')!r}")
    doc["_binary"] = _binary_stamp()
    doc["_emitted_in_s"] = round(time.time() - t0, 3)
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def legacy(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The v1-shaped document for the existing backends (`parse_ir(..., require_checks=False)`)."""
    return ir_v2.to_legacy_doc(doc)


# --------------------------------------------------------------------------------------------------
# TODO-3 (partial): legacy-shaped wrappers, `shor.lowgate.ir/v1`, for primitives migrating off
# `emit.py`. Only `lower_qft` is actually wired to a `qiskit_bridge.py` call site today (`qft`-kind
# documents require zero blocking checks in the old schema, so `to_legacy_doc`'s output is already
# complete). `lower_rec` is NOT yet safe to use in place of `emit.lower_rec`, kept here only as a
# starting point for whoever closes the gap below -- do not wire it up as-is.
#
# `cmodmul`/`shor`'s `orderfinding`/`modexp`/`multiply` have no drop-in wrapper at all:
# `emit.lower_cmodmul` takes a target precision `eta` and derives its own workspace internally, while
# the new `shor_emit cmodmul` takes an explicit workspace width `m` with no established eta->m sizing
# rule anywhere in the tree (Lean or Python) to derive it from; `emit.lower_modexp`/`lower_multiply`
# have no new-side command at all. See REFACTOR_STAGE1_LOG.md's TODO-3 entry.

def lower_rec(k: int, n: int, phi: Fraction = Fraction(1, 8), timeout: float = DEFAULT_TIMEOUT,
              refresh: bool = False) -> Dict[str, Any]:
    """NOT YET SAFE as a drop-in for `emit.lower_rec` -- confirmed live: `py/lowgate_ir.py`'s
    `parse_ir` (the default, `require_checks=True` path every real caller uses) raises `IRSchemaError`
    on this function's output, because `signed_phase_product` documents require five blocking checks
    in the old schema (`count_matches_q_k`, `run_ok`, `width_bound_ok`, `coeff_verified`,
    `layout_disjoint` -- `py/lowgate_ir.py`'s `REQUIRED_CHECKS`) that `ir_v2.to_legacy_doc` does not
    reconstruct from the new document's own (differently-named) `checks` list. Fix `to_legacy_doc`
    (or this wrapper) to populate all five before pointing any real call site at this function."""
    doc = pp(k, n, f"{phi.numerator}/{phi.denominator}", refresh=refresh, timeout=timeout)
    return legacy(doc)


def lower_qft(k: int, w: int, phi: Fraction = Fraction(1, 1), timeout: float = DEFAULT_TIMEOUT,
              refresh: bool = False) -> Dict[str, Any]:
    """Drop-in replacement for `emit.lower_qft`, via the new `shor_emit qft` command: same
    `shor.lowgate.ir/v1` return shape. `phi` (the twiddle-scaling knob `emit.lower_qft` exposed) has
    no equivalent parameter in `shor_emit qft` -- it is always 1 there, which every existing call site
    already passes, so rejecting anything else is not a behavior change for real callers."""
    if phi != Fraction(1, 1):
        raise ValueError(f"emit2.lower_qft: phi must be Fraction(1, 1) (shor_emit qft has no twiddle-scale knob), got {phi}")
    doc = qft(k, w, refresh=refresh, timeout=timeout)
    return legacy(doc)
