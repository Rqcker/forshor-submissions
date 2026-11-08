"""intake_v2: one submission from a policy JSON to the publisher, with the Lean harness and the publisher stubbed.
Needs the vendored engine and its Python environment: python -m pytest export_v2/pytests/test_intake_v2.py -q --runslow  (from evaluation/).
The evidence is a REAL archived envelope (the PhaseProduct baseline 6f036362, accepted at the epoch's pins), so a stubbed mirror run hands back
what the harness really produced for that exact policy."""
import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
import intake_v2  # noqa: E402
from export_v2 import epoch  # noqa: E402
from publish_v2 import ModelMismatch, ReservedHandle  # noqa: E402

pytestmark = pytest.mark.slow
ARCHIVE = EVAL / "export_v2" / "evidence-archive" / "494185cf666f9752"
DIGEST = "6f036362302e1ecd"


@pytest.fixture(scope="module")
def model():
    return epoch.model_of()


@pytest.fixture(scope="module")
def policy_doc():
    return json.loads((ARCHIVE / f"{DIGEST}.policy.json").read_text())


def fake_publisher(model, calls, number=1):
    state = {"epoch": "e", "model": model, "baseGeneration": "1", "nextNumbers": {"tester": number}, "completedEvaluations": {}, "submissionIds": ["f" * 40],
             "publication": {"updatedAt": "2026-11-08T00:00:00Z", "snapshotId": "s" * 64}}   # the real /v2/state has no archiveCommit

    def request_json(url, token, payload=None):
        calls.append((url, payload))
        if url.endswith("/v2/state"):
            return state
        if url.endswith("/results/v2/dashboard.json"):
            return {"publication": {"archiveCommit": "a" * 40}}
        return {"snapshotId": "n" * 64, "published": True}
    return request_json


def archived_mirror(calls):
    def run(policy_json, board, tree, out_dir, **kw):
        calls.append(("mirror", board))
        out_dir.mkdir(parents=True, exist_ok=True)
        for ext in ("policy", "record", "evidence"):
            shutil.copy(ARCHIVE / f"{DIGEST}.{ext}.json", out_dir / f"{DIGEST}.{ext}.json")
        return json.loads((ARCHIVE / f"{DIGEST}.evidence.json").read_text())
    return run


def test_a_policy_is_published_with_the_mirror_evidence_and_the_next_name(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls, number=3))
    result = intake_v2.run_intake(policy_doc, login="tester", pull=7, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w",
                                  run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    assert result["published"] and result["policy"]["stoMirror"] and not result["policy"]["forshorPolicy"]
    sent = [p for u, p in calls if u.endswith("/v2/publish")]
    assert len(sent) == 1
    req = sent[0]
    assert req["schema"] == "forshor.publish/v2" and req["baseGeneration"] == "1" and req["model"] == model
    boards = sorted({(p["board"], p["delta"]) for p in req["priced"]})
    assert boards == sorted((b, d) for b in ("full", "pp") for d in ("1e-2", "3e-4", "1e-4", "1e-6")), "a PhaseProduct policy is priced on the PP and full boards at four deltas"
    assert {p["row"]["name"] for p in req["priced"]} == {"tester_sub3"}
    assert all(p["panel"]["verification"]["stoMirror"]["verdict"] == "accepted" for p in req["priced"])
    assert all(p["panel"]["verification"]["forshorPolicy"] is None for p in req["priced"])
    assert req["evaluation"]["githubLogin"] == "tester" and req["evaluation"]["pullRequestUrl"].endswith("/pull/7")
    assert "f" * 40 in req["archive"]["acceptedIds"] and req["archive"]["policyId"] == req["evaluation"]["headSha"]
    assert calls[0][0] == "http://x/v2/state", "the live state is read before any compute"


def test_in_production_the_accepted_ids_and_archive_commit_are_the_archive_branchs(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    seen = []
    def view():
        seen.append(1)
        return (["1" * 40, "2" * 40], "c" * 40)
    intake_v2.run_intake(policy_doc, login="tester", pull=9, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w",
                         run_mirror_fn=archived_mirror(calls), archive_view=view, log=lambda s: None)
    req = [p for u, p in calls if u.endswith("/v2/publish")][0]
    assert req["archive"]["acceptedIds"] == ["1" * 40, "2" * 40] and req["evaluation"]["archiveCommit"] == "c" * 40
    assert len(seen) >= 2, "read before pricing and again by the recovery loop"


def test_the_archive_commit_is_read_from_the_published_dashboard_or_must_be_given(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    intake_v2.run_intake(policy_doc, login="tester", pull=2, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w1",
                         run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    assert [p for u, p in calls if u.endswith("/v2/publish")][0]["evaluation"]["archiveCommit"] == "a" * 40
    fake = fake_publisher(model, calls)
    monkeypatch.setattr(intake_v2, "request_json", lambda u, t, p=None: (_ for _ in ()).throw(intake_v2.RetryPublication("down")) if "dashboard" in u else fake(u, t, p))
    with pytest.raises(RuntimeError, match="--archive-commit"):
        intake_v2.run_intake(policy_doc, login="tester", pull=3, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w2",
                             run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    intake_v2.run_intake(policy_doc, login="tester", pull=4, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w3",
                         run_mirror_fn=archived_mirror(calls), archive_commit="d" * 40, log=lambda s: None)


def test_the_reserved_handle_and_a_foreign_epoch_are_refused_before_the_lean_run(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    with pytest.raises(ReservedHandle):
        intake_v2.run_intake(policy_doc, login="forshor", pull=1, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w1",
                             run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    other = copy.deepcopy(model)
    other["forshor"]["pin"] = "0" * 40
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(other, calls))
    with pytest.raises(ModelMismatch):
        intake_v2.run_intake(policy_doc, login="tester", pull=1, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w2",
                             run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    assert not any(c[0] == "mirror" for c in calls), "no Lean run for either"


def test_a_policy_the_harness_does_not_accept_is_refused_with_its_stage_and_nothing_is_published(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))

    def rejecting(policy_json, board, tree, out_dir, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        env = json.loads((ARCHIVE / f"{DIGEST}.evidence.json").read_text())
        env["record"] = {**env["record"], "verdict": "rejected", "rejection": {"stage": "threshold", "reason": "threshold too low", "detail": "band 1 ..."}}
        (out_dir / f"{DIGEST}.evidence.json").write_text(json.dumps(env))
        return env
    with pytest.raises(intake_v2.Refused) as e:
        intake_v2.run_intake(policy_doc, login="tester", pull=1, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=rejecting, log=lambda s: None)
    assert e.value.stage == "threshold" and "threshold too low" in e.value.reason
    assert not any(u.endswith("/v2/publish") for u, _ in calls if isinstance(u, str))


def test_extra_keys_and_a_declared_ladder_are_dropped_before_anything_runs(policy_doc):
    noisy = {**policy_doc, "declared_ladder": [[2048, 6]], "widths": "scan", "note": "x"}
    assert intake_v2.normalise(noisy) == intake_v2.normalise(policy_doc)
    assert set(intake_v2.normalise(noisy)) <= {"name", "mode", "bands"}
    with pytest.raises(intake_v2.Refused):
        intake_v2.normalise({"policies": [policy_doc, policy_doc]})
    with pytest.raises(intake_v2.Refused):
        intake_v2.normalise({"mode": "PhaseProduct", "bands": []})


def test_forshor_record_needs_the_printed_bands_to_be_the_submitted_bands_and_the_same_price(model):
    from export_v2 import engine
    eng = engine.load()
    pack = EVAL / "starter" / "pack" / "examples" / "forshor_dropin.policy.json"
    doc = json.loads(pack.read_text())
    P = intake_v2.policy_from_document(eng, doc)
    rec = intake_v2.forshor_record(eng, P, doc, model["forshor"]["pin"])
    assert rec == {"verified": True, "bandsAgree": True, "irPriceAgrees": True, "claim": "forshor-policy-submission", "forshorCommit": model["forshor"]["pin"]}
    other = intake_v2.normalise(json.loads((ARCHIVE / f"{DIGEST}.policy.json").read_text()))
    with pytest.raises(intake_v2.Refused) as e:
        intake_v2.forshor_record(eng, eng.tables.Policy.from_json(other), doc, model["forshor"]["pin"])
    assert "not the submitted bands" in e.value.reason


def test_the_command_line_maps_outcomes_to_exit_codes(monkeypatch, tmp_path, policy_doc):
    pol = tmp_path / "p.json"
    pol.write_text(json.dumps(policy_doc))
    base = ["--policy", str(pol), "--login", "tester", "--pull", "1", "--tree", str(tmp_path), "--publisher-url", "http://x", "--workdir", str(tmp_path / "w")]
    monkeypatch.setattr(intake_v2, "run_intake", lambda *a, **k: {"published": True})
    assert intake_v2.main(base) == 0
    monkeypatch.setattr(intake_v2, "run_intake", lambda *a, **k: (_ for _ in ()).throw(intake_v2.Refused("threshold", "too low")))
    assert intake_v2.main(base) == 1
    monkeypatch.setattr(intake_v2, "run_intake", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the harness wrote no record")))
    assert intake_v2.main(base) == 2
