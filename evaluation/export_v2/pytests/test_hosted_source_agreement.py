"""The receipt<->source agreement check, lifted out of `request.rehearse_qualified_request` into the hosted path.

`rehearse_qualified_request` (export_v2/request.py) refuses a receipt whose `leanSha256` does not hash its own
`leanSource`. Its docstring forbids giving it a production caller, so the CHECK is lifted here instead, into
`intake_v2.publish_verification`, where the source does come from the trusted GitHub run.

Why the hosted path needs it. `publish_verification` applies the source gates (`check_legacy_full_run_source`,
`refuse_unqualified_design`) to `v["leanSource"]`, while `archive_policy.archive_policy` compares `v["leanSha256"]` AND
`v["leanSource"]` against the file fetched from the pull request's head commit. If the receipt's two source fields
disagree, the gate was applied to text that is not the submission. The archive catches that -- but only when the publish
job runs with `--archive`; `publish_verification` is reachable with `archive_write=None`, and then nothing compared the
two. The receipt is untrusted input ("nothing in `v` is trusted"), so its internal disagreement is refused here, before
the gate's verdict is relied on.

This is the agreement check only. No publication authority moves: the trusted-source comparison itself stays in
`archive_policy`, and nothing here binds a readout or grants a measured export a publishing route.

Each test names, in a comment, the production change that would make it fail. The engine and Lean are NOT needed: the
check runs before `engine.load()`.
"""
import hashlib
from pathlib import Path
import sys

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL.parent))
import intake_v2  # noqa: E402

POLICY = "import ForShor.ShorVerification.Framework.Policy\n"
SOURCE = f"{POLICY}namespace PolicySubmission\ndef points := #[1, 2, 3]\nend PolicySubmission\n"
OTHER = f"{POLICY}namespace PolicySubmission\ndef points := #[4, 5, 6]\nend PolicySubmission\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def publish(receipt, tmp_path):
    return intake_v2.publish_verification(receipt, login="tester", pull=1, url="http://unused",
                                          token="unused", workdir=tmp_path)


def test_a_receipt_whose_hash_does_not_match_its_source_is_refused(tmp_path):
    # FAILS IF: the `leanSha256`/`leanSource` agreement check added to `intake_v2.publish_verification` is removed. Without
    # it this receipt reaches the schema check and refuses with "not a policy verification" instead, i.e. the source gates
    # above it were applied to bytes that the archive would later reject as not being the head commit's file.
    with pytest.raises(intake_v2.Refused, match="Lean source does not match its own digest"):
        publish({"leanSource": SOURCE, "leanSha256": sha(OTHER)}, tmp_path)


def test_the_refusal_is_a_format_refusal_and_emits_no_receipt(tmp_path):
    # FAILS IF: the new check raises a bare ValueError (or an Incomplete) instead of `Refused("format", ...)`. A refusal
    # must stay a refusal: it carries a stage, it is not a resource stop, and it must not return a success payload.
    with pytest.raises(intake_v2.Refused, match="Lean source does not match its own digest") as caught:
        publish({"leanSource": SOURCE, "leanSha256": sha(OTHER)}, tmp_path)
    assert caught.value.stage == "format"
    assert not list(tmp_path.rglob("*.json")), "a refused receipt must not write evidence or a result"


def test_an_agreeing_receipt_still_passes_the_source_gate(tmp_path):
    # FAILS IF: the new check is written so that it refuses a receipt whose fields DO agree (for example by comparing the
    # hash of the encoded text against the text itself, or by requiring `leanSha256` unconditionally). An agreeing source
    # must get no further objection here and fall through to the schema check, exactly as before this change.
    with pytest.raises(intake_v2.Refused, match="not a policy verification"):
        publish({"leanSource": SOURCE, "leanSha256": sha(SOURCE)}, tmp_path)


def test_a_receipt_with_no_hash_is_unchanged_by_the_lift(tmp_path):
    # FAILS IF: the new check starts demanding `leanSha256`. A policy-JSON submission carries no Lean source at all and a
    # receipt may legitimately omit the hash; the archive is what requires it to be present and to equal the head commit's
    # file. Widening this check into a presence requirement would refuse submissions that are not malformed.
    for receipt in ({"leanSource": SOURCE}, {}):
        with pytest.raises(intake_v2.Refused, match="not a policy verification"):
            publish(receipt, tmp_path)


def test_the_check_runs_before_the_engine_and_the_publisher(tmp_path, monkeypatch):
    # FAILS IF: the agreement check is placed after `engine.load()`/`epoch.model_of()` or after the first publisher call.
    # A malformed receipt must be refused without spending a build or reaching the network, like the other source gates.
    monkeypatch.setattr(intake_v2, "request_json", lambda *a, **kw: pytest.fail("a malformed receipt must not hit the publisher"))
    with pytest.raises(intake_v2.Refused, match="Lean source does not match its own digest"):
        publish({"leanSource": SOURCE, "leanSha256": sha(OTHER)}, tmp_path)


def test_the_mismatch_is_caught_even_when_the_source_gate_accepts(tmp_path):
    # FAILS IF: the agreement check is made to depend on the source gate's verdict (for example by only checking the digest
    # on a source the gates rejected). The two are independent: a source that passes `refuse_unqualified_design` can still
    # fail to hash to the receipt's digest, and that must still refuse.
    assert intake_v2.refuse_unqualified_design(SOURCE) is None       # the gate itself accepts this text
    with pytest.raises(intake_v2.Refused, match="Lean source does not match its own digest"):
        publish({"leanSource": SOURCE, "leanSha256": sha(OTHER)}, tmp_path)
