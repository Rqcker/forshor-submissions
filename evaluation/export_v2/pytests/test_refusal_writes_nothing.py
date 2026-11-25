"""A refusal writes nothing: no archive entry may survive a publication that pricing refuses.

THE DEFECT. `intake_v2.publish_verification` called `archive_write` BEFORE
`publish_v2_with_recovery` ran `price()`, so every refusal raised while pricing came after the entry had already been
written to `submission-results`. The reachable case is a measured receipt whose compiled document names a full-run
selection its own Lean source does not: `compiled_full_run` converts the selection (so the ordinary
`require_legacy_export` is skipped), the source gate passes (the source has no `submittedFullRunDesign`), the archive
is written -- with `design=` non-None when the selection is an explicit reference-topology body -- and only then does
`request.build_measured_request` refuse the document on `check_no_full_run_export`, unconditionally.

This fixes the ORDER only. The verdict for the inconsistent receipt is also EARLIER, not later: the measured route now admits a
selection only when it is bound to the source that declares it, so the receipt below is refused by name at the binding
gate, before pricing, and a refusal raised while pricing is pinned separately with a pricer that refuses.

WHAT IS REAL: the recorded measured compiled export and its source, `verify_submission`, `publish_verification`, the
real readout binding, the real measured pricer's refusal, and the real `archive_policy.write_policy` writing to a
checkout on disk. STOOD IN FOR: ForShor's build and the STO mirror, the publisher, and the trusted artifact fetch (the
inconsistent receipt IS the artifact here, which is the only way the case can arise).
"""
from copy import deepcopy
import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL / "starter" / "pack" / "harness"))
import archive_policy  # noqa: E402
import intake_v2  # noqa: E402
from export_v2 import canon, epoch, evidence, receipt_origin, request  # noqa: E402
from export_v2.receipt_origin import ReceiptOrigin  # noqa: E402
from policy_options import FULL_RUN_ROUTE  # noqa: E402

DATA = Path(__file__).parent / "data"
FROZEN_CLOCK = "2026-11-24T00:00:00Z"
HEAD = "a" * 40
RUN = "1"
PULL = 7
IDENTITY = {"schema": "forshor-verification/v1", "key": "k" * 64, "headSha": HEAD, "fingerprint": "f" * 64}
MIRROR = {"harness": "check_banded_policy", "widths": "exact", "verdict": "accepted", "recordSha256": "b" * 64,
          "claim": "policy-algebra-and-exact-widths"}

#: Two selections a receipt can carry that its source does not declare, both of which `compiled_full_run` converts:
#: an explicit reference-topology design body (converts to a real `design`, so the archive was told `design=` non-None)
#: and the explicit `reference` profile (converts to None, the same skipped gate with `design=None`).
INCONSISTENT = {
    "explicit-design": {"schema": "forshor.full-run-selection/v1",
                        "design": {"schema": "forshor.design-local/v1", "modmul": "reference",
                                   "qft": {"data": {"backend": "ross_selinger", "target": "approximate",
                                                    "max_error": "1e-6"}}}},
    "explicit-reference-profile": {"schema": "forshor.full-run-selection/v1", "profile": "reference"},
}


@pytest.fixture(scope="module")
def measured():
    compiled = json.loads(gzip.decompress((DATA / "readout_submitted_931bbf1.json.gz").read_bytes()))
    return compiled["document"], compiled["source"]


@pytest.fixture
def publisher(monkeypatch):
    model = epoch.model_of()
    sent = []
    state = {"epoch": "e", "model": model, "baseGeneration": "1", "nextNumbers": {"tester": 1},
             "completedEvaluations": {}, "submissionIds": ["f" * 40],
             "publication": {"updatedAt": FROZEN_CLOCK, "snapshotId": "s" * 64}}

    def request_json(url, token, payload=None):
        if url.endswith("/v2/state"):
            return state
        if url.endswith("/results/v2/dashboard.json"):
            return {"publication": {"archiveCommit": "a" * 40}}
        sent.append(payload)
        return {"snapshotId": "n" * 64, "published": True}

    monkeypatch.setattr(intake_v2, "request_json", request_json)
    monkeypatch.setattr(intake_v2, "_now", lambda: FROZEN_CLOCK)
    monkeypatch.setattr(evidence.EvidenceStore, "stomirror", lambda self, *a: (MIRROR, None))
    monkeypatch.setattr(evidence, "stomirror_from_envelope", lambda *a: (MIRROR, None))
    return SimpleNamespace(sent=sent)


def receipt_for(measured, monkeypatch, tmp_path, selection=None):
    """The trusted verifier job's measured receipt, stamped as the publish job receives it. With `selection`, the
    compiled document is altered AFTER verification to name a full-run selection the source does not -- the verifier
    itself refuses such a document, so this inconsistency can only arrive in an artifact, never be produced by one."""
    compiled, source = measured
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: compiled)
    v = intake_v2.verify_submission(None, lean_policy=source, login="tester", tree=tmp_path,
                                    workdir=tmp_path / "verify", forshor_tree=tmp_path,
                                    run_mirror_fn=lambda *a, **kw: {"record": {"verdict": "accepted"}},
                                    qualify_readout=True, log=lambda s: None)
    receipt = json.loads(json.dumps(v))
    if selection is not None:
        receipt["printed"] = {**deepcopy(receipt["printed"]), "submitted_full_run": deepcopy(selection)}
        # Self-consistent otherwise: the readout evidence digests the whole compiled document, so an artifact that
        # carries the altered document carries the evidence for it (`readout_overlay.bind_compiled_readout`).
        receipt["localReadoutQualification"]["evidence"]["irSha256"] = canon.sha256_hex(receipt["printed"])
        assert "submittedFullRunDesign" not in receipt["leanSource"], "the source names no selection"
    receipt[receipt_origin.STAMP] = {**IDENTITY, "runId": RUN, "runAttempt": 2, "pullRequest": PULL}
    return receipt


def origin_for(receipt):
    artifact = json.dumps({k: v for k, v in receipt.items() if k != receipt_origin.STAMP}).encode()
    return ReceiptOrigin(identity=lambda head: dict(IDENTITY, headSha=head), fetch=lambda *a: artifact)


def archive_into(checkout, order):
    """The production writer (`archive_policy.write_policy`) on a real checkout, recording when it was reached."""
    (checkout / archive_policy.INDEX_PATH).parent.mkdir(parents=True)

    def write(v, **kind):
        order.append(("archive", kind))
        archive_policy.write_policy(checkout, v, login="tester", pull=PULL, head_sha=HEAD, run_id=RUN,
                                    source=v["leanSource"], **kind)
        return {"path": f"{archive_policy.DIR}/{HEAD}", "commit": "c" * 40, "added": True}
    return write


def archived(checkout):
    entries = [p for p in archive_policy.read_index(checkout)["policies"] if p["policy_id"] == HEAD]
    return entries, (checkout / archive_policy.DIR / HEAD).exists()


def publish(receipt, tmp_path, write):
    return intake_v2.publish_verification(receipt, login="tester", pull=PULL, url="http://x", token="t",
                                          workdir=tmp_path / "publish", head_sha=HEAD, run_id=RUN,
                                          origin=origin_for(receipt), archive_write=write, log=lambda s: None)


@pytest.mark.parametrize("case", sorted(INCONSISTENT))
def test_an_unbound_selection_is_refused_by_name_and_leaves_no_archive_entry(case, measured, publisher, monkeypatch, tmp_path):
    """THE DEFECT'S RECEIPT. It was once refused at pricing with the entry already written. The measured route
    admits a full-run selection only when it is bound, so the receipt is refused where it is first wrong --
    its document names a selection its own source does not declare -- by name (`FULL_RUN_BINDING_REFUSAL`), before
    the pricer and before the writer. Production change that would fail this: admitting an unbound selection, or
    reaching the archive before ruling on it."""
    order = []
    checkout = tmp_path / "archive"
    receipt = receipt_for(measured, monkeypatch, tmp_path, INCONSISTENT[case])
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("an unbound selection prices nothing"))
    with pytest.raises(intake_v2.Refused, match=intake_v2.FULL_RUN_BINDING_REFUSAL) as refusal:
        publish(receipt, tmp_path, archive_into(checkout, order))
    assert refusal.value.stage == "format"
    assert archived(checkout) == ([], False), "a refusal writes nothing: no index entry and no submission folder"
    assert order == [], "the archive writer was never reached"
    assert publisher.sent == []


def test_a_publication_refused_at_pricing_leaves_no_archive_entry(measured, publisher, monkeypatch, tmp_path):
    """THE DEFECT ITSELF, kept: whatever the pricer refuses, it refuses with the archive untouched. The refusal is the
    standing full-run gate's message raised from inside pricing (the shape the old unconditional refusal had), so the
    test is about the ORDER and not about which gate fires. Production change that would fail this: writing the archive
    entry before the request is priced."""
    order = []
    checkout = tmp_path / "archive"

    def refuses(*a, **kw):
        raise ValueError(f"{FULL_RUN_ROUTE}: refused while pricing")

    monkeypatch.setattr(request, "build_measured_request", refuses)
    with pytest.raises(ValueError, match=FULL_RUN_ROUTE):
        publish(receipt_for(measured, monkeypatch, tmp_path), tmp_path, archive_into(checkout, order))
    assert archived(checkout) == ([], False) and order == [] and publisher.sent == []


def test_the_positive_control_the_same_receipt_without_the_selection_is_archived(measured, publisher, monkeypatch, tmp_path):
    """NON-VACUITY. The same receipt, the same writer and the same checkout, minus the inconsistent selection: the
    entry IS written, so an empty archive above is the refusal's doing and not a writer that can never write. The
    pricer is stood in for (the real one prices for minutes and is pinned elsewhere), and the order is asserted:
    priced, then archived, then sent -- the publisher still finds the head among the archive's accepted ids."""
    order = []
    checkout = tmp_path / "archive"
    real_send = intake_v2.request_json

    def priced(*a, **kw):
        order.append(("price", kw.get("design")))
        return {"schema": "forshor.publish/v2", "evaluationKey": "d" * 64, "baseGeneration": "1",
                "model": epoch.model_of(), "evaluation": {"headSha": HEAD}, "archive": {"policyId": HEAD},
                "submission": {"id": HEAD, "boards": ["pp", "full"], "verification": {}},
                "priced": [{"board": "pp", "delta": "1e-2", "row": {"name": "tester_sub0", "T": 1, "qubits": 1},
                            "panel": {}}]}

    def send(url, token, payload=None):
        if url.endswith("/v2/publish"):
            order.append(("send", None))
        return real_send(url, token, payload)

    monkeypatch.setattr(request, "build_measured_request", priced)
    monkeypatch.setattr(intake_v2, "request_json", send)
    result = publish(receipt_for(measured, monkeypatch, tmp_path), tmp_path, archive_into(checkout, order))
    assert result["published"] and result["archive"]["added"]
    entries, folder = archived(checkout)
    assert len(entries) == 1 and folder and entries[0]["readout"] == "semiclassical"
    assert order == [("price", None), ("archive", {"readout": "semiclassical", "primitive": None, "design": None}),
                     ("send", None)]
    assert len(publisher.sent) == 1


def test_a_stop_while_pricing_stays_incomplete_and_writes_nothing(measured, publisher, monkeypatch, tmp_path):
    """A timeout or resource stop during pricing is `Incomplete` -- never a refusal, never a pass -- and, now that
    pricing precedes the archive, it leaves no entry behind either: no success record of any kind."""
    order = []
    checkout = tmp_path / "archive"

    def stopped(*a, **kw):
        raise intake_v2.Incomplete("limits", "pricing exceeded its time budget")

    monkeypatch.setattr(request, "build_measured_request", stopped)
    with pytest.raises(intake_v2.Incomplete) as stop:
        publish(receipt_for(measured, monkeypatch, tmp_path), tmp_path, archive_into(checkout, order))
    assert stop.value.stage == "limits" and not isinstance(stop.value, intake_v2.Refused)
    assert archived(checkout) == ([], False) and order == [] and publisher.sent == []


def test_without_an_archive_the_pricer_runs_where_it_always_did(measured, publisher, monkeypatch, tmp_path):
    """The `archive_write=None` route (a local rehearsal, and `run_intake`) is untouched: pricing stays inside the
    recovery loop, after its already-published check, and is called once."""
    calls, priced = [], []
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: priced.append(kw) or {})
    monkeypatch.setattr(intake_v2, "publish_v2_with_recovery",
                        lambda metadata, model, state_of, price, refresh, send: calls.append(price) or {"published": True})
    result = publish(receipt_for(measured, monkeypatch, tmp_path), tmp_path, None)
    assert result["published"] and len(calls) == 1 and "archive" not in result
    assert priced == [], "nothing is priced outside the recovery loop when there is no archive to write"
