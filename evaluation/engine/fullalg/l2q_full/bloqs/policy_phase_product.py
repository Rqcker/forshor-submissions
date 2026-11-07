r"""`PolicyPhaseProduct(policy, wx, wz)` — the signed phase product under a width-indexed policy.

The varying-`k` counterpart of :class:`LeanRecursivePhaseProduct`. At each node the policy picks
the table by operand width, exactly as ForShor's `Policy.signedPlanOf` does
(`PhaseProduct/Lowering/PlanBuilders.lean`):

    PPP(wx, wz) = { A_T adder-class ops at W', q(k_T) × PPP(W', W') }   if T = choose(max wx wz)
                                                                         and W' = nextWidth_T(wx, wz)
                                                                         < max wx wz
                = { one wx × wz leaf }                                    otherwise (signedBase)

so one call graph can contain Toom-3 nodes over Karatsuba nodes over leaves. How the arithmetic
and the leaves are realised is the bloq's ``convention`` (`forshor_qualtran.cost_conventions`):
the default QUALTRAN tier (Qualtran `Add`/`Subtract`/`Negate`, leaf = the cheaper of a `CZPow`
grid and a computed product) is the headline; LEAN_FAITHFUL (Cuccaro adders, ForShor's merged
`CPhase` leaf) is the cross-check; LEGACY reproduces `LeanRecursivePhaseProduct`'s pricing. The
planner prices with the very same callees, so planner == bloq under every spec.

**Three views of one construction** (roundtable 2026-10-12, C-05 / C-12):

* *cost* — :meth:`build_call_graph` (the convention's callees; children carry no angle, so the
  cost recursion shares one child per level);
* *structure* — :meth:`build_composite_bloq` at concrete widths and a concrete ``phi``: the operands
  are split into ForShor's limbs (lower limbs unsigned, top limb signed), every limb is grown to
  the child width (zero- / sign-extension into freshly allocated qubits — ForShor's reserve), the
  table's program runs on both operands (`addScaled` as a Qualtran `Add` on a zero-padded shifted
  view of the source, `Subtract` realised as `X·Add·X` exactly as Qualtran's own `Subtract`
  decomposes, `negate` as `Negate`), each `phaseProduct` checkpoint `l` calls a child at angle
  `phi·c_l(2^W)` (ForShor's interpolation weight at limb width `W`), and the growth is undone. The
  flattened decomposition's `QECGatesCost` equals the call graph's on Toffoli/And/measurement/
  rotation, and its classical action is the identity on `x`, `z` (the program returns to its
  start state, ForShor's C4). Leaves are diagonal: their classical action is the identity;
* *meaning* — `lean_resources()` / `lean_qubit_count()` predict ForShor's `LowGate.resources` and
  `LowGate.qubitCount` of the literal Lean lowering (independent of the convention).

**Qubits** (C-05). :meth:`qubits` is one model tied to the convention:
LEAN_FAITHFUL (`adder="cuccaro"`) → exactly ForShor's `qubitCount` (`|usedQubits| + cleanAnc`,
checked on all 160 fuzz cases); Qualtran tiers → the same registers and activated workspace plus
the peak ancilla of the Qualtran realisation (`Add`/`Subtract` `w−1`, `Negate`, the And of a
`CZPow` cell, the product register of the product leaf). `my_static_costs(QubitCount)` returns
it, so Qualtran's own qubit counter agrees. `reserve_provisioned()` is the old
`2n + Policy.reserveNeed + 1` (what `Policy.WorkspaceOK` provisions — an allocation, neither an
upper nor a lower bound on the qubits used); `qubit_footprint` is a deprecated alias for it.

The children are symmetric `(W', W')` because `targetSignedLayoutState` pads every slot to the
one common needed width; only a top-level node can be rectangular.

Widths must be concrete integers: a policy's table choice depends on the actual width (band
thresholds), so there is no single symbolic law. Price symbolic-looking sweeps by evaluating at
concrete `n` (or with `policy_planner`).

LEAN STATUS: `Shor.Policy.lowerSignedPhaseProduct_correct` (PhaseProduct/Main.lean) proves the
lowering of `Policy.signedPlan P` correct for any admissible `P`. The IR this bloq is tied to is
*composed* in Python from ForShor's per-table extracted documents (`forshor_qualtran.policy`),
because ForShor's extractor emits single-table documents only.
"""
from __future__ import annotations

import warnings
from fractions import Fraction
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import attrs
import numpy as np
import sympy
from qualtran import Bloq, BloqBuilder, DecomposeTypeError, QAny, QBit, QInt, QUInt, Register, Signature
from qualtran.resource_counting import BloqCountDictT, QubitCount, SympySymbolAllocator

from .. import cost_conventions as cc
from ..mirrors import width as _wm
from ..policy import PhasePolicy, ToomCookTable, q

#: The generic angle the cost views use (owner decision: mix = 1, every leaf rotation priced as a
#: generic synthesised rotation whatever the concrete angle).
_GENERIC = sympy.Symbol("e")


# ============================================================================ field normalisation
def _norm_policy(p: PhasePolicy) -> PhasePolicy:
    """Hashable policy: bands as a tuple (a list-banded `PhasePolicy` is equal but unhashable)."""
    if isinstance(p, PhasePolicy) and not isinstance(p.bands, tuple):
        return PhasePolicy(tuple(p.bands), name=p.name)
    return p


def _norm_phi(phi) -> Optional[Fraction]:
    if phi is None:
        return None
    if isinstance(phi, sympy.Basic):
        raise TypeError("phi must be a concrete rational (units of pi), not a symbol")
    return Fraction(phi)


def _concrete_width(w, what: str) -> int:
    if isinstance(w, (bool,)) or isinstance(w, sympy.Basic) and not w.is_Integer:
        raise TypeError(
            f"{what} must be a concrete integer, got {w!r}. A policy chooses its table by the actual "
            f"operand width (band thresholds), so a policy phase product has no single symbolic law: "
            f"evaluate at concrete n, or sweep with forshor_qualtran.policy_planner.")
    try:
        v = int(w)
    except TypeError:
        raise TypeError(f"{what} must be a concrete integer, got {w!r}") from None
    if v != w or v < 0:
        raise ValueError(f"{what} must be a non-negative integer, got {w!r}")
    return v


def signed_bit_weight(width: int, i: int) -> int:
    """`signedBitWeight` (NaiveLeaf.lean): the top bit of a two's-complement register is negative."""
    return -(1 << i) if i + 1 == width else (1 << i)


def _wrap(v: int, w: int) -> int:
    """`v` reduced to a `w`-bit two's-complement value."""
    m = 1 << w
    v %= m
    return v - m if v >= m >> 1 else v


# ============================================================================ leaf bloqs (structure)
@attrs.frozen
class PhaseCell(Bloq):
    """One leaf cell: `exp(i·pi·exponent)` when both qubits are 1 — a `CZPowGate`. Priced as a
    generic-angle `CZPowGate` (the cost views' convention); classically the identity (diagonal).

    ``tape`` instrumentation: when :data:`PHASE_TAPE` is a list, classical simulation appends the
    cell's exponent whenever both bits are 1, so a test can recover the accumulated phase of a
    flattened decomposition (`call_classically` itself has no phase)."""
    exponent: Fraction

    @property
    def signature(self) -> Signature:
        return Signature([Register("q", QBit(), shape=(2,))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import CZPowGate
        return {CZPowGate(exponent=_GENERIC): 1}

    def on_classical_vals(self, q):
        if PHASE_TAPE is not None and int(q[0]) == 1 and int(q[1]) == 1:
            PHASE_TAPE.append(self.exponent)
        return {"q": q}

    def __str__(self) -> str:
        return f"PhaseCell({self.exponent})"


#: Set to a list to record the exponents of cells that fire during classical simulation.
PHASE_TAPE: Optional[list] = None


@attrs.frozen
class GridPhaseLeaf(Bloq):
    """`exp(i·pi·phi·x·z)` on signed `x`, `z` as one `PhaseCell` per operand-bit pair with weight
    `phi·sbw(wx,i)·sbw(wz,j)` — ForShor's `Naive_SignedPhaseProd` grid, realised as `CZPow`s."""
    wx: int
    wz: int
    phi: Fraction

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.wx)), Register("z", QInt(self.wz))])

    def build_composite_bloq(self, bb: BloqBuilder, x, z):
        xb, zb = list(bb.split(x))[::-1], list(bb.split(z))[::-1]        # LSB first
        for i in range(self.wx):
            for j in range(self.wz):
                e = self.phi * signed_bit_weight(self.wx, i) * signed_bit_weight(self.wz, j)
                xb[i], zb[j] = bb.add(PhaseCell(e), q=np.array([xb[i], zb[j]]))
        return {"x": bb.join(np.array(xb[::-1]), dtype=QInt(self.wx)),
                "z": bb.join(np.array(zb[::-1]), dtype=QInt(self.wz))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import CZPowGate
        return {CZPowGate(exponent=_GENERIC): self.wx * self.wz}

    def on_classical_vals(self, x, z):
        return {"x": x, "z": z}


@attrs.frozen
class CGridPhaseLeaf(Bloq):
    """The controlled grid leaf: per row `i`, `t = ctrl ∧ x_i` (an And, kept for the row), then a
    `PhaseCell` on `(t, z_j)` for every `j`, then the And uncomputed — the fold `tcount/fair.py`
    gives a controlled leaf (`wx` Ands instead of one per cell)."""
    wx: int
    wz: int
    phi: Fraction

    @property
    def signature(self) -> Signature:
        return Signature([Register("ctrl", QBit()), Register("x", QInt(self.wx)),
                          Register("z", QInt(self.wz))])

    def build_composite_bloq(self, bb: BloqBuilder, ctrl, x, z):
        from qualtran.bloqs.mcmt import And
        xb, zb = list(bb.split(x))[::-1], list(bb.split(z))[::-1]
        for i in range(self.wx):
            (ctrl, xb[i]), t = bb.add(And(), ctrl=np.array([ctrl, xb[i]]))
            for j in range(self.wz):
                e = self.phi * signed_bit_weight(self.wx, i) * signed_bit_weight(self.wz, j)
                t, zb[j] = bb.add(PhaseCell(e), q=np.array([t, zb[j]]))
            ctrl, xb[i] = bb.add(And(uncompute=True), ctrl=np.array([ctrl, xb[i]]), target=t)
        return {"ctrl": ctrl, "x": bb.join(np.array(xb[::-1]), dtype=QInt(self.wx)),
                "z": bb.join(np.array(zb[::-1]), dtype=QInt(self.wz))}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import CZPowGate
        from qualtran.bloqs.mcmt import And
        return {And(): self.wx, And(uncompute=True): self.wx, CZPowGate(exponent=_GENERIC): self.wx * self.wz}

    def on_classical_vals(self, ctrl, x, z):
        return {"ctrl": ctrl, "x": x, "z": z}


@attrs.frozen
class DiagonalLeaf(Bloq):
    """A cost-declared diagonal leaf (`MergedCPhaseLeaf`, `ProductPhaseLeaf` and their controlled
    forms): priced as ``inner``, classically the identity, not decomposed further (Q01: its
    costs are carried as declared, `[cited]`). ``phi`` records the angle it realises."""
    inner: Bloq
    phi: Optional[Fraction] = None

    @property
    def signature(self) -> Signature:
        return self.inner.signature

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {self.inner: 1}

    def on_classical_vals(self, **vals):
        # tape: the declared action `exp(i·pi·phi·x·z)` (times `ctrl` when controlled)
        if PHASE_TAPE is not None and self.phi is not None:
            on = int(vals.get("ctrl", 1))
            PHASE_TAPE.append(self.phi * int(vals["x"]) * int(vals["z"]) * on)
        return dict(vals)

    def __str__(self) -> str:
        return f"Diag({self.inner})"


@attrs.frozen
class CProductPhaseLeaf(Bloq):
    """Controlled product leaf: the product arithmetic of `cost_conventions.ProductPhaseLeaf` runs
    uncontrolled (it is uncomputed); only the `wx + wz` product-bit rotations are controlled
    (`CZPow(ctrl, p_l)`)."""
    wx: int
    wz: int

    @property
    def signature(self) -> Signature:
        return Signature([Register("ctrl", QBit()), Register("x", QInt(self.wx)),
                          Register("z", QInt(self.wz))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.arithmetic import CAdd, Product
        from qualtran.bloqs.basic_gates import CZPowGate
        prod = Product(self.wx, self.wz)
        out: Dict[Bloq, int] = {prod: 1, prod.adjoint(): 1, CZPowGate(exponent=_GENERIC): self.wx + self.wz}
        for w in (self.wx, self.wz):
            c = CAdd(QUInt(w), QUInt(w))
            out[c] = out.get(c, 0) + 1
            out[c.adjoint()] = out.get(c.adjoint(), 0) + 1
        return out


@attrs.frozen
class CLeanCCPhaseLeaf(Bloq):
    """ForShor's controlled leaf, `wx·wz` `LowGate.CCPhase`s, read literally as V-2 reads a
    circuit: each cell is a `{Phase, CNOT}` run with 4 phase terms (ctrl, x_i, ctrl⊕x_i, z_j),
    a Toffoli, one more phase term (z_j), a Toffoli — 5 rotations, 2 Toffolis, 2 CNOTs per cell
    (the Toffolis close every run, so nothing merges across cells)."""
    wx: int
    wz: int

    @property
    def signature(self) -> Signature:
        return Signature([Register("ctrl", QBit()), Register("x", QInt(self.wx)),
                          Register("z", QInt(self.wz))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import CNOT, Toffoli, ZPowGate
        cells = self.wx * self.wz
        return {ZPowGate(exponent=_GENERIC): 5 * cells, Toffoli(): 2 * cells, CNOT(): 2 * cells}


def controlled_leaf_callees(wx: int, wz: int, leaf: str) -> Dict[Bloq, int]:
    """The callees of one controlled leaf under a concrete realisation."""
    if leaf == "grid":
        from qualtran.bloqs.basic_gates import CZPowGate
        from qualtran.bloqs.mcmt import And
        return {And(): wx, And(uncompute=True): wx, CZPowGate(exponent=_GENERIC): wx * wz}
    if leaf == "product":
        return {CProductPhaseLeaf(wx, wz): 1}
    if leaf == "merged_cphase":
        return {CLeanCCPhaseLeaf(wx, wz): 1}
    raise ValueError(f"leaf must be concrete, got {leaf!r}")


# ============================================================================ arithmetic bloqs (structure)
@attrs.frozen
class CuccaroAddScaled(Bloq):
    """`dst ← dst ± src·2^shift` (mod `2^w`) priced as ForShor's Cuccaro adder(s) — the
    LEAN_FAITHFUL realisation of one `addScaled`. Classical action declared; not decomposed."""
    w: int
    shift: int = 0
    neg: bool = False
    legacy_negsrc: bool = False

    @property
    def signature(self) -> Signature:
        return Signature([Register("src", QInt(self.w)), Register("dst", QInt(self.w))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        out: Dict[Bloq, int] = {cc.CuccaroAdder(self.w): 1}
        if self.neg and self.legacy_negsrc:
            out[cc.CuccaroAdder(self.w, negate=True)] = 2
        return out

    def on_classical_vals(self, src, dst):
        s = -1 if self.neg else 1
        return {"src": src, "dst": _wrap(int(dst) + s * (int(src) << self.shift), self.w)}


@attrs.frozen
class CuccaroNegate(Bloq):
    """`r ← −r` priced as ForShor's `negateResources`. Classical action declared."""
    w: int

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.w))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {cc.CuccaroAdder(self.w, negate=True): 1}

    def on_classical_vals(self, x):
        return {"x": _wrap(-int(x), self.w)}


# ============================================================================ shared tree analytics
def _split_widths(w: int, limb: int, k: int) -> List[int]:
    return [_wm.phase_split_logical_width(w, limb, k, i) for i in range(k)]


@lru_cache(maxsize=1 << 14)
def _used_extra(policy: PhasePolicy, wx: int, wz: int) -> int:
    """Reserve qubits ForShor's lowering activates below a node (both operands): every limb grown
    to the child width, plus — once per slot that hosts a phase-product checkpoint — the child's
    own activated reserve (a slot's children run on the same physical registers)."""
    s = policy.step(wx, wz)
    if s is None:
        return 0
    T, w2 = s
    limb = min(wx // T.k, wz // T.k)
    grow = sum(max(0, w2 - a) for a in _split_widths(wx, limb, T.k)) + \
        sum(max(0, w2 - a) for a in _split_widths(wz, limb, T.k))
    slots = {o["i"] for o in T.ops if o["op"] == "phaseProduct"}
    return grow + len(slots) * _used_extra(policy, w2, w2)


@lru_cache(maxsize=1 << 14)
def _lean_clean_anc(policy: PhasePolicy, wx: int, wz: int) -> int:
    """ForShor's `cleanAnc` (a peak, `max` over `seq`): 1 for any Cuccaro add and, since modular
    swap 001, 1 for a negate (`negateResources`: one clean ancilla; it was `w + 1`)."""
    s = policy.step(wx, wz)
    if s is None:
        return 0
    T, w2 = s
    c = T.census()
    here = max(1 if c["addScaled"] else 0, cc.negate_clean_ancilla(w2) if c["negate"] else 0)
    return max(here, _lean_clean_anc(policy, w2, w2))


@lru_cache(maxsize=None)
def _qc(bloq: Bloq) -> int:
    """Qualtran's `QubitCount` of a unit bloq. Small fixed-size bloqs only: for the width-scaling
    arithmetic bloqs use :func:`qubit_count_closed_form`, because Qualtran's counter recurses
    through their decompositions and exceeds Python's recursion limit at widths ~1000."""
    from qualtran.resource_counting import get_cost_value
    return int(get_cost_value(bloq, QubitCount()))


def qubit_count_closed_form(kind: str, a: int, b: Optional[int] = None) -> int:
    """Qualtran `QubitCount` of the width-scaling bloqs this module prices, in closed form.

    Exact for the frozen Qualtran tree (pinned against `QubitCount` in
    `tests/test_policy_qubits.py::test_qubit_count_closed_forms_match_qualtran`):

        Add / Subtract / Negate (QInt(w))   3w - 1        (w >= 2)
        CAdd(QUInt(w), QUInt(w))            4w
        Product(a, b)                       a + b + 2 max(a, b)
    """
    if kind in ("Add", "Subtract", "Negate"):
        if a < 2:
            raise ValueError(f"{kind}: closed form holds for w >= 2, got {a}")
        return 3 * a - 1
    if kind == "CAdd":
        return 4 * a
    if kind == "Product":
        return a + b + 2 * max(a, b)
    raise ValueError(kind)


def _qualtran_level_ancilla(T: ToomCookTable, w2: int, spec: cc.ConventionSpec) -> int:
    """Peak extra qubits while one node's program runs (Qualtran realisation): the adder's
    ancillas plus the zero padding of a shifted source view."""
    if spec.adder == "cuccaro":
        c = T.census()
        return max(1 if c["addScaled"] else 0,
                   cc.negate_clean_ancilla(w2, spec.negate) if c["negate"] else 0)
    add_x = qubit_count_closed_form("Add", w2) - 2 * w2
    sub_x = qubit_count_closed_form("Subtract", w2) - 2 * w2
    neg_x = 1 if spec.negate == "incrementer" else qubit_count_closed_form("Negate", w2) - w2
    peak = 0
    for o in T.ops:
        if o["op"] == "addScaled":
            sh = int(o.get("shift", 0))
            if o.get("negSrc"):
                adder = sub_x if spec.negsrc == "subtract" else max(add_x, neg_x)
            else:
                adder = add_x
            peak = max(peak, sh + adder)
        elif o["op"] == "negate":
            peak = max(peak, neg_x)
    return peak


def _qualtran_leaf_ancilla(wx: int, wz: int, leaf: str, controlled: bool) -> int:
    from qualtran.bloqs.arithmetic import Product
    from qualtran.bloqs.basic_gates import CZPowGate
    cz = _qc(CZPowGate(exponent=_GENERIC)) - 2
    if leaf == "grid":
        return cz + (1 if controlled else 0)            # the row And of the controlled fold
    if leaf == "merged_cphase":
        return 0
    if leaf == "product":
        prod = Product(wx, wz)
        r = sum(reg.total_bits() for reg in prod.signature if reg.name == "result")
        during_prod = qubit_count_closed_form("Product", wx, wz) - wx - wz
        cadd = max(qubit_count_closed_form("CAdd", w) - 2 * w for w in (wx, wz))
        rot = cz if controlled else 0
        return max(during_prod, r + cadd, r + rot)
    raise ValueError(leaf)


def _qualtran_ancilla_peak(policy: PhasePolicy, wx: int, wz: int, spec: cc.ConventionSpec,
                           controlled: bool) -> int:
    peak, w_x, w_z = 0, wx, wz
    while True:
        s = policy.step(w_x, w_z)
        if s is None:
            return max(peak, _qualtran_leaf_ancilla(w_x, w_z, spec.leaf, controlled))
        T, w2 = s
        peak = max(peak, _qualtran_level_ancilla(T, w2, spec))
        w_x = w_z = w2


def _lean_resources(policy: PhasePolicy, wx: int, wz: int, controlled: bool) -> Dict[str, int]:
    res = {"rz": 0, "cnot": 0, "toffoli": 0, "x": 0, "h": 0}
    nodes = 1
    while True:
        s = policy.step(wx, wz)
        if s is None:
            cells = nodes * wx * wz
            if controlled:                    # LowGate.CCPhase per cell: 5 rz, 2 Toffoli, 2 CNOT
                res["rz"] += 5 * cells
                res["toffoli"] += 2 * cells
                res["cnot"] += 2 * cells
            else:                             # LowGate.CPhase per cell: 3 rz, 2 CNOT
                res["rz"] += 3 * cells
                res["cnot"] += 2 * cells
            return res
        T, w2 = s
        cen = T.census()
        # `addScaledResources` = `cuccaroModAddResources (width dst)`; `negateResources` (swap 001)
        # = `w` X plus `32 w` Toffoli (one-clean-ancilla incrementer). Every chunk has already
        # been grown to `w2` when the program runs.
        adds = nodes * 2 * cen["addScaled"]
        negs = nodes * 2 * cen["negate"]
        add_r, neg_r = cc.cuccaro_resources(w2), cc.negate_resources(w2)   # ℕ-truncated, as Lean
        for ch in ("toffoli", "cnot", "x"):
            res[ch] += adds * add_r[ch] + negs * neg_r[ch]
        for side_w in (wx, wz):                 # sign-extend + dealloc of each top limb
            limb = min(wx // T.k, wz // T.k)
            top = side_w - (T.k - 1) * limb
            res["cnot"] += 2 * nodes * max(0, w2 - top)
        nodes *= q(T.k)
        wx = wz = w2


# ============================================================================ the bloqs
class _PolicyPPBase(Bloq):
    """Shared machinery of the uncontrolled and controlled policy phase products."""

    _is_controlled: bool = False

    def __attrs_post_init__(self):
        object.__setattr__(self, "wx", _concrete_width(self.wx, "wx"))
        wz = self.wx if self.wz is None else _concrete_width(self.wz, "wz")
        object.__setattr__(self, "wz", wz)

    # ---- shape
    @property
    def wz_(self) -> int:
        return self.wz

    @property
    def w(self) -> int:
        return max(self.wx, self.wz)

    def step(self) -> Optional[Tuple[ToomCookTable, int]]:
        """`(table, child width)` if this node recurses, else `None` (the leaf)."""
        return self.policy.step(self.wx, self.wz)

    def ladder(self) -> List[Tuple[int, Optional[str]]]:
        """`[(width, band table name), …, (leaf width, None)]`."""
        return self.policy.ladder(self.wx, self.wz)

    # ---- Lean-faithful meaning (independent of the convention)
    def lean_resources(self) -> Dict[str, int]:
        """`LowGate.resources` of the whole lowered recursion (`h, x, cnot, toffoli, rz`), predicted
        from the ladder with `k`, `q` and the census of whichever table the policy chose at each
        level. `cleanAnc` is :meth:`lean_clean_anc` (a peak, not a sum)."""
        return _lean_resources(self.policy, self.wx, self.wz, self._is_controlled)

    def lean_clean_anc(self) -> int:
        """ForShor's `cleanAnc` channel of the lowered circuit."""
        return _lean_clean_anc(self.policy, self.wx, self.wz)

    def lean_used_qubits(self) -> int:
        """`|LowGate.usedQubits|`: operands, control, and every reserve qubit the lowering
        activates (limb growth at each node; each phase-product slot's child once)."""
        return self.wx + self.wz + _used_extra(self.policy, self.wx, self.wz) + (1 if self._is_controlled else 0)

    def lean_qubit_count(self) -> int:
        """`LowGate.qubitCount shorGateResourceModel` = `|usedQubits| + cleanAnc`."""
        return self.lean_used_qubits() + self.lean_clean_anc()

    # ---- the qubit model tied to the convention
    def qubits(self) -> int:
        """Qubits of this construction under its convention: LEAN_FAITHFUL (Cuccaro) → exactly
        ForShor's `qubitCount`; Qualtran adders → the same registers and activated workspace plus
        the Qualtran realisation's peak ancilla."""
        spec = self.resolved_convention()
        if spec.adder == "cuccaro" and spec.leaf == "merged_cphase":
            return self.lean_qubit_count()
        return self.lean_used_qubits() + _qualtran_ancilla_peak(
            self.policy, self.wx, self.wz, spec, self._is_controlled)

    def my_static_costs(self, cost_key):
        if isinstance(cost_key, QubitCount):
            return self.qubits()
        return NotImplemented

    def reserve_qubits(self) -> Tuple[int, int]:
        """`Policy.reserveNeed P wx wz` — what `Policy.WorkspaceOK` requires."""
        return self.policy.reserve_need(self.wx, self.wz)

    def reserve_provisioned(self) -> int:
        """`wx + wz + Policy.reserveNeed + 1`: what `Policy.WorkspaceOK` provisions. An
        allocation, not the qubits used (it over-counts by up to ~8× and omits `cleanAnc`)."""
        rx, rz = self.reserve_qubits()
        return self.wx + self.wz + rx + rz + 1

    @property
    def qubit_footprint(self) -> int:
        """DEPRECATED alias of :meth:`reserve_provisioned` (roundtable 2026-10-12, C-05): the
        provisioned reserve, not a qubit count. Use :meth:`qubits` (convention-tied) or
        :meth:`lean_qubit_count` (ForShor's)."""
        warnings.warn("qubit_footprint is the provisioned reserve, not a qubit count; use "
                      "qubits() or lean_qubit_count()", DeprecationWarning, stacklevel=2)
        return self.reserve_provisioned()

    # ---- conventions
    def resolved_convention(self) -> cc.ConventionSpec:
        """The spec with a concrete leaf realisation for this tree (all leaves of a policy tree
        have the same width, so one choice per tree, made on the tree's honest T)."""
        spec = self.convention
        if spec.leaf != "best":
            return spec
        if not self._is_controlled:
            return spec.with_leaf(cc.resolve_leaf(self.policy, self.wx, self.wz, spec))
        return spec.with_leaf(min(
            spec.leaves, key=lambda r: (spec.price(_ctree_cost(self.policy, self.wx, self.wz, spec, r)),
                                        cc.LEAVES.index(r))))

    # ---- structure (decomposition)
    def _chunks(self, bb: BloqBuilder, reg, w: int, limb: int, k: int, w2: int):
        """Split `reg` (QInt(w)) into ForShor's limbs, each grown to QInt(w2): lower limbs
        zero-extended (unsigned), the top limb sign-extended (CNOT from its sign bit)."""
        from qualtran.bloqs.basic_gates import CNOT
        bits = list(bb.split(reg))[::-1]
        widths = _split_widths(w, limb, k)
        out, lo = [], 0
        for i, wi in enumerate(widths):
            chunk = bits[lo:lo + wi]
            lo += wi
            ext = w2 - wi
            if ext < 0:
                raise DecomposeTypeError(f"limb width {wi} exceeds child width {w2}")
            eb = list(bb.split(bb.allocate(ext))) if ext else []
            if i + 1 == k and wi > 0:
                for j in range(ext):
                    chunk[-1], eb[j] = bb.add(CNOT(), ctrl=chunk[-1], target=eb[j])
            out.append(bb.join(np.array((chunk + eb)[::-1]), dtype=QInt(w2)))
        return out, widths

    def _unchunk(self, bb: BloqBuilder, regs, widths: List[int], w: int, w2: int):
        from qualtran.bloqs.basic_gates import CNOT
        k = len(widths)
        bits_all: list = []
        for i, (r, wi) in enumerate(zip(regs, widths)):
            b = list(bb.split(r))[::-1]
            chunk, eb = b[:wi], b[wi:]
            if i + 1 == k and wi > 0:
                for j in range(len(eb)):
                    chunk[-1], eb[j] = bb.add(CNOT(), ctrl=chunk[-1], target=eb[j])
            if eb:
                bb.free(bb.join(np.array(eb[::-1]), dtype=QAny(len(eb))))
            bits_all.extend(chunk)
        return bb.join(np.array(bits_all[::-1]), dtype=QInt(w))

    def _add_scaled(self, bb: BloqBuilder, src, dst, w2: int, shift: int, neg: bool, spec):
        """`dst ← dst ± src·2^shift` on QInt(w2) registers, realised under `spec`."""
        if spec.adder == "cuccaro":
            return bb.add(CuccaroAddScaled(w2, shift, neg, spec.negsrc == "negate_add_negate"),
                          src=src, dst=dst)
        from qualtran.bloqs.arithmetic import Add, BitwiseNot
        if shift:
            sb = list(bb.split(src))[::-1]
            zb = list(bb.split(bb.allocate(shift)))
            view = bb.join(np.array((zb + sb[:w2 - shift])[::-1]), dtype=QInt(w2))
            top = sb[w2 - shift:]
        else:
            view = src
        if neg and spec.negsrc == "subtract":         # Qualtran's Subtract: X · Add · X on dst
            dst = bb.add(BitwiseNot(QInt(w2)), x=dst)
            view, dst = bb.add(Add(QInt(w2)), a=view, b=dst)
            dst = bb.add(BitwiseNot(QInt(w2)), x=dst)
        elif neg:                                     # legacy Negate · Add · Negate on the source
            view = bb.add(cc.negate_bloq(w2, spec), x=view)
            view, dst = bb.add(Add(QInt(w2)), a=view, b=dst)
            view = bb.add(cc.negate_bloq(w2, spec), x=view)
        else:
            view, dst = bb.add(Add(QInt(w2)), a=view, b=dst)
        if shift:
            vb = list(bb.split(view))[::-1]
            bb.free(bb.join(np.array(vb[:shift][::-1]), dtype=QAny(shift)))
            src = bb.join(np.array((vb[shift:] + top)[::-1]), dtype=QInt(w2))
        else:
            src = view
        return src, dst

    def _negate(self, bb: BloqBuilder, r, w2: int, spec):
        if spec.negate == "incrementer":
            return bb.add(cc.IncrementerNegate(w2), x=r)
        if spec.adder == "cuccaro":
            return bb.add(CuccaroNegate(w2), x=r)
        from qualtran.bloqs.arithmetic import Negate
        return bb.add(Negate(QInt(w2)), x=r)

    def _structure(self, bb: BloqBuilder, x, z, ctrl=None):
        if self.phi is None:
            raise DecomposeTypeError(
                f"{self} has no phi: the cost views need no angle, but a decomposition does "
                f"(pass phi=Fraction(...), in units of pi)")
        spec = self.resolved_convention()
        s = self.step()
        if s is None:
            return self._leaf(bb, x, z, ctrl, spec)
        T, w2 = s
        limb = min(self.wx // T.k, self.wz // T.k)
        xs, wxs = self._chunks(bb, x, self.wx, limb, T.k, w2)
        zs, wzs = self._chunks(bb, z, self.wz, limb, T.k, w2)
        l = 0
        for o in T.ops:
            op = o["op"]
            if op == "addScaled":
                d, sr, sh, ng = int(o["dst"]), int(o["src"]), int(o.get("shift", 0)), bool(o.get("negSrc"))
                xs[sr], xs[d] = self._add_scaled(bb, xs[sr], xs[d], w2, sh, ng, spec)
                zs[sr], zs[d] = self._add_scaled(bb, zs[sr], zs[d], w2, sh, ng, spec)
            elif op == "negate":
                i = int(o["i"])
                xs[i] = self._negate(bb, xs[i], w2, spec)
                zs[i] = self._negate(bb, zs[i], w2, spec)
            elif op == "phaseProduct":
                i = int(o["i"])
                child_phi = self.phi * T.coeff(l, limb)
                l += 1
                child = self._child(w2, spec, child_phi)
                if ctrl is None:
                    xs[i], zs[i] = bb.add(child, x=xs[i], z=zs[i])
                else:
                    ctrl, xs[i], zs[i] = bb.add(child, ctrl=ctrl, x=xs[i], z=zs[i])
            else:
                raise DecomposeTypeError(f"no structural realisation of schedule op {op!r} "
                                         f"(shiftL/shiftR do not occur in the shipped catalog)")
        out = {"x": self._unchunk(bb, xs, wxs, self.wx, w2), "z": self._unchunk(bb, zs, wzs, self.wz, w2)}
        if ctrl is not None:
            out["ctrl"] = ctrl
        return out


@attrs.frozen
class PolicyPhaseProduct(_PolicyPPBase):
    """Signed phase product `exp(i·pi·phi·[[x]]·[[z]])` under a width-indexed policy."""
    policy: PhasePolicy = attrs.field(converter=_norm_policy)
    wx: int = attrs.field()
    wz: Optional[int] = None
    #: How leaves and adders are realised and priced (`cost_conventions`). Default: the QUALTRAN
    #: headline tier. With ``leaf="best"`` the root resolves one realisation for the whole tree.
    convention: cc.ConventionSpec = cc.QUALTRAN
    #: The angle (units of pi). Needed only by the decomposition; the cost views are angle-free.
    phi: Optional[Fraction] = attrs.field(default=None, converter=_norm_phi)

    _is_controlled = False

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.wx)), Register("z", QInt(self.wz))])

    def _child(self, w2: int, spec, phi):
        return PolicyPhaseProduct(self.policy, w2, convention=spec, phi=phi)

    def _leaf(self, bb, x, z, ctrl, spec):
        if spec.leaf == "grid":
            x, z = bb.add(GridPhaseLeaf(self.wx, self.wz, self.phi), x=x, z=z)
        else:
            (inner,) = cc.leaf_callees(self.wx, self.wz, spec.leaf)
            x, z = bb.add(DiagonalLeaf(inner, self.phi), x=x, z=z)
        return {"x": x, "z": z}

    def build_composite_bloq(self, bb: BloqBuilder, x, z):
        return self._structure(bb, x, z)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        spec = self.resolved_convention()
        s = self.step()
        if s is None:
            return cc.leaf_callees(self.wx, self.wz, spec.leaf, ssa)
        T, w2 = s
        cen = T.census()
        counts: Dict[Bloq, int] = {PolicyPhaseProduct(self.policy, w2, convention=spec): cen["phaseProduct"]}
        for b, k in cc.level_callees(cen, w2, spec).items():
            counts[b] = counts.get(b, 0) + k
        return counts

    def on_classical_vals(self, x, z):
        return {"x": x, "z": z}

    def get_ctrl_system(self, ctrl_spec):
        from qualtran.bloqs.mcmt.specialized_ctrl import get_ctrl_system_1bit_cv_from_bloqs
        return get_ctrl_system_1bit_cv_from_bloqs(
            self, ctrl_spec, current_ctrl_bit=None,
            bloq_with_ctrl=CPolicyPhaseProduct(self.policy, self.wx, self.wz, self.convention, self.phi),
            ctrl_reg_name="ctrl")

    def __str__(self) -> str:
        dims = f"{self.wx}" if self.wx == self.wz else f"{self.wx}x{self.wz}"
        return f"PolicyPP({self.policy.name}, {dims})"


@attrs.frozen
class CPolicyPhaseProduct(_PolicyPPBase):
    """Controlled policy phase product (ForShor's `Policy.cSignedPlan`): the table's arithmetic
    runs uncontrolled (it returns to its start state), only the leaves are controlled."""
    policy: PhasePolicy = attrs.field(converter=_norm_policy)
    wx: int = attrs.field()
    wz: Optional[int] = None
    convention: cc.ConventionSpec = cc.QUALTRAN
    phi: Optional[Fraction] = attrs.field(default=None, converter=_norm_phi)

    _is_controlled = True

    @property
    def signature(self) -> Signature:
        return Signature([Register("ctrl", QBit()), Register("x", QInt(self.wx)), Register("z", QInt(self.wz))])

    def _child(self, w2: int, spec, phi):
        return CPolicyPhaseProduct(self.policy, w2, convention=spec, phi=phi)

    def _leaf(self, bb, x, z, ctrl, spec):
        if spec.leaf == "grid":
            ctrl, x, z = bb.add(CGridPhaseLeaf(self.wx, self.wz, self.phi), ctrl=ctrl, x=x, z=z)
        else:
            (inner,) = controlled_leaf_callees(self.wx, self.wz, spec.leaf)
            ctrl, x, z = bb.add(DiagonalLeaf(inner, self.phi), ctrl=ctrl, x=x, z=z)
        return {"ctrl": ctrl, "x": x, "z": z}

    def build_composite_bloq(self, bb: BloqBuilder, ctrl, x, z):
        return self._structure(bb, x, z, ctrl)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        spec = self.resolved_convention()
        s = self.step()
        if s is None:
            return controlled_leaf_callees(self.wx, self.wz, spec.leaf)
        T, w2 = s
        cen = T.census()
        counts: Dict[Bloq, int] = {CPolicyPhaseProduct(self.policy, w2, convention=spec): cen["phaseProduct"]}
        for b, k in cc.level_callees(cen, w2, spec).items():
            counts[b] = counts.get(b, 0) + k
        return counts

    def on_classical_vals(self, ctrl, x, z):
        return {"ctrl": ctrl, "x": x, "z": z}

    def __str__(self) -> str:
        dims = f"{self.wx}" if self.wx == self.wz else f"{self.wx}x{self.wz}"
        return f"CPolicyPP({self.policy.name}, {dims})"


@lru_cache(maxsize=None)
def _cleaf_cost(wx: int, wz: int, leaf: str) -> cc.TCost:
    return cc.callees_cost(controlled_leaf_callees(wx, wz, leaf))


def _ctree_cost(policy, wx: int, wz: int, spec: cc.ConventionSpec, leaf: str) -> cc.TCost:
    """Whole-tree `TCost` of the controlled product with every leaf realised as `leaf`."""
    tot, nodes = cc.ZERO, 1
    while True:
        s = policy.step(wx, wz)
        if s is None:
            return tot + _cleaf_cost(wx, wz, leaf).scale(nodes)
        T, w2 = s
        tot = tot + cc.level_cost(cc.census_key(T), w2, spec).scale(nodes)
        nodes *= 2 * T.k - 1
        wx = wz = w2


# ============================================================================ Q01 factory
def make_q01(manifest: str, n: int, phi: str = "1/8", convention: str = "qualtran",
             leaf: Optional[str] = None, controlled: bool = False, verify: bool = True) -> Bloq:
    """Q01 `bloq_gate_spec.json` factory: a (controlled) policy phase product at concrete `n`.

    ``manifest`` is a `forshor_qualtran.policy/v1` manifest path (relative paths resolve against the
    repository root); ``convention`` names a tier in `cost_conventions.TIERS` (or ``legacy``);
    ``leaf`` optionally pins the leaf realisation."""
    from pathlib import Path
    from .. import paths
    from ..policy import load_policy
    p = Path(manifest)
    if not p.is_absolute():
        p = paths.PIPE_ROOT / p
    P, _ = load_policy(p, verify=verify)
    spec = {"legacy": cc.LEGACY, **cc.TIERS}[convention]
    if leaf is not None:
        spec = spec.with_(leaf=leaf, leaf_options=(leaf,))
    cls = CPolicyPhaseProduct if controlled else PolicyPhaseProduct
    return cls(P, int(n), convention=spec, phi=Fraction(phi))
