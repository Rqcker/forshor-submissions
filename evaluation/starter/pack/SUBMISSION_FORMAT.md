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
| `bands` | At least one band, widest first. Thresholds are non-negative integers and strictly decrease. A band serves every width w ≥ its threshold that is below the previous band's threshold. Below the last threshold, the leaf is used. |
| one table per k | Two bands with the same k must use the same table. |
| `k` | 2 ≤ k ≤ 8 for a submission. A PP table evaluates at 2k − 1 points, a PTP table at 3k − 2. The boards price a policy of any k, but a submission takes 2 ≤ k ≤ 8 for both PP and PTP (section 2). |
| `point_order` | The order in which the program consumes the points: `"z"` (an integer), `"1/m"` or `"-1/m"`, `"inf"`. Distinct and unisolvent. |
| `operations` | `["addScaled", dst, src, sign, shift]` (sign +1 or −1, dst ≠ src), `["negate", i]`, `["shiftL", i, n]`, `["shiftR", i, n]` (exact division only), `["phaseProduct", i]`, on registers 0 to k − 1. The program consumes the points in order, restores the registers, and satisfies the exact Toom-Cook identity. |
| widths | **Do not declare widths.** The board derives the exact widths and prices at them. Any `declared_ladder`, `ladder`, `widths`, `child_width` (and similar) is ignored, with a notice. It is never passed to Lean (`starter.py lean-input` drops it). |
| `negation` | Optional: `"oneQubit"` (ForShor's default, `Shor.NegationMethod`) or `"scratch"`. The boards, and this pack, price every policy under `oneQubit`. A policy that asks for `scratch` is **refused by `starter.py`** (a number for it would be wrong), and its meaning is in section 3. |
| `name` | Optional, at most 200 characters. Other keys are ignored. |

The site also reads `{"policy": {...}}` and `{"policies": [...]}` (at most 8 policies per file).

## 2. The submission file `Policy.lean` (and the full order-finding board)

A submission is this one Lean file, `Policy.lean`, holding the policy: for PhaseProduct a ForShor `ShorPolicySubmission` (bands with 2 ≤ k ≤ 8), for PhaseTripleProduct the same file with a `PTPPolicy` (bands with 2 ≤ k ≤ 8; this pack has no template for it yet).
The full order-finding board prices every PP policy (section 1). ForShor builds the PhaseProduct file (`starter.py template` writes it from site policy JSON) at the pinned commit `21e129aeefc0fd1bbb8fa63402f5799af2490788`
(VerifiedQC/ForShor `main`, the merge of PR #80, which contains PR #79). Its Lean library is `ForShor`. If you write the file by hand, these rules apply:

* **One file: `TableGeneration/Submission/Policy.lean`, the only file a pull request may change**, at most 1,000,000 bytes. For ForShor's build it is
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
* The document is printed by `lake exe forshor_policy_submission --balanced`: exact register widths
  (`ir.width_rule`) and the balanced IR templates. At the pinned commit the document also prints the policy's `negation` and the per-method
  `negate_charge`. A per-submission rebuild touches only ForShor's `PolicyTemplate` module,
  `PolicyCheck` and `PolicyMain`.
* The printed `policy.json` must carry the organisers' fixed values: `benchmark.bits` 2048, `benchmark.trial_count`
  386, `precision.eta` 2^-150, `precision.m` = 2^150 − 3. `starter.py forshor-doc` says whether it does.

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
| PP | STO policy mirror, `check_banded_policy.py --widths exact` (the interim STO fork, branch `port/forshor-21e129a`) | the "STO mirror" tag (policy checked in Lean at exact widths) | Every band's table satisfies STO's table contract; the thresholds decrease; the evaluation equals φ·a·b; the register semantics is correct at the exact widths (`correct_tight`); the exact widths are the least overflow-free widths (`exact_ladder` at 2048); the threshold rule holds on the recursion from 2048 (`chain_contracts_tight`, `thresholds_at_target`), and the policy is correct concretely at n = 2048 (`correct_at_target`, `exact_ladder_at_target`). A policy whose thresholds are set too low is rejected at stage `threshold`. |
| PTP | `check_ptp_policy.py --widths exact` of the same fork, against ForShor's `Shor.PTP` | the same tag | The same, for φ·a·b·c (`correct_exact`). The register model is by analogy with PP. In addition (O2, cert `gate_recursion`): for every checked policy and width, a gate-level recursion following the policy exists and is correct through every depth (`PolicyImpl.specOn`), with its census equal to the policy formulas, using the suite's Toffoli+Rz+Toffoli sandwich leaf; since O5 the same gate-level result holds at the exact widths (`gate_recursion_exact`, `gate_recursion_exact_at_target`). The same threshold rule and at-target certificates as PP. Equal-width operands only. |
| Full run | ForShor `21e129a`, `ShorPolicySubmission` | ForShor policy-verified | C1 to C4 for each band (`decide +kernel`), the sorted thresholds, the exact register widths, and `policy_submission_correct` (circuit-level). |

The PP mirror level is **not circuit-level**: it proves nothing about the gates of the priced Qualtran circuit or of
ForShor's lowering, and it is weaker than "ForShor policy-verified". ForShor's PhaseTripleProduct gate-level result is about
its own sandwich leaf, not the digital leaf the board prices. No level proves the T
count. The full run's census is ForShor's IR, matched to the compiled circuit at sampled widths, not proved at
n = 2048.

## 5. Resources

The Lean checks are heavy and the pack contains none of the Lean build trees. Measured on the 15 policies the results seed lists (ForShor `21e129a`, one machine): most policies pass
the STO mirror in seconds to a few minutes; a PhaseProduct policy whose first band has k = 8 took about 2.4 hours in the mirror's report stage (three of them passed); the PhaseTripleProduct
policy with a k = 8 first band did not finish that stage in 12 hours (with the PhaseTripleProduct harness's `--fast` mode, see `harness/LEAN_CHECKS.md`, it takes about a minute). Expect hours for large PhaseProduct first bands, and keep a first attempt small (k up to 6, few bands). The organisers' own limits for a submission
are in the repository's instructions, not here.

## 6. Sending it

This pack prices a policy and prepares it for the Lean checks; **it does not submit anything**. **What you submit is one Lean file, `TableGeneration/Submission/Policy.lean`, holding your policy** (section 2; the
format is `templates/Policy.lean`, and `starter.py template` writes it for a PhaseProduct policy). A pull request may change that file and nothing else. The submission repository is
`zapatacomputing/forshor-submissions` (private during the sprint; the challenge page's **Participate** dialog has the commands), and its own instructions win over this file. The boards check the Lean file in Lean and price the policy it
contains with the same engine as `starter.py price`. Whatever you send, quote the `starter.py price` output you got for the same policy as JSON (and, for a full-run policy, the `forshor-doc` output), and name the pack's build date
(`MANIFEST.json`). The boards' numbers are the ones that count.

A policy that is accepted appears on the boards under your GitHub handle as `<handle>_sub<N>`, N counting your own submissions from 1; the handle `forshor` is reserved. The boards also list the
planner's own policies and nine deliberately weak single-arity baselines (all named `forshor_sub<N>`), ranked with yours by Q × T, and mark each row "STO mirror" if the policy has been checked in Lean.
