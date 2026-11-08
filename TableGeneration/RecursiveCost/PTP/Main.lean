import TableGeneration.RecursiveCost.PTP.Assertions

/-!
# PTP main theorems

Proves the named assertions of `Assertions.lean`. Every supporting lemma lives in the other
modules of this folder; this file only packages (the PP pattern: `PhaseProduct/Main.lean`).
The `#assert_axioms` guard for these theorems is `scripts/PTPAxiomCheck.lean`.
-/

namespace TableGeneration.PTP

open Shor

/-- **Main theorem (state level, exact board).** -/
theorem ptp_lowerExact_correct (qs : QSemantics) [RegEncoding qs.Basis] [LowerGateClass qs]
    (P : PTPPolicy) (n : ℕ) (X : Fin 3 → ExtReg) (base : ℕ) :
    PTPLowerExactCorrect qs P n X base := by
  intro hP hG
  obtain ⟨W, G, hI, hB, _, hR⟩ := ptp_gate_full_exact (qs := qs) P hP n X base hG
  exact ⟨W, G, hI, hB,
    fun φ U ψ hψ => evalL_policyImplD_state hP (admissible_exact P) hI φ U hψ, hR⟩

end TableGeneration.PTP
