import TableGeneration.BestKnown
import TableGeneration.RecursiveCost.Model

namespace TableGeneration.RecursiveCost

/-- One verified PhaseProduct program available to the recursive planner. -/
structure Candidate where
  policyId : String
  k : Nat
  hk : k >= 2
  program : Prog k
  /-- How a negation of the program is charged (ForShor `NegationMethod`); `.oneQubit` unless asked.
  `.scratch` reproduces the pre-swap (ForShor 6d55ad9) numbers. -/
  negation : Shor.NegationMethod := .oneQubit

namespace Candidate

/-- Stable display key for a policy at one `k`. -/
def key (candidate : Candidate) : String :=
  candidate.policyId ++ ":k=" ++ toString candidate.k

/-- Analyze a candidate at a balanced `n`-bit PhaseProduct node. -/
def analyze (candidate : Candidate) (n : Nat) : ProgramAnalysis :=
  analyzeBalancedProgram n candidate.program candidate.negation

/-- The same candidate, charged under another negation method. -/
def withNegation (candidate : Candidate) (m : Shor.NegationMethod) : Candidate :=
  { candidate with negation := m }

end Candidate

/-- Current promoted PhaseProduct program for one `k`. -/
def bestKnownCandidate (k : Nat) (hk : k >= 2) : Candidate where
  policyId := bestKnownPolicyId .PhaseProduct k
  k
  hk
  program := bestKnownGenerate .PhaseProduct k hk

/--
Initial standalone catalog. Promotion can regenerate this list when the
published policy catalog grows beyond one promoted program per `k`.
-/
def bestKnownCandidates : List Candidate :=
  [ bestKnownCandidate 2 (by decide)
  , bestKnownCandidate 3 (by decide)
  , bestKnownCandidate 4 (by decide)
  , bestKnownCandidate 5 (by decide)
  , bestKnownCandidate 6 (by decide)
  , bestKnownCandidate 7 (by decide)
  , bestKnownCandidate 8 (by decide)
  , bestKnownCandidate 9 (by decide)
  , bestKnownCandidate 10 (by decide)
  , bestKnownCandidate 11 (by decide)
  , bestKnownCandidate 12 (by decide)
  , bestKnownCandidate 13 (by decide)
  , bestKnownCandidate 14 (by decide)
  , bestKnownCandidate 15 (by decide)
  , bestKnownCandidate 16 (by decide) ]

end TableGeneration.RecursiveCost
