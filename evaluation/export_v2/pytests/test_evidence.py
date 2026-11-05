"""Run from evaluation/: python -m pytest export_v2/pytests -q. The engine and Lean are NOT needed: the harness is a stub."""
import copy
import hashlib
import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import canon, evidence  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "data" / "golden.json").read_text())
MODEL = GOLDEN["model"]
PP_POLICY = GOLDEN["cases"]["full_submission"]["view"]["policy_json"]
PTP_POLICY = GOLDEN["cases"]["ptp_planner"]["view"]["policy_json"]

STUB = textwrap.dedent('''
    import hashlib, json, sys
    from pathlib import Path
    args = sys.argv[1:]
    policy = Path(args[0]); out = Path(args[args.index("--out") + 1])
    doc = json.loads(policy.read_text())
    verdict = "rejected" if "REJECT" in doc["name"] else "accepted"
    rec = {"tool": "stub", "policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest(), "target_width": 2048,
           "widths_rule": "exact", "mode": doc["mode"], "verdict": verdict,
           "bands": [{"threshold": b["threshold"], "k": b["k"]} for b in doc["bands"]]}
    if verdict == "rejected":
        rec["rejection"] = {"stage": "threshold", "reason": "does not shrink"}
    out.write_text(json.dumps(rec))
    sys.exit(0 if verdict == "accepted" else 1)
''')


def make_tree(tmp_path, *, lean=None, pin=None, base="361e81e", stub=STUB):
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "lean-toolchain").write_text((lean or MODEL["lean"]) + "\n")
    (tree / "lake-manifest.json").write_text(json.dumps({"packages": [
        {"name": "mathlib", "rev": "x"}, {"name": "Fast_multiplication", "rev": pin or MODEL["forshor"]["pin"]}]}))
    (tree / "JMC_NOTES.md").write_text(f"- **Base:** upstream `main` at `{base}` (2026-11-03)\n")
    for name in ("check_banded_policy.py", "check_ptp_policy.py"):
        (tree / "scripts" / name).write_text(stub)
    return tree


def envelope(tmp_path, policy=PP_POLICY, board="pp", **tree_kwargs):
    return evidence.run_mirror(policy, board, make_tree(tmp_path, **tree_kwargs), tmp_path / "out")


def test_a_bound_accepted_exact_check_on_the_epochs_toolchain_becomes_evidence(tmp_path):
    env = envelope(tmp_path)
    got, why = evidence.stomirror_from_envelope(env, PP_POLICY, MODEL, "pp")
    assert why is None
    assert got["harness"] == "check_banded_policy" and got["widths"] == "exact" and got["verdict"] == "accepted"
    assert got["claim"] == "policy-algebra-and-exact-widths"
    assert got["ran"] == {"lean": MODEL["lean"], "stoCommit": MODEL["sto"]["commit"], "forshorPin": MODEL["forshor"]["pin"]}
    assert got["recordSha256"] == canon.sha256_hex(env["record"])
    # the same policy under another title is the same policy: the evidence still applies
    retitled = copy.deepcopy(PP_POLICY); retitled["name"] = "another title"
    assert evidence.stomirror_from_envelope(env, retitled, MODEL, "full")[0] is not None


def test_a_phase_triple_product_policy_uses_its_own_harness(tmp_path):
    env = envelope(tmp_path, PTP_POLICY, "ptp")
    assert env["harness"] == "check_ptp_policy"
    assert evidence.stomirror_from_envelope(env, PTP_POLICY, MODEL, "ptp")[0]["harness"] == "check_ptp_policy"
    assert "harness" in evidence.stomirror_from_envelope(env, PTP_POLICY, MODEL, "pp")[1]


@pytest.mark.parametrize("name,mutate,expected", [
    ("another policy", lambda e: e.__setitem__("policyDigest", "0" * 64), "another policy"),
    ("not accepted", lambda e: e["record"].update(verdict="rejected", rejection={"stage": "threshold"}), "did not accept"),
    ("scan widths", lambda e: e["record"].update(widths_rule="scan"), "exact-width"),
    ("other width", lambda e: e["record"].update(target_width=1024), "exact-width"),
    ("other mode", lambda e: e["record"].update(mode="PhaseTripleProduct"), "mode or bands"),
    ("other bands", lambda e: e["record"]["bands"][0].update(threshold=1), "mode or bands"),
    ("other file", lambda e: e["policyFile"].update(sha256="1" * 64), "different file"),
    ("wrong schema", lambda e: e.update(schema="x"), "not a mirror-evidence"),
])
def test_evidence_that_is_not_bound_accepted_exact_and_for_this_policy_is_refused(tmp_path, name, mutate, expected):
    env = envelope(tmp_path)
    mutate(env)
    got, why = evidence.stomirror_from_envelope(env, PP_POLICY, MODEL, "pp")
    assert got is None and expected in why, (name, why)


@pytest.mark.parametrize("kwargs,expected", [
    ({"lean": "leanprover/lean4:v4.28.0"}, "v4.28.0"),
    ({"pin": "f" * 40}, "ForShor fffffffff"),
    ({"base": "deadbee"}, "STO base"),
])
def test_evidence_from_another_toolchain_is_reported_not_used(tmp_path, kwargs, expected):
    got, why = evidence.stomirror_from_envelope(envelope(tmp_path, **kwargs), PP_POLICY, MODEL, "pp")
    assert got is None and expected in why


def test_the_runner_records_a_rejection_and_fails_loudly_without_a_record(tmp_path):
    bad = copy.deepcopy(PP_POLICY); bad["name"] = "REJECT me"
    env = evidence.run_mirror(bad, "pp", make_tree(tmp_path), tmp_path / "o1")
    assert env["record"]["verdict"] == "rejected"
    assert "did not accept" in evidence.stomirror_from_envelope(env, bad, MODEL, "pp")[1]
    silent = make_tree(tmp_path / "x", stub="import sys; sys.exit(3)")
    with pytest.raises(RuntimeError, match="no record"):
        evidence.run_mirror(PP_POLICY, "pp", silent, tmp_path / "o2")


def test_the_store_finds_evidence_by_policy_and_explains_what_is_missing(tmp_path):
    out = tmp_path / "out"
    evidence.run_mirror(PP_POLICY, "pp", make_tree(tmp_path, base="deadbee"), out)
    store = evidence.EvidenceStore(out)
    got, why = store.stomirror(PP_POLICY, MODEL, "pp")
    assert got is None and "STO base" in why
    assert evidence.EvidenceStore(out).stomirror(PTP_POLICY, MODEL, "ptp") == (None, "no mirror evidence for this policy")
    good = tmp_path / "good"
    evidence.run_mirror(PP_POLICY, "pp", make_tree(tmp_path / "g"), good)
    assert evidence.EvidenceStore(good).stomirror(PP_POLICY, MODEL, "pp")[0]["ran"]["lean"] == MODEL["lean"]


def test_tree_pins_read_a_trees_own_files(tmp_path):
    assert evidence.tree_pins(make_tree(tmp_path)) == {"lean": MODEL["lean"], "forshorPin": MODEL["forshor"]["pin"], "stoBase": "361e81e"}
    assert evidence.tree_pins(tmp_path / "empty") == {"lean": None, "forshorPin": None, "stoBase": None}


VERIFIED = {"lean": {"status": "policy-verified", "policy_verified": True, "runs": [
    {"status": "lean-ok", "printed_policy_matches": True, "conversion_agrees": True,
     "forshor": {"commit": "10fe620568dfc1b4b3d98debaaf78219e3fa40f2"}}]}}


def test_forshor_evidence_counts_only_at_the_epochs_pin_and_maps_its_two_flags():
    got, why = evidence.forshor_from_seed(VERIFIED, MODEL)
    assert got is None and "10fe620" in why and "6d55ad9" in why
    at_pin = copy.deepcopy(VERIFIED)
    at_pin["lean"]["runs"][0]["forshor"]["commit"] = MODEL["forshor"]["pin"]
    got, why = evidence.forshor_from_seed(at_pin, MODEL)
    assert why is None and got == {"verified": True, "bandsAgree": True, "irPriceAgrees": True,
                                   "claim": "forshor-policy-submission", "forshorCommit": MODEL["forshor"]["pin"]}
    at_pin["lean"]["runs"][0]["conversion_agrees"] = False
    assert evidence.forshor_from_seed(at_pin, MODEL)[0]["irPriceAgrees"] is False
    stale = {"lean": {"status": "stale", "policy_verified": False, "reason": "checked before it was re-planned"}}
    assert evidence.forshor_from_seed(stale, MODEL) == (None, "checked before it was re-planned")


def test_a_demo_mirror_record_cannot_satisfy_the_pinned_contract():
    rec = {"sto_mirror": {"status": "accepted", "lean_toolchain": "leanprover/lean4:v4.28.0", "mirror_tree_sha256": "a" * 64}}
    got, why = evidence.stomirror_from_seed(rec, MODEL)
    assert got is None and "v4.28.0" in why
    rec["sto_mirror"]["lean_toolchain"] = MODEL["lean"]
    got, why = evidence.stomirror_from_seed(rec, MODEL)
    assert got is None and "revisions" in why
