# ForShor Challenge starter pack

Use this pack to write a policy, price it locally with the challenge site's own engine, and get it ready for the Lean
checks before you submit. The challenge runs from November 12 to December 12, 2026 (the challenge page has the current dates).

A submission is a **full policy**: Toom-Cook tables and the width thresholds at which each table is used. A bare table
is not a submission. There are three boards, each ranked separately by **Q × T** (qubits times T count, lower is better, at n = 2048 and δ = 0.01), where **Q is the live-qubit peak**
(the most qubits that exist at any one moment; `SUBMISSION_FORMAT.md` section 3):

| Board | What you submit | How it is priced | What Lean certifies |
|---|---|---|---|
| PhaseProduct (PP), Q × T of one phase product at n = 2048 | a banded PP policy (site policy JSON) | Python (Qualtran costing) at the **exact** widths, which the board derives | the policy's tables and thresholds, through the STO policy mirror |
| PhaseTripleProduct (PTP), the same for a triple product | a banded PTP policy (site policy JSON) | Python (Qualtran costing) at exact widths, digital triple-product leaf | the policy's tables and thresholds at exact widths, through the PhaseTripleProduct harness (ForShor's `Shor.PTP`); ForShor also proves a gate-level recursion following the policy correct, at the scan and at the exact widths (O2, O5; with the suite's own Toffoli+Rz+Toffoli sandwich leaf, not the board's digital leaf) |
| Full order finding, Q × T (single-run T) of Shor's order-finding circuit at n = 2048 | a PP policy (site policy JSON, written as a ForShor `PolicyTemplate.lean`) | the IR ForShor prints under `--balanced`, priced exactly: exact register widths, the balanced split for unequal operands, m2048, Step 4 `cmpLtNW`, 4095 modular multiplications | ForShor's own build (`21e129a`) (`ShorPolicySubmission`, C1 to C4 by `decide +kernel`, `policy_submission_correct`) |

**Trust, in one line.** The T counts are Qualtran's, counted in Python, and *not proved in Lean*; Q is a formula over the policy's widths (ForShor has
checked it in Lean for the boards' own policies, not for yours). Lean proves the structure: the tables, the thresholds and the widths (and, for the full run, ForShor's
circuit-level correctness). It does not certify the price. The full run's gate census comes from ForShor's IR, matched to the compiled circuit at sampled widths, not proved at n = 2048.
Rotations are priced with Ross–Selinger, 3.075 log2(1/ε) + 0.86 T at ε = 0.5 δ / R, never at a flat 11 T.

## Contents

| Path | What it is |
|---|---|
| `SUBMISSION_FORMAT.md` | The rules a submission must meet. Read this first. |
| `SKILL.md` | The same workflow, written for AI agents (Claude and others). |
| `starter.py` | `price`, `forshor-doc`, `template`, `lean-input`, `examples` (see below). |
| `templates/` | `pp_policy.json` and `ptp_policy.json` (site policy JSON; both price as they stand), their Lean-checker inputs `*.lean-input.json`, and `PolicyTemplate.lean` (ForShor's shipped template, verbatim). |
| `examples/` | The STO reference PP, a re-planned PP seed, a PTP seed, the board's reference document (ForShor `21e129a`'s printed `policy.json` for the shipped template at exact widths), upstream ForShor's own document (scanned widths, the cross-check), a policy with a declared ladder, and the expected output of each (`expected/`). |
| `harness/LEAN_CHECKS.md` | How to run the Lean checks. The build trees are not in the pack; the file says where they come from. |
| `harness/policy_to_template.py` | The site's converter from a PP policy to a ForShor template (used by `starter.py template`). |
| `engine/` | The site's pricing engine, vendored: `tcost` (engine 1), `forshor_qualtran` (engine 2), the full-run pricer `site_engine/fullalg.py` with Lean-To-Qualtran's `PolicyShorCore`, the exact-width functions (`tcost/exact`, and ForShor's rule `fullalg/forshor_exact_width.py`), the two ForShor policy documents (`fullalg/data`: the exact-width one, which is the board's IR, and the default-scan one, the cross-check), Qualtran 0.8.0.dev0, and the table library (used only to name tables). |
| `MANIFEST.json` | Every file, with its sha256 and the source it was copied from. |

## 1. Install

The challenge page is a browser application: its text is not readable with `curl`. Its data is public JSON (`/results/v2/dashboard.json`), and the commands for taking part are in `SUBMISSION_FORMAT.md` section 6 and the page's Participate dialog.


You need Python 3.11 or 3.12 (the default `python3` is often newer: find one with `python3.12 --version`, `pyenv versions` or `uv python install 3.12`).

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # do not pip install qualtran: it is vendored in engine/vendor
python starter.py examples --check     # prices every example and compares with examples/expected/ (under a minute)
```

## 2. Write a policy

* **PP or PTP:** copy `templates/pp_policy.json` or `templates/ptp_policy.json` and replace the tables and thresholds
  with your own. Do not declare widths. If you declare a width ladder, the board ignores it and shows a notice (see
  `examples/declared_ladder_ignored.policy.json`).
* **Full order finding:** the same PP policy. The site writes it as ForShor's `PolicyTemplate.lean`; you can do the
  same with `python starter.py template my_pp_policy.json --out PolicyTemplate.lean --forshor /path/to/ForShor`
  (`templates/PolicyTemplate.lean` is the shipped one, from ForShor `21e129a`). ForShor's Lean library is `ForShor`;
  `--forshor` checks the name against your checkout. Up to k = 6 and any number of bands: ForShor's check has no
  heartbeat wall. ForShor's policies also carry an optional `negation` (`oneQubit` or `scratch`); the boards price
  `oneQubit` only, and `starter.py` refuses a policy that asks for `scratch`.

## 3. Price it

```bash
python starter.py price my_pp_policy.json            # PP board at exact widths + the full order-finding run
python starter.py price my_ptp_policy.json           # PTP board at exact widths
python starter.py price my_pp_policy.json --no-full --delta 3e-4 --json out.json
python starter.py forshor-doc /path/to/policy.json  # a ForShor build's printed document (full run)
```

`price` reports:

* the board's T at the exact widths, with the **exact ladder** (for example `2048:6 -> 364:2 -> 184:2 -> 94:2 -> 49:2 -> 26:leaf`);
* engine 2's T and whether the two engines agree;
* Q, the live-qubit peak (a formula over your policy's widths, not Lean-proved for your policy), the score Q × T, the static-layout Q as
  a cross-check, and where the score would rank on the board's snapshot taken when the pack was built (`examples/board_snapshot.json`);
* the ForShor scanned-width price, as a labelled cross-check;
* for PP, the full order-finding single-run T on ForShor's exact-width IR (exact register widths, balanced split),
  its Q (the live-qubit peak, with the static layout as a cross-check) and the score Q × T, the main ladder (the one the balanced Step 2 descends, for example
  `2348:6 -> 414:3 -> 141:2 -> 72:2 -> 38:leaf`), the trial count (386) and the end-to-end T, with the grid-leaf and
  literal-Lean cross-checks and upstream ForShor's scanned-width IR as a labelled cross-check (`forshor_scan_ir`).

Each number comes from the same code the site runs (see `MANIFEST.json`), so a policy prices here as it does on the
site's boards. The full run is priced locally with the site's own full-run pricer (`engine/site_engine/fullalg.py`),
vendored unchanged.

## 4. Check it in Lean, then submit

See `harness/LEAN_CHECKS.md`. The Lean build trees are not in the pack. In short:

* PP: `python starter.py lean-input my_pp_policy.json`, then the STO policy mirror (`check_banded_policy.py --widths exact`, in the interim STO fork,
  `courtneyjzapata/ShorTableOptimization_temp`, branch `port/forshor-21e129a`).
* PTP: the same input, then `check_ptp_policy.py --widths exact` of the same fork (against ForShor's `Shor.PTP`).
* Full run: ForShor at the pinned commit `21e129a` (VerifiedQC/ForShor). The template goes to
  `ForShor/ShorVerification/Submission/PolicyTemplate.lean`. Then:
  `lake exe cache get`, `lake build PolicySubmission forshor_policy_submission`, then
  `lake exe forshor_policy_submission --balanced > policy.json` and `python starter.py forshor-doc policy.json`.

The PP mirror and the PhaseTripleProduct harness reject a policy whose thresholds are set too low (stage `threshold`): on the
recursion the policy takes from n = 2048 at exact widths, every band applied at width w must have k ≤ w and must
shrink the width. `price` prints the exact ladder, so you can see this before submitting.

These checks are heavy: a PhaseProduct policy with a k = 8 first band took about 2.4 hours in the mirror's report stage. Start small.
How to send a policy is in `SUBMISSION_FORMAT.md`, section 6: the pack prices and prepares it, it does not submit it.

## What the numbers assume

* Logical counts only.
* Ross–Selinger synthesis of each rotation, at its mean cost. The error budget is δ = 0.01, half of it to synthesis,
  split evenly over the rotations.
* PP leaf: the Qualtran digital product. PTP leaf: the digital triple product (x·y·z computed exactly with two Qualtran products, one rotation per bit); the schoolbook grid is a labelled cross-check.
* Adders: Qualtran/Gidney, 4(w − 1) T. A negated source costs one `Subtract`. And, Toffoli or CSwap: 4 T each.
* Q is the live-qubit peak (`SUBMISSION_FORMAT.md` section 3), the score is Q × T. A negation is priced with ForShor's explicit counts (the complement plus a
  one-ancilla increment: Toffoli `12w - 28` for even `w`, `10w - 18` for odd, with its CNOTs and X gates), under ForShor's default method `oneQubit`.
* PP and PTP boards: every child at the exact width, meaning the least width at which every reachable register value
  and child operand fits (floor limbs). ShorTableOptimization's `exact_width` (Lean Tight.lean) and the PhaseTripleProduct exact-width function (ForShor's `Shor.PTP`, mirrored in the STO fork) are vendored verbatim. Python matching Lean is cross-checked, not proved.
* Full run: ForShor's native structure (ForShor `21e129a`), with Step 4 `cmpLtNW` and Lean's work width (2347 bits at n = 2048, m = 2^150 − 3).
  Exact register widths: each table's child width is ForShor's `exactSignedWidthOfWidths`, max(tight, 1 + the widest
  initial chunk). The balanced split: Steps 1, 2 and 5 are padded to 2348 × 2348, and Step 4's 2348 × 4399 product
  becomes two 2348 × 2348 products. The census comes from ForShor's IR, matched to the compiled circuit at sampled
  widths only: at n = 2048 it is not proved. Upstream ForShor's own IR (scanned widths, no split) is the labelled
  cross-check (`forshor_scan_ir`); `examples/expected/` pins what each example prices to.
* The table library in `engine/data` is the snapshot the engine had when the pack was built (`MANIFEST.json`). It only names tables.
* The ranking line in `price` compares with `examples/board_snapshot.json`, the board's rows when the pack was built; the page shows the live board.

## Licences

`engine/vendor/Qualtran-main` is Qualtran (Apache-2.0, see its `LICENSE`). `engine/fullalg/l2q_full` is from
Lean-To-Qualtran (Apache-2.0, `engine/fullalg/LICENSE.l2q`). `templates/PolicyTemplate.lean` and
`examples/forshor_dropin.policy.json` / `examples/forshor_upstream_scan.policy.json` (also in `engine/fullalg/data`) are from ForShor (Apache-2.0, `templates/LICENSE.forshor`). The rest of the pack
(`starter.py`, the site engine and the documentation) is provided by Zapata Quantum and the University of Maryland
for the challenge, under the licence of the repository that publishes it.
