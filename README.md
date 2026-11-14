# forshor-submissions

Where submissions to the ForShor challenge are checked, priced and published. A participant submits a complete **policy**: a recursion that splits a large multiplication into
smaller ones, with the tables and thresholds that say how. This repository proves the policy correct in Lean, prices the circuit it describes, and publishes its place on the
leaderboards of the challenge page. Website: https://github.com/zapatacomputing/forshor-site. Public participation: [MIS-23](https://linear.app/zapata-quantum/issue/MIS-23/public-submissions).

There are two challenges: **PhaseProduct** (scored on one phase product, or on full order finding) and **PhaseTripleProduct**. The challenge page says what is scored and why.

## Submit with the CLI

With [GitHub CLI](https://cli.github.com/), Git, and Python 3.12 or newer installed:

```sh
git clone https://github.com/zapatacomputing/forshor-submissions.git
cd forshor-submissions
./forshor setup

# Your submission is one Lean file, a policy. The starter pack prices and
# generates it (see the challenge page); put it in:
# TableGeneration/Submission/Policy.lean
./forshor check
./forshor submit
```

The first setup prepares pinned Lean and prebuilt dependencies, then warms the trusted checker; it can take several minutes. `check` runs full local verification over your
current edits. `submit` checks freshness, creates or updates a PR, and follows that exact submission through hosted Lean verification, resource estimation, and publication. It
prints the accepted-source and dashboard links. Refresh the website after publication; acceptance does not guarantee a new winner.

No website account or API key is needed to submit. After the repository becomes public, `submit` creates or reuses your GitHub fork and opens a PR from there; you do not need
Write access to the company repository. The CLI handles GitHub sign-in when needed. While the repository remains private, existing authorized staging contributors retain their
current submission route and staging retains its separate password login. See [CLI behavior and recovery](docs/CLI.md) or `./forshor --help`.

## What you submit

A pull request to **main** changes only `TableGeneration/Submission/Policy.lean` and, optionally, a LaTeX summary of it in `TableGeneration/Submission/summary.tex` (a summary alone
is not a submission, and the file is never compiled). The policy file declares `namespace PolicySubmission` for PhaseProduct or `namespace PTPPolicySubmission` for
PhaseTripleProduct; the form of the policy depends on the challenge, and the starter pack on the challenge page generates a file in the right form.

Source is filtered before anything is compiled: no build-time commands, metaprogramming, new axioms, native proof shortcuts, compiler overrides or extra imports. The full list and
the rest of the policy route's rules are in [docs/POLICY_CI.md](docs/POLICY_CI.md).

## How a submission is checked and published

1. **Policy submission** runs the trusted base verifier. It prepares the pinned public Lean library and this repository's own tree, kernel-checks the submitted file, checks exact widths and saves `verification.json`. It has no publication credentials.
2. **Publish Verified Results** runs trusted main code after verification. It resolves the PR and files through GitHub, binds evidence to the exact source and author, then calls the policy intake's publish-from-verification path.
3. The intake revalidates the evidence, archives the policy and its verification under `submission-results/results/policy-submissions/<head>`, and runs the v2 resource engine (vendored Qualtran **0.8.0.dev0**, `evaluation/engine/requirements.txt`, in its own Python environment).
4. A short-lived Google identity calls the IAM-protected v2 publisher. It writes immutable snapshots and the private ledger before atomically advancing the dashboard pointer.
5. The website reads those shared JSON results; publishing data does not redeploy it. The same PR receives **Estimate resources with Qualtran and publish results** and closes without merging only after successful publication.

Trusted main warms separate Lean and Python caches; PR jobs only restore them. Policies are checked against the active model, the **epoch**: a change under `evaluation/engine/` starts a
new epoch, which is a planned migration, not a routine edit (see [AGENTS.md](AGENTS.md)).

## Reference

| | |
| --- | --- |
| [docs/CLI.md](docs/CLI.md) | the contributor CLI: commands, local checking, forks, publication and recovery, state |
| [docs/POLICY_CI.md](docs/POLICY_CI.md) | the policy route's CI: environments and limits, source restrictions, trusted publication, the optional summary |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | queues, job limits, receipts and how to recover a failed publication |
| [docs/PUBLIC_PARTICIPATION.md](docs/PUBLIC_PARTICIPATION.md) | repository protections, limits, fork behavior and the release checklist |
| [docs/CHECKER.md](docs/CHECKER.md) | how the policy check works and how to run it by hand |
| [PROVENANCE.md](PROVENANCE.md) | the base this repository started from and where the pins that decide a result live |
| [BENCHMARK.md](BENCHMARK.md), `leaderboard/` | the scoring configuration read by the pricing engine |
| [evaluation/INTAKE_FOR_CI.md](evaluation/INTAKE_FOR_CI.md) | the intake and publish commands |

## Layout

```
forshor                  the contributor CLI (Python 3.12, no dependencies)
TableGeneration/         the Lean tree the policy check runs in; Submission/Policy.lean is the one file a submission changes
scripts/                 the verifier, the policy checkers and their harness; ci/ holds the trusted CI tools
evaluation/              intake_v2.py and publish_v2.py (the policy route's intake), export_v2/ (evidence and the epoch), engine/ (the pricing engine),
                         starter/ (the participant starter pack), harness/, policy-trees.json (the Lean trees a submission is checked in)
leaderboard/, policies/  the scoring configuration, and policy fixtures (negative controls included)
docs/                    the documents above
.github/workflows/       the hosted verification, publication and cache jobs
```

The earlier table-generator format is not part of the challenge. Its code, accepted catalog and archive are still here as records. License: Apache 2.0 (see `LICENSE`).
