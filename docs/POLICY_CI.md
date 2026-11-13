# Policy submission CI

The existing verification and trusted publication jobs serve both submission formats. `PolicySubmission` and `PTPPolicySubmission` select the complete-policy route; other files retain the older table-generator route.

Verification uses trusted base-branch code and full Git history. It builds the public ForShor pin and the in-repository STO targets in `evaluation/policy-trees.json`, then calls the existing verifier with `FORSHOR_TREE`, `STO_TREE`, `ENGINE_PYTHON`, and the GitHub PR author's login. Only the submitted Lean file reaches ForShor's disposable verification tree. No Google publication identity is available in this job.

## Environments and limits

`Warm policy verification environments` builds on trusted `main`, then saves separate ForShor, STO, and Python caches. PR jobs restore only; they never save these caches. The v2 Python environment is separate from the legacy estimator, and uses `evaluation/engine/requirements.txt` with vendored Qualtran. The publisher checkout must include full history so `engineRevision` is the actual last engine change, rather than the shallow checkout's tip.

`POLICY_RUNNER` may name a provisioned Linux runner label. The default is the existing `ubuntu-24.04` private runner. This has 8 GB physical RAM; on disposable GitHub-hosted Linux machines we reclaim unused SDK space, add 8 GiB swap, and use `LEAN_NUM_THREADS=1`. **This does not satisfy a 16 GB physical RAM guarantee.** Hosted measurements must determine whether it is sufficient for the tested policies; larger policies may need a larger runner. Never perform that machine cleanup on a custom runner.

Bootstrap explicitly fetches the compiled `Mathlib` root. The cache tool does not traverse downstream ForShor/STO roots; requesting those roots silently omits the Mathlib artifacts and causes a source build. It suppresses the automatic whole-library fetch during Lake dependency updates. The two trusted trees share dependency directories only when their manifests pin the same Git source and revision. Relative links preserve this layout in the separate caches. On Linux, disposable verification trees copy project sources and their own build outputs while sharing the trusted job's dependency directory; they do not copy Mathlib again. Those trees and PR jobs never save dependency caches. Bootstrap stops its own build process if free disk falls below 3 GiB, retaining stage timings and available disk space before the runner's log writer fails.

The verification job is bounded to 30 minutes in total, including setup, ForShor, and the STO mirror. The mirror also has a 10-minute overall limit. `POLICY_STAGE_TIMEOUT=900` bounds individual STO replay stages; it is not a separate ForShor deadline. The outer job limit covers the ForShor harness as well. The trusted Qualtran/archive/publication job is bounded to 20 minutes. A resource timeout or exhausted memory is an infrastructure limit, not a mathematical verdict. Timings are retained in the `policy-bootstrap` artifact. The large disposable Lean tree is excluded from result artifacts; `verification.json`, identity, outcomes, and logs are retained.

## Source restrictions before compilation

The trusted `scripts/ci/policy_source.py` filter runs before dependency setup or
verification reuse, and local `./forshor check` runs it from the fetched company
checker. It applies the existing site-harness restrictions missing from the
upstream shell gate: no build-time commands, effects, metaprogramming, unproved
axioms, native proof shortcuts, compiler overrides, or extra imports. It uses
the existing hardened comment lexer. All 14 archived policy files and shipped
starter Lean templates passed the filter during MIS-23.

A source refusal updates the trusted preflight result, so the PR receives a
rejection with its reason and no Lean compilation or Google publication occurs.
The optional summary remains text and is not passed to this filter.

The trusted publisher applies the same filter to the exact file read from
GitHub, including archived/reused verification. This operational guard is
always applied around the mathematical proof cache; it does not change the
Lean theorems, estimator/model pins, epoch, or the archived proof identity.

## Trusted publication

After GitHub confirms the exact source run and PR files, the publisher checks the artifact's source/run metadata and compares the verification's Lean bytes and author with GitHub. For a policy it calls `intake_v2.py --publish-from` using the isolated v2 engine and short-lived Google identity. That call revalidates evidence, archives the policy on `submission-results`, calculates resource values, and publishes into the existing v2 epoch.

The archive carries a trusted verification receipt. Publisher retries may recover evidence from it if the original Actions artifact has expired. Evaluation identity stays bound to the original accepted verification run. Completed evaluations skip the engine and archive steps. Both PR checks remain attached to the source head, and successful publication closes the PR without merging it. Policy comments link to the v2 immutable snapshot and `results/policy-submissions/<head>` archive.

The policy archive is separate from the older `leaderboard/results.json` index. Model changes or any engine change require the epoch migration procedure; ordinary CI wiring must not alter scientific sources or the active model.

## The optional summary (`TableGeneration/Submission/summary.tex`)

A policy submission may carry a LaTeX summary of what the participant did, in one extra file: `TableGeneration/Submission/summary.tex`. It is text for people to read.

* **Admitted paths.** A pull request may change `Policy.lean` and, optionally, exactly `summary.tex`. A pull request that changes only `summary.tex` is refused ("a submission needs a policy"). Every other extra path stays refused: `notes.tex`, anything under `Policy/` other than `.lean` helpers (which a policy submission does not take either), `README`, workflows. The same rule runs in the verify job (`scripts/policy_verifier.py`), in the trusted publish job (`scripts/ci/table_generation_report.js`, `scripts/ci/policy_publication.js`) and in `./forshor` (`forshor`), and the notifier words it in plain language.
* **File rules** (one definition, `evaluation/summary_tex.py`; a violation is a rejection of the file with a message naming the rule, not a verdict on the policy): a regular file (no symlink, no directory); not empty; at most 100 KiB (102,400 bytes: about twenty-five dense pages, ten times anything worth reading, a tenth of the policy file's own limit); valid UTF-8; no control characters except tab, line feed and carriage return (so no NUL).
* **Never executed.** **No workflow or script compiles, renders, includes or runs the LaTeX.** TeX can run shell commands (`\write18`) and read files (`\input{/etc/passwd}`), so the file is only ever read as bytes, stored verbatim and shown as text. It cannot change verification: the policy's digest is computed from the policy alone, and the summary is carried beside it.
* **Flow.** The verify job reads the file from the submission checkout and records `summarySource`, `summarySha256` and `summaryBytes` in `verification.json`. The trusted publish job takes the identity (pull request, head commit, author) from the GitHub API, fetches `summary.tex` at the head commit through the contents API exactly as it does `Policy.lean`, and refuses unless the verification carries the same text, in both directions (a summary on one side only is refused). `evaluation/archive_policy.py` then writes it verbatim to `results/policy-submissions/<head>/summary.tex` on `submission-results`, records `summarySha256` in the index entry, and a re-run is a no-op. The v2 publisher contract and the site are unchanged; a link from the policy dialog to the archived summary is a follow-up.

### Validating the summary live, after the merge

What can be tested before the merge is everything that does not need the default-branch workflows: the unit and trust suites above, the local intake (`evaluation/intake_v2.py --lean-policy ... --summary ... --no-publish`) and the archive round trip against a local bare repository. The pull-request verify job runs the verifier from the PR's **base** commit and the publish and notify workflows (`workflow_run`) run their **default-branch** versions, so until this change is on `main` a hosted run still refuses a `summary.tex`. After the merge, on the staging publisher:

1. Warm the environments as for a normal rehearsal. Open a throwaway PR (a deliberate test handle) with the supplied `seed_pp_S707dd51bf951.Policy.lean` as `Policy.lean` **and** a small `TableGeneration/Submission/summary.tex` (for instance `examples/summary.tex` from the starter pack, plus a line of hostile text such as `\immediate\write18{touch /tmp/x}`).
2. Expect the verify job to pass, its artifact `verification.json` to carry `summarySha256` and `summaryBytes`, and the step summary to say the summary is kept as text and never compiled. Expect no TeX process in any log.
3. Expect the publish job to archive `results/policy-submissions/<head>/summary.tex` byte-identical to the PR's file (compare `git show submission-results:...` with the PR's blob), the `leaderboard/policy-submissions.json` entry to carry `summarySha256`, and the policy row to be unchanged from the same PR without the summary (same digest).
4. Re-run the publish job: expect "already archived", no new commit on `submission-results`.
5. Negative PRs, one each, with the expected comment from the notifier: `summary.tex` alone (a submission needs a policy), a `notes.tex` beside the pair (names the allowed pair), an oversize file, a file with a NUL byte, a symlink (the summary rule named in plain words).

## Hosted rehearsal

First warm the policy environments on `main`. Open a PR containing only the supplied `seed_pp_S707dd51bf951.Policy.lean` as `TableGeneration/Submission/Policy.lean` (and, once, the same PR with a small `summary.tex` beside it: the archive then holds `summary.tex` and the entry a `summarySha256`). Confirm both PR checks, archived source and evidence, CLI status, and the PhaseProduct/full-board rows. Repeat with the supplied PhaseTripleProduct example. A published row remains in the ledger, so use a deliberate test handle. Test failed verification and publisher retry without replacing the valid snapshot.

Tracking: [QP-436](https://linear.app/zapata-quantum/issue/QP-436), [QP-437](https://linear.app/zapata-quantum/issue/QP-437).

## Temporary cold-build fallback

`Temporary policy cache bootstrap` is manual-only and checks out trusted `main`. It uses the one-job `forshor-bootstrap-20261109` Linux/x64 runner to create the same cache keys as standard hosted verification. It cannot publish results. Ordinary PR verification continues on `ubuntu-24.04`; no repository runner variable changes.

The fallback VM has 8 vCPUs, 32 GiB RAM, a 100 GiB boot disk, no Google service account, and an administrative SSH rule restricted to Google IAP. Its runner registration is ephemeral, and the VM is configured to delete itself after at most two hours. Delete the VM immediately after its job, then remove its IAP firewall rule, dedicated subnet, and network. The workflow has a separate concurrency group so the original hosted bootstrap can complete independently; cancel the remaining bootstrap once one has saved both caches.

The temporary bootstrap uses four Lean threads and preserves trusted local `.lake` files when retrying an interrupted build. PR verification keeps one thread and clean checkouts. Both trees share the one compiled Mathlib download by matching their pinned manifests.

## Hosted results: November 9, 2026

Both supplied starter policies passed the CLI and both GitHub checks, were archived byte-for-byte, and were closed without merging:

* PhaseProduct: [PR #52](https://github.com/zapatacomputing/forshor-submissions/pull/52), head `de4a5a9935e2cd3605c9f6ca2df3b1e6418142fc`, published as `davidleocadio94_sub1` on PP/full.
* PhaseTripleProduct: [PR #53](https://github.com/zapatacomputing/forshor-submissions/pull/53), head `a18ffa70b4a8a74b0f173a66873f2c0b1b0451a9`, published as `davidleocadio94_sub2` on PTP.

Active epoch: `7a11b2b8b6d68718`. Two-submission snapshot: `9eb7cb213b599a33765e0b619ac9294af8858aa0f20a2db01c3420314d367925`.

The browser passed all 12 board/budget views, both submissions' details, refresh, desktop, and 390px mobile without page errors or horizontal overflow. Both queued submissions were retained. [PR #54](https://github.com/zapatacomputing/forshor-submissions/pull/54) passed structural preflight but failed the deliberately false Lean proof; publication was skipped. Re-running [PP publication](https://github.com/zapatacomputing/forshor-submissions/actions/runs/42716235736) succeeded without changing the snapshot or adding another submission. The ledger still has two completed evaluations and the next participant number is 3. v1 data is unchanged; all five reviewer logins work and anonymous access remains blocked.

### Measured compute

These are measured starter examples on the standard private Ubuntu x64 runner, using trusted caches and one Lean thread. They are not a bound for every k ≤ 8 policy.

| Example | Verification | Publication | Rounded job minutes | Gross CI estimate |
| --- | --- | --- | --- | --- |
| PP, one k=3 band | 9m 54s | 53s | 10 + 1 | $0.066 |
| PTP, one k=4 band | 9m 15s | 64s | 10 + 2 | $0.072 |

Calculated at [GitHub's Linux x64 rate](https://docs.github.com/en/billing/reference/actions-runner-pricing) of $0.006/minute, rounding each job separately. Queue time and the custom publication check consume no extra runner minutes. Estimates precede the organization's included usage, and exclude failed submissions, retries, storage, and one-off bootstrap/debug work. For a similar workload, 1,000 accepted policies imply about $66–72 gross CI compute. The private sprint currently runs one verifier at a time (roughly six of these verifications per hour); production concurrency is a separate setting.

The corrected cold bootstrap took 17m 44s on the temporary 8-vCPU/32-GiB VM with four threads. Warm checker preparation on the first ordinary runner spent 34s fetching/checking dependencies and 10s building ForShor, then 6s/4s for STO; restoring the cache and preparing the runner add time. The temporary VM, IAP rule, subnet, network, and task SSH key were removed. [Self-hosted GitHub compute is free](https://docs.github.com/en/billing/concepts/product-billing/github-actions); that temporary VM's Google charge is separate from the per-submission estimate.

### Public-release boundaries

The tested flow uses collaborators' branches in the company repository. The CLI currently requires that repository as both the base and head, so public fork submissions need a separate integration change. The v2 contract caps each board and the submission index at 2,000 rows; paginate before scaling beyond those caps. These constraints concern submission volume, independently of visitor traffic receiving shared JSON.
