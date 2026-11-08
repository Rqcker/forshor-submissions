import ForShor.ShorVerification.Submission.Audit

/-!
# ForShor's axiom-audit command, for the PTP axiom guard

Re-exports ForShor's `Submission/Audit.lean` (`#assert_axioms`) for
`scripts/PTPAxiomCheck.lean`. ForShor imports Mathlib, so a direct ForShor import
under `scripts/` would fall outside the subtree `check_mathlib_confinement.py`
allows (`TableGeneration/RecursiveCost/`); this module keeps it inside.
-/
