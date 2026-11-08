# MERGE-NOTES: swap-002 integration (forshor-submissions)

Branch `submissions/swap002-integration` (local, not pushed). ForShor pin: M = `21e129aeefc0fd1bbb8fa63402f5799af2490788`
(merge commit of PR #79, tree identical to the PR head 2b7aaf7 the Lean mirror was built against).

## Branches merged (all `--no-ff`, no rebase, no squash)
1. base `submissions/negation-option` 56c4710 = B1 (ffae2f3: `negation` key in both generators, PTP peak tier, docs) + B2 (0ed8fbc: Lean
   mirror ported to ForShor's real negation counts) + pin commit (M).
2. `submissions/stage5-negation-pricing` 655c27a (B4), which contains `submissions/policy-to-template-negation` 44b757c (B3):
   merge commit 6bf6612. **No conflicts** (B2 touched Lean/scripts/docs, B3/B4 `evaluation/harness` and `evaluation/engine`; the sets
   are disjoint).
3. Integration commit (default convention flip, regenerated caches/goldens, test updates): see `git log`.

## Default-convention flip (B4's open question 1)
* `cost_conventions.DEFAULT_NEGATE` = `"gidney_circuit"` (= ForShor `oneQubit`), was `"legacy"`. `forshor_verify.REQUIRE_PRINTED_NEGATION` =
  `True`: a printed `policy.json` without `negation` is a stage-"python" error ("build tree older than ForShor eb00c5a"). A submitted
  policy JSON without the key is still `oneQubit`; `scratch` is still `legacy`.
* `ir_v1.lowgate_resources(negation=None)` followed the old default implicitly (hard-wired to scratch); it now follows `DEFAULT_NEGATE`.
* Consequence to know: the harness's build tree `.cache/forshor_latest_build` (ForShor 10fe620) prints no `negation`, so `forshor_verify`
  now refuses it until the tree is re-made at eb00c5a. Docstrings in forshor_verify.py / engine/README.md say so.
* Regenerated: `evaluation/engine/cache/fullalg_*.json` (`python -m site_engine.full_frontier --workers 3`, ~1 min). They equal the export
  branch's caches (E1 7e48afc) in every shared field except the fingerprint (E1's carry extra live-peak fields). Row shifts vs the old cache:
  +4,751,936,280 T for the 157 rows whose run has only the 24,570 template negations at w = 4398 (= E1's -8.6475e9 vs the 32w draft + the
  draft's 13,399,396,920 over legacy), larger for policies with negating tables.
* Goldens, recomputed not pasted: `tests/data/stage5_pricing_golden.json` keeps the legacy part (what `negation="scratch"` must still
  reproduce, sha256 of whole price dicts recorded at 44b757c) and gains `default` T values computed by `tests/negation_arith.py`
  (legacy T + sum over negation sites counted off bloq call graphs of k*(4*Toffoli(12w-28|10w-18) - legacy T at w)). The tests recompute
  the same sum on every run for all tiers (headline, grid leaf, literal Lean with the Cuccaro legacy price, scan IR) and require the engine
  to match. Reference document: oneQubit 3,633,237,524,122.465 vs scratch 3,240,663,026,770.465 (+392,574,497,352), = E1's engine.
* Other pinned-legacy engine tests re-derived the same way (E1's approach): test_balanced_ir, test_site_engine (seam under
  `negate="legacy"` + a divergence test for KMY, step-4 share, headline/grid pins, work-width residual), test_stage12 (KMY lean qubit count
  5292+1 vs 5292+699 legacy; paper policy pins), test_stage13 (`full_board_stage19_pins`, per point).

## B2/B3/B4 interplay
* `scripts/policy_json_to_lean.py`, `ptp_policy_json_to_lean.py` (B2) and `evaluation/harness/policy_to_template.py` (B3) emit the same
  line `  negation := .scratch` and refuse bad values with the same text; asserted by
  `test_the_lean_generators_and_the_template_agree_on_the_negation_line` (engine/tests/test_stage5_negation_pricing.py).
* Golden-hash tests of B1 (`scripts/tests/policy_json_lean_golden.json`), B3 (`tests/data/policy_to_template_golden.json`) and B4
  (`stage5_pricing_golden.json`, legacy part) pass together.

## Places that still mention a stale pin or `32 w`
* `lakefile.lean:36-39` comment: "PIN-PLACES (change to the PR merge commit M ...)" and "head 2b7aaf7" wording (the require line itself is M).
  Not edited: a lakefile edit re-triggers configuration; harmless text.
* `JMC_NOTES.md:15`, `JMC_VALIDATION.md:54-140`, `RECURSIVE_COST_MODEL.md:74,132`, Lean docstrings (`Catalog.lean:13`, `Model.lean:125`,
  `Planner.lean:14`, `Capacity.lean:65`), `scripts/test_cost_model_lean.py`, `scripts/tests/*.lean`: 6d55ad9 / bb2aae0 as the DATED
  regression baseline of the `scratch` method (intended, historical).
* `evaluation/engine/fullalg/l2q_full/cost_conventions.py` (3 places) and its tests: the SUPERSEDED `incrementer` (32 w) option, labelled so.
  `RECURSIVE_COST_MODEL.md:67`, `CompanionCost.lean:182`, `Model.lean:122`: `34w-53 / 32w-22` is the oneQubit negation's TOTAL gate count, not
  the 32 w draft.
* `10fe620` as "the latest ForShor" in `evaluation/engine/site_engine/{fullalg,seed_policies,trust}.py`, `harness/{forshor_verify,submit}.py`,
  `tests/test_stage15.py` and the pinned records under `engine/data/forshor_submitted/*` (records are historical; none carries `negation`).
  A re-run of the harness at eb00c5a would re-pin these.
* NOT touched (still the old Negate model): `engine/paper_model/forshor_qualtran`, `engine/forshor_qualtran`, `engine/tcost` (board Qualtran
  `Negate`), `evaluation/estimator/**`, `bloqs/recursive.py` (Lean-literal single-k tier), live-peak/site-reserve parts of E1 (not vendored).

## Results of the checks (2026-11-07)
* `lake build TableGeneration` + all 185 non-`Policies/Accepted` modules: 0 errors, 206 warning lines (same as B2). It was a cache replay
  (6 s, 0.8 GB): Lean sources are identical to B2's tree (no `.lean`/lakefile/manifest diff vs 56c4710) and the `.lake` is B2's clone with
  the package at M. `lake env lean scripts/PTPAxiomCheck.lean`: 41 theorems, all `[propext, Classical.choice, Quot.sound]` or axiom-free,
  17.5 s, 3.3 GB. `check_mathlib_confinement.py`, `check_ptp_layers.py`: OK.
* engine `pytest tests -q -m "not lean"`: 685 passed, 30 skipped, 3 deselected (15 min); B4's branch: 666 / 30 / 3.
* scripts: test_policy_json_negation 8, test_ptp_peak_qubits 5, test_archive_completeness 11, test_cost_model_lean 10 (via lakeslot, ~8 min
  with the syntax gate), test_submission_syntax_gate 14, test_contributor_cli 20, test_promote_audit 6, test_publish_promotion,
  test_submission_preflight 10, test_verifier_deadline 5, test_verifier_hardening 23, scripts/ci unittest 6, `py_compile forshor`: all OK.
* `evaluation/test_publication_recovery.py` 5 OK. `evaluation/test_estimate.py` cannot import (no vendored Qualtran tree in
  `evaluation/estimator/vendor`); not run, as on B4.

## Not tested
No `forshor_policy_submission` binary exists for this tree, so no end-to-end `forshor_verify` run: the printed-vs-submitted check, the
required key and `price()` on a real eb00c5a document are tested only with stubbed Lean (and with the existing printed documents plus a
`negation` key). `negation: "scratch"` through the real Lean template (B3's line) and the B2 scratch PTP end-to-end were not run.

## Reproduce
```sh
cd ~/Desktop/github_packages/_wt_submissions_int
# Lean (one job at a time, only through the slot script); .lake cloned from _wt_submissions, package Fast_multiplication at M
MODS=($(find TableGeneration -name '*.lean' | grep -v "Policies/Accepted" | sed 's/\.lean$//; s|/|.|g' | sort))
~/Desktop/ForShor_shards/lakeslot.sh lake build TableGeneration "${MODS[@]}"
~/Desktop/ForShor_shards/lakeslot.sh lake env lean scripts/PTPAxiomCheck.lean
python3 -I scripts/check_mathlib_confinement.py; python3 -I scripts/check_ptp_layers.py
# Python
for t in scripts/test_*.py; do python3 -I $t; done       # test_cost_model_lean.py and test_submission_syntax_gate.py call lake:
#   ~/Desktop/ForShor_shards/lakeslot.sh python3 -I scripts/test_cost_model_lean.py   (same for the gate)
python3 -I -m unittest discover -s scripts/ci -p 'test_*.py'
cd evaluation/engine && ~/Desktop/github_packages/sto_site/.venv/bin/python -m pytest tests -q -m "not lean"   # ~18 min
~/Desktop/github_packages/sto_site/.venv/bin/python -m site_engine.full_frontier --workers 3                     # regenerate caches
```
