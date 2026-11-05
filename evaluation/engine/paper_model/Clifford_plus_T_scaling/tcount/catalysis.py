"""Rotation cost under CATALYSIS instead of per-rotation Ross–Selinger synthesis.

WHAT IS PRICED
--------------
Every rotation the production model charges is a phase-product / twiddle SITE: Qualtran lowers the
controlled phase to `And ; ZPow(2·tau) ; And^dagger`, so the cell And (4 T) is already inside the
non-rotation base `T_base = T(11) − 11·R` and is identical under every route. What differs is the
single-qubit phase diag(1, e^{2πi·tau}) on the And target. Its resolution `s` (tau = c/2^s, c odd)
comes from `forshor_qualtran.rotation_histogram.histogram`, whose total equals `QECGatesCost.rotation`.

Routes (per site, on top of the shared base):

  naive RS     every site  3.075·log2(1/eps) + 0.86,   eps = delta_rot / R        (existing figures)
  catalysis    s = 0: 0;  s = 1, 2: 0 (Z, S);  s = 3: 1 (T)
               4 <= s <= b: exact, A·(s−3) + 1                                   (Amy et al. §VI.C chain:
                                                                                  truncated catalyst of
                                                                                  b−3 qubits, A = 8 unitary
                                                                                  And pairs, 4 measurement)
               s > b, and every `generic` (U1/U5/U2/PP) site: rounded to b bits, A·(b−3) + 1
               pure turn +-2^-s beyond b: dropped (deterministic) or randomly rounded (mixing)
  strong-K     the global denominator shift (notes/kmy-denominator-shift-analysis): every generic
               site exact at K bits, A·(K−3) + 1, no per-site error

ERROR METRIC (all routes held to the same target)
-------------------------------------------------
The rotation share `delta_rot` bounds the total-variation distance of the output distribution.
Deterministic routes: TV <= sum of operator-norm site errors (the existing RS convention, eps =
delta_rot / R). A b-bit rounding of a turn has operator-norm error <= pi·2^-b; dropping a pure turn
2^-s costs <= 2pi·2^-s. Mixed routes: TV <= (1/2)·sum of diamond-norm site errors, so the diamond sum
may reach 2·delta_rot; unbiased random rounding of a turn with residuals (u, d) has diamond error
<= 8pi²·u·d <= 2pi²·4^-b (notes/mixing-04-formal-theory Thm 1). Sites realised exactly spend nothing,
so `b` is sized on the non-exact sites only -- a saving RS, being angle-blind, cannot make.

One catalyst (b−3 qubits) serves every constant; its preparation is b−3 single-qubit phase states,
each synthesised once at the tolerance `prep_fraction·delta_rot / (b−3)` and charged once.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, Mapping, Optional, Tuple, Union

from .budget import ross_selinger_t

Key = Union[int, str]

#: T per And pair inside the catalyst adder: 8 = unitary uncompute (gate-verified site circuit,
#: exact_dyadic_catalysis/code/amortized_site_circuit.py), 4 = measurement-based uncompute (cited).
PAIR_T_UNITARY = 8
PAIR_T_MEASURED = 4

#: Mixed-fallback synthesis law (Kliuchnikov et al. 2023, the source of Qualtran's 11 T at 1e-3).
MIXED_FALLBACK = (0.53, 4.86)


# ----------------------------------------------------------------------------- histogram keys

def split_key(key: Key) -> Tuple[Optional[int], bool, Optional[int]]:
    """(lowest resolution, pure, highest resolution) for a compressed or raw histogram key.

    Raw keys are ints `s` or `'p<s>'`; `rotation_histogram.compress` adds power-of-two bins
    `'bin:lo-hi'` / `'pbin:lo-hi'`. `generic` / `symbolic` give (None, False, None).
    """
    if isinstance(key, int):
        return key, False, key
    k = str(key)
    pure = k.startswith("p")
    body = k[1:] if pure else k
    if body.isdigit():
        s = int(body)
        return s, pure, s
    if body.startswith("bin:"):
        lo, hi = body[4:].split("-")
        return int(lo), pure, int(hi)
    if k.lstrip("-").isdigit():                      # JSON round-trip turns int keys into strings
        s = int(k)
        return s, False, s
    return None, False, None


# ----------------------------------------------------------------------------- per-site prices

def exact_site_t(s: int, pair_t: int = PAIR_T_UNITARY) -> int:
    """T for an exactly realised site of resolution s, on the And target."""
    if s <= 2:
        return 0                                      # identity, Z, S
    if s == 3:
        return 1                                      # T
    return pair_t * (s - 3) + 1                       # truncated-catalyst addition (s−3 qubits)


def rounded_site_t(b: int, pair_t: int = PAIR_T_UNITARY) -> int:
    """T for a site rounded to b bits and added into the catalyst."""
    return exact_site_t(b, pair_t)


@dataclass
class CatalysisPrice:
    """Priced rotations of one circuit under catalysis at gradient width `b`."""
    b: int
    mixing: bool
    pair_t: int
    rot_T: float                 # expected T of all sites (excluding the shared base)
    prep_T: float                # one-time catalyst preparation
    error: float                 # TV bound actually spent (<= delta_rot)
    exact_sites: float
    rounded_sites: float
    dropped_sites: float
    catalyst_qubits: int
    by_class: Dict[str, float] = field(default_factory=dict)

    @property
    def total_T(self) -> float:
        return self.rot_T + self.prep_T


def _price_at(hist: Mapping[Key, float], b: int, mixing: bool, pair_t: int):
    """(rot_T, TV error, exact, rounded, dropped, by_class) at a fixed b."""
    rot_T = err = 0.0
    n_ex = n_rd = n_dr = 0.0
    cls = {"exact_T": 0.0, "rounded_T": 0.0, "dropped_T": 0.0}
    site_b = rounded_site_t(b, pair_t)
    for key, cnt in hist.items():
        cnt = float(cnt)
        if cnt <= 0:
            continue
        lo, pure, hi = split_key(key)
        if lo is not None and hi <= b:                       # exact at its own resolution
            t = exact_site_t(hi, pair_t) * cnt                # bins priced at their top (upper bound)
            rot_T += t; n_ex += cnt; cls["exact_T"] += t
            continue
        if lo is not None and pure and lo > b:              # a pure turn +-2^-s finer than the register
            s = lo
            if mixing:
                # unbiased rounding to {0, 2^-b}: nonzero with probability 2^(b-s); diamond <= 8pi² u d
                p = 2.0 ** (b - s)
                t = p * site_b * cnt
                rot_T += t; n_rd += p * cnt; n_dr += (1 - p) * cnt; cls["dropped_T"] += t
                err += 0.5 * 8 * math.pi ** 2 * 2.0 ** (-s) * 2.0 ** (-b) * cnt
            elif s >= b + 2:
                n_dr += cnt
                err += 2 * math.pi * 2.0 ** (-s) * cnt        # drop: |1 - e^{2pi i 2^-s}| <= 2pi 2^-s
            else:                                             # s = b+1: round like any other site
                t = site_b * cnt
                rot_T += t; n_rd += cnt; cls["rounded_T"] += t
                err += math.pi * 2.0 ** (-b) * cnt
            continue
        # generic / symbolic / resolution above b (or a bin straddling b): rounded to b bits
        t = site_b * cnt
        rot_T += t; n_rd += cnt; cls["rounded_T"] += t
        err += (0.5 * 2 * math.pi ** 2 * 4.0 ** (-b) if mixing else math.pi * 2.0 ** (-b)) * cnt
    return rot_T, err, n_ex, n_rd, n_dr, cls


def catalyst_prep_t(b: int, delta_prep: float) -> float:
    """b−3 single-qubit phase states (resolutions 4..b), each synthesised once (RS) at delta_prep/(b−3).
    The catalyst's error is paid once, however many sites use it (unitarity; EDC Thm C)."""
    m = max(0, b - 3)
    if m == 0:
        return 0.0
    return m * ross_selinger_t(delta_prep / m)


def price_catalysis(hist: Mapping[Key, float], delta_rot: float, *, mixing: bool = False,
                    pair_t: int = PAIR_T_UNITARY, prep_fraction: float = 1e-3,
                    b_max: int = 126) -> CatalysisPrice:
    """Cheapest gradient width b meeting the rotation budget, and the price at that b.

    Cost is non-decreasing in b (a rounded site costs A(b−3)+1; an exact site's cost does not depend
    on b), so the smallest feasible b is optimal.
    """
    if delta_rot <= 0:
        raise ValueError("delta_rot must be positive")
    total = sum(float(v) for v in hist.values())
    if total <= 0:
        return CatalysisPrice(0, mixing, pair_t, 0.0, 0.0, 0.0, 0, 0, 0, 0)
    d_prep = prep_fraction * delta_rot
    budget = delta_rot - d_prep
    for b in range(4, b_max + 1):
        rot_T, err, n_ex, n_rd, n_dr, cls = _price_at(hist, b, mixing, pair_t)
        if err <= budget:
            needs_catalyst = (n_rd > 0) or any(
                (split_key(k)[2] or 0) >= 4 and (split_key(k)[2] or 0) <= b for k in hist)
            prep = catalyst_prep_t(b, d_prep) if needs_catalyst else 0.0
            return CatalysisPrice(b, mixing, pair_t, rot_T, prep, err + (d_prep if prep else 0.0),
                                  n_ex, n_rd, n_dr, (b - 3) if needs_catalyst else 0, cls)
    raise ValueError(f"no gradient width <= {b_max} meets delta_rot={delta_rot:g} "
                     f"(compress the histogram with exact_up_to >= b_max)")


def price_strong_exact(hist: Mapping[Key, float], K_generic: int, delta_rot: float, *,
                       pair_t: int = PAIR_T_UNITARY) -> CatalysisPrice:
    """Strong exact catalysis: generic sites exact at K bits (global denominator shift); every dyadic
    site exact at its own resolution. The shift's single global error is budgeted inside K."""
    rot_T = 0.0
    n = 0.0
    for key, cnt in hist.items():
        lo, pure, hi = split_key(key)
        s = K_generic if lo is None else hi
        rot_T += exact_site_t(s, pair_t) * float(cnt)
        n += float(cnt)
    prep = catalyst_prep_t(K_generic, 1e-3 * delta_rot)
    return CatalysisPrice(K_generic, False, pair_t, rot_T, prep, delta_rot, n, 0, 0,
                          K_generic - 3, {"exact_T": rot_T})


# ----------------------------------------------------------------------------- synthesis controls

def rs_det_t(R: float, delta_rot: float) -> float:
    """Naive Ross–Selinger, operator-norm union bound (per site)."""
    return ross_selinger_t(delta_rot / R)


def rs_mix_t(R: float, delta_rot: float) -> float:
    """Ross–Selinger + mixing (Campbell 2017 / Hastings 2017), per site. ASSUMPTION: mixing two
    eps-approximations gives diamond error ~2·eps² (constant unverified; +-1 bit). Diamond budget
    2·delta_rot / R per site  ->  eps = sqrt(delta_rot / R)."""
    return ross_selinger_t(math.sqrt(delta_rot / R))


def mixed_fallback_t(R: float, delta_rot: float) -> float:
    """Mixed fallback (Kliuchnikov et al. 2023), T = 0.53·log2(1/eps) + 4.86 at diamond eps
    = 2·delta_rot / R per site. The reading of eps as diamond distance is to be verified."""
    a, c = MIXED_FALLBACK
    return a * math.log2(R / (2.0 * delta_rot)) + c


def strong_K(n: int, w_work: int, n_mults: int, delta_rot: float) -> int:
    """K = n + w_work + ceil(log2(pi·L/delta_rot)): one rounding of kappa/N to Q/2^K per multiply,
    ||PP(phi') − PP(phi)|| <= 2pi·N·2^m·2^-(K+1) (kmy-denominator-shift note §2), union over L."""
    return int(n + w_work + math.ceil(math.log2(math.pi * max(1, n_mults) / delta_rot)))


# ============================================================================= MERGED catalysis
#
# Amy et al. §VI.C (p.29): every rotation sharing a target is ONE adder into the catalyst. Applied to a
# phase-product grid exp(2pi i tau x z), row i's phase is x_i·frac(tau·2^i·z): one controlled addition
# of a b-bit value into a b-bit phase-gradient catalyst per row, instead of w per-site additions or
# syntheses. Construction-level costs below are And counts (Gidney adders: an m-bit carry chain is m
# Ands; ANDing a control into an m-bit addend is m more; adding a classical constant under a control
# needs no addend Ands); `and_t` T per And (4 = Qualtran's convention, which the shared base uses).
#
#   pure twiddle (QFT) PhaseProdUsing(2pi/2^t, r1, r2): row i adds z·2^(i-t) mod 1, the SHIFTED z
#       register itself -- no arithmetic. Adder length L_i = min(b, t-i), control Ands m_i = the z bits
#       landing inside the b kept positions. Rows with t-i > b drop bits: one rounding, <= pi 2^-b.
#   generic leaf (w x w, turn tau not a pure power of two), cheaper of
#       Y   Y = frac(tau z) to b+w+g bits by w controlled-constant additions (compute + uncompute),
#           then w rows each adding a b-bit window of Y under x_i:     2w(b+w+g) + 2wb (+w ctrl)
#           error per row <= 2pi 2^-b (rounding + constant truncation, g = ceil(log2 w)+1 guard bits)
#       P   p = x·z exactly (schoolbook, compute + uncompute 4w^2), then 2w controlled-constant
#           additions frac(tau 2^l) into the catalyst:                  4w^2 + 2w(b+1)
#           error per term <= pi 2^-b
#   The base's per-cell And (and the U1/U5 control And) are refunded; a twiddle's whole Toom
#   decomposition (adders + cell Ands) is refunded, since the merged twiddle is not decomposed.
#
# Mixing is NOT applied to merged rows: an added REGISTER value can only be unbiasedly rounded by
# dithering over its dropped bits, whose bias term is first order -- so the dither must reach the
# deterministic width anyway. Merged routes are priced deterministically.
#
# V2 (2026-09-23, decisions D1-D5): `price_merged` now prices the NATIVE bloq counts with EXACT error units
# through `forshor_qualtran.catalysis.budget.choose_b` (shared with the wired bloqs); CLASSICAL constants
# (P kicks, `other` kicks) are mixed (D5), register rows stay deterministic as argued above. The comment
# block above describes the v1 contract, still reachable as `price_merged_v1` (+ `_twiddle_rows`,
# `_leaf_options`).

import numpy as _np


def _twiddle_rows(r1: int, r2: int, t: int, b: int):
    """(Ands, rounded rows) for one merged pure twiddle; rows over the shorter register."""
    if r1 > r2:
        r1, r2 = r2, r1
    i = _np.arange(r1, dtype=_np.int64)
    depth = t - i                                        # position of z's lowest bit in row i
    L = _np.minimum(b, depth)
    m = _np.clip(r2 - _np.maximum(0, depth - b), 0, r2)
    ands = int((L + m).sum())
    rounded = int((depth > b).sum())
    return ands, rounded


def _leaf_options(w: int, b: int, ctrl: bool):
    """[(Ands, error units in pi 2^-b)] for the Y and P constructions of one generic w x w leaf."""
    g = max(1, math.ceil(math.log2(max(w, 2)))) + 1
    y = 2 * w * (b + w + g) + 2 * w * b + (w if ctrl else 0)
    p = 4 * w * w + 2 * w * (b + 1) + (2 * w if ctrl else 0)
    return [(y, 2.0 * w), (p, 1.0 * 2 * w)]


@dataclass
class MergedPrice:
    b: int
    delta_T: float                      # route T minus the shared base (refunds included)
    ands: float
    refund_T: float
    prep_T: float
    error: float                        # TV spent: v1 rows + prep; v2 catalysis_error + rotation_error
    chose_Y: float = 0.0                # leaves priced with the Y construction
    chose_P: float = 0.0
    # ---- v2 only (price_merged); v1 leaves the defaults
    version: str = "v1"
    catalysis_error: float = 0.0
    rotation_error: float = 0.0
    catalysis_budget: float = 0.0
    rotation_budget: float = 0.0
    rotation_unused: float = 0.0
    prep_eps: float = 0.0               # per synthesised catalyst rotation
    catalyst_prep_eps: float = 0.0      # the eps to hand CatalystPrep(b, eps)
    exact_leaves: float = 0.0
    identity_leaves: float = 0.0
    breakdown: Dict[str, float] = field(default_factory=dict)


def price_merged_v1(census: Mapping[tuple, float], delta_rot: float, *, and_t: int = 4,
                    prep_fraction: float = 1e-3, b_max: int = 126) -> MergedPrice:
    """V1 CONTRACT (the figures up to 2026-09-23; kept verbatim): smallest b meeting delta_rot with a flat
    pi 2^-b per rounded twiddle row, 2w units per generic leaf, the `_twiddle_rows` / `_leaf_options`
    And counts, and prep at prep_fraction x delta_rot. Superseded by `price_merged` (v2)."""
    d_prep = prep_fraction * delta_rot
    budget = delta_rot - d_prep
    refund = 0.0
    for key, cnt in census.items():
        if key[0] == "tw":
            refund += float(key[5]) * cnt                     # Toom decomposition at 0 T/rotation
        elif key[0] == "leaf":
            w, ctrl = int(key[1]), bool(key[2])
            refund += 4.0 * w * w * (2 if ctrl else 1) * cnt   # cell And (+ control And), Qualtran 4 T
    for b in range(4, b_max + 1):
        ands = err_units = 0.0
        cy = cp = 0.0
        for key, cnt in census.items():
            if key[0] == "tw":
                a, r = _twiddle_rows(int(key[1]), int(key[2]), int(key[3]), b)
                ands += a * cnt
                err_units += r * cnt
            elif key[0] == "leaf":
                (ya, ye), (pa, pe) = _leaf_options(int(key[1]), b, bool(key[2]))
                if ya <= pa:
                    ands += ya * cnt; err_units += ye * cnt; cy += cnt
                else:
                    ands += pa * cnt; err_units += pe * cnt; cp += cnt
            elif key[0] == "other":
                ands += cnt * (b - 3); err_units += cnt        # per-site catalysis fallback
        err = err_units * math.pi * 2.0 ** (-b)
        if err <= budget:
            prep = catalyst_prep_t(b, d_prep)
            return MergedPrice(b, ands * and_t - refund + prep, ands, refund, prep, err + d_prep, cy, cp)
    raise ValueError("no b meets the budget")


def _budget_module():
    """`forshor_qualtran.catalysis.budget` (THE b rule shared with the wired bloqs)."""
    try:
        from forshor_qualtran.catalysis import budget
    except ImportError:                                   # tcount used without the pipeline root on sys.path
        import sys
        from pathlib import Path
        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path:
            sys.path.insert(0, root)
        from forshor_qualtran.catalysis import budget
    return budget


def price_merged(census: Mapping[tuple, float], delta_rot: Optional[float] = None, *, partition=None,
                 and_t: int = 4, mixing: bool = True, prep_fraction: Optional[float] = None,
                 b_max: int = 126) -> MergedPrice:
    """Merged catalysis, V2 pricing (decisions D1-D5, docs/CATALYSIS_BLOQ_SPEC.md "v2 pricing").

    b comes from `forshor_qualtran.catalysis.budget.choose_b` -- the same b the wired bloqs use:
      * native bloq Ands: twiddle rows of `twiddle.twiddle_row_plan` (exact row m+L-1, rounded m+L,
        no-op 0); leaves `spec.leaf_class_cost` (P 4 wx wz + (b-1)/nonzero kick, Y 2(A-1)/nonzero C_j
        + 2b/row; ctrl AND x once = wx Ands, D4); `other` b-1; and_t T per And (D1: 4)
      * exact errors: twiddle rows at their true max over z; dyadic leaves of resolution <= b exact (D3);
        P kicks and `other` kicks MIXED (D5, 1/2 diamond in TV); Y rows deterministic
      * partition: structure / catalysis / rotation = 0.5 / 0.25 / 0.25 of delta. `delta_rot` (the older
        callers' synthesis half, e.g. the figures' `RotationBudget.delta_rot`) is catalysis + rotation, so
        by default delta = 2 delta_rot; pass `partition` to set it directly.
      * prep: (b-3) RS rotations at rotation_budget/(b-3) + 1 T (PhaseGradientState), charged once.
    `prep_fraction` is a v1 argument and is ignored (the rotation share replaces it).
    The old contract is `price_merged_v1`."""
    B = _budget_module()
    if partition is None:
        if delta_rot is None:
            partition = B.ErrorPartition()
        else:
            partition = B.ErrorPartition.from_synthesis_budget(float(delta_rot))
    ch = B.choose_b(census, partition, and_t=and_t, mixing=mixing, b_max=b_max)
    refund = B.refund_T(census)
    delta_T = ch.ands * and_t - refund + ch.prep_T
    return MergedPrice(ch.b, delta_T, ch.ands, refund, ch.prep_T, ch.error, ch.chose_Y, ch.chose_P,
                       version="v2", catalysis_error=ch.catalysis_error, rotation_error=ch.rotation_error,
                       catalysis_budget=ch.catalysis_budget, rotation_budget=ch.rotation_budget,
                       rotation_unused=ch.rotation_unused, prep_eps=ch.prep_eps,
                       catalyst_prep_eps=ch.catalyst_prep_eps, exact_leaves=ch.price.exact_leaves,
                       identity_leaves=ch.price.identity_leaves, breakdown=dict(ch.price.breakdown))


def price_rs_restructured(census: Mapping[tuple, float], delta_rot: float, *,
                          per_rot: Callable[[float, float], float] = None, and_t: int = 4) -> float:
    """BUDGET NOTE (v2, 2026-09-23): this RS control is unchanged. It has no catalyst, so it keeps the whole
    synthesis half (catalysis + rotation = 0.5 delta = `delta_rot` as the figures pass it) for its
    synthesised rotations, while merged catalysis splits that half 0.25 / 0.25 (catalysis / prep).

    FAIRNESS CONTROL: the best a synthesis route can do with the same leaf restructuring, but no
    catalyst. Per item, the cheaper of
      kept           the production grid, every cell a synthesised rotation (cell Ands stay in the base)
      restructured   p = x·z computed digitally, then one synthesised rotation per bit of p:
                     generic leaf  4w^2 Ands (+2w ctrl) and 2w rotations frac(tau 2^l)
                     pure twiddle  4 r1 r2 Ands and r1+r2 dyadic rotations 2^(l-t) (resolution <= 3 exact)
    Every synthesised rotation shares one tolerance delta_rot / R' (R' = rotations actually kept),
    found by two fixed-point passes. Returns route T minus the shared base (refunds included)."""
    per_rot = per_rot or rs_det_t
    items = []
    R0 = 0.0
    for key, cnt in census.items():
        if key[0] == "tw":
            r1, r2, t, rot0, t0 = int(key[1]), int(key[2]), int(key[3]), float(key[4]), float(key[5])
            rot_r = sum(1 for l in range(r1 + r2) if t - l > 3)
            items.append((cnt, rot0, 0.0, 0.0, rot_r, 4.0 * r1 * r2, t0))
            R0 += rot0 * cnt
        elif key[0] == "leaf":
            w, ctrl = int(key[1]), bool(key[2])
            refund = 4.0 * w * w * (2 if ctrl else 1)
            items.append((cnt, float(w * w), 0.0, 0.0, 2.0 * w, 4.0 * w * w + (2 * w if ctrl else 0), refund))
            R0 += w * w * cnt
        elif key[0] == "other":
            items.append((cnt, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
            R0 += cnt
    R = R0
    total = 0.0
    for _ in range(3):
        c = per_rot(max(R, 1.0), delta_rot)
        total, R_new = 0.0, 0.0
        for cnt, rot_k, ands_k, ref_k, rot_r, ands_r, ref_r in items:
            kept = rot_k * c + ands_k * and_t - ref_k
            restr = rot_r * c + ands_r * and_t - ref_r
            if restr < kept:
                total += restr * cnt; R_new += rot_r * cnt
            else:
                total += kept * cnt; R_new += rot_k * cnt
        R = max(R_new, 1.0)
    return total
