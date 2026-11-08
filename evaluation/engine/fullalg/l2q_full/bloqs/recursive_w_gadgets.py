"""`LeanRecursivePhaseProductWithGadgets` / `LeanAsymRecursivePhaseProductWithGadgets` — copies of
`bloqs/recursive.py`'s two classes that carry a concrete `phi` THROUGH the same width recursion, for
exact `prune_trivial` (Thm 3.1) pruning on the Lean-backed `core="recursive"` pp-core. New file,
`recursive.py` untouched — `LeanRecursivePhaseProduct`/`LeanAsymRecursivePhaseProduct` and everything
that depends on them (frame-invariance, the priced headline curves) are unaffected.

THE RECURSION RULE (from `lean/ForshorEmit/LowerRec.lean::lowerLeaf`, lines 119-136): at width (wx, wz),
if `nextSignedWidth(wx,wz) < max(wx,wz)`, the phase product does NOT stay one leaf — it expands into
`q = len(coeff_polys)` children, each a UNIFORM SYMMETRIC phase product at the narrower width `W2 =
nextSignedWidth(wx,wz)`, with the child's angle

    child_phi = phi * coeff_poly_l(2 ** m),   m = min(wx, wz) // k   (phaseLimbWidth, DefsCore.lean:127)

for each of the `q` Toom-Cook coefficient polynomials `coeff_poly_l` (the SAME `coeff_polys` field
`phase_product.LeanPhaseProduct` already parses from the bundle — this is not new data, just applied at
every level instead of once). Recursion continues on each child until a level's `W2 >= max(wx,wz)`, at
which point that leaf is a real, single `Naive_SignedPhaseProd(phi, wx, wz)` gate and its exponent for
signed bit-weight pair `(wa, wb)` is `(phi * wa * wb) mod 2` — `prune_trivial` drops the zero ones,
exactly `phase_product.py`'s `_leaf_exponents()` base-case formula, now applied at whatever phi this
leaf actually carries after however many recursion levels.

VERIFIED, not assumed (this sandbox has a cached REAL-LEAN bundle, not a mirror): this exact recursive
child-phi rule was checked against the REAL emitted circuit at k=2, n=8 and n=16 for the STANDALONE
symmetric case (`emit.lower_rec`) — EXACT multiset match of every leaf angle at depth 1 (3 leaves) and
depth 4 (81 leaves) respectively — and against a REAL `emit.lower_cmodmul` circuit's asymmetric U1/U5
sites (k=2, n=8, c=2, N=127) — EXACT multiset match of all 1458 combined U1+U5 leaves, and U2's 729
leaves confirmed as an exact subset of that width's real leaves (the other 54 leaves at the same
coincidental width belong to an unrelated QFT twiddle recursion, identified by path and by their
angles' much smaller numerators — not a modeling gap). See
`tests/test_recursive_w_gadgets.py::test_recursive_phi_propagation_matches_real_lean_ir`.

WHAT DOESN'T CHANGE: the width recursion itself (`next_width`, the ladder, `reserve_qubits`,
`qubit_footprint`) is IDENTICAL to `recursive.py` — gadgets prune GATES, never registers or the
recursion structure. `gadgets=False` reproduces `LeanRecursivePhaseProduct`/
`LeanAsymRecursivePhaseProduct` exactly (verified in tests, not assumed).

`core="versionB"` (`recursion_versions.ToomRecursivePhaseProduct`) is a deliberately DIFFERENT,
hypothetical recursion rule (Toom-k at every level, not ForShor's own adaptive `nextSignedWidth`) — this
module does not touch it; see `recursion_versions_w_gadgets.py`.

MODULUS CLAMPING (`clamp=True`, added 2026-09-09; Thm 5.1 of `dyadic_phase_structure.2026-08-26.tex`).
For a node whose turn `phi/2 = c/2^t` is dyadic in lowest terms with `0 < t < max(wx, wz)`, the phase
product equals the identity on the high bits tensored with the SAME phase product on the low `t` bits
of each operand: `xy - x_lo*y_lo` is a multiple of `2^t` and `turn * 2^t` is an integer. The pipeline
models registers as two's-complement signed, so note the low-`t`-bit register read as SIGNED still
realises the same unitary: the signed value differs from `x mod 2^t` by a multiple of `2^t`, and that
difference again contributes an integer turn. Hence the clamped node is EXACTLY the node of this same
class at widths `(min(wx, t), min(wz, t))`, same phi — its own (narrower) recursion, adders and leaves.

Two deliberate boundaries of this implementation:
  * CONSERVATIVE: only the clamped node's own subtree narrows. Thm 5.1 also lets the PARENT's
    combination adders that feed a clamped child run modulo `2^t`; those adders are shared by all `q`
    siblings (with differing resolutions), so they are left at the parent's width. The clamped count
    is therefore an exact circuit's count that may slightly OVER-state the best clamped circuit; it
    never under-states.
  * Clamping does not fire for a non-dyadic turn (odd factors in the denominator) — there is no `t`,
    and clamping would be a fiction (same refusal as `kmy_phaseproduct_skeleton.PhaseProduct`).
    Turns that are already integral (`t == 0`) are prune_trivial's endpoint and are left to the
    existing leaf pruning (structure frozen, as above).
`CLAMP_STATS` is a per-process census (nodes seen / dyadic / clamped) that callers reset per pricing
pass — it exists so a "clamp on" estimate can say WHERE the theorem fired, not just report a number.
MEASURED 2026-09-09 on the exact Lean modmul at N = 2^n - 1, cores recursive/versionB/onelevel, k = 2, 3,
n = 16..256: thousands of dyadic nodes (the U2 phase product, turn N / 2^(w_work + w_carry), and its
descendants) but ZERO nodes with resolution below width — U2's resolution is the SUM of its operand
widths, so it stays above every width in its recursion, while U1/U5 (denominator N) are never dyadic.
So on the exact stack `clamp=True` is a proven no-op; it bites only for phase products whose resolution
is genuinely below their width (the bounded-precision regime of `py/kmy_gadgets.py`).

PERFORMANCE (2026-09-09): the per-leaf exponent multiset is now computed in integer arithmetic on the
turn's numerator/denominator (`_leaf_residues`) rather than with one `Fraction` multiply-and-mod per
bit-pair group; the result is identical (same reduced rationals, same floats — `r / q` for ints is the
correctly-rounded quotient `float(Fraction(r, q))` also uses). `total_czpow` takes an optional memo so
the census traversal is linear in the number of DISTINCT nodes, as Qualtran's cost traversal already is.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, Iterator, Optional, Tuple, Union

import attrs
from qualtran import Bloq, QInt, QUInt, Register, Signature
from qualtran.bloqs.arithmetic import Add, Negate
from qualtran.bloqs.basic_gates import CZPowGate
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from .. import bundle_compat, widths
from ..emit import Bundle
from ..mirrors.coeff_poly import poly_eval

# ---------------------------------------------------------------------------------------------
# Leaf exponent multisets (closed form, integer arithmetic)
# ---------------------------------------------------------------------------------------------

def _leaf_groups(wx: int, wz: int) -> Iterator[Tuple[int, int, int]]:
    """The four-case partition of the signed `wx × wz` bit-pair grid as `(s, sign, multiplicity)`:
    every bit weight is `+2**i` except the top (sign) bit, `-2**(w-1)`, so `wa * wb = sign * 2**s`
    depends on `(i, j)` only through `s = i + j` and which of the two, if either, is a sign bit.
    (Generalises `py/qualtran_backend.py::CompactSignedPhaseGrid` to asymmetric widths.)"""
    non_sign_x, non_sign_z = wx - 1, wz - 1
    # Case 1: bulk, neither index is a sign bit -- weight = +2**(i+j).
    for s in range(non_sign_x + non_sign_z - 1):
        cnt = min(s, non_sign_x - 1, non_sign_z - 1, non_sign_x + non_sign_z - 2 - s) + 1
        if cnt > 0:
            yield s, 1, cnt
    # Case 2: i is x's sign bit, j is not -- weight = -2**(i+j).
    if wx >= 1:
        for j in range(non_sign_z):
            yield (wx - 1) + j, -1, 1
    # Case 3: j is z's sign bit, i is not -- weight = -2**(i+j).
    if wz >= 1:
        for i in range(non_sign_x):
            yield i + (wz - 1), -1, 1
    # Case 4: both are sign bits -- weight = +2**((wx-1)+(wz-1)).
    if wx >= 1 and wz >= 1:
        yield (wx - 1) + (wz - 1), 1, 1


def _leaf_residues(wx: int, wz: int, phi: Fraction) -> Tuple[Dict[int, int], int]:
    """`({r: multiplicity}, q)` with every leaf exponent `(phi * wa * wb) mod 2 == r / q`, zero
    residues (trivial gates, Thm 3.1) dropped. `phi = p / q` in lowest terms, `r = (±p·2^s) mod 2q`."""
    p, q = phi.numerator, phi.denominator
    two_q = 2 * q
    out: Dict[int, int] = {}
    for s, sign, mult in _leaf_groups(wx, wz):
        r = ((p << s) if sign > 0 else -(p << s)) % two_q
        if r:
            out[r] = out.get(r, 0) + mult
    return out, q


def _flat_leaf_exponents(wx: int, wz: int, phi: Fraction) -> Dict[Fraction, int]:
    """One `Naive_SignedPhaseProd(phi, wx, wz)` leaf's exact exponent multiset, zero-mod-2 (trivial)
    entries already dropped -- `prune_trivial`, Thm 3.1. Exact rationals in lowest terms; see
    `_leaf_residues` for the integer core (`tests/test_gadgets_wiring.py` pins this against the
    original double loop)."""
    residues, q = _leaf_residues(wx, wz, phi)
    return {Fraction(r, q): mult for r, mult in residues.items()}


def _flat_leaf_float_exponents(wx: int, wz: int, phi: Fraction) -> Dict[float, int]:
    """Same multiset keyed by the `CZPowGate.exponent` float Qualtran will classify. `r / q` on ints is
    the correctly rounded quotient, i.e. exactly `float(Fraction(r, q))`."""
    residues, q = _leaf_residues(wx, wz, phi)
    out: Dict[float, int] = {}
    for r, mult in residues.items():
        f = r / q
        out[f] = out.get(f, 0) + mult
    return out


def _leaf_call_graph(wx: int, wz: int, phi: Fraction) -> BloqCountDictT:
    return {CZPowGate(exponent=f): mult for f, mult in _flat_leaf_float_exponents(wx, wz, phi).items()}


def _child_phis(coeff_polys: Tuple[Tuple[Fraction, ...], ...], m: int, phi: Fraction) -> list:
    b = Fraction(2) ** m
    return [phi * poly_eval(list(poly), b) for poly in coeff_polys]


# ---------------------------------------------------------------------------------------------
# Modulus clamping (Thm 5.1)
# ---------------------------------------------------------------------------------------------

#: Census of the most recent clamp-aware pricing pass (see module docstring). Reset with
#: `reset_clamp_stats()` before a pass; Qualtran visits each DISTINCT node once, so `nodes` counts
#: distinct phase-product nodes, `dyadic` those with a dyadic turn, `clamped` those Thm 5.1 narrowed.
CLAMP_STATS: Dict[str, int] = {'nodes': 0, 'dyadic': 0, 'clamped': 0}


def reset_clamp_stats() -> None:
    CLAMP_STATS.update(nodes=0, dyadic=0, clamped=0)


def phase_resolution(phi: Fraction) -> Optional[int]:
    """`t` such that the turn `phi / 2 = c / 2^t` in lowest terms (`c` odd), or None when the turn is
    not dyadic (an odd factor survives in the denominator -- Thm 5.1 does not apply)."""
    p, q = phi.numerator, phi.denominator            # lowest terms, so gcd(p, 2q) is 1 or 2
    den = q if p % 2 == 0 else 2 * q                 # denominator of p / (2q) in lowest terms
    if den & (den - 1):
        return None
    return den.bit_length() - 1


def clamped_widths(wx: int, wz: int, phi: Fraction, census: bool = True) -> Optional[Tuple[int, int]]:
    """`(wx', wz')` when Thm 5.1 narrows a node of widths `(wx, wz)` carrying angle `phi`, else None.
    Updates `CLAMP_STATS` when `census` (the call-graph pass; the czpow census pass passes False so a
    node is counted once)."""
    t = phase_resolution(phi)
    if census:
        CLAMP_STATS['nodes'] += 1
        CLAMP_STATS['dyadic'] += t is not None
    if t is None:
        return None
    if 0 < t < max(wx, wz):
        if census:
            CLAMP_STATS['clamped'] += 1
        return min(wx, t), min(wz, t)
    return None


def _adder_counts(counts: Dict[Bloq, int], w2: int, n_add: int, n_neg: int, n_add_negsrc: int) -> None:
    sides = 2
    if n_add:
        counts[Add(QInt(w2))] = sides * n_add
    n_negate = sides * (n_neg + 2 * n_add_negsrc)
    if n_negate:
        counts[Negate(QInt(w2))] = n_negate


# ---------------------------------------------------------------------------------------------
# Bloqs
# ---------------------------------------------------------------------------------------------

@attrs.frozen
class LeanRecursivePhaseProductWithGadgets(Bloq):
    """Symmetric recursive phase product (E7). `gadgets=False` (default) matches
    `LeanRecursivePhaseProduct` exactly. `gadgets=True` needs a concrete `phi` (+ `coeff_polys`,
    `ops`, both from `from_bundle`) and prunes exactly, at every recursion level; `clamp=True`
    additionally narrows dyadic nodes per Thm 5.1 -- see module docstring."""
    k: int
    w: int
    q: int
    n_add: int
    n_neg: int
    n_add_negsrc: int = 0
    policy: str = "sym"
    rule: Optional[widths.WidthRule] = attrs.field(default=None, eq=False, repr=False)
    general: Optional[dict] = attrs.field(default=None, eq=False, repr=False)
    gadgets: bool = False
    phi: Optional[Fraction] = None
    coeff_polys: Optional[Tuple[Tuple[Fraction, ...], ...]] = attrs.field(default=None, eq=False, repr=False)
    clamp: bool = False

    def __attrs_post_init__(self):
        if self.clamp and not self.gadgets:
            raise ValueError("clamp=True needs gadgets=True (a concrete phi to take the resolution of)")
        if self.gadgets:
            if self.phi is None:
                raise ValueError("gadgets=True needs a concrete `phi`")
            if not isinstance(self.phi, Fraction):
                raise TypeError("phi must be an exact fractions.Fraction, never a float")
            if not self.coeff_polys:
                raise ValueError("gadgets=True needs `coeff_polys` (from from_bundle)")
            if len(self.coeff_polys) != self.q:
                raise ValueError(f"len(coeff_polys)={len(self.coeff_polys)} != q={self.q} -- "
                                 "census/coeff_poly mismatch, refusing rather than silently "
                                 "using the wrong branching factor")

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.w)), Register("z", QUInt(self.w))])

    def next_width(self) -> int:
        if self.general is not None and self.w in self.general:
            return self.general[self.w]
        assert self.rule is not None
        return self.rule.at_n(self.w)

    def ladder(self):
        out, cur = [self.w], self
        while True:
            nxt = cur.next_width()
            if nxt < cur.w:
                out.append(nxt)
                cur = attrs.evolve(cur, w=nxt)
            else:
                return out

    def reserve_qubits(self):
        from ..mirrors.schedule import raw_table
        from ..mirrors.width import reserve_need
        ops = raw_table(self.k)[0]["ops"]
        return reserve_need(self.k, ops, self.w, self.w)

    @property
    def qubit_footprint(self) -> int:
        rx, rz = self.reserve_qubits()
        return 2 * self.w + rx + rz + 1

    def _clamped(self, census: bool = True) -> Optional["LeanRecursivePhaseProductWithGadgets"]:
        """The Thm 5.1-narrowed node, or None when clamping is off / does not apply here."""
        if not (self.gadgets and self.clamp):
            return None
        narrowed = clamped_widths(self.w, self.w, self.phi, census)
        return attrs.evolve(self, w=narrowed[0]) if narrowed else None

    def _children(self, w2: int):
        if not self.gadgets:
            return [attrs.evolve(self, w=w2)] * self.q     # q copies of ONE object (parity path)
        m = self.w // self.k
        phis = _child_phis(self.coeff_polys, m, self.phi)
        return [attrs.evolve(self, w=w2, phi=p) for p in phis]

    def total_czpow(self, memo: Optional[dict] = None) -> int:
        if not self.gadgets:
            lad = self.ladder()
            return (self.q ** (len(lad) - 1)) * lad[-1] ** 2
        if memo is None:
            memo = {}
        if self in memo:
            return memo[self]
        narrowed = self._clamped(census=False)
        if narrowed is not None:
            val = narrowed.total_czpow(memo)
        else:
            w2 = self.next_width()
            if w2 < self.w:
                val = sum(c.total_czpow(memo) for c in self._children(w2))
            else:
                val = sum(_leaf_residues(self.w, self.w, self.phi)[0].values())
        memo[self] = val
        return val

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        narrowed = self._clamped()
        if narrowed is not None:
            return {narrowed: 1}
        w2 = self.next_width()
        if w2 < self.w:
            counts: Dict[Bloq, int] = {}
            for child in self._children(w2):
                counts[child] = counts.get(child, 0) + 1
            _adder_counts(counts, w2, self.n_add, self.n_neg, self.n_add_negsrc)
            return counts
        if not self.gadgets:
            return {CZPowGate(exponent=ssa.new_symbol("e")): self.w * self.w}
        return _leaf_call_graph(self.w, self.w, self.phi)

    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], w: int, policy: str = "sym",
                    gadgets: bool = False, phi: Optional[Fraction] = None,
                    clamp: bool = False) -> "LeanRecursivePhaseProductWithGadgets":
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        # `bundle_compat.schedule_census`, not the raw census: v2 reports `addScaled` as the
        # TOTAL and marks `negSrc` on each op, so the separate `addScaled_negSrc` count has to
        # be recovered from the ops. Defaulting it to 0 silently dropped every Negate from the
        # priced call graph -- measured at k=2 m=2 as 24 And against the IR's 48, because the
        # backend realises a negSrc add as Negate . Add . Negate.
        cen = bundle_compat.schedule_census(doc)
        rule = widths.width_rule(doc, policy)  # type: ignore[arg-type]
        general = widths.general_widths(doc, policy)  # type: ignore[arg-type]
        coeff_polys = None
        if gadgets:
            coeff_polys = bundle_compat.coeff_polys(doc)
        return cls(k=doc["k"], w=w, q=cen["phaseProduct"], n_add=cen["addScaled"], n_neg=cen["negate"],
                  n_add_negsrc=cen.get("addScaled_negSrc", 0), policy=policy, rule=rule, general=general,
                  gadgets=gadgets, phi=phi, coeff_polys=coeff_polys, clamp=clamp)

    def __str__(self) -> str:
        g = f", gadgets(phi={self.phi}{', clamp' if self.clamp else ''})" if self.gadgets else ""
        return f"LeanRecursivePPWithGadgets(k={self.k}, w={self.w}, {self.policy}{g})"


@attrs.frozen
class LeanAsymRecursivePhaseProductWithGadgets(Bloq):
    """Asymmetric top level (cmodmul's real internal phase products); recurses into
    `LeanRecursivePhaseProductWithGadgets` children exactly like `LeanAsymRecursivePhaseProduct` does
    for `LeanRecursivePhaseProduct`. `gadgets=False` matches the original exactly."""
    k: int
    wx: int
    wz: int
    q: int
    n_add: int
    n_neg: int
    n_add_negsrc: int = 0
    policy: str = "sym"
    rule: Optional[widths.WidthRule] = attrs.field(default=None, eq=False, repr=False)
    general: Optional[dict] = attrs.field(default=None, eq=False, repr=False)
    ops: Optional[tuple] = attrs.field(default=None, eq=False, repr=False)
    gadgets: bool = False
    phi: Optional[Fraction] = None
    coeff_polys: Optional[Tuple[Tuple[Fraction, ...], ...]] = attrs.field(default=None, eq=False, repr=False)
    clamp: bool = False

    def __attrs_post_init__(self):
        if self.clamp and not self.gadgets:
            raise ValueError("clamp=True needs gadgets=True (a concrete phi to take the resolution of)")
        if self.gadgets:
            if self.phi is None:
                raise ValueError("gadgets=True needs a concrete `phi`")
            if not isinstance(self.phi, Fraction):
                raise TypeError("phi must be an exact fractions.Fraction, never a float")
            if not self.coeff_polys:
                raise ValueError("gadgets=True needs `coeff_polys` (from from_bundle)")
            if len(self.coeff_polys) != self.q:
                raise ValueError(f"len(coeff_polys)={len(self.coeff_polys)} != q={self.q}")

    @property
    def w(self) -> int:
        return max(self.wx, self.wz)

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.wx)), Register("z", QUInt(self.wz))])

    def next_width(self) -> int:
        assert self.ops is not None, "LeanAsymRecursivePhaseProductWithGadgets needs `ops` (from from_bundle)"
        from ..mirrors.width import next_width as _next_width
        return _next_width(self.k, self.ops, self.wx, self.wz)

    def reserve_qubits(self):
        from ..mirrors.width import reserve_need
        return reserve_need(self.k, self.ops, self.wx, self.wz)

    @property
    def qubit_footprint(self) -> int:
        rx, rz = self.reserve_qubits()
        return self.wx + self.wz + rx + rz + 1

    def _clamped(self, census: bool = True) -> Optional["LeanAsymRecursivePhaseProductWithGadgets"]:
        if not (self.gadgets and self.clamp):
            return None
        narrowed = clamped_widths(self.wx, self.wz, self.phi, census)
        return attrs.evolve(self, wx=narrowed[0], wz=narrowed[1]) if narrowed else None

    def _children(self, w2: int):
        common = dict(k=self.k, w=w2, q=self.q, n_add=self.n_add, n_neg=self.n_neg,
                      n_add_negsrc=self.n_add_negsrc, policy=self.policy, rule=self.rule,
                      general=self.general)
        if not self.gadgets:
            return [LeanRecursivePhaseProductWithGadgets(**common)] * self.q
        m = min(self.wx, self.wz) // self.k
        phis = _child_phis(self.coeff_polys, m, self.phi)
        return [LeanRecursivePhaseProductWithGadgets(**common, gadgets=True, phi=p, coeff_polys=self.coeff_polys,
                                                     clamp=self.clamp) for p in phis]

    def total_czpow(self, memo: Optional[dict] = None) -> int:
        w2 = self.next_width()
        if not self.gadgets:
            if w2 < max(self.wx, self.wz):
                return self.q * self._children(w2)[0].total_czpow()
            return self.wx * self.wz
        if memo is None:
            memo = {}
        if self in memo:
            return memo[self]
        narrowed = self._clamped(census=False)
        if narrowed is not None:
            val = narrowed.total_czpow(memo)
        elif w2 < max(self.wx, self.wz):
            val = sum(c.total_czpow(memo) for c in self._children(w2))
        else:
            val = sum(_leaf_residues(self.wx, self.wz, self.phi)[0].values())
        memo[self] = val
        return val

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        narrowed = self._clamped()
        if narrowed is not None:
            return {narrowed: 1}
        w2 = self.next_width()
        if w2 < max(self.wx, self.wz):
            counts: Dict[Bloq, int] = {}
            for child in self._children(w2):
                counts[child] = counts.get(child, 0) + 1
            _adder_counts(counts, w2, self.n_add, self.n_neg, self.n_add_negsrc)
            return counts
        if not self.gadgets:
            return {CZPowGate(exponent=ssa.new_symbol("e")): self.wx * self.wz}
        return _leaf_call_graph(self.wx, self.wz, self.phi)

    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], wx: int, wz: int, policy: str = "sym",
                    gadgets: bool = False, phi: Optional[Fraction] = None,
                    clamp: bool = False) -> "LeanAsymRecursivePhaseProductWithGadgets":
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        # `bundle_compat.schedule_census`, not the raw census: v2 reports `addScaled` as the
        # TOTAL and marks `negSrc` on each op, so the separate `addScaled_negSrc` count has to
        # be recovered from the ops. Defaulting it to 0 silently dropped every Negate from the
        # priced call graph -- measured at k=2 m=2 as 24 And against the IR's 48, because the
        # backend realises a negSrc add as Negate . Add . Negate.
        cen = bundle_compat.schedule_census(doc)
        rule = widths.width_rule(doc, policy)  # type: ignore[arg-type]
        general = widths.general_widths(doc, policy)  # type: ignore[arg-type]
        from ..mirrors.schedule import raw_table
        ops = tuple(raw_table(doc["k"])[0]["ops"])
        coeff_polys = None
        if gadgets:
            coeff_polys = bundle_compat.coeff_polys(doc)
        return cls(k=doc["k"], wx=wx, wz=wz, q=cen["phaseProduct"], n_add=cen["addScaled"],
                  n_neg=cen["negate"], n_add_negsrc=cen.get("addScaled_negSrc", 0), policy=policy,
                  rule=rule, general=general, ops=ops, gadgets=gadgets, phi=phi, coeff_polys=coeff_polys,
                  clamp=clamp)

    def __str__(self) -> str:
        g = f", gadgets(phi={self.phi}{', clamp' if self.clamp else ''})" if self.gadgets else ""
        return f"LeanAsymRecursivePPWithGadgets(k={self.k}, wx={self.wx}, wz={self.wz}{g})"
