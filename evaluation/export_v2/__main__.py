"""python -m export_v2 COMMAND   (run from evaluation/, with the engine's Python environment)

  evidence-needed  OUT_DIR              the planner policies and which have valid evidence; writes each policy as a file for the harness
  run-mirror       OUT_DIR --tree DIR   run the tree's own harness on every planner policy still lacking evidence (needs a BUILT tree)
  epoch-input      OUT.json --publisher-commit SHA [--evidence-dir DIR] [--archive-commit SHA] [--updated-at ISO]
  request          OUT.json --policy FILE --evidence-dir DIR --head-sha SHA --pull N --login NAME --run-id ID ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import engine
from .canon import policy_digest
from .evidence import EvidenceStore, run_mirror


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _planner_policies(store: EvidenceStore, model, only: str = "all"):
    """Distinct listed policies (digest -> board family and policy JSON), without pricing them: the planner's frontier policies and the
    single-arity baselines, or one of the two."""
    from . import epoch
    out = {}
    if only in ("all", "planner"):
        for (board, _delta), rows in epoch.planner_rows(_log).items():
            for r in rows:
                out.setdefault(r["digest"], (board, r["policy"].to_json()))
    if only in ("all", "baselines"):
        for b in epoch.baseline_policies(_log):
            out.setdefault(b["digest"], (b["board"], b["policy"].to_json()))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="export_v2")
    sub = ap.add_subparsers(dest="command", required=True)
    n = sub.add_parser("evidence-needed"); n.add_argument("out_dir"); n.add_argument("--evidence-dir")
    r = sub.add_parser("run-mirror"); r.add_argument("out_dir"); r.add_argument("--tree", required=True); r.add_argument("--evidence-dir")
    for p in (n, r):
        p.add_argument("--only", choices=("all", "planner", "baselines"), default="all", help="which listed policies to consider")
    r.add_argument("--timeout", type=int, default=7200, help="seconds before one harness run is killed (default 7200)")
    e = sub.add_parser("epoch-input"); e.add_argument("out"); e.add_argument("--publisher-commit", required=True)
    e.add_argument("--evidence-dir"); e.add_argument("--archive-commit"); e.add_argument("--updated-at"); e.add_argument("--report")
    q = sub.add_parser("request"); q.add_argument("out"); q.add_argument("--policy", required=True); q.add_argument("--evidence-dir", required=True)
    for flag in ("head-sha", "login", "run-id", "submitted-at", "evaluated-at", "archive-commit", "evaluation-key", "base-generation"):
        q.add_argument(f"--{flag}", required=True)
    q.add_argument("--pull", type=int, required=True); q.add_argument("--accepted-id", action="append", default=[])
    q.add_argument("--number", type=int, required=True, help="this handle's submission number: /v2/state nextNumbers[<login>] (1 if absent); the row is named <login>_sub<number>")
    a = ap.parse_args(argv)

    from . import epoch, request
    store = EvidenceStore(Path(a.evidence_dir) if getattr(a, "evidence_dir", None) else None)
    model = epoch.model_of()
    if a.command in ("evidence-needed", "run-mirror"):
        out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
        policies = _planner_policies(store, model, a.only)
        missing = {}
        for digest, (board, pj) in policies.items():
            ok, why = store.stomirror(pj, model, "ptp" if board == "ptp" else "pp")
            if ok is None:
                missing[digest] = (board, pj, why)
        manifest = [{"digest": d, "board": b, "reason": w, "file": f"{d[:16]}.policy.json"} for d, (b, _p, w) in missing.items()]
        (out / "needed.json").write_text(json.dumps({"distinct": len(policies), "withoutEvidence": len(missing), "policies": manifest}, indent=1))
        if a.command == "evidence-needed":
            for d, (b, pj, w) in missing.items():
                (out / f"{d[:16]}.policy.json").write_text(json.dumps(pj, indent=1, ensure_ascii=False))
            print(f"{len(policies)} distinct planner policies, {len(missing)} without valid evidence for this epoch; see {out/'needed.json'}")
        else:
            for d, (b, pj, w) in missing.items():
                _log(f"harness on {d[:16]} ({b}) ...")
                env = run_mirror(pj, b, Path(a.tree), out, timeout=a.timeout)
                _log(f"  verdict: {env['record'].get('verdict')}")
        return 0
    if a.command == "epoch-input":
        document, report = epoch.build_epoch_input(store=store, publisher_commit=a.publisher_commit, archive_commit=a.archive_commit,
                                                   updated_at=a.updated_at, model=model, log=_log)
        Path(a.out).write_text(json.dumps(document, separators=(",", ":")))
        if a.report:
            Path(a.report).write_text(json.dumps(report, indent=1))
        print(json.dumps({k: report[k] for k in ("distinctPolicies", "rowsTotal", "rowsWithEvidence", "rowsUnchecked", "rowsBaseline")}))
        return 0
    policy_json = json.loads(Path(a.policy).read_text())
    req = request.build_request(policy_json, model=model, store=store, head_sha=a.head_sha, pull_number=a.pull, github_login=a.login,
                                run_id=a.run_id, submitted_at=a.submitted_at, evaluated_at=a.evaluated_at, archive_commit=a.archive_commit,
                                accepted_ids=a.accepted_id or [a.head_sha], evaluation_key=a.evaluation_key,
                                base_generation=a.base_generation, number=a.number, log=_log)
    Path(a.out).write_text(json.dumps(req, separators=(",", ":")))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
