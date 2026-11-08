"""Cost constants — the single definition site (audit F9: previously 4–5 copies each).

Every figure and table states which of these it used; nothing else in the package may
redeclare them.
"""
from __future__ import annotations

import math

#: Qualtran's default rotation → T heuristic (``GateCounts.total_t_count(ts_per_rotation=…)``).
TS_PER_ROTATION: int = 11
#: T per Toffoli / And (``ts_per_toffoli``).
TS_PER_TOFFOLI: int = 4

# And-pair contract (owner decision, 14 Sep 2026, T4): the computation stays UNITARY -- every And is uncomputed by
# the exact inverse of its 13-gate 4-T compute word, never by measurement. One pair = 4 + 4 = 8 T. This is the
# price of every gadget cell, adder carry and QROM node in the ledger from T4 on; the measured-uncompute price
# (4 T per pair, Qualtran's `QECGatesCost.total_t_count` convention) is kept only as a labelled comparison column.
T_PER_AND_COMPUTE: int = 4
T_PER_AND_UNCOMPUTE: int = 4
T_PER_AND_PAIR: int = T_PER_AND_COMPUTE + T_PER_AND_UNCOMPUTE
#: Stated schoolbook full-multiply reference, T ≈ 15 n².
SCHOOLBOOK_PREFACTOR: int = 15
#: Default synthesis precision for ε-aware rotation costs.
DEFAULT_EPS: float = 1e-10

# ForShor Framework/Gatecount/CostModel.lean (shorGateCostModel) — the Lean gate model.
LEAN_RIPPLE_ADDER = lambda w: 9 * w + 2            # rippleAdderGateBound
LEAN_LINEAR_PRIM = lambda w: 20 * w + 10           # linearPrimitiveGateBound
LEAN_RADIX_REVERSE = lambda m: 3 * (m // 2)        # radixReverseGateCount
LEAN_NAIVE_PP = lambda x, z: x * z                 # directSignedPhaseProductGateCount
LEAN_NAIVE_CPP = lambda x, z: 5 * x * z            # directCSignedPhaseProductGateCount


def c_syn(eps: float) -> float:
    """Ross–Selinger-style T per rotation at precision ε: 3·log2(1/ε) + 10 (skeletons/t_model)."""
    return 3.0 * math.log2(1.0 / eps) + 10.0


# ---------------------------------------------------------------------------------------------
# BUDGET CONVENTION (2026-09-10) -- the single statement the resource estimator (web/qualtran_worker.py),
# the engine and the Clifford+T synthesizer (clifford_t_synthesis.py) all read:
#   * the ALGORITHMIC error of one order-finding attempt is budgeted at delta through the Lean per-step
#     precision eta = shorEta(delta, n) = delta / n^2 (Shor_GateCount.lean); this fixes every width;
#   * ROTATION SYNTHESIS gets its OWN budget delta, spread evenly over the R arbitrary rotations of the
#     priced object: eps = delta / R per rotation, so the combined failure bound of the attempt is 2*delta.
# Splitting one delta in half instead would change eta and hence every Lean-derived width, so the two
# budgets are kept separate and stated. A phase-gradient register needs b = ceil(log2(1/eps)) bits.
# ---------------------------------------------------------------------------------------------

def rotation_epsilon(delta: float, rotations: int) -> float:
    """Per-rotation synthesis accuracy under the budget convention above: eps = delta / R."""
    return float(delta) / max(int(rotations), 1)


def gradient_bits(delta: float, rotations: int) -> int:
    """Phase-gradient register width b = ceil(log2(1/eps)) for the matched per-rotation eps."""
    return max(1, math.ceil(math.log2(1.0 / rotation_epsilon(delta, rotations))))


#: CERTIFIED against real pygridsynth 2026-09-10 (scripts/certify_c_syn.py ->
#: artifacts/clifford_t/c_syn_certification.json): 17 angles (dyadic 2pi/2^s, the modmul's (c-1)/N for
#: N = 15/21/127, seeded random) x eps in 1e-4..1e-16; least-squares fit of the per-eps MEAN T-count.
#: The legacy `c_syn` (3*log2(1/eps) + 10) sits 7.6% above the measured mean and is exceeded by no single
#: rotation by more than 0.3% -- a safe bound, not an expectation. A resource estimate sums millions of
#: rotations, so the currency prices at the measured mean; `c_syn` stays for callers that want the bound.
C_SYN_CERTIFIED = (3.075, 0.86)


def c_syn_measured(eps: float) -> float:
    """Measured mean T per rotation at precision eps: 3.075*log2(1/eps) + 0.86 (see C_SYN_CERTIFIED)."""
    a, b = C_SYN_CERTIFIED
    return a * math.log2(1.0 / eps) + b


def synthesis_ts_per_rotation(delta: float, rotations: int) -> int:
    """T per rotation under Clifford+T synthesis at the matched eps: round(c_syn_measured(eps))."""
    return int(round(c_syn_measured(rotation_epsilon(delta, rotations))))


def gradient_ts_per_rotation(delta: float, rotations: int, resolution: int | None = None,
                             pair_t: int = T_PER_AND_PAIR) -> int:
    """T per rotation as the phase-gradient gadget actually emitted (T1, 2026-09-11): `pair_t * min(s, b)` for a
    dyadic angle of resolution s >= 1, `pair_t * b` for a generic one -- one And pair per added bit (the control
    And plus the s-1 adder carries), measured in tests/test_t1_lowering_accounting.py. `pair_t` is the And-pair
    contract: 8 T unitary (the T4 decision, default), 4 T with measured uncompute. The former 4*(b-2) was
    Qualtran's cost-only `AddIntoPhaseGrad` formula for an uncontrolled addend and matched no emitted circuit."""
    b = gradient_bits(delta, rotations)
    if resolution is not None and resolution <= 0:
        return 0
    return pair_t * (min(resolution, b) if resolution is not None else b)


def ts_per_rotation(policy: str = "qualtran", eps: float = DEFAULT_EPS) -> float:
    """``'qualtran'`` → 11 (the heuristic every legacy figure used); ``'eps'`` → c_syn(ε)."""
    if policy == "qualtran":
        return float(TS_PER_ROTATION)
    if policy == "eps":
        return c_syn(eps)
    raise ValueError(f"unknown ts_per_rotation policy {policy!r}")
