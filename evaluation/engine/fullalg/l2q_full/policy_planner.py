r"""Optimal width-indexed policies over a catalog of Toom-Cook tables.

The recursion `PolicyPhaseProduct` prices is a tree whose every node at width `w` either takes a
leaf or applies one table `T` (when `T` shrinks the width, `next_T(w) < w`), paying that level's
arithmetic and recursing into `q_T` children of width `next_T(w)`. Children are always square
(`targetSignedLayoutState` pads every slot to one width), so for square operands the optimum is a
DP over widths alone:

    best(w) = min( leaf(w),  min_{T : next_T(w) < w}  level_T(w) + mult_T · best(next_T(w)) )

with `mult_T = q_T` for gate costs and `mult_T = k_T` for the reserve (workspace) cost, which
recurses as `Policy.reserveNeed` does: `immediate_T(w) + k_T · need(child)`.

Objectives:

* ``"t"`` — Clifford+T T-count under a :class:`~forshor_qualtran.cost_conventions.ConventionSpec`
  (default :data:`~forshor_qualtran.cost_conventions.QUALTRAN`, the headline tier; the
  cross-check tier is ``LEAN_FAITHFUL``). Leaf realisation (grid / merged CPhase / product) and
  adder model come from the spec; with ``leaf="best"`` the realisation is chosen inside the DP.
  T = rotation-free T + R · t_rot(delta_rot / R) is **not** additive over the tree (the
  per-rotation price depends on the whole tree's R), so each width keeps the Pareto front of
  `(T_base, R)` over every recursion and leaf realisation below it; T increases in both, so the
  optimum is on the front, and every front point is priced exactly.
* ``"gates"`` — ForShor's `LowGate.resources` gate total (`h + x + cnot + toffoli + rz`) of the
  literal Lean lowering, the quantity `PolicyPhaseProduct.lean_resources()` predicts.
* ``"reserve"`` — `Policy.reserveNeed` x + z.

What ForShor accepts is a **banded** policy: strictly decreasing thresholds, the table chosen by
the first band whose threshold is `≤ w`. For one target `n` only the widths the recursion visits
matter, and they form a strictly decreasing chain, so any per-level assignment is expressible as
bands; a table may appear in several bands (ForShor and `PhasePolicy` both allow that).
:func:`best_banded` also solves the variant in which each table is used in one contiguous run.
The leaf *realisation* is not part of a ForShor policy: it is how this package prices (and would
build) the leaves of the circuit the policy describes, and is recorded beside the policy.

CAVEAT: logical counts of this construction; no error correction, no layout.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import cost_conventions as cc, schemas
from .cost_conventions import TCost  # noqa: F401  (re-exported)
from .policy import PhasePolicy, PolicyBand, ToomCookTable, q

OBJECTIVES = ("t", "gates", "reserve")


def default_budget():
    """`RotationBudget()` — delta = 0.01, half to rotation synthesis (the package's figure default)."""
    return cc.QUALTRAN.budget


def _cuccaro_total(w: int) -> int:
    """`cuccaroModAddResources w` gate total (x + cnot + toffoli), ℕ-truncated."""
    return sum(cc.cuccaro_resources(w).values())


def leaf_cost(objective: str, w: int, leaf: str = "grid"):
    """A square leaf's cost: an `int` for ``gates``/``reserve``, a `TCost` for ``t`` under the
    given concrete realisation."""
    if objective == "t":
        return cc.leaf_cost(w, w, leaf)
    if objective == "gates":
        return 5 * w * w                       # LowGate.CPhase: 3 Phase + 2 CNOT per bit pair
    if objective == "reserve":
        return 0
    raise ValueError(objective)


def level_cost(objective: str, T: ToomCookTable, w: int, w2: int, spec: cc.ConventionSpec = cc.QUALTRAN):
    """One node's own cost at width `w` using `T` (child width `w2`), children excluded."""
    if objective == "t":
        return cc.level_cost(cc.census_key(T), w2, spec)
    if objective == "gates":
        cen = T.census()
        adds, negs = 2 * cen["addScaled"], 2 * cen["negate"]
        top = w - (T.k - 1) * (w // T.k)
        sign = 2 * 2 * max(0, w2 - top)        # signExtend + signDealloc, both sides
        return adds * _cuccaro_total(w2) + negs * sum(cc.negate_resources(w2).values()) + sign
    if objective == "reserve":
        return T.immediate_need("x", w, w) + T.immediate_need("z", w, w)
    raise ValueError(objective)


def multiplier(objective: str, T: ToomCookTable) -> int:
    return T.k if objective == "reserve" else q(T.k)


# ============================================================================ per-width DP
@dataclass(frozen=True)
class Choice:
    cost: float                   # the objective's value (T under the spec for "t")
    table: Optional[str]          # None = leaf
    child: Optional[int]          # child width when table is not None
    raw: object = None            # the TCost (``t``) or int behind ``cost``
    idx: int = 0                  # index of the child's front entry this choice uses
    leaf: Optional[str] = None    # leaf realisation (``t``, leaf entries only)


def _prune(entries, scalar: bool):
    """Pareto front over `(base, rot)` for TCost (both lower-is-better), or the minimum for ints.
    Entries are tuples whose first element is the raw cost."""
    if scalar:
        return [min(entries, key=lambda e: e[0])]
    entries = sorted(entries, key=lambda e: (e[0].rot, e[0].base))
    out, best_base = [], None
    for e in entries:
        if best_base is None or e[0].base < best_base:
            out.append(e)
            best_base = e[0].base
    return out


class Planner:
    """Exact optimum over a catalog (square operands); see the module docstring."""

    def __init__(self, catalog: Sequence[ToomCookTable], objective: str = "t",
                 convention: Optional[cc.ConventionSpec] = None, budget=None,
                 mix: Optional[float] = None, rotation_context: Optional[float] = None):
        if objective not in OBJECTIVES:
            raise ValueError(f"objective must be one of {OBJECTIVES}")
        names = [t.name for t in catalog]
        if len(set(names)) != len(names):
            raise ValueError(f"catalog names must be distinct: {names}")
        self.catalog = {t.name: t for t in catalog}
        self.objective = objective
        self.scalar = objective != "t"
        spec = convention or cc.QUALTRAN
        if budget is not None:
            spec = spec.with_(delta=budget.delta, rotation_fraction=budget.rotation_fraction)
        if mix is not None:
            spec = spec.with_(mix=mix)
        if rotation_context is not None:
            spec = spec.with_(r_context=rotation_context)
        self.spec = spec
        # front entries: (raw, table, child width, child entry index, leaf realisation)
        self._front: Dict[int, list] = {}

    @property
    def budget(self):
        return self.spec.budget

    def value(self, raw) -> float:
        """The objective value of a raw cost (an int, or a TCost priced under the spec)."""
        return raw if self.scalar else self.spec.price(raw)

    def options(self, w: int) -> List[Tuple[str, int]]:
        """`(table, child width)` for every table that shrinks `w`."""
        out = []
        for name, T in self.catalog.items():
            w2 = T.next_width(w, w)
            if w2 < w:
                out.append((name, w2))
        return out

    def leaf_entries(self, w: int):
        if self.scalar:
            return [(leaf_cost(self.objective, w), None, None, 0, None)]
        return [(cc.leaf_cost(w, w, r), None, None, 0, r) for r in self.spec.leaves]

    def _level(self, T: ToomCookTable, w: int, w2: int):
        return level_cost(self.objective, T, w, w2, self.spec)

    def node_raw(self, name: Optional[str], w: int, child_raw=None, leaf: Optional[str] = None):
        if name is None:
            if self.scalar:
                return leaf_cost(self.objective, w)
            return cc.leaf_cost(w, w, leaf or self.spec.leaves[0])
        T = self.catalog[name]
        w2 = T.next_width(w, w)
        lvl, m = self._level(T, w, w2), multiplier(self.objective, T)
        return lvl + (child_raw.scale(m) if not self.scalar else m * child_raw)

    def node_cost(self, name: Optional[str], w: int, child_cost=0):
        return self.node_raw(name, w, child_cost)

    def front(self, w: int) -> list:
        """`[(raw cost, table, child width, child entry index, leaf), …]`, memoised on the widths the
        recursion from `w` actually reaches (a width's front depends only on its children's fronts, so
        this equals the bottom-up fill over 1..w; at n = 50000 it visits a few hundred widths, not 50000)."""
        if w in self._front:
            return self._front[w]
        stack = [w]                                   # iterative post-order: no Python recursion depth
        while stack:
            v = stack[-1]
            if v in self._front:
                stack.pop()
                continue
            opts = self.options(v)
            missing = [v2 for _, v2 in opts if v2 not in self._front]
            if missing:
                stack.extend(missing)
                continue
            entries = list(self.leaf_entries(v))
            for name, v2 in opts:
                for i, e in enumerate(self._front[v2]):
                    entries.append((self.node_raw(name, v, e[0]), name, v2, i, None))
            self._front[v] = _prune(entries, self.scalar)
            stack.pop()
        return self._front[w]

    def _best_entry(self, w: int):
        return min(self.front(w), key=lambda e: self.value(e[0]))

    def best(self, w: int) -> Choice:
        raw, table, child, idx, leaf = self._best_entry(w)
        return Choice(self.value(raw), table, child, raw, idx, leaf)

    def chain_full(self, n: int) -> Tuple[List[Tuple[int, Optional[str]]], Optional[str]]:
        """The optimal recursion from `n` and its leaf realisation."""
        e = self._best_entry(n)
        out, w = [], n
        while True:
            out.append((w, e[1]))
            if e[1] is None:
                return out, e[4]
            w, e = e[2], self._front[e[2]][e[3]]

    def chain(self, n: int) -> List[Tuple[int, Optional[str]]]:
        """The optimal recursion from `n`: `[(width, table), …, (leaf width, None)]`."""
        return self.chain_full(n)[0]

    def leaf_choice(self, n: int) -> Optional[str]:
        return self.chain_full(n)[1]

    def choice_map(self, w_max: int) -> List[Optional[str]]:
        """The per-width optimal top-level choice for w = 1..w_max (index 0 is width 1)."""
        return [self.best(w).table for w in range(1, w_max + 1)]

    def max_front(self, w_max: int) -> int:
        self.front(w_max)
        return max(len(self.front(w)) for w in range(1, w_max + 1))

    # ---- explicit chains / policies (leaf realisation: given, or the spec's best for that chain)
    def chain_raw(self, chain: Sequence[Tuple[int, Optional[str]]], tables=None, leaf: Optional[str] = None):
        if not self.scalar and leaf is None:
            return min((self.chain_raw(chain, tables, r) for r in self.spec.leaves), key=self.value)
        tables = tables or self.catalog
        raw = None
        for w, name in reversed(chain):
            if name is None:
                raw = leaf_cost(self.objective, w) if self.scalar else cc.leaf_cost(w, w, leaf)
            else:
                T = tables[name] if isinstance(tables, dict) else tables(name)
                w2 = T.next_width(w, w)
                lvl, m = self._level(T, w, w2), multiplier(self.objective, T)
                raw = lvl + (raw.scale(m) if not self.scalar else m * raw)
        return raw

    def chain_cost(self, chain: Sequence[Tuple[int, Optional[str]]], leaf: Optional[str] = None) -> float:
        """Cost of an explicit chain `[(w0, T0), (w1, T1), …, (wd, None)]`."""
        return self.value(self.chain_raw(chain, leaf=leaf))

    def single_table_cost(self, name: str, n: int) -> float:
        """Cost of the constant policy (`constPolicy T`, no leaf cutoff) at `n`."""
        return self.chain_cost([(w, t) for w, t in PhasePolicy.const(self.catalog[name]).ladder(n)])

    def policy_raw(self, P: PhasePolicy, n: int, leaf: Optional[str] = None):
        return self.chain_raw(P.ladder(n), P.table, leaf)

    def policy_cost(self, P: PhasePolicy, n: int, leaf: Optional[str] = None) -> float:
        """Cost of any banded policy at `n` (tables resolved by name in `P`)."""
        return self.value(self.policy_raw(P, n, leaf))

    def schoolbook_cost(self, n: int) -> float:
        """No recursion at all: the best leaf realisation at width `n`."""
        return self.chain_cost([(n, None)])

    def best_one_band(self, n: int) -> Tuple[float, str, List[Tuple[int, Optional[str]]]]:
        """The best single table *with* a leaf cutoff (a one-band policy) — the fair single-k
        comparator for a varying-k policy."""
        best = None
        for T in self.catalog.values():
            sub = Planner([T], self.objective, self.spec)
            c = sub.best(n).cost
            if best is None or c < best[0]:
                best = (c, T.name, sub.chain(n))
        return best


# ============================================================================ banded policies
def chain_to_policy(chain: Sequence[Tuple[int, Optional[str]]], catalog: Dict[str, ToomCookTable],
                    name: str = "planned") -> PhasePolicy:
    """Bands from a chain: consecutive levels using the same table share a band (threshold = the
    lowest width of the run). `PhasePolicy` lets one table serve several bands, as ForShor does."""
    runs: List[Tuple[str, int]] = []
    for w, t in chain:
        if t is None:
            break
        if runs and runs[-1][0] == t:
            runs[-1] = (t, w)
        else:
            runs.append((t, w))
    return PhasePolicy(tuple(PolicyBand(thr, catalog[t]) for t, thr in runs), name=name)


def best_banded(planner: Planner, n: int, distinct: bool = False) -> Tuple[float, List[Tuple[int, Optional[str]]]]:
    """Best banded policy at `n`. With ``distinct=False`` it is the per-width optimum (any chain
    is bandable). With ``distinct=True`` each table may be used in one contiguous run only: DP over
    `(width, current table, used set)`, keeping Pareto fronts for ``t`` as :class:`Planner` does."""
    if not distinct:
        return planner.best(n).cost, planner.chain(n)
    names = sorted(planner.catalog)
    idx = {nm: i for i, nm in enumerate(names)}

    @lru_cache(maxsize=None)
    def f(w: int, cur: Optional[str], used: int):
        entries = [(e[0], ((w, None),)) for e in planner.leaf_entries(w)]
        for name, w2 in planner.options(w):
            if name != cur and used >> idx[name] & 1:
                continue                                   # that table's run has already ended
            for sub_raw, sub_chain in f(w2, name, used | (1 << idx[name])):
                entries.append((planner.node_raw(name, w, sub_raw), ((w, name),) + sub_chain))
        pr = _prune(entries, planner.scalar)
        return tuple((e[0], e[1]) for e in pr)

    raw, chain = min(f(n, None, 0), key=lambda e: planner.value(e[0]))
    return planner.value(raw), list(chain)


def regret(planner: Planner, P: PhasePolicy, ns: Sequence[int]) -> Dict[int, float]:
    """`cost_P(n) / best(n) - 1` for each `n`: how far one fixed policy is from each n's optimum."""
    return {n: planner.policy_cost(P, n) / planner.best(n).cost - 1.0 for n in ns}


def fit_universal(planner: Planner, ns: Sequence[int], tables: Sequence[str],
                  start: Optional[Sequence[int]] = None, passes: int = 4,
                  w_max: Optional[int] = None) -> Tuple[PhasePolicy, Dict[int, float]]:
    """One banded policy, tables in the given widest-first order, whose thresholds minimise the
    mean regret over `ns`. Coordinate descent over each threshold in turn (exhaustive over every
    width it may take given its neighbours), so it is a local optimum — the regret it reports is
    exact. ``start`` defaults to evenly spaced thresholds."""
    w_max = w_max or max(ns)
    m = len(tables)
    th = list(start) if start else [w_max * (m - i) // (m + 1) for i in range(m)]
    cat = planner.catalog

    def build(ts):
        return PhasePolicy(tuple(PolicyBand(t, cat[nm]) for t, nm in zip(ts, tables)), name="universal")

    def score(ts):
        r = regret(planner, build(ts), ns)
        return sum(r.values()) / len(r)

    best = score(th)
    for _ in range(passes):
        improved = False
        for i in range(m):
            hi = th[i - 1] - 1 if i else w_max
            lo = th[i + 1] + 1 if i + 1 < m else 1
            for cand in range(lo, hi + 1):
                if cand == th[i]:
                    continue
                trial = th[:i] + [cand] + th[i + 1:]
                s = score(trial)
                if s < best - 1e-12:
                    best, th, improved = s, trial, True
        if not improved:
            break
    P = build(th)
    return P, regret(planner, P, ns)


def policy_manifest(P: PhasePolicy, *, n: int, objective: str, cost: float, note: str = "",
                    budget=None, convention: Optional[cc.ConventionSpec] = None,
                    leaf: Optional[str] = None) -> Dict:
    """A `forshor_qualtran.policy/v1` manifest. The `ir` of each band is a placeholder naming the
    `forshor.ir/v1` document ForShor must extract for that table before `policy.compose` can run."""
    j = P.to_json()
    for b in j["bands"]:
        base = b["table"]["name"].split("#")[0]
        b["ir"] = f"<TODO: forshor.ir/v1 for table {base} (k={b['table']['k']}): " \
                  f"`forshor_emit template {b['table']['k']}` if standard, else extract_ir_doc>"
    j["planner"] = {"target_n": n, "objective": objective, "predicted_cost": cost,
                    "convention": None if objective != "t" else _spec_for(convention, budget).to_json(),
                    "leaf_realisation": leaf,
                    "caveat": "logical counts of this construction; not a fault-tolerant estimate",
                    "note": note}
    assert j["schema"] == schemas.POLICY
    return j


def _spec_for(convention, budget) -> cc.ConventionSpec:
    spec = convention or cc.QUALTRAN
    if budget is not None:
        spec = spec.with_(delta=budget.delta, rotation_fraction=budget.rotation_fraction)
    return spec


# ============================================================================ catalog
def load_catalog(paths: Iterable[Path]) -> List[ToomCookTable]:
    """Tables from `{"name", "k", "points", "ops"}` JSON files, checked and de-duplicated
    (identical `(k, points, ops)` keep the first name)."""
    out, seen = [], {}
    for p in paths:
        j = json.loads(Path(p).read_text())
        T = ToomCookTable.from_json(j["name"], j)
        T.check()
        key = (T.k, T.points, T.ops_frozen)
        if key in seen:
            continue
        seen[key] = T.name
        out.append(T)
    return out
