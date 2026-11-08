import TableGeneration.RecursiveCost.PTP.Main
import TableGeneration.RecursiveCost.PTP.Controlled
import TableGeneration.RecursiveCost.PTP.RecursionWitness
import ForShor.ShorVerification.Model.Standard.LowGates
import ForShor.ShorVerification.Model.Standard.Lift

/-!
# PTP in ForShor's Standard model (non-vacuity of the typeclass stack)

Every PTP gate theorem is stated over `qs : QSemantics`, `[RegEncoding qs.Basis]`,
`[LowerGateClass qs]`. If those laws were contradictory, every such theorem would hold
vacuously and `#print axioms` would not notice. ForShor's `Model/Standard` is a concrete
model of the classes (a `Finsupp`-based Hilbert space with a `LowerGateClass` instance), which
ForShor uses to specialise its own headlines (`Model/Standard/Headline.lean`). This file does
the same for PTP:

* `modelPhaseOp`: the specification operator `PTPPhaseOp` exists in the model (a diagonal
  unit-modulus lift, `norm_ptpEigenphase`), so the state-level assertion's hypothesis is
  satisfiable;
* `model_ptp_lowerExact_correct`: the main theorem specialised to `Model.qs` (every instance
  resolves);
* `model_witness_correct`: a concrete instance, the accepted k = 2 policy `fixed` on the
  three 20-qubit registers of `RecursionWitness`, with its gate-level circuit equal to the
  model's phase operator on every clean state.

Like ForShor's own `Model/`, nothing here is used to prove anything else. Not in the root
import `PTP.lean`.
-/

namespace TableGeneration.PTP.StandardModel

open Shor TableGeneration.PTP

theorem norm_ptpEigenphase {qs : QSemantics} [RegEncoding qs.Basis] (φ : Angle)
    (X : Fin 3 → ExtReg) (b : qs.Basis) : ‖ptpEigenphase φ X b‖ = 1 := by
  unfold ptpEigenphase
  have h : ((Angle.toReal φ : ℝ) : ℂ) * Complex.I *
      (((extToInt (X 0) b : ℤ) : ℂ) * ((extToInt (X 1) b : ℤ) : ℂ) *
        ((extToInt (X 2) b : ℤ) : ℂ)) =
      (((Angle.toReal φ * ((extToInt (X 0) b * extToInt (X 1) b * extToInt (X 2) b : ℤ) : ℝ) : ℝ)
        : ℂ)) * Complex.I := by
    push_cast; ring
  rw [h, Complex.norm_exp_ofReal_mul_I]

/-- The specification operator exists in the Standard model. -/
noncomputable def modelPhaseOp (X : Fin 3 → ExtReg) (φ : Angle) :
    PTPPhaseOp Model.qs X φ where
  op := fun ψ => Model.diagLift (fun b => ptpEigenphase (qs := Model.qs) φ X b)
    (fun b => norm_ptpEigenphase (qs := Model.qs) φ X b) ψ
  op_add := fun ψ χ => map_add _ ψ χ
  op_smul := fun a ψ => map_smul _ a ψ
  op_ket := fun b => Model.diagLift_ket _ _ b

theorem norm_ptpCtlEigenphase {qs : QSemantics} [RegEncoding qs.Basis] (φ : Angle) (c : ℕ)
    (X : Fin 3 → ExtReg) (b : qs.Basis) : ‖ptpCtlEigenphase φ c X b‖ = 1 := by
  unfold ptpCtlEigenphase
  split_ifs
  · exact norm_ptpEigenphase φ X b
  · simp

/-- The controlled specification operator exists in the Standard model. -/
noncomputable def modelCtlPhaseOp (c : ℕ) (X : Fin 3 → ExtReg) (φ : Angle) :
    PTPCtlPhaseOp Model.qs c X φ where
  op := fun ψ => Model.diagLift (fun b => ptpCtlEigenphase (qs := Model.qs) φ c X b)
    (fun b => norm_ptpCtlEigenphase (qs := Model.qs) φ c X b) ψ
  op_add := fun ψ χ => map_add _ ψ χ
  op_smul := fun a ψ => map_smul _ a ψ
  op_ket := fun b => Model.diagLift_ket _ _ b

/-- The controlled main theorem at `Model.qs`. -/
theorem model_ptp_ctl_lowerExact_correct (P : PTPPolicy) (n : ℕ) (X : Fin 3 → ExtReg)
    (Y : ExtReg) (c base : ℕ) : PTPCtlLowerExactCorrect Model.qs P n X Y c base :=
  ptp_ctl_lowerExact_correct (qs := Model.qs) P n X Y c base

/-- The main theorem at `Model.qs`: every instance in its statement resolves. -/
theorem model_ptp_lowerExact_correct (P : PTPPolicy) (n : ℕ) (X : Fin 3 → ExtReg) (base : ℕ) :
    PTPLowerExactCorrect Model.qs P n X base :=
  ptp_lowerExact_correct Model.qs P n X base

/-- A concrete instance: the `RecursionWitness` registers satisfy `Good`. -/
theorem witness_good : Good Witness.X 20 60 := by
  refine ⟨?_, ?_, ?_⟩
  · intro s; revert s; decide +kernel
  · intro s s' h
    apply Witness.disj_of_check
    revert h
    revert s s'
    decide +kernel
  · intro s
    revert s
    decide +kernel

/-- **Concrete instance in the model.** The accepted k = 2 policy `fixed` on the
`RecursionWitness` registers has an exact-board gate-level circuit that equals the model's
phase operator on every clean state. -/
theorem model_witness_correct :
    ∃ (W : List ℕ) (G : Angle → LowGate),
      PolicyImplX Instances.FaultChallenge.fixed Witness.X 20 W G ∧
      ∀ (φ : Angle) (ψ : Model.qs.State), PTPCleanState Model.qs Witness.X 20 W ψ →
        LowerGateClass.evalL (qs := Model.qs) (G φ) ψ = (modelPhaseOp Witness.X φ).op ψ := by
  obtain ⟨W, G, hI, _, hS, _⟩ := model_ptp_lowerExact_correct Instances.FaultChallenge.fixed 20
    Witness.X 60 Instances.FaultChallenge.fixed_accepted witness_good
  exact ⟨W, G, hI, fun φ ψ hψ => hS φ (modelPhaseOp Witness.X φ) ψ hψ⟩

end TableGeneration.PTP.StandardModel
