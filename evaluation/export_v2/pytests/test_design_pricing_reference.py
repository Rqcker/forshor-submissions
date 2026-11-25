"""An independent first-principles reference for the per-family QFT design price, checked against `qft_overlay` on
seeded random synthetic runs and on the recorded measured fixture.

Stdlib, plus sympy for the one symbolic identity (that test is skipped, with its reason, where sympy is missing). Not a
re-derivation of the overlay: the band error is the sum of the dropped controlled-phase angles,
(w - k - 2) 2^-k + 2^(1 - w) turns-of-pi per call, written in its own closed form, with its own bound on pi; the ledger
is checked semantically (sum <= half of delta), and known-bad rules must fail it. Only the overlay's entry points are
called; none of its formulas or constants supplies an expectation.

A catalytic family is the same band, its kept phases applied as one controlled addition per target into the shared
phase-gradient register (Nam, Su & Maslov, npj Quantum Inf. 6, 26 (2020), section V): each kept controlled phase costs
8 T (4 T to map it onto the addition's operand, 4 T for its bit of the adder), no rotation, and the additions are exact
on a register of k + 1 bits, so they add no error. Golden vectors below were worked out from that statement alone.
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


def ref_merged_T_per_call(w, k):
    """8 T for every kept controlled phase (distances 1..k, w - d of each)."""
    return 8 * sum(w - d for d in range(1, k + 1))


#: (width, calls, max_error or None for exact) -> (k, catalyst width k + 1, T per call), on the recorded fixture's
#: families. Literals, so a change in either the band or the merged count is caught without trusting a formula.
GOLDEN_MERGED = [
    (("data", 2049, 8190, "25e-5"), (38, 39, 616968)),
    (("work", 2347, 8190, "25e-5"), (38, 39, 707560)),
    (("scratch", 4398, 16380, "25e-5"), (40, 41, 1400800)),
    (("data", 2049, 8190, "1e-3"), (36, 37, 584784)),
    (("work", 2347, 8190, "1e-6"), (46, 47, 855048)),
    (("scratch", 4398, 16380, "1e-6"), (48, 49, 1679424)),
    (("data", 2049, 8190, None), (2048, 2049, 16785408)),
    (("scratch", 4398, 16380, None), (4397, 4398, 77352024)),
]


@pytest.mark.parametrize("case,expected", GOLDEN_MERGED, ids=[f"{c[0]}-{c[3] or 'exact'}" for c, _ in GOLDEN_MERGED])
def test_banded_merged_golden_vectors(case, expected):
    family, w, calls, max_error = case
    k, b, t = expected
    a = Fraction(0) if max_error is None else Fraction(int(max_error.split("e-")[0]), 10 ** int(max_error.split("e-")[1]))
    assert (ref_k(w, calls, a) if a else w - 1) == k and ref_merged_T_per_call(w, k) == t and b == k + 1
    assert ref_band_error(w, calls, k) <= a < (ref_band_error(w, calls, k - 1) if k > 1 else 1)
    choice = {"backend": "catalytic", "target": "exact"} if max_error is None else \
        {"backend": "catalytic", "target": "approximate", "max_error": max_error}
    P, coh = _fixture()
    r = qo.project(P, coh, {family: choice})
    x = r["families"][family]
    assert (x["width"], x["count"], x["construction"]) == (w, calls, "bandedMerged")
    assert (x["kMax"], x["b"], x["mergedTBase"], r["catalyst"]["b"]) == (k, b, t, b)
    assert r["TBase"] == coh["T_base"] + (t - x["exactTBase"]) * calls
    assert r["rotations"] == coh["rotations"] - x["exactRotations"] * calls       # no rotation is synthesised


def test_known_bad_catalytic_rules_miss_the_golden_vectors():
    """A catalysis that kicks each kept phase separately (a b - 1 And addition per phase), or one that rounds onto a
    register narrower than k + 1 bits, cannot reproduce the golden vectors: the first costs more, the second keeps a
    shallower band than its catalyst can hold exactly."""
    for (family, w, calls, max_error), (k, b, t) in GOLDEN_MERGED[:3]:
        per_rotation = sum(w - d for d in range(1, k + 1)) * 4 * (b - 1)
        assert per_rotation > t
        assert b - 1 < k + 1                                 # a one-bit-short register cannot hold the distance-k phase


_FIXTURE = {}


def _fixture():
    if not _FIXTURE:
        import gzip, json
        from pathlib import Path
        from intake_v2 import policy_from_document
        eng = engine.load()
        doc = json.loads(gzip.decompress((Path(__file__).parent / "data" / "readout_submitted_931bbf1.json.gz").read_bytes()))
        P = policy_from_document(eng, doc["document"]).with_widths("exact")
        _FIXTURE["v"] = (P, eng.fullalg.price_full(P, 2048, 0.01, detail=True))
    return _FIXTURE["v"]


def test_the_headline_design_is_recomputed_from_the_census():
    """All three families catalytic at 25e-5 under the semiclassical readout, from the census and the statement above
    alone: the three bands, 8 T per kept phase, a 41-qubit register, the pool less the allowances and the register's
    preparation share, divided once."""
    from export_v2 import readout_overlay, readout_qft_overlay
    P, coh = _fixture()
    m = readout_overlay.project(P, coh)
    base, rot = m["TBase"], m["rotations"]
    for (family, w, calls, _e), (k, b, t) in GOLDEN_MERGED[:3]:
        part = [p for p in coh["parts"] if p["site"] == "qft" and p["xw"] == w and p["zw"] == 0][0]
        base += (t - round(part["T"] - part["rotations"] * coh["T_per_rotation"]) // calls) * calls
        rot -= part["rotations"]
    pool = Fraction(1, 200)
    claimed = 3 * Fraction(25, 10 ** 5)
    prep = min(pool / 10, (pool - claimed) / 2)
    eps = float((pool - claimed - prep) / rot)
    prep_t = 38 * (3.075 * math.log2(38 / float(prep)) + 0.86) + 1
    by_hand = base + rot * (3.075 * math.log2(1 / eps) + 0.86) + prep_t
    j = readout_qft_overlay.project(P, coh, {f: {"backend": "catalytic", "target": "approximate", "max_error": "25e-5"}
                                             for f in ("data", "work", "scratch")})
    assert (j["TBase"], j["rotations"], j["catalyst"]["b"], j["qubits"]) == (base, rot, 41, m["qubits"] + 41)
    assert j["T"] == pytest.approx(by_hand, rel=1e-12) and j["T"] == 1020219052649.4625


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
        # Both constructions are bands; a catalytic family's additions are exact only on k + 1 bits or more.
        assert x["construction"] in ("banded", "bandedMerged")
        if x["construction"] == "bandedMerged":
            assert x["b"] == x["kMax"] + 1 <= j["catalyst"]["b"]
        spent += ref_band_error(widths[f], calls[f], x["kMax"])
    spent += Fraction(j["budget"]["prepBudget"]) + j["rotations"] * Fraction(j["eps"])
    assert spent <= Fraction(1, 200)
