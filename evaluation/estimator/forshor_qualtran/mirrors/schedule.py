"""E1 mirror: the Toom schedule for arity k, without n (`shor.toomsched/v1` + layer sizes + census).

The table has two sources:

- For k ≤ 7: the frozen documents that Lean emitted (ScheduleEmit, BUILT AND RUN 2026-08-27).
- For k ≥ 8: the legacy Python mirror of ``generateParityProduct``. A check at k = 4..7 showed
  that it is equal to Lean.

``table_source`` records the provenance of the *table*.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .. import paths, schemas

CITES = ["Shor.genOpsWithProduct_returns_to_original", "Shor.genOpsWithProduct_ProgConsumesPtsSafe",
         "Shor.lowerSignedPhaseProduct_correct"]


def q_of(k: int) -> int:
    return 2 * k - 1


def raw_table(k: int) -> tuple[Dict[str, Any], str]:
    """Return (table, source). The source is frozen-lean (k ≤ 7) or py-mirror (k ≥ 8)."""
    if k < 2:
        raise ValueError(f"k = {k} < 2")
    p = paths.SCHED_FROZEN / f"k{k}.lean-emitted.toomsched.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8")), "frozen-lean"
    paths.setup_legacy_sys_path()
    import toomsched  # legacy py/toomsched.py
    return toomsched.from_mirror(k), "py-mirror"


def layer_sizes(k: int) -> List[int]:
    """`Table_Generation.generateLayerSizes .PhaseProduct k` (Generator/Defs.lean:187)."""
    if k == 2:
        return [2, 1]
    if k == 3:
        return [3, 2]
    rem = q_of(k) - 4
    pair_count = rem // 2
    return [4] + [2] * pair_count + ([1] if rem % 2 == 1 else [])


def census(ops: List[Dict[str, Any]]) -> Dict[str, Any]:
    c = {t: sum(1 for o in ops if o["op"] == t)
         for t in ("phaseProduct", "addScaled", "negate", "shiftL", "shiftR")}
    c["addScaled_negSrc"] = sum(1 for o in ops if o["op"] == "addScaled" and o["negSrc"])
    return {"ops_total": len(ops), "phaseProduct": c["phaseProduct"], "addScaled": c["addScaled"],
            "negate": c["negate"], "addScaled_negSrc": c["addScaled_negSrc"], "shiftL": c["shiftL"],
            "shiftR": c["shiftR"], "A_k": c["addScaled"] + c["negate"],
            "A_k_note": "adder-class ops (addScaled + negate); each is a width-W ripple in the cost model"}


def section(k: int) -> Dict[str, Any]:
    tab, src = raw_table(k)
    ops, pts = tab["ops"], tab["points"]
    if sum(1 for o in ops if o["op"] == "phaseProduct") != q_of(k):
        raise ValueError(f"phaseProductCount != q at k={k}")
    if len(pts) != q_of(k):
        raise ValueError(f"points.length != q at k={k}")
    doc = {
        "schema": schemas.TOOMSCHED, "mode": "PhaseProduct", "k": k, "q": q_of(k),
        "decl": f"Table_Generation.generate .PhaseProduct {k}",
        "source_file": ("FastMultiplication/ShorVerification/Implementation/"
                        "PhaseProduct/Math/Table_Generation/Generator/Defs.lean"),
        "provenance": "lean-emitted",
        "ops": [dict(o) for o in ops], "points": [dict(p) for p in pts],
        "layer_sizes": layer_sizes(k), "census": census(ops), "cites": CITES,
    }
    doc["_mirror_table_source"] = src   # removed before the comparison with the Lean output
    return doc
