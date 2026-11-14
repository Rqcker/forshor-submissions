# Agent guidelines

This repository checks, prices and publishes submissions to the ForShor challenge. A submission is a complete policy: one Lean file that the trusted verifier kernel-checks,
whose circuit the v2 resource engine then prices. This file is for agents (and people) changing the repository or preparing a submission. `README.md` is the overview.

## A submission

- Only `TableGeneration/Submission/Policy.lean` and the optional `TableGeneration/Submission/summary.tex` (a LaTeX summary for people to read, never compiled) may change in a submission PR.
- The policy file declares `namespace PolicySubmission` (PhaseProduct, which enters the PhaseProduct and full order-finding boards) or `namespace PTPPolicySubmission`
  (PhaseTripleProduct). The starter pack on the challenge page prices a policy and generates a file in the right form; `./forshor check` verifies it locally and `./forshor submit` submits it.
- Validity is the bar: the proofs must compile and the checks must pass. A policy does not have to beat anything to be accepted; the leaderboard ranks what is accepted.
- The earlier table-generator format (`GeneratorPolicy`) is not part of the challenge. Its code, catalog and archive remain as records; do not extend them.

## What must never be executed or trusted

- Submitted code and every artifact a PR job produces are untrusted. The trusted verifier is the base branch's, never the PR's; source is filtered by `scripts/ci/policy_source.py`
  before any dependency setup or compilation (the rules are in `docs/POLICY_CI.md`).
- Publication runs trusted `main` code after verification, with the Google credentials. It resolves the PR and files through GitHub and binds evidence to the exact source and author.
  PR jobs restore caches; only trusted `main` writes them. Do not add `pull_request_target` or a step that runs PR code with credentials.

## Rules that keep the live challenge intact

These are properties of how results are identified. Break one and the next submission fails to publish, or builds cold.

1. **The epoch.** A model's id is `sha256(model)[:16]`, and the model contains `engineRevision`, the last commit that changed `evaluation/engine/`, and a fingerprint of the engine's data and
   pricing code. **Any commit that changes any file under `evaluation/engine/`, however small, starts a new epoch**, and the publisher refuses an epoch change. Do not edit that folder in
   a cleanup or a drive-by fix. Check before pushing, from `evaluation/` in the pinned engine environment:
   `python -c 'from export_v2 import epoch, canon; print(canon.epoch_of(epoch.model_of()))'`. While the current epoch is live this must print the same id as before your change.
   A deliberate epoch change is a planned migration with a pause on submissions, not a PR.
2. **The Lean caches.** The Lean build-cache keys hash `TableGeneration/`, `lakefile.lean`, `lake-manifest.json`, `lean-toolchain` and `evaluation/policy-trees.json` (see
   `scripts/ci/policy_environment.py`). A change to any of them, even a comment, empties the cache and a cold build can take hours; run the cache-warming workflow and wait for it
   before accepting submissions. A PR that touches `TableGeneration/Submission/**` also runs the submission verifier on that PR.
3. **The frozen records.** Do not edit `evaluation/export_v2/evidence-archive/` (its envelopes are hash-chained and carried by the live epoch's published data), the
   `submission-results` branch, `TableGeneration/Policies/Accepted`, `TableGeneration/BestKnown.lean`, or `leaderboard/` and `BENCHMARK.md` (apart from clarifying notes).
4. **What the evidence reads.** `PROVENANCE.md` must keep the line ``upstream `main` at `<commit>` ``: the evidence for every submission records that base commit and refuses a tree whose
   base is not the model's (`evaluation/export_v2/evidence.py`, guarded by `evaluation/export_v2/pytests/test_evidence.py`).
5. **The verification identity** (`scripts/ci/verification_identity.py`) fingerprints the verifier, the policy checkers, `scripts/leaderboard.py`, `leaderboard/config.json`,
   `evaluation/policy-trees.json`, `evaluation/intake_v2.py`, `evaluation/export_v2/` and `evaluation/engine/`. Changing them is allowed but means an already verified head is re-verified
   instead of reusing its receipt. Say so in the PR.

## Checks

Use Python 3.12 with `evaluation/engine/requirements.txt` plus pytest. Run what your change touches; CI runs the first five.

```sh
python3 -m unittest scripts.test_contributor_cli                # the CLI
python3 -m unittest discover -s scripts/ci -p 'test_*.py'       # the trusted CI tools and these docs
python3 scripts/test_policy_verifier.py
node scripts/test_report_trust.js
node --test scripts/test_publication.js scripts/test_pr_publication.js scripts/test_policy_publication.js scripts/test_notify_submitters.js scripts/test_submission_citation.js
cd evaluation && python -m pytest export_v2/pytests -q --runslow    # intake, export and evidence (about 4 minutes)
```

Lean needs `lake exe cache get` once on a fresh checkout (Mathlib built from source takes hours); how to build and run the checker by hand is in `docs/CHECKER.md`.
Keep pull requests small, one concern each, and open them as drafts.
