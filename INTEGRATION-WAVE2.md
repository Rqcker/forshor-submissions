# Integration wave 2 — the export_v2 pricing chain

Branch `prepush/XF-integration-wave2`, cut from **`prepush/XE-integration-wave1` at `f56bbc0`** (not from
`792464f`), so wave 1 — WJ, WQ, WT, WU, WE2 — is included. Wave 1 was re-verified in its own worktree
before branching: `293 passed, 60 skipped`, exactly the figure `INTEGRATION-WAVE1.md` records.

Final **code** head **`6b56806`**; this document is committed on top of it as `fd2ee26`, which changes no
code. Every gate below was measured at `6b56806` and re-checked at `fd2ee26`: the verification-identity
**fingerprint is identical at both** (`36d74218…`) because a root markdown file is not an identity input —
only the headSha-derived `key` differs. Every merge is `--no-ff`.

## Merge order and cleanliness

The order was not negotiable and was followed exactly.

| # | Lane | Source | Head | Merge commit | Textual result |
|---|------|--------|------|--------------|----------------|
| 1 | `prepush/WP-qft-chain` | `../submissions-WP` | `aaf19a8` | `5ac9f88` | **clean** |
| 2 | `prepush/WR-orthogonal-converter` | `../submissions-WR` | `cac3ac9` | `b3715a3` | 2 conflicts |
| 3 | `prepush/WX-catalytic-qft` | `../submissions-WX` | `36c82f9` | `dbac75a` | 1 conflict |
| 4 | `prepush/WY-design-price-plumbing` | `../submissions-WY` | `9968b30` | `bb9df90` | **clean** |
| 5 | `prepush/XA-primitive-pricing` | `../submissions-XA` | `fcf542f` | `3dd1b34` | **clean** |

Two further commits are integration work, not merges, and are named as such: `c4b08bf` (re-point WY's
plumbing, close WX's qubits hook) and `6b56806` (apply XA's hook, settle the row seam and handoff 2).

The branch topology explains where the conflicts fell. **WX and WY both descend from WP** (`aaf19a8` is an
ancestor of each), so each is a true three-way merge against WP and only the files both touched collide.
**WR does not descend from WP** — it was cut from `792464f` and *re-creates* WP's work — but
`qft_overlay.py`, `request.py` and `test_qft_overlay.py` are **byte-identical blobs** in both
(`739c489`, `9466024`, `db5ab5d`), so git resolved them trivially and only WR's three genuine extensions
conflicted.

Merge cleanliness was verified rather than assumed: at the final head every file is byte-identical to its
owning lane's blob except the six listed under *Conflicts* and *Integration fixes* below.

## Conflicts and resolutions

### 1. `pricing.py` — docstring only (merge 2)

WP and WR differ in this file **only in the docstring**; `git diff WP:pricing.py WR:pricing.py` shows zero
code change. Resolved as the **union**: WP's epoch rationale (why `fullalg.price_full` cannot grow a
`design=` parameter — any edit under `evaluation/engine` moves `dataFingerprint` and `engineRevision`, and
appending one comment line to `site_engine/fullalg.py` was measured to move `ab1f8887b1b50e56` to
`d3bda33d5ddd5dca`) plus WR's statement that the overlay's answer is reported as `view["full"]["qft"]`.

### 2. `test_full_run_design.py` — profile label vs explicit design (merge 2)

WP drove the design-threading test through the **profile label**
`quotient-banded-data-catalytic-work`; WR drove it through the **explicit design**. Took WR's form, because
under the topology gate no profile label converts, so only the explicit path can exercise threading at all.
Both lanes' reconciliation notes are preserved in the docstring.

### 3. `test_full_run_chain.py` — the file's organising fact (merge 3)

WX rewrote it to "three profiles price, four refuse"; wave 2's is "all seven labels refuse, the explicit
design prices". Resolved in favour of the gate, with WX's catalytic facts re-pointed. Detailed below.

---

## THE CONFLICT THAT MATTERED: topology gate vs catalytic profile pricing

**WR's gate wins, and it was not weakened.** The owner has dropped quotient customization from this release
(deferred), so every `quotient-*` and `split-*` profile label **REFUSES**.

### Why the gate is right

All seven non-reference profiles select a multiplier **topology** (`modmul` = `quotient_stream` or
`split_compare`) — `audit_native_fullrun.py:25-30` emits it beside `qft` for every bundle, and
QFT-SURFACE-MAPPING.md section 1.1 lists it for all seven. The topology drives both the T cost
(`policy_design.py:139-142`) and the qubit base (`:160-162`). But `qft_overlay.project` substitutes QFT
sites **by width into the engine's frozen REFERENCE census**, has no topology parameter, and
`parse_design` refuses a `modmul` key outright. Converting a profile to a bare per-family mapping therefore
**dropped the topology silently** and priced a quotientStream run on reference-topology numbers — reporting
the reference price as if the option had been honoured. That is the one silent default this release forbids.

Measured at the merged head, `reference` is the only label that converts:

```
reference                             -> design=None
quotient-exact                        -> REFUSED (multiplier topology 'quotient_stream')
quotient-catalytic                    -> REFUSED (multiplier topology 'quotient_stream')
quotient-banded-data-catalytic-work   -> REFUSED (multiplier topology 'quotient_stream')
quotient-fallback-data-catalytic-work -> REFUSED (dataRotations := fallback)
quotient-step2-fallback               -> REFUSED (step2 := fallback)
split-step4-kicks                     -> REFUSED (splitCompare + step4 := selectiveKick)
split-step2-fallback-step4-kicks      -> REFUSED (splitCompare + step2 + step4)
```

### What ships, and how WX's two tests were re-pointed

The label and the design are **different things**. What a label named was a per-family QFT *combination*
attached to a quotientStream *topology*, and **only the topology is unhonourable**. Every combination the
qualified labels named still prices when expressed as an explicit reference-topology design — the catalytic
ones differentiating exactly as WX measured. So WX's two tests were re-pointed onto that path, not deleted,
and the gate was not touched:

| WX's test | Re-pointed to | How |
|---|---|---|
| `test_the_catalytic_profiles_price_a_differentiated_full_run` | `test_the_catalytic_designs_price_a_differentiated_full_run` | Driven from `full_run_design.QFT_ONLY[...]` — the converter's **own table**, so the two cannot drift — as an explicit reference-topology design. **Every assertion WX wrote is preserved verbatim**: `identity is False`, `T != engine T`, `catalysed`, `b >= B_MIN`, `deltaArm > 0`, `registerQubits == b`, `qubitsPeak == engine peak + b`, `prepT > 0`, `T == cellsT + prepT`, `rotationsCatalysed > 0`, and the panel carrying the block. |
| `test_three_profiles_price_and_four_refuse` | `test_the_explicit_design_prices_every_combination_the_profile_labels_named` | Asserts the surviving substance: all three combinations convert and price on the explicit path, the two catalytic ones selecting the catalytic backend and `quotient-exact`'s all-exact combination collapsing to the engine's untouched default. **Nothing about the pricing was lost when the labels were refused — only the topology they also carried.** |

WP's profile-driven tests in the same file were re-pointed the same way:
`test_a_compiled_document_prices_a_panel_carrying_a_qft_projection` (one profile → three explicit designs,
a strictly wider test), `test_an_all_exact_design_is_bit_identical_to_the_no_selection_baseline` (was
`test_quotient_exact_is_...`), `test_the_chain_never_asks_the_engine_for_a_design`, the three delta-guard
cases, and `test_every_profile_label_refuses_and_the_explicit_design_prices` (was
`test_only_quotient_exact_prices_today`). WP's catalytic-refusal test became
`test_the_three_qft_only_profiles_refuse_on_their_multiplier_topology`, which now positively exercises the
gate over all three labels.

---

## Integration defects found and fixed

These were created by lanes landing on each other and were invisible to each lane alone.

### A. A catalytic design was collapsed to the Ross-Selinger default — a silent default

WR's collapse rule in `_design_of_body` was `not any(target == "approximate")`, written when the band was
the only axis, where `target: "exact"` did mean "no substitution". WX's arm makes **`backend` a second,
orthogonal axis** — `target` picks the construction, `backend` picks the **currency** — and
`qft_overlay.project` counts a catalytic family as `touched` on *either* target
(`qft_overlay.py:442-443`), because the arm re-prices its rotations, takes its own pool share and adds the
catalyst register to Q.

So a catalytic-**exact** design converted to `None` and was priced at the engine's own number. Measured on
the merged tree against the real engine at delta 1e-2 for
`{"data": {"backend": "catalytic", "target": "exact"}}`:

```
design_of(...)  -> None            (the engine's default path)
engine   T = 1628644042547.186
overlay  T = 1641978984650.562     (+0.82%, catalysed ['data'])
```

Fixed: the collapse now tests **both** axes — a design asks for nothing only when no family asks for an
approximate target **and** no family asks for the catalytic currency. Consequently `catalytic` is no longer
a refusal class; it was dropped from `REFUSAL_CLASSES`, and WR's two `catalytic-approximate` /
`catalytic-exact` refusal cases were re-pointed (not deleted) into
`test_a_catalytic_design_converts_on_both_targets_and_is_never_collapsed`.

### B. WX's qubits hook did **not** close itself — verified, not assumed

WY reports that generalising `ranked_price_of` ranks WX's projection qubits without an edit. **It does
not.** The generalisation overlays each key of `mapping.SHOWN == ("T", "qubits")` that the projection
offers, and WX's arm reports `qubitsPeak` / `qubitsUpperBound` / `catalystQubits` but **no `qubits` key**,
so the hook never fired. Measured: a catalytic design ranked at the engine's own count while the
projection's own number was 42 higher — i.e. **charge 2**, the shared catalyst register that WX's docstring
says "feeds the ranked Q × T, so a T-only catalytic price would be wrong", **never reached the ledger row**.
The generalisation was inert for the one projection it was generalised for.

Fixed in `qft_overlay.py`, one line, through WY's own mechanism with **no edit to `mapping.py`**: the
projection names `qubits`, and only when a catalyst exists — exactly when the engine's frozen total stops
being an upper bound. A band-only projection still offers none, so the engine's number is ranked for it,
which is what `ranked_price_of` argues for (banding can only *lower* the count).

**The value is the peak, not the layout total.** `ranked_price_of` overlays onto the *panel's* full-run
block, where `full_run_of:130` sets `"qubits": f["qubits_peak"]` and carries the layout total separately as
`qubitsLayout`; `readout_overlay.py:151` follows the same convention. Offering `coherent["qubits"]` would
have moved the ranked count from 13406 to 44972 — a 3.4× misreport **in the wrong unit**. Caught by the
re-pointed ranking test, which now pins both the hook and its unit:

```
catalytic    projection has qubits=True   ranked qubits=13448 = 13406 + 42
banded-only  projection has qubits=False  ranked qubits=13406 (the engine's own)
```

### C. WR's "corrected pin" was wrong, and shipped six tests red

Lane WR replaced the no-design `full` panel hash — WO's
`998ad142a47fae05466541a3a92e0851cb86bac91876ce409ec4a98b3e0b19e3` — with
`c773f5022e63194d3f95eba8edcd9cf407f846648fd8b03bae98ce53c180b55e`, as a "CORRECTED PIN", on the finding
that the original "does NOT reproduce" at `792464f`. Re-running **WR's own capture command, unmodified**:

```
submissions-INT @ 792464f  -> full 998ad142...   (the base itself)
submissions-XE  @ f56bbc0  -> full 998ad142...   (integration wave 1)
submissions-WR  @ cac3ac9  -> full 998ad142...   (WR'S OWN WORKTREE)
submissions-XF  (WP+WR merged)                   -> full 998ad142...
```

It reproduces everywhere, **including in the worktree that declared it irreproducible**, and `c773f502`
reproduces nowhere. The re-pin shipped 6 slow tests red on `prepush/WR-orthogonal-converter` itself
(measured at `cac3ac9`: *6 failed, 82 passed* with `--runslow`) — they are `@pytest.mark.slow`, so WR's
fast-suite figure never collected them. It would also have failed **the release's own hard gate**, which
quotes `998ad142` verbatim. Original restored; the `pp` value was never in dispute.

---

## Handoffs from wave 1

### Handoff 1 — WQ's no-converter test, now that the converter exists

`test_without_the_converter_a_full_run_key_keeps_its_standing_refusal` was written to pin behaviour on a
checkout with no `export_v2.full_run_design`. This merge **brings** that converter, so the unconditional
`compiled_full_run(...) == (None, None)` assertion no longer described the tree. Re-pointed, not deleted:
it now pins **both branches**, and both still refuse, for different reasons.

* **Converter absent** (`ImportError` branch, `intake_v2.py:247-249`) — still shipped, so still tested, by
  hiding both the package attribute and the `sys.modules` entry. Falls through to
  `check_no_full_run_export` and refuses with WJ's anchor-plus-cause message. **WQ's original assertion,
  unchanged.**
* **Converter present** (this tree) — the converter rules first, at `intake_v2.py:605`, and refuses
  `quotient-exact` **by name on WR's topology gate**. The refusal is now *more* specific, not less: it
  names the topology, not just the profile.

Either way nothing is priced: `build_measured_request` fails the test if reached, and `publisher.sent` stays
empty. **G3 at `intake_v2.py:278-280` stays closed** — the unconditional `check_legacy_full_run_source`. With
the topology gate, every profile label refuses anyway, so opening it would buy nothing and risk pricing a
full-run profile as the reference. **What ships for a design is the explicit per-family path.**

### Handoff 2 — WT's sampled Step 2 fallback vs the QFT pool

Settled from the code, not by merge order.

**1. WT does not take its own share.** `sampled_fallback.py` contains **no reference** to `sto_currency`,
`ROTATION_FRACTION`, `rotation_eps` or `ts_per_rotation`. It receives `eps` — "the per-site half-diamond" —
as a **caller parameter**, and its `budget_seconds` is a wall-clock budget, not an error budget. It also has
**no production caller** anywhere in the tree: `cost_policy_fallback` and `price_step2_budgeted` are
referenced only by `test_sampled_fallback.py`. So nothing composes today, and WT cannot double-spend alone.

**2. But the pool has no slack, so a second spender *would* double-spend.** The overlay partitions the pool
**exactly** over the whole rotation census. Measured on the real engine at delta 1e-2
(`ROTATION_FRACTION = 0.5`, pool = 1/200 = 0.005):

```
R_total 3802591380 = R_cat 350171640 + R_synth 3452419740   (exact)
R_synth*(pool/R_total) + deltaArm = 0.004999999999999999
pool                             = 0.005                    (residual -8.7e-19)
```

Every rotation is already allocated. Step 2's phase-product rotations are part of that same census, so
pricing them a second time against their own `eps` would **charge the same budget twice and hand the
submission free error** — the defect the catalytic arm's own docstring refuses ("would spend the catalysed
rotations' slices twice … and would hand catalysis free error").

**3. So the combination is REFUSED** — and already was, by name, at three independent places:
`BEYOND_QFT` for the profile (`quotient-step2-fallback`), `_design_of_body`'s phase-product check for an
explicit body (`step1`/`step2`/`step4`/`step5`), and `qft_overlay.parse_design`'s
`OUT_OF_SCOPE == ("step2", "step4")` for the selector fields. **No new refusal was needed.** The three are
now pinned together with the arithmetic by
`test_a_qft_design_and_a_step_2_fallback_cannot_be_asked_for_together`, which also fails if
`sampled_fallback` ever starts reading the currency or acquires a production caller — so the refusal cannot
be relaxed without confronting the numbers. **The same call WY made for readout + design, for the same
reason.**

---

## The row/design seam (lane XD) — decided: **add neither key**

XD found that `forshor_bot/leaderboard.py:_design` reads a design off the published **row** while
`mapping.row_of` copies no design key onto it, and proposed adding the design identity to the row,
"mirroring how the row already carries what it needs for `readout`". **Checked first, as instructed, and
the mirror does not exist.** Established from the code on both sides:

1. **`rowSchema` is `z.strictObject({...rowFields, rank})`** (`results-v2.ts:209-215`). A Zod
   `strictObject` **rejects** unrecognised keys. `rowFields` (`:194-207`) names **no `design` and no
   `readout`**. Adding either to `row_of` would make **every** published row fail the contract lane XB
   pinned — not just design-bearing ones.
2. **There is no key-shaped precedent to mirror.** The row carries what it needs for `readout` **not as a
   key** but through `ranked_price_of`, which substitutes the readout's own T and qubits into the row's
   existing `T` / `qubits` fields. `row_of` has never copied a `readout` key. So XD's premise — that
   `readout` is on the row — is false, exactly as XD suspected it might be.
3. **The bot cannot read it from the panel either.** The panel's `fullRun` is itself a `strictObject` whose
   only optional price is `readout` (`:408-412`), with **no `qft` key at all**. A design-bearing panel is as
   unpublishable as a design-bearing row.

So the absent-key branch of `_design` (absent means `reference`, which is statable) is **always correct**: a
design-bearing row cannot be published at all — intake refuses the compiled `submitted_full_run` key
outright and the source gate stays closed. `_design` is already fail-closed for anything else: an
unrecognised design is kept verbatim and **not** stated.

**The smaller correct change is therefore a guard, not a schema change.**
`test_the_row_names_no_design_and_no_readout_because_the_site_contract_has_no_field_for_either` pins
`row_of`'s emitted key set against the site's own `rowFields`, so the key the bot wants cannot be added
without going red first.

**This matters more than the comment it replaces**, because one of the bot's stated justifications is now
**stale**. It reads "the ranked price at mapping.py:234 still reads `readout`, never `qft`" — but WY's
`ranked_price_of` **does** read `qft` (the owner's Tier 2 ruling). A design-bearing row would now silently
carry the projection's T and qubits with nothing naming the design — precisely the "announce an option's
score as if it were the plain one" harm the bot guards against. The only things preventing it are the intake
refusals and the absent site field. **Reported to lane XD; their repository was not touched.**

## Hooks applied

* **XA's `pricing.py` hook — applied.** `price_views(..., primitive=None, preparation=None)`, and in the
  single-product branch after `view = eng.policy_view.view(...)`:
  `if primitive: from .primitive_overlay import project; view["primitive"] = project(P, view, primitive, preparation=preparation)`.
  Same shape as the two full-board hooks above it. **Verified end to end, not assumed**: with no primitive
  the view carries no `primitive` key (so every published panel is byte-identical), and with one the hook's
  output equals calling `primitive_overlay.project` directly, on both `pp` and `ptp` (catalyticB2,
  `rankedT` 26478500).
* **WX's `qubits` hook — did not close itself.** See *Integration defect B*. Fixed in `qft_overlay.py`.

## Reconciled suite counts

Per-lane figures were re-measured in each lane's own worktree and **every one reproduces the brief exactly**:

| Worktree | Head | `export_v2/pytests` (fast) |
|---|---|---|
| `submissions-INT` | `792464f` | 165 passed, 56 skipped |
| `submissions-XE` (wave 1) | `f56bbc0` | **293 passed, 60 skipped** |
| `submissions-WP` | `aaf19a8` | 244 passed, 83 skipped |
| `submissions-WR` | `cac3ac9` | 279 passed, 90 skipped |
| `submissions-WX` | `36c82f9` | 245 passed, 96 skipped |
| `submissions-WY` | `9968b30` | 260 passed, 91 skipped |
| `submissions-XA` | `fcf542f` | 170 passed, 86 skipped |

### After each merge

| After | `export_v2/pytests` | `scripts/ci` | `test_archive_policy.py` | `test_contributor_cli.py` |
|---|---|---|---|---|
| base `f56bbc0` (wave 1) | 293 passed, 60 skipped | 100 p + 96 sub | 36 passed | 54 p + 22 sub |
| 1 · WP | *1 failed*, 371 passed, 87 skipped | 100 p + 96 sub | 36 passed | 54 p + 22 sub |
| 2 · WR (resolved) | 423 passed, 100 skipped | 100 p + 96 sub | 36 passed | 54 p + 22 sub |
| 3 · WX (resolved) | 425 passed, 115 skipped | 100 p + 96 sub | 36 passed | 54 p + 22 sub |
| 4 · WY (resolved) | 441 passed, 122 skipped | 100 p + 96 sub | 36 passed | 54 p + 22 sub |
| 5 · XA + hooks + seams | **448 passed, 152 skipped** | **100 p + 96 sub** | **36 passed** | **54 p + 22 sub** |

The single failure after merge 1 is handoff 1 arriving on schedule — WQ's no-converter test, with the
converter now present. It was resolved at merge 2 rather than merge 1 because **WR is what settles the
refusal message**: at WP alone `quotient-exact` still converted and priced.

`--runslow`, **once**, at the final head `6b56806`
(`cd evaluation && pytest export_v2/pytests -q --runslow`): **599 passed, 1 skipped** in 4m28s.

### Why the total is what it is — reconciled by file, which is exact

A naive sum of per-lane deltas understates the total, because the five lanes overlap on files (WR, WX and
WY all ship their own `test_full_run_design.py` and `test_qft_overlay.py`). Reconciling **by file** is
exact. Collection is identical in both modes: **600 tests collected** at the final head,
353 at wave 1.

| File | Owning lane's count | At `6b56806` | Δ |
|---|---|---|---|
| `test_full_run_design.py` | WR 29 | 29 | 0 |
| `test_full_run_design_explicit.py` | WR 59 | 59 | 0 |
| `test_qft_overlay.py` | WX 74 | 74 | 0 |
| `test_design_price_plumbing.py` | WY 24 | 24 | 0 |
| `test_primitive_overlay.py` | XA 35 | 35 | 0 |
| `test_full_run_chain.py` | WP/WX/WY 19 | 26 | **+7** |
| | **240** | **247** | **+7** |

`353 (wave 1) + 247 = 600`, measured. **Every file is at its owning lane's count**, so no test was lost
anywhere; the only movement is `+7` in `test_full_run_chain.py`, all of it wave 2's own work:

* `+2` — the deliverable test went from one profile to three explicit designs;
* `+1` — the catalytic-refusal test (2 profiles) became the topology-gate test (3 profiles);
* `+2` — WX's catalytic pricing test, re-pointed, parametrised over the two catalytic combinations;
* `+1` — `test_the_explicit_design_prices_every_combination_the_profile_labels_named`;
* `+1` — `test_a_qft_design_and_a_step_2_fallback_cannot_be_asked_for_together` (handoff 2).

A name-level diff confirms **no dropped coverage**: every test name present in WP's or WX's chain file and
absent at the final head has a one-for-one renamed successor. (One of those renames,
`test_a_design_is_refused_at_every_delta_but_1e_2`, is **WY's** own, not wave 2's.)

The single remaining skip under `--runslow` is environmental, not slowness, and is the same one every lane
reports: `test_engine_backed.py:76: FORSHOR_SITE_DIR not set: built the request but did not run the
TypeScript validator`.

Other suites at the final head: `scripts/ci` **100 passed, 96 subtests**; `evaluation/test_archive_policy.py`
**36 passed**; `scripts/test_contributor_cli.py` **54 passed, 22 subtests** — all unchanged from wave 1, as
expected, since the chain touches only `evaluation/export_v2`.

## Hard gates at the final head `6b56806`

Quoted verbatim.

```
epoch           : ab1f8887b1b50e56
dataFingerprint : 176ce4047f5957f1
engineRevision  : 677cc2e05ab178afff8dc632ff56058a8196a116
```

```
$ git diff 792464f -- evaluation/engine
(empty)
$ git diff f56bbc0 -- evaluation/engine
(empty)
```

`evaluation/export_v2/readout_overlay.py` — the value pinned in 8 places across 4 files on the site side:

```
sha256(readout_overlay.py) = 53a7e7ba32f12fe7de423b6d19e3ebdc458fc82b99cdaa7e0eaadeb93a45e032
rule_fingerprint()         = 53a7e7ba32f12fe7de423b6d19e3ebdc458fc82b99cdaa7e0eaadeb93a45e032
VERIFIER_PIN               = 80d7b6b2d0e696df46f1fa6ed928cbd1d9d7d6c5
blob                       = aa736ac
```

**Exactly the required value, and not moved by one byte.** The blob is byte-identical to WQ's, as
`INTEGRATION-WAVE1.md` records. No lane in this wave touches the file.

The no-design request stays byte-identical — measured directly, not only asserted:

```
full             998ad142a47fae05466541a3a92e0851cb86bac91876ce409ec4a98b3e0b19e3
pp               c83bf7a532a639d1b16d2e1e9b86b26e2bef36e8db96137e2d259a72d527179f
whole request    6641a96743ab92aedea0f65d9b883fa745985dfc5931e9e4c4a22967ce86896d
XA default price 36f1945013af84ba4326e3e1cd4dc8d21c05e0c45dfdc5b4675e435eba6dbdd1
```

The `full` value is the one lane WR re-pinned away from; it is restored (*Integration defect C*). XA's
digest is accompanied by its own sensitivity test (`test_the_red_perturbation_moves_the_baseline`), so it is
a live pin and not a constant that happens to match.

Verification-identity fingerprint:

```
$ python scripts/ci/verification_identity.py --head 6b568068dd8ed850fa6c4cf0145138e152e1cef6
{"schema": "forshor-verification/v1",
 "key": "52e543dc7c2ab0b792c0a8320bc5cd579b2a6d6b29a0327e02917af0cfa3b1ce",
 "headSha": "6b568068dd8ed850fa6c4cf0145138e152e1cef6",
 "fingerprint": "36d74218cd07e743301eccec28bdf7ba8207f637a9093f77878ea6f5da0f995a"}
```

It moves, as expected, because `evaluation/export_v2/` is an identity input and this wave changes it —
allowed under AGENTS.md rule 5; it means an already-verified head is re-verified rather than reusing its
receipt. The Lean-cache inputs (`TableGeneration/`, `lakefile.lean`, `lake-manifest.json`, `lean-toolchain`,
`evaluation/policy-trees.json`) are **unchanged**, so no cache is emptied and no cold build is triggered
(rule 2).

Operational pin:

```
$ grep -n 80d7b6b2d0e696df46f1fa6ed928cbd1d9d7d6c5 evaluation/policy-trees.json
5:    "ref": "80d7b6b2d0e696df46f1fa6ed928cbd1d9d7d6c5"
$ git diff 792464f HEAD -- evaluation/policy-trees.json
(empty)
```

**Still at the operational pin; nothing repointed.**

WU's price-surface guard:

```
price surface at 6b568068d (manifest 792464f83)
  dataFingerprint 176ce4047f5957f1  engineRevision 677cc2e05  qualtran 0.8.0.dev0
  caches: 16 committed, 0 not matching their own fingerprint
  moved, advisory only: export_v2.model
OK: no price-relevant path moved while the epoch stood still
```

It passes for the right reason. Every **enforced** group is under `evaluation/engine/**`, which this wave
leaves byte-identical. The one `export_v2` group (`export_v2.model`) is **observe-only** and covers
`epoch.py`, `canon.py`, `pricing.py` and `mapping.py`; it reports as *moved, advisory only* because this
wave does change `pricing.py` (XA's hook, the merged docstring) and `mapping.py` (WY's `ranked_price_of`).
That is the expected, non-blocking signal — the guard is armed, and nothing enforced moved.

## What the release candidate now contains

On top of integration wave 1 (WJ refusal specificity + the full-run opt-in, WQ's readout pin fix and
publication-gate lift, WT's sampled fallback, WU's price-fingerprint guard, WE2's materials):

* **A complete, fail-closed QFT design chain** — compiled document → `full_run_design.selection_of` →
  `design_of` → `pricing.price_views(design=)` → `qft_overlay.project` → `mapping.panel_of`, with the
  design priced **outside** the epoch so `engineRevision` and `dataFingerprint` stand still.
* **The topology gate.** Every `quotient-*` and `split-*` profile label refuses by name. Quotient
  customization is deferred, by owner decision, and that is recorded as an executable fact rather than a
  comment.
* **The explicit per-family design on the reference topology** — `Gate5Design.Design.json`'s own wire
  shape — pricing arbitrary combinations of `data` / `work` / `scratch` / `exponent`, with value validation
  delegated to `qft_overlay` so there is one validator in the repo and not two.
* **A genuinely differentiated full-run price**: WX's catalytic arm, with all three charges — its own
  proportional share of the synthesis pool, the shared catalyst register charged to Q, and
  `catalyst_prep_T` as a separate unconditional charge — and that register now **reaching the ledger row**.
* **The ranked price reading the projection**, scoped to delta 1e-2 and the full board only, with the
  three other deltas priced by the call `792464f` made — no `design=None` keyword anywhere.
* **Standalone B2/B3 primitive pricing** on `pp` and `ptp`, wired through XA's hook.
* **Refusals that stay refusals**: readout + design do not compose; a QFT design + a Step 2 fallback cannot
  be asked for together; the phase-product steps and rotation backends are out of scope; G3 stays closed;
  and no design-bearing row or panel has any publication route.

### What is deliberately *not* in it

* Quotient and splitCompare topology customization — dropped from this release, deferred.
* Any publication route for a full-run design: it remains a **nonpublishing qualification**.
* Any production caller for WT's sampled Step 2 fallback.
* Any `design` or `readout` key on the ledger row — the site contract has no field for either.
