"""THE shared gradient width b for merged catalysis (v2 pricing, decisions D1-D5 of 2026-09-23).

One implementation, used by both the figures (`tcount.catalysis.price_merged`) and the wired bloqs
(`CatalystPrep(b, eps)` + the merged twiddles / leaves): `choose_b(census, partition)`.

ERROR SPLIT (`ErrorPartition`, total-variation of the output distribution)
-------------------------------------------------------------------------
    delta ──┬── structure  0.50 delta  -> eta = structure_budget / n^2   (fixes every register width)
            ├── catalysis  0.25 delta  every rounding of a value ADDED INTO the catalyst:
            │                          twiddle rows, Y rows + Y truncation, P kicks, `other` kicks
            └── rotation   0.25 delta  genuinely synthesised rotations: the b - 3 catalyst-prep states
                                       (+ any site not realised catalytically: none in this model)
Deterministic parts add operator norms; mixed parts (D5: P kicks and `other` kicks, unbiased random
rounding with one classical draw per kick) add 1/2 x diamond. Unused rotation budget is REPORTED
(`BChoice.rotation_unused`), never moved to catalysis.

PRICE AT A FIXED b (`price_at`)
------------------------------
  twiddle ("tw", r1, r2, t, ...)   native rows of `twiddle.twiddle_row_plan` (== `spec.native_twiddle_row_ands`):
                                   exact row m + L - 1, rounded row m + L, no-op row 0; error = sum over rows of
                                   the EXACT max-over-z turn error x 2 pi (`spec.twiddle_row_error_turns`)
  leaf ("leaf", wx, ctrl[, cls, res, wz])
                                   `leaf.leaf_cost_class` -- the LEAF contract, equal to QECGatesCost of
                                   `leaf.catalytic_leaf` (exact for dyadic res <= b, an upper bound for
                                   generic / rounded-dyadic turns); generic when cls/res are absent.
                                   `leaf_model="spec"` uses the coarser independent bound
                                   `spec.leaf_class_cost` instead (>= the LEAF numbers, tested).
                                   ROUTE: fewer Ands, ties -> Y (as catalytic_leaf). The census `res` is an
                                   upper bound on the true resolution; the priced error is a bound for the
                                   built leaf when the wiring builds the priced route (`BChoice.leaf_routes`,
                                   pass it as catalytic_leaf(..., route=...)).
  other ("other",)                 one CtrlAddConstIntoCatalyst(b, k) per site: b - 1 Ands, mixed
  prep                             (b - 3) RS(rotation_budget / (b - 3)) + 1 T; pass
                                   `catalyst_prep_eps` = rotation_budget b / (b - 3) to CatalystPrep
                                   (Qualtran splits eps as eps / b per ZPow)
Ands cost `and_t` T each (D1: 4).

b RULE: the smallest b >= b_min whose catalysis error <= the catalysis share. Every price is
non-decreasing in b and every error non-increasing (tested), so the smallest feasible b is T-optimal
for the fixed route rule (per leaf: fewer Ands, ties -> Y).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

import numpy as np

from . import spec as S

__all__ = ["ErrorPartition", "BChoice", "CensusPrice", "choose_b", "price_at", "leaf_key_fields",
           "twiddle_price", "refund_T"]

_TOL = 1e-12


@dataclass(frozen=True)
class ErrorPartition:
    """delta -> structure / catalysis / rotation fractions (must sum to 1)."""
    delta: float = 1e-2
    structure: float = 0.5
    catalysis: float = 0.25
    rotation: float = 0.25

    def __post_init__(self):
        if not 0 < self.delta < 1:
            raise ValueError("delta must be in (0, 1)")
        for nm in ("structure", "catalysis", "rotation"):
            v = getattr(self, nm)
            if not 0 <= v <= 1:
                raise ValueError(f"{nm} fraction must be in [0, 1], got {v}")
        if self.catalysis <= 0 or self.rotation <= 0:
            raise ValueError("catalysis and rotation fractions must be positive")
        s = self.structure + self.catalysis + self.rotation
        if abs(s - 1.0) > _TOL:
            raise ValueError(f"fractions must sum to 1, got {s!r}")

    @classmethod
    def from_synthesis_budget(cls, delta_syn: float, *, structure: float = 0.5, catalysis: float = 0.25,
                              rotation: float = 0.25) -> "ErrorPartition":
        """The partition whose non-structure half (catalysis + rotation) is `delta_syn` -- what the older
        callers pass as `delta_rot` (the figures' `RotationBudget.delta_rot`)."""
        return cls(delta=delta_syn / (catalysis + rotation), structure=structure, catalysis=catalysis,
                   rotation=rotation)

    @property
    def structure_budget(self) -> float:
        return self.structure * self.delta

    @property
    def catalysis_budget(self) -> float:
        return self.catalysis * self.delta

    @property
    def rotation_budget(self) -> float:
        return self.rotation * self.delta

    @property
    def synthesis_budget(self) -> float:
        """catalysis + rotation: the half a catalyst-free synthesis control (RS) spends on its rotations."""
        return (self.catalysis + self.rotation) * self.delta

    def eta(self, n: int) -> float:
        """Per-modmul precision shorEta(structure_budget, n) = structure_budget / n^2 (unchanged)."""
        return self.structure_budget / float(n) ** 2

    def prep_eps(self, b: int) -> float:
        """Operator-norm tolerance of EACH of the b - 3 synthesised catalyst rotations."""
        return self.rotation_budget / max(1, b - 3)

    def catalyst_prep_eps(self, b: int) -> float:
        """The `eps` argument for `CatalystPrep(b, eps)` (Qualtran's eps / b split per ZPow)."""
        return S.catalyst_prep_eps(b, self.rotation_budget)


# ----------------------------------------------------------------------------- census keys

def leaf_key_fields(key: tuple) -> Tuple[int, int, bool, str, Optional[int]]:
    """(wx, wz, ctrl, cls, res) of a leaf key. v1 keys ("leaf", w, ctrl) are square and generic;
    v2 keys are ("leaf", wx, ctrl, cls, res, wz) (tcount.leaf_census)."""
    wx, ctrl = int(key[1]), bool(key[2])
    cls = str(key[3]) if len(key) > 3 and key[3] is not None else "generic"
    res = None if len(key) <= 4 or key[4] is None else int(key[4])
    wz = int(key[5]) if len(key) > 5 and key[5] is not None else wx
    return wx, wz, ctrl, cls, res


def refund_T(census: Mapping[tuple, float]) -> float:
    """T refunded from the shared base: a twiddle's whole Toom decomposition (key[5], at 0 T/rotation), a
    leaf's cell Ands (wx wz) and, for controlled leaves, the base's per-cell control Ands (another wx wz).
    The base prices every And at Qualtran's 4 T, so the refund is 4 T per And whatever `and_t` the new
    Ands are charged."""
    out = 0.0
    for key, cnt in census.items():
        if key[0] == "tw":
            out += float(key[5]) * float(cnt)
        elif key[0] == "leaf":
            wx, wz, ctrl, _, _ = leaf_key_fields(key)
            out += 4.0 * wx * wz * (2 if ctrl else 1) * float(cnt)
    return out


# ----------------------------------------------------------------------------- twiddles

def twiddle_price(r1: int, r2: int, t: int, b: int) -> Tuple[int, float, int]:
    """(native Ands, operator-norm error, rounded rows) of one MergedTwiddle(r1, r2, t, b); vectorised
    `spec.native_twiddle_ands` / `2 pi spec.twiddle_error_turns` (equal: tested)."""
    lo, hi = (r1, r2) if r1 <= r2 else (r2, r1)
    i = np.arange(lo, dtype=np.int64)
    depth = t - i
    L = np.minimum(b, depth)
    m = np.clip(hi - np.maximum(0, depth - b), 0, hi)
    rounded = depth > b
    noop = depth - b > hi
    ands = np.where(noop, 0, m + L - 1 + rounded.astype(np.int64))
    ands = np.where(depth <= 0, 0, ands)
    s = depth - b
    df = depth.astype(np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        err = np.where(~rounded, 0.0,
                       np.where(hi >= s, 2.0 ** (-(b + 1)), np.exp2(hi - df) - np.exp2(-df)))
    return int(ands.sum()), float(2 * math.pi * err.sum()), int(rounded.sum())


# ----------------------------------------------------------------------------- price at b

@dataclass
class CensusPrice:
    """Everything the census costs at one gradient width b (catalyst prep excluded)."""
    b: int
    ands: float = 0.0
    catalysis_error: float = 0.0          # TV: op-norm parts + 1/2 diamond parts
    err_op: float = 0.0
    err_diamond: float = 0.0
    chose_Y: float = 0.0
    chose_P: float = 0.0
    exact_leaves: float = 0.0             # dyadic leaves with res <= b (no rounding at all)
    identity_leaves: float = 0.0          # leaves whose turn is 0 (priced 0; wiring must skip them)
    rounded_twiddle_rows: float = 0.0
    breakdown: Dict[str, float] = field(default_factory=dict)
    leaf_routes: Dict[tuple, str] = field(default_factory=dict)   # census leaf key -> "P" | "Y" | "id"
    leaf_model: str = "leaf"


def _leaf_pricer(leaf_model: str):
    if leaf_model == "spec":
        return lambda wx, wz, b, ctrl, res, mixing: S.leaf_class_cost(wx, wz, b, ctrl, res, mixing=mixing)
    if leaf_model != "leaf":
        raise ValueError(f"leaf_model must be 'leaf' or 'spec', got {leaf_model!r}")
    from . import leaf as LF

    def f(wx, wz, b, ctrl, res, mixing):
        if res is None:
            return LF.leaf_cost_class(wx, wz, b, "generic", None, bool(ctrl), bool(mixing))
        return LF.leaf_cost_class(wx, wz, b, "dyadic", int(res), bool(ctrl), bool(mixing))
    return f


def price_at(census: Mapping[tuple, float], b: int, *, mixing: bool = True,
             leaf_model: str = "leaf") -> CensusPrice:
    pricer = _leaf_pricer(leaf_model)
    cp = CensusPrice(b)
    bd = {"twiddle_ands": 0.0, "leaf_ands": 0.0, "other_ands": 0.0,
          "twiddle_err": 0.0, "leaf_err_op": 0.0, "leaf_err_diamond": 0.0, "other_err_diamond": 0.0,
          "other_err_op": 0.0}
    tw_cache: Dict[tuple, tuple] = {}
    for key, cnt in census.items():
        cnt = float(cnt)
        if cnt <= 0:
            continue
        if key[0] == "tw":
            g = (int(key[1]), int(key[2]), int(key[3]))
            if g not in tw_cache:
                tw_cache[g] = twiddle_price(*g, b)
            a, e, r = tw_cache[g]
            bd["twiddle_ands"] += a * cnt
            bd["twiddle_err"] += e * cnt
            cp.rounded_twiddle_rows += r * cnt
        elif key[0] == "leaf":
            wx, wz, ctrl, cls, res = leaf_key_fields(key)
            lc = pricer(wx, wz, b, ctrl, res, mixing)
            cp.leaf_routes[key] = lc.route if lc.ands else "id"
            bd["leaf_ands"] += lc.ands * cnt
            bd["leaf_err_op"] += lc.err_op * cnt
            bd["leaf_err_diamond"] += lc.err_diamond * cnt
            if lc.ands == 0:
                cp.identity_leaves += cnt
            elif lc.route == "Y":
                cp.chose_Y += cnt
            else:
                cp.chose_P += cnt
            if res is not None and 0 < res <= b:
                cp.exact_leaves += cnt
        elif key[0] == "other":
            oc = S.other_site_cost(b, mixing=mixing)
            bd["other_ands"] += oc.ands * cnt
            bd["other_err_op"] += oc.err_op * cnt
            bd["other_err_diamond"] += oc.err_diamond * cnt
        else:
            raise ValueError(f"unknown census key {key!r}")
    cp.ands = bd["twiddle_ands"] + bd["leaf_ands"] + bd["other_ands"]
    cp.err_op = bd["twiddle_err"] + bd["leaf_err_op"] + bd["other_err_op"]
    cp.err_diamond = bd["leaf_err_diamond"] + bd["other_err_diamond"]
    cp.catalysis_error = cp.err_op + 0.5 * cp.err_diamond
    cp.breakdown = bd
    cp.leaf_model = leaf_model
    return cp


@dataclass
class BChoice:
    """`choose_b`'s answer: THE b, its errors against the partition, and the catalyst's price."""
    b: int
    catalysis_error: float                # TV spent by roundings into the catalyst (<= catalysis_budget)
    rotation_error: float                 # TV spent by the b - 3 synthesised prep rotations
    ands: float                           # native Ands of every merged construction (prep excluded)
    prep_T: float                         # (b - 3) RS rotations + 1 T
    prep_eps: float                       # per synthesised rotation (operator norm)
    catalyst_prep_eps: float              # pass to CatalystPrep(b, eps)
    catalysis_budget: float
    rotation_budget: float
    rotation_unused: float                # reported, NOT moved to catalysis
    and_t: int
    partition: ErrorPartition
    price: CensusPrice

    @property
    def error(self) -> float:
        return self.catalysis_error + self.rotation_error

    @property
    def and_T(self) -> float:
        return self.ands * self.and_t

    @property
    def catalyst_qubits(self) -> int:
        return self.b

    @property
    def chose_Y(self) -> float:
        return self.price.chose_Y

    @property
    def chose_P(self) -> float:
        return self.price.chose_P

    @property
    def leaf_routes(self) -> Dict[tuple, str]:
        """The route priced for each census leaf key: build it (catalytic_leaf(..., route=...))."""
        return self.price.leaf_routes


def choose_b(census: Mapping[tuple, float], partition: Optional[ErrorPartition] = None, *, and_t: int = 4,
             mixing: bool = True, b_min: int = 4, b_max: int = 126, leaf_model: str = "leaf") -> BChoice:
    """Smallest b in [b_min, b_max] whose catalysis error meets `partition.catalysis_budget`; the prep is
    synthesised at `partition.rotation_budget` (each of the b - 3 rotations at rotation_budget / (b - 3))."""
    if partition is None:
        partition = ErrorPartition()
    if not isinstance(partition, ErrorPartition):
        raise TypeError("choose_b takes an ErrorPartition; for an old-style synthesis half `delta_rot` use "
                        "ErrorPartition.from_synthesis_budget(delta_rot)")
    if b_min < 4:
        raise ValueError("b_min must be >= 4 (PhaseGradientState: 3 exact qubits + >= 1 rotation)")
    budget = partition.catalysis_budget
    for b in range(b_min, b_max + 1):
        cp = price_at(census, b, mixing=mixing, leaf_model=leaf_model)
        if cp.catalysis_error <= budget:
            rot_eps = partition.prep_eps(b)
            rot_err = (b - 3) * rot_eps
            return BChoice(b=b, catalysis_error=cp.catalysis_error, rotation_error=rot_err, ands=cp.ands,
                           prep_T=S.catalyst_prep_T(b, partition.rotation_budget), prep_eps=rot_eps,
                           catalyst_prep_eps=partition.catalyst_prep_eps(b), catalysis_budget=budget,
                           rotation_budget=partition.rotation_budget,
                           rotation_unused=partition.rotation_budget - rot_err, and_t=and_t,
                           partition=partition, price=cp)
    raise ValueError(f"no b <= {b_max} meets the catalysis budget {budget:g}")
