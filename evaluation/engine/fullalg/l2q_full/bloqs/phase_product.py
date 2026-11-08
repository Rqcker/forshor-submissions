"""`LeanPhaseProduct(k, n)` — the one-level Toom-Cook phase product as a symbolic-n Qualtran bloq.

What it models (exactly the naive lowering ForShor emits, `aLowerGateNaive`):
  * operands x, z of n bits, split into k limbs of m = ⌈n/k⌉ bits, each limb extended to the
    working width W(n, k) = m + β_policy(k)  (E3 affine tail);
  * the schedule's A_k adder-class ops applied on BOTH operand sides (Toom evaluation of x and
    of z): 2·A_k ripples — addScaled → `Add(QInt(W))` (+ 2·`Negate` when negSrc), negate →
    `Negate(QInt(W))`; shifts free (rewiring);
  * q = 2k-1 phase-product leaves, each a W×W grid of `CZPowGate` with exponent
    c_l(2^m)·φ·2^{a+b} (E2 polynomial) — symbolic exponents in the call graph.

Footprint: 2·k·W = 2n + 2k(W - m) = 2n + 2kβ qubits (the k limbs of both operands, extended),
NOT 2W (the legacy `LeanPhaseProduct` modelled a single leaf pair; audit F1/F3).

`n` symbolic → `get_cost_value(bloq, QECGatesCost())` is a closed form in n; `n` concrete →
the same call graph evaluates to integers, and `concrete_ir_bloq(n)` builds the legacy
IR-lowered bloq (schedule_expand → qualtran_backend) whose counts the differential test
compares against — the priced object and the verified object are tied by that test.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, Optional, Union

import attrs
import sympy
from qualtran import Bloq, QAny, QUInt, Register, Signature
from qualtran.bloqs.arithmetic import Add, Negate
from qualtran.bloqs.basic_gates import CZPowGate
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator
from qualtran.symbolics import SymbolicInt

from .. import widths
from ..emit import Bundle
from .. import bundle_compat
from ..mirrors.coeff_poly import poly_eval

# qualtran's QInt is what Add/Negate want for two's-complement limbs
from qualtran import QInt  # noqa: E402


def _is_symbolic(x) -> bool:
    return isinstance(x, sympy.Basic) and bool(x.free_symbols)


def _czpow_fold(exponent: Fraction) -> float:
    """`exp(i*pi*e)` has period 2: reduce into (-1, 1] EXACTLY, then convert to float.

    The same reduction as `py/qualtran_backend.py::_czpow_exponent`, kept here so the priced and
    the IR-lowered paths classify identically. Folding before the float also keeps the
    conversion meaningful -- at 2048 bits these exponents reach 2^6132, where `float(Fraction)`
    overflows outright.
    """
    return float(((exponent + 1) % 2) - 1)


@attrs.frozen
class LeanPhaseProduct(Bloq):
    """One-level Toom-Cook phase product exp(i·φ·x·z) on n-bit operands, arity k."""
    k: int
    n: SymbolicInt
    q: int
    n_add: int
    n_neg: int
    beta: int                    # W = m + beta for the chosen policy (m ≥ from_m)
    n_add_negsrc: int = 0        # addScaled ops with negSrc: realised as Negate·Add·Negate (2 extra Negates)
    policy: str = "tight"
    phi: Optional[Fraction] = None   # units of π; None → generic (symbolic) rotations
    leaf_policy: str = "reduced"     # concrete φ: "reduced" (mod 2, zero-skip: the true gate count)
                                     #             | "ir" (unreduced floats, all q·W² pairs: what the legacy IR emits)
    coeff_polys: Optional[tuple] = attrs.field(default=None, eq=False, repr=False)

    # ---- derived widths ---------------------------------------------------------------
    @property
    def m(self) -> SymbolicInt:
        return widths.m_of_n(self.n, self.k)

    @property
    def W(self) -> SymbolicInt:
        return self.m + self.beta

    @property
    def ext(self) -> SymbolicInt:
        """Extension bits per limb (W - m = beta) — constant in n."""
        return self.beta

    @property
    def qubit_footprint(self) -> SymbolicInt:
        return 2 * self.n + 2 * self.k * self.ext

    # ---- Qualtran interface -------------------------------------------------------------
    @property
    def signature(self) -> Signature:
        regs = [Register("x", QUInt(self.n)), Register("z", QUInt(self.n))]
        if not (_is_symbolic(self.ext) or self.ext == 0):
            regs += [Register("ext_x", QAny(self.k * self.ext)), Register("ext_z", QAny(self.k * self.ext))]
        elif _is_symbolic(self.ext):
            regs += [Register("ext_x", QAny(self.k * self.ext)), Register("ext_z", QAny(self.k * self.ext))]
        return Signature(regs)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        W = self.W
        counts: Dict[Bloq, SymbolicInt] = {}
        if self.phi is None or _is_symbolic(self.n):
            counts[CZPowGate(exponent=ssa.new_symbol("e"))] = self.q * W ** 2
        else:
            # concrete n and φ: enumerate the exact leaf exponents (mod 2) so Qualtran classifies
            # Clifford/identity CZPows exactly as the IR-lowered bloq does
            for expo, mult in self._leaf_float_exponents().items():
                counts[CZPowGate(exponent=expo)] = counts.get(CZPowGate(exponent=expo), 0) + mult
        sides = 2                                  # every schedule op acts on x AND z
        if self.n_add:
            counts[Add(QInt(W))] = sides * self.n_add
        n_negate = sides * (self.n_neg + 2 * self.n_add_negsrc)
        if n_negate:
            counts[Negate(QInt(W))] = n_negate
        return counts

    @property
    def adder_count(self) -> int:
        """Ripples of width W: 2·A_k (both sides) — plus 2 Negates per negSrc addScaled."""
        return 2 * (self.n_add + self.n_neg)

    def _leaf_exponents(self) -> Dict[Fraction, int]:
        """{exponent mod 2: multiplicity} over all q leaves × W×W bit pairs (concrete n, φ),
        with two's-complement SIGNED bit weights (top bit −2^{W−1}) exactly as the IR lowering
        (`signed_bit_weight_lsb`) and zero exponents skipped (the IR emits no gate for them).

        CLOSED FORM (exact, `O(q*W)` not `O(q*W^2)`): every bit weight is `+2**i` except the top
        (sign) bit, which is `-2**(W-1)` -- so `raw = c * wa * wb` depends on `(i, j)` only through
        `i+j` (and which of `i, j`, if either, is the sign bit), exactly the pattern
        `py/qualtran_backend.py::CompactSignedPhaseGrid.build_call_graph` already closed for a
        DIFFERENT reduction (there: skip only a literal-zero exponent; here: full `mod 2` reduction,
        `raw % 2`, since this leaf reports the TRUE gate count including Clifford `CZPow`s an
        integer-turn would trivialize). The four-case partition (bulk sum-of-two-uniforms + two
        sign-bit boundary rows + one corner) is unchanged; only the per-group value (`(c *
        sign*2**s) % 2` instead of a raw product) and the skip condition (`% 2 == 0` instead of
        `== 0`) differ.

        WHY THIS MATTERS: `leaf_policy="reduced"` (this method) is exactly the path
        `gadgets=True` pricing (`engine_export.py::make_qualtran_pricer`, wired 2026-09-09) uses at
        every base-case Toom-Cook recursion leaf, for BOTH `core="onelevel"` and (via
        `LeanAsymRecursivePhaseProductWithGadgets`) `core="recursive"`. Found 2026-09-09 while
        actually running the wide-n-grid `engine_export.json` sweep for the first time: the OLD
        `O(W^2)` loop made `gadgets=True` pricing ~1000x+ slower than `gadgets=False` even at
        n=21-23 (73-99s per call, growing with n) -- intractable across the notebook's intended
        n-grid (up to n=4096). VERIFIED bit-for-bit against the OLD double loop before landing: 302
        trials across W in [1,40], both `leaf_policy` values, fixed + randomized coefficient sets,
        0 mismatches (`tests/test_gadgets_wiring.py` carries the permanent regression)."""
        if self.leaf_policy == "ir":
            return self._leaf_exponents_ir()
        out: Dict[Fraction, int] = {}
        for residues, q in self._leaf_residues():
            for r, mult in residues.items():
                e = Fraction(r, q)
                out[e] = out.get(e, 0) + mult
        return out

    def _leaf_coefficients(self):
        """`(W, [c_l * phi ...])`: the exact angle each of the q leaves carries (units of pi)."""
        assert self.coeff_polys is not None and self.phi is not None and not _is_symbolic(self.n)
        m, W = int(self.m), int(self.W)
        b = Fraction(2) ** m
        return W, [poly_eval([Fraction(x) for x in poly], b) * self.phi for poly in self.coeff_polys]

    def _leaf_residues(self):
        """Per leaf, `({r: multiplicity}, q)` with reduced exponent `r / q`, zeros dropped -- the
        integer-arithmetic core shared with `recursive_w_gadgets._leaf_residues` (2026-09-09; the
        four-case partition above, evaluated on the turn's numerator/denominator instead of one
        `Fraction` multiply-and-mod per group)."""
        from .recursive_w_gadgets import _leaf_residues
        W, cs = self._leaf_coefficients()
        return [_leaf_residues(W, W, c) for c in cs]

    def _leaf_float_exponents(self) -> Dict[float, int]:
        """The `CZPowGate.exponent` floats Qualtran classifies, with multiplicities.

        Every exponent is reduced mod 2 into the centred range (-1, 1] FIRST, exactly as the
        IR-lowering path does (`py/qualtran_backend.py::_czpow_exponent`, which has folded since
        2026-09-14). `CZPowGate(e)` has period 2 in `e`, so this never changes the operator --
        only the cost, and by a lot.

        Without the fold the priced side handed Qualtran raw exponents up to |120| (a leaf angle
        absorbs the limb significance `2^(i+j)` of the operands it multiplies, and nothing
        normalises it). Qualtran prices anything outside `{0, +-1/2, +-1, +-1/4}` as a generic
        rotation, so an exponent of -120 -- which IS the identity -- was counted as a rotation.
        Measured at k=2, m=2: priced 46 rotations against the IR's 3, on identical leaf
        multisets. `leaf_policy="ir"` claimed to reproduce "what the legacy IR emits"; the legacy
        IR folds, so reproducing it means folding too.
        """
        out: Dict[float, int] = {}
        if self.leaf_policy == "ir":
            for e, mult in self._leaf_exponents_ir().items():
                f = _czpow_fold(e)
                out[f] = out.get(f, 0) + mult
            return out
        for residues, q in self._leaf_residues():
            for r, mult in residues.items():
                f = _czpow_fold(Fraction(r, q))
                out[f] = out.get(f, 0) + mult
        return out

    def _leaf_exponents_ir(self) -> Dict[Fraction, int]:
        """`leaf_policy="ir"`: the UNREDUCED raw exponents, zeros included -- what the legacy IR emits."""
        from .recursive_w_gadgets import _leaf_groups
        W, cs = self._leaf_coefficients()
        out: Dict[Fraction, int] = {}
        for c in cs:
            for s, sign, mult in _leaf_groups(W, W):
                e = c * sign * (1 << s)
                out[e] = out.get(e, 0) + mult
        return out

    def total_czpow(self) -> SymbolicInt:
        return self.q * self.W ** 2

    # ---- constructors -----------------------------------------------------------------
    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], n: SymbolicInt,
                    policy: str = "tight", phi: Optional[Fraction] = None,
                    leaf_policy: str = "reduced") -> "LeanPhaseProduct":
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        k = doc["k"]
        rule = widths.width_rule(doc, policy)  # type: ignore[arg-type]
        if not _is_symbolic(n) and int(widths.m_of_n(int(n), k)) < rule.from_m:
            # below the affine tail: use the exact table value via a per-instance beta
            m = int(widths.m_of_n(int(n), k))
            beta = rule.at_m(m) - m
        else:
            beta = rule.beta
        # `bundle_compat.schedule_census`, not the raw census: v2 reports `addScaled` as the
        # TOTAL and marks `negSrc` on each op, so the separate `addScaled_negSrc` count has to
        # be recovered from the ops. Defaulting it to 0 silently dropped every Negate from the
        # priced call graph -- measured at k=2 m=2 as 24 And against the IR's 48, because the
        # backend realises a negSrc add as Negate . Add . Negate.
        cen = bundle_compat.schedule_census(doc)
        polys = bundle_compat.coeff_polys(doc)
        return cls(k=k, n=n, q=cen["phaseProduct"], n_add=cen["addScaled"], n_neg=cen["negate"],
                   beta=beta, n_add_negsrc=cen.get("addScaled_negSrc", 0), policy=policy, phi=phi,
                   leaf_policy=leaf_policy, coeff_polys=polys)

    def __str__(self) -> str:
        return f"LeanPhaseProduct(k={self.k}, n={self.n}, W={self.W}, {self.policy})"


def concrete_ir_bloq(bundle: Union[Bundle, Dict[str, Any]], n: int, phi: Fraction = Fraction(1, 8),
                     policy: str = "tight"):
    """The legacy IR-lowered Qualtran bloq at concrete n (schedule_expand → qualtran_backend),
    the object every verifier simulates. Returns (bloq, ir_document)."""
    from .. import paths
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend, schedule_expand  # legacy py/
    doc = bundle.doc if isinstance(bundle, Bundle) else bundle
    k = doc["k"]
    m = -(-n // k)
    sched = bundle_compat.schedule_view(doc)
    rule = widths.width_rule(doc, policy)  # type: ignore[arg-type]
    W = rule.at_m(m)
    ir_doc = schedule_expand.build_document_from_schedule(sched, m=m, phi=phi,
                                                          width_override=None if policy == "tight" else W)
    ir = lowgate_ir.parse_ir(ir_doc)
    return qualtran_backend.build_qualtran_bloq(ir), ir_doc


def ir_leaf_exponents(ir_doc: Dict[str, Any], reduce: bool = True) -> Dict[Fraction, int]:
    """{exponent mod 2: multiplicity} of every CZPow the legacy IR lowering emits — walked from the
    `Naive_SignedPhaseProd` leaves of the document with the same signed weights and zero-skip."""
    out: Dict[Fraction, int] = {}

    def weights(width: int):
        return [-(1 << (width - 1)) if i == width - 1 else (1 << i) for i in range(width)]

    def walk(node):
        if isinstance(node, dict):
            if node.get("op") in ("Naive_SignedPhaseProd", "NaiveSignedPhaseProd"):
                phi = node["phi"]
                c = Fraction(phi["num"], phi["den"])
                wx, wz = len(node["x"]["active"]), len(node["z"]["active"])
                for wa in weights(wx):
                    for wb in weights(wz):
                        raw = c * wa * wb
                        e = raw % 2 if reduce else raw
                        if reduce and not e:
                            continue
                        out[e] = out.get(e, 0) + 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(ir_doc.get("circuit"))
    return out
