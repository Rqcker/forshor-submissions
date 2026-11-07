r"""Pricing conventions for the (policy) phase product — one definition, used by planner and bloq.

The roundtable audit of 2026-10-12 (C-03) found the shipped T-counts rested on an undocumented mix
of Qualtran and Lean realisations. This module names every choice that moves the number and
records it with the result (:meth:`ConventionSpec.to_json`):

* **leaf** — how a phase-product leaf `exp(i·phi·x·z)` on `wx × wz` signed operand bits is built:
    ``grid``          one Qualtran `CZPowGate` per operand-bit pair (And + 1 rotation each);
    ``merged_cphase`` ForShor's verified leaf (`LowGate.CPhase` per cell: Phase, Phase, CNOT,
                      Phase, CNOT) with the single-qubit phases on each operand qubit merged —
                      the leaf's phase polynomial has exactly `wx·wz + wx + wz` terms, so that many
                      rotations, `2·wx·wz` CNOTs and no And;
    ``product``       the product computed digitally: Qualtran `Product(wx, wz)` of the unsigned
                      patterns, two controlled subtractions folding in the two's-complement sign
                      bits, one rotation per bit of the `wx + wz`-bit signed product, then the
                      arithmetic uncomputed (the `product` realisation of `tcount/fair.py`, priced
                      through Qualtran bloqs);
    ``best``          per node, the cheapest of ``leaf_options`` (decided on the whole tree's
                      honest T, since the rotation price depends on the tree's total `R`).
* **adder** — ``qualtran``: `Add`/`Subtract`/`Negate` on `QInt(w)`; ``cuccaro``: ForShor's
  `cuccaroModAddResources` (`x 2w-6, cnot 5w-7, toffoli 2w-3`, ℕ-truncated) and
  `negateResources` (the same adder plus `w + 2` X).
* **negsrc** — an `addScaled` with a negated source: ``subtract`` (one adder: Qualtran `Subtract`,
  or one Cuccaro adder as Lean charges it) or ``negate_add_negate`` (the legacy
  `Negate · Add · Negate`).
* **synthesiser** — per-rotation T at `eps = delta_rot / R_ctx` from
  `Clifford_plus_T_scaling/tcount`: ``ross_selinger`` (`budget.ross_selinger_t`), ``fallback``
  (`fair.fb_site_t`), ``mixed_fallback`` (`fair.mf_site_t`). `R_ctx` is the priced object's own
  rotation count unless ``r_context`` is given; ``mix`` scales the per-rotation cost.

Two named tiers (owner decision: Qualtran costing takes precedence):

    QUALTRAN       leaf best of {grid, product}, Qualtran adders, negsrc = Subtract, Ross-Selinger
                   — the HEADLINE tier;
    LEAN_FAITHFUL  merged_cphase leaf, Cuccaro adders, negsrc = one adder, Ross-Selinger — the
                   cross-check tier; its counts equal those read off the literal Lean-lowered
                   circuit (`tests/test_cost_conventions.py`, verification task V-2).

Every cost here is a `TCost(base, rot)`: rotation-free T (Qualtran `QECGatesCost`,
`total_t_count(ts_per_rotation=0)`) and a rotation count. The T of a tree is
`base + R · t_rot(R_ctx)` — never a flat per-rotation charge.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from typing import Dict, Optional, Tuple

import attrs
import sympy
from qualtran import Bloq, QAny, QInt, QUInt, Register, Signature
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from . import paths as _paths

LEAVES = ("grid", "merged_cphase", "product")
ADDERS = ("qualtran", "cuccaro")
NEGSRC = ("subtract", "negate_add_negate")
#: The negation subroutine's cost convention.
#:   `gidney_circuit` (the DEFAULT): complement plus Gidney's one-clean-ancilla increment gate, priced by the counts of the explicit
#:     circuit built and simulated in `bloqs/gidney_increment.py` (Toffoli 12w-28 even / 10w-18 odd, CNOT 17w-22 / 17w-5, X 5w-3 / 5w+1,
#:     one clean qubit): exactly what ForShor's `negateResources` charges (the `oneQubit` NegationMethod, `negateChargeAt`).
#:   `legacy`: a `w`-bit constant-one register plus a Cuccaro adder (`2w-3` Toffoli, `w+1` clean qubits): ForShor's `scratch` method.
#:   `incrementer`: SUPERSEDED. Complement plus `32 w` Toffoli (and no CNOT), the first reading of Khattar-Gidney's Table 1, which listed
#:     Gidney's TOTAL gate count as Toffolis. It over-charges T and is kept selectable only to reproduce PR #79's first-draft numbers; it is
#:     never a default.
#: Do not confuse this name with the EPOCH model's `negation: "incrementer"` (export_v2): that label means ForShor's `oneQubit`, i.e.
#: `gidney_circuit` here (see `export_v2/epoch.py::NEGATION_OF`).
NEGATE_IMPLS = ("legacy", "incrementer", "gidney_circuit")
DEFAULT_NEGATE = "gidney_circuit"
#: The impls whose negation borrows one clean qubit (everything except `legacy`).
ONE_QUBIT_NEGATE = ("incrementer", "gidney_circuit")
SYNTHESISERS = ("ross_selinger", "fallback", "mixed_fallback")


# ============================================================================ tcount access
def _budget_module():
    """`tcount.budget` — resolved by `paths.tcount_module` (installed package, else the tree)."""
    return _paths.tcount_module("budget")


def _fair_module():
    return _paths.tcount_module("fair")


# ============================================================================ the spec
@dataclass(frozen=True)
class ConventionSpec:
    name: str
    leaf: str = "best"
    leaf_options: Tuple[str, ...] = ("grid", "product")
    adder: str = "qualtran"
    negsrc: str = "subtract"
    synthesiser: str = "ross_selinger"
    delta: float = 0.01
    rotation_fraction: float = 0.5
    mix: float = 1.0
    r_context: Optional[float] = None
    negate: str = DEFAULT_NEGATE

    def __post_init__(self):
        if self.leaf != "best" and self.leaf not in LEAVES:
            raise ValueError(f"leaf {self.leaf!r} not in {LEAVES + ('best',)}")
        if any(o not in LEAVES for o in self.leaf_options) or not self.leaf_options:
            raise ValueError(f"leaf_options {self.leaf_options!r} must be a non-empty subset of {LEAVES}")
        if self.adder not in ADDERS:
            raise ValueError(f"adder {self.adder!r} not in {ADDERS}")
        if self.negsrc not in NEGSRC:
            raise ValueError(f"negsrc {self.negsrc!r} not in {NEGSRC}")
        if self.negate not in NEGATE_IMPLS:
            raise ValueError(f"negate {self.negate!r} not in {NEGATE_IMPLS}")
        if self.synthesiser not in SYNTHESISERS:
            raise ValueError(f"synthesiser {self.synthesiser!r} not in {SYNTHESISERS}")

    # ---- leaf realisations this spec may use
    @property
    def leaves(self) -> Tuple[str, ...]:
        return self.leaf_options if self.leaf == "best" else (self.leaf,)

    def with_leaf(self, leaf: str) -> "ConventionSpec":
        return replace(self, leaf=leaf)

    def with_(self, **kw) -> "ConventionSpec":
        return replace(self, **kw)

    @property
    def budget(self):
        return _budget_module().RotationBudget(delta=self.delta, rotation_fraction=self.rotation_fraction)

    def t_per_rotation(self, rotations: float) -> float:
        """Synthesis T for one rotation when the priced object has `rotations` of them."""
        r_ctx = self.r_context if self.r_context is not None else rotations
        eps = self.budget.epsilon(r_ctx)
        if self.synthesiser == "ross_selinger":
            t = _budget_module().ross_selinger_t(eps)
        elif self.synthesiser == "fallback":
            t = _fair_module().fb_site_t(eps)
        else:
            t = _fair_module().mf_site_t(eps)
        return self.mix * t

    def price(self, cost: "TCost") -> float:
        """T of a whole priced object: rotation-free T + R synthesised rotations."""
        if cost.rot == 0:
            return float(cost.base)
        return cost.base + cost.rot * self.t_per_rotation(cost.rot)

    def to_json(self) -> Dict:
        j = asdict(self)
        j["leaf_options"] = list(self.leaf_options)
        j["package"] = "forshor_qualtran.cost_conventions"
        j["rotation_cost_model"] = "Clifford_plus_T_scaling/tcount " + {
            "ross_selinger": "budget.ross_selinger_t", "fallback": "fair.fb_site_t",
            "mixed_fallback": "fair.mf_site_t"}[self.synthesiser] + " at eps = delta*rotation_fraction / R_ctx"
        return j


QUALTRAN = ConventionSpec(name="qualtran", leaf="best", leaf_options=("grid", "product"),
                          adder="qualtran", negsrc="subtract")
LEAN_FAITHFUL = ConventionSpec(name="lean_faithful", leaf="merged_cphase", leaf_options=("merged_cphase",),
                               adder="cuccaro", negsrc="subtract")
#: The pricing the planner/bloq used before the audit (grid leaf, Qualtran adders, negSrc as
#: Negate·Add·Negate). Kept only to compare against old numbers.
LEGACY = ConventionSpec(name="legacy_grid", leaf="grid", leaf_options=("grid",), adder="qualtran",
                        negsrc="negate_add_negate")
TIERS = {"qualtran": QUALTRAN, "lean_faithful": LEAN_FAITHFUL}


# ============================================================================ costs
@dataclass(frozen=True)
class TCost:
    """A Clifford+T cost before the rotation price is fixed: rotation-free T and rotation count."""
    base: int
    rot: int

    def __add__(self, o: "TCost") -> "TCost":
        return TCost(self.base + o.base, self.rot + o.rot)

    def scale(self, m: int) -> "TCost":
        return TCost(m * self.base, m * self.rot)

    def t(self, budget=None, mix: float = 1.0, rotation_context: Optional[float] = None) -> float:
        """Ross-Selinger T under a bare `RotationBudget` (kept for callers predating the spec)."""
        if self.rot == 0:
            return float(self.base)
        b = budget or _budget_module().RotationBudget()
        r_ctx = rotation_context if rotation_context is not None else self.rot
        return self.base + self.rot * b.t_per_rotation(r_ctx, mix_ratio=mix)


ZERO = TCost(0, 0)


def split(bloq: Bloq) -> TCost:
    """Qualtran `QECGatesCost` of `bloq` as `(rotation-free T, rotations)`."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    c = get_cost_value(bloq, QECGatesCost())
    return TCost(int(c.total_t_count(ts_per_rotation=0)), int(c.rotation))


def price_bloq(bloq: Bloq, spec: ConventionSpec) -> float:
    return spec.price(split(bloq))


def cuccaro_resources(w: int) -> Dict[str, int]:
    """`cuccaroModAddResources w` (ForShor `ResourceModel.lean`), with Lean's ℕ truncation."""
    return {"x": max(0, 2 * w - 6), "cnot": max(0, 5 * w - 7), "toffoli": max(0, 2 * w - 3)}


def negate_resources(w: int, impl: str = DEFAULT_NEGATE) -> Dict[str, int]:
    """`negateResources` on a `w`-bit register (ForShor `ResourceModel.lean`).

    ``gidney_circuit`` (the default; what Lean charges): the counts of the explicit circuit of
    `bloqs/gidney_increment.py` (Toffoli 12w-28 even / 10w-18 odd, CNOT 17w-22 / 17w-5, X 5w-3 / 5w+1; Lean's
    truncating subtraction, exact for w >= 7, an upper bound below).
    ``legacy``: the Cuccaro adder plus `w + 2` X (ForShor's `scratch` method).
    ``incrementer``: SUPERSEDED first draft, `w` X and a flat `32 w` Toffoli, no CNOT (Khattar-Gidney's Table 1 listed
    Gidney's total gate count as Toffolis). Kept selectable only to reproduce that draft; not a default.
    """
    if impl == "gidney_circuit":
        from .bloqs.gidney_increment import bound_counts
        return bound_counts(w)
    if impl == "incrementer":
        return {"x": w, "cnot": 0, "toffoli": 32 * w}
    if impl != "legacy":
        raise ValueError(f"negate impl {impl!r} not in {NEGATE_IMPLS}")
    r = cuccaro_resources(w)
    r["x"] += w + 2
    return r


def negate_clean_ancilla(w: int, impl: str = DEFAULT_NEGATE) -> int:
    """Clean qubits `negateResources` charges while a `w`-bit negation runs."""
    return 1 if impl in ONE_QUBIT_NEGATE else w + 1


# ---------------------------------------------------------------- realisation bloqs
_E = sympy.Symbol("e")


def _rot():
    from qualtran.bloqs.basic_gates import ZPowGate
    return ZPowGate(exponent=_E)


@attrs.frozen
class CuccaroAdder(Bloq):
    """ForShor's adder as priced by `cuccaroModAddResources` (`negate=True`: `negateResources`)."""
    w: int
    negate: bool = False

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QAny(max(1, self.w)))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import CNOT, Toffoli, XGate
        # `negate=True` is the LEGACY negation (Cuccaro + constant register); the swap-001
        # negation is `IncrementerNegate`.
        r = negate_resources(self.w, "legacy") if self.negate else cuccaro_resources(self.w)
        out: Dict[Bloq, int] = {}
        for b, k in ((Toffoli(), r["toffoli"]), (CNOT(), r["cnot"]), (XGate(), r["x"])):
            if k:
                out[b] = k
        return out


def _wrap_tc(v: int, w: int) -> int:
    m = 1 << w
    v %= m
    return v - m if v >= m // 2 else v


@attrs.frozen
class IncrementerNegate(Bloq):
    """`r <- -r (mod 2^w)` as ForShor's `negateResources` charges it (ForShor `oneQubit`): the complement then Gidney's
    increment gate with ONE clean ancilla, with the counts of `negate_resources(w, impl)`: for `impl = "gidney_circuit"`
    (the default) the explicit circuit's counts, for `impl = "incrementer"` the SUPERSEDED flat `32 w` Toffoli.
    Classical action declared."""
    w: int
    impl: str = DEFAULT_NEGATE

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(max(1, self.w)))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import CNOT, XGate, Toffoli
        r = negate_resources(self.w, self.impl)
        return {b: k for b, k in ((Toffoli(), r["toffoli"]), (CNOT(), r["cnot"]), (XGate(), r["x"])) if k}

    def on_classical_vals(self, x):
        return {"x": _wrap_tc(-int(x), self.w)}


def negate_bloq(w: int, spec: "ConventionSpec") -> Bloq:
    """The bloq a `w`-bit negation is priced as under `spec`."""
    if spec.negate in ONE_QUBIT_NEGATE:
        return IncrementerNegate(w, spec.negate)
    if spec.adder == "cuccaro":
        return CuccaroAdder(w, negate=True)
    from qualtran.bloqs.arithmetic import Negate
    return Negate(QInt(w))


@attrs.frozen
class MergedCPhaseLeaf(Bloq):
    """ForShor's verified leaf, `wx·wz` `LowGate.CPhase`s, with single-qubit phases merged: one
    rotation per phase-polynomial term (`wx·wz + wx + wz`), `2·wx·wz` CNOTs, no And."""
    wx: int
    wz: int

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.wx)), Register("z", QInt(self.wz))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import CNOT
        return {_rot(): self.wx * self.wz + self.wx + self.wz, CNOT(): 2 * self.wx * self.wz}


@attrs.frozen
class ProductPhaseLeaf(Bloq):
    """`exp(i·phi·x·z)` for signed `x`, `z` by computing the product: `p = u_x·u_z` (Qualtran
    `Product` on the unsigned patterns), `p -= s_x·u_z·2^wx`, `p -= s_z·u_x·2^wz` (controlled
    subtractions, priced as `CAdd`), one rotation per bit of the `wx + wz`-bit two's-complement
    `p` (the `2^(wx+wz)` term vanishes mod `2^(wx+wz)`, and `x·z` fits), then everything
    uncomputed. Qubit cost: the `wx + wz`-bit product register."""
    wx: int
    wz: int

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QInt(self.wx)), Register("z", QInt(self.wz))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.arithmetic import CAdd, Product
        prod = Product(self.wx, self.wz)
        out: Dict[Bloq, int] = {prod: 1, prod.adjoint(): 1, _rot(): self.wx + self.wz}
        for w in (self.wx, self.wz):
            c = CAdd(QUInt(w), QUInt(w))
            out[c] = out.get(c, 0) + 1
            out[c.adjoint()] = out.get(c.adjoint(), 0) + 1
        return out


def leaf_callees(wx: int, wz: int, leaf: str, ssa: Optional[SympySymbolAllocator] = None) -> Dict[Bloq, int]:
    """The callees of one leaf under a concrete realisation."""
    if leaf == "grid":
        from qualtran.bloqs.basic_gates import CZPowGate
        e = ssa.new_symbol("e") if ssa is not None else _E
        return {CZPowGate(exponent=e): wx * wz}
    if leaf == "merged_cphase":
        return {MergedCPhaseLeaf(wx, wz): 1}
    if leaf == "product":
        return {ProductPhaseLeaf(wx, wz): 1}
    raise ValueError(f"leaf must be concrete, got {leaf!r}")


def level_callees(census: Dict[str, int], w2: int, spec: ConventionSpec) -> Dict[Bloq, int]:
    """The arithmetic of one recursive node (children excluded): every schedule op acts on both
    operands, at the child width `w2` (all chunks are grown to it before the program runs)."""
    sides = 2
    adds = sides * census["addScaled"]
    negsrc = sides * census["addScaled_negSrc"]
    negs = sides * census["negate"]
    out: Dict[Bloq, int] = {}

    def put(b, k):
        if k:
            out[b] = out.get(b, 0) + k

    if spec.adder == "cuccaro":
        # Lean charges every addScaled one Cuccaro adder whatever its sign (`addScaledResources`)
        put(CuccaroAdder(w2), adds)
        put(negate_bloq(w2, spec), negs + (2 * negsrc if spec.negsrc == "negate_add_negate" else 0))
        return out
    from qualtran.bloqs.arithmetic import Add, Subtract
    if spec.negsrc == "subtract":
        put(Add(QInt(w2)), adds - negsrc)
        put(Subtract(QInt(w2)), negsrc)
        put(negate_bloq(w2, spec), negs)
    else:
        put(Add(QInt(w2)), adds)
        put(negate_bloq(w2, spec), negs + 2 * negsrc)
    return out


@lru_cache(maxsize=None)
def _split_cached(bloq: Bloq) -> TCost:
    return split(bloq)


def callees_cost(callees: Dict[Bloq, int]) -> TCost:
    tot = ZERO
    for b, k in callees.items():
        tot = tot + _split_cached(b).scale(k)
    return tot


@lru_cache(maxsize=None)
def leaf_cost(wx: int, wz: int, leaf: str) -> TCost:
    return callees_cost(leaf_callees(wx, wz, leaf))


def level_cost(census_items: Tuple[Tuple[str, int], ...], w2: int, spec: ConventionSpec) -> TCost:
    return _level_cost(census_items, w2, spec.adder, spec.negsrc)


@lru_cache(maxsize=None)
def _level_cost(census_items, w2, adder, negsrc) -> TCost:
    spec = ConventionSpec(name="_", leaf="grid", leaf_options=("grid",), adder=adder, negsrc=negsrc)
    return callees_cost(level_callees(dict(census_items), w2, spec))


def census_key(T) -> Tuple[Tuple[str, int], ...]:
    return tuple(sorted(T.census().items()))


# ============================================================================ trees under a policy
def tree_cost(policy, wx: int, wz: int, spec: ConventionSpec, leaf: str) -> TCost:
    """Whole-tree `TCost` of `policy` at `(wx, wz)` with every leaf realised as `leaf`."""
    tot, nodes = ZERO, 1
    while True:
        s = policy.step(wx, wz)
        if s is None:
            return tot + leaf_cost(wx, wz, leaf).scale(nodes)
        T, w2 = s
        tot = tot + level_cost(census_key(T), w2, spec).scale(nodes)
        nodes *= 2 * T.k - 1
        wx = wz = w2


def resolve_leaf(policy, wx: int, wz: int, spec: ConventionSpec) -> str:
    """The leaf realisation a ``best`` spec uses for this tree: every leaf of a policy tree has the
    same width, so one choice per tree, made on the tree's honest T."""
    if spec.leaf != "best":
        return spec.leaf
    return min(spec.leaves, key=lambda r: (spec.price(tree_cost(policy, wx, wz, spec, r)), LEAVES.index(r)))
