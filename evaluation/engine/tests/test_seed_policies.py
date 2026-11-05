"""seed_policies.py: the planner outputs the site shows, as seeded policy submissions (deterministic, re-cost through intake
to the planner's T, deduplicated by content, no T stored, PTP flagged).

Stage 13: the seeds are generated from the planner outputs at ForShor's SCANNED widths (their ids and their pre-re-plan
policy, `site_policy.pre_exact.json`, are checked against those outputs here), then re-planned under exact widths
(`site_policy.json`; tests/test_stage13.py checks the re-plan and its Lean runs)."""
from __future__ import annotations

import json

import pytest

from site_engine import intake, precompute as pc, seed_policies as sp, submitted
from site_engine import crosscheck as xc
from tcost import score as ts
from tcost import tables as tt

N = sp.HERO_N


@pytest.fixture(scope="module")
def outputs():
    return sp.planner_outputs()


@pytest.fixture(scope="module")
def files(outputs):
    return sp.generate(outputs=outputs)


def _policy(files, sid) -> tt.Policy:
    """The seed as it was seeded (the planner output at scanned widths): its pre-re-plan policy."""
    return intake.parse(files[sid]["site_policy.pre_exact.json"])[0].with_widths("scan")


_T = {}


def _seed_T(files, sid, delta) -> float:
    """T of the seeded (pre-re-plan) policy at scanned widths, the planner's rule (memoised)."""
    if (sid, delta) not in _T:
        _T[sid, delta] = ts.score_policy(_policy(files, sid), N, delta)["t_count"]
    return _T[sid, delta]


def test_generation_is_deterministic_and_matches_the_installed_records(outputs, files):
    assert sp.generate(outputs=outputs) == files
    assert sp.differs(files) == [], "installed seeds are stale: run python -m site_engine.seed_policies"


def test_dedupe_by_policy_content(outputs, files):
    ids = [sp.policy_id(sp.canonical(P)) for P, _ in outputs]
    assert set(ids) == set(files)
    contents = {(P.mode, tuple((b.threshold, b.table.digest) for b in P.bands)) for P in (_policy(files, s) for s in files)}
    assert len(contents) == len(files)                      # no two records hold the same policy
    for sid in files:
        P = _policy(files, sid)
        assert sp.policy_id(P) == sid
        assert all(a.table.digest != b.table.digest for a, b in zip(P.bands, P.bands[1:]))
    occ = sum(len(json.loads(f["record.json"])["provenance"]["occurrences"]) for f in files.values())
    assert occ == len(outputs)                              # every planner output is in exactly one record


def test_merging_bands_never_changes_the_table_chosen_at_any_width(outputs):
    for P, _ in outputs:
        C = sp.canonical(P)
        assert all(P.choose(w) == C.choose(w) for w in range(0, 2 * N + 1))
        assert P.ladder(N) == C.ladder(N)


def test_each_seed_re_costs_through_intake_to_its_planner_T(outputs, files):
    for P, prov in outputs:
        sid = sp.policy_id(sp.canonical(P))
        xc.to_l2q(_policy(files, sid))                      # intake's Toom-Cook identity check
        d = prov["delta"]
        assert _seed_T(files, sid, d) == ts.score_policy(P, N, d)["t_count"], (sid, prov)


@pytest.mark.parametrize("delta", sp.SEED_DELTAS)
def test_seeds_reproduce_the_cached_leaderboard_rows(files, delta):
    """Where engine/cache holds a valid frontier, every leaderboard row's T is the T of one seed."""
    by_row = {}
    for sid, f in files.items():
        for o in json.loads(f["record.json"])["provenance"]["occurrences"]:
            if o["source"] == "leaderboard" and o["delta"] == delta:
                by_row[(o["mode"], o["row"])] = sid
    checked = 0
    for mode in (tt.PAIR,):          # Stage 13: only PP keeps a scanned-width curve (frontier_PP_scan_*), the seeds' rule
        c = pc.load(mode, delta, "scan")
        if c is None:
            continue
        for row in c["leaderboard"]["rows"]:
            sid = by_row[(mode, row["id"])]
            assert _seed_T(files, sid, delta) == row["T"], (mode, row["id"], sid)
            checked += 1
        for key, by_n in c["policies"].items():
            assert sp.policy_id(sp.canonical(tt.Policy.from_json(by_n[str(N)], widths="scan"))) in files, (mode, key)
    if not checked:
        pytest.skip(f"no valid cache at delta {delta:g}")


@pytest.mark.parametrize("mode", [tt.PAIR, tt.TRIPLE])
def test_one_seed_goes_through_the_full_intake(files, mode):
    sid = next(s for s, f in files.items() if json.loads(f["record.json"])["mode"] == mode)
    rec = json.loads(files[sid]["record.json"])
    d = rec["provenance"]["occurrences"][0]["delta"]
    r = intake.cost(files[sid]["site_policy.json"], N, d)["policies"][0]
    # the re-planned policy, priced at exact widths as intake prices any policy
    assert r["point"]["T"] == ts.score_policy(intake.parse(files[sid]["site_policy.json"])[0], N, d)["t_count"]
    assert r["cross_check"]["agree"] and r["widths"] == "exact"


def _keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _keys(v)
    elif isinstance(x, list):
        for v in x:
            yield from _keys(v)


def test_records_store_no_T_and_carry_trust_and_provenance(files):
    for sid, f in files.items():
        rec = json.loads(f["record.json"])
        assert rec["seeded_from_planner"] and "seeded-from-planner" in rec["flags"]
        assert not {"T", "single_run_T", "pp_T", "t_count", "T_document"} & set(_keys(rec)), sid
        assert rec["provenance"]["planner"].startswith("engine/tcost/planner.py") and rec["provenance"]["occurrences"]
        assert rec["trust"]["tables"]["level"] in ("lean", "ci", "replay", "none")
        if rec["mode"] == tt.TRIPLE:
            assert "PolicyTemplate.lean" not in f and "ptp-no-policy-harness" in rec["flags"]
            assert rec["lean"]["status"] == "no-policy-harness" and rec["lean_verified"] is False
        else:
            assert f["PolicyTemplate.lean"].startswith("import FastMultiplication.")
            assert rec["lean_verified"] == (rec["lean"]["status"] == "policy-verified")
            if rec["site_override"]:
                assert rec["lean_verified"] and "site-override" in rec["flags"]


def test_board_hook_lists_seeds_and_keeps_them_out_of_the_harness_board(files):
    rows = submitted.seeded()
    assert sorted(r["id"] for r in rows) == sorted(files)
    assert not any(r.get("seeded_from_planner") for r in submitted.records())
    for r in rows:
        assert tt.load_policies(r["site_policy_path"])[0].bands
