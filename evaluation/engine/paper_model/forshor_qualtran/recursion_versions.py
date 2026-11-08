"""Recursive PhaseProduct — arity versions, width/stop rules, and the slope diagnosis (2026-09-02).

WHY THIS MODULE EXISTS. V11 drew the Lean plan (`standardSignedPhaseLoweringPlan`) at full depth and its
per-prefix cutoff; neither showed the KMY asymptotics T ∝ n^{log_k(2k−1)} with the exponent DECREASING in k.
The cause is not the Qualtran counting; it is the recursion RULE that was costed:

  (i)   STOP RULE.  The plan recurses while nextSignedWidth(W) < W (`signedBase` carries ¬(next < W)).
        With β_k bits of width overhead per level the tail levels shrink W by ~1 bit each — k=4, n=4096:
        ladder 4096 → 1044 → 281 → 91 → 45 → 32 → 28 → 27 — while EVERY level multiplies the leaves by
        q = 2k−1.  The tail is a constant factor q^{tail}·(W_d/2)² ≈ 10^5 and the depth increments once per
        factor k in n, so T(n) is a staircase with mean slope log_k q but riser height log q.
  (ii)  LIMBS.  ForShor splits with floor limbs, the top limb absorbing the remainder
        (`phaseSplitLogicalWidth`): for w ≢ 0 (mod k) the top limb is up to k−1 bits wider, so next(w)
        is non-monotone (k=4: next(7) = 8 > 7) and the tail in (i) is longer.
  (iii) WIDTH.  The symbolic policy adds one bit per addScaled/negate (β_k ≈ A_k: 3, 12, 20, 38, 61, …),
        so the shrink ratio at practical n is far from k (k=6: 4096 → 746 → 186 → 92).  The tight
        (reachable-value) policy γ_k = 2, 4, 5, 9, 12, … is the KMY magnitude bound.

THE CORRECTION (rule R2 = "kmy"): tight width, operands zero-extended to a multiple of the level's k
(`AGate.zeroExtend`, the step `standardPhaseProdUsingPlan` already takes), and a COST-OPTIMAL stop
(a level is taken iff it is cheaper than the leaf grid).  Under R2 the fitted exponents on n ∈ [2^12, 2^15]
are 1.58, 1.45, 1.38, 1.35, 1.35 for k = 2..6 (theory log_k q = 1.585, 1.465, 1.404, 1.365, 1.338).

VERSIONS (which arity is used where):
  A  top-k / Karatsuba below : k at the root, k = 2 at every deeper level (the KMY skeleton of the measured
                               sweep).  Its exponent is log_2 3 for every k — the top split is a constant factor.
  B  uniform k               : k at every level — the KMY table exponent log_k(2k−1).
  C  per-level optimal k     : min over k ∈ K at every node (KMY Appendix: "different k throughout the recursion").
RULES:
  R0 lean : symbolic width, floor limbs, recurse while next < W       — the proved plan (uniform k only is ForShor's)
  R1 sym  : symbolic width, padded limbs, cost-optimal stop
  R2 kmy  : tight width, padded limbs, cost-optimal stop              — the correction
NONE of R1/R2, and none of version A/C, is a `PhaseLoweringPlan` ForShor proves (single k per plan; `signedBase`
needs ¬(next<W)); they are priced with the same Lean-emitted schedule (q_k, A_k), the same Lean-emitted width
tables (E3, both policies) and the same gadget costs as everything else.  Cost model per node (T, 11 T/rotation):
  leaf  : 15·W²         (W×W CZPow grid: And + rotation)      body : (A_add + A_neg)·4·(W'−1)  (Add/Negate(QInt W'))
`ToomRecursivePhaseProduct` is the Qualtran bloq whose call graph follows the rule; `t_model` is the same
recursion in closed form; `check_model_equals_qualtran` asserts they agree (printed in the notebook).
"""
from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import attrs
import numpy as np
from qualtran import Bloq, QInt, QUInt, Register, Signature
from qualtran.bloqs.arithmetic import Add, Negate
from qualtran.bloqs.basic_gates import CZPowGate
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from . import constants
from . import bundle_compat, widths
from .emit import Bundle

RULES: Tuple[str, ...] = ("lean", "sym", "kmy")
VERSIONS: Tuple[str, ...] = ("A", "B", "C")
RULE_DESC = {
    "lean": "R0 Lean plan: symbolic width (nextSignedWidth), floor limbs, recurse while next(W) < W",
    "sym": "R1: symbolic width, operands zero-extended to a multiple of k, cost-optimal stop",
    "kmy": "R2 (correction): tight width (reachableFits), zero-extended to a multiple of k, cost-optimal stop",
}
VERSION_DESC = {"A": "top level k, every deeper level k = 2 (Karatsuba)", "B": "k at every level",
                "C": "per-level optimal k (min over the loaded arities at every node)"}


def theory_exponent(k: int) -> float:
    return math.log(2 * k - 1, k)


# ------------------------------------------------------------------ per-k data from the bundles
@dataclass(frozen=True)
class ArityData:
    k: int
    q: int
    n_add: int
    n_neg: int
    sym_uniform: Dict[int, int]     # m -> W_sym   (E3 uniform table, n = k·m)
    tight_uniform: Dict[int, int]   # m -> W_tight
    sym_general: Dict[int, int]     # w -> W_sym   (E3 general table, floor limbs)
    tight_general: Dict[int, int]
    beta_sym: int
    beta_tight: int
    from_sym: int
    from_tight: int
    n_add_negsrc: int = 0        # addScaled ops with negSrc: realised as Negate·Add·Negate (2 extra Negates)
    ops: Optional[tuple] = None  # the bundle's schedule ops: the exact width fold, past the emitted table

    @classmethod
    def from_bundle(cls, b: Bundle) -> "ArityData":
        doc = b.doc
        cen = bundle_compat.schedule_census(doc)
        # Read through `widths`, not out of the document: the v2 bundle renames every one of
        # these keys and emits no tight column at all (it is reconstructed from the schedule).
        sym, tight = widths.width_rule(doc, "sym"), widths.width_rule(doc, "tight")
        rows = widths.uniform_rows(doc)
        return cls(k=doc["k"], q=cen["phaseProduct"], n_add=cen["addScaled"], n_neg=cen["negate"],
                   sym_uniform={e["m"]: e["W_sym"] for e in rows},
                   tight_uniform={e["m"]: e["W_tight"] for e in rows},
                   sym_general=widths.general_widths(doc, "sym"),
                   tight_general=widths.general_widths(doc, "tight"),
                   beta_sym=sym.beta, beta_tight=tight.beta,
                   from_sym=sym.from_m, from_tight=tight.from_m,
                   n_add_negsrc=cen["addScaled_negSrc"],
                   ops=tuple(tuple(sorted(o.items(), key=lambda kv: kv[0])) for o in bundle_compat.schedule_view(doc)["ops"]))

    def next_width(self, w: int, policy: str, pad: bool) -> int:
        """W' for square operands of width w.  pad: zero-extend to k·⌈w/k⌉ first (uniform limbs, E3 uniform table);
        else floor limbs with the remainder in the top limb (E3 general table)."""
        k = self.k
        if pad:
            m = -(-w // k)
            tab, beta, frm = (self.sym_uniform, self.beta_sym, self.from_sym) if policy == "sym" else (self.tight_uniform, self.beta_tight, self.from_tight)
            if m in tab:
                return tab[m]
            if m >= frm:
                return m + beta
            raise ValueError(f"no width entry for k={k}, m={m}")
        tab = self.sym_general if policy == "sym" else self.tight_general
        if w in tab:
            return tab[w]
        if policy == "sym" and self.ops is not None:
            # Past the emitted table: the exact re-fold, as `LeanRecursivePhaseProduct.next_width` does. The affine
            # tail over-estimates by one off the uniform ladder, which ended ladders a level early.
            from .width_scan import next_width as exact_next_width
            return exact_next_width([dict(o) for o in self.ops], k, w)
        m = -(-w // k)
        beta = self.beta_sym if policy == "sym" else self.beta_tight
        return m + beta       # beyond the table, tight policy: the closed-form affine tail at ⌈w/k⌉


class RuleSet:
    """All loaded arities; the thing every version/rule query goes through."""

    def __init__(self, bundles: Sequence[Bundle]):
        self.data: Dict[int, ArityData] = {b.k: ArityData.from_bundle(b) for b in bundles}
        self.bundles = {b.k: b for b in bundles}
        self.ks = sorted(self.data)
        if 2 not in self.data:
            raise ValueError("version A needs the k = 2 bundle")

    def certifies(self, ks: Sequence[int]) -> bool:
        return all(self.bundles[k].certifies for k in ks)

    # -------- rule primitives
    @staticmethod
    def _rule_params(rule: str) -> Tuple[str, bool, bool]:
        """(policy, pad, cost_optimal_stop)"""
        return {"lean": ("sym", False, False), "sym": ("sym", True, True), "kmy": ("tight", True, True)}[rule]

    def leaf_T(self, w: int, ts_per_rotation=constants.TS_PER_ROTATION) -> float:
        """T of a w x w leaf grid under a currency: a float `ts_per_rotation` is the Qualtran heuristic
        (`w^2 * (4 + tsr)`); any object with `.leaf(w)` (see `leaf_currency.py`: gadget, windowed) is asked
        directly. The currency is part of every cached decision's identity."""
        if hasattr(ts_per_rotation, "leaf"):
            return float(ts_per_rotation.leaf(w))
        return w * w * (constants.TS_PER_TOFFOLI + ts_per_rotation)

    def body_T(self, k: int, W2: int) -> float:
        d = self.data[k]
        # sides=2: every schedule op acts on x AND z (LeanPhaseProduct's/LeanRecursivePhaseProduct's
        # convention, fixed 2026-09-02/03 — this closed form was missing it too, which made golden.py's
        # "Qualtran count == independent closed form" check a tautology: both sides were undercounting
        # identically). n_add_negsrc contributes 2 extra Negates each, per side.
        sides = 2
        return sides * (d.n_add + d.n_neg + 2 * d.n_add_negsrc) * constants.TS_PER_TOFFOLI * max(0, W2 - 1)

    def arities_at(self, version: str, k_top: int, root: bool) -> List[int]:
        if version == "A":
            return [k_top] if root else [2]
        if version == "B":
            return [k_top]
        if version == "C":
            return list(self.ks)
        raise ValueError(version)

    # -------- the closed-form recursion (== the bloq's call graph)
    def t_model(self, w: int, version: str, k_top: int, rule: str,
                ts_per_rotation=constants.TS_PER_ROTATION) -> float:
        return self._t(w, version, k_top, rule, True, _currency_key(ts_per_rotation))[0]

    def decision(self, w: int, version: str, k_top: int, rule: str, root: bool,
                 ts_per_rotation=constants.TS_PER_ROTATION) -> Optional[Tuple[int, int]]:
        """None → leaf grid at width w; (k, W') → one Toom-k level with q_k children at width W'.
        `ts_per_rotation`: a float (Qualtran heuristic) or a leaf currency object (`leaf_currency.py`)."""
        return self._t(w, version, k_top, rule, root, _currency_key(ts_per_rotation))[1]

    @functools.lru_cache(maxsize=None)
    def _t(self, w: int, version: str, k_top: int, rule: str, root: bool, tsr):
        policy, pad, opt = self._rule_params(rule)
        leaf = self.leaf_T(w, tsr)
        best, choice = leaf, None
        for k in self.arities_at(version, k_top, root):
            d = self.data[k]
            W2 = d.next_width(w, policy, pad)
            if W2 >= w:
                continue
            child = self._t(W2, version, k_top, rule, False, tsr)[0]
            T = d.q * child + self.body_T(k, W2)
            if not opt:                      # R0: the plan is forced to take the level
                return T, (k, W2)
            if T < best:
                best, choice = T, (k, W2)
        return best, choice

    def tree(self, w: int, version: str, k_top: int, rule: str,
             ts_per_rotation: float = constants.TS_PER_ROTATION) -> List[Dict[str, Any]]:
        """Level table along the (unique for A/B; leftmost for C) descent: width, arity, children, cumulative leaves,
        T contributed by this level's bodies, and T of the leaf grids if the recursion stopped here."""
        rows, mult, cur, root = [], 1, w, True
        while True:
            dec = self.decision(cur, version, k_top, rule, root, ts_per_rotation)
            if dec is None:
                rows.append({"level": len(rows), "W": cur, "k": None, "nodes": mult, "body_T": 0.0,
                             "leaf_T": mult * self.leaf_T(cur, ts_per_rotation), "stop": True})
                return rows
            k, W2 = dec
            rows.append({"level": len(rows), "W": cur, "k": k, "nodes": mult, "body_T": mult * self.body_T(k, W2),
                         "leaf_T": mult * self.leaf_T(cur, ts_per_rotation), "stop": False})
            mult *= self.data[k].q
            cur, root = W2, False


def _currency_key(tsr):
    """Floats stay floats (the heuristic); currency objects are frozen attrs instances, hashable as-is."""
    return float(tsr) if isinstance(tsr, (int, float)) else tsr


# ------------------------------------------------------------------ the Qualtran bloq
@attrs.frozen
class ToomRecursivePhaseProduct(Bloq):
    """One recursive signed phase product of width w under (version, k_top, rule); the call graph is the rule's
    decision at every node.  Concrete w by construction (the decisions depend on it); `RuleSet` is not part of
    equality — (version, k_top, rule, w, root) identify the node."""
    w: int
    version: str
    k_top: int
    rule: str
    root: bool = True
    #: STOP-RULE PRICE (2026-09-10): the T per arbitrary rotation the cost-optimal stop rule R2 evaluates
    #: `leaf_T` at. Part of the node's identity (a different price can choose a different tree). The
    #: default reproduces every count priced before this field existed.
    tsr: float = constants.TS_PER_ROTATION
    rules: Optional[RuleSet] = attrs.field(default=None, eq=False, repr=False)

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.w)), Register("z", QUInt(self.w))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        assert self.rules is not None
        dec = self.rules.decision(self.w, self.version, self.k_top, self.rule, self.root, self.tsr)
        if dec is None:
            return {CZPowGate(exponent=ssa.new_symbol("e")): self.w * self.w}
        k, W2 = dec
        # SINGLE-k GUARD: version B ("k at every level") must recurse at exactly k_top — the child below
        # keeps (version, k_top) via evolve, so this holds inductively down the whole ladder. Versions A/C
        # deliberately switch k and are NOT used by the QFT twiddle or the ForShor plan.
        if self.version == "B":
            assert k == self.k_top, f"version B recursed at k={k} != k_top={self.k_top} (single-k violated)"
        d = self.rules.data[k]
        counts: Dict[Bloq, int] = {attrs.evolve(self, w=W2, root=False): d.q}
        sides = 2                                  # every schedule op acts on x AND z (see body_T's fix)
        if d.n_add:
            counts[Add(QInt(W2))] = sides * d.n_add
        n_negate = sides * (d.n_neg + 2 * d.n_add_negsrc)
        if n_negate:
            counts[Negate(QInt(W2))] = n_negate
        return counts

    # ---- what the consumers (QFT twiddles, multiplier cores) need
    def total_czpow(self) -> int:
        """Σ over leaf grids of W² — every CZPow leaf of the whole recursion (the controlled-PP And count uses it)."""
        assert self.rules is not None
        return int(round(sum(r["leaf_T"] for r in self.rules.tree(self.w, self.version, self.k_top, self.rule, self.tsr)
                             if r["stop"]) / (constants.TS_PER_TOFFOLI + self.tsr)))

    @property
    def qubit_footprint(self) -> int:
        """2w operands + zero-extension slack along the descent (2·(k⌈W/k⌉ − W) at each level taken, for both operands) + 1
        And ancilla.  This is the R2 model's footprint (the padding it costs), NOT the Lean `reserveNeed` (that belongs to
        the proved plan R0 and is larger)."""
        assert self.rules is not None
        slack = 0
        for r in self.rules.tree(self.w, self.version, self.k_top, self.rule, self.tsr):
            if not r["stop"]:
                k = r["k"]
                slack += 2 * (k * (-(-r["W"] // k)) - r["W"])
        return 2 * self.w + slack + 1

    @classmethod
    def from_rules(cls, rules: RuleSet, w: int, k_top: int, version: str = "B", rule: str = "kmy",
                   tsr: float = constants.TS_PER_ROTATION) -> "ToomRecursivePhaseProduct":
        return cls(w=w, version=version, k_top=k_top, rule=rule, rules=rules, tsr=tsr)

    def __str__(self) -> str:
        return f"ToomRecPP({self.version},k={self.k_top},{self.rule},w={self.w})"


def qualtran_T(rules: RuleSet, w: int, version: str, k_top: int, rule: str,
               ts_per_rotation: float = constants.TS_PER_ROTATION) -> float:
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    b = ToomRecursivePhaseProduct(w=w, version=version, k_top=k_top, rule=rule, rules=rules)
    return float(get_cost_value(b, QECGatesCost()).total_t_count(ts_per_rotation=ts_per_rotation))


def check_model_equals_qualtran(rules: RuleSet, ns: Sequence[int], versions: Sequence[str] = ("A", "B"),
                                ks: Optional[Sequence[int]] = None, rule: str = "kmy") -> List[Dict[str, Any]]:
    out = []
    for version in versions:
        for k in (ks or rules.ks):
            for n in ns:
                tm, tq = rules.t_model(n, version, k, rule), qualtran_T(rules, n, version, k, rule)
                out.append({"version": version, "k": k, "n": n, "rule": rule, "T_model": tm, "T_qualtran": tq, "equal": abs(tm - tq) < 1e-6 * max(1.0, tm)})
    return out


# ------------------------------------------------------------------ sweeps, slopes, exponents
# PATCH 3 FIX 2026-09-03 (HANDOFF_PATCH_VERIFY_AND_PERF): the endpoints of both `exponent_table` fit
# windows ((1024,8192) and (4096,32768)) plus the jump-aligned bracket around the n=10000 anchor for
# every k this module sweeps by default (2..6) -- see `jump_aligned_bracket` below for why the bracket
# must be `k`-dependent, not the fixed (8192,16384) this file originally shipped. `dense_ns` (and any
# grid feeding `exponent_table`/`fit_exponent`) must ALWAYS include these, even under a trim (a smaller
# requested n_hi, or a coarser grid for speed) -- otherwise a trimmed sweep can silently drop a fit
# window down to < 2 in-window points and the exponent goes to None with no warning.
MINIMAL_FIT_NS: Tuple[int, ...] = tuple(sorted({1024, 4096, 8192, 32768}
                                               | {8192 * k for k in range(2, 7)}))


def dense_ns(n_lo: int = 32, n_hi: int = 32768) -> List[int]:
    """Quarter-octave grid: n = 2^j·{1, 2^¼, 2^½, 2^¾}, UNION `MINIMAL_FIT_NS` (always present, even if
    the requested [n_lo, n_hi] would otherwise exclude them -- see the module-level note above)."""
    out = set(MINIMAL_FIT_NS)
    j = int(math.floor(math.log2(n_lo)))
    while 2 ** j <= n_hi:
        for f in (1.0, 2 ** 0.25, 2 ** 0.5, 2 ** 0.75):
            v = int(round(2 ** j * f))
            if n_lo <= v <= n_hi:
                out.add(v)
        j += 1
    return sorted(out)


def sweep(rules: RuleSet, ns: Sequence[int], versions: Sequence[str] = ("A", "B"), rule_names: Sequence[str] = RULES,
          ks: Optional[Sequence[int]] = None, ts_per_rotation: float = constants.TS_PER_ROTATION) -> List[Dict[str, Any]]:
    rows = []
    for version in versions:
        k_iter = list(ks or rules.ks)
        if version == "C":
            k_iter = [max(k_iter)]      # C does not depend on k_top; one row set, keyed by the largest arity loaded
        for k in k_iter:                # (A with k = 2 coincides with B, k = 2 — kept so both panels carry the k = 2 reference)
            for rule in rule_names:
                for n in ns:
                    tr = rules.tree(n, version, k, rule)
                    rows.append({"version": version, "k": k, "rule": rule, "n": int(n),
                                 "T": rules.t_model(n, version, k, rule, ts_per_rotation),
                                 "depth": len(tr) - 1, "base_width": tr[-1]["W"], "leaves": tr[-1]["nodes"],
                                 "ladder": [r["W"] for r in tr], "arities": [r["k"] for r in tr if r["k"] is not None]})
    return rows


def fit_exponent(rows: Sequence[Dict[str, Any]], n_lo: int, n_hi: int) -> Optional[float]:
    """log-log slope of T(n) over n in [n_lo, n_hi]. PATCH 3 FIX 2026-09-03: the guard used to require
    >= 3 in-window points, silently returning None on exactly 2 -- but a degree-1 `np.polyfit` on 2
    points already gives the EXACT secant, and `dense_ns`'s `MINIMAL_FIT_NS` now guarantees at least the
    two window endpoints survive any trim. So the floor is 2, not 3: the minimal case is a clean
    two-point secant, not a silent None. On exactly 2 points this is identical to `secant_exponent`."""
    pts = sorted((r["n"], r["T"]) for r in rows if n_lo <= r["n"] <= n_hi and r["T"] > 0)
    if len(pts) < 2:
        return None
    x = np.log([p[0] for p in pts]); y = np.log([p[1] for p in pts])
    return float(np.polyfit(x, y, 1)[0])


MIN_OCTAVE_RATIO: float = 1.9   # n_hi/n_lo must clear this to trust a two-point secant (~1 octave, per below)


def jump_aligned_bracket(k: int, anchor: int = 8192) -> Tuple[int, int]:
    """The (n_lo, n_hi) secant bracket that spans EXACTLY one recursion-depth jump near `anchor`, for
    arity `k` -- pass straight to `secant_exponent`.

    PATCH 3 CORRECTION 2026-09-03: this file originally recommended a FIXED bracket (8192, 16384) for
    every k, on the assumption that jumps are ~1 octave apart universally. They are not: each factor
    of k in n buys exactly one more recursion level (`ANALYSIS_EXPONENT_FIT_DEVIATION.2026-09-03.md`
    derives this from the recursion's own width map w -> ceil(w/k) + beta_k), so jumps sit a factor
    `k` apart, NOT a factor 2, for k != 2. A fixed (8192,16384) bracket is a genuine one-jump span only
    at k=2; at larger k it clips a THIRD to a SIXTH of the true spacing, landing mid-jump or mid-tread
    depending on phase and returning a badly biased secant. Verified: at k=5 the fixed bracket reads
    1.2607 (true 1.3652, error 0.10 -- worse than useless); the k-dependent bracket (8192, 8192*5)
    reads 1.3700 (error 0.005). Same at k=6 (fixed error 0.14 vs k-dependent error 0.006).

    Both endpoints are already unconditionally present in `MINIMAL_FIT_NS`/`dense_ns`'s output for
    k=2..6, so this bracket is always usable without a fresh sweep."""
    return (anchor, anchor * int(k))


def secant_exponent(rows: Sequence[Dict[str, Any]], n_lo: int, n_hi: int, *,
                    require_octave: bool = True) -> Optional[float]:
    """log-log slope between the LOWEST and HIGHEST in-window points only (ignores any points strictly
    between them). Equals the mean staircase slope only when n_hi/n_lo >= ~2 (spans >= one riser --
    `LeanRecursivePhaseProduct`'s T(n) is flat between width-transition thresholds and jumps at them, so
    a sub-octave pair reads a flat tread -- a SEVERE underestimate of the true exponent, not a clean
    zero, which is exactly why it must not be trusted silently). For a target anchor A at arity k, use
    `jump_aligned_bracket(k, A)` -- NOT a fixed octave bracket; see that function's docstring for why
    the correct spacing is a factor of k, not 2, for k != 2.

    `require_octave=True` (the default) REJECTS a sub-octave request outright, returning `None` rather
    than a silently-wrong number -- the caller must either widen the bracket or explicitly route through
    `tread_averaged_T` instead (pass `require_octave=False` only if you have already done that and are
    calling this purely to compute the final secant on cluster-averaged T values)."""
    if require_octave and n_lo > 0 and (n_hi / n_lo) < MIN_OCTAVE_RATIO:
        return None
    lo = min((r for r in rows if r["T"] > 0 and r["n"] >= n_lo), key=lambda r: r["n"], default=None)
    hi = max((r for r in rows if r["T"] > 0 and r["n"] <= n_hi), key=lambda r: r["n"], default=None)
    if lo is None or hi is None or lo["n"] == hi["n"]:
        return None
    return float((np.log(hi["T"]) - np.log(lo["T"])) / (np.log(hi["n"]) - np.log(lo["n"])))


def tread_averaged_T(eval_T, n: int, half_octave: float = 0.5, samples: int = 5) -> float:
    """If you insist on a TIGHT anchor pair (e.g. exactly 9900 & 10000), average T over a +/- half-octave
    cluster around each anchor so the secant recovers the MEAN staircase slope instead of a flat tread.
    `eval_T(n')` is one (memoized) T evaluation -- cheap. Returns the log-mean T at n."""
    lo, hi = n * 2 ** (-half_octave), n * 2 ** half_octave
    ns = np.unique(np.round(np.geomspace(lo, hi, samples)).astype(int))
    return float(np.exp(np.mean([np.log(eval_T(int(m))) for m in ns])))


def local_slopes(rows: Sequence[Dict[str, Any]], window_octaves: float = 1.0) -> List[Tuple[float, float]]:
    """d log T / d log n by least squares over a sliding window of `window_octaves` (centred), on the row grid."""
    pts = sorted((r["n"], r["T"]) for r in rows)
    x = np.log2([p[0] for p in pts]); y = np.log2([p[1] for p in pts])
    out = []
    for i in range(len(pts)):
        sel = np.abs(x - x[i]) <= window_octaves / 2 + 1e-9
        if sel.sum() >= 3:
            out.append((float(pts[i][0]), float(np.polyfit(x[sel], y[sel], 1)[0])))
    return out


def exponent_table(rows: Sequence[Dict[str, Any]], windows: Sequence[Tuple[int, int]] = ((1024, 8192), (4096, 32768))) -> List[Dict[str, Any]]:
    """PATCH 3 FIX 2026-09-03 (HANDOFF_PATCH_VERIFY_AND_PERF): each window's fit now also records
    `method` -- "secant" when exactly its two endpoints survived (honest provenance: the value is a
    two-point secant, not a least-squares fit over a denser window), "polyfit" otherwise. The VALUE is
    identical either way (a degree-1 fit on 2 points already IS the exact secant); only the label
    changes, so a downstream reader can tell a trimmed grid from a dense one at a glance."""
    out = []
    keys = sorted({(r["version"], r["k"], r["rule"]) for r in rows}, key=lambda t: (t[0], t[1], RULES.index(t[2])))
    for version, k, rule in keys:
        sub = [r for r in rows if r["version"] == version and r["k"] == k and r["rule"] == rule]
        theory = math.log2(3) if (version == "A" and k != 2) else (theory_exponent(k) if version != "C" else min(theory_exponent(kk) for kk in {r["k"] for r in rows}))
        row = {"version": version, "k": k, "rule": rule, "theory": round(theory, 3)}
        for lo, hi in windows:
            e = fit_exponent(sub, lo, hi)
            n_in_window = sum(1 for r in sub if lo <= r["n"] <= hi and r["T"] > 0)
            row[f"fit[{lo},{hi}]"] = None if e is None else round(e, 3)
            row[f"fit[{lo},{hi}].method"] = None if e is None else ("secant" if n_in_window <= 2 else "polyfit")
        out.append(row)
    return out


# ------------------------------------------------------------------ figures
def _style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def figure_versions(rows: Sequence[Dict[str, Any]], rules: RuleSet, out_dir: Path, version: str, name: str,
                    cert_tag: str, ks: Optional[Sequence[int]] = None) -> str:
    """Two panels: T(n) per k (solid = R2 kmy, dashed = R1 sym, dotted = R0 lean; black dotted = schoolbook) and the local
    log-log slope of R2 with the theoretical exponent as a horizontal reference (log_2 3 for A; log_k q for B)."""
    plt = _style()
    ks = list(ks or rules.ks)
    cmap = plt.get_cmap("viridis"); col = {k: cmap(i / max(1, len(ks) - 1)) for i, k in enumerate(ks)}
    ls = {"kmy": "-", "sym": "--", "lean": ":"}
    fig, axs = plt.subplots(1, 2, figsize=(14, 5.5))
    for k in ks:
        for rule in RULES:
            pts = sorted((r["n"], r["T"]) for r in rows if r["version"] == version and r["k"] == k and r["rule"] == rule)
            if pts:
                axs[0].plot(*zip(*pts), ls[rule], color=col[k], lw=1.6 if rule == "kmy" else 1.0,
                            label=f"k={k}" + ("" if rules.bundles[k].certifies else " [MIRROR]") if rule == "kmy" else None)
    ns_ = sorted({r["n"] for r in rows if r["version"] == version})
    axs[0].plot(ns_, [constants.SCHOOLBOOK_PREFACTOR * n * n for n in ns_], "k:", lw=2, label="schoolbook 15n²")
    axs[0].plot([], [], "-", color="gray", label="R2 tight, padded, cost-optimal stop")
    axs[0].plot([], [], "--", color="gray", label="R1 symbolic, padded, cost-optimal stop")
    axs[0].plot([], [], ":", color="gray", label="R0 Lean plan (forced depth)")
    axs[0].set_xscale("log"); axs[0].set_yscale("log"); axs[0].set_xlabel("n"); axs[0].set_ylabel("T of one recursive PhaseProduct (11 T/rotation)")
    axs[0].grid(True, which="both", alpha=.3); axs[0].legend(fontsize=6, ncol=2)
    # right panel: fitted exponent vs k, two windows, all three rules; theory curve
    windows = ((1024, 8192), (4096, 32768))
    marks = {"kmy": ("o", "s"), "sym": ("^", "v"), "lean": ("x", "+")}
    for rule in RULES:
        for wi, (lo, hi) in enumerate(windows):
            xs, ys = [], []
            for k in ks:
                e = fit_exponent([r for r in rows if r["version"] == version and r["k"] == k and r["rule"] == rule], lo, hi)
                if e is not None:
                    xs.append(k); ys.append(e)
            axs[1].plot(xs, ys, marks[rule][wi], color={"kmy": "C0", "sym": "C1", "lean": "C3"}[rule], ms=7,
                        mfc=("none" if wi else None), label=f"{ {'kmy': 'R2', 'sym': 'R1', 'lean': 'R0'}[rule]} fit on n∈[{lo},{hi}]")
    th = [math.log2(3) if (version == "A" and k != 2) else theory_exponent(k) for k in ks]
    axs[1].plot(ks, th, "k--", lw=1, label=("theory log₂3 (A: Karatsuba below)" if version == "A" else "theory log_k(2k−1)"))
    axs[1].axhline(2.0, color="k", ls=":", lw=1)
    axs[1].set_xlabel("k (arity at the top level)"); axs[1].set_ylabel("fitted exponent of T(n)"); axs[1].set_xticks(ks)
    axs[1].set_ylim(1.0, 2.6); axs[1].grid(alpha=.3); axs[1].legend(fontsize=6, ncol=2)
    fig.suptitle(f"{name} — recursive PhaseProduct, version {version}: {VERSION_DESC[version]}  [{cert_tag}]", fontsize=10)
    f = out_dir / f"{name}_recursive_version_{version}.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return str(f)


def power_fit(rows: Sequence[Dict[str, Any]], n_lo: int, n_hi: int) -> Optional[Tuple[float, float]]:
    """T ≈ C·n^e by least squares on log–log over n ∈ [n_lo, n_hi]; returns (C, e)."""
    pts = sorted((r["n"], r["T"]) for r in rows if n_lo <= r["n"] <= n_hi and r["T"] > 0)
    if len(pts) < 3:
        return None
    e, lc = np.polyfit(np.log([p[0] for p in pts]), np.log([p[1] for p in pts]), 1)
    return float(math.exp(lc)), float(e)


def power_fit_r2(rows: Sequence[Dict[str, Any]], n_lo: int, n_hi: int) -> Optional[Tuple[float, float, float]]:
    """(C, e, R²): the least-squares line in (log n, log T) over n ∈ [n_lo, n_hi] and its coefficient of determination
    R² = 1 − SS_res/SS_tot on log T."""
    pts = sorted((r["n"], r["T"]) for r in rows if n_lo <= r["n"] <= n_hi and r["T"] > 0)
    if len(pts) < 3:
        return None
    x = np.log([p[0] for p in pts]); y = np.log([p[1] for p in pts])
    e, lc = np.polyfit(x, y, 1)
    yhat = e * x + lc
    ss_res = float(np.sum((y - yhat) ** 2)); ss_tot = float(np.sum((y - y.mean()) ** 2))
    return float(math.exp(lc)), float(e), (1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"))


def figure_clean(rows: Sequence[Dict[str, Any]], rules: RuleSet, out_dir: Path, version: str, name: str, cert_tag: str,
                 ks: Optional[Sequence[int]] = None, fit_window: Tuple[int, int] = (4096, 32768)) -> str:
    """Only the cost-optimal recursion (rule R2) per k and schoolbook; each curve carries its power-law fit
    T = C·n^e (thin line, fitted on n ∈ fit_window, drawn over the whole range) with e and the theory exponent in the legend."""
    plt = _style()
    ks = list(ks or rules.ks)
    cmap = plt.get_cmap("viridis"); col = {k: cmap(i / max(1, len(ks) - 1)) for i, k in enumerate(ks)}
    fig, ax = plt.subplots(figsize=(9, 6.5))
    ns_all = sorted({r["n"] for r in rows if r["version"] == version and r["rule"] == "kmy"})
    ax.plot(ns_all, [constants.SCHOOLBOOK_PREFACTOR * n * n for n in ns_all], "k:", lw=2, label="schoolbook 15·n²  (exponent 2)")
    for k in ks:
        sub = [r for r in rows if r["version"] == version and r["k"] == k and r["rule"] == "kmy"]
        pts = sorted((r["n"], r["T"]) for r in sub)
        if not pts:
            continue
        th = math.log2(3) if (version == "A" and k != 2) else theory_exponent(k)
        pf = power_fit_r2(sub, *fit_window)
        tag = "" if rules.bundles[k].certifies else " [MIRROR]"
        ax.plot(*zip(*pts), "o", color=col[k], ms=3, alpha=0.9)
        if pf:
            C, e, r2 = pf
            xs = np.array([ns_all[0], ns_all[-1]], dtype=float)
            ax.plot(xs, C * xs ** e, "-", color=col[k], lw=1.2,
                    label=f"k={k}{tag}:  fit  T = {C:.3g}·n^{e:.3f}   (theory exponent {th:.3f};  R² = {r2:.5f})")
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("n (bits)"); ax.set_ylabel("T of one recursive PhaseProduct (11 T/rotation)")
    ax.grid(True, which="both", alpha=.3)
    ax.legend(fontsize=7, loc="upper left", title=f"points: R2 cost-optimal recursion, quarter-octave grid; lines: power-law fit on n∈[{fit_window[0]},{fit_window[1]}]", title_fontsize=7)
    fig.suptitle(f"{name} — recursive PhaseProduct, version {version}: {VERSION_DESC[version]}; cost-optimal recursion (R2) vs schoolbook  [{cert_tag}]", fontsize=9)
    f = out_dir / f"{name}_recursive_version_{version}_clean.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return str(f)


def figure_diagnosis(rules: RuleSet, out_dir: Path, k: int, n: int, name: str = "V15", cert_tag: str = "") -> str:
    """Level-by-level anatomy at one (k, n): width ladder and the T each level would cost if the recursion stopped
    there (leaf grids) plus the bodies already paid — R0 (Lean plan) vs R2 (correction), version B."""
    plt = _style()
    fig, axs = plt.subplots(1, 2, figsize=(14, 5))
    for rule, mk, lab in (("lean", "o:", "R0 Lean plan"), ("kmy", "s-", "R2 correction")):
        tr = rules.tree(n, "B", k, rule)
        lv = [r["level"] for r in tr]
        axs[0].plot(lv, [r["W"] for r in tr], mk, label=f"{lab}: W per level")
        cum_body = np.cumsum([r["body_T"] for r in tr])
        stop_T = [cum_body[i] - tr[i]["body_T"] + tr[i]["leaf_T"] for i in range(len(tr))]
        axs[1].plot(lv, stop_T, mk, label=f"{lab}: T if stopped at this level")
    axs[0].set_yscale("log"); axs[0].set_xlabel("level"); axs[0].set_ylabel("operand width W at the level"); axs[0].grid(alpha=.3); axs[0].legend(fontsize=7)
    axs[1].axhline(constants.SCHOOLBOOK_PREFACTOR * n * n, color="k", ls=":", label="schoolbook 15n²")
    axs[1].set_yscale("log"); axs[1].set_xlabel("level"); axs[1].set_ylabel("T (11 T/rotation)"); axs[1].grid(alpha=.3); axs[1].legend(fontsize=7)
    fig.suptitle(f"{name} — level anatomy, version B, k={k}, n={n}  [{cert_tag}]", fontsize=10)
    f = out_dir / f"{name}_recursion_anatomy_k{k}_n{n}.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return str(f)


# ------------------------------------------------------------------ one call for the CLI / notebook
def run_all(bundles: Sequence[Bundle], out_dir: Path, ks: Sequence[int] = (2, 3, 4, 5, 6), n_hi: int = 32768,
            diag_k: int = 4, diag_n: int = 4096) -> Dict[str, Any]:
    sel = [b for b in bundles if b.k in ks]
    rules = RuleSet(sel)
    ks = [k for k in ks if k in rules.data]
    ns = dense_ns(32, n_hi)
    rows = sweep(rules, ns, versions=("A", "B", "C"), ks=ks)
    cert = "REAL-LEAN" if rules.certifies(ks) else "NOT Lean-certified for k=" + ",".join(str(k) for k in ks if not rules.bundles[k].certifies)
    out_dir.mkdir(parents=True, exist_ok=True)
    figs = {"V13": figure_versions(rows, rules, out_dir, "A", "V13", cert, ks),
            "V14": figure_versions(rows, rules, out_dir, "B", "V14", cert, ks),
            "V15": figure_diagnosis(rules, out_dir, diag_k if diag_k in ks else ks[-1], diag_n, "V15", cert),
            "V13c": figure_clean(rows, rules, out_dir, "A", "V13c", cert, ks),
            "V14c": figure_clean(rows, rules, out_dir, "B", "V14c", cert, ks)}
    chk = check_model_equals_qualtran(rules, [n for n in (64, 256, 1024, 4096) if n <= n_hi], ("A", "B"), ks, "kmy")
    return {"rows": rows, "exponents": exponent_table(rows), "figures": figs, "qualtran_check": chk,
            "qualtran_check_ok": all(c["equal"] for c in chk), "rules": {r: RULE_DESC[r] for r in RULES},
            "versions": {v: VERSION_DESC[v] for v in VERSIONS}, "certification": cert}
