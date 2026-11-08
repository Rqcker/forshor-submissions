"""Engine 2: count the same construction a second way (forshor_qualtran, the Lean-To-Qualtran lineage).

Engine 1 (tcost) builds ONE symbolic Qualtran bloq for the policy's chain and evaluates its closed form at n.
Engine 2 walks the policy tree at concrete widths with the forshor_qualtran dispatcher (`PhasePolicy.step`,
its own nextWidth mirror) and sums per-node Qualtran costs (`cost_conventions.tree_cost`).

What is shared, and therefore NOT independently checked: the Qualtran tree (vendored 0.8.0.dev0), the currency
(`sto_currency`), the construction itself (PP digital-product leaf and PTP digital triple-product leaf (Stage 17; grid leaf as a cross-check), uniform-width Qualtran adders, one Subtract for a
negated source). Both lines descend from Lean-To-Qualtran. Agreement means "the same constructions, counted two
ways", not two independent implementations. The site says so.

What IS checked: the table -> width recursion (two implementations of the policy's width rule: for the exact widths
of Stage 13, ShorTableOptimization's `exact_width` / the PTP suite's `ptp_tight_width` in engine 1 and the LowGateIR
`reachableFits` bisection `mirrors.width.exact_width` in engine 2; for the scan, two nextWidth implementations), the policy dispatcher, the child
count per level, the op census, and the exact Toom-Cook identity of each table on intake: PhaseProduct through
`forshor_qualtran.table_catalog.checked`, PhaseTripleProduct through `ptp_identity` (the same checks at 3k - 2 points).
"""
from __future__ import annotations

from typing import Any, Dict

from . import DEFAULT_DELTA
from forshor_qualtran import cost_conventions as cc
from forshor_qualtran.policy import PhasePolicy, PolicyBand, ToomCookTable
from forshor_qualtran.sto_policy import policy_from_sto
from forshor_qualtran.table_catalog import op_from_sto, point_from_sto
from tcost import tables as tt
from .ptp_identity import verify_ptp_policy


def to_l2q(P: tt.Policy) -> PhasePolicy:
    """Convert a tcost policy to a forshor_qualtran `PhasePolicy`. PhaseProduct tables pass the full
    forshor_qualtran check (C1, C2, C4 and the exact Toom-Cook identity). PhaseTripleProduct tables pass the same
    checks at q(k) = 3k - 2 (`ptp_identity.verify_ptp_policy`), because `ToomCookTable.check` is PP-only."""
    import dataclasses
    doc = P.to_json()
    if P.mode == tt.PAIR:
        return dataclasses.replace(policy_from_sto(doc), widths=P.widths)   # the same width rule, engine 2's own code
    verify_ptp_policy(P)
    bands, names = [], {}
    for i, b in enumerate(doc["bands"]):
        tab = b.get("table", b)
        T = ToomCookTable.from_json(f"band{i}-k{int(tab['k'])}", {
            "k": int(tab["k"]), "points": [point_from_sto(p) for p in tab["point_order"]],
            "ops": [op_from_sto(o) for o in tab["operations"]]})
        T = names.setdefault((T.k, T.points, T.ops_frozen), T)
        bands.append(PolicyBand(int(b["threshold"]), T))
    return PhasePolicy(tuple(bands), name=str(doc.get("name") or "sto-policy"), widths=P.widths)


def l2q_count(P: tt.Policy, n: int, delta: float = DEFAULT_DELTA) -> Dict[str, Any]:
    """Engine 2's T for policy P at operand width n."""
    L = to_l2q(P)
    spec = cc.SITE.with_(delta=delta)
    cost = cc.tree_cost(L, n, n, spec, mode=P.mode)
    ladder = [(w, (L.table(t).k if t else None)) for w, t in L.ladder(n)]
    return {"T": spec.price(cost), "t_base": cost.base, "rotations": cost.rot,
            "ts_per_rotation": spec.t_per_rotation(cost.rot) if cost.rot else 0, "ladder": ladder,
            "convention": spec.to_json()}


def cross_check(P: tt.Policy, n: int, delta: float = DEFAULT_DELTA, tcost_score: Dict[str, Any] = None) -> Dict[str, Any]:
    """Run both engines and compare T, the rotation count, the T without rotations and the ladder."""
    from tcost.score import score_policy
    s = tcost_score or score_policy(P, n, delta)
    e2 = l2q_count(P, n, delta)
    t1 = s["t_count"]
    gc = s["gate_counts"]
    base1 = s["t_breakdown"]["t_from_toffoli_class"]
    lad1 = [(r["width"], r["k"]) for r in s["ladder"]]
    checks = {"T": t1 == e2["T"], "rotations": gc["rotation"] == e2["rotations"],
              "t_without_rotations": base1 == e2["t_base"], "ladder": lad1 == e2["ladder"]}
    return {"agree": all(checks.values()), "checks": checks,
            "engine1": {"name": "tcost (symbolic chain bloq)", "T": t1, "rotations": gc["rotation"], "t_base": base1,
                        "ladder": lad1},
            "engine2": {"name": "forshor_qualtran (concrete tree walk)", "T": e2["T"], "rotations": e2["rotations"],
                        "t_base": e2["t_base"], "ladder": e2["ladder"]}}
