"""`LeanCtrlModMulWithGadgets` — a copy of `bloqs/modmul.py::LeanCtrlModMul` that FORWARDS gadget
kwargs into its internal phase-product construction, for the roles where that is actually possible
today. This is a NEW file, not an edit to `modmul.py` — `LeanCtrlModMul` and its frame-invariance
proof-of-concept (`method_compare.frame_invariance`) are completely untouched.

WHY A SEPARATE FILE, NOT AN EDIT: this needed investigating, not assuming. `LeanCtrlModMul._pp()`
dispatches to three different phase-product bloqs depending on `core`. ALL THREE are now gadget-aware:

  core="onelevel"   -> `phase_product.LeanPhaseProduct`'s existing `phi`/`leaf_policy="reduced"` path.
  core="recursive"  -> `recursive_w_gadgets.LeanAsymRecursivePhaseProductWithGadgets` -- the Lean-backed
                       headline core. VERIFIED against the real emitted circuit, node-level AND
                       bit-level (exact CZPow-exponent multiset match) -- see that module's docstring.
  core="versionB"   -> `recursion_versions_w_gadgets.ToomRecursivePhaseProductWithGadgets` -- a
                       deliberately different, hypothetical (non-Lean) recursion rule. There is NO
                       real circuit anywhere to verify this against; its gadget extension rests on a
                       stated, documented modeling assumption (sign-preserving padding) rather than
                       external ground truth -- see that module's docstring before trusting a number
                       from it, and `docs/history/RESULT_VERSIONB_GADGETS.2026-09-08.md`.

`onelevel` is TEXTBOOK-badged in `engine_registry.py`; `recursive` and `versionB` are both LEAN_BACKED
(widths from the REAL-LEAN bundle either way) -- gadget pricing now reaches both headline curves, at
two different levels of rigor (verified vs. stated-assumption), stated as such, not blurred together.

THE PHI QUESTION — RESOLVED, NOT GUESSED. An earlier version of this module took a caller-supplied
`phi: Fraction` and flagged as an OPEN QUESTION whether a single scalar phi is the physically-correct
angle for every one of `CmodMulInPlaceCore`'s internal phase-product call sites (U1, U2, U5). It is
not a single value, and the answer is not a guess: `lean/ForshorEmit/ModMulEmitLower.lean:106-116`
(`Shor.ForshorEmit.phi1/phi2/phi5`) gives THREE distinct closed-form exact-rational formulas, in units
of pi:

    phi1(c, N)         = 2 * ((c + N - 1) mod N) / N                    -- U1: H;CPhaseProd(ctrl;data,work);IQFT
    phi2(N, ww, wc)     = 2 * N / 2**(ww + wc)                            -- U2: QFT;PhaseProd(work,dataCarry);IQFT
    phi5(c, N)         = 2 * ((1 + N - modinv(c, N)) mod N) / N          -- U5: adjoint of a phi5 version of U1

(`ww` = work width = `n + extra_bits(eta)`, `wc` = dataCarry width = `n + 1`; `modinv(c, N)` needs
`gcd(c, N) = 1`, exactly ForShor's own precondition for `step5Constant`, checked here the same way
Lean's `modInv` blocking check does.) phi1 and phi5 depend on the controlled multiplier `c`; phi2 does
NOT (only on N and the widths) — this asymmetry is real, not a simplification.

VERIFIED, not assumed: these three Python closed forms were checked against the REAL
`emit.lower_cmodmul(k, n, c, N, eta)` IR (this sandbox has a cached REAL-LEAN bundle) at multiple
(k, n, c, N) combinations — k=2..4, several (n, c, N) each — by walking the emitted circuit for its
`NaiveCSignedPhaseProd` (U1, U5 — controlled) and `NaiveSignedPhaseProd` (U2 and QFT twiddles —
uncontrolled) leaves and comparing EXACT Fraction equality against phi1/phi2/phi5 computed here, at
n small enough that no Toom-Cook recursion fires (so each site is exactly one leaf, unambiguous). All
matched exactly, register-width-tagged to confirm site identity (U1: x=n, z=ww; U2: x=ww, z=wc; U5:
x=wc, z=ww) — see `tests/test_modmul_w_gadgets.py::test_phi_formulas_match_real_lean_ir`. This closes
the "open question" from the earlier version of this module: for `core="onelevel"`, gadget pricing is
now an EXACT application of `prune_trivial` at the algorithm's real angles, not a mechanism demo at an
arbitrary one.

NOT WIRED into engine_registry.py/engine_export.py's active pricing path, even though all three cores
now support `gadgets=True` without raising. The reason is no longer "would crash a core" -- it is that
`gadgets=True` prices ONE CONCRETE (c, N) instance (a specific QPE round), not the generic
representative step `LeanCtrlModMul`/the engine's pricer currently prices. Deciding how a "gadgets on"
resource ESTIMATE (as opposed to a one-off concrete pricing) should represent that -- e.g. which c to
use as representative, or whether to price and aggregate over all QPE rounds -- is a real, undecided
design question, not attempted here. This stays a standalone, independently importable/testable module
until that question is answered.
"""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Any, Dict, Optional, Union

import attrs
from qualtran import Bloq, QBit, QUInt, Register, Signature
from qualtran.bloqs.arithmetic import AddK, GreaterThanConstant, LessThanConstant
from qualtran.bloqs.basic_gates import Hadamard
from qualtran.bloqs.mcmt import And
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator
from qualtran.symbolics import SymbolicInt

from ..emit import Bundle
from .modmul import extra_bits
from .phase_product import LeanPhaseProduct
from .qft import LeanQFT, ReferenceQFT

class Step5ConstantUndefined(ValueError):
    """gcd(c, N) != 1 -- step5Constant (hence phi5) is undefined, exactly ForShor's own precondition
    (`ModMulEmitLower.lean::modInv`'s blocking check)."""


def modinv(c: int, N: int) -> int:
    """`c^-1 mod N`, matching `ModMulEmitLower.lean::modInv` exactly (including its blocking check,
    here just Python's own guarantee: `pow(c, -1, N)` raises ValueError itself when gcd(c, N) != 1)."""
    try:
        return pow(int(c), -1, int(N))
    except ValueError as exc:
        raise Step5ConstantUndefined(
            f"modinv({c}, {N}): gcd != 1, step5Constant (and phi5) undefined -- refusing, matching "
            f"ModMulEmitLower.lean::modInv's blocking check") from exc


def step5_constant(c: int, N: int) -> int:
    """`(1 + N - c^-1 mod N) mod N`, matching `ModMulEmitLower.lean::step5Constant` exactly."""
    return (1 + N - modinv(c, N)) % N


def phi1(c: int, N: int) -> Fraction:
    """U1's controlled phase-product angle (units of pi). `ModMulEmitLower.lean::phi1`, line ~106-108.
    Depends on c (the controlled multiplier)."""
    return Fraction(2 * ((int(c) + int(N) - 1) % int(N)), int(N))


def phi2(N: int, ww: int, wc: int) -> Fraction:
    """U2's UNCONTROLLED phase-product angle (units of pi). `ModMulEmitLower.lean::phi2`, line
    ~110-112. Does NOT depend on c -- only on the modulus and the two operand widths."""
    return Fraction(2 * int(N), 2 ** (int(ww) + int(wc)))


def phi5(c: int, N: int) -> Fraction:
    """U5's controlled phase-product angle (units of pi) -- U5 is the adjoint of a phi1-shaped block
    at k5 = step5Constant(c, N) instead of c. `ModMulEmitLower.lean::phi5`, line ~114-116."""
    k5 = step5_constant(c, N)
    return Fraction(2 * (k5 % int(N)), int(N))


@attrs.frozen
class LeanCtrlModMulWithGadgets(Bloq):
    """`LeanCtrlModMul` plus `gadgets`/`c`. `gadgets=False` (default) reproduces `LeanCtrlModMul`'s
    signature, qubit_footprint, and build_call_graph EXACTLY -- see
    tests/test_modmul_w_gadgets.py::test_gadgets_off_matches_lean_ctrl_modmul_exactly.

    `gadgets=True` prices ONE CONCRETE controlled multiply `x -> c*x mod N` (needs `c`, and `N` must
    be the real modulus, not the default N=0 placeholder) -- a specific QPE round, not
    LeanCtrlModMul's generic representative step. That is a real semantic difference from
    LeanCtrlModMul, not a detail: the returned T-count is for THIS c, and prune_trivial's savings
    (which leaves are trivial) genuinely depend on which c is used, per phi1/phi5's own formulas."""
    k: int
    n: int
    eta: float = 1e-3
    N: int = 0                     # must be the REAL modulus (not 0) when gadgets=True
    policy: str = "tight"
    qft: str = "lean"
    core: str = "onelevel"
    depth: str = "lean"
    bundle_doc: dict = attrs.field(default=None, eq=False, repr=False)
    rules: Optional[Any] = attrs.field(default=None, eq=False, repr=False)
    #: directive: apply prune_trivial (Thm 3.1) at the algorithm's REAL phi1/phi2/phi5 angles (see
    #: module docstring). Implemented for all three cores, at two different levels of rigor:
    #: onelevel/recursive via LeanPhaseProduct's exact leaf_policy="reduced" path (recursive:
    #: verified against real Lean IR); versionB via a stated, unverified modeling assumption.
    gadgets: bool = False
    #: the controlled multiplier x -> c*x mod N. REQUIRED when gadgets=True (phi1/phi5 depend on it).
    c: Optional[int] = None
    #: directive: additionally narrow dyadic phase-product nodes per Thm 5.1 (modulus clamping) inside
    #: the recursive and versionB cores -- see recursive_w_gadgets.py's CLAMPING section, including
    #: the measured null result on the exact stack. Needs gadgets=True. No structural effect on the
    #: onelevel core (its single level's zero-skip already removes exactly the gates clamping would).
    clamp: bool = False
    #: BOUNDED-PRECISION VARIANT (2026-09-10, requested as an explicit model switch): when set, every
    #: phase-product turn (phi1/2, phi2/2, phi5/2) is replaced by its nearest b-bit dyadic
    #: round(turn * 2^b) / 2^b before the recursion is built. This is NOT the Lean modmul: the exact
    #: rational constants (c-1)/N, N/2^(ww+wc), k5/N become b-bit approximations and the circuit's
    #: unitary changes by at most `phase_error_bound()` turns per product. It exists so the clamp
    #: theorem has something to act on and the trade-off (T saved vs phase error) can be measured;
    #: `phase_bits_sound()` is the b at which the induced error drops below the step precision eta.
    phase_bits: Optional[int] = None
    #: versionB stop-rule price (see modmul.py / ToomRecursivePhaseProduct.tsr).
    stop_tsr: float = 11.0

    def __attrs_post_init__(self):
        if self.phase_bits is not None and (not self.gadgets or self.phase_bits < 1):
            raise ValueError("phase_bits needs gadgets=True and a positive bit count")
        if self.clamp and not self.gadgets:
            raise ValueError("clamp=True needs gadgets=True (clamping takes the resolution of the same "
                             "concrete phi1/phi2/phi5 angles that pruning uses)")
        if self.bundle_doc is not None and self.bundle_doc.get("k") not in (None, self.k):
            raise ValueError(f"LeanCtrlModMulWithGadgets k={self.k} != bundle k={self.bundle_doc.get('k')}")
        if self.core not in ("onelevel", "recursive", "versionB"):
            raise ValueError(f"LeanCtrlModMulWithGadgets core={self.core!r} unknown")
        if self.qft not in ("lean", "reference", "reference-aqft"):
            raise ValueError(f"LeanCtrlModMulWithGadgets qft={self.qft!r} unknown")
        if self.gadgets:
            if self.c is None:
                raise ValueError("gadgets=True needs a concrete `c` (the controlled multiplier) -- "
                                 "phi1/phi5 are exact functions of c, not a free parameter (see the "
                                 "module docstring's phi1/phi2/phi5 formulas)")
            if not self.N:
                raise ValueError("gadgets=True needs the REAL modulus `N` (got N=0, the generic-"
                                 "pricing placeholder) -- phi1/phi2/phi5 all depend on N")

    @property
    def w_work(self) -> int:
        return self.n + extra_bits(self.eta)

    @property
    def w_carry(self) -> int:
        return self.n + 1

    @property
    def qubit_footprint(self) -> int:
        """Identical shape to LeanCtrlModMul.qubit_footprint -- gadgets prune GATES (trivial CZPow
        leaves), never registers, so the width formula is unchanged by design."""
        pp = self._pp_generic(self.w_work, self.w_carry)
        if self.core == "recursive":
            rx, rz = pp.reserve_qubits()
            extra = rx + rz
        elif self.core == "versionB":
            extra = pp.qubit_footprint - 2 * pp.w - 1
        else:
            extra = 2 * self.k * int(pp.ext)
        return 1 + self.n + 1 + self.w_work + 1 + extra + 1

    @property
    def signature(self) -> Signature:
        return Signature([Register("ctrl", QBit()), Register("data", QUInt(self.n)),
                          Register("carry", QBit()), Register("work", QUInt(self.w_work)),
                          Register("flag", QBit())])

    def _pp_generic(self, a: int, b: int) -> Bloq:
        """The un-pruned phase product at shape (a, b) -- used by `qubit_footprint` (register width
        never depends on gadgets, so this is called UNCONDITIONALLY, regardless of `self.gadgets`) and
        by `build_call_graph`'s `gadgets=False` path. Never itself gated on `self.gadgets`: both
        "recursive" and "versionB" are fully closed (see `_pp_at`/`_pp_at_versionb`), so nothing here
        needs to refuse anymore."""
        if self.core == "recursive":
            from .recursive import LeanAsymRecursivePhaseProduct
            return LeanAsymRecursivePhaseProduct.from_bundle(self.bundle_doc, a, b, self.policy)
        size = max(a, b) + 1
        if self.core == "versionB":
            from ..recursion_versions import ToomRecursivePhaseProduct
            assert self.rules is not None, "core='versionB' needs a RuleSet"
            return ToomRecursivePhaseProduct.from_rules(self.rules, size, self.k, "B", "kmy", tsr=self.stop_tsr)
        return LeanPhaseProduct.from_bundle(self.bundle_doc, size, self.policy)

    def _pp_at_versionb(self, a: int, b: int, phi: Fraction) -> Bloq:
        """core="versionB" gadget-pruned phase product. Uses `size = max(a, b) + 1`, the SAME
        symmetric-only convention `_pp_generic`'s (unpruned) versionB branch already uses --
        `ToomRecursivePhaseProduct`'s signature is genuinely `(w, w)`-only (unlike
        `LeanAsymRecursivePhaseProduct`, which is asymmetric-native), so this is not a new
        approximation, just the existing one carried through unchanged."""
        from ..recursion_versions_w_gadgets import ToomRecursivePhaseProductWithGadgets
        assert self.rules is not None, "core='versionB' needs a RuleSet"
        size = max(a, b) + 1
        return ToomRecursivePhaseProductWithGadgets.from_rules(self.rules, size, self.k, "B", "kmy",
                                                               gadgets=True, phi=phi, clamp=self.clamp,
                                                               tsr=self.stop_tsr)

    def _pp_at(self, a: int, b: int, phi: Fraction) -> Bloq:
        """The prune_trivial-pruned phase product at shape (a, b), REAL angle `phi`.
        core="onelevel": LeanPhaseProduct's exact leaf_policy="reduced" path.
        core="recursive": LeanAsymRecursivePhaseProductWithGadgets -- carries phi through the SAME
          width recursion LeanAsymRecursivePhaseProduct uses (verified against real Lean IR at the
          bit level, see recursive_w_gadgets.py's module docstring and
          tests/test_recursive_w_gadgets.py). This is the Lean-backed headline core.
        core="versionB": routed separately -- see `_pp_at_versionb` (a different recursion rule, a
          different `RuleSet`-based bloq class; not reachable from this method)."""
        size = max(a, b) + 1
        if self.core == "recursive":
            from .recursive_w_gadgets import LeanAsymRecursivePhaseProductWithGadgets
            return LeanAsymRecursivePhaseProductWithGadgets.from_bundle(
                self.bundle_doc, a, b, self.policy, gadgets=True, phi=phi, clamp=self.clamp)
        return LeanPhaseProduct.from_bundle(self.bundle_doc, size, self.policy, phi=phi,
                                            leaf_policy="reduced")

    def _qft(self, w: int) -> Bloq:
        if self.qft == "reference":
            return ReferenceQFT(w)
        if self.qft == "reference-aqft":
            k_max = max(1, math.ceil(math.log2(float(w) / float(self.eta))))
            return ReferenceQFT(w, k_max=k_max)
        return LeanQFT.from_bundle(self.bundle_doc, w, self.policy, core=self.core, depth=self.depth,
                                   rules=self.rules, stop_tsr=self.stop_tsr)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        n, ww, wc = self.n, self.w_work, self.w_carry
        counts: Dict[Bloq, SymbolicInt] = {}

        def add(b: Bloq, c: SymbolicInt) -> None:
            counts[b] = counts.get(b, 0) + c

        def emit_hcppiqft(cpp: Bloq) -> None:
            add(Hadamard(), ww)
            leaves = cpp.total_czpow()
            add(cpp, 1)
            add(And(), leaves)
            add(And().adjoint(), leaves)
            add(self._qft(ww), 1)

        if self.gadgets and self.core in ("onelevel", "recursive", "versionB"):
            # U1 (data x work, angle phi1) and U5 (dataCarry x work, angle phi5) are NOT the same
            # phase product once concrete -- LeanCtrlModMul's `for a in (n, wc): self._pp(a, ww)` loop
            # is only valid symbolically (both iterations reuse one generic shape); here they take
            # genuinely different phi (see module docstring), so they are two separate calls.
            at = self._pp_at_versionb if self.core == "versionB" else self._pp_at
            sites = {name: used for name, _, _, _, used in self.phase_sites()}
            emit_hcppiqft(at(n, ww, sites['U1']))
            emit_hcppiqft(at(wc, ww, sites['U5']))
            add(self._qft(wc), 2)
            add(at(ww, wc, sites['U2']), 1)
        else:
            for a in (n, wc):
                emit_hcppiqft(self._pp_generic(a, ww))
            add(self._qft(wc), 2)
            add(self._pp_generic(ww, wc), 1)
        add(GreaterThanConstant(wc, max(self.N, 1)), 1)
        add(AddK(QUInt(wc), k=-max(self.N, 1)).controlled(), 1)
        add(LessThanConstant(wc + ww, max(self.N, 1)), 1)
        return counts

    # -- bounded-precision phases --------------------------------------------------------------
    @staticmethod
    def bounded_phi(phi: Fraction, b: int) -> Fraction:
        """phi (units of pi) with its turn phi/2 rounded to the nearest b-bit dyadic."""
        turn = Fraction(phi) / 2
        return 2 * Fraction(round(turn * (1 << b)), 1 << b)

    def phase_sites(self):
        """[(name, wx, wz, exact_phi, used_phi)] for U1, U2, U5 -- `used_phi` is `exact_phi` unless
        `phase_bits` is set."""
        n, ww, wc = self.n, self.w_work, self.w_carry
        exact = [('U1', n, ww, phi1(self.c, self.N)), ('U2', ww, wc, phi2(self.N, ww, wc)), ('U5', wc, ww, phi5(self.c, self.N))]
        return [(name, wx, wz, phi, phi if self.phase_bits is None else self.bounded_phi(phi, self.phase_bits))
                for name, wx, wz, phi in exact]

    def phase_error_bound(self) -> Fraction:
        """Worst-case phase error, in turns, of ONE bounded-precision phase product versus the exact
        one: max over sites of |turn_used - turn_exact| * 2^wx * 2^wz (unsigned operand bound). Zero when
        phases are exact."""
        return max((abs(Fraction(used) / 2 - Fraction(phi) / 2) * (1 << wx) * (1 << wz)
                    for _, wx, wz, phi, used in self.phase_sites()), default=Fraction(0))

    def phase_bits_sound(self) -> int:
        """Smallest b for which the rounding error 2^-(b+1) * 2^(wx+wz) is below eta at every site --
        i.e. b >= wx + wz - 1 + log2(1/eta). Above this b the variant reproduces the exact modmul to
        within the step precision; below it, it is a different circuit."""
        need = math.ceil(math.log2(1.0 / self.eta))
        return max(wx + wz - 1 + need for _, wx, wz, _, _ in self.phase_sites())

    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], n: int, eta: float = 1e-3,
                    N: int = 0, policy: str = "tight", qft: str = "lean", core: str = "onelevel",
                    depth: str = "lean", rules: Optional[Any] = None, gadgets: bool = False,
                    c: Optional[int] = None, clamp: bool = False,
                    phase_bits: Optional[int] = None, stop_tsr: float = 11.0) -> "LeanCtrlModMulWithGadgets":
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        return cls(k=doc["k"], n=n, eta=eta, N=N, policy=policy, qft=qft, core=core, depth=depth,
                  bundle_doc=doc, rules=rules, gadgets=gadgets, c=c, clamp=clamp, phase_bits=phase_bits,
                  stop_tsr=stop_tsr)

    def __str__(self) -> str:
        g = f", gadgets(c={self.c}{', clamp' if self.clamp else ''}{f', phase_bits={self.phase_bits}' if self.phase_bits else ''})" if self.gadgets else ""
        return f"LeanCtrlModMulWithGadgets(k={self.k}, n={self.n}, eta={self.eta}{g})"
