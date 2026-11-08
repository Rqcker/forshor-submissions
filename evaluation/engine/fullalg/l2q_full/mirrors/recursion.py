"""E6 mirror — the recursive Toom-Cook lowering skeleton (widths, multiplicities, reserve,
Lean cost-model bound). Mirrors Emit/ForshorEmit/Recursion.lean line by line.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .. import schemas
from ..constants import LEAN_RIPPLE_ADDER
from .schedule import census, q_of, raw_table
from .width import reserve_need, sym_width

CITES = ["Shor.lowerSignedPhaseProduct_correct", "Shor.standardSignedPhaseLoweringPlan",
         "Shor.evalL_lowerGateRec_correct", "Shor.evalL_lowerSignedPhaseProd_of_plan",
         "Shor.RecursivePhaseWorkspace.reserveNeed", "Shor.phaseProductGateCountBound_of_programOK"]


def width_ladder(k: int, ops, w: int) -> List[int]:
    """W₀ = w, W_{i+1} = next(W_i) while next(W_i) < W_i (PhaseLoweringPlan signedStep/signedBase)."""
    ladder = [w]
    while True:
        w2 = sym_width(k, ops, ladder[-1])
        if w2 < ladder[-1]:
            ladder.append(w2)
        else:
            return ladder


def lean_gate_bound(k: int, ops, w: int) -> int:
    """Σ_{i<d} qⁱ·A_k·(9·W_{i+1}+2) + q^d·W_d² (shorGateCostModel evaluated on the skeleton)."""
    q = q_of(k)
    A = census(ops)["A_k"]
    ladder = width_ladder(k, ops, w)
    d = len(ladder) - 1
    bodies = sum(q ** i * (A * LEAN_RIPPLE_ADDER(ladder[i + 1])) for i in range(d))
    return bodies + q ** d * ladder[-1] ** 2


def entry(k: int, ops, w: int) -> Dict[str, Any]:
    ladder = width_ladder(k, ops, w)
    d = len(ladder) - 1
    x, z = reserve_need(k, ops, w, w)
    return {"w": w, "levels": ladder, "depth": d, "leaves": q_of(k) ** d, "base_width": ladder[-1],
            "reserve_need": {"x": x, "z": z}, "lean_gate_bound": lean_gate_bound(k, ops, w)}


def recursion_ladder(w_max: int) -> List[int]:
    return [w for w in range(2, w_max + 1) if w <= 64 or w % 32 == 0]


def section(k: int, w_max: int = 96) -> Dict[str, Any]:
    ops = raw_table(k)[0]["ops"]
    return {
        "schema": schemas.FORSHOR_RECURSION, "k": k, "q": q_of(k), "A_k": census(ops)["A_k"],
        "rule": {
            "step": "SignedPhaseProd at width W -> body(ops) at width W' = nextSignedWidth(W) with q leaves at width W', while W' < W",
            "base": "Naive_SignedPhaseProd: W x W phase grid (q W^2 CZPow at the top level of the naive lowering)",
            "next": "W' = symWidth(W) — the E3 symbolic table / affine tail (W' = W/k + beta for uniform limbs)",
            "k_policy": "single k per plan (ForShor); the KMY skeleton's per-level k switching is NOT the Lean object"},
        "cost_model": {"body": "A_k * (9*W' + 2)", "base": "W_d * W_d",
                       "bound": "sum_{i<d} q^i * body(W_{i+1}) + q^d * base(W_d)"},
        "table": [entry(k, ops, w) for w in recursion_ladder(w_max)],
        "cites": CITES,
        "provenance": "lean-emitted skeleton (widths, multiplicities, reserve, cost-model bound); layouts and phases of the plan are noncomputable in ForShor and are NOT emitted",
    }
