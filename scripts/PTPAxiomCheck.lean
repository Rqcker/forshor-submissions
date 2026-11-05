import TableGeneration.RecursiveCost.PTP
import TableGeneration.RecursiveCost.PTP.Main
import TableGeneration.RecursiveCost.PTP.Controlled
import TableGeneration.RecursiveCost.PTP.Asymptotic
import TableGeneration.RecursiveCost.PTP.Unbalanced
import TableGeneration.RecursiveCost.PTP.QubitsTight
import TableGeneration.RecursiveCost.PTP.StandardModel
import TableGeneration.RecursiveCost.PTP.AxiomAudit

/-!
# PTP axiom guard

The PTP counterpart of ForShor's `scripts/AxiomCheck.lean`: `#assert_axioms` (ForShor
`Submission/Audit.lean`) fails the build if any listed theorem depends on anything outside
`propext`, `Classical.choice`, `Quot.sound`.

Run: `lake env lean scripts/PTPAxiomCheck.lean`
-/

open Shor.Audit

-- Policy level (value semantics).
#assert_axioms TableGeneration.PTP.evalPhase_correct
#assert_axioms TableGeneration.PTP.evalPhaseW_correct
#assert_axioms TableGeneration.PTP.node_identity
#assert_axioms TableGeneration.PTP.node_width_sound

-- Exact widths and threshold rule.
#assert_axioms TableGeneration.PTP.evalPhaseTight_correct
#assert_axioms TableGeneration.PTP.PTPPolicy.chainContractsTight_iff_reach

-- Gate level (basis kets).
#assert_axioms TableGeneration.PTP.evalL_ptpNode_ket_exact
#assert_axioms TableGeneration.PTP.ptp_gate_full
#assert_axioms TableGeneration.PTP.ptp_gate_full_exact

-- Gate level (all clean states) and the named assertion.
#assert_axioms TableGeneration.PTP.evalL_policyImplD_state
#assert_axioms TableGeneration.PTP.ptp_lowerExact_correct

-- Non-vacuity in ForShor's Standard model.
#assert_axioms TableGeneration.PTP.StandardModel.norm_ptpEigenphase
#assert_axioms TableGeneration.PTP.StandardModel.model_ptp_lowerExact_correct
#assert_axioms TableGeneration.PTP.StandardModel.model_witness_correct

-- Controlled PTP (masked operand): generic, PTP-level and exact-board.
#assert_axioms TableGeneration.PTP.evalL_maskGate_ket
#assert_axioms TableGeneration.PTP.evalL_unmask_mask
#assert_axioms TableGeneration.PTP.evalL_ctlGate_ket
#assert_axioms TableGeneration.PTP.evalL_ctl_ptp_ket
#assert_axioms TableGeneration.PTP.evalL_ctl_ptp_state
#assert_axioms TableGeneration.PTP.ptp_ctl_lowerExact_correct
#assert_axioms TableGeneration.PTP.StandardModel.model_ptp_ctl_lowerExact_correct

-- Asymptotic gate count.
#assert_axioms TableGeneration.PTP.scanMax_le
#assert_axioms TableGeneration.PTP.childWidth_le
#assert_axioms TableGeneration.PTP.gateTotal_node
#assert_axioms TableGeneration.PTP.ptp_gateTotal_bound
#assert_axioms TableGeneration.PTP.ptp_circuit_gateCount_bound

-- Unbalanced operand widths (padding).
#assert_axioms TableGeneration.PTP.padBasis_spec
#assert_axioms TableGeneration.PTP.evalL_pad_ptp_ket
#assert_axioms TableGeneration.PTP.ptp_pad_lowerExact_correct

-- Tight qubit accounting.
#assert_axioms TableGeneration.PTP.card_poolU_le
#assert_axioms TableGeneration.PTP.exists_policyImplD_tight
#assert_axioms TableGeneration.PTP.ptp_qubits_tight
#assert_axioms TableGeneration.PTP.ptp_qubits_tight_exact
#assert_axioms TableGeneration.PTP.ptp_qubits_le_ptpQubits
