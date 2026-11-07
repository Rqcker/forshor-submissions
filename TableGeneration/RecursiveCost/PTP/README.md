# `RecursiveCost/PTP/`: PhaseTripleProduct verification suite

Verified recursive Toom–Cook **phase-triple-product** construction over STO tables: the PTP
counterpart of ForShor's `Implementation/PhaseProduct`, standalone (no ForShor policy linkage;
it uses ForShor's gate language `LowGate`, its semantics classes, and the pure `MasterTheorem`).

## Layer order (enforced by `scripts/check_ptp_layers.py`)

```
Contract < Width < Semantics < Leaf < LeafSandwich < Node < Census < Exact < Recursion < Build
 < CensusFull < ExactNode < ExactRecursion < ExactBuild < Threshold < Assertions < Main
 < Controlled < Asymptotic < Unbalanced < QubitsTight < StandardModel
```
A module may import only modules to its left. `Plan*`, `PlanSeeds/`, `Instances/`, `RecursionWitness`
are leaf consumers.

| Layer | Modules | Content |
|---|---|---|
| table + policy | `Contract`, `Width` | `PTPTable.contract` (= STO `ValidPointList` + consumption), magnitude width scan, wrapping = exact |
| value semantics | `Semantics`, `Exact` | `evalPhase_correct`, `evalPhaseW_correct`, exact widths `ptpTightWidth` (sound, minimal, ≤ scan) |
| gate level | `Leaf`, `LeafSandwich`, `Node`, `ExactNode` | CCPhase / sandwich leaf, one node in `LowGate` |
| recursion + census | `Census`, `Recursion`, `Build`, `CensusFull`, `ExactRecursion`, `ExactBuild` | `PolicyImpl(D)`, existence, correctness and census at every depth, scan and exact boards (`ptp_gate_full_exact`) |
| acceptance | `Threshold` | `chainContractsTight_iff_reach` |
| public surface | `Assertions`, `Main` | named spec `PTPPhaseOp`, state-level theorem (`CleanClosure`), `ptp_lowerExact_correct` |
| extensions | `Controlled`, `Asymptotic`, `Unbalanced`, `QubitsTight` | controlled PTP by operand masking (`ptp_ctl_lowerExact_correct`); `O(n^{log_k(3k-2)})` gate count (`ptp_gateTotal_bound`, `ptp_circuit_gateCount_bound`); unequal operand widths by in-place sign-extension padding (`ptp_pad_lowerExact_correct`); tight qubit count `3n + ptpWsTD` and `ptpQubits` as a proved upper bound (`ptp_qubits_tight`, `ptp_qubits_le_ptpQubits`) |
| non-vacuity | `StandardModel`, `RecursionWitness`, `Instances/` | specialisation to ForShor's `Model.qs`; concrete circuits; fault witnesses |

## Checks

```
lake build TableGeneration.RecursiveCost.PTP TableGeneration.RecursiveCost.PTP.Main \
           TableGeneration.RecursiveCost.PTP.Controlled TableGeneration.RecursiveCost.PTP.Asymptotic \
           TableGeneration.RecursiveCost.PTP.StandardModel
lake build ForShor.ShorVerification.Submission.Audit   # once
lake env lean scripts/PTPAxiomCheck.lean    # #assert_axioms on the headlines
python3 scripts/check_ptp_layers.py          # layer order, no sorry/native_decide/axiom
```

## Scope and caveats (read before citing)

* State-level theorems cover the span of **clean** kets (operands fit signed `n ≥ 1` bits, workspace `|0⟩`).
* Controlled PTP is built by **masking** one operand (`Y = ctrl ∧ X₀`), costing `2n` Toffoli
  (`n` of them `†Toffoli`), `n+1` qubits and no extra rotations. ForShor's leaf-control
  (every leaf phase controlled) is a different construction and is **not** proved here.
* The asymptotic theorem is for a **constant policy** (one table) and the exact-board gate count
  (`rz + toffoli + x + h + cnot` incl. extension), under ForShor's `shorGateResourceModel`
  (Cuccaro adders, Rz = arbitrary-angle rotations, not T).
* Unequal operand widths are handled by **padding** (sign-extend each operand in place into a clean reserve to
  the common width `n`, run the balanced circuit, undo): CNOT cost `+2(d₀+d₁+d₂)`, not a native unbalanced node
  (a node at the minimum limb width is not built).
* Qubits: the circuit names at most `3n + ptpWsTD` distinct qubits (`LowGate.usedQubits`; `ptpWsTD` = `3(kC'−n)`
  per node, children share one pool, 2 leaf ancillas), and `ptpQubits` (scan board) is a proved *upper bound* of
  that count. This is **not** the framework's `qubitCount`, which adds `resources.cleanAnc` (Cuccaro/negation
  scratch): that scratch is not bounded here for the recursive circuit. `ptpQubits` is also loose by up to a
  factor `k` per level (private per-child workspace vs the shared pool). ForShor (pin `<FORSHOR_PIN>`) states and
  proves the sum: `ptp_peak_qubits` / `_exact` / `_scan` (`PhaseTripleProduct/Statement.lean`, `PTPPeakQubits*`),
  some circuit following the policy has `qubitCount <= 3n + ptpWsTD + ptpCleanAncD` (an existential upper
  bound, both negation methods); `scripts/check_ptp_policy.py` reports that number as `ptp_peak_qubits`.
* Zero widths: `FitsSignedWidth` needs `0 < w`, so for `n = 0` or any operand of width 0 the clean-state
  statements are vacuous.
* Open: native unbalanced node; least width at which the wrapping semantics is correct (O4); leaf-control variant.
