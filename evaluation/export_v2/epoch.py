"""Build an epoch: the model, the planner rows with their evidence, the reference curves and every panel, as one
`forshor.epoch-input/v2` document. forshor-site derives ranks, boards and the snapshot id from it.

Planner rows are the policies the epoch's planner builds from the table history ("STO frontier after <submission>"), at each
delta, canonicalized as the seed records are (consecutive same-table bands merged; prices unchanged). Many rows share one
policy, so the harness needs to run on far fewer policies than there are rows. A row with no valid policy-level evidence is
still listed, unranked."""
from __future__ import annotations

import copy
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import engine, mapping, pricing
from .canon import epoch_of, policy_digest, sha256_hex
from .evidence import EvidenceStore

REPO = Path(__file__).resolve().parents[2]
FILE_KEY = {"1e-2": "1e-02", "3e-4": "3e-04", "1e-4": "1e-04", "1e-6": "1e-06"}
MODES = {"pp": "PhaseProduct", "ptp": "PhaseTripleProduct"}
#: ShorTableOptimization commit the interim fork is based on (upstream main, 2026-11-03).
STO_COMMIT = "361e81e25965506423d169530a7decb5b27973d4"
ARCHIVE_PR = "https://github.com/VerifiedQC/ShorTableOptimization/pull/{}"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def forshor_pin(repo: Path = REPO) -> str:
    for package in json.loads((repo / "lake-manifest.json").read_text())["packages"]:
        if package["name"] in ("Fast_multiplication", "ForShor"):
            return package["rev"]
    raise RuntimeError("lake-manifest.json has no ForShor package")


def model_of(*, sto_commit: str = STO_COMMIT, engine_revision: Optional[str] = None) -> Dict[str, Any]:
    """The epoch's model identity. `engineRevision` is the last commit that changed `evaluation/engine`: stable across
    unrelated commits, so only a change that can move a price starts a new epoch. `dataFingerprint` is the engine's own
    fingerprint of its data and every source a price depends on (including the full-run pricer)."""
    eng = engine.load()
    return {
        "id": eng.SITE_MODEL_VERSION,
        "dataFingerprint": eng.full_frontier.fingerprint(),
        "engineRevision": engine_revision or _git("log", "-1", "--format=%H", "--", "evaluation/engine"),
        "qualtran": {"version": eng.tcost.qualtran_provenance()["version"], "source": "vendored"},
        "forshor": {"pin": forshor_pin()},
        "sto": {"commit": sto_commit},
        "lean": (REPO / "lean-toolchain").read_text().strip(),
        "widthRule": "exact", "n": pricing.N,
        "rotation": {"synthesis": "Ross-Selinger measured mean, 3.075*log2(1/eps) + 0.86", "fraction": 0.5},
    }


def planner_rows(log: Callable[[str], None] = lambda s: None) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """(board, delta) -> the frontier rows with the canonical planner policy behind each."""
    eng = engine.load()
    subs = eng.library.submissions()
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for delta_key, file_key in FILE_KEY.items():
        delta = mapping.DELTA_VALUES[delta_key]
        for board, mode in MODES.items():
            cache = json.loads((eng.engine_dir / "cache" / f"frontier_{board.upper()}_{file_key}.json").read_text())
            rows = []
            for r in cache["leaderboard"]["rows"]:
                i = next(i for i, s in enumerate(subs, start=1) if s.id == r["id"])
                tabs = [T for T in eng.library.sto_tables(upto=i) if T.mode == mode]
                P, _ = eng.planner.optimal_policy(tabs, mode, pricing.N, delta)
                Q = eng.seed_policies.canonical(P)
                rows.append({"id": r["id"], "createdAt": r["created_at"], "pr": r.get("pr"), "policy": Q,
                             "digest": policy_digest(Q.to_json())})
            out[(board, delta_key)] = rows
            log(f"planner rows {board} {delta_key}: {len(rows)}")
    return out


def build_epoch_input(*, store: EvidenceStore, publisher_commit: str, archive_commit: Optional[str] = None,
                      updated_at: Optional[str] = None, model: Optional[Dict[str, Any]] = None,
                      log: Callable[[str], None] = lambda s: None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The epoch input and the evidence report (which policies have valid evidence, and why the others do not)."""
    eng = engine.load()
    model = model or model_of()
    epoch = epoch_of(model)
    rows_in = planner_rows(log)
    views: Dict[Tuple[str, str, str], Dict[str, Any]] = {}   # (board, delta, policy digest) -> view
    policies: Dict[str, Dict[str, Any]] = {}                 # digest -> {mode, policy, uses}

    for (board, delta), rows in rows_in.items():
        for r in rows:
            entry = policies.setdefault(r["digest"], {"mode": MODES[board], "policy": r["policy"], "uses": []})
            entry["uses"].append({"board": board, "delta": delta, "rowId": r["id"]})
            boards = ("pp", "full") if board == "pp" else ("ptp",)
            for b in boards:
                if (b, delta, r["digest"]) not in views:
                    title = f"Planner policy {r['digest'][:12]}"
                    log(f"pricing {b} {delta} {r['digest'][:12]}")
                    views[(b, delta, r["digest"])] = pricing.price_views(r["policy"], title, delta, [b])[b]

    evidence: Dict[str, Tuple[Optional[Dict[str, Any]], Optional[str]]] = {}
    for digest, entry in policies.items():
        view = next(v for (b, d, g), v in views.items() if g == digest)
        evidence[digest] = store.stomirror(view["policy_json"], model, "ptp" if entry["mode"] == MODES["ptp"] else "pp")

    panels: Dict[str, Any] = {}
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for (board, delta), board_rows in rows_in.items():
        for r in board_rows:
            if evidence[r["digest"]][0] is None:
                continue   # D8: only policies with accepted STO mirror evidence are listed; the report names the rest
            for b in (("pp", "full") if board == "pp" else ("ptp",)):
                view = copy.deepcopy(views[(b, delta, r["digest"])])
                title = f"STO frontier after {r['id']}, {MODES[board]}, n = 2048, delta = {mapping.DELTA_VALUES[delta]:g}" + (
                    ", full order finding" if b == "full" else "")
                view["title"] = view["policy_json"]["name"] = title
                mirror = evidence[r["digest"]][0]
                verification = {"stoMirror": mirror, "forshorPolicy": None}
                detail = mapping.panel_path(epoch, r["id"], b, delta)
                panel = mapping.panel_of(view, epoch=epoch, row_id=r["id"], board=b, delta=delta, verification=verification)
                panels[detail] = panel
                rows.setdefault(f"{b}/{delta}", []).append(mapping.row_of(
                    panel, origin="planner", created_at=r["createdAt"], pull_request_url=ARCHIVE_PR.format(r["pr"]) if r["pr"] else None,
                    submission_id=None, detail=detail))

    charts: Dict[str, Any] = {}
    benchmarks: Dict[str, Any] = {}
    for delta, file_key in FILE_KEY.items():
        for b in mapping.BOARDS:
            name = "fullalg" if b == "full" else f"frontier_{b.upper()}"
            series = {m: v for m, v in json.loads((eng.engine_dir / "cache" / f"{name}_{file_key}.json").read_text())["milestones"].items()}
            series = {m: v["points"] for m, v in series.items()}
            charts[mapping.chart_path(epoch, b, delta)] = mapping.chart_of(series, epoch=epoch, board=b, delta=delta)
            benchmarks[f"{b}/{delta}"] = mapping.benchmarks_of(series)

    updated_at = updated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = {"schema": "forshor.epoch-input/v2", "model": model, "rows": rows, "benchmarks": benchmarks, "submissions": [],
                "charts": charts, "panels": panels, "updatedAt": updated_at,
                "archiveCommit": archive_commit or _git("rev-parse", "HEAD"), "publisherCommit": publisher_commit}
    document["sourceDigest"] = sha256_hex(document)
    report = evidence_report(policies, evidence)
    return document, report


def evidence_report(policies: Dict[str, Dict[str, Any]], evidence: Dict[str, Tuple[Optional[Dict[str, Any]], Optional[str]]]) -> Dict[str, Any]:
    """Which planner policies have valid policy-level evidence for this epoch, and why not for the rest (open item O1b). Rows of policies without it are not exported (D8)."""
    items = []
    for digest, entry in sorted(policies.items(), key=lambda kv: (kv[1]["mode"], kv[0])):
        mirror, why = evidence[digest]
        items.append({"digest": digest, "mode": entry["mode"], "rows": len(entry["uses"]), "uses": entry["uses"],
                      "ladder": " -> ".join(f"{b.threshold}:{b.table.k}" for b in entry["policy"].bands),
                      "evidence": "valid" if mirror else "missing", "reason": why})
    rows = sum(i["rows"] for i in items)
    return {"policies": items, "distinctPolicies": len(items), "rowsTotal": rows,
            "rowsWithEvidence": sum(i["rows"] for i in items if i["evidence"] == "valid"),
            "rowsExcluded": sum(i["rows"] for i in items if i["evidence"] != "valid"),
            "policiesWithoutEvidence": [i["digest"] for i in items if i["evidence"] != "valid"]}
