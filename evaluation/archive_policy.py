"""Archive an accepted policy submission to the `submission-results` branch.

The trusted publish job calls this (through `intake_v2.py --publish-from ... --archive CHECKOUT`) *before* it publishes: the publisher requires the archive's
accepted ids to include the new head commit, and `publish_v2._archive_view` reads them from this branch.

Layout, beside the table-generation archive and independent of it (the v1 index `leaderboard/results.json` is table-generation's: its writer drops any entry
without table results, so policy entries do not go there):

    leaderboard/policy-submissions.json          {"schema": "forshor.policy-archive/v1", "policies": [<entry>, ...]}   sorted by (created_at, policy_id)
    results/policy-submissions/<head>/Policy.lean     the submitted file, byte for byte
    results/policy-submissions/<head>/policy.json     the policy as the boards price it, with its digest
    results/policy-submissions/<head>/verification.json   the verification the publish job checked (mirror envelope and ForShor's printed document)

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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BRANCH = "submission-results"
INDEX_PATH = "leaderboard/policy-submissions.json"
INDEX_SCHEMA = "forshor.policy-archive/v1"
DIR = "results/policy-submissions"
SOURCE_PATH = "TableGeneration/Submission/Policy.lean"
REPOSITORY = "zapatacomputing/forshor-submissions"
HEAD = re.compile(r"[0-9a-f]{40}")


class ArchiveRefused(Exception):
    pass


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def github_source(head_sha: str, token: str, request_json: Callable[..., Any], repository: str = REPOSITORY) -> str:
    """`Policy.lean` as it is at `head_sha` on GitHub (the contents API returns it base64-encoded)."""
    doc = request_json(f"https://api.github.com/repos/{repository}/contents/{SOURCE_PATH}?ref={head_sha}", token)
    if doc.get("encoding") != "base64" or not isinstance(doc.get("content"), str):
        raise ArchiveRefused(f"GitHub returned no file content for {SOURCE_PATH} at {head_sha}")
    return base64.b64decode(doc["content"]).decode()


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


def write_policy(checkout: Path, v: Dict[str, Any], *, login: str, pull: int, head_sha: str, run_id: str, source: str,
                 repository: str = REPOSITORY) -> bool:
    """Write the files and the index entry into the checkout. Returns whether anything changed. Raises `ArchiveRefused`."""
    if not HEAD.fullmatch(head_sha):
        raise ArchiveRefused("the head commit is not a 40-hex sha")
    if not source.strip():
        raise ArchiveRefused("there is no Lean source to archive")
    if v.get("leanSha256") != sha256(source) or v.get("leanSource") != source:
        raise ArchiveRefused("the verification's Lean source is not the file at the pull request's head commit")
    entry = {"policy_id": head_sha, "digest": v["digest"], "mode": v["mode"], "board": v["board"], "sha256": sha256(source),
             "path": f"{DIR}/{head_sha}", "source": {"repository": repository, "pr_number": pull, "github_login": login, "run_id": str(run_id),
                                                      "created_at": v["createdAt"]}}
    index = read_index(checkout)
    old = next((p for p in index["policies"] if p["policy_id"] == head_sha), None)
    if old is not None:
        if old["sha256"] != entry["sha256"] or old["digest"] != entry["digest"]:
            raise ArchiveRefused(f"{head_sha} is already archived with a different policy")
        return False
    folder = checkout / DIR / head_sha
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Policy.lean").write_text(source)
    (folder / "policy.json").write_text(json.dumps({"digest": v["digest"], "mode": v["mode"], "board": v["board"], "policy": v["policy"]}, indent=1) + "\n")
    stored = {k: val for k, val in v.items() if k != "leanSource"}   # the source is already stored as Policy.lean
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


def archive_policy(checkout: Path, v: Dict[str, Any], *, login: str, pull: int, head_sha: str, run_id: str, source: str, token: Optional[str] = None,
                   remote: str = "origin", attempts: int = 4, repository: str = REPOSITORY) -> Dict[str, Any]:
    """Add the policy to `BRANCH` and push, starting each attempt from the branch's current tip (a competing archive push is retried, not merged).
    `checkout` is a clone with `remote`; returns `{"path", "commit", "added"}`."""
    env = _env(token)
    last = ""
    for _ in range(attempts):
        _git(checkout, "fetch", "--depth=1", remote, BRANCH, env=env)
        _git(checkout, "checkout", "--detach", "FETCH_HEAD")
        if not write_policy(checkout, v, login=login, pull=pull, head_sha=head_sha, run_id=run_id, source=source, repository=repository):
            tip = _git(checkout, "rev-parse", "HEAD").stdout.strip()
            return {"path": f"{DIR}/{head_sha}", "commit": tip, "added": False}
        _git(checkout, "add", INDEX_PATH, f"{DIR}/{head_sha}")
        _git(checkout, "-c", "user.name=forshor-results", "-c", "user.email=forshor-results@users.noreply.github.com", "commit", "-q", "-m",
             f"Archive policy submission {head_sha[:12]} (PR #{pull}, {login})")
        pushed = _git(checkout, "push", remote, f"HEAD:refs/heads/{BRANCH}", env=env, check=False)
        if pushed.returncode == 0:
            return {"path": f"{DIR}/{head_sha}", "commit": _git(checkout, "rev-parse", "HEAD").stdout.strip(), "added": True}
        last = (pushed.stderr or "").strip().splitlines()[-1:] and (pushed.stderr or "").strip().splitlines()[-1] or "push failed"
    raise RuntimeError(f"could not push the archive after {attempts} attempts: {last[:300]}")
