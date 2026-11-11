"""Qualtran bloqs for recursive phase products that a policy controls (pair and triple).

These bloqs are copies from Lean-To-Qualtran, made general (see the package docstring). They keep these
conventions exactly as in the priced bloqs of Lean-To-Qualtran (`LeanPhaseProduct`, `LeanRecursivePhaseProduct`):

  * Angles are in units of pi: the phase is exp(i*pi*phi*x*z) (pair) / exp(i*pi*phi*x*y*z) (triple). The
    operands are two's-complement signed (`QInt`). This is exactly the `SignedPhaseProd` semantics of ForShor.
  * The leaf is a grid of `CZPowGate` cells (Qualtran: And -> ZPowGate -> And^dagger) with signed bit weights
    (top bit -2^(W-1)). The exponents are folded into (-1, 1], and the zero cells are skipped.
  * Each table op acts on ALL operand sides (2 for pair, 3 for triple) at the uniform child width
    W' = nextSignedWidth: addScaled -> `Add(QInt(W'))`, negSrc is one `Subtract(QInt(W'))` (`AddNegSrcW`),
    negate -> `NegateW(W')`, ForShor's `oneQubit` negation (as the full run prices it).
  * One recursion level has `pointCount(mode, k)` children with angles phi * c_l(2^limb).

These items are new relative to Lean-To-Qualtran. All of them decompose, so the Q01 seam checks can run on them:

  * A real `build_composite_bloq` for one level of ANY table: the limb split, the zero or sign extension of
    each limb to W', the table program on each side, the children, and the teardown. Sign extension is a CNOT
    fan-out (ForShor `signExtend`), charged on alloc and on dealloc.
  * Shifts are physical: a cyclic bit permutation, then n CNOTs that restore the sign or zero bits. (Lean
    prices shifts as free relabelings. Here they are Clifford-only and T-neutral.)
  * A shifted addScaled is ShiftL . Add(QInt(W')) . ShiftR on the source. This keeps the uniform-W adder
    convention.
  * `PolicyPhaseProduct` selects its table for each level from a width-banded `Policy`, so k changes with
    depth. The 3-sided level and `DigitalTripleLeaf` (Stage 17: p = x*y*z computed exactly with two products, one
    rotation per bit of p) give the PhaseTripleProduct construction; the grid `TripleLeaf` is its labelled
    cross-check (`leaf="grid"`).

The generic angle (`phi=None`) uses one fixed symbol for each cell. Thus each cell is a rotation, and identical
children are memoized. The leaderboard prices this configuration.
"""
from __future__ import annotations

import cmath
import math
from collections import Counter
from fractions import Fraction
from functools import cached_property
from typing import Dict, Optional, Tuple

import attrs
import numpy as np
import sympy
from qualtran import Bloq, BloqBuilder, QAny, QBit, QInt, QUInt, Register, Side, Signature, Soquet
from qualtran.bloqs.arithmetic import Add, CAdd, Subtract
from qualtran.bloqs.basic_gates import CNOT, CZPowGate, Toffoli, ZPowGate
from qualtran.bloqs.mcmt import And
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from .tables import PAIR, TRIPLE, Policy, Table, arity

#: The single symbol of each generic-angle cell (one symbol => one memoized cell type).
THETA = sympy.Symbol("theta", real=True)

OPERANDS = {PAIR: ("x", "z"), TRIPLE: ("x", "y", "z")}


def fold(e: Fraction) -> float:
    """exp(i*pi*e) has period 2: reduce e exactly into (-1, 1], then convert it to float (Lean-To-Qualtran
    `_czpow_fold`)."""
    return float(((e + 1) % 2) - 1)


def bit_weight(idx: int, width: int) -> int:
    """Return the signed weight of big-endian bit `idx` of a QInt(width). The MSB (idx 0) has the weight
    -2^(width-1)."""
    return -(1 << (width - 1)) if idx == 0 else 1 << (width - 1 - idx)


def _phase(phi: Fraction, *vals: int) -> complex:
    e = phi
    for v in vals:
        e *= int(v)
    return cmath.exp(1j * math.pi * fold(e))


# ---------------------------------------------------------------------------------------------
# Leaf cells
# ---------------------------------------------------------------------------------------------

@attrs.frozen
class PhaseCell(Bloq):
    """One controlled-phase cell: |a,b> -> exp(i*pi*e*a*b)|a,b>. It decomposes to the Qualtran `CZPowGate`.

    The only purpose of this bloq is to give the cell a classical action and a `basis_state_phase`. With them, the
    phased classical simulation can check the phase of the construction, and it does not touch the Qualtran
    `ZPowGate`.
    """
    exponent: object        # float in (-1, 1] or THETA

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("q", QBit(), shape=(2,))])

    def build_composite_bloq(self, bb: BloqBuilder, q) -> Dict[str, Soquet]:
        q = bb.add(CZPowGate(exponent=self.exponent), q=q)
        return {"q": q}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {CZPowGate(exponent=self.exponent): 1}

    def on_classical_vals(self, q):
        return {"q": q}

    def basis_state_phase(self, q) -> complex:
        if int(q[0]) and int(q[1]):
            return cmath.exp(1j * math.pi * float(self.exponent))
        return 1.0

    def __str__(self):
        return f"PhaseCell({self.exponent})"


def _pair_exponents(wx: int, wz: int, phi: Optional[Fraction]) -> Dict[Tuple[int, int], object]:
    if phi is None:
        return {(a, b): THETA for a in range(wx) for b in range(wz)}
    out = {}
    for a in range(wx):
        for b in range(wz):
            e = fold(phi * bit_weight(a, wx) * bit_weight(b, wz))
            if e != 0.0:
                out[(a, b)] = e
    return out


def _product_toffolis(wx, wz):
    """Toffolis of one Qualtran `Product(wx, wz)`: max * (2 min - 1). Symbolic widths are square at every leaf."""
    if _is_concrete(wx) and _is_concrete(wz):
        return max(int(wx), int(wz)) * (2 * min(int(wx), int(wz)) - 1)
    return wx * (2 * wz - 1)


def product_leaf_callees(wx, wz) -> BloqCountDictT:
    """The digital-product leaf: `p = u_x * u_z` (Qualtran `Product`, Toffolis only), two controlled subtractions that
    fold in the two's-complement sign bits (`CAdd` at each operand width), one rotation per bit of the
    `wx + wz`-bit product, then all of it uncomputed. The same construction as L2Q `ProductPhaseLeaf`. The Toffoli
    count of `Product` is attested against Qualtran at concrete widths in tests/test_digital_leaf.py (`Product`
    itself cannot take a symbolic width)."""
    out: Counter = Counter()
    out[Toffoli()] += 2 * _product_toffolis(wx, wz)
    for w in (wx, wz):
        c = CAdd(QUInt(w), QUInt(w))
        out[c] += 1
        out[c.adjoint()] += 1
    out[ZPowGate(exponent=THETA)] += wx + wz
    return dict(out)


@attrs.frozen
class AddNegSrcW(Bloq):
    """`b <- b - a` on QInt(W): an `addScaled` whose source is negated, priced as ONE adder (Qualtran `Subtract`).
    This is the site convention (`sto_currency.NEGSRC`), the one L2Q and Lean charge. It is atomic here: its
    classical action is written out and its cost is the Qualtran `Subtract` it names."""
    W: object

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("a", QInt(self.W)), Register("b", QInt(self.W))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {Subtract(QInt(self.W)): 1}

    def on_classical_vals(self, a, b):
        W = int(self.W)
        v = (int(b) - int(a)) % (1 << W)
        return {"a": a, "b": v - (1 << W) if v >= 1 << (W - 1) else v}

    def my_static_costs(self, cost_key):
        from qualtran.resource_counting import QubitCount, get_cost_value
        if isinstance(cost_key, QubitCount):
            return get_cost_value(Subtract(QInt(self.W)), cost_key)
        return NotImplemented

    def __str__(self):
        return f"AddNegSrcW({self.W})"


@attrs.frozen
class PairLeaf(Bloq):
    """The schoolbook pair leaf: a wx x wz grid of `PhaseCell`s (ForShor `Naive_SignedPhaseProd`, with Qualtran
    cells)."""
    wx: int
    wz: int
    phi: Optional[Fraction] = None

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.wx)), Register("z", QInt(self.wz))])

    def build_composite_bloq(self, bb: BloqBuilder, x: Soquet, z: Soquet) -> Dict[str, Soquet]:
        xb, zb = bb.split(x), bb.split(z)
        for (a, b), e in _pair_exponents(self.wx, self.wz, self.phi).items():
            xb[a], zb[b] = bb.add(PhaseCell(e), q=np.array([xb[a], zb[b]]))
        return {"x": bb.join(xb, dtype=QInt(self.wx)), "z": bb.join(zb, dtype=QInt(self.wz))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        if self.phi is None:                       # costing: the digital-product leaf (the site's PhaseProduct leaf)
            return product_leaf_callees(self.wx, self.wz)
        return dict(Counter(PhaseCell(e) for e in _pair_exponents(self.wx, self.wz, self.phi).values()))

    def on_classical_vals(self, x, z):
        return {"x": x, "z": z}

    def basis_state_phase(self, x, z) -> complex:
        return _phase(self.phi, x, z) if self.phi is not None else None

    def my_static_costs(self, cost_key):
        """sto_site: the peak qubit count of the leaf, stated so Qualtran need not decompose it. Generic angle (the
        costed digital-product leaf): the two operands, the (wx + wz)-bit product register, and the ancillas of the
        `CAdd` in flight (a `CAdd(w)` counts 4w qubits, 2w + 1 of them its registers: 2w - 1 extra, at the larger
        width). Concrete phi (the grid, used to simulate): the two operands plus the one And ancilla of the
        CZPowGate in flight (tests/test_site_engine.py::test_leaf_qubit_count_matches_decomposition)."""
        from qualtran.resource_counting import QubitCount
        if isinstance(cost_key, QubitCount):
            if self.phi is None:
                wmax = max(int(self.wx), int(self.wz)) if _is_concrete(self.wx) and _is_concrete(self.wz) else self.wx
                return 2 * (self.wx + self.wz) + 2 * wmax - 1
            return int(self.wx) + int(self.wz) + 1
        return NotImplemented

    def __str__(self):
        return f"PairLeaf({self.wx}x{self.wz})"


def _triple_rows(wx: int, wy: int, wz: int, phi: Optional[Fraction]):
    """Return {(a, b): [(c, exponent), ...]} without the rows that are all zero (concrete phi)."""
    rows = {}
    for a in range(wx):
        for b in range(wy):
            if phi is None:
                rows[(a, b)] = [(c, THETA) for c in range(wz)]
                continue
            r = []
            for c in range(wz):
                e = fold(phi * bit_weight(a, wx) * bit_weight(b, wy) * bit_weight(c, wz))
                if e != 0.0:
                    r.append((c, e))
            if r:
                rows[(a, b)] = r
    return rows


@attrs.frozen
class TripleLeaf(Bloq):
    """The schoolbook triple leaf. For each bit pair (x_a, y_b), it computes t = x_a AND y_b one time, then it
    applies a row of `PhaseCell(t, z_c)` over z, then it uncomputes t. The cost per cell is 1 And + 1 rotation,
    plus wx*wy shared Ands."""
    wx: int
    wy: int
    wz: int
    phi: Optional[Fraction] = None

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.wx)), Register("y", QInt(self.wy)), Register("z", QInt(self.wz))])

    def build_composite_bloq(self, bb: BloqBuilder, x: Soquet, y: Soquet, z: Soquet) -> Dict[str, Soquet]:
        xb, yb, zb = bb.split(x), bb.split(y), bb.split(z)
        for (a, b), row in _triple_rows(self.wx, self.wy, self.wz, self.phi).items():
            (xb[a], yb[b]), t = bb.add(And(), ctrl=np.array([xb[a], yb[b]]))
            for c, e in row:
                t, zb[c] = bb.add(PhaseCell(e), q=np.array([t, zb[c]]))
            xb[a], yb[b] = bb.add(And(uncompute=True), ctrl=np.array([xb[a], yb[b]]), target=t)
        return {"x": bb.join(xb, dtype=QInt(self.wx)), "y": bb.join(yb, dtype=QInt(self.wy)),
                "z": bb.join(zb, dtype=QInt(self.wz))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        if self.phi is None:                       # closed form: all rows are present, each cell is a rotation
            pairs = self.wx * self.wy
            return {And(): pairs, And(uncompute=True): pairs, PhaseCell(THETA): pairs * self.wz}
        rows = _triple_rows(self.wx, self.wy, self.wz, self.phi)
        counts: Counter = Counter()
        counts[And()] += len(rows)
        counts[And(uncompute=True)] += len(rows)
        for row in rows.values():
            for _, e in row:
                counts[PhaseCell(e)] += 1
        return dict(counts)

    def on_classical_vals(self, x, y, z):
        return {"x": x, "y": y, "z": z}

    def basis_state_phase(self, x, y, z) -> complex:
        return _phase(self.phi, x, y, z) if self.phi is not None else None

    def my_static_costs(self, cost_key):
        """sto_site: peak qubits of the triple leaf: three operands, the shared And target t of the row in flight,
        and the And ancilla inside the CZPowGate cell in flight. Checked against the decomposition in tests."""
        from qualtran.resource_counting import QubitCount
        if isinstance(cost_key, QubitCount):
            return int(self.wx) + int(self.wy) + int(self.wz) + 2
        return NotImplemented

    def __str__(self):
        return f"TripleLeaf({self.wx}x{self.wy}x{self.wz})"


# ---------------------------------------------------------------------------------------------
# The digital triple-product leaf (Stage 17, 2026-10-28): p = x*y*z exactly, one rotation per bit of p
# ---------------------------------------------------------------------------------------------

@attrs.frozen
class PhaseBit(Bloq):
    """One rotation on one qubit: |b> -> exp(i*pi*e*b)|b>. It decomposes to the Qualtran `ZPowGate`. Like `PhaseCell`
    it exists to give the rotation a classical action and a `basis_state_phase` for the phased classical
    simulation."""
    exponent: object        # float in (-1, 1] or THETA

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("q", QBit())])

    def build_composite_bloq(self, bb: BloqBuilder, q) -> Dict[str, Soquet]:
        return {"q": bb.add(ZPowGate(exponent=self.exponent), q=q)}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {ZPowGate(exponent=self.exponent): 1}

    def on_classical_vals(self, q):
        return {"q": q}

    def basis_state_phase(self, q) -> complex:
        return cmath.exp(1j * math.pi * float(self.exponent)) if int(q) else 1.0

    def __str__(self):
        return f"PhaseBit({self.exponent})"


def uproduct_toffolis(na, nb):
    """Toffolis of the unsigned product of an na-bit and an nb-bit number: 2 na nb - max(na, nb), the count of the
    Qualtran `Product(na, nb)`. Symbolic widths: the caller passes the narrower operand as `na` (nb (2 na - 1))."""
    if _is_concrete(na) and _is_concrete(nb):
        return max(int(na), int(nb)) * (2 * min(int(na), int(nb)) - 1)
    return nb * (2 * na - 1)


def _bits(bb: BloqBuilder, reg) -> list:
    """Little-endian list of the qubits of a register (bit 0 = least significant)."""
    return list(bb.split(reg))[::-1]


def _reg(bb: BloqBuilder, le_bits, dtype):
    return bb.join(np.array(list(le_bits)[::-1]), dtype=dtype)


@attrs.frozen
class UProduct(Bloq):
    """`result = a * b` (unsigned) into a FRESH (na + nb)-bit register (`uncompute=True`: the inverse, which consumes
    the register and requires it to hold a * b). It is priced as the Qualtran `Product(na, nb)`: 2 na nb - max Toffolis,
    the same count each way (the uncompute is priced as `Product`'s adjoint, as the PhaseProduct leaf does).

    The decomposition (concrete widths) is the schoolbook witness that the simulations check: row 0 copies a_0 AND b into result[0:nb] (nb `And`s); row i >= 1 adds b, controlled by a_i, into the window
    result[i : i + nb + 1] (`CAdd(QUInt(nb), QUInt(nb + 1))`: 2 nb Ands). The partial sum before row i is below
    2^(i + nb), so each window holds its sum exactly. Total nb (2 na - 1) Ands: Qualtran's `Product` count when `a` is
    the narrower operand. The uncompute runs the rows backwards as controlled subtractions (X . CAdd . X), frees the
    top na bits (zero once only row 0 is left) and uncomputes row 0 with And^dagger."""
    na: object
    nb: object
    uncompute: bool = False

    @cached_property
    def signature(self) -> Signature:
        side = Side.LEFT if self.uncompute else Side.RIGHT
        return Signature([Register("a", QUInt(self.na)), Register("b", QUInt(self.nb)),
                          Register("result", QUInt(self.na + self.nb), side=side)])

    def build_composite_bloq(self, bb: BloqBuilder, a: Soquet, b: Soquet, result: Soquet = None) -> Dict[str, Soquet]:
        from qualtran.bloqs.basic_gates import XGate
        na, nb = int(self.na), int(self.nb)
        al, bl = _bits(bb, a), _bits(bb, b)

        def window_add(i, r, sub):
            breg = _reg(bb, bl, QUInt(nb))
            win = r[i:i + nb + 1]
            if sub:
                win = [bb.add(XGate(), q=q) for q in win]
            winreg = _reg(bb, win, QUInt(nb + 1))
            al[i], breg, winreg = bb.add(CAdd(QUInt(nb), QUInt(nb + 1)), ctrl=al[i], a=breg, b=winreg)
            win = _bits(bb, winreg)
            if sub:
                win = [bb.add(XGate(), q=q) for q in win]
            r[i:i + nb + 1] = win
            bl[:] = _bits(bb, breg)

        if not self.uncompute:
            r = [None] * (na + nb)
            for j in range(nb):
                (al[0], bl[j]), r[j] = bb.add(And(), ctrl=np.array([al[0], bl[j]]))
            for i in range(nb, na + nb):
                r[i] = bb.allocate(dtype=QBit())
            for i in range(1, na):
                window_add(i, r, sub=False)
            return {"a": _reg(bb, al, QUInt(na)), "b": _reg(bb, bl, QUInt(nb)), "result": _reg(bb, r, QUInt(na + nb))}
        r = _bits(bb, result)
        for i in range(na - 1, 0, -1):
            window_add(i, r, sub=True)
        for i in range(nb, na + nb):
            bb.free(r[i])
        for j in range(nb):
            al[0], bl[j] = bb.add(And(uncompute=True), ctrl=np.array([al[0], bl[j]]), target=r[j])
        return {"a": _reg(bb, al, QUInt(na)), "b": _reg(bb, bl, QUInt(nb))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {Toffoli(): uproduct_toffolis(self.na, self.nb)}

    def on_classical_vals(self, a, b, result=None):
        if not self.uncompute:
            return {"a": a, "b": b, "result": int(a) * int(b)}
        if int(result) != int(a) * int(b):
            raise ValueError(f"UProduct uncompute: result {result} != {a} * {b}")
        return {"a": a, "b": b}

    def __str__(self):
        return f"UProduct({self.na}x{self.nb}{', uncompute' if self.uncompute else ''})"


@attrs.frozen
class CSubW(Bloq):
    """`b <- b - ctrl * a  (mod 2^m)` on unsigned m-bit registers: X^m . CAdd(QUInt(m)) . X^m, because
    ~(~b + a) = b - a and ~~b = b when the control is off. Same T as one `CAdd` (2m - 1 Ands); its inverse is the
    plain `CAdd`."""
    m: object

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("ctrl", QBit()), Register("a", QUInt(self.m)), Register("b", QUInt(self.m))])

    def build_composite_bloq(self, bb: BloqBuilder, ctrl, a, b) -> Dict[str, Soquet]:
        from qualtran.bloqs.basic_gates import XGate
        m = int(self.m)
        bl = [bb.add(XGate(), q=q) for q in _bits(bb, b)]
        ctrl, a, b = bb.add(CAdd(QUInt(m), QUInt(m)), ctrl=ctrl, a=a, b=_reg(bb, bl, QUInt(m)))
        bl = [bb.add(XGate(), q=q) for q in _bits(bb, b)]
        return {"ctrl": ctrl, "a": a, "b": _reg(bb, bl, QUInt(m))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import XGate
        return {CAdd(QUInt(self.m), QUInt(self.m)): 1, XGate(): 2 * self.m}

    def on_classical_vals(self, ctrl, a, b):
        return {"ctrl": ctrl, "a": a, "b": (int(b) - int(ctrl) * int(a)) % (1 << int(self.m))}

    def __str__(self):
        return f"CSubW({self.m})"


def digital_triple_rotation_bits(wx, wy, wz):
    """Rotations of the digital triple leaf: wx + wy + wz - 2. For signed operands |x y z| <= 2^(wx+wy+wz-3), and the
    maximum -2^(W-3) is reached only negatively (all three operands at their minimum; a positive product has 0 or 2
    negative factors, each bounded strictly below 2^(W-3)). So x y z fits a signed (W - 2)-bit register: the top
    three bits of the W-bit product register are copies of its sign bit, and the phase needs bits 0..W-3 only,
    bit W-3 with weight -2^(W-3)."""
    return wx + wy + wz - 2


def digital_triple_callees(wx, wy, wz) -> BloqCountDictT:
    """The priced callees of the digital triple leaf (generic angle), the PTP counterpart of `product_leaf_callees`:
    t = x*y (one `Product(wx, wy)`, two sign folds), p = t*z (one `Product(wx + wy, wz)`, two sign folds), W - 2
    rotations, all of it uncomputed. Each product is priced as Qualtran `Product` both ways; each sign fold is one
    `CSubW` (a `CAdd` and 2m X) and its uncompute one `CAdd`. Engine 2 (`cost_conventions.leaf_callees`) states the
    same with Qualtran's own `Product` and `CAdd`; the T, rotation and And counts agree (tests)."""
    wxy = wx + wy
    out: Counter = Counter()
    out[Toffoli()] += 2 * (uproduct_toffolis(wx, wy) + uproduct_toffolis(wz, wxy))
    for m in (wy, wx, wz, wxy):
        out[CSubW(m)] += 1
        out[CAdd(QUInt(m), QUInt(m))] += 1
    out[ZPowGate(exponent=THETA)] += digital_triple_rotation_bits(wx, wy, wz)
    return dict(out)


def _uproduct_qubits(na, nb):
    """Peak of one `UProduct` witness: a, b, the (na + nb)-bit result, and, from row 1 on, the windowed `CAdd`'s And
    register (nb) and the adder's carries (nb)."""
    return 2 * (na + nb) + (2 * nb if na >= 2 else 0)


def digital_triple_qubits(wx, wy, wz):
    """Peak qubits of the digital triple leaf's decomposition (equal to Qualtran's QubitCount of the decomposition;
    tests/test_digital_triple_leaf.py): the maximum over the steps of operands + live registers + the ancillas of the
    sub-bloq in flight. The largest is building p: x, y (wx + wy) plus `UProduct(wz, wx + wy)` (z, t, p and the window
    adder), 2W + 3 (wx + wy) for wz >= 2. Square width w >= 2: 12w. Symbolic widths: that term."""
    W, wxy = wx + wy + wz, wx + wy
    if not (_is_concrete(wx) and _is_concrete(wy) and _is_concrete(wz)):
        return 2 * W + 3 * wxy
    wx, wy, wz = int(wx), int(wy), int(wz)
    W, wxy = wx + wy + wz, wx + wy
    return max(wz + _uproduct_qubits(wx, wy),                     # t = ux * uy
               W + wxy + 2 * max(wx, wy) - 1,                     # the sign folds of t (CSubW / CAdd in flight)
               wxy + _uproduct_qubits(wz, wxy),                   # p = uz * ut
               2 * W + wxy + 2 * max(wz, wxy) - 1)                # the sign folds of p


@attrs.frozen
class DigitalTripleLeaf(Bloq):
    """The digital PhaseTripleProduct leaf: exp(i*pi*phi*x*y*z) on signed x (wx), y (wy), z (wz).

    1. t = x*y exactly in a fresh (wx + wy)-bit register: `UProduct` of the unsigned patterns ux, uy, then two
       controlled subtractions fold in the sign bits. With x = ux - sx 2^wx, y = uy - sy 2^wy,
       x y = ux uy - sx uy 2^wx - sy ux 2^wy (mod 2^(wx+wy)): subtract uy from the top wy bits controlled by sx and ux
       from the top wx bits controlled by sy. |x y| <= 2^(wx+wy-2), so the signed reading of t IS x y (no wraparound).
    2. p = t*z the same way in a fresh W = wx + wy + wz bit register (`UProduct(wz, wx + wy)`, then subtract uz from
       the top wz bits controlled by t's sign bit, and ut from the top wx + wy bits controlled by z's sign bit).
       |x y z| <= 2^(W-3) < 2^(W-1): the signed reading of p IS x y z.
    3. One rotation per bit b = 0..W-3 of p, angle phi 2^b, the top one (b = W-3) with -phi 2^(W-3) (two's complement;
       p fits W - 2 signed bits, `digital_triple_rotation_bits`). No controlled phase is needed.
    4. Uncompute 2 then 1; every ancilla is returned clean (the simulations check it: `Free` and And^dagger raise
       on a dirty qubit).
    """
    wx: object
    wy: object
    wz: object
    phi: Optional[Fraction] = None

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.wx)), Register("y", QInt(self.wy)), Register("z", QInt(self.wz))])

    def _exponents(self):
        W = int(self.wx) + int(self.wy) + int(self.wz)
        top = W - 3
        if self.phi is None:
            return [THETA] * (W - 2)
        return [fold(self.phi * (-(1 << b) if b == top else (1 << b))) for b in range(W - 2)]

    def build_composite_bloq(self, bb: BloqBuilder, x: Soquet, y: Soquet, z: Soquet) -> Dict[str, Soquet]:
        wx, wy, wz = int(self.wx), int(self.wy), int(self.wz)
        wxy, W = wx + wy, wx + wy + wz
        xl, yl, zl = _bits(bb, x), _bits(bb, y), _bits(bb, z)          # little-endian; sign bit = last

        def csub(m, ctrl_bits, ci, src_bits, dst_bits, lo, uncompute=False):
            """dst[lo:lo+m] -= ctrl * src (uncompute: +=). ctrl is ctrl_bits[ci]."""
            src = _reg(bb, src_bits, QUInt(m))
            dst = _reg(bb, dst_bits[lo:lo + m], QUInt(m))
            b = CAdd(QUInt(m), QUInt(m)) if uncompute else CSubW(m)
            ctrl_bits[ci], src, dst = bb.add(b, ctrl=ctrl_bits[ci], a=src, b=dst)
            src_bits[:] = _bits(bb, src)
            dst_bits[lo:lo + m] = _bits(bb, dst)

        def product(a_bits, na, b_bits, nb, res=None):
            a, b = _reg(bb, a_bits, QUInt(na)), _reg(bb, b_bits, QUInt(nb))
            if res is None:
                a, b, r = bb.add(UProduct(na, nb), a=a, b=b)
                a_bits[:], b_bits[:] = _bits(bb, a), _bits(bb, b)
                return _bits(bb, r)
            a, b = bb.add(UProduct(na, nb, uncompute=True), a=a, b=b, result=_reg(bb, res, QUInt(na + nb)))
            a_bits[:], b_bits[:] = _bits(bb, a), _bits(bb, b)
            return None

        # 1. t = x * y
        t = product(xl, wx, yl, wy)
        csub(wy, xl, wx - 1, yl, t, wx)
        csub(wx, yl, wy - 1, xl, t, wy)
        # 2. p = t * z (rows over z, the narrower operand at square widths)
        p = product(zl, wz, t, wxy)
        csub(wz, t, wxy - 1, zl, p, wxy)
        csub(wxy, zl, wz - 1, t, p, wz)
        # 3. one rotation per bit of the signed (W - 2)-bit value
        for b, e in enumerate(self._exponents()):
            p[b] = bb.add(PhaseBit(e), q=p[b])
        # 4. uncompute
        csub(wxy, zl, wz - 1, t, p, wz, uncompute=True)
        csub(wz, t, wxy - 1, zl, p, wxy, uncompute=True)
        product(zl, wz, t, wxy, res=p)
        csub(wx, yl, wy - 1, xl, t, wy, uncompute=True)
        csub(wy, xl, wx - 1, yl, t, wx, uncompute=True)
        product(xl, wx, yl, wy, res=t)
        return {"x": _reg(bb, xl, QInt(wx)), "y": _reg(bb, yl, QInt(wy)), "z": _reg(bb, zl, QInt(wz))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        if self.phi is None:                       # costing: the closed form (symbolic widths allowed)
            return digital_triple_callees(self.wx, self.wy, self.wz)
        out = Counter(digital_triple_callees(self.wx, self.wy, self.wz))
        del out[ZPowGate(exponent=THETA)]
        for e in self._exponents():
            out[PhaseBit(e)] += 1
        return dict(out)

    def on_classical_vals(self, x, y, z):
        return {"x": x, "y": y, "z": z}

    def basis_state_phase(self, x, y, z) -> complex:
        return _phase(self.phi, x, y, z) if self.phi is not None else None

    def my_static_costs(self, cost_key):
        from qualtran.resource_counting import QubitCount
        if isinstance(cost_key, QubitCount):
            return digital_triple_qubits(self.wx, self.wy, self.wz)
        return NotImplemented

    def __str__(self):
        return f"DigitalTripleLeaf({self.wx}x{self.wy}x{self.wz})"


# ---------------------------------------------------------------------------------------------
# Physical shifts (Clifford-only)
# ---------------------------------------------------------------------------------------------

@attrs.frozen
class ShiftL(Bloq):
    """x -> x * 2^n in a QInt(W) with headroom: a cyclic bit rotation, then n CNOTs clear the low bits. (The low
    bits got copies of the sign bit.) Precondition: |x * 2^n| < 2^(W-1). The width scan makes sure of this."""
    W: int
    n: int

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.W))])

    def build_composite_bloq(self, bb: BloqBuilder, x: Soquet) -> Dict[str, Soquet]:
        W, n = self.W, self.n
        le = list(bb.split(x))[::-1]                       # little-endian
        new = [None] * W
        for p in range(W):
            new[(p + n) % W] = le[p]
        for j in range(n):                                 # the sign copies are now at 0..n-1
            new[W - 1], new[j] = bb.add(CNOT(), ctrl=new[W - 1], target=new[j])
        return {"x": bb.join(np.array(new[::-1]), dtype=QInt(W))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {CNOT(): self.n}

    def on_classical_vals(self, x):
        v = int(x) << self.n
        if not -(1 << (self.W - 1)) <= v < (1 << (self.W - 1)):
            raise ValueError(f"ShiftL({self.W},{self.n}) overflow on {x}")
        return {"x": v}


@attrs.frozen
class ShiftR(Bloq):
    """x -> x / 2^n (exact) in a QInt(W): a cyclic rotation, then n CNOTs copy the sign into the top bits. (The
    top bits got the n low zero bits.) Precondition: 2^n divides x."""
    W: int
    n: int

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.W))])

    def build_composite_bloq(self, bb: BloqBuilder, x: Soquet) -> Dict[str, Soquet]:
        W, n = self.W, self.n
        le = list(bb.split(x))[::-1]
        new = [None] * W
        for p in range(W):
            new[(p - n) % W] = le[p]
        s = W - 1 - n                                      # the new position of the sign bit
        for j in range(n):
            new[s], new[W - n + j] = bb.add(CNOT(), ctrl=new[s], target=new[W - n + j])
        return {"x": bb.join(np.array(new[::-1]), dtype=QInt(W))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {CNOT(): self.n}

    def on_classical_vals(self, x):
        v = int(x)
        if v % (1 << self.n):
            raise ValueError(f"ShiftR({self.W},{self.n}) on non-divisible {x}")
        return {"x": v >> self.n}


@attrs.frozen
class NegateW(Bloq):
    """Two's-complement negation of a QInt(W), priced as the full order-finding run prices it: ForShor's `oneQubit` negation (the
    bitwise complement and Gidney's increment gate with ONE clean ancilla), with the counts of `tcost.negation.negate_counts`
    (Toffoli 12W - 28 / 10W - 18, CNOT 17W - 22 / 17W - 5, X 5W - 3 / 5W + 1) and one clean ancilla.

    Before 2026-11-10 this was the Qualtran `Negate` (about 4 (W - 1) T and 2 W - 1 clean ancillas), which made the PhaseProduct and
    PhaseTripleProduct boards disagree with the full run for any policy whose table negates. It is atomic here, like `AddNegSrcW`:
    its classical action is written out and its cost is the call graph below. The cost seam checks it against the full run's
    `IncrementerNegate` (tests/test_negation_counts.py)."""
    W: object

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.W))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import XGate
        from .negation import negate_counts
        r = negate_counts(self.W)
        return {b: k for b, k in ((Toffoli(), r["toffoli"]), (CNOT(), r["cnot"]), (XGate(), r["x"])) if not (isinstance(k, int) and k == 0)}

    def on_classical_vals(self, x):
        # Two's-complement negation mod 2^W, written out.
        W = int(self.W)
        v = (-int(x)) % (1 << W)
        return {"x": v - (1 << W) if v >= 1 << (W - 1) else v}

    def my_static_costs(self, cost_key):
        from qualtran.resource_counting import QubitCount
        from .negation import CLEAN_ANCILLA
        if isinstance(cost_key, QubitCount):
            return self.W + CLEAN_ANCILLA                    # the register and the one clean ancilla
        return NotImplemented

    def __str__(self):
        return f"NegateW({self.W})"


@attrs.frozen
class Extend(Bloq):
    """Extend a wi-bit limb into a QInt(W) slot: allocate W - wi qubits. An unsigned (lower) limb gets a zero
    extension. The signed top limb gets a sign extension, that is, a CNOT fan-out from the sign bit of the limb
    (ForShor `signExtend`). `uncompute=True` is the inverse: it clears the extension and frees it (ForShor
    `signDealloc`). There is one bloq per limb, so the decomposition of a level has O(ops + k) instances at all
    widths."""
    wi: object
    W: object
    signed: bool
    uncompute: bool = False

    @cached_property
    def signature(self) -> Signature:
        limb_t = QInt(self.wi) if self.signed else QUInt(self.wi)
        a, b = (Side.LEFT, Side.RIGHT) if not self.uncompute else (Side.RIGHT, Side.LEFT)
        return Signature([Register("limb", limb_t, side=a), Register("slot", QInt(self.W), side=b)])

    def build_composite_bloq(self, bb: BloqBuilder, **regs) -> Dict[str, Soquet]:
        wi, W = int(self.wi), int(self.W)
        n = W - wi
        if not self.uncompute:
            limb = list(bb.split(regs["limb"]))
            ext = list(bb.split(bb.allocate(n))) if n > 1 else ([bb.allocate(dtype=QBit())] if n == 1 else [])
            if self.signed:
                for j in range(n):
                    limb[0], ext[j] = bb.add(CNOT(), ctrl=limb[0], target=ext[j])
            return {"slot": bb.join(np.array(ext + limb), dtype=QInt(W))}
        bits = list(bb.split(regs["slot"]))
        ext, limb = bits[:n], bits[n:]
        if self.signed:
            for j in range(n):
                limb[0], ext[j] = bb.add(CNOT(), ctrl=limb[0], target=ext[j])
        if n > 1:
            bb.free(bb.join(np.array(ext), dtype=QAny(n)))
        elif n == 1:
            bb.free(ext[0])
        return {"limb": bb.join(np.array(limb), dtype=QInt(wi) if self.signed else QUInt(wi))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {CNOT(): self.W - self.wi} if self.signed else {}

    def on_classical_vals(self, **vals):
        if not self.uncompute:
            return {"slot": int(vals["limb"])}
        v = int(vals["slot"])
        lo, hi = (-(1 << (int(self.wi) - 1)), 1 << (int(self.wi) - 1)) if self.signed else (0, 1 << int(self.wi))
        if not lo <= v < hi:
            raise ValueError(f"Extend uncompute: slot value {v} does not fit the {self.wi}-bit limb")
        return {"limb": v}


# ---------------------------------------------------------------------------------------------
# One Toom-Cook level (all the recursive bloqs below use it)
# ---------------------------------------------------------------------------------------------

#: The leaves a tree can end in. "product": the site's leaf of each mode (PP digital product, PTP digital triple
#: product since Stage 17). "grid": the schoolbook grid `TripleLeaf`, PhaseTripleProduct only, the labelled
#: cross-check (and the leaf the PTP seeds were planned under before Stage 17).
LEAVES = ("product", "grid")


def _leaf_for(mode: str, w, phi: Optional[Fraction], leaf: str = "product") -> Bloq:
    """The leaf of each mode. PhaseTripleProduct: the digital triple product (Stage 17, 2026-10-28; was the grid
    `TripleLeaf`, kept as the labelled cross-check, `leaf="grid"`)."""
    if leaf not in LEAVES:
        raise ValueError(f"leaf must be one of {LEAVES}, got {leaf!r}")
    if mode == PAIR:
        if leaf != "product":
            raise ValueError("tcost prices PhaseProduct with the digital-product leaf only")
        return PairLeaf(w, w, phi)
    return DigitalTripleLeaf(w, w, w, phi) if leaf == "product" else TripleLeaf(w, w, w, phi)


def width_symbol(level: int):
    """Return the width symbol. w_0 = n is the operand width. w_i is the operand width at recursion level i."""
    return sympy.Symbol("n" if level == 0 else f"w{level}", positive=True, integer=True)


def _is_concrete(w) -> bool:
    return isinstance(w, (int, np.integer))


def _limb_widths(table: Table, w):
    """Return (lower-limb width, top-limb width), concrete or symbolic."""
    if _is_concrete(w):
        ws = table.limb_widths(int(w))
        return ws[0], ws[-1]
    from .tables import sym_limb_widths
    return sym_limb_widths(table.k, w)


def _child_phis(table: Table, w, phi: Optional[Fraction]):
    if phi is None:
        return [None] * table.q
    return [phi * c for c in table.phase_coeffs(int(w) // table.k)]


def _level_call_graph(mode: str, table: Table, w, W2, children) -> BloqCountDictT:
    """Return the attested cost of one level: the table program on each side at width W2, the children, and the
    sign-extension CNOTs. This function is independent of `_level_decompose`. The Q01 cost seam compares the
    two."""
    sides = arity(mode)
    counts: Counter = Counter()
    for op in table.ops:
        kind = op[0]
        if kind == "shiftL":
            counts[ShiftL(W2, op[2])] += sides
        elif kind == "shiftR":
            counts[ShiftR(W2, op[2])] += sides
        elif kind == "negate":
            counts[NegateW(W2)] += sides
        elif kind == "addScaled":
            counts[AddNegSrcW(W2) if op[3] else Add(QInt(W2))] += sides
            if op[4]:
                counts[ShiftL(W2, op[4])] += sides
                counts[ShiftR(W2, op[4])] += sides
    for child in children:
        counts[child] += 1
    L, top = _limb_widths(table, w)
    for unc in (False, True):                                    # extend and un-extend each limb, on each side
        counts[Extend(top, W2, True, unc)] += sides
        counts[Extend(L, W2, False, unc)] += sides * (table.k - 1)        # k >= 2 (Table.check)
    return dict(counts)


def _level_decompose(bb: BloqBuilder, regs: Dict[str, Soquet], names, table: Table, w: int, W2: int,
                     children) -> Dict[str, Soquet]:
    """Build one level as gates: the limb split and extension, the table program on each side, the children and
    the teardown."""
    k = table.k
    widths = table.limb_widths(w)

    # 1. Split each operand into k limbs. Extend each limb to a QInt(W2) slot.
    slots: Dict[str, list] = {}
    for nm in names:
        bits = bb.split(regs[nm])                              # big-endian
        pos, sl = 0, []
        for i, wi in enumerate(widths):
            signed = i == k - 1                                # the top limb is signed
            limb = [bits[w - 1 - p] for p in range(pos, pos + wi)][::-1]   # big-endian limb bits
            pos += wi
            limb_reg = bb.join(np.array(limb), dtype=QInt(wi) if signed else QUInt(wi))
            sl.append(bb.add(Extend(wi, W2, signed), limb=limb_reg))
        slots[nm] = sl

    # 2. Apply the table program on each side. The phase products become children.
    l = 0
    for op in table.ops:
        kind = op[0]
        if kind == "phaseProduct":
            i = op[1]
            outs = bb.add(children[l], **{nm: slots[nm][i] for nm in names})
            outs = outs if isinstance(outs, tuple) else (outs,)
            for nm, sq in zip(names, outs):
                slots[nm][i] = sq
            l += 1
            continue
        for nm in names:
            sl = slots[nm]
            if kind == "shiftL":
                sl[op[1]] = bb.add(ShiftL(W2, op[2]), x=sl[op[1]])
            elif kind == "shiftR":
                sl[op[1]] = bb.add(ShiftR(W2, op[2]), x=sl[op[1]])
            elif kind == "negate":
                sl[op[1]] = bb.add(NegateW(W2), x=sl[op[1]])
            elif kind == "addScaled":
                _, d, src_i, neg, sh = op
                if d == src_i:
                    raise ValueError("addScaled with dst == src has no in-place adder realization")
                src = sl[src_i]
                if sh:
                    src = bb.add(ShiftL(W2, sh), x=src)
                src, sl[d] = bb.add(AddNegSrcW(W2) if neg else Add(QInt(W2)), a=src, b=sl[d])
                if sh:
                    src = bb.add(ShiftR(W2, sh), x=src)
                sl[src_i] = src

    # 3. Teardown: un-extend each limb (sign-dealloc the top limb), then join the operand again.
    out: Dict[str, Soquet] = {}
    for nm in names:
        bits = [None] * w
        pos = 0
        for i, wi in enumerate(widths):
            limb_reg = bb.add(Extend(wi, W2, i == k - 1, uncompute=True), slot=slots[nm][i])
            limb = list(bb.split(limb_reg))[::-1]                          # little-endian
            for p_ in range(wi):
                bits[w - 1 - (pos + p_)] = limb[p_]
            pos += wi
        out[nm] = bb.join(np.array(bits), dtype=QInt(w))
    return out


class _PhaseAction:
    """The declared action of all the recursive bloqs: identity on the basis, phase exp(i*pi*phi*prod)."""

    def on_classical_vals(self, **vals):
        return dict(vals)

    def basis_state_phase(self, **vals):
        if self.phi is None:
            return None
        return _phase(self.phi, *(vals[nm] for nm in OPERANDS[self.mode]))


# ---------------------------------------------------------------------------------------------
# ChainPhaseProduct: a forced sequence of tables, at a symbolic width (ranking) or a tiny one (validation)
# ---------------------------------------------------------------------------------------------

@attrs.frozen
class ChainPhaseProduct(_PhaseAction, Bloq):
    """exp(i*pi*phi * x*z) (pair) or exp(i*pi*phi * x*y*z) (triple) on signed w-bit operands. `chain[0]` lowers it
    at this level, `chain[1:]` lowers it below, and then the schoolbook leaf. There is no shrink condition. A Toom
    level is correct at all widths. The width must shrink only for termination. Thus tiny widths exercise the
    same construction that the ranking prices.

    * `w` symbolic (`width_symbol(level)`): the child width is the symbol of the next level. Thus the Qualtran
      cost is a polynomial in (n, w_1, ..., w_d), and `width_relations(chain)` closes it. This is the ranking
      object.
    * `w` concrete: the child width is `chain[0].next_width(w)`, and the bloq decomposes (the validation object).
    """
    mode: str
    chain: Tuple[Table, ...]
    w: object
    phi: Optional[Fraction] = None
    level: int = 0
    leaf: str = "product"

    @property
    def names(self) -> Tuple[str, ...]:
        return OPERANDS[self.mode]

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register(nm, QInt(self.w)) for nm in self.names])

    def _child_width(self):
        if _is_concrete(self.w):
            return self.chain[0].next_width(int(self.w))
        return width_symbol(self.level + 1)

    def _children(self):
        table, W2 = self.chain[0], self._child_width()
        return [ChainPhaseProduct(self.mode, self.chain[1:], W2, phi_l, self.level + 1, self.leaf)
                for phi_l in _child_phis(table, self.w, self.phi)]

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        if not self.chain:
            return {_leaf_for(self.mode, self.w, self.phi, self.leaf): 1}
        return _level_call_graph(self.mode, self.chain[0], self.w, self._child_width(), self._children())

    def build_composite_bloq(self, bb: BloqBuilder, **regs: Soquet) -> Dict[str, Soquet]:
        if not self.chain:
            outs = bb.add(_leaf_for(self.mode, self.w, self.phi, self.leaf), **regs)
            return dict(zip(self.names, outs))
        return _level_decompose(bb, regs, self.names, self.chain[0], int(self.w), self._child_width(),
                                self._children())

    def __str__(self):
        return f"ChainPhaseProduct({self.mode[5:]}, w={self.w}, k={[t.k for t in self.chain]})"


def SymbolicPhaseProduct(mode: str, chain: Tuple[Table, ...], level: int = 0, leaf: str = "product") -> ChainPhaseProduct:
    """Return the ranking object: generic angle, symbolic width w_level. `leaf="grid"`: the PTP grid-leaf cross-check."""
    return ChainPhaseProduct(mode, tuple(chain), width_symbol(level), None, level, leaf)


def width_relations(chain: Tuple[Table, ...]):
    """Return [(w_{i+1}, nextSignedWidth_i(w_i))] as sympy closed forms."""
    from .tables import sym_next_width
    return [(width_symbol(i + 1), sym_next_width(t, width_symbol(i))) for i, t in enumerate(chain)]


def width_values(chain: Tuple[Table, ...], n: int) -> Dict[object, int]:
    """Return {w_i: value} at operand width n (an exact integer evaluation of the relations)."""
    vals = {width_symbol(0): int(n)}
    w = int(n)
    for i, t in enumerate(chain):
        w = t.next_width(w)
        vals[width_symbol(i + 1)] = w
    return vals


# ---------------------------------------------------------------------------------------------
# PolicyPhaseProduct: the policy semantics (ForShor `choose` / `Stops`) at a concrete width
# ---------------------------------------------------------------------------------------------

@attrs.frozen
class PolicyPhaseProduct(_PhaseAction, Bloq):
    """At width w, the policy selects a table (ForShor `choose`). If the table shrinks the width (W' < w), this
    node is one Toom-Cook level with `pointCount` children at width W'. If not, this node is the schoolbook leaf.
    This bloq is equal to `ChainPhaseProduct(mode, chain_for(policy, w), w)` level for level. It is not the
    ranking object (the ranking object is the symbolic `ChainPhaseProduct`). It is the independent concrete
    oracle of the certification: T4 compares the symbolic ranking path with it. It also states the policy
    semantics directly."""
    policy: Policy
    w: int
    phi: Optional[Fraction] = None
    leaf: str = "product"

    @property
    def mode(self) -> str:
        return self.policy.mode

    @property
    def names(self) -> Tuple[str, ...]:
        return OPERANDS[self.mode]

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register(nm, QInt(self.w)) for nm in self.names])

    def _children(self, table: Table, W2: int):
        return [PolicyPhaseProduct(self.policy, W2, phi_l, self.leaf) for phi_l in _child_phis(table, self.w, self.phi)]

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        step = self.policy.step(self.w)
        if step is None:
            return {_leaf_for(self.mode, self.w, self.phi, self.leaf): 1}
        table, W2 = step
        return _level_call_graph(self.mode, table, self.w, W2, self._children(table, W2))

    def build_composite_bloq(self, bb: BloqBuilder, **regs: Soquet) -> Dict[str, Soquet]:
        step = self.policy.step(self.w)
        if step is None:
            outs = bb.add(_leaf_for(self.mode, self.w, self.phi, self.leaf), **regs)
            return dict(zip(self.names, outs))
        table, W2 = step
        return _level_decompose(bb, regs, self.names, table, self.w, W2, self._children(table, W2))

    def __str__(self):
        s = self.policy.step(self.w)
        what = f"k={s[0].k}->W'={s[1]}" if s else "leaf"
        return f"PolicyPhaseProduct({self.mode[5:]}, w={self.w}, {what})"


def make_chain_bloq(mode: str, chain: list, w: int, phi: Optional[str] = None) -> ChainPhaseProduct:
    """Make the bloq for `bloq_gate_spec.json`: a forced chain of table JSONs at a tiny width."""
    tabs = tuple(Table.from_json({**t, "mode": mode}) for t in chain)
    return ChainPhaseProduct(mode, tabs, int(w), None if phi is None else Fraction(phi))
