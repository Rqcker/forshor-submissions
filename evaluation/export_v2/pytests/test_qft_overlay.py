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


CATALYTIC_EXACT = {"backend": "catalytic", "target": "exact"}


def _approx(max_error="1e-6", backend="ross_selinger"):
    return {"backend": backend, "target": "approximate", "max_error": max_error}


#: `overlay.project(policy, full, {f: CATALYTIC_EXACT for f in FAMILIES})` -- the headline differentiated
#: price. Every one of these is a closed form of the baseline above plus `catalysis/spec.py`; see
#: `test_the_catalytic_headline_price_is_differentiated_and_pinned` for the derivation of each.
CAT_ALL_T = 2104470524231.6128
CAT_ALL_ROTATIONS_CATALYSED = 4102025031           # data 65950*8190 + work 78134*8190
CAT_ALL_ROTATIONS_SYNTHESISED = 1078377300         # + scratch 178376*16380 + exponent 178191*1
CAT_ALL_B = 42
CAT_ALL_CELLS_T = 672732105084                     # R_cat * AND_T * (b - 1) = R_cat * 4 * 41
CAT_ALL_PREP_T = 2820.6100827013543
CAT_ALL_QUBITS_PEAK = 13538                        # 13496 + 42
BASELINE_QUBITS_PEAK = 13496
BASELINE_QUBITS_LAYOUT = 47474


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


# --------------------------------------------------------------------------- the catalytic backend


@pytest.fixture(scope="module")
def cspec():
    """`l2q_full.catalysis.spec`, importable only after `engine.load()` puts `fullalg` on the path."""
    engine.load()
    from l2q_full.catalysis import spec
    return spec


def test_the_four_closed_forms_are_pure_and_live_where_the_brief_says(cspec):
    """PRE-REQUISITE, verified rather than assumed. The catalytic arm is priceable from `export_v2`
    only because its four inputs are pure closed forms of their arguments -- no cache, no word bank, no
    certified table lookup -- so calling them cannot read engine state that the epoch does not cover.
    Each is checked against its own documented formula here; if any grows a cache or a table, these
    equalities break and the arm must be refused again."""
    assert cspec.AND_T == 4                                                      # spec.py:549
    for R, dc in ((1, 1e-3), (5_180_402_331, 3.955e-3), (10**12, 1e-6)):         # spec.py:410
        assert cspec.per_site_b(R, dc) == max(4, math.ceil(math.log2(math.pi * R / dc)))
        assert cspec.per_site_b(R, dc) == cspec.per_site_b(R, dc)                # no memo drift
    for b in (4, 21, 42, 126):
        assert cspec.mixing_diamond_max(b) == 2.0 * math.pi ** 2 * 4.0 ** -b     # spec.py:603
        lone = cspec.other_site_cost(b, mixing=False)                            # spec.py:719
        assert (lone.route, lone.ands, lone.err_diamond) == ("K", b - 1, 0.0)
        assert lone.err_op == math.pi * 2.0 ** -b                                # "turn unknown -> worst case"
        assert cspec.other_site_cost(b).err_diamond == cspec.mixing_diamond_max(b)
        m = b - 3                                                                # spec.py:736
        assert cspec.catalyst_prep_T(b, 1e-6) == m * cspec.ross_selinger_t(1e-6 / m) + 1.0


@pytest.mark.slow
def test_the_catalytic_headline_price_is_differentiated_and_pinned(priced, cspec):
    """THE RELEASE HEADLINE. A catalytic design must produce a price DIFFERENT from the default, and
    every term of it must be the engine's or `catalysis/spec.py`'s own closed form -- not a constant
    chosen to make a number move. Reverting `project`'s catalytic arm (so `backend: "catalytic"` is
    refused again, or priced at the Ross-Selinger default) fails this test first."""
    policy, full = priced
    out = overlay.project(policy, full, {f: CATALYTIC_EXACT for f in overlay.FAMILIES})
    cat = out["catalyst"]

    # DIFFERENTIATED: the whole point of the release.
    assert out["T"] != full["T"]
    assert out["T"] == CAT_ALL_T
    assert out["identity"] is False and out["catalysed"] == sorted(overlay.FAMILIES)

    # The census is UNCHANGED -- `target: "exact"` substitutes no construction. Only the currency moved.
    assert out["TBase"] == full["T_base"] == BASELINE_T_BASE
    assert out["rotations"] == full["rotations"] == BASELINE_ROTATIONS
    assert out["tPerRotation"] == full["T_per_rotation"]

    # Every term, re-derived here from the engine and the spec.
    assert cat["b"] == CAT_ALL_B == cspec.per_site_b(cat["rotationsCatalysed"], cat["deltaCells"])
    assert out["rotationsCatalysed"] == CAT_ALL_ROTATIONS_CATALYSED
    assert out["rotationsSynthesised"] == CAT_ALL_ROTATIONS_SYNTHESISED
    assert out["rotationsSynthesised"] + out["rotationsCatalysed"] == out["rotations"]
    assert cat["andsPerSite"] == cspec.other_site_cost(cat["b"], mixing=False).ands == CAT_ALL_B - 1
    assert cat["cellsT"] == CAT_ALL_CELLS_T == out["rotationsCatalysed"] * cspec.AND_T * cat["andsPerSite"]
    assert cat["prepT"] == CAT_ALL_PREP_T == cspec.catalyst_prep_T(cat["b"], cat["prepBudget"])
    assert out["T"] == out["TBase"] + out["rotationsSynthesised"] * out["tPerRotation"] \
        + cat["cellsT"] + cat["prepT"]


@pytest.mark.slow
def test_the_catalyst_register_is_charged_to_the_qubit_count(priced):
    """SUB-PROBLEM 2. The catalytic arm adds a SHARED b-qubit phase-gradient catalyst that is live for
    the whole run, and Q feeds the ranked Q x T. A T-only catalytic price would understate Q and so
    understate the product. Deleting `catalystQubits` from the qubit totals -- reporting the engine's
    frozen layout as if the register were free -- fails this."""
    policy, full = priced
    out = overlay.project(policy, full, {f: CATALYTIC_EXACT for f in overlay.FAMILIES})
    b = out["catalyst"]["b"]
    assert out["catalystQubits"] == b == CAT_ALL_B
    assert full["qubits_peak"] == BASELINE_QUBITS_PEAK and full["qubits"] == BASELINE_QUBITS_LAYOUT
    assert out["qubitsPeak"] == BASELINE_QUBITS_PEAK + b == CAT_ALL_QUBITS_PEAK
    assert out["qubitsUpperBound"] == BASELINE_QUBITS_LAYOUT + b
    assert out["qubitsPeak"] > full["qubits_peak"]              # the delta is real, not cosmetic
    assert out["qubitTimesT"] == out["qubitsPeak"] * out["T"]
    assert out["qubitTimesT"] > full["qubits_peak"] * full["T"]


@pytest.mark.slow
def test_one_shared_register_serves_every_catalytic_family(priced):
    """The catalyst is SHARED (one `PhaseGradientState(b)` restored after every kick), not one register
    per family. Charging b per catalytic family would scale the qubit delta with the number of families
    and fails this: one family and four families pay the same register."""
    policy, full = priced
    one = overlay.project(policy, full, {"exponent": CATALYTIC_EXACT})
    four = overlay.project(policy, full, {f: CATALYTIC_EXACT for f in overlay.FAMILIES})
    assert one["catalystQubits"] == four["catalystQubits"] == four["catalyst"]["b"]
    assert one["qubitsPeak"] == four["qubitsPeak"]
    assert one["catalyst"]["families"] == ["exponent"]
    assert four["catalyst"]["families"] == sorted(overlay.FAMILIES)
    assert one["T"] != four["T"]                      # the T side still differentiates them


@pytest.mark.slow
def test_the_catalytic_arm_takes_its_own_share_of_the_run_wide_synthesis_budget(priced, cspec):
    """SUB-PROBLEM 1. Catalysis REPLACES Ross-Selinger for the rotations it covers, so it must be
    charged its own share of the one run-wide synthesis pool (`sto_currency.ROTATION_FRACTION * delta`
    = 1/200 at delta = 1e-2), not be handed free error. The partition is exact: the pool is divided
    evenly over the rotation census exactly as `sto_currency.rotation_eps` divides it, and the
    catalysed rotations take the slices that were theirs. Two failures this catches:
      * giving the arm no budget at all (catalysis for free), and
      * re-dividing the pool over the SURVIVING rotations only, which spends the catalysed rotations'
        slices twice -- once on the survivors' eps and once on the catalyst's b."""
    policy, full = priced
    out = overlay.project(policy, full, {f: CATALYTIC_EXACT for f in overlay.FAMILIES})
    cat = out["catalyst"]
    eng = engine.load()
    pool = eng.fullalg.sc.ROTATION_FRACTION * full["delta"]
    assert pool == 1.0 / 200.0                                   # DELTA / 2 at delta = 1e-2
    assert cat["pool"] == pool

    # The arm's share is its rotations' share of the census, and nothing else.
    assert cat["deltaArm"] == pool * out["rotationsCatalysed"] / out["rotations"]
    assert 0.0 < cat["deltaArm"] < pool
    # The survivors keep the engine's OWN per-rotation eps, unchanged. This is what makes it a
    # partition rather than a re-division: no rotation's slice is spent twice.
    assert out["tPerRotation"] == full["T_per_rotation"]
    assert out["epsSynthesised"] == full["eps"] == eng.fullalg.sc.rotation_eps(full["delta"], out["rotations"])
    spent = out["rotationsSynthesised"] * out["epsSynthesised"] + cat["deltaArm"]
    assert spent == pytest.approx(pool, rel=1e-12)
    # And the arm really is spent: prep + cells exhaust it, and the charged error fits inside it.
    assert cat["prepBudget"] + cat["deltaCells"] == pytest.approx(cat["deltaArm"], rel=1e-12)
    assert 0.0 < cat["errOpCharged"] <= cat["deltaCells"]


@pytest.mark.slow
def test_catalytic_is_not_priced_on_ross_selinger_currency(priced):
    """THE SILENT DEFAULT THE RELEASE RULE FORBIDS. The catalysed rotations must cost
    `AND_T * (b - 1)` T each (Ands into the catalyst), NOT `tPerRotation` T each. At this policy the
    two differ by a wide margin in the EXPENSIVE direction -- 164 T per catalysed site against the
    engine's 123.596 T per synthesised rotation -- so an overlay that quietly kept the Ross-Selinger
    rate would report a CHEAPER price than the truth, which is the exact failure mode."""
    policy, full = priced
    out = overlay.project(policy, full, {f: CATALYTIC_EXACT for f in overlay.FAMILIES})
    cat = out["catalyst"]
    rs_rate, cat_rate = out["tPerRotation"], cat["tPerSite"]
    assert cat_rate == 4 * (cat["b"] - 1) == 164
    assert rs_rate == pytest.approx(123.59639006403667)
    assert cat_rate != rs_rate
    would_have_been_rs = out["rotationsCatalysed"] * rs_rate
    assert cat["cellsT"] != would_have_been_rs
    assert cat["cellsT"] > would_have_been_rs                 # catalysis is DEARER here, and we say so
    assert out["T"] > full["T"]


@pytest.mark.slow
def test_the_catalyst_preparation_is_a_separate_unconditional_charge(priced, cspec):
    """SUB-PROBLEM 3. `catalyst_prep_T` is a separately certified gradient-preparation allowance: the
    b - 3 genuine ZPow rotations of `PhaseGradientState(b)` plus its 1 T, synthesised against the arm's
    own prep share. It is charged whenever a catalyst exists, even though the kicks themselves are
    priced as Ands and even when the rounding they do is free. Making prep conditional -- on the band,
    on inexactness, on anything -- fails this, as does folding it into `cellsT`."""
    policy, full = priced
    for design in ({f: CATALYTIC_EXACT for f in overlay.FAMILIES},
                   {"exponent": CATALYTIC_EXACT},
                   {"data": _approx("25e-5", backend="catalytic")}):
        cat = overlay.project(policy, full, design)["catalyst"]
        assert cat["prepT"] > 0.0
        assert cat["prepT"] == cspec.catalyst_prep_T(cat["b"], cat["prepBudget"])
        assert cat["T"] == cat["cellsT"] + cat["prepT"] and cat["cellsT"] != cat["T"]
        assert cat["prepBudget"] == overlay.PREP_FRACTION * cat["deltaArm"]
        assert cat["prepRotations"] == cat["b"] - 3


@pytest.mark.slow
def test_the_deterministic_arm_is_the_conservative_one(priced, cspec):
    """The overlay charges the DETERMINISTIC round-to-nearest error (`other_site_cost(b, mixing=False)`,
    operator norm pi 2^-b per site), which is the metric the engine's delta is stated in and the metric
    `per_site_b` budgets. The mixed arm (D5) would be cheaper in error and so admit a smaller b, but it
    buys that with classical randomness and reports a diamond/TV bound instead. Switching the overlay
    to `mixing=True` without re-deriving the b rule would charge the budget in the wrong metric; this
    pins that the arm actually chosen is the worst case of the two."""
    policy, full = priced
    cat = overlay.project(policy, full, {f: CATALYTIC_EXACT for f in overlay.FAMILIES})["catalyst"]
    assert cat["mixing"] is False
    assert cat["errOpCharged"] == cat["rotationsCatalysed"] * math.pi * 2.0 ** -cat["b"]
    assert cat["errMixedTV"] == cat["rotationsCatalysed"] * 0.5 * cspec.mixing_diamond_max(cat["b"])
    assert cat["errOpCharged"] > cat["errMixedTV"]             # deterministic is the upper bound


@pytest.mark.slow
def test_an_exact_target_with_a_catalytic_backend_leaves_the_construction_untouched(priced):
    """The owner's two axes are ORTHOGONAL: `target` chooses the construction, `backend` chooses the
    currency its rotations are synthesised in. So `catalytic` + `exact` must leave the family's native
    `PolicyQFT` template exactly as the engine built it -- no `ReferenceQFT`, no band -- and move only
    the currency. Substituting the exact ladder here costs 14.8x T and fails this."""
    policy, full = priced
    out = overlay.project(policy, full, {f: CATALYTIC_EXACT for f in overlay.FAMILIES})
    assert out["substituted"] == []                     # no construction was replaced
    assert out["TBase"] == full["T_base"] and out["rotations"] == full["rotations"]
    for family in overlay.FAMILIES:
        record = out["families"][family]
        assert record["target"] == "exact" and record["backend"] == "catalytic"
        assert record["kMax"] is None and record["banded"] is False
        assert record["realization"].startswith("unchanged:")
        assert record["currency"] == "catalytic"


@pytest.mark.slow
def test_the_four_axis_combinations_all_price_and_all_differ(priced):
    """The release ships an AXIS, so all four corners of exact/approximate x ross_selinger/catalytic
    must price, and they must price differently. A collapse of any corner onto another -- the backend
    ignored, or the target ignored -- fails this."""
    policy, full = priced
    corners = {
        ("ross_selinger", "exact"): {"work": EXACT},
        ("ross_selinger", "approximate"): {"work": _approx("25e-5")},
        ("catalytic", "exact"): {"work": CATALYTIC_EXACT},
        ("catalytic", "approximate"): {"work": _approx("25e-5", backend="catalytic")},
    }
    prices = {k: overlay.project(policy, full, d)["T"] for k, d in corners.items()}
    assert prices[("ross_selinger", "exact")] == full["T"]        # the identity corner
    assert len(set(prices.values())) == 4, prices


@pytest.mark.slow
def test_catalysis_prices_the_rotations_that_survive_banding(priced):
    """COMPOSITION. When a family is both banded and catalytic, the catalyst must price the band's
    rotation count, not the native template's: banding happens first and removes rotations the
    catalyst then never sees. Catalysing the pre-band count would over-charge the arm and fails this."""
    policy, full = priced
    banded_only = overlay.project(policy, full, {"work": _approx("25e-5")})
    both = overlay.project(policy, full, {"work": _approx("25e-5", backend="catalytic")})
    record = both["families"]["work"]
    # Same construction, same census: banding is unaffected by the currency choice.
    assert (both["TBase"], both["rotations"]) == (banded_only["TBase"], banded_only["rotations"])
    assert record["banded"] is True and record["currency"] == "catalytic"
    assert both["rotationsCatalysed"] == record["bandedRotations"] * record["count"]
    assert both["rotationsCatalysed"] < record["exactRotations"] * record["count"]
    assert both["T"] != banded_only["T"]


@pytest.mark.slow
def test_b_depends_on_the_run_wide_census_not_on_how_much_is_catalysed(priced, cspec):
    """A PROPERTY OF THE PARTITION, pinned because it is the check that the share is proportional. Each
    catalysed site is allotted the same slice pool / R_total that the engine allots each rotation, so
    the per-site requirement pi 2^-b <= slice -- and hence b -- is set by the run-wide census alone and
    is identical whether one family or four are catalysed. If b ever varies with the catalysed count at
    a FIXED census, the arm is no longer taking exactly its own share."""
    policy, full = priced
    bs = {tuple(sorted(d)): overlay.project(policy, full, d)["catalyst"]["b"]
          for d in ({"exponent": CATALYTIC_EXACT}, {"data": CATALYTIC_EXACT},
                    {"data": CATALYTIC_EXACT, "work": CATALYTIC_EXACT},
                    {f: CATALYTIC_EXACT for f in overlay.FAMILIES})}
    assert set(bs.values()) == {CAT_ALL_B}, bs


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


def test_the_catalytic_backend_parses_rather_than_refusing():
    """WAS a refusal (lane WM: "a second axis"). It is now PRICED -- the three sub-problems are handled
    in `project`: the arm takes its own proportional share of the synthesis pool, the shared catalyst
    register is charged to Q, and `catalyst_prep_T` is a separate unconditional charge. `parse_design`
    must therefore accept the backend on every family and on both targets. The pricing itself is pinned
    by `test_the_catalytic_headline_price_is_differentiated_and_pinned`."""
    assert overlay.parse_design({"exponent": _approx("1e-6", backend="catalytic")}) == \
        {"exponent": ("catalytic", "approximate", "1e-6")}
    assert overlay.parse_design({f: CATALYTIC_EXACT for f in overlay.FAMILIES}) == \
        {f: ("catalytic", "exact", None) for f in overlay.FAMILIES}


@pytest.mark.slow
def test_a_catalytic_family_with_no_rotations_to_catalyse_is_refused(priced, monkeypatch):
    """FAIL CLOSED, naming the family and the value. A catalyst exists only to absorb rotations; a
    request to catalyse a family that contributes none has no priceable arm, and must be reported
    rather than priced as the untouched default."""
    policy, full = priced
    monkeypatch.setattr(overlay, "_catalysed_rotations", lambda *a, **kw: 0)
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.project(policy, full, {"exponent": CATALYTIC_EXACT})
    assert "exponent" in str(error.value) and "catalytic" in str(error.value)
    assert "0 rotations" in str(error.value)


@pytest.mark.slow
def test_a_catalyst_wider_than_the_specs_own_range_is_refused(priced, monkeypatch):
    """FAIL CLOSED. `catalysis.budget.choose_b` searches b in [4, 126]; a budget so tight that
    `per_site_b` lands outside that range is outside the qualified closed form, so the overlay reports
    it instead of extrapolating. Raising `B_MAX` past the spec's own cap fails this."""
    policy, full = priced
    monkeypatch.setattr(overlay, "B_MAX", 8)
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.project(policy, full, {"exponent": CATALYTIC_EXACT})
    assert "exponent" in str(error.value) and "42" in str(error.value) and "[4, 8]" in str(error.value)


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
def test_the_out_of_scope_selector_fields_are_refused_by_name(field):
    """The phase-product steps are fields of the same compiled selector record but are not symbolic,
    deterministic or closed-form. Silently ignoring them -- pricing the QFT families and dropping the
    rest of the record -- would publish a price for a design that was not priced, and fails this."""
    with pytest.raises(overlay.QftOverlayRefused) as error:
        overlay.parse_design({"exponent": EXACT, field: "whatever"})
    assert field in str(error.value) and "not symbolic, deterministic or closed-form" in str(error.value)
    assert "phase-product step" in str(error.value) and f"re-submit without {field!r}" in str(error.value)


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
