"""Archive an accepted policy submission to the `submission-results` branch.

The trusted publish job calls this (through `intake_v2.py --publish-from ... --archive CHECKOUT`) *before* it publishes: the publisher requires the archive's
accepted ids to include the new head commit, and `publish_v2._archive_view` reads them from this branch.

Layout, beside the table-generation archive and independent of it (the v1 index `leaderboard/results.json` is table-generation's: its writer drops any entry
without table results, so policy entries do not go there):

    leaderboard/policy-submissions.json          {"schema": "forshor.policy-archive/v1", "policies": [<entry>, ...]}   sorted by (created_at, policy_id)
    results/policy-submissions/<head>/Policy.lean     the submitted file, byte for byte
    results/policy-submissions/<head>/policy.json     the policy as the boards price it, with its digest
    results/policy-submissions/<head>/verification.json   the verification the publish job checked (mirror envelope and ForShor's printed document)
    results/policy-submissions/<head>/summary.tex     only when the pull request has one: the optional LaTeX summary, byte for byte (never compiled or run);
                                                      the index entry then records `summarySha256`

The summary is held to the same standard as the policy: it must equal the file at `head_sha` on GitHub (fetched through the contents API; no file there means no summary)
AND the verification must carry the same text and hash. A summary on one side only, or two that differ, is refused (stage "archive"), as is one that breaks the rules of
`summary_tex.py`.

`<head>` is the pull request's head commit, which is the submission id on the boards. Writing is idempotent: the same head with the same file is a no-op, and the
same head with a different file is refused. The source is not taken from the (untrusted) verification alone: it must equal the file at `head_sha` on GitHub.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Collection, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summary_tex  # noqa: E402

BRANCH = "submission-results"
INDEX_PATH = "leaderboard/policy-submissions.json"
INDEX_SCHEMA = "forshor.policy-archive/v1"
DIR = "results/policy-submissions"
SOURCE_PATH = "TableGeneration/Submission/Policy.lean"
SUMMARY_PATH = summary_tex.SUMMARY_PATH
REPOSITORY = "zapatacomputing/forshor-submissions"
HEAD = re.compile(r"[0-9a-f]{40}")


class ArchiveRefused(Exception):
    pass


#: What to do when a policy with the same digest (mode and bands) is already archived. "same_participant": the same GitHub login (compared case-insensitively);
#: "other_participant": anyone else. "refuse" publishes no row and tells the author which earlier submission it repeats; "allow" publishes as usual and the repeat is
#: only identified (the `duplicates` of the archive result, logged by `intake_v2`). Change `other_participant` to "refuse" to stop others repeating a published policy.
DUPLICATE_RULES = {"same_participant": "refuse", "other_participant": "allow"}


class DuplicatePolicy(ArchiveRefused):
    """A policy this submission repeats, and a rule that refuses it. `duplicate_of` describes the earlier archived submission.

    `source` names which check refused, because the same message was raised from two of them (census C21): the readout
    variant planner, whose refusal the writer may catch as control flow, and the writer's own duplicate rules. An
    operator reading a refused run could not tell whether the repeat was found while planning the readout variant or
    while writing the entry, and those are different places to look."""

    def __init__(self, duplicate_of: Dict[str, Any], source: str = "the archive's duplicate rules"):
        who = "you" if duplicate_of["sameParticipant"] else "another participant"
        super().__init__(f"{who} already submitted this exact policy as pull request #{duplicate_of['pull']} (commit {duplicate_of['policyId'][:7]}, "
                         f"{str(duplicate_of['createdAt'])[:10]}); each policy can be published once"
                         + (" per participant" if duplicate_of["sameParticipant"] else "")
                         + f" (refused by {source})")
        self.duplicate_of = duplicate_of
        self.source = source


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def github_source(head_sha: str, token: str, request_json: Callable[..., Any], repository: str = REPOSITORY) -> str:
    """`Policy.lean` as it is at `head_sha` on GitHub (the contents API returns it base64-encoded)."""
    doc = request_json(f"https://api.github.com/repos/{repository}/contents/{SOURCE_PATH}?ref={head_sha}", token)
    if doc.get("encoding") != "base64" or not isinstance(doc.get("content"), str):
        raise ArchiveRefused(f"GitHub returned no file content for {SOURCE_PATH} at {head_sha}")
    return base64.b64decode(doc["content"]).decode()


def github_summary(head_sha: str, token: str, request_json: Callable[..., Any], repository: str = REPOSITORY) -> Optional[str]:
    """`summary.tex` as it is at `head_sha` on GitHub, or None when the commit has no such file (HTTP 404: the only answer that means "no summary"; any other failure
    propagates, so an outage never turns into "no summary"). Strict UTF-8: a file that is not text is refused here, whatever the verification says."""
    try:
        doc = request_json(f"https://api.github.com/repos/{repository}/contents/{SUMMARY_PATH}?ref={head_sha}", token)
    except RuntimeError as error:
        if "HTTP 404" in str(error):
            return None
        raise
    if isinstance(doc, list) or doc.get("type") not in (None, "file") or doc.get("encoding") != "base64" or not isinstance(doc.get("content"), str):
        raise ArchiveRefused(f"{summary_tex.NAME} is not a regular file at {head_sha}")
    data = base64.b64decode(doc["content"])
    why = summary_tex.problem(data)
    if why:
        # Census C20: summary_tex.problem's three messages are raised here and again from check_summary below, so each
        # route says which file it read. Here it is GitHub's copy at the head commit; there it is the carried copy.
        raise ArchiveRefused(f"{why} (the summary.tex at the pull request's head commit)")
    return data.decode("utf-8")


def read_index(checkout: Path) -> Dict[str, Any]:
    path = checkout / INDEX_PATH
    if not path.exists():
        return {"schema": INDEX_SCHEMA, "policies": []}
    index = json.loads(path.read_text())
    if index.get("schema") != INDEX_SCHEMA or not isinstance(index.get("policies"), list):
        raise ArchiveRefused(f"{INDEX_PATH} is not a {INDEX_SCHEMA} index")
    return index


def policy_ids(checkout: Path) -> List[str]:
    return [p["policy_id"] for p in read_index(checkout)["policies"]]


def find_duplicates(index: Dict[str, Any], *, digest: str, mode: str, login: str, head_sha: str,
                    published_ids: Optional[Collection[str]] = None) -> List[Dict[str, Any]]:
    """The archived submissions with this policy (same digest and mode), other than `head_sha` itself, oldest first. The archive is written before the publisher is
    called, so an entry can exist for a head that was never published; when `published_ids` (the publisher's own list of published heads) is given, only those count."""
    found = []
    for p in index["policies"]:
        if p["digest"] != digest or p["mode"] != mode or p["policy_id"] == head_sha:
            continue
        if published_ids is not None and p["policy_id"] not in published_ids:
            continue
        who = str(p["source"].get("github_login") or "")
        found.append({"policyId": p["policy_id"], "login": who, "pull": p["source"].get("pr_number"), "createdAt": p["source"].get("created_at"),
                      "sameParticipant": who.lower() == login.lower()})
    return sorted(found, key=lambda d: (d["createdAt"] or "", d["policyId"]))


def plan_readout_variant(index: Dict[str, Any], *, digest: str, login: str, head_sha: str,
                         readout: str, published_ids: Optional[Collection[str]] = None,
                         mode: str = "PhaseProduct") -> Optional[str]:
    """How this participant's own earlier archived copies of this policy bear on it. Return a prior primitive ID, if any.

    Raises `DuplicatePolicy` for a repeat with the same readout. `mode` is the
    board mode to compare on; readout designs exist for PhaseProduct only, but
    `write_policy` asks about every mode it archives, so the same refusal holds.

    Take only visible records into account. Legacy records omit both circuit
    and board metadata and mean coherent readout with a primitive result.
    Future opt-in entries can carry `readout` and `boards` without backfill.
    """
    if readout not in ("coherent", "semiclassical"):
        raise ArchiveRefused("unknown readout design")
    records = {p["policy_id"]: p for p in index["policies"]}
    primitive = None
    for previous in find_duplicates(index, digest=digest, mode=mode, login=login, head_sha=head_sha,
                                    published_ids=published_ids):
        if not previous["sameParticipant"]:
            continue
        entry = records[previous["policyId"]]
        chosen = entry.get("readout", "coherent")
        boards = entry.get("boards", ["pp", "full"])
        if chosen not in ("coherent", "semiclassical") or boards not in (["pp", "full"], ["full", "pp"], ["full"]):
            raise ArchiveRefused("unrecognized circuit or boards in published archive entry")
        if chosen == readout:
            raise DuplicatePolicy(previous, "the readout variant planner")
        if "pp" in boards and primitive is None:
            primitive = previous["policyId"]
    return primitive


def check_summary(v: Dict[str, Any], summary: Optional[str]) -> None:
    """`summary` is the file at the head commit (None: there is none). It must satisfy the rules, and the verification must carry exactly it, in both directions."""
    carried = v.get("summarySource")
    if summary is None and carried is None and "summarySha256" not in v:
        return
    if summary is None:
        raise ArchiveRefused("the verification carries a summary.tex that the pull request's head commit does not have")
    why = summary_tex.problem(summary.encode("utf-8"))
    if why:
        raise ArchiveRefused(f"{why} (cross-checking the verification against the head commit)")
    if carried is None:
        raise ArchiveRefused("the pull request's head commit has a summary.tex that the verification does not carry")
    if carried != summary or v.get("summarySha256") != sha256(summary):
        raise ArchiveRefused("the verification's summary.tex is not the file at the pull request's head commit")


def write_policy(checkout: Path, v: Dict[str, Any], *, login: str, pull: int, head_sha: str, run_id: str, source: str,
                 summary: Optional[str] = None, repository: str = REPOSITORY, duplicates: Optional[List[Dict[str, Any]]] = None,
                 published_ids: Optional[Collection[str]] = None, readout: str = "coherent") -> bool:
    """Write the files and the index entry into the checkout. Returns whether anything changed. Raises `ArchiveRefused` (`DuplicatePolicy` when `DUPLICATE_RULES`
    refuse a repeat). Repeats that the rules allow are appended to `duplicates`. `published_ids`: see `find_duplicates`.

    `readout` is this submission's readout design. Your own earlier copies of the policy go through `plan_readout_variant`: the same readout is still the repeat it has
    always been, the other readout is a variant of them and keeps their primitive, so it enters the full board alone. Only such a non-legacy entry records `readout` and
    `boards`; the default coherent entry is written exactly as it always was, and no archived row is backfilled."""
    if not HEAD.fullmatch(head_sha):
        raise ArchiveRefused("the head commit is not a 40-hex sha")
    if not source.strip():
        raise ArchiveRefused("there is no Lean source to archive")
    if v.get("leanSha256") != sha256(source) or v.get("leanSource") != source:
        raise ArchiveRefused("the verification's Lean source is not the file at the pull request's head commit")
    check_summary(v, summary)
    entry = {"policy_id": head_sha, "digest": v["digest"], "mode": v["mode"], "board": v["board"], "sha256": sha256(source),
             "path": f"{DIR}/{head_sha}", "source": {"repository": repository, "pr_number": pull, "github_login": login, "run_id": str(run_id),
                                                      "created_at": v["createdAt"]}}
    if summary is not None:
        entry["summarySha256"] = sha256(summary)
    index = read_index(checkout)
    old = next((p for p in index["policies"] if p["policy_id"] == head_sha), None)
    if old is not None:
        if old["sha256"] != entry["sha256"] or old["digest"] != entry["digest"] or old.get("summarySha256") != entry.get("summarySha256"):
            raise ArchiveRefused(f"{head_sha} is already archived with a different policy")
        return False
    repeats = find_duplicates(index, digest=v["digest"], mode=v["mode"], login=login, head_sha=head_sha,
                              published_ids=published_ids)
    primitive_from = None
    try:
        primitive_from = plan_readout_variant(index, digest=v["digest"], mode=v["mode"], login=login, head_sha=head_sha,
                                              readout=readout, published_ids=published_ids)
    except DuplicatePolicy:
        if DUPLICATE_RULES["same_participant"] == "refuse":     # the same readout: the repeat the rules have always refused
            raise
    else:
        repeats = [r for r in repeats if not r["sameParticipant"]]   # your own copies are all of the other readout, so this is a variant of them
    for repeat in repeats:
        if DUPLICATE_RULES["same_participant" if repeat["sameParticipant"] else "other_participant"] == "refuse":
            raise DuplicatePolicy(repeat, "the archive's duplicate rules, while writing the entry")
        if duplicates is not None:
            duplicates.append(repeat)
    if readout != "coherent" or primitive_from is not None:
        entry["readout"] = readout
        entry["boards"] = ["full"] if primitive_from else ["pp", "full"]
    folder = checkout / DIR / head_sha
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Policy.lean").write_text(source)
    (folder / "policy.json").write_text(json.dumps({"digest": v["digest"], "mode": v["mode"], "board": v["board"], "policy": v["policy"]}, indent=1) + "\n")
    if summary is not None:
        (folder / "summary.tex").write_bytes(summary.encode("utf-8"))   # verbatim: bytes, so no newline is translated
    stored = {k: val for k, val in v.items() if k not in ("leanSource", "summarySource")}   # the sources are already stored as Policy.lean and summary.tex
    (folder / "verification.json").write_text(json.dumps(stored, indent=1) + "\n")
    index["policies"] = sorted(index["policies"] + [entry], key=lambda p: (p["source"].get("created_at") or "", p["policy_id"]))
    (checkout / INDEX_PATH).write_text(json.dumps(index, indent=1) + "\n")
    return True


def _env(token: Optional[str]) -> Dict[str, str]:
    """The token is only ever in the child's environment, never in argv or a remote URL (as `publish.refresh_archive` does)."""
    env = dict(os.environ)
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader", GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}")
    return env


def _git(checkout: Path, *args: str, env: Optional[Dict[str, str]] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(checkout), *args], env=env, check=check, text=True, capture_output=True)


def archive_policy(checkout: Path, v: Dict[str, Any], *, login: str, pull: int, head_sha: str, run_id: str, source: str, summary: Optional[str] = None,
                   token: Optional[str] = None, remote: str = "origin", attempts: int = 4, repository: str = REPOSITORY,
                   published_ids: Optional[Collection[str]] = None, readout: str = "coherent") -> Dict[str, Any]:
    """Add the policy to `BRANCH` and push, starting each attempt from the branch's current tip (a competing archive push is retried, not merged).
    `checkout` is a clone with `remote`; returns `{"path", "commit", "added", "duplicates"}` (the earlier submissions of the same policy that the rules allowed).
    `readout` is passed to `write_policy`, which decides there what an earlier copy of the policy with another readout design means."""
    env = _env(token)
    last = ""
    for _ in range(attempts):
        _git(checkout, "fetch", "--depth=1", remote, BRANCH, env=env)
        _git(checkout, "checkout", "--detach", "FETCH_HEAD")
        duplicates: List[Dict[str, Any]] = []
        if not write_policy(checkout, v, login=login, pull=pull, head_sha=head_sha, run_id=run_id, source=source, summary=summary, repository=repository,
                            duplicates=duplicates, published_ids=published_ids, readout=readout):
            tip = _git(checkout, "rev-parse", "HEAD").stdout.strip()
            return {"path": f"{DIR}/{head_sha}", "commit": tip, "added": False, "duplicates": []}
        _git(checkout, "add", INDEX_PATH, f"{DIR}/{head_sha}")
        _git(checkout, "-c", "user.name=forshor-results", "-c", "user.email=forshor-results@users.noreply.github.com", "commit", "-q", "-m",
             f"Archive policy submission {head_sha[:12]} (PR #{pull}, {login})")
        pushed = _git(checkout, "push", remote, f"HEAD:refs/heads/{BRANCH}", env=env, check=False)
        if pushed.returncode == 0:
            return {"path": f"{DIR}/{head_sha}", "commit": _git(checkout, "rev-parse", "HEAD").stdout.strip(), "added": True,
                    "duplicates": duplicates}
        last = (pushed.stderr or "").strip().splitlines()[-1:] and (pushed.stderr or "").strip().splitlines()[-1] or "push failed"
    raise RuntimeError(f"could not push the archive after {attempts} attempts: {last[:300]}")
