# Contributor CLI

`forshor` is a standalone Python 3.12+ executable in this repository. It has no
third-party Python dependencies. Run it as `./forshor` in the checkout, or copy
the executable into a directory already on your PATH and use `forshor`.
macOS, Linux, and Windows through WSL are supported; local acceptance was tested
on macOS and hosted evaluation remains Ubuntu. Git and GitHub CLI are prerequisites.

## Commands

| Command | Behavior |
| --- | --- |
| `./forshor setup` | Authenticate if needed, fetch trusted main, prepare the pinned Lean toolchain, fetch dependency binaries, and build the trusted verifier closure |
| `./forshor setup DIRECTORY` | First clone a fresh checkout into a new directory, then prepare it; run later commands inside that directory |
| `./forshor check` | Verify the current policy file, including uncommitted edits; write local reports under `artifacts/forshor/` |
| `./forshor submit` | Recheck when needed; commit allowed edits, prepare your fork for public submissions, push, create/reuse the PR, and follow its publication |
| `./forshor submit --no-wait` | Submit and print initial status without staying attached |
| `./forshor status` | Inspect the last submission from this checkout |
| `./forshor status --watch` | Resume following queued verification or publication |

`submit --title TEXT` customizes the commit/PR title. `submit` and `status`
accept `--timeout SECONDS` (default one hour) and `--json` (also print the final
machine-readable status record). Interrupting the local watcher does not cancel
hosted jobs. The first setup can be slow; later checks retain compiled dependencies.

## What local checking means

The CLI fetches main from the fixed company submission repository, checks all
committed and working changes, then prepares a generated checker checkout inside
the checkout's Git metadata. The verifier, scoring configuration, adapters, and
scientific model come from the fetched main commit. Only the editable submission
file and allowed Lean helpers are copied into that checker. Symlinks and changes
outside the submission boundary are rejected.

A policy submission may also carry an optional LaTeX summary,
`TableGeneration/Submission/summary.tex`. `check` validates it with the same rules
as the hosted check before anything is built (a regular file, UTF-8, at most
100 KiB, no control characters except tab and line breaks; it cannot be
submitted without a policy, and nothing else may accompany the pair), and
`submit` commits it with the policy when it exists. The CLI never compiles or
renders it. `status` is unchanged.

A local snapshot commit is made only inside the generated checker so the trusted
changed-file check has concrete base/head identities. `check` leaves your original
branch, index and source commits alone. A successful receipt includes the fetched
main SHA and digest of all current source files; edited sources or changed main
invalidate that receipt. Preflight-only success is never recorded as full success.
The CLI neither changes protected model files nor substitutes local quantum cost
estimates for hosted Qualtran evaluation.

`submit` commits your allowed working edits. If you are on main or detached HEAD,
it first creates a submission branch. It never force-pushes, merges a PR, or adds
unrelated files. Existing open PRs on the branch are reused. Source changes while
verification/commit is running cause an error before publication is claimed.
After an accepted PR closes, editing the policy and running `submit` creates a
fresh submission branch and PR. Running it again without edits follows the
existing accepted submission instead.

## Forks and source identity

After `zapatacomputing/forshor-submissions` becomes public, `submit` creates or
reuses your own GitHub fork and pushes there, even when you are a company
maintainer. It does not request upstream Write access, rename your remotes, or
change global Git configuration. A checkout cloned from the company repository
or from a verified fork is supported. In either case the verifier is fetched
directly from the fixed company's `main`, never the fork's `main`.

Your fork must belong to the authenticated GitHub account and be linked to this
repository. An unrelated repository with the same name is refused. GitHub API
denials and outages stop the command; they are not treated as a missing fork.
PR discovery includes the fork owner, and status binds the PR to the recorded
fork and exact commit. Identically named branches in other forks cannot be reused.

While the repository remains private and company policy disables private forks,
existing contributors with upstream Write access retain the staging route. A
read-only user is never silently given Write access or pushed upstream. Public
fork testing requires the public release or an explicitly authorized private
fork policy; the CLI does not change repository visibility or company policy.

New branch names select one of four fixed verification queues. Manual or older
branch names use the first queue. This controls scheduling only; every policy
still goes through the same verification and publication checks.

## Publication and recovery

There are two hosted workflows: Policy submission, then Publish Verified
Results. The publisher's Git head points to main, not the submitted commit.
Both report checks on your PR under **Policy submission**: **Verify submission with
Lean**, then **Estimate resources with Qualtran and publish results**.
If Lean fails, the second check is skipped and the PR stays open.
A failed first check does not always mean your policy is wrong: its run summary names the check that failed. `trees` ("FORSHOR_TREE must name a
built ForShor checkout") means the hosted runner has no prepared Lean trees, which is the hosted setup. `./forshor check` verifies the same file
locally.
When Google confirms publication, the publisher posts links to the accepted
policy and staging dashboard, then closes the PR without merging it. A failed
estimation, publication, or final reporting step keeps the second check red;
rerun the publisher to recover. A newer commit cannot be closed by an old result.
The CLI therefore matches trusted publication diagnostics against the exact
verifier run ID, attempt, PR number, and submitted SHA. An unrelated success or
a successful job without a publication receipt is never reported as your result.

You are told how it ended. When verification finishes the PR gets a comment that mentions the PR's author, so GitHub notifies them
(web, email or mobile, as their notification settings say): **accepted and published** (the publisher's comment, with the row name and a "Cite this submission" section: the policy digest, the submission commit, the snapshot it was published in, and a ready citation and BibTeX entry; a DOI is assigned when accepted submissions are deposited after the challenge closes),
**not accepted** (the check that failed, in plain words, and the verifier's message), or **could not complete** (a tool, a limit or the
runner failed, or the run was cancelled: this is not a verdict on the policy, and nothing needs to change on your side). There is one
comment per commit and outcome, updated in place on a re-run. The *Notify Submitters* workflow posts "not accepted" and
"could not complete"; the publisher posts "accepted" and reports a publication failure that happens after Lean passed.

If Lean rejects a submission, open the printed verification URL, fix the source,
and run `./forshor submit` again. If publication fails after Lean passes, open
the matching publisher URL and rerun its failed job; then use
`./forshor status --watch`. Do not repeat Lean just to retry publication. Already
published replay receipts are treated as success without inventing a new snapshot.

If a queue is slow or the watcher times out, jobs continue. Resume with `status
--watch`. GitHub API/authentication failures return an error, not an empty success.
If an operation is interrupted after PR creation, re-running `submit` discovers
the matching PR rather than creating a duplicate. Closing a PR does not remove
accepted output from the archive.

Acceptance produces an archived table and recalculated plans. Eligibility depends
on the planner's depth bound; champion replacement and new winning metrics are
separate outcomes. The CLI prints the accepted archive and private staging URL.
It does not collect staging passwords, bypass website authentication, or deploy
frontend code. Contributors refresh the website after publication.

## State and credentials

The CLI stores source digests, PR identity, and publication receipt under the
checkout's Git metadata (`forshor/state.json`). Compiled checker files are stored
there too. Reports and setup/build logs live under ignored `artifacts/forshor/`.
GitHub CLI manages authentication. The CLI does not request or persist raw tokens,
change global Git credential helpers, or create Google service-account keys.
The pinned elan installer runs with `--no-modify-path`; its bin directory is added
only to the tool subprocess environment. Existing toolchain defaults are preserved.
