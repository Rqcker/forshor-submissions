"""archive_policy: the accepted policy goes to the `submission-results` branch before it is published. Runs with plain git (a local bare repository
stands in for GitHub): python -m pytest test_archive_policy.py -q  (from evaluation/)."""
import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive_policy as ap  # noqa: E402

HEAD = "ab" * 20
SOURCE = "namespace PolicySubmission\n-- the file\n"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, text=True, capture_output=True).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    """A bare `origin` whose `submission-results` branch already holds the table-generation index and one old entry."""
    bare = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", ap.BRANCH, str(seed)], check=True)
    (seed / "leaderboard").mkdir()
    (seed / "leaderboard/results.json").write_text(json.dumps({"schema_version": 3, "challenge": "table-generation", "policies": [{"policy_id": "c" * 40}]}))
    git(seed, "add", "-A")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "-q", "origin", ap.BRANCH)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    return bare, clone


def verification(source=SOURCE, digest="d" * 16, created="2026-11-08T10:00:00Z"):
    return {"digest": digest, "mode": "pair", "board": "pp", "policy": {"bands": []}, "createdAt": created, "envelope": {"record": {"verdict": "accepted"}},
            "leanSource": source, "leanSha256": ap.sha256(source)}


def archive(clone, v=None, head=HEAD, source=SOURCE, **kw):
    return ap.archive_policy(clone, v or verification(), login="tester", pull=7, head_sha=head, run_id="99", source=source, **kw)


def test_a_policy_is_pushed_with_its_source_and_a_separate_index(remote):
    bare, clone = remote
    out = archive(clone)
    assert out["added"] is True and out["path"] == f"results/policy-submissions/{HEAD}"
    assert git(bare, "rev-parse", ap.BRANCH) == out["commit"]
    show = lambda p: git(bare, "show", f"{ap.BRANCH}:{p}")
    assert show(f"{out['path']}/Policy.lean") + "\n" == SOURCE
    index = json.loads(show(ap.INDEX_PATH))
    assert [p["policy_id"] for p in index["policies"]] == [HEAD]
    assert index["policies"][0]["source"] == {"repository": ap.REPOSITORY, "pr_number": 7, "github_login": "tester", "run_id": "99", "created_at": "2026-11-08T10:00:00Z"}
    assert "leanSource" not in json.loads(show(f"{out['path']}/verification.json")), "the source is stored once, as Policy.lean"
    assert json.loads(show("leaderboard/results.json"))["policies"] == [{"policy_id": "c" * 40}], "the table-generation index is untouched"


def test_publish_v2_reads_the_accepted_ids_from_both_indexes(remote, monkeypatch):
    _, clone = remote
    archive(clone)
    import publish_v2
    monkeypatch.setattr(publish_v2, "refresh_archive", lambda path: (clone, "r" * 40))
    ids, revision = publish_v2._archive_view(clone)
    assert ids == sorted(["c" * 40, HEAD]) and revision == "r" * 40


def test_archiving_twice_is_a_no_op_and_changing_the_file_is_refused(remote):
    bare, clone = remote
    first = archive(clone)
    again = archive(clone)
    assert again["added"] is False and again["commit"] == first["commit"] == git(bare, "rev-parse", ap.BRANCH)
    other = "namespace PolicySubmission\n-- another\n"
    with pytest.raises(ap.ArchiveRefused, match="different policy"):
        archive(clone, verification(other, digest="e" * 16), source=other)


def test_a_source_that_is_not_the_file_at_the_head_commit_is_refused(remote):
    bare, clone = remote
    before = git(bare, "rev-parse", ap.BRANCH)
    with pytest.raises(ap.ArchiveRefused, match="head commit"):
        archive(clone, verification("namespace PolicySubmission\n-- forged\n"))     # the verification says one thing, GitHub's file another
    v = verification()
    v["leanSha256"] = "0" * 64
    with pytest.raises(ap.ArchiveRefused):
        archive(clone, v)
    assert git(bare, "rev-parse", ap.BRANCH) == before, "nothing was pushed"


def test_a_verification_without_lean_source_cannot_be_archived(remote):
    _, clone = remote
    v = verification()
    del v["leanSource"], v["leanSha256"]
    with pytest.raises(ap.ArchiveRefused):
        archive(clone, v)
    with pytest.raises(ap.ArchiveRefused, match="40-hex"):
        archive(clone, head="abc")


def test_a_competing_push_is_retried_from_the_new_tip(remote, tmp_path, monkeypatch):
    bare, clone = remote
    rival = tmp_path / "rival"
    subprocess.run(["git", "clone", "-q", str(bare), str(rival)], check=True)
    git(rival, "checkout", "-q", ap.BRANCH)
    other_head = "cd" * 20
    ap.write_policy(rival, verification("namespace PolicySubmission\n-- rival\n", digest="f" * 16, created="2026-11-08T09:00:00Z"), login="rival", pull=6,
                    head_sha=other_head, run_id="98", source="namespace PolicySubmission\n-- rival\n")
    git(rival, "add", "-A")
    git(rival, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "rival")
    # the rival lands between `clone`'s first fetch and its push, so that push is rejected and must be retried from the new tip
    real, raced = ap._git, []
    def racing(checkout, *args, **kw):
        result = real(checkout, *args, **kw)
        if args[0] == "fetch" and not raced:
            raced.append(1)
            git(rival, "push", "-q", "origin", ap.BRANCH)
        return result
    monkeypatch.setattr(ap, "_git", racing)
    out = archive(clone)
    assert out["added"] is True
    ids = ap.policy_ids(clone)
    assert sorted(ids) == sorted([HEAD, other_head]), "both archives are on the branch; neither overwrote the other"
    assert git(bare, "rev-parse", ap.BRANCH) == out["commit"]


def test_github_source_decodes_the_contents_api_and_refuses_anything_else():
    calls = []
    def request(url, token, payload=None):
        calls.append((url, token))
        return {"encoding": "base64", "content": base64.encodebytes(SOURCE.encode()).decode()}
    assert ap.github_source(HEAD, "tok", request) == SOURCE
    assert calls == [(f"https://api.github.com/repos/{ap.REPOSITORY}/contents/{ap.SOURCE_PATH}?ref={HEAD}", "tok")]
    with pytest.raises(ap.ArchiveRefused):
        ap.github_source(HEAD, "tok", lambda *a, **k: {"type": "dir"})
