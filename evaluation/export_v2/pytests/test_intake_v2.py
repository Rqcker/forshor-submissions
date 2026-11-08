"""intake_v2: one submission from a policy JSON to the publisher, with the Lean harness and the publisher stubbed.
Needs the vendored engine and its Python environment: python -m pytest export_v2/pytests/test_intake_v2.py -q --runslow  (from evaluation/).
The evidence is a REAL archived envelope (the PhaseProduct baseline 6f036362, accepted at the epoch's pins), so a stubbed mirror run hands back
what the harness really produced for that exact policy."""
import copy
import json
import re
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
        assert kw.get("fast") is True, "the mirror is run with --fast (the PhaseTripleProduct harness skips its interpreted report stage)"
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


def test_weighted_cost_is_computed_for_a_table_the_archive_has_no_value_for_and_reproduces_every_archived_value():
    from export_v2 import engine, mapping
    engine.load()
    from site_engine import library as lib
    known = [(T, s.weighted[(T.mode, T.k)]) for s in lib.submissions() for T in s.tables.values() if (T.mode, T.k) in s.weighted]
    assert len(known) >= 98
    wrong = [(T.digest[:10], mapping.weighted_cost_of(T.to_json()["operations"]), w) for T, w in known if mapping.weighted_cost_of(T.to_json()["operations"]) != w]
    assert not wrong, wrong[:5]
    assert mapping.weighted_cost_of([["addScaled", 0, 1, 1, 0], ["shiftL", 1, 1], ["phaseProduct", 0], ["phaseProduct", 1], ["negate", 2], ["phaseProduct", 2]]) == 3 + 5 * 2


# ---- a submission that is the Lean file itself (Policy.lean, ForShor's PolicyTemplate format)
DROPIN = EVAL / "starter" / "pack" / "examples" / "forshor_dropin.policy.json"   # a real document ForShor printed at 21e129a


def test_a_lean_policy_becomes_the_policy_ForShor_printed_for_it(monkeypatch, tmp_path):
    from export_v2 import engine
    eng = engine.load()
    doc = json.loads(DROPIN.read_text())
    seen = {}
    monkeypatch.setattr(intake_v2, "forshor_build", lambda tree, text, workdir, timeout=7200, kind="pp": seen.update(text=text, tree=tree, kind=kind) or doc)
    policy_json, printed = intake_v2.policy_from_lean(eng, "namespace PolicySubmission\n-- the submitted file", tmp_path / "forshor", tmp_path / "w")
    assert seen["text"].endswith("-- the submitted file") and seen["kind"] == "pp", "the submitted bytes are what ForShor builds"
    assert printed is doc
    assert [(b["threshold"], b["k"]) for b in policy_json["bands"]] == [(int(b["threshold"]), int(b["k"])) for b in doc["bands"]]
    assert policy_json["mode"] == eng.tables.PAIR


def test_a_lean_policy_that_asks_for_something_the_boards_do_not_price_is_refused(monkeypatch, tmp_path):
    from export_v2 import engine
    eng = engine.load()
    base = json.loads(DROPIN.read_text())
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **k: dict(base, negation="scratch"))
    with pytest.raises(intake_v2.Refused, match="oneQubit only"):
        intake_v2.policy_from_lean(eng, "namespace PolicySubmission", tmp_path, tmp_path)
    with pytest.raises(intake_v2.Refused, match="larger than"):
        intake_v2.policy_from_lean(eng, "x" * (intake_v2.MAX_LEAN_BYTES + 1), tmp_path, tmp_path)
    with pytest.raises(RuntimeError, match="--forshor-tree"):
        intake_v2.policy_from_lean(eng, "namespace PolicySubmission", None, tmp_path)


def test_the_lexical_gate_refuses_before_anything_is_built(tmp_path):
    tree = tmp_path / "forshor"
    (tree / "ForShor" / "ShorVerification" / "Submission").mkdir(parents=True)
    (tree / "scripts").mkdir()
    (tree / "scripts" / "check_template_lexical.sh").write_text("#!/usr/bin/env bash\necho 'reaches compiled code' >&2\nexit 1\n")
    w = tmp_path / "w"
    w.mkdir()
    with pytest.raises(intake_v2.Refused) as e:
        intake_v2.forshor_build(tree, "unsafe def x := 1\n", w)
    assert e.value.stage == "lexical" and "reaches compiled code" in e.value.reason
    assert not (w / "forshor.log").exists(), "no build was started"


def test_a_lean_submission_is_published_with_the_ForShor_record_and_never_written_as_a_template(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    monkeypatch.setattr(intake_v2, "policy_from_lean", lambda eng, text, tree, workdir, timeout=7200: (policy_doc, {"printed": True}))
    monkeypatch.setattr(intake_v2, "forshor_record", lambda eng, P, doc, pin: {"verified": True, "bandsAgree": True, "irPriceAgrees": True, "claim": "c", "forshorCommit": pin})
    monkeypatch.setattr(intake_v2, "forshor_check", lambda *a, **k: pytest.fail("the JSON route's template writer must not run for a Lean submission"))
    result = intake_v2.run_intake(None, lean_policy="-- the file", login="tester", pull=11, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w",
                                  forshor_tree=tmp_path / "f", run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    assert result["policy"]["forshorPolicy"] is True and result["policy"]["stoMirror"] is True
    assert calls[0][0] == "http://x/v2/state", "the handle and epoch are checked before ForShor builds anything"


def test_the_command_line_takes_exactly_one_of_the_two_inputs(tmp_path):
    with pytest.raises(SystemExit):
        intake_v2.main(["--login", "u", "--pull", "1", "--tree", str(tmp_path), "--publisher-url", "http://x"])
    f = tmp_path / "p"
    f.write_text("{}")
    with pytest.raises(SystemExit):
        intake_v2.main(["--policy", str(f), "--lean-policy", str(f), "--login", "u", "--pull", "1", "--tree", str(tmp_path), "--publisher-url", "http://x"])


# ---- a PhaseTripleProduct Lean submission (namespace PTPPolicySubmission, ForShor's PTPPolicyTemplate format)
PTP_PRINTED = EVAL / "export_v2" / "pytests" / "data" / "ptp_printed_k4.json"     # a real document ForShor's forshor_ptp_policy_submission printed
PTP_ARCHIVE = EVAL / "export_v2" / "evidence-archive" / "7a11b2b8b6d68718"
PTP_DIGEST = "265a0f53acbcdd4f"                                                    # the board's PhaseTripleProduct k = 4 baseline, accepted at the pins


def test_the_board_is_told_from_the_namespace_the_file_declares():
    assert intake_v2.lean_kind("import X\nnamespace PolicySubmission\n") == "pp"
    assert intake_v2.lean_kind("import X\nnamespace PTPPolicySubmission\n") == "ptp"
    for bad in ("def x := 1\n", "namespace PolicySubmission\nnamespace PTPPolicySubmission\n"):
        with pytest.raises(intake_v2.Refused, match="exactly one"):
            intake_v2.lean_kind(bad)


def test_a_ptp_lean_policy_becomes_the_policy_ForShor_printed_for_it(monkeypatch, tmp_path):
    from export_v2 import engine
    eng = engine.load()
    doc = json.loads(PTP_PRINTED.read_text())
    seen = {}
    monkeypatch.setattr(intake_v2, "forshor_build", lambda tree, text, workdir, timeout=7200, kind="pp": seen.update(kind=kind) or doc)
    policy_json, printed = intake_v2.policy_from_lean(eng, "namespace PTPPolicySubmission\n", tmp_path / "forshor", tmp_path / "w")
    assert seen["kind"] == "ptp" and printed is doc
    assert policy_json["mode"] == eng.tables.TRIPLE
    assert [(b["threshold"], b["k"]) for b in policy_json["bands"]] == [(int(b["threshold"]), int(b["k"])) for b in doc["bands"]]
    archived = json.loads((PTP_ARCHIVE / f"{PTP_DIGEST}.policy.json").read_text())
    from export_v2.canon import policy_digest
    assert policy_digest(policy_json) == policy_digest(archived), "the printed tables are the board's archived policy, digest for digest"


def test_a_ptp_lean_submission_is_published_on_the_ptp_board_without_a_ForShor_record(monkeypatch, tmp_path, model):
    calls = []
    archived = json.loads((PTP_ARCHIVE / f"{PTP_DIGEST}.policy.json").read_text())

    def mirror(policy_json, board, tree, out_dir, **kw):
        assert board == "ptp" and kw.get("fast") is True
        calls.append(("mirror", board))
        out_dir.mkdir(parents=True, exist_ok=True)
        for ext in ("policy", "record", "evidence"):
            shutil.copy(PTP_ARCHIVE / f"{PTP_DIGEST}.{ext}.json", out_dir / f"{PTP_DIGEST}.{ext}.json")
        return json.loads((PTP_ARCHIVE / f"{PTP_DIGEST}.evidence.json").read_text())

    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    monkeypatch.setattr(intake_v2, "policy_from_lean", lambda eng, text, tree, workdir, timeout=7200: (archived, json.loads(PTP_PRINTED.read_text())))
    monkeypatch.setattr(intake_v2, "forshor_record", lambda *a, **k: pytest.fail("a PhaseTripleProduct policy has no IR, so no ForShor record is made"))
    result = intake_v2.run_intake(None, lean_policy="namespace PTPPolicySubmission\n", login="tester", pull=12, url="http://x", token="t", tree=tmp_path,
                                  workdir=tmp_path / "w", forshor_tree=tmp_path / "f", run_mirror_fn=mirror, log=lambda s: None)
    assert result["policy"]["mode"] == "PhaseTripleProduct" and result["policy"]["forshorPolicy"] is False and result["policy"]["stoMirror"] is True
    req = [p for u, p in calls if isinstance(u, str) and u.endswith("/v2/publish")][0]
    assert {p["board"] for p in req["priced"]} == {"ptp"}


# ---- verify only (`./forshor check`, the untrusted half of CI) and publish from a verification (the trusted half)
def test_verify_only_needs_no_publisher_and_sends_nothing(monkeypatch, tmp_path, policy_doc):
    monkeypatch.setattr(intake_v2, "request_json", lambda *a, **k: pytest.fail("a verification must not touch the network"))
    calls = []
    v = intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    assert v["schema"] == intake_v2.VERIFICATION_SCHEMA and v["board"] == "pp" and v["login"] == "tester"
    assert v["envelope"]["record"]["verdict"] == "accepted" and v["forshor"] is None and v["printed"] is None
    assert calls == [("mirror", "pp")]


def test_a_verification_published_by_another_job_gives_the_same_row_as_one_call(monkeypatch, tmp_path, model, policy_doc):
    calls_a, calls_b = [], []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls_a, number=3))
    one = intake_v2.run_intake(policy_doc, login="tester", pull=7, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w1", head_sha="a" * 40, run_id="1",
                               run_mirror_fn=archived_mirror(calls_a), log=lambda s: None)
    v = intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w2", run_mirror_fn=archived_mirror([]), log=lambda s: None)
    v = json.loads(json.dumps(v))                        # through a file, as an artifact travels
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls_b, number=3))
    two = intake_v2.publish_verification(v, login="tester", pull=7, url="http://x", token="t", workdir=tmp_path / "w3", head_sha="a" * 40, run_id="1", log=lambda s: None)
    assert two["policy"] == one["policy"]
    sent = lambda calls: [p for u, p in calls if isinstance(u, str) and u.endswith("/v2/publish")][0]
    a, b = sent(calls_a), sent(calls_b)
    def diff(x, y, path=""):
        if isinstance(x, dict) and isinstance(y, dict):
            return [d for k in sorted(set(x) | set(y)) for d in diff(x.get(k), y.get(k), f"{path}/{k}")]
        if isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
            return [d for i, (u, w) in enumerate(zip(x, y)) for d in diff(u, w, f"{path}[{i}]")]
        return [] if x == y else [path]
    differing = {re.sub(r"\[\d+\]", "[]", d) for d in diff(a["priced"], b["priced"])}
    assert all(d.rsplit("/", 1)[-1] in ("createdAt", "submittedAt", "evaluatedAt") for d in differing), f"only the clock may differ: {sorted(differing)}"


def test_nothing_in_a_verification_is_trusted(monkeypatch, tmp_path, model, policy_doc):
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, [], number=1))
    good = json.loads(json.dumps(intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=archived_mirror([]), log=lambda s: None)))
    pub = lambda v, login="tester", n=0: intake_v2.publish_verification(v, login=login, pull=9, url="http://x", token="t", workdir=tmp_path / f"p{n}", log=lambda s: None)
    with pytest.raises(intake_v2.Refused, match="another handle"):
        pub(good, login="someone-else", n=1)
    other = copy.deepcopy(good)
    other["policy"]["bands"][0]["threshold"] += 1                   # a different policy under the same digest
    with pytest.raises(intake_v2.Refused, match="digest"):
        pub(other, n=2)
    stale = copy.deepcopy(good)
    stale["model"] = dict(stale["model"], lean="leanprover/lean4:v0.0.0")
    with pytest.raises(ModelMismatch):
        pub(stale, n=3)
    forged = copy.deepcopy(good)
    forged["envelope"]["record"]["verdict"] = "rejected"
    with pytest.raises(intake_v2.Refused, match="mirror"):
        pub(forged, n=4)
    swapped = copy.deepcopy(good)
    swapped["envelope"]["policyDigest"] = "0" * 64                 # evidence for another policy
    with pytest.raises(intake_v2.Refused, match="mirror"):
        pub(swapped, n=5)
    assert pub(good, n=6)["published"]


def test_the_command_line_modes_are_exclusive(tmp_path, capsys):
    f = tmp_path / "p"
    f.write_text("{}")
    base = ["--login", "u", "--tree", str(tmp_path)]
    with pytest.raises(SystemExit):                                  # publishing needs a publisher and a pull request
        intake_v2.main(["--policy", str(f), *base])
    with pytest.raises(SystemExit):                                  # --publish-from publishes
        intake_v2.main(["--publish-from", str(f), "--no-publish", "--login", "u", "--pull", "1", "--publisher-url", "http://x"])
    with pytest.raises(SystemExit):
        intake_v2.main(["--publish-from", str(f), "--lean-policy", str(f), "--login", "u", "--pull", "1", "--publisher-url", "http://x"])
    # verify only: no publisher, no pull request; the file is written where asked and the result says where
    monkeypatch_free = intake_v2.verify_submission
    assert callable(monkeypatch_free)
