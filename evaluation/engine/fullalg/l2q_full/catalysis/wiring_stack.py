"""Merged catalysis wired through the production stages: QFT, MULT, MODMUL, MODEXP, SHOR.

Every stage here is the production bloq (`bloqs/qft.py` LeanQFT versionB, `bloqs/multiplier.py`
ToomCookMultiplier, `bloqs/modmul.py` LeanCtrlModMul, `bloqs/modexp.py` LeanModExp,
`bloqs/orderfinding.py` LeanOrderFinding) with each rotation-bearing piece swapped for its catalytic
counterpart. All of them share ONE catalyst register ``catalyst: QUInt(b)``, which is threaded through
as a THRU register. It is prepared ONCE, by `Catalyzed`, at the top of whichever stage is priced.

COMPOSITION (production -> catalytic)
------------------------------------
QFT     LeanQFT(w) = QFT(right) ; twiddle(left, right, 2pi/2^w) ; QFT(left) ; RadixReverse
        -> CatalyticQFT(w, b): the same Cooley-Tukey recursion (left = w//2, right = w - left). Every
           twiddle is `twiddle.MergedTwiddle(left, right, t=w, b)`, kept undecomposed (no Toom).
           RadixReverse is a wire relabelling (zero gates). Production prices it as 3*(left//2)
           TwoBitSwaps, so the two differ in Cliffords only.
IQFT    CatalyticQFT(w, b, inverse=True) = X^{(x)b}(catalyst) ; reversed body ; X^{(x)b}(catalyst).
        X on every catalyst qubit maps |Phi_b> to its complex conjugate (up to a global phase), so
        every addition inside the reversed body kicks back the conjugate phase. That is the IQFT's
        -1 site sign, paid as 2b Cliffords. Equivalently, subtraction is ~(~k + v), so the body's adders
        subtract. The reversed body's sub-QFTs are `bare=True`, which stops the X layer from being
        applied again at every level.
MULT    ToomCookMultiplier(n, w_out=n) = H^{w_out} ; PP(x, out; tau = a/2^{w_out}) ; IQFT(w_out)
        -> CatalyticMultiplier: PP = wiring_pp.CatalyticToomPhaseProduct (ctrl=False), IQFT catalytic.
MODMUL  LeanCtrlModMul (U1..U5)
        U1  H^{ww}(work) ; C-PP(ctrl; data, work; tau1 = ((c-1) mod N)/N) ; IQFT(work)
        U2  QFT(dataCarry) ; PP(work, dataCarry; tau2 = N/2^(ww+wc)) ; IQFT(dataCarry)
        U3  GreaterThanConstant(wc, N)(dataCarry -> flag) ; AddK(QUInt(wc), -N).controlled()(flag)
        U4  LessThanConstant(wc + ww, N)(dataCarry||work -> flag)
        U5  adjoint of U1 on (dataCarry, work): QFT(work) ; C-PP(ctrl; dataCarry, work; -tau5) ; H^{ww}
            with tau5 = step5Constant(c, N)/N. The adjoint's -1 sign is passed as the turn
            (-tau5) mod 1, because x*z is an integer.
        U3 and U4 are the production bloqs, with the same counts. dataCarry = [carry (MSB)] + data.
        Every PP is square at size = max(a, b) + 1 with zero-padded operands. That is the production
        `_pp` convention (`pp_pad="square"`). `pp_pad="tight"` passes (a + 1, b + 1) instead.
MODEXP  LeanModExp = prod_e C-ModMul(c_e) controlled on exponent bit e (tbits = n_steps = 2n)
        -> CatalyticModExp: `schedule="representative"` (production's default) prices tbits copies
           of the step-0 multiplier c = a mod N. `schedule="real"` builds c_e = a^(2^e) mod N step
           by step. Production also counts one X (|1> init) inside the modexp; that X is in the
           order finding here (initY1), so the modexp carries one Clifford fewer.
SHOR    LeanOrderFinding = H^L(x) ; X(data LSB) ; modexp ; IQFT(x), L = exp_width = 2n
        -> CatalyticOrderFinding: the readout IQFT is catalytic. carry/work/flag are allocated
           and freed inside, as ancillae.

WHERE b AND THE CATALYST ENTER
------------------------------
* b is a field of every class and is passed down unchanged: to every MergedTwiddle, every
  CatalyticToomPhaseProduct (and so every leaf) and every sub-QFT. No class chooses b internally.
* `catalytic_bloq(stage, ...)` / `<Stage>.for_budget(n, k, delta, partition, rules=...)` builds the
  structure at a probe b and takes the census of THAT structure (`stack_census`: ("tw", r1, r2, t,
  0, 0) per MergedTwiddle, ("leaf", wx, ctrl, cls, res, wz) per built leaf with the EXACT resolution
  of its turn; the keys do not depend on b). It then calls `budget.choose_b(census, partition,
  mixing=...)` (THE shared rule, also behind tcount `price_merged` v2) and rebuilds at the chosen b.
  CERTIFICATION (class level): `stack_error` sums the exact twiddle row
  errors and, per leaf, the CLASS error of a `CatalyticLeafClass` (err_op + err_diamond/2) -- the same
  numbers `choose_b` used, so a bump cannot trigger from the leaves; it is a class bound, >= every
  concrete leaf's error (simulated), not an independent certificate of a concrete circuit. It must
  meet `partition.catalysis_budget`, otherwise b is raised one bit at a time (`.meta["bumps"]`).
  MIXED P KICKS: err_diamond certifies the channel AVERAGED over draws, i.e. only
  under randomised compiling -- fresh, independent draws per shot, per kick and per step. A circuit
  with draws fixed by a seed is one unitary with FIRST-order error; for it use mixing=False
  (deterministic kicks, operator norm). The `schedule="representative"` modexp reuses one modmul
  (and its draws) across all steps: it is a COST representative only, not a certified circuit.
* `Catalyzed(inner, eps=BChoice.catalyst_prep_eps)` decomposes as `CatalystPrep(b, eps)` ->
  catalyst -> inner(catalyst=...). The catalyst leaves as a RIGHT register: intact, never
  uncomputed, reusable. It is the ONLY CatalystPrep in the whole call graph.
* eta = partition.structure_budget / n^2, as the figures use it (5e-3 / n^2 at delta = 1e-2, in both v1 and v2).
* `v1_partition(delta)` reproduces the pre-2026-09-23 contract: `spec.choose_b_v1`, prep at
  1e-3 delta_rot. It is kept for reconciliation against the figures as they stand.
* mixing defaults to True (decision D5: P-route kicks are unbiased random roundings with per-leaf
  seeds; rows stay deterministic). The same flag goes to the leaves and to choose_b.

COSTING
-------
Every class has an explicit `build_call_graph` that names only whole sub-bloqs (sub-QFTs,
MergedTwiddles, the PP, comparators), so QECGatesCost runs at production widths without
decomposing to bits. `build_composite_bloq` is the real wiring, and it must agree with the call
graph callee for callee (bookkeeping excluded; tested). `stage_T(bloq)` counts 4 T per And, plus the
QECGatesCost T gates, plus the b - 3 prep rotations at the partition's prep tolerance. In v2 that
equals `spec.catalyst_prep_T` including its +1 T, because the 1 T is already a QECGatesCost T gate.
`and_breakdown` splits the Ands into twiddle / leaf / Toom body / comparators. It sets each built piece
next to what the v1 and v2 pricing charge for it, and the signed deltas come from that comparison.
"""
from __future__ import annotations

import math
from fractions import Fraction
from functools import cached_property
from typing import Any, Dict, List, Mapping, Optional, Tuple

import attrs
import numpy as np

import l2q_full  # noqa: F401  (binds the frozen Qualtran tree before `qualtran`)
from qualtran import Bloq, BloqBuilder, QBit, QInt, QUInt, Register, Side, Signature, SoquetT
from qualtran.bloqs.arithmetic import AddK, GreaterThanConstant, LessThanConstant
from qualtran.bloqs.basic_gates import Hadamard, XGate
from qualtran.resource_counting import (BloqCountDictT, CostKey, QECGatesCost, SympySymbolAllocator,
                                        get_bloq_callee_counts, get_cost_value)

from .catalyst import CatalystPrep
from .twiddle import MergedTwiddle

__all__ = [
    "CatalyticQFT", "CatalyticMultiplier", "CatalyticCtrlModMul", "CatalyticModExp",
    "CatalyticOrderFinding", "CatalyticModMulStep", "Catalyzed", "catalytic_bloq", "stack_census", "stack_choose_b",
    "stage_T", "stack_error", "and_breakdown", "default_partition", "v1_partition", "qft_error_units", "STAGES", "PROBE_B", "representative_mult_constant",
]

STAGES = ("QFT", "MULT", "MODMUL", "MODEXP", "SHOR")
#: b used only to BUILD the structure whose census picks the real b (census keys are b-free).
PROBE_B = 32


# ============================================================================= wiring helpers

def _msb_bits(bb: BloqBuilder, soq) -> List:
    return list(bb.split(soq))


def _join(bb: BloqBuilder, bits: List, dtype):
    return bb.join(np.array(bits), dtype=dtype)


def _h_one(bb, soq):
    bits = _msb_bits(bb, soq)
    bits = [bb.add(Hadamard(), q=q) for q in bits]
    return _join(bb, bits, QUInt(1))


def _x_all(bb: BloqBuilder, soq, w: int):
    bits = [bb.add(XGate(), q=q) for q in _msb_bits(bb, soq)]
    return _join(bb, bits, QUInt(w))


def _routes(v) -> Optional[tuple]:
    """BChoice.leaf_routes (dict) -> the hashable sorted tuple CatalyticToomPhaseProduct stores."""
    if v is None:
        return None
    return tuple(sorted(dict(v).items(), key=repr))


def _pp_bloq(wx: int, wz: int, k: int, tau: Fraction, b: int, ctrl: bool, rules, stop_tsr: float,
             mixing: bool, seed: int, cls: str = "generic", res: Optional[int] = None,
             leaf_routes: Optional[tuple] = None) -> Bloq:
    """wiring_pp.CatalyticToomPhaseProduct (registers [ctrl], x QInt(wx), z QInt(wz), catalyst QUInt(b)).
    Its equality / call graph are CLASS-level (tau and seed are eq=False), so a stage's call graph never
    depends on the concrete angle; `cls` / `res` are the production census's, so each leaf position's
    census key -- and its priced route in `leaf_routes` -- is the figures'."""
    from .wiring_pp import CatalyticToomPhaseProduct
    return CatalyticToomPhaseProduct(int(wx), int(wz), int(k), Fraction(tau), int(b), bool(ctrl), rules,
                                     stop_tsr=float(stop_tsr), mixing=bool(mixing), seed=int(seed),
                                     cls=str(cls), res=None if res is None else int(res),
                                     leaf_routes=leaf_routes)


def _derive_seed(seed: int, tag: int) -> int:
    from .wiring_pp import derive_seed
    return derive_seed(int(seed), int(tag))


def _pp_widths(a: int, c: int, pad: str) -> Tuple[int, int]:
    """Operand widths handed to the PP. 'square': production `_pp` (size = max + 1 on both sides);
    'tight': each side zero-extended by one bit (the spec's non-negativity extension only)."""
    if pad == "square":
        s = max(a, c) + 1
        return s, s
    if pad == "tight":
        return a + 1, c + 1
    raise ValueError(f"pp_pad={pad!r} (square | tight)")


def _apply_pp(bb: BloqBuilder, pp: Bloq, x_bits: List, z_bits: List, cat, ctrl=None):
    """Zero-pad two MSB-first bit lists to the PP's (wx, wz), apply it and strip the pads.
    Returns (x_bits, z_bits, catalyst, ctrl)."""
    wx, wz = int(pp.wx), int(pp.wz)
    px = [bb.allocate(dtype=QBit()) for _ in range(wx - len(x_bits))]
    pz = [bb.allocate(dtype=QBit()) for _ in range(wz - len(z_bits))]
    soqs = {"x": _join(bb, px + list(x_bits), QInt(wx)), "z": _join(bb, pz + list(z_bits), QInt(wz)),
            "catalyst": cat}
    if ctrl is not None:
        soqs["ctrl"] = ctrl
    out = bb.add_d(pp, **soqs)
    xb, zb = _msb_bits(bb, out["x"]), _msb_bits(bb, out["z"])
    for q in xb[:len(px)] + zb[:len(pz)]:
        bb.free(q)
    return xb[len(px):], zb[len(pz):], out["catalyst"], out.get("ctrl")


# ============================================================================= QFT

@attrs.frozen
class CatalyticQFT(Bloq):
    """QFT_w |x> = 2^{-w/2} sum_y exp(+2 pi i x y / 2^w) |y> (register r, QUInt(w)), with every twiddle
    a MergedTwiddle into the shared catalyst. `inverse=True` gives the IQFT (X-conjugated catalyst,
    reversed order). `bare` is internal: an inverse body without its own X layer.

    Exact when b >= w (no twiddle row rounds). Otherwise the operator-norm error, with the catalyst in
    |Phi_b>, is <= qft_error_units(w, b) * pi * 2^-b."""
    w: int
    b: int
    inverse: bool = False
    bare: bool = False

    def __attrs_post_init__(self):
        if self.w < 1 or self.b < 1:
            raise ValueError("CatalyticQFT needs w >= 1, b >= 1")

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("r", QUInt(self.w)), Register("catalyst", QUInt(self.b))])

    @property
    def halves(self) -> Tuple[int, int]:
        left = self.w // 2
        return left, self.w - left

    def _sub(self, w: int) -> "CatalyticQFT":
        return attrs.evolve(self, w=w, bare=self.inverse)

    def twiddle(self) -> MergedTwiddle:
        left, right = self.halves
        return MergedTwiddle(left, right, self.w, self.b)

    def build_composite_bloq(self, bb: BloqBuilder, r, catalyst) -> Dict[str, SoquetT]:
        if self.inverse and not self.bare:
            catalyst = _x_all(bb, catalyst, self.b)
            r, catalyst = bb.add(attrs.evolve(self, bare=True), r=r, catalyst=catalyst)
            catalyst = _x_all(bb, catalyst, self.b)
            return {"r": r, "catalyst": catalyst}
        if self.w == 1:
            return {"r": _h_one(bb, r), "catalyst": catalyst}
        left, right = self.halves
        bits = _msb_bits(bb, r)
        tw = self.twiddle()
        if not self.inverse:
            hi = _join(bb, bits[:right], QUInt(right))          # x2 = top `right` bits
            lo = _join(bb, bits[right:], QUInt(left))           # x1 = low `left` bits
            hi, catalyst = bb.add(self._sub(right), r=hi, catalyst=catalyst)
            lo, hi, catalyst = bb.add(tw, x=lo, z=hi, catalyst=catalyst)
            lo, catalyst = bb.add(self._sub(left), r=lo, catalyst=catalyst)
            out = _msb_bits(bb, lo) + _msb_bits(bb, hi)          # y = y1 + 2^right y2: y2 on top
        else:
            lo = _join(bb, bits[:left], QUInt(left))            # y2 = top `left` bits
            hi = _join(bb, bits[left:], QUInt(right))           # y1 = low `right` bits
            lo, catalyst = bb.add(self._sub(left), r=lo, catalyst=catalyst)
            lo, hi, catalyst = bb.add(tw, x=lo, z=hi, catalyst=catalyst)   # conjugated catalyst
            hi, catalyst = bb.add(self._sub(right), r=hi, catalyst=catalyst)
            out = _msb_bits(bb, hi) + _msb_bits(bb, lo)
        return {"r": _join(bb, out, QUInt(self.w)), "catalyst": catalyst}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        if self.inverse and not self.bare:
            return {attrs.evolve(self, bare=True): 1, XGate(): 2 * self.b}
        if self.w == 1:
            return {Hadamard(): 1}
        left, right = self.halves
        counts: Dict[Bloq, int] = {}
        for sub in (self._sub(right), self._sub(left)):
            counts[sub] = counts.get(sub, 0) + 1
        counts[self.twiddle()] = 1
        return counts

    def __str__(self) -> str:
        return f"Catalytic{'I' if self.inverse else ''}QFT(w={self.w}, b={self.b})"


def qft_error_units(w: int, b: int) -> int:
    """Sum over the recursion of rounded twiddle rows (contract units of pi 2^-b)."""
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def f(u: int) -> int:
        if u <= 1:
            return 0
        left = u // 2
        return f(u - left) + f(left) + MergedTwiddle(left, u - left, u, b).n_rounded_rows
    return f(int(w))


# ============================================================================= MULT

def representative_mult_constant(w_out: int) -> int:
    """A generic odd multiplier 0b0101...01 (full bit length; no dyadic accidents in the leaf turns)."""
    return (((1 << w_out) - 1) // 3) | 1


@attrs.frozen
class CatalyticMultiplier(Bloq):
    """|x>|0> -> |x>|a x mod 2^{w_out}>: H^{w_out} ; CatalyticToomPhaseProduct(tau = a/2^{w_out}) ;
    CatalyticQFT(w_out, inverse). The figures' MULT stage is w_out = n."""
    n: int
    k: int
    b: int
    a: int = attrs.field(eq=False)                 # concrete multiplier: decomposition only
    w_out: Optional[int] = None
    stop_tsr: float = 11.0
    mixing: bool = True
    seed: int = attrs.field(default=0, eq=False)
    pp_pad: str = "square"
    leaf_routes: Optional[tuple] = attrs.field(default=None, converter=_routes, eq=False, repr=False)
    rules: Optional[Any] = attrs.field(default=None, eq=False, repr=False)

    @property
    def wo(self) -> int:
        return int(self.w_out) if self.w_out is not None else int(self.n)

    @property
    def tau(self) -> Fraction:
        return Fraction(int(self.a), 1 << self.wo) % 1

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.n)), Register("out", QUInt(self.wo)),
                          Register("catalyst", QUInt(self.b))])

    def pp(self) -> Bloq:
        wx, wz = _pp_widths(self.n, self.wo, self.pp_pad)
        return _pp_bloq(wx, wz, self.k, self.tau, self.b, False, self.rules, self.stop_tsr, self.mixing,
                        self.seed, cls="MULT", res=self.wo, leaf_routes=self.leaf_routes)

    def iqft(self) -> CatalyticQFT:
        return CatalyticQFT(self.wo, self.b, inverse=True)

    def build_composite_bloq(self, bb: BloqBuilder, x, out, catalyst) -> Dict[str, SoquetT]:
        ob = [bb.add(Hadamard(), q=q) for q in _msb_bits(bb, out)]
        xb, ob, catalyst, _ = _apply_pp(bb, self.pp(), _msb_bits(bb, x), ob, catalyst)
        out, catalyst = bb.add(self.iqft(), r=_join(bb, ob, QUInt(self.wo)), catalyst=catalyst)
        return {"x": _join(bb, xb, QUInt(self.n)), "out": out, "catalyst": catalyst}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {Hadamard(): self.wo, self.pp(): 1, self.iqft(): 1}

    def __str__(self) -> str:
        return f"CatalyticMultiplier(n={self.n}, k={self.k}, b={self.b})"


# ============================================================================= MODMUL

def _extra_bits(eta: float) -> int:
    from ..bloqs.modmul import extra_bits
    return extra_bits(eta)


@attrs.frozen
class CatalyticCtrlModMul(Bloq):
    """Catalytic LeanCtrlModMul (U1..U5, see module docstring). Registers: ctrl, data QUInt(n),
    carry QBit, work QUInt(n + extra(eta)), flag QBit, catalyst QUInt(b)."""
    n: int
    k: int
    N: int
    c: int = attrs.field(eq=False)                 # concrete multiplier: decomposition only
    eta: float
    b: int
    stop_tsr: float = 11.0
    mixing: bool = True
    seed: int = attrs.field(default=0, eq=False)
    pp_pad: str = "square"
    leaf_routes: Optional[tuple] = attrs.field(default=None, converter=_routes, eq=False, repr=False)
    rules: Optional[Any] = attrs.field(default=None, eq=False, repr=False)

    def __attrs_post_init__(self):
        if self.N < 3 or self.N % 2 == 0:
            raise ValueError(f"CatalyticCtrlModMul needs an odd modulus N >= 3 (got {self.N})")
        if math.gcd(int(self.c) % int(self.N), int(self.N)) != 1:
            raise ValueError(f"c={self.c} not invertible mod N={self.N} (U5 undefined)")

    @property
    def w_work(self) -> int:
        return self.n + _extra_bits(self.eta)

    @property
    def w_carry(self) -> int:
        return self.n + 1

    # ---- exact turns (docs §2.1; spec.turn_u1/u2/u5)
    @property
    def tau1(self) -> Fraction:
        from .spec import turn_u1
        return turn_u1(self.c, self.N)

    @property
    def tau2(self) -> Fraction:
        from .spec import turn_u2
        return turn_u2(self.N, self.w_work, self.w_carry)

    @property
    def tau5(self) -> Fraction:
        from .spec import turn_u5
        return turn_u5(self.c, self.N)

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("ctrl", QBit()), Register("data", QUInt(self.n)),
                          Register("carry", QBit()), Register("work", QUInt(self.w_work)),
                          Register("flag", QBit()), Register("catalyst", QUInt(self.b))])

    def _pp(self, a: int, c: int, tau: Fraction, ctrl: bool, site: int, cls: str,
            res: Optional[int]) -> Bloq:
        """The site's PP. cls / res are the production census's (U1U5: generic; U2: dyadic at
        w_work + w_carry), so each leaf's census key -- and its priced route -- is the figures'. Each
        site draws its mixing seeds from its own stream (derive_seed(seed, site))."""
        wx, wz = _pp_widths(a, c, self.pp_pad)
        return _pp_bloq(wx, wz, self.k, tau, self.b, ctrl, self.rules, self.stop_tsr, self.mixing,
                        _derive_seed(self.seed, site), cls=cls, res=res, leaf_routes=self.leaf_routes)

    def pp_u1(self) -> Bloq:
        return self._pp(self.n, self.w_work, self.tau1, True, 1, "U1U5", None)

    def pp_u2(self) -> Bloq:
        return self._pp(self.w_work, self.w_carry, self.tau2, False, 2, "U2", self.w_work + self.w_carry)

    def pp_u5(self) -> Bloq:
        # adjoint: sign -1, passed as the turn (-tau5) mod 1
        return self._pp(self.w_carry, self.w_work, (-self.tau5) % 1, True, 5, "U1U5", None)

    def qft(self, w: int, inverse: bool) -> CatalyticQFT:
        return CatalyticQFT(w, self.b, inverse=inverse)

    def comparators(self) -> List[Bloq]:
        wc, ww, N = self.w_carry, self.w_work, int(self.N)
        return [GreaterThanConstant(wc, N), AddK(QUInt(wc), k=-N).controlled(),
                LessThanConstant(wc + ww, N)]

    # ---- the five steps, each a function of the full register dict (shared by the parent's
    #      decomposition and by `CatalyticModMulStep`, so the per-step tests exercise this exact code)
    def _u1(self, bb: BloqBuilder, s: Dict[str, Any]) -> None:
        """U1: H(work) ; C-PP(ctrl; data, work; tau1) ; IQFT(work)."""
        ww = self.w_work
        wb = [bb.add(Hadamard(), q=q) for q in _msb_bits(bb, s["work"])]
        db, wb, s["catalyst"], s["ctrl"] = _apply_pp(bb, self.pp_u1(), _msb_bits(bb, s["data"]), wb,
                                                     s["catalyst"], s["ctrl"])
        s["data"] = _join(bb, db, QUInt(self.n))
        s["work"], s["catalyst"] = bb.add(self.qft(ww, True), r=_join(bb, wb, QUInt(ww)),
                                          catalyst=s["catalyst"])

    def _dc(self, bb: BloqBuilder, s: Dict[str, Any]):
        """dataCarry = [carry (MSB)] + data, as one QUInt(n + 1)."""
        return _join(bb, [s.pop("carry")] + _msb_bits(bb, s.pop("data")), QUInt(self.w_carry))

    def _undc(self, bb: BloqBuilder, s: Dict[str, Any], dc) -> None:
        bits = _msb_bits(bb, dc)
        s["carry"], s["data"] = bits[0], _join(bb, bits[1:], QUInt(self.n))

    def _u2(self, bb: BloqBuilder, s: Dict[str, Any]) -> None:
        """U2: QFT(dataCarry) ; PP(work, dataCarry; tau2) ; IQFT(dataCarry)."""
        wc, ww = self.w_carry, self.w_work
        dc, s["catalyst"] = bb.add(self.qft(wc, False), r=self._dc(bb, s), catalyst=s["catalyst"])
        wb, dcb, s["catalyst"], _ = _apply_pp(bb, self.pp_u2(), _msb_bits(bb, s["work"]),
                                              _msb_bits(bb, dc), s["catalyst"])
        s["work"] = _join(bb, wb, QUInt(ww))
        dc, s["catalyst"] = bb.add(self.qft(wc, True), r=_join(bb, dcb, QUInt(wc)), catalyst=s["catalyst"])
        self._undc(bb, s, dc)

    def _u3(self, bb: BloqBuilder, s: Dict[str, Any]) -> None:
        """U3: CMP_GE_CONST(N; dataCarry -> flag) ; CSUB_CONST(N; flag |> dataCarry) (production bloqs)."""
        gt, csub, _ = self.comparators()
        dc, s["flag"] = bb.add(gt, x=self._dc(bb, s), target=s["flag"])
        s["flag"], dc = bb.add(csub, ctrl=s["flag"], x=dc)
        self._undc(bb, s, dc)

    def _u4(self, bb: BloqBuilder, s: Dict[str, Any]) -> None:
        """U4: CMP_LT_NW(N; dataCarry, work -> flag) (production bloq on dataCarry || work)."""
        wc, ww = self.w_carry, self.w_work
        lt = self.comparators()[2]
        both = _join(bb, _msb_bits(bb, self._dc(bb, s)) + _msb_bits(bb, s["work"]), QUInt(wc + ww))
        both, s["flag"] = bb.add(lt, x=both, target=s["flag"])
        bits = _msb_bits(bb, both)
        self._undc(bb, s, _join(bb, bits[:wc], QUInt(wc)))
        s["work"] = _join(bb, bits[wc:], QUInt(ww))

    def _u5(self, bb: BloqBuilder, s: Dict[str, Any]) -> None:
        """U5 = adjoint(H(work) ; C-PP(ctrl; dataCarry, work) ; IQFT(work)) = QFT ; C-PP(-tau5) ; H."""
        ww, wc = self.w_work, self.w_carry
        work, s["catalyst"] = bb.add(self.qft(ww, False), r=s["work"], catalyst=s["catalyst"])
        dcb, wb, s["catalyst"], s["ctrl"] = _apply_pp(bb, self.pp_u5(), _msb_bits(bb, self._dc(bb, s)),
                                                      _msb_bits(bb, work), s["catalyst"], s["ctrl"])
        self._undc(bb, s, _join(bb, dcb, QUInt(wc)))
        s["work"] = _join(bb, [bb.add(Hadamard(), q=q) for q in wb], QUInt(ww))

    STEPS = ("U1", "U2", "U3", "U4", "U5")

    def run_step(self, bb: BloqBuilder, s: Dict[str, Any], step: str) -> None:
        getattr(self, "_" + step.lower())(bb, s)

    def step_call_graph(self, step: str) -> Dict[Bloq, int]:
        ww, wc = self.w_work, self.w_carry
        gt, csub, lt = self.comparators()
        return {
            "U1": {Hadamard(): ww, self.pp_u1(): 1, self.qft(ww, True): 1},
            "U2": {self.qft(wc, False): 1, self.pp_u2(): 1, self.qft(wc, True): 1},
            "U3": {gt: 1, csub: 1},
            "U4": {lt: 1},
            "U5": {self.qft(ww, False): 1, self.pp_u5(): 1, Hadamard(): ww},
        }[step]

    def build_composite_bloq(self, bb: BloqBuilder, **soqs) -> Dict[str, SoquetT]:
        s = dict(soqs)
        for step in self.STEPS:
            self.run_step(bb, s, step)
        return s

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        counts: Dict[Bloq, int] = {}
        for step in self.STEPS:
            for bl, m in self.step_call_graph(step).items():
                counts[bl] = counts.get(bl, 0) + m
        return counts

    def __str__(self) -> str:
        return f"CatalyticCtrlModMul(n={self.n}, k={self.k}, N={self.N}, c={self.c}, b={self.b})"


@attrs.frozen
class CatalyticModMulStep(Bloq):
    """One step (U1..U5) of `mm`, on the full modmul signature; the same code as mm's decomposition.
    For per-step functional tests and step-resolved costing."""
    mm: CatalyticCtrlModMul
    step: str

    def __attrs_post_init__(self):
        if self.step not in CatalyticCtrlModMul.STEPS:
            raise ValueError(self.step)

    @property
    def signature(self) -> Signature:
        return self.mm.signature

    @property
    def b(self) -> int:
        return int(self.mm.b)

    def build_composite_bloq(self, bb: BloqBuilder, **soqs) -> Dict[str, SoquetT]:
        s = dict(soqs)
        self.mm.run_step(bb, s, self.step)
        return s

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return dict(self.mm.step_call_graph(self.step))

    def __str__(self) -> str:
        return f"{self.step}[{self.mm}]"


# ============================================================================= MODEXP

@attrs.frozen
class CatalyticModExp(Bloq):
    """tbits controlled CatalyticCtrlModMuls (exponent bit e controls step e), one shared catalyst.
    Per-step eta = shorEta(delta, n) = delta/n^2, as in LeanModExp."""
    n: int
    k: int
    N: int
    b: int
    a: int = attrs.field(default=2, eq=False)
    delta: float = 1e-2
    tbits: Optional[int] = None
    schedule: str = "representative"
    stop_tsr: float = 11.0
    mixing: bool = True
    seed: int = attrs.field(default=0, eq=False)
    pp_pad: str = "square"
    leaf_routes: Optional[tuple] = attrs.field(default=None, converter=_routes, eq=False, repr=False)
    rules: Optional[Any] = attrs.field(default=None, eq=False, repr=False)

    def __attrs_post_init__(self):
        if self.schedule not in ("representative", "real"):
            raise ValueError(f"schedule={self.schedule!r} (representative | real)")

    @property
    def eta(self) -> float:
        return float(self.delta) / float(self.n) ** 2

    @property
    def n_steps(self) -> int:
        return int(self.tbits) if self.tbits is not None else 2 * self.n

    def multipliers(self) -> List[int]:
        if self.schedule == "representative":
            return [int(self.a) % int(self.N)] * self.n_steps
        return [pow(int(self.a), 1 << e, int(self.N)) for e in range(self.n_steps)]

    def modmul_for(self, c: int, e: int = 0) -> CatalyticCtrlModMul:
        """Step e's modmul. `schedule="real"`: its own seed stream derive_seed(seed, 100 + e), so
        the mixing draws of different steps are independent. "representative": the ONE step-0 modmul,
        and so its seed, reused tbits times, exactly like production's cost convention."""
        seed = _derive_seed(self.seed, 100 + e) if self.schedule == "real" else self.seed
        return CatalyticCtrlModMul(n=self.n, k=self.k, N=self.N, c=int(c), eta=self.eta, b=self.b,
                                   stop_tsr=self.stop_tsr, mixing=self.mixing, seed=seed,
                                   pp_pad=self.pp_pad, leaf_routes=self.leaf_routes, rules=self.rules)

    def modmul(self) -> CatalyticCtrlModMul:
        return self.modmul_for(int(self.a) % int(self.N))

    def steps(self) -> List[CatalyticCtrlModMul]:
        return [self.modmul_for(c, e) for e, c in enumerate(self.multipliers())]

    @property
    def w_work(self) -> int:
        return self.n + _extra_bits(self.eta)

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("exponent", QUInt(self.n_steps)), Register("data", QUInt(self.n)),
                          Register("carry", QBit()), Register("work", QUInt(self.w_work)),
                          Register("flag", QBit()), Register("catalyst", QUInt(self.b))])

    def build_composite_bloq(self, bb: BloqBuilder, exponent, data, carry, work, flag, catalyst
                             ) -> Dict[str, SoquetT]:
        eb = _msb_bits(bb, exponent)[::-1]                        # LSB first: eb[e] = bit e
        for e, mm in enumerate(self.steps()):
            out = bb.add_d(mm, ctrl=eb[e], data=data, carry=carry, work=work,
                           flag=flag, catalyst=catalyst)
            eb[e], data, carry, work, flag, catalyst = (out["ctrl"], out["data"], out["carry"],
                                                        out["work"], out["flag"], out["catalyst"])
        return {"exponent": _join(bb, eb[::-1], QUInt(self.n_steps)), "data": data, "carry": carry,
                "work": work, "flag": flag, "catalyst": catalyst}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        if self.schedule == "representative":
            # ONE step-0 modmul, tbits times (production's cost convention): O(1), not O(tbits) objects
            return {self.modmul(): self.n_steps}
        counts: Dict[Bloq, int] = {}
        for bl in self.steps():
            counts[bl] = counts.get(bl, 0) + 1
        return counts

    def __str__(self) -> str:
        return f"CatalyticModExp(n={self.n}, k={self.k}, tbits={self.n_steps}, b={self.b})"


# ============================================================================= SHOR (order finding)

@attrs.frozen
class CatalyticOrderFinding(Bloq):
    """H^L(exponent) ; X(data LSB) ; CatalyticModExp ; CatalyticQFT(L, inverse)(exponent).
    Registers: exponent QUInt(L), data QUInt(n), catalyst QUInt(b) (carry/work/flag are internal)."""
    n: int
    k: int
    N: int
    b: int
    a: int = attrs.field(default=2, eq=False)
    delta: float = 1e-2
    L: Optional[int] = None
    schedule: str = "representative"
    stop_tsr: float = 11.0
    mixing: bool = True
    seed: int = attrs.field(default=0, eq=False)
    pp_pad: str = "square"
    leaf_routes: Optional[tuple] = attrs.field(default=None, converter=_routes, eq=False, repr=False)
    rules: Optional[Any] = attrs.field(default=None, eq=False, repr=False)

    @property
    def exp_width(self) -> int:
        return int(self.L) if self.L is not None else 2 * self.n

    def modexp(self) -> CatalyticModExp:
        return CatalyticModExp(n=self.n, k=self.k, N=self.N, b=self.b, a=self.a, delta=self.delta,
                               tbits=self.exp_width, schedule=self.schedule, stop_tsr=self.stop_tsr,
                               mixing=self.mixing, seed=self.seed, pp_pad=self.pp_pad,
                               leaf_routes=self.leaf_routes, rules=self.rules)

    def iqft(self) -> CatalyticQFT:
        return CatalyticQFT(self.exp_width, self.b, inverse=True)

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("exponent", QUInt(self.exp_width)), Register("data", QUInt(self.n)),
                          Register("catalyst", QUInt(self.b))])

    def build_composite_bloq(self, bb: BloqBuilder, exponent, data, catalyst) -> Dict[str, SoquetT]:
        L = self.exp_width
        me = self.modexp()
        exponent = _join(bb, [bb.add(Hadamard(), q=q) for q in _msb_bits(bb, exponent)], QUInt(L))
        db = _msb_bits(bb, data)
        db[-1] = bb.add(XGate(), q=db[-1])                        # initY1: data = |1>
        data = _join(bb, db, QUInt(self.n))
        carry = bb.allocate(dtype=QBit())
        work = bb.allocate(dtype=QUInt(me.w_work))
        flag = bb.allocate(dtype=QBit())
        out = bb.add_d(me, exponent=exponent, data=data, carry=carry, work=work, flag=flag,
                       catalyst=catalyst)
        for nm in ("carry", "work", "flag"):
            bb.free(out[nm])
        exponent, catalyst = bb.add(self.iqft(), r=out["exponent"], catalyst=out["catalyst"])
        return {"exponent": exponent, "data": out["data"], "catalyst": catalyst}

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {Hadamard(): self.exp_width, XGate(): 1, self.modexp(): 1, self.iqft(): 1}

    def __str__(self) -> str:
        return f"CatalyticOrderFinding(n={self.n}, k={self.k}, L={self.exp_width}, b={self.b})"


# ============================================================================= the one catalyst prep

@attrs.frozen
class Catalyzed(Bloq):
    """`CatalystPrep(b, eps)` ONCE, then `inner` with that catalyst. The catalyst leaves as a RIGHT
    register (intact |Phi_b>, never uncomputed). `choice` records how b was chosen (not part of eq)."""
    inner: Bloq
    eps: float = 1e-10
    choice: Optional[Any] = attrs.field(default=None, eq=False, repr=False)
    meta: Optional[dict] = attrs.field(default=None, eq=False, repr=False)

    @property
    def b(self) -> int:
        return int(self.inner.b)

    @cached_property
    def signature(self) -> Signature:
        regs = [r for r in self.inner.signature if r.name != "catalyst"]
        return Signature(regs + [Register("catalyst", QUInt(self.b), side=Side.RIGHT)])

    def prep(self) -> CatalystPrep:
        return CatalystPrep(self.b, eps=float(self.eps))

    def build_composite_bloq(self, bb: BloqBuilder, **soqs) -> Dict[str, SoquetT]:
        cat = bb.add(self.prep())
        return bb.add_d(self.inner, **soqs, catalyst=cat)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {self.prep(): 1, self.inner: 1}

    def __str__(self) -> str:
        return f"Catalyzed({self.inner}, b={self.b})"


# ============================================================================= census and b

def _is_leaf(bl: Bloq) -> bool:
    from .leaf import _LeafBase
    return isinstance(bl, _LeafBase)


def _is_leaf_class(bl: Bloq) -> bool:
    from .wiring_pp import CatalyticLeafClass
    return isinstance(bl, CatalyticLeafClass)


def _is_pp(bl: Bloq) -> bool:
    from .wiring_pp import CatalyticToomPhaseProduct
    return isinstance(bl, CatalyticToomPhaseProduct)


def _leaf_key(bl: Bloq) -> tuple:
    """v2 census key ("leaf", wx, ctrl, cls, res, wz) of a BUILT leaf. res is the EXACT resolution of its
    concrete turn (`spec.resolution`; None = non-dyadic), i.e. <= the production census's upper bound."""
    from .spec import resolution
    wx, wz, ctrl = int(bl.wx), int(bl.wz), bool(bl.ctrl)
    res = resolution(bl.tau)
    cls = "dyadic" if res is not None else ("U1U5" if ctrl else "generic")
    return ("leaf", wx, ctrl, cls, res, wz)


def _walk(bloq: Bloq, get_callee_cost) -> dict:
    total: Dict[Any, float] = {}
    for callee, mult in get_bloq_callee_counts(bloq):
        for key, cnt in get_callee_cost(callee).items():
            total[key] = total.get(key, 0) + cnt * int(mult)
    return total


class StackCensus(CostKey[dict]):
    """b-free census of a catalytic stack in the tcount.leaf_census (v2) key format:
    ("tw", r1, r2, t, 0, 0) per MergedTwiddle (no Toom refund: the twiddle is not decomposed),
    ("leaf", wx, ctrl, cls, res, wz) per leaf POSITION of each PP (its `census_key`: the site class and
    the production's resolution upper bound, so BChoice.leaf_routes applies to it), ("other",) per
    rotation outside the CatalystPrep (none in a merged stack). Concrete PPs skip subtrees whose
    turn is exactly 0, and so their leaves are absent. Class PPs list them at res 0 (priced 0), like
    the production census."""

    def compute(self, bloq: Bloq, get_callee_cost) -> dict:
        from qualtran.resource_counting.classify_bloqs import bloq_is_rotation
        if isinstance(bloq, MergedTwiddle):
            return {("tw", int(bloq.r1), int(bloq.r2), int(bloq.t), 0, 0): 1}
        if _is_pp(bloq):
            if bloq.is_identity():
                return {}
            if bloq.decision() is None:
                return {bloq.census_key(): 1}
        if _is_leaf(bloq):
            return {_leaf_key(bloq): 1}
        if isinstance(bloq, CatalystPrep):
            return {}
        if bloq_is_rotation(bloq):
            return {("other",): 1}
        return _walk(bloq, get_callee_cost)

    def zero(self) -> dict:
        return {}

    def __str__(self) -> str:
        return "catalytic stack census"


def stack_census(bloq: Bloq, cache: Optional[dict] = None) -> Dict[tuple, int]:
    return get_cost_value(bloq, StackCensus(), costs_cache=cache if cache is not None else {})


class StackError(CostKey[dict]):
    """Catalysis error of the built stack: per MergedTwiddle the exact max-over-z row error
    (`budget.twiddle_price`), per leaf the error its bloq reports -- for a `CatalyticLeafClass` the CLASS
    bound (the same number `choose_b` used), >= any concrete leaf's. Mixed P kicks are a diamond bound
    on the draw-averaged channel (randomised compiling only; see the module docstring).
    Returns {"op": .., "diamond": ..}; TV = op + diamond / 2."""

    def compute(self, bloq: Bloq, get_callee_cost) -> dict:
        if isinstance(bloq, MergedTwiddle):
            from .budget import twiddle_price
            return {"op": twiddle_price(int(bloq.r1), int(bloq.r2), int(bloq.t), int(bloq.b))[1]}
        if _is_leaf_class(bloq):
            c = bloq.class_cost()
            return {"op": float(c.err_op), "diamond": float(c.err_diamond)}
        if _is_leaf(bloq):
            return {"op": float(bloq.err_op), "diamond": float(bloq.err_diamond)}
        if isinstance(bloq, CatalystPrep):
            return {}
        return _walk(bloq, get_callee_cost)

    def zero(self) -> dict:
        return {}

    def __str__(self) -> str:
        return "catalytic stack error"


def stack_error(bloq: Bloq) -> Dict[str, float]:
    e = dict(get_cost_value(bloq, StackError()))
    op, dia = float(e.get("op", 0.0)), float(e.get("diamond", 0.0))
    return {"op": op, "diamond": dia, "tv": op + 0.5 * dia}


@attrs.frozen
class _V1BChoice:
    """b under the v1 contract (spec.choose_b_v1 == tcount `price_merged_v1(...).b`)."""
    b: int
    catalyst_prep_eps: float
    units: float
    source: str = "v1 contract (spec.choose_b_v1)"


def _v1_partition_cls():
    from . import spec
    return getattr(spec, "ErrorPartitionV1", None) or spec.__dict__["ErrorPartition"]


def default_partition(delta: float = 1e-2):
    """THE partition: `budget.ErrorPartition(delta)` (structure / catalysis / rotation = .5/.25/.25)."""
    try:
        from .budget import ErrorPartition
    except ImportError:
        ErrorPartition = _v1_partition_cls()
    return ErrorPartition(delta)


def v1_partition(delta: float = 1e-2):
    """The figures' pre-2026-09-23 (v1) partition: delta_struct = delta_rot = delta/2, prep 1e-3 delta_rot."""
    return _v1_partition_cls()(delta)


def _is_v1(partition) -> bool:
    return not hasattr(partition, "catalysis_budget")


def _delta_struct(part) -> float:
    """The structural share that sets eta = delta_struct / n^2 (identical in v1 and v2 at delta = 1e-2)."""
    return float(part.delta_struct if _is_v1(part) else part.structure_budget)


def stack_choose_b(census: Mapping[tuple, float], partition, *, mixing: bool = True, and_t: int = 4,
                   b_min: int = 4) -> Any:
    """THE shared b for a stack: `budget.choose_b(census, partition, mixing=...)` (v2); for a v1 partition
    the v1 contract-unit rule (== tcount `price_merged_v1(...).b`)."""
    if not _is_v1(partition):
        from .budget import choose_b
        return choose_b(census, partition, mixing=mixing, and_t=and_t, b_min=b_min)
    from . import spec
    rule = getattr(spec, "choose_b_v1", None) or spec.__dict__["choose_b"]
    b = rule(census, float(partition.delta_rot), prep_fraction=float(partition.prep_fraction))
    eps = float(partition.delta_prep) * b / max(1, b - 3)
    return _V1BChoice(b=b, catalyst_prep_eps=eps, units=spec.census_error_units(census, b))


def _prep_eps(ch, b: int, partition) -> float:
    """The eps handed to CatalystPrep(b, eps) (Qualtran splits it eps / b per ZPow)."""
    if ch is not None and getattr(ch, "b", None) == b:
        return float(ch.catalyst_prep_eps)
    if _is_v1(partition):
        return float(partition.delta_prep) * b / max(1, b - 3)
    return float(partition.catalyst_prep_eps(b))


# ============================================================================= builders

def _stage_structure(stage: str, rules, k: int, n: int, b: int, delta_struct: float, **kw) -> Bloq:
    """The bare (catalyst THRU) catalytic bloq for `stage`, mirroring notebook_helpers._bloq_b:
    QFT(w=n); MULT(n, w_out=n); MODMUL(n, N=2^n-1, c=2, eta=delta_struct/n^2);
    MODEXP / SHOR(n, N=2^n-1, a=2, delta=delta_struct, tbits = L = 2n)."""
    n = int(n)
    N = (1 << n) - 1
    common = {key: kw[key] for key in ("stop_tsr", "mixing", "seed", "pp_pad", "leaf_routes")
              if key in kw}
    if stage == "QFT":
        return CatalyticQFT(n, b)
    if stage == "MULT":
        a = kw.get("a", representative_mult_constant(n))
        return CatalyticMultiplier(n=n, k=k, b=b, a=int(a), rules=rules, **common)
    if stage == "MODMUL":
        eta = float(delta_struct) / float(n) ** 2
        return CatalyticCtrlModMul(n=n, k=k, N=N, c=int(kw.get("c", 2)), eta=eta, b=b, rules=rules,
                                   **common)
    if stage == "MODEXP":
        return CatalyticModExp(n=n, k=k, N=N, b=b, a=int(kw.get("a", 2)), delta=float(delta_struct),
                               schedule=kw.get("schedule", "representative"), rules=rules, **common)
    if stage == "SHOR":
        return CatalyticOrderFinding(n=n, k=k, N=N, b=b, a=int(kw.get("a", 2)), delta=float(delta_struct),
                                     schedule=kw.get("schedule", "representative"), rules=rules, **common)
    raise ValueError(f"unknown stage {stage!r} (one of {STAGES})")


def catalytic_bloq(stage: str, rules, k: int, n: int, delta: float = 1e-2, *, partition=None,
                   b: Optional[int] = None, prep: bool = True, certify: bool = True,
                   max_bump: int = 8, **kw) -> Bloq:
    """Catalytic counterpart of `notebook_helpers._bloq_b(stage, rules, k, n, eta, delta)` for the five
    stages, with eta = partition's structural share / n^2 (as the figures take it).

    b: unless given, `stack_choose_b(census of THIS structure, partition)` -- `budget.choose_b` in v2.
    certify (v2 only): the stack's error (`stack_error`: exact twiddle rows, each leaf's class-level
    err_op + err_diamond/2) must meet the catalysis share, else b is raised (at most `max_bump` bits;
    recorded). With class leaves this re-checks the class bound, not a concrete circuit.
    prep=True wraps the stack in `Catalyzed` -- the one CatalystPrep -- with `.choice` (the BChoice) and
    `.meta` (certified error, bumps). `mixing` (default True: decision D5) goes to the leaves AND to
    choose_b."""
    part = partition if partition is not None else default_partition(delta)
    d_struct = _delta_struct(part)
    kw.setdefault("mixing", True)
    ch, meta = None, {"partition": part}
    if b is None:
        probe = _stage_structure(stage, rules, k, n, PROBE_B, d_struct, **kw)
        cen = stack_census(probe)
        ch = stack_choose_b(cen, part, mixing=bool(kw["mixing"]))
        b = int(ch.b)
        meta["b_choice"] = b
        if not _is_v1(part):
            kw["leaf_routes"] = ch.leaf_routes          # build every leaf with its PRICED route
        inner = _stage_structure(stage, rules, k, n, b, d_struct, **kw)
        if certify and not _is_v1(part):
            bumps = 0
            while True:
                err = stack_error(inner)
                if err["tv"] <= float(part.catalysis_budget) * (1 + 1e-12) or bumps >= max_bump:
                    break
                bumps += 1
                b += 1
                kw["leaf_routes"] = stack_choose_b(cen, part, mixing=bool(kw["mixing"]), b_min=b).leaf_routes
                inner = _stage_structure(stage, rules, k, n, b, d_struct, **kw)
            meta.update(certified_error=err, bumps=bumps,
                        certified=err["tv"] <= float(part.catalysis_budget) * (1 + 1e-12))
    else:
        inner = _stage_structure(stage, rules, k, n, int(b), d_struct, **kw)
    if not prep:
        return inner
    return Catalyzed(inner, eps=_prep_eps(ch, int(b), part), choice=ch, meta=meta)


def _for_budget(stage: str):
    def f(cls, n: int, k: int, delta: float = 1e-2, partition=None, *, rules=None, **kw) -> "Catalyzed":
        """Structure at a probe b -> census -> budget.choose_b -> rebuilt at that b (and certified), wrapped
        in the one CatalystPrep (`Catalyzed`; `.choice` holds the BChoice, `.inner` the stack)."""
        return catalytic_bloq(stage, rules, k, n, delta, partition=partition, **kw)
    return classmethod(f)


CatalyticQFT.for_budget = _for_budget("QFT")
CatalyticMultiplier.for_budget = _for_budget("MULT")
CatalyticCtrlModMul.for_budget = _for_budget("MODMUL")
CatalyticModExp.for_budget = _for_budget("MODEXP")
CatalyticOrderFinding.for_budget = _for_budget("SHOR")


# ============================================================================= pricing

def _prep_rotation_T(b: int, partition) -> float:
    """T of the b - 3 synthesised prep rotations ONLY (the PhaseGradientState's 1 T is already a QECGates
    T gate): v2 `spec.catalyst_prep_T(b, rotation_budget) - 1`; v1 `tcount catalyst_prep_t(b, delta_prep)`."""
    if b <= 3:
        return 0.0
    if _is_v1(partition):
        import sys
        from pathlib import Path
        cts = str(Path(__file__).resolve().parents[2] / "Clifford_plus_T_scaling")
        if cts not in sys.path:
            sys.path.insert(0, cts)
        from tcount.catalysis import catalyst_prep_t
        return float(catalyst_prep_t(b, float(partition.delta_prep)))
    from .spec import catalyst_prep_T
    return float(catalyst_prep_T(b, float(partition.rotation_budget))) - 1.0


def stage_T(bloq: Bloq, partition=None, *, and_t: int = 4) -> Dict[str, float]:
    """T of a stack in the figures' currency: and_t T per And (D1: 4 = Qualtran), QECGatesCost T / Toffoli
    / CSwap as T, and the CatalystPrep's b - 3 rotations at the partition's prep tolerance. Any other
    rotation raises: a merged stack has none."""
    part = partition
    if part is None and isinstance(bloq, Catalyzed) and bloq.meta:
        part = bloq.meta.get("partition")
    if part is None:
        part = default_partition()
    qec = get_cost_value(bloq, QECGatesCost())
    b = int(getattr(bloq, "b", 0))
    # The only rotations allowed are the CatalystPrep's own. Compare with THAT bloq's count, not b - 3:
    # Qualtran classifies ZPow exponents below its 1e-12 tolerance as identity, so PhaseGradientState(b)
    # reports at most 37 rotations (b >= 41: the 2^-40.. qubits count as Clifford). Physically those
    # finest prep rotations are ~2^-40 rad, far inside the prep tolerance; the T charge below still
    # prices ALL b - 3 prep states (an upper bound, and exactly what the v2 figures charge).
    n_prep = (int(get_cost_value(bloq.prep(), QECGatesCost()).rotation)
              if isinstance(bloq, Catalyzed) else 0)
    if int(qec.rotation) != n_prep:
        raise RuntimeError(f"rotations {qec.rotation} != catalyst prep {n_prep}: a stray rotation site")
    n_prep = max(0, b - 3) if isinstance(bloq, Catalyzed) else 0
    prep_T = _prep_rotation_T(b, part) if n_prep else 0.0
    T = float(and_t * int(qec.and_bloq) + int(qec.t) + 4 * int(qec.toffoli) + 7 * int(qec.cswap)) + prep_T
    return {"T": T, "and": int(qec.and_bloq), "t": int(qec.t), "toffoli": int(qec.toffoli),
            "cswap": int(qec.cswap), "rotation": int(qec.rotation), "clifford": int(qec.clifford),
            "prep_rot_T": float(prep_T), "b": b}


class AndBreakdown(CostKey[dict]):
    """Ands of a catalytic stack by origin, next to what the pricing charges for the same built pieces:
        twiddle, twiddle_v1                MergedTwiddles; v1 = tcount `_twiddle_rows` (v2 == native)
        leaf, leaf_v1, leaf_v2             leaf positions; v1 = `_leaf_options` (cheaper route, generic);
                                           v2 = `leaf.leaf_cost_class` at the position's census key and
                                           priced route (what `budget.price_at` charges for it)
        body                               Toom evaluation arithmetic (Add / Negate): production's too
        comparator                         U3/U4 production bloqs
        other                              anything else (expected 0)."""

    def __init__(self, mixing: bool = True):
        self._qec: dict = {}
        self.mixing = bool(mixing)

    def _ands(self, bl: Bloq) -> int:
        return int(get_cost_value(bl, QECGatesCost(), costs_cache=self._qec).and_bloq)

    def compute(self, bloq: Bloq, get_callee_cost) -> dict:
        from qualtran.bloqs.arithmetic import Add, Negate
        from qualtran.bloqs.mcmt import And
        if isinstance(bloq, MergedTwiddle):
            from .twiddle import contract_twiddle_ands
            return {"twiddle": self._ands(bloq),
                    "twiddle_v1": contract_twiddle_ands(bloq.r1, bloq.r2, bloq.t, bloq.b)[0]}
        if _is_leaf_class(bloq):
            from .spec import contract_leaf_ands
            return {"leaf": self._ands(bloq), "leaf_v2": int(bloq.class_cost().ands),
                    "leaf_v1": min(contract_leaf_ands(r, max(bloq.wx, bloq.wz), bloq.b, bloq.ctrl)
                                   for r in ("Y", "P"))}
        if isinstance(bloq, (Add, Negate)):
            return {"body": self._ands(bloq)}
        if isinstance(bloq, (GreaterThanConstant, LessThanConstant)) or "AddK" in str(bloq):
            return {"comparator": self._ands(bloq)}
        if isinstance(bloq, And):
            return {"other": 0 if bloq.uncompute else 1}
        if isinstance(bloq, CatalystPrep):
            return {}
        callees = get_bloq_callee_counts(bloq)
        if not callees:
            a = self._ands(bloq)
            return {"other": a} if a else {}
        return _walk(bloq, get_callee_cost)

    def zero(self) -> dict:
        return {}

    def __str__(self) -> str:
        return "catalytic And breakdown"


def and_breakdown(bloq: Bloq, mixing: bool = True) -> Dict[str, int]:
    return dict(get_cost_value(bloq, AndBreakdown(mixing)))
