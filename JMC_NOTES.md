# Interim fork: banded PP/PTP policies, planner and ForShor bridge

This is an interim fork of [VerifiedQC/ShorTableOptimization](https://github.com/VerifiedQC/ShorTableOptimization).
It is a proposed extension that implements the policy-submission format on the STO side. It is
**not merged into or endorsed by upstream**, and it is shared so collaborators have a working copy
while that is discussed. If upstream adopts these changes, use upstream instead.

- **Base:** upstream `main` at `361e81e` (2026-11-03, "Update ForShor to c0eb474").
  Toolchain and Mathlib are upstream's, unchanged: `leanprover/lean4:v4.36.0-rc1`, Mathlib `4dc43842`.
- **ForShor pin:** this fork pins ForShor (`Fast_multiplication`) to the exact commit `8e37ce7` (modular swap 001, negation
  as complement plus a one-clean-ancilla incrementer; ForShor PR #79), which descends from `6d55ad9`, the tip
  of ForShor's `qubitCount` branch on 2026-11-04. Not `main`. That branch renames the library
  `FastMultiplication` to `ForShor`, so a few STO files were ported: import paths, two removed
  helper names re-stated locally, and the confinement check. ForShor itself is not modified. See
  `JMC_VALIDATION.md` (sections "Port to ForShor `bb2aae0`" and "Pin bump to ForShor `6d55ad9`").
- **Apart from that port, the change adds files only.** It adds 180 files under `TableGeneration/RecursiveCost/{BandedPolicy,PTP}/`,
  `TableGeneration/RecursiveCost/PlanCore.lean`, `scripts/` and `policies/`, plus this file and
  `JMC_VALIDATION.md`. The only upstream files it touches are `README.md` (a pointer at the top)
  and `.gitignore` (two lines for harness scratch modules). The umbrella `TableGeneration.lean` is unchanged,
  so `lake build TableGeneration` and upstream CI behave as on upstream. ForShor is not modified.

## What it adds

- **Lean.** It adds banded (multi-table, recursive) PhaseProduct and PhaseTripleProduct policies
  built from STO tables. Each policy has a decidable check, and an accepted check implies
  correctness at every width. A threshold acceptance rule rejects any band that does not shrink
  the width (`BandedPolicy/Threshold.lean`, `PTP/Threshold.lean`). Theorems at the target
  `n = 2048` are exact-width correctness and the exact ladder; for PTP they also cover the
  gate-level recursion. A planner (`PlanCore`, `*/Plan.lean`) is included with 31 reconstructed
  seeds (`*/PlanSeeds/`). A bridge to ForShor's policy submission (`BandedPolicy/Bridge*.lean`)
  connects the policies to ForShor's verified compiler.
- **Harnesses.** `scripts/check_banded_policy.py` and `scripts/check_ptp_policy.py` check a
  policy JSON end to end: they generate Lean, have the kernel decide the check, replay the kernel
  and pin the statements. The planner bridge is `scripts/plan_from_site.py`. Each harness exits
  with 0 when the policy is accepted and 1 when it is rejected.

## Build

```
lake exe cache get
lake build TableGeneration.RecursiveCost.BandedPolicy \
           TableGeneration.RecursiveCost.BandedPolicy.BridgeClosure \
           TableGeneration.RecursiveCost.BandedPolicy.Plan \
           TableGeneration.RecursiveCost.BandedPolicy.Instances.RefPP \
           TableGeneration.RecursiveCost.PTP TableGeneration.RecursiveCost.PTP.Plan \
           TableGeneration.RecursiveCost.PTP.RecursionWitness \
           TableGeneration.RecursiveCost.PTP.Instances.ExactFault
```
`BridgeClosure` imports most of ForShor. A cold ForShor build took about 7 min on an M-series Mac
(peak 7.5 GB), and the new modules took about 4.5 min more (peak 5.8 GB).

## PTP rigor extension (2026-11-04)
Added `PTP/{Assertions,Main,Controlled,Asymptotic,StandardModel}.lean`, `PTP/README.md`, `scripts/PTPAxiomCheck.lean`,
`scripts/check_ptp_layers.py`, `docs/ptp_gap_note/`. See `docs/ptp_gap_note/PTP_RIGOR_2026-11-04.md` (scope, caveats, audit).
Extra build: `lake build TableGeneration.RecursiveCost.PTP.{Main,Controlled,Asymptotic,Unbalanced,QubitsTight,StandardModel}`
(StandardModel needs ForShor `Model.Standard` built once); then `lake env lean scripts/PTPAxiomCheck.lean`.

## Check a policy

```
python3 scripts/check_banded_policy.py policies/ref_pp_2048.json  --target-width 2048 --widths exact --out ref_pp.record.json
python3 scripts/check_ptp_policy.py    policies/ref_ptp_2048.json --target-width 2048 --widths exact --out ref_ptp.record.json
```
Kernel replay peaks at about 6 GB per module. Use `--cap-gb` to cap it. Negative controls, which
must be rejected, are `policies/threshold_*.json` (except `threshold_scanonly_*`, which pass in
exact mode) and `policies/faulty_width.json`.

## Validation on this commit

See `JMC_VALIDATION.md`.
