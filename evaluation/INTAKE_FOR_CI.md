# Wiring `intake_v2.py` into GitHub CI (what the job has to do, and nothing more)

`intake_v2.py` is the whole of "a participant's policy JSON becomes a row on the boards". It was run end to end on a local publisher (a challenger-written policy, the Lean mirror,
the publisher, the page) before this was written; the workflow below only has to call it. Nothing here is wired into any workflow yet.

## The call

A submission is the Lean file `TableGeneration/Submission/Policy.lean` (the only file a PR may change; ForShor's PolicyTemplate format, see the starter pack's `templates/Policy.lean`):

```sh
python evaluation/intake_v2.py --lean-policy "$POLICY_LEAN" --login "$PR_AUTHOR" --pull "$PR_NUMBER" --head-sha "$PR_HEAD_SHA" --run-id "$GITHUB_RUN_ID" \
  --tree "$STO_TREE" --forshor-tree "$FORSHOR_TREE" --archive "$ARCHIVE_CHECKOUT" --publisher-url "$PUBLISHER_URL" \
  --workdir "$RUNNER_TEMP/intake" --stage-timeout 21600 --mirror-timeout 43200 --out result.json
```
with `PUBLISHER_TOKEN` in the environment (the identity token the publisher's IAM check wants) and `GH_TOKEN` for the archive fetch, as `publish_v2.py` already uses them.

| input | what it is |
|---|---|
| `$POLICY_LEAN` | the participant's `TableGeneration/Submission/Policy.lean` from the PR. It is only compiled by ForShor (after ForShor's lexical gate), never run by us. `--forshor-tree` is required with it. `--policy FILE.json` (site policy JSON: `mode` and `bands`) is the other input, for rehearsals and for a PhaseTripleProduct policy, which has no Lean template yet; give exactly one of the two. |
| `$PR_AUTHOR`, `$PR_NUMBER`, `$PR_HEAD_SHA` | from the PR event. `forshor` is a reserved handle and is refused. The row is named `<login>_sub<N>`, N assigned by the publisher's ledger. |
| `$STO_TREE` | a **built** Lean tree for the STO mirror at the epoch's pins: the interim STO fork, branch `port/forshor-21e129a`, ForShor `21e129a`, Lean 4.36.0-rc1. `lake build TableGeneration` does **not** build the PhaseTripleProduct modules: build `TableGeneration/RecursiveCost/PTP/**` by name too (the module list is in the fork's `JMC_VALIDATION.md`). |
| `$FORSHOR_TREE` | a built ForShor checkout at `21e129a` (`lake build PolicySubmission forshor_policy_submission`). Copied per run (it is written into), so use a cache and a copy-on-write or a throwaway checkout. Optional: without it, no row has the `ForShor` tag. |
| `$ARCHIVE_CHECKOUT` | a checkout of the `submission-results` branch, as `publish_v2.py --archive` reads it. Omit only for a local rehearsal. |

The epoch's pins are the ones in this repo's `lake-manifest.json` / `lean-toolchain` and the publisher's `/v2/state` model; a publisher on a different epoch is refused before any Lean runs.

## What it does, and what comes back

Order: reserved handle and epoch check (before any compute) → normalise the policy → STO mirror at exact widths → ForShor's build and printed document (a PhaseProduct policy whose k are all at most 8, when
a ForShor tree is given; always for a Lean submission) → price once → publish through the recovery client (a 409 or a transport error re-reads the live state and refreshes only what depends on it).

| exit | meaning | `result.json` |
|---|---|---|
| 0 | published, or already published | the publisher's answer (`snapshotId`, ...) and `policy` (digest, whether the mirror and ForShor evidence were attached) |
| 1 | refused: the policy does not qualify | `{"refused": true, "stage": ..., "reason": ...}`; stages: `format`, a mirror stage (`threshold`, `report`, `cert`, ...), `forshor`; a reserved handle or epoch mismatch |
| 2 | the run could not complete (a tool failed, the harness wrote no record, the publisher is unreachable after retries) | `{"failed": true, "error": ...}`: re-run, it is not a verdict |

A stage that hit its time limit is exit 1 with a `report`/timeout reason: a resource limit, not a mathematical rejection, and it yields no evidence. Measured: most policies take seconds to minutes in the mirror; a
first band of k = 8 took about 2.4 hours in the report stage, so set the limits above and the job's own timeout accordingly.

## What the job must also do (not part of the script)

* Run only on the participant's PR with the trusted code from the base repository; the script runs Lean on the policy's data only, but the PR's files must never be executed.
* Cache the two Lean trees (they are several GB and take tens of minutes cold), and set `TMPDIR` to a directory the job controls: a 2.4-hour run once lost a file in the system temporary directory.
* Post `result.json` back on the PR (the CLI follows publication, `./forshor status`), and re-run on exit 2.

## ForShor tree for the submission check

Both boards are checked by ForShor, with the same lexical gate. The PhaseProduct files are in ForShor `21e129a`. The PhaseTripleProduct files (`PTPPolicyTemplate.lean`, `PTPPolicyCheck.lean`,
`PTPPolicyMain.lean`, two lakefile entries) are in ForShor `main` from `349849e` (PR #83), which is the pin `21e129a` plus documentation changes and those files and nothing else. `--forshor-tree` must therefore be a build
of ForShor `main` at `349849e` or later; the epoch's pin stays `21e129a`, because the added files change no theorem or definition the pricing or the evidence depends on. The STO tree must be the fork's `main` at `2558595`
or later (`ShorTableOptimization_temp`, which contains `--fast`, PR #5), because `intake_v2.py` runs the mirror with `--fast`.

Measured: a PhaseTripleProduct file with bands k = 8 and k = 4 went from the Lean file to a published row in 2.5 minutes (gate, ForShor build and kernel check about 25 s, mirror with `--fast` about a minute);
a PhaseProduct k = 3 file in 4 minutes; a PhaseProduct k = 8, 4, 3 file builds in ForShor in 40 s and prints in about 5.5 minutes.

## Open

* A PhaseTripleProduct row carries no `forshorPolicy` record: ForShor has no IR for it, so there is no agreement tier. ForShor's kernel check is still a gate (a failed build is a refusal).
* `scripts/verifier.py` allows `Policy.lean` and, through `HELPER_PREFIX`, helper files under `TableGeneration/Submission/Policy/`; if only `Policy.lean` is to be accepted, the new workflow must refuse the rest.
