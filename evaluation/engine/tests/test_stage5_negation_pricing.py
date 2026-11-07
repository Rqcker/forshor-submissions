"""Swap 002: the stage-5 pricing of a submission follows the policy's `negation` ("oneQubit" | "scratch"); Lean's printed method
is compared with the submitted key; the default (no key) is unchanged. Offline, no Lean.

Live pricing copy: `engine/fullalg/l2q_full` through `site_engine.fullalg` (`forshor_verify.price` -> `fa.price_full` /
`fa.price_document`). `engine/paper_model/forshor_qualtran` is not on that path and is left as vendored.

* GOLDEN (tests/data/stage5_pricing_golden.json, recorded at 44b757c BEFORE this change): sha256 of the whole price dict and T
  of `price_document`, `price_full` (converted policy) and `price_full(detail=True)` for every printed policy document and the
  first six site policies. No key, and `"scratch"` (the Cuccaro charge this tree has always priced), reproduce it exactly.
* "oneQubit" is priced with the Gidney-increment counts: the T of the board's reference document equals the one the export
  branch's engine (E1, 7e48afc, whose default is that convention) gives; per-negation counts equal ForShor's numerals.
"""
import copy
import glob
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "evaluation/harness"))
sys.path.insert(0, str(ROOT / "evaluation/engine"))
import site_engine  # noqa: E402,F401
from site_engine import fullalg as fa  # noqa: E402
from site_engine import seed_policies as sp  # noqa: E402
import forshor_verify as fv  # noqa: E402
import policy_to_template as ptt  # noqa: E402
import submit as sb  # noqa: E402
from l2q_full import cost_conventions as cc, ir_v1, policy_ir as pi  # noqa: E402
from tcost import tables as tt  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "data/stage5_pricing_golden.json").read_text())
REF = json.loads(fa.REFERENCE.read_text())
SCRATCH_ONLY = ("negation", "negate_impl")           # keys an explicit method adds to a price dict
#: T of fa.REFERENCE's converted policy at n = 2048, delta = 0.01 under `gidney_circuit`: computed with the export branch's
#: engine (E1 7e48afc, DEFAULT_NEGATE = gidney_circuit) on the same document, all three tiers.
ONE_QUBIT_T = {"T": 3633237524122.465, "lean_grid_leaf_T": 10507549117932.008, "literal_lean_T": 15326671086895.008}
SCRATCH_T = 3240663026770.465


def _h(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()


def _strip(d):
    return {k: v for k, v in d.items() if k not in SCRATCH_ONLY}


def _doc(path):
    return json.loads((ROOT / path).read_text())


# ------------------------------------------------------------------ golden: the default is unchanged
@pytest.mark.parametrize("negation", [None, "scratch"])
@pytest.mark.parametrize("rel", [k for k, v in GOLDEN.items() if "price_full_sha" in v])
def test_documents_price_as_before(rel, negation):
    g, doc = GOLDEN[rel], _doc(rel)
    if negation:
        doc = {**doc, "negation": negation}                 # price_document reads the document's own key
    rule = fa.doc_width_rule(doc)
    assert rule == g["rule"]
    t_doc = fa.price_document(doc, 2048, 0.01)
    assert t_doc["T"] == g["T_document"] and _h(t_doc) == g["price_document_sha"]
    full = fa.price_full(fv.policy_from_doc(doc), 2048, 0.01, detail=False, rule=rule, negation=negation)
    assert full["T"] == g["T_full"] and full["qubits"] == g["qubits"]
    assert _h(_strip(full)) == g["price_full_sha"]
    if negation:
        assert full["negation"] == negation and full["negate_impl"] == "legacy"
    else:
        assert "negation" not in full


@pytest.mark.parametrize("rel", [k for k, v in GOLDEN.items() if "price_full_detail_sha" in v])
@pytest.mark.parametrize("negation", [None, "scratch"])
def test_site_policies_price_as_before_with_detail(rel, negation):
    P = tt.checked_policies(tt.policy_docs(_doc(rel)))[0]
    r = fa.price_full(P, 2048, 0.01, detail=True, negation=negation)
    assert r["T"] == GOLDEN[rel]["T"] and _h(_strip(r)) == GOLDEN[rel]["price_full_detail_sha"]


# ------------------------------------------------------------------ oneQubit is priced differently, and correctly
def test_one_qubit_uses_the_gidney_increment_counts_in_every_tier():
    P = fv.policy_from_doc(REF)
    s, o = (fa.price_full(P, 2048, 0.01, detail=False, negation=k) for k in ("scratch", "oneQubit"))
    assert s["T"] == SCRATCH_T
    assert (o["T"], o["lean_grid_leaf_T"], o["literal_lean_T"]) == (
        ONE_QUBIT_T["T"], ONE_QUBIT_T["lean_grid_leaf_T"], ONE_QUBIT_T["literal_lean_T"])
    assert o["negation"] == "oneQubit" and o["negate_impl"] == "gidney_circuit"
    assert o["T"] != s["T"] and o["lean_grid_leaf_T"] != s["lean_grid_leaf_T"] and o["literal_lean_T"] != s["literal_lean_T"]
    assert o["qubits"] == s["qubits"]                        # the static layout is not method-dependent here (a live peak would be)


def test_a_document_prices_with_its_own_key_and_agrees_with_the_converted_policy():
    P = fv.policy_from_doc(REF)
    for neg, want in (("oneQubit", ONE_QUBIT_T["T"]), ("scratch", SCRATCH_T)):
        doc = {**REF, "negation": neg}
        assert fa.price_document(doc, 2048, 0.01)["T"] == want
        priced = fv.price(doc, P, 0.01)                      # raises if the document and the converted policy disagree
        assert priced["single_run_T"] == want and priced["native"]["conversion_agrees"]
        assert priced["negation"] == {"printed": neg, "priced_as": cc.NEGATION_IMPL[neg]}
    plain = fv.price(REF, P, 0.01)                           # no key: the repo default, unchanged
    assert plain["single_run_T"] == SCRATCH_T and plain["negation"] == {"printed": None, "priced_as": "legacy"}


def test_per_negation_counts_are_foshors_numerals():
    """ForShor `negate_charge_by_method` (eb00c5a), as read by P4/E1: X / CNOT / Toffoli, clean ancillas."""
    one, scr = cc.negate_impl("oneQubit"), cc.negate_impl("scratch")
    for w, (x, c, t) in {8: (37, 114, 68), 9: (46, 148, 72), 2048: (10237, 34794, 24548)}.items():
        assert cc.negate_resources(w, one) == {"x": x, "cnot": c, "toffoli": t}
        assert cc.negate_clean_ancilla(w, one) == 1
    assert cc.negate_resources(2048, scr) == {"x": 6140, "cnot": 10233, "toffoli": 4093}
    assert cc.negate_clean_ancilla(2048, scr) == 2049


# ------------------------------------------------------------------ the key: reading and refusing
def test_native_policy_reads_the_key():
    assert pi.load_native(REF).negation is None and pi.load_native(REF).negate_impl == cc.DEFAULT_NEGATE == "legacy"
    for neg, impl in (("oneQubit", "gidney_circuit"), ("scratch", "legacy")):
        nat = pi.load_native({**REF, "negation": neg})
        assert nat.negation == neg and nat.negate_impl == impl


@pytest.mark.parametrize("bad", [None, "", "Scratch", "incrementer", "legacy", 1, True, ["scratch"]])
def test_bad_negation_is_refused(bad):
    with pytest.raises(pi.NativeIRError, match="negation"):             # a document that carries the key must carry a method
        pi.load_native({**REF, "negation": bad})
    if bad is not None:                                                  # (the function's own None means "key absent")
        with pytest.raises(ValueError, match="negation"):
            cc.negate_impl(bad)
        with pytest.raises(ValueError, match="negation"):
            fa.price_full(fv.policy_from_doc(REF), 2048, 0.01, detail=False, negation=bad)


def test_lowgate_resources_follow_the_method():
    leaf = [{"op": "Negate", "r": {"active": list(range(9))}}]
    default, scr, one = (ir_v1.lowgate_resources(leaf, k) for k in (None, "scratch", "oneQubit"))
    assert default == scr and one == {"h": 0, "x": 46, "cnot": 148, "toffoli": 72, "rz": 0} and scr != one
    with pytest.raises(ir_v1.IRv1Error):
        ir_v1.lowgate_resources(leaf, "incrementer")


def test_score_document_follows_the_key():
    from l2q_full import policy_score as ps
    base = ps.score(REF)
    assert "negation" not in base["instance"]
    one, scr = ps.score({**REF, "negation": "oneQubit"}), ps.score({**REF, "negation": "scratch"})
    assert one["instance"]["negation"] == "oneQubit" and scr["instance"]["negation"] == "scratch"
    for tier in ("headline", "cross_check"):
        assert scr["single_run"][tier]["T"] == base["single_run"][tier]["T"]
        assert one["single_run"][tier]["T"] != base["single_run"][tier]["T"]


# ------------------------------------------------------------------ forshor_verify: printed vs submitted
def test_check_negation_matches_and_mismatches():
    ok = lambda printed, requested: fv.check_negation({} if printed is None else {"negation": printed}, requested)
    assert ok("oneQubit", "oneQubit") == {"requested": "oneQubit", "printed": "oneQubit", "priced_as": "gidney_circuit"}
    assert ok("scratch", "scratch")["priced_as"] == "legacy"
    assert ok("scratch", None)["requested"] is None            # a template file: nothing to compare, the printed method rules
    for printed, requested in (("oneQubit", "scratch"), ("scratch", "oneQubit")):
        with pytest.raises(fv.HarnessError, match="negation mismatch") as e:
            ok(printed, requested)
        assert e.value.stage == "python" and repr(printed) in e.value.detail and repr(requested) in e.value.detail


def test_check_negation_old_tree_without_the_key(monkeypatch):
    r = fv.check_negation({}, "oneQubit")
    assert r["printed"] is None and r["priced_as"] == "legacy" and "predates" in r["note"]
    assert fv.check_negation({}, None)["printed"] is None
    with pytest.raises(fv.HarnessError, match="predates"):
        fv.check_negation({}, "scratch")
    monkeypatch.setattr(fv, "REQUIRE_PRINTED_NEGATION", True)
    with pytest.raises(fv.HarnessError, match="no `negation` key"):
        fv.check_negation({}, "oneQubit")


@pytest.mark.parametrize("bad", [None, "", "Scratch", "incrementer", 1, ["oneQubit"]])
def test_check_negation_refuses_a_printed_value_that_is_not_a_method(bad):
    with pytest.raises(fv.HarnessError, match="not one of"):
        fv.check_negation({"negation": bad}, "oneQubit")


def _run_stubbed(tmp_path, monkeypatch, key, printed):
    """`forshor_verify.run(..., from_policy=True, lean_only=True)` with Lean and the trusted tree stubbed out: the printed
    document is a copy of the board's reference document with `negation` = `printed`."""
    src = _doc("evaluation/engine/data/forshor_submitted/S00c49eff1f98/site_policy.json")
    if key is not None:
        src["negation"] = key
    f = tmp_path / "policy.json"
    f.write_text(json.dumps(src))
    doc = {**REF, **({"negation": printed} if printed is not None else {})}
    seen = {}
    monkeypatch.setattr(fv, "stage", lambda forshor, data, work, hb=None: (seen.setdefault("template", data), work.mkdir(parents=True, exist_ok=True)))
    monkeypatch.setattr(fv, "lexical_gate", lambda *a, **k: None)
    monkeypatch.setattr(fv, "exe_args", lambda forshor: [])
    monkeypatch.setattr(fv, "lean_build", lambda work, out, args=None: (doc, {}))
    monkeypatch.setattr(fv, "python_stage", lambda doc, forshor, name: (fv.policy_from_doc(doc), []))
    monkeypatch.setattr(fv, "forshor_provenance", lambda forshor: {})
    monkeypatch.setattr(fv, "reference_document", lambda forshor: ({}, {}))
    r = fv.run(f, forshor=tmp_path / "tree", out_root=tmp_path / "out", lean_only=True, from_policy=True, wait_slot=False)
    return r, seen["template"].decode()


@pytest.mark.parametrize("key,printed", [(None, "oneQubit"), ("oneQubit", "oneQubit"), ("scratch", "scratch"), (None, None)])
def test_run_agrees(tmp_path, monkeypatch, key, printed):
    r, tpl = _run_stubbed(tmp_path, monkeypatch, key, printed)
    assert r["status"] == "lean-ok" and r["negation"]["printed"] == printed
    assert r["negation"]["requested"] == (key or "oneQubit")
    assert ("negation := .scratch" in tpl) == (key == "scratch")


@pytest.mark.parametrize("key,printed", [("scratch", "oneQubit"), (None, "scratch"), ("oneQubit", "scratch"), ("scratch", None)])
def test_run_rejects_a_mismatch(tmp_path, monkeypatch, key, printed):
    with pytest.raises(fv.HarnessError) as e:
        _run_stubbed(tmp_path, monkeypatch, key, printed)
    assert e.value.stage == "python" and ("mismatch" in e.value.detail or "predates" in e.value.detail)


def test_run_refuses_a_bad_key_at_intake(tmp_path, monkeypatch):
    with pytest.raises(fv.HarnessError, match="bad negation") as e:
        _run_stubbed(tmp_path, monkeypatch, "incrementer", "oneQubit")
    assert e.value.stage == "intake"


# ------------------------------------------------------------------ submit.py and seed_policies
def test_submit_refuses_a_bad_key_before_any_work(tmp_path, monkeypatch):
    src = _doc("evaluation/engine/data/forshor_submitted/S00c49eff1f98/site_policy.json")
    f = tmp_path / "p.json"
    f.write_text(json.dumps({**src, "negation": "Scratch"}))
    monkeypatch.setattr(sb.intake, "cost", lambda *a, **k: pytest.fail("priced a policy with a bad negation"))
    r = sb.submit(f)
    assert r["verdict"] == "rejected-negation" and "bad negation" in r["reason"]


def test_submit_labels_the_sto_tiers_for_scratch(tmp_path, monkeypatch):
    src = _doc("evaluation/engine/data/forshor_submitted/S00c49eff1f98/site_policy.json")
    f = tmp_path / "p.json"
    f.write_text(json.dumps({**src, "negation": "scratch"}))
    monkeypatch.setattr(sb.intake, "cost", lambda text, delta=0.01: {"policies": [{"mode": tt.PAIR, "point": {"T": 1.0}}]})
    monkeypatch.setattr(sb.spv, "wait_lean_slot", lambda **k: None)
    monkeypatch.setattr(sb.spv, "check", lambda *a, **k: {"verdict": "rejected", "stage": "stub"})
    r = sb.submit(f)
    n = r["negation"]
    assert n["requested"] == "scratch" and "no negation field" in n["sto"] and "not the method's cost" in n["board"]
    assert r["verdict"] == "sto-rejected"


def test_seed_policies_template_forwards_the_key(tmp_path):
    f = ROOT / "evaluation/engine/data/forshor_submitted/S00c49eff1f98/site_policy.json"
    P = tt.load_policies(f)[0]
    default = sp.template(P)
    assert default == sp.template(P, "oneQubit") == sp.template(P, None)
    scratch = sp.template(P, "scratch")
    assert scratch.replace("  negation := .scratch\n", "") == default and scratch != default
    with pytest.raises(ptt.TemplateError):
        sp.template(P, "bogus")
    assert sp._file_negation(f) == "oneQubit"
    g = tmp_path / "s.json"
    g.write_text(json.dumps({**json.loads(f.read_text()), "negation": "scratch"}))
    assert sp._file_negation(g) == "scratch"


def test_requested_negation_helpers():
    assert ptt.requested_negation({}) == "oneQubit" and ptt.requested_negation({"negation": "oneQubit"}) == "oneQubit"
    assert ptt.requested_negation({"negation": "scratch"}) == "scratch"
    with pytest.raises(ptt.NegationError):
        ptt.requested_negation({"negation": None})
