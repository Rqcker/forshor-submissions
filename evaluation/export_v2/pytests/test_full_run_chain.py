"""END TO END: a compiled document's `submitted_full_run` -> a priced panel carrying a `qft` projection.

This is lane WP's deliverable: the join of lane WO (`full_run_design`, the `design=` threading) and lane WM
(`qft_overlay`, the Coppersmith-banded QFT price) into ONE chain, exercised from the verifier's own output.

    compiled document
      -> full_run_design.selection_of   (key PRESENCE is the test)
      -> full_run_design.design_of      (profile -> per-family QFT design, or None, or REFUSED by name)
      -> pricing.price_views(design=)   (delta 1e-2 and the full board only)
      -> qft_overlay.project            (the hook at pricing.py:52-54, mirroring `measured` at :48-51)
      -> mapping.panel_of               (the panel carries `qft` beside the engine's own price)

Lane WY then removed the two structural obstacles this chain still ran into -- a design-bearing publish request
raised partway through its four-delta loop, and the projection was never the ranked price. See
`test_design_price_plumbing.py`; the chain tests below are unchanged except where they stated an obstacle as a
permanent fact.

WHAT THIS LANE FOUND, and what these tests therefore pin
--------------------------------------------------------------------------------------------------
The two lanes were written against DIFFERENT contracts and only one of them landed:

* WO threaded the design into the ENGINE (`price_full(..., design=design)`), per a brief that expected lane
  WM to add that parameter -- the brief whose name `prepush/WM-engine-design-param` still carries.
* WM did not, and documented why: `evaluation/engine` is an epoch input through BOTH `engineRevision`
  (`epoch.py:91`) and `dataFingerprint` (`full_frontier.py:43-50`, a byte hash of the pricer sources), so an
  additive `design=` parameter could not be default-preserving at the epoch level however carefully written
  (measured: one comment line appended to `site_engine/fullalg.py` moves ab1f8887b1b50e56 to
  d3bda33d5ddd5dca). WM qualified the axis as an out-of-engine OVERLAY instead.

So `price_full` still has its 792464f signature and WO's engine-side call raised
`TypeError: price_full() got an unexpected keyword argument 'design'` against the real engine. WP kept all of
WO's threading -- the signature, the 1e-2 guard, the full-board guard, the refuse-before-pricing ordering in
`request._build_request` -- and routed the design to the overlay instead. `test_the_chain_never_asks_the_engine_
for_a_design` pins that, so nobody re-adds the engine-side call.

TWO GATES REFUSE, NOT ONE. `full_run_design` converts three of the seven profiles, but `qft_overlay` then
refuses two of those three, because they ask for `backend: "catalytic"` on a QFT family and the catalytic arm
re-prices every rotation in the run and adds a shared catalyst register -- a second axis, not a QFT band.
Only `quotient-exact` prices today, and `quotient-exact` is BY CONSTRUCTION the engine's own price. So the
merged chain is complete and fail-closed, but it cannot yet produce a DIFFERENTIATED full-run price at all.
`test_only_quotient_exact_prices_today` states that as a fact rather than leaving it to be discovered.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import canon, full_run_design, mapping, pricing, qft_overlay, request  # noqa: E402

DATA = Path(__file__).parent / "data"
POLICY = DATA / "real/1bc92ac6e1e82ba3.policy.json"

#: Panel sha256 at 792464f for `POLICY` at delta 1e-2, recorded by lane WO and re-verified on this merged
#: branch. The non-negotiable number: anything that moves it has changed a published price.
HEAD_PANELS = {"full": "998ad142a47fae05466541a3a92e0851cb86bac91876ce409ec4a98b3e0b19e3",
               "pp": "c83bf7a532a639d1b16d2e1e9b86b26e2bef36e8db96137e2d259a72d527179f"}

#: Priced here (QFT families only, no catalytic arm).
PRICES = ("quotient-exact",)
#: Converted by `full_run_design`, then refused by `qft_overlay`: `backend: "catalytic"` on a QFT family.
REFUSED_BY_OVERLAY = ("quotient-catalytic", "quotient-banded-data-catalytic-work")
#: Refused by `full_run_design` itself: they select a rotation or phase-product backend, not a QFT.
REFUSED_BY_DESIGN = ("quotient-fallback-data-catalytic-work", "quotient-step2-fallback",
                     "split-step4-kicks", "split-step2-fallback-step4-kicks")

#: The compiled document the verifier prints for a non-reference profile: the measured (semiclassical) schema,
#: because `PolicyMain.lean:444-447` refuses a full-run selection unless the saved readout IS semiclassical,
#: plus the selection object `PolicyMain.lean:462-465` merges in.
MEASURED_SCHEMA = "forshor.policy-submission-semiclassical/v1"


def document(profile=None):
    doc = {"schema": MEASURED_SCHEMA, "submitted_readout": "semiclassical"}
    if profile is not None:
        doc["submitted_full_run"] = {"schema": "forshor.full-run-selection/v1", "profile": profile}
    return doc


def design_from(doc):
    """The two WO steps of the chain, in order, exactly as `request._build_request` runs them."""
    return full_run_design.design_of(full_run_design.selection_of(doc))


@pytest.fixture(scope="module")
def policy():
    from export_v2 import engine
    return engine.load().tables.Policy.from_json(json.loads(POLICY.read_text()))


@pytest.fixture(scope="module")
def epoch_id():
    return canon.epoch_of(json.loads((DATA / "golden.json").read_text())["model"])


def panel(view, epoch_id, board):
    return mapping.panel_of(view, epoch=epoch_id, row_id="a" * 40, board=board, delta="1e-2",
                            verification={"stoMirror": None, "forshorPolicy": None})


# ------------------------------------------------------------------ the chain, with the real engine


@pytest.mark.slow
@pytest.mark.parametrize("profile", PRICES)
def test_a_compiled_document_prices_a_panel_carrying_a_qft_projection(policy, epoch_id, profile):
    """THE DELIVERABLE. Start at the verifier's compiled document, end at a panel whose full-run price carries
    a `qft` projection, through all four production steps.

    Fails if the hook at `pricing.py:52-54` is reverted (then `view["full"]` has no `qft` and the panel has
    none either), if `mapping.full_run_of` stops carrying `qft`, or if either WO step stops converting."""
    doc = document(profile)
    assert doc["submitted_full_run"]["profile"] == profile

    selection = full_run_design.selection_of(doc)
    assert selection == {"schema": "forshor.full-run-selection/v1", "profile": profile}
    design = full_run_design.design_of(selection)
    assert design is not None and set(design) <= set(qft_overlay.FAMILIES)

    views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"], design=design)
    projection = views["full"]["full"]["qft"]
    assert projection["pricingRule"] == qft_overlay.RULE == "coppersmith-banded-qft-v1"
    assert projection["epoch"] == qft_overlay.EPOCH == "ab1f8887b1b50e56"
    assert projection["ruleSha256"] == qft_overlay.rule_fingerprint()
    assert set(projection["families"]) == set(design)

    built = panel(views["full"], epoch_id, "full")
    assert built["fullRun"]["qft"] == projection, "the panel must carry the projection the pricer produced"


@pytest.mark.slow
def test_quotient_exact_is_bit_identical_to_the_no_selection_baseline(policy, epoch_id):
    """THE REGRESSION GUARD. `target: "exact"` means "leave this family's QFT exactly as the engine priced it"
    (k_max = None, no substitution), never "substitute the exact ladder" -- lane WM measured that substituting
    the exact Draper ladder at w = 4095 takes (37_598_400, 178_191) to (33_533_953, 8_289_333), a 14.8x
    rotation-driven blow-up in T. If `qft_overlay.project` ever starts substituting on an exact target, or if
    `full_run_design` stops emitting `target: "exact"` for `quotient-exact`, this test goes red.

    Four statements, strongest last: the projection declares itself the identity; its census is the SAME float
    as the engine's own; the priced panel differs from the baseline panel in NOTHING except the added `qft`
    evidence block; and with that block removed the panel bytes are the recorded 792464f hash exactly.

    The last two are the honest form of "bit-identical" once a panel can carry the projection at all: no PRICE
    moves, and the only new bytes are an evidence block that says nothing was substituted. A plain hash compare
    could not distinguish "nothing moved" from "two things moved and cancelled"."""
    baseline = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"])
    priced = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"],
                                 design=design_from(document("quotient-exact")))
    engine_price, projection = baseline["full"]["full"], priced["full"]["full"]["qft"]

    assert projection["identity"] is True and projection["substituted"] == []
    assert all(f["target"] == "exact" and f["kMax"] is None and f["banded"] is False
               for f in projection["families"].values())
    assert projection["T"] == engine_price["T"]
    assert projection["TBase"] == engine_price["T_base"]
    assert projection["rotations"] == engine_price["rotations"]

    built, base_panel = panel(priced["full"], epoch_id, "full"), panel(baseline["full"], epoch_id, "full")
    assert built["fullRun"].pop("qft") == projection
    assert built == base_panel, "a design must change nothing but add its own evidence block"
    assert canon.sha256_hex(built) == canon.sha256_hex(base_panel) == HEAD_PANELS["full"]


@pytest.mark.slow
@pytest.mark.parametrize("doc,label", [(document(), "absent-selection"), (document("reference"), "explicit-reference")],
                         ids=["absent-selection", "explicit-reference"])
def test_absent_and_explicit_reference_are_design_none_and_the_byte_identical_baseline(policy, epoch_id, doc, label):
    """Fails if `reference` becomes an all-defaults record (`{}`) instead of None, if `selection_of` stops using
    key PRESENCE as the test, or if the threading perturbs the default price by one byte. Both panels, both
    boards, against the hashes lane WO recorded at 792464f."""
    design = design_from(doc)
    assert design is None, label
    views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["pp", "full"], design=design)
    for board in ("pp", "full"):
        assert "qft" not in views[board].get("full", {}), "no design means no projection at all"
        assert canon.sha256_hex(panel(views[board], epoch_id, board)) == HEAD_PANELS[board], (label, board)


# ------------------------------------------------------------------ refusals: by name, pricing nothing


@pytest.mark.parametrize("profile", REFUSED_BY_DESIGN)
def test_the_four_non_qft_profiles_refuse_by_name_at_the_design_step(profile):
    """Fails if `full_run_design.design_of` prices a selection the QFT design cannot express -- silently at the
    default, which would publish a cheaper price than the submission asked for. The refusal must NAME the
    profile and what it selects beyond the QFT families."""
    with pytest.raises(ValueError) as caught:
        design_from(document(profile))
    message = str(caught.value)
    assert profile in message, "a refusal that does not name the profile is not actionable"
    assert full_run_design.BEYOND_QFT[profile] in message


@pytest.mark.parametrize("profile", REFUSED_BY_DESIGN)
def test_a_refused_selection_prices_nothing_and_builds_no_request(monkeypatch, profile):
    """Fails if `request._build_request` converts the selection AFTER the evidence lookup or the pricing loop.
    A refusal must not load the engine, not price a board, and not emit any part of a request."""
    monkeypatch.setattr(request.engine, "load", lambda: pytest.fail("a refused selection must not load the engine"))
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a refused selection must not be priced"))
    args = dict(model=None, store=None, head_sha="a" * 40, pull_number=1, github_login="t", run_id="1",
                submitted_at="x", evaluated_at="y", archive_commit="b" * 40, accepted_ids=[], evaluation_key="e",
                base_generation="1", number=1)
    with pytest.raises(ValueError, match=profile):
        request._build_request({}, full_run=full_run_design.selection_of(document(profile)), **args)


@pytest.mark.slow
@pytest.mark.parametrize("profile", REFUSED_BY_OVERLAY)
def test_the_catalytic_profiles_convert_then_refuse_at_the_overlay_naming_the_family(policy, profile):
    """THE SECOND GATE, and the lane's main finding. These two profiles pass `full_run_design` -- they name only
    QFT families -- and are then refused by `qft_overlay.parse_design`, which does not price `backend:
    "catalytic"`: the catalytic closed form re-prices every rotation in the run and adds a shared catalyst
    register, so honouring it as Ross-Selinger would be a silent default. Fails if either lane starts pricing
    the catalytic arm without qualifying that second axis, or if the refusal stops naming the family."""
    design = design_from(document(profile))
    assert design is not None
    assert any(choice["backend"] == "catalytic" for choice in design.values())
    with pytest.raises(qft_overlay.QftOverlayRefused) as caught:
        pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"], design=design)
    message = str(caught.value)
    assert "catalytic" in message
    assert any(f"QFT family {family!r}" in message for family in design), "the refusal must name the family"


def test_only_quotient_exact_prices_today(policy=None):
    """The release-decision fact, kept as an assertion so it cannot rot: of the seven compiled profiles, six
    refuse -- four at `full_run_design`, two at `qft_overlay` -- and the one that prices is the identity. The
    merged chain is complete and fail-closed, but it cannot yet produce a differentiated full-run price.
    Fails the moment a lane wires the catalytic arm or the out-of-scope selectors, which is the signal that
    this statement needs rewriting."""
    assert set(full_run_design.PROFILES) == {"reference", *PRICES, *REFUSED_BY_OVERLAY, *REFUSED_BY_DESIGN}
    assert set(full_run_design.QFT_ONLY) == {*PRICES, *REFUSED_BY_OVERLAY}
    for profile in REFUSED_BY_OVERLAY:
        with pytest.raises(qft_overlay.QftOverlayRefused):
            qft_overlay.parse_design(design_from(document(profile)))
    plan = qft_overlay.parse_design(design_from(document("quotient-exact")))
    assert plan and all(target == "exact" for _backend, target, _max_error in plan.values())


# ------------------------------------------------------------------ the engine is never asked for a design


def test_the_chain_never_asks_the_engine_for_a_design(monkeypatch):
    """Pins lane WP's merge resolution. `evaluation/engine` is an epoch input, `price_full` still has its
    792464f signature `(P, n, delta, detail, rule, negation)`, and WO's `price_full(..., design=design)` raised
    TypeError against it. The design reaches the OVERLAY. Fails if the engine-side call is ever re-added."""
    import inspect
    from export_v2 import engine

    assert "design" not in inspect.signature(engine.load().fullalg.price_full).parameters

    calls, seen = [], []

    class Fullalg:
        @staticmethod
        def price_full(P, n, delta, **kw):
            calls.append(kw)
            return {"usage": [], "tables_used": []}

    class Tables:
        PAIR, TRIPLE = "PhaseProduct", "PhaseTripleProduct"

    class PolicyView:
        @staticmethod
        def view(P, n, delta, title):
            return {"levels": [], "bands": []}

        @staticmethod
        def table_view(t):
            return t

    class Eng:
        fullalg, tables, policy_view = Fullalg, Tables, PolicyView

    class FakePolicy:
        mode, bands = "PhaseProduct", ()

        def with_widths(self, _):
            return self

    monkeypatch.setattr(pricing.engine, "load", lambda: Eng)
    monkeypatch.setattr(qft_overlay, "project", lambda P, full, design: seen.append(copy.deepcopy(design)) or {"ok": True})
    design = design_from(document("quotient-exact"))
    views = pricing.price_views(FakePolicy(), "t", "1e-2", ["full"], design=design)

    assert calls == [{"detail": True}], "the full-run pricer is called exactly as it was at 792464f"
    assert seen == [design], "the design reaches the overlay instead"
    assert views["full"]["full"]["qft"] == {"ok": True}


@pytest.mark.parametrize("delta", ["3e-4", "1e-4", "1e-6"])
def test_a_design_is_refused_at_every_delta_but_1e_2(delta):
    """WO's guard, still a guard. When this lane wrote it, `request._build_request` passed the design at all
    four deltas, so this refusal fired partway through the pricing loop and a design-bearing document could not
    complete a publish request at all -- the STRUCTURAL obstacle lane WY then removed by scoping the design to
    `mapping.DESIGN_DELTA` in the request and leaving this guard exactly as it is. It stays a precondition on
    the caller rather than a silent drop; `test_design_price_plumbing.py` pins the scoping and the byte
    identity of the other three panels. Fails if the 1e-2 guard in `price_views` is relaxed."""
    class FakePolicy:
        mode, bands = "PhaseProduct", ()

        def with_widths(self, _):
            return self

    with pytest.raises(ValueError, match="delta 1e-2"):
        pricing.price_views(FakePolicy(), "t", delta, ["full"], design=design_from(document("quotient-exact")))
