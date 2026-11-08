"""The ForShor paper's custom varying-k policy, re-priced under the site's model (PLAN_v2 Stage 12, decision D).

The paper (ForShor_Paper/main-latest.tex, lines 398, 414, 440; NOT edited) names k = (5, 3, 2, 2); the run behind its
printed 2.31e12 T (`paper_full`, `bundles_job(2048, 0.01, widths="r2")`) descends 2107:5 -> 431:5 -> 96:3 -> 36:2 ->
20:leaf, i.e. k = (5, 5, 3, 2). That figure is the retired paper model: GRID leaf (recursion_versions.py
leaf_T = w^2 (4 + tsr)), R2 widths (value-range widths on zero-extended limbs), version C (the cheapest table at every
width, not a policy) and no Step 4.

Tables: the run's own, ForShor's "standard" tables emitted by the Lean binary (REAL-LEAN bundles
`engine/paper_model/artifacts/lean/k{2,3,5}.forshor.json`, `schedule.points` / `schedule.ops`; their points include
+-3 and +-4, so they are ForShor tables, not STO-contract tables). Here each is a banded policy (one table per arity,
thresholds strictly decreasing) priced by the site's ONE full-run pricer (`fullalg`: ForShor native structure, Step 4
included, scan widths, Lean work width 2347, Qualtran digital-product leaf, Gidney adders, eps = 0.5 delta / R_total).

Pricing (the owner's final settings): ForShor's emitted IR priced exactly, Lean -> IR -> dictionary -> Qualtran bloqs:
scanned widths, m2048 (298 extra work bits, work 2347), ForShor's Step 4 cmpLtNW, 4095 modmuls, the dictionary's Qualtran
costing (digital-product leaf, Gidney adders, Ross-Selinger rotations). That is `fullalg`, the full board's pricer.

Bands. Under scan widths the top product (work + 1) x (data + 2) = 2348 x 2050 descends differently from R2, so the
thresholds are chosen to give the named k sequence on that product's ladder (the paper's thresholds 431/96/36 give
(5, 5, 3, 3, 2) under scan widths; priced too, as `paper_thresholds`).
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from . import DEFAULT_DELTA
from . import fullalg as fa

BUNDLES = fa.ENGINE / "paper_model" / "artifacts" / "lean"
#: name -> (bands [(threshold, k)], what it is)
POLICIES: Dict[str, Tuple[List[Tuple[int, int]], str]] = {
    "k5532": ([(798, 5), (252, 3), (101, 2)],
              "k = (5, 5, 3, 2), the descent of the run behind the printed 2.31e12, as a banded policy"),
    "k5322": ([(799, 5), (798, 3), (147, 2)],
              "k = (5, 3, 2, 2), the sequence the paper's text names, with the same tables"),
    "paper_thresholds": ([(431, 5), (96, 3), (36, 2)],
                         "the R2 ladder's widths (431, 96, 36) used as thresholds; under scan widths this gives "
                         "k = (5, 5, 3, 3, 2)"),
    # the owner's reconciliation (2026-10-28), for comparison, same tables and pricer
    "replanned_best": ([(4399, 2), (2391, 4), (1387, 2), (384, 3), (112, 2)],
                       "the re-planned best banded policy over the same tables (owner's reconciliation: 3.31993e12)"),
    "constant_k2": ([(112, 2)], "the best constant k = 2 policy (std-k2 from 112; owner's reconciliation: 3.77364e12)"),
    # Stage 18 (2026-10-28): re-planned on the board's IR (ForShor drop-in: exact widths + balanced split), the numbers the
    # paper quotes at n = 2048 (site planner `policy_shor_plan.best_policy`, passes = 3, over std k2..7 / one table)
    "replanned_best_exact": ([(2348, 6), (2200, 7), (2049, 6), (1101, 5), (1026, 6), (551, 5), (513, 4), (405, 5), (332, 4), (294, 3), (276, 4), (258, 3), (244, 4), (230, 5), (214, 3), (184, 4), (102, 3), (95, 2), (90, 3), (71, 2)],
                             "the best banded policy over std k2..7 on the drop-in IR (exact widths + balanced split)"),
    "constant_k4_exact": ([(129, 4)], "the best constant-k policy on the drop-in IR: std-k4 (k = 4 is the best constant arity)"),
}


@lru_cache(maxsize=8)
def table(k: int):
    from l2q_full import policy as lpol
    d = json.loads((BUNDLES / f"k{k}.forshor.json").read_text())
    if d.get("_provenance") != "REAL-LEAN":
        raise ValueError(f"k{k} bundle is not REAL-LEAN")
    return lpol.ToomCookTable.from_json(f"std-k{k}", {"k": k, "points": d["schedule"]["points"],
                                                      "ops": d["schedule"]["ops"]})


def policy(name: str):
    from l2q_full import policy as lpol
    bands, _ = POLICIES[name]
    return lpol.PhasePolicy(tuple(lpol.PolicyBand(t, table(k)) for t, k in bands), name=f"paper-{name}")


def price(name: str, delta: float = DEFAULT_DELTA, rule: Optional[str] = None) -> Dict[str, Any]:
    """The policy on the board's IR and width rule (Stage 18: the ForShor drop-in, exact widths + balanced split);
    ``rule="scan"``: upstream ForShor's IR at scanned widths (the Stage 12 figures)."""
    from l2q_full import policy as lpol
    rule = fa.width_rule() if rule is None else rule
    LP = lpol.with_width_rule(policy(name), rule)
    r = fa.price_l2q(LP, fa.HERO_N, delta, detail=False, rule=rule)
    w = r["widths"]
    tops = {"Step 1/5 (data+1 x work+1)": (w["yW"] + 1, w["workW"] + 1),
            "Step 2 (work+1 x data+2)": (w["workW"] + 1, w["yW"] + 2),
            "Step 4 (work+1 x scratch+1)": (w["workW"] + 1, w["scratchW"] + 1)}
    lad = {k: " -> ".join(f"{x['W']}:{x['k'] or 'leaf'}" for x in fa._ladder(LP, a, b)) for k, (a, b) in tops.items()}
    pts = {k: [str(v) if kd == "int" else ("inf" if v == 0 else f"1/2^{v}") for kd, v in table(k).points]
           for k in sorted({k for _, k in POLICIES[name][0]})}
    return {"name": name, "what": POLICIES[name][1], "bands": POLICIES[name][0], "n": fa.HERO_N, "delta": delta,
            "T": r["T"], "qubits": r["qubits"], "rotations": r["rotations"], "T_base": r["T_base"],
            "T_per_rotation": r["T_per_rotation"], "lean_grid_leaf_T": r["lean_grid_leaf_T"],
            "literal_lean_T": r["literal_lean_T"], "work_width": r["work_width"], "widths": w, "ladders": lad,
            "tables": {f"std-k{k}": {"source": f"engine/paper_model/artifacts/lean/k{k}.forshor.json (REAL-LEAN)",
                                     "points": p} for k, p in pts.items()},
            "tier": fa.TIER, "step4": True, "leaf": "Qualtran digital product",
            "widths_rule": ("exact (ForShor drop-in nextSignedWidth = exactSignedWidthOfWidths), balanced split"
                            if rule == "exact" else "scan (each table's nextSignedWidth, the Lean width scan)"),
            "model": r.get("model", fa.MODEL)}


def report(delta: float = DEFAULT_DELTA, rule: Optional[str] = None) -> Dict[str, Any]:
    return {name: price(name, delta, rule) for name in POLICIES}


if __name__ == "__main__":
    print(json.dumps(report(), indent=1))
