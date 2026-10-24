r"""Price conventions for the (policy) phase product. They have one definition, and the planner and the bloq use it.

Without it, T counts can use an undocumented mix of Qualtran and Lean realisations. This module
gives a name to each choice that changes the number. It records the choices with the result (:meth:`ConventionSpec.to_json`):

* **leaf**: how we build a phase-product leaf `exp(i·phi·x·z)` on `wx × wz` signed operand bits.
    ``grid``          one Qualtran `CZPowGate` for each pair of operand bits (And + 1 rotation each).
    ``merged_cphase`` the verified leaf of ForShor (one `LowGate.CPhase` for each cell: Phase, Phase,
                      CNOT, Phase, CNOT), with the single-qubit phases on each operand qubit merged.
                      The phase polynomial of the leaf has exactly `wx·wz + wx + wz` terms. Thus the
                      leaf has that number of rotations, `2·wx·wz` CNOTs and no And.
    ``product``       the product, computed digitally. Qualtran `Product(wx, wz)` of the unsigned
                      patterns, then two controlled subtractions that add the two's-complement sign
                      bits, then one rotation for each bit of the signed product of `wx + wz` bits.
                      Then the arithmetic is uncomputed. Qualtran bloqs give the price.
    ``best``          for each node, the cheapest of ``leaf_options``. The choice uses the honest T of
                      the full tree, because the rotation price depends on the total `R` of the tree.
* **adder**: ``qualtran`` is `Add`/`Subtract`/`Negate` on `QInt(w)`. ``cuccaro`` is the ForShor
  `cuccaroModAddResources` (`x 2w-6, cnot 5w-7, toffoli 2w-3`, ℕ-truncated) and
  `negateResources` (the same adder plus `w + 2` X).
* **negsrc**: an `addScaled` with a negated source. ``subtract`` is one adder (Qualtran `Subtract`,
  or one Cuccaro adder, as Lean counts it). ``negate_add_negate`` is the legacy
  `Negate · Add · Negate`.
* **rotations**: always Ross-Selinger (`rotation.ross_selinger_t`, 3.075 log2(1/eps) + 0.86) at
  `eps = delta * rotation_fraction / R_ctx`. `R_ctx` is the rotation count of the priced object,
  if you do not give ``r_context``. There is no other synthesiser and no discount for the angle mix.

Two named tiers (owner decision: Qualtran costing has priority):

    QUALTRAN       leaf best of {grid, product}, Qualtran adders, negsrc = Subtract, Ross-Selinger.
                   This is the HEADLINE tier.
    LEAN_FAITHFUL  merged_cphase leaf, Cuccaro adders, negsrc = one adder, Ross-Selinger. This is
                   the cross-check tier. Its counts are equal to the counts of the literal circuit
                   that Lean lowers (`tests/test_cost_conventions.py`, verification task V-2).

Each cost here is a `TCost(base, rot)`: the T without rotations (Qualtran `QECGatesCost`,
`total_t_count(ts_per_rotation=0)`) and a rotation count. The T of a tree is
`base + R · t_rot(R_ctx)`. It is never a flat price for each rotation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from typing import Dict, Optional, Tuple

import attrs
import sympy
from qualtran import Bloq, QAny, QInt, QUInt, Register, Signature
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from .rotation import RotationBudget, ross_selinger_t

LEAVES = ("grid", "merged_cphase", "product")
ADDERS = ("qualtran", "cuccaro")
NEGSRC = ("subtract", "negate_add_negate")


# ============================================================================ the spec
@dataclass(frozen=True)
class ConventionSpec:
    name: str
    leaf: str = "best"
    leaf_options: Tuple[str, ...] = ("grid", "product")
    adder: str = "qualtran"
    negsrc: str = "subtract"
    delta: float = 0.01
    rotation_fraction: float = 0.5
    r_context: Optional[float] = None

    def __post_init__(self):
        if self.leaf != "best" and self.leaf not in LEAVES:
            raise ValueError(f"leaf {self.leaf!r} not in {LEAVES + ('best',)}")
        if any(o not in LEAVES for o in self.leaf_options) or not self.leaf_options:
            raise ValueError(f"leaf_options {self.leaf_options!r} must be a non-empty subset of {LEAVES}")
        if self.adder not in ADDERS:
            raise ValueError(f"adder {self.adder!r} not in {ADDERS}")
        if self.negsrc not in NEGSRC:
            raise ValueError(f"negsrc {self.negsrc!r} not in {NEGSRC}")

    # ---- the leaf realisations that this spec can use
    @property
    def leaves(self) -> Tuple[str, ...]:
        return self.leaf_options if self.leaf == "best" else (self.leaf,)

    def with_leaf(self, leaf: str) -> "ConventionSpec":
        return replace(self, leaf=leaf)

    def with_(self, **kw) -> "ConventionSpec":
        return replace(self, **kw)

    @property
    def budget(self):
        return RotationBudget(delta=self.delta, rotation_fraction=self.rotation_fraction)

    def t_per_rotation(self, rotations: float) -> float:
        """Return the Ross-Selinger T for one rotation, if the priced object has `rotations` rotations."""
        r_ctx = self.r_context if self.r_context is not None else rotations
        return ross_selinger_t(self.budget.epsilon(r_ctx))

    def price(self, cost: "TCost") -> float:
        """Return the T of a full priced object: the T without rotations + R synthesised rotations."""
        if cost.rot == 0:
            return float(cost.base)
        return cost.base + cost.rot * self.t_per_rotation(cost.rot)

    def to_json(self) -> Dict:
        j = asdict(self)
        j["leaf_options"] = list(self.leaf_options)
        j["package"] = "forshor_qualtran.cost_conventions"
        j["rotation_cost_model"] = ("Ross-Selinger 3.075*log2(1/eps)+0.86 (forshor_qualtran.rotation) "
                                    "at eps = delta*rotation_fraction / R_ctx")
        return j


QUALTRAN = ConventionSpec(name="qualtran", leaf="best", leaf_options=("grid", "product"),
                          adder="qualtran", negsrc="subtract")
LEAN_FAITHFUL = ConventionSpec(name="lean_faithful", leaf="merged_cphase", leaf_options=("merged_cphase",),
                               adder="cuccaro", negsrc="subtract")
#: The prices that the planner/bloq used before the audit (grid leaf, Qualtran adders, negSrc as
#: Negate·Add·Negate). We keep it only for comparison with old numbers.
LEGACY = ConventionSpec(name="legacy_grid", leaf="grid", leaf_options=("grid",), adder="qualtran",
                        negsrc="negate_add_negate")
TIERS = {"qualtran": QUALTRAN, "lean_faithful": LEAN_FAITHFUL}


# ============================================================================ costs
@dataclass(frozen=True)
class TCost:
    """A Clifford+T cost before the rotation price is known: the T without rotations, and the rotation count."""
    base: int
    rot: int

    def __add__(self, o: "TCost") -> "TCost":
        return TCost(self.base + o.base, self.rot + o.rot)

    def scale(self, m: int) -> "TCost":
        return TCost(m * self.base, m * self.rot)

    def t(self, budget: Optional[RotationBudget] = None, rotation_context: Optional[float] = None) -> float:
        """Return the Ross-Selinger T for a bare `RotationBudget`. We keep it for callers older than the spec."""
        if self.rot == 0:
            return float(self.base)
        b = budget or RotationBudget()
        r_ctx = rotation_context if rotation_context is not None else self.rot
        return self.base + self.rot * b.t_per_rotation(r_ctx)


ZERO = TCost(0, 0)


def split(bloq: Bloq) -> TCost:
    """Return the Qualtran `QECGatesCost` of `bloq` as `(T without rotations, rotations)`."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    c = get_cost_value(bloq, QECGatesCost())
    return TCost(int(c.total_t_count(ts_per_rotation=0)), int(c.rotation))


def price_bloq(bloq: Bloq, spec: ConventionSpec) -> float:
    return spec.price(split(bloq))


def cuccaro_resources(w: int) -> Dict[str, int]:
    """`cuccaroModAddResources w` (ForShor `ResourceModel.lean`), with Lean's ℕ truncation."""
    return {"x": max(0, 2 * w - 6), "cnot": max(0, 5 * w - 7), "toffoli": max(0, 2 * w - 3)}


def negate_resources(w: int) -> Dict[str, int]:
    """`negateResources` on a register of `w` bits: the Cuccaro adder plus `w + 2` X."""
    r = cuccaro_resources(w)
    r["x"] += w + 2
    return r


# ---------------------------------------------------------------- realisation bloqs
_E = sympy.Symbol("e")


def _rot():
    from qualtran.bloqs.basic_gates import ZPowGate
    return ZPowGate(exponent=_E)


@attrs.frozen
class CuccaroAdder(Bloq):
    """The ForShor adder, with the price from `cuccaroModAddResources` (`negate=True`: `negateResources`)."""
    w: int
    negate: bool = False

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QAny(max(1, self.w)))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import CNOT, Toffoli, XGate
        r = negate_resources(self.w) if self.negate else cuccaro_resources(self.w)
        out: Dict[Bloq, int] = {}
        for b, k in ((Toffoli(), r["toffoli"]), (CNOT(), r["cnot"]), (XGate(), r["x"])):
            if k:
                out[b] = k
        return out


@attrs.frozen
class MergedCPhaseLeaf(Bloq):
    """The verified leaf of ForShor, `wx·wz` `LowGate.CPhase`s, with the single-qubit phases merged.
    It has one rotation for each term of the phase polynomial (`wx·wz + wx + wz`), `2·wx·wz` CNOTs, no And."""
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
    """Do `exp(i·phi·x·z)` for signed `x`, `z` through the product. The steps are:

    1. `p = u_x·u_z` (Qualtran `Product` on the unsigned patterns).
    2. `p -= s_x·u_z·2^wx`, `p -= s_z·u_x·2^wz` (controlled subtractions, with the price of `CAdd`).
    3. One rotation for each bit of the two's-complement `p` of `wx + wz` bits. The `2^(wx+wz)` term
       is zero mod `2^(wx+wz)`, and `x·z` fits.
    4. Uncompute all of it.

    Qubit cost: the product register of `wx + wz` bits."""
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
    """Return the callees of one leaf for a concrete realisation."""
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
    """Return the arithmetic of one recursive node (without the children). Each schedule op acts on the
    two operands at the child width `w2`. All chunks get this width before the program runs."""
    sides = 2
    adds = sides * census["addScaled"]
    negsrc = sides * census["addScaled_negSrc"]
    negs = sides * census["negate"]
    out: Dict[Bloq, int] = {}

    def put(b, k):
        if k:
            out[b] = out.get(b, 0) + k

    if spec.adder == "cuccaro":
        # Lean counts one Cuccaro adder for each addScaled, for each sign (`addScaledResources`)
        put(CuccaroAdder(w2), adds)
        put(CuccaroAdder(w2, negate=True), negs + (2 * negsrc if spec.negsrc == "negate_add_negate" else 0))
        return out
    from qualtran.bloqs.arithmetic import Add, Negate, Subtract
    if spec.negsrc == "subtract":
        put(Add(QInt(w2)), adds - negsrc)
        put(Subtract(QInt(w2)), negsrc)
        put(Negate(QInt(w2)), negs)
    else:
        put(Add(QInt(w2)), adds)
        put(Negate(QInt(w2)), negs + 2 * negsrc)
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
    """Return the `TCost` of the full tree of `policy` at `(wx, wz)`, with each leaf realised as `leaf`."""
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
    """Return the leaf realisation that a ``best`` spec uses for this tree. All leaves of a policy tree
    have the same width. Thus there is one choice for each tree, and it uses the honest T of the tree."""
    if spec.leaf != "best":
        return spec.leaf
    return min(spec.leaves, key=lambda r: (spec.price(tree_cost(policy, wx, wz, spec, r)), LEAVES.index(r)))
