r"""Price conventions for the (policy) phase product: ONE convention in sto_site (sto-site-v3).

sto_site fork of Lean-To-Qualtran `cost_conventions` (via sto_policy_cost). The convention, PLAN_v2 Stage 5:

* **leaf**: PhaseProduct is priced with the digital-product leaf (the L2Q `ProductPhaseLeaf`): a Qualtran `Product` of
  the unsigned patterns, two controlled subtractions (`CAdd`) that fold in the sign bits, one rotation per bit of the
  `wx + wz`-bit product, and all of it uncomputed. PhaseTripleProduct (Stage 17, 2026-10-28) is priced with the
  digital triple-product leaf (tcost `DigitalTripleLeaf`): t = x*y (`Product(wx, wy)`, two sign folds), p = t*z
  (`Product(wx + wy, wz)`, two sign folds), one rotation per bit of the signed (W - 2)-bit value p, all of it
  uncomputed. The grid leaf (one Qualtran `CZPowGate` per (x, y, z) bit triple, one shared And per (x, y) pair, tcost
  `TripleLeaf`) is kept as the labelled cross-check (`leaf="grid"`).
* **adder**: Qualtran `Add(QInt(W'))` at the uniform child width W'.
* **negsrc**: an `addScaled` with a negated source costs one adder, Qualtran `Subtract` (the L2Q convention and the
  one Lean charges). The tcost engine states the same thing as the atomic bloq `AddNegSrcW`.
* **rotations**: `sto_currency`, shared with tcost: eps = 0.5 * delta / R, T per rotation unrounded.

Each cost is a `TCost(base, rot)`: T without rotations (Qualtran `QECGatesCost`,
`total_t_count(ts_per_rotation=0)`) and a rotation count. The T of a tree is `base + R * t_rot(delta, R)`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from typing import Dict, Optional, Tuple

import sympy
from qualtran import Bloq, QInt
from qualtran.resource_counting import SympySymbolAllocator

from sto_currency import DEFAULT_DELTA, LEAF_BY_MODE, SITE_MODEL_VERSION, ts_per_rotation

from .rotation import RotationBudget

LEAVES = ("grid", "product")
NEGSRC = ("negate_add_negate", "subtract")
PAIR, TRIPLE = "PhaseProduct", "PhaseTripleProduct"
SIDES = {PAIR: 2, TRIPLE: 3}


def point_count(mode: str, k: int) -> int:
    return 2 * k - 1 if mode == PAIR else 3 * k - 2


@dataclass(frozen=True)
class ConventionSpec:
    name: str
    negsrc: str = "subtract"
    delta: float = DEFAULT_DELTA

    def __post_init__(self):
        if self.negsrc not in NEGSRC:
            raise ValueError(f"negsrc {self.negsrc!r} not in {NEGSRC}")
        RotationBudget(self.delta)

    leaf = "product (PP); digital triple product (PTP)"
    adder = "qualtran"

    @property
    def leaves(self) -> Tuple[str, ...]:
        return ("product",)

    def with_(self, **kw) -> "ConventionSpec":
        return replace(self, **kw)

    @property
    def budget(self) -> RotationBudget:
        return RotationBudget(self.delta)

    def t_per_rotation(self, rotations: float) -> float:
        return ts_per_rotation(self.delta, int(rotations))

    def price(self, cost: "TCost") -> float:
        """T of a full priced object: T without rotations + R synthesised rotations at eps = 0.5 delta / R."""
        if cost.rot == 0:
            return float(cost.base)
        return float(cost.base + cost.rot * self.t_per_rotation(cost.rot))

    def to_json(self) -> Dict:
        j = asdict(self)
        j.update(leaf=self.leaf, adder=self.adder, model=SITE_MODEL_VERSION,
                 package="forshor_qualtran.cost_conventions (sto_site fork)",
                 rotation_cost_model="3.075*log2(1/eps)+0.86 unrounded, eps = 0.5*delta/R (sto_currency)")
        return j


#: The one tier of the site.
SITE = ConventionSpec(name="sto_site")
TIERS = {"sto_site": SITE}


@dataclass(frozen=True)
class TCost:
    """A Clifford+T cost before the rotation price is known: the T without rotations, and the rotation count."""
    base: int
    rot: int

    def __add__(self, o: "TCost") -> "TCost":
        return TCost(self.base + o.base, self.rot + o.rot)

    def scale(self, m: int) -> "TCost":
        return TCost(m * self.base, m * self.rot)


ZERO = TCost(0, 0)


def split(bloq: Bloq) -> TCost:
    """Return the Qualtran `QECGatesCost` of `bloq` as `(T without rotations, rotations)`."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    c = get_cost_value(bloq, QECGatesCost())
    return TCost(int(c.total_t_count(ts_per_rotation=0)), int(c.rotation))


_E = sympy.Symbol("e")


def leaf_callees(wx: int, wz: int, leaf: str = "product", ssa: Optional[SympySymbolAllocator] = None,
                 wy: Optional[int] = None) -> Dict[Bloq, int]:
    """Callees of one leaf. Pair, `product`: the digital product (a `Product`, two `CAdd`s and their adjoints, wx + wz
    rotations). Pair, `grid`: wx*wz CZPowGates. Triple (wy given), `product` (the site's PTP leaf since Stage 17):
    the digital triple product, `Product(wx, wy)` and `Product(wx + wy, wz)` each with its adjoint, four sign folds
    (`CAdd` at wy, wx, wz, wx + wy, each with its inverse) and wx + wy + wz - 2 rotations (x*y*z fits a signed
    (W - 2)-bit register, tcost `digital_triple_rotation_bits`). Triple, `grid` (the labelled cross-check): wx*wy
    And/And^dagger pairs and wx*wy*wz CZPowGates (tcost `TripleLeaf`)."""
    if leaf not in LEAVES:
        raise ValueError(f"leaf must be one of {LEAVES}, got {leaf!r}")
    from qualtran.bloqs.basic_gates import CZPowGate
    e = ssa.new_symbol("e") if ssa is not None else _E
    if leaf == "product":
        from qualtran import QUInt
        from qualtran.bloqs.arithmetic import CAdd, Product
        from qualtran.bloqs.basic_gates import ZPowGate
        if wy is None:
            prods, folds, rot = [Product(wx, wz)], (wx, wz), wx + wz
        else:
            prods, folds, rot = [Product(wx, wy), Product(wx + wy, wz)], (wy, wx, wz, wx + wy), wx + wy + wz - 2
        out: Dict[Bloq, int] = {ZPowGate(exponent=e): rot}
        for prod in prods:
            out[prod] = out.get(prod, 0) + 1
            out[prod.adjoint()] = out.get(prod.adjoint(), 0) + 1
        for w in folds:
            c = CAdd(QUInt(w), QUInt(w))
            out[c] = out.get(c, 0) + 1
            out[c.adjoint()] = out.get(c.adjoint(), 0) + 1
        return out
    if wy is None:
        return {CZPowGate(exponent=e): wx * wz}
    from qualtran.bloqs.mcmt import And
    pairs = wx * wy
    return {And(): pairs, And(uncompute=True): pairs, CZPowGate(exponent=e): pairs * wz}


def level_callees(census: Dict[str, int], w2: int, spec: ConventionSpec, sides: int = 2) -> Dict[Bloq, int]:
    """The arithmetic of one recursive node (without the children): each op on each of the `sides` operands at
    the uniform child width `w2`. Shifts and sign extensions are Clifford-only and add no T."""
    from qualtran.bloqs.arithmetic import Add, Negate, Subtract
    adds = sides * census["addScaled"]
    negsrc = sides * census["addScaled_negSrc"]
    negs = sides * census["negate"]
    out: Dict[Bloq, int] = {}

    def put(b, k):
        if k:
            out[b] = out.get(b, 0) + k

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
def leaf_cost(wx: int, wz: int, leaf: str = "product", wy: Optional[int] = None) -> TCost:
    return callees_cost(leaf_callees(wx, wz, leaf, wy=wy))


def level_cost(census_items: Tuple[Tuple[str, int], ...], w2: int, spec: ConventionSpec, sides: int = 2) -> TCost:
    return _level_cost(census_items, w2, spec.negsrc, sides)


@lru_cache(maxsize=None)
def _level_cost(census_items, w2, negsrc, sides) -> TCost:
    return callees_cost(level_callees(dict(census_items), w2, ConventionSpec("_", negsrc=negsrc), sides))


def census_key(T) -> Tuple[Tuple[str, int], ...]:
    return tuple(sorted(T.census().items()))


def leaf_of(mode: str) -> str:
    """The leaf the site prices for `mode` (`sto_currency.LEAF_BY_MODE`)."""
    return LEAF_BY_MODE[mode]


def tree_cost(policy, wx: int, wz: int, spec: ConventionSpec = SITE, leaf: Optional[str] = None,
              mode: str = PAIR) -> TCost:
    """`TCost` of the full tree of `policy` at square operand width (wx = wz). For PhaseTripleProduct the walk
    uses 3 sides and 3k-2 children per level, and the triple leaf."""
    sides = SIDES[mode]
    leaf = leaf or leaf_of(mode)
    tot, nodes = ZERO, 1
    while True:
        s = policy.step(wx, wz)
        if s is None:
            lc = leaf_cost(wx, wz, leaf, wy=(wx if mode == TRIPLE else None))
            return tot + lc.scale(nodes)
        T, w2 = s
        tot = tot + level_cost(census_key(T), w2, spec, sides).scale(nodes)
        nodes *= point_count(mode, T.k)
        wx = wz = w2


def resolve_leaf(policy, wx: int, wz: int, spec: ConventionSpec, mode: str = PAIR) -> str:
    return leaf_of(mode)
