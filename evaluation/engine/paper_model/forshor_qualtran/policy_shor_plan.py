r"""The best banded policy for one native Shor instance, priced exactly as the score prices it.

The objective is `policy_shor_price.price` of a `PolicyShorCore`: the native ``shor`` template's census at
``n`` with every site priced under the candidate policy, all rotations at the whole run's R_total. The
census does not depend on the policy (the template reaches the bands only through the dispatchers), so
one core is built per ``n`` and only its ``policy`` changes.

Search (the same two steps as `policy_shor.plan_reachable` + `policy_shor.refine`, on the native sites):

1. **seed**: the `policy_planner.Planner` per-width optimum at the run's rotation context, read along
   the optimal chains from every width the dispatcher is entered at (each phase-product site's
   ``max(xw, zw)`` and every QFT twiddle's ``(left+1, right+1)``), turned into bands; re-planned at the
   priced R_total until the bands repeat;
2. **refine**: coordinate descent against the exact T (shift a threshold, swap a table, delete a band,
   insert a band), from the seed and from the all-leaf policy. A local optimum, never worse than either
   seed.

ForShor needs at least one band, so an all-leaf optimum is returned as :func:`inert_band`: one band
above every width the run reaches, which never fires and prices identically.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

import attrs

from . import cost_conventions as cc
from . import policy_shor_price as pr
from .policy import PhasePolicy, PolicyBand, ToomCookTable


def entry_widths(core: pr.PolicyShorCore) -> List[int]:
    """Widths at which the dispatcher is entered from outside the phase-product family, widest first."""
    tops = set()
    for (name, a, b), _ in core.sites:
        if name == "qft":
            stack = [a]
            while stack:
                v = stack.pop()
                if v < 2:
                    continue
                left, right = v // 2, v - v // 2
                tops.add(max(left, right) + 1)
                stack += [left, right]
        else:
            tops.add(max(a, b))
    return sorted(tops, reverse=True)


def inert_band(core: pr.PolicyShorCore, table: ToomCookTable) -> PhasePolicy:
    """The all-leaf policy in a form ForShor accepts: one band no site reaches."""
    return PhasePolicy((PolicyBand(entry_widths(core)[0] + 1, table),), name="all-leaf")


class Objective:
    """Exact whole-run T of ``core`` under a policy, cached by bands."""

    def __init__(self, core: pr.PolicyShorCore, spec: cc.ConventionSpec = cc.QUALTRAN):
        self.core, self.spec, self.cache = core, spec, {}

    @staticmethod
    def key(bands) -> Tuple:
        return tuple((b.threshold, b.table.name) for b in bands)

    def priced(self, bands) -> Dict:
        k = self.key(bands)
        if k not in self.cache:
            P = PhasePolicy(tuple(bands), name="candidate")
            self.cache[k] = pr.price(attrs.evolve(self.core, policy=P, convention=self.spec), self.spec)
        return self.cache[k]

    def __call__(self, bands) -> float:
        return self.priced(bands)["T"]


def seed(core: pr.PolicyShorCore, catalog: Sequence[ToomCookTable], obj: Objective,
         max_iter: int = 4) -> List[PolicyBand]:
    from .policy_planner import Planner
    sites = entry_widths(core)
    r_ctx = obj.priced([])["rotations"] or 1
    best, seen = None, set()
    for _ in range(max_iter):
        pl = Planner(catalog, "t", convention=obj.spec, rotation_context=float(r_ctx))
        assign: Dict[int, Optional[str]] = {}
        for w in sites:
            for wi, tn in pl.chain(w):
                assign.setdefault(wi, tn)
        runs: List[Tuple[int, str]] = []
        for w, tn in sorted(((w, tn) for w, tn in assign.items() if tn), reverse=True):
            if runs and runs[-1][1] == tn:
                runs[-1] = (w, tn)
            else:
                runs.append((w, tn))
        bands = [PolicyBand(w, pl.catalog[tn]) for w, tn in runs]
        if best is None or obj(bands) < obj(best):
            best = bands
        key = tuple(runs)
        if key in seen:
            break
        seen.add(key)
        r_ctx = obj.priced(bands)["rotations"] or 1
    return best


def refine_bands(bands: List[PolicyBand], catalog: Sequence[ToomCookTable], price: Callable[[List[PolicyBand]], float],
                 passes: int = 4, steps: Sequence[int] = (1, 2, 4, 8, 16, 32, 64, 128)) -> List[PolicyBand]:
    """Coordinate descent (the moves of `policy_shor.refine`) against ``price``; strict improvements only,
    then drop every band whose removal does not raise the price."""
    def valid(bs) -> bool:
        ts = [b.threshold for b in bs]
        return all(t >= 0 for t in ts) and all(x > y for x, y in zip(ts, ts[1:]))

    cur, best = list(bands), price(list(bands))
    for _ in range(passes):
        improved = False
        cands = []
        for i, b in enumerate(cur):
            for st in steps:
                for sgn in (-1, 1):
                    cands.append(cur[:i] + [PolicyBand(b.threshold + sgn * st, b.table)] + cur[i + 1:])
            cands += [cur[:i] + [PolicyBand(b.threshold, t)] + cur[i + 1:] for t in catalog if t.name != b.table.name]
            cands.append(cur[:i] + cur[i + 1:])
        edges = [10 ** 6] + [b.threshold for b in cur] + [0]
        for i in range(len(edges) - 1):
            mid = (edges[i] + edges[i + 1]) // 2 if i else max(edges[1] * 2, 64)
            cands += [cur[:i] + [PolicyBand(mid, t)] + cur[i:] for t in catalog]
        for nb in cands:
            if valid(nb):
                v = price(nb)
                if v < best * (1 - 1e-12):
                    best, cur, improved = v, nb, True
        if not improved:
            break
    changed = True
    while changed:
        changed = False
        for i in range(len(cur)):
            nb = cur[:i] + cur[i + 1:]
            if valid(nb) and price(nb) <= best:
                cur, best, changed = nb, price(nb), True
                break
    return cur


def best_policy(core: pr.PolicyShorCore, catalog: Sequence[ToomCookTable], spec: cc.ConventionSpec = cc.QUALTRAN,
                passes: int = 3) -> Dict:
    """The best banded policy over ``catalog`` for ``core`` (see the module docstring)."""
    obj = Objective(core, spec)
    starts = {"planner": seed(core, catalog, obj), "all-leaf": []}
    results = {name: refine_bands(b, catalog, obj, passes=passes) for name, b in starts.items()}
    name, bands = min(results.items(), key=lambda kv: obj(kv[1]))
    if not bands:
        P = inert_band(core, min(catalog, key=lambda t: t.k))
    else:
        P = PhasePolicy(tuple(bands), name=f"best_n{core.nbits}")
    return {"policy": P, "from": name, "all_leaf": not bands, **{k: v for k, v in obj.priced(bands).items()
                                                                  if k != "convention"},
            "evaluations": len(obj.cache), "seed_T": obj(starts["planner"]), "all_leaf_T": obj([])}
