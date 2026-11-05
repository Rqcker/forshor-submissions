"""Seed the policy-only boards with the planner outputs the site already has (one record per distinct policy).

    cd engine && ../.venv/bin/python -m site_engine.seed_policies            # (re)write engine/data/forshor_submitted/S*/
    cd engine && ../.venv/bin/python -m site_engine.seed_policies --check    # exit 1 if the installed seeds differ
    cd engine && ../.venv/bin/python -m site_engine.seed_policies verify --budget default [--ids S..] [--jobs 3]
    cd engine && ../.venv/bin/python -m site_engine.seed_policies verify --budget override [--ids S..]

Until the move to policy-only submissions every leaderboard row was `tcost.planner.optimal_policy` over the STO tables
submitted so far, so the boards held no real policy submission. This module turns each planner output the site shows
into a submittable policy and a record, marked `seeded_from_planner`:

  sources    n = 2048, at every delta engine/cache holds (SEED_DELTAS), both modes:
               * every PP / PTP leaderboard row (the best policy over the tables of submissions 1..i; the full-run board's
                 rows are the PP rows' policies), as `frontier.leaderboard`, `full_frontier.compute` and
                 `policy_view.for_leaderboard_row` build them;
               * every library milestone at n = 2048 (`frontier.planned`: kmy, champions, all_sto, dp), the frontier points
                 and the library policies of the page.
             The STO reference policy is not a planner output and is not seeded.
  dedupe     By policy content. The planner writes one band per width it visits; consecutive bands with the same table are
             merged, keeping the lowest threshold (`canonical`). `Policy.choose(w)` is unchanged at every width (tested),
             so the policy is priced the same at every n and in the full run, and has fewer bands for ForShor's budget.
  files      per record `engine/data/forshor_submitted/<id>/`: `record.json`, `site_policy.json` (the tcost policy JSON
             that intake accepts, the "Download policy JSON" format) and, for PP, `PolicyTemplate.lean`
             (`harness/policy_to_template.py`, what `./run.sh forshor-template` prints).
  no T       A record stores no T count: the board prices each record live.
  Lean       `verify` runs `harness/forshor_verify.py` (the code of `./run.sh forshor <policy.json> --from-policy`, stages
             0 to 4, then stage 5's conversion check without keeping its T) on the site policy, at ForShor's stock budget or
             at the documented site override, with a wall-clock limit per run, and pins the outcome in `SEED_RUNS`; the
             generator reads only that pin, so its output is deterministic.
             PTP has no ForShor harness: PTP records carry the flag `ptp-no-policy-harness`.
  STO mirror `./run.sh sto-policy --seeds --install` (harness/sto_policy_verify.py, PP and PTP until Stage 12) ran every
             seed through ShorTableOptimization's policy mirror at scanned widths (`seed_sto_mirror_runs.json`, kept as
             history). A pin whose site_policy sha256 differs from the record's is `stale`, not a pass.
  exact      Stage 13 (2026-10-28): the PP and PTP boards price at EXACT widths, so every seed's thresholds are RE-PLANNED
             (`replan`: `optimal_policy(widths="exact")` over the seed's own tables at n = 2048, delta = REPLAN_DELTA,
             then `canonical`). The seed keeps its id (the hash of the planner output it was seeded from); the scanned
             policy is kept as `history` in the record and as `site_policy.pre_exact.json`. Every re-planned seed is run
             through Lean at exact widths: PP `./run.sh sto-policy --seeds --install` (the live mirror,
             check_banded_policy.py --widths exact), PTP `./run.sh ptp-policy --seeds --install` (the PTP suite,
             check_ptp_policy.py --widths exact); both pin into `seed_exact_runs.json`, which the generator reads
             (`sto_mirror` in the record). A ForShor run pinned for the pre-re-plan policy is `stale` when the policy
             changed (its template sha256 differs).
  latest     Stage 15 (2026-10-28): the site's ForShor harness runs the LATEST ForShor (10fe620, Lean v4.36.0-rc1,
             `.cache/forshor_latest_build`). Each run is pinned with its `variant` ("pre_exact": the policy before the
             Stage 13 re-plan, `site_policy.pre_exact.json`, which the full-run board prices; "replanned": the current
             `site_policy.json`) and the ForShor commit and toolchain (`forshor`). The ForShor-main (v4.28.0) runs of
             Stage 11 are kept in the pin under `superseded` (history); they no longer support a trust level.
  PTP leaf   Stage 17 (2026-10-28): the PhaseTripleProduct board's leaf is the digital triple product
             (`tcost.bloqs.DigitalTripleLeaf`). The PTP seeds keep their ids: the planner outputs they were seeded from are
             regenerated under the leaf in force when they were seeded (the grid leaf, `SEED_LEAF`). Their thresholds are
             re-planned again, under exact widths AND the digital leaf (`replan`, the user's rule: seeds score as well as
             they can); the Stage 13 re-plan (exact widths, grid leaf) is kept as a second `history` entry and as
             `site_policy.pre_digital.json`, with its Lean run (pin key `superseded_stage13_grid_leaf`). Every re-planned
             PTP seed was run through the PTP suite again at exact widths. PP seeds are unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import DATA, ENGINE
from . import library as lib
from . import trust
from tcost import planner as tp
from tcost import tables as tt

ROOT = ENGINE.parent
HARNESS = ROOT / "harness"
OUT = DATA / "forshor_submitted"
SEED_RUNS = OUT / "seed_lean_runs.json"       # pinned harness outcomes (written by `verify`)
SEED_EVIDENCE = OUT / "seed_lean"             # ForShor's printed policy.json of each accepted run
SEED_MIRROR_RUNS = OUT / "seed_sto_mirror_runs.json"   # Stage 11 STO policy-mirror outcomes at scanned widths (history)
SEED_EXACT_RUNS = OUT / "seed_exact_runs.json"         # Stage 13 Lean outcomes at exact widths (PP mirror, PTP suite)
REPLAN_DELTA = 0.01
REPLAN_N = 2048
REPLAN_NOTE = "re-planned under exact widths, 2026-10-28"
REPLAN_NOTE_PTP = "re-planned under exact widths and the digital PTP leaf, 2026-10-28 (Stage 17)"
#: The leaf each mode's seeds were planned under when they were seeded (2026-10-27): the ids are hashes of those outputs.
SEED_LEAF = {tt.PAIR: "product", tt.TRIPLE: "grid"}
#: The key in `seed_exact_runs.json` that keeps the Stage 13 PTP runs (exact widths, grid-leaf thresholds) as history.
STAGE13_PTP_KEY = "superseded_stage13_grid_leaf"
RUN_ROOT = ROOT / ".cache" / "seed_runs"
SCHEMA = "sto-site-seeded-policy/1"
HERO_N = 2048
#: The deltas engine/cache holds frontiers for (1e-2, 3e-4, 1e-4, 1e-6).
SEED_DELTAS = (1e-2, 3e-4, 1e-4, 1e-6)
PREFIX = "S"
OVERRIDE_HEARTBEATS = 400_000_000           # README "Known limits": the documented site override
RUN_LIMIT_S = 1800                          # wall clock per harness run (build + print)
MEM_CAP_BYTES = 8 << 30                     # memory cap (Stage 15: 8 GB, was 6): enforced per stage by forshor_verify._run_rss
VARIANTS = ("pre_exact", "replanned")
VARIANT_FILE = {"pre_exact": "site_policy.pre_exact.json", "replanned": "site_policy.json"}
NOT_ATTEMPTED_K6 = ("refused at default budget; override not attempted (k = 6 does not finish at ForShor-main, "
                    "PLAN_v2 3.2)")
PLANNER = "engine/tcost/planner.py::optimal_policy (one_table_per_k=True)"
CANON_NOTE = ("consecutive planner bands with the same table merged, lowest threshold kept; Policy.choose(w) is unchanged "
              "at every width, so every price is unchanged")


# ---------------------------------------------------------------------------------------------- enumeration
def canonical(P: tt.Policy, name: str = "") -> tt.Policy:
    bands: List[tt.Band] = []
    for b in P.bands:
        if bands and bands[-1].table.digest == b.table.digest:
            bands[-1] = tt.Band(b.threshold, b.table)
        else:
            bands.append(b)
    return tt.Policy(mode=P.mode, bands=tuple(bands), name=name or P.name, widths=P.widths)


def replan(P: tt.Policy, name: str = "", leaf: str = "product") -> tt.Policy:
    """Stage 13: the seed's thresholds re-planned under exact widths over its own tables (n = REPLAN_N,
    delta = REPLAN_DELTA), canonical. The planner may leave one of the seed's tables unused. Stage 17: under the
    site's leaf of each mode (`leaf="grid"` gives the Stage 13 PTP re-plan, kept as history)."""
    tabs = list(dict.fromkeys(b.table for b in P.bands))
    Q, _ = tp.optimal_policy(tabs, P.mode, REPLAN_N, REPLAN_DELTA, name=name or P.name, widths="exact", leaf=leaf)
    return canonical(Q, name=name or P.name)


def policy_id(P: tt.Policy) -> str:
    key = json.dumps({"mode": P.mode, "bands": [[b.threshold, b.table.digest] for b in P.bands]}, separators=(",", ":"))
    return PREFIX + hashlib.sha256(key.encode()).hexdigest()[:12]


def _short(mode: str) -> str:
    return "PP" if mode == tt.PAIR else "PTP"


def planner_outputs(deltas=SEED_DELTAS, n: int = HERO_N) -> List[Tuple[tt.Policy, Dict[str, Any]]]:
    """Every planner output the site shows at n, with its provenance, in a fixed order (mode, delta, row, milestone)."""
    out = []
    subs = lib.submissions()
    for mode in lib.MODES:
        for d in deltas:
            for i, s in enumerate(subs, start=1):
                if not any(T.mode == mode for T in s.tables.values()):
                    continue
                pool = [T for T in lib.sto_tables(upto=i) if T.mode == mode]
                P, _ = tp.optimal_policy(pool, mode, n, d, name=f"upto:{s.id}", widths="scan", leaf=SEED_LEAF[mode])
                boards = [f"{_short(mode)} leaderboard"] + (["full-run leaderboard"] if mode == tt.PAIR else [])
                out.append((P, {"source": "leaderboard", "boards": boards, "mode": mode, "n": n, "delta": d,
                                "row": s.id, "row_number": i, "row_created_at": s.created_at,
                                "tables_from": [x.id for x in subs[:i]]}))
            for key, label, _ in lib.PLANNED:
                P, _ = tp.optimal_policy(lib.milestone_tables(key, mode), mode, n, d, name=f"{key}@{n}", widths="scan",
                                         leaf=SEED_LEAF[mode])
                boards = [f"{_short(mode)} frontier n = {n}", "library policies"] + \
                    (["full-run frontier n = 2048"] if mode == tt.PAIR else [])
                out.append((P, {"source": "milestone", "boards": boards, "mode": mode, "n": n, "delta": d,
                                "milestone": key, "milestone_label": label}))
    return out


def distinct(outputs) -> Dict[str, Dict[str, Any]]:
    """id -> {"policy": canonical policy, "occurrences": [...]} in first-seen order."""
    seeds: Dict[str, Dict[str, Any]] = {}
    for P, prov in outputs:
        C = canonical(P)
        sid = policy_id(C)
        e = seeds.setdefault(sid, {"original": canonical(P, name=f"seeded {sid}"), "occurrences": []})
        if "policy" not in e:
            e["policy"] = replan(e["original"], name=f"seeded {sid}")
            if P.mode == tt.TRIPLE:
                e["stage13"] = replan(e["original"], name=f"seeded {sid}", leaf="grid")
        e["occurrences"].append({**prov, "planner_bands": [[b.threshold, b.table.k, b.table.digest[:12]] for b in P.bands]})
    return seeds


# ---------------------------------------------------------------------------------------------- files
def template(P: tt.Policy) -> Optional[str]:
    if P.mode != tt.PAIR:
        return None
    if str(HARNESS) not in sys.path:
        sys.path.insert(0, str(HARNESS))
    from policy_to_template import template_text
    return template_text(P)


def _origin(digest: str) -> Dict[str, Any]:
    sid = lib.table_origin().get(digest)
    for s in lib.submissions():
        if s.id == sid:
            return {"id": sid, "kind": "STO submission", "created_at": s.created_at, "pr": s.pr}
    if any(r["id"] == sid for r in lib.personal_tables()):
        return {"id": sid, "kind": "DP library", "created_at": None}
    if any(r["id"] == sid for r in lib.kmy_tables()):
        return {"id": sid, "kind": "KMY benchmark", "created_at": None}
    return {"id": sid, "kind": "unknown", "created_at": None}


def load_runs() -> Dict[str, Any]:
    return json.loads(SEED_RUNS.read_text()) if SEED_RUNS.is_file() else {"runs": {}}


def _runs_of(sid: str, runs: Dict[str, Any], variant: str, key: str = "runs") -> List[Dict[str, Any]]:
    order = {"default": 0, "override": 1}
    rs = [r for r in (runs.get(key) or {}).values() if r.get("id") == sid and r.get("variant", "pre_exact") == variant]
    return sorted(rs, key=lambda r: order.get(r.get("budget"), 2))


def _superseded_summary(sid: str, runs: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"budget": r.get("budget"), "status": r.get("status"), "reason": r.get("reason"),
             "forshor": (r.get("forshor") or {}).get("label") or SUPERSEDED_LABEL}
            for r in _runs_of(sid, runs, "pre_exact", "superseded")]


SUPERSEDED_LABEL = "ForShor-main (no commit, Lean v4.28.0)"


def _slim(rs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Runs as a record keeps them: without the stage-5 prices (a record stores no T; the pin keeps them)."""
    return [{k: v for k, v in r.items() if k != "price"} for r in rs]


def _forshor_of(r: Dict[str, Any]) -> Optional[str]:
    f = r.get("forshor") or {}
    if not f:
        return None
    return f"ForShor {f.get('commit_short') or f.get('commit') or '?'}, {str(f.get('lean_toolchain') or '').split(':')[-1]}"


def lean_status(sid: str, P: tt.Policy, runs: Dict[str, Any], tpl_sha: Optional[str] = None,
                changed: bool = False, variant: Optional[str] = None) -> Dict[str, Any]:
    """The ForShor status of one policy of a seed. `changed`: the policy is the Stage 13 re-plan (variant "replanned");
    otherwise the pre-re-plan policy (variant "pre_exact"). Only the current pin's runs (the latest ForShor) count; the
    ForShor-main runs under `superseded` are reported as history."""
    if P.mode != tt.PAIR:
        return {"status": "no-policy-harness", "policy_verified": False, "budget": None, "runs": [],
                "reason": "ForShor's policy submission is PhaseProduct only; there is no PTP policy harness. Tables only."}
    variant = variant or ("replanned" if changed else "pre_exact")
    sup = _superseded_summary(sid, runs)
    base = {"variant": variant, "superseded": sup if variant == "pre_exact" else []}
    every = _slim(_runs_of(sid, runs, variant))
    rs = [r for r in every if tpl_sha is None or r.get("template_sha256") in (None, tpl_sha)]
    if every and not rs:
        return {**base, "status": "stale", "policy_verified": False, "budget": None, "runs": every,
                "reason": "the pinned ForShor run checked another template than this policy's; re-run it"}
    if not rs:
        if variant == "replanned" and (_runs_of(sid, runs, "pre_exact") or sup):
            return {**base, "status": "stale", "policy_verified": False, "budget": None, "runs": [],
                    "reason": "ForShor's pinned runs checked this seed's policy before it was " + REPLAN_NOTE +
                              " (the template differs); the re-planned policy was not run through ForShor's harness"}
        hist = "; ".join(f"{x['budget']}: {x['status']}" for x in sup)
        return {**base, "status": "not-run", "policy_verified": False, "budget": None, "runs": [],
                "reason": "not run on the latest ForShor (10fe620)" + (f"; {SUPERSEDED_LABEL}, superseded: {hist}" if hist else "")}
    ok = next((r for r in rs if r["status"] == "lean-ok" and r.get("conversion_agrees") is True), None)
    lean_only = next((r for r in rs if r["status"] == "lean-ok"), None)
    if lean_only and not ok:
        why = (f"the conversion check failed: {lean_only['conversion_check_error']}" if lean_only.get("conversion_check_error")
               else "the conversion check did not pass")
        return {**base, "status": "lean-ok-price-check-failed", "policy_verified": False, "budget": lean_only["budget"], "runs": rs,
                "forshor": _forshor_of(lean_only),
                "reason": f"ForShor's Lean checks passed ({lean_only['budget']} budget) but the harness's stage 5 refuses: {why}"}
    if ok:
        reason = None
        if ok["budget"] != "default":
            d = next((r for r in rs if r["budget"] == "default"), None)
            reason = ("refused at ForShor's stock budget; accepted under the documented site override of the heartbeat cap"
                      if d is not None and d["status"] != "lean-ok" else
                      "accepted under the documented site override of the heartbeat cap (the stock budget refused it on "
                      "ForShor-main and was not re-run on the latest ForShor)")
        return {**base, "status": "policy-verified", "policy_verified": True, "budget": ok["budget"], "runs": rs,
                "forshor": _forshor_of(ok), "reason": reason}
    last = rs[-1]                                   # the override run when there is one
    if last["status"] == "not-attempted":
        return {**base, "status": "refused", "policy_verified": False, "budget": None, "runs": rs, "reason": last.get("reason") or NOT_ATTEMPTED_K6}
    if last["status"] == "killed":                  # stopped by the site, not a Lean verdict
        return {**base, "status": "killed", "policy_verified": False, "budget": None, "runs": rs,
                "reason": "; ".join(f"{r['budget']}: {r['reason']}" for r in rs)}
    return {**base, "status": "refused", "policy_verified": False, "budget": None, "runs": rs,
            "reason": "; ".join(f"{r['budget']}: {r['reason']}" for r in rs)}


def load_mirror_runs() -> Dict[str, Any]:
    """The Stage 13 exact-width Lean pin (the records' `sto_mirror`)."""
    return json.loads(SEED_EXACT_RUNS.read_text()) if SEED_EXACT_RUNS.is_file() else {"runs": {}}


def load_scan_mirror_runs() -> Dict[str, Any]:
    """The Stage 11 scanned-width STO mirror pin (history)."""
    return json.loads(SEED_MIRROR_RUNS.read_text()) if SEED_MIRROR_RUNS.is_file() else {"runs": {}}


def _ladder_str(lad) -> str:
    return " -> ".join(f"{w}:{k if k else 'leaf'}" for w, k in lad)


def mirror_status(sid: str, site_sha: str, site_ladder, mirror_runs: Dict[str, Any]) -> Dict[str, Any]:
    """The pinned STO policy-mirror outcome of a seed, as the record keeps it (no Lean is run here)."""
    r = mirror_runs["runs"].get(sid)
    if r is None:
        return {"status": "not-run", "reason": "not run yet at exact widths (./run.sh sto-policy --seeds --install; "
                                               "PTP: ./run.sh ptp-policy --seeds --install)"}
    if r.get("site_policy_sha256") != site_sha:
        return {"status": "stale", "reason": "the pinned run checked a different site_policy.json; re-run it",
                "checked_at": r.get("checked_at")}
    st = {"accepted": "accepted", "rejected": "rejected"}.get(r.get("verdict"), "killed" if r.get("verdict") == "killed" else "error")
    lad = [[x["width"], x["k"]] for x in (r.get("exact_ladder") or r.get("ladder") or [])]
    return {"status": st, "stage": r.get("stage"), "reason": r.get("reason"), "target_width": r.get("target_width"),
            "seconds": r.get("seconds"), "peak_gb": r.get("peak_gb"), "cap_gb": r.get("cap_gb"),
            "stage_seconds": r.get("stage_seconds"), "replay": r.get("replay"), "axioms": r.get("axioms"),
            "statement_pinned": r.get("statement_pinned"), "ladder_str": _ladder_str(lad) if lad else None,
            "ladder_matches_site": (lad == [list(x) for x in site_ladder]) if lad else None,
            "mirror_tree_sha256": r.get("mirror_tree_sha256"), "lean_toolchain": r.get("lean_toolchain"),
            "checked_at": r.get("checked_at"), "evidence": r.get("evidence"), "command": r.get("command"),
            "widths": r.get("widths", "scan"), "checker": r.get("checker"),
            "exact_ladder_kernel_proved": r.get("exact_ladder_kernel_proved"),
            "proves": EXACT_PROVES.get(r.get("checker"), EXACT_PROVES["sto-mirror"])}


#: What an accepted exact-width run proves, per checker (the record's `sto_mirror.proves`).
EXACT_PROVES = {
    "sto-mirror": "STO policy mirror, --widths exact: policy.check (STO's table contract per band, thresholds decrease) and "
                  "chain_contracts at 2048 by decide +kernel; correct (evalPhase n φ xs = φ·∏xs at every n); correct_reg and "
                  "capacity (register semantics at the scan); correct_tight (the wrapping register semantics at the exact "
                  "widths gives φ·∏xs on operands fitting n) and exact_ladder (the exact ladder at 2048, by the kernel); "
                  "kernel-replayed, axioms within {propext, Classical.choice, Quot.sound}, statements pinned. Not proved: "
                  "gate-level equivalence with any circuit, the +1 root, reserve qubits.",
    "ptp-suite": "PTP suite (TableGeneration.RecursiveCost.PTP), --widths exact: the PTP check and chain contracts; correct "
                 "(evalPhase = φ·xyz at every n), correct_wrapped, width_sound; chain_contracts_exact, ladder_exact (the "
                 "exact ladder at 2048, by the kernel), correct_exact (the register semantics at the exact widths gives "
                 "φ·xyz on operands fitting n), exact_minimal, exact_le_scan; kernel-replayed, axioms within {propext, "
                 "Classical.choice, Quot.sound}, statements pinned. PTP's register model is by analogy with PP (no PTP "
                 "circuit exists); not circuit-level.",
}


def build_record(sid: str, e: Dict[str, Any], runs: Dict[str, Any],
                 mirror_runs: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[str]]:
    P: tt.Policy = e["policy"]
    old: tt.Policy = e.get("original", P)
    occ = e["occurrences"]
    doc = P.to_json()
    tpl = template(P)
    old_doc = old.to_json()
    old_tpl = template(old)
    origins = {b.table.digest: _origin(b.table.digest) for b in P.bands}
    dates = [o["created_at"] for o in origins.values() if o["created_at"]]
    tl = trust.of_digests(b.table.digest for b in P.bands)
    ttag, _ = trust.tag(tl)
    changed = [(b.threshold, b.table.digest) for b in P.bands] != [(b.threshold, b.table.digest) for b in old.bands]
    lean = lean_status(sid, P, runs, hashlib.sha256(tpl.encode()).hexdigest() if tpl else None, changed)
    ok_run = next((r for r in lean["runs"] if r["status"] == "lean-ok" and r.get("conversion_agrees") is True), None) \
        if lean["policy_verified"] else None          # a stale run (Stage 13 re-plan) verifies nothing about this policy
    site_sha = hashlib.sha256(_dump(doc).encode()).hexdigest()
    mirror = mirror_status(sid, site_sha, P.ladder(HERO_N), mirror_runs if mirror_runs is not None else load_mirror_runs())
    old_sha = hashlib.sha256(_dump(old_doc).encode()).hexdigest()
    old_mirror = mirror_status(sid, old_sha, old.with_widths("scan").ladder(HERO_N), load_scan_mirror_runs())
    changed = [(b.threshold, b.table.digest) for b in P.bands] != [(b.threshold, b.table.digest) for b in old.bands]
    s13: Optional[tt.Policy] = e.get("stage13")
    s13_doc = s13.to_json() if s13 is not None else None
    s13_changed = s13 is not None and [(b.threshold, b.table.digest) for b in P.bands] != \
        [(b.threshold, b.table.digest) for b in s13.bands]
    flags = ["seeded-from-planner"] + (["ptp-no-policy-harness"] if P.mode == tt.TRIPLE else []) \
        + (["site-override"] if ok_run and ok_run["budget"] == "override" else []) \
        + (["sto-mirror-algebra-verified"] if mirror["status"] == "accepted" else []) \
        + (["exact-widths-lean-verified"] if mirror["status"] == "accepted" and mirror.get("widths") == "exact" else []) \
        + (["replanned-exact-widths"] if changed else []) \
        + (["replanned-digital-ptp-leaf"] if s13_changed else [])
    ptrust = trust.policy_trust({"lean": lean, "sto_mirror": mirror})
    rec = {
        "schema": SCHEMA, "id": sid, "seeded_from_planner": True, "mode": P.mode,
        # the date the policy's tables were all public (newest STO origin); not a submission date
        "created_at": max(dates) if dates else None,
        "bands": [{"threshold": b.threshold, "k": b.table.k, "digest": b.table.digest, "table_id": origins[b.table.digest]["id"],
                   "table": b.table.to_json()} for b in P.bands],
        "widths": "exact",
        "ladder_2048": [[w, k] for w, k in P.ladder(HERO_N)],
        "ladder_2048_scan": [[w, k] for w, k in P.with_widths("scan").ladder(HERO_N)],
        "replanned": {"note": REPLAN_NOTE_PTP if P.mode == tt.TRIPLE else REPLAN_NOTE, "changed": changed,
                      "planner": PLANNER + ', widths="exact"' + (', leaf="product" (digital PTP leaf)' if P.mode == tt.TRIPLE else ""),
                      "n": REPLAN_N, "delta": REPLAN_DELTA, "tables": "the seed's own tables",
                      "unused_tables": sorted({b.table.digest[:12] for b in old.bands} - {b.table.digest[:12] for b in P.bands})},
        "history": [{"provenance": "planner output at ForShor scanned widths (seeded 2026-10-27); superseded: " + REPLAN_NOTE,
                     "widths": "scan",
                     "bands": [{"threshold": b.threshold, "k": b.table.k, "digest": b.table.digest} for b in old.bands],
                     "ladder_2048_scan": [[w, k] for w, k in old.with_widths("scan").ladder(HERO_N)],
                     "ladder_2048_exact": [[w, k] for w, k in old.with_widths("exact").ladder(HERO_N)],
                     "site_policy": "site_policy.pre_exact.json", "site_policy_sha256": old_sha,
                     "template_sha256": hashlib.sha256(old_tpl.encode()).hexdigest() if old_tpl else None,
                     # the trust of THIS (pre-re-plan) policy: the full order-finding board, which keeps ForShor's
                     # scanned widths, prices it (full_frontier._records) and shows this trust
                     "lean": lean_status(sid, old, runs, hashlib.sha256(old_tpl.encode()).hexdigest() if old_tpl else None),
                     "sto_mirror": old_mirror}] + ([_stage13_history(sid, s13, s13_doc)] if s13 is not None else []),
        "files": {"site_policy": "site_policy.json", "template": "PolicyTemplate.lean" if tpl else None,
                  "site_policy_pre_exact": "site_policy.pre_exact.json",
                  **({"site_policy_pre_digital": "site_policy.pre_digital.json"} if s13 is not None else {})},
        "site_policy_sha256": site_sha,
        "template_sha256": hashlib.sha256(tpl.encode()).hexdigest() if tpl else None,
        "provenance": {
            "planner": PLANNER, "n": HERO_N, "canonicalized": CANON_NOTE,
            "first_seen": _first(occ), "occurrences": occ,
            "tables": [{"digest": d, "k": next(b.table.k for b in P.bands if b.table.digest == d), **o} for d, o in origins.items()],
        },
        "lean": lean,
        "lean_verified": lean["policy_verified"],          # ForShor policy-verified only (never the STO mirror)
        "sto_mirror": mirror,
        "site_override": ({"max_heartbeats": ok_run["max_heartbeats"], "stock": 4_000_000,
                           "note": "site override of ForShor's resource cap in PolicyCheck.lean; not ForShor's stock check"}
                          if ok_run and ok_run["budget"] == "override" else None),
        "trust": {"policy": ptrust["tag"], "policy_level": ptrust["level"], "policy_kind": ptrust["kind"],
                  "policy_text": ptrust["text"], "tables": {**tl, "tag": ttag},
                  "cost": "no T stored: the board prices this record live (counted in Python, not proved in Lean)"},
        "flags": flags,
    }
    return rec, doc, tpl, old_doc, s13_doc


def _stage13_history(sid: str, s13: tt.Policy, s13_doc: Dict[str, Any]) -> Dict[str, Any]:
    """The Stage 13 PTP re-plan (exact widths, grid leaf), superseded by the Stage 17 re-plan, with its Lean run."""
    sha = hashlib.sha256(_dump(s13_doc).encode()).hexdigest()
    pin = load_mirror_runs().get(STAGE13_PTP_KEY) or {}
    return {"provenance": "re-planned under exact widths with the grid PTP leaf (Stage 13, 2026-10-28); superseded: "
                          + REPLAN_NOTE_PTP,
            "widths": "exact", "leaf": "grid",
            "bands": [{"threshold": b.threshold, "k": b.table.k, "digest": b.table.digest} for b in s13.bands],
            "ladder_2048_exact": [[w, k] for w, k in s13.ladder(HERO_N)],
            "site_policy": "site_policy.pre_digital.json", "site_policy_sha256": sha,
            "sto_mirror": mirror_status(sid, sha, s13.ladder(HERO_N), {"runs": pin})}


def _first(occ: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The earliest place the policy appears: the first leaderboard row (date order), else the first milestone."""
    rows = [o for o in occ if o["source"] == "leaderboard"]
    o = min(rows, key=lambda o: (o["row_number"], SEED_DELTAS.index(o["delta"]))) if rows else occ[0]
    return {k: o[k] for k in ("source", "row", "row_created_at", "milestone", "delta") if k in o}


def _dump(x: Any) -> str:
    return json.dumps(x, indent=1, sort_keys=False) + "\n"


def generate(deltas=SEED_DELTAS, outputs=None) -> Dict[str, Dict[str, str]]:
    """Every seed's files as {id: {filename: text}} (no disk writes). `outputs`: `planner_outputs(deltas)`, if at hand."""
    runs, mruns = load_runs(), load_mirror_runs()
    files: Dict[str, Dict[str, str]] = {}
    for sid, e in distinct(outputs if outputs is not None else planner_outputs(deltas)).items():
        rec, doc, tpl, old_doc, s13_doc = build_record(sid, e, runs, mruns)
        f = {"record.json": _dump(rec), "site_policy.json": _dump(doc), "site_policy.pre_exact.json": _dump(old_doc)}
        if s13_doc is not None:
            f["site_policy.pre_digital.json"] = _dump(s13_doc)
        if tpl:
            f["PolicyTemplate.lean"] = tpl
        files[sid] = f
    return files


def installed_ids() -> List[str]:
    return sorted(p.name for p in OUT.glob(f"{PREFIX}*") if p.is_dir() and (p / "record.json").is_file()
                  and json.loads((p / "record.json").read_text()).get("seeded_from_planner"))


def write(files: Dict[str, Dict[str, str]]) -> List[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in set(installed_ids()) - set(files):
        shutil.rmtree(OUT / stale)
    for sid, fs in files.items():
        d = OUT / sid
        if d.is_dir():
            for p in d.iterdir():
                if p.is_file() and p.name not in fs:
                    p.unlink()
        d.mkdir(exist_ok=True)
        for name, text in fs.items():
            (d / name).write_text(text)
    return sorted(files)


def differs(files: Dict[str, Dict[str, str]]) -> List[str]:
    bad = sorted(set(installed_ids()) ^ set(files))
    for sid, fs in files.items():
        d = OUT / sid
        for name, text in fs.items():
            if not (d / name).is_file() or (d / name).read_text() != text:
                bad.append(f"{sid}/{name}")
    return bad


# ---------------------------------------------------------------------------------------------- Lean
def _classify(err: str, limit: int = RUN_LIMIT_S) -> str:
    e = err.lower()
    if "heartbeat" in e:
        return "ForShor's PolicyCheck exceeded its maxHeartbeats budget"
    if "out of memory" in e or "killed" in e:
        return "out of memory"
    return err.strip().splitlines()[0][:300] if err.strip() else "unknown failure"


def run_key(sid: str, budget: str, variant: str = "pre_exact") -> str:
    return f"{sid}:{budget}" if variant == "pre_exact" else f"{sid}:{variant}:{budget}"


def verify_one(sid: str, budget: str, limit: int = RUN_LIMIT_S, variant: str = "pre_exact") -> Dict[str, Any]:
    """One harness run, in this process: `forshor_verify.run(..., from_policy=True)` (the `./run.sh forshor
    <policy.json> --from-policy` path), never installed. `variant`: "pre_exact" (`site_policy.pre_exact.json`, the policy
    the full-run board prices) or "replanned" (`site_policy.json`). The harness waits while two or more `lean` run,
    runs each stage in its own process group with an 8 GB cap, and `limit` is one wall-clock deadline over build and
    print. Then stage 5's conversion check, and the full-run price the site shows for this policy."""
    if variant not in VARIANTS:
        raise ValueError(f"variant {variant!r}")
    if str(HARNESS) not in sys.path:
        sys.path.insert(0, str(HARNESS))
    import forshor_verify as fv
    from . import fullalg as fa
    inner = fv._run_rss
    deadline = [None]

    def bounded(cmd, cwd, timeout, log, stdout_to=None, **kw):
        if deadline[0] is None:          # the clock starts at the first Lean stage (after the wait for a Lean slot)
            deadline[0] = time.time() + limit
        return inner(cmd, cwd, max(1, min(timeout, int(deadline[0] - time.time()))), log, stdout_to, **kw)
    hb = OVERRIDE_HEARTBEATS if budget == "override" else None
    src = OUT / sid / VARIANT_FILE[variant]
    out_root = RUN_ROOT / f"{sid}-{variant}-{budget}"
    forshor = Path(os.environ.get("FORSHOR_BUILD", fv.DEFAULT_FORSHOR))
    mine = tt.load_policies(src)[0]
    tpl = template(mine).encode()
    t0 = time.time()
    res: Dict[str, Any] = {"id": sid, "variant": variant, "budget": budget, "max_heartbeats": hb or fv.STOCK_HEARTBEATS,
                           "limit_s": limit, "mem_cap_gb": fv.MEM_CAP_BYTES / (1 << 30),
                           "site_policy": VARIANT_FILE[variant],
                           "site_policy_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                           "template_sha256": hashlib.sha256(tpl).hexdigest(),
                           "forshor": fv.forshor_provenance(forshor),
                           "command": f"./run.sh forshor engine/data/forshor_submitted/{sid}/{VARIANT_FILE[variant]} --from-policy"
                                      + (f" --max-heartbeats {hb}" if hb else ""),
                           "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    fv._run_rss = bounded
    try:
        # stages 0-4 (intake, scans, lexical gate, Lean build and print, Python checks); the record is ours (no T stored)
        r = fv.run(src, forshor, out_root, 0.01, lean_only=True, from_policy=True, max_heartbeats=hb)
        pj = Path(r["policy_json"])
        pdoc = json.loads(pj.read_text())
        P = fv.policy_from_doc(pdoc)
        same = [(b.threshold, b.table.digest) for b in P.bands] == [(b.threshold, b.table.digest) for b in mine.bands]
        res.update({"status": "lean-ok" if same else "mismatch", "stage": None,
                    "reason": None if same else "ForShor's printed policy differs from the submitted policy",
                    "harness_id": r["id"], "harness_id_matches_template": r["id"] == f"F{fv.content_id(tpl)[:12]}",
                    "forshor_tree_sha256": fv._tree_digest(forshor), "lean_version": fv._version(["lean", "--version"], forshor),
                    "reference_document": r["forshor"].get("reference_document"),
                    "printed_trial_count": (pdoc.get("benchmark") or {}).get("trial_count"),
                    "run": {**r["stats"], "seconds": r["seconds"]}, "printed_policy_matches": same})
        # stage 5's conversion check (the printed document and the converted policy price alike), and the full-run price
        # the site shows for this policy (fullalg on the site policy as the board loads it)
        try:
            t5 = time.time()
            pr = fv.price(pdoc, P, 0.01)
            # Stage 18: the site's price on the printed document's own IR and width rule (a stock-tree document: scan)
            site_T = float(fa.price_full(mine, fa.HERO_N, 0.01, detail=False, rule=fa.doc_width_rule(pdoc))["T"])
            res["conversion_agrees"] = bool(pr["native"]["conversion_agrees"])
            res["price"] = {"T_document": pr["native"]["T_document"], "T_converted_policy": pr["native"]["T_converted_policy"],
                            "site_full_run_T": site_T, "agrees_with_site": pr["native"]["T_document"] == site_T,
                            "seconds": round(time.time() - t5, 1)}
        except Exception as e:  # noqa: BLE001
            res["conversion_agrees"] = None
            res["conversion_check_error"] = f"{type(e).__name__}: {e}"[:300]
        SEED_EVIDENCE.mkdir(parents=True, exist_ok=True)
        ev = SEED_EVIDENCE / f"{sid}.{variant}.{budget}.forshor_policy.json"
        shutil.copy2(pj, ev)
        res["evidence"] = str(ev.relative_to(OUT))
    except fv.HarnessError as e:
        d = e.detail
        if "MEMCAP" in d:           # our kill, not a Lean verdict
            res.update({"status": "killed", "stage": e.stage, "reason": "killed: memory cap 8 GB", "detail_tail": d[-800:]})
        elif "timeout after" in d.lower():
            res.update({"status": "killed", "stage": e.stage, "reason": f"killed: timeout ({limit} s)", "detail_tail": d[-800:]})
        else:
            res.update({"status": "refused", "stage": e.stage, "reason": _classify(d, limit), "detail_tail": d[-800:]})
    except Exception as e:  # noqa: BLE001  (recorded, never hidden)
        res.update({"status": "error", "stage": None, "reason": f"{type(e).__name__}: {e}"[:300]})
    finally:
        fv._run_rss = inner
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _pin(results: List[Dict[str, Any]]) -> None:
    runs = load_runs()
    for r in results:
        runs["runs"][run_key(r["id"], r["budget"], r.get("variant", "pre_exact"))] = r
    runs["runs"] = dict(sorted(runs["runs"].items()))
    runs["note"] = ("Outcomes of harness/forshor_verify.py on the seeded site policies (seed_policies verify). "
                    "status lean-ok = ForShor's lake build PolicySubmission and lake exe forshor_policy_submission passed and "
                    "the site's Python checks (stage 4) passed; conversion_agrees = stage 5's conversion check; killed = "
                    "stopped by the site (memory cap or timeout), not a Lean verdict; not-attempted = not run on purpose. "
                    "The record itself is written by seed_policies. Stage 15 (2026-10-28): `runs` are runs on the latest "
                    "ForShor (10fe620, Lean v4.36.0-rc1; each names its `forshor` commit and toolchain and its `variant`: "
                    "pre_exact = site_policy.pre_exact.json, replanned = site_policy.json); `superseded` keeps the "
                    "ForShor-main (Lean v4.28.0) runs of Stage 11 as history: they support no trust level.")
    SEED_RUNS.write_text(json.dumps(runs, indent=1) + "\n")


def verify(ids: List[str], budget: str, jobs: int, limit: int, variant: str = "pre_exact") -> List[Dict[str, Any]]:
    """Run each id in its own process (the harness patches module state), `jobs` at a time (1: the machine rule);
    pin each result as it lands."""
    def one(sid):
        cmd = [sys.executable, "-m", "site_engine.seed_policies", "verify-one", sid, "--budget", budget, "--limit", str(limit),
               "--variant", variant]
        p = subprocess.run(cmd, cwd=ENGINE, capture_output=True, text=True)
        try:
            r = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            r = {"id": sid, "variant": variant, "budget": budget, "status": "error", "reason": (p.stderr or p.stdout)[-300:]}
        print(f"{sid} {budget}: {r['status']} {r.get('reason') or ''} ({r.get('seconds')} s)", flush=True)
        return r
    from concurrent.futures import as_completed
    results = []
    if budget == "override":
        k6 = [s for s in ids if any(b.table.k == 6 for b in tt.load_policies(OUT / s / VARIANT_FILE[variant])[0].bands)]
        for s in k6:
            r = {"id": s, "variant": variant, "budget": budget, "status": "not-attempted", "stage": None, "reason": NOT_ATTEMPTED_K6}
            results.append(r)
            _pin([r])
        ids = [s for s in ids if s not in k6]
    with ThreadPoolExecutor(jobs) as ex:
        for f in as_completed([ex.submit(one, s) for s in ids]):
            r = f.result()
            results.append(r)
            _pin([r])
    write(generate())
    return results


def recheck_conversion() -> List[Dict[str, Any]]:
    """Re-run stage 5's conversion check on ForShor's printed document of every Lean-accepted run (no Lean needed)."""
    if str(HARNESS) not in sys.path:
        sys.path.insert(0, str(HARNESS))
    import forshor_verify as fv
    runs, out = load_runs(), []
    for key, r in runs["runs"].items():
        if r["status"] != "lean-ok":
            continue
        doc = json.loads((OUT / r["evidence"]).read_text())
        r.pop("conversion_check_error", None)
        try:
            pr = fv.price(doc, fv.policy_from_doc(doc), 0.01)
            r["conversion_agrees"] = bool(pr["native"]["conversion_agrees"])
        except Exception as e:  # noqa: BLE001
            r["conversion_agrees"] = None
            r["conversion_check_error"] = f"{type(e).__name__}: {e}"[:300]
        out.append(r)
    _pin(out)
    write(generate())
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the installed seeds differ from what would be written")
    sub = ap.add_subparsers(dest="cmd")
    v = sub.add_parser("verify")
    v.add_argument("--ids", nargs="*", default=None)
    v.add_argument("--budget", choices=("default", "override"), default="default")
    v.add_argument("--jobs", type=int, default=1)
    v.add_argument("--limit", type=int, default=RUN_LIMIT_S)
    v.add_argument("--variant", choices=VARIANTS, default="pre_exact")
    o = sub.add_parser("verify-one")
    o.add_argument("id")
    o.add_argument("--budget", choices=("default", "override"), default="default")
    o.add_argument("--limit", type=int, default=RUN_LIMIT_S)
    o.add_argument("--variant", choices=VARIANTS, default="pre_exact")
    sub.add_parser("recheck-conversion")
    a = ap.parse_args(argv)
    if a.cmd == "recheck-conversion":
        for r in recheck_conversion():
            print(r["id"], r["budget"], r["conversion_agrees"], r.get("conversion_check_error") or "")
        return 0
    if a.cmd == "verify-one":
        print(json.dumps(verify_one(a.id, a.budget, a.limit, a.variant)))
        return 0
    if a.cmd == "verify":
        files = generate()
        write(files)
        ids = a.ids or [s for s, f in files.items() if "PolicyTemplate.lean" in f]
        verify(ids, a.budget, a.jobs, a.limit, a.variant)
        return 0
    files = generate()
    if a.check:
        bad = differs(files)
        print("\n".join(bad) or "seeds up to date")
        return 1 if bad else 0
    ids = write(files)
    n_pp = sum("PolicyTemplate.lean" in files[s] for s in ids)
    print(f"{len(ids)} seeded policies ({n_pp} PP, {len(ids) - n_pp} PTP) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
