"""The STO policy-mirror harness (`./run.sh sto-policy`, harness/sto_policy_verify.py), its policy trust level, the
seeded records on the boards (priced live) and the KMY-PP3 promotion (PLAN_v2 Stage 11, STO mirror).

The Lean tests (marked `lean`, `./run.sh test-lean`) run one PP and one PTP seed through the real mirror; they need the
built mirror tree ($STO_POLICY_BUILD, default ../ShorTableOptimization-policy-mirror) and run one Lean process at a time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT))

import sto_policy_verify as spv  # noqa: E402
from site_engine import DATA, frontier as fr, library as lib, policy_view as pv, seed_policies as sp  # noqa: E402
from site_engine import seeded_board as sb, submitted, trust  # noqa: E402
from tcost import tables as tt  # noqa: E402

SEEDS = DATA / "forshor_submitted"
KMY_SEED = "S707dd51bf951"                  # the KMY-PP3 single-band policy, ForShor policy-verified at stock budget


@pytest.fixture(autouse=True)
def _fresh():
    trust.clear()
    yield
    trust.clear()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


def _rec(sid):
    return json.loads((SEEDS / sid / "record.json").read_text())


# ------------------------------------------------------------------------------------------------ input conversion
def test_site_policy_converts_to_the_mirror_shape():
    for sid in submitted.seeded():
        doc = json.loads(Path(sid["site_policy_path"]).read_text())
        m = spv.to_mirror(doc)
        assert m["mode"] == doc["mode"] and len(m["bands"]) == len(doc["bands"])
        for a, b in zip(m["bands"], doc["bands"]):
            assert set(a) == {"threshold", "k", "point_order", "operations"}
            assert all(a[k] == b[k] for k in a)


def test_conversion_renders_with_the_mirrors_own_renderer():
    build = spv.build_dir()
    if not (build / "scripts" / "policy_json_to_lean.py").is_file():
        pytest.skip("no STO policy mirror tree")
    sys.path.insert(0, str(build / "scripts"))
    from policy_json_to_lean import render
    for mode in (tt.PAIR, tt.TRIPLE):
        r = next(r for r in submitted.seeded() if r["mode"] == mode)
        src = render(spv.to_mirror(json.loads(Path(r["site_policy_path"]).read_text())), "Submitted", None, "data")
        assert "PolicyMirror.Instances.Submitted" in src


def test_conversion_refuses_mixed_modes_and_missing_fields():
    doc = json.loads((SEEDS / KMY_SEED / "site_policy.json").read_text())
    bad = json.loads(json.dumps(doc))
    bad["bands"][0]["mode"] = tt.TRIPLE
    with pytest.raises(spv.HarnessError, match="differs"):
        spv.to_mirror(bad)
    bad = json.loads(json.dumps(doc))
    del bad["bands"][0]["point_order"]
    with pytest.raises(spv.HarnessError, match="missing"):
        spv.to_mirror(bad)


def test_install_takes_seeded_records_only(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text((SEEDS / KMY_SEED / "site_policy.json").read_text())
    assert spv.seed_id_of(p) is None
    assert spv.seed_id_of(SEEDS / KMY_SEED / "site_policy.json") == KMY_SEED
    assert spv.main([str(p), "--install"]) == 2


# ------------------------------------------------------------------------------------------------ trust wording
def test_policy_levels_are_distinct_and_the_mirror_level_is_worded_with_its_limits():
    tags, text = trust.POLICY_TAGS, trust.explain_policy()
    assert trust.POLICY_LEVELS == ("forshor", "sto-mirror", "tables")
    # Re-pin 2026-10-28 (Stage 14), reason: owner renamed the displayed label (was "Policy algebra Lean-verified (STO
    # mirror)"): the exact-width runs prove the register semantics at the priced widths, and PTP is checked by the PTP
    # suite, not the mirror. The level key "sto-mirror" is unchanged.
    assert tags["sto-mirror"] == "Policy Lean-verified at exact widths (STO mirror / PTP suite)"
    assert tags["forshor"] == "ForShor policy-verified"
    assert len(set(tags.values())) == 3 and "ForShor" not in tags["sto-mirror"]
    t = text["sto-mirror"]
    # Stage 13 re-pin, reason: the seeds are now checked at EXACT widths (PP: the live mirror's correct_tight and
    # exact_ladder; PTP: the PTP suite's correct_exact), so the register semantics at the priced widths IS proved; the
    # text says so and keeps the limits (not circuit-level, PTP by analogy, not ForShor's level). The tag is unchanged.
    # Stage 14 re-pin (label rename), reason: the text states what is proved (register semantics correct at exact
    # widths, exact widths least overflow-free, STO's table contract; PP by the STO mirror, PTP by the PTP suite) and
    # says "Weaker than ForShor policy-verified" (was "Not the same as ...").
    for must in ("EXACT widths", "register semantics is correct", "least overflow-free widths", "STO's table contract",
                 "correct_tight", "correct_exact", "policy mirror", "PTP suite", "not a circuit-level check", "by analogy",
                 "Weaker than ForShor policy-verified", "φ·∏xs", "φab", "φabc"):
        assert must in t, must
    assert trust.POLICY_KIND["sto-mirror"] != trust.POLICY_KIND["forshor"]


def test_policy_trust_takes_the_strongest_supported_level():
    ok = {"status": "accepted"}
    assert trust.policy_trust({"lean": {"policy_verified": True, "budget": "default"}, "sto_mirror": ok})["level"] == "forshor"
    o = trust.policy_trust({"lean": {"policy_verified": True, "budget": "override"}, "sto_mirror": {}})
    assert o["tag"] == "ForShor policy-verified (site override of the heartbeat cap)"
    assert trust.policy_trust({"lean": {"policy_verified": False}, "sto_mirror": ok})["level"] == "sto-mirror"
    for st in ("rejected", "killed", "stale", "not-run", "error"):
        assert trust.policy_trust({"lean": {"policy_verified": False}, "sto_mirror": {"status": st}})["level"] == "tables"


def test_mirror_status_is_stale_when_the_policy_changed_and_never_a_pass():
    run = {"runs": {"Sx": {"verdict": "accepted", "site_policy_sha256": "a" * 64, "ladder": [{"width": 2048, "k": 3},
                                                                                          {"width": 98, "k": None}]}}}
    assert sp.mirror_status("Sx", "b" * 64, [(2048, 3), (98, None)], run)["status"] == "stale"
    m = sp.mirror_status("Sx", "a" * 64, [(2048, 3), (98, None)], run)
    assert m["status"] == "accepted" and m["ladder_matches_site"] is True and "correct_tight" in m["proves"]   # Stage 13
    assert sp.mirror_status("Sy", "a" * 64, [], run)["status"] == "not-run"
    run["runs"]["Sx"]["verdict"] = "killed"
    assert sp.mirror_status("Sx", "a" * 64, [], run)["status"] == "killed"


def test_seed_records_carry_the_pinned_mirror_outcome_and_never_conflate_levels():
    pin = submitted.seed_exact_runs()["runs"]          # Stage 13: the exact-width runs of the re-planned seeds
    for r in submitted.seeded():
        m, lvl = r["sto_mirror"], r["trust"]["policy_level"]
        assert lvl in trust.POLICY_LEVELS
        assert r["lean_verified"] == (lvl == "forshor")          # lean_verified is ForShor's, never the mirror's
        if r["id"] in pin:
            assert pin[r["id"]]["site_policy_sha256"] == r["site_policy_sha256"]
            assert m["status"] in ("accepted", "rejected", "killed", "error")
        if lvl == "sto-mirror":
            assert m["status"] == "accepted" and r["trust"]["policy"] == trust.POLICY_TAGS["sto-mirror"]
            assert "sto-mirror-algebra-verified" in r["flags"]
        if r["mode"] == tt.TRIPLE:
            assert lvl != "forshor"


# ------------------------------------------------------------------------------------------------ KMY-PP3 promotion
def test_kmy_pp3_is_promoted_by_the_forshor_policy_run():
    kmy = {r["id"]: r["table"] for r in lib.kmy_tables()}
    assert KMY_SEED in submitted.seed_forshor_verified()
    assert kmy["KMY-PP3"].digest in submitted.table_digests()
    assert trust.level_of_id("KMY-PP3") == "lean" and trust.level_of_digest(kmy["KMY-PP3"].digest) == "lean"
    # Stage 12 (A): KMY-PTP3/4 are lean now by their own one-band mirror runs; without that pin they are replay
    assert trust.level_of_id("KMY-PTP3") == "lean" and trust.level_of_id("KMY-PTP4") == "lean"
    assert trust.level_of_digest(kmy["KMY-PTP3"].digest, use_table_mirror=False) == "replay"
    assert _rec(KMY_SEED)["trust"]["tables"]["tag"] == "Tables Lean-verified"
    assert "ForShor policy" in pv._source(kmy["KMY-PP3"].digest)["status"]


def test_promotion_needs_a_passing_forshor_run(tmp_path, monkeypatch):
    d = tmp_path / submitted.KIND / KMY_SEED
    d.mkdir(parents=True)
    (d / "site_policy.json").write_text((SEEDS / KMY_SEED / "site_policy.json").read_text())
    run = {"id": KMY_SEED, "status": "lean-ok", "conversion_agrees": None, "printed_policy_matches": True}
    (tmp_path / submitted.KIND / submitted.SEED_RUNS).write_text(json.dumps({"runs": {f"{KMY_SEED}:default": run}}))
    monkeypatch.setattr(submitted, "DATA", tmp_path)
    assert submitted.seed_forshor_verified() == {}
    run["conversion_agrees"] = True
    (tmp_path / submitted.KIND / submitted.SEED_RUNS).write_text(json.dumps({"runs": {f"{KMY_SEED}:default": run}}))
    assert list(submitted.seed_forshor_verified()) == [KMY_SEED]


# ------------------------------------------------------------------------------------------------ boards: hook and live pricing
@pytest.mark.parametrize("mode,count", [("PP", 23), ("PTP", 8)])
def test_seeded_board_lists_every_seed_with_trust_provenance_and_badge(client, mode, count):
    r = client.get(f"/api/seeded?mode={mode}&delta=0.01").json()
    assert r["count"] == count == len(r["policies"])
    assert [p["rank"] for p in r["policies"]] == list(range(1, count + 1))
    assert sum(r["policy_levels"].values()) == count
    for p in r["policies"]:
        assert p["seeded_from_planner"] and p["badge"] == "seeded from planner outputs" and p["priced_live"]
        assert p["policy_trust"]["tag"] == trust.policy_trust(_rec(p["id"]))["tag"]
        assert p["provenance"]["planner"].startswith("engine/tcost/planner.py") and p["provenance"]["occurrences"] >= 1
    assert {b["key"] for b in r["benchmarks"]} == {"reference", "kmy"}
    assert all("not ranked" in b["note"] and "rank" not in b for b in r["benchmarks"])
    assert not {p["id"] for p in r["policies"]} & {"reference", "kmy"}


def test_seeded_board_prices_live_with_tcost():
    b = sb.board(tt.PAIR, 1e-4)                               # a delta no record could have stored
    for p in b["policies"][:4]:
        P = tt.load_policies(SEEDS / p["id"] / "site_policy.json")[0]
        assert p["T"] == fr.point(P, fr.HERO_N, 1e-4, with_qubits=False)["T"] and p["agree"]
        assert p["delta"] == 1e-4


def test_seeded_board_refuses_a_record_whose_policy_does_not_match_its_bands(tmp_path, monkeypatch):
    import shutil
    shutil.copytree(SEEDS / KMY_SEED, tmp_path / submitted.KIND / KMY_SEED)
    rec = json.loads((tmp_path / submitted.KIND / KMY_SEED / "record.json").read_text())
    rec["bands"][0]["digest"] = "0" * 64
    (tmp_path / submitted.KIND / KMY_SEED / "record.json").write_text(json.dumps(rec))
    monkeypatch.setattr(submitted, "DATA", tmp_path)
    r = sb.board(tt.PAIR, 0.01)
    assert r["count"] == 0 and r["policies"][0]["T"] is None and "does not match" in r["policies"][0]["error"]


def test_full_board_hooks_the_seeds_with_trust_and_benchmarks(client):
    from site_engine import fullalg as fa
    r = client.get("/api/full/submitted?delta=0.01").json()
    seeded = [p for p in r["policies"] if p.get("seeded_from_planner")]
    assert len(seeded) == r["seeded"] == 23
    for p in seeded:
        assert p["priced_live"] and p["model"] == fa.MODEL and p["badge"] == "seeded from planner outputs"
        assert p["policy_trust"]["level"] in trust.POLICY_LEVELS and p["provenance"]["first_seen"]
        assert "lean" not in p and "sto_mirror" not in p          # the board row carries the summary, not the raw runs
    assert [b["key"] for b in r["benchmarks"]] == ["reference", "kmy", "paper"]
    assert all("not ranked" in b["note"] for b in r["benchmarks"])
    assert r["benchmarks"][2]["T"] == 2.31e12 and "not comparable" in r["benchmarks"][2]["note"]
    one = next(p for p in seeded if p["id"] == KMY_SEED)
    # Stage 13: the full run keeps scanned widths and prices the seed's pre-re-plan thresholds (with that policy's trust)
    P = tt.load_policies(SEEDS / KMY_SEED / "site_policy.pre_exact.json")[0]
    assert one["single_run_T"] == pytest.approx(fa.price_full(P, fa.HERO_N, 0.01)["T"], rel=1e-12)
    assert one["policy_trust"]["level"] == "forshor"


def test_policy_panel_of_a_record_shows_its_policy_trust(client):
    v = client.get(f"/api/policy?mode=PP&delta=0.01&record={KMY_SEED}").json()
    # Stage 13: the PP board's record is re-planned under exact widths; ForShor ran the old thresholds (stale), the exact-
    # width mirror run is the policy's level. The full run (below) prices the old thresholds and keeps ForShor's level.
    # Stage 15 re-pin: the re-planned policy itself passed ForShor 10fe620 (at ForShor's scanned widths); the exact-width
    # mirror run is still accepted.
    assert v["policy_trust"]["level"] == "forshor" and v["seeded_from_planner"] and v["sto_mirror"]["status"] == "accepted"
    assert v["policy_trust"]["tag"] == "ForShor policy-verified (at ForShor's scanned widths)"
    assert v["forshor"]["status"] == "policy-verified" and v["replanned"]["changed"] and v["history"][0]["bands"]
    ptp = next(r for r in submitted.seeded() if r["mode"] == tt.TRIPLE)
    v = client.get(f"/api/policy?mode=PTP&delta=0.01&record={ptp['id']}").json()
    assert v["policy_trust"]["level"] in ("sto-mirror", "tables") and v["mode"] == tt.TRIPLE
    v = client.get(f"/api/full/policy?delta=0.01&record={KMY_SEED}").json()
    assert v["policy_trust"]["tag"] == "ForShor policy-verified" and v["full"]["T"] > 0
    assert client.get("/api/policy?mode=PP&record=Snope").status_code == 404


def test_the_page_shows_the_policy_trust_and_the_seeded_badge():
    """Stage 19: the policy-record lists are off the page (leaderboards only); the policy panel still shows a policy's
    trust and the seeded badge, and the API keeps /api/seeded and /api/full/submitted for scripts."""
    js = (ROOT / "frontend" / "app.js").read_text()
    html = (ROOT / "frontend" / "index.html").read_text()
    assert "seeded from planner outputs" in js and "policy_trust" in js
    assert 'id="seeded-pp"' not in html and 'id="seeded-ptp"' not in html and 'id="sub"' not in html
    assert "/api/seeded" not in js and "/api/full/submitted" not in js
    assert "Policy Lean-verified at exact widths" not in js and "Policy algebra" not in js            # the tag comes from the server (one source of wording)


# ------------------------------------------------------------------------------------------------ Lean (real mirror runs)
def _mirror_or_skip():
    build = spv.build_dir()
    if not (build / ".lake").is_dir():
        pytest.skip(f"no built STO policy mirror at {build}")
    if spv.other_lean():
        pytest.skip("another Lean process is running (one at a time)")
    return build


def _smallest(mode):
    rs = [r for r in submitted.seeded() if r["mode"] == mode]
    return min(rs, key=lambda r: (sum(b["k"] for b in r["bands"]), r["id"]))


@pytest.mark.lean
@pytest.mark.parametrize("mode", [tt.PAIR, tt.TRIPLE])
def test_mirror_accepts_a_seed(mode, tmp_path):
    _mirror_or_skip()
    r = _smallest(mode)
    # Stage 13: exact widths (PP: the live mirror; PTP: the PTP suite)
    if mode == tt.PAIR:
        res = spv.check(Path(r["site_policy_path"]), widths="exact")
    else:
        import ptp_policy_verify as ppv
        res = ppv.check(Path(r["site_policy_path"]))
    assert res["verdict"] == "accepted", (res.get("stage"), res.get("reason"))
    assert res["mode"] == mode and res["statement_pinned"] and res["exact_ladder_kernel_proved"]
    assert set(res["axioms"]) <= {"propext", "Classical.choice", "Quot.sound"}
    assert res["peak_gb"] < spv.CAP_GB and all(m["verdict"] == "replayed" and m["peak_gb"] < spv.CAP_GB for m in res["replay"])
    assert [[x["width"], x["k"]] for x in res["exact_ladder"]] == [list(x) for x in r["ladder_2048"]]
    assert res["site_policy_sha256"] == r["site_policy_sha256"]


@pytest.mark.lean
def test_mirror_rejects_a_faulty_point_order(tmp_path):
    _mirror_or_skip()
    doc = json.loads((SEEDS / KMY_SEED / "site_policy.json").read_text())
    po = doc["bands"][0]["point_order"]
    po[0], po[1] = po[1], po[0]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(doc))
    res = spv.check(p)
    assert res["verdict"] == "rejected" and res["stage"] == "check"
