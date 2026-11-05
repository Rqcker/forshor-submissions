"""Merged-catalysis bloqs for the GENERIC phase-product leaf (Amy et al., arXiv:2305.07720 §VI.C).

A leaf of the KMY/Toom-Cook pipeline is one ``wx x wz`` signed grid

    U_tau |x>|z> = exp(2 pi i * tau * s(x) * s(z)) |x>|z>,   x: QInt(wx), z: QInt(wz), two's complement
                                                            (bit weights +2^i, top bit -2^(w-1)),

i.e. ``Naive_SignedPhaseProd(phi, wx, wz)`` with ``tau = phi / 2``. U1/U5 leaves carry an extra QPE
control qubit (``ctrl=True``). ``tau`` is an exact ``fractions.Fraction``; every constant below is
computed classically from it.

Merged catalysis replaces the wx*wz per-cell rotations by additions into ONE b-qubit phase-gradient
catalyst ``|psi_b> = 2^(-b/2) sum_k exp(-2 pi i k / 2^b) |k>`` (catalyst[0] = half-turn bit): adding
an integer ``v`` into its top L qubits kicks back ``exp(+2 pi i v / 2^L)`` and leaves ``|psi_b>``
unchanged. Every bloq here is a PERMUTATION of basis states that moves the catalyst by
``k -> k + V(x, z) mod 2^b`` and restores every other register; the realised leaf is
``exp(2 pi i V(x,z) / 2^b)``. No arbitrary rotations anywhere -- only And / And^dagger / Clifford.

PUBLIC API (what the pricing and wiring agents call)
----------------------------------------------------
``catalytic_leaf(wx, wz, b, tau, ctrl, *, mixing=True, seed=0, draws=None, route=None) -> Bloq``
    registers ``[ctrl: QBit], x: QInt(wx), z: QInt(wz), catalyst: QUInt(b)`` (all pass through);
    builds the route with fewer native Ands (ties -> Y), or the forced ``route``. The automatic
    choice compares the CLASS counts (``leaf_cost_class`` of tau's class), so it never depends on a
    rounding / draw that happens to hit 0: the built route is always the route the class pricing
    charged, and the built leaf's Ands and errors are <= the class numbers for it.
``leaf_cost(wx, wz, b, tau, ctrl, *, mixing=True, seed=0, draws=None, route=None) -> LeafCost``
    pure closed form (no bloq build). ``.ands`` == ``QECGatesCost(catalytic_leaf(...)).and_bloq``
    (+ ``.toffoli``, which is 0) EXACTLY, same arguments; ``.err_op`` / ``.err_diamond`` are the
    CONCRETE bounds of that realisation (see ERRORS).
``leaf_cost_class(wx, wz, b, kind, resolution, ctrl, mixing, *, route=None) -> LeafCost``
    the same numbers without a concrete tau (memoised). kind='dyadic' (tau = odd/2^resolution, or
    resolution 0 for tau = 0 mod 1) or 'generic' (an odd prime divides the denominator).
    EQUALITY vs ``leaf_cost`` of any concrete tau of that class:
      * dyadic, resolution r <= b:  ands, nonzero_consts EQUAL (every constant exact, zero error);
        err_op == err_diamond == 0 EQUAL.
      * dyadic r > b, and generic:  ands / nonzero_consts are UPPER BOUNDS, equal unless some INEXACT
        constant rounds (P, deterministic), draws (P, mixing) or truncates (Y) to 0 mod 2^L -- an
        event of probability ~2^(1-b) per P kick for a generic turn; errors are worst-case bounds
        (>= the concrete ones).
      * the automatic route is the class route by construction (``leaf_cost`` / ``catalytic_leaf``
        pick it from ``leaf_cost_class``), so concrete <= class holds for Ands AND errors.

Two constructions
-----------------
``LeafP`` (product route)
    [ctrl: x' = ctrl AND x, wx Ands]; p = s(x')*s(z) computed digitally into a (wx+wz)-bit
    two's-complement register (``SignedProduct``: 2*wx*wz Ands, uncompute the same); for each bit l
    of p one controlled classical-constant addition k_l ~ frac(tau * wp_l) * 2^b into the catalyst
    (wp_l = 2^l, sign bit -2^(wx+wz-1)); uncompute p; [And^dagger x'].
    * exact constant (tau * wp_l * 2^b is an integer mod 2^b, e.g. dyadic tau of resolution <= b + l):
      k_l exact; a constant with t trailing zeros is added into the top b - t catalyst qubits only
      (b - t - 1 Ands); a zero constant is skipped.
    * inexact constant, ``mixing=False``: round-half-up to b bits (b - 1 Ands, skipped if it rounds
      to 0); error <= 2^-(b+1) turns.
    * inexact constant, ``mixing=True`` (D5): unbiased random rounding, k_l = floor(s) + B with
      s = frac(tau wp_l) 2^b and B ~ Bernoulli(frac(s)) drawn EXACTLY (``randrange(den) < num``)
      from ``random.Random(seed)`` in kick order (or given by ``draws``); fixed at construction.
      Per-kick diamond bound 8 pi^2 u d (d = frac(s) 2^-b, u = 2^-b - d; Theorem 1 of
      notes/mixing-04-formal-theory). Cost = b - 1, the same as deterministic.
``LeafY`` (Y route)
    Y = sum_j z_j C_j mod 2^A, C_j = floor(frac(tau * wz_j) 2^A) built by wz controlled-constant
    additions into an A-bit register; row i (x' bit i) adds frac(2^i Y / 2^A) into the catalyst,
    the SIGN row i = wx-1 subtracts it (catalyst slice complemented around the add: X gates);
    Y is uncomputed. Row depth d_i = A - i:
      d_i <= 0     no-op (skipped, and its x bit needs no ctrl fold);
      d_i <= b     EXACT row: the low d_i bits of Y into the top d_i catalyst qubits
                   (``CtrlAddIntoCatalyst(d, d)``: 2 d_i - 1 Ands);
      d_i >  b     ROUNDED row: b-bit window Y[d-b .. d-1] + carry-in Y[d-b-1] (round half up,
                   deterministic) into all b qubits (``CtrlAddIntoCatalyst(b, b, cin)``: 2b Ands).
    A = b + wx + g, g = guard_bits(wz) = ceil(log2 max(wz,2)) + 1 (2^g >= 2 wz) for a generic tau;
    A = min(resolution, b + wx + g) for a dyadic tau (D3: Y is then exact at A = resolution bits,
    no guard bits at all; for resolution <= b every row is exact). Exact C_j with t trailing zeros
    are added into the top A - t bits of Y.

AND COUNTS (QECGatesCost ``and_bloq``; And = 4 T, And^dagger = 0 T) -- ``leaf_cost`` sums exactly:
    const(L, k, exact) = 0 if k = 0 mod 2^L; (L - tz(k)) - 1 if exact; L - 1 if inexact
    P  = 4 wx wz + sum_l const(b, k_l) + [ctrl] wx                       (0 if every k_l = 0)
    Y  = 2 sum_j const(A, C_j) + sum_live rows (2 d_i - 1 | 2b) + [ctrl] min(wx, A)
                                                                        (0 if every C_j = 0)
  class closed forms (n = wx + wz):
    generic P: 4 wx wz + n (b - 1) + [ctrl] wx
    generic Y: 2 wz (A - 1) + 2 b wx + [ctrl] wx,  A = b + wx + g
    dyadic r, P: kicks l < min(r, n): (r - l - 1) if l >= r - b else (b - 1)
    dyadic r, Y: A = min(r, b+wx+g); consts j < min(r, wz): (r - j - 1) if j >= r - A else (A - 1),
                 doubled; rows as above.

ERRORS (radians of operator norm / diamond norm):
    P det.   err_op = sum_inexact 2 pi |k_l/2^b - v_l|          (class: n_inexact * pi 2^-b)
    P mixing err_op = 0; err_diamond = sum_inexact 8 pi^2 u_l d_l  (class: n_inexact * 2 pi^2 4^-b)
    Y        err_op = n_rounded_rows * pi 2^-b + 2 pi 2^(wx-1) sum_inexact (frac(tau wz_j) - C_j 2^-A)
             (class: truncation term with 2^-A per inexact constant, <= wz/2^g <= 1/2 unit)
             err_diamond = 0 (Y is deterministic; the P mixing flag does not affect it).
    Total diamond bound of a leaf = 2 err_op + err_diamond. The mixing bound is a property of the
    CHANNEL averaged over draws: draws must be fresh per execution and independent across kicks --
    across leaves too, so the wiring must give every leaf instance its own seed.

The primitives come from ``forshor_qualtran.catalysis.catalyst`` (frozen; no stand-ins).
"""
from __future__ import annotations

import math
import random
from fractions import Fraction
from functools import cached_property, lru_cache
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

import numpy as np
from attrs import field, frozen

from qualtran import Bloq, BloqBuilder, QBit, QInt, QUInt, Register, Signature, SoquetT
from qualtran.bloqs.arithmetic import Add
from qualtran.bloqs.basic_gates import CNOT, XGate
from qualtran.bloqs.mcmt import And
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from .catalyst import CtrlAddConstIntoCatalyst, CtrlAddIntoCatalyst

__all__ = [
    # public API
    "catalytic_leaf", "leaf_cost", "leaf_cost_class", "LeafCost", "LeafP", "LeafY", "SignedProduct",
    "turn_resolution",
    # legacy (square) helpers and wrappers
    "tau_from_phi", "guard_bits", "round_b_int", "p_route_constants", "y_route_constants",
    "contract_and_count", "leaf_options", "CatalyticLeafP", "CatalyticLeafY",
    "SignedSchoolbookProduct", "leaf_error_bound", "exact_turn",
]


# ============================================================================= classical helpers

def tau_from_phi(phi: Fraction) -> Fraction:
    """The leaf's turn: ``Naive_SignedPhaseProd(phi, wx, wz)`` applies CZPow exponents phi*wa*wb, i.e.
    phase exp(i pi phi x z) = exp(2 pi i (phi/2) x z)."""
    return Fraction(phi) / 2


def guard_bits(w: int) -> int:
    """g = ceil(log2 w) + 1 (w=1 treated as 2), identical to ``_leaf_options`` (w = wz here)."""
    return max(1, math.ceil(math.log2(max(w, 2)))) + 1


def turn_resolution(tau: Fraction) -> Optional[int]:
    """r with tau = odd / 2^r mod 1 (0 for tau = 0 mod 1), or None (generic: odd prime in denominator)."""
    t = Fraction(tau) % 1
    if t == 0:
        return 0
    d = t.denominator
    if d & (d - 1):
        return None
    return d.bit_length() - 1


def _signed_weights(width: int) -> List[int]:
    return [(1 << i) for i in range(width - 1)] + [-(1 << (width - 1))]


def _tz(k: int) -> int:
    return (k & -k).bit_length() - 1


def round_b_int(v: Fraction, b: int) -> int:
    """round-to-nearest (ties up) of the turn ``v mod 1`` to b bits, as an integer mod 2^b."""
    v = Fraction(v) % 1
    return math.floor(v * (1 << b) + Fraction(1, 2)) % (1 << b)


def p_route_constants(w: int, b: int, tau: Fraction, wz: Optional[int] = None) -> List[int]:
    """DETERMINISTIC P constants k_l = round_b(frac(tau * wp_l)) for the w + wz bits of the signed
    product register (wz defaults to w: the legacy square leaf)."""
    n = w + (w if wz is None else wz)
    return [round_b_int(Fraction(tau) * wl, b) for wl in _signed_weights(n)]


def y_route_constants(w: int, b: int, tau: Fraction) -> List[int]:
    """Legacy square GENERIC Y constants c_j = floor(frac(tau * wz_j) * 2^A), A = b + w + g (spec.y_constants).
    (``LeafY`` uses A = min(resolution, b + wx + g) for a dyadic tau.)"""
    A = b + w + guard_bits(w)
    return [math.floor((Fraction(tau) * wj % 1) * (1 << A)) for wj in _signed_weights(w)]


def exact_turn(tau: Fraction, x: int, z: int) -> Fraction:
    """The target phase (in turns, mod 1) for signed operand values x, z."""
    return (Fraction(tau) * x * z) % 1


def leaf_error_bound(route: str, w: int, b: int) -> float:
    """Legacy CONTRACT charge (radians): P 2w*pi*2^-b, Y w*2pi*2^-b."""
    return (2 * w * math.pi if route == "P" else w * 2 * math.pi) * 2.0 ** (-b)


# ============================================================================= contract (legacy mirror)

def contract_and_count(route: str, w: int, b: int, ctrl: bool) -> int:
    """Construction-level And count of the tcount contract, verbatim ``_leaf_options`` (square leaf)."""
    if route == "Y":
        return 2 * w * (b + w + guard_bits(w)) + 2 * w * b + (w if ctrl else 0)
    if route == "P":
        return 4 * w * w + 2 * w * (b + 1) + (2 * w if ctrl else 0)
    raise ValueError(route)


def leaf_options(w: int, b: int, ctrl: bool) -> List[Tuple[int, float]]:
    """[(Ands, error units in pi 2^-b)] for (Y, P) -- the ``_leaf_options`` mirror."""
    return [(contract_and_count("Y", w, b, ctrl), 2.0 * w), (contract_and_count("P", w, b, ctrl), 2.0 * w)]


# ============================================================================= plans (pure python)

class _PKick(NamedTuple):
    l: int               # bit of p
    k: int               # UNREDUCED integer constant (may equal 2^b after a ceil)
    exact: bool          # tau * wp_l * 2^b is an integer (no rounding at all)
    v: Fraction          # frac(tau * wp_l), the exact turn
    s_frac: Fraction     # frac(v 2^b) (0 when exact): P(ceil) under mixing


class _Slice(NamedTuple):
    idx: int             # control bit (p bit l for P, z bit j for Y)
    L: int               # adder length (top L qubits of the target)
    kk: int              # constant added into that slice, in [1, 2^L)


def _const_slice(idx: int, L: int, k: int, exact: bool) -> Optional[_Slice]:
    km = k % (1 << L)
    if km == 0:
        return None
    if exact:
        t = _tz(km)
        return _Slice(idx, L - t, km >> t)
    return _Slice(idx, L, km)


def _slice_ands(s: _Slice) -> int:
    return s.L - 1


def _p_kicks(wx: int, wz: int, b: int, tau: Fraction, mixing: bool, seed: int,
             draws: Optional[Sequence[int]]) -> Tuple[_PKick, ...]:
    n = wx + wz
    if draws is not None and len(draws) != n:
        raise ValueError(f"draws must have one entry per p bit ({n}), got {len(draws)}")
    rng = None
    out = []
    for l, wl in enumerate(_signed_weights(n)):
        v = (tau * wl) % 1
        s = v * (1 << b)
        lo = math.floor(s)
        rem = s - lo
        if rem == 0:
            out.append(_PKick(l, lo, True, v, Fraction(0)))
            continue
        if not mixing:
            k = math.floor(s + Fraction(1, 2))
        elif draws is not None:
            k = lo + (1 if draws[l] else 0)
        else:
            if rng is None:
                rng = random.Random(seed)
            k = lo + (1 if rng.randrange(rem.denominator) < rem.numerator else 0)
        out.append(_PKick(l, k, False, v, rem))
    return tuple(out)


def _y_acc(wx: int, wz: int, b: int, r: Optional[int]) -> int:
    A0 = b + wx + guard_bits(wz)
    return A0 if r is None else min(r, A0)


class _YConst(NamedTuple):
    j: int
    C: int               # floor(frac(tau wz_j) 2^A)
    exact: bool
    delta: Fraction      # truncation error in turns, [0, 2^-A)


def _y_consts(wz: int, A: int, tau: Fraction) -> Tuple[_YConst, ...]:
    out = []
    for j, wj in enumerate(_signed_weights(wz)):
        v = (tau * wj) % 1
        s = v * (1 << A)
        C = math.floor(s)
        out.append(_YConst(j, C, s == C, v - Fraction(C, 1 << A)))
    return tuple(out)


def _y_rows(wx: int, A: int, b: int) -> Tuple[Tuple[int, str, int], ...]:
    """(i, 'exact'|'round', depth) for every live row (depth > 0)."""
    rows = []
    for i in range(wx):
        d = A - i
        if d <= 0:
            continue
        rows.append((i, "exact" if d <= b else "round", d))
    return tuple(rows)


def _row_ands(kind: str, d: int, b: int) -> int:
    return 2 * d - 1 if kind == "exact" else 2 * b


# ============================================================================= cost records

class LeafCost(NamedTuple):
    route: str             # 'P' or 'Y'
    ands: int              # QECGatesCost and_bloq (+ toffoli = 0) of the built leaf
    err_op: float          # deterministic operator-norm error bound (radians)
    err_diamond: float     # sum of the mixed kicks' diamond bounds 8 pi^2 u d
    nonzero_consts: int    # P: nonzero kicks; Y: nonzero Y constants


_PI = math.pi


def _p_cost_concrete(wx, wz, b, ctrl, kicks, mixing) -> LeafCost:
    slices = [s for s in (_const_slice(kk.l, b, kk.k, kk.exact) for kk in kicks) if s]
    if not slices:
        ands = 0
    else:
        ands = 4 * wx * wz + sum(_slice_ands(s) for s in slices) + (wx if ctrl else 0)
    eop = 0.0
    edi = 0.0
    for kk in kicks:
        if kk.exact:
            continue
        if mixing:
            d = kk.s_frac / (1 << b)
            u = Fraction(1, 1 << b) - d
            edi += 8 * _PI ** 2 * float(u * d)
        else:
            eop += 2 * _PI * float(abs(Fraction(kk.k, 1 << b) - kk.v))
    return LeafCost("P", ands, eop, edi, len(slices))


def _y_cost_concrete(wx, wz, b, ctrl, A, consts) -> LeafCost:
    slices = [s for s in (_const_slice(c.j, A, c.C, c.exact) for c in consts) if s]
    if not slices:
        return LeafCost("Y", 0, 0.0, 0.0, 0)
    rows = _y_rows(wx, A, b)
    ands = 2 * sum(_slice_ands(s) for s in slices) + sum(_row_ands(k, d, b) for _, k, d in rows)
    ands += len(rows) if ctrl else 0
    n_round = sum(1 for _, k, _ in rows if k == "round")
    trunc = sum((c.delta for c in consts if not c.exact), Fraction(0))
    eop = n_round * _PI * 2.0 ** (-b) + 2 * _PI * float(trunc * (1 << (wx - 1)))
    return LeafCost("Y", ands, eop, 0.0, len(slices))


def _pick(p: LeafCost, y: LeafCost, route: Optional[str]) -> LeafCost:
    if route == "P":
        return p
    if route == "Y":
        return y
    if route is not None:
        raise ValueError(f"route must be 'P', 'Y' or None, got {route!r}")
    return y if y.ands <= p.ands else p


def _check_tau(tau) -> Fraction:
    if isinstance(tau, bool) or not isinstance(tau, (Fraction, int)):
        raise TypeError(f"tau must be an exact fractions.Fraction, got {type(tau).__name__}")
    return Fraction(tau)


def _check_shape(wx, wz, b):
    for name, v in (("wx", wx), ("wz", wz), ("b", b)):
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise TypeError(f"{name} must be a positive int, got {v!r} (signature is "
                            f"(wx, wz, b, tau, ctrl) -- the square (w, b, tau, ctrl) form is gone)")


def leaf_cost(wx: int, wz: int, b: int, tau: Fraction, ctrl: bool, *, mixing: bool = True,
              seed: int = 0, draws: Optional[Sequence[int]] = None,
              route: Optional[str] = None) -> LeafCost:
    """Closed-form cost of ``catalytic_leaf`` with the SAME arguments (no bloq build). ``.ands`` equals
    ``QECGatesCost(catalytic_leaf(...)).and_bloq`` exactly; errors are the concrete bounds."""
    _check_shape(wx, wz, b)
    tau = _check_tau(tau)
    r = turn_resolution(tau)
    if route is None:        # auto route = the CLASS choice (draw- and rounding-independent)
        route = leaf_cost_class(wx, wz, b, "generic" if r is None else "dyadic", r, bool(ctrl),
                                bool(mixing)).route
    if route == "P":
        return _p_cost_concrete(wx, wz, b, ctrl, _p_kicks(wx, wz, b, tau, mixing, seed, draws), mixing)
    if route != "Y":
        raise ValueError(f"route must be 'P', 'Y' or None, got {route!r}")
    A = _y_acc(wx, wz, b, r)
    return (_y_cost_concrete(wx, wz, b, ctrl, A, _y_consts(wz, A, tau)) if A > 0
            else LeafCost("Y", 0, 0.0, 0.0, 0))


@lru_cache(maxsize=None)
def leaf_cost_class(wx: int, wz: int, b: int, kind: str, resolution: Optional[int], ctrl: bool,
                    mixing: bool = True, *, route: Optional[str] = None) -> LeafCost:
    """``leaf_cost`` without a concrete tau (see the module docstring for when it is EXACT and when
    it is an upper bound). kind 'generic' (resolution ignored) or 'dyadic' (resolution r >= 0)."""
    _check_shape(wx, wz, b)
    ctrl, mixing = bool(ctrl), bool(mixing)
    if kind == "generic":
        r = None
    elif kind == "dyadic":
        if resolution is None or int(resolution) < 0:
            raise ValueError("a dyadic class needs resolution >= 0")
        r = int(resolution)
    else:
        raise ValueError(f"kind must be 'generic' or 'dyadic', got {kind!r}")
    n = wx + wz
    # ---- P
    p_ands, p_nz, p_inex = 0, 0, 0
    for l in range(n):
        if r is None or l < r - b:
            p_ands += b - 1
            p_nz += 1
            p_inex += 1
        elif l < r:
            p_ands += r - l - 1
            p_nz += 1
    if p_nz:
        p_ands += 4 * wx * wz + (wx if ctrl else 0)
    if mixing:
        p = LeafCost("P", p_ands, 0.0, p_inex * 2 * _PI ** 2 * 4.0 ** (-b), p_nz)
    else:
        p = LeafCost("P", p_ands, p_inex * _PI * 2.0 ** (-b), 0.0, p_nz)
    # ---- Y
    A = _y_acc(wx, wz, b, r)
    y_c, y_nz, y_inex = 0, 0, 0
    for j in range(wz):
        if r is None or j < r - A:
            y_c += A - 1
            y_nz += 1
            y_inex += 1
        elif j < r:
            y_c += r - j - 1
            y_nz += 1
    if y_nz:
        rows = _y_rows(wx, A, b)
        y_ands = 2 * y_c + sum(_row_ands(k, d, b) for _, k, d in rows) + (len(rows) if ctrl else 0)
        n_round = sum(1 for _, k, _ in rows if k == "round")
        eop = n_round * _PI * 2.0 ** (-b) + 2 * _PI * y_inex * 2.0 ** (wx - 1 - A)
        y = LeafCost("Y", y_ands, eop, 0.0, y_nz)
    else:
        y = LeafCost("Y", 0, 0.0, 0.0, 0)
    return _pick(p, y, route)


# ============================================================================= wiring helpers

def _lsb_bits(bb: BloqBuilder, reg) -> List:
    """Split a register into a little-endian python list (Qualtran splits big-endian)."""
    return list(bb.split(reg))[::-1]


def _join_lsb(bb: BloqBuilder, bits: List, dtype):
    return bb.join(np.array(bits[::-1]), dtype=dtype)


def _fold_ctrl(bb, ctrl, xb, n):
    """x'_i = ctrl AND x_i for i < n (one And each). Returns (ctrl, xb, x' list)."""
    xp = []
    for i in range(n):
        (ctrl, xb[i]), t = bb.add(And(), ctrl=np.array([ctrl, xb[i]]))
        xp.append(t)
    return ctrl, xb, xp


def _unfold_ctrl(bb, ctrl, xb, xp):
    for i in reversed(range(len(xp))):
        ctrl, xb[i] = bb.add(And().adjoint(), ctrl=np.array([ctrl, xb[i]]), target=xp[i])
    return ctrl, xb


# ============================================================================= signed product

@frozen
class SignedProduct(Bloq):
    """``|x>|z>|0> -> |x>|z>|s(x) s(z)>`` (``uncompute``: the inverse) for signed QInt(wx) x, QInt(wz) z
    into a (wx+wz)-bit two's-complement register p, in exactly 2 wx wz Ands.

    Row i (i = 0..wx-1) adds x_i * z * 2^i (row wx-1, x's sign bit, subtracts it). Before row i the
    partial sum is a signed (i+wz)-bit number in p[0 : i+wz]; the row sign-extends it by one CNOT
    p[i+wz-1] -> p[i+wz], ANDs x_i into the wz bits of z (wz Ands), sign-extends that addend to wz+1
    bits (CNOT) and runs ``Add(QUInt(wz+1))`` (wz Ands) on p[i : i+wz+1]. Subtraction is ``~(~A + d)``.
    Uncompute replays the rows in reverse."""
    wx: int
    wz: int
    uncompute: bool = False

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.wx)), Register("z", QInt(self.wz)),
                          Register("p", QUInt(self.wx + self.wz))])

    def _row(self, bb, i, xb, zb, pb, subtract):
        wz = self.wz
        t = []
        for j in range(wz):
            (xb[i], zb[j]), tj = bb.add(And(), ctrl=np.array([xb[i], zb[j]]))
            t.append(tj)
        e = bb.allocate(dtype=QBit())
        t[wz - 1], e = bb.add(CNOT(), ctrl=t[wz - 1], target=e)
        win = list(range(i, i + wz + 1))
        if subtract:
            for q in win:
                pb[q] = bb.add(XGate(), q=pb[q])
        a = _join_lsb(bb, t + [e], QUInt(wz + 1))
        s = _join_lsb(bb, [pb[q] for q in win], QUInt(wz + 1))
        a, s = bb.add(Add(QUInt(wz + 1)), a=a, b=s)
        for q, bit in zip(win, _lsb_bits(bb, s)):
            pb[q] = bit
        if subtract:
            for q in win:
                pb[q] = bb.add(XGate(), q=pb[q])
        ab = _lsb_bits(bb, a)
        t, e = ab[:wz], ab[wz]
        t[wz - 1], e = bb.add(CNOT(), ctrl=t[wz - 1], target=e)
        bb.free(e)
        for j in range(wz):
            xb[i], zb[j] = bb.add(And().adjoint(), ctrl=np.array([xb[i], zb[j]]), target=t[j])

    def build_composite_bloq(self, bb: BloqBuilder, x, z, p) -> Dict[str, SoquetT]:
        wx, wz = self.wx, self.wz
        xb, zb, pb = _lsb_bits(bb, x), _lsb_bits(bb, z), _lsb_bits(bb, p)
        rows = range(wx - 1, -1, -1) if self.uncompute else range(wx)
        for i in rows:
            sign_row = i == wx - 1
            if not self.uncompute:
                pb[i + wz - 1], pb[i + wz] = bb.add(CNOT(), ctrl=pb[i + wz - 1], target=pb[i + wz])
                self._row(bb, i, xb, zb, pb, subtract=sign_row)
            else:
                self._row(bb, i, xb, zb, pb, subtract=not sign_row)
                pb[i + wz - 1], pb[i + wz] = bb.add(CNOT(), ctrl=pb[i + wz - 1], target=pb[i + wz])
        return {"x": _join_lsb(bb, xb, QInt(wx)), "z": _join_lsb(bb, zb, QInt(wz)),
                "p": _join_lsb(bb, pb, QUInt(wx + wz))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        wx, wz = self.wx, self.wz
        n_sub = (wx - 1) if self.uncompute else 1          # rows run as subtractions
        out = {And(): wx * wz, And().adjoint(): wx * wz, Add(QUInt(wz + 1)): wx,
               CNOT(): 3 * wx, XGate(): 2 * (wz + 1) * n_sub}
        return {k: v for k, v in out.items() if v}

    def __str__(self):
        return f"SignedProduct{'^-1' if self.uncompute else ''}({self.wx},{self.wz})"


def SignedSchoolbookProduct(w: int, uncompute: bool = False) -> SignedProduct:
    """Legacy square form: ``SignedProduct(w, w, uncompute)``."""
    return SignedProduct(w, w, uncompute)


# ============================================================================= leaves

def _draws_conv(d):
    return None if d is None else tuple(int(bool(v)) for v in d)


class _LeafBase(Bloq):
    route: str = "?"

    @cached_property
    def signature(self) -> Signature:
        regs = [Register("ctrl", QBit())] if self.ctrl else []
        regs += [Register("x", QInt(self.wx)), Register("z", QInt(self.wz)),
                 Register("catalyst", QUInt(self.b))]
        return Signature(regs)

    @property
    def w(self) -> int:
        """Square-leaf alias (legacy)."""
        return self.wx

    def error_bound(self) -> float:
        """Per-REALISATION operator-norm bound (radians): every classical basis state's realised turn
        is within this of tau*s(x)*s(z) (for mixing: sum over kicks of 2 pi |k_l/2^b - v_l|)."""
        raise NotImplementedError

    def realised_turn(self, x: int, z: int) -> Fraction:
        """V(x,z)/2^b: what the circuit adds to the catalyst when active (classical closed form)."""
        raise NotImplementedError

    def _passthrough(self, soqs):
        return dict(soqs)


@frozen
class LeafP(_LeafBase):
    """Product route on a wx x wz signed leaf (see module docstring)."""
    wx: int
    wz: int
    b: int
    tau: Fraction = field(converter=_check_tau)
    ctrl: bool = False
    mixing: bool = True
    seed: int = 0
    draws: Optional[Tuple[int, ...]] = field(default=None, converter=_draws_conv)
    route = "P"

    def __attrs_post_init__(self):
        _check_shape(self.wx, self.wz, self.b)

    @cached_property
    def kicks(self) -> Tuple[_PKick, ...]:
        return _p_kicks(self.wx, self.wz, self.b, self.tau, self.mixing, self.seed, self.draws)

    @cached_property
    def slices(self) -> Tuple[_Slice, ...]:
        return tuple(s for s in (_const_slice(k.l, self.b, k.k, k.exact) for k in self.kicks) if s)

    @property
    def constants(self) -> List[int]:
        """k_l mod 2^b (the realised constants)."""
        return [k.k % (1 << self.b) for k in self.kicks]

    @cached_property
    def cost(self) -> LeafCost:
        return _p_cost_concrete(self.wx, self.wz, self.b, self.ctrl, self.kicks, self.mixing)

    @property
    def kick_diamond_bounds(self) -> List[float]:
        """Per-kick diamond bound 8 pi^2 u d (0 for exact kicks; deterministic kicks are reported in
        err_op instead, so this is 0 for them too)."""
        out = []
        for k in self.kicks:
            if k.exact or not self.mixing:
                out.append(0.0)
            else:
                d = k.s_frac / (1 << self.b)
                out.append(8 * _PI ** 2 * float((Fraction(1, 1 << self.b) - d) * d))
        return out

    @property
    def err_op(self) -> float:
        return self.cost.err_op

    @property
    def err_diamond(self) -> float:
        return self.cost.err_diamond

    def error_bound(self) -> float:
        return sum(2 * _PI * float(abs(Fraction(k.k, 1 << self.b) - k.v))
                   for k in self.kicks if not k.exact)

    def build_composite_bloq(self, bb: BloqBuilder, **soqs) -> Dict[str, SoquetT]:
        if not self.slices:
            return self._passthrough(soqs)
        wx, wz, b, n = self.wx, self.wz, self.b, self.wx + self.wz
        ctrl = soqs.get("ctrl")
        x, z, cat = soqs["x"], soqs["z"], soqs["catalyst"]
        if self.ctrl:
            xb = _lsb_bits(bb, x)
            ctrl, xb, xp = _fold_ctrl(bb, ctrl, xb, wx)
            xin = _join_lsb(bb, xp, QInt(wx))
        else:
            xin = x
        p = bb.allocate(dtype=QUInt(n))
        xin, z, p = bb.add(SignedProduct(wx, wz), x=xin, z=z, p=p)
        pb, cb = _lsb_bits(bb, p), _lsb_bits(bb, cat)
        for s in self.slices:
            ph = _join_lsb(bb, cb[b - s.L:], QUInt(s.L))
            pb[s.idx], ph = bb.add(CtrlAddConstIntoCatalyst(s.L, s.kk), ctrl=pb[s.idx], phase=ph)
            cb[b - s.L:] = _lsb_bits(bb, ph)
        cat = _join_lsb(bb, cb, QUInt(b))
        p = _join_lsb(bb, pb, QUInt(n))
        xin, z, p = bb.add(SignedProduct(wx, wz, uncompute=True), x=xin, z=z, p=p)
        bb.free(p)
        out = {"z": z, "catalyst": cat}
        if self.ctrl:
            ctrl, xb = _unfold_ctrl(bb, ctrl, xb, _lsb_bits(bb, xin))
            out["x"] = _join_lsb(bb, xb, QInt(wx))
            out["ctrl"] = ctrl
        else:
            out["x"] = xin
        return out

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        if not self.slices:
            return {}
        out: Dict[Bloq, int] = {SignedProduct(self.wx, self.wz): 1,
                                SignedProduct(self.wx, self.wz, uncompute=True): 1}
        for s in self.slices:
            bl = CtrlAddConstIntoCatalyst(s.L, s.kk)
            out[bl] = out.get(bl, 0) + 1
        if self.ctrl:
            out[And()] = self.wx
            out[And().adjoint()] = self.wx
        return out

    def realised_turn(self, x: int, z: int) -> Fraction:
        n, b = self.wx + self.wz, self.b
        p = (x * z) % (1 << n)
        V = sum(s.kk << (b - s.L) for s in self.slices if p >> s.idx & 1)
        return Fraction(V % (1 << b), 1 << b)

    def __str__(self):
        return (f"LeafP({self.wx}x{self.wz}, b={self.b}{', ctrl' if self.ctrl else ''}"
                f"{', mix' if self.mixing else ''})")


@frozen
class LeafY(_LeafBase):
    """Y route on a wx x wz signed leaf (see module docstring). Deterministic."""
    wx: int
    wz: int
    b: int
    tau: Fraction = field(converter=_check_tau)
    ctrl: bool = False
    route = "Y"

    def __attrs_post_init__(self):
        _check_shape(self.wx, self.wz, self.b)

    @property
    def acc(self) -> int:
        return _y_acc(self.wx, self.wz, self.b, turn_resolution(self.tau))

    @cached_property
    def yconsts(self) -> Tuple[_YConst, ...]:
        return _y_consts(self.wz, self.acc, self.tau) if self.acc > 0 else ()

    @property
    def constants(self) -> List[int]:
        return [c.C for c in self.yconsts]

    @cached_property
    def slices(self) -> Tuple[_Slice, ...]:
        A = self.acc
        return tuple(s for s in (_const_slice(c.j, A, c.C, c.exact) for c in self.yconsts) if s)

    @cached_property
    def rows(self):
        return _y_rows(self.wx, self.acc, self.b) if self.slices else ()

    @cached_property
    def cost(self) -> LeafCost:
        if self.acc <= 0:
            return LeafCost("Y", 0, 0.0, 0.0, 0)
        return _y_cost_concrete(self.wx, self.wz, self.b, self.ctrl, self.acc, self.yconsts)

    @property
    def err_op(self) -> float:
        return self.cost.err_op

    @property
    def err_diamond(self) -> float:
        return 0.0

    def error_bound(self) -> float:
        return self.cost.err_op

    def _row_bloq(self, kind: str, d: int) -> CtrlAddIntoCatalyst:
        return CtrlAddIntoCatalyst(d, d) if kind == "exact" else CtrlAddIntoCatalyst(self.b, self.b, carry_in=True)

    def build_composite_bloq(self, bb: BloqBuilder, **soqs) -> Dict[str, SoquetT]:
        if not self.slices:
            return self._passthrough(soqs)
        wx, wz, b, A = self.wx, self.wz, self.b, self.acc
        ctrl = soqs.get("ctrl")
        x, z, cat = soqs["x"], soqs["z"], soqs["catalyst"]
        zb, xb = _lsb_bits(bb, z), _lsb_bits(bb, x)
        xc = xb
        if self.ctrl:
            ctrl, xb, xp = _fold_ctrl(bb, ctrl, xb, len(self.rows))
            xc = xp
        Y = bb.allocate(dtype=QUInt(A))
        yb = _lsb_bits(bb, Y)

        def const_add(s: _Slice, kk: int):
            seg = _join_lsb(bb, yb[A - s.L:], QUInt(s.L))
            zb[s.idx], seg = bb.add(CtrlAddConstIntoCatalyst(s.L, kk), ctrl=zb[s.idx], phase=seg)
            yb[A - s.L:] = _lsb_bits(bb, seg)

        for s in self.slices:                                   # compute Y
            const_add(s, s.kk)
        for i, kind, d in self.rows:
            L = d if kind == "exact" else b
            neg = i == wx - 1                                   # sign row: P - v = ~(~P + v)
            cb = _lsb_bits(bb, cat)
            if neg:
                for q in range(b - L, b):
                    cb[q] = bb.add(XGate(), q=cb[q])
            ph = _join_lsb(bb, cb[b - L:], QUInt(L))
            if kind == "exact":
                W = _join_lsb(bb, yb[0:d], QUInt(d))
                xc[i], W, ph = bb.add(self._row_bloq(kind, d), ctrl=xc[i], x=W, phase=ph)
                yb[0:d] = _lsb_bits(bb, W)
            else:
                lo = d - b - 1                                  # carry-in bit; window [lo+1, d)
                W = _join_lsb(bb, yb[lo + 1:d], QUInt(b))
                xc[i], W, yb[lo], ph = bb.add(self._row_bloq(kind, d), ctrl=xc[i], x=W, cin=yb[lo],
                                              phase=ph)
                yb[lo + 1:d] = _lsb_bits(bb, W)
            cb[b - L:] = _lsb_bits(bb, ph)
            if neg:
                for q in range(b - L, b):
                    cb[q] = bb.add(XGate(), q=cb[q])
            cat = _join_lsb(bb, cb, QUInt(b))
        for s in reversed(self.slices):                         # uncompute Y
            const_add(s, (-s.kk) % (1 << s.L))
        bb.free(_join_lsb(bb, yb, QUInt(A)))
        out = {"z": _join_lsb(bb, zb, QInt(wz)), "catalyst": cat}
        if self.ctrl:
            ctrl, xb = _unfold_ctrl(bb, ctrl, xb, xc)
            out["ctrl"] = ctrl
        out["x"] = _join_lsb(bb, xb, QInt(wx))
        return out

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        if not self.slices:
            return {}
        out: Dict[Bloq, int] = {}

        def inc(bl, k=1):
            out[bl] = out.get(bl, 0) + k

        for s in self.slices:
            inc(CtrlAddConstIntoCatalyst(s.L, s.kk))
            inc(CtrlAddConstIntoCatalyst(s.L, (-s.kk) % (1 << s.L)))
        for i, kind, d in self.rows:
            inc(self._row_bloq(kind, d))
            if i == self.wx - 1:
                inc(XGate(), 2 * (d if kind == "exact" else self.b))
        if self.ctrl:
            inc(And(), len(self.rows))
            inc(And().adjoint(), len(self.rows))
        return out

    def realised_turn(self, x: int, z: int) -> Fraction:
        b, wx, wz = self.b, self.wx, self.wz
        if not self.slices:
            return Fraction(0)
        A = self.acc
        zu = z % (1 << wz)
        Y = sum(c.C for c in self.yconsts if zu >> c.j & 1) % (1 << A)
        xu = x % (1 << wx)
        V = 0
        for i, kind, d in self.rows:
            if not xu >> i & 1:
                continue
            if kind == "exact":
                val = (Y % (1 << d)) << (b - d)
            else:
                win = (Y >> (d - b - 1)) & ((1 << (b + 1)) - 1)
                val = (win >> 1) + (win & 1)
            V += -val if i == wx - 1 else val
        return Fraction(V % (1 << b), 1 << b)

    def __str__(self):
        return f"LeafY({self.wx}x{self.wz}, b={self.b}{', ctrl' if self.ctrl else ''})"


# ============================================================================= public constructor

def catalytic_leaf(wx: int, wz: int, b: int, tau: Fraction, ctrl: bool = False, *,
                   mixing: bool = True, seed: int = 0, draws: Optional[Sequence[int]] = None,
                   route: Optional[str] = None) -> _LeafBase:
    """The merged-catalysis leaf exp(2 pi i tau s(x) s(z)) on x: QInt(wx), z: QInt(wz), catalyst
    QUInt(b) [, ctrl: QBit]. Route = fewer CLASS Ands (ties -> Y) unless forced; its cost is
    ``leaf_cost(wx, wz, b, tau, ctrl, mixing=..., seed=..., draws=...)``."""
    _check_shape(wx, wz, b)
    tau = _check_tau(tau)
    pick = leaf_cost(wx, wz, b, tau, ctrl, mixing=mixing, seed=seed, draws=draws, route=route)
    if pick.route == "P":
        return LeafP(wx, wz, b, tau, bool(ctrl), bool(mixing), int(seed), draws)
    return LeafY(wx, wz, b, tau, bool(ctrl))


# ============================================================================= legacy square wrappers

def CatalyticLeafP(w: int, b: int, tau: Fraction, ctrl: bool = False, *, mixing: bool = False,
                   seed: int = 0, draws=None) -> LeafP:
    """Legacy square P leaf (deterministic by default): ``LeafP(w, w, b, tau, ctrl, mixing, ...)``."""
    return LeafP(w, w, b, tau, ctrl, mixing, seed, draws)


def CatalyticLeafY(w: int, b: int, tau: Fraction, ctrl: bool = False) -> LeafY:
    """Legacy square Y leaf: ``LeafY(w, w, b, tau, ctrl)``."""
    return LeafY(w, w, b, tau, ctrl)
