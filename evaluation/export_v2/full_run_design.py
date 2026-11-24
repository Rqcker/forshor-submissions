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
(`data`, `work`, `scratch`, `exponent`) only. Three of the seven profiles are pure QFT choices and convert.
The other four additionally select a rotation backend or a phase-product step backend, which the contract has
no field for; they are REFUSED by name. A selection that is reported is never priced as the default.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

KEY = "submitted_full_run"
SELECTION_SCHEMA = "forshor.full-run-selection/v1"
REFERENCE = "reference"

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


def design_of(selection: Optional[Any]) -> Optional[Dict[str, Any]]:
    """The engine's `design` argument for one compiled full-run selection.

    None (no selection) and the explicit `reference` both give None: the engine's default path, bit for bit.
    A selection this pipeline cannot convert is refused by name; it is never priced as the default."""
    if selection is None:
        return None
    if not isinstance(selection, dict):
        raise ValueError(f"full-run selection is not a {SELECTION_SCHEMA} object: {selection!r}")
    if selection.get("schema") != SELECTION_SCHEMA:
        raise ValueError(f"full-run selection does not carry schema {SELECTION_SCHEMA}: {selection.get('schema')!r}")
    profile = selection.get("profile")
    if not isinstance(profile, str) or not profile:
        raise ValueError(f"full-run selection names no profile: {profile!r}")
    if profile == REFERENCE:
        return None
    if profile in QFT_ONLY:
        return copy.deepcopy(QFT_ONLY[profile])
    if profile in BEYOND_QFT:
        raise ValueError(
            f"full-run selection {profile!r} cannot be priced here: it selects {BEYOND_QFT[profile]}, which the "
            f"engine's QFT design (data, work, scratch, exponent) cannot express. Unsupported scopes are reported, "
            f"not priced as defaults.")
    raise ValueError(f"unrecognised full-run selection {profile!r}: this pipeline converts only {sorted(PROFILES)}")
