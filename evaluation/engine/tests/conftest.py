import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import site_engine  # noqa: E402,F401  (binds the vendored Qualtran before anything else)


def pytest_configure(config):
    config.addinivalue_line("markers", "lean: runs a real submission through the Lean verifier (slow; needs a built STO tree)")
    config.addinivalue_line("markers", "legacy_negation: pins numbers priced before modular swap 001 (negation = a w-bit constant-one register "
                                       "plus a Cuccaro adder); the test runs with that negation, which must reproduce them bit for bit")


#: Tests of the demo's own page, backend and run.sh. Those are not vendored here (the public page lives in forshor-site),
#: so they cannot run; they are skipped by exact node id so that no other failure can be skipped by accident. The page
#: wording rules they encode (e.g. "never says the cost is proved in Lean") must be re-checked against the new page.
NEEDS_DEMO_PAGE = frozenset({
    "tests/test_stage13.py::test_both_lean_harnesses_use_the_group_wrapper_8gb_and_wait_for_a_slot",
    "tests/test_sto_mirror.py::test_full_board_hooks_the_seeds_with_trust_and_benchmarks",
    "tests/test_sto_mirror.py::test_policy_panel_of_a_record_shows_its_policy_trust",
    "tests/test_sto_mirror.py::test_seeded_board_lists_every_seed_with_trust_provenance_and_badge[PP-23]",
    "tests/test_sto_mirror.py::test_seeded_board_lists_every_seed_with_trust_provenance_and_badge[PTP-8]",
    "tests/test_sto_mirror.py::test_the_page_shows_the_policy_trust_and_the_seeded_badge",
    "tests/test_trust.py::test_cost_endpoint_returns_trust",
    "tests/test_trust.py::test_every_dom_id_the_script_reads_exists",
    "tests/test_trust.py::test_faq_note_matches_the_trust_model",
    "tests/test_trust.py::test_full_submitted_endpoint_prices_every_record_live",
    "tests/test_trust.py::test_js_label_trust_matches_trust_tag",
    "tests/test_trust.py::test_meta_exposes_the_trust_map_and_wording",
    "tests/test_trust.py::test_participate_has_no_tabs_left",
    "tests/test_trust.py::test_the_hero_no_longer_claims_the_paper_reproduction_for_the_headline",
    "tests/test_trust.py::test_the_leaderboard_rule_and_subtitle_are_not_hidden",
    "tests/test_trust.py::test_the_page_does_not_describe_forshors_eval_gap",
    "tests/test_trust.py::test_the_page_never_says_the_cost_is_proved_in_lean",
    "tests/test_trust.py::test_the_page_no_longer_says_there_is_no_lean",
    "tests/test_trust.py::test_the_page_shows_no_dp_library_and_no_frequency_plots",
})


def pytest_collection_modifyitems(config, items):
    import pytest

    skip = pytest.mark.skip(reason="needs the sto_site demo's frontend/backend/run.sh, which are not vendored")
    for item in items:
        if item.nodeid in NEEDS_DEMO_PAGE:
            item.add_marker(skip)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _legacy_negation(request):
    """Run a `legacy_negation` test with the pre-swap negation, and leave nothing behind. `fullalg._price` is cached on its arguments but
    reads the conventions from module globals, so the cache is cleared on both sides of the change (clearing only before would let a
    legacy price leak into the next test, and clearing only after would let an earlier price leak into this one)."""
    if request.node.get_closest_marker("legacy_negation") is None:
        yield
        return
    from site_engine import fullalg as fa
    names = ("HEADLINE", "GRID_LEAF", "LITERAL_LEAN")
    saved = {n: getattr(fa, n) for n in names}
    saved_default = fa.cc.DEFAULT_NEGATE
    for n in names:
        setattr(fa, n, saved[n].with_(negate="legacy"))
    fa.cc.DEFAULT_NEGATE = "legacy"        # swap 002: a price call names its method (`negate_impl(None)` reads this default at call time)
    fa._price.cache_clear()
    try:
        yield
    finally:
        for n in names:
            setattr(fa, n, saved[n])
        fa.cc.DEFAULT_NEGATE = saved_default
        fa._price.cache_clear()
