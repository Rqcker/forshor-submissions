"""The verifier's compiled full-run selection -> the engine's `design` argument.

The compiled document names the selection in ONE top-level key, `submitted_full_run`, whose value is
`{"schema": "forshor.full-run-selection/v1", "profile": "<label>"}`. Evidence, from the recorded fixtures:

* `readiness/pre-push/overnight-native-compiler-matrix/<profile>/checked-export.json` carries exactly that
  object for each of the seven native profiles (and `submitted_readout: "semiclassical"` beside it);
* the same matrix's `explicit-reference/checked-export.json` carries NO such key, because the verifier emits
  the object only when the selection is not `.reference`
  (`QFT-SURFACE-MAPPING.md` section 1.2, citing `PolicyMain.lean:463-465`);
* `readiness/pre-push/audit_native_fullrun.py:131` and `replay_native_quotient_step2.py:96` assert that pair.

`reference` therefore means NO design at all (research side: `ForShorGate5.compiledSelection = none`), not an
all-defaults record -- `QFT-SURFACE-MAPPING.md` section 1.2, and `design_matrix.py:157` ("the empty selection
is not a design"). Both the absent key and an explicit `reference` convert to `design = None`, which is the
engine's untouched default path.

The per-profile designs below are a transcription of the existing research<->production bridge,
`readiness/pre-push/audit_native_fullrun.py:20-46` (its `EXACT` / `CAT` / `BAND` constants and `CASES` table),
which `QFT-SURFACE-MAPPING.md` section 1.0 records as the way that mapping was established.

What this pipeline can convert. The engine contract's `design` is a mapping over the QFT families
(`data`, `work`, `scratch`, `exponent`) only. Four of the seven profiles select a rotation backend or a
phase-product step backend, which the contract has no field for; they are REFUSED by name.

THE REMAINING THREE ARE ALSO NOT PRICEABLE TODAY, and this is the headline for the release. All seven
non-reference profiles select a MULTIPLIER TOPOLOGY (`modmul`: quotientStream or splitCompare) --
`audit_native_fullrun.py:25-30` emits it beside `qft` for every bundle and QFT-SURFACE-MAPPING.md section
1.1 lists it for all seven. The topology drives the T cost (`policy_design.py:139-142`) and the qubit base
(`:160-162`). `qft_overlay` prices the QFT BAND ONLY: its families are the four QFT registers, `project`
has no topology parameter, its source names no multiplier at all, and `parse_design` refuses a `modmul`
key outright. So a profile's topology cannot be honoured, and converting it to a bare family mapping would
price a quotientStream run on REFERENCE-topology numbers -- reporting the reference price as if the option
had been honoured. That is refused by name instead. Only `reference` converts, to None.

An arbitrary per-family QFT design on the REFERENCE topology does price, and that is this module's second
path below. A selection that is reported is never priced as the default.

THE SECOND PATH: an EXPLICIT per-family design
--------------------------------------------------------------------------------------------------
The profile path above resolves a CLOSED ENUM of 8 labels, so it can only name the combinations that have
already been qualified -- 3 of them priceable. That narrowness is the enum's, not the pricing's:
`qft_overlay.parse_design` already accepts an arbitrary mapping with independent `data` / `work` /
`scratch` / `exponent` keys and prices any combination of them. So a selection may instead carry an
explicit design BODY, and then an arbitrary combination converts.

The wire shape is not invented here. It is `ForShorGate5.Design.json`
(`postprocess/synthesis/qualification/Gate5Design.lean:83-91`), the only serializer that exists for this
record, inside the envelope the recorded compiled exports already use:

    "submitted_full_run": {"schema": "forshor.full-run-selection/v1",
                           "design": {"schema": "forshor.design-local/v1",
                                      "modmul": "reference",
                                      "qft": {"data": {"backend": "ross_selinger",
                                                       "target": "approximate", "max_error": "1e-6"},
                                              "work": {"backend": "ross_selinger", "target": "exact"}}}}

`profile` and `design` are alternatives: exactly one. A family's value is exactly what
`QftChoice.json` (`Gate5Design.lean:39-44`) emits -- `{"backend", "target": "exact"}` or
`{"backend", "target": "approximate", "max_error": "<units>e-<scale>"}` -- which is already
`qft_overlay.parse_design`'s contract, so this module does not restate the value rules: it performs the
SHAPE translation (envelope and `Design.json` nesting -> the flat per-family mapping) and DELEGATES value
validation to the overlay, eagerly, so a design that cannot be honoured is refused at conversion time and
no board is ever priced for it. One validator in the repo, not two.

`readout` is deliberately not a body key: `Design.json` takes it as a parameter (`:83`) because the
research document has no separate key, whereas production carries `submitted_readout` beside
`submitted_full_run` and `QFT-SURFACE-MAPPING.md` section 4.2's `FullRunDesign` has no readout field.

EXACT MEANS UNTOUCHED. `target: "exact"` is "leave this family as the engine priced it" (`k_max = None`,
no substitution), never "substitute the exact ladder" -- lane WM measured that substitution at 14.8x T
(`qft_overlay.py:34-40`). A design in which no family asks for an `approximate` target therefore asks for
nothing at all and converts to None, the engine's untouched default, exactly as `reference` does: section
1.2's rule that the empty selection is not a design. The collapse is applied on THIS path only; the
profile path's 8 labels are already-qualified fixtures whose conversion is recorded byte-for-byte, and
`quotient-exact` keeps resolving to its mapping. Both price the same T (the overlay returns
`identity: True` for an all-exact mapping), so that asymmetry is in reporting, not in price.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

KEY = "submitted_full_run"
SELECTION_SCHEMA = "forshor.full-run-selection/v1"
REFERENCE = "reference"

# --- the explicit-design path. Every name below is transcribed from `Gate5Design.lean`.
PROFILE_KEY = "profile"                     # the closed-enum path
DESIGN_KEY = "design"                       # the orthogonal path
DESIGN_SCHEMA = "forshor.design-local/v1"   # Design.json:86
QFT_KEY = "qft"                             # Design.json:89 -- the per-family sub-object
MULTIPLIER_KEY = "modmul"                   # Design.json:88
MULTIPLIER_REFERENCE = REFERENCE
MULTIPLIERS = ("reference", "split_compare", "quotient_stream")   # Multiplier.label:11-14
PHASE_KEY = "phase_product"                 # Design.json:91
PHASE_STEPS = ("step1", "step2", "step4", "step5")                # Design.json:84
ROTATION_FIELD = "rotation_backend"         # familyJson:63, the `<family>Rotations` axis
BODY_KEYS = ("schema", MULTIPLIER_KEY, QFT_KEY, PHASE_KEY)

#: Every way the explicit path can refuse. The tests assert this set is exactly the cases they cover, so a
#: new refusal cannot be added without being named here and a refusal cannot be dropped silently.
REFUSAL_CLASSES = (
    "unknown-backend",      # a backend qft_overlay.BACKENDS does not name
    "unknown-target",       # a target qft_overlay.TARGETS does not name
    "catalytic",            # a second axis: its own budget share and a catalyst register (WM)
    "max-error",            # not '<units>e-<scale>', or not a precision in (0, 1), or missing/unexpected
    "phase-product",        # step1/step2/step4/step5: certified word banks, out of scope
    "rotation-backend",     # the `<family>Rotations` axis; the contract has no field for it
    "unknown-family",       # a key that is not data/work/scratch/exponent
    "multiplier",           # anything but `reference`: the overlay prices the frozen reference census
    "unknown-body-key",     # including `readout`, which production carries as its own top-level key
    "body-schema",          # a design body that is not forshor.design-local/v1
    "malformed-shape",      # a non-mapping selection, body or qft object
    "profile-and-design",   # both paths named at once, or neither
)

# audit_native_fullrun.py:20-22. The ceiling 25e-5 = 1/4000 is the error ceiling of every approximate case
# (QFT-SURFACE-MAPPING.md section 1, Gate5Design.lean:25-30).
RS_EXACT = {"backend": "ross_selinger", "target": "exact"}
CATALYTIC = {"backend": "catalytic", "target": "approximate", "max_error": "25e-5"}
BANDED = {"backend": "ross_selinger", "target": "approximate", "max_error": "25e-5"}

# The quotientStream multiplier removes the `scratch` family and semiclassical readout replaces `exponent`
# (QFT-SURFACE-MAPPING.md section 1, citing joint_policy.py:44,46), so these designs name `data` and `work` only.
QFT_ONLY: Dict[str, Dict[str, Any]] = {
    "quotient-exact": {"data": RS_EXACT, "work": RS_EXACT},
    "quotient-catalytic": {"data": CATALYTIC, "work": CATALYTIC},
    "quotient-banded-data-catalytic-work": {"data": BANDED, "work": CATALYTIC},
}

# THE MULTIPLIER TOPOLOGY each profile selects, which must NOT be dropped. `audit_native_fullrun.py:25-30`
# builds every bundle through `quotient(...)` / `split(...)`, both of which emit a `modmul` key beside the
# `qft` object, and QFT-SURFACE-MAPPING.md section 1.1 gives the same `multiplier :=` field for all seven.
# The topology is not cosmetic: it drives both the T cost and the qubit base (`policy_design.py:139-142`
# and `:160-162`), so a conversion that drops it prices a quotientStream or splitCompare run on
# REFERENCE-topology numbers -- the silent default the release forbids. `reference` names no topology at
# all (section 1.2: it is `compiledSelection = none`, not an all-defaults record).
PROFILE_TOPOLOGY: Dict[str, Optional[str]] = {
    REFERENCE: None,
    "quotient-exact": "quotient_stream",
    "quotient-catalytic": "quotient_stream",
    "quotient-banded-data-catalytic-work": "quotient_stream",
    "quotient-fallback-data-catalytic-work": "quotient_stream",
    "quotient-step2-fallback": "quotient_stream",
    "split-step4-kicks": "split_compare",
    "split-step2-fallback-step4-kicks": "split_compare",
}

# What each remaining profile selects beyond the QFT families, named in its refusal.
BEYOND_QFT: Dict[str, str] = {
    "quotient-fallback-data-catalytic-work":
        "a fallback rotation backend on the data QFT (dataRotations := fallback)",
    "quotient-step2-fallback":
        "a Step 2 phase-product backend (step2 := fallback)",
    "split-step4-kicks":
        "the splitCompare multiplier with a Step 4 phase-product backend (step4 := selectiveKick)",
    "split-step2-fallback-step4-kicks":
        "the splitCompare multiplier with a Step 2 phase-product backend (step2 := fallback) and a Step 4 "
        "phase-product backend (step4 := selectiveKick)",
}

PROFILES = (REFERENCE, *QFT_ONLY, *BEYOND_QFT)

#: Each profile's design in the shape `audit_native_fullrun.py` itself builds -- `modmul` BESIDE `qft`,
#: which is also `Gate5Design.Design.json`'s shape and the explicit path's wire shape. Profiles are
#: converted through exactly the same reader as an explicit design, so the topology cannot be dropped on
#: one path and honoured on the other, and so a profile becomes priceable the moment the overlay can
#: honour its topology -- without another edit here.
PROFILE_DESIGN: Dict[str, Dict[str, Any]] = {
    profile: {MULTIPLIER_KEY: PROFILE_TOPOLOGY[profile], QFT_KEY: families}
    for profile, families in QFT_ONLY.items()
}


def topology_of(selection: Optional[Any]) -> Optional[str]:
    """The multiplier topology one compiled selection names, or None for no topology (the `reference`
    default). Exists so the topology is observable rather than silently dropped during conversion."""
    if selection is None:
        return None
    if not isinstance(selection, dict):
        raise ValueError(f"full-run selection is not a {SELECTION_SCHEMA} object: {selection!r}")
    if PROFILE_KEY in selection:
        profile = selection[PROFILE_KEY]
        if profile not in PROFILE_TOPOLOGY:
            raise ValueError(f"unrecognised full-run selection {profile!r}")
        return PROFILE_TOPOLOGY[profile]
    body = selection.get(DESIGN_KEY)
    if not _is_mapping(body):
        raise ValueError(f"full-run selection {DESIGN_KEY!r} is not a {DESIGN_SCHEMA} object: {body!r}")
    return body.get(MULTIPLIER_KEY) or MULTIPLIER_REFERENCE


def selection_of(document: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The compiled document's full-run selection, or None when the document has no such key.

    Key PRESENCE is the test, not the value: that is the rule the shipped intake gate uses
    (`starter/pack/harness/policy_options.py:10`), so an explicit `null` is malformed, not a default."""
    if KEY not in document:
        return None
    selection = document[KEY]
    if selection is None:
        raise ValueError(f"compiled {KEY} is present but names no selection: an explicit null is not the default")
    return selection


def _is_mapping(value: Any) -> bool:
    return hasattr(value, "keys") and hasattr(value, "__getitem__")


def _design_of_body(body: Any, profile: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The engine's `design` argument for a `Design.json` body, or None when it asks nothing.

    Refuses, naming the offending value, everything the overlay cannot honour. Value rules (backend,
    target, max_error) are delegated to `qft_overlay`, which owns them. `profile` names the label the body
    came from, when it came from one, so a profile's refusal still refuses BY NAME."""
    from . import qft_overlay

    named = f"full-run selection {profile!r}" if profile else "full-run design"
    if not _is_mapping(body):
        raise ValueError(
            f"{named} {DESIGN_KEY!r} is not a {DESIGN_SCHEMA} object: {body!r}")

    unknown = sorted(set(body) - set(BODY_KEYS))
    if unknown:
        raise ValueError(
            f"{named} body has keys this pipeline cannot honour: {unknown} (known: "
            f"{list(BODY_KEYS)}). Note that `readout` is NOT a design key -- production carries the "
            f"readout axis as its own top-level `submitted_readout`, and it is priced by readout_overlay")

    if "schema" in body and body["schema"] != DESIGN_SCHEMA:
        raise ValueError(
            f"{named} body does not carry schema {DESIGN_SCHEMA}: {body['schema']!r}")

    # THE TOPOLOGY GATE. `qft_overlay` prices the QFT BAND and nothing else: its families are exactly
    # (data, work, scratch, exponent), `project(policy, coherent, design)` has no topology parameter, the
    # module's source contains no occurrence of `modmul`, `multiplier`, `topology`, `quotient` or
    # `splitCompare`, and `parse_design` REFUSES any design carrying a `modmul` key -- even
    # `modmul: "reference"` -- as a key it "cannot honour". `project` substitutes QFT sites by width into
    # `coherent`, which it requires to be the engine's exact REFERENCE price. So the overlay can honour one
    # topology only: the reference one, implicitly, by pricing against the reference census.
    #
    # A non-reference topology therefore CANNOT be priced by this chain. The multiplier drives the T cost
    # (`policy_design.py:139-142`) and the qubit base (`:160-162`), so pricing a quotientStream or
    # splitCompare run on reference-topology numbers would silently report the reference price as if the
    # option had been honoured. That is the silent default the release forbids, so it is refused BY NAME.
    # `modmul` is NOT dropped here: it is read, and it decides.
    multiplier = body.get(MULTIPLIER_KEY) or MULTIPLIER_REFERENCE
    if multiplier not in MULTIPLIERS:
        raise ValueError(
            f"{named} names an unrecognised {MULTIPLIER_KEY} {multiplier!r} (known: {list(MULTIPLIERS)})")
    if multiplier != MULTIPLIER_REFERENCE:
        raise ValueError(
            f"{named} selects the multiplier topology {multiplier!r}, which this chain cannot price. "
            f"`qft_overlay` prices the QFT band only: its families are {list(qft_overlay.FAMILIES)}, it has "
            f"no topology parameter, and it substitutes banded QFT sites into the engine's frozen "
            f"REFERENCE census. The multiplier drives both the T cost and the qubit base, so pricing a "
            f"{multiplier!r} run on reference-topology numbers would report the reference price as if the "
            f"topology had been honoured. It is reported, never priced as the reference default")

    phases = body.get(PHASE_KEY) or {}
    if phases:
        if not _is_mapping(phases):
            raise ValueError(f"{named} {PHASE_KEY!r} is not an object: {phases!r}")
        steps = sorted(set(phases) & set(PHASE_STEPS)) or sorted(phases)
        raise ValueError(
            f"{named} selects the phase-product step backend(s) {steps}, which this path cannot "
            f"price: the step backends are not symbolic, deterministic or closed-form and need certified "
            f"word banks, so they are out of scope and separately qualified "
            f"(qft_overlay.py SCOPE). They are reported by name, never ignored and never priced as "
            f"{MULTIPLIERS[0]!r} defaults")

    qft = body.get(QFT_KEY)
    if qft is None:
        qft = {}
    if not _is_mapping(qft):
        raise ValueError(f"{named} {QFT_KEY!r} is not a per-family object: {qft!r}")

    design: Dict[str, Any] = {}
    for family in qft:
        choice = qft[family]
        if _is_mapping(choice) and ROTATION_FIELD in choice:
            raise ValueError(
                f"QFT family {family!r} names a {ROTATION_FIELD!r} ({choice[ROTATION_FIELD]!r}), the "
                f"`{family}Rotations` axis, which this path cannot price: the engine's QFT design "
                f"(data, work, scratch, exponent) has no field for a rotation backend. It is refused by "
                f"name, the same reason profile 'quotient-fallback-data-catalytic-work' is refused")
        design[family] = copy.deepcopy(choice)

    # The overlay owns the value rules. Validate EAGERLY -- before any board is priced -- so an
    # unconvertible design is reported at conversion time and prices nothing.
    plan = qft_overlay.parse_design(design)
    for family, (_backend, target, max_error) in plan.items():
        if target == "approximate":
            qft_overlay.parse_max_error(family, max_error)

    # EXACT MEANS UNTOUCHED. No family asking for an approximate target is no design at all.
    if not any(target == "approximate" for _b, target, _m in plan.values()):
        return None
    return design


def design_of(selection: Optional[Any]) -> Optional[Dict[str, Any]]:
    """The engine's `design` argument for one compiled full-run selection.

    None (no selection) and the explicit `reference` both give None: the engine's default path, bit for bit.
    A selection this pipeline cannot convert is refused by name; it is never priced as the default.

    A selection names EITHER a `profile` (the closed enum of 8 qualified labels) OR an explicit `design`
    body carrying arbitrary per-family QFT choices. Never both and never neither."""
    if selection is None:
        return None
    if not isinstance(selection, dict):
        raise ValueError(f"full-run selection is not a {SELECTION_SCHEMA} object: {selection!r}")
    if selection.get("schema") != SELECTION_SCHEMA:
        raise ValueError(f"full-run selection does not carry schema {SELECTION_SCHEMA}: {selection.get('schema')!r}")
    has_profile, has_design = PROFILE_KEY in selection, DESIGN_KEY in selection
    if has_profile and has_design:
        raise ValueError(
            f"full-run selection names both a {PROFILE_KEY!r} ({selection[PROFILE_KEY]!r}) and an explicit "
            f"{DESIGN_KEY!r}: they are alternatives, and a selection that names both leaves it ambiguous "
            f"which one was attested. Exactly one is converted")
    if not has_profile and not has_design:
        raise ValueError(
            f"full-run selection names neither a {PROFILE_KEY!r} nor an explicit {DESIGN_KEY!r}: "
            f"one of the two is required, and an empty selection is not the default "
            f"(the default is the absent key)")
    if has_design:
        return _design_of_body(selection[DESIGN_KEY])
    profile = selection.get(PROFILE_KEY)
    if not isinstance(profile, str) or not profile:
        raise ValueError(f"full-run selection names no profile: {profile!r}")
    if profile == REFERENCE:
        return None
    if profile in BEYOND_QFT:
        raise ValueError(
            f"full-run selection {profile!r} cannot be priced here: it selects {BEYOND_QFT[profile]}, which the "
            f"engine's QFT design (data, work, scratch, exponent) cannot express. Unsupported scopes are reported, "
            f"not priced as defaults.")
    if profile in PROFILE_DESIGN:
        # Through the SAME reader as an explicit design, carrying `modmul`. Every one of these selects
        # quotientStream, so today every one of them is refused on its topology (see `_design_of_body`).
        return _design_of_body(PROFILE_DESIGN[profile], profile=profile)
    raise ValueError(f"unrecognised full-run selection {profile!r}: this pipeline converts only {sorted(PROFILES)}")
