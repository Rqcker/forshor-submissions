# Public-repository preparation review — November 12, 2026

Scope: `zapatacomputing/forshor-submissions` at
`7c6264d71ffb3bc90592d427e47d581e602f9af1`, plus all locally fetched Git refs
(426 commits, including accepted-result history). The website repository and
Google buckets remain private. No history was rewritten or contribution removed.

## Credentials and dependency access

Gitleaks 8.30.1 scanned all fetched history with its built-in rules and fully
redacted output. The release binary was checked against the upstream release's
SHA-256 checksum. There were 21 generic-key detections: all were 64-character
hexadecimal verification/evaluation identity hashes in archived JSON receipts.
They are deterministic identifiers from the verification code, not credentials.
No credentials were detected within that scan's scope. This is a scanner result,
not proof that every possible secret or private document has been identified.
The redacted detailed report is retained locally, not committed.

The current policy checker uses the public `VerifiedQC/ForShor` repository at
`349849e091fc05a4e80c99ae3511985faf3b8843`; anonymous GitHub requests confirmed
both repository and exact commit are available. STO is the checked-in mirror
(`evaluation/policy-trees.json`), so participants do not clone Courtney's private
working repository. `VerifiedQC/ShorTableOptimization` is also public. The
resource engine and its Qualtran copy are vendored with the submission code.
The private website repository is not needed to submit a policy.

## Access and workflow settings

At review time the organization uses GitHub Free, the repository is private,
and private forks are disabled by company policy. GitHub returned HTTP 403 for
the private repository's branch-protection API. Public branch protection and a
fresh external fork submission therefore remain release-time checks.

The repository's default workflow token was changed from Write to Read, and
workflow approval of PR reviews was disabled. The trusted publisher retains
its explicit permissions. Verification has Contents Read, no Google publishing
credentials or identity permission, and no persisted checkout credential.
PR jobs restore dependency caches without saving them; trusted main warms them.

The collaborator review found 14 accounts: seven Admin, six Write, one Read.
No collaborator's access was changed. Confirm the intended maintainer list and
MFA with the repository owner; the API did not expose MFA state to this caller.
No company-wide MFA or private-fork policy was changed.

`scripts/ci/repository_security.py` prepares the release protections with zero
required reviewers, required maintenance checks, administrator enforcement,
and no force pushes or branch deletion. The archive write allowlist names only
the publisher App. Activation must verify GitHub accepts the App restriction;
if it does not, use a dedicated installed publisher App rather than a human
bypass. These rules cannot yet be claimed active on the private Free repository.

## Remaining release checks

- Apply and read back the prepared protections after the authorized public switch.
- Confirm public fork jobs receive no write token or publishing secrets.
- Test setup/check/submit/status using a fresh external account without upstream Write.
- Confirm the intended maintainers and MFA; do not remove access automatically.
- Independent backups remain MIS-24; domain and final release checks remain MIS-25.

The Cloud migration (MIS-26) is planned separately and does not block this work.
