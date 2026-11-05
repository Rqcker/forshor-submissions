"""Rotation-resolution histograms: how many of a bloq's arbitrary rotations carry a dyadic turn of
resolution s (turn = c / 2^s in lowest terms), so each can be priced as a phase-gradient addition of
min(s, b) bits instead of a full-precision synthesized rotation.

WHY A "RESOLUTION TWIN" AND NOT AN ANGLE ENUMERATION. The unpruned pipeline models keep phase-product
angles symbolic (one `CZPowGate(exponent=e)` bucket per leaf grid), so no histogram can be read off
their call graphs; enumerating concrete angles (the gadget path) creates q^depth distinct nodes and is
capped at small n. But for a STRUCTURALLY DYADIC turn the histogram depends on the turn only through its
resolution — the corrected theory's own `prop:mprime` (Dyadic_Phase_Compilation, Stage 2): a leaf pair
(i, j) with weights ±2^i, ±2^j carries turn tau * 2^(i+j), i.e. resolution t − (i+j) (trivial when
t ≤ i+j); a child with integer Toom weight c carries resolution t − v2(c) (a zero weight makes the
whole child trivial). The bundled coefficient polynomials evaluate to INTEGERS at 2^m for every arity
k = 2..6 (checked 2026-09-10), so the recursion keyed by (widths, t) is exact and has O(depth · #t)
nodes — no sweep cap. `tests/test_rotation_histogram.py` pins the twin against the exact angle
enumeration of the gadget path on small twiddles.

WHAT IS AND IS NOT RESOLVED
  * QFT twiddles (`LeanQFT`, all three cores): turn 2^-w at width w (`lowerQFTPlan`: PhaseProdUsing(2π/2^w));
    exact resolutions via the twin. `ReferenceQFT` ladders: exact from the distance d (turn 2^-(d+1)).
  * KMY modular-multiply phase products (U1/U2/U5 and their recursion): bucket `generic` — U1/U5 carry
    denominator N (never dyadic, see recursive_w_gadgets.py's CLAMPING notes); U2 is dyadic with
    resolution w_work + w_carry, above any gradient register, so pricing it as generic loses nothing.
  * Beauregard Fourier arithmetic (2026-09-10, item 12): resolved structurally. A `PhiAdd` wire k carries
    turn r / 2^(k+1) with r = addend mod 2^(k+1), so its resolution is AT MOST k+1 (exactly k+1 − v2(r));
    with the addend symbolic the twin keys the wire at the bound k+1 (sound under the monotone gradient
    price). The textbook QFTs inside every modular adder are exact ladders (turn 2^-s at distance s−1).
    Concrete addends (small n, `BeauregardCtrlModMul` with its k_max truncation) are enumerated exactly
    through the call graph instead. The ripple arm has no rotations.
  * Any other rotation the model keeps symbolic: bucket `symbolic`.
  * PURE turns ('p<s>' keys): a leaf whose reduced turn is exactly ±2^-s (odd numerator 1: QFT ladders,
    the QFT twiddle root and every Toom child reached only through power-of-two weights) has magnitude
    2^-s, so once s ≥ b + 2 the b-bit phase-gradient rounding sends it to zero and it costs nothing —
    Coppersmith's AQFT criterion inside the gradient currency, within the same per-rotation ε (the error
    of dropping it, 2^-s ≤ 2^-(b+2), is below the half-ulp rounding every kept gate already incurs).
    A leaf reached through a weight with an odd part ≠ 1 keeps an int key: its magnitude is unknown
    to the twin (large in general), so it is priced as a b-bit addition. The twin's purity flag is a
    sufficient condition (pinned against the exact census in tests/test_histogram_vs_ir_census.py).
  * Buckets 0..3 are pairs whose exact gate is trivial (s ≤ 0), CZ (1), CS (2) or CT (3) — CS and CT are
    controlled gates (3 T and 5 T exact, NOT Clifford / a single T); the symbolic models still charge them as
    rotations, and the histogram counts every rotation THE MODEL charges, so its total equals
    `QECGatesCost().rotation` for the same bloq (pinned in tests). `gate_histogram` (concrete `CZPowGate`
    leaves) excludes exponents 1, 1/2, 1/4 because `QECGatesCost` already charges those bloqs directly
    (And + Clifford / And + T: 4, 4, 5 T), so they must not be priced twice.
  * PRICE (T1, 2026-09-11): `gradient_price` charges what `clifford_t_synthesis._gadget_leaf_local` EMITS —
    `s` And pairs per site (the control And plus the `s−1` carries of `Add(QUInt(s))`), i.e. `4·min(s, b)` T at
    4 T per And, `4·b` for a generic/rounded turn, 0 for a pure turn beyond `b+1` or an identity. The former
    `4·(min(s,b) − 2)` was Qualtran's `AddIntoPhaseGrad` formula — a cost-only bloq with no decomposition, for an
    UNCONTROLLED addend — and no controlled realisation reaches it (candidates measured in the audit note §12).
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
from typing import Any, Callable, Dict, List, Optional, Union

from qualtran import Bloq
from qualtran.bloqs.basic_gates import CZPowGate, ZPowGate
from qualtran.resource_counting import CostKey, QECGatesCost, get_bloq_callee_counts, get_cost_value
from qualtran.resource_counting.classify_bloqs import (bloq_is_clifford, bloq_is_rotation, bloq_is_state_or_effect,
                                                       bloq_is_t_like)
from qualtran.symbolics import is_symbolic

from . import bundle_compat
from . import constants, widths
from .bloqs.phase_product import LeanPhaseProduct
from .bloqs.qft import LeanQFT, QftRecursiveTwiddle, ReferenceQFT
from .bloqs.recursive import LeanAsymRecursivePhaseProduct, LeanRecursivePhaseProduct
from .bloqs.recursive_w_gadgets import (LeanAsymRecursivePhaseProductWithGadgets, LeanRecursivePhaseProductWithGadgets,
                                        _leaf_groups, phase_resolution)
from .mirrors.coeff_poly import poly_eval
from .recursion_versions import ToomRecursivePhaseProduct
from .recursion_versions_w_gadgets import ToomRecursivePhaseProductWithGadgets

Hist = Dict[Union[int, str], int]
GENERIC = 'generic'
SYMBOLIC = 'symbolic'
PURE = 'p'          # key prefix: 'p<s>' is a leaf whose reduced turn is exactly ±2^-s


def pure_key(s: int) -> str:
    return f'{PURE}{s}'


def split_key(key) -> tuple:
    """(resolution or None, pure) for a histogram key; buckets 'generic'/'symbolic'/'bin:..' give (None, False)."""
    if isinstance(key, int):
        return key, False
    if isinstance(key, str) and key.startswith(PURE) and key[1:].isdigit():
        return int(key[1:]), True
    return None, False


def resolution_of(key) -> Optional[int]:
    return split_key(key)[0]
PHASE_PRODUCTS = (LeanPhaseProduct, LeanRecursivePhaseProduct, LeanAsymRecursivePhaseProduct, ToomRecursivePhaseProduct,
                  QftRecursiveTwiddle, LeanRecursivePhaseProductWithGadgets, LeanAsymRecursivePhaseProductWithGadgets,
                  ToomRecursivePhaseProductWithGadgets)


def v2(x: int) -> int:
    """2-adic valuation of a non-zero integer."""
    return (x & -x).bit_length() - 1


class ResolutionTwin:
    """Resolution-keyed twin of the pipeline's twiddle recursions for one bundle (see module docstring).

    `t` is the resolution of the turn carried by a node; `None` means the turn is zero (a vanished Toom
    weight), so every leaf below is trivial and lands in bucket 0.
    """

    def __init__(self, doc: dict, policy: str, core: str, rules: Any = None, tsr: float = constants.TS_PER_ROTATION):
        self.doc, self.policy, self.core, self.rules, self.tsr = doc, policy, core, rules, tsr
        self.k = doc['k']
        # Read through `bundle_compat`/`widths`, not the v1 field names. This class was missed in
        # the 2026-09-18 cutover and read `coeff_poly.polys` (v2 emits `coeff_poly.rows`) and
        # `width.general` (v2 emits `width.by_w`), so it raised `KeyError: 'polys'` for every
        # configuration whose path reaches the rotation histogram with a Lean QFT. The failure was
        # not visible as a failure: `engine_export.build_export` catches it per point and records
        # an `error`, so the curve simply came out empty and notebook 10 lost its entire Lean-QFT
        # arm -- 15 of its 27 KMY configurations -- with nothing louder than a matplotlib
        # "No artists with labels found to put in legend" warning.
        self.polys = [list(p) for p in bundle_compat.coeff_polys(doc)]
        self.rule_sym = widths.width_rule(doc, 'sym')
        self.general_sym = widths.general_widths(doc, 'sym')
        # The ops must come from THIS bundle, not from `mirrors.schedule.raw_table`. That helper
        # reads the frozen 2026-08-27 reference documents, which are on the `generate` table,
        # while the bundle handed in here is emitted on `standard` (DEFAULT_TABLE since section
        # 8.19.1). Those tables have different op lists -- at k=3, 21 ops versus 17 -- so this
        # would have costed one table's recursion against another's coefficients, quietly.
        self.ops = tuple(bundle_compat.schedule_view(doc)['ops'])
        self.memo: Dict[tuple, Counter] = {}

    # -- primitives --------------------------------------------------------------------------
    def child_resolutions(self, t: Optional[int], m: int, pure: bool = False) -> List[tuple]:
        """(resolution, pure) per Toom child: weight c gives resolution t − v2(c); the child stays pure
        only when the parent is pure and |c| is a power of two (odd part 1)."""
        out = []
        for poly in self.polys:
            c = poly_eval(list(poly), Fraction(2) ** m)
            if c.denominator != 1:
                raise ValueError(f'Toom weight {c} is not an integer at m={m}: the resolution twin needs integer weights')
            if c == 0 or t is None:
                out.append((None, False))
            else:
                ci = abs(int(c))
                out.append((t - v2(ci), pure and (ci & (ci - 1)) == 0))
        return out

    @staticmethod
    def leaf(wx: int, wz: int, t: Optional[int], pure: bool = False) -> Counter:
        h: Counter = Counter()
        for s, _sign, mult in _leaf_groups(wx, wz):
            r = 0 if t is None else max(t - s, 0)
            h[pure_key(r) if (pure and r > 0) else r] += mult
        return h

    def _memo(self, key: tuple, build: Callable[[], Counter]) -> Counter:
        if key not in self.memo:
            self.memo[key] = build()
        return self.memo[key]

    # -- core="recursive": the Lean lowerLeaf rule ----------------------------------------------
    def sym(self, w: int, t: Optional[int], pure: bool = False) -> Counter:
        def build():
            # The exact re-fold, as `LeanRecursivePhaseProduct.next_width` does: the emitted tables and the
            # affine tail are only the cross-check (the tail over-estimates by one off the uniform ladder, so
            # the twin's ladder would end a level early and disagree with the bloq it mirrors).
            from .width_scan import next_width as exact_next_width
            W2 = exact_next_width([dict(o) for o in self.ops], self.k, w)
            if W2 < w:
                total: Counter = Counter()
                for tc, pc in self.child_resolutions(t, w // self.k, pure):
                    total.update(self.sym(W2, tc, pc))
                return total
            return self.leaf(w, w, t, pure)
        return self._memo(('sym', w, t, pure), build)

    def asym(self, wx: int, wz: int, t: Optional[int], pure: bool = False) -> Counter:
        def build():
            from .mirrors.width import next_width
            W2 = next_width(self.k, self.ops, wx, wz)
            if W2 < max(wx, wz):
                total: Counter = Counter()
                for tc, pc in self.child_resolutions(t, min(wx, wz) // self.k, pure):
                    total.update(self.sym(W2, tc, pc))
                return total
            return self.leaf(wx, wz, t, pure)
        return self._memo(('asym', wx, wz, t, pure), build)

    # -- core="versionB": the R2 rule -------------------------------------------------------------
    def toom(self, w: int, t: Optional[int], root: bool = True, pure: bool = False) -> Counter:
        def build():
            assert self.rules is not None, "core='versionB' needs a RuleSet"
            dec = self.rules.decision(w, 'B', self.k, 'kmy', root, self.tsr)
            if dec is None:
                return self.leaf(w, w, t, pure)
            k, W2 = dec
            assert k == self.k
            total: Counter = Counter()
            for tc, pc in self.child_resolutions(t, -(-w // k), pure):
                total.update(self.toom(W2, tc, False, pc))
            return total
        return self._memo(('toom', w, t, root, pure), build)

    # -- core="onelevel" ----------------------------------------------------------------------
    def onelevel(self, size: int, t: Optional[int], pure: bool = False) -> Counter:
        def build():
            pp = LeanPhaseProduct.from_bundle(self.doc, size, self.policy)
            W, m = int(pp.W), int(pp.m)
            total: Counter = Counter()
            for tc, pc in self.child_resolutions(t, m, pure):
                total.update(self.leaf(W, W, tc, pc))
            return total
        return self._memo(('onelevel', size, t, pure), build)

    def twiddle(self, left: int, right: int, t: int, pure: bool = True) -> Counter:
        """The QFT twiddle `PhaseProdUsing(2π/2^w, left, right)` at width w = t (turn 2^-w, a pure turn)."""
        if self.core == 'recursive':
            return self.asym(left + 1, right + 1, t, pure)      # zero-extended, as QftRecursiveTwiddle prices it
        size = max(left, right) + 1
        if self.core == 'versionB':
            return self.toom(size, t, True, pure)
        return self.onelevel(size, t, pure)


_TWINS: Dict[tuple, ResolutionTwin] = {}


def twin_for(qft: LeanQFT) -> ResolutionTwin:
    tsr = getattr(qft, 'stop_tsr', constants.TS_PER_ROTATION)
    key = (id(qft.bundle_doc), qft.policy, qft.core, id(qft.rules), tsr)
    if key not in _TWINS:
        _TWINS[key] = ResolutionTwin(qft.bundle_doc, qft.policy, qft.core, qft.rules, tsr)
    return _TWINS[key]


def reference_qft_histogram(q: ReferenceQFT) -> Counter:
    """Exact from the ladder distances: CZPow(2^-d) has turn 2^-(d+1); d = 1, 2 are S and T (not rotations)."""
    h: Counter = Counter()
    d_stop = q.w if q.k_max is None else min(q.w, int(q.k_max) + 1)
    for d in range(1, d_stop):
        z = ZPowGate(exponent=2.0 ** -d) if d <= 60 else None
        if z is not None and (bloq_is_t_like(z) or bloq_is_clifford(z)):
            continue
        h[pure_key(d + 1)] += q.w - d
    return h


def qft_textbook_histogram(m: int) -> Counter:
    """Qualtran's `QFTTextBook(m)` leaves as the symbolic-width model charges them: every controlled phase
    CZ(2^-i), i = 1..j, of the PhaseGradientUnitary at row j (j = 1..m-1): turn 2^-(i+1), pure;
    m(m-1)/2 rotations in all, including the CS/T-size ones the model still counts."""
    h: Counter = Counter()
    for s in range(2, m + 1):
        h[pure_key(s)] += m - s + 1
    return h


def phiadd_histogram(m: int) -> Counter:
    """`PhiAdd` with a symbolic addend on an m-wire Fourier register: wire k carries r/2^(k+1), resolution
    at most k+1; the symbolic model charges every wire, so m rotations keyed at their resolution bound."""
    return Counter({k + 1: 1 for k in range(m)})


def beauregard_twin(n: int) -> Counter:
    """Structural resolution histogram of one Beauregard `CUa` (Fig 7) at width n under the symbolic-addend,
    untruncated (k_max = None) convention: 2 CMultMod, each 2 QFT(n+1) + n PhiAddMod, each 5 PhiAdd(n+1)
    (three doubly controlled, one singly, one plain) + 4 QFT(n+1). Its total is the structural rotation
    count, `beauregard_counts(n).rotation`."""
    m = n + 1
    qft = qft_textbook_histogram(m)
    per_mod = _scaled(phiadd_histogram(m), 5) + _scaled(qft, 4)
    cmult = _scaled(per_mod, n) + _scaled(qft, 2)
    return _scaled(cmult, 2)


def _scaled(h: Counter, mult: int) -> Counter:
    return Counter({key: count * mult for key, count in h.items()})


def beauregard_counts(n: int):
    """Exact structural `GateCounts` of one `CUa` at width n in the symbolic-addend, untruncated model:
    every PhiAdd wire and every QFT leaf charged as a rotation (an And pair per control: doubly controlled
    2 And, singly 1, plain 0; each QFT leaf one And), n controlled swaps, and the Clifford glue of the
    model (H, swaps, CNOT/X). Equals Qualtran's symbolic annotation evaluated at odd n; at even n the
    symbolic `QFTTextBook` term undercounts its own leaves by n/2 per QFT (a floor artefact of the
    symbolic width), which this closed form does not."""
    from qualtran.resource_counting import GateCounts
    m = n + 1
    leaves = m * (m - 1) // 2
    qft = dict(rotation=leaves, and_bloq=leaves, measurement=leaves, clifford=leaves + m + m // 2)
    phi = dict(rotation=5 * m, and_bloq=7 * m, measurement=7 * m, clifford=7 * m)
    per_mod = {key: phi[key] + 4 * qft[key] for key in phi}
    per_mod['clifford'] += 4                                  # 2 CNOT + 2 X
    cmult = {key: n * per_mod[key] + 2 * qft[key] for key in per_mod}
    cua = {key: 2 * cmult[key] for key in cmult}
    return GateCounts(cswap=n, **cua)


def gate_histogram(bloq: Bloq) -> Hist:
    """A single CZPow / ZPow leaf: {} if Qualtran would not count it as a rotation."""
    e = bloq.exponent
    if is_symbolic(e):
        return {SYMBOLIC: 1}
    z = ZPowGate(exponent=e, eps=bloq.eps) if isinstance(bloq, CZPowGate) else bloq
    if bloq_is_t_like(z) or bloq_is_clifford(z):
        return {}
    phi = Fraction(float(e))
    if phi.numerator.bit_length() > 24:            # a generic angle rounded to a double, not a structural dyadic
        return {GENERIC: 1}
    s = phase_resolution(phi)
    if s is None:
        return {GENERIC: 1}
    return {pure_key(s) if is_pure_turn(phi, s) else s: 1}


def is_pure_turn(phi: Fraction, s: int) -> bool:
    """The reduced turn phi/2 mod 1 is exactly +-2^-s (numerator 1 or 2^s - 1 in lowest terms)."""
    turn = (phi / 2) % 1
    return turn.numerator in (1, (1 << s) - 1)


class RotationResolution(CostKey[dict]):
    """Qualtran CostKey: {resolution or 'generic'/'symbolic': rotation count} through the call graph."""

    def __init__(self, qec_cache: Optional[dict] = None):
        self.qec_cache = qec_cache if qec_cache is not None else {}

    def rotations(self, bloq: Bloq) -> int:
        return int(get_cost_value(bloq, QECGatesCost(), costs_cache=self.qec_cache).rotation)

    def compute(self, bloq: Bloq, get_callee_cost: Callable[[Bloq], dict]) -> dict:
        from qualtran.bloqs.basic_gates import Discard, GlobalPhase, Identity, MeasureX, MeasureZ, Toffoli, TwoBitCSwap
        from qualtran.bloqs.bookkeeping._bookkeeping_bloq import _BookkeepingBloq
        from qualtran.bloqs.mcmt import And, MultiTargetCNOT
        from .bloqs.beauregard_modmul import BeauregardCtrlModMul

        if isinstance(bloq, LeanQFT):
            if bloq.w <= 1:
                return {}
            left, right = bloq.w // 2, bloq.w - bloq.w // 2
            total: Counter = Counter()
            for sub in (bloq._sub(right), bloq._sub(left)):
                total.update(get_callee_cost(sub))
            total.update(twin_for(bloq).twiddle(left, right, bloq.w))
            return dict(total)
        if isinstance(bloq, ReferenceQFT):
            return dict(reference_qft_histogram(bloq))
        if isinstance(bloq, PHASE_PRODUCTS):
            n = self.rotations(bloq)
            return {GENERIC: n} if n else {}
        if isinstance(bloq, BeauregardCtrlModMul):
            if bloq.adder != 'fourier':
                return {}                                          # the ripple arm carries no rotations
            twin = beauregard_twin(int(bloq.n))
            if sum(twin.values()) == self.rotations(bloq):
                return dict(twin)                                  # the untruncated symbolic-addend model
            # concrete addends with k_max truncation: enumerate the real call graph (small n only)
            total = Counter()
            for callee, mult in get_bloq_callee_counts(bloq):
                for key, count in get_callee_cost(callee).items():
                    total[key] += count * mult
            return dict(total)
        if isinstance(bloq, (CZPowGate, ZPowGate)):
            return gate_histogram(bloq)
        if (bloq_is_t_like(bloq) or bloq_is_clifford(bloq) or bloq_is_state_or_effect(bloq)
                or isinstance(bloq, (And, Toffoli, TwoBitCSwap, MeasureX, MeasureZ, MultiTargetCNOT,
                                     _BookkeepingBloq, GlobalPhase, Identity, Discard))):
            return {}
        if bloq_is_rotation(bloq):
            return {SYMBOLIC: 1}
        total = Counter()
        for callee, mult in get_bloq_callee_counts(bloq):
            for key, count in get_callee_cost(callee).items():
                total[key] += count * mult
        return dict(total)

    def zero(self) -> dict:
        return {}

    def __str__(self) -> str:
        return 'rotation resolutions'


def histogram(bloq: Bloq, qec_cache: Optional[dict] = None, hist_cache: Optional[dict] = None) -> Hist:
    return get_cost_value(bloq, RotationResolution(qec_cache), costs_cache=hist_cache if hist_cache is not None else {})


def total_rotations(hist: Hist) -> int:
    return sum(hist.values())


def exact_small_t(pair_t: int = constants.T_PER_AND_PAIR) -> Dict[int, int]:
    """CZ (0), CS (3, ancilla-free), CT (one And pair around a T on the ancilla: 1 + pair_t)."""
    return {1: 0, 2: 3, 3: 1 + pair_t}


EXACT_SMALL_T = exact_small_t()          # at the ledger's And-pair contract (unitary: {1: 0, 2: 3, 3: 9})


def gradient_price(key: Union[int, str], b: int, small_exact: bool = False, pair_t: int = constants.T_PER_AND_PAIR) -> int:
    """T gates for one model rotation as the phase-gradient gadget actually emitted: `pair_t·min(s, b)` (one And
    pair per bit of the added constant — see module doc), `pair_t·b` when the resolution is unknown or the turn
    is rounded, 0 for an identity (s = 0) and for a PURE turn +-2^-s with s >= b + 2 (rounds to zero in the
    b-bit register). `small_exact` prices s = 1, 2, 3 as the exact CZ / CS / CT words (`small_resolution_exact`
    toggle) instead of the gadget. `pair_t`: 8 T per And pair (unitary uncompute, the T4 decision, default) or
    4 T (measured uncompute, Qualtran's convention)."""
    s, pure = split_key(key)
    if s is not None:
        if s == 0 or (pure and s >= b + 2):
            return 0
        if small_exact and s in (1, 2, 3):
            return exact_small_t(pair_t)[s]
        return pair_t * min(s, b)
    if isinstance(key, str) and key.startswith('bin:'):
        lo = int(key[4:].split('-')[0])
        return pair_t * min(lo, b)
    if isinstance(key, str) and key.startswith(PURE + 'bin:'):
        lo = int(key[5:].split('-')[0])
        return 0 if lo >= b + 2 else pair_t * min(lo, b)
    return pair_t * b


def dropped_rotations(hist: Hist, b: int) -> int:
    """Pure rotations the b-bit gradient register rounds away (priced 0 by `gradient_price`)."""
    return sum(count for key, count in hist.items() if gradient_price(key, b) == 0)


def compress(hist: Hist, exact_up_to: int = 128) -> Dict[str, int]:
    """JSON-friendly form: exact resolutions up to `exact_up_to`, power-of-two bins above, plus the
    'generic' and 'symbolic' buckets."""
    out: Counter = Counter()
    for key, count in hist.items():
        s, pure = split_key(key)
        if s is not None:
            prefix = PURE if pure else ''
            if s <= exact_up_to:
                out[f'{prefix}{s}'] += count
            else:
                lo = 1 << (s - 1).bit_length() - 1
                out[f'{prefix}bin:{lo + 1}-{lo * 2}'] += count
        else:
            out[str(key)] += count
    return dict(out)


def gradient_t_count(counts, hist: Hist, b: int, pair_t: int = constants.T_PER_AND_PAIR) -> int:
    """`GateCounts.total_t_count` with rotations priced per resolution bucket instead of one flat rate, and every
    And pair (Qualtran counts the compute; the uncompute is its inverse under the unitary contract) at `pair_t`.
    Toffoli / CSWAP stay at Qualtran's 4 (none occur in the pipeline's leaves; a unitary Toffoli would be 7)."""
    rot = sum(gradient_price(key, b, pair_t=pair_t) * count for key, count in hist.items())
    return int(counts.t + 4 * counts.toffoli + 4 * counts.cswap + pair_t * counts.and_bloq + rot)
