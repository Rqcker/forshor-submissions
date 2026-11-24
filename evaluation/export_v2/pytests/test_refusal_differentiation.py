"""A refused policy must name WHICH cause refused it.

Owner priority 2026-11-24: "being able to handle and correctly differentiate incorrect policies
from correct policies is the priority". A message shared by several causes cannot tell a
PARTICIPANT ERROR (rewrite the file) from an UNSUPPORTED-BUT-VALID request (use the dedicated
route) — those need different participant actions — so each class gets its own, anchored reason.

Every test below names the production change that would make it fail. Matching is by
anchored, class-level patterns on the *cause* clause, never on incidental wording.
Census and cause enumeration: ForShor_Integration/readiness/pre-push/REFUSAL-CENSUS.md
(collisions C1, C2 for this module).
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))                       # evaluation/
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "starter" / "pack" / "harness"))

from policy_options import check_coherent_export, check_no_full_run_export  # noqa: E402

#: The anchors the rest of the repository already matches on; they must survive every rewording.
#: starter/test_pack_validation.py and export_v2/pytests/test_readout_overlay.py assert these,
#: and ForShor_Integration/readiness/pre-push/bind_native_split_mirror.py:73 asserts the first.
FULL_RUN_ANCHOR = "compiled submitted_full_run metadata requires dedicated nonpublishing full-run qualification"
COHERENT_ANCHOR = "is not supported by the coherent pricing route"

#: Cause classes. Each pattern is anchored on a word that names the CAUSE, not the rule.
QUALIFIED_ROUTE = re.compile(r"readout[- ]qualification route", re.I)     # unsupported-but-valid: use the other route
UNKNOWN_READOUT = re.compile(r"not a readout (?:mode )?this release offers", re.I)   # participant error
LEGACY_KEY = re.compile(r"legacy `?readout`? key", re.I)                  # wrong key


# --- C2: `check_coherent_export`, one message for three causes --------------------------------

def test_a_semiclassical_export_is_told_to_use_the_readout_qualification_route():
    # Production change that would fail this: collapsing the three branches of
    # policy_options.check_coherent_export back into one `readout` message that does not name the
    # cause. A semiclassical export is a VALID request on the wrong route, so the participant must
    # be sent to the local readout-qualification route, not told to edit the policy.
    with pytest.raises(ValueError) as refusal:
        check_coherent_export({"schema": "forshor.policy-submission/v1", "submitted_readout": "semiclassical"})
    reason = str(refusal.value)
    assert COHERENT_ANCHOR in reason                       # the anchor the rest of the repo matches
    assert QUALIFIED_ROUTE.search(reason), reason
    assert not UNKNOWN_READOUT.search(reason), reason      # not a participant error
    assert not LEGACY_KEY.search(reason), reason
    assert "semiclassical" in reason, reason               # the value the participant wrote


def test_an_unknown_readout_value_is_told_it_is_not_offered_and_is_not_sent_to_the_other_route():
    # Production change that would fail this: making every non-coherent `submitted_readout` report
    # the readout-qualification route. "banana" is a participant error: no route accepts it, so
    # sending the participant to the qualification harness would waste a run.
    with pytest.raises(ValueError) as refusal:
        check_coherent_export({"schema": "forshor.policy-submission/v1", "submitted_readout": "banana"})
    reason = str(refusal.value)
    assert COHERENT_ANCHOR in reason
    assert UNKNOWN_READOUT.search(reason), reason
    assert not QUALIFIED_ROUTE.search(reason), reason
    assert "banana" in reason, reason


def test_a_legacy_readout_key_names_the_key_and_not_a_readout_mode():
    # Production change that would fail this: folding the legacy `readout` key back into the
    # `submitted_readout` branch. The key is the cause here; there is no selected mode to report.
    with pytest.raises(ValueError) as refusal:
        check_coherent_export({"schema": "forshor.policy-submission/v1", "readout": "coherent"})
    reason = str(refusal.value)
    assert COHERENT_ANCHOR in reason
    assert LEGACY_KEY.search(reason), reason
    assert not QUALIFIED_ROUTE.search(reason), reason
    assert not UNKNOWN_READOUT.search(reason), reason


def test_the_three_coherent_route_causes_do_not_share_a_message():
    # Production change that would fail this: any rewording that makes two of the three causes
    # produce the same string again. This is the collision itself (census C2), asserted directly.
    reasons = []
    for document in ({"submitted_readout": "semiclassical"}, {"submitted_readout": "banana"}, {"readout": "coherent"}):
        with pytest.raises(ValueError) as refusal:
            check_coherent_export({"schema": "forshor.policy-submission/v1", **document})
        reasons.append(str(refusal.value))
    assert len(set(reasons)) == 3, reasons


def test_compiled_submitted_design_keeps_its_own_message():
    # Production change that would fail this: routing `submitted_design` through the readout
    # branch. It is a separate cause and the starter pack's validation asserts this exact text.
    with pytest.raises(ValueError, match=r"^compiled submitted_design metadata is not supported"):
        check_coherent_export({"schema": "forshor.policy-submission/v1", "submitted_design": {}})


# --- C1: `check_no_full_run_export`, one message for every shape of the key --------------------

@pytest.mark.parametrize("value,expected", [
    ({"schema": "forshor.full-run-selection/v1", "profile": "quotient-exact"}, "quotient-exact"),
    ({"schema": "forshor.full-run-selection/v1", "profile": "split-step2-fallback-step4-kicks"},
     "split-step2-fallback-step4-kicks"),
])
def test_a_full_run_export_names_the_profile_it_selected(value, expected):
    # Production change that would fail this: dropping the profile from
    # policy_options.check_no_full_run_export's message. The operator triaging a refused export
    # cannot otherwise tell which of the seven profiles the submission asked for.
    with pytest.raises(ValueError) as refusal:
        check_no_full_run_export({"schema": "forshor.policy-submission/v1", "submitted_full_run": value})
    reason = str(refusal.value)
    assert FULL_RUN_ANCHOR in reason
    assert re.search(rf"profile {re.escape(repr(expected))}", reason), reason


@pytest.mark.parametrize("value", [None, "malformed", {"modmul": "split_compare"}, 0, []])
def test_a_full_run_key_without_a_profile_says_so_rather_than_naming_one(value):
    # Production change that would fail this: reading the profile without checking the shape, or
    # dropping the no-profile branch. `null` and a malformed value are a different cause from a
    # real profile selection and must not be reported as one. Key presence still refuses, exactly
    # as before: the ACCEPT/REFUSE decision is unchanged, only the reason is specific.
    with pytest.raises(ValueError) as refusal:
        check_no_full_run_export({"schema": "forshor.policy-submission/v1", "submitted_full_run": value})
    reason = str(refusal.value)
    assert FULL_RUN_ANCHOR in reason
    assert re.search(r"carries a `?submitted_full_run`? key with no profile", reason), reason


def test_absent_and_non_dict_documents_are_untouched():
    # Production change that would fail this: making check_no_full_run_export refuse a document
    # that does not carry the key, or refuse a non-dict at all (census finding F1 records that a
    # non-dict is deliberately NOT refused here; changing that changes the outcome class).
    check_no_full_run_export({"schema": "forshor.policy-submission/v1"})
    check_no_full_run_export(None)
    check_no_full_run_export("not a document")
