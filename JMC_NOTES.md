# Interim fork: banded PP/PTP policies, planner and ForShor bridge

This is an interim fork of [VerifiedQC/ShorTableOptimization](https://github.com/VerifiedQC/ShorTableOptimization).
It is a proposed extension that implements the policy-submission format on the STO side. It is
**not merged into or endorsed by upstream**, and it is shared so collaborators have a working copy
while that is discussed. If upstream adopts these changes, use upstream instead.

- **Base:** upstream `main` at `361e81e` (2026-11-03, "Update ForShor to c0eb474").
  Toolchain and Mathlib are upstream's, unchanged: `leanprover/lean4:v4.36.0-rc1`, Mathlib `4dc43842`.
- **ForShor pin:** this fork pins ForShor (`Fast_multiplication`) to the exact commit `21e129a`, ForShor's
  `main` after PR #80 (board PhaseProduct policies and their kernel-checked peaks) was merged; it contains PR #79
  (`eb00c5a`: modular swaps 001 and 002, the negation charged as a complement plus a one-ancilla increment with
  ForShor's explicit gate counts, and the negation method a policy option) and the earlier merges of `qubitCount`
  (PR #75, the same Lean tree as `qubitCount` `6d55ad9`) and PhaseTripleProduct (PR #76), not a moving branch. `qubitCount` renamed the library `FastMultiplication` to
  `ForShor`, so a few STO files were ported: import paths, two removed helper names re-stated locally,
  and the confinement check. ForShor itself is not modified.
- **PhaseTripleProduct lives in ForShor.** The PTP proofs (`Statement.lean`, `Proofs/`, `Main.lean`, in
  `Implementation/PhaseTripleProduct`, namespace `Shor.PTP`) are ForShor's and are imported here, not
  copied. This fork holds only the PTP planner (`TableGeneration/RecursiveCost/PTPPlan/`), its 31 seeds,
  and the policy-checker harness (`scripts/check_ptp_policy.py` and friends).
- **Apart from that port, the change adds files only.** It adds files under `TableGeneration/RecursiveCost/{BandedPolicy,PTPPlan,PTPCheck}/`,
  `TableGeneration/RecursiveCost/PlanCore.lean`, `scripts/` and `policies/`, plus this file. The only upstream files it touches are `README.md` (a pointer at the top)
  and `.gitignore` (two lines for harness scratch modules). The umbrella `TableGeneration.lean` is unchanged,
  so `lake build TableGeneration` and upstream CI behave as on upstream. ForShor is not modified.

## What it adds

- **Lean.** It adds banded (multi-table, recursive) PhaseProduct and PhaseTripleProduct policies
  built from STO tables. Each policy has a decidable check, and an accepted check implies
  correctness at every width. A threshold acceptance rule rejects any band that does not shrink
  the width (`BandedPolicy/Threshold.lean`; for PTP, ForShor's `Proofs/Threshold.lean`). Theorems at the
  target `n = 2048` are exact-width correctness and the exact ladder; for PTP they also cover the
  gate-level recursion (ForShor's `Main.lean` and `Proofs/`, instantiated by the generated certificate).
  A planner (`PlanCore`, `BandedPolicy/Plan.lean`, `PTPPlan/Plan.lean`) is included with 31 reconstructed
  seeds per mode (`*/PlanSeeds/`). A bridge to ForShor's policy submission (`BandedPolicy/Bridge*.lean`)
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
           TableGeneration.RecursiveCost.PTPPlan.Plan \
           TableGeneration.RecursiveCost.PTPCheck.AxiomAudit
```
`BridgeClosure` imports most of ForShor. A cold ForShor build took about 7 min on an M-series Mac
(peak 7.5 GB), and the new modules took about 4.5 min more (peak 5.8 GB).

## PhaseTripleProduct moved to ForShor (2026-11-05)
The PTP proofs that this fork used to carry (`TableGeneration/RecursiveCost/PTP/`: 25 proof modules, the
fault and recursion witnesses, the standard-model specialisation, the umbrella) were ported into ForShor
in the Statement / Proofs / Main layout (ForShor PR #76) and are deleted here. Theorem names are the same
(`Shor.PTP.*`). What remains here: `PTPPlan/` (the planner, `plan_check` ... `plan_ne_of_foreign`, and 31
seeds with their negative controls), `PTPCheck/` (the `AxiomAudit` indirection; the checker writes its
generated `PTPSubmitted*.lean` here, git-ignored), the policy-checker harness, `scripts/PTPAxiomCheck.lean`
(the ForShor theorems this fork instantiates plus the planner's own) and `scripts/check_ptp_single_source.py`
(fails if the fork grows its own PTP definitions). Check: `lake env lean scripts/PTPAxiomCheck.lean` and `python3 scripts/check_ptp_single_source.py`.

## Check a policy

```
python3 scripts/check_banded_policy.py policies/ref_pp_2048.json  --target-width 2048 --widths exact --out ref_pp.record.json
python3 scripts/check_ptp_policy.py    policies/ref_ptp_2048.json --target-width 2048 --widths exact --out ref_ptp.record.json
```
Kernel replay peaks at about 6 GB per module. Use `--cap-gb` to cap it. Negative controls, which
must be rejected, are `policies/threshold_*.json` (except `threshold_scanonly_*`, which pass in
exact mode) and `policies/faulty_width.json`.
