"""Reference specification for MERGED catalysis (Amy et al., arXiv:2305.07720 §VI.C, p.29).

Pure Python (fractions / math / dataclasses only; no `qualtran` import here). The bloqs in
`forshor_qualtran/catalysis/{catalyst,twiddle,leaf}.py` and their tests import this module for the
three things the design must pin down exactly:

  1. ERROR PARTITION   `ErrorPartition`, `choose_b`, `UNIT` bookkeeping          (docs §1)
  2. ANGLE CONSTRUCTION exact `Fraction` turns for every site family, rounding rules, the b-bit
                        constants each bloq adds, and the exact diagonal it must realise (docs §2)
  3. COSTING           the `tcount.catalysis` contract And counts and the Qualtran-native And counts
                        of the prescribed decompositions, with their signed deltas              (docs §3)

The prose is `docs/CATALYSIS_BLOQ_SPEC.md`; section numbers below refer to it.

V2 (2026-09-23, decisions D1-D5, docs §8 "v2 pricing"): section 4 below holds the v2 formulas (exact
twiddle row errors, mixing bounds, the coarse leaf class bound, prep). The v2 partition and b rule live
in `catalysis.budget` (`spec.ErrorPartition` / `spec.choose_b` / `spec.BChoice` resolve there); the v1
contract keeps `ErrorPartitionV1`, `choose_b_v1`, `census_error_units`.

Conventions (docs §2.0)
-----------------------
* A TURN is a rational `tau` in [0, 1); the phase is exp(2 pi i tau). The emitter's `phi` is in units
  of pi (CZPow exponent), so tau = phi / 2.
* A phase-product grid on registers x (wx bits) and z (wz bits) realises
      U_tau |x>|z> = exp(2 pi i * tau * s(x) * s(z)) |x>|z>
  with s(.) the two's-complement value (bit i has weight +2^i, the top bit -2^(w-1)) for Toom
  leaves; QFT twiddles and root U1/U2/U5/MULT products act on zero-extended (hence non-negative)
  registers, where signed and unsigned coincide.
* The catalyst is Qualtran's `PhaseGradientState(b)` (exponent = -1):
      |psi_b> = 2^(-b/2) sum_k exp(-2 pi i k / 2^b) |k>.
  ADDING an integer v (mod 2^b) into it kicks back exp(+2 pi i v / 2^b); subtracting kicks back the
  conjugate. A site of sign -1 (U5, every IQFT twiddle) subtracts.
* ROUNDING of a turn to b bits is round-half-UP, `floor(v 2^b + 1/2) mod 2^b`: exactly what a ripple
  adder computes when the next-lower bit is fed in as carry-in. The same rule is used for classical
  constants so register rows and constant rows agree bit for bit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    # turns
    "turn_from_phi", "step5_constant", "turn_u1", "turn_u5", "turn_u2", "turn_twiddle", "turn_mult",
    "SITE_SIGN", "poly_eval", "child_turn", "leaf_turn", "resolution", "signed_weight", "signed_value",
    "grid_turn",
    # rounding
    "round_nearest", "truncate", "phase_distance",
    # twiddle rows
    "RowGeometry", "twiddle_row", "twiddle_rows", "twiddle_row_value", "merged_twiddle_turn",
    # generic leaf
    "guard_bits", "p_constants", "y_constants", "y_register", "y_row_value", "merged_leaf_turn_P",
    "merged_leaf_turn_Y",
    # error (v1 contract; `ErrorPartition` / `choose_b` / `BChoice` now resolve to catalysis.budget, v2)
    "UNIT", "unit", "ErrorPartition", "ErrorPartitionV1", "census_error_units", "census_error_units_v1",
    "choose_b", "choose_b_v1", "per_site_b", "strict_twiddle_units", "strict_leaf_units",
    # costing
    "contract_twiddle_ands", "contract_leaf_ands", "contract_other_ands", "contract_refund_T",
    "native_twiddle_row_ands", "native_twiddle_ands", "native_leaf_ands", "catalyst_prep_rotations",
    "QUALTRAN_ADD_ANDS", "QUALTRAN_CTRL_ADD_ANDS", "QUALTRAN_PRODUCT_TOFFOLI", "QUALTRAN_AIPG_TOFFOLI",
    # v2 pricing (docs "v2 pricing"): decisions D1-D5
    "AND_T", "C_SYN_MEASURED", "ross_selinger_t", "twiddle_row_error_turns", "twiddle_error_turns",
    "mix_residuals", "mixing_diamond", "mixing_diamond_max", "LeafCost", "leaf_y_acc", "native_leaf_ands_v2",
    "leaf_class_cost", "leaf_cost_mirror", "other_site_cost", "catalyst_prep_T", "catalyst_prep_eps",
]

# ============================================================================= 2. ANGLE CONSTRUCTION


def turn_from_phi(phi: Fraction) -> Fraction:
    """tau = phi / 2 mod 1 (phi in units of pi, as the Lean emitter and `CZPowGate.exponent` use)."""
    return (Fraction(phi) / 2) % 1


def step5_constant(c: int, N: int) -> int:
    """`Shor.step5Constant c N` = (1 + N - c^-1 mod N) mod N. REFUSES gcd(c, N) != 1 (the Lean
    precondition, as `bloqs/modmul_w_gadgets.step5_constant` does). NOTE: `family.step5_constant`
    returns 0 in that case instead -- a silent identity U5; see docs §5 (conflict C6)."""
    c, N = int(c), int(N)
    if N <= 1 or math.gcd(c % N, N) != 1:
        raise ValueError(f"step5Constant({c}, {N}) undefined: gcd(c, N) != 1")
    return (1 + N - pow(c % N, -1, N)) % N


def turn_u1(c: int, N: int) -> Fraction:
    """U1 (controlled): tau1 = kappa1 / N, kappa1 = (c - 1) mod N  (phi1 = 2 kappa1 / N)."""
    return Fraction((int(c) + int(N) - 1) % int(N), int(N))


def turn_u5(c: int, N: int) -> Fraction:
    """U5 (controlled, applied as an ADJOINT, i.e. sign -1): tau5 = kappa5 / N, kappa5 = step5Constant."""
    return Fraction(step5_constant(c, N) % int(N), int(N))


def turn_u2(N: int, w_work: int, w_carry: int) -> Fraction:
    """U2 (uncontrolled): tau2 = N / 2^(w_work + w_carry)  (phi2 = 2N / 2^(ww + wc)); dyadic,
    resolution ww + wc (> every leaf width, so Thm 5.1 clamping never fires)."""
    return Fraction(int(N), 1 << (int(w_work) + int(w_carry))) % 1


def turn_twiddle(t: int) -> Fraction:
    """LeanQFT(w) twiddle PhaseProdUsing(2 pi / 2^w, left, right): tau = 2^-t, t = w = left + right."""
    return Fraction(1, 1 << int(t))


def turn_mult(c: int, w_out: int) -> Fraction:
    """ToomCookMultiplier: phi = 2 pi c / 2^w_out, tau = c / 2^w_out (the figures build w_out = n;
    a full product register w_out = 2n gives c / 2^(2n)). Dyadic, resolution <= w_out."""
    return Fraction(int(c), 1 << int(w_out)) % 1


#: sign with which each site family's grid enters the catalyst (+1 add, -1 subtract).
SITE_SIGN: Dict[str, int] = {"U1": +1, "U2": +1, "U5": -1, "MULT": +1, "QFT": +1, "IQFT": -1}


def poly_eval(coeffs: Sequence[Fraction], x: Fraction) -> Fraction:
    """sum_j coeffs[j] x^j (Horner), exact."""
    acc = Fraction(0)
    for c in reversed(list(coeffs)):
        acc = Fraction(c) + x * acc
    return acc


def child_turn(parent: Fraction, poly: Sequence[Fraction], m: int) -> Fraction:
    """Toom child l of a node with limb width m: tau_child = tau_parent * c_l(2^m) mod 1, with c_l the
    l-th row of M^-1 as a polynomial in the radix (`mirrors/coeff_poly.coeff_polys`). versionB/kmy
    uses m = ceil(W / k) at a node of width W. The polynomials have rational coefficients (1/6, 1/36,
    ...), but EVALUATED at a power-of-two radix every c_l(2^m) is an INTEGER (checked k = 2..7,
    m = 1..24 in tests): a child is the parent times an integer, so dyadic turns stay dyadic with
    resolution <= the parent's (only purity, tau = +-2^-s, is lost) and kappa/N children keep
    denominator | N."""
    return (Fraction(parent) * poly_eval(poly, Fraction(2) ** int(m))) % 1


def leaf_turn(root: Fraction, path: Iterable[Tuple[Sequence[Fraction], int]]) -> Fraction:
    """Turn of a Toom leaf reached from `root` along `path = [(poly_l, m), ...]` (one entry per level)."""
    t = Fraction(root) % 1
    for poly, m in path:
        t = child_turn(t, poly, m)
    return t


def resolution(turn: Fraction) -> Optional[int]:
    """s with turn = odd / 2^s (0 for the identity), or None when an odd prime divides the denominator."""
    t = Fraction(turn) % 1
    if t == 0:
        return 0
    d = t.denominator
    if d & (d - 1):
        return None
    return d.bit_length() - 1


def signed_weight(width: int, i: int) -> int:
    """Two's-complement weight of bit i (0 = LSB); the top bit is -2^(width-1)."""
    if not 0 <= i < width:
        raise ValueError(f"bit {i} outside a {width}-bit register")
    return -(1 << i) if i == width - 1 else (1 << i)


def signed_value(bits: int, width: int) -> int:
    """s(bits) for a width-bit two's-complement pattern."""
    bits &= (1 << width) - 1
    return bits - (1 << width) if bits >> (width - 1) & 1 else bits


def grid_turn(tau: Fraction, x: int, z: int) -> Fraction:
    """THE diagonal every merged bloq approximates: tau * x * z mod 1 (x, z are VALUES, signed or not)."""
    return (Fraction(tau) * x * z) % 1


# ----------------------------------------------------------------------------- rounding

def round_nearest(v: Fraction, b: int) -> int:
    """round-half-up of the turn v (mod 1) to b bits, as an integer in [0, 2^b): floor(v 2^b + 1/2)."""
    v = Fraction(v) % 1
    return math.floor(v * (1 << b) + Fraction(1, 2)) % (1 << b)


def truncate(v: Fraction, bits: int) -> int:
    """floor(frac(v) 2^bits): the truncated constant (error in [0, 2^-bits))."""
    return math.floor((Fraction(v) % 1) * (1 << bits))


def phase_distance(a: Fraction, b: Fraction) -> float:
    """|exp(2 pi i a) - exp(2 pi i b)| = 2 |sin(pi (a - b))|, computed from the exact difference."""
    d = float((Fraction(a) - Fraction(b)) % 1)
    return 2.0 * abs(math.sin(math.pi * d))


# ----------------------------------------------------------------------------- QFT twiddle rows

@dataclass(frozen=True)
class RowGeometry:
    """Row i of a merged pure twiddle tau = 2^-t over (r1 <= r2): adds x_i * z * 2^(i - t) mod 1.

    depth  binary position of z's LSB (weight 2^-depth); z bit j sits at position depth - j
    L      adder length min(b, depth): catalyst bits the carry chain spans
    m      z bits landing inside the kept b positions (the control Ands x_i AND z_j)
    rounded depth > b: the row drops bits and is rounded (carry-in = z bit at position b + 1)
    noop   rounded and z lies entirely below position b + 1: the row rounds to 0 for every z
    right_shift  AddIntoPhaseGrad(x_bitsize=r2, phase_bitsize=b, right_shift=depth - r2) equivalent
    """
    i: int
    depth: int
    L: int
    m: int
    rounded: bool
    noop: bool
    right_shift: int


def twiddle_row(i: int, r1: int, r2: int, t: int, b: int) -> RowGeometry:
    lo, hi = (r1, r2) if r1 <= r2 else (r2, r1)
    if not 0 <= i < lo:
        raise ValueError(f"row {i} outside the shorter register ({lo} bits)")
    depth = t - i
    L = min(b, depth)
    m = min(max(hi - max(0, depth - b), 0), hi)
    rounded = depth > b
    noop = rounded and depth - b > hi          # r bit (index depth-b-1) beyond z's top: value 0
    return RowGeometry(i, depth, L, m, rounded, noop, depth - hi)


def twiddle_rows(r1: int, r2: int, t: int, b: int) -> List[RowGeometry]:
    lo = min(r1, r2)
    return [twiddle_row(i, r1, r2, t, b) for i in range(lo)]


def twiddle_row_value(z: int, i: int, t: int, b: int, mode: str = "nearest") -> int:
    """The integer row i adds into the b-bit catalyst when x_i = 1: z * 2^(i - t) to b bits.
    mode 'nearest' (default, carry-in rounding, error <= 2^-(b+1) turns) or 'floor' (what Qualtran's
    `AddIntoPhaseGrad` computes, error < 2^-b turns)."""
    v = Fraction(int(z), 1 << (t - i))
    if mode == "nearest":
        return round_nearest(v, b)
    if mode == "floor":
        return truncate(v, b)
    raise ValueError(mode)


def merged_twiddle_turn(x: int, z: int, r1: int, r2: int, t: int, b: int, mode: str = "nearest") -> Fraction:
    """Realised turn of the merged twiddle: x over the SHORTER register, z over the longer (both
    unsigned). Exact (== grid_turn) iff no row is rounded."""
    lo = min(r1, r2)
    V = sum(twiddle_row_value(z, i, t, b, mode) for i in range(lo) if x >> i & 1)
    return Fraction(V % (1 << b), 1 << b)


# ----------------------------------------------------------------------------- generic leaf (Y / P)

def guard_bits(w: int) -> int:
    """g = ceil(log2 max(w, 2)) + 1 (>= 2); 2^g >= 2w, which bounds the Y truncation (docs §1.4)."""
    return max(1, math.ceil(math.log2(max(w, 2)))) + 1


def p_constants(tau: Fraction, w: int, b: int) -> List[int]:
    """P route: k_l = round_nearest(tau * wp_l, b) for the 2w bits of the signed product p = x*z
    (wp_l = 2^l, the sign bit wp_{2w-1} = -2^(2w-1)). Bit l of p (two's complement, 2w bits)
    controls the classical-constant addition of k_l."""
    return [round_nearest(Fraction(tau) * signed_weight(2 * w, l), b) for l in range(2 * w)]


def y_constants(tau: Fraction, w: int, b: int) -> List[int]:
    """Y route: C_j = truncate(tau * wz_j, A), A = b + w + g, for the w bits of z (signed weights)."""
    A = b + w + guard_bits(w)
    return [truncate(Fraction(tau) * signed_weight(w, j), A) for j in range(w)]


def y_register(tau: Fraction, z_bits: int, w: int, b: int) -> int:
    """Y = sum_j z_j C_j mod 2^A (the A-bit register the w controlled-constant additions build)."""
    A = b + w + guard_bits(w)
    C = y_constants(tau, w, b)
    return sum(C[j] for j in range(w) if z_bits >> j & 1) % (1 << A)


def y_row_value(Y: int, i: int, w: int, b: int) -> int:
    """|value| row i adds (x bit i < w-1) or SUBTRACTS (the sign bit i = w-1): round_nearest(frac(2^i Y), b),
    i.e. the b-bit window Y[A-i-b .. A-i-1] plus carry-in bit Y[A-i-b-1]."""
    A = b + w + guard_bits(w)
    return round_nearest(Fraction(Y << i, 1 << A), b)


def merged_leaf_turn_P(tau: Fraction, x_bits: int, z_bits: int, w: int, b: int) -> Fraction:
    p = (signed_value(x_bits, w) * signed_value(z_bits, w)) % (1 << (2 * w))
    k = p_constants(tau, w, b)
    V = sum(k[l] for l in range(2 * w) if p >> l & 1)
    return Fraction(V % (1 << b), 1 << b)


def merged_leaf_turn_Y(tau: Fraction, x_bits: int, z_bits: int, w: int, b: int) -> Fraction:
    Y = y_register(tau, z_bits, w, b)
    V = 0
    for i in range(w):
        if x_bits >> i & 1:
            d = y_row_value(Y, i, w, b)
            V += -d if i == w - 1 else d
    return Fraction(V % (1 << b), 1 << b)


# ============================================================================= 1. ERROR PARTITION

#: One error UNIT is pi * 2^-b radians of operator-norm error: the cost of one round-to-nearest of a
#: turn to b bits, |1 - exp(2 pi i e)| <= 2 pi |e| <= pi 2^-b for |e| <= 2^-(b+1).
UNIT = "pi*2^-b"


def unit(b: int) -> float:
    return math.pi * 2.0 ** (-b)


@dataclass(frozen=True)
class ErrorPartitionV1:
    """V1 (superseded 2026-09-23 by `catalysis.budget.ErrorPartition`, the structure/catalysis/rotation
    split). Global delta -> (delta_struct, delta_rot); delta_rot -> (catalyst prep, rows).

    delta_struct = (1 - rotation_fraction) delta   -> eta = delta_struct / n^2 (precision_policy)
    delta_rot    = rotation_fraction delta         (TV of the output distribution)
    delta_prep   = prep_fraction delta_rot         (b-3 synthesised catalyst angles, each at
                                                    delta_prep / (b-3), operator norm, paid ONCE)
    delta_rows   = delta_rot - delta_prep          (every rounded row + every truncated constant)
    """
    delta: float = 1e-2
    rotation_fraction: float = 0.5
    prep_fraction: float = 1e-3

    def __post_init__(self):
        if not 0 < self.delta < 1:
            raise ValueError("delta must be in (0, 1)")
        if not 0 < self.rotation_fraction <= 1:
            raise ValueError("rotation_fraction must be in (0, 1]")
        if not 0 <= self.prep_fraction < 1:
            raise ValueError("prep_fraction must be in [0, 1)")

    @property
    def delta_struct(self) -> float:
        return self.delta * (1.0 - self.rotation_fraction)

    @property
    def delta_rot(self) -> float:
        return self.delta * self.rotation_fraction

    @property
    def delta_prep(self) -> float:
        return self.prep_fraction * self.delta_rot

    @property
    def delta_rows(self) -> float:
        return self.delta_rot - self.delta_prep

    def eta(self, n: int) -> float:
        """Per-modmul precision `shorEta(delta_struct, n) = delta_struct / n^2`."""
        return self.delta_struct / float(n) ** 2

    def prep_eps(self, b: int) -> float:
        """Operator-norm tolerance of EACH of the b-3 synthesised catalyst angles."""
        return self.delta_prep / max(1, b - 3)

    def rows_ok(self, units: float, b: int) -> bool:
        return units * unit(b) <= self.delta_rows

    def report(self, units: float, b: int, truncation_units: float = 0.0) -> Dict[str, float]:
        return {"delta": self.delta, "delta_struct": self.delta_struct, "delta_rot": self.delta_rot,
                "delta_prep": self.delta_prep, "delta_rows": self.delta_rows, "b": b,
                "units": units, "rows_error": units * unit(b),
                "rounding_error": (units - truncation_units) * unit(b),
                "truncation_error": truncation_units * unit(b),
                "slack": self.delta_rows - units * unit(b)}


def census_error_units(census: Mapping[tuple, float], b: int) -> float:
    """CONTRACT units (tcount.catalysis.price_merged): 1 per rounded twiddle row, 2w per generic leaf
    (Y: w rows x 2; P: 2w constants x 1 -- equal, so the Y/P choice never moves b), 1 per `other` site."""
    u = 0.0
    for key, cnt in census.items():
        if key[0] == "tw":
            u += sum(r.rounded for r in twiddle_rows(int(key[1]), int(key[2]), int(key[3]), b)) * float(cnt)
        elif key[0] == "leaf":
            u += 2.0 * int(key[1]) * float(cnt)
        elif key[0] == "other":
            u += float(cnt)
    return u


def choose_b_v1(census: Mapping[tuple, float], delta_rot: float, *, prep_fraction: float = 1e-3,
                b_min: int = 4, b_max: int = 126) -> int:
    """V1 rule (reproduces `tcount.catalysis.price_merged_v1(...).b`). Smallest b in [b_min, b_max] with census_error_units(b) * pi 2^-b <= (1 - prep_fraction) delta_rot.
    Cost is non-decreasing in b, so smallest = best. The v2 rule is `catalysis.budget.choose_b`."""
    budget = delta_rot - prep_fraction * delta_rot
    for b in range(b_min, b_max + 1):
        if census_error_units(census, b) * unit(b) <= budget:
            return b
    raise ValueError("no b meets the budget")


census_error_units_v1 = census_error_units


def __getattr__(name: str):
    """`ErrorPartition`, `choose_b` and `BChoice` are the v2 objects of `catalysis.budget` (one
    implementation shared by the figures and the wired bloqs); the v1 ones keep a `V1`/`_v1` suffix."""
    if name in ("ErrorPartition", "choose_b", "BChoice"):
        from . import budget
        return getattr(budget, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def per_site_b(R: int, delta_cells: float) -> int:
    """Per-SITE rounding (no merging): smallest b >= 4 with R pi 2^-b <= delta_cells. Equals
    `ctbp.freeze.budget_b(R, 2 * delta_cells)`. `forshor_qualtran.constants.gradient_bits(delta_cells, R)`
    = ceil(log2(R / delta_cells)) omits the pi: it is ceil-short by log2(pi) = 1.65 bits (conflict C1)."""
    return max(4, math.ceil(math.log2(math.pi * R / delta_cells)))


def strict_twiddle_units(r1: int, r2: int, t: int, b: int) -> float:
    """Exact per-row bound (units of pi 2^-b), summed: a rounded row costs min(1, 2 (2^hi - 1) 2^(b - depth))
    (round-to-nearest bound vs. the whole row value), exact rows 0. No-op rows sum geometrically to
    <= 2 units in total, so `census_error_units` (1 per rounded row) over-charges big twiddles."""
    hi = max(r1, r2)
    u = 0.0
    for r in twiddle_rows(r1, r2, t, b):
        if r.rounded:
            v = Fraction(2 * ((1 << hi) - 1)) * Fraction(2) ** (b - r.depth)
            u += 1.0 if v >= 1 else float(v)
    return u


def strict_leaf_units(route: str, w: int) -> Tuple[float, float]:
    """(rounding units, truncation units) actually incurred: P = (2w, 0); Y = (w, w / 2^g <= 1/2).
    Y truncation is ONE error per leaf: |s(x)| dY <= 2^(w-1) * w 2^-(b+w+g) turns (docs §1.4)."""
    if route == "P":
        return 2.0 * w, 0.0
    if route == "Y":
        return 1.0 * w, w / 2.0 ** guard_bits(w)
    raise ValueError(route)


# ============================================================================= 3. COSTING
# And counts. T = 4 x Ands under Qualtran's convention (And = 4 T, And^dagger = 0 T by measurement);
# the ledger's unitary contract (constants.T_PER_AND_PAIR = 8) doubles them.

def QUALTRAN_ADD_ANDS(L: int) -> int:
    """`Add(QUInt(L))`: L - 1 And (Gidney), measured uncompute. The adjoint costs the same."""
    return max(0, L - 1)


def QUALTRAN_CTRL_ADD_ANDS(L: int) -> int:
    """`Add(QUInt(L)).controlled()`: 2L - 1 And."""
    return 2 * L - 1


def QUALTRAN_PRODUCT_TOFFOLI(L: int) -> int:
    """`Product(L, L)`: 2L^2 - L Toffoli (cost-only; Toffoli = 4 T with NO free uncompute)."""
    return 2 * L * L - L


def QUALTRAN_AIPG_TOFFOLI(b: int, controlled: bool) -> int:
    """`AddIntoPhaseGrad(x, b)`: b - 2 Toffoli, 2(b - 2) controlled -- independent of x's width and
    shift, FLOOR semantics, cost-only (no decomposition)."""
    return (2 if controlled else 1) * (b - 2)


def contract_twiddle_ands(r1: int, r2: int, t: int, b: int) -> int:
    """tcount `_twiddle_rows` Ands: sum over rows of L_i + m_i."""
    return sum(r.L + r.m for r in twiddle_rows(r1, r2, t, b))


def contract_leaf_ands(route: str, w: int, b: int, ctrl: bool) -> int:
    """tcount `_leaf_options` Ands."""
    if route == "Y":
        return 2 * w * (b + w + guard_bits(w)) + 2 * w * b + (w if ctrl else 0)
    if route == "P":
        return 4 * w * w + 2 * w * (b + 1) + (2 * w if ctrl else 0)
    raise ValueError(route)


def contract_other_ands(b: int) -> int:
    """tcount `price_merged` 'other' key: b - 3 Ands per site (per-site fallback)."""
    return b - 3


def contract_refund_T(key: tuple) -> float:
    """T refunded from the shared base per census entry: the twiddle's whole Toom decomposition
    (key[5] = its T at 0 T/rotation), or the leaf's cell And (+ control And), 4 T each."""
    if key[0] == "tw":
        return float(key[5])
    if key[0] == "leaf":
        w, ctrl = int(key[1]), bool(key[2])
        return 4.0 * w * w * (2 if ctrl else 1)
    return 0.0


def native_twiddle_row_ands(r: RowGeometry) -> int:
    """Prescribed decomposition (docs §3.2):
      exact row    m control Ands (x_i AND z_j) + Add(QUInt(L)) on the top L catalyst bits  -> m + L - 1
      rounded row  m + 1 control Ands (incl. the carry-in bit x_i AND z_{depth-b-1}) + a Gidney adder
                   whose first carry IS the carry-in qubit (`catalyst.AddWithCarryIn`, L - 1 Ands)
                                                                                            -> m + L
      no-op row    dropped (value 0 for every z)                                            -> 0
    (== `catalysis.twiddle.merged_twiddle_ands` for t = r1 + r2.)"""
    if r.noop:
        return 0
    if r.rounded:
        return r.m + 1 + QUALTRAN_ADD_ANDS(r.L)
    return r.m + QUALTRAN_ADD_ANDS(r.L)


def native_twiddle_ands(r1: int, r2: int, t: int, b: int) -> int:
    return sum(native_twiddle_row_ands(r) for r in twiddle_rows(r1, r2, t, b))


def native_leaf_ands(route: str, w: int, b: int, ctrl: bool, *, n_zero_consts: int = 0) -> int:
    """Prescribed decompositions (docs §3.3), Qualtran And counts:
      P  signed schoolbook p = x z: w rows x (w AND + Add(QUInt(w+1)) = w) = 2w^2, uncompute 2w^2;
         each nonzero constant: CNOT-load + Add(QUInt(b)) = b - 1; ctrl: +1 And per nonzero constant
      Y  each nonzero C_j: CNOT-load + Add(QUInt(A)) = A - 1, compute + uncompute; w rows of
         (b + 1 control Ands [window + carry-in bit] + AddWithCarryIn(b, b) = b - 1) = 2b; ctrl: +1 And
         per row. (leaf.py's TEMP `_CtrlWindowAdd` uses Add(QUInt(b + 1)) with a low ancilla: 2b + 1.)
    `n_zero_consts` = constants that are 0 mod 2^b (P) / 2^A (Y), skipped (dyadic turns)."""
    if route == "P":
        nz = 2 * w - n_zero_consts
        return 4 * w * w + nz * (b - 1) + (nz if ctrl else 0)
    if route == "Y":
        A = b + w + guard_bits(w)
        nz = w - n_zero_consts
        return 2 * nz * (A - 1) + w * 2 * b + (w if ctrl else 0)
    raise ValueError(route)


def catalyst_prep_rotations(b: int) -> int:
    """`PhaseGradientState(b)`: b - 3 non-Clifford+T rotations (resolutions 4..b) + 1 T + Cliffords."""
    return max(0, b - 3)


# ============================================================================= 4. V2 PRICING (D1-D5)
#
# The user's decisions of 2026-09-23 (docs "v2 pricing"):
#   D1  every And = 4 T (Qualtran; And^dagger = measurement, 0 T).
#   D2  native bloq And counts, EXACT error per row / constant (no flat unit per row).
#   D3  dyadic leaves of resolution <= b are exact (0 error; constants of weight >= 2^res are 0 and skipped).
#   D4  U1/U5 control: x' = ctrl AND x once per leaf (wx Ands), not per row / kick.
#   D5  classical constants (P kicks, `other` sites) are MIXED: unbiased random rounding, one classical draw
#       per kick, diamond <= 8 pi^2 u d <= 2 pi^2 4^-b (notes/mixing-04-formal-theory Thm 1). Rows that add a
#       REGISTER value (twiddle rows, Y rows) stay deterministic.
# Error metric: TV of the output distribution <= sum(operator-norm errors) + 1/2 sum(diamond errors).

AND_T = 4                                     # D1
C_SYN_MEASURED = (3.075, 0.86)                # == tcount.budget.C_SYN_MEASURED (tested)


def ross_selinger_t(eps: float) -> float:
    """Mean T of one RS-synthesised z-rotation at operator-norm tolerance eps (mirror of tcount.budget)."""
    if not 0.0 < eps < 1.0:
        raise ValueError(f"eps must be in (0, 1); got {eps!r}")
    a, c = C_SYN_MEASURED
    return a * math.log2(1.0 / eps) + c


# ----------------------------------------------------------------------------- twiddle, exact per row

def twiddle_row_error_turns(r: RowGeometry, hi: int, b: int) -> Fraction:
    """EXACT max over z in [0, 2^hi) of |realised - exact| (turns) for one merged twiddle row (D2).

    Row value v(z) = z 2^-depth mod 1, realised round-half-up to b bits. With s = depth - b dropped bits:
      depth <= b                 0                        (exact)
      s >= 1 and hi >= s         2^-(b+1)                 (z mod 2^s hits 2^(s-1): a half-way tie)
      s > hi   (a no-op row)     (2^hi - 1) 2^-depth      (z < 2^(s-1): always rounds DOWN, to 0)
    Operator norm of the row's phase error: 2 pi x this (|1 - e^{2 pi i e}| <= 2 pi |e|)."""
    if not r.rounded:
        return Fraction(0)
    s = r.depth - b
    if hi >= s:
        return Fraction(1, 1 << (b + 1))
    return Fraction((1 << hi) - 1, 1 << r.depth)


def twiddle_error_turns(r1: int, r2: int, t: int, b: int) -> Fraction:
    """Sum of the rows' exact max turn errors (== `strict_twiddle_units(...) * 2^-(b+1)`)."""
    hi = max(r1, r2)
    return sum((twiddle_row_error_turns(r, hi, b) for r in twiddle_rows(r1, r2, t, b)), Fraction(0))


# ----------------------------------------------------------------------------- mixing (D5)

def mix_residuals(v: Fraction, b: int) -> Tuple[Fraction, Fraction]:
    """(u, d) in turns: v - floor_b(v) and ceil_b(v) - v (both 0 when v is exact at b bits)."""
    x = (Fraction(v) % 1) * (1 << b)
    fl = math.floor(x)
    if fl == x:
        return Fraction(0), Fraction(0)
    return (x - fl) / (1 << b), (fl + 1 - x) / (1 << b)


def mixing_diamond(v: Fraction, b: int) -> float:
    """Diamond-norm error of unbiased random rounding of the constant turn v to b bits:
    8 pi^2 u d (mixing-04 Thm 1), <= `mixing_diamond_max(b)` = 2 pi^2 4^-b."""
    u, d = mix_residuals(v, b)
    return 8.0 * math.pi ** 2 * float(u * d)


def mixing_diamond_max(b: int) -> float:
    return 2.0 * math.pi ** 2 * 4.0 ** (-b)


# ----------------------------------------------------------------------------- generic leaf (v2)

@dataclass(frozen=True)
class LeafCost:
    """One leaf's price (the LEAF-agent contract `leaf.leaf_cost`): chosen route, And count, operator-norm
    error (deterministic parts), diamond error (mixed kicks), nonzero classical constants."""
    route: str                   # "P" | "Y" | "id" (turn == 0: nothing to do) | "K" (lone constant kick)
    ands: int
    err_op: float
    err_diamond: float
    nonzero_consts: int

    @property
    def tv(self) -> float:
        return self.err_op + 0.5 * self.err_diamond


def leaf_y_acc(wx: int, wz: int, b: int) -> int:
    """Y accumulator width A = b + wx + g(wz): the truncation |s(x)| dY < 2^(wx-1) wz 2^-A <= 2^-(b+2)."""
    return b + wx + guard_bits(wz)


def native_leaf_ands_v2(route: str, wx: int, wz: int, b: int, ctrl: bool, nonzero: int) -> int:
    """Native Qualtran Ands (D2 + D4):
      P  signed schoolbook compute + uncompute 4 wx wz; each nonzero kick CtrlAddConstIntoCatalyst(b, k)
         = b - 1; ctrl: x' = ctrl AND x once, wx Ands (D4)
      Y  each nonzero C_j: CtrlAddConstIntoCatalyst(A, C_j) = A - 1, compute + uncompute; wx rows of
         CtrlAddIntoCatalyst(b, b, carry_in) = 2b; ctrl: wx Ands (D4)."""
    c = wx if ctrl else 0
    if route == "P":
        return 4 * wx * wz + nonzero * (b - 1) + c
    if route == "Y":
        return 2 * nonzero * (leaf_y_acc(wx, wz, b) - 1) + 2 * b * wx + c
    raise ValueError(route)


def _pick(p: LeafCost, y: LeafCost, route: Optional[str]) -> LeafCost:
    if route == "P":
        return p
    if route == "Y":
        return y
    return y if y.ands <= p.ands else p              # ties -> Y, as `leaf.catalytic_leaf`


def leaf_class_cost(wx: int, wz: int, b: int, ctrl: bool, res: Optional[int], *, mixing: bool = True,
                    route: Optional[str] = None) -> LeafCost:
    """Worst case over every turn of the class (D2, D3): `res` None = generic (kappa/N: every constant
    nonzero and inexact), else the leaf is dyadic with resolution <= res (an upper bound is safe: every
    count and error below is non-decreasing in res).
      res == 0         identity leaf (turn 0 mod 1): 0 Ands, exact  [the wiring must skip it]
      res <= b         exact: P kicks l < min(res, wx+wz) nonzero; Y constants j < min(res, wz)
      res > b          P: kicks l < res - b inexact (turn res - l > b); Y: rows i < res - b round,
                       C_j truncated iff res - j > A (then every row rounds)."""
    if res is not None and res <= 0:
        return LeafCost("id", 0, 0.0, 0.0, 0)
    nP = wx + wz
    A = leaf_y_acc(wx, wz, b)
    if res is None:
        nzP, inexP, nzY, truncY, rowsY = nP, nP, wz, wz, wx
    else:
        nzP = min(res, nP)
        inexP = min(nP, max(0, res - b))
        nzY = min(res, wz)
        truncY = min(wz, max(0, res - A))
        rowsY = wx if truncY else min(wx, max(0, res - b))
    if mixing:
        p_op, p_dia = 0.0, inexP * mixing_diamond_max(b)
    else:
        p_op, p_dia = 2 * math.pi * inexP * 2.0 ** (-(b + 1)), 0.0
    y_turns = rowsY * 2.0 ** (-(b + 1)) + truncY * 2.0 ** (wx - 1 - A)
    P = LeafCost("P", native_leaf_ands_v2("P", wx, wz, b, ctrl, nzP), p_op, p_dia, nzP)
    Y = LeafCost("Y", native_leaf_ands_v2("Y", wx, wz, b, ctrl, nzY), 2 * math.pi * y_turns, 0.0, nzY)
    return _pick(P, Y, route)


def leaf_cost_mirror(wx: int, wz: int, b: int, tau: Fraction, ctrl: bool, *, mixing: bool = True,
                     route: Optional[str] = None) -> LeafCost:
    """Mirror of the LEAF contract `leaf.leaf_cost` for an EXACT turn (cross-checked when leaf.py exports
    it). P: k_l = round_nearest(tau wp_l, b) over the wx + wz signed product bits; deterministic error
    sum |k_l 2^-b - frac(tau wp_l)|, mixed 8 pi^2 u_l d_l per kick; a kick exists iff its constant is not
    0 (deterministic) / frac(tau wp_l) != 0 (mixed: either draw may be nonzero). Y: C_j = trunc_A(tau wz_j);
    rows round unless every C_j is exact and res(tau) - i <= b."""
    tau = Fraction(tau) % 1
    if tau == 0:
        return LeafCost("id", 0, 0.0, 0.0, 0)
    nP = wx + wz
    vals = [(tau * signed_weight(nP, l)) % 1 for l in range(nP)]
    if mixing:
        nzP = sum(v != 0 for v in vals)
        p_op, p_dia = 0.0, sum(mixing_diamond(v, b) for v in vals)
    else:
        ks = [round_nearest(v, b) for v in vals]
        nzP = sum(k != 0 for k in ks)
        err = sum(abs(((Fraction(k, 1 << b) - v + Fraction(1, 2)) % 1) - Fraction(1, 2))
                  for k, v in zip(ks, vals))
        p_op, p_dia = 2 * math.pi * float(err), 0.0
    A = leaf_y_acc(wx, wz, b)
    fr = [(tau * signed_weight(wz, j)) % 1 for j in range(wz)]
    C = [truncate(f, A) for f in fr]
    dsum = sum((f - Fraction(c, 1 << A) for f, c in zip(fr, C)), Fraction(0))
    res = resolution(tau)
    if dsum or res is None:
        rowsY = wx
    else:
        rowsY = min(wx, max(0, res - b))
    y_turns = rowsY * Fraction(1, 1 << (b + 1)) + (1 << (wx - 1)) * dsum
    nzY = sum(c != 0 for c in C)
    P = LeafCost("P", native_leaf_ands_v2("P", wx, wz, b, ctrl, nzP), p_op, p_dia, nzP)
    Y = LeafCost("Y", native_leaf_ands_v2("Y", wx, wz, b, ctrl, nzY), 2 * math.pi * float(y_turns), 0.0, nzY)
    return _pick(P, Y, route)


def other_site_cost(b: int, *, mixing: bool = True) -> LeafCost:
    """A lone rotation site outside every grid: one CtrlAddConstIntoCatalyst(b, k) controlled on the
    site's qubit (b - 1 Ands), turn unknown -> worst case; mixed (D5) or deterministic round-to-nearest."""
    if mixing:
        return LeafCost("K", b - 1, 0.0, mixing_diamond_max(b), 1)
    return LeafCost("K", b - 1, math.pi * 2.0 ** (-b), 0.0, 1)


# ----------------------------------------------------------------------------- catalyst preparation

def catalyst_prep_eps(b: int, rotation_budget: float) -> float:
    """eps to pass to `CatalystPrep(b, eps)`: Qualtran's PhaseGradientState splits eps as eps / b per
    ZPow, and b - 3 of them are genuine rotations, so eps = rotation_budget * b / (b - 3) spends exactly
    rotation_budget / (b - 3) on each synthesised rotation."""
    return rotation_budget * b / max(1, b - 3)


def catalyst_prep_T(b: int, rotation_budget: float) -> float:
    """(b - 3) RS rotations at rotation_budget / (b - 3) each, + the 1 T of PhaseGradientState (D2)."""
    m = max(0, b - 3)
    if m == 0:
        return 1.0 if b == 3 else 0.0
    return m * ross_selinger_t(rotation_budget / m) + 1.0
