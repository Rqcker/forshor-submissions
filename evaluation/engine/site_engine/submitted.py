"""Lean-verified banded policies for the full order-finding board (PLAN_v2 L1, 3.3 and 3.2).

A record is `engine/data/forshor_submitted/<id>/record.json`, written by the ForShor harness (Stage 3.2). Until that
harness exists the directory is absent and the board is empty: the site says so, and lists nothing it cannot prove.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from . import DATA

KIND = "forshor_submitted"
REQUIRED = ("id", "created_at", "bands", "single_run_T", "lean_verified")


def records() -> List[Dict[str, Any]]:
    """Every installed record that is Lean-verified, validated. A malformed record is an error, never skipped."""
    rows: List[Dict[str, Any]] = []
    for f in sorted((DATA / KIND).glob("*/record.json")) if (DATA / KIND).is_dir() else []:
        r = json.loads(f.read_text())
        if r.get("seeded_from_planner"):          # a seeded planner policy (seed_policies.py): see `seeded()`
            continue
        missing = [k for k in REQUIRED if k not in r]
        if missing:
            raise ValueError(f"{f}: record is missing {missing}")
        if r["lean_verified"] is True:
            rows.append(r)
    return rows


def table_digests() -> List[str]:
    """Digests of the tables of Lean-verified banded policies (kernel-checked C1 to C4): they are `lean` in `trust`.
    Includes the seeded planner policies ForShor's harness verified (`seed_forshor_verified`)."""
    return [b["digest"] for r in records() for b in r["bands"] if b.get("digest")] + \
        [d for ds in seed_forshor_verified().values() for d in ds]


SEED_RUNS = "seed_lean_runs.json"                 # ForShor harness outcomes of the seeds (seed_policies verify)
SEED_MIRROR_RUNS = "seed_sto_mirror_runs.json"    # STO policy-mirror outcomes of the seeds (./run.sh sto-policy --install)


def seed_forshor_verified() -> Dict[str, List[str]]:
    """Seed id -> table digests, for every seeded policy whose pinned ForShor run passed: status lean-ok, stage 5's
    conversion check agrees, and ForShor's printed policy equals the submitted one (so the tables ForShor's kernel
    checked are exactly these). Read from the pin and the seed's policy file of the run's variant, never from a
    generated record. Only the pin's current `runs` count (Stage 15: the latest ForShor); the ForShor-main runs under
    `superseded` are history."""
    pin = DATA / KIND / SEED_RUNS
    if not pin.is_file():
        return {}
    out: Dict[str, List[str]] = {}
    for r in json.loads(pin.read_text())["runs"].values():
        if r.get("status") == "lean-ok" and r.get("conversion_agrees") is True and r.get("printed_policy_matches") is True:
            # the policy ForShor ran: pre_exact (before the Stage 13 re-plan, site_policy.pre_exact.json) or replanned
            name = "site_policy.json" if r.get("variant") == "replanned" else "site_policy.pre_exact.json"
            f = DATA / KIND / r["id"] / name
            if not f.is_file():
                f = DATA / KIND / r["id"] / "site_policy.json"
            if f.is_file():
                from tcost import tables as tt
                ds = [b.table.digest for b in tt.load_policies(f)[0].bands]
                out[r["id"]] = list(dict.fromkeys(out.get(r["id"], []) + ds))
    return out


SEED_EXACT_RUNS = "seed_exact_runs.json"          # Stage 13: exact-width Lean runs of the re-planned seeds (PP + PTP)


def seed_exact_runs() -> Dict[str, Any]:
    pin = DATA / KIND / SEED_EXACT_RUNS
    return json.loads(pin.read_text()) if pin.is_file() else {"runs": {}}


def seed_mirror_runs() -> Dict[str, Any]:
    pin = DATA / KIND / SEED_MIRROR_RUNS
    return json.loads(pin.read_text()) if pin.is_file() else {"runs": {}}


def listing() -> Dict[str, Any]:
    # the listing carries what the board shows; the tables stay in the record (and in the policy panel)
    rows = [{**r, "bands": [{k: v for k, v in b.items() if k != "table"} for b in r["bands"]]} for r in records()]
    rows.sort(key=lambda r: (r["single_run_T"], r["created_at"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return {"policies": rows, "count": len(rows),
            "note": "Lean-verified banded policies (ForShor ShorPolicySubmission), ranked by single-run T of the full "
                    "order-finding run at n = 2048 in the paper model." if rows else
                    "No Lean-verified banded policy has been submitted yet. Submit one with ./run.sh forshor (see Participate); it is listed here once ForShor's Lean checks and the site's Python checks pass."}


SEED_REQUIRED = ("id", "mode", "bands", "provenance", "lean", "lean_verified", "trust", "flags")


def seeded() -> List[Dict[str, Any]]:
    """The policies seeded from the planner's outputs (`site_engine.seed_policies`), PP and PTP, Lean-verified as a policy
    or not (`lean_verified`, `lean.status`, `trust`). They store no T: a board prices each one live from
    `site_policy.json` (`tables.load_policies`). A malformed record is an error, never skipped."""
    rows: List[Dict[str, Any]] = []
    for f in sorted((DATA / KIND).glob("*/record.json")) if (DATA / KIND).is_dir() else []:
        r = json.loads(f.read_text())
        if not r.get("seeded_from_planner"):
            continue
        missing = [k for k in SEED_REQUIRED if k not in r]
        if missing:
            raise ValueError(f"{f}: seeded record is missing {missing}")
        r["site_policy_path"] = str(f.parent / r["files"]["site_policy"])
        rows.append(r)
    return rows
