# Submission format (strict)

A submission to the ForShor Challenge is a **full policy**: Toom-Cook tables together with the width thresholds that
choose among them. A bare table, or a set of tables without thresholds, is not a submission. A policy that breaks a
rule below is refused before it is priced. `python starter.py price` applies the site's intake rules (1 to 3). The
Lean checks (4) are described below; the pack does not run Lean, and section 6 says what the pack does not do.

## 1. PP and PTP policies (site policy JSON)

One JSON object (UTF-8, at most 2,000,000 bytes):

```json
{"name": "my policy", "mode": "PhaseProduct",
 "bands": [{"threshold": 300, "k": 3, "point_order": ["0", "1", "inf", "-1", "-2"], "operations": [...]},
           {"threshold": 40,  "k": 2, "point_order": [...], "operations": [...]}]}
```

| Field | Rule |
|---|---|
| `mode` | `PhaseProduct` (PP board) or `PhaseTripleProduct` (PTP board). |
| `bands` | A submission takes 1 to 256 bands, widest first. Thresholds are non-negative integers and strictly decrease. A band serves every width w ≥ its threshold that is below the previous band's threshold. Below the last threshold, the leaf is used. |
| tables per k | Several bands may carry **different tables of the same k**, selected per width band. There is no separate limit: only the 256 bands. Each band carries its own table and is checked on its own, so nothing about the policy's shape has to change for this — C1 to C4 are proved per table and per band, and `sorted` constrains the thresholds only. An accepted policy on the boards uses 11 tables over 22 bands, with two different tables at each of k = 3, 5, 7 and 8. |
| `k` | 2 ≤ k ≤ 8 for a submission. A PP table evaluates at 2k − 1 points, a PTP table at 3k − 2. The boards price a policy of any k, but a submission takes 2 ≤ k ≤ 8 for both PP and PTP (section 2). |
| `point_order` | The order in which the program consumes the points: `"z"` (an integer), `"1/m"` or `"-1/m"`, `"inf"`. Distinct and unisolvent. The site's panel files (`policyJson`) spell this key `pointOrder`; `starter.py` accepts either spelling (a band that carries both with different values is refused). |
| `operations` | `["addScaled", dst, src, sign, shift]` (sign +1 or −1, dst ≠ src), `["negate", i]`, `["shiftL", i, n]`, `["shiftR", i, n]` (exact division only), `["phaseProduct", i]`, on registers 0 to k − 1. The program consumes the points in order, restores the registers, and satisfies the exact Toom-Cook identity. |
| widths | **Do not declare widths.** The board derives the exact widths and prices at them. Any `declared_ladder`, `ladder`, `widths`, `child_width` (and similar) is ignored, with a notice. It is never passed to Lean (`starter.py lean-input` drops it). The derived width of a table is the least width at which every value the program ever holds fits, **intermediate values included**, so a `shiftL` / `shiftR` pair that makes a register briefly larger raises it (see "Widths and shift pairs" below). |
| `negation` | Optional: `"oneQubit"` (ForShor's default, `Shor.NegationMethod`) or `"scratch"`. The boards, and this pack, price every policy under `oneQubit`. A policy that asks for `scratch` is **refused by `starter.py`** (a number for it would be wrong), and its meaning is in section 3. |
| `name` | Optional, at most 200 characters. Table JSON is not a circuit-design selection format: a policy or band field requesting readout, QFT, multiplier, primitive-synthesis or full-run-design options is refused whatever its value. The Lean file declares the design, and only the default declarations are accepted there (section 2). |

The site also reads `{"policy": {...}}` and `{"policies": [...]}` (at most 8 policies per file).

### Widths and shift pairs

Widths are derived, never declared: a table's child width at a given operand width is the least width at which every register value the table's program ever holds fits. Both exact-width rules count every
state the program visits, not only its final one: the policy check's `tightWidth` (over every reachable row) and ForShor's `exactSignedWidthOfWidths`. Two consequences follow, and both are intended.

* **A shift pair can raise a width.** A `shiftL` followed by the matching `shiftR` leaves the register unchanged, but between the two the register holds a larger value, and the rule counts it. For example, a
  `["shiftL", 2, 3]` and `["shiftR", 2, 3]` inserted into a table can make its child width at one size 480 where it was 479. The program's points, result and T count are unchanged. The shift gates themselves cost no T gates
  and no qubits (the full-run pricing charges them nothing; the PP and PTP board engine charges `n` CNOTs, which are Clifford). The register is allocated at the derived width, so a wider derived width is priced as a
  wider register. This is how a policy can choose a width above the minimum its points allow, for instance to make the next level divide evenly.
* **Widths follow the table, not the band.** The derived width depends only on the table and the operand width, so the same pair can change the child width at every size that table serves (some of them may not move), not only at one band's threshold.
  Read the ladder that `starter.py price` prints for every width the policy uses.

Declaring widths directly (for instance, any width at or above the exact one) is not supported: a declared ladder is ignored with a notice.

## 2. The submission file `Policy.lean` (and the full order-finding board)

A submission is this one Lean file, `Policy.lean`, holding the policy: for PhaseProduct a ForShor `ShorPolicySubmission` (bands with 2 ≤ k ≤ 8), for PhaseTripleProduct a ForShor `PTPPolicy` in `namespace PTPPolicySubmission` (bands with 2 ≤ k ≤ 8; the format is `templates/ptp.Policy.lean`, ForShor's `PTPPolicyTemplate.lean`, and `starter.py template` writes it from a PTP site policy). Either kind may have at most 256 bands. The namespace the file declares says which board it is for. Both are built by ForShor (`lake build PolicySubmission forshor_policy_submission`, or `lake build PTPPolicySubmission forshor_ptp_policy_submission`) after ForShor's lexical gate; the PhaseTripleProduct files are in ForShor `main` from `349849e` (PR #83), not in the pinned `21e129a`, which is otherwise unchanged by them.
The full order-finding board prices every PP policy (section 1). ForShor's Lean library is `ForShor`. For a manual verifier build, use the submissions repository's operational pin in `evaluation/policy-trees.json`; `./forshor check` selects it automatically. The pack's model pin `21e129aeefc0fd1bbb8fa63402f5799af2490788` identifies the pricing baseline, not the verifier checkout. If you write the file by hand, these rules apply:

* **One file: `TableGeneration/Submission/Policy.lean`, the only file a pull request must change** (the optional `TableGeneration/Submission/summary.tex`, section 6, is the only other file it may carry), at most 1,000,000 bytes. For ForShor's build it is
  copied to `ForShor/ShorVerification/Submission/PolicyTemplate.lean` (ForShor's name for the file); nothing else in the ForShor tree changes.
* Imports: only the three modules the shipped template imports (`ForShor.ShorVerification.Framework.ToomCookTable`,
  `ForShor.ShorVerification.Framework.Policy`, `ForShor.ShorVerification.Submission.Decide`).
* Per band, as in `templates/Policy.lean`: `k` (1 < k ≤ 8), the points in consumption order, the program, and
  the `ShorLoweringSetup` record with C1 to C4 proved (`decide +kernel`). Then
  `def policy : ShorPolicySubmission where bands := [(threshold, setup), ...]`, widest first, with
  `sorted := by decide`. PhaseProduct only.
* Banned anywhere, comments included: `#eval` and the other `#` commands, `run_cmd`/`run_meta`/`run_elab`,
  `native_decide`, `decide +native`, `sorry`, `admit`, `axiom`, `constant`, `opaque`, `unsafe`, `implemented_by`,
  `extern`, `csimp`, `initialize`, macros, `syntax` and `notation`, `set_option`, `import Lean` and `import Mathlib`,
  and `IO.`/`System.`. On top of this, ForShor's lexical gate (`scripts/check_template_lexical.sh`) applies.
* ForShor's check proves C1 to C4 by `decide +kernel` and extracts the IR at run time in the compiled exe, so k = 5
  to 8 tables and three or more bands pass.
* **The design selectors.** The submittable design is the default one: coherent readout, the exact logical QFT, Ross–Selinger
  synthesis of every rotation and `oneQubit` negation. Leaving the selectors out of the file gives exactly that, and writing a
  default out in full is accepted too. Every value of every selector is listed, with its real status, under "The design options,
  in three tiers" below. Read the tier before you write a selector: only tier 1 can be submitted for a score.
* The document is printed by `lake exe forshor_policy_submission --balanced`: exact register widths
  (`ir.width_rule`) and the balanced IR templates. At the pinned commit the document also prints the policy's `negation` and the per-method
  `negate_charge`. A per-submission rebuild touches only ForShor's `PolicyTemplate` module,
  `PolicyCheck` and `PolicyMain`.
* The printed `policy.json` must carry the organisers' fixed values: `benchmark.bits` 2048, `benchmark.trial_count`
  386, `precision.eta` 2^-150, `precision.m` = 2^150 − 3. `starter.py forshor-doc` says whether it does.

### The design options, in three tiers

Anything that chooses a **circuit design** — the readout, the QFT, the rotation-synthesis backend, the modular multiplier,
the per-step phase backends — is declared in the Lean source, **never** in the table policy JSON (see "Where an option is
declared", below). Every option the surface has is listed here with its tier. **The tier is the first thing to read about an
option**, because the three tiers are not degrees of the same thing:

| Tier | What it means | Hosted intake | Score |
|---|---|---|---|
| **1. Submittable** | the design a submission for a score uses | accepts, verifies, publishes | yes, it ranks on the boards |
| **2. Local qualification only, nonpublishing** | the verifier attests the choice on your own machine; there is **no hosted publication route** | refuses, naming the selector | **no score, ever** — attested and priced locally; not accepted by hosted intake; no score |
| **3. Not available** | no typed selector exists; nothing accepts it anywhere | refuses | no score |

Work is in flight to price the QFT axis. **The publication gate is untouched and nothing in tier 2 publishes today.**
Do not read "in flight" as "soon submittable": today a tier-2 submission is refused.

#### Tier 1 — submittable for a score

1. **The default design.** Coherent readout, the **exact logical QFT**, **Ross–Selinger** synthesis of every rotation, and
   `oneQubit` negation. Leaving all three selectors out of `Policy.lean` gives exactly this, and it is what every board row
   and every pinned example in this pack is priced under.
2. **An explicit default declaration, which is identical to omission.** Owner ruling of 2026-11-24 (landed in the submissions
   repository as commit `a4a7609`): hosted intake resolves an explicit default declaration exactly as it resolves omission.
   The explicit file and the omitting file build the same document, price the same, get the same policy digest and count as
   the **same submission in either arrival order**. The three declarations are, verbatim:

   ```lean
   def submittedPrimitive : Shor.PrimitiveSynthesis := .rossSelinger
   def submittedDesign : Shor.SubmissionDesign := { readout := .coherent }
   def submittedFullRunDesign : Shor.FullRunDesign := .reference
   ```

   `submittedDesign` may equivalently use the two-line `where` form, with `readout := .coherent` indented on the next line.
   `submittedFullRunDesign` is recognised **on the PhaseProduct board only** (`namespace PolicySubmission`); ForShor's
   `PTPPolicyCheck` refuses it on the PhaseTripleProduct board even at `.reference`. The other two are recognised on both
   boards. Declaring `submittedFullRunDesign` is also the only reason the file may add the import
   `ForShor.ShorVerification.Framework.FullRunDesign`.

   **The recogniser's shape is exact.** A declaration is read as the default only when all of this holds, and it is a lexical
   check — nothing is run:
   * one `def`, starting at **column 0**;
   * **directly** inside the board's namespace (not nested in a further namespace or section, not in another namespace);
   * annotated with ForShor's own type (`Shor.PrimitiveSynthesis`, `Shor.SubmissionDesign`, `Shor.FullRunDesign`; the
     `_root_.Shor.` prefix and the fully qualified value, e.g. `Shor.PrimitiveSynthesis.rossSelinger`, are accepted);
   * the **default value**, and **nothing else on the statement** — no continuation line, no value on the next line;
   * **exactly once** per selector;
   * with no modifier or attribute line immediately before it (`private`, `protected`, `noncomputable`, `@[…]`);
   * written as `def`. An `abbrev`, an indented `def`, or a `def` with no type annotation is not the default declaration.

   Anything outside that shape — any other value, a second copy, a decoy namespace, a bare mention of the name anywhere
   else in the file — is refused with the tier-2 message for that selector, below. A refusal of a *mis-shaped default* says
   the same thing as a refusal of a *real opt-in*: if you meant the default, delete the declaration or rewrite it exactly as
   above; the value was already the default and you lose nothing.
3. **Several tables of the same arity `k` in one policy — the relaxed PhaseProduct** (section 1, the `tables per k` row).
   Tables of the same `k` may be selected **per width band**, and the only limit is the 256 bands a submission may have.
   **No verifier change is needed**: C1 to C4 are proved per table and per band, and `sorted` constrains the thresholds
   only. An accepted policy on the boards does this with **11 tables over 22 bands, two different tables at each of
   k = 3, 5, 7 and 8** (the pack's `test_pack_multi_table_same_k` prices exactly that policy).

#### Tier 2 — local qualification only, nonpublishing

Each of these is a **real, typed choice that the verifier attests** when you run it yourself. None of them has a hosted
publication route. For every one: **attested and priced locally; not accepted by hosted intake; no score.** Do not submit
one, and do not treat its local number as a board number.

| Option | Declaration | Hosted refusal, verbatim |
|---|---|---|
| Semiclassical (measured) readout | `def submittedDesign : Shor.SubmissionDesign := { readout := .semiclassical }` (PP and PTP) | `submittedDesign is not enabled for ordinary submissions; it requires local readout qualification` |
| Standalone primitive, catalytic B2 | `def submittedPrimitive : Shor.PrimitiveSynthesis := .catalyticB2` (PP and PTP) | `submittedPrimitive requires separate nonpublishing primitive qualification` |
| Standalone primitive, adaptive B3 | `def submittedPrimitive : Shor.PrimitiveSynthesis := .adaptiveB3` (PP and PTP) | `submittedPrimitive requires separate nonpublishing primitive qualification` |
| Any of the 7 non-default full-run profiles | `def submittedFullRunDesign : Shor.FullRunDesign := .<profile>` (PhaseProduct board; `PTPPolicyCheck` refuses the name outright on PTP) | `submittedFullRunDesign requires dedicated nonpublishing full-run qualification` |

Even a receipt produced by the local readout-qualification route cannot be published. `publish_verification` refuses
**every measured receipt at its first gate**, before anything else about the submission is looked at:

```
qualified readout receipts are local only; publishing is not enabled
```

**The full-run profiles are a closed enum of 8.** `Shor.FullRunDesign` has exactly these constructors and no others — there
is no way to ask for anything else, and `reference` is the tier-1 default. What each selects (the modular multiplier, the
per-family QFT choice, and the per-step phase backend):

| # | `submittedFullRunDesign :=` | attested label | modular multiplier | data QFT | work QFT | scratch QFT | Step 2 | Step 4 | rotations |
|---|---|---|---|---|---|---|---|---|---|
| 0 | `.reference` | `reference` | the reference (coherent) multiplier | — | — | — | default | default | default |
| 1 | `.quotientExact` | `quotient-exact` | quotient stream | exact direct | exact direct | — | default | default | default |
| 2 | `.quotientCatalytic` | `quotient-catalytic` | quotient stream | approximate catalytic | approximate catalytic | — | default | default | default |
| 3 | `.quotientBandedDataCatalyticWork` | `quotient-banded-data-catalytic-work` | quotient stream | banded direct | approximate catalytic | — | default | default | default |
| 4 | `.quotientFallbackDataCatalyticWork` | `quotient-fallback-data-catalytic-work` | quotient stream | banded direct | approximate catalytic | — | default | default | **data rotations: fallback** |
| 5 | `.quotientStep2Fallback` | `quotient-step2-fallback` | quotient stream | approximate catalytic | approximate catalytic | — | **fallback** | default | default |
| 6 | `.splitStep4Kicks` | `split-step4-kicks` | split compare | approximate catalytic | approximate catalytic | approximate catalytic | default | **selective kicks** | default |
| 7 | `.splitStep2FallbackStep4Kicks` | `split-step2-fallback-step4-kicks` | split compare | approximate catalytic | approximate catalytic | approximate catalytic | **fallback** | **selective kicks** | default |

"Direct" is Ross–Selinger synthesis of the QFT's own rotations; "catalytic" is the catalytic route; "exact" is the exact
logical QFT and "approximate"/"banded" is the approximate one, in both cases at the one staged error ceiling (25e-5 = 1/4000).
A `—` means the family is not selected at all, and `default` means that slot carries no override, so the default
(Ross–Selinger synthesis of that step's rotations) stands — there is no selector to set it explicitly. Note that **`reference` selects nothing**: a `reference` document carries no
`submitted_full_run` key, which is exactly why the explicit `.reference` declaration is identical to omission (tier 1).
Several families and slots are **never** set by any of the 8 — the `exponent` QFT, `workRotations`, `scratchRotations`,
`exponentRotations`, `step1` and `step5`. Those are tier 3.

All 7 non-`reference` profiles are **semiclassical by construction**: ForShor's own executable refuses them in any other
mode, with

```
full-run synthesis requires saved semiclassical readout and --balanced --readout submitted
```

and refuses mixing one with a standalone primitive at all, with `full-run and standalone synthesis opt-ins are not jointly
qualified`. Both reach a participant through hosted intake as `ForShor's check refused the policy: <the last 500 characters>`,
so the text above is what you see quoted back.

#### Tier 3 — not available (so you do not waste a submission)

Nothing here can be selected, locally or otherwise. There is no typed selector for it.

* **Arbitrary per-family QFT combinations beyond the 8 named profiles.** `Shor.FullRunDesign` is a closed enum, so a
  per-family mixture that is not one of the 8 rows above cannot be written down. In particular: the whole `exponent` QFT
  family, the exact-catalytic QFT choice, banded-direct on `work` or `scratch`, and any error ceiling other than the one
  staged value.
* **Step 4 adaptive fallback.** This is a *different thing* from Step 4 selective kicks: kicks are profiles 6 and 7 of
  tier 2, adaptive fallback has no construction and is in no profile.
* **Controlled Steps 1 and 5, and broader QFT mixes.** `step1` and `step5` are set by no profile.
* **Rotation fallback on `work`, `scratch` or `exponent`.** Only the data family's rotation fallback exists, as profile 4.
* **Any precision other than δ = 1e-2.** The boards, the board snapshot, the exporter's row keys and every pinned example in
  this pack are at δ = 0.01, and that is the only precision the boards rank against. There is no precision control to set,
  and `starter.py` exposes none beyond its documented default of 0.01.

### Where an option is declared: Lean source, never table JSON

This is the mistake most likely to stop a run, so it is worth stating twice. **Design options live in `Policy.lean`.**
The table policy JSON of section 1 is not a design-selection format, and the pack's own harness refuses one that tries:
a policy or band object carrying any of

`readout`, `submittedDesign`, `submitted_design`, `submitted_readout`, `qft`, `modmul`, `submittedPrimitive`,
`submitted_primitive`, `submittedFullRunDesign`, `submitted_full_run`, `phase_product`, `synthesis`, `synthesis_backend`

is refused **whatever its value**, with the keys it found named:

```
table policy JSON cannot select circuit design options (<the keys>); use the supported Lean policy interface
```

The same check runs in `starter.py` and at hosted intake, so a JSON design key fails identically in both. Putting
`"readout": "coherent"` in the JSON does **not** express the tier-1 default — the default is what you get by writing nothing.

### A refusal, an incomplete attempt and a pass are three different outcomes

A refusal and a resource stop look similar in a log and mean opposite things:

| Outcome | What it is | What to do |
|---|---|---|
| **Refused** | the submission was examined and does not qualify; the record carries `refused`, a `stage` and a `reason` | fix the named rule |
| **Incomplete** | a **timeout or resource stop** — the check never finished. Its text ends *"This is not a verdict on the policy."* | **retry.** It is not a verdict and it is **never** a refusal and **never** a pass |
| **Pass** | the checks completed and the policy qualifies | nothing |

An incomplete attempt says nothing at all about your policy: do not change the policy because of one, and do not report
one as either a rejection or an acceptance. Memory and time stops on the hosted path are reported as
**Stopped: memory limit reached** and **Stopped: time limit reached** for the same reason.

**A note on the exact wording.** The refusal messages quoted in this section are the ones this pack was built against.
They may become **more specific** — several of them are reachable from more than one cause, and work is in progress to
name the cause. The rule each one is about does not change; the sentence may gain detail.

## 3. How a submission is priced

**The score is Q × T** (qubits times T count, lower is better), on each board separately, at n = 2048, δ = 0.01. **Q is the live-qubit peak**: the most qubits that
exist at any one moment of the circuit. Each recursive level activates the qubits that grow its chunks to the next width, then holds the larger of one child's peak and
its arithmetic's scratch (1 qubit for an addition or for a negation by the one-ancilla increment), the children sharing one pool. A PhaseProduct adds its two operand
registers, a PhaseTripleProduct its three (and two ancillas for its leaf, as ForShor states them), and the full run its base registers (12,890 at n = 2048). It is a formula
over the policy's widths, not a Lean-proved count for your own policy (ForShor has checked it for the board's own policies; the page says which). `starter.py price` prints Q,
the score, the policy's static-layout Q as a cross-check, and where the score would rank against the board's rows when the pack was built.

**Negation.** A table that negates a register builds it as the complement plus Gidney's one-ancilla increment (ForShor's `oneQubit`), with ForShor's explicit gate counts:
Toffoli `12w - 28` (w even) or `10w - 18` (w odd), CNOT `17w - 22` or `17w - 5`, X `5w - 3` or `5w + 1`, one clean qubit. ForShor also has `scratch` (a clean w-register of ones and
a Cuccaro adder, `w + 1` clean qubits, `2w - 3` Toffolis): fewer T and about 4,400 more live qubits on the full run, so a worse score there. The boards price `oneQubit` only.

* **PP and PTP boards:** Q and T of one phase product (or triple product) at n = 2048, δ = 0.01, at the exact widths. The
  policy is priced as written: its thresholds are never re-planned. Two engines count it (tcost and
  forshor_qualtran), and both must agree. ForShor's scanned widths are shown as a cross-check, not as the score.
* **Full order finding:** Q and the single-run T of the whole order-finding circuit at n = 2048, on the IR the ForShor
  prints under `--balanced` (Lean builds full Shor, then IR, then the dictionary, then Qualtran bloqs), priced exactly: exact
  register widths (ForShor's `exactSignedWidthOfWidths`), the balanced split for unequal operands (Steps 1, 2 and 5
  padded to 2348 × 2348, Step 4's 2348 × 4399 product as two 2348 × 2348 products), m2048, Step 4 `cmpLtNW`, 4095
  modular multiplications, the Qualtran digital-product leaf, Gidney adders, ForShor's negation counts above and Ross–Selinger rotations. The
  end-to-end T is the trial count (386) times the single-run T. Upstream ForShor's own IR (scanned widths, no split)
  is shown as a labelled cross-check (`forshor_scan_ir`), not as the score.
* **PTP leaf:** the digital triple product (x·y·z computed exactly with two Qualtran products and sign folds, one
  rotation per bit of the product). The schoolbook grid leaf is a labelled cross-check.
* T counts are Qualtran's. Lean's gate census (Cuccaro adders, grid leaf) is a labelled cross-check. A rotation is
  never priced at 11 T.

## 4. What Lean certifies (and what it does not)

| Board | Checker | When it passes, the site shows | What is proved |
|---|---|---|---|
| PP | policy check, `check_banded_policy.py --widths exact` (in the `forshor-submissions` repository, `main`) | the policy is listed as checked in Lean at exact widths | Every band's table satisfies the table contract; the thresholds decrease; the evaluation equals φ·a·b; the register semantics is correct at the exact widths (`correct_tight`); the exact widths are the least overflow-free widths (`exact_ladder` at 2048); the threshold rule holds on the recursion from 2048 (`chain_contracts_tight`, `thresholds_at_target`), and the policy is correct concretely at n = 2048 (`correct_at_target`, `exact_ladder_at_target`). A policy whose thresholds are set too low is rejected at stage `threshold`. |
| PTP | `check_ptp_policy.py --widths exact` of the same repository, against ForShor's `Shor.PTP` | the same tag | The same, for φ·a·b·c (`correct_exact`). The register model is by analogy with PP. In addition (O2, cert `gate_recursion`): for every checked policy and width, a gate-level recursion following the policy exists and is correct through every depth (`PolicyImpl.specOn`), with its census equal to the policy formulas, using the suite's Toffoli+Rz+Toffoli sandwich leaf; since O5 the same gate-level result holds at the exact widths (`gate_recursion_exact`, `gate_recursion_exact_at_target`). The same threshold rule and at-target certificates as PP. Equal-width operands only. |
| Full run | ForShor at the operational pin in `evaluation/policy-trees.json`, `ShorPolicySubmission` | ForShor policy-verified | C1 to C4 for each band (`decide +kernel`), the sorted thresholds, the exact register widths, and `policy_submission_correct` (circuit-level). |

The PP policy-check level is **not circuit-level**: it proves nothing about the gates of the priced Qualtran circuit or of
ForShor's lowering, and it is weaker than "ForShor policy-verified". ForShor's PhaseTripleProduct gate-level result is about
its own sandwich leaf, not the digital leaf the board prices. No level proves the T
count. The full run's census is ForShor's IR, matched to the compiled circuit at sampled widths, not proved at
n = 2048.

## 5. Resources

The Lean checks are heavy and the pack contains none of the Lean build trees. Measured on earlier policies (ForShor `21e129a`, one machine): most pass
the policy check in seconds to a few minutes. The policy check's interpreted report stage is the slow part (a PhaseProduct policy whose first band has k = 8 took about 2.4 hours in it, and a PhaseTripleProduct one with a k = 8 first band did not finish it in 12 hours); the hosted check skips that stage (`--fast`, see `harness/LEAN_CHECKS.md`), and the kernel certificate then proves the ladder: such a policy takes about two minutes in the policy check. Keep a first attempt small (k up to 6, few bands). The organisers' own limits for a submission
are in the repository's instructions, not here.

## 6. Sending it

Once installed, the pack prices and prepares policies offline without sign-in; **it does not submit anything**. Run `./forshor check` in the submissions checkout to sign in, fetch the trusted base, and verify locally without opening a pull request. **What you submit is one Lean file, `TableGeneration/Submission/Policy.lean`, holding your policy** (section 2). `starter.py template` writes it for both PhaseProduct (`templates/Policy.lean`) and PhaseTripleProduct (`templates/ptp.Policy.lean`). A pull request may change that file and one optional second file, `TableGeneration/Submission/summary.tex`, and nothing else. The optional summary is LaTeX written for people to read, in its own file (not inside `Policy.lean`): a regular UTF-8 file of at most 100 KiB (102,400 bytes), not empty, with no control characters except tab and line breaks; it cannot be submitted without a policy; the harness never compiles or runs it, and it never affects scoring (template: `examples/summary.tex`; details: `SKILL.md`, "Optional LaTeX Summary"). The submission repository is
`zapatacomputing/forshor-submissions` (private during the sprint; the challenge page's **Participate** dialog has the commands), and its own instructions win over this file. The boards check the Lean file in Lean and price the policy it
contains with the same engine as `starter.py price`. Whatever you send, quote the `starter.py price` output you got for the same policy as JSON (and, for a full-run policy, the `forshor-doc` output), and name the pack's build date
(`MANIFEST.json`). The boards' numbers are the ones that count.

A policy that is accepted appears on the boards under your GitHub handle as `<handle>_sub<N>`, N counting your own submissions from 1; the handle `forshor` is reserved. The boards start with the starting policies, built from the tables in the “Parallel sequences” appendix of Kahanamoku-Meyer and Yao (2024) (named `forshor_sub<N>`), ranked with yours by Q × T, and mark each row "policy check" if the policy has been checked in Lean.
