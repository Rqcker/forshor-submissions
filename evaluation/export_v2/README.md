# export_v2: the engine's output as contract-v2 documents

Design and contract: `contracts/v2/DESIGN.md` in `zapatacomputing/forshor-site` (branch `contracts/v2-design`; unmerged).
This package is the Python half of "engine -> contract". It is **not wired into any workflow**.

It produces ledger-level inputs only: the model, rows **without ranks**, benchmarks, reference curves, panels and evidence.
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

## Negation: what the epoch field `negation` means

The epoch model carries `negation: "incrementer"` (no contract change). That label names ForShor's `NegationMethod`, not the engine's
`ConventionSpec.negate` option, and the two vocabularies differ:

| Epoch `negation` | ForShor method | Engine `negate` | What a w-bit negation is charged |
| --- | --- | --- | --- |
| `incrementer` | `oneQubit` | `gidney_circuit` (the default) | complement plus Gidney's one-clean-ancilla increment: Toffoli 12w-28 (even w) / 10w-18 (odd w), CNOT 17w-22 / 17w-5, X 5w-3 / 5w+1 (exact for w >= 7, upper bounds below); ONE clean qubit |
| `scratch` | `scratch` | `legacy` | a w-bit constant-one register plus a Cuccaro adder (Toffoli 2w-3, w+1 clean qubits) |
| none | none | `incrementer` | SUPERSEDED first draft of PR #79 (flat 32 w Toffoli, no CNOT). Selectable, never the default, and it cannot start an epoch (`epoch.negation_label` refuses it) |

At w = 8 / 9 / 2048 the first row gives X / CNOT / Toffoli of 37 / 114 / 68, 46 / 148 / 72 and 10237 / 34794 / 24548, and `scratch` at 2048 gives
6140 / 10233 / 4093 with 2049 clean qubits: ForShor's `negateChargeAt` examples (`Emit/Json/Common.lean`), asserted in
`engine/tests/test_swap001.py`. The mapping lives in `epoch.NEGATION_OF`. The ForShor pin is the merge commit of PR #79, written `eb00c5adc72832469a5f223fbec6c8b75745d2dd` in
text until it exists.

The qubit text of the full board's peak (`mapping.peak_label`, `mapping.peak_rule`) is a function of the per-row flag `leanPeak` (default False:
"Lean has not evaluated it on this policy"). It is set per row from `epoch.LEAN_PEAK` (board, row id -> the kernel-checked qubit number): `build_epoch_input` refuses to export a row whose Q
differs from that number. The table holds the six PhaseTripleProduct board rows (ForShor `Peak.Board.Row<id>.peak_oneQubit_exact`: 6405, 7095, 6261, 6342,
6573, 6828); on a PhaseTripleProduct panel the flag replaces the engine's "formula, not Lean-proved" qubit label and rule (`mapping.PTP_LEAN_LABEL`,
`mapping.ptp_lean_rule`: the Q equals ForShor's upper bound `ptpPeakQubits`, a bound on an existential circuit). The PhaseProduct rows have no kernel numeral yet
(`LEAN_PEAK["pp"]` is empty); to flip one, add (its row id -> the kernel's total qubits for that policy) and re-export.

## Evidence rules

Evidence is for one policy (its digest: mode and bands, not the display title). A harness record counts only if the harness
accepted it, at exact widths, at the model's width, for the same bands, on the exact policy file the envelope names (the record
carries that file's sha256). **Evidence is valid only for the toolchain it ran on, and an epoch fixes the toolchain**: the Lean
version, the STO base and the ForShor pin must equal the model's. Anything else is reported, never silently used. A planner policy without
valid evidence is **still exported** (decision D9, which superseded D8) with `stoMirror: null`, and the page marks it unchecked; a
*submission* without evidence is still refused. The evidence report counts them (`rowsUnchecked`, `policiesWithoutEvidence`). A policy
that does have evidence is exported at every delta, priced there, even where the planner picked another policy (`rowsAdded`): evidence is
about the policy, and only the rotation-synthesis price depends on delta.

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
  the harness could not verify. With evidence for 2 of 6 policies, 6 of 104 planner rows have evidence (one PhaseTripleProduct row at
  every delta, one PhaseProduct row at 1e-4 and 1e-6), so the checked policies alone are far weaker than the true frontier. D9 lists the unchecked ones too, marked as such.
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
