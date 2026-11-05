"""The catalytic Toom-Cook PHASE PRODUCT as a Qualtran bloq with real register wiring.

    CatalyticToomPhaseProduct(wx, wz, k, tau, b, ctrl, rules, *, stop_tsr=11.0, mixing=True, seed=0)

realises (on registers ``[ctrl: QBit], x: QInt(wx), z: QInt(wz), catalyst: QUInt(b)``, all returned)

    |c, x, z, catalyst k>  ->  |c, x, z, catalyst k + V(x, z)·c mod 2^b>,
    V(x, z) / 2^b  ~  tau · s(x) · s(z)   (mod 1),       s = two's-complement value,

i.e. the signed phase product exp(2 pi i tau s(x) s(z)) kicked back from ONE phase-gradient catalyst
(``catalyst.CatalystPrep``) that is threaded through every leaf of the Toom tree and returned exactly.
With ``ctrl`` the product is controlled (U1/U5); the control is passed down to the leaves, which fold
it in once per leaf (decision D4). The Toom body (evaluation / un-evaluation) is uncontrolled: it is
computed and uncomputed, so only the kicks need the control.

TREE. The recursion is the production ``recursion_versions.ToomRecursivePhaseProduct`` (version B,
rule ``kmy``): at a node of width w = max(wx, wz) the decision is ``RuleSet.decision(w, "B", k, "kmy",
..., stop_tsr)``. ``None`` -> one catalytic leaf; ``(k, W2)`` -> one Toom level with q = 2k-1 children
at width W2. So the tree (and the body count) are exactly the production's at the same stop price.

ONE TOOM LEVEL (the wiring; everything below is a real decomposition):
  1. sign-extend x, z to P = k·m bits, m = ceil(w/k) (``SignExtend``; the kmy "zero-extension to a
     multiple of k" -- for a signed operand the value-preserving extension is the sign extension);
  2. split each into k limbs of m bits (lower limbs unsigned, top limb signed -- ``tight_width.limb_range``)
     and widen every limb to a ``QInt(W2)`` register (fresh zero qubits for the lower limbs,
     ``SignExtend`` for the top limb). W2 is the tight (reachable-value) width of the bundle's schedule,
     so every intermediate value of step 3 fits ``QInt(W2)`` and modular adders are exact;
  3. run the bundle's n-free Toom schedule (``bundle.schedule.ops``, the same ops the production
     census counts) on the k x-registers AND the k z-registers (sides = 2):
        addScaled(dst, src, shift, negSrc): dst += (±src) << shift   -> ``Add(QInt(W2))`` (a shift is a
            relabelling onto fresh zero qubits; negSrc is ``Negate·Add·Negate``, as the production counts);
        negate(i)                                                     -> ``Negate(QInt(W2))``;
        phaseProduct(i)  (the l-th one)                               -> child l on (x_i, z_i) with turn
            tau_l = tau · c_l(2^m) mod 1, c_l the bundle's Toom coefficient polynomial l
            (``bundle_compat.coeff_polys``); every c_l(2^m) is an integer (checked at construction), so
            the mod-1 reduction is exact;
     the schedule returns every register to its limb (``genOpsWithProduct_returns_to_original``,
     re-checked here by coefficient simulation) and the evaluation identity
     sum_l c_l(2^m) r_l[j] r_l[j'] = 2^{m(j+j')} is re-checked exactly for the rows r_l the schedule
     actually holds at each phaseProduct;
  4. undo 2 and 1 (free the zero qubits, ``SignTruncate``).

Compared with the production ``ToomRecursivePhaseProduct`` call graph, a level adds only Clifford
bookkeeping (``SignExtend``/``SignTruncate`` = multi-target CNOTs, Allocate/Free, Split/Join), so its
QECGatesCost And count is the production body Ands + the leaves' own Ands; rotations are 0.

REUSE. The arithmetic pattern is adapted from ``skeletons/kmy_phaseproduct_skeleton.PhaseProduct``
(partition, shift-and-add into a register, compute / child / uncompute, exact-``Fraction`` child
turns), but the evaluation itself is NOT the skeleton's (integer points, fresh accumulators): it is
the production's in-place signed schedule, so that the counts line up with ``body_T``.

LEAVES. ``leaf.catalytic_leaf(wx, wz, b, tau, ctrl, mixing=, seed=, route=)`` (asymmetric API). Every
leaf position gets its own seed, derived from its path (``derive_seed``), so mixing draws are
independent across leaves. A controlled leaf takes the narrower operand as its x (the ctrl fold costs
wx Ands). Child turns use the BUNDLE's coefficient polynomials (``bundle_compat.coeff_polys``, the
smaller-loss set the census prices). A node whose turn is 0 mod 1 (vanishing Toom weight, or a dyadic
turn made integral) is skipped entirely, as the pricing does (0 Ands); the parent body still runs.
PRICED ROUTE: pass ``route="P"|"Y"`` (all leaves) or ``leaf_routes=BChoice.leaf_routes`` plus the site
``cls`` and root ``res``; each leaf looks up its census key ("leaf", wx, ctrl, cls, res, wz), with res
walked exactly as ``tcount.leaf_census`` walks it (``census_losses``).

``build_call_graph`` is explicit and closed-form (it never decomposes), so large widths are costed
through the same tree as the production; ``build_composite_bloq`` is the real decomposition.
``force_levels`` (testing only) takes a Toom level wherever the tight width shrinks, for the given
number of levels, so the wiring can be checked exhaustively at widths where the cost-optimal rule
would still choose a leaf.
"""
from __future__ import annotations

import functools
import hashlib
import math
from fractions import Fraction
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import attrs
import numpy as np

from qualtran import Bloq, BloqBuilder, DecomposeTypeError, QBit, QInt, QUInt, Register, Signature, SoquetT
from qualtran.bloqs.arithmetic import Add, Negate
from qualtran.bloqs.arithmetic.conversions.sign_extension import SignExtend, SignTruncate
from qualtran.bloqs.bookkeeping import Allocate, Free
from qualtran.bloqs.mcmt import And
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from .. import bundle_compat
from ..tight_width import apply_op, start_state
from . import leaf as _leaf_mod

__all__ = ["CatalyticToomPhaseProduct", "CatalyticLeafClass", "make_leaf", "level_schedule",
           "child_weights", "census_losses", "derive_seed"]


# ============================================================================ schedule data

def _ops_key(ops: Sequence[Dict[str, Any]]) -> tuple:
    return tuple((o["op"], o.get("i"), o.get("dst"), o.get("src"), bool(o.get("negSrc")),
                  int(o.get("shift", 0) or 0), o.get("n")) for o in ops)


def _key_to_op(t: tuple) -> Dict[str, Any]:
    return {"op": t[0], "i": t[1], "dst": t[2], "src": t[3], "negSrc": t[4], "shift": t[5], "n": t[6]}


@functools.lru_cache(maxsize=None)
def _pp_rows(ops_key: tuple, k: int) -> Tuple[Tuple[int, ...], ...]:
    """Coefficient row (over the k limbs) held by the target register at each phaseProduct, in order.
    Also checks that the schedule returns every register to its limb."""
    sigma = start_state(k)
    rows = []
    for t in ops_key:
        op = _key_to_op(t)
        if op["op"] == "phaseProduct":
            rows.append(sigma[int(op["i"])])
            continue
        if op["op"] in ("shiftL", "shiftR"):
            raise NotImplementedError("shiftL/shiftR schedule ops are not wired (none in k<=7 bundles)")
        nxt = apply_op(sigma, op)
        if nxt is None:
            raise ValueError(f"schedule op {op} is not executable")
        sigma = nxt
    if sigma != start_state(k):
        raise ValueError(f"k={k}: the schedule does not return the registers to their limbs")
    return tuple(rows)


def level_schedule(rules, k: int) -> Tuple[tuple, Tuple[Tuple[Fraction, ...], ...]]:
    """(ops_key, coeff_polys) for arity k, read from the SAME bundle the RuleSet prices, and checked
    against the census the production call graph counts (n_add, n_neg, n_add_negsrc, q)."""
    bundle = rules.bundles[k]
    ops = bundle_compat.schedule_view(bundle)["ops"]
    polys = bundle_compat.coeff_polys(bundle.doc)
    d = rules.data[k]
    n_pp = sum(1 for o in ops if o["op"] == "phaseProduct")
    n_add = sum(1 for o in ops if o["op"] == "addScaled")
    n_ns = sum(1 for o in ops if o["op"] == "addScaled" and o.get("negSrc"))
    n_neg = sum(1 for o in ops if o["op"] == "negate")
    if (n_pp, n_add, n_ns, n_neg) != (d.q, d.n_add, d.n_add_negsrc, d.n_neg) or len(polys) != d.q:
        raise ValueError(f"k={k}: schedule ops {(n_pp, n_add, n_ns, n_neg)} / {len(polys)} polys disagree "
                         f"with the priced census {(d.q, d.n_add, d.n_add_negsrc, d.n_neg)}")
    return _ops_key(ops), polys


@functools.lru_cache(maxsize=None)
def _child_weights_cached(ops_key: tuple, polys: tuple, k: int, m: int) -> Tuple[int, ...]:
    B = Fraction(2) ** m
    cs = []
    for poly in polys:
        acc = Fraction(0)
        for c in reversed(poly):
            acc = c + B * acc
        cs.append(acc)
    rows = _pp_rows(ops_key, k)
    if len(rows) != len(cs):
        raise ValueError("phaseProduct count != number of coefficient polynomials")
    for j in range(k):                     # evaluation identity: sum_l c_l r_l[j] r_l[j'] = B^(j+j')
        for jj in range(k):
            s = sum((c * r[j] * r[jj] for c, r in zip(cs, rows)), Fraction(0))
            if s != B ** (j + jj):
                raise ValueError(f"k={k}, m={m}: Toom identity fails at limbs ({j},{jj}): {s}")
    for c in cs:
        if c.denominator != 1:
            raise ValueError(f"k={k}, m={m}: c_l(2^m) = {c} is not an integer; the mod-1 reduction of "
                             "child turns would be unsound")
    return tuple(int(c) for c in cs)


def child_weights(rules, k: int, m: int) -> Tuple[int, ...]:
    """The integers c_l(2^m), l in phaseProduct order, after the exact identity check."""
    ops_key, polys = level_schedule(rules, k)
    return _child_weights_cached(ops_key, tuple(polys), k, m)


def _v2(c: int) -> Optional[int]:
    return None if c == 0 else (abs(c) & -abs(c)).bit_length() - 1


@functools.lru_cache(maxsize=None)
def _mirror_weights(k: int, m: int) -> Tuple[int, ...]:
    from ..mirrors import coeff_poly as CP, schedule as SC
    polys = CP.coeff_polys(k, SC.raw_table(k)[0]["points"])
    out = []
    for p in polys:
        v = CP.poly_eval(list(p), Fraction(2) ** m)
        if v.denominator != 1:
            raise ValueError(f"mirror Toom weight not an integer at k={k}, m={m}")
        out.append(int(v))
    return tuple(out)


def census_losses(rules, k: int, m: int) -> Tuple[Optional[int], ...]:
    """Resolution loss assigned to each child l (phaseProduct order) for the census key, exactly as
    ``tcount.leaf_census.child_losses`` combines them: the bundle's and the frozen mirror's sorted loss
    lists are merged elementwise by the SMALLER loss (None = vanishing weight). The merged list is handed
    to the bundle children in their own sorted-loss order, so each child's census `res` is >= its true
    resolution bound (a looser, still valid upper bound). Child TURNS always use the bundle weights."""
    own = [_v2(c) for c in child_weights(rules, k, m)]
    key = lambda v: math.inf if v is None else v
    lists = [sorted(own, key=key), sorted((_v2(c) for c in _mirror_weights(k, m)), key=key)]
    merged = [a if b is None else (b if a is None else min(a, b)) for a, b in zip(*lists)]
    order = sorted(range(len(own)), key=lambda l: key(own[l]))
    out: List[Optional[int]] = [None] * len(own)
    for rank, l in enumerate(order):
        out[l] = merged[rank]
    return tuple(out)


def derive_seed(seed: int, l: int) -> int:
    """Deterministic child seed (independent streams per tree position)."""
    h = hashlib.sha256(f"{int(seed)}/{int(l)}".encode()).digest()
    return int.from_bytes(h[:4], "little")


# ============================================================================ leaves

def make_leaf(wx: int, wz: int, b: int, tau: Fraction, ctrl: bool, *, mixing: bool = True, seed: int = 0,
              route: Optional[str] = None):
    """The concrete leaf: ``leaf.catalytic_leaf(wx, wz, b, tau, ctrl, mixing=, seed=, route=)``."""
    return _leaf_mod.catalytic_leaf(wx, wz, b, tau, bool(ctrl), mixing=mixing, seed=seed, route=route)


def _tau(t) -> Optional[Fraction]:
    if t is None:
        return None
    if isinstance(t, bool) or not isinstance(t, (Fraction, int)):
        raise TypeError(f"tau must be an exact fractions.Fraction, got {type(t).__name__}")
    return Fraction(t) % 1


def _opt_int(v):
    return None if v is None else int(v)


def _kind(res: Optional[int]) -> str:
    return "generic" if res is None else "dyadic"


def _regs(ctrl: bool, wx: int, wz: int, b: int) -> Signature:
    regs = [Register("ctrl", QBit())] if ctrl else []
    return Signature(regs + [Register("x", QInt(wx)), Register("z", QInt(wz)),
                             Register("catalyst", QUInt(b))])


@attrs.frozen
class CatalyticLeafClass(Bloq):
    """CLASS-level catalytic leaf: equality / hash carry only (wx, wz, b, kind, res, ctrl, route, mixing).

    * ``build_call_graph``: exactly ``leaf.leaf_cost_class(...).ands`` Ands (and as many And^dagger,
      0 T); no rotations -- what the pricing charges (``budget.price_at``).
    * ``build_composite_bloq``: the CONCRETE ``leaf.catalytic_leaf`` for the carried ``tau`` / ``seed``
      (eq=False), on the class's route, so the built leaf is the priced one. Its QECGatesCost is <= the
      class count (equal for dyadic r <= b, and for generic unless a constant rounds / draws / truncates
      to 0) -- ``leaf.leaf_cost_class``'s documented semantics, re-checked in the tests.
    Registers as the leaf: ``[ctrl: QBit], x: QInt(wx), z: QInt(wz), catalyst: QUInt(b)``."""
    wx: int
    wz: int
    b: int
    kind: str
    res: Optional[int] = attrs.field(converter=_opt_int)
    ctrl: bool
    route: Optional[str] = None
    mixing: bool = True
    tau: Optional[Fraction] = attrs.field(default=None, eq=False, converter=_tau)
    seed: int = attrs.field(default=0, eq=False)

    @property
    def signature(self) -> Signature:
        return _regs(self.ctrl, self.wx, self.wz, self.b)

    def class_cost(self):
        return _leaf_mod.leaf_cost_class(self.wx, self.wz, self.b, self.kind, self.res, bool(self.ctrl),
                                         bool(self.mixing), route=self.route)

    def concrete(self):
        if self.tau is None:
            raise DecomposeTypeError("a class-only leaf (tau=None) has no concrete circuit")
        return make_leaf(self.wx, self.wz, self.b, self.tau, self.ctrl, mixing=self.mixing,
                         seed=self.seed, route=self.class_cost().route)

    def build_composite_bloq(self, bb: BloqBuilder, **soqs) -> Dict[str, SoquetT]:
        return bb.add_d(self.concrete(), **soqs)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        n = int(self.class_cost().ands)
        return {And(): n, And().adjoint(): n} if n else {}

    def __str__(self) -> str:
        r = "" if self.res is None else f",r={self.res}"
        return f"CatLeafClass({self.wx}x{self.wz},b={self.b},{self.kind}{r}{',ctrl' if self.ctrl else ''})"


# ============================================================================ the phase product

#: per-process memo of concrete children, keyed by (id(rules), node, tau, seed): node equality is
#: CLASS-level, so the concrete turn / seed are part of the key.
_CLASS_KIDS: Dict[tuple, list] = {}


@attrs.frozen
class CatalyticToomPhaseProduct(Bloq):
    """exp(2 pi i tau s(x) s(z)) via Toom-Cook (production versionB/kmy tree) with catalytic leaves.

    Registers: ``[ctrl: QBit], x: QInt(wx), z: QInt(wz), catalyst: QUInt(b)``.

    SYMBOLIC COSTING. Equality / hash are CLASS-level: (wx, wz, k, b, ctrl, cls, res, route, leaf_routes,
    mixing, stop_tsr, force_levels). ``tau`` and ``seed`` are eq=False and never read by the call graph:
    identical children of a level merge into ONE callee with multiplicity (as the production), and the
    leaves are ``CatalyticLeafClass`` (priced by ``leaf.leaf_cost_class``). A costing walk visits
    O(depth x distinct resolutions) nodes, independent of n. ``tau=None`` gives a cost-only bloq.
    ``res`` is the census resolution bound of this node's turn (None = generic); children lose
    ``census_losses`` bits. CERTAINLY-zero children (vanishing Toom weight, or res = 0) are skipped, as
    the pricing skips them; the parent body still runs.

    The decomposition uses the concrete ``tau`` / per-path ``seed`` (it needs ``tau``)."""
    wx: int
    wz: int
    k: int
    tau: Optional[Fraction] = attrs.field(eq=False, converter=_tau)
    b: int
    ctrl: bool
    rules: Any = attrs.field(eq=False, repr=False)
    stop_tsr: float = attrs.field(default=11.0, kw_only=True)
    mixing: bool = attrs.field(default=True, kw_only=True)
    seed: int = attrs.field(default=0, kw_only=True, eq=False)
    force_levels: int = attrs.field(default=0, kw_only=True)
    #: PRICED ROUTE. `route` forces "P"/"Y" on every leaf; `leaf_routes` is `BChoice.leaf_routes` (census
    #: key -> "P" | "Y" | "id"), looked up per leaf by ("leaf", wx, ctrl, cls, res, wz). A missing key raises.
    route: Optional[str] = attrs.field(default=None, kw_only=True)
    leaf_routes: Optional[tuple] = attrs.field(default=None, kw_only=True,
                                              converter=lambda v: None if v is None else tuple(
                                                  sorted(dict(v).items(), key=repr)))
    cls: str = attrs.field(default="generic", kw_only=True)
    res: Optional[int] = attrs.field(default=None, kw_only=True, converter=_opt_int)

    def __attrs_post_init__(self):
        if self.wx < 1 or self.wz < 1 or self.b < 1:
            raise ValueError("widths and b must be >= 1")
        if self.k not in self.rules.data:
            raise ValueError(f"no bundle loaded for k={self.k}")

    @classmethod
    def from_turn(klass, wx: int, wz: int, k: int, tau: Fraction, b: int, ctrl: bool, rules, **kw):
        """Root with res = the exact resolution of tau (None if generic) unless given."""
        kw.setdefault("res", _leaf_mod.turn_resolution(tau))
        return klass(wx, wz, k, tau, b, ctrl, rules, **kw)

    # ------------------------------------------------------------------ structure
    @property
    def w(self) -> int:
        return max(self.wx, self.wz)

    @property
    def signature(self) -> Signature:
        return _regs(self.ctrl, self.wx, self.wz, self.b)

    def decision(self) -> Optional[Tuple[int, int]]:
        """None -> leaf; (k, W2) -> Toom level. The production rule unless ``force_levels``."""
        w = self.w
        if self.force_levels > 0:
            W2 = self.rules.data[self.k].next_width(w, "tight", True)
            if W2 < w:
                return self.k, W2
        dec = self.rules.decision(w, "B", self.k, "kmy", True, float(self.stop_tsr))
        if dec is not None:
            assert dec[0] == self.k, "version B must recurse at k"
        return dec

    def _geometry(self, dec) -> Tuple[int, int, int]:
        k, W2 = dec
        m = -(-self.w // k)
        P = k * m
        if W2 < m:
            raise ValueError(f"tight width W2={W2} < limb width m={m}")
        return m, P, W2

    def _child_res(self, loss: Optional[int]) -> Optional[int]:
        """Census resolution bound of a child (``tcount.leaf_census.leaf_resolutions``)."""
        if self.res is None:
            return None
        if loss is None or self.res == 0:
            return 0
        return max(0, self.res - loss)

    def children(self) -> List[Optional["CatalyticToomPhaseProduct"]]:
        """One entry per phaseProduct op (circuit order); None for a certainly-zero child (skipped).
        CONCRETE (carries each child's exact turn / seed) -- for the decomposition and the checks only.
        Not memoised globally: a concrete turn is an ~n-bit Fraction, and a process-wide memo keyed by it
        kept every one alive (the 2026-09-24 n=50000 out-of-memory)."""
        return self._children()

    def class_children(self) -> List[Optional["CatalyticToomPhaseProduct"]]:
        """The same children at CLASS level (tau=None, seed=0): what the call graph uses. Memoised on the
        class-level node, so costing never touches a concrete turn and is independent of n's bit size."""
        key = (id(self.rules), self)
        if key not in _CLASS_KIDS:
            _CLASS_KIDS[key] = attrs.evolve(self, tau=None, seed=0)._children()
        return _CLASS_KIDS[key]

    def _children(self):
        dec = self.decision()
        if dec is None:
            return []
        m, _, W2 = self._geometry(dec)
        cs = child_weights(self.rules, self.k, m)
        losses = census_losses(self.rules, self.k, m)
        out = []
        for l, c in enumerate(cs):
            r = self._child_res(losses[l])
            if c == 0 or r == 0:
                out.append(None)
                continue
            out.append(attrs.evolve(self, wx=W2, wz=W2, tau=None if self.tau is None else (self.tau * c) % 1,
                                    seed=derive_seed(self.seed, l), res=r,
                                    force_levels=max(0, self.force_levels - 1)))
        return out

    # -- leaf
    @property
    def swap(self) -> bool:
        """A controlled leaf takes the NARROWER operand as its x (the ctrl fold costs wx Ands); the
        product is symmetric, so x and z are simply wired the other way round."""
        return bool(self.ctrl) and self.wx > self.wz

    def leaf_shape(self) -> Tuple[int, int]:
        return (self.wz, self.wx) if self.swap else (self.wx, self.wz)

    def census_key(self) -> tuple:
        lx, lz = self.leaf_shape()
        return ("leaf", lx, bool(self.ctrl), self.cls, self.res, lz)

    def leaf_route(self) -> Optional[str]:
        """Forced `route`, else the priced one from `leaf_routes` ("id" -> None: priced 0), else None."""
        if self.route is not None:
            return self.route
        if self.leaf_routes is not None:
            d = dict(self.leaf_routes)
            if self.census_key() not in d:
                raise KeyError(f"leaf {self.census_key()} is not in the priced leaf_routes")
            r = d[self.census_key()]
            return None if r == "id" else r
        return None

    def leaf_class(self) -> CatalyticLeafClass:
        lx, lz = self.leaf_shape()
        return CatalyticLeafClass(lx, lz, self.b, _kind(self.res), self.res, bool(self.ctrl),
                                  route=self.leaf_route(), mixing=self.mixing, tau=self.tau, seed=self.seed)

    def is_identity(self) -> bool:
        """CLASS-certain identity: res = 0 (integral turn), or a leaf whose class price is 0 Ands."""
        if self.res == 0:
            return True
        return self.decision() is None and self.leaf_class().class_cost().ands == 0

    # -- enumeration (concrete)
    def leaf_nodes(self) -> Iterator["CatalyticToomPhaseProduct"]:
        """Every non-skipped leaf position (one per tree position; concrete tau / seed)."""
        if self.is_identity():
            return
        if self.decision() is None:
            yield self
            return
        for ch in self.children():
            if ch is not None:
                yield from ch.leaf_nodes()

    def leaves(self) -> Iterator[Any]:
        """The concrete leaf bloqs (needs tau)."""
        for n in self.leaf_nodes():
            yield n.leaf_class().concrete()

    def leaf_costs(self) -> List[Any]:
        """Concrete ``leaf.leaf_cost`` per leaf position (on the class route)."""
        out = []
        for n in self.leaf_nodes():
            lc = n.leaf_class()
            out.append(_leaf_mod.leaf_cost(lc.wx, lc.wz, self.b, lc.tau, lc.ctrl, mixing=lc.mixing,
                                           seed=lc.seed, route=lc.class_cost().route))
        return out

    def class_leaf_costs(self) -> List[Any]:
        return [n.leaf_class().class_cost() for n in self.leaf_nodes()]

    def error_bound(self) -> float:
        """Per-realisation operator-norm error (radians): sum of the concrete leaves' ``error_bound()``
        (the Toom body is exact arithmetic)."""
        return float(sum(lf.error_bound() for lf in self.leaves()))

    def error_budget(self) -> Dict[str, float]:
        """Summed concrete leaf ``err_op`` / ``err_diamond`` (radians)."""
        cs = self.leaf_costs()
        return {"err_op": float(sum(c.err_op for c in cs)),
                "err_diamond": float(sum(c.err_diamond for c in cs))}

    # ------------------------------------------------------------------ classical model
    def realised_turn(self, x: int, z: int, c: int = 1) -> Fraction:
        """V(x, z)/2^b, the turn the circuit adds to the catalyst, from the concrete leaves' closed forms
        and the schedule run on integers (x, z are signed values)."""
        if (self.ctrl and not c) or self.is_identity():
            return Fraction(0)
        dec = self.decision()
        if dec is None:
            if self.swap:
                x, z = z, x
            return Fraction(self.leaf_class().concrete().realised_turn(x, z)) % 1
        m, P, W2 = self._geometry(dec)
        k = self.k
        ops_key, _ = level_schedule(self.rules, k)

        def limbs(v):
            u = v % (1 << P)
            out = [(u >> (j * m)) & ((1 << m) - 1) for j in range(k)]
            if out[-1] >> (m - 1):
                out[-1] -= 1 << m
            return out

        X, Z = limbs(x), limbs(z)
        kids = self.children()
        tot, l = Fraction(0), 0
        lim = 1 << (W2 - 1)
        for t in ops_key:
            op = _key_to_op(t)
            if op["op"] == "phaseProduct":
                i = int(op["i"])
                if kids[l] is not None:
                    tot += kids[l].realised_turn(X[i], Z[i], c)
                l += 1
            elif op["op"] == "negate":
                i = int(op["i"])
                X[i], Z[i] = -X[i], -Z[i]
            else:
                d, s, sh = int(op["dst"]), int(op["src"]), op["shift"]
                sg = -1 if op["negSrc"] else 1
                X[d] += sg * (X[s] << sh)
                Z[d] += sg * (Z[s] << sh)
            assert all(-lim <= v < lim for v in X + Z), "tight width violated"
        return tot % 1

    # ------------------------------------------------------------------ decomposition
    def build_composite_bloq(self, bb: BloqBuilder, **soqs) -> Dict[str, SoquetT]:
        if self.tau is None:
            raise DecomposeTypeError("a class-only CatalyticToomPhaseProduct (tau=None) has no concrete "
                                     "decomposition; construct it with a concrete tau")
        ctrl = soqs.get("ctrl")
        x, z, cat = soqs["x"], soqs["z"], soqs["catalyst"]
        dec = self.decision()
        if self.is_identity():
            pass
        elif dec is None:
            ctrl, x, z, cat = self._build_leaf(bb, ctrl, x, z, cat)
        else:
            ctrl, x, z, cat = self._build_level(bb, dec, ctrl, x, z, cat)
        out = {"x": x, "z": z, "catalyst": cat}
        if self.ctrl:
            out["ctrl"] = ctrl
        return out

    @staticmethod
    def _add(bb, bloq, ctrl, x, z, cat, has_ctrl):
        if has_ctrl:
            o = bb.add_d(bloq, ctrl=ctrl, x=x, z=z, catalyst=cat)
            return o["ctrl"], o["x"], o["z"], o["catalyst"]
        o = bb.add_d(bloq, x=x, z=z, catalyst=cat)
        return ctrl, o["x"], o["z"], o["catalyst"]

    def _build_leaf(self, bb, ctrl, x, z, cat):
        if self.swap:
            x, z = z, x
        ctrl, x, z, cat = self._add(bb, self.leaf_class(), ctrl, x, z, cat, self.ctrl)
        if self.swap:
            x, z = z, x
        return ctrl, x, z, cat

    # -- limb plumbing
    @staticmethod
    def _to_limbs(bb, reg, k: int, m: int, W2: int) -> List[Any]:
        lsb = list(bb.split(reg))[::-1]
        out = []
        for j in range(k):
            bits = lsb[j * m:(j + 1) * m]
            if j < k - 1:
                zeros = [bb.allocate(dtype=QBit()) for _ in range(W2 - m)]
                out.append(bb.join(np.array(zeros + bits[::-1]), dtype=QInt(W2)))
            else:
                top = bb.join(np.array(bits[::-1]), dtype=QInt(m))
                if W2 > m:
                    top = bb.add(SignExtend(QInt(m), QInt(W2)), x=top)
                out.append(top)
        return out

    @staticmethod
    def _from_limbs(bb, regs: List[Any], k: int, m: int, W2: int, P: int):
        lsb: List[Any] = []
        for j in range(k):
            if j < k - 1:
                be = list(bb.split(regs[j]))
                for q in be[:W2 - m]:
                    bb.free(q)
                lsb += be[W2 - m:][::-1]
            else:
                top = regs[j]
                if W2 > m:
                    top = bb.add(SignTruncate(QInt(W2), QInt(m)), x=top)
                lsb += list(bb.split(top))[::-1]
        return bb.join(np.array(lsb[::-1]), dtype=QInt(P))

    @staticmethod
    def _add_scaled(bb, src, dst, shift: int, neg: bool, W2: int):
        """dst += (±src) << shift  (mod 2^W2): Add(QInt(W2)); negSrc as Negate·Add·Negate."""
        if neg:
            src = bb.add(Negate(QInt(W2)), x=src)
        if shift == 0:
            src, dst = bb.add(Add(QInt(W2)), a=src, b=dst)
        else:
            be = list(bb.split(src))                      # be[0] = MSB
            zeros = [bb.allocate(dtype=QBit()) for _ in range(shift)]
            sh = bb.join(np.array(be[shift:] + zeros), dtype=QInt(W2))     # (src mod 2^(W2-s)) << s
            sh, dst = bb.add(Add(QInt(W2)), a=sh, b=dst)
            sb = list(bb.split(sh))
            be[shift:] = sb[:W2 - shift]
            for q in sb[W2 - shift:]:
                bb.free(q)
            src = bb.join(np.array(be), dtype=QInt(W2))
        if neg:
            src = bb.add(Negate(QInt(W2)), x=src)
        return src, dst

    def _build_level(self, bb, dec, ctrl, x, z, cat):
        k = self.k
        m, P, W2 = self._geometry(dec)
        ops_key, _ = level_schedule(self.rules, k)
        kids = self.children()
        if P > self.wx:
            x = bb.add(SignExtend(QInt(self.wx), QInt(P)), x=x)
        if P > self.wz:
            z = bb.add(SignExtend(QInt(self.wz), QInt(P)), x=z)
        X = self._to_limbs(bb, x, k, m, W2)
        Z = self._to_limbs(bb, z, k, m, W2)
        l = 0
        for t in ops_key:
            op = _key_to_op(t)
            if op["op"] == "phaseProduct":
                i = int(op["i"])
                if kids[l] is not None and not kids[l].is_identity():
                    ctrl, X[i], Z[i], cat = self._add(bb, kids[l], ctrl, X[i], Z[i], cat, self.ctrl)
                l += 1
            elif op["op"] == "negate":
                i = int(op["i"])
                X[i] = bb.add(Negate(QInt(W2)), x=X[i])
                Z[i] = bb.add(Negate(QInt(W2)), x=Z[i])
            elif op["op"] == "addScaled":
                d, s = int(op["dst"]), int(op["src"])
                for R in (X, Z):
                    R[s], R[d] = self._add_scaled(bb, R[s], R[d], op["shift"], op["negSrc"], W2)
            else:
                raise NotImplementedError(op)
        x = self._from_limbs(bb, X, k, m, W2, P)
        z = self._from_limbs(bb, Z, k, m, W2, P)
        if P > self.wx:
            x = bb.add(SignTruncate(QInt(P), QInt(self.wx)), x=x)
        if P > self.wz:
            z = bb.add(SignTruncate(QInt(P), QInt(self.wz)), x=z)
        return ctrl, x, z, cat

    # ------------------------------------------------------------------ call graph (class level, O(depth))
    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        counts: Dict[Bloq, int] = {}

        def inc(bl, n=1):
            if n:
                counts[bl] = counts.get(bl, 0) + n

        node = self if self.tau is None else attrs.evolve(self, tau=None, seed=0)   # class level only
        if node.is_identity():
            return counts
        dec = node.decision()
        if dec is None:
            inc(node.leaf_class())
            return counts
        k = self.k
        m, P, W2 = self._geometry(dec)
        ops_key, _ = level_schedule(self.rules, k)
        for ch in node.class_children():
            if ch is not None and not ch.is_identity():
                inc(ch)                                   # class-equal children merge (multiplicity)
        for wo in (self.wx, self.wz):
            if P > wo:
                inc(SignExtend(QInt(wo), QInt(P)))
                inc(SignTruncate(QInt(P), QInt(wo)))
        sides = 2
        inc(Allocate(QBit()), sides * (k - 1) * (W2 - m))
        inc(Free(QBit()), sides * (k - 1) * (W2 - m))
        if W2 > m:
            inc(SignExtend(QInt(m), QInt(W2)), sides)
            inc(SignTruncate(QInt(W2), QInt(m)), sides)
        d = self.rules.data[k]
        inc(Add(QInt(W2)), sides * d.n_add)
        inc(Negate(QInt(W2)), sides * (d.n_neg + 2 * d.n_add_negsrc))
        n_shift = sum(_key_to_op(t)["shift"] for t in ops_key if t[0] == "addScaled")
        inc(Allocate(QBit()), sides * n_shift)
        inc(Free(QBit()), sides * n_shift)
        return counts

    def __str__(self) -> str:
        r = "" if self.res is None else f",r={self.res}"
        return f"CatToomPP(k={self.k},{self.wx}x{self.wz},b={self.b}{',ctrl' if self.ctrl else ''},{self.cls}{r})"
