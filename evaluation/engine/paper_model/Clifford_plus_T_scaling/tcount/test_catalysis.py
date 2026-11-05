"""Checks of `tcount.catalysis`: exact classical simulation of the diagonal phases it prices.

Every construction priced is diagonal in the computational basis, so its action is fully described
by the phase it applies to each basis state |x, z>. These tests enumerate every basis state of small
instances and compare the merged / per-site realisation's phase with the exact phase product,
against the error bound the pricing charges. Standalone: `python -m pytest tcount/test_catalysis.py`
from `Clifford_plus_T_scaling/` (no Qualtran, no Lean, no pygridsynth).
"""
from __future__ import annotations

import cmath
import math
import random
from fractions import Fraction

import pytest

from tcount import catalysis as C


def _phase_err(a: float, b: float) -> float:
    """Operator-norm distance |e^{2pi i a} - e^{2pi i b}| of two diagonal phases (turns)."""
    return abs(cmath.exp(2j * math.pi * a) - cmath.exp(2j * math.pi * b))


def _round_b(v: Fraction, b: int) -> Fraction:
    """Round a turn to the nearest multiple of 2^-b (the carry-in of the next bit)."""
    return Fraction(math.floor(v * 2 ** b + Fraction(1, 2)), 2 ** b)


# --------------------------------------------------------------------------- per-site prices

def test_exact_site_prices():
    assert [C.exact_site_t(s) for s in range(0, 4)] == [0, 0, 0, 1]
    assert C.exact_site_t(4) == 9 and C.exact_site_t(10) == 8 * 7 + 1
    assert C.exact_site_t(10, C.PAIR_T_MEASURED) == 4 * 7 + 1


def test_price_catalysis_picks_smallest_feasible_b():
    hist = {"generic": 1000, "p5": 50, 7: 30}
    p = C.price_catalysis(hist, 1e-3)
    assert p.error <= 1e-3
    # one bit less must break the budget
    _, err_below, *_ = C._price_at(hist, p.b - 1, False, C.PAIR_T_UNITARY)
    assert err_below > 1e-3 - 1e-3 * 1e-3


def test_exact_sites_spend_no_error():
    p = C.price_catalysis({"p6": 10 ** 6, 9: 10 ** 6}, 1e-9)
    assert p.b >= 9 and p.rounded_sites == 0 and p.exact_sites == 2 * 10 ** 6


# --------------------------------------------------------------------------- merged twiddle (Amy §VI.C)

@pytest.mark.parametrize("r1,r2,b", [(2, 3, 2), (3, 3, 3), (3, 4, 4), (4, 4, 3), (2, 5, 6), (4, 3, 5)])
def test_merged_twiddle_matches_phase_product(r1, r2, b):
    """exp(2pi i x z / 2^t) as rows: x_i · round_b(frac(z 2^(i-t))), t = r1 + r2 (a QFT twiddle)."""
    t = r1 + r2
    lo, hi = (r1, r2) if r1 <= r2 else (r2, r1)
    _, rounded = C._twiddle_rows(r1, r2, t, b)
    bound = rounded * math.pi * 2.0 ** (-b)
    worst = 0.0
    for x in range(2 ** lo):
        for z in range(2 ** hi):
            exact = Fraction(x * z, 2 ** t) % 1
            merged = sum((_round_b(Fraction(z * 2 ** i, 2 ** t) % 1, b) for i in range(lo) if x >> i & 1),
                         Fraction(0)) % 1
            worst = max(worst, _phase_err(float(exact), float(merged)))
    assert worst <= bound + 1e-12
    if rounded == 0:
        assert worst < 1e-12


def test_twiddle_row_counts():
    ands, rounded = C._twiddle_rows(4, 4, 8, 64)
    assert rounded == 0 and ands == sum(min(64, 8 - i) + 4 for i in range(4))
    ands, rounded = C._twiddle_rows(4, 4, 8, 5)
    assert rounded == 3                                    # rows i = 0, 1, 2 have 8 - i > 5


# --------------------------------------------------------------------------- merged generic leaf

@pytest.mark.parametrize("w,b,seed", [(3, 4, 1), (3, 6, 2), (4, 5, 3), (4, 8, 4)])
def test_leaf_product_route(w, b, seed):
    """P route: p = x·z digitally, then one rounded constant frac(tau 2^l) per bit of p."""
    rnd = random.Random(seed)
    tau = Fraction(rnd.getrandbits(40) | 1, 2 ** 40) + Fraction(1, 3 * 2 ** 41)   # non-dyadic part
    consts = [_round_b((tau * 2 ** l) % 1, b) for l in range(2 * w)]
    bound = 2 * w * math.pi * 2.0 ** (-b)
    worst = 0.0
    for x in range(2 ** w):
        for z in range(2 ** w):
            p = x * z
            merged = sum((consts[l] for l in range(2 * w) if p >> l & 1), Fraction(0)) % 1
            worst = max(worst, _phase_err(float((tau * p) % 1), float(merged)))
    assert worst <= bound + 1e-12


@pytest.mark.parametrize("w,b,seed", [(3, 4, 5), (4, 5, 6), (4, 7, 7)])
def test_leaf_Y_route(w, b, seed):
    """Y route: Y = sum_j z_j trunc(frac(tau 2^j)) to b+w+g bits, row i adds round_b(frac(2^i Y))."""
    rnd = random.Random(seed)
    tau = Fraction(rnd.getrandbits(48) | 1, 2 ** 48) + Fraction(1, 5 * 2 ** 49)
    g = max(1, math.ceil(math.log2(max(w, 2)))) + 1
    acc = b + w + g
    cj = [Fraction(math.floor(((tau * 2 ** j) % 1) * 2 ** acc), 2 ** acc) for j in range(w)]
    bound = w * 2 * math.pi * 2.0 ** (-b)                # the 2 pi 2^-b per row the pricing charges
    worst = 0.0
    for z in range(2 ** w):
        Y = sum((cj[j] for j in range(w) if z >> j & 1), Fraction(0)) % 1
        rows = [_round_b((Y * 2 ** i) % 1, b) for i in range(w)]
        for x in range(2 ** w):
            merged = sum((rows[i] for i in range(w) if x >> i & 1), Fraction(0)) % 1
            worst = max(worst, _phase_err(float((tau * x * z) % 1), float(merged)))
    assert worst <= bound + 1e-12


def test_merged_price_is_monotone_and_feasible():
    census = {("tw", 20, 20, 40, 441, 5000): 10, ("leaf", 12, True): 100, ("leaf", 12, False): 50}
    m = C.price_merged(census, 1e-4)
    assert m.error <= 1e-4 + 1e-12
    assert m.delta_T > -m.refund_T                          # never cheaper than refunding everything
