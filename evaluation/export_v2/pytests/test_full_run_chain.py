"""END TO END: a compiled document's `submitted_full_run` -> a priced panel carrying a `qft` projection.

The chain joins `full_run_design` (the conversion and the `design=` threading) and `qft_overlay` (the
Coppersmith-banded QFT price) into ONE path, exercised from the verifier's own output.

    compiled document
      -> full_run_design.selection_of   (key PRESENCE is the test)
      -> full_run_design.design_of      (profile -> per-family QFT design, or None, or REFUSED by name)
      -> pricing.price_views(design=)   (delta 1e-2 and the full board only)
      -> qft_overlay.project            (the hook at pricing.py:52-54, mirroring `measured` at :48-51)
      -> mapping.panel_of               (the panel carries `qft` beside the engine's own price)

A design-bearing publish request is scoped to `mapping.DESIGN_DELTA`, and the projection is the ranked price;
`test_design_price_plumbing.py` pins both.

WHAT THESE TESTS PIN
--------------------------------------------------------------------------------------------------
* THE ENGINE IS NEVER ASKED FOR A DESIGN. `evaluation/engine` is an epoch input through BOTH `engineRevision`
  (`epoch.py:91`) and `dataFingerprint` (`full_frontier.py:43-50`, a byte hash of the pricer sources), so an
  additive `design=` parameter could not be default-preserving at the epoch level however carefully written
  (measured: one comment line appended to `site_engine/fullalg.py` moves ab1f8887b1b50e56 to
  d3bda33d5ddd5dca). The design axis is priced by an out-of-engine OVERLAY instead, so `price_full` keeps its
  792464f signature (a `design=` keyword raises `TypeError: price_full() got an unexpected keyword argument
  'design'`). The threading -- the signature, the 1e-2 guard, the full-board guard, the refuse-before-pricing
  ordering in `request._build_request` -- routes the design to the overlay. `test_the_chain_never_asks_the_
  engine_for_a_design` pins that.

* EVERY PROFILE LABEL REFUSES; THE EXPLICIT DESIGN IS WHAT PRICES. Converting a profile to a bare per-family
  mapping would DROP its multiplier topology: all seven non-reference profiles select `modmul` =
  `quotient_stream` or `split_compare`, the topology drives both the T cost and the qubit base, and
  `qft_overlay.project` substitutes QFT sites into the engine's frozen REFERENCE census and has no topology
  parameter at all. That would price a quotientStream run on reference-topology numbers -- reporting the
  reference price as if the option had been honoured, a silent default. The TOPOLOGY GATE refuses it instead,
  by name, so every `quotient-*` and every `split-*` profile label REFUSES. What prices is the EXPLICIT
  per-family design on the reference topology (`full_run_design`'s second path), which takes any combination
  of the QFT families. `test_every_profile_label_refuses_and_the_explicit_design_prices` states that as a fact.

* THE CHAIN DIFFERENTIATES, ON THAT PATH. The catalytic arm is priced: its own proportional share of the
  run-wide synthesis pool, the shared catalyst register charged to Q, and `catalyst_prep_T` as a separate
  unconditional charge. So a design naming `backend: "catalytic"` produces a price that DIFFERS from the
  engine's own. The per-family combinations the profile labels named -- exact/exact, catalytic/catalytic,
  banded-data/catalytic-work -- all price when expressed as EXPLICIT reference-topology designs; only the
  quotientStream topology attached to the labels cannot be honoured:
  `test_the_catalytic_designs_price_a_differentiated_full_run` and
  `test_the_explicit_design_prices_every_combination_the_profile_labels_named`.
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

#: Panel sha256 at 792464f for `POLICY` at delta 1e-2, recorded  and re-verified on this merged
#: branch. The non-negotiable number: anything that moves it has changed a published price.
HEAD_PANELS = {"full": "998ad142a47fae05466541a3a92e0851cb86bac91876ce409ec4a98b3e0b19e3",
               "pp": "c83bf7a532a639d1b16d2e1e9b86b26e2bef36e8db96137e2d259a72d527179f"}

#: REFUSED ON THEIR MULTIPLIER TOPOLOGY. These three name only QFT families, so they once
#: converted; they select `modmul: quotient_stream`, which `qft_overlay` cannot honour, so they refuse by name.
REFUSED_BY_TOPOLOGY = ("quotient-exact", "quotient-catalytic", "quotient-banded-data-catalytic-work")
#: Of those three, the two whose family combination names `backend: "catalytic"`. The LABELS refuse; the
#: same combinations as EXPLICIT reference-topology designs price, and differentiate.
CATALYTIC_COMBINATIONS = ("quotient-catalytic", "quotient-banded-data-catalytic-work")
#: Refused by `full_run_design` itself: they select a rotation or phase-product backend, not a QFT. Two of
#: these also select `split_compare`; they are refused on the selector, which is checked first.
REFUSED_BY_DESIGN = ("quotient-fallback-data-catalytic-work", "quotient-step2-fallback",
                     "split-step4-kicks", "split-step2-fallback-step4-kicks")
#: No profile label prices in this release. Kept as an explicit empty tuple so the fact is stated, not implied.
PRICES = ()

#: The per-family choices the explicit path carries, in the research record's per-family choice shape.
RS_EXACT = {"backend": "ross_selinger", "target": "exact"}
BANDED = {"backend": "ross_selinger", "target": "approximate", "max_error": "25e-5"}

#: The compiled document the verifier prints for a non-reference profile: the measured (semiclassical) schema,
#: because `PolicyMain.lean` refuses a full-run selection unless the saved readout IS semiclassical,
#: plus the selection object it merges in.
MEASURED_SCHEMA = "forshor.policy-submission-semiclassical/v1"


def document(profile=None):
    doc = {"schema": MEASURED_SCHEMA, "submitted_readout": "semiclassical"}
    if profile is not None:
        doc["submitted_full_run"] = {"schema": "forshor.full-run-selection/v1", "profile": profile}
    return doc


def explicit_document(qft, modmul="reference"):
    """The compiled document carrying an EXPLICIT per-family design BODY -- `full_run_design`'s second path,
    in the research record's wire shape, inside the envelope the recorded exports already use.

    This is the path that prices in this release. Every profile LABEL refuses on its multiplier topology
, so the chain tests that need a design to thread drive it from here instead."""
    return {"schema": MEASURED_SCHEMA, "submitted_readout": "semiclassical",
            "submitted_full_run": {"schema": "forshor.full-run-selection/v1",
                                   "design": {"schema": "forshor.design-local/v1",
                                              "modmul": modmul, "qft": qft}}}


def design_from(doc):
    """The two conversion steps of the chain, in order, exactly as `request._build_request` runs them."""
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
@pytest.mark.parametrize("qft", [{"data": BANDED}, {"work": BANDED}, {"data": BANDED, "work": RS_EXACT}],
                         ids=["data-banded", "work-banded", "data-banded-work-exact"])
def test_a_compiled_document_prices_a_panel_carrying_a_qft_projection(policy, epoch_id, qft):
    """THE DELIVERABLE. Start at the verifier's compiled document, end at a panel whose full-run price carries
    a `qft` projection, through all four production steps.

    RE-POINTED: this was parametrized over `PRICES`, the profile labels that used to
    convert. the topology gate refuses every label, so the deliverable is exercised over EXPLICIT
    reference-topology designs instead -- which is both what ships and a strictly wider test, since the
    explicit path prices arbitrary per-family combinations rather than three fixed ones.

    Fails if the hook at `pricing.py:52-54` is reverted (then `view["full"]` has no `qft` and the panel has
    none either), if `mapping.full_run_of` stops carrying `qft`, or if either conversion step stops working."""
    doc = explicit_document(qft)

    selection = full_run_design.selection_of(doc)
    assert selection["design"]["modmul"] == "reference"
    design = full_run_design.design_of(selection)
    assert design is not None and set(design) <= set(qft_overlay.FAMILIES)

    views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"], design=design)
    projection = views["full"]["full"]["qft"]
    assert projection["pricingRule"] == qft_overlay.RULE == "qft-design-axis-v3"
    assert projection["epoch"] == qft_overlay.EPOCH == "ab1f8887b1b50e56"
    assert projection["ruleSha256"] == qft_overlay.rule_fingerprint()
    assert set(projection["families"]) == set(design)

    built = panel(views["full"], epoch_id, "full")
    assert built["fullRun"]["qft"] == projection, "the panel must carry the projection the pricer produced"


@pytest.mark.slow
def test_an_all_exact_design_is_bit_identical_to_the_no_selection_baseline(policy, epoch_id):
    """THE REGRESSION GUARD. `target: "exact"` means "leave this family's QFT exactly as the engine priced it"
    (the native template, no substitution), never "substitute the exact ladder" -- substituting
    the exact Draper ladder at w = 4095 takes (37_598_400, 178_191) to (33_533_953, 8_289_333), a 14.8x
    rotation-driven blow-up in T. If `qft_overlay.project` ever starts substituting on an exact target this
    test goes red.

    RE-POINTED. This drove `quotient-exact`, which the topology gate now refuses. The
    guard it encodes is about the EXACT TARGET, not about that label, so it is restated over an all-exact
    EXPLICIT design and nothing is lost -- both halves are now checked, at the two layers that own them:

      1. THE CONVERTER collapses an all-exact design to None ("exact means untouched": a design in which no
         family asks for an approximate target asks for nothing at all), so the request takes the engine's
         untouched default path and the panels are the recorded 792464f bytes exactly.
      2. THE OVERLAY, driven through the production hook with that same all-exact mapping handed to
         `price_views` directly -- which is what the converter would have passed before the collapse --
         still reports `identity: True`, substitutes nothing, and reproduces the engine's census as the
         SAME float; and the priced panel differs from the baseline panel in NOTHING except the added
         `qft` evidence block. This is the assertion that actually pins the no-substitution rule; the
         collapse above would otherwise hide a regression in it.

    That last point is the honest form of "bit-identical" once a panel can carry the projection at all: no
    PRICE moves, and the only new bytes are an evidence block saying nothing was substituted. A plain hash
    compare could not distinguish "nothing moved" from "two things moved and cancelled".
    """
    all_exact = {"data": RS_EXACT, "work": RS_EXACT}

    # 1. the converter: all-exact asks for nothing, so the default path and the recorded bytes.
    assert design_from(explicit_document(all_exact)) is None
    baseline = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"], design=None)
    assert "qft" not in baseline["full"]["full"]

    # 2. the overlay, through the production hook: the mapping is still the identity, so the collapse
    #    above hides nothing.
    priced = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"], design=all_exact)
    engine_price, projection = baseline["full"]["full"], priced["full"]["full"]["qft"]

    assert projection["identity"] is True and projection["substituted"] == []
    assert all(f["target"] == "exact" and f["construction"] == "native" and f["maxError"] is None
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
    boards, against the hashes recorded at 792464f."""
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


@pytest.mark.parametrize("profile", REFUSED_BY_TOPOLOGY)
def test_the_three_qft_only_profiles_refuse_on_their_multiplier_topology(profile):
    """THE TOPOLOGY GATE. These three name only QFT families, so they once
    converted to a bare per-family mapping -- silently dropping the `modmul: quotient_stream` they also
    select. The topology drives both the T cost and the qubit base, and `qft_overlay` substitutes into the
    engine's frozen REFERENCE census, so that conversion priced a quotientStream run on reference numbers.

    The refusal must NAME the profile and the topology. Fails if the gate is weakened to let a profile label
    price again."""
    with pytest.raises(ValueError) as caught:
        design_from(document(profile))
    message = str(caught.value)
    assert profile in message, "a refusal that does not name the profile is not actionable"
    assert "quotient_stream" in message
    assert full_run_design.PROFILE_TOPOLOGY[profile] == "quotient_stream"


def test_every_profile_label_refuses_and_the_explicit_design_prices(policy=None):
    """THE PROFILE POSITION, kept as an assertion so it cannot rot.

    ALL SEVEN non-reference profiles refuse -- the four selector profiles at `full_run_design`'s `BEYOND_QFT`
    check, the three QFT-only ones at the topology gate -- and `reference` alone converts, to None. What prices
    is the explicit per-family design on the reference topology, which takes arbitrary combinations of the
    QFT families.

    Fails the moment a profile label starts converting again, which is the signal to rewrite this."""
    assert set(full_run_design.PROFILES) == {"reference", *REFUSED_BY_TOPOLOGY, *REFUSED_BY_DESIGN}
    assert set(full_run_design.QFT_ONLY) == set(REFUSED_BY_TOPOLOGY)
    assert PRICES == (), "no profile label prices in this release"

    # Every non-reference label refuses; `reference` is the only one that converts, and it converts to None.
    for profile in (*REFUSED_BY_TOPOLOGY, *REFUSED_BY_DESIGN):
        with pytest.raises(ValueError):
            design_from(document(profile))
    assert design_from(document("reference")) is None
    assert design_from(document()) is None

    # Every profile label selects a non-reference topology; `reference` names none at all. This is WHY they
    # all refuse, stated once, from the converter's own table.
    assert all(full_run_design.PROFILE_TOPOLOGY[p] in ("quotient_stream", "split_compare")
               for p in (*REFUSED_BY_TOPOLOGY, *REFUSED_BY_DESIGN))
    assert full_run_design.PROFILE_TOPOLOGY["reference"] is None

    # And the explicit reference-topology path does price: an approximate target converts to a real design.
    design = design_from(explicit_document({"data": BANDED}))
    assert design == {"data": BANDED}
    plan = qft_overlay.parse_design(design)
    assert plan and all(target == "approximate" for _backend, target, _max_error in plan.values())


@pytest.mark.slow
@pytest.mark.parametrize("named", CATALYTIC_COMBINATIONS)
def test_the_catalytic_designs_price_a_differentiated_full_run(policy, epoch_id, named):
    """THE DIFFERENTIATED PRICE. The catalytic axis is priced, so a catalytic design reaches a real price
    through the same four production steps. It must differ from the engine's own price, must carry a catalyst block with all
    three of its charges, and must charge the shared register to Q.

    The two catalytic PROFILE LABELS are refused by the topology gate: they also select
    `modmul: quotient_stream`, and the overlay prices against the frozen REFERENCE census. What the labels
    named was a per-family QFT COMBINATION attached to a quotientStream topology, and only the topology is
    unhonourable -- so the identical combination is driven here as an EXPLICIT reference-topology design,
    taken from the converter's own `QFT_ONLY` table so the two cannot drift.

    Fails if the catalytic arm is reverted (back to a refusal, or to the Ross-Selinger default), if the
    catalyst register stops being charged to the qubit total, or if the panel stops carrying the block."""
    design = design_from(explicit_document(full_run_design.QFT_ONLY[named]))
    assert design is not None
    assert any(choice["backend"] == "catalytic" for choice in design.values())

    views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"], design=design)
    full, projection = views["full"]["full"], views["full"]["full"]["qft"]
    catalyst = projection["catalyst"]

    assert projection["identity"] is False
    assert projection["T"] != full["T"], "a catalytic design must not price at the engine's own number"
    assert projection["catalysed"] == sorted(f for f, c in design.items() if c["backend"] == "catalytic")
    # All three charges present and nonzero.
    assert catalyst["b"] >= qft_overlay.B_MIN and float(projection["budget"]["prepBudget"].split("/")[0]) > 0
    assert catalyst["registerQubits"] == catalyst["b"]                            # the shared register
    assert projection["qubitsPeak"] == full["qubits_peak"] + catalyst["b"] + projection["workspaceQubits"]
    assert catalyst["prepT"] > 0.0 and catalyst["T"] == catalyst["prepT"]         # the preparation
    assert all(projection["families"][f]["construction"] == "mergedTwiddle" for f in projection["catalysed"])

    built = panel(views["full"], epoch_id, "full")
    assert built["fullRun"]["qft"] == projection, "the panel must carry the projection the pricer produced"


def test_the_explicit_design_prices_every_combination_the_profile_labels_named(policy=None):
    """The other half of the release-decision fact: what REFUSES is the label, not the design.

    Under the topology gate no profile label prices. At the level of the pricing: all three per-family COMBINATIONS those labels named still
    convert and price as EXPLICIT reference-topology designs, with the two catalytic ones selecting the
    catalytic backend and `quotient-exact`'s all-exact combination collapsing to the engine's untouched
    default ("exact means untouched"). So nothing about the PRICING was lost when the labels were refused;
    only the quotientStream topology they also carried was.

    Fails if the explicit path stops honouring any combination the qualified labels named."""
    for named, qft in full_run_design.QFT_ONLY.items():
        design = design_from(explicit_document(qft))
        if named in CATALYTIC_COMBINATIONS:
            plan = qft_overlay.parse_design(design)
            assert plan and any(backend == "catalytic" for backend, _t, _m in plan.values())
        else:
            # quotient-exact's combination is all-exact, so it asks for nothing: the engine's own price.
            assert design is None, named
            assert all(choice["backend"] == "ross_selinger" and choice["target"] == "exact"
                       for choice in qft.values())


# ------------------------------------------------------------------ the engine is never asked for a design


def test_the_chain_never_asks_the_engine_for_a_design(monkeypatch):
    """Pins the merge resolution. `evaluation/engine` is an epoch input, `price_full` still has its
    792464f signature `(P, n, delta, detail, rule, negation)`, and the `price_full(..., design=design)` raised
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
    # RE-POINTED: driven from the EXPLICIT reference-topology design, since the topology gate
    # refuses every profile label. What is pinned -- the design reaches the overlay and the engine's pricer
    # is called exactly as it was at 792464f -- is unchanged.
    design = design_from(explicit_document({"data": BANDED}))
    views = pricing.price_views(FakePolicy(), "t", "1e-2", ["full"], design=design)

    assert calls == [{"detail": True}], "the full-run pricer is called exactly as it was at 792464f"
    assert seen == [design], "the design reaches the overlay instead"
    assert views["full"]["full"]["qft"] == {"ok": True}


@pytest.mark.parametrize("delta", ["3e-4", "1e-4", "1e-6"])
def test_a_design_is_refused_at_every_delta_but_1e_2(delta):
    """The delta guard. `request._build_request` scopes the design to `mapping.DESIGN_DELTA`, so a
    design-bearing document completes a publish request, and this guard stays exactly as it is. It stays a precondition on
    the caller rather than a silent drop; `test_design_price_plumbing.py` pins the scoping and the byte
    identity of the other three panels. Fails if the 1e-2 guard in `price_views` is relaxed."""
    class FakePolicy:
        mode, bands = "PhaseProduct", ()

        def with_widths(self, _):
            return self

    with pytest.raises(ValueError, match="delta 1e-2"):
        pricing.price_views(FakePolicy(), "t", delta, ["full"],
                            design=design_from(explicit_document({"data": BANDED})))


# ------------------------------------------------- the QFT design and a Step 2 backend share one budget


def test_a_qft_design_and_a_step_2_fallback_cannot_be_asked_for_together(policy=None):
    """A Step 2 B3 backend priced by the Step 2 costing module in `export_v2` (the file read below) would
    overlap this chain SEMANTICALLY, because both re-divide the same run-wide synthesis pool
    `sto_currency.ROTATION_FRACTION * delta` (measured: ROTATION_FRACTION = 0.5, so the pool is 1/200 at
    delta 1e-2). The relationship, established from the code:

    1. THAT MODULE DOES NOT TAKE ITS OWN SHARE. It contains no reference to `sto_currency`,
       `ROTATION_FRACTION`, `rotation_eps` or `ts_per_rotation`. It receives `eps` -- "the per-site
       half-diamond" -- as a PARAMETER from its caller, and its `budget_seconds` is a wall-clock budget,
       not an error budget. It also has no production caller anywhere in the tree: its two entry points are
       referenced only by its own test. So there is no live composition, and it cannot double-spend on its
       own.

    2. BUT THE POOL HAS NO SLACK, so a second spender WOULD double-spend. The overlay spends the pool
       EXACTLY: the families' allowances and the gradient's preparation are carved out of it, and what is
       left is divided over the whole remaining rotation census (`qft_overlay.price_design`, whose ledger
       sums to at most the pool). Every rotation in the run is already allocated. Step 2's phase-product
       rotations are part of that same census, so pricing them a SECOND time against their own per-site eps
       would charge the same budget twice and hand the submission free error.

    3. SO THE COMBINATION IS REFUSED, and already was, by name, at three independent places. This test pins
       all three together with the reason, so none can be relaxed without confronting the arithmetic above.
       Two methods that each re-divide one nonlinear budget cannot be composed by picking a precedence.
    """
    import re
    from pathlib import Path as _Path

    # 1. That module reads none of the currency, and nothing in production calls it.
    source = _Path(qft_overlay.__file__).parent.joinpath("sampled_fallback.py").read_text()
    for name in ("sto_currency", "ROTATION_FRACTION", "rotation_eps", "ts_per_rotation"):
        assert name not in source, f"sampled_fallback now reads {name}: the budgets may no longer be independent"
    production = [p for p in _Path(qft_overlay.__file__).parent.glob("*.py")
                  if p.name not in ("sampled_fallback.py",)]
    for path in production:
        text = path.read_text()
        assert "sampled_fallback" not in text, \
            f"{path.name} now calls the sampled fallback: the double-spend analysis above must be redone"

    # 2. The profile that selects a Step 2 backend is refused by name, at the design step.
    with pytest.raises(ValueError) as profile_refusal:
        design_from(document("quotient-step2-fallback"))
    assert "step2 := fallback" in str(profile_refusal.value)

    # 3. An explicit design naming a phase-product step is refused, naming the step.
    for step in ("step1", "step2", "step4", "step5"):
        body = {"schema": "forshor.design-local/v1", "modmul": "reference",
                "qft": {"data": BANDED}, "phase_product": {step: {"backend": "fallback"}}}
        with pytest.raises(ValueError) as explicit_refusal:
            full_run_design.design_of({"schema": "forshor.full-run-selection/v1", "design": body})
        assert step in str(explicit_refusal.value)

    # 4. And the overlay refuses the selector fields outright, so neither path can reach a price.
    #    All four phase-product steps, the same set the explicit path refuses, so each is refused by name as
    #    a phase-product step rather than as an unrecognised family.
    assert qft_overlay.OUT_OF_SCOPE == ("step1", "step2", "step4", "step5")
    assert set(qft_overlay.OUT_OF_SCOPE) == set(full_run_design.PHASE_STEPS), \
        "the overlay's out-of-scope selectors and the explicit path's phase steps must be the same set"
    for field in qft_overlay.OUT_OF_SCOPE:
        with pytest.raises(qft_overlay.QftOverlayRefused, match=re.escape(field)):
            qft_overlay.parse_design({"data": BANDED, field: {"backend": "fallback"}})
