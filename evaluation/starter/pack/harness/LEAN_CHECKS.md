# Lean checks of a policy

Lean decides whether a policy is correct; `starter.py` only prices it. The three Lean checkers below are **not in this
pack**: each needs its own Lean + Mathlib build tree (several GB, tens of minutes to build). You can run them yourself if you have the trees; the organisers' checks of a submitted policy
are the ones in the submission repository.

Once installed, the pack's pricing and template commands work offline without sign-in. The separate `./forshor check` command uses GitHub sign-in and fetches the trusted submissions base, then runs Lean locally without submitting. For manual builds, use the trusted base's `evaluation/policy-trees.json`: its `sto_build` targets are for the submissions tree and its `forshor.ref` is the operational VerifiedQC/ForShor pin. The frozen model pin `21e129a` in the pack's manifest describes pricing, not the verifier checkout.

| Submission | Checker | Build tree | Where it comes from |
|---|---|---|---|
| PhaseProduct (PP) banded policy, site policy JSON | policy check, `scripts/check_banded_policy.py --widths exact` | the `forshor-submissions` repository | trusted `main`: `lake exe cache get`, then `lake build` with the `sto_build` targets from `evaluation/policy-trees.json` |
| PhaseTripleProduct (PTP) banded policy, site policy JSON | `scripts/check_ptp_policy.py --widths exact`, against ForShor's `Shor.PTP` | the same repository | the same `sto_build` targets include the PTP planner and axiom audit; use those targets, not just `lake build TableGeneration` |
| PP or PTP submission file `Policy.lean` | ForShor's own build (sections 3 and 4 below) | a disposable ForShor checkout | `VerifiedQC/ForShor` at `forshor.ref` from the trusted base |

Both PP and PTP submissions allow up to 256 bands, each with `2 ≤ k ≤ 8`.

Use a machine with at least 16 GB of memory. Run one or two Lean checks at a time and keep every run under a memory cap (each tree's `scripts/memcap.py`). Measured on earlier policies: most checks take seconds to a few minutes; a PhaseProduct policy with a k = 8 first band took about 2.4 hours in the policy check's interpreted report stage (the same stage as PhaseTripleProduct's: Lean's interpreter never memoises the closures a table's state is built from, so its cost doubles about every ten operations), and the PhaseTripleProduct policy with a k = 8
first band did not finish that stage in 12 hours. Both harnesses now have a `--fast` option (`check_banded_policy.py --widths exact --fast` for PhaseProduct, `check_ptp_policy.py --fast` for PhaseTripleProduct, both in the submissions repository; the hosted check uses it) that skips that interpreted stage: the ladder comes from Python
and the kernel certificate re-proves it, so a wrong ladder makes the certificate fail. With it a k = 8 PhaseProduct policy that took 2.4 hours in the report stage was accepted in about two minutes, and a PhaseTripleProduct k = 8 policy and a k = 6 baseline in about a minute each, with the same audited theorems; the census and formula numbers of the report (and PhaseProduct's advisory `bands_contract` theorem) are not
evaluated (they are diagnostics). Give a long run a stage limit (`--timeout`), and set `TMPDIR` to a directory you control: a run that long lost a file in the system temporary directory once.
A `rejected` record whose reason is a missing object file, a constructor-field error or a missing file in a temporary directory is an infrastructure failure, not a verdict on the policy: fix it and run again.

## 1. PhaseProduct: the policy check

```bash
python starter.py lean-input my_pp_policy.json --out my_pp_policy.lean-input.json   # the checker's input (no widths)
cd /path/to/forshor-submissions                   # a clone of `main`, built
python3 scripts/memcap.py --cap-gb 8 --timeout 3600 -- \
  python3 scripts/check_banded_policy.py /abs/path/my_pp_policy.lean-input.json --target-width 2048 \
    --widths exact --cap-gb 8 --workdir /tmp/sto_policy_work --out /tmp/my_pp_record.json
```

Exit 0 means accepted. The record names the stage that failed and why. When the check passes, Lean's kernel has
proved the following at the exact widths:

* every band's table satisfies the table contract, and the thresholds strictly decrease;
* `correct`: the recursive evaluation equals φ·a·b at every width;
* `correct_tight`: the register semantics at the exact widths gives φ·a·b on operands that fit;
* `exact_ladder`: the exact ladder at 2048 is the least overflow-free one (Tight.lean);
* the **threshold rule** (`chain_contracts_tight`, `thresholds_at_target`; Threshold.lean): on the recursion the policy
  takes from 2048 at exact widths, every band applied at width w has k ≤ w and shrinks the width. A policy whose
  thresholds are set too low is rejected at stage `threshold`, with the band, its threshold and k, the width and the
  exact child width in the reason. A declared ladder must also pass it (`declared_thresholds`);
* `correct_at_target`, `exact_ladder_at_target`: the same results instantiated at n = 2048.

Every module is replayed by the trusted kernel replay. The axioms must lie within {propext, Classical.choice,
Quot.sound}, and the statements are pinned.

## 2. PhaseTripleProduct: the harness against ForShor's `Shor.PTP`

```bash
python starter.py lean-input my_ptp_policy.json --out my_ptp_policy.lean-input.json
cp -R /path/to/forshor-submissions /tmp/ptp_work           # use a new destination and a tree built with the PTP targets above
cd /tmp/ptp_work
python3 scripts/memcap.py --cap-gb 8 --timeout 3600 -- \
  python3 scripts/check_ptp_policy.py /abs/path/my_ptp_policy.lean-input.json --target-width 2048 \
    --widths exact --cap-gb 8 --out /tmp/my_ptp_record.json
```

The harness checks the same things as the PP policy check for φ·a·b·c, with ForShor's own `Shor.PTP` statements (`correct`, `correct_exact`, `ladder_exact`,
`exact_minimal`). It also compares its Python exact width with the Lean report.

ForShor's PhaseTripleProduct statements also give gate-level correctness through the full recursion (headline cert `gate_recursion`): for every
checked policy, width and disjoint registers, a gate-level recursion following the policy exists (`PolicyImpl`,
`exists_policyImpl`), is correct at every depth (`PolicyImpl.specOn`, `ptp_gate_full`), and its Rz / Toffoli / X / H /
CNOT / And-dagger census equals the policy formulas. Its leaf is the suite's compute–rotate–uncompute sandwich
(Toffoli, Rz, Toffoli per cell: `ptpLeafSandwich`). That is not the digital triple-product leaf the PTP board prices
(the board's leaf is Qualtran's, checked by simulation and operator norm in the site's tests, not in Lean). Since O5
the gate-level result also holds at the exact widths (`gate_recursion_exact`; at n = 2048,
`gate_recursion_exact_at_target`). The harness applies the same threshold rule and at-target certificates as the
PP policy check. It covers equal-width operands only. The qubit formula and the T count are not proved.

## 3. PhaseProduct and full order finding: ForShor

```bash
F=/path/to/ForShor                       # disposable checkout at the operational pin above; first time: lake exe cache get
python starter.py template my_pp_policy.json --out Policy.lean --forshor $F   # or by hand (templates/Policy.lean)
cp Policy.lean $F/ForShor/ShorVerification/Submission/PolicyTemplate.lean   # ForShor's name for the file
cd $F
bash scripts/check_template_lexical.sh ForShor/ShorVerification/Submission/PolicyTemplate.lean   # lexical gate
lake build PolicySubmission forshor_policy_submission       # rebuilds only PolicyTemplate, PolicyCheck, PolicyMain
lake exe forshor_policy_submission --balanced > policy.json
cd - && python starter.py forshor-doc $F/policy.json         # price the printed document (exact-width IR)
```

`Policy.lean` (ForShor's `PolicyTemplate.lean`) is the only file you edit. ForShor's kernel checks C1 to C4 for each band and the sorted
thresholds. `policy_submission_correct` covers the compiled circuit, and at the exact widths the allocated register
widths are the exact widths (`allocated_widths_sound`, re-proved from the exact width). `--balanced` also runs the
balanced tier and prints the balanced IR templates (`balanced_phase_product`, `balanced_cphase_product`). The IR and every kernel-checked part
are the same however the evaluation tier is probed; the document records the choice in `extraction.probe_thresholds`. `starter.py forshor-doc` prices the document's own IR and the
converted policy; the two must agree (it prints `agrees`). The gate census of that IR is matched to the compiled circuit at sampled
widths, not proved at n = 2048. Checking the printed `policy.json` again in Python (the organisers' fixed trial count 386 and precision, the thresholds, each table's Toom-Cook
identity) is what `forshor-doc` does for the fixed values; the template's lexical gate refuses `#eval`, `axiom`, `native_decide`, `set_option` and similar before any Lean runs.

ForShor's template has an optional `negation` (`.oneQubit`, the default, or `.scratch`). The printed `policy.json` carries `negation` and `negate_charge`. The boards, and this pack, price `oneQubit` only.

The design selectors (`submittedDesign`, `submittedPrimitive`, `submittedFullRunDesign`) are left out of the file by default, which gives coherent readout, the exact
logical QFT and Ross–Selinger synthesis. Writing a default declaration out in full is accepted and resolves exactly as omission; any other value is refused, and the
routes those other values name are local qualification routes with no publication route. The exact declarations are in `SUBMISSION_FORMAT.md` section 2.

**k = 5 to 8, many bands, repeated k.** ForShor accepts them, through run-time IR extraction: up to 256 bands, and several bands may carry different tables of the
same k, since C1 to C4 are proved per table and per band and `sorted` constrains the thresholds only.

**Scan documents.** A document printed without `--balanced`/`--exact-widths` (ForShor's default width scan, no balanced split, no `ir.width_rule`)
is priced by `starter.py forshor-doc` on upstream's IR, the board's labelled cross-check, not on the board's IR
(`examples/forshor_upstream_scan.policy.json`).

Running a check executes Lean on your machine. For code you do not trust, use a disposable VM or container.

## 4. PhaseTripleProduct: ForShor

A PhaseTripleProduct `Policy.lean` (`namespace PTPPolicySubmission`, `templates/ptp.Policy.lean`) is built by ForShor like the PhaseProduct one, with its own library and executable. Use the same operational pin, not the frozen model checkout:

```bash
F=/path/to/ForShor            # disposable checkout at the operational pin above; first time: lake exe cache get
python starter.py template my_ptp_policy.json --out Policy.lean --forshor $F
cp Policy.lean $F/ForShor/ShorVerification/Submission/PTPPolicyTemplate.lean   # ForShor's name for the file
cd $F
bash scripts/check_template_lexical.sh ForShor/ShorVerification/Submission/PTPPolicyTemplate.lean   # the same lexical gate
lake build PTPPolicySubmission forshor_ptp_policy_submission && lake exe forshor_ptp_policy_submission > policy.json
```

The build kernel-checks the table contract, the strict decrease of the thresholds and the exact-width threshold rule from 2048 (`decide +kernel`) and audits the axioms;
a bad table, a threshold that breaks the rule or an attribute that reaches compiled code makes it fail. Measured: a policy with bands k = 8 and k = 4 builds in about 25 seconds.
The executable prints the bands, the negation method and the benchmark width. It is not a full-run IR and is not an input to `starter.py forshor-doc`; price the PTP table JSON with `starter.py price`.
