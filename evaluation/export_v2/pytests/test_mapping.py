"""Run from evaluation/: python -m pytest export_v2/pytests -q   (the engine is NOT needed for these)."""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import canon, mapping  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "data" / "golden.json").read_text())


def test_epoch_matches_forshor_sites_hash_of_the_same_model():
    assert canon.epoch_of(GOLDEN["model"]) == GOLDEN["epoch"] == "db4be672dc2626b6"


def test_canonical_json_is_sorted_compact_and_rejects_nan():
    assert canon.canonical_json({"b": 1, "a": [2, {"d": 1, "c": 2}]}) == '{"a":[2,{"c":2,"d":1}],"b":1}'
    try:
        canon.canonical_json({"x": float("nan")})
    except ValueError:
        pass
    else:
        raise AssertionError("NaN must not be hashable")


def test_every_panel_equals_what_forshor_sites_builder_made_from_the_same_engine_view():
    for name, case in GOLDEN["cases"].items():
        expected = case["panel"]
        got = mapping.panel_of(case["view"], epoch=GOLDEN["epoch"], row_id=case["rowId"], board=case["board"],
                               delta=case["delta"], verification=expected["verification"])
        assert got == expected, name


def test_the_full_board_panel_carries_the_run_and_the_others_do_not():
    cases = GOLDEN["cases"]
    assert cases["full_submission"]["panel"]["fullRun"] is not None
    assert cases["pp_planner"]["panel"]["fullRun"] is None and cases["ptp_planner"]["panel"]["fullRun"] is None


def test_policy_digest_is_the_same_as_forshor_sites_and_ignores_the_display_title():
    view = GOLDEN["cases"]["full_submission"]["view"]
    assert canon.policy_digest(view["policy_json"]) == GOLDEN["submissionPolicyDigest"]
    retitled = copy.deepcopy(view["policy_json"])
    retitled["name"] = "another title"
    assert canon.policy_digest(retitled) == GOLDEN["submissionPolicyDigest"]
    changed = copy.deepcopy(view["policy_json"])
    changed["bands"][0]["threshold"] += 1
    assert canon.policy_digest(changed) != GOLDEN["submissionPolicyDigest"]


def test_ladder_parsing():
    assert mapping.ladder_of("2048:8 -> 279:3 -> 35:leaf") == [
        {"width": 2048, "k": 8}, {"width": 279, "k": 3}, {"width": 35, "k": None}]


def test_a_row_takes_its_numbers_from_the_panel_it_came_from():
    for name, case in GOLDEN["cases"].items():
        panel = case["panel"]
        row = mapping.row_of(panel, origin="planner", created_at="2026-01-01T00:00:00Z", pull_request_url=None,
                             submission_id=None, detail=mapping.panel_path(GOLDEN["epoch"], case["rowId"], case["board"], case["delta"]))
        full = panel["fullRun"]
        assert row["T"] == (full["T"] if full else panel["T"]), name
        assert row["qubits"] == (full["qubits"] if full else panel["qubits"]), name
        assert row["ladderLabel"] == (full["ladderLabel"] if full else panel["ladderLabel"]), name
        assert "rank" not in row and "ranked" not in row
        assert row["trust"] == mapping.trust_of(panel["verification"])


def test_trust_is_derived_from_the_evidence():
    mirror = {"stoMirror": {"x": 1}, "forshorPolicy": None}
    assert mapping.trust_of(mirror) == {"stoMirror": "accepted", "forshorPolicy": "none", "cost": "python-not-proved-in-lean"}
    assert mapping.trust_of({"stoMirror": None, "forshorPolicy": None})["stoMirror"] == "none"
    both = {"stoMirror": {"x": 1}, "forshorPolicy": {"verified": True}}
    assert mapping.trust_of(both)["forshorPolicy"] == "verified"
    assert mapping.trust_of({"stoMirror": {"x": 1}, "forshorPolicy": {"verified": False}})["forshorPolicy"] == "not-verified"


def test_a_chart_carries_the_four_reference_series_and_no_dp():
    series = {name: [{"n": 2048, "T": 1.0, "ladder_str": "x"}] for name in ("reference", "kmy", "champions", "all_sto", "dp")}
    chart = mapping.chart_of(series, epoch="e" * 16, board="pp", delta="1e-2")
    assert set(chart["series"]) == {"reference", "kmy", "champions", "all_sto"}
