# Submissions integration — `prepush/integration-2026-11-24`

Base: `a713dc7` (`prepush/W8-explicit-defaults`). Last merge: `8c17e73`; release head `b464a24`
(this file, which is not a verification input and leaves the fingerprint at `8b0a2fc4…`).
Four lanes merged with `git merge --no-ff`; every lane's own commits stay reachable and identifiable.

## Merge order

`WA → WB → W9 → WE`

W9 must precede WE: both register the previously unlisted pack test in
`evaluation/starter/README.md`, and WE's pack verification runs "the pack tests listed in"
that README. The order keeps W9's (more specific) registration as the one that survives.

| # | Merge | Lane | Clean? |
|---|-------|------|--------|
| 1 | `20558ae` | `prepush/WA-archive-readout` | clean |
| 2 | `0c00a65` | `prepush/WB-intake-readout` | clean |
| 3 | `e1cad9b` | `prepush/W9-relaxed-phaseproduct` (rebased) | clean |
| 4 | `8c17e73` | `prepush/WE-materials` | **1 conflict, resolved — see below** |

## Lanes

**WA — `prepush/WA-archive-readout`** (merge `20558ae`)
- `488d985` — the readout planner compares on the mode it is given (`plan_readout_variant` takes `mode`, default `"PhaseProduct"`).
- `5050f0a` — your own policy with the other readout is a variant, not a repeat; such an entry records `readout`/`boards`.

**WB — `prepush/WB-intake-readout`** (merge `0c00a65`)
- `a144d2a` — move the operational verifier pin to the native candidate `80d7b6b` (`evaluation/policy-trees.json`).
- `de986a4` — the hosted path checks the receipt's source against its own digest.

**W9 — `prepush/W9-relaxed-phaseproduct`** (merge `e1cad9b`) — rebased from `c94d9b4` onto `a713dc7`
- `f646579` (was `441daa6`) — pin that a one-table-per-k policy still prices exactly as the engine alone.
- `d691630` (was `06f262e`) — list the multi-table-per-k pack test with the other pack tests.

The rebase was clean and content-preserving: `git diff c94d9b4 06f262e` and
`git diff a713dc7 d691630` are byte-identical.

**WE — `prepush/WE-materials`** (merge `8c17e73`)
- `cdd7bf1` — advertise the explicit default selection and several tables per k.
- `6a1486b` — the pack's README documents only delta = 0.01.
- `57eee53` — the build README lists every pack test.

## The one conflict, and how it was resolved

`evaluation/starter/README.md`. W9 (`06f262e`) and WE (`57eee53`) **independently fixed the
same bug**: `evaluation/starter/test_pack_multi_table_same_k.py` has existed since `aae316c`
but was never listed in the README, so it had never run. Both add the same two lines at the
same insertion point; only the trailing comment differs. Git auto-merged the command line and
conflicted on the comment alone:

```
python evaluation/starter/test_pack_multi_table_same_k.py <dir>/forshor-challenge-starter --python <python>
<<<<<<< HEAD
                                                                                               # PR #132's 11-table policy prices to its published numbers; a broken k = 7 table is refused
=======
                                                                                               # several bands with different tables of the same k price and convert to the published numbers
>>>>>>> prepush/WE-materials
```

**Resolution:** one copy of the entry, with W9's comment; WE's discarded. W9's wording is the
specific and verifiable one — it names PR #132, the 11-table policy, the published numbers and
the broken-k=7 refusal, and the test body does exactly that (`test_pack_multi_table_same_k.py`
docstring line 8; the break at line 58; the refusal assertion at line 67). WE's comment omits
the refusal half entirely.

The test is listed **exactly once** (`grep -c` = 1). A duplicated entry would have run it twice
and is a defect. No test assertion was weakened anywhere in this integration.

## Suite results

All suites run **from the repo root** with
`/Users/joshua/Desktop/Resource_Lab/projects/synthesis_backends/.venv/bin/python`.

| Suite | `a713dc7` baseline | after W9 (`e1cad9b`) | **final, after WE (`8c17e73`)** |
|---|---|---|---|
| `evaluation/export_v2/pytests` | 154 passed, 55 skipped | 165 passed, 56 skipped | **165 passed, 56 skipped** |
| `evaluation/export_v2/pytests --runslow` | — | 220 passed, 1 skipped | **220 passed, 1 skipped** |
| `scripts/ci` | 66 passed, 60 subtests passed | 66 passed, 60 subtests passed | **66 passed, 60 subtests passed** |
| `evaluation/test_archive_policy.py` | 24 passed | 33 passed | **33 passed** |

Baseline matched the expected counts exactly. The `+11 passed / +1 skipped` on `export_v2` is
WB's two new pytest files plus W9's new pricing-table test; `24 → 33` on the archive suite is WA.
**Zero failures at every step.** WE changes documentation only, so its merge moves no counts.

### Starter pack tests listed in `evaluation/starter/README.md`

Pack rebuilt from the final head (1100 files, 4.2 MB zip), then all listed tests run:

| Pack test | Result |
|---|---|
| `test_pack_docs.py` | `0 stale references` |
| `test_pack_kmy_only.py` | `1100 files checked; 0 problems` (535 archive identifiers searched) |
| `test_pack_point_order_alias.py` | `point_order alias: ok` |
| `test_pack_validation.py` | `starter pack validation: OK` |
| `test_pack_multi_table_same_k.py` | `ok` ← **the newly registered test; it now actually runs** |
| `test_pack_matches_seed.py` | **not run** — see below |

`test_pack_matches_seed.py` cannot run: it requires an exporter `epoch-input.json` that is not
committed (`git ls-files | grep epoch-input` is empty). **Pre-existing, not merge-induced.**

## Epoch gate — UNCHANGED

`canon.epoch_of(epoch.model_of())` at the final head `8c17e73`, verbatim:

```
ab1f8887b1b50e56
```

Matches the required value. Checked at both `e1cad9b` and the final `8c17e73`. WB's pin move is
**not** an epoch input: the epoch's model/mirror pin is the frozen `21e129a` in
`lake-manifest.json`, which no lane touched. `delta=1e-2` only. Nothing under
`evaluation/engine/`, `TableGeneration/`, `lakefile.lean`, `lake-manifest.json` or
`lean-toolchain` was modified.

## Verification identity

Final head `8c17e73`:

```json
{"schema": "forshor-verification/v1", "key": "690b83213cdd7fa14834685dd63e29d79ea52bf44295334e4060034356ca09db", "headSha": "8c17e7309ac660c09e88598d5f59c7b7d7f6f444", "fingerprint": "8b0a2fc480ab581477cb00cc343afe219c87c540f6912987d2054fe5287c6e1d"}
```

**Fingerprint: `8b0a2fc480ab581477cb00cc343afe219c87c540f6912987d2054fe5287c6e1d`**

Per-lane attribution (`INPUTS` is tree content, so it is independent of `--head`):

| Head | Fingerprint | Moved by |
|---|---|---|
| `a713dc7` | `8a996fe2…0126abc` | baseline |
| `20558ae` WA | `8a996fe2…0126abc` | **unchanged** — WA touches no `INPUTS` path |
| `0c00a65` WB | `f915b493…6fcdd083` | `policy-trees.json`, `intake_v2.py`, +2 files under `evaluation/export_v2` |
| `e1cad9b` W9 | `8b0a2fc4…287c6e1d` | `evaluation/export_v2/pytests/test_pricing_tables.py` |
| `8c17e73` WE | `8b0a2fc4…287c6e1d` | **unchanged** — WE touches no `INPUTS` path |

The integration value differs from the `4b852a81…` WB recorded for the pin move alone because
`INPUTS` contains the whole `evaluation/export_v2` directory: WB's own two new pytest files and
W9's new pricing-table test are all verification inputs. WE independently reported its lane
fingerprint unchanged at `8a996fe2…`, which this reproduces — it did not edit
`evaluation/starter/pack/harness/policy_options.py`, the only starter path in `INPUTS`.

**Consequence for reviewers: every already-verified head re-verifies, and the Lean policy cache
is cold for this new ref.** The epoch is untouched, so no re-pricing or re-seeding is implied —
this is a verification-input move only.

## Cross-lane interactions checked (no single lane covered these)

The four lanes touch strictly disjoint files apart from the README above, so the real risk was
semantic, not textual.

**(a) WA's and W9's tests under WB's moved pin — PASS.** Neither
`evaluation/test_archive_policy.py` nor `evaluation/export_v2/pytests/test_pricing_tables.py`
references the pin: grepping both for `82e2274`, `80d7b6b`, `policy-trees` and `OPERATIONAL`
returns nothing. `evaluation/policy-trees.json` is read only by `scripts/ci/policy_environment.py`,
`scripts/ci/test_docs.py`, `scripts/ci/test_policy_environment.py`,
`scripts/ci/verification_identity.py` and WB's own `test_operational_pin.py`. Empirically, both
lanes' tests pass on the merged branch carrying the new pin `80d7b6b` (archive 33 passed; the
pin/pricing subset 22 passed, 1 skipped). **Neither lane depends on the pin.**

**(b) WA's new `readout`/`boards` index keys — PASS, nothing downstream chokes.** The keys are
written only when `readout != "coherent" or primitive_from is not None`
(`archive_policy.py:221-223`); the default coherent entry is written exactly as before and **no
archived row is backfilled**. WA's own test pins this: *"the legacy coherent entry is read as it
is, never backfilled"* (`test_archive_policy.py:412`). No consumer validates the entry schema
strictly — there is no `additionalProperties` or unknown-key rejection anywhere in
`evaluation/` or `evaluation/export_v2/` — so extra keys are inert for legacy readers.

The one real coupling is the signature change: `evaluation/export_v2/request.py:120` (publisher
request building) imports `plan_readout_variant` and calls it **without** `mode`, so it takes the
new default `"PhaseProduct"` — exactly the value WA removed from the internal hardcode. Publisher
request building is therefore behaviourally identical. `published_ids` also gained a default,
which only widens the accepted calls.

**(c) Epoch and identity re-confirmed once at the final head** — see the two sections above.

## Notes for the reviewer

- **The served starter pack was not installed.** WE rebuilt it without `--allow-dirty` and
  verified 1100 zip members byte for byte, manifest 1099 = on disk 1099, and the bootstrap
  checksum, but did **not** install it. The served starter remains the old baseline; it still
  needs to be published separately.
- `prepush/W9-relaxed-phaseproduct` arrived in a **separate clone**
  (`submissions-W9`), not a worktree of this repo. Its commits were brought in with
  `git fetch <local path> <branch>:<branch>` — nothing was copied by hand. The clones share
  `c94d9b4`, so the histories are genuinely compatible. The branch in this repo now points at
  the rebased `d691630`.
- Running a single pytest **file** under `evaluation/export_v2/pytests/` fails to collect
  (`ModuleNotFoundError: No module named 'export_v2'`) because the `sys.path` bootstrap only
  happens on directory collection. This reproduces identically on the untouched `a713dc7`
  baseline — **pre-existing, not merge-induced**. Select the directory (optionally with `-k`).
- Nothing has been pushed. No lane's assertions were altered.
