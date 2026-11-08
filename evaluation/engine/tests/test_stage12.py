"""PLAN_v2 Stage 12 (2026-10-28): per-table STO mirror promotion (A), Qualtran vs Lean qubits (B), the pricing rule
wording (C), the paper's custom policy re-priced (D)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from site_engine import frontier as fr
from site_engine import fullalg as fa
from site_engine import library as lib
from site_engine import paper_policies as pp
from site_engine import qubit_compare as qc
from site_engine import trust
from tcost import tables as tt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
import sto_table_verify as stv  # noqa: E402

TIMED_OUT = "5fad4a5d0b55"
FROZEN = "/Users/joshua/Desktop/github_packages/ShorTableOptimization-policy-mirror-frozen-2026-10-28"


@pytest.fixture(autouse=True)
def _fresh():
    trust.clear()
    yield
    trust.clear()


# ------------------------------------------------------------------------------------------------ A: table promotion
def test_every_replay_or_ci_table_has_an_accepted_pinned_mirror_run():
    runs = trust.table_mirror_runs()
    targets = stv.targets()
    assert len(targets) == 51                      # 45 DP replay + KMY-PTP3, KMY-PTP4 + 4 STO-CI-only tables
    for t in targets:
        r = runs[t["digest"]]
        if t["digest"].startswith(TIMED_OUT):
            # PTP7-e1d7b1c6690f: the mirror's report stage (lean --run, diagnostics) times out at 1800 s, twice; the
            # kernel stage never ran. Not a Lean verdict; the table stays replay.
            assert r["status"] == "killed" and r["stage"] == "report" and "report stage hung" in r["reason"]
            continue
        assert r["status"] == "accepted", (t["digest"][:12], r["status"])
        assert r["site_policy_sha256"] == hashlib.sha256(stv.policy_bytes(t["table"])).hexdigest()
        assert r["mirror_build"] == FROZEN and r["cap_gb"] == 8.0 and r["bands"] == [[2048, t["k"]]]
        assert set(r["axioms"]) <= trust.MIRROR_AXIOMS and r["statement_pinned"]
        assert [m["verdict"] for m in r["replay"]] == ["replayed", "replayed"]
        assert r["peak_gb"] < 8.0 and all(m["peak_gb"] < 8.0 for m in r["replay"])
        assert (stv.EVIDENCE.parent / r["evidence"]).is_file() and (stv.EVIDENCE.parent / r["policy"]).is_file()
    assert {d for d, r in runs.items() if r["status"] == "accepted"} == {t["digest"] for t in targets
                                                                         if not t["digest"].startswith(TIMED_OUT)}


def test_one_band_policy_is_deterministic_and_its_sha_is_what_trust_checks():
    T = next(r["table"] for r in lib.kmy_tables() if r["id"] == "KMY-PTP3")
    assert stv.policy_bytes(T) == stv.policy_bytes(T)
    assert trust._one_band_sha(T) == hashlib.sha256(stv.policy_bytes(T)).hexdigest()
    doc = json.loads(stv.policy_bytes(T))
    assert len(doc["bands"]) == 1 and doc["bands"][0]["threshold"] == 2048 and doc["mode"] == T.mode


def test_a_stale_or_rejected_pin_does_not_promote(tmp_path, monkeypatch):
    pin = json.loads(trust.TABLE_MIRROR_PIN.read_text())
    T = next(r["table"] for r in lib.kmy_tables() if r["id"] == "KMY-PTP3")
    U = next(r["table"] for r in lib.kmy_tables() if r["id"] == "KMY-PTP4")
    pin["runs"][T.digest]["site_policy_sha256"] = "0" * 64                     # stale
    pin["runs"][U.digest]["verdict"] = "rejected"                              # a failed run
    f = tmp_path / "pin.json"
    f.write_text(json.dumps(pin))
    monkeypatch.setattr(trust, "TABLE_MIRROR_PIN", f)
    trust.clear()
    assert trust.table_mirror_runs()[T.digest]["status"] == "stale"
    assert trust.table_mirror_runs()[U.digest]["status"] == "rejected"
    assert trust.level_of_id("KMY-PTP3") == "replay" and trust.level_of_id("KMY-PTP4") == "replay"


def test_promoted_levels_and_who_checked_them():
    # was (Stage 11): ci 4, replay 47, lean 155; now 50 of the 51 are kernel-checked by their own mirror run
    lv = trust.id_levels()
    n = {l: sum(v == l for v in lv.values()) for l in trust.LEVELS}
    assert n == {"lean": 205, "ci": 0, "replay": 1, "none": 0}
    assert trust.level_of_id("PTP7-e1d7b1c6690f") == "replay"
    assert trust.level_of_id("390223a8146a") == "lean" and trust.level_of_id("651fc8ae389d") == "lean"
    assert trust.level_of_id("KMY-PTP3") == "lean" and trust.level_of_id("KMY-PTP4") == "lean"
    kp3 = next(r["table"] for r in lib.kmy_tables() if r["id"] == "KMY-PP3")
    assert "forshor" in trust.checkers_of_digest(kp3.digest)
    kt3 = next(r["table"] for r in lib.kmy_tables() if r["id"] == "KMY-PTP3")
    assert trust.checkers_of_digest(kt3.digest) == ["sto-mirror", "python-replay"]
    s651 = next(s for s in lib.submissions() if s.id == "651fc8ae389d")
    assert all("sto-mirror" in trust.checkers_of_digest(d) or trust.level_of_digest(d, use_table_mirror=False) == "lean"
               for d in s651.tables)
    assert "STO mirror" in trust.checker_text(kt3.digest) and "STO policy mirror" in trust.explain()["lean"]


def test_without_the_pin_the_old_levels_come_back(tmp_path, monkeypatch):
    monkeypatch.setattr(trust, "TABLE_MIRROR_PIN", tmp_path / "missing.json")
    trust.clear()
    lv = trust.id_levels()
    n = {l: sum(v == l for v in lv.values()) for l in trust.LEVELS}
    assert (n["lean"], n["ci"], n["replay"]) == (155, 4, 47)


# ------------------------------------------------------------------------------------------------ B: qubits
def _ref():
    return lib.sto_reference_policies()[tt.PAIR]


def test_pp_qubits_term_by_term_reference():
    r = qc.pp_compare(_ref(), 2048)
    assert (r["lean_static"], r["reserve"], r["lean_used"], r["lean_qubit_count"]) == (6624, [1264, 1264], 5956, 5957)
    assert (r["qualtran_tcost"], r["qualtran_l2q"], r["qualtran_ancilla_peak"]) == (5004, 6360, 404)
    # tcost's Qualtran peak: operands + limb growth 524 at the top level + the Gidney Add(QInt(385)) ancillas 384
    assert r["qualtran_tcost"] == 2 * 2048 + 524 + 384
    assert r["qualtran_l2q"] == r["lean_used"] + r["qualtran_ancilla_peak"]


def test_analytic_peak_equals_qualtran_qubitcount_of_the_tcost_bloq():
    # Stage 13: the analytic peak runs on the l2q policy (scanned widths), so it is compared at scanned widths
    for P, w in ((_ref(), 2048), (_ref(), 1024), (fr.planned("kmy", tt.PAIR, 2048, 0.01, "scan")[0], 2048)):
        # Stage 18: to_l2q_full now defaults to the board's exact rule, so ask for the scanned widths explicitly
        assert 2 * w + qc.peak_extra(fa.to_l2q_full(P, "scan"), w, w) == fr.qubits(P.with_widths("scan"), w)


def test_kmy_qualtran_peak_exceeds_the_lean_static_layout():
    # KMY-PP3 has a negate: Qualtran Negate(QInt(698)) needs 2*698 - 1 ancillas; Lean's 2w + R has no room for them.
    # Lean's own literal lowering needed cleanAnc = w + 1 = 699 for the negate (5,292 + 699 = 5,991, the pin before modular
    # swap 001), and needs 1 since (5,292 + 1 = 5,293): the incrementer negation borrows one clean ancilla, not a w-bit register.
    r = qc.pp_compare(fr.planned("kmy", tt.PAIR, 2048, 0.01, "scan")[0], 2048)     # Stage 12's comparison: scanned widths
    assert (r["lean_static"], r["qualtran_tcost"], r["lean_qubit_count"]) == (5292, 5583, 5293)
    assert r["lean_qubit_count"] - r["lean_static"] == 1 and 5991 - 5292 == 698 + 1


def test_full_run_qubits_compared():
    # Stage 18: full_compare follows the full-run board (the ForShor drop-in IR: exact widths, balanced split). The Stage
    # 12 pins are kept on upstream's IR at scanned widths (rule="scan").
    f = qc.full_compare(_ref(), rule="scan")
    assert (f["lean_static"], f["qualtran_bloq"], f["qualtran_peak"], f["active_registers"]) == (56_738, 56_738, 38_459, 12_889)
    assert f["peak_set_by"]["site"] == "phase_product" and (f["peak_set_by"]["xw"], f["peak_set_by"]["zw"]) == (2348, 4399)
    s = qc.full_compare(None, policy_l2q=fa.native_scan().policy, rule="scan")
    assert (s["lean_static"], s["qualtran_bloq"], s["qualtran_peak"]) == (909_794, 909_794, 26_098)
    # Stage 18 re-pin, the board: the balanced split leaves no 2348 x 4399 product, so the sequential Qualtran peak is set
    # by the Step 4 Negate on the 4398-bit scratch register (2 x 4398 - 1 ancillas)
    f = qc.full_compare(_ref())
    assert (f["lean_static"], f["qualtran_bloq"], f["qualtran_peak"], f["active_registers"]) == (47_474, 47_474, 21_684, 12_889)
    assert (f["peak_set_by"]["site"], f["peak_set_by"]["xw"], f["peak_set_by"]["extra"]) == ("negate", 4398, 8795)
    s = qc.full_compare(None, policy_l2q=fa.native().policy)
    assert (s["lean_static"], s["qualtran_bloq"], s["qualtran_peak"]) == (43_904, 43_904, 21_684)


def test_pp_chart_qubits_follow_the_board_rule():
    # Stage 13 re-pin, reason: the PP board's qubits are the static-layout formula at EXACT widths (labelled not
    # Lean-proved); the Stage 12 rule (Lean static at scanned widths, 6,624; Qualtran 5,004) is the scan point's
    p = fr.point(_ref(), 2048, 0.01, check=False)
    assert p["qubits"] == 4992 and p["qubits_lean_static_scan"] == 6624 and "not a Lean-proved layout" in p["qubits_rule"]
    s = fr.point(_ref().with_widths("scan"), 2048, 0.01, check=False)
    assert s["qubits"] == 6624 and s["qubits_qualtran_peak"] == 5004 and "Lean static" in s["qubits_rule"]
    q = fr.point(lib.sto_reference_policies()[tt.TRIPLE], 2048, 0.01, check=False)
    assert q["qubits"] == q["qubits_qualtran_peak"] and "no PhaseTripleProduct layout" in q["qubits_rule"]


# ------------------------------------------------------------------------------------------------ C: pricing wording
def test_pricing_rule_wording():
    t = trust.explain()["pricing"]
    for s in ("Qualtran costing of ForShor's IR", "Gidney", "digital-product leaf", "Ross-Selinger", "Lean proves the structure, the widths and "
              "the tables", "Cuccaro", "cross-check"):
        assert s in t
    assert not (lib.DATA / "substitutions.json").exists()


# ------------------------------------------------------------------------------------------------ D: the paper's policy
@pytest.mark.legacy_negation
def test_paper_policies_on_the_board_ir():
    # Stage 18: the paper follows the board, i.e. the ForShor drop-in IR (exact widths + balanced split)
    assert pp.price("k5532")["T"] == pytest.approx(1_934_872e6, rel=1e-6) and pp.price("k5532")["qubits"] == 35_282
    assert pp.price("k5322")["T"] == pytest.approx(2_228_329e6, rel=1e-6)
    best, k4 = pp.price("replanned_best_exact"), pp.price("constant_k4_exact")
    assert best["T"] == pytest.approx(1.685193e12, rel=1e-6) and best["qubits"] == 45_296
    assert k4["T"] == pytest.approx(1.819733e12, rel=1e-6) and k4["bands"] == [(129, 4)]
    assert best["T"] < k4["T"] < pp.price("constant_k2")["T"]
    assert best["ladders"]["Step 2 (work+1 x data+2)"] == "2348:6 -> 655:5 -> 140:3 -> 51:leaf"
    assert best["widths_rule"].startswith("exact")


@pytest.mark.legacy_negation
def test_paper_policies_repriced():
    # Stage 12 figures, kept on upstream ForShor's IR at scanned widths (Stage 18: rule="scan")
    a, b = pp.price("k5532", rule="scan"), pp.price("k5322", rule="scan")
    assert a["T"] == pytest.approx(5_555_017_353_851.2, rel=1e-12) and a["qubits"] == 52_090
    assert b["T"] == pytest.approx(5_379_199_707_079.5, rel=1e-12) and b["qubits"] == 46_370
    assert a["ladders"]["Step 2 (work+1 x data+2)"] == "2348:5 -> 798:5 -> 252:3 -> 101:2 -> 56:leaf"
    assert b["ladders"]["Step 2 (work+1 x data+2)"] == "2348:5 -> 798:3 -> 283:2 -> 147:2 -> 79:leaf"
    # the owner's reconciliation figures, same tables and pricer
    assert pp.price("replanned_best", rule="scan")["T"] == pytest.approx(3.31993e12, rel=1e-5)
    assert pp.price("constant_k2", rule="scan")["T"] == pytest.approx(3.77364e12, rel=1e-5)
    assert a["step4"] and a["work_width"] == 2347 and a["tables"]["std-k5"]["points"][-1] == "4"
