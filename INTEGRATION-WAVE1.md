# Integration wave 1 — the lanes independent of the export_v2 pricing chain

Branch `prepush/XE-integration-wave1`, cut from `prepush/integration-2026-11-24` at `792464f`.

This wave merges the five lanes that do **not** depend on the export_v2 pricing chain, so that the chain
(WP → WR → WX → WY, plus XA) can merge on top of a settled base. Every merge is `--no-ff`.

## Merge order and cleanliness

| # | Lane | Source | Head | Merge |
|---|------|--------|------|-------|
| 1 | `prepush/WJ-refusal-patches` | `../submissions-WJ` | `fa4d4d9` (8 commits) | clean |
| 2 | `prepush/WQ-lift-publication-gate` | `../submissions-WQ` | `a5e69a9` | clean textually; **2 cross-lane test failures**, resolved in `48df033` |
| 3 | `prepush/WT-sampled-fallback` | `../submissions-WT` | `f42e2ce` | clean |
| 4 | `prepush/WU-fingerprint-guard` | `../submissions-WU` | `e9d8e7f` | clean |
| 5 | `prepush/WE2-option-docs` | `../submissions-WE2` | `7cbfd76` | clean |

All five branch directly off `792464f` (merge-base `792464f` in every case), so each merge is a true two-parent
merge of a single lane.

## Conflicts and resolutions

### No textual conflicts anywhere

`git merge` reported no conflict for any of the five. In particular `evaluation/intake_v2.py`, which **both** WJ and
WQ rewrite, auto-merged: WJ's edits and WQ's edits fall in disjoint line ranges.

That `intake_v2.py` is the exact union was then proved rather than assumed:

```
WJ  + (exactly WQ's base→WQ line changes) == merged
WQ  + (exactly WJ's base→WJ line changes) == merged
```

Both directions are line-for-line identical, so no behaviour from either lane was dropped or rewritten. Both sets
survive, specifically:

* **WJ kept**: `INCOMPLETE_STAGES` (:62) and its use at the mirror-stage split (:520), `_run_mirror_or_incomplete`
  (:453) and its call (:512), `_selector_cause` (:181) with the cause-naming in `refuse_unqualified_design` (:198) and
  `refuse_unqualified_primitive` (:206), the `lean_kind` both/neither split, and the two
  "bands ForShor printed are not the submitted bands" messages now naming which document and which step (:683).
* **WQ kept**: the publication-gate lift (:602-604, `measured` + `qualified_in_run`), `require_coherent_export` made
  conditional (:610-613), `require_readout_epoch` (:257) applied in both `verify` (:512 region) and
  `publish_verification` (:670), `bind_measured_readout` (:271), `compiled_full_run`/`FULL_RUN_KEY` (:229-244), the
  `publishing` parameter through `verify_submission`/`_verify_submission`, and the measured pricing route (:701).

The two lanes meet in `publish_verification`'s pricing branch, where the merged form keeps both: WQ's
`if readout is not None:` measured arm first, then the pre-existing coherent arm, then WJ's PTP arm carrying its more
specific message (:673-683).

### Two cross-lane *test* failures after merge 2 — resolved in `48df033`

Merging cleanly is not the same as agreeing. Each lane's tests encoded an assumption the other lane moved. Both
production behaviours were kept; only two assertions moved. Nothing about what is refused, or about what gets priced,
changed in either resolution.

**(a) `test_full_run_opt_in.py::test_every_shipped_route_still_reaches_the_default_gate`** (WJ's structural guard).
WJ asserts the number of `check_no_full_run_export` references in `intake_v2.py` is exactly 3, to catch any shipped
route being swapped onto the looser opt-in gate. WQ's lift adds a **fourth** reference at `intake_v2.py:613` —
`publish_verification`'s measured arm, for an export naming a selection this checkout has no converter for.

*Resolution*: that fourth site **is** the default gate (`check_no_full_run_export`, not
`allow_qualified_full_run_export`), so WJ's invariant holds and only the hard-coded count was stale; 3 → 4 with a
comment naming the new site. The sibling test `test_the_opt_in_is_wired_into_no_route` still pins
`allow_qualified_full_run_export` to **zero** callers, so the opt-in remains default-off. The guard was not weakened.

**(b) `test_publication_gate.py::test_without_the_converter_a_full_run_key_keeps_its_standing_refusal`** (WQ's
retained-refusal test). WQ asserted the refusal reason by byte-equality against
`"compiled submitted_full_run metadata requires dedicated nonpublishing full-run qualification"`. WJ's refusal
specificity appends the cause after a colon, so the live message is now that sentence plus
`": the export selects the full-run profile 'quotient-exact'"`.

*Resolution*: WJ's `policy_options.py` documents `FULL_RUN_ROUTE` as *the anchor the rest of the repository matches on
and never changes; the cause follows it after a colon*, matched as a **prefix** (as
`starter/test_pack_validation.py` already does). WQ's equality predated the cause clause. The assertion now
(1) pins `FULL_RUN_ROUTE` byte-for-byte so the anchor cannot drift, (2) asserts the prefix contract, and (3) asserts
WJ's exact full message including the cause. That is strictly stronger than either lane had alone: WJ's specificity is
positively exercised instead of merely tolerated, and WQ's "refused, and nothing priced" (the monkeypatched
`build_measured_request` that fails the test if reached, plus `publisher.sent == []`) is untouched.

## Expected overlaps, as checked

* **WJ ∩ WQ on `evaluation/intake_v2.py`** — both sets survive; proved by the union check above.
* **WJ owns `scripts/ci/policy_source.py` and `evaluation/starter/pack/harness/policy_options.py`; WE2 owns the pack
  docs and must not touch `policy_options.py`** — **holds.** WE2's seven files are `docs/CLI.md`, four pack docs,
  `templates/Policy.lean` and `starter/test_pack_docs.py`. At the merged head both `policy_source.py` and
  `policy_options.py` are byte-identical to WJ's blobs.
* **WU is `scripts/ci/` only, three new files** — holds; clean.
* **WT is a new `export_v2/sampled_fallback.py` plus `step2_sampled_contract.json` and tests** — holds; clean. It
  lands in `export_v2`, which the pricing chain also touches later; it adds only new files, so the chain will not
  collide with it textually.

Beyond `intake_v2.py` and the two test files above, **every file touched by any of the five lanes is byte-identical
at the merged head to that lane's own blob.**

## Per-step suite counts

Fast suites after every merge. Baseline measured in this worktree at `792464f` and matches the brief exactly.

| After | `export_v2/pytests` | `scripts/ci` | `test_archive_policy.py` | `test_contributor_cli.py` |
|-------|--------------------|--------------|--------------------------|---------------------------|
| base `792464f` | 165 passed, 56 skipped | 66 passed, 60 subtests | 33 passed | 54 passed, 22 subtests |
| 1 · WJ | 240 passed, 58 skipped | 78 passed, 89 subtests | 36 passed | 54 passed, 22 subtests |
| 2 · WQ (pre-resolution) | *2 failed*, 265 passed, 60 skipped | 78 passed, 89 subtests | 36 passed | 54 passed, 22 subtests |
| 2 · WQ (after `48df033`) | 267 passed, 60 skipped | 78 passed, 89 subtests | 36 passed | 54 passed, 22 subtests |
| 3 · WT | 293 passed, 60 skipped | 78 passed, 89 subtests | 36 passed | 54 passed, 22 subtests |
| 4 · WU | 293 passed, 60 skipped | 100 passed, 96 subtests | 36 passed | 54 passed, 22 subtests |
| 5 · WE2 | 293 passed, 60 skipped | 100 passed, 96 subtests | 36 passed | 54 passed, 22 subtests |

`--runslow`, once, at the final head `f6cbbfa` (`cd evaluation && pytest export_v2/pytests -q --runslow`):
**352 passed, 1 skipped** in 4m45s.

### Reconciliation against the per-lane figures

Nothing is lost; every total is the sum of the per-lane deltas.

* **export_v2** — deltas over the 165-passed base: WJ +75 (240), WQ +27 (192), WT +26 (191), WU +0, WE2 +0.
  165 + 75 + 27 + 26 = **293 passed**. Skips: 56 + 2 (WJ) + 2 (WQ) = **60**. Both match.
  Note the pre-resolution run already collected 267 (265 passed + 2 failed), so the two failures were genuine
  disagreements, **not** lost tests — the collection count was right throughout.
* **scripts/ci** — WJ +12 passed/+29 subtests, WU +22 passed/+7 subtests.
  66 + 12 + 22 = **100 passed**; 60 + 29 + 7 = **96 subtests**. Both match.
  The brief quoted WU as "88 (66 + 22)", which is its passed count only; WU measured alone in `../submissions-WU` is
  **88 passed, 67 subtests**, i.e. it also carries +7 subtests. That accounts for the whole 89 → 96 move.
* **export_v2 `--runslow`** — `--runslow` turns 59 of the 60 skips into passes, so 293 + 59 = **352 passed, 1
  skipped**. On the runslow basis the quoted per-lane figures imply a base of 220 passed / 1 skipped (WQ's 249/1 is
  192 + 57, WT's 246/1 is 191 + 55, and both agree on 220), with deltas WJ +77, WQ +29, WT +26, WU +0, WE2 +0:
  220 + 77 + 29 + 26 = **352**. The single remaining skip is the same one every lane reports (WQ 249/1, WT 246/1),
  i.e. skipped for a reason other than slowness.
* **archive** — WJ +3 (33 → 36); no other lane touches it. Matches.
* **CLI** — unchanged by all five, 54 passed + 22 subtests throughout. Matches WJ's quoted 54+22.
* **`readiness/pre-push/test_bind_native_split_mirror.py`** — 8 passed. This file lives in
  `ForShor_Integration/readiness/`, outside this repository, and stubs every repo import at module scope, so it is
  unaffected by the merge. Re-run at this head: 8 passed, as WJ recorded.
* `evaluation/starter/test_pack_docs.py` is a script taking a pack directory (`python test_pack_docs.py PACK_DIR`),
  not a pytest-collectable module; run against `evaluation/starter/pack` it reports **0 stale references**, matching
  the figure in `INTEGRATION.md`. Whole-directory `pytest evaluation/starter` cannot be collected — an argparse
  script in that tree exits during collection — which is pre-existing at `792464f` and in WE2's own clone, and is not
  one of the four wave suites.

## Hard gates at the merged head

Quoted verbatim.

```
epoch           : ab1f8887b1b50e56
dataFingerprint : 176ce4047f5957f1
engineRevision  : 677cc2e05ab178afff8dc632ff56058a8196a116
```

```
$ git diff 792464f -- evaluation/engine
(empty)
```

Verification-identity fingerprint (`scripts/ci/verification_identity.py`) — it moves, as expected:

```
792464f (base) : 8b0a2fc480ab581477cb00cc343afe219c87c540f6912987d2054fe5287c6e1d
WJ alone       : c1c10e6aa04d884a7b08d8ddb772a28b4e02f7f12a05d85b52dd7cbcbb675044
WQ alone       : 7b02cbd34f055562819235c917f723d98e3e1cdf1bd27710510fb5f33725efe6
XE merged head : da72c98ede7f741dc31f0446c2fdd8d1d7a63414d05ba19cad1de03612f34022
```

The base and the two per-lane values reproduce the brief's figures with the same function, so the merged value is
measured on the same basis. It moves because identity inputs `evaluation/intake_v2.py`,
`evaluation/starter/pack/harness/policy_options.py`, `scripts/ci/policy_source.py` and `evaluation/export_v2/`
changed — allowed under AGENTS.md rule 5, and it means an already-verified head is re-verified rather than reusing its
receipt. The Lean-cache inputs (`TableGeneration/`, `lakefile.lean`, `lake-manifest.json`, `lean-toolchain`,
`evaluation/policy-trees.json`) are **unchanged**, so no cache is emptied and no cold build is triggered (rule 2).

Operational pin, `evaluation/policy-trees.json`:

```
$ grep -n 80d7b6b2d0e696df46f1fa6ed928cbd1d9d7d6c5 evaluation/policy-trees.json
5:    "ref": "80d7b6b2d0e696df46f1fa6ed928cbd1d9d7d6c5"
$ git diff 792464f HEAD -- evaluation/policy-trees.json
(empty)
```

The pin still stands and nothing repointed it.

The three committed-record switches are a SITE concern and were deliberately not checked here.

### `evaluation/export_v2/readout_overlay.py` — the value two lanes and the site depend on

```
VERIFIER_PIN       = 80d7b6b2d0e696df46f1fa6ed928cbd1d9d7d6c5
rule_fingerprint() = 53a7e7ba32f12fe7de423b6d19e3ebdc458fc82b99cdaa7e0eaadeb93a45e032
```

**Both are exactly the required values.** WQ is the only one of the five that touches this file: it corrects the pin
from the base's `931bbf16e7d37e38fa938a590e0f3e444222b812` to the operational `80d7b6b2…`. The file's blob at the
merged head is `aa736acfc235b09adeec04282a6192efccfe9ace`, byte-identical to WQ's own blob and unchanged by the three
merges that followed, so the bytes the published `ruleSha256` covers did not move. Site lane XB's pin in
`src/contracts/results-v2.ts` and the three generated schemas therefore still agrees, and no readout-bearing panel
will be rejected.

### WU's price-surface guard, at this head

```
price surface at f6cbbfa7e (manifest 792464f83)
  dataFingerprint 176ce4047f5957f1  engineRevision 677cc2e05  qualtran 0.8.0.dev0
  caches: 16 committed, 0 not matching their own fingerprint
OK: no price-relevant path moved while the epoch stood still
```

This passes for the right reason, not by being blind: every **enforced** group is under `evaluation/engine/**`, which
this wave leaves byte-identical, and the one `export_v2` group (`export_v2.model`) is observe-only and covers just
`epoch.py`, `canon.py`, `pricing.py` and `mapping.py` — none of which WQ or WT touched. `price_surface.check` hashes
the groups live from the tree and files an enforced move as a hard finding whenever the epoch stood still, so the
guard is armed for the chain.

## What remains to merge

The export_v2 pricing chain, on top of this branch, in order:

1. `prepush/WP-qft-chain`
2. `prepush/WR-orthogonal-converter`
3. `prepush/WX-catalytic-qft`
4. `prepush/WY-design-price-plumbing`
5. `prepush/XA-primitive-pricing`, when it lands

Notes for whoever merges the chain:

* **`evaluation/intake_v2.py` is now the union of WJ and WQ.** WQ left two seams the chain is expected to fill:
  `compiled_full_run` returns `(None, None)` when `export_v2.full_run_design` (WO's converter, arriving with WP) is
  absent, and `publish_verification` passes `design` only when a selection actually converted. Once the converter
  exists those seams go live; `test_publication_gate.py` currently exercises the routing against an injected double,
  and `test_without_the_converter_a_full_run_key_keeps_its_standing_refusal` asserts the no-converter branch, so that
  test is the one to revisit when WP lands.
* **WT added `export_v2/sampled_fallback.py` and `step2_sampled_contract.json` as new files**, so the chain's
  export_v2 edits should not conflict textually with it, but the chain and WT both price Step 2 paths — reconcile
  deliberately rather than by merge order.
* **Do not let the chain move `readout_overlay.py`.** Its bytes are pinned on the site side (lane XB); any change to
  that file moves `rule_fingerprint()` away from `53a7e7ba…` and the site will reject every readout-bearing panel.
* **The epoch must keep standing still.** `git diff 792464f -- evaluation/engine` must stay empty; WU's guard now
  fails the build if an enforced price-relevant path moves while it does.
