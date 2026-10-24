"""Score a recursion policy: compute the Qualtran gate counts of its phase product SYMBOLICALLY in n.

    python -m tcost.score policy.json [--n 2048] [--delta 0.01]

At n, the ladder of the policy sets the table that each recursion level uses (`chain_for`). Qualtran then
costs `SymbolicPhaseProduct(mode, chain)` one time, as a polynomial in the level widths (n, w_1, ..., w_d).
`width_relations` gives each w_{i+1} as a closed form in w_i. The score is that closed form, evaluated at n.
The closed form is exact for each n in `valid_n` (the contiguous range around n where the policy uses the same
chain).

Ranking configuration (`leaderboard/config.json` -> `tcount`): generic angle, n = 2048, delta = 0.01.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Tuple

from . import tables
from .tables import Policy, chain_for

SCORE_SCHEMA = "tcost.score/v3"
DEFAULT_N = 2048


def valid_range(policy: Policy, n: int, span: int = 4) -> Tuple[int, int]:
    """Return the maximal contiguous window [lo, hi] around n, inside [n/span, n*span], where `chain_for` does not
    change. In general, the set of n that share the chain is NOT contiguous: thresholds and floor(w/k) make the
    ladder change back and forth. Thus this function does a linear scan, because a bisection would be unsound."""
    ch = chain_for(policy, n)
    lo = n
    while lo - 1 >= max(2, n // span) and chain_for(policy, lo - 1) == ch:
        lo -= 1
    hi = n
    while hi + 1 <= n * span and chain_for(policy, hi + 1) == ch:
        hi += 1
    return lo, hi


def symbolic_cost(policy: Policy, n: int) -> Dict[str, Any]:
    """Return the closed-form gate counts for the chain that the policy uses at n: {field: sympy expr}, the
    relations and the chain."""
    from .bloqs import SymbolicPhaseProduct, width_relations
    from .counts import symbolic_gate_counts
    chain = chain_for(policy, n)
    return {"chain": chain, "counts": symbolic_gate_counts(SymbolicPhaseProduct(policy.mode, chain)),
            "relations": width_relations(chain)}


def score_policy(policy: Policy, n: int = DEFAULT_N, delta: float | None = None,
                 with_range: bool = False, eps: float | None = None) -> Dict[str, Any]:
    """Return the score at operand width n. This function evaluates the symbolic Qualtran counts at n. Then it
    converts them to T with a total rotation budget `delta` (default 0.01) or a fixed precision `eps` per rotation."""
    import sympy
    from .bloqs import width_values
    from .currency import MODEL_VERSION, t_count
    if not isinstance(n, int) or n < 2:
        raise ValueError(f"operand width n must be an integer >= 2, got {n!r}")
    sym = symbolic_cost(policy, n)
    wv = width_values(sym["chain"], n)
    counts = {k: int(sympy.sympify(v).subs(wv)) for k, v in sym["counts"].items()}
    t = t_count(counts, delta=delta, eps=eps)
    ladder = policy.ladder(n)
    leaves = 1
    for _, k in ladder[:-1]:
        leaves *= tables.point_count(policy.mode, k)
    out = {"schema": SCORE_SCHEMA, "model_version": MODEL_VERSION, "policy": policy.name, "mode": policy.mode, "n": n,
           "t_count": t["t_count"], "gate_counts": counts, "t_breakdown": t,
           "ladder": [{"width": w, "k": k} for w, k in ladder],
           "recursion_depth": len(ladder) - 1, "leaf_calls": leaves, "leaf_width": ladder[-1][0],
           "closed_form": {
               "counts": {k: str(sympy.expand(v)) for k, v in sym["counts"].items()},
               "widths": [f"{a} = {b}" for a, b in sym["relations"]],
               "label": "EXACT (Qualtran call graph; width relations = Lean nextSignedWidth)",
           }}
    if with_range:
        out["closed_form"]["valid_n"] = list(valid_range(policy, n))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tcost.score")
    ap.add_argument("policy", help="policy file (any format `tables.policy_docs` reads); every policy is scored")
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    ap.add_argument("--delta", type=float, default=None)
    a = ap.parse_args(argv)
    if a.n < 2 or (a.delta is not None and not 0 < a.delta < 1):
        ap.error("need --n >= 2 and 0 < --delta < 1")
    try:
        policies = tables.load_policies(a.policy)
    except tables.TableError as e:
        raise SystemExit(f"{a.policy}: {e}")
    scores = [score_policy(p, a.n, a.delta, with_range=True) for p in policies]
    json.dump(scores[0] if len(scores) == 1 else scores, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
