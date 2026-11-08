# Validation of this commit (2026-11-03)

Machine: macOS arm64, 24 GB. Base: upstream `361e81e` plus the files in `JMC_NOTES.md`.
Pins: Lean `v4.36.0-rc1`, Mathlib `4dc43842`, ForShor `c0eb474`, all unchanged from upstream.
ForShor was not modified. The ForShor main tested (`4ce58eb`) has the same Lean sources as `c0eb474`.
Peak memory is resident plus compressed memory of the whole process tree (`scripts/memcap.py`).

This package was first validated on upstream `dfe296b` (ForShor `d367fb0`). Since then, ForShor has
changed about 40 Lean files: it added `WidthRule` (default `.scan`) and an exact-width compiler. Every
new module builds against `c0eb474` with **no source edits**.

## Build

| target | result | time | peak |
|---|---|---|---|
| `lake build TableGeneration FastMultiplication.ShorVerification.Submission.Correct` (cold ForShor) | OK, 8427 jobs | 402 s | 7.5 GB |
| `lake build` of all 154 new Lean modules | OK, 8660 jobs | 266 s | 5.8 GB |

## STO CI checks

| check | result |
|---|---|
| `check_mathlib_confinement.py` | 204 Lean files scanned, Mathlib confined to `TableGeneration/RecursiveCost/` |
| `test_submission_syntax_gate.py` | 14 passed, 0 failed (87 s) |
| `test_cost_model_lean.py` | 6 passed, 0 failed (191 s) |
| `test_verifier_hardening.py` | 23 passed |
| `test_submission_preflight.py` | 10 passed |
| `test_archive_completeness.py` | 11 passed |
| `test_verifier_deadline.py` | 5 passed |
| `test_promote_audit.py` | 6 passed |
| `test_publish_promotion.py` | all checks passed |
| `test_report_trust.js` | all checks passed |
| `python3 -m scripts.ci.export_benchmark_config` | rc 0 |

## Policy harnesses (`--widths exact`, kernel replay included)

| policy | expected | verdict | time |
|---|---|---|---|
| `ref_pp_2048.json` | accept | **accepted**. Exact ladder 2048(k6)→364→184→94→49→26, scan ladder 2048→385→140→73→40→23, both identical to the pre-port record | 102 s |
| `ref_ptp_2048.json` | accept | **accepted** | 415 s |
| `threshold_noshrink_pp.json` | reject | **rejected** at `threshold` (band 4, k=2, at width 4 does not shrink) | 61 s |
| `threshold_degenerate_pp.json` | reject | **rejected** at `threshold` (k=6 band at width 4 < k) | 69 s |
| `threshold_declared_pp.json` | reject | **rejected** at `threshold` (declared leaf at 27 where band 2 is chosen) | 68 s |
| `threshold_noshrink_ptp.json` | reject | **rejected** at `threshold` | 376 s |
| `threshold_degenerate_ptp.json` | reject | **rejected** at `threshold` | 765 s |
| `faulty_width.json` (n=20) | reject | **rejected** at `widths` | 52 s |

## Not rerun on this commit

The full per-module kernel replay of all 141 modules (2851 s) and the 31 planner-seed
reconstructions were run on `dfe296b`. Both are recorded in the original package's `CHECKS.md`. On
this commit, the harnesses above replayed the reference and fault instances through the kernel.

## Port to ForShor `bb2aae0` (`qubitCount`), 2026-11-04 (branch `port/forshor-qubitcount`)

ForShor `bb2aae0` renames its library `FastMultiplication` to `ForShor`, moves modules and drops
three names STO used. ForShor was not modified; every change is on the STO side.

**Pin.** `lakefile.lean` and `lake-manifest.json` now pin ForShor to the exact commit
`bb2aae01f3397d2338e53809ff12bc8ba5ad8516`, not `main`, so a later ForShor rename cannot break
the build until the pin is bumped deliberately. Bump it when `qubitCount` merges.

**Edits:**
- `import FastMultiplication.…` becomes `import ForShor.…` in 17 Lean files.
- Moved modules:
  - `PhaseProduct.Compiler.{Widths,Coefficients}` to `PhaseProduct.Proofs.Compiler.*`;
  - `PhaseProduct.Math.{ToomCook,MasterTheorem,Table_Generation.Core.Coverage}` to `PhaseProduct.Proofs.Math.*`;
  - `GateCount.PhaseProduct.Lemmas` to `PhaseProduct.Proofs.GateCount.Lemmas`;
  - `GateCount.Definitions` to `PhaseProduct.Proofs.GateCount.Basic`;
  - `Shared.{GateLaws,Registers}` to `PhaseProduct.Proofs.*`;
  - `Shor.Lowering.LowerGate` to `Compiler.Proofs.SingleTable` (same `Shor.lowerGate` signature).
- Removed upstream:
  - `Shor.phaseLimbWidthOfWidth w k` (`= w / k`) is inlined (`Model.lean`, `AllocationAgreement.lean`,
    `Capacity.lean` simp sets);
  - `Shor.commonNeededWidth_ge_xneed` is reproved locally in `BandedPolicy/Capacity.lean` (8 lines,
    same statement).
- `check_mathlib_confinement.py` now also matches `import ForShor` (it matched only
  `FastMultiplication`, so after the rename ForShor imports outside `TableGeneration/RecursiveCost/`
  would have gone unflagged).
- `scripts/PTPAxiomCheck.lean` imports ForShor's `Submission.Audit` through
  `TableGeneration/RecursiveCost/PTP/AxiomAudit.lean`. Its direct ForShor import under `scripts/`
  already failed the confinement check on `eaded51`, before this port.

| check | result |
|---|---|
| `lake build` of all 205 tracked modules under `TableGeneration/` (+ root) | OK, 8751 jobs. The only warnings are deprecations that were already there. |
| `check_mathlib_confinement.py` | 215 Lean files scanned, Mathlib confined to `TableGeneration/RecursiveCost/` |
| verifier hardening / preflight / archive / deadline / promote audit | 23 / 10 / 11 / 5 / 6 passed |
| `test_publish_promotion.py`, `test_report_trust.js`, `export_benchmark_config` | all passed, rc 0 |
| `test_submission_syntax_gate.py` | passed (88 s) |
| `test_cost_model_lean.py` | passed (185 s) |
| `lake env lean scripts/PTPAxiomCheck.lean` | passed, no errors |

| policy (`--widths exact`) | expected | verdict | time |
|---|---|---|---|
| `ref_pp_2048.json` | accept | **accepted**. Exact ladder 2048(k6)→364→184→94→49→26 and scan ladder 2048→385→140→73→40→23, both identical to before. All audited theorems within {propext, Classical.choice, Quot.sound}. | 100 s |
| `ref_ptp_2048.json` | accept | **accepted** | 413 s |
| `threshold_noshrink_pp.json` | reject | **rejected** at `threshold` (band 4, k = 2, width 4) | 68 s |
| `threshold_degenerate_pp.json` | reject | **rejected** at `threshold` (band 5, k = 6, width 4) | 77 s |
| `threshold_declared_pp.json` | reject | **rejected** at `threshold` (declared leaf at 27 where band 2 is chosen) | 79 s |
| `faulty_width.json` (n = 20) | reject | **rejected** at `widths` | 61 s |
| `threshold_noshrink_ptp.json` | reject | **rejected** at `threshold` (band 3, k = 2, width 4) | 372 s |
| `threshold_degenerate_ptp.json` | reject | **rejected** at `threshold` (band 4, k = 6, width 4) | 758 s |

**Not rerun on the port:** the full per-module kernel replay and the 31 planner seeds (the
harnesses above replayed the reference and fault instances through the kernel). Two
gitignored leftovers from an earlier checker run, `PTP/Instances/PTPSubmitted{,Cert}.lean`,
are not part of the repo and were excluded from the module build.

## Pin bump to ForShor `6d55ad9` (`qubitCount` tip), 2026-11-04 (branch `port/forshor-6d55ad9`)

ForShor `6d55ad9` ("Cleanups and Gauss-Jordan in compiled code") follows `bb2aae0` on `qubitCount`. It
moves the Gauss-Jordan `@[csimp]` lemma for `cramerCoeffFromPts` into `PhaseProduct/Statement.lean`, right
after the definition and ahead of its compiled callers, and reorganises `Reference/` into `Standard/` and
`Balanced/{Main,Statement,Proofs}`. No module or name STO imports was removed, and toolchain, Mathlib and the
other packages are unchanged. **The only STO change is the pin** (`lakefile.lean`, `lake-manifest.json`).

| check | result |
|---|---|
| `lake build` of all 205 modules under `TableGeneration/` (+ root) | OK, 8753 jobs, 589 s, peak 3.7 GB, no source edits |
| `check_mathlib_confinement.py` | 215 Lean files scanned, Mathlib confined to `TableGeneration/RecursiveCost/` |
| verifier hardening / preflight / archive / deadline / promote audit | 23 / 10 / 11 / 5 / 6 passed |
| `test_publish_promotion.py`, `test_report_trust.js`, `export_benchmark_config`, `check_ptp_layers.py` | all passed (22 layers) |
| `test_submission_syntax_gate.py` | 14 passed (88 s) |
| `test_cost_model_lean.py` | 6 passed (185 s) |
| `lake env lean scripts/PTPAxiomCheck.lean` | 34 theorems kernel-checked, axioms within {propext, Classical.choice, Quot.sound} |
| `ref_pp_2048.json` / `ref_ptp_2048.json` | **accepted** / **accepted**. PP exact ladder 2048(k6)→364→184→94→49→26 unchanged |
| 5 threshold faults, `faulty_width.json` | all **rejected**, at `threshold` (5) and `widths` (1) |

**ForShor-side check of `6d55ad9`** (its own `lake exe forshor_policy_submission --balanced`, memcap 12 GB):

| policy | `bb2aae0` | `6d55ad9` |
|---|---|---|
| STO reference PP (k 6/3/2/2/2) | killed above 10 GB | passes, peak 0.80 GB, evaluation tier 4.9 s |
| seed `Scc866e679f67` (k 5/5/3) | evaluation tier 405 s | 3.5 s, peak 0.79 GB |
| shipped template | passes | passes, 0.77 GB |

Single-run T priced on the printed documents (site pricer) is identical to `main` and `bb2aae0` for all
three: 3,240,663,026,770 / 1,933,981,965,831 / 1,650,011,804,372. The documents for the shipped template
and `Scc866e679f67` are identical in every top-level field to the `bb2aae0` ones, for both executables.
Not rerun: full per-module kernel replay, 31 planner seeds.

## PhaseTripleProduct moved to ForShor, pin ForShor `52a2c9e`, 2026-11-05 (branch `ptp-canonical-forshor`)

ForShor `main` `52a2c9e` is `qubitCount` (ForShor PR #75, the same Lean tree as `6d55ad9`) plus
PhaseTripleProduct (PR #76): the PTP proofs now live in ForShor (`Implementation/PhaseTripleProduct`,
`Statement.lean` / `Proofs/` / `Main.lean`, namespace `Shor.PTP`, on ForShor's own table language). This
fork no longer carries a copy: `TableGeneration/RecursiveCost/PTP/` (25 proof modules, the fault and
recursion witnesses, the standard-model specialisation, the umbrella) is deleted, and what remains is the
planner and its 31 seeds (`PTPAxiomCheck`-guarded, `TableGeneration/RecursiveCost/PTPPlan/`), the
policy-checker harness, and two re-export modules in `PTPCheck/`. Only ForShor's revision moved at the pin
(Mathlib `4dc43842` and the toolchain are unchanged).

**Edits.** Planner and seeds: imports, namespace (`TableGeneration.PTP` -> `TableGeneration.PTPPlan`),
`open _root_.Operations Shor.PTP`. Two additions, both in this fork's files: `deriving instance DecidableEq
for Operations.valid_ops` (ForShor's table language has none; the planner and the seeds compare tables),
and `sortedDesc_eq_mirror` (ForShor's `Shor.PTP.sortedDesc` is the same definition as the mirror's, which
`PlanCore` reasons about). The planner's theorems are unchanged in meaning. Harness: generated modules now
live in `PTPCheck/` against ForShor's `Shor.PTP`; the audit's statement pins changed only by namespace
(`TableGeneration.PTP.` -> `Shor.PTP.`, since ForShor's statements are verbatim copies) and the audit
matched them on the reference policy. `check_ptp_layers.py` (the STO PTP layer order, which no longer
exists) is replaced by `check_ptp_single_source.py`. `scripts/` may not import ForShor directly (the
confinement check), so `PTPCheck/{AxiomAudit,ForShorPTP}.lean` re-export it.

| check | result |
|---|---|
| ForShor PTP modules at the new pin (`lake build` of `Main` and four `Proofs` modules) | OK, 6549 jobs, 94 s, peak 3.3 GB |
| `lake build` of all 179 tracked fork modules (205 before; 28 PTP modules deleted, 2 added) | OK, 8695 jobs, peak 3.6 GB. 60 warning lines, all deprecations in files this branch did not change (`PlanCore` 13, `BandedPolicy/*`, `Companion*`, ...); none from `PTPPlan/`, `PTPCheck/` or `scripts/` |
| planner seeds (31 modules, incl. the `_unsorted`, `_foreignidx`, `_foreigntab` negative controls) | all build; each control's `reconstruction_fails` is decided by the kernel |
| `lake env lean scripts/PTPAxiomCheck.lean` | 33 theorems (24 ForShor, 9 planner), all within {propext, Classical.choice, Quot.sound} |
| `check_ptp_single_source.py` | ok; fails on each of a planted PTP definition, a `sorry` in `PTPPlan/`, and a re-created `PTP/` folder |
| `check_mathlib_confinement.py` | 190 Lean files scanned, Mathlib confined to `TableGeneration/RecursiveCost/` |
| verifier hardening / preflight / archive / deadline / promote audit | 23 / 10 / 11 / 5 / 6 passed |
| `test_publish_promotion.py`, `test_report_trust.js`, `export_benchmark_config` | all passed, rc 0 |
| `test_submission_syntax_gate.py` / `test_cost_model_lean.py` | 14 passed / 6 passed |

| policy (`--widths exact`) | expected | verdict |
|---|---|---|
| `ref_ptp_2048.json` | accept | **accepted**. Both generated modules replayed through the trusted kernel (`PTPSubmitted`: 17 decls; `PTPSubmittedCert`: 24 decls, axioms within {propext, Classical.choice, Quot.sound}); 19 audited theorems matched their statement pins. Kernel exact ladder = Python ladder = 2048(k6)→375(k2)→189(k2)→96(k2)→50. Report stage 432 s |
| `threshold_noshrink_ptp.json` | reject | **rejected** at `threshold` (band 3, k = 2, width 4). Report stage 426 s |
| `threshold_degenerate_ptp.json` | reject | **rejected** at `threshold` (band 4, k = 6, width 4). Report stage 884 s |
| `threshold_scanonly_ptp.json` | accept | **accepted** (both replays passed; the scan chain does not contract, recorded as advisory) |
| `ref_pp_2048.json` | accept | **accepted**. PP exact ladder 2048(k6)→364→184→94→49→26 unchanged |
| `faulty_width.json` (n = 20) | reject | **rejected** at `widths` |

**Not rerun on this change:** `check_ptp_policy.py --full-replay` (the trusted replay of every ForShor
PhaseTripleProduct module from this fork; ForShor's own trusted replay of all 28 of them, 2,141
declarations, was run on the ForShor side for PR #76); the trusted replay of the 31 planner seeds
(`plan_from_site.py --replay`; the seeds were built, which kernel-checks them); the PP threshold-fault
policies (`threshold_*_pp.json`) and `faulty_point_order.json`. The 31-seed site bridge
`plan_from_site.py` was edited (paths and namespaces) but not run end to end.

## Pin bump to ForShor `21e129a` (PR #80 merged; contains #79), 2026-11-07 (branch `port/forshor-21e129a`)

ForShor `main` `21e129a` is the merge of PR #80 on top of the merge of PR #79 (`eb00c5a`). Between `52a2c9e` and `eb00c5a` ForShor
**changed what a negation costs**: `Shor.negateResources` is `x = negateXGates w`, `cnot = negateCnotGates w`,
`toffoli = negateToffoliGates w`, `cleanAnc = 1` (Toffoli `12 w - 28` / `10 w - 18`, CNOT `17 w - 22` / `17 w - 5`, X `5 w - 3` / `5 w + 1` for `w` even / odd,
exact for `w >= 7`; the earlier "32 n" was the post's total of all gates, not of Toffolis), and the method is a policy field
(`NegationMethod.oneQubit`, the default, or `scratch`, the pre-swap construction). PR #80 adds the board PhaseProduct policies and their
kernel-checked peaks and changes no resource-model file; toolchain, Mathlib and the other packages are unchanged.

**The mirror's port is not written here.** It is the one built for `forshor-submissions` (branch `submissions/swap002-integration`, commit `0ed8fbc` and
its generators): the files below are that branch's versions, laid over this fork unchanged because the fork's copies were identical to that
branch's base. The PhaseTripleProduct proofs are not ported: this fork imports ForShor's `Shor.PTP`, which already carries the corrected counts.

* `TableGeneration/RecursiveCost/{CompanionCost,Model,Catalog,ChampionLowering,Correctness,Planner}.lean` and `BandedPolicy/{Bridge,Defs,Instances/FaultChallenge}.lean`:
  the cost mirror under a negation method (`negateResourcesAtWidth w m`, `phaseArithmeticOpCostFor m`), `oneQubit` by default.
* `scripts/policy_json_to_lean.py`: the optional `"negation"` key (`oneQubit` default, `scratch`); `scripts/test_policy_json_negation.py` and its golden.
* `scripts/tests/companion-cost-model.tsv` (and the scratch variant), `RecursiveCostOracle.lean`, `ChampionGateCountDifferential.lean`, `test_cost_model_lean.py`, `RECURSIVE_COST_MODEL.md`.

The PhaseTripleProduct generator and harness scripts (`ptp_policy_json_to_lean.py`, `check_ptp_policy.py`, `PTPPolicyReport.lean`, `ptp_exact_width.py`) differ in this fork (they were
re-pointed at `Shor.PTP`), so W1's changes were applied to them as three-way patches (clean); in `PTPAxiomCheck.lean` the only conflict was W1's list of axiom checks for
the local PhaseTripleProduct suite, which this fork does not have, so the fork's own file is kept. W1's change to the mirror's `BandedPolicy` (it gained a `negation` field) broke
`PTPPolicyReport.lean` here until that patch was applied: every PhaseTripleProduct harness run failed at the report stage with "Insufficient number of fields" before it.
`scripts/tests/policy_json_lean_golden.json`: the PhaseProduct digests are W1's; the four PhaseTripleProduct policies' digests were re-recorded from this fork's generator as it was
before the key (`origin/main`), since its output differs from the integration branch's.

| check | result |
|---|---|
| `lake build TableGeneration` | OK, 6673 jobs |
| `lake build` of all 182 modules under `TableGeneration/` by name (the PhaseTripleProduct planner and its 31 seeds included) | OK, 8756 jobs |
| `test_cost_model_lean.py` (with W1's negation tests) | 10 passed |
| `test_policy_json_negation.py` | 8 passed (the PhaseTripleProduct golden digests re-recorded from this fork's generator before the key) |
| `check_mathlib_confinement.py` / `check_ptp_single_source.py` | 190 files scanned, confined / ok |
| `lake env lean scripts/PTPAxiomCheck.lean` | the planner's theorems and ForShor's, all within {propext, Quot.sound, Classical.choice} |

**Policy harnesses at this pin (`--widths exact`, 2026-11-07/08).** Run on the 15 policies the results seed lists, from this tree (and an APFS clone for the two long PhaseTripleProduct
policies), ForShor `21e129a`, Lean 4.36.0-rc1: 13 accepted, one timeout, one not finished. The 13: the five PhaseProduct baselines (k = 2 to 6), the PhaseProduct planner policies with first bands
k = 6 (54 s) and k = 8 (`80132d41`, `ab119309`, `92d5b75d`: about 2.4 h each in the report stage, run with a 6 h stage limit), the three PhaseTripleProduct baselines k = 3, 4, 5 and the
PhaseTripleProduct planner policy with one k = 4 band. Not accepted: the PhaseTripleProduct planner policy `48063efa` (first band k = 8): its report stage was killed at a 12 h limit; the PhaseTripleProduct k = 6
baseline had not finished. The envelopes and their provenance are archived in `forshor-submissions` (`evaluation/export_v2/evidence-archive/494185cf666f9752/`, branch `export/v2-seed-m2-evidence`).
Two false rejections on the way, both from this fork and now fixed: `PTP.Census` unbuilt (build the PhaseTripleProduct modules by name), and `PTPPolicyReport.lean` building a `BandedPolicy` without the new
`negation` field.

**Last two policies, 2026-11-08, `check_ptp_policy.py --fast`.** The default path's report stage (`lean --run scripts/PTPPolicyReport.lean`, the interpreter) never finished for the PhaseTripleProduct planner policy `48063efa` (bands k = 8 and k = 4; killed at 12 h) or the k = 6 baseline `c733baf3` (6 h and running when stopped). Profiling the k = 5 baseline showed the interpreter takes 99 s just for `ladderX`, while the certificate that follows recomputes the same ladder in the kernel in seconds (`ladder_exact := by decide +kernel`). The new `--fast` flag skips the report: the exact ladder comes from `ptp_exact_width` (Python, milliseconds) and the kernel proves it, so a wrong ladder fails the certificate. Both policies were **accepted** (data about 10 s, certificate 14 to 23 s, two kernel replays 10 to 27 s each; the same 19 audited headline theorems and axioms). On the k = 5 baseline, default and fast paths agree on every audited statement and on `ptp_peak_qubits` (39 s against about 820 s). Not produced in fast mode: the report's census and formula numbers (diagnostics) and the specific threshold-rejection reason. With these two, all 15 policies of the results seed have accepted evidence. Branch `harness/ptp-fast-report`.

Not rerun on this pin: `ref_ptp_2048.json` / `ref_pp_2048.json`, the threshold fault policies and `faulty_width.json` through the harnesses, the verifier hardening / preflight / archive / deadline /
promote tests, `test_submission_syntax_gate.py`, and the full-replay of every ForShor PhaseTripleProduct module from this fork.
