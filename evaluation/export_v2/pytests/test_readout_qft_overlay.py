"""The composed measured-readout + QFT-design rule: both reductions bit for bit, an independent recomputation of a
combined case, the monotonicity that does and does not hold, and the refusals.

Every test names the production change in `export_v2/readout_qft_overlay.py` that would fail it. The policy is the
recorded compiled semiclassical fixture `readout_submitted_931bbf1` -- the policy a measured submission really
carries -- priced by the frozen engine at n = 2048, delta = 1e-2 (`mapping.DESIGN_DELTA`).
"""
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
        == "b3c2f317cf68bc47649a634862540177f5fc135d1e6fa543ce9fe4cb1161cd96"
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
    or dividing the pool over any total but the readout's own."""
    policy, full = priced
    alone = readout_overlay.project(policy, full)
    joint = composed.project(policy, full, design)
    for key in ("T", "TBase", "rotations", "tPerRotation", "eps", "rounds", "iqftTBase", "iqftRotations",
                "peakExtra", "qubits"):
        assert joint[key] == alone[key], key
    assert joint["identity"] and joint["catalyst"] is None and joint["rotationsCatalysed"] == 0
    assert (joint["readoutTBase"], joint["readoutRotations"]) == (alone["TBase"], alone["rotations"])


@pytest.mark.slow
@pytest.mark.parametrize("design", [None, {"data": approx()}, {f: approx() for f in FAMILIES},
                                    {"exponent": approx("1e-6")},
                                    {"data": approx(backend="catalytic"), "work": approx(backend="catalytic")},
                                    {"data": {"backend": "catalytic", "target": "exact"}}])
def test_b_with_the_readout_off_the_joint_rule_is_the_qft_overlays_bit_for_bit(priced, design):
    """(b) readout off reduces EXACTLY to `qft_overlay.project`, catalytic arm included. Production change that would
    fail this: the composed rule's census loop drifting from the overlay's (a different association, a different
    catalytic share, carving a QFT ceiling out of the pool)."""
    policy, full = priced
    alone = qft_overlay.project(policy, full, design)
    joint = composed._joint(policy, full, design, readout=False)
    for key in ("T", "TBase", "rotations", "rotationsSynthesised", "rotationsCatalysed", "tPerRotation", "eps",
                "families", "substituted", "catalysed", "catalyst", "catalystQubits"):
        assert joint[key] == alone[key], key


# --------------------------------------------------------------------------- (c) a combined case, by hand


def _part(full, width):
    rows = [p for p in full["parts"] if p["site"] == "qft" and p["xw"] == width and p["zw"] == 0]
    assert len(rows) == 1
    row = rows[0]
    # The part's rotation-free T, read off the frozen row itself: T = base + rotations * tPerRotation.
    return row["count"], row["rotations"], round(row["T"] - row["rotations"] * full["T_per_rotation"])


def _rate(rotations):
    eps = 0.5 * 1e-2 / rotations
    return eps, max(1.0, 3.075 * math.log2(1 / eps) + 0.86)


@pytest.mark.slow
def test_c_a_banded_data_and_work_design_under_the_readout_is_recomputed_from_the_census_by_hand(priced):
    """(c) The joint rule recomputed independently: the IQFT and the two families' exact parts are read off the frozen
    coherent rows (not `PolicyQFT`), the band is the engine's `ReferenceQFT` at a hand-computed Coppersmith k, and the
    rate is the closed form typed out here (not `sto_currency`). Neither overlay module is called.
    Production change that would fail this: any joint census or division other than the one the docstring states."""
    policy, full = priced
    from l2q_full.bloqs.qft import ReferenceQFT
    fa = engine.load().fullalg
    w = full["widths"]
    xw = w["xW"]
    one, iqft_rot, iqft_base = _part(full, xw)
    assert one == 1
    base = full["T_base"] - iqft_base
    rotations = full["rotations"] - iqft_rot + (xw - 1)
    for width in (w["yW"] + 1, w["workW"]):
        count, rot_row, base_row = _part(full, width)
        k = max(1, math.ceil(math.log2(width / 25e-5)))
        band = fa.cc.split(ReferenceQFT(width, k_max=k))
        base += band.base * count - base_row
        rotations += band.rot * count - rot_row
    eps, rate = _rate(rotations)
    by_hand = base + rotations * rate

    joint = composed.project(policy, full, {"data": approx(), "work": approx()})
    assert (joint["TBase"], joint["rotations"]) == (base, rotations)
    assert joint["eps"] == pytest.approx(eps, rel=1e-15)
    assert joint["T"] == pytest.approx(by_hand, rel=1e-13)
    assert joint["qubits"] == readout_overlay.project(policy, full)["qubits"]   # banding adds no qubit

    # It is a genuinely joint answer: neither overlay alone, nor the two adjustments added.
    ro, qd = readout_overlay.project(policy, full), qft_overlay.project(policy, full, {"data": approx(), "work": approx()})
    stacked = ro["T"] + (qd["T"] - full["T"])
    assert joint["T"] < ro["T"] and joint["T"] < qd["T"] and joint["T"] != stacked
    assert abs(joint["T"] - stacked) > 1e4        # the nonlinearity of the rate, not float noise


@pytest.mark.slow
def test_c_the_catalytic_arm_takes_its_share_of_the_joint_total_by_hand(priced):
    """(c, catalytic) The arm's share is `pool * R_cat / R_joint`, with the feed-forward rotations IN R_joint and
    paid Ross-Selinger; b, the kicks and the register are recomputed here from that share. Production change that
    would fail this: partitioning the pool over the coherent total, or catalysing the feed-forward phases."""
    policy, full = priced
    joint = composed.project(policy, full, {"data": approx(backend="catalytic")})
    arm = joint["catalyst"]
    r_joint, r_cat = joint["rotations"], joint["rotationsCatalysed"]
    assert r_cat == joint["families"]["data"]["bandedRotations"] * joint["families"]["data"]["count"]
    delta_arm = 0.5 * 1e-2 * r_cat / r_joint
    cells = delta_arm - 1e-3 * delta_arm
    b = max(4, math.ceil(math.log2(math.pi * r_cat / cells)))
    assert arm["deltaArm"] == pytest.approx(delta_arm, rel=1e-15) and arm["b"] == b
    assert arm["cellsT"] == r_cat * 4 * (b - 1)
    _, rate = _rate(r_joint)
    assert joint["T"] == pytest.approx(joint["TBase"] + (r_joint - r_cat) * rate + arm["T"], rel=1e-13)
    assert joint["qubits"] == readout_overlay.project(policy, full)["qubits"] + b
    assert joint["rotationsSynthesised"] == r_joint - r_cat >= joint["feedForwardRotations"] == full["widths"]["xW"] - 1


# --------------------------------------------------------------------------- (d) monotonicity, and its limit


LOOSENING = ("1e-45", "1e-30", "1e-12", "1e-6", "25e-5")


@pytest.mark.slow
@pytest.mark.parametrize("family", FAMILIES)
def test_d_a_looser_ceiling_never_costs_more_and_the_qualified_ceiling_beats_exact(priced, family):
    """(d) Under the same readout, loosening a family's ceiling never raises T, and at the qualified ceiling 25e-5 the
    approximate family costs no more than leaving it exact (the readout's own price). Production change that would
    fail this: a band that grows with the ceiling, or a joint division that penalises substitution."""
    policy, full = priced
    exact = composed.project(policy, full, {family: EXACT})["T"]
    assert exact == readout_overlay.project(policy, full)["T"]
    prices = [composed.project(policy, full, {family: approx(m)})["T"] for m in LOOSENING]
    assert prices == sorted(prices, reverse=True)
    assert prices[-1] <= exact


@pytest.mark.slow
def test_d_approximate_is_a_construction_swap_so_a_tight_enough_ceiling_costs_more_than_exact(priced):
    """The limit of (d), pinned so nobody claims more. `approximate` REPLACES the native `PolicyQFT` with a banded
    textbook `ReferenceQFT` (`qft_overlay`'s own docstring), so "approximate <= exact" is not a law: at 1e-60 the
    data band keeps k = 211 and costs MORE than the untouched family. This is inherited from `qft_overlay` (the same
    holds with the readout off), not introduced by the composition. Fails if that ever stops being true -- at which
    point the module docstrings and the report should say approximate is always cheaper."""
    policy, full = priced
    exact = composed.project(policy, full, {"data": EXACT})["T"]
    tight = composed.project(policy, full, {"data": approx("1e-60")})
    assert tight["families"]["data"]["kMax"] == 211 and tight["T"] > exact
    assert qft_overlay.project(policy, full, {"data": approx("1e-60")})["T"] > full["T"]


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
