"""Qualtran bloqs for recursive phase products that a policy controls (pair and triple).

These bloqs are copies from Lean-To-Qualtran, made general (see the package docstring). They keep these
conventions exactly as in the priced bloqs of Lean-To-Qualtran (`LeanPhaseProduct`, `LeanRecursivePhaseProduct`):

  * Angles are in units of pi: the phase is exp(i*pi*phi*x*z) (pair) / exp(i*pi*phi*x*y*z) (triple). The
    operands are two's-complement signed (`QInt`). This is exactly the `SignedPhaseProd` semantics of ForShor.
  * The leaf is a grid of `CZPowGate` cells (Qualtran: And -> ZPowGate -> And^dagger) with signed bit weights
    (top bit -2^(W-1)). The exponents are folded into (-1, 1], and the zero cells are skipped.
  * Each table op acts on ALL operand sides (2 for pair, 3 for triple) at the uniform child width
    W' = nextSignedWidth: addScaled -> `Add(QInt(W'))`, negSrc is realized as Negate.Add.Negate,
    negate -> `Negate(QInt(W'))`.
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
    depth. `TripleLeaf` and the 3-sided level give the PhaseTripleProduct construction.

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
from qualtran.bloqs.arithmetic import Add, Negate
from qualtran.bloqs.basic_gates import CNOT, CZPowGate
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
        if self.phi is None:
            return {PhaseCell(THETA): self.wx * self.wz}
        return dict(Counter(PhaseCell(e) for e in _pair_exponents(self.wx, self.wz, self.phi).values()))

    def on_classical_vals(self, x, z):
        return {"x": x, "z": z}

    def basis_state_phase(self, x, z) -> complex:
        return _phase(self.phi, x, z) if self.phi is not None else None

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

    def __str__(self):
        return f"TripleLeaf({self.wx}x{self.wy}x{self.wz})"


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
    """Two's-complement negation of a QInt(W): the Qualtran `Negate`, with an exact statement of its call graph.

    In Qualtran, `Negate` = BitwiseNot + AddK(1), and AddK(1) = XorK(1) . Add . XorK(1). At concrete W,
    XorK(1) is one X gate. But the SYMBOLIC XorK of Qualtran charges W, so a symbolic `Negate` reports 2W - 2
    extra Cliffords (10W - 10 instead of 8W - 8). This wrapper attests the concrete structure for each W, so the
    symbolic and concrete costs agree. It decomposes to the Qualtran `Negate` (the cost seam checks it).
    """
    W: object

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.W))])

    def build_composite_bloq(self, bb: BloqBuilder, x: Soquet) -> Dict[str, Soquet]:
        return {"x": bb.add(Negate(QInt(self.W)), x=x)}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.arithmetic import BitwiseNot
        from qualtran.bloqs.basic_gates import XGate
        return {BitwiseNot(QInt(self.W)): 1, XGate(): 2, Add(QInt(self.W)): 1}

    def on_classical_vals(self, x):
        # Two's-complement negation mod 2^W, written out. (The `Negate` of qualtran 0.7.0 has no classical action.)
        W = int(self.W)
        v = (-int(x)) % (1 << W)
        return {"x": v - (1 << W) if v >= 1 << (W - 1) else v}


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

def _leaf_for(mode: str, w, phi: Optional[Fraction]) -> Bloq:
    return PairLeaf(w, w, phi) if mode == PAIR else TripleLeaf(w, w, w, phi)


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
            counts[Add(QInt(W2))] += sides
            if op[3]:
                counts[NegateW(W2)] += 2 * sides
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
                if neg:
                    src = bb.add(NegateW(W2), x=src)
                if sh:
                    src = bb.add(ShiftL(W2, sh), x=src)
                src, sl[d] = bb.add(Add(QInt(W2)), a=src, b=sl[d])
                if sh:
                    src = bb.add(ShiftR(W2, sh), x=src)
                if neg:
                    src = bb.add(NegateW(W2), x=src)
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
        return [ChainPhaseProduct(self.mode, self.chain[1:], W2, phi_l, self.level + 1)
                for phi_l in _child_phis(table, self.w, self.phi)]

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        if not self.chain:
            return {_leaf_for(self.mode, self.w, self.phi): 1}
        return _level_call_graph(self.mode, self.chain[0], self.w, self._child_width(), self._children())

    def build_composite_bloq(self, bb: BloqBuilder, **regs: Soquet) -> Dict[str, Soquet]:
        if not self.chain:
            outs = bb.add(_leaf_for(self.mode, self.w, self.phi), **regs)
            return dict(zip(self.names, outs))
        return _level_decompose(bb, regs, self.names, self.chain[0], int(self.w), self._child_width(),
                                self._children())

    def __str__(self):
        return f"ChainPhaseProduct({self.mode[5:]}, w={self.w}, k={[t.k for t in self.chain]})"


def SymbolicPhaseProduct(mode: str, chain: Tuple[Table, ...], level: int = 0) -> ChainPhaseProduct:
    """Return the ranking object: generic angle, symbolic width w_level."""
    return ChainPhaseProduct(mode, tuple(chain), width_symbol(level), None, level)


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
        return [PolicyPhaseProduct(self.policy, W2, phi_l) for phi_l in _child_phis(table, self.w, self.phi)]

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        step = self.policy.step(self.w)
        if step is None:
            return {_leaf_for(self.mode, self.w, self.phi): 1}
        table, W2 = step
        return _level_call_graph(self.mode, table, self.w, W2, self._children(table, W2))

    def build_composite_bloq(self, bb: BloqBuilder, **regs: Soquet) -> Dict[str, Soquet]:
        step = self.policy.step(self.w)
        if step is None:
            outs = bb.add(_leaf_for(self.mode, self.w, self.phi), **regs)
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
