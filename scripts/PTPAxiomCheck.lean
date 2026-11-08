import TableGeneration.RecursiveCost.PTPCheck.AxiomAudit
import TableGeneration.RecursiveCost.PTPPlan.Plan
import TableGeneration.RecursiveCost.PTPCheck.ForShorPTP

/-!
# PTP axiom guard (what this fork instantiates)

The PhaseTripleProduct proofs live in ForShor (`Implementation/PhaseTripleProduct`, namespace
`Shor.PTP`); ForShor's own `scripts/AxiomCheck.lean` guards its headline claims. This file
guards (a) the ForShor theorems this fork's planner and policy-checker harness actually
instantiate, and (b) the planner's own theorems, which are proved here.

`#assert_axioms` (ForShor `Submission/Audit.lean`) fails the build if any listed theorem depends
on anything outside `propext`, `Classical.choice`, `Quot.sound`.

Run: `lake env lean scripts/PTPAxiomCheck.lean`
-/

open Shor.Audit

-- ForShor: value semantics, node identity and widths (instantiated by the planner and the certificates).
#assert_axioms Shor.PTP.evalPhase_correct
#assert_axioms Shor.PTP.evalPhaseW_correct
#assert_axioms Shor.PTP.node_identity
#assert_axioms Shor.PTP.node_width_sound

-- ForShor: exact widths and the threshold rule.
#assert_axioms Shor.PTP.ptpTightWidth_spec
#assert_axioms Shor.PTP.tight_minimal
#assert_axioms Shor.PTP.tight_le_childWidth
#assert_axioms Shor.PTP.exactWidth_eq
#assert_axioms Shor.PTP.evalPhaseTight_correct
#assert_axioms Shor.PTP.PTPPolicy.chainContractsTight_iff_reach

-- ForShor: gate level.
#assert_axioms Shor.PTP.evalL_ptpNode_ket
#assert_axioms Shor.PTP.evalL_ptpNode_ket_exact
#assert_axioms Shor.PTP.ptp_gate_full
#assert_axioms Shor.PTP.ptp_gate_full_exact
#assert_axioms Shor.PTP.evalL_policyImplD_state

-- ForShor: the nine claims of PhaseTripleProduct/Main.lean.
#assert_axioms Shor.PTP.ptp_lowerExact_correct
#assert_axioms Shor.PTP.ptp_ctl_lowerExact_correct
#assert_axioms Shor.PTP.ptp_pad_lowerExact_correct
#assert_axioms Shor.PTP.ptp_gateTotal_bound
#assert_axioms Shor.PTP.ptp_circuit_gateCount_bound
#assert_axioms Shor.PTP.ptp_qubits_tight
#assert_axioms Shor.PTP.ptp_qubits_tight_exact
#assert_axioms Shor.PTP.ptp_qubits_le_ptpQubits
#assert_axioms Shor.PTP.ptp_chainContractsTight_iff_reach

-- This fork: the PTP planner (PTPPlan/Plan.lean), every output valid for every oracle.
#assert_axioms TableGeneration.PTPPlan.sortedDesc_eq_mirror
#assert_axioms TableGeneration.PTPPlan.Planner.plan_check
#assert_axioms TableGeneration.PTPPlan.Planner.plan_correct
#assert_axioms TableGeneration.PTPPlan.Planner.plan_correct_w
#assert_axioms TableGeneration.PTPPlan.Planner.plan_correct_tight
#assert_axioms TableGeneration.PTPPlan.Planner.plan_ladder
#assert_axioms TableGeneration.PTPPlan.Planner.plan_choose_visit
#assert_axioms TableGeneration.PTPPlan.Planner.plan_ne_of_unsorted
#assert_axioms TableGeneration.PTPPlan.Planner.plan_ne_of_foreign
