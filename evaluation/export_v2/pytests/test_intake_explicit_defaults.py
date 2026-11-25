"""Ross-Selinger, the reference full run and coherent readout are ALWAYS the defaults, and an explicit default
token is identical to omission everywhere, including hosted intake. Only the three default declarations; every other selection stays refused.

Each test names, in a comment, the production change that would make it fail. Lean and the mirror are stubbed: the compiled document is the
same for the two spellings, exactly as Lean's resolvers return the default for an absent declaration and `attest` leaves the document
unchanged for the default (PolicyMain.lean, PrimitiveSynthesis.lean)."""
import json
from pathlib import Path
import sys

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL.parent))
import archive_policy as ap  # noqa: E402
import intake_v2  # noqa: E402
from export_v2 import engine  # noqa: E402
from export_v2.canon import policy_digest  # noqa: E402

DATA = Path(__file__).parent / "data"
DROPIN = EVAL / "starter" / "pack" / "examples" / "forshor_dropin.policy.json"     # a real PhaseProduct document ForShor printed
PTP_PRINTED = DATA / "ptp_printed_k4.json"
POLICY = "import ForShor.ShorVerification.Framework.Policy\n"
PRIM = "def submittedPrimitive : Shor.PrimitiveSynthesis := .rossSelinger"
FULL = "def submittedFullRunDesign : Shor.FullRunDesign := .reference"
DESIGN = "def submittedDesign : Shor.SubmissionDesign := { readout := .coherent }"
FULL_IMPORT = "import ForShor.ShorVerification.Framework.FullRunDesign\n"


def lean(*declarations, namespace="PolicySubmission", imports=""):
    return f"{POLICY}{imports}namespace {namespace}\n" + "".join(d + "\n" for d in declarations) + f"def points := #[1, 2, 3]\nend {namespace}\n"


def with_decoy(declaration, imports=""):
    """The board's file plus a second, unrelated namespace holding the declaration: Lean would never read it."""
    return lean(imports=imports) + f"namespace Decoy\n{declaration}\ndef other := 1\nend Decoy\n"


OMITTED = lean()
EXPLICIT = lean(DESIGN, PRIM, FULL, imports=FULL_IMPORT)
PTP_OMITTED = lean(namespace="PTPPolicySubmission")
PTP_EXPLICIT = lean(DESIGN, PRIM, namespace="PTPPolicySubmission")


def build_stub(monkeypatch, document):
    seen = []
    monkeypatch.setattr(intake_v2, "forshor_build", lambda tree, text, workdir, timeout=7200, kind="pp", **kw: seen.append((text, kind)) or document)
    return seen


# ---- the gates

@pytest.mark.parametrize("text", [EXPLICIT, lean(DESIGN), lean(PRIM), lean(FULL, imports=FULL_IMPORT), PTP_EXPLICIT,
                                  lean("def submittedDesign : Shor.SubmissionDesign where\n  readout := .coherent"),
                                  lean(PRIM.replace("Shor.", "_root_.Shor.").replace(":= .rossSelinger", ":= Shor.PrimitiveSynthesis.rossSelinger"))])
def test_each_explicit_default_passes_every_hosted_source_gate(text):
    # Fails if refuse_unqualified_design / refuse_unqualified_primitive / check_legacy_full_run_source still search the raw token.
    intake_v2.refuse_unqualified_design(text)
    intake_v2.refuse_unqualified_primitive(text)
    intake_v2.check_legacy_full_run_source(text)


NON_DEFAULTS = [   # (source, gate that must refuse it, the message it always gave)
    (lean("def submittedDesign : Shor.SubmissionDesign := { readout := .semiclassical }"), "design", "submittedDesign is not accepted as written"),
    (lean("def submittedDesign : Shor.SubmissionDesign where\n  readout := .semiclassical"), "design", "submittedDesign is not accepted as written"),
    (lean(DESIGN, DESIGN), "design", "submittedDesign is not accepted as written"),
    (lean(DESIGN, "def other := submittedDesign"), "design", "submittedDesign is not accepted as written"),
    (with_decoy(DESIGN), "design", "submittedDesign is not accepted as written"),
    (lean("def submittedPrimitive : Shor.PrimitiveSynthesis := .catalyticB2"), "primitive", "submittedPrimitive is not accepted for a scored submission"),
    (lean("def submittedPrimitive : Shor.PrimitiveSynthesis := .adaptiveB3"), "primitive", "submittedPrimitive is not accepted for a scored submission"),
    (lean("def submittedPrimitive : Shor.PrimitiveSynthesis := .rossSelingerX"), "primitive", "submittedPrimitive is not accepted for a scored submission"),
    (lean("def submittedPrimitive := Shor.PrimitiveSynthesis.rossSelinger |> id"), "primitive", "submittedPrimitive is not accepted for a scored submission"),
    (lean(PRIM, PRIM), "primitive", "submittedPrimitive is not accepted for a scored submission"),
    (with_decoy(PRIM), "primitive", "submittedPrimitive is not accepted for a scored submission"),
    (lean("private " + PRIM), "primitive", "submittedPrimitive is not accepted for a scored submission"),
    (lean(PRIM, "  |> fun _ => Shor.PrimitiveSynthesis.adaptiveB3"), "primitive", "submittedPrimitive is not accepted for a scored submission"),
    (lean(FULL.replace(".reference", ".quotientExact"), imports=FULL_IMPORT), "fullrun", "submittedFullRunDesign is not accepted as written"),
    (lean(FULL, FULL, imports=FULL_IMPORT), "fullrun", "submittedFullRunDesign is not accepted as written"),
    (with_decoy(FULL, imports=FULL_IMPORT), "fullrun", "submittedFullRunDesign is not accepted as written"),
    # PTPPolicyCheck refuses a full-run declaration on the PhaseTripleProduct board, `.reference` included
    (lean(FULL, namespace="PTPPolicySubmission", imports=FULL_IMPORT), "fullrun", "submittedFullRunDesign is not accepted as written"),
]


@pytest.mark.parametrize("text,gate,message", NON_DEFAULTS)
def test_every_non_default_selection_is_still_refused_with_the_same_message(text, gate, message, tmp_path, monkeypatch):
    # Fails if the default recogniser is wider than the three canonical declarations (value, type, namespace, board, count, continuation).
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: pytest.fail("a refused selection must never reach the compiler"))
    gates = {"design": intake_v2.refuse_unqualified_design, "primitive": intake_v2.refuse_unqualified_primitive,
             "fullrun": intake_v2.check_legacy_full_run_source}
    with pytest.raises((intake_v2.Refused, ValueError), match=message):
        gates[gate](text)
    with pytest.raises(intake_v2.Refused, match=message):      # and the hosted entry point as a whole, in its own order of checks
        intake_v2.policy_from_lean(None, text, tmp_path, tmp_path)


def test_a_mixed_file_is_refused_for_its_non_default_part_only():
    # Fails if one accepted default declaration disables the check of the others.
    text = lean(PRIM, "def submittedDesign : Shor.SubmissionDesign := { readout := .semiclassical }")
    intake_v2.refuse_unqualified_primitive(text)
    with pytest.raises(intake_v2.Refused, match="submittedDesign is not accepted"):
        intake_v2.refuse_unqualified_design(text)


def test_comments_and_omission_are_unchanged():
    for text in (OMITTED, "-- submittedPrimitive submittedDesign submittedFullRunDesign\n/- def submittedDesign := x -/\n" + OMITTED):
        intake_v2.refuse_unqualified_design(text)
        intake_v2.refuse_unqualified_primitive(text)
        intake_v2.check_legacy_full_run_source(text)


# ---- identity of the design, the export and the verdict

def test_explicit_and_omitting_sources_build_the_same_design(monkeypatch, tmp_path):
    # Fails if policy_from_lean refuses an explicit default, or treats the two spellings differently after the build.
    doc = json.loads(DROPIN.read_text())
    seen = build_stub(monkeypatch, doc)
    eng = engine.load()
    results = [intake_v2.policy_from_lean(eng, text, tmp_path / "forshor", tmp_path / "w") for text in (OMITTED, EXPLICIT)]
    assert [s[0] for s in seen] == [OMITTED, EXPLICIT], "ForShor builds exactly the submitted bytes, in both spellings"
    assert [s[1] for s in seen] == ["pp", "pp"]
    assert OMITTED != EXPLICIT
    assert results[0] == results[1] and results[0][1] is doc
    assert policy_digest(results[0][0]) == policy_digest(results[1][0])


def test_explicit_and_omitting_ptp_sources_build_the_same_design(monkeypatch, tmp_path):
    # Fails if the PhaseTripleProduct board's explicit primitive/design default is refused.
    doc = json.loads(PTP_PRINTED.read_text())
    seen = build_stub(monkeypatch, doc)
    eng = engine.load()
    results = [intake_v2.policy_from_lean(eng, text, tmp_path / "forshor", tmp_path / "w") for text in (PTP_OMITTED, PTP_EXPLICIT)]
    assert [s[1] for s in seen] == ["ptp", "ptp"]
    assert results[0] == results[1]


def test_the_publisher_recheck_accepts_the_explicit_default_and_refuses_the_rest(tmp_path):
    # `publish_verification` re-runs the source gates on the verification's leanSource; an accepted source then fails only on the schema.
    for text in (OMITTED, EXPLICIT, PTP_EXPLICIT):
        with pytest.raises(intake_v2.Refused, match="not a policy verification"):
            intake_v2.publish_verification({"leanSource": text}, login="tester", pull=1, url="http://unused", token="unused", workdir=tmp_path)
    for text, gate, message in NON_DEFAULTS:                   # (the publisher re-checks the full-run and design gates, not the primitive one)
        if gate != "primitive":
            with pytest.raises(intake_v2.Refused, match=message):
                intake_v2.publish_verification({"leanSource": text}, login="tester", pull=1, url="http://unused", token="unused", workdir=tmp_path)


@pytest.mark.parametrize("first,second", [(OMITTED, EXPLICIT), (EXPLICIT, OMITTED)])
def test_duplicates_are_detected_in_both_arrival_orders(first, second, tmp_path, monkeypatch):
    # Fails if the two spellings produced different policy digests (the archive's duplicate key) or the rule keyed on the source bytes.
    build_stub(monkeypatch, json.loads(DROPIN.read_text()))
    eng = engine.load()
    verified = []
    for number, text in enumerate((first, second)):
        policy_json, _ = intake_v2.policy_from_lean(eng, text, tmp_path / "forshor", tmp_path / "w")
        verified.append({"digest": policy_digest(policy_json), "mode": policy_json["mode"], "board": "pp", "policy": policy_json,
                         "createdAt": f"2026-11-24T10:0{number}:00Z", "leanSource": text, "leanSha256": ap.sha256(text)})
    assert verified[0]["digest"] == verified[1]["digest"] and first != second
    checkout = tmp_path / "archive"
    (checkout / ap.INDEX_PATH).parent.mkdir(parents=True)
    heads = ["a" * 40, "b" * 40]
    ap.write_policy(checkout, verified[0], login="tester", pull=1, head_sha=heads[0], run_id="1", source=first)
    with pytest.raises(ap.DuplicatePolicy) as refused:          # the same participant repeating the design under the other spelling
        ap.write_policy(checkout, verified[1], login="tester", pull=2, head_sha=heads[1], run_id="2", source=second)
    assert refused.value.duplicate_of["policyId"] == heads[0] and refused.value.duplicate_of["sameParticipant"] is True
    repeats = []                                                # another participant is archived and identified (the rule table's decision)
    assert ap.write_policy(checkout, verified[1], login="other", pull=3, head_sha=heads[1], run_id="3", source=second, duplicates=repeats)
    assert [(r["policyId"], r["sameParticipant"]) for r in repeats] == [(heads[0], False)]


def test_the_ptp_digest_is_independent_of_the_spelling_too(monkeypatch, tmp_path):
    build_stub(monkeypatch, json.loads(PTP_PRINTED.read_text()))
    eng = engine.load()
    digests = {policy_digest(intake_v2.policy_from_lean(eng, t, tmp_path / "forshor", tmp_path / "w")[0]) for t in (PTP_OMITTED, PTP_EXPLICIT)}
    assert len(digests) == 1
