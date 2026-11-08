#!/usr/bin/env python3
"""One submission, end to end: a policy JSON through the Lean checks to the v2 publisher (and so onto the boards).

    python intake_v2.py --policy P.json --login HANDLE --pull N --tree STO_TREE --publisher-url URL
                        [--forshor-tree FORSHOR_CHECKOUT] [--workdir DIR] [--head-sha SHA] [--run-id ID] [--stage-timeout S]

This is the whole of "everything after the participant's policy exists", in one command a workflow can call (GitHub CI supplies the PR's login, number
and head commit, the Lean trees and the publisher's URL and token; nothing else is needed). It never runs anything a participant wrote except Lean
on the policy's own data:

  1. reads the policy and normalises it to the policy the engine prices (a declared width ladder and any other extra key are dropped, never passed to Lean);
  2. refuses the reserved handle, and a publisher whose epoch was built for a different model than this checkout's, BEFORE any compute;
  3. runs the STO mirror (PhaseProduct) or the PhaseTripleProduct harness at exact widths in the Lean tree at the epoch's pins: an envelope that is not
     accepted ends the run, with the stage and reason;
  4. for a PhaseProduct policy whose k are all at most 6, and when a ForShor checkout is given: writes ForShor's PolicyTemplate for it into a disposable copy of
     the checkout, builds it, prints ForShor's document and requires its bands to be the submitted bands and its own IR to price to the same T as the policy.
     A check that was attempted and fails ends the run. Without a checkout, or with a k above 6 (ForShor takes 1 < k <= 6), the row carries no ForShor record;
  5. prices the policy once, then publishes it through `publish_v2_with_recovery` (the CI client): a conflict or transport error re-reads the live state
     and refreshes only what depends on it.

Exit status: 0 published (or already published), 1 refused (the result says why), 2 the run could not complete (a tool failed).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "starter" / "pack" / "harness"))

from publish import RetryPublication, request_json  # noqa: E402  (v1 helpers, unchanged)
from publish_v2 import ModelMismatch, ReservedHandle, policy_name, publish_v2_with_recovery  # noqa: E402

REPO = "zapatacomputing/forshor-submissions"
MAX_FORSHOR_K = 6


class Refused(Exception):
    """The submission was examined and does not qualify. `stage` names the check, `reason` says why."""

    def __init__(self, stage: str, reason: str):
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason = stage, reason


def normalise(policy_doc: Any) -> Dict[str, Any]:
    """The policy the engine prices, as site policy JSON: mode, name, bands (threshold, k, point_order, operations). Anything else is dropped."""
    from export_v2 import engine
    eng = engine.load()
    docs = eng.tables.policy_docs(policy_doc)
    if len(docs) != 1:
        raise Refused("format", f"expected one policy, found {len(docs)}")
    try:
        P = eng.tables.Policy.from_json(docs[0])
    except Exception as e:  # noqa: BLE001
        raise Refused("format", f"not an accepted policy: {e}")
    if not P.bands:
        raise Refused("format", "a policy needs at least one band (a bare leaf is not a submission; the Lean harness refuses it too)")
    return P.to_json()


def _point(p: Dict[str, Any]) -> str:
    """A ForShor point (`{"kind": "int", "z": z}` or a fraction with denominator m, 0 meaning infinity) in the site's spelling."""
    if p["kind"] == "int":
        return str(int(p["z"]))
    m = int(p["m"])
    return "inf" if m == 0 else f"{1 if m > 0 else -1}/{abs(m)}"


def policy_from_document(eng, doc: Dict[str, Any]):
    """The policy ForShor's printed document describes, as the engine's policy (its tables spelled as the site spells them)."""
    T = eng.tables
    return T.Policy(mode=T.PAIR, name="forshor-printed", bands=tuple(
        T.Band(int(b["threshold"]), T.Table.from_json({"mode": T.PAIR, "k": int(b["k"]), "point_order": [_point(p) for p in b["points"]],
                                                       "operations": b["ops"]})) for b in doc["bands"]))


def forshor_record(eng, P, doc: Dict[str, Any], pin: str) -> Dict[str, Any]:
    """`verification.forshorPolicy` for a PhaseProduct policy, from ForShor's printed document: its bands are the submitted bands and its own IR
    prices to the same single-run T as the policy does (the site's two agreement checks). Raises Refused when either fails."""
    from export_v2.evidence import FORSHOR_CLAIM
    sys.path.insert(0, str(eng.engine_dir))
    from site_engine import DEFAULT_DELTA
    from site_engine import fullalg as fa
    printed = policy_from_document(eng, doc)
    bands_agree = [(b.threshold, b.table.k, b.table.digest) for b in printed.bands] == [(b.threshold, b.table.k, b.table.digest) for b in P.bands]
    rule = fa.doc_width_rule(doc)
    t_document = float(fa.price_document(doc, 2048, DEFAULT_DELTA)["T"])
    t_policy = float(fa.price_full(P, 2048, DEFAULT_DELTA, detail=False, rule=rule)["T"])
    ir_agrees = t_document == t_policy
    if not bands_agree:
        raise Refused("forshor", "the bands ForShor printed are not the submitted bands")
    if not ir_agrees:
        raise Refused("forshor", f"ForShor's own IR prices to {t_document!r} T, the policy to {t_policy!r} T")
    return {"verified": True, "bandsAgree": True, "irPriceAgrees": True, "claim": FORSHOR_CLAIM, "forshorCommit": pin}


def forshor_check(eng, P, forshor_tree: Path, workdir: Path, pin: str, timeout: int = 7200) -> Dict[str, Any]:
    """ForShor's build of the policy's template in a disposable copy of the checkout, then its printed document, then `forshor_record`."""
    import policy_to_template as ptt
    tree = workdir / "forshor-tree"
    if tree.exists():
        shutil.rmtree(tree)
    subprocess.run((["cp", "-Rc"] if sys.platform == "darwin" else ["cp", "-a"]) + [str(forshor_tree), str(tree)], check=True)
    (tree / "ForShor" / "ShorVerification" / "Submission" / "PolicyTemplate.lean").write_text(ptt.template_text(P, "ForShor"))
    log = workdir / "forshor.log"
    with open(log, "w") as out:
        built = subprocess.run(["lake", "build", "PolicySubmission", "forshor_policy_submission"], cwd=tree, stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
    if built.returncode != 0:
        raise Refused("forshor", "ForShor's build of the policy template failed: " + log.read_text()[-500:])
    printed = subprocess.run(["lake", "exe", "forshor_policy_submission", "--balanced"], cwd=tree, capture_output=True, text=True, timeout=timeout)
    if printed.returncode != 0:
        raise Refused("forshor", "ForShor's check refused the policy: " + (printed.stderr or printed.stdout)[-500:])
    return forshor_record(eng, P, json.loads(printed.stdout), pin)


def _published_archive_commit(url: str, token: str) -> str:
    """The archive commit the live snapshot records, read from the published dashboard (`/results/v2/dashboard.json`, which a local publisher serves and
    staging serves from the bucket mount). `/v2/state` does not carry it. Without it, pass `archive_commit` (or `--archive`)."""
    try:
        return request_json(url.rstrip("/") + "/results/v2/dashboard.json", token)["publication"]["archiveCommit"]
    except (RetryPublication, RuntimeError, KeyError) as e:
        raise RuntimeError("the archive commit is not in /v2/state: pass --archive-commit (or --archive); "
                           f"reading it from the published dashboard failed ({type(e).__name__})") from None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_intake(policy_doc: Any, *, login: str, pull: int, url: str, token: str, tree: Path, workdir: Path,
               head_sha: Optional[str] = None, run_id: Optional[str] = None, forshor_tree: Optional[Path] = None,
               stage_timeout: Optional[int] = None, mirror_timeout: int = 7200, archive_commit: Optional[str] = None,
               archive_view: Optional[Callable[[], Any]] = None,
               run_mirror_fn: Optional[Callable[..., Dict[str, Any]]] = None, python: str = sys.executable,
               log: Callable[[str], None] = lambda s: print(s, flush=True)) -> Dict[str, Any]:
    """The pipeline above. Returns the publisher's result plus `row` (name, board ranks, evidence). Raises `Refused`, `ReservedHandle`, `ModelMismatch`."""
    from export_v2 import engine, epoch, request
    from export_v2.canon import policy_digest
    from export_v2.evidence import EvidenceStore, run_mirror
    eng = engine.load()
    model = epoch.model_of()
    policy_json = normalise(policy_doc)
    digest = policy_digest(policy_json)
    head_sha = head_sha or hashlib.sha1(digest.encode()).hexdigest()
    run_id = run_id or str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    submitted_at = _now()
    pr_url = f"https://github.com/{REPO}/pull/{pull}"
    evaluation_key = hashlib.sha256(f"{head_sha}:{run_id}:{pull}".encode()).hexdigest()
    metadata = {"headSha": head_sha, "pullRequestUrl": pr_url, "githubLogin": login, "runId": run_id, "submittedAt": submitted_at,
                "evaluationKey": evaluation_key}
    state_of = lambda: request_json(url.rstrip("/") + "/v2/state", token)

    state = state_of()
    policy_name(state, login)                                   # the reserved handle, before any compute
    if state["model"] != model:
        raise ModelMismatch(f"live epoch {state['epoch']} was built for a different model than this checkout's; not publishing")

    workdir.mkdir(parents=True, exist_ok=True)
    board = "ptp" if policy_json["mode"] == eng.tables.TRIPLE else "pp"
    log(f"policy {digest[:12]} ({policy_json['mode']}, {len(policy_json['bands'])} bands): the Lean mirror at exact widths ...")
    envelope = (run_mirror_fn or run_mirror)(policy_json, board, tree, workdir / "evidence", timeout=mirror_timeout, python=python, stage_timeout=stage_timeout)
    store = EvidenceStore(workdir / "evidence")
    mirror, why = store.stomirror(policy_json, model, board)
    if mirror is None:
        rec = envelope.get("record", {})
        rej = rec.get("rejection") or {}
        raise Refused(rej.get("stage") or "mirror", f"{rej.get('reason') or why}: {str(rej.get('detail') or '')[:400]}".strip(": "))
    log("  accepted by the STO mirror")

    forshor = None
    P = eng.tables.Policy.from_json(policy_json)
    if forshor_tree and P.mode == eng.tables.PAIR and max(b.table.k for b in P.bands) <= MAX_FORSHOR_K:
        log("ForShor's build and printed document ...")
        forshor = forshor_check(eng, P, Path(forshor_tree), workdir, model["forshor"]["pin"])
        log("  ForShor policy-verified")
    else:
        log("no ForShor check (" + ("PhaseTripleProduct" if P.mode != eng.tables.PAIR else "no ForShor checkout given" if not forshor_tree else f"k above {MAX_FORSHOR_K}") + ")")

    # Production: the accepted ids and the archive commit are the `submission-results` branch's (`archive_view`, as publish_v2 reads it). A local
    # rehearsal has no such branch: the accepted ids are the publisher's own ledger plus this submission.
    archive = archive_commit or (archive_view()[1] if archive_view else _published_archive_commit(url, token))
    accepted_now = archive_view or (lambda: (sorted(set(state_of().get("submissionIds", [])) | {head_sha}), archive))

    def price() -> Dict[str, Any]:
        return request.build_request(policy_json, model=model, store=store, head_sha=head_sha, pull_number=pull, github_login=login, run_id=run_id,
                                     submitted_at=submitted_at, evaluated_at=submitted_at, archive_commit=archive, accepted_ids=[head_sha], number=0,
                                     evaluation_key=evaluation_key, base_generation="", forshor=forshor, log=log)

    result = publish_v2_with_recovery(metadata, model, state_of, price, accepted_now, lambda payload: request_json(url.rstrip("/") + "/v2/publish", token, payload))
    result["policy"] = {"digest": digest, "headSha": head_sha, "login": login, "pull": pull, "mode": policy_json["mode"],
                        "stoMirror": True, "forshorPolicy": bool(forshor)}
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--login", required=True, help="the submitter's GitHub handle (the PR author)")
    ap.add_argument("--pull", type=int, required=True, help="the pull request number")
    ap.add_argument("--head-sha", help="the PR head commit (40 hex); default: derived from the policy")
    ap.add_argument("--run-id")
    ap.add_argument("--tree", type=Path, required=True, help="the built Lean tree for the STO mirror, at the epoch's pins")
    ap.add_argument("--forshor-tree", type=Path, help="a built ForShor checkout at the epoch's pin (copied per run); omit to skip the ForShor check")
    ap.add_argument("--publisher-url", default=os.environ.get("PUBLISHER_URL"))
    ap.add_argument("--workdir", type=Path, default=Path("intake-work"))
    ap.add_argument("--archive", type=Path, help="a checkout of the `submission-results` branch (as publish_v2.py reads it; needs GH_TOKEN); omit for a local rehearsal")
    ap.add_argument("--archive-commit", help="the submission-results commit to record (default: the live snapshot's, read from the published dashboard)")
    ap.add_argument("--stage-timeout", type=int)
    ap.add_argument("--mirror-timeout", type=int, default=7200)
    ap.add_argument("--out", type=Path, help="write the result JSON here")
    a = ap.parse_args(argv)
    if not a.publisher_url:
        ap.error("--publisher-url (or PUBLISHER_URL) is required")
    try:
        result = run_intake(json.loads(a.policy.read_text()), login=a.login, pull=a.pull, url=a.publisher_url, token=os.environ.get("PUBLISHER_TOKEN", "local"),
                            tree=a.tree, workdir=a.workdir, head_sha=a.head_sha, run_id=a.run_id, forshor_tree=a.forshor_tree,
                            stage_timeout=a.stage_timeout, mirror_timeout=a.mirror_timeout,
                            archive_commit=a.archive_commit,
                            archive_view=(lambda: __import__("publish_v2")._archive_view(a.archive)) if a.archive else None)
        code = 0
    except (Refused, ReservedHandle, ModelMismatch) as e:
        result, code = {"refused": True, "stage": getattr(e, "stage", type(e).__name__), "reason": getattr(e, "reason", str(e))}, 1
    except (RetryPublication, RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError) as e:
        result, code = {"failed": True, "error": f"{type(e).__name__}: {e}"[:600]}, 2
    text = json.dumps(result, indent=1)
    print(text)
    if a.out:
        a.out.write_text(text + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
