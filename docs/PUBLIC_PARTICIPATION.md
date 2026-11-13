# Public participation and repository protections

Tracking: [MIS-23](https://linear.app/zapata-quantum/issue/MIS-23/public-submissions).
The Google Cloud project migration (MIS-26) is separate; this work can be tested
against the current deployment. Neither the CLI nor the protection script makes
a repository public or changes Google resources.

## Participant workflow

Clone `zapatacomputing/forshor-submissions`, run `./forshor setup`, write
`TableGeneration/Submission/Policy.lean`, then run `./forshor check` and
`./forshor submit`. The optional `summary.tex` remains text and is never compiled.

For public submissions the CLI creates or reuses the authenticated user's fork,
pushes there, and opens a PR against `zapatacomputing/forshor-submissions/main`.
The fixed company main supplies the verifier even when origin is a fork.
Participants need only Read access upstream. Accepted source and evidence go
to `zapatacomputing/forshor-submissions/submission-results`; the submitted policy
PR closes without merging after Google confirms publication. The website uses
the same cached JSON data flow as today.

While private, existing authorized writers retain their staging workflow.
Private forks are currently disabled by company policy. Do not change that
organization-wide setting or repository visibility just to bypass the limit.

## Job limits and queue behavior

| Work | Maximum active | Timeout |
| --- | ---: | ---: |
| Lean/exact-width verification, including setup | 4 | 30 minutes per job |
| Trusted Qualtran, archive, publication and PR reporting | 1 | 20 minutes per job |

GitHub has no numeric repository-wide four-job setting. New CLI branch names
are randomly assigned to `submission/q0/` through `submission/q3/`. The trusted
verification workflow maps those prefixes to exactly four fixed concurrency
groups, each with one active job and `queue: max` (100 waiting jobs). All other
branch names use q0. Arbitrary branch names cannot create extra queues. A busy
queue can wait while another is idle; this is not a shared work-stealing queue.
Publication has its own single queue with 100 waiting jobs. Organization-wide
runner limits can further reduce available capacity.

The 30-minute verifier job includes setup, a 15-minute ForShor stage limit, and
a 10-minute mirror stage limit. The separately trusted main cache warmer may
take up to 180 minutes; that is maintenance, not a per-submission allowance.
Warm the pinned caches before testing the largest supported policies. Cold
cache misses can cause a submission job to time out: report an infrastructure
failure, warm the trusted cache, then rerun verification. PR jobs never save
trusted dependency caches.

An updated commit cancels only the older verification of that same PR. The
publisher is never automatically canceled during archive or snapshot writes.
Timeouts, exhausted queues and missing artifacts are visible failures, not
mathematical rejections. Retry a canceled queued verification after capacity is
available. If Lean passed and publication failed, rerun the publisher rather
than Lean. This bounded queue cannot absorb a 10,000-submission burst.

## Rules enforced by GitHub

For **zapatacomputing/forshor-submissions/main**, maintenance changes must use
a PR and pass the `Maintenance checks` job from GitHub Actions on the current
base. There are **zero required approving reviews**: David can merge his own
maintenance PR. Maintenance checks run on every PR, including documentation
changes, so a required check cannot remain absent because of a path filter.
They are separate from the scientific Lean and Qualtran submission checks.

For **zapatacomputing/forshor-submissions/submission-results**, only the
publishing automation's GitHub App is permitted to push. No users or teams are
on the push allowlist. Disable force pushes and branch deletion on both
branches, including for administrators. Administrators can still edit GitHub
settings or delete a repository; these settings do not replace MIS-24 backups.

The current publisher uses GitHub's Actions App. Its token gets explicit
Contents Write only in the trusted publisher workflow. Verification gets
Contents Read, no publishing secrets, and no Google identity token permission.
GitHub restricts public fork tokens to read access; do not enable write tokens
or secrets for fork PR jobs. App restrictions identify an App, not an individual
workflow: the trusted main workflows and their permissions remain part of the
security boundary. A separately installed publisher App can be selected with
`--publisher-app SLUG` if required by GitHub or the company's access model.

Default workflow tokens are read-only and cannot approve PRs. Review the
existing collaborator list with the repository owner; do not automatically
remove collaborators or change company-wide MFA policy. Confirm maintainers
use two-factor authentication. A missing MFA field from the API means unknown,
not that MFA is disabled.

## Prepare and activate protections

```sh
python3 scripts/ci/repository_security.py plan
# Only after the public switch has been separately authorized and completed:
python3 scripts/ci/repository_security.py apply
```

The script never changes visibility, saves the previous settings under ignored
`artifacts/security/`, applies the rules through GitHub's API, and reads them
back. It stops if the publisher App restriction is rejected; do not replace it
with an administrator or human bypass. In that case configure an installed
publisher App with repository-scoped Contents Write and keep its credentials
only in trusted publication before continuing the release check.

The company currently uses GitHub Free. GitHub returns HTTP 403 for private
branch protection; therefore the protection payloads are prepared but cannot
be claimed active while this repository is private. Activate and verify them
as part of the authorized public switch. This does not require migrating Google.

## Rehearsal and monitoring

Before release, run the CLI/fork tests, publisher trust and conflict-recovery
suites, and at least one hosted PhaseProduct and PhaseTripleProduct submission
against private staging. Test invalid source, a new commit during verification,
and four simultaneous queues. Check archived source bytes, every accepted
identity, PR outcomes, and the dashboard's snapshot. Preserve accepted test
contributions; do not rewrite the archive to clean them up.

After public visibility and protections are active, use an external GitHub
account with no upstream Write access to test the full fork route. Unit tests
and a maintainer's private submission are not a substitute for that test.

```sh
gh run list --repo zapatacomputing/forshor-submissions --workflow table-generation-submission.yml --limit 100
gh run list --repo zapatacomputing/forshor-submissions --workflow publish-results.yml --limit 100
gh api repos/zapatacomputing/forshor-submissions/actions/permissions/workflow
```

Inspect queue age, canceled runs, timeout conclusions, cache misses, publication
failures, and storage usage in GitHub Actions and the organization's billing
dashboard. Standard hosted compute is free for the public repository; larger
runners and excess artifact/cache storage can still cost money. Current
private runs consume the organization's shared allowance. No organization-wide
budget or email notification settings are changed by this ticket.

Sources: [GitHub concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency),
[branch protections and plans](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/managing-a-branch-protection-rule),
[Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
