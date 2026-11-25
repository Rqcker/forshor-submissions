"""An independent first-principles reference for the per-family QFT design price, checked against `qft_overlay` on
seeded random synthetic runs and on the recorded measured fixture.

Stdlib, plus sympy for the one symbolic identity (that test is skipped, with its reason, where sympy is missing). Not a
re-derivation of the overlay: the band error is the sum of the dropped controlled-phase angles,
(w - k - 2) 2^-k + 2^(1 - w) turns-of-pi per call, written in its own closed form, with its own bound on pi; the ledger
is checked semantically (sum <= half of delta), and known-bad rules must fail it. Only the overlay's entry points are
called; none of its formulas or constants supplies an expectation.
"""
import math
import random
from fractions import Fraction

import pytest

from export_v2 import engine, qft_overlay as qo

try:
    import sympy as sp
except ImportError:                                  # pragma: no cover - the pinned environment ships it
    sp = None

#: A rational upper bound on pi, independent of the overlay's own constant (pi < 3.14159265358979323846264338328).
PI_UP = Fraction(314159265358979323846264338328, 10 ** 29)
assert Fraction(math.pi) < PI_UP < Fraction(math.pi) + Fraction(1, 10 ** 15)
SEEDS = range(40)


def ref_band_error(w, calls, k):
    """pi_up * calls * sum_{d > k} (w - d) 2^-d, closed form (Coppersmith band, union bound)."""
    return PI_UP * calls * (Fraction(w - k - 2, 1 << k) + Fraction(2, 1 << w))


def ref_k(w, calls, a):
    lo, hi = 1, w - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if ref_band_error(w, calls, mid) <= a:
            hi = mid
        else:
            lo = mid + 1
    return lo


@pytest.mark.skipif(sp is None, reason="sympy is not installed; the closed form is still checked numerically below")
def test_band_error_is_symbolically_the_reference():
    n, k, c = sp.symbols("n k c", integer=True, positive=True)
    m = n - k - 1
    impl = 2 * c * ((m - 1) * 2 ** m + 1) / sp.Integer(2) ** n              # qo.band_error / PI_UP at depth k+1
    ref = c * ((n - k - 2) * sp.Integer(2) ** (-k) + sp.Integer(2) ** (1 - n))
    assert sp.simplify(impl - ref) == 0


@pytest.mark.parametrize("seed", SEEDS)
def test_band_rule_and_census_match_the_reference(seed):
    rnd = random.Random(seed)
    for _ in range(50):
        w = rnd.randint(4, 5000)
        calls = rnd.randint(1, 20000)
        a = Fraction(rnd.randint(1, 999), 10 ** rnd.randint(2, 30))
        depth = qo.band_depth(w, calls, a)
        assert depth - 1 == ref_k(w, calls, a)
        k = depth - 1
        assert qo.band_error(w, calls, depth) == ref_band_error(w, calls, k) <= a
        assert qo.band_census(w, k) == (4 * (k * (2 * w - k - 1) // 2) + (w - 2 if k >= 2 else 0),
                                        (k - 2) * (2 * w - k - 3) // 2 if k >= 3 else 0)


@pytest.mark.parametrize("rule", ["per_call", "no_pi", "no_calls_in_k"])
def test_known_bad_band_rules_break_the_ledger(rule):
    """The OLD per-call rule and its relatives must violate sum(E_f) <= allowance on the real family shapes."""
    shapes = [(2049, 8190), (2347, 8190), (4398, 16380)]
    a = Fraction(25, 100000)
    broken = False
    for w, calls in shapes:
        if rule == "per_call":
            k = max(1, math.ceil(math.log2(w / float(a))))               # the old aqft_k_max(w, eta)
        elif rule == "no_pi":
            k = next(K for K in range(1, w) if Fraction(calls * w, 1 << K) <= a)
        else:
            k = ref_k(w, 1, a)
        broken |= ref_band_error(w, calls, k) > a
    assert broken


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_accepted_designs_fit_half_delta_semantically(seed):
    """On the recorded measured fixture: for every accepted design, the sum of the reference family bounds plus the
    preparation plus R eps is at most delta / 2; a design whose allowances reach the pool is refused."""
    import gzip, json
    from pathlib import Path
    from intake_v2 import policy_from_document
    eng = engine.load()
    doc = json.loads(gzip.decompress((Path(__file__).parent / "data" / "readout_submitted_931bbf1.json.gz").read_bytes()))
    P = policy_from_document(eng, doc["document"]).with_widths("exact")
    coh = eng.fullalg.price_full(P, 2048, 0.01, detail=True)
    rnd = random.Random(seed)
    fams = ("data", "work", "scratch")
    design = {f: {"backend": rnd.choice(["ross_selinger", "catalytic"]), "target": "approximate",
                  "max_error": f"{rnd.randint(1, 30)}e-{rnd.randint(4, 7)}"} for f in rnd.sample(fams, rnd.randint(1, 3))}
    allowance = {f: Fraction(int(v["max_error"].split("e-")[0]), 10 ** int(v["max_error"].split("e-")[1])) for f, v in design.items()}
    claimed = sum(allowance.values())
    if claimed >= Fraction(1, 200):
        with pytest.raises(qo.QftOverlayRefused):
            qo.project(P, coh, design)
        return
    j = qo.project(P, coh, design)
    widths = {"data": coh["widths"]["yW"] + 1, "work": coh["widths"]["workW"], "scratch": coh["widths"]["scratchW"]}
    calls = {"data": 8190, "work": 8190, "scratch": 16380}
    spent = Fraction(0)
    for f, x in j["families"].items():
        if x["construction"] == "banded":
            spent += ref_band_error(widths[f], calls[f], x["kMax"])
        else:
            assert Fraction(x["errorBound"]) <= allowance[f]
            spent += Fraction(x["errorBound"])
    spent += Fraction(j["budget"]["prepBudget"]) + j["rotations"] * Fraction(j["eps"])
    assert spent <= Fraction(1, 200)
