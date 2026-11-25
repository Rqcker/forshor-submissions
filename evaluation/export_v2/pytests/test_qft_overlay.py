"""The per-family QFT design overlay: bit-identical defaults, family-total error allowances carved from the
synthesis pool, the closed-form band, banded merged catalysis, and fail-closed refusals.

Every test names the production change in `export_v2/qft_overlay.py` that would fail it.

Two policies. The STO reference PhaseProduct policy pins the default path (HEAD 792464f's own engine price at
n = 2048, delta = 1e-2, unchanged because the overlay does not touch `evaluation/engine`). The compiled measured
fixture `readout_submitted_931bbf1` pins the design prices against an independent pricer on the same policy and
topology (`RESEARCH`, below).
"""
from fractions import Fraction
import gzip
import json
import math
from pathlib import Path

import pytest

from export_v2 import engine, epoch, qft_overlay as overlay
from export_v2.canon import epoch_of

#: `fullalg.price_full(sto_reference_policies()[PAIR], 2048, 1e-2, detail=True)` at 792464f.
BASELINE_T = 1938733902110.921
BASELINE_T_BASE = 1298454874920
BASELINE_ROTATIONS = 5180402331
BASELINE_QUBITS_PEAK = 13496
BASELINE_QUBITS_LAYOUT = 47474

EXACT = {"backend": "ross_selinger", "target": "exact"}
CATALYTIC_EXACT = {"backend": "catalytic", "target": "exact"}
FOUR = overlay.FAMILIES


def _approx(max_error="25e-5", backend="ross_selinger"):
    return {"backend": backend, "target": "approximate", "max_error": max_error}


#: All four families catalytic-approximate at 25e-5 on the STO reference policy: bands k = 38, 38, 40 and 26
#: (exponent, 1 call), so the shared register is 40 + 1 = 41 qubits.
CAT25_ALL_T = 522238606513.8889
CAT25_ALL_B = 41

#: The independent pricer (`policy_design.Evaluator.evaluate`, coherent readout, reference topology) on the
#: fixture's policy: `T_total`, gradient width, and its preparation T. Production differs from each by exactly two
#: construction constants (see `test_the_design_prices_match_the_independent_pricer`). Its catalytic arm prices a
#: different construction (the native template's twiddles rounded to b bits), so catalytic families are checked
#: against the first-principles reference in `test_design_pricing_reference.py` instead.
RESEARCH = {
    "all3 rs 25e-5": ({f: _approx() for f in ("data", "work", "scratch")}, 1518352087432.5208, 0, 0),
    "scratch rs 25e-5": ({"scratch": _approx()}, 2037029069961.4434, 0, 0),
}


@pytest.fixture(scope="module")
def priced():
    eng = engine.load()
    policy = eng.library.sto_reference_policies()[eng.tables.PAIR]
    return policy, eng.fullalg.price_full(policy, 2048, 1e-2, detail=True)


@pytest.fixture(scope="module")
def measured_policy():
    eng = engine.load()
    data = Path(__file__).parent / "data" / "readout_submitted_931bbf1.json.gz"
    policy = __import__("intake_v2").policy_from_document(
        eng, json.loads(gzip.decompress(data.read_bytes()))["document"]).with_widths("exact")
    return policy, eng.fullalg.price_full(policy, 2048, 1e-2, detail=True)


# --------------------------------------------------------------------------- the engine is untouched


def test_the_overlay_does_not_move_the_epoch():
    """`qft_overlay.py` lives in `export_v2`, which is not an epoch input. Moving any part of this overlay into
    `evaluation/engine` would fail this test."""
    assert epoch_of(epoch.model_of()) == overlay.EPOCH == "ab1f8887b1b50e56"


@pytest.mark.slow
def test_the_frozen_baseline_is_still_the_engines_own_price(priced):
    _, full = priced
    assert (full["T"], full["T_base"], full["rotations"]) == (BASELINE_T, BASELINE_T_BASE, BASELINE_ROTATIONS)


# --------------------------------------------------------------------------- bit-identical defaults


@pytest.mark.slow
@pytest.mark.parametrize("design", [
    None, {}, {f: EXACT for f in FOUR}, {f: None for f in FOUR}, {"exponent": EXACT}, {"data": None, "work": EXACT},
], ids=["none", "empty", "all-exact", "all-none", "one-exact", "mixed-absent-and-exact"])
def test_a_default_or_all_exact_design_is_bit_identical_to_todays_price(priced, design):
    """BIT-IDENTITY. `ross_selinger` + `exact` must leave the family's QFT exactly as the engine priced it, and a
    design asking nothing must carve nothing out of the pool. Substituting `ReferenceQFT(w)`, or carving a budget on
    the identity path, fails this."""
    policy, full = priced
    out = overlay.project(policy, full, design)
    assert out["identity"] is True and out["substituted"] == [] and out["budget"] is None
    assert out["TBase"] == full["T_base"] == BASELINE_T_BASE
    assert out["rotations"] == full["rotations"] == BASELINE_ROTATIONS
    assert out["tPerRotation"] == full["T_per_rotation"] and out["eps"] == full["eps"]
    assert out["T"] == full["T"] == BASELINE_T
    assert "qubits" not in out and out["qubitsPeak"] == BASELINE_QUBITS_PEAK


@pytest.mark.slow
def test_identity_is_reached_through_the_division_not_by_short_circuit(priced):
    policy, full = priced
    eng = engine.load()
    out = overlay.project(policy, full, {f: EXACT for f in FOUR})
    recomputed = eng.fullalg.sc.ts_per_rotation(full["delta"], out["rotations"])
    assert out["tPerRotation"] == recomputed and out["T"] == out["TBase"] + out["rotations"] * recomputed


# --------------------------------------------------------------------------- Fix A: the allowance is the family's total


def test_the_band_depth_is_the_smallest_whose_total_error_fits_the_allowance():
    """PER-CALL k IS GONE. The family's error is summed over all its calls: data runs 8190 times, so at 25e-5 its band
    keeps distances up to k = 38 (depth 39), not the per-call Coppersmith k = 23. Reverting to `aqft_k_max(w, eta)`
    per call fails the first assertion; dropping the call count from the bound fails the minimality check."""
    allowance = Fraction(25, 10 ** 5)
    for w, calls, depth in ((2049, 8190, 39), (2347, 8190, 39), (4398, 16380, 41), (4095, 1, 27)):
        assert overlay.band_depth(w, calls, allowance) == depth, w
    # The rule itself: the smallest depth whose bound fits, for every family at every offered allowance.
    for w, calls in ((2049, 8190), (2347, 8190), (4398, 16380)):
        for allowance in (Fraction(1, 1000), Fraction(25, 10 ** 5), Fraction(1, 10 ** 6)):
            d = overlay.band_depth(w, calls, allowance)
            assert overlay.band_error(w, calls, d) <= allowance < overlay.band_error(w, calls, d - 1)
            assert d - 1 > max(1, math.ceil(math.log2(w / float(allowance))))   # deeper than the per-call band


def test_the_band_error_is_the_rigorous_sum_of_the_omitted_angles():
    """The bound is 2 PI_UP times the omitted turns (each dropped phase of turn 2^-s costs at most 2 pi 2^-s in
    operator norm), checked against a direct sum. A bound without the 2 pi, or over one call, fails this."""
    assert overlay.PI_UP > Fraction(math.pi) and overlay.PI_UP - Fraction(math.pi) < Fraction(1, 10 ** 15)
    for w, depth in ((7, 3), (40, 10), (100, 39)):
        direct = sum(Fraction(w - s + 1, 2 ** s) for s in range(depth + 1, w + 1))
        assert overlay.omitted_turns(w, depth) == direct
        assert overlay.band_error(w, 5, depth) == 2 * overlay.PI_UP * 5 * direct


@pytest.mark.slow
def test_the_band_census_is_the_closed_form_including_the_tiny_angles(priced):
    """CLOSED FORM. `ReferenceQFT`'s split counts the phases of distance 40..60 as free; a family-total band reaches
    them (scratch at 25e-5 keeps distance 40). The overlay must count them: replacing `band_census` by the engine's
    rotation count fails the strict inequality. Its base T must still be the engine's own."""
    from l2q_full.bloqs.qft import ReferenceQFT
    fa = engine.load().fullalg
    for w, k in ((100, 39), (100, 45), (2049, 38), (4398, 40)):
        base, rot = overlay.band_census(w, k)
        split = fa.cc.split(ReferenceQFT(w, k_max=k))
        assert base == split.base and rot == sum(w - d for d in range(3, k + 1))
        assert (split.rot == rot) is (k < 40)
    policy, full = priced
    out = overlay.project(policy, full, {"scratch": _approx()})
    record = out["families"]["scratch"]
    assert record["kMax"] == 40
    assert record["bandedRotations"] == overlay.band_census(4398, 40)[1] \
        > fa.cc.split(ReferenceQFT(4398, k_max=40)).rot


@pytest.mark.slow
def test_the_allowances_are_carved_out_of_the_pool_before_it_is_divided(priced):
    """THE DELTA DEDUCTION. eps = (pool - allowances - prep) / R, not pool / R. Dropping the deduction (dividing the
    whole pool) fails the eps equality and the pinned price."""
    policy, full = priced
    design = {f: _approx() for f in FOUR}
    out = overlay.project(policy, full, design)
    pool = Fraction(1, 200)
    assert out["budget"]["pool"] == str(pool) and out["budget"]["allowances"] == str(Fraction(4 * 25, 10 ** 5))
    assert out["budget"]["prepBudget"] == "0"                       # no catalyst, no preparation
    rest = pool - Fraction(1, 1000)
    assert out["budget"]["synthesisBudget"] == str(rest)
    assert Fraction(out["eps"]) <= rest / out["rotations"] < Fraction(math.nextafter(out["eps"], 1))
    assert out["eps"] != 0.5 * 1e-2 / out["rotations"]
    assert out["tPerRotation"] == engine.load().fullalg.sc.ts_per_rotation_eps(out["eps"])
    assert out["T"] == 1011277898534.9502


ACCEPTED = [
    {f: _approx(e) for f in FOUR} for e in ("1e-3", "25e-5", "1e-6")
] + [
    {f: _approx(e, "catalytic") for f in FOUR} for e in ("1e-3", "25e-5", "1e-6")
] + [
    {f: CATALYTIC_EXACT for f in FOUR}, {"data": _approx("1e-3"), "work": _approx("1e-3", "catalytic"), "scratch": CATALYTIC_EXACT},
    {"exponent": _approx("12e-4")}, {"scratch": _approx("49e-4", "catalytic")},
]


@pytest.mark.slow
@pytest.mark.parametrize("design", ACCEPTED)
def test_the_error_ledger_fits_delta_for_every_accepted_design(priced, design):
    """ERROR ACCOUNTING, re-derived here in exact arithmetic: each family's own bound (recomputed from the band or
    the rows) is within its allowance, and the bounds plus the gradient's preparation plus the synthesis actually
    spent (R x eps) fit the pool, which is half of delta."""
    policy, full = priced
    out = overlay.project(policy, full, design)
    b = out["budget"]
    pool, prep, rest = Fraction(b["pool"]), Fraction(b["prepBudget"]), Fraction(b["synthesisBudget"])
    bounds = Fraction(0)
    for family, record in out["families"].items():
        allowance = Fraction(record["allowance"])
        # Both constructions are bands; the merged additions are exact (b = k_max + 1), so they add nothing.
        assert record["construction"] in ("banded", "bandedMerged")
        bound = overlay.band_error(record["width"], record["count"], record["kMax"] + 1)
        if record["construction"] == "bandedMerged":
            assert record["b"] == record["kMax"] + 1 <= out["catalyst"]["b"]
        assert bound <= allowance and record["errorBound"] >= bound
        bounds += bound
    assert prep == (min(pool / 10, (pool - Fraction(b["allowances"])) / 2) if out["catalyst"] else 0)
    assert rest == pool - Fraction(b["allowances"]) - prep > 0
    assert bounds + prep + out["rotations"] * Fraction(out["eps"]) <= pool <= Fraction(1, 100)


@pytest.mark.parametrize("design,named", [
    ({"data": _approx("3e-3"), "work": _approx("2e-3")}, ["'data': '3e-3'", "'work': '2e-3'"]),
    ({"scratch": _approx("5e-3", "catalytic")}, ["'scratch': '5e-3'"]),
    ({"exponent": _approx("9e-1")}, ["'exponent': '9e-1'"]),
])
def test_allowances_that_leave_no_synthesis_budget_are_refused_by_name(design, named):
    """FAIL CLOSED: a design whose allowances use up the pool is refused naming exactly the families and values that
    were asked, before anything is priced. Dropping `check_budget` fails this."""
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.check_budget(overlay.parse_design(design))
    message = str(error.value)
    assert all(n in message for n in named) and "leaves no synthesis budget" in message


@pytest.mark.slow
def test_a_design_that_leaves_no_budget_is_refused_at_pricing_too(priced):
    policy, full = priced
    with pytest.raises(overlay.QftOverlayRefused, match="leaves no synthesis budget"):
        overlay.project(policy, full, {"data": _approx("3e-3"), "work": _approx("2e-3")})
    assert overlay.project(policy, full, {"data": _approx("3e-3"), "work": _approx("1e-3")})["identity"] is False


# --------------------------------------------------------------------------- banded merged catalysis


def test_a_merged_addition_is_the_engines_own_controlled_addition():
    """Each target's kept phases are ONE `CtrlAddIntoCatalyst(m + 1, m)`: m control Ands plus an (m + 1)-bit adder,
    2m Ands, 8m T and no rotation by the engine's own cost split. `merged_plan` sums those rows and must equal
    2 N_cp, N_cp = k (2w - k - 1) / 2 the kept phases; its workspace is the widest row's m + L - 1 = 2k."""
    _cs, cat = overlay._catalysis()
    fa = engine.load().fullalg
    for m in (1, 2, 5, 23, 38, 40):
        assert cat.ctrl_add_ands(m + 1, m, False) == 2 * m
        split = fa.cc.split(cat.CtrlAddIntoCatalyst(m + 1, m))
        assert (int(split.base), int(split.rot)) == (8 * m, 0)
    for w, k in ((2, 1), (5, 2), (5, 4), (2049, 38), (2347, 38), (4398, 40), (4095, 4094)):
        p = overlay.merged_plan(w, k)
        assert p["ands"] == sum(2 * min(k, w - 1 - j) for j in range(w)) == k * (2 * w - k - 1)
        assert p["workspace"] == 2 * k
    assert overlay.merged_plan(2049, 38)["ands"] * 4 == 616968 and overlay.merged_plan(4398, 40)["ands"] * 4 == 1400800


@pytest.mark.slow
def test_a_catalytic_family_is_banded_then_merged_not_one_kick_per_rotation(priced):
    """NO PER-ROTATION KICKS, NO ROUNDED ROWS. A catalytic family keeps the same band as its Ross-Selinger twin, then
    applies each target's kept phases as one exact addition: 8 N_cp T per call, no rotation. Reinstating per-rotation
    kicks or the rounded native rows fails the census and the pinned price, and catalysis must beat the banded twin."""
    policy, full = priced
    cat = overlay.project(policy, full, {f: _approx(backend="catalytic") for f in FOUR})
    rs = overlay.project(policy, full, {f: _approx() for f in FOUR})
    assert cat["T"] == CAT25_ALL_T and cat["catalyst"]["b"] == CAT25_ALL_B
    removed = sum(r["exactRotations"] * r["count"] for r in cat["families"].values())
    assert cat["rotations"] == full["rotations"] - removed
    for family, r in cat["families"].items():
        assert r["construction"] == "bandedMerged" and "bandedRotations" not in r
        assert r["kMax"] == rs["families"][family]["kMax"]                          # the same band
        assert r["b"] == r["kMax"] + 1 and r["banded"] is True
        assert r["mergedAnds"] == r["kMax"] * (2 * r["width"] - r["kMax"] - 1) and r["mergedTBase"] == 4 * r["mergedAnds"]
        assert r["workspaceQubits"] == 2 * r["kMax"] and r["errorBound"] == rs["families"][family]["errorBound"]
    assert cat["TBase"] == full["T_base"] + sum((r["mergedTBase"] - r["exactTBase"]) * r["count"]
                                                for r in cat["families"].values())
    assert cat["T"] < rs["T"] < full["T"]


@pytest.mark.slow
def test_the_catalyst_width_is_the_widest_band_plus_one(priced):
    """b = max over the catalytic families of k_max + 1: the narrowest register on which every merged addition is exact.
    A register one bit short would round the distance-k_max phase without charging it, which fails this."""
    policy, full = priced
    out = overlay.project(policy, full, {"data": _approx("25e-5", "catalytic"), "scratch": _approx("1e-6", "catalytic")})
    for family, r in out["families"].items():
        depth = overlay.band_depth(r["width"], r["count"], Fraction(r["allowance"]))
        assert r["kMax"] == depth - 1 and r["b"] == depth
    assert out["catalyst"]["b"] == max(r["kMax"] + 1 for r in out["families"].values()) == 49
    assert out["catalyst"]["bRule"] == overlay.B_RULE and out["catalyst"]["mergeRule"] == overlay.MERGE_RULE


@pytest.mark.slow
def test_catalytic_exact_is_an_unbanded_merged_qft_with_its_qubits_charged(priced):
    """CATALYTIC EXACT. No band (k_max = w - 1, error 0, no allowance), so the gradient is as wide as the register,
    and its b qubits -- plus the widest addition's 2 (w - 1) temporaries beyond the run's peak extra -- are charged."""
    policy, full = priced
    out = overlay.project(policy, full, {"work": CATALYTIC_EXACT})
    r = out["families"]["work"]
    assert r["kMax"] == r["width"] - 1 == 2346 and r["banded"] is False
    assert r["b"] == r["width"] == 2347 and r["errorBound"] == 0.0 and r["allowance"] == "0"
    assert r["mergedTBase"] == 4 * r["width"] * (r["width"] - 1)
    assert out["catalystQubits"] == 2347 and r["workspaceQubits"] == 2 * 2346 > full["peak_extra"]
    assert out["workspaceQubits"] == r["workspaceQubits"] - full["peak_extra"]
    assert out["qubitsPeak"] == full["qubits_peak"] + 2347 + out["workspaceQubits"] == out["qubits"]
    assert out["qubitsUpperBound"] == full["qubits"] + 2347 + out["workspaceQubits"]
    assert Fraction(out["budget"]["prepBudget"]) == Fraction(1, 2000)     # pool / 10: nothing else is claimed


def test_the_qubits_are_re_derived_site_by_site_on_the_live_qubit_timeline():
    """A merged addition holds 2 k_max temporaries, which can be MORE than the native template held at that QFT site.
    The design's qubits are therefore the timeline's new maximum less the run's peak extra: nothing while every site
    stays under the peak the other steps set, the excess when one does not. A Ross-Selinger band never adds (its ladder
    needs one And temporary). A timeline that does not reproduce the run's peak extra is refused, not guessed."""
    sites = {0: 394, "work": 66, "data": 78, 3: 393, 4: 3, "scratch": 91, 6: 391, 7: 393}
    assert overlay.site_excess(sites, 394, {"work": 76, "data": 76, "scratch": 80}) == 0      # work grows 66 -> 76, under 394
    assert overlay.site_excess(sites, 394, {"work": 400}) == 6                                # a site above the peak: charged
    assert overlay.site_excess(sites, 394, {"scratch": 2 * 4397}) == 2 * 4397 - 394
    assert overlay.site_excess(sites, 394, {"data": None, "work": None, "scratch": None}) == 0
    assert overlay.site_excess({**sites, "work": 0}, 394, {"work": None}) == 0
    with pytest.raises(overlay.QftOverlayRefused, match="live-qubit timeline's peak 394 is not the run's peak extra 412"):
        overlay.site_excess(sites, 412, {"work": 76})
    with pytest.raises(overlay.QftOverlayRefused, match="no site on the live-qubit timeline"):
        overlay.site_excess(sites, 394, {"exponent": 10})


@pytest.mark.slow
def test_the_timeline_reproduces_the_runs_peak_and_charges_only_an_excess(measured_policy):
    """On the measured fixture's policy the engine's timeline reproduces the coherent peak extra (with the exponent QFT)
    and the readout's (without it); the three catalysed sites at 25e-5 (2 k = 76, 76, 80) stay under it and add nothing,
    while an exact catalytic data family's 2 x 2048 temporaries exceed it by exactly that much."""
    policy, full = measured_policy
    fa = engine.load().fullalg
    coherent_sites = overlay._site_extras(fa, policy, full, measured=False)
    measured_sites = overlay._site_extras(fa, policy, full, measured=True)
    assert max(coherent_sites.values()) == full["peak_extra"] == 412 and "exponent" not in measured_sites
    assert {f: measured_sites[f] for f in ("work", "data", "scratch")} == {"work": 106, "data": 116, "scratch": 118}
    cat = overlay.project(policy, full, {f: _approx(backend="catalytic") for f in ("data", "work", "scratch")})
    assert cat["workspaceQubits"] == 0 and max(r["workspaceQubits"] for r in cat["families"].values()) == 80
    exact = overlay.project(policy, full, {"data": CATALYTIC_EXACT})
    assert exact["workspaceQubits"] == 2 * 2048 - 412 and exact["qubitsPeak"] == full["qubits_peak"] + 2049 + 2 * 2048 - 412


@pytest.mark.slow
def test_one_shared_register_is_charged_once(priced):
    policy, full = priced
    one = overlay.project(policy, full, {"scratch": _approx(backend="catalytic")})
    three = overlay.project(policy, full, {f: _approx(backend="catalytic") for f in ("data", "work", "scratch")})
    assert one["catalystQubits"] == three["catalystQubits"] == 41 and one["workspaceQubits"] == 0
    assert one["qubitsPeak"] == three["qubitsPeak"] == BASELINE_QUBITS_PEAK + 41
    assert three["catalyst"]["families"] == ["data", "scratch", "work"]
    assert three["qubitTimesT"] == three["qubitsPeak"] * three["T"]


@pytest.mark.slow
def test_the_catalyst_preparation_is_charged_against_its_own_carved_budget(priced):
    policy, full = priced
    cs, _cat = overlay._catalysis()
    for design in ({f: CATALYTIC_EXACT for f in FOUR}, {"data": _approx("25e-5", "catalytic")}):
        out = overlay.project(policy, full, design)
        cat = out["catalyst"]
        assert cat["prepT"] > 0 and cat["T"] == cat["prepT"] == cs.catalyst_prep_T(cat["b"], float(Fraction(cat["prepBudget"])))
        assert cat["prepRotations"] == cat["b"] - 3
        assert out["T"] == out["TBase"] + out["rotations"] * out["tPerRotation"] + cat["T"]


@pytest.mark.slow
def test_the_four_axis_combinations_all_price_and_all_differ(priced):
    policy, full = priced
    corners = {"rs exact": {"work": EXACT}, "rs approx": {"work": _approx()},
               "cat exact": {"work": CATALYTIC_EXACT}, "cat approx": {"work": _approx(backend="catalytic")}}
    prices = {k: overlay.project(policy, full, d)["T"] for k, d in corners.items()}
    assert prices["rs exact"] == full["T"] and len(set(prices.values())) == 4, prices


@pytest.mark.slow
@pytest.mark.parametrize("case", sorted(RESEARCH))
def test_the_design_prices_match_the_independent_pricer(measured_policy, case):
    """PARITY on the measured fixture's policy (coherent readout), Ross-Selinger families. Rotations equal the
    independent pricer's; T differs by exactly one construction constant: (w - 1) x calls per banded family (this band
    charges an And, 4 T, on each distance-1 controlled phase where that pricer charges 3 T)."""
    policy, full = measured_policy
    design, research_t, research_b, research_prep = RESEARCH[case]
    out = overlay.project(policy, full, design)
    construction = sum((r["width"] - 1) * r["count"] for r in out["families"].values() if r["construction"] == "banded")
    prep = out["catalyst"]["prepT"] if out["catalyst"] else 0.0
    assert (out["catalyst"]["b"] if out["catalyst"] else 0) == research_b
    assert out["T"] - research_t - construction - (prep - research_prep) == pytest.approx(0.0, abs=1e-3)


# --------------------------------------------------------------------------- monotonicity


@pytest.mark.slow
def test_a_looser_allowance_gives_a_shallower_band(priced):
    policy, full = priced
    prices, bands = [], []
    for max_error in ("1e-12", "1e-9", "1e-6", "1e-3"):
        out = overlay.project(policy, full, {"scratch": _approx(max_error)})
        prices.append(out["T"])
        bands.append(out["families"]["scratch"]["kMax"])
    assert bands == sorted(bands, reverse=True) and len(set(bands)) == 4
    assert prices == sorted(prices, reverse=True)


@pytest.mark.slow
def test_the_like_for_like_crosscheck_uses_the_engines_own_float_association(priced):
    policy, full = priced
    for family in FOUR:
        overlay.project(policy, full, {family: _approx("1e-6")})


@pytest.mark.slow
def test_the_family_widths_are_the_engines_own_qft_sites(priced):
    policy, full = priced
    sites = {(p["xw"], p["count"]) for p in full["parts"] if p["site"] == "qft"}
    seen = {(r["width"], r["count"]) for f in FOUR
            for r in [overlay.project(policy, full, {f: _approx("1e-6")})["families"][f]]}
    assert seen == sites


# --------------------------------------------------------------------------- parsing and fail closed


@pytest.mark.parametrize("value,expected", [("1e-6", 1e-6), ("25e-9", 25e-9), ("5e-1", 0.5)])
def test_max_error_parses_the_compiled_string_form(value, expected):
    assert overlay.parse_max_error("exponent", value) == pytest.approx(expected, rel=1e-15)
    assert overlay.allowance_of("exponent", value) == Fraction(value.replace("e-", "e-"))


@pytest.mark.parametrize("value", ["1.5e-6", "1E-6", "1e+6", "-1e-6", " 1e-6", "1e-6 ", "", "1e6",
                                   "0e-0", "1e-0", "abc", "1e-", "e-6"])
def test_an_unparseable_or_out_of_range_max_error_is_refused_by_value(value):
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_max_error("scratch", value)
    assert "scratch" in str(error.value) and repr(value) in str(error.value)


@pytest.mark.parametrize("value", [1e-6, 0.000001, None, 6, ("1", "6")])
def test_a_non_string_max_error_is_refused(value):
    with pytest.raises(overlay.QftOverlayRefused, match="not the compiled form"):
        overlay.parse_max_error("work", value)


def test_an_unrecognised_backend_is_refused_and_names_the_family_and_the_value():
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"work": {"backend": "gridsynth", "target": "exact"}})
    assert "work" in str(error.value) and "gridsynth" in str(error.value)
    assert "reported, not priced at the default" in str(error.value)


def test_the_catalytic_backend_parses_on_both_targets():
    assert overlay.parse_design({"exponent": _approx("1e-6", backend="catalytic")}) == \
        {"exponent": ("catalytic", "approximate", "1e-6")}
    assert overlay.parse_design({f: CATALYTIC_EXACT for f in FOUR}) == {f: ("catalytic", "exact", None) for f in FOUR}


def test_an_unrecognised_target_is_refused():
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"data": {"backend": "ross_selinger", "target": "banded"}})
    assert "data" in str(error.value) and "banded" in str(error.value)


def test_an_approximate_target_without_a_max_error_is_not_priced_as_exact():
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"scratch": {"backend": "ross_selinger", "target": "approximate"}})
    assert "scratch" in str(error.value) and "refused, not priced as exact" in str(error.value)


def test_an_exact_target_carrying_a_max_error_is_refused():
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"work": {"backend": "ross_selinger", "target": "exact", "max_error": "1e-6"}})
    assert "work" in str(error.value) and "1e-6" in str(error.value)


@pytest.mark.parametrize("field", list(overlay.OUT_OF_SCOPE))
def test_the_out_of_scope_selector_fields_are_refused_by_name(field):
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"exponent": EXACT, field: "whatever"})
    assert f"names the phase-product step {field!r}, which is refused" in str(error.value)
    assert "prices the QFT families" in str(error.value) and f"re-submit without {field!r}" in str(error.value)


def test_an_unknown_family_key_is_refused_rather_than_ignored():
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"modmul": EXACT})
    assert "modmul" in str(error.value)


@pytest.mark.parametrize("design", ["exact", 7, [("data", EXACT)]])
def test_a_non_mapping_design_is_refused(design):
    with pytest.raises(overlay.QftOverlayRefused, match="must be a mapping"):
        overlay.parse_design(design)


@pytest.mark.parametrize("value", ["exact", 7, ["backend"]])
def test_a_non_mapping_family_value_is_refused(value):
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"work": value})
    assert "work" in str(error.value)


def test_an_unexpected_key_in_a_family_value_is_refused():
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"data": {**EXACT, "k_max": 19}})
    assert "data" in str(error.value) and "k_max" in str(error.value)


def test_absent_and_none_families_mean_unchanged_not_an_error():
    assert overlay.parse_design(None) == {} == overlay.parse_design({}) == overlay.parse_design({f: None for f in FOUR})


def test_the_budget_check_uses_the_engines_synthesis_fraction():
    assert engine.load().fullalg.sc.ROTATION_FRACTION == 0.5        # `check_budget`'s pool is half of delta


# --------------------------------------------------------------------------- the safety properties


def test_the_overlay_fingerprints_its_own_bytes():
    from hashlib import sha256
    assert overlay.rule_fingerprint() == sha256(Path(overlay.__file__).read_bytes()).hexdigest()


@pytest.mark.slow
def test_the_receipt_carries_the_rule_identity(priced):
    policy, full = priced
    out = overlay.project(policy, full, {"exponent": _approx("1e-6")})
    assert out["pricingRule"] == overlay.RULE == "qft-design-axis-v4"
    assert out["ruleSha256"] == overlay.rule_fingerprint() and out["epoch"] == overlay.EPOCH
    assert out["families"]["exponent"]["realization"].startswith("ReferenceQFT(w, k_max)")
    assert "kMaxSource" not in out


@pytest.mark.slow
def test_the_epoch_guard_refuses_any_other_engine_model(priced, monkeypatch):
    policy, full = priced
    monkeypatch.setattr(overlay, "epoch_of", lambda model: "0000000000000000")
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.project(policy, full, None)
    assert overlay.EPOCH in str(error.value) and "0000000000000000" in str(error.value)


@pytest.mark.slow
def test_a_coarse_price_without_the_per_part_census_is_refused(priced):
    policy, full = priced
    coarse = {k: v for k, v in full.items() if k != "parts"}
    with pytest.raises(overlay.QftOverlayRefused, match="detail=True"):
        overlay.project(policy, coarse, {"exponent": _approx("1e-6")})


@pytest.mark.slow
def test_a_price_that_is_not_the_current_exact_reference_is_refused(priced):
    policy, full = priced
    with pytest.raises(overlay.QftOverlayRefused, match="exact reference price"):
        overlay.project(policy, {**full, "width_rule": "scan"}, None)
    with pytest.raises(overlay.QftOverlayRefused, match="exact reference price"):
        overlay.project(policy, {**full, "delta": 0.123}, None)
