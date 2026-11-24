"""The compiled full-run selection -> the engine's `design` argument.

Which production change each test pins is named in the test's docstring. The tests split in two:

* seam tests (no engine): they inject a fake pricer into `pricing.price_views`, because the vendored
  `fullalg.price_full` does not take `design=` until lane WM lands the engine side of the contract.
* real-engine tests (marked slow): they price the real policy
  `pytests/data/real/1bc92ac6e1e82ba3.policy.json` through the vendored engine and assert the panels are
  byte-identical to 792464f. They never pass a design, because the engine cannot accept one yet; what they
  prove is that the default path is untouched.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import canon, full_run_design, mapping, pricing, request  # noqa: E402

DATA = Path(__file__).parent / "data"

# The shape the verifier's compiled document actually uses, transcribed from the recorded fixtures:
# readiness/pre-push/overnight-native-compiler-matrix/<profile>/checked-export.json carries, at the top level,
#   "submitted_full_run": {"schema": "forshor.full-run-selection/v1", "profile": "<label>"}
# and readiness/pre-push/overnight-native-legacy-replay.json's sibling export for `explicit-reference` carries no
# such key at all. The same pair is asserted by readiness/pre-push/audit_native_fullrun.py:131 and
# readiness/pre-push/replay_native_quotient_step2.py:96.
RECORDED = {
    "quotient-exact", "quotient-catalytic", "quotient-banded-data-catalytic-work",
    "quotient-fallback-data-catalytic-work", "quotient-step2-fallback", "split-step4-kicks",
    "split-step2-fallback-step4-kicks",
}


def selection(profile):
    return {"schema": "forshor.full-run-selection/v1", "profile": profile}


# --------------------------------------------------------------------------------------------------
# the compiled key and shape
# --------------------------------------------------------------------------------------------------

def test_the_module_reads_the_key_and_schema_the_compiled_document_uses():
    """Fails if full_run_design.KEY / SELECTION_SCHEMA drift from the recorded compiled exports."""
    assert full_run_design.KEY == "submitted_full_run"
    assert full_run_design.SELECTION_SCHEMA == "forshor.full-run-selection/v1"
    assert set(full_run_design.PROFILES) == RECORDED | {"reference"}


def test_a_document_without_the_key_has_no_selection_and_an_explicit_null_is_refused():
    """Fails if full_run_design.selection_of stops treating key PRESENCE (not its value) as the test, the rule
    the shipped intake gate uses (starter/pack/harness/policy_options.py:10)."""
    assert full_run_design.selection_of({"schema": "forshor.policy-submission-semiclassical/v1"}) is None
    assert full_run_design.selection_of({}) is None
    assert full_run_design.selection_of({"submitted_full_run": selection("quotient-exact")}) == selection("quotient-exact")
    with pytest.raises(ValueError, match="names no selection"):
        full_run_design.selection_of({"submitted_full_run": None})


# --------------------------------------------------------------------------------------------------
# the conversion
# --------------------------------------------------------------------------------------------------

def test_no_selection_and_the_explicit_reference_are_both_no_design_at_all():
    """Fails if full_run_design.design_of returns an all-defaults record ({}) for `reference` instead of None.
    QFT-SURFACE-MAPPING.md section 1.2: `reference` is compiledSelection = none, NOT an empty Design."""
    assert full_run_design.design_of(None) is None
    got = full_run_design.design_of(selection("reference"))
    assert got is None and got != {}


def test_the_pure_qft_profiles_convert_to_the_contract_mapping():
    """Fails if full_run_design.PROFILES stops reproducing readiness/pre-push/audit_native_fullrun.py:20-46,
    the existing research<->production bridge (EXACT / CAT / BAND and the CASES table)."""
    exact = {"backend": "ross_selinger", "target": "exact"}
    catalytic = {"backend": "catalytic", "target": "approximate", "max_error": "25e-5"}
    banded = {"backend": "ross_selinger", "target": "approximate", "max_error": "25e-5"}
    assert full_run_design.design_of(selection("quotient-exact")) == {"data": exact, "work": exact}
    assert full_run_design.design_of(selection("quotient-catalytic")) == {"data": catalytic, "work": catalytic}
    assert full_run_design.design_of(selection("quotient-banded-data-catalytic-work")) == {"data": banded, "work": catalytic}


def test_a_converted_design_is_a_fresh_mapping_the_caller_cannot_poison():
    """Fails if design_of hands out the module's own table (a caller mutating it would change every later price)."""
    first = full_run_design.design_of(selection("quotient-exact"))
    first["data"] = {"backend": "catalytic", "target": "exact"}
    assert full_run_design.design_of(selection("quotient-exact")) == {
        "data": {"backend": "ross_selinger", "target": "exact"}, "work": {"backend": "ross_selinger", "target": "exact"}}


def test_every_converted_design_satisfies_the_engine_contract():
    """Fails if a typo in full_run_design.PROFILES emits a backend, target, max_error or family the contract
    does not define -- the engine would RAISE on it, and a silent default is never acceptable."""
    for profile in full_run_design.PROFILES:
        design = full_run_design.design_of(selection(profile)) if profile in full_run_design.QFT_ONLY else None
        if design is None:
            continue
        assert set(design) <= {"data", "work", "scratch", "exponent"}
        assert "exponent" not in design, "semiclassical readout replaces qft.exponent in all seven bundles"
        for choice in design.values():
            assert choice["backend"] in ("ross_selinger", "catalytic")
            if choice["target"] == "exact":
                assert set(choice) == {"backend", "target"}
            else:
                assert choice["target"] == "approximate" and set(choice) == {"backend", "target", "max_error"}
                units, _, scale = choice["max_error"].partition("e-")
                assert units.isdigit() and scale.isdigit()


@pytest.mark.parametrize("profile, names", [
    ("quotient-fallback-data-catalytic-work", "fallback rotation backend"),
    ("quotient-step2-fallback", "Step 2"),
    ("split-step4-kicks", "Step 4"),
    ("split-step2-fallback-step4-kicks", "Step 2"),
])
def test_a_selection_beyond_the_qft_families_is_refused_and_names_itself(profile, names):
    """Fails if full_run_design.design_of prices a selection the design contract cannot express -- the release
    rule is that unsupported scopes are reported, not priced as defaults."""
    with pytest.raises(ValueError) as caught:
        full_run_design.design_of(selection(profile))
    assert profile in str(caught.value) and names in str(caught.value)


@pytest.mark.parametrize("bad", [
    selection("quotient-reference"), selection("Quotient-Exact"), selection(""),
    {"schema": "forshor.full-run-selection/v2", "profile": "quotient-exact"},
    {"profile": "quotient-exact"}, {"schema": "forshor.full-run-selection/v1"},
    {"schema": "forshor.full-run-selection/v1", "profile": None},
    "quotient-exact", ["quotient-exact"], 7,
])
def test_an_unknown_or_malformed_selection_is_refused(bad):
    """Fails if full_run_design.design_of falls back to a default for anything it does not recognise."""
    with pytest.raises(ValueError, match="full-run selection"):
        full_run_design.design_of(bad)


# --------------------------------------------------------------------------------------------------
# the pricing call (seam: an injected pricer, because the engine has no `design=` until lane WM lands)
# --------------------------------------------------------------------------------------------------

class FakePolicy:
    mode = "PhaseProduct"
    bands = ()

    def with_widths(self, _):
        return self


@pytest.fixture
def seam(monkeypatch):
    """`price_views` with the engine replaced: records exactly what reached the full-run pricer."""
    calls = []

    class Fullalg:
        @staticmethod
        def price_full(P, n, delta, **kw):
            calls.append({"n": n, "delta": delta, **kw})
            return {"usage": [], "tables_used": []}

    class Tables:
        PAIR = "PhaseProduct"
        TRIPLE = "PhaseTripleProduct"

    class PolicyView:
        @staticmethod
        def view(P, n, delta, title):
            return {"levels": [], "bands": []}

        @staticmethod
        def table_view(t):
            return t

    class Eng:
        fullalg, tables, policy_view = Fullalg, Tables, PolicyView

    monkeypatch.setattr(pricing.engine, "load", lambda: Eng)
    return calls


def test_no_selection_passes_no_design_argument_at_all(seam):
    """Fails if pricing.price_views starts sending `design=None` to price_full: the vendored engine has no such
    parameter until lane WM lands, and the default path must call it exactly as 792464f did."""
    pricing.price_views(FakePolicy(), "t", "1e-2", ["full"])
    pricing.price_views(FakePolicy(), "t", "1e-2", ["full"], design=None)
    assert seam == [{"n": pricing.N, "delta": 0.01, "detail": True}] * 2
    assert all("design" not in call for call in seam)


def test_a_design_is_threaded_to_the_full_run_pricer(seam):
    """Fails if pricing.price_views drops the design argument on the floor."""
    design = full_run_design.design_of(selection("quotient-banded-data-catalytic-work"))
    pricing.price_views(FakePolicy(), "t", "1e-2", ["full"], design=design)
    assert seam == [{"n": pricing.N, "delta": 0.01, "detail": True, "design": design}]


def test_a_design_is_refused_at_every_delta_but_1e_2(seam):
    """Fails if pricing.price_views prices a design outside the only precision the QFT surface is qualified at
    (QFT-SURFACE-MAPPING.md section 1: delta = 1e-2 only)."""
    design = full_run_design.design_of(selection("quotient-exact"))
    for delta in ("3e-4", "1e-4", "1e-6"):
        with pytest.raises(ValueError, match="delta 1e-2"):
            pricing.price_views(FakePolicy(), "t", delta, ["full"], design=design)
    assert seam == []


def test_a_design_never_reaches_a_board_that_is_not_the_full_run(seam):
    """Fails if pricing.price_views lets a full-run design through on the pp board, where price_full is not called
    at all and the design would be silently ignored."""
    with pytest.raises(ValueError, match="full order-finding board"):
        pricing.price_views(FakePolicy(), "t", "1e-2", ["pp"], design=full_run_design.design_of(selection("quotient-exact")))
    assert seam == []


def test_a_refused_selection_prices_nothing_and_builds_no_request(monkeypatch):
    """Fails if request._build_request converts the compiled selection after the evidence lookup or after the
    pricing loop: a refusal must not price a board or emit any part of a request."""
    monkeypatch.setattr(request.engine, "load", lambda: pytest.fail("a refused selection must not load the engine"))
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a refused selection must not be priced"))
    args = dict(model=None, store=None, head_sha="a" * 40, pull_number=1, github_login="t", run_id="1",
                submitted_at="x", evaluated_at="y", archive_commit="b" * 40, accepted_ids=[], evaluation_key="e",
                base_generation="1", number=1)
    with pytest.raises(ValueError, match="quotient-step2-fallback"):
        request._build_request({}, full_run=selection("quotient-step2-fallback"), **args)


# --------------------------------------------------------------------------------------------------
# byte identity against 792464f (the real engine)
# --------------------------------------------------------------------------------------------------

# Recorded at 792464f with the HEAD signature `price_full(P, N, delta, detail=True)`, for the real policy
# pytests/data/real/1bc92ac6e1e82ba3.policy.json at delta 1e-2. See the lane report for the capture command.
HEAD_PANELS = {"full": "998ad142a47fae05466541a3a92e0851cb86bac91876ce409ec4a98b3e0b19e3",
               "pp": "c83bf7a532a639d1b16d2e1e9b86b26e2bef36e8db96137e2d259a72d527179f"}


@pytest.mark.slow
@pytest.mark.parametrize("document", [
    {"schema": "forshor.policy-submission-semiclassical/v1"},
    {"schema": "forshor.policy-submission-semiclassical/v1",
     "submitted_full_run": {"schema": "forshor.full-run-selection/v1", "profile": "reference"}},
], ids=["no-selection", "explicit-reference"])
def test_a_real_policy_with_no_selection_or_the_explicit_reference_prices_byte_identically_to_792464f(document):
    """THE non-negotiable test. Fails if the design threading perturbs the default price by one byte -- for
    instance if price_views started sending `design={}` for the explicit reference, or if design_of turned
    `reference` into an all-defaults record."""
    from export_v2 import engine

    design = full_run_design.design_of(full_run_design.selection_of(document))
    assert design is None
    model = json.loads((DATA / "golden.json").read_text())["model"]
    policy_json = json.loads((DATA / "real/1bc92ac6e1e82ba3.policy.json").read_text())
    policy = engine.load().tables.Policy.from_json(policy_json)
    views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["pp", "full"], design=design)
    for board in ("pp", "full"):
        panel = mapping.panel_of(views[board], epoch=canon.epoch_of(model), row_id="a" * 40, board=board,
                                 delta="1e-2", verification={"stoMirror": None, "forshorPolicy": None})
        assert canon.sha256_hex(panel) == HEAD_PANELS[board], board
