"""Trust tags (PLAN_v2 3.3): what the site says is verified must be exactly what the data supports.

Lean proves tables; the T count is Python. Each number carries the weakest trust level among its tables."""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from site_engine import DATA, intake, library as lib, policy_view as pv, submitted, trust  # noqa: E402
from tcost import tables as tt  # noqa: E402

FRONT = ROOT / "frontend"


@pytest.fixture(autouse=True)
def _fresh():
    trust.clear()
    yield
    trust.clear()


# ------------------------------------------------------------------------------------------------ levels
def test_every_library_id_has_a_level_and_the_counts_are_the_known_ones():
    lv = trust.id_levels()
    sources = {s.id for s in lib.submissions()} | {r["id"] for r in lib.personal_tables()} | {r["id"] for r in lib.kmy_tables()}
    assert set(lv) == sources
    assert set(lv.values()) <= set(trust.LEVELS) - {"none"}
    n = {l: sum(v == l for v in lv.values()) for l in trust.LEVELS}
    # Stage 11: ci 4 (3 archive-only submissions + P651fc8ae389d), replay 47 (45 DP + KMY-PTP3/4), lean 155.
    # Stage 12 (A): each of the 51 replay/ci tables passed its own one-band STO mirror run (kernel-checked
    # PolicyTable.check = STO's table contract): ci 4 -> 0, replay 47 -> 1, lean 155 -> 205. The one left,
    # PTP7-e1d7b1c6690f, timed out in the mirror's report stage (not a Lean verdict).
    assert n["ci"] == 0 and n["replay"] == 1
    assert n["lean"] == 17 + 137 + 1 + 4 + 46


def test_levels_by_source():
    assert trust.level_of_id("KMY-PP3") == "lean"          # promoted by a ForShor policy run (Stage 11, STO mirror)
    assert trust.level_of_id("KMY-PTP3") == "lean"          # was replay; Stage 12 (A): its own one-band STO mirror run kernel-checked it (table_sto_mirror_runs.json)
    assert trust.level_of_id("293d51de2c05") == "lean"      # an accepted STO policy
    assert trust.level_of_id("390223a8146a") == "lean"      # was ci (STO CI only); Stage 12 (A): its own one-band STO mirror run kernel-checked it (table_sto_mirror_runs.json)
    assert trust.level_of_id("651fc8ae389d") == "lean"      # was ci (no Lean source in STO main); Stage 12 (A): its own one-band STO mirror run kernel-checked it (table_sto_mirror_runs.json)
    assert len(trust.replayed()) == 17 and "651fc8ae389d" not in trust.replayed()
    assert trust.level_of_id("not-a-table") == "none"
    dp = {r["status"]: r["id"] for r in lib.personal_tables()}
    assert trust.level_of_id(dp["kernel"]) == "lean" and trust.level_of_id(dp["full_verifier"]) == "lean"
    assert trust.level_of_id(dp["replay"]) == "lean"         # was replay; Stage 12 (A): its own one-band STO mirror run kernel-checked it (table_sto_mirror_runs.json)


def test_level_by_content_takes_the_strongest_source():
    # a PTP KMY table: KMY-PP3 is Lean-verified since Stage 11 (STO mirror), by the ForShor policy run of S707dd51bf951
    kmy = next(r["table"] for r in lib.kmy_tables() if r["id"] == "KMY-PTP3")
    assert trust.level_of_digest(kmy.digest) == "lean"         # was replay; Stage 12 (A): its own one-band STO mirror run kernel-checked it (table_sto_mirror_runs.json)
    assert trust.level_of_digest(kmy.digest, use_table_mirror=False) == "replay"
    assert trust.level_of_digest(next(r["table"] for r in lib.kmy_tables() if r["id"] == "KMY-PP3").digest) == "lean"
    acc = next(s for s in lib.submissions() if s.accepted and s.id in trust.replayed())
    assert trust.level_of_digest(next(iter(acc.tables))) == "lean"
    assert trust.level_of_digest("0" * 64) == "none"


def test_aggregate_and_tag_wording():
    t = lambda ls: trust.tag(trust.aggregate(ls))
    assert t([]) == (trust.NO_TABLES, "ok")
    assert t(["lean", "lean"]) == ("Tables Lean-verified", "ok")
    assert t(["lean", "ci"]) == ("Tables Lean-verified (1 of 2 by STO CI archive)", "ok")
    assert t(["lean", "replay", "ci"])[1] == "warn" and "1 of 3 tables replay-checked only" in t(["lean", "replay", "ci"])[0]
    assert t(["lean", "none"]) == ("1 of 2 tables unknown: not verified", "warn")
    assert t(["none"]) == ("pasted: not verified", "warn")
    assert trust.aggregate(["lean", "replay"])["level"] == "replay"      # the weakest wins


def test_label_parsing_and_hero_labels():
    assert trust.label_ids("a + b + leaf") == ["a", "b"] and trust.label_ids("leaf") == [] and trust.label_ids("") == []
    assert trust.of_label("293d51de2c05 + 546fea33a7a4 + PP2-b5a233d70d7b")["level"] == "lean"
    assert trust.of_label("KMY-PTP4 + KMY-PTP3")["level"] == "lean"     # was replay; Stage 12 (A): its own one-band STO mirror run kernel-checked it (table_sto_mirror_runs.json)
    assert trust.of_label("leaf")["total"] == 0


def test_the_wording_never_claims_lean_for_the_cost():
    txt = trust.explain()
    assert "not" in txt["cost"].lower() and "Python" in txt["cost"]
    assert set(txt) == set(trust.LEVELS) | {"cost", "pricing"}      # Stage 12 (C): the pricing rule


# ------------------------------------------------------------------------------------------------ pasted policies
def _wrap(T: tt.Table) -> dict:
    return {"mode": T.mode, "bands": [{"threshold": 0, "k": T.k, "point_order": [tt.point_str(p) for p in T.point_order],
                                        "operations": [tt.op_json(o) for o in T.ops]}]}


def test_pasted_policy_of_lean_verified_tables_is_tagged_lean_but_not_as_a_whole():
    doc = json.loads((DATA / "reference_policies.json").read_text())["policies"][0]["policy"]
    p = intake.cost(json.dumps(doc))["policies"][0]
    assert p["trust"]["level"] == "lean" and p["tags"][0] == "Tables Lean-verified"
    assert "pasted: bands not Lean-checked" in p["tags"]            # never "verified" as a whole
    assert p["policy"]["trust"]["level"] == "lean"


def test_pasted_policy_of_kmy_ptp_table_is_lean_since_stage12():
    # PTP: the PP KMY table is Lean-verified since Stage 11 (STO mirror), by the ForShor policy run of S707dd51bf951
    T = next(r["table"] for r in lib.kmy_tables() if r["table"].mode == tt.TRIPLE)
    p = intake.cost(json.dumps(_wrap(T)))["policies"][0]
    # was replay-only; Stage 12 (A): its own one-band STO mirror run kernel-checked it (table_sto_mirror_runs.json)
    assert p["trust"]["level"] == "lean" and p["tags"][0] == "Tables Lean-verified"


def test_pasted_policy_with_an_unknown_table_is_not_verified():
    base = next(T for T in lib.sto_tables() if T.mode == tt.PAIR and T.k == 2)
    ops = [["shiftL", 0, 1], ["shiftR", 0, 1]] + [tt.op_json(o) for o in base.ops]      # same table, new content, still valid
    doc = {"mode": base.mode, "bands": [{"threshold": 0, "k": base.k, "point_order": [tt.point_str(q) for q in base.point_order],
                                         "operations": ops}]}
    assert trust.level_of_digest(tt.Table.from_json({"mode": base.mode, "k": base.k, "point_order": doc["bands"][0]["point_order"],
                                                     "operations": ops}).digest) == "none"
    p = intake.cost(json.dumps(doc))["policies"][0]
    assert p["trust"]["level"] == "none" and p["tags"][0] == "pasted: not verified" and p["trust"]["kind"] == "warn"


def test_policy_views_carry_trust_and_tables_their_level():
    v = pv.for_milestone("all_sto", lib.PAIR, 2048, 0.01)
    assert v["trust"]["level"] in ("lean", "ci") and v["trust"]["tag"].startswith("Tables Lean-verified")
    assert all(t["level"] in trust.LEVELS for t in v["tables"])
    k = pv.for_milestone("kmy", lib.TRIPLE, 2048, 0.01)     # PP's KMY-PP3 is Lean-verified since Stage 11 (STO mirror)
    assert k["trust"]["level"] == "lean" and k["trust"]["tag"] == "Tables Lean-verified"   # was replay; Stage 12 (A): its own one-band STO mirror run kernel-checked it (table_sto_mirror_runs.json)
    assert pv.for_milestone("kmy", lib.PAIR, 2048, 0.01)["trust"]["tag"] == "Tables Lean-verified"


# ------------------------------------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


def test_meta_exposes_the_trust_map_and_wording(client):
    m = client.get("/api/meta").json()
    assert m["table_trust"]["KMY-PTP3"] == "lean" and m["table_trust"]["293d51de2c05"] == "lean"   # was replay; Stage 12 (A): its own one-band STO mirror run kernel-checked it (table_sto_mirror_runs.json)
    assert m["table_trust"]["KMY-PP3"] == "lean"            # Stage 11 (STO mirror): promoted by the ForShor policy run
    assert set(m["trust_text"]) == set(trust.LEVELS) | {"cost", "pricing"}     # Stage 12 (C): the pricing rule
    assert "Lean: not in this demo" not in m["trust_tags"]


def test_cost_endpoint_returns_trust(client):
    doc = json.loads((DATA / "reference_policies.json").read_text())["policies"][0]["policy"]
    r = client.post("/api/cost", json={"text": json.dumps(doc), "n": 2048, "delta": 0.01}).json()
    assert r["policies"][0]["trust"]["tag"] == "Tables Lean-verified"


def test_full_submitted_endpoint_prices_every_record_live(client):
    """Stage 11: /api/full/submitted re-prices each PhaseProduct policy record with the full-run pricer; a stored price is
    never shown as the score (was: an empty Lean-verified-only list read from the records' own single_run_T)."""
    from site_engine import fullalg as fa
    from display_text import plain                       # Stage 19: the API serves the plain wording (no module names)
    r = client.get("/api/full/submitted").json()
    assert r["tier"] == plain(fa.TIER)
    for p in r["policies"]:
        assert p.get("error") or (p["priced_live"] and p["model"] == fa.MODEL and p["single_run_T"] > 0)
    if not r["policies"]:
        assert "./run.sh forshor" in r["note"]


def _record(root: Path, rid: str, T: float, lean: bool = True, drop: str = "") -> None:
    rec = {"id": rid, "created_at": "2026-10-23T00:00:00Z", "bands": [{"threshold": 2048, "k": 3}], "single_run_T": T,
           "lean_verified": lean}
    rec.pop(drop, None)
    (root / rid).mkdir(parents=True)
    (root / rid / "record.json").write_text(json.dumps(rec))


def test_submitted_board_lists_only_lean_verified_records_ranked_by_T(tmp_path, monkeypatch):
    root = tmp_path / submitted.KIND
    _record(root, "b", 3.0e12)
    _record(root, "a", 2.0e12)
    _record(root, "c", 1.0e12, lean=False)                  # never listed: not Lean-verified
    monkeypatch.setattr(submitted, "DATA", tmp_path)
    r = submitted.listing()
    assert [p["id"] for p in r["policies"]] == ["a", "b"] and [p["rank"] for p in r["policies"]] == [1, 2]
    assert "ranked by single-run T" in r["note"]


def test_submitted_board_refuses_a_malformed_record(tmp_path, monkeypatch):
    _record(tmp_path / submitted.KIND, "x", 1.0, drop="bands")
    monkeypatch.setattr(submitted, "DATA", tmp_path)
    with pytest.raises(ValueError, match="missing"):
        submitted.listing()


# ------------------------------------------------------------------------------------------------ the page does not overclaim
def test_the_page_no_longer_says_there_is_no_lean():
    for f in ("index.html", "app.js"):
        text = (FRONT / f).read_text()
        assert "Lean: not in this demo" not in text and "no Lean verification" not in text, f
    assert "not in this demo" not in json.loads((FRONT / "faq.json").read_text())["note"]


def test_the_page_never_says_the_cost_is_proved_in_lean():
    html, js = (FRONT / "index.html").read_text(), (FRONT / "app.js").read_text()
    assert "T counted in Python, not proved in Lean" in js and "counted in Python" in html


def test_participate_has_no_tabs_left():
    # Re-pin 2026-10-28 (PLAN_v2 Stage 14), reason: the owner's queued UI change removed the "Submit tables (Lean)" and
    # "Quick check (Python)" tabs (policy-only submissions); the one remaining section is static, so there are no tabs or
    # panes and no paste box. The elements the script still writes into must stay (see test_every_dom_id_the_script_reads_exists).
    html = (FRONT / "index.html").read_text()
    assert not re.findall(r'data-ptab="(\w+)"', html) and not re.findall(r'data-pane="(\w+)"', html)
    assert 'id="p-go"' not in html and 'id="p-text"' not in html
    assert 'id="sub-note"' not in html               # Stage 19: the policy-record lists are off the page


def test_every_dom_id_the_script_reads_exists():
    html, js = (FRONT / "index.html").read_text(), (FRONT / "app.js").read_text()
    ids = set(re.findall(r'id="([\w-]+)"', html))
    used = set(re.findall(r'\$\("#([\w-]+)["\s\)]', js)) | set(re.findall(r'\$\("#([\w-]+)"', js))
    assert not (used - ids), sorted(used - ids)


def test_faq_note_matches_the_trust_model():
    note = json.loads((FRONT / "faq.json").read_text())["note"]
    assert "Lean proves the tables" in note and "not proved in Lean" in note and "ForShor" in note and "not wired" not in note


# ---- the page's JS mirror of trust.tag (frontend/app.js labelTrust) must say what trust.tag says
import subprocess as _subprocess

_APP_JS = Path(__file__).resolve().parents[2] / "frontend" / "app.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_label_trust_matches_trust_tag():
    src = _APP_JS.read_text()
    block = src[src.index("const TRUST_RANK"):src.index("const costTag")]
    cases = [
        [], ["lean"], ["lean", "lean", "lean"], ["lean", "ci"], ["ci", "ci", "lean"], ["lean", "replay"],
        ["replay"], ["replay", "replay", "ci"], ["none"], ["none", "none"], ["lean", "none"], ["ci", "replay", "none"],
    ]
    probe = (
        "const S={meta:{table_trust:{}}};const tag=(text,kind='ok')=>({text,kind});\n" + block +
        "\nconst out=[];for(const lv of " + json.dumps(cases) + "){const ids=lv.map((l,i)=>'t'+i);"
        "S.meta.table_trust={};ids.forEach((id,i)=>{if(lv[i]!=='none')S.meta.table_trust[id]=lv[i];});"
        "const t=labelTrust(ids.length?ids.join(' + '):'leaf');out.push([t.text,t.kind]);}\n"
        "console.log(JSON.stringify(out));"
    )
    r = _subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    for lv, (text, kind) in zip(cases, got):
        want_text, want_kind = trust.tag(trust.aggregate(lv))
        assert (text, kind) == (want_text, want_kind), lv


# ---- Stage 5 (PLAN_v2 5.1): the board says its rule; the page does not disclose ForShor's unreported gap
def test_the_leaderboard_rule_and_subtitle_are_not_hidden():
    html, js = (FRONT / "index.html").read_text(), (FRONT / "app.js").read_text()
    for i in ("board-full", "board-pp", "board-ptp"):              # one board per section (PLAN_v2 8)
        tag = re.search(r'<[^>]*id="%s"[^>]*>' % i, html).group(0)
        assert "quiet" not in tag, i
    assert 'class: "board-rule"' in js and 'class: "board-sub"' in js


def test_the_page_shows_no_dp_library_and_no_frequency_plots():
    html, js = (FRONT / "index.html").read_text(), (FRONT / "app.js").read_text()
    assert "DP library" not in html and "+ DP" not in js and 'id="chart"' not in html and 'id="milestones"' not in html


def test_the_page_does_not_describe_forshors_eval_gap():
    html = (FRONT / "index.html").read_text()
    faq = (FRONT / "faq.json").read_text()
    for text in (html, faq):
        assert "does not ban" not in text and "arbitrary code" not in text


def test_the_hero_no_longer_claims_the_paper_reproduction_for_the_headline():
    assert "reproduces the paper's stack exactly" not in (FRONT / "app.js").read_text()
