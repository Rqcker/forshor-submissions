r"""Qualtran bloqs for Gidney's one-ancilla increment gate and the negation built on it (modular swap 001).

`GidneyIncrement(n)`: `x -> x + 1 (mod 2^n)` on a `QUInt(n)` register and one BORROWED qubit `anc` (any state,
returned exactly). `GidneyNegate(n)`: `x -> -x (mod 2^n)` = `n` NOTs then the increment. Both have a REAL
decomposition into `XGate` / `CNOT` / `Toffoli` (the explicit gate list of :mod:`.gidney_increment`) acting on the
register's bits and `anc` only: no allocation, so Qualtran's own `QubitCount` of the decomposition is `n + 1`.
Qualtran's classical simulator runs the decomposition (`call_classically`).

The call graph is the closed-form count (`gidney_increment.exact_counts`); the tests check that it equals what
Qualtran counts from the decomposition. Decomposition is capped (`MAX_DECOMPOSE`): the builder is slow for
thousands of wires; the gate list itself is generated and simulated at any n.
"""
from __future__ import annotations

import attrs
import numpy as np
from qualtran import Bloq, BloqBuilder, DecomposeTypeError, QBit, QUInt, Register, Signature
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from . import gidney_increment as gi

MAX_DECOMPOSE = 600


def _call_graph(n: int, negate: bool) -> BloqCountDictT:
    from qualtran.bloqs.basic_gates import CNOT, Toffoli, XGate
    c = gi.exact_counts(n, negate)
    return {b: k for b, k in ((Toffoli(), c["toffoli"]), (CNOT(), c["cnot"]), (XGate(), c["x"])) if k}


def _decompose(bb: BloqBuilder, n: int, gates, x, anc):
    from qualtran.bloqs.basic_gates import CNOT, Toffoli, XGate
    if n > MAX_DECOMPOSE:
        raise DecomposeTypeError(f"n = {n} > MAX_DECOMPOSE = {MAX_DECOMPOSE}")
    w = list(bb.split(x))[::-1] + [anc]                    # wires 0..n-1 LSB first, wire n = the ancilla
    for t in gates:
        if t[0] == "X":
            w[t[1]] = bb.add(XGate(), q=w[t[1]])
        elif t[0] == "CX":
            w[t[1]], w[t[2]] = bb.add(CNOT(), ctrl=w[t[1]], target=w[t[2]])
        else:
            ctrl, w[t[3]] = bb.add(Toffoli(), ctrl=np.array([w[t[1]], w[t[2]]]), target=w[t[3]])
            w[t[1]], w[t[2]] = ctrl
    return {"x": bb.join(np.array(w[:n][::-1]), dtype=QUInt(n)), "anc": w[n]}


@attrs.frozen
class GidneyIncrement(Bloq):
    n: int

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.n)), Register("anc", QBit())])

    def build_composite_bloq(self, bb: BloqBuilder, x, anc):
        return _decompose(bb, self.n, gi.increment_gates(self.n), x, anc)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return _call_graph(self.n, False)


@attrs.frozen
class GidneyNegate(Bloq):
    n: int

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.n)), Register("anc", QBit())])

    def build_composite_bloq(self, bb: BloqBuilder, x, anc):
        return _decompose(bb, self.n, gi.negate_gates(self.n), x, anc)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return _call_graph(self.n, True)
