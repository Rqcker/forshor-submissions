"""Phase-gradient CATALYST primitives for merged catalysis (Amy et al., arXiv:2305.07720 §VI.C p.29).

Every bloq here has a real gate-level decomposition (And / And† / CNOT, plus Qualtran's own
`Add` where no carry-in is needed). Nothing stands on `AddIntoPhaseGrad`, which in the frozen
Qualtran tree is cost-only (a Toffoli count with no decomposition).

SIGN / BIT CONVENTION (the one every caller must use)
-----------------------------------------------------
The catalyst is a b-qubit register, dtype ``QUInt(b)``, big-endian as everywhere in Qualtran:
``catalyst[0]`` is the most-significant bit = the HALF-TURN bit (catalyst position p = 1),
``catalyst[p-1]`` has turn weight 2^-p, ``catalyst[b-1]`` is the 2^-b bit. Its state is

    |Phi_b>  =  2^(-b/2) · sum_k  exp(-2 pi i k / 2^b) |k>        (Qualtran PhaseGradientState, exponent=-1)

so ADDING an integer v (mod 2^b) to the register returns |Phi_b> times  exp(+2 pi i v / 2^b):
    |Phi_b>  ->  sum_k e^{-2pi i k/2^b} |k+v>  =  e^{+2pi i v/2^b} |Phi_b>.
Adding u into the top L qubits ``catalyst[0:L]`` (a QUInt(L) slice, carries run toward the
half-turn bit, the carry out of catalyst[0] is discarded = a whole turn) is adding u·2^(b-L), i.e.
the phase exp(+2 pi i u / 2^L). The realised diagonal of `MergedTwiddle` is therefore
exp(+2 pi i · x·z / 2^t) (exactly when no row rounds), not its conjugate. Subtraction (the adjoint)
gives the conjugate phase.

PUBLIC BLOQS
------------
CatalystPrep(b, eps)            RIGHT register ``catalyst`` QUInt(b); wraps PhaseGradientState(b, -1).
                                QECGatesCost: 1 T + (b-3) rotations (+ Cliffords): the 3 top qubits are
                                exact (Z, S†, T† after H), the b-3 others are rotations synthesised ONCE.
CtrlAddIntoCatalyst(L, m, carry_in)
                                ctrl: QBit, x: QUInt(m) (absent if m = 0), [cin: QBit], phase: QUInt(L).
                                if ctrl: phase += x (+ cin)  (mod 2^L); x zero-extended into the low end.
                                Ands = m + carry_in (ctrl AND addend bits) + (L - 1) (carry chain)
                                     = L + m          with carry-in  (the contract, exactly)
                                     = L + m - 1      without        (contract minus 1)
                                vs Qualtran's Add(QUInt(L)).controlled() = 2L - 1: we AND the control
                                only into the m live addend bits.
CtrlAddConstIntoCatalyst(L, const)
                                ctrl: QBit, phase: QUInt(L); if ctrl: phase += const (mod 2^L).
                                Controlled load of the constant is CNOTs only; then Add(QUInt(L)):
                                Ands = L - 1 (contract "b" minus 1: the mod-2^L adder has no top carry);
                                0 Ands when const = 0 mod 2^L (identity).
AddWithCarryIn(m, L)            a: QUInt(m) (absent if m = 0), cin: QBit, b: QUInt(L); b += a + cin
                                (mod 2^L). Gidney ripple adder whose first carry IS the cin qubit:
                                L - 1 Ands, L - 1 And†. The building block used by CtrlAddIntoCatalyst.

Costs are counted with Qualtran's QECGatesCost (``and_bloq``; And† is a measurement + Clifford).
"""
from __future__ import annotations

from collections import Counter
from functools import cached_property
from typing import Dict, Iterator, Optional

import attrs
import cirq
import numpy as np

from qualtran import (
    Bloq,
    BloqBuilder,
    QBit,
    QUInt,
    Register,
    Side,
    Signature,
    Soquet,
    SoquetT,
)
from qualtran.bloqs.arithmetic import Add
from qualtran.bloqs.basic_gates import CNOT
from qualtran.bloqs.mcmt import And
from qualtran.bloqs.rotations.phase_gradient import PhaseGradientState
from qualtran.cirq_interop import decompose_from_cirq_style_method

__all__ = [
    "CatalystPrep",
    "AddWithCarryIn",
    "CtrlAddIntoCatalyst",
    "CtrlAddConstIntoCatalyst",
    "catalyst_phase_turns",
    "ctrl_add_ands",
    "ctrl_add_const_ands",
]


def catalyst_phase_turns(v: int, L: int) -> float:
    """Turns of the phase kicked back by adding integer v into the top-L slice: +v / 2^L (mod 1)."""
    return (v % (2 ** L)) / 2 ** L


def ctrl_add_ands(L: int, m: int, carry_in: bool) -> int:
    """QECGatesCost And count of CtrlAddIntoCatalyst(L, m, carry_in): m + carry_in + (L - 1)."""
    return m + int(carry_in) + (L - 1)


def ctrl_add_const_ands(L: int, const: int) -> int:
    """QECGatesCost And count of CtrlAddConstIntoCatalyst(L, const): L - 1, or 0 for const = 0."""
    return 0 if const % (2 ** L) == 0 else L - 1


def _split_le(bb: BloqBuilder, soq: Soquet, n: int):
    """Split a big-endian n-bit soquet into an LSB-first Python list of bit soquets."""
    return list(bb.split(soq))[::-1]


def _join_le(bb: BloqBuilder, bits_le, dtype):
    bits_le = list(bits_le)
    return bb.join(np.array(bits_le[::-1]), dtype=dtype)


# --------------------------------------------------------------------------- catalyst preparation

@attrs.frozen
class CatalystPrep(Bloq):
    """Prepare the b-qubit phase-gradient catalyst |Phi_b> (see module docstring for the sign).

    Decomposes into Qualtran's `PhaseGradientState(b, exponent=-1, eps)` (H on every qubit, then
    ZPow(-1/2^i) on catalyst[i]) and a Cast QFxp(b, b) -> QUInt(b). Under QECGatesCost the top three
    qubits are exact (Z, S†, T† -> 1 T) and the remaining b - 3 are arbitrary rotations: the catalyst's
    one-time price is (b - 3) synthesised rotations, whatever number of additions later use it.
    `eps` is the total synthesis budget, split evenly by Qualtran (eps / b per ZPow).

    Registers:
        catalyst: RIGHT, QUInt(b).
    """

    b: int
    eps: float = 1e-10

    def __attrs_post_init__(self):
        if int(self.b) < 1:
            raise ValueError("catalyst width b must be >= 1")

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("catalyst", QUInt(self.b), side=Side.RIGHT)])

    @property
    def n_rotations(self) -> int:
        return max(0, self.b - 3)

    def build_composite_bloq(self, bb: BloqBuilder) -> Dict[str, SoquetT]:
        from qualtran.bloqs.bookkeeping import Cast

        pg = PhaseGradientState(self.b, exponent=-1, eps=self.eps)
        phi = bb.add(pg)
        phi = bb.add(Cast(pg.phase_dtype, QUInt(self.b)), reg=phi)
        return {"catalyst": phi}


# --------------------------------------------------------------------------- adder with carry-in

@attrs.frozen
class AddWithCarryIn(Bloq):
    """b <- b + a + cin (mod 2^L); a is m bits (m <= L), zero-extended; 0 <= m.

    Gidney (arXiv:1709.06648) ripple-carry adder with temporary logical-ANDs, in exactly the form of
    Qualtran's `Add` decomposition except that carry c_0 is the `cin` qubit instead of 0:
        c_{d+1} = MAJ(a_d, b_d, c_d) = ((a_d ^ c_d) AND (b_d ^ c_d)) ^ c_d,   d = 0 .. L-2
    (for d >= m, a_d = 0 and c_d stands in for a_d ^ c_d, as Qualtran does for narrower `a`).
    Cost: L - 1 And, L - 1 And† (measurement), Cliffords. `cin` is returned unchanged.
    """

    m: int
    L: int

    def __attrs_post_init__(self):
        if not (0 <= self.m <= self.L) or self.L < 1:
            raise ValueError(f"need 0 <= m <= L, L >= 1; got m={self.m}, L={self.L}")

    @cached_property
    def signature(self) -> Signature:
        regs = []
        if self.m > 0:
            regs.append(Register("a", QUInt(self.m)))
        regs.append(Register("cin", QBit()))
        regs.append(Register("b", QUInt(self.L)))
        return Signature(regs)

    def on_classical_vals(self, **vals) -> Dict[str, int]:
        a = int(vals.get("a", 0))
        out = dict(vals)
        out["b"] = (int(vals["b"]) + a + int(vals["cin"])) % (2 ** self.L)
        return out

    def decompose_bloq(self):
        return decompose_from_cirq_style_method(self)

    def decompose_from_registers(self, *, context: cirq.DecompositionContext, **quregs) -> Iterator:
        a = list(quregs["a"][::-1]) if self.m > 0 else []      # LSB first
        b = list(quregs["b"][::-1])
        c0 = quregs["cin"][0]
        L, m = self.L, self.m
        if L == 1:
            yield CNOT().on(c0, b[0])
            if m == 1:
                yield CNOT().on(a[0], b[0])
            return
        anc = context.qubit_manager.qalloc(L - 1)              # anc[d] = carry c_{d+1}

        def carry(d):
            return c0 if d == 0 else anc[d - 1]

        for d in range(L - 1):                                 # compute carries
            cd = carry(d)
            if d < m:
                yield CNOT().on(cd, a[d])
                ctl = a[d]
            else:
                ctl = cd
            yield CNOT().on(cd, b[d])
            yield And().on(ctl, b[d], anc[d])
            yield CNOT().on(cd, anc[d])
        yield CNOT().on(carry(L - 1), b[L - 1])               # top sum bit
        if L - 1 < m:
            yield CNOT().on(a[L - 1], b[L - 1])
        for d in range(L - 2, -1, -1):                         # uncompute carries, write sums
            cd = carry(d)
            yield CNOT().on(cd, anc[d])
            if d < m:
                yield And().adjoint().on(a[d], b[d], anc[d])
                yield CNOT().on(cd, a[d])
                yield CNOT().on(a[d], b[d])
            else:
                yield And().adjoint().on(cd, b[d], anc[d])
        context.qubit_manager.qfree(anc)

    def _n_cnot(self) -> int:
        L, m = self.L, self.m
        if L == 1:
            return 1 + (m == 1)
        n = 0
        for d in range(L - 1):
            n += (3 if d < m else 2) + (3 if d < m else 1)
        n += 1 + (L - 1 < m)
        return n

    def build_call_graph(self, ssa) -> Dict[Bloq, int]:
        cg: Counter = Counter({CNOT(): self._n_cnot()})
        if self.L > 1:
            cg[And()] += self.L - 1
            cg[And().adjoint()] += self.L - 1
        return dict(cg)

    def __str__(self):
        return f"AddWithCarryIn({self.m},{self.L})"


# --------------------------------------------------------------------------- primitive 1

@attrs.frozen
class CtrlAddIntoCatalyst(Bloq):
    """Controlled addition of an m-bit register window (optionally + one rounding bit) into an L-bit
    catalyst slice:  if ctrl:  phase <- phase + x + cin  (mod 2^L).

    `x` lands in the LOW end of the slice (x's LSB on phase[L-1]); carries run toward phase[0]. Pass
    the catalyst's most-significant L qubits as `phase` (catalyst[0:L]); then the kicked-back phase is
    exp(+2 pi i (x + cin) / 2^L) when ctrl = 1 and the catalyst returns unchanged.

    `carry_in=True` adds register ``cin``: the first DROPPED addend bit (catalyst position b+1).
    Adding it is round-half-up of the truncated window, i.e. round_b of the full addend.

    Decomposition: m (+1) Ands t_j = ctrl AND x_j (and ctrl AND cin); `Add(QUInt(m), QUInt(L))`
    (L - 1 Ands) without carry-in, `AddWithCarryIn(m, L)` (L - 1 Ands, first carry = ctrl AND cin)
    with it; then And† of the temporaries. QECGatesCost Ands = m + carry_in + L - 1.

    Args:
        L: slice length (1 <= L); m: live addend bits, 0 <= m <= L (m = 0 only with carry_in).
    Registers:
        ctrl: QBit; x: QUInt(m) if m > 0; cin: QBit if carry_in; phase: QUInt(L).
    """

    L: int
    m: int
    carry_in: bool = False

    def __attrs_post_init__(self):
        if self.L < 1 or not (0 <= self.m <= self.L):
            raise ValueError(f"need L >= 1 and 0 <= m <= L; got L={self.L}, m={self.m}")
        if self.m == 0 and not self.carry_in:
            raise ValueError("m = 0 without carry_in is the identity; do not instantiate it")

    @cached_property
    def signature(self) -> Signature:
        regs = [Register("ctrl", QBit())]
        if self.m > 0:
            regs.append(Register("x", QUInt(self.m)))
        if self.carry_in:
            regs.append(Register("cin", QBit()))
        regs.append(Register("phase", QUInt(self.L)))
        return Signature(regs)

    @property
    def n_ands(self) -> int:
        return ctrl_add_ands(self.L, self.m, self.carry_in)

    @cached_property
    def _adder(self) -> Bloq:
        if self.carry_in:
            return AddWithCarryIn(self.m, self.L)
        if self.L == 1:
            return CNOT()          # Qualtran's Add(QUInt(1)) call graph reports -3 CNOTs; avoid it
        return Add(QUInt(self.m), QUInt(self.L))

    def on_classical_vals(self, **vals) -> Dict[str, int]:
        out = dict(vals)
        if int(vals["ctrl"]):
            add = int(vals.get("x", 0)) + int(vals.get("cin", 0))
            out["phase"] = (int(vals["phase"]) + add) % (2 ** self.L)
        return out

    def build_composite_bloq(self, bb: BloqBuilder, ctrl: Soquet, phase: Soquet,
                             x: Optional[Soquet] = None, cin: Optional[Soquet] = None
                             ) -> Dict[str, SoquetT]:
        xs = _split_le(bb, x, self.m) if self.m > 0 else []
        tmps = []
        for j in range(self.m):
            (ctrl, xs[j]), t = bb.add(And(), ctrl=[ctrl, xs[j]])
            tmps.append(t)
        c = None
        if self.carry_in:
            (ctrl, cin), c = bb.add(And(), ctrl=[ctrl, cin])
        if self.carry_in:
            if self.m > 0:
                a = _join_le(bb, tmps, QUInt(self.m))
                a, c, phase = bb.add(self._adder, a=a, cin=c, b=phase)
                tmps = _split_le(bb, a, self.m)
            else:
                c, phase = bb.add(self._adder, cin=c, b=phase)
        elif self.L == 1:
            ph = bb.split(phase)
            tmps[0], ph[0] = bb.add(CNOT(), ctrl=tmps[0], target=ph[0])
            phase = bb.join(ph, dtype=QUInt(1))
        else:
            a = _join_le(bb, tmps, QUInt(self.m))
            a, phase = bb.add(self._adder, a=a, b=phase)
            tmps = _split_le(bb, a, self.m)
        if self.carry_in:
            ctrl, cin = bb.add(And().adjoint(), ctrl=[ctrl, cin], target=c)
        for j in reversed(range(self.m)):
            ctrl, xs[j] = bb.add(And().adjoint(), ctrl=[ctrl, xs[j]], target=tmps[j])
        out = {"ctrl": ctrl, "phase": phase}
        if self.m > 0:
            out["x"] = _join_le(bb, xs, QUInt(self.m))
        if self.carry_in:
            out["cin"] = cin
        return out

    def build_call_graph(self, ssa) -> Dict[Bloq, int]:
        k = self.m + int(self.carry_in)
        cg: Counter = Counter({self._adder: 1})
        if k:
            cg[And()] += k
            cg[And().adjoint()] += k
        return dict(cg)

    def __str__(self):
        return f"CtrlAddIntoCatalyst(L={self.L},m={self.m}{',cin' if self.carry_in else ''})"


# --------------------------------------------------------------------------- primitive 2

@attrs.frozen
class CtrlAddConstIntoCatalyst(Bloq):
    """if ctrl: phase <- phase + const (mod 2^L), a classical constant; kicks back exp(+2 pi i const/2^L).

    Used by the P route of a generic leaf: const = round_b(frac(tau 2^l)) · 2^b with L = b (the whole
    catalyst). Decomposition: allocate L qubits, CNOT(ctrl -> k_i) for every set bit of const (the
    controlled load is Clifford: no addend Ands), `Add(QUInt(L))` (L - 1 Ands), unload, free.
    const is reduced mod 2^L; const = 0 decomposes to nothing.

    Registers:
        ctrl: QBit; phase: QUInt(L).
    """

    L: int
    const: int

    def __attrs_post_init__(self):
        if self.L < 1:
            raise ValueError("L must be >= 1")

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("ctrl", QBit()), Register("phase", QUInt(self.L))])

    @property
    def k(self) -> int:
        return int(self.const) % (2 ** self.L)

    @property
    def n_ands(self) -> int:
        return ctrl_add_const_ands(self.L, self.const)

    def on_classical_vals(self, ctrl, phase) -> Dict[str, int]:
        if int(ctrl):
            phase = (int(phase) + self.k) % (2 ** self.L)
        return {"ctrl": ctrl, "phase": phase}

    def build_composite_bloq(self, bb: BloqBuilder, ctrl: Soquet, phase: Soquet) -> Dict[str, SoquetT]:
        if self.k == 0:
            return {"ctrl": ctrl, "phase": phase}
        if self.L == 1:                              # +1 mod 2 under a control is one CNOT
            ph = bb.split(phase)
            ctrl, ph[0] = bb.add(CNOT(), ctrl=ctrl, target=ph[0])
            return {"ctrl": ctrl, "phase": bb.join(ph, dtype=QUInt(1))}
        kreg = bb.allocate(dtype=QUInt(self.L))
        ks = _split_le(bb, kreg, self.L)
        set_bits = [i for i in range(self.L) if self.k >> i & 1]
        for i in set_bits:
            ctrl, ks[i] = bb.add(CNOT(), ctrl=ctrl, target=ks[i])
        kreg = _join_le(bb, ks, QUInt(self.L))
        kreg, phase = bb.add(Add(QUInt(self.L)), a=kreg, b=phase)
        ks = _split_le(bb, kreg, self.L)
        for i in reversed(set_bits):
            ctrl, ks[i] = bb.add(CNOT(), ctrl=ctrl, target=ks[i])
        bb.free(_join_le(bb, ks, QUInt(self.L)))
        return {"ctrl": ctrl, "phase": phase}

    def build_call_graph(self, ssa) -> Dict[Bloq, int]:
        if self.k == 0:
            return {}
        if self.L == 1:
            return {CNOT(): 1}
        from qualtran.bloqs.bookkeeping import Allocate, Free
        return {CNOT(): 2 * bin(self.k).count("1"), Add(QUInt(self.L)): 1,
                Allocate(QUInt(self.L)): 1, Free(QUInt(self.L)): 1}

    def __str__(self):
        return f"CtrlAddConstIntoCatalyst(L={self.L},k={self.k})"
