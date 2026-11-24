"""The Coppersmith-banded QFT overlay: bit-identical defaults, a real band, and fail-closed refusals.

Every test names the production change in `export_v2/qft_overlay.py` that would fail it.

The baseline is HEAD 792464f's own price for the STO reference PhaseProduct policy at n = 2048,
delta = 1e-2, captured from `fullalg.price_full(P, 2048, 1e-2, detail=True)` BEFORE any of this lane's
work and unchanged by it (the overlay does not touch `evaluation/engine`, so the engine is still
byte-identical and these numbers are still the engine's own).
"""
import math

import pytest

from export_v2 import engine, epoch, qft_overlay as overlay
from export_v2.canon import epoch_of

#: `fullalg.price_full(sto_reference_policies()[PAIR], 2048, 1e-2, detail=True)` at 792464f.
BASELINE_T = 1938733902110.921
BASELINE_T_BASE = 1298454874920
BASELINE_ROTATIONS = 5180402331

EXACT = {"backend": "ross_selinger", "target": "exact"}


def _approx(max_error="1e-6", backend="ross_selinger"):
    return {"backend": backend, "target": "approximate", "max_error": max_error}


@pytest.fixture(scope="module")
def priced():
    eng = engine.load()
    policy = eng.library.sto_reference_policies()[eng.tables.PAIR]
    return policy, eng.fullalg.price_full(policy, 2048, 1e-2, detail=True)


# --------------------------------------------------------------------------- the engine is untouched


def test_the_overlay_does_not_move_the_epoch():
    """THE HEADLINE. `qft_overlay.py` lives in `export_v2`, which is not an epoch input: neither
    `engineRevision` (`epoch.py:91`, git log over `evaluation/engine`) nor `dataFingerprint`
    (`full_frontier.py:43-50`, a byte hash of the engine's pricer sources) can see it. Moving any part
    of this overlay into `evaluation/engine` would fail this test."""
    assert epoch_of(epoch.model_of()) == overlay.EPOCH == "ab1f8887b1b50e56"


@pytest.mark.slow
def test_the_frozen_baseline_is_still_the_engines_own_price(priced):
    """Guards the baseline the identity tests are stated against: if the engine ever moves, these pins
    go red here rather than silently re-basing the identity proof."""
    _, full = priced
    assert (full["T"], full["T_base"], full["rotations"]) == (BASELINE_T, BASELINE_T_BASE, BASELINE_ROTATIONS)


# --------------------------------------------------------------------------- bit-identical defaults


@pytest.mark.slow
@pytest.mark.parametrize("design", [
    None,
    {},
    {f: EXACT for f in overlay.FAMILIES},
    {f: None for f in overlay.FAMILIES},
    {"exponent": EXACT},
    {"data": None, "work": EXACT},
], ids=["none", "empty", "all-exact", "all-none", "one-exact", "mixed-absent-and-exact"])
def test_a_default_or_all_exact_design_is_bit_identical_to_todays_price(priced, design):
    """BIT-IDENTITY. `target: "exact"` must mean "leave this family's QFT exactly as the engine priced
    it" (k_max = None, no substitution). Changing `project` so that an exact target substitutes
    `ReferenceQFT(w)` -- the exact ladder, which is a DIFFERENT construction and costs
    (33_533_953, 8_289_333) at w = 4095 against PolicyQFT's (37_598_400, 178_191) -- fails this."""
    policy, full = priced
    out = overlay.project(policy, full, design)
    assert out["identity"] is True
    assert out["substituted"] == []
    assert out["TBase"] == full["T_base"] == BASELINE_T_BASE
    assert out["rotations"] == full["rotations"] == BASELINE_ROTATIONS
    assert out["tPerRotation"] == full["T_per_rotation"]
    assert out["eps"] == full["eps"]
    assert out["T"] == full["T"] == BASELINE_T          # bit-identical, not approx


@pytest.mark.slow
def test_identity_is_reached_through_the_substitution_machinery_not_by_short_circuit(priced):
    """Non-vacuity of the identity tests: the default path still re-divides the whole run's synthesis
    budget over the rotation total and recombines `base + rotations * rate`. Deleting the budget
    re-division (returning `coherent`'s fields verbatim) would make the identity tests pass for the
    wrong reason, so this pins that the rate is genuinely recomputed from the census."""
    policy, full = priced
    eng = engine.load()
    out = overlay.project(policy, full, {f: EXACT for f in overlay.FAMILIES})
    recomputed = eng.fullalg.sc.ts_per_rotation(full["delta"], out["rotations"])
    assert out["tPerRotation"] == recomputed
    assert out["T"] == out["TBase"] + out["rotations"] * recomputed


# --------------------------------------------------------------------------- the band is real


@pytest.mark.slow
@pytest.mark.parametrize("family", list(overlay.FAMILIES))
def test_an_approximate_target_actually_bands_that_family(priced, family):
    """NON-VACUITY. Every family must reach a real `ReferenceQFT(w, k_max)` substitution. Dropping a
    family from `FAMILY_WIDTH`, or mis-mapping its width (e.g. `data -> yW` instead of `yW + 1`, which
    `fullalg.py:196` labels "QFT on data+1"), fails this."""
    policy, full = priced
    out = overlay.project(policy, full, {family: _approx("1e-6")})
    record = out["families"][family]
    assert out["identity"] is False and out["substituted"] == [family]
    assert record["banded"] is True and record["kMax"] < record["width"] - 1
    assert record["bandedTBase"] < record["exactTBase"]
    assert out["T"] != full["T"] and out["T"] < full["T"]


@pytest.mark.slow
def test_the_family_widths_are_the_engines_own_qft_sites(priced):
    """The four families must land on the four QFT part rows the engine actually prices, with the
    engine's own counts. Re-pointing a family at a width with no QFT site fails here."""
    policy, full = priced
    sites = {(p["xw"], p["count"]) for p in full["parts"] if p["site"] == "qft"}
    seen = set()
    for family in overlay.FAMILIES:
        record = overlay.project(policy, full, {family: _approx("1e-6")})["families"][family]
        seen.add((record["width"], record["count"]))
    assert seen == sites


@pytest.mark.slow
def test_a_looser_error_threshold_gives_a_tighter_band_and_a_cheaper_price(priced):
    """The band must track the threshold monotonically through `aqft_k_max`. Inverting the closed form
    (`log2(eta/w)`, or dropping the `max(1, ...)`) fails this."""
    policy, full = priced
    prices, bands = [], []
    for max_error in ("1e-12", "1e-9", "1e-6", "1e-3"):
        out = overlay.project(policy, full, {"scratch": _approx(max_error)})
        prices.append(out["T"])
        bands.append(out["families"]["scratch"]["kMax"])
    assert bands == sorted(bands, reverse=True)
    assert prices == sorted(prices, reverse=True)


@pytest.mark.slow
def test_the_like_for_like_crosscheck_uses_the_engines_own_float_association(priced):
    """The overlay refuses to substitute into a census it cannot reproduce, by recomputing
    `PolicyQFT`'s split and comparing with the frozen part row. `fullalg.parts` forms that row as
    `split(bloq).scale(k)` then `base + rot * tpr`; comparing instead with `(base + rot*tpr) * k` is a
    different float and spuriously refuses the `work` and `data` families (count 8190). This pins that
    the scaled association is used, so the guard rejects only real disagreement."""
    policy, full = priced
    for family in overlay.FAMILIES:               # all four must pass the guard, not just count == 1
        overlay.project(policy, full, {family: _approx("1e-6")})


# --------------------------------------------------------------------------- the precision band


def test_aqft_k_max_is_the_documented_closed_form():
    """`k_max = max(1, ceil(log2(w/eta)))` (`qft.py:448`, `precision_policy.py:84`). If the in-epoch
    source is loadable the overlay must delegate to it, and the local mirror must agree with it; a
    drift in either fails here."""
    for w in (2, 17, 2049, 2347, 4095, 4398):
        for eta in (1e-2, 1e-6, 1e-12, 0.5):
            assert overlay.aqft_k_max(w, eta) == max(1, math.ceil(math.log2(float(w) / float(eta))))


def test_the_in_epoch_precision_policy_is_the_source_when_present():
    """The repo has one definition of the band; the overlay must not quietly prefer its own mirror."""
    assert overlay.k_max_source().startswith("engine:")
    assert overlay._engine_aqft_k_max()(4095, 1e-6) == overlay.aqft_k_max(4095, 1e-6) == 32


@pytest.mark.parametrize("value,expected", [("1e-6", 1e-6), ("25e-9", 25e-9), ("5e-1", 0.5)])
def test_max_error_parses_the_compiled_string_form(value, expected):
    assert overlay.parse_max_error("exponent", value) == pytest.approx(expected, rel=1e-15)


# --------------------------------------------------------------------------- fail closed


@pytest.mark.parametrize("value", ["1.5e-6", "1E-6", "1e+6", "-1e-6", " 1e-6", "1e-6 ", "", "1e6",
                                   "0e-0", "1e-0", "abc", "1e-", "e-6"])
def test_an_unparseable_or_out_of_range_max_error_is_refused_by_value(value):
    """FAIL CLOSED. `MAX_ERROR_RE` is a `fullmatch` on two non-negative integers, and the resulting eta
    must be a precision in (0, 1). Relaxing the regex to `search`, or accepting a float, fails this."""
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


def test_the_catalytic_backend_is_reported_not_priced_as_ross_selinger():
    """The catalytic closed form exists in `catalysis/spec.py` (per_site_b:410, other_site_cost:719,
    mixing_diamond_max:603, catalyst_prep_T:736 -- all pure, no cache, no word bank) but it re-prices
    every rotation in the run and adds a catalyst register, which is a separate axis. Making the
    overlay accept `catalytic` and price it on the Ross-Selinger currency would be exactly the silent
    default the release rule forbids, and fails this."""
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"exponent": _approx("1e-6", backend="catalytic")})
    assert "exponent" in str(error.value) and "catalytic" in str(error.value)
    assert "ross_selinger" in str(error.value)


def test_an_unrecognised_target_is_refused():
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"data": {"backend": "ross_selinger", "target": "banded"}})
    assert "data" in str(error.value) and "banded" in str(error.value)


def test_an_approximate_target_without_a_max_error_is_not_priced_as_exact():
    """The dangerous default: a band request with no threshold must NOT fall through to the exact
    price. Replacing this refusal with `max_error = None` -> exact fails this."""
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"scratch": {"backend": "ross_selinger", "target": "approximate"}})
    assert "scratch" in str(error.value) and "refused, not priced as exact" in str(error.value)


def test_an_exact_target_carrying_a_max_error_is_refused():
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"work": {"backend": "ross_selinger", "target": "exact", "max_error": "1e-6"}})
    assert "work" in str(error.value) and "1e-6" in str(error.value)


@pytest.mark.parametrize("field", list(overlay.OUT_OF_SCOPE))
def test_the_out_of_scope_selector_fields_are_refused_with_a_qualification_message(field):
    """`step2 := fallback` and `step4 := selectiveKick` are fields of the same compiled selector record
    but are not symbolic, deterministic or closed-form. Silently ignoring them -- pricing the QFT
    families and dropping the rest of the record -- would publish a price for a design that was not
    priced, and fails this."""
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"exponent": EXACT, field: "whatever"})
    assert field in str(error.value) and "separate qualification" in str(error.value)
    assert "word bank" in str(error.value)


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
    assert overlay.parse_design(None) == {}
    assert overlay.parse_design({}) == {}
    assert overlay.parse_design({f: None for f in overlay.FAMILIES}) == {}


# --------------------------------------------------------------------------- the safety properties


def test_the_overlay_fingerprints_its_own_bytes():
    """SELF-FINGERPRINT (`readout_overlay.py:36-37`). The epoch fingerprint cannot cover `export_v2`,
    so a receipt carrying an overlay price must carry the bytes of the rule that produced it. Dropping
    `ruleSha256` from the receipt fails this."""
    from hashlib import sha256
    from pathlib import Path

    assert overlay.rule_fingerprint() == sha256(Path(overlay.__file__).read_bytes()).hexdigest()


@pytest.mark.slow
def test_the_receipt_carries_the_rule_identity_and_the_band_source(priced):
    policy, full = priced
    out = overlay.project(policy, full, {"exponent": _approx("1e-6")})
    assert out["pricingRule"] == overlay.RULE
    assert out["ruleSha256"] == overlay.rule_fingerprint()
    assert out["epoch"] == overlay.EPOCH
    assert out["kMaxSource"].startswith("engine:")
    assert out["families"]["exponent"]["realization"].startswith("ReferenceQFT(w, k_max)")


@pytest.mark.slow
def test_the_epoch_guard_refuses_any_other_engine_model(priced, monkeypatch):
    """EPOCH GUARD (`readout_overlay.py:95`). An overlay price is only honest against the engine model
    it was qualified on. Deleting the guard would let the overlay price a moved engine, and fails
    this."""
    policy, full = priced
    monkeypatch.setattr(overlay, "epoch_of", lambda model: "0000000000000000")
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.project(policy, full, None)
    assert overlay.EPOCH in str(error.value) and "0000000000000000" in str(error.value)


@pytest.mark.slow
def test_a_coarse_price_without_the_per_part_census_is_refused(priced):
    """The overlay needs `detail=True`'s parts to locate the QFT sites; without them it must refuse,
    not price the run as if no QFT were banded."""
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
