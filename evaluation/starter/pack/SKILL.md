---
name: forshor-challenge-submission
description: "Use when helping a solver or coding agent take part in the ForShor Challenge with its starter pack: set up the pack, write a banded PhaseProduct or PhaseTripleProduct policy, price it with starter.py (examples, price, forshor-doc, template, lean-input), run the optional Lean checks (policy check, PhaseTripleProduct harness, ForShor's build), and prepare it for submission. Explains the three boards, the score Q x T, exact widths, the threshold rule, what Lean does and does not certify, the exact trust wording, resource limits, the circuit-design options and which of them can be submitted for a score, staying current with the leaderboards, and common errors."
---

# ForShor Challenge: Policy Submission

Use this skill to write, price and submit a policy for the ForShor Challenge from a terminal. The challenge is fixed:
n = 2048, δ = 0.01, three boards. Once installed, run `starter.py` commands offline without a login from the unpacked pack
(`starter.py`, `engine/`, `templates/`, `examples/`, `harness/`, `SUBMISSION_FORMAT.md`). In contrast, `./forshor check` and
`./forshor submit` run from the submissions checkout and use GitHub sign-in. `check` fetches the trusted base and checks locally; it does not submit anything.

A submission is a **full policy**: Toom-Cook tables plus the width thresholds that choose among them. A bare table,
or tables without thresholds, is not a submission. Read `SUBMISSION_FORMAT.md` before writing anything. Its rules are
strict, and a single violation is a rejection.

## Setup

Create a Python 3.11 or 3.12 virtual environment and install the pack's requirements. The default `python3` on a machine is often newer: find a 3.12 or 3.11 interpreter first (`python3.12 --version`, `ls /opt/homebrew/bin/python3*`, `pyenv versions`, or `uv python install 3.12`) and use it for the venv:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

Never `pip install qualtran`. It is vendored in `engine/vendor`, and the pack's numbers depend on that exact copy.

Check that the engine is the site's engine:

```bash
python starter.py examples --check
```

Every example must print `MATCHES`. If one prints `DIFFERS FROM`, the local engine does not reproduce the site's
numbers: stop and report it rather than pricing anything.

## The Challenge

Every board is ranked by the score **Q × T** (qubits times T count, lower is better) at n = 2048, δ = 0.01. **Q is the live-qubit peak**: the most qubits that exist at any one moment (a formula over the policy's widths).

| Board | You submit | T and Q |
|---|---|---|
| PhaseProduct (PP) | a banded PP policy in `Policy.lean` | T of one phase product at the exact widths the board derives; Q the peak of one product |
| PhaseTripleProduct (PTP) | a banded PTP policy in `Policy.lean` | T of one triple product at exact widths (digital triple-product leaf); Q the peak of one triple product |
| Full order finding | a PP policy: the board prices any k; **submitted as the Lean file `Policy.lean`** (bands k ≤ 8), which ForShor builds (the `ForShor` tag) | single-run T of Shor's order-finding circuit on the IR ForShor prints under `--balanced`; Q its peak (12,890 base registers plus the policy's extra) |

A PP policy enters both the PP board and the full run. They often prefer different policies: price both.

The full run is priced on ForShor's IR:
- exact register widths;
- the balanced split for unequal operands: Steps 1, 2 and 5 are padded to 2348 × 2348, and Step 4's 2348 × 4399 product
  becomes two 2348 × 2348 products;
- m2048, Step 4 `cmpLtNW`, 4095 modular multiplications;
- the Qualtran digital-product leaf, Gidney adders, ForShor's negation counts (the complement plus a one-ancilla increment: Toffoli `12w - 28` for even `w`, `10w - 18` for odd) and Ross–Selinger rotations.

ForShor policies also carry an optional `negation` (`oneQubit`, the default, or `scratch`). The boards and this pack price `oneQubit` only; `starter.py` refuses a policy that asks for `scratch`. End-to-end T is the trial count (386) times the single-run T. Upstream ForShor's own IR (scanned widths, no split) is
shown as a labelled cross-check (`forshor_scan_ir`), never the score.

## Local Loop

Start from a template:

```bash
cp templates/pp_policy.json my_policy.json      # or templates/ptp_policy.json
```

Each band has `threshold`, `k`, `point_order` and `operations` (the site's panel files spell the second key `pointOrder`; `starter.py` accepts either):
- bands are listed widest first, with thresholds strictly decreasing;
- different bands may carry different tables of the same k, selected per width band, with no limit beyond the 256 bands a submission may have;
- below the last threshold the leaf is used.

Do not declare widths. The board derives the exact widths, and ignores any declared ladder with a notice. The derived width counts every value the table's program holds, intermediate ones included, so a `shiftL` / `shiftR` pair that makes a register briefly larger raises it, at every size that table serves; the shifts themselves cost no T gates and no qubits, but the wider register is priced. Read the ladder `starter.py price` prints, and see "Widths and shift pairs" in `SUBMISSION_FORMAT.md`.

Price it (a few seconds for PP/PTP, longer with the full run):

```bash
python starter.py price my_policy.json                         # board T, Q and Q x T + exact ladder; PP also prices the full run
python starter.py price my_policy.json --no-full --json out.json
```

Read the output in this order:
1. the board T at **exact** widths and the exact ladder (for example `2048:6 -> 364:2 -> ... -> 26:leaf`);
2. whether the two engines agree;
3. Q (the live-qubit peak), the score Q × T, and where the score would rank on the board's snapshot taken when the pack was built;
4. for PP, the full-run single-run T, Q and score Q × T, its main ladder (for example `2348:6 -> 414:3 -> 141:2 -> 72:2 -> 38:leaf`) and where it would rank on the full board;
5. the scanned-width cross-check and the static-layout Q (cross-checks, not the score);
6. any widths notice.

Iterate here: other tables, other thresholds, a different k at each level.

**What makes a policy score better** (a lower Q × T):
- Q is the peak of one recursion path, so a wide first level that grows its operands a lot raises Q; compare Q and T together, not T alone.
- T at n = 2048 is driven by how narrow the children are and by the rotation count, not by the operation count.
  Compare exact ladders. Evaluation points that need fewer guard bits give narrower children at every level below.
- Thresholds matter as much as tables. A wide top table (k = 5 or 6) can pay off at 2048.
- The PP leaf is the digital product, so stopping the recursion earlier trades rotations for Toffolis.

## Searching Many Policies

`starter.py price` prices one file at a time (a file may hold up to 8 policies). To search, price in-process with the pack's own engine: from the pack's root, with its venv,

```python
import sys; sys.path.insert(0, "engine")
import site_engine
from site_engine import frontier as fr
from tcost import score as ts, tables as tt
P = tt.Policy.from_json(policy_dict).with_widths("exact")
T = ts.score_policy(P, 2048, 0.01)["t_count"]        # the board's T
Q = fr.live_peak_qubits(P, 2048)                       # the board's Q
score = Q * T
```

This is exactly what `starter.py price` calls for the board numbers (about 0.01 s a policy), so the two agree. The full-run price is slower (`site_engine.fullalg.price_full`). Tables to start from are in `engine/data` and in the policies of the boards (the page's data, `/results/v2/dashboard.json`, names each row's panel file, whose `policyJson` has the bands, with `pointOrder` for `point_order`; `starter.py` accepts both spellings). Re-price the final policy with `starter.py price` before you report it.

## Thresholds Must Be Safe

The PP policy check and the PhaseTripleProduct harness apply a **threshold rule**, proved in Lean. On the recursion that the policy actually
takes from n = 2048, at exact widths, every band applied at width w must have k ≤ w and must make the width strictly
smaller. A policy whose thresholds are set too low is **rejected at stage `threshold`**. The reason names the band, its
threshold and k, the width and the exact child width.

`price` shows the exact ladder, so you can check this yourself: every arrow must go to a smaller width, and no
`k:` may exceed the width it is applied at.

## Preparing For Lean

The Lean checkers' input for PP or PTP (widths are dropped):

```bash
python starter.py lean-input my_policy.json --out my_policy.lean-input.json
```

The submission file, `TableGeneration/Submission/Policy.lean` (PP; bands take 1 < k ≤ 8; a pull request may change this file and the optional `summary.tex`, nothing else; a PhaseTripleProduct policy is submitted in the same file as a `PTPPolicy` in `namespace PTPPolicySubmission`, format `templates/ptp.Policy.lean`, written by the same command):

```bash
python starter.py template my_pp_policy.json --out Policy.lean --forshor /path/to/ForShor
```

ForShor's Lean library is `ForShor`; `--forshor` checks the library name in your checkout. For a manual verifier build, use the operational pin in the submissions repository's `evaluation/policy-trees.json`; `./forshor check` selects it automatically. The pack's model pin `21e129aeefc0fd1bbb8fa63402f5799af2490788` identifies the pricing baseline, not the verifier checkout.

A ForShor build's printed document, priced on the board's IR. It must say `agrees`:

```bash
python starter.py forshor-doc /path/to/ForShor/policy.json
```

## Lean Checks (Optional)

The build trees are not in the pack; `harness/LEAN_CHECKS.md` says where they come from and gives the exact commands. In short:

- **PP:** the policy check, `check_banded_policy.py --widths exact --target-width 2048` (in the `forshor-submissions` repository, `main`: github.com/zapatacomputing/forshor-submissions; `./forshor check` in a clone of it runs the whole check).
- **PTP:** `check_ptp_policy.py --widths exact --target-width 2048` of the same repository, against ForShor's `Shor.PTP`. Build the PhaseTripleProduct modules by name first.
- **Full run:** VerifiedQC/ForShor at the operational verifier pin. Put your `Policy.lean` at `ForShor/ShorVerification/Submission/PolicyTemplate.lean` (ForShor's name for the file). Then:

  ```bash
  lake exe cache get
  lake build PolicySubmission forshor_policy_submission
  lake exe forshor_policy_submission --balanced > policy.json
  ```

  `--balanced` implies exact widths. Extracting the IR takes 2 to 3 minutes for a k = 5 or 6 policy.

Resource rules:
- Run one or two Lean processes at a time, under a memory cap (each tree's `scripts/memcap.py`); wait while `pgrep -x lean | wc -l` is 2 or more.
- Some checks are long: a PP policy with a k = 8 first band took about 2.4 hours in the policy check's report stage. Start small (k up to 6, few bands), give a long run a stage limit, and set `TMPDIR` to a directory you control.
- A `rejected` record about a missing object file, a constructor-field error or a missing file in a temporary directory is an infrastructure failure, not a verdict: fix it and run again.
- Running a check executes Lean on your machine. For a policy you did not write, use a disposable VM or container.

## Submitting

Read `SUBMISSION_FORMAT.md` section 6 first. **This pack prices a policy and prepares it; it does not submit it.** The submission repository is
`zapatacomputing/forshor-submissions` (private during the sprint; the challenge page's **Participate** dialog has the commands), and its instructions win over this file.
Once the repository is public, `./forshor submit` creates or reuses the participant's GitHub fork and opens the PR against `zapatacomputing/forshor-submissions/main`; participants do not need upstream Write access. While private, existing authorized staging contributors retain their current route.

Copy the generated Lean policy to `TableGeneration/Submission/Policy.lean` in the submission checkout. The CLI accepts a
`ShorPolicySubmission` (PhaseProduct) or `PTPPolicy` (PhaseTripleProduct), not the pricing JSON. The design options are
covered in full below, in "Design Options"; the exact declarations, the recogniser's shape and the verbatim refusals are in
`SUBMISSION_FORMAT.md` section 2.

Run `./forshor check` before `./forshor submit`. Pricing and template generation are not proof checks. When checking fails, read the
failed stage and reason, then `artifacts/forshor/verification/table-generation-summary.md` for details. On GitHub, open the
**Verify submission with Lean** check and its run summary or PR comment; `./forshor status` also follows the hosted check.
If a policy or file rule failed, fix that rule and rerun the check. A time or memory stop is not a verdict on the policy;
see the run details before changing it. A publication failure after a green verification is a separate issue.

What you can always give the user: the policy file, the `starter.py price` output (and, for a full-run policy, the `forshor-doc` output), the pack's build date (`MANIFEST.json`) and a
short public Markdown note: the objective, the starting point (a starting policy, a leaderboard policy or a template), what you changed, the exact commands, the prices with exact ladders (including
attempts that did not help), limitations, and the model or harness you used. Leave out private reasoning and secrets.

An accepted policy appears on the boards under the submitter's GitHub handle as `<handle>_sub<N>`, N counting their own submissions from 1 (the handle `forshor` is reserved), ranked by Q × T
with the starting rows, the starting policies, built from the tables in the “Parallel sequences” appendix of Kahanamoku-Meyer and Yao (2024) (named `forshor_sub<N>`), and is listed as checked in Lean if it has been. It inherits the repository's
Apache 2.0 licence. **The boards' numbers are the ones that count:** the site prices the policy again with the same engine.

## Design Options

Every circuit-design option is declared in the **Lean source** (`Policy.lean`), never in the table policy JSON.
**Whether an option can be submitted for a score is the most important thing about it.** `SUBMISSION_FORMAT.md` section 2,
"The design options, and which of them can be submitted", is the full matrix: the exact declarations, the recogniser's shape and the verbatim refusals. The short form:

| Options | Status |
|---|---|
| the default design (coherent readout, exact logical QFT, Ross–Selinger synthesis, `oneQubit` negation), whether left out or written out in full; several tables of the same `k` selected per width band | **submittable for a score**: hosted intake accepts, publishes and ranks it |
| semiclassical (measured) readout; the standalone synthesis primitives `.catalyticB2` and `.adaptiveB3`; any `submittedFullRunDesign` other than `.reference` | **local qualification only, nonpublishing**: attested and priced locally; not accepted by hosted intake; no score |
| anything else, including any precision other than δ = 1e-2 | no typed selector exists; refused |

**The default design in full.** Leaving the selectors out gives the submittable design. Writing a default out in full changes nothing:
hosted intake treats an explicit default declaration as **identical to omission** (owner ruling 2026-11-24), so
`def submittedPrimitive : Shor.PrimitiveSynthesis := .rossSelinger`,
`def submittedDesign : Shor.SubmissionDesign := { readout := .coherent }` and, on the PhaseProduct board only,
`def submittedFullRunDesign : Shor.FullRunDesign := .reference` are accepted, build the same document, price the same and
count as the same submission in either arrival order. The recogniser is exact: one `def` at column 0, directly in the
board's namespace, ForShor's own type, the default value, nothing else on the statement, once. Prefer **omitting** the
selectors — it is shorter and cannot be mis-shaped.

**Local qualification is not a way to score.** Each of these is a real typed choice the verifier attests when you run it
yourself, and none of them has a hosted publication route. Even a receipt from the local readout-qualification route is
refused by the publisher at its first gate — `a semiclassical readout receipt is published only from the trusted verification run that produced it, and this receipt does not come from one`. Never
offer one to a user as a submission, and never present its local number as a board number.

**`submittedFullRunDesign` is submittable at `.reference` only.** `Shor.FullRunDesign` is a closed enum; hosted intake
refuses every other value by name, and there is no way to ask for a combination outside the enum.

**So a submission is not wasted.** Do not try to build any other per-family QFT mixture, rotation backend or phase-step
backend, or a precision other than δ = 1e-2. Only δ = 1e-2 is in scope and there is no precision control to set.

**Design options are never JSON.** A policy or band object carrying `readout`, `submittedDesign`, `submittedPrimitive`,
`submittedFullRunDesign`, `qft`, `modmul`, `phase_product`, `synthesis` or `synthesis_backend` (or their snake_case forms)
is refused whatever its value, by `starter.py` and by hosted intake alike:
`table policy JSON cannot select circuit design options (<keys>); use the supported Lean policy interface`.
Putting `"readout": "coherent"` in the JSON does not express the default — the default is what you get by writing nothing.

**A stop is not a verdict.** A timeout or a memory stop is *incomplete*, never a refusal and never a pass: its text ends
"This is not a verdict on the policy." Retry it; do not change the policy because of one, and do not report it as a
rejection or an acceptance.

The refusal messages quoted here and in `SUBMISSION_FORMAT.md` are the ones this pack was built against. Several are
reachable from more than one cause and **the wording may become more specific**; the rule each one is about does not change.

## Optional LaTeX Summary

Participants may add a summary that explains their policy: the idea, why it is cheap, the derivation, what they tried and what did not work. **The summary is optional.** It never affects
verification, pricing or rank, and a policy without it is complete. Offer it to the user; do not write one unless the user wants it, and never put private reasoning or secrets in it.

**Where it goes.** In its own file, next to the policy: `TableGeneration/Submission/summary.tex` (exactly that path, lower case). It is **not** a comment inside `Policy.lean`: do not put LaTeX in the Lean file.
A pull request may change `Policy.lean` and this one file, and nothing else: a pull request that adds any other file (a `notes.tex`, a `README`, a figure, a `.bib`) is refused, and a pull request that changes
only `summary.tex` is refused too ("a submission needs a policy"). `./forshor check` validates the summary with the same rules as the hosted check, and `./forshor submit` includes it in the pull request when the file exists.
The hosted pipeline archives it unchanged beside the accepted policy; the challenge page does not render it.

**What it is.** Text for people to read, written in LaTeX. **Nothing compiles, renders or runs it**: not the harness, not the hosted check, not the archive. TeX can run shell commands and read files
(`\write18`, `\input{/etc/passwd}`), so the file is only ever stored and shown as text. Write it so a reader can follow it as plain text, and so it compiles on the reader's own machine if they choose to.
Either form is fine: a plain LaTeX body (sections, formulas, lists), or a small self-contained document (`\documentclass` … `\end{document}`) that needs no other file. Nothing else is in the pull request, so
`\input`, `\include`, `\includegraphics` and `\bibliography` of your own files will not find anything: put figures in words or tables, and type the references in the text.

**Rules, checked by the verifier** (not by compiling it; a file that breaks one is rejected with a message naming the rule, which is about the file, not a verdict on your policy):

1. a regular file: not a symbolic link, not a directory;
2. not empty;
3. at most 100 KiB (102,400 bytes), which is about twenty-five dense pages;
4. valid UTF-8 (save the file as UTF-8; accents and symbols such as `é` or `∑` are fine);
5. no control characters except tab, line feed and carriage return (so no NUL byte and no escape sequences).

Backslashes, `$`, `%`, braces, `\begin{align}` and Unicode are fine. The Lean checks never read the file, so none of the Lean comment limits applies to it.

A short example (`examples/summary.tex` is a template to start from):

```latex
\section*{Idea}
We split the $n$-bit operand into $k=3$ limbs and evaluate the product polynomial at $0, 1, -1, -2, \infty$.
\begin{align}
  c(t) &= a(t)\,b(t) = \sum_{i=0}^{4} c_i t^i, \\
  T(n) &\le 5\,T(\lceil n/3 \rceil) + O(n).
\end{align}
\emph{Why it is cheap:} the two bands reuse one addition chain.
The starting point is the planner's optimum over the tables in the Parallel sequences appendix of Kahanamoku-Meyer and Yao (2024); the prices below are the `starter.py price` output.
```

What to write: the objective, the construction, why it should be cheap (reasoning, not a proof unless you have one), the prices you got from `starter.py price` (copy the numbers, with the exact ladder), what is not proved, and credit for
anything you reused (see Reuse And Provenance). Do not claim a number the pack did not print.

## Staying Current

The challenge page's **Leaderboard** section lists the current best policy on each board; its data is public JSON (`/results/v2/dashboard.json` on the site, where each
board names its rows file). Check it before you start and periodically while you work, so you do not improve from a stale baseline. `examples/board_snapshot.json` is the boards as they were when
the pack was built. The pack's
`examples/` hold the starting policies (PhaseProduct and PhaseTripleProduct) and their expected prices. They are
starting points, not the frontier.

If a better policy has been accepted, start from it: re-price it with `starter.py price`, then iterate from there.

## Reporting To The User

Give the user:
- the policy file;
- the board T, Q and score Q × T, and the exact ladder;
- the engine agreement;
- for PP, the full-run single-run T, end-to-end T, Q, score and main ladder;
- the scanned-width cross-check;
- any widths notice.

Use the trust wording below exactly.

## Trust Wording (Use Exactly These Words)

**Any T count:** "T counted in Python (Qualtran), not proved in Lean."

**Any Q:** "Q is the live-qubit peak, a formula over the policy's widths, not proved in Lean for this policy." (ForShor has checked it in Lean for the boards' own policies; never say that of yours.)

**PP or PTP that passed the policy check or the PhaseTripleProduct harness:** "Policy Lean-verified at exact widths" for PP, and "Policy Lean-verified at exact widths (PhaseTripleProduct harness, ForShor's Shor.PTP)" for PTP.
At exact widths, Lean's kernel proved:
- the register semantics is correct;
- the exact widths are the least overflow-free widths, and the policy's exact ladder at 2048 is proved;
- every table satisfies the table contract;
- the thresholds decrease, and they pass the threshold rule;
- the policy is correct concretely at n = 2048 (`correct_at_target`, `exact_ladder_at_target`).

For PP this is not circuit-level, and it is weaker than "ForShor policy-verified". ForShor's PhaseTripleProduct statements also give a
gate-level circuit following the policy, correct through the whole recursion at the scan and **exact** widths. That circuit uses ForShor's Toffoli + Rz + Toffoli sandwich
leaf, not the digital leaf the board prices. PTP covers equal-width operands only.

**Full run that passed ForShor's check:** "ForShor policy-verified". This is circuit-level `policy_submission_correct`.
The census, taken from ForShor's IR, is matched to the compiled circuit at sampled widths, not proved at n = 2048.

**No level proves the T count.**

## Updating The Pack

The pack is versioned by its build date (`README.md`) and `MANIFEST.json`, which lists every file with its sha256
and source. To update, download the current pack from the challenge's GitHub repository, unpack it over a fresh
directory (not over edited files), recreate the virtual environment, and run `python starter.py examples --check`
again. Re-price your working policies afterwards: a new pack can change the engine, and old local numbers then no
longer match the site.

## Agent Skill Install

This file is the skill. Install it by copying it into your agent's skills directory under its own folder. For
example, for Claude Code:

```bash
mkdir -p ~/.claude/skills/forshor-challenge-submission
cp SKILL.md ~/.claude/skills/forshor-challenge-submission/SKILL.md
```

If your agent runs in a sandbox that cannot write to `~/.claude/skills`, put the folder wherever your agent loads skills from. Restart the agent after installing or updating the skill. Updating the pack does not update an installed copy: copy
it again.

## Uninstall

Delete the unpacked pack directory, its `.venv`, and the installed skill folder (for example
`~/.claude/skills/forshor-challenge-submission`). The pack writes nothing outside its own directory, except files
you name with `--out` or `--json`.

## Common Errors

| Message or symptom | Cause | Fix |
|---|---|---|
| `DIFFERS FROM examples/expected/...` | the engine is not the site's: a reinstalled or upgraded Qualtran, a modified `engine/`, the wrong Python | fresh pack, fresh venv, `pip install -r requirements.txt` only |
| `Not valid JSON` / `Malformed policy` / `Not an accepted policy` | the file is not the site's policy JSON | compare with `templates/*.json` and `SUBMISSION_FORMAT.md` §1 |
| `a table fails the Toom-Cook check` | the program does not consume its points in order, does not restore the registers, or breaks the exact identity | fix the table, not the threshold |
| widths notice | the file declares a ladder or widths | remove them: the board derives exact widths and ignores declared ones |
| rejected at stage `threshold` | a band is applied where it does not shrink the width, or at a width below k | raise that band's threshold (the reason names it), and re-check the exact ladder |
| `starter.py template` refuses | a format limit or unsupported option | the template tool writes PP and PTP policies with 1 < k ≤ 8; read the named refusal rather than changing a valid PTP policy to PP |
| `forshor-doc` prints `DIFFERS` | the printed document does not match the converted policy, or is a scan document | rebuild with `--balanced`. A document without `width_rule` is priced on the upstream cross-check, not the board's IR. |
| `error: the policy asks for negation 'scratch'` | ForShor's `scratch` method is not priced by the boards or this pack | remove the `negation` key (or set `"oneQubit"`) |
| `submittedDesign is not accepted as written` | the file asks for measured readout (local qualification only), or writes the `.coherent` default in a shape the recogniser does not take, or mentions the name elsewhere | leave the selector out, or write exactly the default declaration (`SUBMISSION_FORMAT.md` section 2). Measured readout is local qualification only and cannot be published, so there is no submittable variant |
| `submittedPrimitive is not accepted for a scored submission` | the file asks for `.catalyticB2` or `.adaptiveB3` (local qualification only), or mis-shapes the `.rossSelinger` default, or mentions the name elsewhere | same fix. The standalone primitives have no hosted publication route |
| `submittedFullRunDesign is not accepted as written` | the file sets `submittedFullRunDesign` to anything but `.reference`, mis-shapes `.reference`, declares it twice, or declares it on the PhaseTripleProduct board (refused there even at `.reference`) | same fix. No other value has a hosted publication route |
| `table policy JSON cannot select circuit design options (…); use the supported Lean policy interface` | a design key (`readout`, `qft`, `modmul`, `synthesis`, `phase_product`, a `submitted*` name, …) is in the **table JSON** | design options are declared in `Policy.lean` only; delete the key. The default is what you get by writing nothing |
| `full-run synthesis requires saved semiclassical readout and --balanced --readout submitted` / `full-run and standalone synthesis opt-ins are not jointly qualified` | ForShor refusing a non-`reference` full-run profile outside its qualification mode, or mixed with a standalone primitive | these are local qualification routes with no hosted publication route; a scoring submission does not declare them at all |
| `a semiclassical readout receipt is published only from the trusted verification run that produced it, and this receipt does not come from one` | a measured-readout receipt was given to the publisher | measured readout is attested and priced locally; it is not accepted by hosted intake and gets no score |
| a Lean run is killed for memory or time | over the memory cap, or the stage limit you set | run one or two checks at a time; some are hours long (see Lean Checks). If it persists, report the policy and the record rather than raising the cap. |
| `rejected`: a missing object file, `Insufficient number of fields`, or a missing file under a temporary directory | an infrastructure failure (an unbuilt PhaseTripleProduct module, a stale harness script, a cleaned temporary directory) | fix the setup and run again; it is not a verdict on the policy |

## Never

- Never submit bare tables, and never declare widths.
- Never price a rotation at 11 T. Never replace the pack's engine or its Qualtran.
- Never say a policy is "verified" because `price` ran. `price` runs Python only. Lean decides correctness, and no
  level proves the T count.
- Never use `sorry`, `admit`, `axiom`, `native_decide`, `#eval`, `set_option`, macros, `unsafe`, `extern`, `csimp` or
  `implemented_by` in a `Policy.lean`. Never edit any other ForShor file.
- Never present a policy that asks for `negation: scratch` as priced. Never say Q is Lean-proved for a policy of yours.
- Never set a design selector to anything but its default, and never offer one as a way to score. Measured readout, the
  standalone synthesis primitives and every non-`reference` full-run design are local qualification only: attested and
  priced locally, not accepted by hosted intake, no score. Say that plainly rather than hedging.
- Never put a design key in the table policy JSON. Design options are declared in `Policy.lean` only.
- Never promise an option that has no selector (any other per-family QFT mixture, rotation backend or phase-step backend)
  or any precision other than δ = 1e-2.
- Never report a timeout or memory stop as a refusal or as a pass. It is incomplete, it is not a verdict, and it may be retried.
- Never present a local number as final. The site prices the policy again, and its numbers count.

## Reuse And Provenance

Say precisely what you used: a starting or leaderboard policy, another solver's table, or a published construction (for
example the points of the Kahanamoku-Meyer and Yao tables). Naming prior work as a baseline you compared against does not claim your policy derives from
it; say which it is. If you are unsure of a table's origin, say that rather than guessing. Accepted policies are
Apache 2.0, so reuse is allowed, but it must be credited in the submission.
