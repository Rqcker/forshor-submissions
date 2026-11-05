"""PLAN_v2 Stage 15 (2026-10-28): the site's ForShor harness on the latest ForShor (10fe620, Lean v4.36.0-rc1).

Fast tests (no Lean): the default tree, the organiser-fixed constants read from the build's own emitted document (not a
fixed number), the commit and toolchain in every record, the memory cap and process-group kill of a stage, and the seed
pin (latest runs count, ForShor-main runs are superseded history)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
import forshor_verify as fv  # noqa: E402

from site_engine import seed_policies as sp, submitted, trust  # noqa: E402
from tcost import tables as tt  # noqa: E402

LATEST = fv.DEFAULT_FORSHOR
MAIN = fv.FALLBACK_FORSHOR
HAVE_LATEST = (LATEST / fv.TEMPLATE_REL).is_file()
HAVE_MAIN = (MAIN / fv.TEMPLATE_REL).is_file()
latest = pytest.mark.skipif(not HAVE_LATEST, reason="no .cache/forshor_latest_build")
both = pytest.mark.skipif(not (HAVE_LATEST and HAVE_MAIN), reason="needs both ForShor trees")


def test_the_default_tree_is_the_latest_forshor():
    assert fv.DEFAULT_FORSHOR.name == "forshor_latest_build" and fv.FALLBACK_FORSHOR.name == "forshor_main_build"
    assert fv.MEM_CAP_BYTES == 8 << 30 and fv.LEAN_SLOT_LIMIT == 3


@latest
def test_provenance_names_the_commit_and_toolchain():
    p = fv.forshor_provenance(LATEST)
    assert p["commit"] == "10fe620568dfc1b4b3d98debaaf78219e3fa40f2" and p["lean_toolchain"] == "leanprover/lean4:v4.36.0-rc1"
    assert p["mathlib"].startswith("4dc43842") and p["default_tree"] is True
    assert not (LATEST / ".git").exists()          # the copy has no pointer into the other agent's worktree


@latest
def test_the_fixed_constants_come_from_the_builds_own_emitted_document():
    doc, info = fv.reference_document(LATEST)
    assert info["source"].startswith("emitted") and info["checked_in_agrees"] is True
    assert doc["benchmark"]["trial_count"] == "386" and info["fixed"]["benchmark"]["trial_count"] == "386"


@both
def test_a_current_document_passes_on_the_latest_tree_and_is_refused_by_the_old_one():
    """Stage 14 open item 3: the old tree's own constant (2,216,900,437,333,460) refused a current document."""
    doc = json.loads((LATEST / "policy.json").read_text())
    fv.python_stage(doc, LATEST, "current")
    assert fv.reference_document(MAIN)[0]["benchmark"]["trial_count"] == "2216900437333460"
    with pytest.raises(fv.HarnessError) as e:
        fv.python_stage(doc, MAIN, "current")
    assert e.value.stage == "python" and "organiser" in e.value.detail


@latest
def test_every_record_names_the_forshor_commit_and_toolchain(tmp_path):
    doc = json.loads((LATEST / "policy.json").read_text())
    P, checks = fv.python_stage(doc, LATEST, "shipped")
    priced = fv.price(doc, P, 0.01)
    out = tmp_path / "run"
    out.mkdir()
    (out / "policy.json").write_text(json.dumps(doc))
    data = (LATEST / fv.TEMPLATE_REL).read_bytes()
    rec = json.loads((fv.record(out, data, fv.content_id(data), LATEST, doc, P, checks, priced, {}, {}) / "record.json").read_text())
    assert rec["forshor"]["commit"].startswith("10fe620") and rec["forshor"]["lean_toolchain"].endswith("v4.36.0-rc1")
    assert rec["forshor"]["reference_document"]["fixed"]["benchmark"]["trial_count"] == "386"


def test_a_stage_over_the_memory_cap_is_killed_with_its_tree(tmp_path):
    info = {}
    code, _ = fv._run_rss([sys.executable, "-c", "x = bytearray(300 << 20); import time; time.sleep(30)"], tmp_path, 60,
                          tmp_path / "log", info=info, mem_cap=100 << 20)
    assert code == 137 and info["killed"] == "memcap" and "MEMCAP" in (tmp_path / "log").read_text()


def test_a_stage_reports_its_peak_tree_memory(tmp_path):
    info = {}
    code, rss = fv._run_rss([sys.executable, "-c", "x = bytearray(200 << 20); import time; time.sleep(4)"], tmp_path, 60,
                            tmp_path / "log", info=info)
    assert code == 0 and info["killed"] is None and info["peak_tree_mb"] >= 150 and rss >= 150


# ------------------------------------------------------------------------------------------------ the seed pin
PIN = json.loads((sp.SEED_RUNS).read_text())


def test_forshor_main_runs_are_superseded_history():
    sup = PIN["superseded"]
    assert len(sup) == 44 and all(r["forshor"]["lean_toolchain"].endswith("v4.28.0") for r in sup.values())
    assert all(r.get("superseded") for r in sup.values())


def test_every_current_run_is_on_the_latest_forshor():
    assert PIN["runs"]
    for r in PIN["runs"].values():
        assert r["forshor"]["commit"] == "10fe620568dfc1b4b3d98debaaf78219e3fa40f2", r["id"]
        assert r["forshor"]["lean_toolchain"] == "leanprover/lean4:v4.36.0-rc1" and r["variant"] in sp.VARIANTS


def _P(sid, name="site_policy.pre_exact.json"):
    return tt.load_policies(sp.OUT / sid / name)[0]


def test_a_superseded_lean_ok_run_supports_no_level():
    sid = "S707dd51bf951"
    only_old = {"runs": {}, "superseded": {k: r for k, r in PIN["superseded"].items() if r["id"] == sid}}
    st = sp.lean_status(sid, _P(sid), only_old)
    assert st["status"] == "not-run" and not st["policy_verified"] and st["superseded"][0]["status"] == "lean-ok"


def test_seed_forshor_verified_reads_only_current_runs(tmp_path, monkeypatch):
    run = {"id": "S707dd51bf951", "budget": "default", "status": "lean-ok", "conversion_agrees": True, "printed_policy_matches": True}
    (tmp_path / submitted.KIND).mkdir()
    monkeypatch.setattr(submitted, "DATA", tmp_path)
    (tmp_path / submitted.KIND / submitted.SEED_RUNS).write_text(json.dumps({"runs": {}, "superseded": {"x": run}}))
    assert submitted.seed_forshor_verified() == {}


def test_the_forshor_level_text_cites_the_latest_forshor():
    assert "10fe620" in trust.explain_policy()["forshor"] and "v4.36.0-rc1" in trust.explain_policy()["forshor"]
    t = trust.policy_trust({"lean": {"policy_verified": True, "budget": "default", "variant": "replanned",
                                     "forshor": "ForShor 10fe620, v4.36.0-rc1"}})
    assert t["level"] == "forshor" and t["tag"].endswith("(at ForShor's scanned widths)") and "EXACT" in t["text"]
