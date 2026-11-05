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

## Evidence rules

Evidence is for one policy (its digest: mode and bands, not the display title). A harness record counts only if the harness
accepted it, at exact widths, at the model's width, for the same bands, on the exact policy file the envelope names (the record
carries that file's sha256). **Evidence is valid only for the toolchain it ran on, and an epoch fixes the toolchain**: the Lean
version, the STO base and the ForShor pin must equal the model's. Anything else is reported, never silently used. A row without
valid evidence is still listed, unranked.

## What the real data says (2026-11-05)

* The engine's frontier rows (17 PhaseProduct, 9 PhaseTripleProduct, at four deltas) are **104 row-policies but only 6 distinct
  policies** (4 PhaseProduct, 2 PhaseTripleProduct). Policy-level evidence needs 6 harness runs, not 104.
* **None of them has valid evidence for this epoch.** The 31 seed records are earlier planner outputs, re-planned over only their
  own tables, so only 4 of the 104 row-policies even match a seed; and no existing record satisfies the toolchain rule: the demo's
  mirror records name Lean 4.28.0 and a tree hash, and the only ForShor-verified seeds ran on ForShor 10fe620, not this epoch's
  pin 6d55ad9. Under the ranking rule every planner row is therefore currently unranked.
* The only built Lean trees on the machine that ran the demo are at Lean 4.28 / ForShor fadcf92. The interim STO fork
  (`~/Desktop/ShorTableOptimization_temp-main`, Lean 4.36.0-rc1, ForShor 6d55ad9) is not built. Producing evidence means building it
  (about 7 min cold with the Mathlib cache and ~7.5 GB peak per the fork's notes) and then running `run-mirror` (PhaseProduct about
  100 s each, PhaseTripleProduct about 7 to 13 minutes each, up to ~6 GB).

## Tests

```sh
python -m pytest export_v2/pytests -q                       # no engine needed (mapping, evidence rules, stub harness)
FORSHOR_SITE_DIR=/path/to/forshor-site python -m pytest export_v2/pytests -q --runslow    # prices a real policy, validates in TypeScript
```
The tests live in `pytests/` (no `__init__.py`) on purpose: CI's `python -m unittest discover -s evaluation` runs in the pinned
Qualtran 0.7.0 environment and must not import the vendored engine.

The harness runner is verified only against a stub script: it has **not** been run against a real Lean tree.
