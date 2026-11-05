import TableGeneration.RecursiveCost.PTP.ExactBuild

/-!
# PTP public specification and assertions (state level)

The PTP analogue of ForShor's `PhaseProduct/Spec/Assertions.lean`: the independent
specification of what a PTP circuit must do, and the final claims as named propositions
(proved in `Main.lean`).

## The specification (`ptpEigenphase`, `PTPPhaseOp`)

`ptpEigenphase φ X b = exp(i·π·φ·⟦X₀⟧·⟦X₁⟧·⟦X₂⟧)` is the phase the PTP gate applies to the basis
ket `b` (signed two's-complement values of three registers). `PTPPhaseOp qs X φ` is a linear
state map with exactly that action on every basis ket: it plays the role of ForShor's
`qs.eval (Gate.SignedPhaseProd φ x z)`, whose ket law is the class field
`PhaseSemantics.eval_SignedPhaseProd_ket`. It is a *structure the reader supplies*, so the
specification is stated independently of the circuit (nothing in it mentions `LowGate`).

## What is new relative to `ExactBuild.lean`

`ptp_gate_full_exact` is about basis kets with a zero workspace. Here the claim is about **all
states** in the linear closure of the clean basis kets (`CleanClosure`, ForShor's
`Shared/States.lean`): the circuit equals the specification operator on that whole subspace
(`evalL_eq_phaseOp_of_clean`), by linearity of `evalL` (`evalL_add`, `evalL_smul`).

What this does not claim: that the spec operator exists in an arbitrary `qs` (it does in
ForShor's Standard model, see `StandardModel.lean`), or anything about states outside the clean
closure (the circuit is only specified on a clean workspace).
-/

namespace TableGeneration.PTP

open Shor

/-! ## The specification -/

/-- The PTP phase on a basis ket: `exp(iπφ · ⟦X₀⟧⟦X₁⟧⟦X₂⟧)`. -/
noncomputable def ptpEigenphase {qs : QSemantics} [RegEncoding qs.Basis]
    (φ : Angle) (X : Fin 3 → ExtReg) (b : qs.Basis) : ℂ :=
  Complex.exp (((Angle.toReal φ : ℝ) : ℂ) * Complex.I *
    (((extToInt (X 0) b : ℤ) : ℂ) * ((extToInt (X 1) b : ℤ) : ℂ) * ((extToInt (X 2) b : ℤ) : ℂ)))

/-- A linear state map acting as the PTP phase on every basis ket (the specification operator;
cf. ForShor's `Gate.SignedPhaseProd` evaluation). -/
structure PTPPhaseOp (qs : QSemantics) [RegEncoding qs.Basis] (X : Fin 3 → ExtReg) (φ : Angle) where
  op : qs.State → qs.State
  op_add : ∀ ψ χ, op (ψ + χ) = op ψ + op χ
  op_smul : ∀ (a : ℂ) ψ, op (a • ψ) = a • op ψ
  op_ket : ∀ b : qs.Basis, op (qs.ket b) = ptpEigenphase φ X b • qs.ket b

/-! ## Clean states -/

/-- The basis-level precondition of the PTP gate: the three operands fit signed `n` bits and the
workspace `W` holds `|0⟩`. -/
def PTPCleanBasis (qs : QSemantics) [RegEncoding qs.Basis] (X : Fin 3 → ExtReg) (n : ℕ)
    (W : List ℕ) (b : qs.Basis) : Prop :=
  (∀ s, FitsSignedWidth n (extToInt (X s) b)) ∧ ∀ q ∈ W, RegEncoding.bit q b = false

/-- A state with a clean workspace: the linear closure of the clean basis kets. -/
abbrev PTPCleanState (qs : QSemantics) [RegEncoding qs.Basis] (X : Fin 3 → ExtReg) (n : ℕ)
    (W : List ℕ) : qs.State → Prop :=
  CleanClosure (PTPCleanBasis qs X n W)

/-! ## State-level lift -/

section Lift

variable {qs : QSemantics} [RegEncoding qs.Basis] [LowerGateClass qs]

theorem evalL_zero (L : LowGate) : LowerGateClass.evalL (qs := qs) L 0 = 0 := by
  have h := LowerGateClass.evalL_smul (qs := qs) L 0 (0 : qs.State)
  simpa using h

/-- **Linearity lift.** A circuit that acts as `U` on every clean basis ket acts as `U` on every
clean state. -/
theorem evalL_eq_of_clean_kets {L : LowGate} {P : qs.Basis → Prop} (U : qs.State → qs.State)
    (hU0 : U 0 = 0) (hUadd : ∀ ψ χ, U (ψ + χ) = U ψ + U χ)
    (hUsmul : ∀ (a : ℂ) ψ, U (a • ψ) = a • U ψ)
    (hket : ∀ b, P b → LowerGateClass.evalL (qs := qs) L (qs.ket b) = U (qs.ket b))
    {ψ : qs.State} (hψ : CleanClosure P ψ) :
    LowerGateClass.evalL (qs := qs) L ψ = U ψ := by
  induction hψ with
  | zero => rw [evalL_zero, hU0]
  | ket b h => exact hket b h
  | add _ _ ih1 ih2 => rw [LowerGateClass.evalL_add, hUadd, ih1, ih2]
  | smul a _ ih => rw [LowerGateClass.evalL_smul, hUsmul, ih]

theorem PTPPhaseOp.op_zero {X : Fin 3 → ExtReg} {φ : Angle} (U : PTPPhaseOp qs X φ) :
    U.op 0 = 0 := by
  have h := U.op_smul 0 (0 : qs.State)
  simpa using h

/-- **State-level PTP correctness for any gate correct on the clean kets.** -/
theorem evalL_eq_phaseOp_of_clean {X : Fin 3 → ExtReg} {n : ℕ} {W : List ℕ} {φ : Angle}
    {L : LowGate}
    (hket : ∀ b : qs.Basis, PTPCleanBasis qs X n W b →
      LowerGateClass.evalL (qs := qs) L (qs.ket b) = ptpEigenphase φ X b • qs.ket b)
    (U : PTPPhaseOp qs X φ) {ψ : qs.State} (hψ : PTPCleanState qs X n W ψ) :
    LowerGateClass.evalL (qs := qs) L ψ = U.op ψ :=
  evalL_eq_of_clean_kets U.op U.op_zero U.op_add U.op_smul
    (fun b hb => by rw [U.op_ket, hket b hb]) hψ

/-- **The policy circuit at any admissible width rule is the PTP phase operator on every clean
state** (state-level form of `evalL_policyImplD_ket`). -/
theorem evalL_policyImplD_state {P : PTPPolicy} (hP : P.check = true) {wd : WidthRule}
    (hwd : Admissible P wd) {X : Fin 3 → ExtReg} {n : ℕ} {W : List ℕ} {G : Angle → LowGate}
    (h : PolicyImplD P wd X n W G) (φ : Angle) (U : PTPPhaseOp qs X φ) {ψ : qs.State}
    (hψ : PTPCleanState qs X n W ψ) :
    LowerGateClass.evalL (qs := qs) (G φ) ψ = U.op ψ :=
  evalL_eq_phaseOp_of_clean
    (fun b hb => evalL_policyImplD_ket hP hwd h φ b hb.1 hb.2) U hψ

end Lift

/-! ## The named assertion -/

/-- The gate-level census of a circuit equals the exact-board formulas for `P` at width `n`. -/
def PTPExactCensus (P : PTPPolicy) (n : ℕ) (G : LowGate) : Prop :=
  let R := LowGate.resources shorGateResourceModel G
  R.rz = (ptpResourcesX P n).rz ∧ R.toffoli = (ptpResourcesX P n).toffoli ∧
    R.x = (ptpResourcesX P n).x ∧ R.h = (ptpResourcesX P n).h ∧
    R.cnot = ptpCnotFullX P n ∧ adjToffCount G = ptpAndDagX P n ∧
    (LowGate.usedQubits G).card ≤ 3 * n + ptpWorkspaceX P n

/-- **Assertion (state level, exact board).** For a checked policy `P` and three pairwise disjoint `n`-qubit
registers below `base`, there is a circuit family following `P` at exact widths, with workspace
`W` above `base`, which acts as the PTP phase operator on every clean state (for every
specification operator `U`) and has the exact-board gate counts (equalities) and a qubit upper bound. For `n = 0` no basis ket is
clean (`FitsSignedWidth` needs `0 < n`), so the statement is trivial there. -/
def PTPLowerExactCorrect (qs : QSemantics) [RegEncoding qs.Basis] [LowerGateClass qs]
    (P : PTPPolicy) (n : ℕ) (X : Fin 3 → ExtReg) (base : ℕ) : Prop :=
  P.check = true → Good X n base →
    ∃ (W : List ℕ) (G : Angle → LowGate), PolicyImplX P X n W G ∧
      (∀ q ∈ W, base ≤ q ∧ q < base + ptpWorkspaceX P n) ∧
      (∀ (φ : Angle) (U : PTPPhaseOp qs X φ) (ψ : qs.State), PTPCleanState qs X n W ψ →
        LowerGateClass.evalL (qs := qs) (G φ) ψ = U.op ψ) ∧
      (∀ φ : Angle, PTPExactCensus P n (G φ))

end TableGeneration.PTP
