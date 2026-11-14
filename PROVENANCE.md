# Provenance

This repository began as an interim fork of the original table repository and keeps its history. The checker's Lean tree is this repository itself.

- **Base:** upstream `main` at `361e81e` (2026-11-03, "Update ForShor to c0eb474"). The Lean toolchain and Mathlib are the base's, unchanged.
- **What decides a result** is pinned in `lean-toolchain`, `lake-manifest.json` (the Lean library the checker builds), `evaluation/policy-trees.json` (the trees a submission is checked in) and the epoch's model (`evaluation/export_v2/epoch.py`), not in this note.
- **Read by code.** The evidence for a submission records the base commit from the line above (`evaluation/export_v2/evidence.py`, `tree_pins`), and refuses a tree whose base is not the epoch's. Keep that line's wording exactly:
  ``upstream `main` at `<commit>` ``. `evaluation/export_v2/pytests/test_evidence.py` fails if this file stops satisfying it.

How the checker works and how to run it: [`docs/CHECKER.md`](docs/CHECKER.md).
