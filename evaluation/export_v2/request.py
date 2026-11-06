"""A `forshor.publish/v2` request for one accepted policy: what policy intake in CI will send to the publisher.

The policy is priced at all four deltas on the boards it enters (a PhaseProduct policy: pp and full; a PhaseTripleProduct
policy: ptp only). The request is refused here, before any compute is spent on a hopeless one, if the policy has no valid
STO mirror evidence: only an accepted policy is ever published."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import engine, mapping, pricing
from .canon import epoch_of, policy_digest
from .evidence import EvidenceStore, forshor_from_seed  # noqa: F401  (ForShor evidence for new runs comes with policy intake)

SUBMISSIONS_REPO = "zapatacomputing/forshor-submissions"


def boards_for(mode: str) -> List[str]:
    eng = engine.load()
    return ["pp", "full"] if mode == eng.tables.PAIR else ["ptp"]


def build_request(policy_json: Dict[str, Any], *, model: Dict[str, Any], store: EvidenceStore, head_sha: str, pull_number: int,
                  github_login: str, run_id: str, submitted_at: str, evaluated_at: str, archive_commit: str,
                  accepted_ids: List[str], evaluation_key: str, base_generation: str, number: int,
                  forshor: Optional[Dict[str, Any]] = None, log: Callable[[str], None] = lambda s: None) -> Dict[str, Any]:
    eng = engine.load()
    policy = eng.tables.Policy.from_json(policy_json)
    boards = boards_for(policy.mode)
    mirror_board = "ptp" if "ptp" in boards else "pp"
    mirror, why = store.stomirror(policy_json, model, mirror_board)
    if mirror is None:
        raise ValueError(f"not publishable: no valid STO mirror evidence for this policy ({why})")
    verification = {"stoMirror": mirror, "forshorPolicy": forshor if mirror_board == "pp" else None}
    epoch = epoch_of(model)
    pr_url = f"https://github.com/{SUBMISSIONS_REPO}/pull/{pull_number}"
    priced = []
    for delta in mapping.DELTA_KEYS:
        views = pricing.price_views(policy, f"Submission {head_sha[:12]}", delta, boards)
        for board in boards:
            log(f"priced {board} {delta}")
            detail = mapping.panel_path(epoch, head_sha, board, delta)
            panel = mapping.panel_of(views[board], epoch=epoch, row_id=head_sha, board=board, delta=delta, verification=verification)
            row = mapping.row_of(panel, origin="submission", name=f"forshor_sub{number}", created_at=submitted_at, pull_request_url=pr_url,
                                 submission_id=head_sha, detail=detail)
            priced.append({"board": board, "delta": delta, "row": row, "panel": panel})
    return {
        "schema": "forshor.publish/v2", "evaluationKey": evaluation_key, "baseGeneration": base_generation,
        "evaluation": {"repository": SUBMISSIONS_REPO, "headSha": head_sha, "archiveCommit": archive_commit, "runId": run_id,
                       "githubLogin": github_login, "pullRequestUrl": pr_url, "evaluatedAt": evaluated_at, "submittedAt": submitted_at},
        "archive": {"policyId": head_sha, "runId": run_id, "pullRequestNumber": str(pull_number), "acceptedIds": accepted_ids},
        "model": model,
        "submission": {"id": head_sha, "pullRequestUrl": pr_url, "submittedAt": submitted_at,
                       "contributors": [{"githubLogin": github_login, "role": "submitter", "evidenceUrl": pr_url}],
                       "policyDigest": policy_digest(policy_json), "boards": boards, "verification": verification},
        "priced": priced,
    }
