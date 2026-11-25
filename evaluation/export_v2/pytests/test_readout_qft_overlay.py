"""The composed measured-readout + QFT-design rule: both reductions bit for bit, an independent recomputation of a
combined case, the monotonicity that does and does not hold, and the refusals.

Every test names the production change in `export_v2/readout_qft_overlay.py` that would fail it. The policy is the
recorded compiled semiclassical fixture `readout_submitted_931bbf1` -- the policy a measured submission really
carries -- priced by the frozen engine at n = 2048, delta = 1e-2 (`mapping.DESIGN_DELTA`).
"""
from fractions import Fraction
import gzip
from hashlib import sha256
import json
import math
from pathlib import Path

import pytest

from export_v2 import engine, qft_overlay, readout_overlay, readout_qft_overlay as composed
from intake_v2 import policy_from_document

DATA = Path(__file__).parent / "data"
EXACT = {"backend": "ross_selinger", "target": "exact"}
FAMILIES = ("data", "work", "scratch")      # the families a measured run can design; `exponent` is refused


def approx(max_error="25e-5", backend="ross_selinger"):
    return {"backend": backend, "target": "approximate", "max_error": max_error}


@pytest.fixture(scope="module")
def priced():
    eng = engine.load()
    compiled = json.loads(gzip.decompress((DATA / "readout_submitted_931bbf1.json.gz").read_bytes()))
    policy = policy_from_document(eng, compiled["document"]).with_widths("exact")
    return policy, eng.fullalg.price_full(policy, 2048, 1e-2, detail=True)


# --------------------------------------------------------------------------- the pins this rule must not move


def test_the_composed_rule_leaves_both_overlays_byte_for_byte_alone():
    """The readout rule is a cross-repository byte pin and the QFT rule's fingerprint is on every design-only panel.
    Production change that would fail this: folding the composition into `readout_overlay.py` (rotating the
    published `ruleSha256`) instead of importing it."""
    from test_operational_pin import READOUT_RULE_SHA256

    assert readout_overlay.rule_fingerprint() == READOUT_RULE_SHA256 \
        == "892db5df3fba435e93509499862f804dc95d65faaa21aace116775f735f70903"
    assert composed.rule_fingerprint() == sha256(Path(composed.__file__).read_bytes()).hexdigest()
    assert composed.rule_fingerprint() not in (READOUT_RULE_SHA256, qft_overlay.rule_fingerprint())
    assert composed.component_rules() == {"readout": READOUT_RULE_SHA256, "qft": qft_overlay.rule_fingerprint()}


@pytest.mark.parametrize("value", [approx(), EXACT, approx(backend="catalytic"),
                                   {"backend": "catalytic", "target": "exact"}, None])
def test_the_exponent_family_is_refused_by_name_under_a_measured_readout(value):
    """The readout removes the exponent IQFT, so there is nothing left to band or catalyse, and `exact` names a site
    that is not in the measured circuit, so it is refused too rather than dropped. Positive control: the
    same family is accepted with the readout off, so the refusal is the readout's and not the overlay's.
    Production change that would fail this: applying an exponent band to the feed-forward phases, or dropping the key."""
    with pytest.raises(composed.ReadoutQftRefused, match="'exponent'.*semiclassical readout"):
        composed.check_design_under_readout({"data": approx(), "exponent": value})
    assert qft_overlay.parse_design({"data": approx(), "exponent": value}) is not None
    assert set(composed.check_design_under_readout({"data": approx(), "work": EXACT})) == {"data", "work"}


@pytest.mark.parametrize("bad,reason", [({"step2": {}}, "phase-product step 'step2'"),
                                        ({"modmul": "reference"}, "cannot honour"),
                                        ({"data": approx("1.5e-6")}, "compiled form"),
                                        ({"data": {"backend": "fallback", "target": "exact"}}, "unrecognised backend")])
def test_the_value_rules_stay_the_qft_overlays_own(bad, reason):
    """One validator in the repo: every value refusal the QFT overlay makes, the composed rule makes too, as a
    `ValueError`. Production change that would fail this: a second, looser parser in the composed rule."""
    with pytest.raises(ValueError, match=reason):
        composed.check_design_under_readout(bad)


# --------------------------------------------------------------------------- (a) and (b): the two reductions


@pytest.mark.slow
@pytest.mark.parametrize("design", [None, {}, {f: EXACT for f in FAMILIES}, {f: None for f in FAMILIES}])
def test_a_with_no_design_the_composed_price_is_the_readouts_bit_for_bit(priced, design):
    """(a) design=None (and every design asking nothing) reduces EXACTLY to `readout_overlay.project`.
    Production change that would fail this: re-deriving the readout patch instead of taking the pinned rule's,
    carving a budget when nothing is designed, or dividing the pool over any total but the readout's own."""
    policy, full = priced
    alone = readout_overlay.project(policy, full)
    joint = composed.project(policy, full, design)
    for key in ("T", "TBase", "rotations", "tPerRotation", "eps", "rounds", "iqftTBase", "iqftRotations",
                "peakExtra", "qubits"):
        assert joint[key] == alone[key], key
    assert alone["T"] == 3633127264685.6953
    assert joint["identity"] and joint["catalyst"] is None and joint["budget"] is None
    assert (joint["readoutTBase"], joint["readoutRotations"]) == (alone["TBase"], alone["rotations"])


@pytest.mark.slow
@pytest.mark.parametrize("design", [None, {"data": approx()}, {f: approx() for f in FAMILIES},
                                    {"exponent": approx("1e-6")},
                                    {"data": approx(backend="catalytic"), "work": approx(backend="catalytic")},
                                    {"data": {"backend": "catalytic", "target": "exact"}}])
def test_b_with_the_readout_off_the_joint_rule_is_the_qft_overlays_bit_for_bit(priced, design):
    """(b) readout off reduces EXACTLY to `qft_overlay.project`, catalysis included. Production change that would
    fail this: the composed rule pricing the design with anything but `qft_overlay.price_design`."""
    policy, full = priced
    alone = qft_overlay.project(policy, full, design)
    joint = composed._joint(policy, full, design, readout=False)
    for key in ("T", "TBase", "rotations", "tPerRotation", "eps", "families", "substituted", "catalysed", "catalyst",
                "catalystQubits", "workspaceQubits", "budget", "identity"):
        assert joint[key] == alone[key], key


# --------------------------------------------------------------------------- (c) combined cases, by hand


def _part(full, width):
    rows = [p for p in full["parts"] if p["site"] == "qft" and p["xw"] == width and p["zw"] == 0]
    assert len(rows) == 1
    row = rows[0]
    # The part's rotation-free T, read off the frozen row itself: T = base + rotations * tPerRotation.
    return row["count"], row["rotations"], round(row["T"] - row["rotations"] * full["T_per_rotation"])


def _readout_census(full):
    xw = full["widths"]["xW"]
    one, iqft_rot, iqft_base = _part(full, xw)
    assert one == 1
    return full["T_base"] - iqft_base, full["rotations"] - iqft_rot + (xw - 1)


def _omitted(w, depth):
    return sum(Fraction(w - s + 1, 2 ** s) for s in range(depth + 1, w + 1))


def _rs(eps):
    return max(1.0, 3.075 * math.log2(1 / eps) + 0.86)


@pytest.mark.slow
def test_c_a_banded_data_and_work_design_under_the_readout_is_recomputed_from_the_census_by_hand(priced):
    """(c) The joint rule recomputed independently: the IQFT and the two families' exact parts are read off the frozen
    coherent rows, the band depth is found here by a linear scan over the family-total bound (each family runs 8190
    times), the band is counted by its closed form, the two allowances come out of the pool, and the rate is typed
    out here. Neither overlay's pricing is called. Production change that would fail this: a per-call band, a band
    counted by the engine's split, or a pool that keeps the allowances."""
    policy, full = priced
    w = full["widths"]
    base, rotations = _readout_census(full)
    pi_up = Fraction("3.14159265358979323846264338328")
    allowance = Fraction(25, 10 ** 5)
    for width in (w["yW"] + 1, w["workW"]):
        count, rot_row, base_row = _part(full, width)
        depth = next(d for d in range(2, width + 1) if 2 * pi_up * count * _omitted(width, d) <= allowance)
        k = depth - 1
        assert k == 38
        band_rot = sum(width - d for d in range(3, k + 1))
        band_base = 4 * sum(width - d for d in range(1, k + 1)) + (width - 2)
        base += band_base * count - base_row
        rotations += band_rot * count - rot_row
    rest = Fraction(1, 200) - 2 * allowance
    eps = float(rest / rotations)
    by_hand = base + rotations * _rs(eps)

    joint = composed.project(policy, full, {"data": approx(), "work": approx()})
    assert (joint["TBase"], joint["rotations"]) == (base, rotations)
    assert joint["eps"] == pytest.approx(eps, rel=1e-15) and Fraction(joint["eps"]) <= rest / rotations
    assert joint["T"] == pytest.approx(by_hand, rel=1e-13)
    assert joint["qubits"] == readout_overlay.project(policy, full)["qubits"]   # banding adds no qubit

    # It is a genuinely joint answer: neither overlay alone, nor the two adjustments added.
    ro, qd = readout_overlay.project(policy, full), qft_overlay.project(policy, full, {"data": approx(), "work": approx()})
    stacked = ro["T"] + (qd["T"] - full["T"])
    assert joint["T"] < ro["T"] and joint["T"] < qd["T"] and abs(joint["T"] - stacked) > 1e3   # not float noise


@pytest.mark.slow
def test_c_all_three_families_catalysed_under_the_readout_by_hand(priced):
    """(c, catalytic) The headline design, recomputed here: each family is banded by a linear scan over its
    family-total bound (as for the Ross-Selinger twin), then every target's kept phases are one controlled addition of
    2 min(k, w - 1 - j) Ands (summed here target by target), with no rotation; the gradient is the widest band plus
    one, its preparation takes min(pool / 10, (pool - allowances) / 2) and the rest of the pool is divided over the
    joint total, feed-forward phases included. Neither overlay's pricing is called. Production change that would
    fail this: per-rotation kicks, rounded rows on a narrower gradient, a per-call band, or a pool that keeps the
    allowances."""
    policy, full = priced
    eng = engine.load()
    from l2q_full.catalysis import spec as cs
    w = full["widths"]
    base, rotations = _readout_census(full)
    pi_up = Fraction("3.14159265358979323846264338328")
    allowance = Fraction(25, 10 ** 5)
    widths = {"data": w["yW"] + 1, "work": w["workW"], "scratch": w["scratchW"]}
    bands = {}
    for family, width in widths.items():
        count, rot_row, base_row = _part(full, width)
        depth = next(d for d in range(2, width + 1) if 2 * pi_up * count * _omitted(width, d) <= allowance)
        k = bands[family] = depth - 1
        ands = sum(2 * min(k, width - 1 - j) for j in range(width))
        base += 4 * ands * count - base_row
        rotations -= rot_row
    assert bands == {"data": 38, "work": 38, "scratch": 40}
    b = max(bands.values()) + 1
    pool, claimed = Fraction(1, 200), 3 * allowance
    prep = min(pool / 10, (pool - claimed) / 2)
    rest = pool - claimed - prep
    prep_t = (b - 3) * cs.ross_selinger_t(float(prep) / (b - 3)) + 1.0
    joint = composed.project(policy, full, {f: approx(backend="catalytic") for f in FAMILIES})
    assert (joint["TBase"], joint["rotations"]) == (base, rotations)
    assert joint["catalyst"]["b"] == b == 41 and joint["catalyst"]["prepT"] == pytest.approx(prep_t, rel=1e-13)
    assert joint["T"] == pytest.approx(base + rotations * _rs(float(rest / rotations)) + prep_t, rel=1e-13)
    assert joint["T"] == 1020219052649.4625
    assert joint["qubits"] == readout_overlay.project(policy, full)["qubits"] + 41 == 9249
    assert joint["workspaceQubits"] == 0 and max(r["workspaceQubits"] for r in joint["families"].values()) == 80
    assert joint["rotations"] >= joint["feedForwardRotations"] == w["xW"] - 1
    assert eng.fullalg.sc.ts_per_rotation_eps(joint["eps"]) == joint["tPerRotation"]


#: The independent pricer (`policy_design.Evaluator.evaluate`, semiclassical readout, reference topology) on this
#: fixture's policy: T, gradient width, preparation T, Ross-Selinger families (that pricer's catalytic arm is a
#: different construction). See `test_qft_overlay.py` for the construction constant.
RESEARCH = {
    "all3 rs 25e-5": ({f: approx() for f in FAMILIES}, 1518242573193.455, 0, 0),
    "all3 rs 1e-6": ({f: approx("1e-6") for f in FAMILIES}, 1627067455755.2039, 0, 0),
    "data rs 1e-3": ({"data": approx("1e-3")}, 3397129077588.8223, 0, 0),
}


@pytest.mark.slow
@pytest.mark.parametrize("case", sorted(RESEARCH))
def test_c_the_composed_prices_match_the_independent_pricer(priced, case):
    policy, full = priced
    design, research_t, research_b, research_prep = RESEARCH[case]
    out = composed.project(policy, full, design)
    construction = sum((r["width"] - 1) * r["count"] for r in out["families"].values() if r["construction"] == "banded")
    prep = out["catalyst"]["prepT"] if out["catalyst"] else 0.0
    assert (out["catalyst"]["b"] if out["catalyst"] else 0) == research_b
    assert out["T"] - research_t - construction - (prep - research_prep) == pytest.approx(0.0, abs=1e-3)


# --------------------------------------------------------------------------- (d) monotonicity, and its limits


LOOSENING = ("1e-45", "1e-30", "1e-12", "1e-6", "25e-5")


@pytest.mark.slow
@pytest.mark.parametrize("family", FAMILIES)
def test_d_a_looser_allowance_never_costs_more_up_to_25e_5_and_beats_exact(priced, family):
    """(d) Under the same readout, loosening a family's allowance up to 25e-5 never raises T, and there the
    approximate family costs less than leaving it exact. Production change that would fail this: a band that grows
    with the allowance."""
    policy, full = priced
    exact = composed.project(policy, full, {family: EXACT})["T"]
    assert exact == readout_overlay.project(policy, full)["T"]
    prices = [composed.project(policy, full, {family: approx(m)})["T"] for m in LOOSENING]
    assert prices == sorted(prices, reverse=True) and prices[-1] < exact


@pytest.mark.slow
def test_d_the_limits_of_monotonicity_are_pinned(priced):
    """The limits of (d), pinned so nobody claims more. (1) An allowance is carved from the pool, so a loose one
    raises every other rotation's rate: banding data at 1e-3 costs MORE than at 25e-5. (2) `approximate` on the
    Ross-Selinger backend REPLACES the native template with a textbook ladder, so at 1e-60 it costs more than exact."""
    policy, full = priced
    p25 = composed.project(policy, full, {"data": approx("25e-5")})["T"]
    p3 = composed.project(policy, full, {"data": approx("1e-3")})["T"]
    assert p3 > p25
    exact = composed.project(policy, full, {"data": EXACT})["T"]
    tight = composed.project(policy, full, {"data": approx("1e-60")})
    assert tight["families"]["data"]["kMax"] > 200 and tight["T"] > exact


@pytest.mark.slow
def test_allowances_that_exhaust_the_pool_are_refused_by_name_under_the_readout(priced):
    policy, full = priced
    with pytest.raises(ValueError, match="'data': '4e-3'.*'work': '1e-3'.*leaves no synthesis budget"):
        composed.check_design_under_readout({"data": approx("4e-3"), "work": approx("1e-3")})
    with pytest.raises(qft_overlay.QftOverlayRefused, match="leaves no synthesis budget"):
        composed.project(policy, full, {"data": approx("4e-3"), "work": approx("1e-3")})
    assert composed.project(policy, full, {"data": approx("3e-3"), "work": approx("1e-3")})["budget"] is not None


# --------------------------------------------------------------------------- the width collision


@pytest.mark.slow
def test_a_family_at_the_measured_registers_width_is_refused(priced, monkeypatch):
    """A family whose QFT part IS the exponent IQFT the readout removed is refused, not substituted twice.
    Positive control first: the unpatched family prices. Production change that would fail this: dropping the
    `w != rounds` guard, which would band a part that is not in the measured circuit."""
    policy, full = priced
    assert composed.project(policy, full, {"data": approx()})["substituted"] == ["data"]
    monkeypatch.setitem(qft_overlay.FAMILY_WIDTH, "data", lambda w: w["xW"])
    with pytest.raises(composed.ReadoutQftRefused, match="measured register"):
        composed.project(policy, full, {"data": approx()})
