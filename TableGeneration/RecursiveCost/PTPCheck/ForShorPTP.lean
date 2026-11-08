import ForShor.ShorVerification.Implementation.PhaseTripleProduct.Main

/-!
# ForShor's PhaseTripleProduct, for the PTP report and axiom guard

Re-exports ForShor's `Implementation/PhaseTripleProduct/Main.lean` (which imports, transitively, every
`Proofs/` module the checker's report program and `scripts/PTPAxiomCheck.lean` use). ForShor imports
Mathlib, so a direct ForShor import under `scripts/` would fall outside the subtree
`check_mathlib_confinement.py` allows (`TableGeneration/RecursiveCost/`); this module keeps it inside.
-/
