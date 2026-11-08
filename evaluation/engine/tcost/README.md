# tcost: the T-count cost model

`tcost` prices a ShorTableOptimization recursion policy by the Qualtran gate counts of the
recursive phase product it induces. The counts are converted to T. This file explains the
construction, its certification and its trust model. The trust model was written for the
`tcount` branch of STO, where this T is the leaderboard score; upstream `main` has no such
score, and the sections on the verifier, leaderboard and CI jobs describe that branch only.

## Verified table in, T count out

```text
# the formally verified table(s) of a built checkout (`lake build TableGeneration` done):
python -m tcost.tcount --lean <ShorTableOptimization checkout> --mode PhaseProduct --k 3 [--k 5 ...] [--delta 0.01 | --eps 1e-12] [--n 2048]

# the same tables from archived verifier output, or a whole width-banded policy:
python -m tcost.tcount --operations results/policies/<id>/operations.json --mode PhaseProduct --k 3
python -m tcost.tcount --policy policy.json      # any format tcost.tables.policy_docs reads; scores every policy in it
```

* `--lean` evaluates the verified `generate mode k` / `generatePointsInOrder mode k` under
  `lake env lean` (`tcost/lean.py`; the verifier uses the same function). That is the
  submission's own table when its policy handles `(mode, k)`, otherwise the best-known
  table. Every table carries the contract proofs `lake build` checked. Any number of
  `(mode, k)` can be fed at once, and different tables are fed by changing
  `Submission/Policy.lean` and rebuilding.
* **Error threshold.** `--delta` is the total rotation-synthesis budget (ε = δ/R per
  rotation). `--eps` fixes the per-rotation precision instead.
* **Recursion.** `--recursion best` (default) uses the given tables wherever they lower T,
  so k varies with depth when several are given, with the leaf below the cutoff. `top` uses
  one table at the top level only. `constant` uses one table at every level while it
  shrinks (ForShor `constPolicy`).
* **Output.** The T count, every gate class, the ladder, and the closed form with its valid
  n range; `--out` writes the full JSON report. About 2.5 s per run, mostly the Qualtran
  import.

## Lineage (ResearchSkills Q01 / Q02)

The construction is copied from **Lean-To-Qualtran** and generalized:

| Here | Copied from (Lean-To-Qualtran) | Generalization |
| --- | --- | --- |
| `bloqs.ChainPhaseProduct` / `SymbolicPhaseProduct` (the ranking object), `bloqs.PolicyPhaseProduct` (the concrete oracle T4 compares against) | `bloqs/phase_product.py` `LeanPhaseProduct`, `bloqs/recursive.py` `LeanRecursivePhaseProduct` | any table (not only the Lean-emitted standard schedules); a table chosen **per level** from width bands, so `k` varies with depth; symbolic widths per level; a real `build_composite_bloq` (upstream these are call-graph only) |
| `bloqs.PairLeaf` | the `CZPowGate` grid in the same files | unchanged convention: signed bit weights, exponent folded into (−1, 1], zero cells skipped |
| `bloqs.TripleLeaf`, 3-sided levels | *none* (no PhaseTripleProduct construction exists upstream) | new: exp(iπφ·x·y·z), 3k−2 children per level |
| `tables._next_width` | `width_scan.py` (= ForShor `Layout.lean`, `nextSignedWidth`) | scans `arity` operand sets (x, z or x, y, z) |
| `tables._coeff_polys` | `mirrors/coeff_poly.py` (= ForShor `interpEntry`, `phaseCoeffFromPts`) | q = pointCount(mode, k): 2k−1 or 3k−2 points |
| `currency` (the single T formula, `MODEL_VERSION`, `CONVENTIONS`), `counts` | `constants.py` (`TS_PER_TOFFOLI`, `C_SYN_CERTIFIED`, `rotation_epsilon`) | adds `CNOTCount`, since Qualtran's `GateCounts` folds CNOTs into `clifford`; config is validated against `CONVENTIONS` |
| `tables.Policy` | ForShor `Framework/Policy.lean` `ShorLoweringPolicy.choose` / `Stops` | none |

## Symbolic costing (the ranking path)

The score is a Qualtran closed form evaluated at n. It never comes from a decomposition.

* `tables.chain_for(policy, n)` lists the table each recursion level uses at n. That chain
  is the only thing fixed per policy.
* `bloqs.SymbolicPhaseProduct(mode, chain)` gives level `i` the width symbol `w_i`
  (`w_0 = n`). Qualtran's `QECGatesCost` and `CNOTCount` are then small polynomials in
  `(n, w_1, …, w_d)`. For example, the golden PhaseProduct reference policy
  (`scripts/fixtures/reference_policies.json`, chain k = 6, 3, 2, 2, 2; the 19d683c library's optimum, 68,819,254 T) gives
  `rotation = 1485·w5²`, `and = 226·w1 + 418·w2 + 440·w3 + 1320·w4 + 1485·w5² + 3960·w5 − 6364`,
  with `w1 = Max(⌊n/6⌋ + 42, n − 5⌊n/6⌋ + 41) + 1`. Regenerate with
  `python -m tcost.score <policy.json>`.
* `bloqs.width_relations(chain)` gives `w_{i+1} = nextSignedWidth_i(w_i)` in closed form.
  `tables.width_form` runs Lean's width scan on three integers per slot, because every slot
  width is `max(T + c_T, L + c_L, c_0)` with `L = ⌊w/k⌋` and `T = w − (k−1)L`. So each
  relation is `1 + Max(⌊w/k⌋ + a, w − (k−1)⌊w/k⌋ + b[, c])`, at most three terms. (Nesting
  sympy `Max` naively grows combinatorially; that was the earlier slowdown.)
* `score_policy` substitutes n: about 0.001–0.03 s per policy at n = 2048 (in process; the
  CLI's wall time is dominated by importing Qualtran, about 2 s). The closed form is labelled
  EXACT, and `valid_n` is the *maximal contiguous window* around the scored n (searched within
  [n/4, 4n]) where the policy uses the same chain. The set of n sharing a chain is in general
  not contiguous, so `valid_n` understates where the form holds, never overstates it.
* The planner compiles each table's one-level closed form `own_t(w, W')` and the leaf's
  `leaf(w)` once (integer-exact: `floor(a/b)` is emitted as `a // b`). The dynamic program is
  then integer arithmetic: about 1–2 s over the whole archive, mostly spent compiling. It is
  deterministic (canonical candidate order, explicit tie-break on T, Clifford, CNOT, table
  digest) and returns one table per k, so its output is always a submittable policy.

One Qualtran inconsistency is corrected. With a symbolic width, Qualtran's `XorK(1)` (inside
`Negate = BitwiseNot + AddK(1)`) charges W X gates instead of 1. A symbolic `Negate` therefore
reports 10W − 10 Cliffords against 8W − 8 for its concrete decomposition (T is unaffected).
`bloqs.NegateW` decomposes to Qualtran's `Negate` but attests the concrete call graph
(`BitwiseNot + 2 X + Add`), so symbolic and concrete agree for every W.

## One recursion level

At operand width `w` the policy's table `T` (arity `k`, `q` points) shrinks the width to
`W' = nextSignedWidth(T, w) < w`. One level then does the following on every operand
(2 for the pair, 3 for the triple):

1. **Split and extend.** Split the operand into `k` limbs: `k−1` low limbs of `⌊w/k⌋`
   bits and a top limb holding the rest. Extend each limb to a `QInt(W')` slot: low limbs
   by fresh zero qubits, the signed top limb by a CNOT fan-out from its sign bit (ForShor
   `signExtend`).
2. **Run the table program.** `addScaled` becomes `Add(QInt(W'))`. A negated source is
   Negate·Add·Negate, and a shifted source is ShiftL·Add·ShiftR. `negate` becomes
   `Negate(QInt(W'))`. Shifts are a cyclic bit permutation plus `n` CNOTs that restore the
   zero or sign bits: Clifford-only, where Lean prices them as free relabelings.
3. **Phase products.** The `l`-th `phaseProduct i` is a child at width `W'` on slot `i`,
   with angle `φ·c_l(2^⌊w/k⌋)` (`Σ_l c_l·Π_ops(p_l) = Π_ops(2^limb)`).
4. **Teardown.** The program returns every slot to its limb (contract C4). Clear the
   sign extension with the same CNOTs, free the extension (Qualtran's `Free` asserts the
   qubits are zero), and rejoin.

The leaves are a `w×w` grid of `CZPowGate` cells for the pair, each cell And → ZPow →
And†. For the triple, `t = x_a ∧ y_b` is computed once per bit pair, followed by a row of
cells `(t, z_c)` and an uncompute: 1 And + 1 rotation per cell, plus `w²` shared Ands.

## Conventions (fixed by `leaderboard/config.json → tcount`)

* **Angle.** Generic: every leaf cell is a rotation, and identical children are memoized.
* **T conversion.** 4 T per And, Toffoli or CSwap; And† is a measurement. This is
  Lean-To-Qualtran's measured-uncompute comparison column; its own ledger currency is 8 T per
  And pair (unitary uncompute). Gate counts are identical to Lean-To-Qualtran's; T counts use
  the 4 T convention. At 7 or 8 T per And the PhaseProduct optimum drops its last k = 2 band
  (BENCHMARK.md, sensitivity).
* **Rotations.** Ross–Selinger synthesis only: `round(3.075·log2(1/ε) + 0.86)` T each, with
  `ε = δ/R`, `δ = 0.01`, and `R` the policy's rotation count. No flat per-rotation price.
* **Width.** n = 2048 operand bits.
* **Stored per result.** toffoli, and, cswap, t, rotation, clifford, cnot, measurement,
  T count and T per rotation.

`cnot` counts every CNOT, including those inside Qualtran's atomic `And`. Those are read
from Qualtran's own Clifford+T word for it (`And.to_clifford_t_circuit()`): 6 CNOTs to
compute, 0 for the measurement-based uncompute. `clifford` is Qualtran's `QECGatesCost`
Clifford count, which treats `And` as atomic, so it excludes the CNOTs and other Cliffords
inside each And. That is why `cnot` can exceed `clifford`.

## Consistency across the three repositories

These are the conventions that differed and how they are resolved:

| Quantity | ForShor / ShorTableOptimization | Lean-To-Qualtran | here |
| --- | --- | --- | --- |
| leaf cell | `CPhase` = 3 Rz + 2 CNOT | CZPow = And + 1 rotation + And† | CZPow (Lean-To-Qualtran) |
| adder | Cuccaro, 2w−3 Toffoli | `Add(QInt(W))`, W−1 And | `Add(QInt(W'))`, uniform child width |
| negSrc | free (`addScaledResources` ignores it) | Negate·Add·Negate | Negate·Add·Negate |
| shifts | free | free | n CNOTs (physical) |
| sign extension | 2·(W'−top) CNOT per side per node | not priced | 2·(W'−top) CNOT per side per node |
| child width | `nextSignedWidth` | `nextSignedWidth` (sym policy) | `nextSignedWidth`, generalized to 3 operands |

The T5 oracle (below) checks this table. On Lean-To-Qualtran's own Lean-emitted standard
tables (k = 2..7, n ∈ {64, 256, 2048}), `tcost` reproduces `LeanRecursivePhaseProduct`'s
ladder and its And, rotation, measurement and Toffoli counts exactly. The Clifford
difference equals, exactly, the sign-extension and shift CNOTs that `tcost` makes
physical. So gate counts agree with Lean-To-Qualtran's wherever both apply; T differs only
by the 4-vs-8 T-per-And currency choice described above.

## Certification

`python -m tcost.certify --archive <operations.json ...> [--l2q <Lean-To-Qualtran>]` (or
`TCOST_L2Q`) writes `certification/results/tcost_seam_witness.json`. Every check is either
symbolic or exact at a scale where the full unitary is cheap. Nothing simulates a large
circuit. The witness records the Qualtran actually loaded (its own `__version__` and path),
a content hash of the archive and of the Lean-To-Qualtran sources, and the seed rule.

| Check | What it establishes |
| --- | --- |
| T0 width form | closed-form `nextSignedWidth` equals Lean's literal scan, every table, for w ∈ [k, 256] and every width (±2) any scored ladder visits |
| T1 tensor action | QFT(z)·PP·IQFT(z) at φ = 2c/2^w is the permutation z ← z + c·x (triple: + c·x·y). The fully flattened circuit (every And, ZPowGate, adder, CNOT, extension qubit) is tensor-contracted at ≤ 10 external qubits: every leaf (pair w ≤ 5, triple w ≤ 3), forced pair Karatsuba levels, and a two-level chain |
| T1d diagonal action | the same circuits at the non-dyadic φ = 3/7, contracted directly and compared with diag(exp(iπφ·x_s·z_s)) on **signed** operands. This catches sign-bit and pruned-cell errors that the dyadic QFT form cannot see |
| T1b primitives | `ShiftL`/`ShiftR`/`NegateW`/`Extend` decompositions equal their classical action, exhaustively for W ≤ 6 |
| T2 level phase | one forced level of every table at limb width 1 (and 2 for k ≤ 5) and at the first limb width where every child angle is nonzero (at limb m the radix 2^m is often a table point, leaving one live child), children acting as declared: exact phased classical simulation, exhaustive when there are at most 4096 inputs |
| T3 cost seam | symbolic cost (substituted) equals the attested call graph equals the decomposition, in all 8 fields, for one level of every table and every leaf; plus one end-to-end flattening |
| T4 ranking path | symbolic score equals concrete call-graph cost (every table as a constant policy, n ∈ {64, 2048}); planner's composed counts equal the score |
| T5 oracle | Lean-To-Qualtran `LeanRecursivePhaseProduct` on its own Lean-emitted tables: ladder and And/rotation/measurement/Toffoli equal; Clifford gap equals the CNOTs made physical here. The import is asserted to come from `--l2q` |

T1/T1d + T2 is the induction over the recursion, the shape of ForShor's
`evalL_lowerGateRec_correct`. Leaves are exact quantum, and each level is correct given
correct children. It covers every policy at every width, given that the slot width W′
suffices.

**Width sufficiency is inherited from ForShor, not certified here.** The construction
allocates exactly Lean's `nextSignedWidth` for every slot. Lean's width model is what
guarantees no overflow; T2's small inputs are not a width-sufficiency proof.

Pair levels with k ≥ 3 have child widths of 14–41 bits even at w = 5, and a triple level's
7×7×7 child grids take about 50 s to contract. Those levels get the exact T2 check instead.

`scripts/test_tcost.py` and `scripts/test_tcount_backend.py` are the fast subset that CI
runs in a trusted job. They include golden T values and ladders of the reference policies
(`scripts/fixtures/`), currency pins, the signed diagonal check, and every adversarial case
from the 2026-10-12 audit. The audit's surviving mutants (T formula, width off-by-one,
unsigned sign bit) now fail them.

ResearchSkills `bloq_gate.py` (HG9) specs are in `certification/specs/`, each carrying its
exact regenerate command, and their witnesses are in `certification/results/`. `bloq_gate.py` is not part of this
repository: only the author can regenerate these witnesses. Everyone can read and validate them:

* The φ = 0 specs (`pair_k2_w4`, `pair_k2k2_w4`, `triple_k2_w2`) **PASS**. At φ = 0 every
  leaf cell is skipped, so they certify the arithmetic and extension scaffold and clean
  ancillas only.
* `pair_k2_w4.cost_generic` records the decomposition, cost seam and logical profile of the
  ranked configuration. Its action seam fails by construction, because `ZPowGate(theta)` has
  no classical action. The phase action is T1/T1d/T2.

## Trust model (who can influence a ranked number)

This section applies when `tcost` is part of a ranking pipeline (the `tcount` branch). A user of this folder as a
cost tool needs only `tcost.lean` (the nonce protocol) from it.

Submissions are untrusted Lean code; the verifier builds them. The defences:

* **Code at build time.** The denylist bans every elaboration-time execution form
  (`#eval`, `run_tac`, `run_meta`, `run_elab`, `elab`/`macro`/`syntax`/`notation` in any form,
  meta attributes, `import Lean`) and every output primitive (`dbgTrace`, `dbg_trace`,
  `IO.print*`). The verifier imports all trusted modules, Qualtran included, *before*
  building the submission. Under CI, `tcost` never imports a Qualtran tree from the workspace.
* **Lean output.** Every value the backend reads carries a fresh per-run nonce
  (`tcost/lean.py`), is parsed strictly, and is replayed in Python: tables through
  `Table.check`, bounded before replay; printed metrics against the printed program.
* **Ranked T.** The leaderboard step, which runs in the trusted report job, re-scores every
  submitted policy from its archived band tables with tcost and rejects the artifact on any
  mismatch. Configuration comes from the trusted checkout, must agree with
  `tcost.currency`, and its fingerprint is stored with every score.
* **Promotion.** Accepted sources are scanned before they are built, and no token is on
  disk until the publish step.

* **Two CI jobs.** `scripts/verifier.py --stage lean` runs in the untrusted job: preflight,
  `lake build`, axiom audit, and the two Lean evaluations, saved as raw records in
  `table-generation-lean-evaluation.json`. That job has no secrets, no Qualtran and no
  `tcost` scoring. `--stage score` runs in the trusted job, which never has a Lean build. It
  re-runs the static preflight on the submission source, re-parses the records strictly,
  requires them to cover exactly the needed targets, replays every table (`Table.check`) and
  every printed count, and scores. `--stage all` (the default) runs both halves in one process
  for local use.
* **Library.** The fallback table library is chosen by T_solo, computed in the trusted
  leaderboard step and re-derived at promotion.

What still rests on the untrusted job's report is that the submission's Lean proofs check
(`lake build` and the axiom audit). The denylist is enforced again in the trusted job, so no
code can have run during that build to falsify the report.

## Tools

* `python -m tcost.score policy.json`: score one policy (the file's first).
* `tcost.tables.policy_docs(doc)` / `load_policies(path)`: the one policy reader every entry point uses.
* `tcost.planner.optimal_policy(tables, mode, n)`: the cheapest policy buildable from a
  set of tables, by dynamic programming over compiled closed forms. Its composed counts equal
  the symbolic score of the policy it returns (checked).
* `tests/test_tcost.py`: fast regression tests. `tests/`: the full suite.

Outside CI, Qualtran is loaded from `vendor/Qualtran-main/Qualtran-main` (0.8.0.dev0,
the tree Lean-To-Qualtran's numbers come from); under CI (`GITHUB_ACTIONS`) the installed
`qualtran==0.7.0`, which counts identically on the certified policies.
Override with `TCOST_QUALTRAN_PATH`. Import `tcost` before `qualtran`.
