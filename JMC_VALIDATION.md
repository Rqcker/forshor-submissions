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
