"""Find the cheapest policy that a set of verified tables can build (a reference policy or a submission seed).

    best(w) = min( leaf(w),  min over tables t with W' = t.next_width(w) < w of  own_t(w, W') + q_t * best(W') )

The costs are symbolic. For each table, Qualtran costs ONE level of `SymbolicPhaseProduct` one time. This gives
the gate counts of the node itself (without the children) as a closed form in (w, W'). The leaf gets a closed
form in w in the same way. The planner compiles these forms to integer-exact functions, so the dynamic program
uses only integer arithmetic.

* **Objective.** T from `tcost.currency.t_from_counts` at a T per rotation. The T per rotation is fixed
  (`fixed_tpr`, for example from an eps per rotation), or the planner iterates it to the fixed point of
  ts_per_rotation(delta / R).
* **Determinism.** The planner visits the candidates in canonical order. It breaks ties explicitly by
  (T, clifford, cnot, table digest). Thus the result does not depend on PYTHONHASHSEED.
* **One table per (mode, k).** A `Recursion.lean` band names only k, so a policy can use only one table for each
  k. The unconstrained DP can select different tables with the same k at different levels. If it does, the
  planner runs the DP again over each combination of the variants of the conflicting k values (bounded). The
  cheapest admissible policy wins.

The visited widths strictly decrease. Thus a `Policy` can always express the choices for each node: one band for
each recursive node, at threshold = the width of that node. (ForShor `choose` selects the first band with
threshold <= w, and the leaf width is below all thresholds.)
"""
from __future__ import annotations

import itertools
from functools import lru_cache
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .currency import t_from_counts, ts_per_rotation
from .tables import Band, Policy, Table, point_count

KEYS = ("toffoli", "and", "cswap", "t", "rotation", "clifford", "measurement", "cnot")
MAX_VARIANT_COMBOS = 256


def _compile(exprs: Dict[str, object], symbols) -> Callable[..., Dict[str, int]]:
    """Compile closed forms to integer-exact Python. The printer writes floor(a/b) as a // b (no float rounding)."""
    import sympy
    from sympy.printing.pycode import PythonCodePrinter

    class _IntPrinter(PythonCodePrinter):
        def _print_floor(self, e):
            num, den = sympy.fraction(sympy.together(e.args[0]))
            return f"(({self._print(num)}) // ({self._print(den)}))"

    f = sympy.lambdify(symbols, [sympy.sympify(exprs[k]) for k in KEYS], printer=_IntPrinter)
    return lambda *args: dict(zip(KEYS, (int(v) for v in f(*args))))


@lru_cache(maxsize=4096)
def node_form(table: Table) -> Callable[[int, int], Dict[str, int]]:
    """own_t(w, W'): the gate counts of one level without its q children, compiled from the Qualtran closed form."""
    from qualtran.resource_counting import SympySymbolAllocator
    from .bloqs import ChainPhaseProduct, SymbolicPhaseProduct, width_symbol
    from .counts import symbolic_gate_counts
    node = SymbolicPhaseProduct(table.mode, (table,), 0)
    tot = {k: 0 for k in KEYS}
    for callee, n in node.build_call_graph(SympySymbolAllocator()).items():
        if isinstance(callee, ChainPhaseProduct):
            continue
        c = symbolic_gate_counts(callee)
        tot = {k: tot[k] + n * c[k] for k in KEYS}
    return _compile(tot, [width_symbol(0), width_symbol(1)])


@lru_cache(maxsize=8)
def leaf_form(mode: str) -> Callable[[int], Dict[str, int]]:
    from .bloqs import SymbolicPhaseProduct, width_symbol
    from .counts import symbolic_gate_counts
    return _compile(symbolic_gate_counts(SymbolicPhaseProduct(mode, (), 0)), [width_symbol(0)])


def own_counts(table: Table, w: int) -> Dict[str, int]:
    return node_form(table)(w, table.next_width(w))


def leaf_counts(mode: str, w: int) -> Dict[str, int]:
    return leaf_form(mode)(w)


def _dp(cands: Sequence[Table], mode: str, n: int, tpr: int):
    """Run the unconstrained DP at a fixed T per rotation. Return the memo: w -> (key, table|None, counts)."""
    memo: Dict[int, tuple] = {}
    own_memo: Dict[tuple, tuple] = {}

    def key(c, t):
        return (t_from_counts(c, tpr), c["clifford"], c["cnot"], t.digest if t is not None else "")

    def best(w: int):
        if w in memo:
            return memo[w]
        lc = leaf_counts(mode, w)
        choice = (key(lc, None), None, lc)
        for t in cands:
            ok = own_memo.get((t.digest, w))
            if ok is None:
                w2 = t.next_width(w)
                ok = own_memo[(t.digest, w)] = (w2, node_form(t)(w, w2) if w2 < w else None)
            w2, own = ok
            if own is None:
                continue
            sub = best(w2)
            q = point_count(mode, t.k)
            c = {k: own[k] + q * sub[2][k] for k in KEYS}
            kc = key(c, t)
            if kc < choice[0]:
                choice = (kc, t, c)
        memo[w] = choice
        return choice

    best(n)
    return memo


def _extract(memo, n: int) -> List[Band]:
    bands, w = [], n
    while memo[w][1] is not None:
        t = memo[w][1]
        bands.append(Band(w, t))
        w = t.next_width(w)
    return bands


def _solve(cands: Sequence[Table], mode: str, n: int, delta: float, fixed_tpr: Optional[int]):
    """Run the DP with the fixed point of the T per rotation (or with a fixed value). Return (bands, counts)."""
    tpr, seen, last = fixed_tpr or ts_per_rotation(delta, n * n), set(), None
    while True:
        memo = _dp(cands, mode, n, tpr)
        counts = memo[n][2]
        result = (_extract(memo, n), counts, t_from_counts(counts, tpr))
        if fixed_tpr is not None:
            return result[:2]
        new_tpr = ts_per_rotation(delta, counts["rotation"])
        if new_tpr == tpr:
            return result[:2]
        if new_tpr in seen:                    # a cycle: keep the cheapest policy in the cycle
            return min((result, last), key=lambda r: t_from_counts(r[1], ts_per_rotation(delta, r[1]["rotation"])))[:2]
        seen.add(tpr)
        tpr, last = new_tpr, result


def optimal_policy(tables: Iterable[Table], mode: str, n: int = 2048, delta: float = 0.01,
                   name: str = "archive-optimal", fixed_tpr: Optional[int] = None,
                   one_table_per_k: bool = True) -> Tuple[Policy, Dict[str, int]]:
    cands = sorted({t for t in tables if t.mode == mode}, key=lambda t: (t.k, t.digest))
    bands, counts = _solve(cands, mode, n, delta, fixed_tpr)
    if one_table_per_k:
        used: Dict[int, set] = {}
        for b in bands:
            used.setdefault(b.table.k, set()).add(b.table)
        conflicts = sorted(k for k, ts in used.items() if len(ts) > 1)
        if conflicts:
            variants = [sorted((t for t in cands if t.k == k), key=lambda t: t.digest) for k in conflicts]
            combos = list(itertools.islice(itertools.product(*variants), MAX_VARIANT_COMBOS))
            best = None
            for combo in combos:
                pick = dict(zip(conflicts, combo))
                pool = [t for t in cands if t.k not in pick or t is pick[t.k]]
                b, c = _solve(pool, mode, n, delta, fixed_tpr)
                tpr = fixed_tpr or ts_per_rotation(delta, c["rotation"])
                k_ = (t_from_counts(c, tpr), c["clifford"], c["cnot"], tuple(t.digest for t in combo))
                if best is None or k_ < best[0]:
                    best = (k_, b, c)
            _, bands, counts = best
    return Policy(mode=mode, bands=tuple(bands), name=name), counts
