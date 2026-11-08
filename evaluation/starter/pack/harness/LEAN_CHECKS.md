# Lean checks of a policy

Lean decides whether a policy is correct; `starter.py` only prices it. The three Lean checkers below are **not in this
pack**: each needs its own Lean + Mathlib build tree (several GB, tens of minutes to build). You can run them yourself if you have the trees; the organisers' checks of a submitted policy
are the ones in the submission repository.

| Submission | Checker | Build tree | Where it comes from |
|---|---|---|---|
| PhaseProduct (PP) banded policy, site policy JSON | STO policy mirror, `scripts/check_banded_policy.py --widths exact` | the interim STO fork | `courtneyjzapata/ShorTableOptimization_temp`, branch `port/forshor-21e129a` (ForShor pinned at `21e129a`, Lean `v4.36.0-rc1`): `lake exe cache get`, then `lake build TableGeneration` |
| PhaseTripleProduct (PTP) banded policy, site policy JSON | `scripts/check_ptp_policy.py --widths exact`, against ForShor's `Shor.PTP` | the same fork | The same tree. `lake build TableGeneration` does **not** build the PhaseTripleProduct modules: build them by name first (`lake build TableGeneration.RecursiveCost.PTP.Census` and the other modules under `TableGeneration/RecursiveCost/PTP/`) or the harness fails at its report stage |
| Full order finding, the submission file `Policy.lean` (ForShor's `PolicyTemplate` format) | ForShor's own build at `21e129a`: `lake build PolicySubmission forshor_policy_submission`, then `lake exe forshor_policy_submission --balanced` | a ForShor checkout | `VerifiedQC/ForShor`, commit `21e129aeefc0fd1bbb8fa63402f5799af2490788` |

Use a machine with at least 16 GB of memory. Run one or two Lean checks at a time and keep every run under a memory cap (each tree's `scripts/memcap.py`). Measured on the policies of the
results seed: most checks take seconds to a few minutes; a PhaseProduct policy with a k = 8 first band took about 2.4 hours in the mirror's report stage, and the PhaseTripleProduct policy with a k = 8
first band did not finish that stage in 12 hours. For PhaseTripleProduct, `check_ptp_policy.py --fast` (branch `harness/ptp-fast-report` of the same fork) skips that interpreted stage: the ladder comes from Python
and the kernel certificate re-proves it, so a wrong ladder makes the certificate fail. With it that k = 8 policy and a k = 6 baseline were accepted in about a minute each, with the same audited theorems; the census and formula numbers of the report are not
evaluated (they are diagnostics). The PhaseProduct harness has no such option. Give a long run a stage limit (`--timeout`), and set `TMPDIR` to a directory you control: a run that long lost a file in the system temporary directory once.
A `rejected` record whose reason is a missing object file, a constructor-field error or a missing file in a temporary directory is an infrastructure failure, not a verdict on the policy: fix it and run again.

## 1. PhaseProduct: the STO policy mirror

```bash
python starter.py lean-input my_pp_policy.json --out my_pp_policy.lean-input.json   # the checker's input (no widths)
cd /path/to/ShorTableOptimization_temp            # branch port/forshor-21e129a
python3 scripts/memcap.py --cap-gb 8 --timeout 3600 -- \
  python3 scripts/check_banded_policy.py /abs/path/my_pp_policy.lean-input.json --target-width 2048 \
    --widths exact --cap-gb 8 --workdir /tmp/sto_policy_work --out /tmp/my_pp_record.json
```

Exit 0 means accepted. The record names the stage that failed and why. When the check passes, Lean's kernel has
proved the following at the exact widths:

* every band's table satisfies STO's table contract, and the thresholds strictly decrease;
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
cp -cR /path/to/ShorTableOptimization_temp /tmp/ptp_work          # the checker writes into its tree: use a copy (built, PTP modules included)
cd /tmp/ptp_work
python3 scripts/memcap.py --cap-gb 8 --timeout 3600 -- \
  python3 scripts/check_ptp_policy.py /abs/path/my_ptp_policy.lean-input.json --target-width 2048 \
    --widths exact --cap-gb 8 --out /tmp/my_ptp_record.json
```

The harness checks the same things as the mirror for φ·a·b·c, with ForShor's own `Shor.PTP` statements (`correct`, `correct_exact`, `ladder_exact`,
`exact_minimal`). It also compares its Python exact width with the Lean report.

ForShor's PhaseTripleProduct statements also give gate-level correctness through the full recursion (headline cert `gate_recursion`): for every
checked policy, width and disjoint registers, a gate-level recursion following the policy exists (`PolicyImpl`,
`exists_policyImpl`), is correct at every depth (`PolicyImpl.specOn`, `ptp_gate_full`), and its Rz / Toffoli / X / H /
CNOT / And-dagger census equals the policy formulas. Its leaf is the suite's compute–rotate–uncompute sandwich
(Toffoli, Rz, Toffoli per cell: `ptpLeafSandwich`). That is not the digital triple-product leaf the PTP board prices
(the board's leaf is Qualtran's, checked by simulation and operator norm in the site's tests, not in Lean). Since O5
the gate-level result also holds at the exact widths (`gate_recursion_exact`; at n = 2048,
`gate_recursion_exact_at_target`). The harness applies the same threshold rule and at-target certificates as the
mirror. It covers equal-width operands only. The qubit formula and the T count are not proved.

## 3. Full order finding: ForShor

```bash
F=/path/to/ForShor                       # VerifiedQC/ForShor at 21e129aeefc0fd1bbb8fa63402f5799af2490788; first time: lake exe cache get
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

**k = 5 / 6 and three or more bands.** ForShor accepts them, through run-time IR extraction (the STO reference policy, k = 6, 3, 2, 2, 2, passes).

**Scan documents.** A document printed without `--balanced`/`--exact-widths` (ForShor's default width scan, no balanced split, no `ir.width_rule`)
is priced by `starter.py forshor-doc` on upstream's IR, the board's labelled cross-check, not on the board's IR
(`examples/forshor_upstream_scan.policy.json`).

Running a check executes Lean on your machine. For code you do not trust, use a disposable VM or container.
