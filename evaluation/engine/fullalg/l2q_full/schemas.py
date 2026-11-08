"""Schema registry — every schema string the pipeline reads or writes, defined once.

Audit F9 found eleven schema strings scattered across modules and notebook cells, with
``shor.lowgate.ir/v1`` re-spelled as a literal in five places and the KMY measurement cache
carrying no schema at all. Writers import the constant; readers call :func:`expect`.
"""
from __future__ import annotations

from typing import Any, Mapping

# --- new (this package) ------------------------------------------------------------------
FORSHOR_EMIT = "forshor.emit/v1"            # the per-k bundle (Lean forshor_emit / mirror)
FORSHOR_COEFF_POLY = "forshor.coeff_poly/v1"
FORSHOR_WIDTH = "forshor.width/v1"
FORSHOR_QFT_PLAN = "forshor.qft_plan/v1"
FORSHOR_MODMUL_PLAN = "forshor.modmul_plan/v1"
FORSHOR_RECURSION = "forshor.recursion/v1"
ESTIMATE_MANIFEST = "forshor.estimate.manifest/v1"
ONECLICK_WITNESS = "forshor.oneclick.witness/v1"

# --- the 2026-09-18 collaborator drop (v2 emitter) ---------------------------------------
FORSHOR_LOWGATE_V2 = "forshor.lowgate/v2"   # flat lowered circuit (`--flat`, and `shor`)
FORSHOR_PLAN = "forshor.plan/v1"            # the annotated view (`pp`/`cpp`/`qft` default)
FORSHOR_SHOR_PLAN = "forshor.shor_plan/v1"  # bundle section: per-(n, m) register widths

# --- ForShor cdca809 (2026-10-10): the extracted IR and width-indexed policies ----------
FORSHOR_IR_V1 = "forshor.ir/v1"             # extracted templates (`template`, `forshor_submission`)
FORSHOR_SUBMISSION = "forshor.submission/v1"  # a submitted table + its IR
POLICY = "forshor_qualtran.policy/v1"       # bands (threshold, table, IR) -> policy.load_policy
POLICY_SUBMISSION = "forshor.policy-submission/v1"  # ForShor `forshor_policy_submission` output -> policy.load_policy_submission
POLICY_REFERENCE = "forshor_qualtran.policy_reference/v1"  # Lean-lowered policy circuit hashes

# --- legacy (py/, qiskit/, notebooks) ----------------------------------------------------
TOOMSCHED = "shor.toomsched/v1"
LOWGATE_IR = "shor.lowgate.ir/v1"
QFT_LADDER = "shor.qft/v1"                   # the REFERENCE ladder (not the Lean lowerQFT term)
KMY_MEASURE_CACHE = "kmy.measure_cache/v1"
PRESENTATION_MANIFEST = "shor_toomcook_presentation.manifest/v2"
SYMBOLIC_WITNESS = "symbolic_resource_scaling.witness/v1"
APP_WITNESS = "oneclick.phase-frontend.witness/v2"
FOURIER_MUL_VERIFY = "lean_ir_fourier_multiply.verify/v1"
QFT_VERIFY = "lean_ir_qft.verify/v1"         # Phase-A differential: LeanQFT priced == lowerQFT IR (V)
REC_VERIFY = "lean_ir_recursive_pp.verify/v1"  # Phase-B differential: swept skeleton == E7 lowerRec IR (V) at anchors
MODMUL_VERIFY = "lean_ir_cmodmul.verify/v1"    # Phase-C differential (C1): priced LeanCtrlModMul == E5 lowerCmodMul IR cost-structure
MODEXP_VERIFY = "lean_modexp.verify/v1"        # Phase-D: priced LeanModExp == Lean modexp descriptor (REAL-LEAN only)
MODEXP_IR = "shor.modexp.ir/v1"                # Phase-D Lean IR: {tbits, multiplier schedule, embedded step-template lowgate IR}
ORDERFINDING_VERIFY = "lean_orderfinding.verify/v1"  # Phase-E: priced LeanOrderFinding == Lean orderfinding descriptor (REAL-LEAN only)
ORDERFINDING_IR = "shor.orderfinding.ir/v1"    # Phase-E Lean IR: {L, embedded modexp descriptor, embedded IQFT lowgate IR}
MULTIPLY_VERIFY = "lean_multiply.verify/v1"    # Phase-F: priced ToomCookMultiplier == Lean multiply descriptor (REAL-LEAN only)
MULTIPLY_IR = "shor.multiply.ir/v1"            # Phase-F Lean IR: {pp_width, embedded PhaseProduct IR (coeffs+recursion), embedded IQFT IR}
MODMULT_STAGE = "lean_modmult.stage/v1"
QASM_BUNDLE = "shor.qasm_bundle/v1"

ALL = {name: value for name, value in globals().items()
       if name.isupper() and isinstance(value, str) and "/" in value}


class SchemaError(ValueError):
    pass


def expect(doc: Mapping[str, Any], schema: str, *, where: str = "") -> Mapping[str, Any]:
    """Raise :class:`SchemaError` unless ``doc['schema'] == schema``; return ``doc``."""
    got = doc.get("schema") if isinstance(doc, Mapping) else None
    if got != schema:
        raise SchemaError(f"{where or 'document'}: expected schema {schema!r}, got {got!r}")
    return doc
