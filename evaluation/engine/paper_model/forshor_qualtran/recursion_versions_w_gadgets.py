"""`ToomRecursivePhaseProductWithGadgets` — a copy of `recursion_versions.py::ToomRecursivePhaseProduct`
that carries a concrete `phi` through the SAME (cost-optimal, version-B/rule-"kmy") recursion, for
`prune_trivial` pruning on the `core="versionB"` pp-core. New file, `recursion_versions.py` untouched.

WHY THIS IS DIFFERENT FROM `recursive_w_gadgets.py` (`core="recursive"`, closed earlier): that core is
what the real ForShor/Lean plan actually emits, and its gadget extension was VERIFIED bit-for-bit
against a real emitted circuit. `core="versionB"` is explicitly a DIFFERENT, HYPOTHETICAL recursion
rule (see `recursion_versions.py`'s own module docstring: "NONE of R1/R2, and none of version A/C, is a
`PhaseLoweringPlan` ForShor proves") — there is NO real circuit anywhere in this repo that versionB's
"kmy" rule (tight width, operands padded to a multiple of k, cost-optimal stop) actually gets emitted
to, so there is nothing to verify a gadget extension against the way core="recursive" was verified.
What follows is therefore a stated, documented MODELING ASSUMPTION, not a proven fact — read this
whole docstring before trusting a `gadgets=True` number from this module.

DESIGN DECISION 1 — freeze the (unpruned) recursion structure. `RuleSet.decision(...)` (the
cost-optimal stop/recurse choice) is called UNCHANGED, exactly as `core="versionB"` already does
without gadgets — `gadgets`/`phi` never influence which nodes recurse or how deep. The alternative
(recompute cost-optimality using PRUNED leaf costs, so a node might recurse deeper post-pruning than
it would pre-pruning) is a genuinely different, NEW optimization policy, not just "apply an existing
theorem" — kept out of scope here, consistent with how `core="recursive"`'s gadget extension also never
touches recursion structure (there, because rule "lean" is not cost-optimal at all, so no such choice
existed; here the choice does exist, and this module explicitly declines it for the same "gadgets prune
gates, not structure" discipline).

DESIGN DECISION 2 — the coefficient-tree child-phi formula, `child_phi = phi * coeff_poly_l(2**m)`, is
carried over UNCHANGED from `recursive_w_gadgets.py`, with `m = ceil(w / k)` (NOT floor) at the padded
width, matching `ArityData.next_width`'s own `pad=True` branch (`m = -(-w // k)`,
recursion_versions.py:108). This is algebraically sound ONLY under an assumption stated explicitly as
DESIGN DECISION 3.

DESIGN DECISION 3 — padding is assumed SIGN-PRESERVING (register width grows by SIGN-extension, not
naive zero-extension), matching how extension is handled everywhere else this repo actually builds
real circuits (`py/qualtran_backend.py`'s `signExtend`/`_apply_extension`, CNOT-from-sign-bit, as
opposed to `zeroExtend`, which is reserved for operands already known non-negative). This choice is
NOT arbitrary: naive zero-extension of a NEGATIVE two's-complement value changes its numeric value
(verified directly: 3-bit `-1` = `0b111`; zero-extending to 4 bits gives `0b0111` = `+7`, not `-1`;
sign-extending gives `0b1111` = `-1`, correctly) and would introduce real cross terms
`2**w*(s_x*z + s_z*x) + 2**(2w)*s_x*s_z` (s_x, s_z the sign bits) that a single scalar `phi` cannot
compensate for. `ToomRecursivePhaseProduct` itself, being a pure resource-COUNT model (no
`build_composite_bloq`, only `build_call_graph` leaf/register counts), does not specify which
convention its "padding" represents -- there is no explicit padding gate anywhere in its call graph.
Sign-preserving is the only convention under which the coefficient-tree model is even well-defined
(the alternative would need cross-term bookkeeping this module does not attempt), and it is the
convention the rest of this codebase actually uses whenever it builds a real circuit. If versionB is
ever wired to an actual circuit builder with a DIFFERENT (e.g. literal zero-extension) convention, this
module's gadget numbers would need re-deriving, not just re-trusting.

WHAT "q" MEANS HERE: `ArityData.q` (`cen["phaseProduct"]`, the Lean-emitted census count) must equal
`len(coeff_polys)` for the branching factor used here to be correct -- checked in
`__attrs_post_init__`, not assumed.

CLAMPING (`clamp=True`, 2026-09-09): the same Thm 5.1 node narrowing as `recursive_w_gadgets.py`
(see that module's docstring for the statement, the signed-register remark, the conservative boundary
and the measured null result on the exact stack). Here the narrowed node keeps its `root` flag, so the
version-B arity rule is applied to it exactly as to any node of that width. The narrowing is decided
BEFORE `RuleSet.decision`, i.e. it does not re-optimise the stop rule (Design Decision 1 stands).
"""
from __future__ import annotations

from fractions import Fraction
from typing import Dict, Optional, Tuple

import attrs
from qualtran import Bloq, QInt, QUInt, Register, Signature
from qualtran.bloqs.arithmetic import Add, Negate
from qualtran.bloqs.basic_gates import CZPowGate
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from . import bundle_compat, constants
from .bloqs.recursive_w_gadgets import _leaf_call_graph, _leaf_residues, clamped_widths
from .mirrors.coeff_poly import poly_eval
from .recursion_versions import RuleSet


@attrs.frozen
class ToomRecursivePhaseProductWithGadgets(Bloq):
    """`ToomRecursivePhaseProduct` plus `gadgets`/`phi` (+ `clamp`). `gadgets=False` (default) matches
    the original exactly (verified, not assumed). See module docstring for what `gadgets=True` assumes
    and why there is no real circuit to verify it against."""
    w: int
    version: str
    k_top: int
    rule: str
    root: bool = True
    tsr: float = constants.TS_PER_ROTATION      # stop-rule price, see ToomRecursivePhaseProduct.tsr
    rules: Optional[RuleSet] = attrs.field(default=None, eq=False, repr=False)
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
                raise ValueError("gadgets=True needs `coeff_polys` (from from_rules)")
            assert self.rules is not None
            q = self.rules.data[self.k_top].q
            if len(self.coeff_polys) != q:
                raise ValueError(f"len(coeff_polys)={len(self.coeff_polys)} != q={q} for k_top="
                                 f"{self.k_top} -- census/coeff_poly mismatch, refusing rather than "
                                 "silently using the wrong branching factor")

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.w)), Register("z", QUInt(self.w))])

    def _clamped(self, census: bool = True) -> Optional["ToomRecursivePhaseProductWithGadgets"]:
        if not (self.gadgets and self.clamp):
            return None
        narrowed = clamped_widths(self.w, self.w, self.phi, census)
        return attrs.evolve(self, w=narrowed[0]) if narrowed else None

    def _children(self, k: int, W2: int):
        d = self.rules.data[k]
        if not self.gadgets:
            return [attrs.evolve(self, w=W2, root=False)] * d.q
        m = -(-self.w // k)   # ceil(w/k), matching ArityData.next_width's pad=True branch exactly
        phis = [self.phi * poly_eval(list(poly), Fraction(2) ** m) for poly in self.coeff_polys]
        return [attrs.evolve(self, w=W2, root=False, phi=p) for p in phis]

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        assert self.rules is not None
        narrowed = self._clamped()
        if narrowed is not None:
            return {narrowed: 1}
        dec = self.rules.decision(self.w, self.version, self.k_top, self.rule, self.root, self.tsr)
        if dec is None:
            if not self.gadgets:
                return {CZPowGate(exponent=ssa.new_symbol("e")): self.w * self.w}
            return _leaf_call_graph(self.w, self.w, self.phi)
        k, W2 = dec
        if self.version == "B":
            assert k == self.k_top, f"version B recursed at k={k} != k_top={self.k_top} (single-k violated)"
        d = self.rules.data[k]
        counts: Dict[Bloq, int] = {}
        for child in self._children(k, W2):
            counts[child] = counts.get(child, 0) + 1
        sides = 2
        if d.n_add:
            counts[Add(QInt(W2))] = sides * d.n_add
        n_negate = sides * (d.n_neg + 2 * d.n_add_negsrc)
        if n_negate:
            counts[Negate(QInt(W2))] = n_negate
        return counts

    def total_czpow(self, memo: Optional[dict] = None) -> int:
        assert self.rules is not None
        if not self.gadgets:
            return int(round(sum(r["leaf_T"] for r in self.rules.tree(self.w, self.version, self.k_top, self.rule, self.tsr)
                                 if r["stop"]) / (constants.TS_PER_TOFFOLI + self.tsr)))
        if memo is None:
            memo = {}
        if self in memo:
            return memo[self]
        narrowed = self._clamped(census=False)
        if narrowed is not None:
            val = narrowed.total_czpow(memo)
        else:
            dec = self.rules.decision(self.w, self.version, self.k_top, self.rule, self.root, self.tsr)
            if dec is None:
                val = sum(_leaf_residues(self.w, self.w, self.phi)[0].values())
            else:
                k, W2 = dec
                val = sum(c.total_czpow(memo) for c in self._children(k, W2))
        memo[self] = val
        return val

    @property
    def qubit_footprint(self) -> int:
        """Identical to the original -- gadgets prune GATES, never registers, so the R2-model
        padding-slack footprint (unaffected by gadgets, uses `rules.tree` directly) is unchanged."""
        assert self.rules is not None
        slack = 0
        for r in self.rules.tree(self.w, self.version, self.k_top, self.rule, self.tsr):
            if not r["stop"]:
                k = r["k"]
                slack += 2 * (k * (-(-r["W"] // k)) - r["W"])
        return 2 * self.w + slack + 1

    @classmethod
    def from_rules(cls, rules: RuleSet, w: int, k_top: int, version: str = "B", rule: str = "kmy",
                   gadgets: bool = False, phi: Optional[Fraction] = None,
                   clamp: bool = False, tsr: float = constants.TS_PER_ROTATION) -> "ToomRecursivePhaseProductWithGadgets":
        coeff_polys = None
        if gadgets:
            bundle = rules.bundles[k_top]
            coeff_polys = bundle_compat.coeff_polys(bundle.doc)
        return cls(w=w, version=version, k_top=k_top, rule=rule, rules=rules, gadgets=gadgets, phi=phi,
                   coeff_polys=coeff_polys, clamp=clamp, tsr=tsr)
