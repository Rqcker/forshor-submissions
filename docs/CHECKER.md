# The policy checker (for maintainers)

How a complete policy is checked in Lean, what lives where, and how to run the check by hand. For the participant's side see `README.md`; for the CI that runs it see
[`POLICY_CI.md`](POLICY_CI.md); for the base this repository started from and the pins that decide a result see [`../PROVENANCE.md`](../PROVENANCE.md).

## Two checks

- **The policy check** runs in this repository's own Lean tree. It checks the policy itself: that each band's table satisfies its contract, that the thresholds decrease, that the
  recursion evaluates to the right product at every width, that the exact ladder is the least overflow-free one, and that the threshold rule holds on the recursion the policy takes
  from 2048. Its pieces are `TableGeneration/RecursiveCost/{BandedPolicy,PTPPlan,PTPCheck}`, `TableGeneration/RecursiveCost/PlanCore.lean` and the harnesses in `scripts/`.
- **The pinned public Lean library** builds the participant's `TableGeneration/Submission/Policy.lean` (PhaseProduct) or its PhaseTripleProduct counterpart. That check is the one
  that reaches the compiled circuit.

The PhaseTripleProduct proofs (`Statement`, `Proofs/`, `Main`, namespace `Shor.PTP`) live in the pinned library and are imported here, not copied. This repository holds only the
PhaseTripleProduct planner (`TableGeneration/RecursiveCost/PTPPlan/`), the audit indirection (`PTPCheck/AxiomAudit`), the harnesses, `scripts/PTPAxiomCheck.lean` (the library's theorems
the checker instantiates, plus the planner's own) and `scripts/check_ptp_single_source.py` (fails if this repository grows its own PhaseTripleProduct definitions).

## Build

The Lean targets the policy route builds are `sto_build` in `evaluation/policy-trees.json`:

```
lake exe cache get
lake build TableGeneration.RecursiveCost.BandedPolicy \
           TableGeneration.RecursiveCost.BandedPolicy.BridgeClosure \
           TableGeneration.RecursiveCost.BandedPolicy.Plan \
           TableGeneration.RecursiveCost.BandedPolicy.Instances.RefPP \
           TableGeneration.RecursiveCost.PTPPlan.Plan \
           TableGeneration.RecursiveCost.PTPCheck.AxiomAudit
```

`BridgeClosure` imports most of the pinned library. Measured on 2026-11-03 on an M-series Mac: a cold build of the library took about 7 minutes (peak 7.5 GB) and the modules here
about 4.5 minutes more (peak 5.8 GB). CI warms these environments with `policy-build-cache.yml`.

## Check a policy by hand

```
python3 scripts/check_banded_policy.py policies/ref_pp_2048.json  --target-width 2048 --widths exact --out ref_pp.record.json
python3 scripts/check_ptp_policy.py    policies/ref_ptp_2048.json --target-width 2048 --widths exact --out ref_ptp.record.json
```

Each exits 0 when the policy is accepted and 1 when it is rejected, and writes a record that names the stage that failed. The stages (generate Lean, build, kernel check, trusted
kernel replay, axiom and statement audit) are described at the top of each script. Useful options: `--widths scan|exact` (the width rule), `--fast` (skip the interpreted report stage,
which is the slow one for a policy with a large first band), `--cap-gb` (memory cap; kernel replay peaks at about 6 GB per module), `--timeout`, `--workdir` or `--in-place`.

Negative controls, which must be rejected: `policies/threshold_*.json` (except `threshold_scanonly_*`, which pass in exact mode), `policies/faulty_width.json` and
`policies/faulty_point_order.json`. The planner bridge `scripts/plan_from_site.py` generates the seeds under `PlanSeeds/`, which the harness runs; they are not imported by the build.
