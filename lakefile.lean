import Lake
open Lake DSL

package ShorTableOptimization where

/--
Pinned to the revision the companion uses, so the companion's own definitions
elaborate here unchanged -- `Function.update` in the width scan and
`Finset.univ.sup` in `commonNeededWidth` have no Lean-core equivalent. The
companion pins Mathlib too, so the two pins move together. `update-forshor`
fails outright when the Lean toolchains differ, and warns when only the Mathlib
revisions do -- our pin governs the build, so the build decides whether that
matters.

Listed first because Lake resolves a package name once, so the root pin wins.

CI confines the import to `TableGeneration/RecursiveCost/**`. That is necessary
for the propext-only axiom audit but not sufficient: core Lean can route an
instance through a classical definition with no Mathlib in sight (v4.36.0-rc1
did exactly that to `BEq Int`). The audit itself is the check.
-/
require mathlib from git
  "https://github.com/leanprover-community/mathlib4.git" @ "4dc43842a11f257ffba2a611c943a133a65cb4b2"

/--
Supplies the gate cost model, confined to `TableGeneration/RecursiveCost/**`
like Mathlib.

The library is `ForShor` (it was `FastMultiplication` before ForShor bb2aae0); the
PACKAGE is named `Fast_multiplication` here and in `lake-manifest.json`, but
upstream declares `package «ForShor»`. Resolution works anyway because lake takes
the name from the manifest, not from the remote lakefile. Renaming is cosmetic and
costs the build cache (D3).

Pinned to an exact commit rather than `main`: a ForShor rename or module move then
cannot break this build until the pin is bumped on purpose. 21e129a is the merge
commit of PR #80 on ForShor (the board PhaseProduct policies and their kernel-checked order-finding peaks, PTP page text), on top of eb00c5a, the merge
commit of PR #79 (negation as complement plus a one-clean-ancilla incrementer, with Lean's real counts); each merge commit's tree equals its PR head on which CI passed, which descends from 6d55ad9, the
`qubitCount` branch tip of 2026-11-04 (qubit count, unconditional balanced split, module
reorganisation; Gauss-Jordan `@[csimp]` restored ahead of its compiled callers).
-/
require «Fast_multiplication» from git
  "https://github.com/VerifiedQC/ForShor.git" @ "21e129aeefc0fd1bbb8fa63402f5799af2490788"

@[default_target]
lean_lib TableGeneration where
  moreLeanArgs := #["-s", "1048576"]
