"""The first REAL verifier-emitted reference-topology QFT design document, and where it stops today.

`data/design_body_submitted_3a2b878.json.gz` was produced by the real executable, not written by hand: ForShor
`3a2b878` (the verifier pin, which emits the per-family QFT design body) built the recorded measured
fixture's source (`readout_submitted_931bbf1`) plus one declaration,

    def submittedFullRunDesign : Shor.FullRunDesign :=
      { data := some ⟨.rossSelinger, .approximate ⟨25, 5, by decide, by decide, by decide⟩⟩ }

and `lake exe forshor_policy_submission --balanced --readout submitted` printed this document. The same
build run the way the COHERENT route runs it (`--balanced`, no `--readout submitted`) exited 2 with empty stdout:
`PolicyMain`'s guard admits a non-reference full-run selection only beside a saved semiclassical readout. So every
design-bearing document the verifier can emit is a MEASURED document; there is no coherent design document to publish.

Publishing this document needs the measured readout and the QFT design to COMPOSE: the source gate admits this declaration beside the semiclassical readout, the
measured route's document gate admits the bound body, and `readout_qft_overlay` prices the two together. The
operational verifier pin is a commit that can name a body (`80d7b6b`, the previous pin, had the closed enum and
could not). This file pins: every gate admits the
document on the measured route and refuses it everywhere else, the refusals that stay refusals, and the measured
request it produces -- `fullRun.readoutQft` on the full 1e-2 panel, the archive's `design` key, and the other seven
panels byte-identical to the readout-only request.
"""
import gzip
import hashlib
import json
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL / "starter" / "pack" / "harness"))
sys.path.insert(0, str(EVAL.parent / "scripts" / "ci"))
sys.path.insert(0, str(Path(__file__).parent))
import archive_policy  # noqa: E402
import intake_v2  # noqa: E402
import policy_source  # noqa: E402
from export_v2 import canon, engine, full_run_design, mapping, request  # noqa: E402
from policy_options import (FULL_RUN_ROUTE, check_coherent_export, check_measured_full_run_export,  # noqa: E402
                            check_no_full_run_export)

DATA = Path(__file__).parent / "data"
RECORD = json.loads(gzip.decompress((DATA / "design_body_submitted_3a2b878.json.gz").read_bytes()))
MEASURED = json.loads(gzip.decompress((DATA / "readout_submitted_931bbf1.json.gz").read_bytes()))
DESIGN = {"data": {"backend": "ross_selinger", "target": "approximate", "max_error": "25e-5"}}


def test_the_record_is_the_verifiers_measured_export_plus_one_selection():
    """The emitted document is the recorded measured export byte for byte, plus `submitted_full_run` and nothing else,
    so the design-body emitter moved no other byte of a measured document. And the coherent invocation of the same
    build refused: a design-bearing document is measured by construction."""
    document = RECORD["document"]
    # The digest is of the executable's stdout, which this re-serialisation reproduces byte for byte.
    stdout = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode()
    assert hashlib.sha256(stdout).hexdigest() == RECORD["documentSha256"]
    assert RECORD["verifier"] == "3a2b878c7e6194e182e10ad9bc06dec90e027a06"
    assert {k: v for k, v in document.items() if k != full_run_design.KEY} == MEASURED["document"]
    assert document["schema"] == "forshor.policy-submission-semiclassical/v1"
    assert document["submitted_readout"] == "semiclassical"
    assert RECORD["source"].count("submittedFullRunDesign") == 1
    coherent = RECORD["coherentInvocation"]
    assert coherent["returncode"] == 2 and coherent["stdout"] == ""
    assert coherent["stderr"] == "full-run synthesis requires saved semiclassical readout and --balanced --readout submitted"


def test_the_selection_converts_to_a_reference_topology_design():
    selection = full_run_design.selection_of(RECORD["document"])
    assert full_run_design.topology_of(selection) == full_run_design.MULTIPLIER_REFERENCE
    assert full_run_design.design_of(selection) == DESIGN


READOUT_DECLARATION = "def submittedDesign : Shor.SubmissionDesign where\n  readout := .semiclassical\n"


def test_every_gate_admits_it_on_the_measured_route_and_only_there():
    """Each gate applies the rule that admits a body its own source declares, beside the semiclassical readout, on the
    measured route -- and the coherent route keeps refusing it. In order: the CI source gate, intake's own source gate
    (the same function), the measured route's document gate, the source binding, the conversion under the readout.
    Production change that would fail this: admitting the body without the readout declaration, admitting it on the
    coherent route, or converting a body its source does not declare."""
    source, document = RECORD["source"], RECORD["document"]
    policy_source.validate(source)                                      # the CI source gate
    assert intake_v2.check_legacy_full_run_source(source) is True       # intake's source gate: a design is declared
    check_measured_full_run_export(document)                            # the measured route's document gate
    assert intake_v2.check_full_run_binding(source, document) is True   # bound to the source that declares it
    selection, design = intake_v2.compiled_full_run(document, measured=True)
    assert design == DESIGN
    # The coherent route is unchanged: its gates still refuse the key on presence.
    for gate in (check_no_full_run_export, check_coherent_export):
        with pytest.raises(ValueError, match=FULL_RUN_ROUTE):
            gate(document)


def test_a_design_without_the_readout_declaration_is_refused_by_name():
    """The declaration is admitted ONLY beside the semiclassical readout declaration. Positive control
    above (the same source with it). Production change that would fail this: admitting a design on its own."""
    assert READOUT_DECLARATION in RECORD["source"]
    alone = RECORD["source"].replace(READOUT_DECLARATION, "")
    for gate in (policy_source.validate, intake_v2.check_legacy_full_run_source):
        with pytest.raises(ValueError, match=policy_source.FULL_RUN_REFUSAL) as refused:
            gate(alone)
        assert "selects the non-default full-run design `{ data := some" in str(refused.value), \
            "the multi-line declaration is named as the design it is, not as a mis-written default"
        assert "semiclassical readout" in str(refused.value)


@pytest.mark.parametrize("value,named", [
    ("{ exponent := some ⟨.rossSelinger, .exact⟩ }", "`exponent` QFT family"),
    ("{ multiplier := .quotientStream, data := some ⟨.rossSelinger, .exact⟩ }", "multiplier topology"),
    (".quotientExact", "names a profile"),
    ("{ data := some ⟨.rossSelinger, .exact⟩, dataRotations := some .fallback }", "per-family rotation backend"),
    ("{ data := some ⟨.rossSelinger, .exact⟩, step2 := some .fallback }", "phase-product step backend"),
])
def test_a_declaration_outside_the_reference_qft_axis_is_refused_by_name_before_any_build(value, named):
    """Kept refusals, at the source: a topology, a named profile, a rotation or phase-step backend, or the exponent
    family under the readout. Each names what it selects. Production change that would fail this: admitting any of
    them to the build, where only the verifier or the converter would refuse it."""
    body = "{ data := some ⟨.rossSelinger, .approximate ⟨25, 5, by decide, by decide, by decide⟩⟩ }"
    assert body in RECORD["source"]
    with pytest.raises(ValueError, match=policy_source.FULL_RUN_REFUSAL) as refused:
        policy_source.validate(RECORD["source"].replace(body, value))
    assert named in str(refused.value)


@pytest.mark.parametrize("body,named", [
    ({"exponent": {"backend": "ross_selinger", "target": "exact"}}, "'exponent'"),
    ({"data": {"backend": "ross_selinger", "target": "exact"}, "exponent": None}, "'exponent'"),
    ({"data": {"backend": "ross_selinger", "target": "exact", "rotation_backend": "fallback"}}, "rotation_backend"),
])
def test_a_compiled_body_the_readout_cannot_honour_is_refused_by_name(body, named):
    """The same refusals on the compiled side, on the RAW body: `{"exponent": exact}` would otherwise collapse to "no
    design" in `full_run_design.design_of` and the key would be dropped. Production change that would fail this:
    converting before checking the raw body under the readout."""
    document = {**RECORD["document"], full_run_design.KEY: {
        "schema": full_run_design.SELECTION_SCHEMA, "design": {"schema": full_run_design.DESIGN_SCHEMA, "qft": body}}}
    with pytest.raises(intake_v2.Refused, match=named):
        intake_v2.compiled_full_run(document, measured=True)


def test_a_document_whose_selection_its_source_does_not_declare_is_refused():
    """The binding, both ways. Production change that would fail this: trusting a document's selection without its
    source, which is the inconsistent-receipt case."""
    with pytest.raises(intake_v2.Refused, match=intake_v2.FULL_RUN_BINDING_REFUSAL):
        intake_v2.check_full_run_binding(MEASURED["source"], RECORD["document"])
    with pytest.raises(intake_v2.Refused, match=intake_v2.FULL_RUN_BINDING_REFUSAL):
        intake_v2.check_full_run_binding(RECORD["source"], MEASURED["document"])
    assert intake_v2.check_full_run_binding(MEASURED["source"], MEASURED["document"]) is False


def test_the_archive_records_the_design_as_an_index_key(tmp_path):
    """The real writer, on a real checkout: the design is an index key, and never a number."""
    (tmp_path / archive_policy.INDEX_PATH).parent.mkdir(parents=True)
    policy_json = intake_v2.policy_from_document(engine.load(), RECORD["document"]).to_json()
    source = RECORD["source"]
    v = {"digest": canon.policy_digest(policy_json), "mode": policy_json["mode"], "board": "pp", "policy": policy_json,
         "createdAt": "2026-11-24T00:00:00Z", "leanSource": source,
         "leanSha256": hashlib.sha256(source.encode()).hexdigest(), "printed": RECORD["document"]}
    design = full_run_design.design_of(full_run_design.selection_of(RECORD["document"]))
    assert archive_policy.write_policy(tmp_path, v, login="tester", pull=7, head_sha="a" * 40, run_id="1",
                                       source=source, readout="semiclassical", design=design)
    entry = archive_policy.read_index(tmp_path)["policies"][0]
    assert entry["design"] == DESIGN and entry["readout"] == "semiclassical" and entry["boards"] == ["pp", "full"]
    assert not [k for k in archive_policy.BARE_PRICE_KEYS if k in entry]


@pytest.mark.slow
def test_the_measured_request_prices_the_design_with_the_readout_and_moves_no_other_panel():
    """THE ROUTE, end to end in-process: the real document and source through `build_measured_request` --
    the real readout binding, the real composed pricer. The full 1e-2 panel carries `fullRun.readoutQft` beside the
    readout-only `readout` block and no `qft`; its row ranks the composed T and qubits; and the other seven panels are
    byte-identical to the readout-only request for the same policy. Production change that would fail this: pricing
    the design on the coherent census, at another delta, or letting it touch any other panel."""
    import test_design_price_plumbing as dpp
    from export_v2 import readout_overlay

    policy_json = intake_v2.policy_from_document(engine.load(), RECORD["document"]).to_json()
    args = {k: v for k, v in dpp._request_args(policy_json).items() if k != "forshor"}
    composed = request.build_measured_request(policy_json, compiled=RECORD["document"],
                                              verifier_pin=readout_overlay.VERIFIER_PIN,
                                              lean_source=RECORD["source"].encode(), **args)
    alone = request.build_measured_request(policy_json, compiled=MEASURED["document"],
                                           verifier_pin=readout_overlay.VERIFIER_PIN,
                                           lean_source=MEASURED["source"].encode(), **args)
    carrying = [(p["board"], p["delta"]) for p in composed["priced"] if "readoutQft" in (p["panel"].get("fullRun") or {})]
    assert carrying == [("full", mapping.DESIGN_DELTA)]
    for mine, theirs in zip(composed["priced"], alone["priced"]):
        if (mine["board"], mine["delta"]) == ("full", mapping.DESIGN_DELTA):
            run = mine["panel"]["fullRun"]
            assert "qft" not in run and run["readout"] == theirs["panel"]["fullRun"]["readout"]
            assert (mine["row"]["T"], mine["row"]["qubits"]) == (run["readoutQft"]["T"], run["readoutQft"]["qubits"])
            assert run["readoutQft"]["substituted"] == ["data"] and run["readoutQft"]["T"] < run["readout"]["T"]
        else:
            # The verification differs only in the readout evidence's digest of the compiled document.
            strip = lambda item: {**item, "panel": {**item["panel"], "verification": None}}
            assert canon.sha256_hex(strip(mine)) == canon.sha256_hex(strip(theirs)), (mine["board"], mine["delta"])
    assert composed["submission"]["verification"]["readout"]["irSha256"] == canon.sha256_hex(RECORD["document"])


@pytest.mark.slow
def test_downstream_the_design_is_priced_at_the_design_delta_on_the_full_board_only():
    """With the route gates stood aside, the request builder prices this document's design exactly where the surface
    is qualified: `fullRun.qft` on the full board at `DESIGN_DELTA`, ranked on the projection's T beside the run's
    own qubits, and absent from the other seven panels. Priced on the coherent census, with the readout left off: this
    pins the coherent `qft` plumbing `_build_request` still has. The route that publishes this document is the measured
    one above, where the design is priced WITH the readout."""
    import test_design_price_plumbing as dpp

    policy_json = intake_v2.policy_from_document(engine.load(), RECORD["document"]).to_json()
    built = request._build_request(policy_json, full_run=full_run_design.selection_of(RECORD["document"]),
                                   **dpp._request_args(policy_json))
    carrying = [(p["board"], p["delta"]) for p in built["priced"] if "qft" in (p["panel"].get("fullRun") or {})]
    assert carrying == [("full", mapping.DESIGN_DELTA)]
    priced = next(p for p in built["priced"] if (p["board"], p["delta"]) == ("full", mapping.DESIGN_DELTA))
    full, qft = priced["panel"]["fullRun"], priced["panel"]["fullRun"]["qft"]
    assert qft["substituted"] == ["data"] and qft["catalysed"] == [] and not qft["identity"]
    assert qft["T"] < full["T"], "banding the data QFT lowers T"
    assert priced["row"]["T"] == qft["T"] and priced["row"]["qubits"] == full["qubits"]
