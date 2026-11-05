"""The two records below are REAL output of the interim STO fork's harnesses (built at Lean 4.36.0-rc1, ForShor 6d55ad9), run
2026-11-05, with local scratch paths scrubbed. They exist so the converter is tested against the harnesses' real formats: a first
draft that only knew check_banded_policy's field names refused a genuinely accepted check_ptp_policy record."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import canon, evidence  # noqa: E402

DATA = Path(__file__).parent / "data"
MODEL = json.loads((DATA / "golden.json").read_text())["model"]
REAL = DATA / "real"
TREE = {"lean": "leanprover/lean4:v4.36.0-rc1", "forshorPin": "6d55ad9968dc067c68a7023bbbaed6c955d9190c", "stoBase": "361e81e"}


def envelope(digest16, harness, record_name=None):
    policy_file = REAL / f"{digest16}.policy.json"
    record = json.loads((REAL / (record_name or f"{digest16}.record.json")).read_text())
    return {"schema": evidence.SCHEMA, "policyDigest": canon.policy_digest(json.loads(policy_file.read_text())),
            "policyFile": {"name": policy_file.name, "sha256": hashlib.sha256(policy_file.read_bytes()).hexdigest()},
            "harness": harness, "record": record, "tree": dict(TREE)}, json.loads(policy_file.read_text())


def test_the_real_records_are_bound_to_their_policy_files():
    for d in ("1bc92ac6e1e82ba3", "627bb6e37bc35bfa"):
        env, _ = envelope(d, "x")
        assert env["policyFile"]["sha256"] == env["record"]["policy_sha256"], d


def test_a_real_phase_product_record_becomes_evidence_on_the_epochs_toolchain():
    env, policy = envelope("1bc92ac6e1e82ba3", "check_banded_policy")
    got, why = evidence.stomirror_from_envelope(env, policy, MODEL, "pp")
    assert why is None and got["harness"] == "check_banded_policy"
    assert got["ran"] == {"lean": MODEL["lean"], "stoCommit": MODEL["sto"]["commit"], "forshorPin": MODEL["forshor"]["pin"]}
    assert evidence.stomirror_from_envelope(env, policy, MODEL, "full")[0] is not None   # the full board uses the same harness


def test_a_real_phase_triple_product_record_becomes_evidence_regression_for_the_widths_field():
    env, policy = envelope("627bb6e37bc35bfa", "check_ptp_policy")
    assert "widths_rule" not in env["record"] and env["record"]["widths"] == "exact"   # the field the first draft did not read
    got, why = evidence.stomirror_from_envelope(env, policy, MODEL, "ptp")
    assert why is None and got["harness"] == "check_ptp_policy" and got["widths"] == "exact"


def test_a_real_timeout_is_not_evidence_and_says_why():
    env, policy = envelope("80132d41f7f5ecf0", "check_banded_policy", "80132d41f7f5ecf0.timeout.record.json")
    got, why = evidence.stomirror_from_envelope(env, policy, MODEL, "pp")
    assert got is None and "did not accept" in why and "timeout" in why


def test_a_real_record_cannot_vouch_for_another_policy():
    env, _ = envelope("1bc92ac6e1e82ba3", "check_banded_policy")
    _, other = envelope("627bb6e37bc35bfa", "check_ptp_policy")
    assert evidence.stomirror_from_envelope(env, other, MODEL, "pp")[0] is None
