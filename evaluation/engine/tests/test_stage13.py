"""PLAN_v2 Stage 13 (2026-10-28): exact register widths on the PhaseProduct and PhaseTripleProduct boards.

  * the vendored exact-width functions are the upstream files (sha256) and give the mirror's pinned exact ladders;
  * the boards (both engines, the planner, the qubit formula) use exact widths; the scan is a labelled cross-check;
  * the 31 seeds are re-planned under exact widths, keep their old thresholds in `history`, and every re-planned seed
    has an accepted exact-width Lean run whose ladder is the site's;
  * a declared width ladder is ignored with a notice (never a rejection);
  * the full order-finding board is unchanged except the trial count;
  * the harness runs each stage in its own process group and kills the group on a timeout.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from site_engine import crosscheck as xc
from site_engine import frontier as fr
from site_engine import fullalg as fa
from site_engine import precompute as pc
from site_engine import intake
from site_engine import library as lib
from site_engine import seed_policies as sp
from site_engine import submitted
from site_engine import trust
from tcost import exact
from tcost import planner as tp
from tcost import score as ts
from tcost import tables as tt

ENGINE = Path(__file__).resolve().parents[1]
ROOT = ENGINE.parent
DATA = Path(__file__).resolve().parent / "data"
SEEDS = ENGINE / "data" / "forshor_submitted"
sys.path.insert(0, str(ROOT / "harness"))
import pgroup_stage as pg  # noqa: E402


# ------------------------------------------------------------------------------------------------ vendored functions
def test_vendored_exact_width_files_are_the_upstream_files():
    src = json.loads((ENGINE / "tcost" / "exact" / "SOURCES.json").read_text())
    assert set(src["files"]) == {"exact_width.py", "ptp_exact_width.py"}
    for name, meta in src["files"].items():
        assert hashlib.sha256((ENGINE / "tcost" / "exact" / name).read_bytes()).hexdigest() == meta["sha256"], name
        up = ROOT.parent / meta["source"]
        if up.is_file():                       # the upstream checkout is next to the package on the dev machine
            assert hashlib.sha256(up.read_bytes()).hexdigest() == meta["sha256"], f"{name}: upstream changed"


def _crosscheck():
    return json.loads((DATA / "mirror_exact_crosscheck_2048.json").read_text())


def test_vendored_ladders_equal_the_mirror_pins_on_the_seeds():
    """The mirror's runs/exact_crosscheck_2048.json pins the exact ladder (Lean = Python) of every seed as seeded on
    2026-10-27 (sha256 of its site_policy.json). Those policies are each record's site_policy.pre_exact.json."""
    seen = 0
    for r in _crosscheck()["results"]:
        if "forshor_submitted" not in r["policy"]:
            continue
        sid = r["policy"].split("/")[-2]
        raw = (SEEDS / sid / "site_policy.pre_exact.json").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == r["sha256"], sid
        doc = json.loads(raw)
        assert [w for w, _ in exact.exact_ladder(doc["mode"], doc, 2048)] == r["ladders"]["mirror_exact"], sid
        # and the site's Table.exact_width (through the cache) gives the same ladder
        P = tt.Policy.from_json(doc)
        assert [w for w, _ in P.ladder(2048)] == r["ladders"]["mirror_exact"], sid
        assert r["lean_tight_eq_mirror"] and r["mirror_eq_site_floor"]
        seen += 1
    assert seen == 31


def test_reference_policies_exact_ladders_match_the_mirror():
    pins = {r["mode"]: r for r in _crosscheck()["results"] if r["policy"].startswith("policies/ref_")}
    for mode, P in lib.sto_reference_policies().items():
        assert [w for w, _ in P.ladder(2048)] == pins[mode]["ladders"]["mirror_exact"]
        assert [w for w, _ in P.with_widths("scan").ladder(2048)] == pins[mode]["ladders"]["scan"]


def test_exact_width_never_exceeds_the_scan_and_engines_agree_on_every_table():
    """Engine 1 (vendored STO / PTP-suite functions) and engine 2 (LowGateIR reachableFits bisection) agree; the exact
    width is at most the scan (Lean: tightWidth_le_childWidth)."""
    from forshor_qualtran.mirrors import width as wm
    from forshor_qualtran.table_catalog import op_from_sto
    tabs = {T.digest: T for T in lib.all_tables()} if hasattr(lib, "all_tables") else {}
    if not tabs:
        for key in ("all_sto", "dp", "kmy"):
            for mode in lib.MODES:
                tabs.update({T.digest: T for T in lib.milestone_tables(key, mode)})
    assert len(tabs) > 40
    for T in tabs.values():
        ops = [op_from_sto(o) for o in T.to_json()["operations"]]
        for w in (64, 101, 364, 2048):
            e1 = T.exact_width(w)
            assert e1 == wm.exact_width(T.k, ops, w), (T.digest[:12], w)
            assert e1 <= T.next_width(w) + (0 if T.mode == tt.PAIR else 1), (T.digest[:12], w)


def test_ptp_scan_differs_from_pp_scan_only_by_the_negate_bit():
    """Recorded, not used: the PTP suite's own scan (childWidth) adds no bit for a negate; the board's labelled
    scanned-width cross-check keeps the scan it priced with before Stage 13 (STO nextSignedWidth)."""
    for T in lib.milestone_tables("dp", tt.TRIPLE):
        if not any(o[0] == "negate" for o in T.ops):
            assert exact.ptp_scan_width(T.k, T._ops_json(), 2048) == T.next_width(2048)


# ------------------------------------------------------------------------------------------------ the boards
def test_board_points_are_priced_at_exact_widths_by_both_engines():
    for mode, P in lib.sto_reference_policies().items():
        assert P.widths == "exact"
        p = fr.point(P, 2048, 0.01)
        assert p["widths"] == "exact" and p["agree"]
        w, lad = 2048, []
        while True:
            T = P.choose(w)
            if T is None or exact.exact_child_width(mode, T.k, T._ops_json(), w) >= w:
                lad.append((w, None))
                break
            lad.append((w, T.k))
            w = exact.exact_child_width(mode, T.k, T._ops_json(), w)
        assert [(r["width"], r["k"]) for r in p["ladder"]] == lad
        S = fr.point(P.with_widths("scan"), 2048, 0.01, with_qubits=False)
        assert S["T"] > p["T"]                        # exact widths never cost more here


def test_planner_plans_at_exact_widths():
    P, _ = tp.optimal_policy(lib.milestone_tables("all_sto", tt.PAIR), tt.PAIR, 2048, 0.01)
    assert P.widths == "exact"
    S, _ = tp.optimal_policy(lib.milestone_tables("all_sto", tt.PAIR), tt.PAIR, 2048, 0.01, widths="scan")
    assert S.widths == "scan"
    # the scan planner still reproduces the Stage 5 check plan 2048 -> 364 -> 102 -> 54 (13,254,468 T)
    assert [w for w, _ in S.ladder(2048)] == [2048, 364, 102, 54]
    assert ts.score_policy(S, 2048, 0.01)["t_count"] == pytest.approx(13_254_468.055088544, rel=1e-12)
    assert ts.score_policy(P, 2048, 0.01)["t_count"] < ts.score_policy(S, 2048, 0.01)["t_count"]


def test_cached_boards_are_exact_and_the_scan_curve_is_kept():
    from site_engine import precompute as pc
    for mode in lib.MODES:
        c = pc.load(mode, 0.01)
        assert c is not None and c["widths"] == "exact"
        for key, by_n in c["policies"].items():
            P = tt.Policy.from_json(by_n["2048"])
            pt = next(q for q in c["milestones"][key]["points"] if q["n"] == 2048)
            assert pt["widths"] == "exact" and pt["ladder_str"] == fr.ladder_str(fr._ladder(P, 2048))
    s = pc.load(tt.PAIR, 0.01, "scan")
    assert s is not None and s["widths"] == "scan"


def test_pp_qubit_headline_is_the_static_formula_at_exact_widths_labelled_not_lean_proved():
    P = lib.sto_reference_policies()[tt.PAIR]
    p = fr.point(P, 2048, 0.01)
    assert p["qubits_label"] == fr.QUBITS_LABEL_PP == "static-layout formula at exact widths (not Lean-proved)"
    assert "not a Lean-proved layout" in p["qubits_rule"]
    assert p["qubits"] == 4_992                      # 2*2048 + 2*448 at the exact ladder 2048 -> 364 -> 184 -> 94 -> 49 -> 26
    assert p["qubits_lean_static_scan"] == 6_624     # the Lean-proved layout of the same policy at scanned widths
    # the formula (engine 2's reserve recurrence) equals Lean's static layout when evaluated at scanned widths
    for key in ("all_sto", "kmy", "champions"):
        Q = fr.planned(key, tt.PAIR, 2048, 0.01)[0].with_widths("scan")
        rx, rz = xc.to_l2q(Q).reserve_need(2048, 2048)
        assert 4096 + rx + rz == fa.pp_lean_static_qubits(Q, 2048)["qubits"]


def test_policy_panel_shows_the_scanned_width_cross_check():
    from site_engine import policy_view as pv
    v = pv.view(lib.sto_reference_policies()[tt.PAIR], 2048, 0.01)
    assert v["widths"] == "exact" and "ignored" in v["width_rule"]
    c = v["scan_cross_check"]
    assert c["label"] == "ForShor scanned widths" and c["agree"]
    assert c["T"] == pytest.approx(19_046_850.546099674, rel=1e-12)       # the STO reference at scanned widths
    assert c["qubits_lean_static"] == 6_624


# ------------------------------------------------------------------------------------------------ seeds
def _records():
    return {r["id"]: r for r in submitted.seeded()}


def test_seeds_are_replanned_under_exact_widths_with_history():
    recs = _records()
    assert len(recs) == 31
    for sid, r in recs.items():
        new = tt.load_policies(SEEDS / sid / "site_policy.json")[0]
        old = tt.load_policies(SEEDS / sid / "site_policy.pre_exact.json")[0]
        h = r["history"][0]
        assert "re-planned under exact widths, 2026-10-28" in h["provenance"]
        assert [(b["threshold"], b["digest"]) for b in h["bands"]] == [(b.threshold, b.table.digest) for b in old.bands]
        # Stage 17 re-pin: the PTP seeds were re-planned again under the digital PTP leaf (their own note)
        assert r["replanned"]["note"] == ("re-planned under exact widths, 2026-10-28" if r["mode"] == tt.PAIR
                                          else sp.REPLAN_NOTE_PTP)
        want = sp.replan(old.with_widths("scan"))
        assert [(b.threshold, b.table.digest) for b in new.bands] == [(b.threshold, b.table.digest) for b in want.bands], sid
        assert {b.table.digest for b in new.bands} <= {b.table.digest for b in old.bands}      # its own tables only
        assert r["ladder_2048"] == [list(x) for x in new.ladder(2048)]
        t_new = ts.score_policy(new, 2048, 0.01)["t_count"]
        t_old_exact = ts.score_policy(old, 2048, 0.01)["t_count"]
        assert t_new <= t_old_exact * (1 + 1e-12), sid                # re-planning never makes a seed worse


def test_seed_ids_are_the_planner_outputs_they_were_seeded_from():
    files = sp.generate()
    assert sorted(files) == sorted(_records())
    assert sp.differs(files) == []


def test_every_replanned_seed_has_an_accepted_exact_width_lean_run():
    pin = submitted.seed_exact_runs()["runs"]
    recs = _records()
    assert set(pin) == set(recs)
    for sid, r in recs.items():
        run = pin[sid]
        sha = hashlib.sha256((SEEDS / sid / "site_policy.json").read_bytes()).hexdigest()
        assert run["site_policy_sha256"] == sha, sid
        assert run["widths"] == "exact" and run["verdict"] == "accepted", (sid, run.get("stage"), run.get("reason"))
        assert run["checker"] == ("sto-mirror" if r["mode"] == tt.PAIR else "ptp-suite")
        assert run["exact_ladder_kernel_proved"] is True
        assert set(run["axioms"]) <= {"propext", "Classical.choice", "Quot.sound"}
        assert [[x["width"], x["k"]] for x in run["exact_ladder"]] == r["ladder_2048"], sid
        assert r["sto_mirror"]["status"] == "accepted" and r["sto_mirror"]["ladder_matches_site"] is True
        assert r["trust"]["policy_level"] in ("forshor", "sto-mirror")
        if r["mode"] == tt.TRIPLE:
            assert [[w, k] for w, k in run["python_exact_ladder"]] == r["ladder_2048"]


def test_exact_pin_is_deterministic_apart_from_timings():
    """The pinned fields a re-run could change are timings, dates and memory only; everything else is derived from
    the policy, the trees and Lean (re-running the seeds reproduces it)."""
    import sto_policy_verify as spv
    pin = submitted.seed_exact_runs()["runs"]
    for sid, run in pin.items():
        stable = {k: v for k, v in run.items() if k not in spv.VOLATILE}
        assert stable["site_policy_sha256"] and stable["mirror_tree_sha256"] and stable["lean_sha256"]
        assert stable["command"].startswith("./run.sh " + ("sto-policy" if run["checker"] == "sto-mirror" else "ptp-policy"))


STAGE15_REPLANNED_RUNS = ("S707dd51bf951", "S7da0ff5445b5")


def test_a_forshor_run_of_a_changed_seed_is_stale():
    for sid, r in _records().items():
        if r["mode"] != tt.PAIR:
            continue
        if r["replanned"]["changed"]:
            # Stage 15 re-pin: S707dd51bf951 and S7da0ff5445b5 were run as re-planned on ForShor 10fe620 and passed
            if sid in STAGE15_REPLANNED_RUNS:
                assert r["lean"]["status"] == "policy-verified" and r["lean"]["variant"] == "replanned", sid
                assert r["lean_verified"] is True
                continue
            assert r["lean"]["status"] in ("stale", "not-run"), sid
            assert r["lean_verified"] is False
    # KMY-PP3 stays Lean-verified: ForShor's run of the pre-re-plan policy kernel-checked the table itself
    assert "KMY-PP3" in {r["id"] for r in lib.kmy_tables()}
    kmy_pp3 = next(r for r in lib.kmy_tables() if r["id"] == "KMY-PP3")["table"]
    assert trust.level_of_digest(kmy_pp3.digest) == "lean"


def test_real_submissions_are_priced_as_written_not_replanned():
    P = lib.sto_reference_policies()[tt.PAIR]
    r = intake.cost(json.dumps(P.to_json()))
    assert r["policies"][0]["point"]["T"] == fr.point(P, 2048, 0.01, with_qubits=False)["T"]
    got = tt.Policy.from_json(r["policies"][0]["policy"]["policy_json"])
    assert [(b.threshold, b.table.digest) for b in got.bands] == [(b.threshold, b.table.digest) for b in P.bands]


# ------------------------------------------------------------------------------------------------ declared ladders
@pytest.mark.parametrize("decl", [
    {"declared_ladder": [[2048, 6], [385, 3], [140, 2]]},
    {"ladder": [{"width": 2048, "k": 6}, {"width": 100, "k": None}]},
    {"widths": "scan"},
])
def test_a_declared_ladder_is_ignored_with_a_notice(decl):
    P = lib.sto_reference_policies()[tt.PAIR]
    doc = {**P.to_json(), **decl}
    r = intake.cost(json.dumps(doc))
    assert r["declared_widths_ignored"] == sorted(decl)
    assert "ignores" in r["widths_notice"] and r["policies"][0]["widths_notice"] == r["widths_notice"]
    assert r["policies"][0]["point"]["T"] == fr.point(P, 2048, 0.01, with_qubits=False)["T"]
    plain = intake.cost(json.dumps(P.to_json()))
    assert plain["widths_notice"] is None and plain["declared_widths_ignored"] == []


def test_a_per_band_child_width_is_ignored_too():
    P = lib.sto_reference_policies()[tt.TRIPLE]
    doc = P.to_json()
    doc["bands"][0]["child_width"] = 999
    r = intake.cost(json.dumps(doc))
    assert r["declared_widths_ignored"] == ["child_width"]
    assert r["policies"][0]["point"]["ladder"][1]["width"] == P.ladder(2048)[1][0]


# ------------------------------------------------------------------------------------------------ the full board
@pytest.mark.skip(reason="Stage 19 (2026-10-29): the full board prices the PhaseProduct board's recalculated exact-width "
                         "policies; these pins are of the scan-planned policies it priced before (kept as history in tests/data)")
def test_full_board_unchanged_except_the_trial_count():
    """Stage 13 left the full board unchanged except the trial count. Stage 18 moved the board to the ForShor drop-in
    IR on purpose, so this pin is now checked on the board's scan cross-check (`cross_checks.forshor_scan_ir`: upstream
    ForShor's IR at scanned widths), which reproduces every pre-Stage 13 number; the Stage 18 board itself is pinned in
    test_full_board_stage18_pins."""
    from site_engine import full_frontier as ff
    old = json.loads((DATA / "full_board_pre_stage13_1e-02.json").read_text())
    c = ff.load(0.01)
    assert c is not None
    for key, pts in old["milestones"].items():
        got = {p["n"]: p for p in c["milestones"][key]["points"]}
        for n, T, q, lad, ppT in pts:
            p = got[n]
            s = p["cross_checks"]["forshor_scan_ir"]
            assert (s["T"], s["qubits"], s["ladder_str"]) == (pytest.approx(T, rel=1e-12), q, lad), (key, n)
            if ppT is not None:
                assert p["pp_T"] == pytest.approx(ppT, rel=1e-12), (key, n)
    byid = {r["id"]: r for r in c["leaderboard"]["ranked"]}
    assert sorted(byid) == sorted(i for i, *_ in old["ranked"])
    for i, T, q, lad, ppT in old["ranked"]:
        s = byid[i]["cross_checks"]["forshor_scan_ir"]
        assert (s["T"], s["qubits"], s["ladder_str"]) == (pytest.approx(T, rel=1e-12), q, lad), i
        assert byid[i]["pp_T"] == pytest.approx(ppT, rel=1e-12)
    s = c["forshor_shipped"]["scan_cross_check"]
    assert [s["T"], s["qubits"]] == [pytest.approx(old["forshor_shipped"][0], rel=1e-12), old["forshor_shipped"][1]]
    # the one change: ForShor's latest policy.json fixes the trial count at 386 (was 2,216,900,437,333,460)
    assert old["trial_count_before"] == 2_216_900_437_333_460
    assert fa.trial_count_2048() == 386
    hp = next(p for p in c["milestones"]["all_sto"]["points"] if p["n"] == 2048)
    assert hp["trial_count"] == 386 and hp["end_to_end"] == pytest.approx(386 * hp["T"], rel=1e-15)


def test_full_board_stage19_pins():
    """Stage 18 (2026-10-28): the full board on the ForShor drop-in IR (exact widths, balanced split). Stage 19
    (2026-10-29): its policies are the PhaseProduct board's recalculated exact-width policies. Pinned from the cache it
    was built with (tests/data/full_board_stage19_1e-02.json; the Stage 18 pins are kept as history), plus its scan
    cross-check."""
    from site_engine import full_frontier as ff
    new = json.loads((DATA / "full_board_stage19_1e-02.json").read_text())
    c = ff.load(0.01)
    assert c is not None and c["full_model"] == new["model"] == fa.MODEL and c["width_rule"] == new["width_rule"] == "exact"
    for key, pts in new["milestones"].items():
        got = {p["n"]: p for p in c["milestones"][key]["points"]}
        for n, T, q, lad, ppT in pts:
            p = got[n]
            assert (p["T"], p["qubits"], p["ladder_str"]) == (pytest.approx(T, rel=1e-12), q, lad), (key, n)
        for n, T, q, lad in new["scan_milestones"][key]:
            s = got[n]["cross_checks"]["forshor_scan_ir"]
            assert (s["T"], s["qubits"], s["ladder_str"]) == (pytest.approx(T, rel=1e-12), q, lad), (key, n)
    assert [[r["id"], r["qubits"], r["ladder_str"]] for r in c["leaderboard"]["ranked"]] == \
        [[i, q, l] for i, _, q, l, _ in new["ranked"]]
    for r, (_, T, _, _, ppT) in zip(c["leaderboard"]["ranked"], new["ranked"]):
        assert r["T"] == pytest.approx(T, rel=1e-12) and r["pp_T"] == pytest.approx(ppT, rel=1e-12)
    assert [c["forshor_shipped"]["T"], c["forshor_shipped"]["qubits"]] == [pytest.approx(new["forshor_shipped"][0], rel=1e-12),
                                                                       new["forshor_shipped"][1]]
    # the verified headline numbers (n = 2048, delta = 0.01)
    h = {r["key"]: r for r in c["hero"]["milestones"]}
    # Stage 19 (third round): the site's STO reference has its thresholds moved to exact widths (STO's shipped
    # thresholds price 1.933981965831e12 T, 47,474 qubits)
    # Modular swap 001: the STO reference has no negate op, so its T rose by exactly the 24,570 negations of the run (all at width 4398)
    # at 32 w Toffoli = 128 w T each, minus the old 2w - 3 Toffoli: 13,399,396,920 T (it was 2.110297340025e12).
    assert (h["reference"]["T"], h["reference"]["qubits"]) == (pytest.approx(2.110297340025e12 + 13_399_396_920, rel=1e-11), 47_954)
    # the shipped ForShor policy's own table negates, so it pays far more (3.240663026770465e12 before the swap, +46.8 %)
    assert (c["forshor_shipped"]["T"], c["forshor_shipped"]["qubits"]) == (pytest.approx(new["forshor_shipped"][0], rel=1e-12), 43_904)
    assert c["forshor_shipped"]["T"] > 3.240663026770465e12 * 1.4
    # Stage 19: the recalculated KMY benchmark and the leaderboard's PP column follow the PhaseProduct board. KMY's table negates too
    # (it was 1.832437398043e12 before the swap); its static layout is unchanged.
    kmy_2048 = next(row for row in new["milestones"]["kmy"] if row[0] == 2048)
    assert (h["kmy"]["T"], h["kmy"]["qubits"]) == (pytest.approx(kmy_2048[1], rel=1e-12), 27_866)
    assert h["kmy"]["T"] > 1.832437398043e12
    pp = {r["id"]: r["T"] for r in pc.load(tt.PAIR, 0.01)["leaderboard"]["rows"]}
    assert all(r["pp_T"] == pytest.approx(pp[r["id"]], rel=1e-12) for r in c["leaderboard"]["ranked"])


def test_forshor_document_is_the_latest_and_only_trial_count_and_checks_changed():
    # Stage 18: fa.REFERENCE is the drop-in's document; upstream ForShor's own policy.json is fa.REFERENCE_SCAN (the
    # labelled cross-check), and the drop-in document shares its fixed fields (bands, benchmark, precision, leaf).
    doc = json.loads(fa.REFERENCE_SCAN.read_text())
    assert doc["benchmark"]["trial_count"] == "386"
    up = Path("/Users/joshua/Desktop/ForShor/policy.json")
    if up.is_file():
        assert json.loads(up.read_text()) == doc
    drop = json.loads(fa.REFERENCE.read_text())
    for key in ("bands", "benchmark", "precision", "leaf", "schema"):
        assert drop[key] == doc[key], key
    assert fa.doc_width_rule(drop) == "exact" and fa.doc_width_rule(doc) == "scan" and "balanced" in drop


# ------------------------------------------------------------------------------------------------ process groups
MARK = "3017.5"           # a sleep argument no other process uses


def _alive(mark: str) -> list:
    out = subprocess.run(["pgrep", "-f", f"sleep {mark}"], capture_output=True, text=True).stdout.split()
    return out


def test_a_stage_timeout_kills_the_whole_process_group():
    """The Stage 12 bug: subprocess.run(timeout) killed the direct child (lake) and left its child (lean). Here the
    direct child is a shell whose child is a sleep: the timeout must kill both."""
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        pg.run_in_group(["sh", "-c", f"sleep {MARK} & wait"], timeout=1, capture_output=True, text=True)
    assert time.monotonic() - t0 < 30
    time.sleep(0.5)
    assert _alive(MARK) == []
    assert pg.KILLED and "stage timeout" in pg.KILLED[-1]["why"]


def test_an_orphan_left_in_the_group_is_swept_after_the_stage_returns():
    mark = MARK + "1"
    p = pg.run_in_group(["sh", "-c", f"(sleep {mark} &) ; exit 0"], timeout=60, capture_output=True, text=True)
    assert p.returncode == 0
    time.sleep(0.5)
    assert _alive(mark) == []


def test_the_checker_wrapper_routes_timed_calls_through_the_group_runner(tmp_path):
    script = tmp_path / "fake_checker.py"
    script.write_text("import subprocess, sys\n"
                      "def main(argv):\n"
                      "    try:\n"
                      f"        subprocess.run(['sh', '-c', 'sleep {MARK}2 & wait'], capture_output=True, text=True, timeout=1)\n"
                      "    except subprocess.TimeoutExpired:\n"
                      "        print('timeout'); return 1\n"
                      "    return 0\n")
    p = subprocess.run([sys.executable, str(ROOT / "harness" / "pgroup_stage.py"), str(script)], capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 1 and "timeout" in p.stdout
    assert "pgroup_stage_killed" in p.stderr
    time.sleep(0.5)
    assert _alive(MARK + "2") == []


def test_both_lean_harnesses_use_the_group_wrapper_8gb_and_wait_for_a_slot():
    import ptp_policy_verify as ppv
    import sto_policy_verify as spv
    assert spv.CAP_GB == 8.0 and ppv.CAP_GB == 8.0
    for f in ("sto_policy_verify.py", "ptp_policy_verify.py"):
        src = (ROOT / "harness" / f).read_text()
        assert "PGROUP" in src and "wait_lean_slot" in src
    assert "ptp-policy)" in (ROOT / "run.sh").read_text()


def test_ptp7_stays_recorded_as_killed():
    r = trust.table_mirror_runs()
    hung = [v for d, v in r.items() if d.startswith("5fad4a5d0b55")]
    assert hung and hung[0]["status"] == "killed"
