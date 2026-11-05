"""Gate counts (Qualtran `QECGatesCost` and a CNOT count) and the T conversion for the ranking.

The T conversion is in `tcost.currency` (pure Python, the single definition). This module exports it again.

Qualtran's `GateCounts` has no CNOT field, because the CNOTs are part of `clifford`. Thus `CNOTCount` walks the
same call graph and counts the `CNOT` leaves. This count includes the CNOTs inside the atomic `And` of Qualtran.
`CNOTCount` reads them from the Clifford+T decomposition of `And` in Qualtran (`And.to_clifford_t_circuit()`):
6 CNOTs to compute, and 0 for the measurement-based uncompute And^dagger.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable, Dict

import attrs
from qualtran import Bloq
from qualtran.bloqs.basic_gates import CNOT
from qualtran.resource_counting import CostKey, QECGatesCost, get_cost_value

from .currency import (C_SYN_CERTIFIED, DEFAULT_DELTA, MODEL_VERSION, TS_PER_TOFFOLI,  # noqa: F401
                       c_syn_measured, t_count, t_from_counts, ts_per_rotation, ts_per_rotation_eps)

__all__ = ["CNOTCount", "gate_counts", "symbolic_gate_counts",            # from this module
           "C_SYN_CERTIFIED", "DEFAULT_DELTA", "MODEL_VERSION", "TS_PER_TOFFOLI", "c_syn_measured",  # exported again
           "t_count", "t_from_counts", "ts_per_rotation", "ts_per_rotation_eps"]


@attrs.frozen
class CNOTCount(CostKey[int]):
    """The number of `CNOT` leaves in the call graph."""

    def compute(self, bloq: Bloq, get_callee_cost: Callable[[Bloq], int]) -> int:
        if isinstance(bloq, CNOT):
            return 1
        from qualtran.bloqs.mcmt import And
        if isinstance(bloq, And):                      # And is atomic in Qualtran: count its own Clifford+T word
            return _and_cnots(bloq.cv1, bloq.cv2, bloq.uncompute)
        from qualtran.resource_counting import get_bloq_callee_counts
        total = 0
        for callee, n in get_bloq_callee_counts(bloq):
            total += n * get_callee_cost(callee)
        return total

    def zero(self) -> int:
        return 0

    def __str__(self):
        return "cnot count"


@lru_cache(maxsize=None)
def _and_cnots(cv1: int, cv2: int, uncompute: bool) -> int:
    """Return the number of CNOTs in the Qualtran Clifford+T decomposition of `And(cv1, cv2, uncompute)`."""
    import cirq
    from qualtran.bloqs.mcmt import And
    circuit = And(cv1, cv2, uncompute=uncompute).to_clifford_t_circuit()
    return sum(1 for op in circuit.all_operations()
               if isinstance(op.gate, cirq.CXPowGate) and op.gate.exponent == 1)


def gate_counts(bloq: Bloq) -> Dict[str, int]:
    """Return all stored gate classes as Python ints."""
    g = get_cost_value(bloq, QECGatesCost())
    rot = g.rotation
    out = {"toffoli": int(g.toffoli), "and": int(g.and_bloq), "cswap": int(g.cswap),
           "t": int(g.t), "rotation": int(sum(rot.values())) if isinstance(rot, dict) else int(rot),
           "clifford": int(g.clifford), "measurement": int(g.measurement),
           "cnot": int(get_cost_value(bloq, CNOTCount()))}
    return out


def symbolic_gate_counts(bloq: Bloq) -> Dict[str, Any]:
    """Return the gate counts as sympy expressions (closed forms in the width symbol of the bloq)."""
    g = get_cost_value(bloq, QECGatesCost())
    rot = g.rotation
    return {"toffoli": g.toffoli, "and": g.and_bloq, "cswap": g.cswap, "t": g.t,
            "rotation": sum(rot.values()) if isinstance(rot, dict) else rot,
            "clifford": g.clifford, "measurement": g.measurement,
            "cnot": get_cost_value(bloq, CNOTCount())}
