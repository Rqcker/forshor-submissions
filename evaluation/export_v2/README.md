# export_v2: the engine's output as contract-v2 documents

Design and contract: `contracts/v2/DESIGN.md` in `zapatacomputing/forshor-site` (branch `contracts/v2-design`; unmerged).
This package is the Python half of "engine -> contract". It is **not wired into any workflow**.

It produces ledger-level inputs only: the model, rows **without ranks** (only policies with valid evidence), benchmarks, reference curves, panels and evidence.
Ranks, boards, the dashboard and the snapshot id are derived by forshor-site's one shared implementation (`assembleSnapshot`),
so there is no second ranking in Python. `mapping.py` is the twin of forshor-site's `scripts/fixture-v2.ts`; a golden test
checks it reproduces that builder's panels exactly from the same real engine views.

Run from `evaluation/` with the vendored engine's Python environment (`evaluation/engine/requirements.txt`, Python 3.12):

```sh
python -m export_v2 evidence-needed OUT_DIR                      # which planner policies lack valid evidence; writes each as a file
python -m export_v2 run-mirror OUT_DIR --tree PATH               # run that tree's harness on them (needs a BUILT Lean tree)
python -m export_v2 epoch-input OUT.json --publisher-commit SHA --evidence-dir OUT_DIR
python -m export_v2 request OUT.json --policy FILE --evidence-dir DIR --head-sha ... --pull N ...   # one submission
# validate with the code the publisher and browser use (in forshor-site):
npx tsx scripts/publisher/validate-v2.ts OUT.json
```

## Evidence rules

Evidence is for one policy (its digest: mode and bands, not the display title). A harness record counts only if the harness
accepted it, at exact widths, at the model's width, for the same bands, on the exact policy file the envelope names (the record
carries that file's sha256). **Evidence is valid only for the toolchain it ran on, and an epoch fixes the toolchain**: the Lean
version, the STO base and the ForShor pin must equal the model's. Anything else is reported, never silently used. A planner policy without
valid evidence is **not exported** (decision D8: only verified rows are shown); the evidence report counts and names what was left out
(`rowsExcluded`, `policiesWithoutEvidence`).

## What the real data says (2026-11-05)

* The engine's frontier rows (17 PhaseProduct, 9 PhaseTripleProduct, at four deltas) are **104 row-policies but only 6 distinct
  policies** (4 PhaseProduct, 2 PhaseTripleProduct). The 31 seed records are earlier planner outputs, and no existing record satisfies
  the toolchain rule (the demo's mirror records name Lean 4.28.0 and a tree hash; the ForShor-verified seeds ran on 10fe620, not the pin).
* **The interim STO fork was built** at its pins (Lean 4.36.0-rc1, ForShor 6d55ad9; `lake build` of the documented targets, 8406 jobs,
  about 8.5 minutes with the Mathlib cache) and its harnesses were run on the planner policies:

  | Policy | Ladder | Result | Time |
  | --- | --- | --- | --- |
  | PhaseProduct `1bc92ac6` | k = 6, 4, 3 | **accepted** (all stages, both kernel replays) | 138 s |
  | PhaseTripleProduct `627bb6e3` | one band, k = 4 | **accepted** | 103 s |
  | PhaseProduct `80132d41` | k = 8, 4, 3 | **rejected at stage `timeout`**: the evaluation-tier report ran past the harness's 60-minute budget | 3645 s |
  | PhaseProduct `92d5b75d`, `ab119309`; PhaseTripleProduct `48063efa` | all start with k = 8 | **not run** | |

  A timeout is a resource limit, not a mathematical rejection: it says nothing about whether the policy is correct, and it yields no
  evidence. The three not run share the 8-way top band that timed out, and no record in the demo's data has an 8-way first band
  (all accepted ones start at k = 6), so they were not given another hour each on a likely repeat. No root cause is established for the
  slow stage (`scripts/BandedPolicyReport.lean`, run by the interpreter as `lake env lean --run`); that is untested.
* **Consequence:** the policies that verified are the oldest, simplest rows (the earliest PhaseTripleProduct row at all four deltas, and
  the earliest PhaseProduct row at two non-default deltas). The strong current frontier policies are the k = 8 ones, which are the ones
  the harness could not verify. With evidence for 2 of 6 policies, 8 of 104 row-policies are listed (one PhaseTripleProduct row at
  every delta, one PhaseProduct row at two deltas), so the listed baseline is far weaker than the true frontier. Unverified rows are not shown.
  Boards may therefore be empty at launch (the PhaseProduct and full boards have no rows at the default delta).
* **Two bugs the real runs found:** (1) the harnesses name the width-rule field differently (`widths_rule` in check_banded_policy,
  `widths` in check_ptp_policy); the first converter read only the first and refused a genuinely accepted PhaseTripleProduct record.
  The real records are now test fixtures (`pytests/data/real`). (2) After the harness reported its timeout, the `lean --run` process it
  had started kept running at full CPU with no parent; the harness's timeout kills the wrapper but not the interpreter. It also blocked
  the next check, because the harness waits while two or more Lean processes run. It had to be killed by hand.

## Tests

```sh
python -m pytest export_v2/pytests -q                       # no engine needed (mapping, evidence rules, stub harness)
FORSHOR_SITE_DIR=/path/to/forshor-site python -m pytest export_v2/pytests -q --runslow    # prices a real policy, validates in TypeScript
```
The tests live in `pytests/` (no `__init__.py`) on purpose: CI's `python -m unittest discover -s evaluation` runs in the pinned
Qualtran 0.7.0 environment and must not import the vendored engine.

The harness runner (`run_mirror`) was run against a real built tree on 2026-11-05 for the policies in the table above; its tests use
a stub for speed, plus the real records as fixtures.
