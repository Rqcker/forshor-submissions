"""`LeanCtrlModMul(k, n, eta)` — the E5 template `CmodMulInPlaceCore` as a Qualtran bloq.

Registers: ctrl (1), data (n), work (n + extra(η)), flag (1); dataCarry = data + 1 carry bit.
    U1: H⊗work ; C-PhaseProd(ctrl; data, work) ; IQFT(work)
    U2: QFT(dataCarry) ; PhaseProd(work, dataCarry) ; IQFT(dataCarry)
    U3: CMP_GE_CONST(N; dataCarry → flag) ; CSUB_CONST(N; flag ⊳ dataCarry)
    U4: CMP_LT_NW(N; dataCarry, work → flag)
    U5: adjoint(H⊗work ; C-PhaseProd(ctrl; dataCarry, work) ; IQFT(work))
Realisation choices (stated, not Lean): controlled phase-product leaves as And + CZPow + And†
(the legacy `_add_three_qubit_phase` pattern); comparators as `LessThanConstant` /
`GreaterThanConstant`; CSUB_CONST as controlled `AddK(-N)`; QFTs as `LeanQFT` (E4) — or
`ReferenceQFT` with ``qft="reference"``. extra(η) = ⌈2·log2(2 + 1/(2η))⌉ (Algorithm1Precision).

Proof status carried on the bloq: the Lean theorem is an APPROXIMATION bound
(`modMul_approx_valid_dist_uniform`); see mirrors/modmul_plan.py.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Union

import attrs
from qualtran import Bloq, QBit, QUInt, Register, Signature
from qualtran.bloqs.arithmetic import AddK, GreaterThanConstant, LessThanConstant
from qualtran.bloqs.basic_gates import Hadamard
from qualtran.bloqs.mcmt import And
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator
from qualtran.symbolics import SymbolicInt

from ..emit import Bundle
from .phase_product import LeanPhaseProduct
from .qft import LeanQFT, ReferenceQFT


def extra_bits(eta: float) -> int:
    """`Shor.algorithm1ExtraBits eta` = ⌈2·log₂(2 + 1/(2η))⌉."""
    return math.ceil(2 * math.log2(2 + 1 / (2 * eta)))


@attrs.frozen
class LeanCtrlModMul(Bloq):
    k: int
    n: int
    eta: float = 1e-3
    N: int = 0                     # modulus (only its bit-pattern matters for counts; 0 → generic)
    policy: str = "tight"
    qft: str = "lean"              # "lean" (E4 LeanQFT) | "reference" (exact Draper ladder)
                                   # | "reference-aqft" (Coppersmith-banded ladder, matched to η) — see _qft
    core: str = "onelevel"         # "onelevel" | "recursive" (Lean arbitrary-depth plan when policy='sym'; tight = hypothetical)
                                   # | "versionB" (ToomRecursivePhaseProduct rule R2 for every phase product AND every QFT twiddle)
    depth: str = "lean"            # recursive core: "lean" (full plan) | "optimal" (cost cutoff, hypothetical)
    stop_tsr: float = 11.0         # versionB only: the rotation price the cost-optimal stop rule evaluates at
                                   # (constants.TS_PER_ROTATION); other cores' trees do not depend on it
    bundle_doc: dict = attrs.field(default=None, eq=False, repr=False)
    rules: Optional[Any] = attrs.field(default=None, eq=False, repr=False)   # recursion_versions.RuleSet for core="versionB"
    #: Optional width-indexed policy (`forshor_qualtran.policy.PhasePolicy`, core="recursive" only): every
    #: phase product (U1/U5† controlled, U2) and every QFT twiddle becomes a `PolicyPhaseProduct` — k
    #: chosen per recursion level by width. None = unchanged. See `forshor_qualtran.policy_shor`.
    phase_policy: Optional[Any] = attrs.field(default=None, repr=False)
    pp_convention: Optional[Any] = attrs.field(default=None, repr=False)
    #: core="versionB" only: the `ToomRecursivePhaseProduct` version every phase product and QFT twiddle
    #: is built at. "B" (default) = Toom-k_top at every level, unchanged; "C" = the cheapest loaded arity
    #: at every width under the same rule R2 and stop price -- the optimal width-indexed policy of this model.
    pp_version: str = attrs.field(default="B", repr=False)

    def __attrs_post_init__(self):
        if self.pp_version not in ("B", "C") or (self.pp_version != "B" and self.core != "versionB"):
            raise ValueError(f"pp_version={self.pp_version!r} needs core='versionB' and is 'B' or 'C'")
        # SINGLE-k: the KMY modmul is priced at ONE arity — every internal phase product AND every QFT
        # twiddle (through _pp / _qft) is that same k (Phases A/B). Guard k↔bundle consistency.
        if self.bundle_doc is not None and self.bundle_doc.get("k") not in (None, self.k):
            raise ValueError(f"LeanCtrlModMul k={self.k} != bundle k={self.bundle_doc.get('k')}: single-k violated")
        if self.core not in ("onelevel", "recursive", "versionB"):
            raise ValueError(f"LeanCtrlModMul core={self.core!r} unknown")
        if self.qft not in ("lean", "reference", "reference-aqft"):
            raise ValueError(f"LeanCtrlModMul qft={self.qft!r} unknown (lean | reference | reference-aqft)")
        if self.phase_policy is not None and self.core != "recursive":
            raise ValueError("LeanCtrlModMul phase_policy needs core='recursive' (the Lean-plan phase products it replaces)")

    @property
    def w_work(self) -> int:
        return self.n + extra_bits(self.eta)

    @property
    def w_carry(self) -> int:
        return self.n + 1

    @property
    def qubit_footprint(self) -> int:
        """ctrl + data + carry + work + flag + the widest phase product's workspace (one-level: limb extensions 2kβ;
        recursive: the Lean reserveNeed) + 1 And ancilla. Analytic (QubitCount decomposes the recursion; slow)."""
        pp = self._pp(self.w_work, self.w_carry)
        if self.phase_policy is not None:
            from ..policy_shor import pp_extra_qubits
            extra = pp_extra_qubits(pp, self.w_work, self.w_carry, self.pp_convention)
        elif self.core == "recursive":
            # `reserve_qubits()` (rx, rz) directly, rather than back-deriving from `qubit_footprint - 2*w`
            # -- the asymmetric `LeanAsymRecursivePhaseProduct`'s footprint is `wx + wz + rx + rz + 1`, not
            # `2*max(wx,wz) + rx + rz + 1`, so that subtraction would silently pick up a `|wx-wz|` term.
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

    def _pp(self, a: int, b: int) -> Bloq:
        # `core="recursive"` is the REAL Lean-tied object (leg C1) -- cmodmul's internal phase products
        # are genuinely asymmetric (a narrow control/carry register against the wide work register), and
        # ForShor's own recursion decision (`nextSignedWidth`) depends on BOTH widths (REV 1 FIX
        # 2026-09-03: modeling every internal phase product as square at `max(a,b)` overcounted the
        # And-count ~16x -- see `LeanAsymRecursivePhaseProduct`'s docstring). `versionB`/`onelevel` are
        # deliberately DIFFERENT, hypothetical comparison rules (not tied to Lean's real behavior), so
        # they keep the `size = max(a, b) + 1` square convention unchanged.
        if self.phase_policy is not None:
            from ..policy_shor import policy_pp
            return policy_pp(self.phase_policy, a, b, self.pp_convention)
        if self.core == "recursive":
            from .recursive import LeanAsymRecursivePhaseProduct
            return LeanAsymRecursivePhaseProduct.from_bundle(self.bundle_doc, a, b, self.policy)
        size = max(a, b) + 1
        if self.core == "versionB":
            from ..recursion_versions import ToomRecursivePhaseProduct
            assert self.rules is not None, "core='versionB' needs a RuleSet"
            return ToomRecursivePhaseProduct.from_rules(self.rules, size, self.k, self.pp_version, "kmy",
                                                        tsr=self.stop_tsr)
        return LeanPhaseProduct.from_bundle(self.bundle_doc, size, self.policy)

    def _qft(self, w: int) -> Bloq:
        # QFT-realization SUBSTITUTION POINT (parallel to LeanModExp.modmul's method seam):
        #   "lean"           → E4 lowerQFT recursive Toom-Cook twiddles (Lean-backed, single-k)
        #   "reference"      → exact textbook Draper H+CP ladder (ReferenceQFT; NOT Lean, by design)
        #   "reference-aqft" → Coppersmith-banded ladder at k_max = ⌈log₂(w/η)⌉, matched to the SAME
        #                      per-step precision η the KMY widths use (mirror of precision_policy.aqft_k_max)
        if self.qft == "reference":
            return ReferenceQFT(w)
        if self.qft == "reference-aqft":
            import math
            k_max = max(1, math.ceil(math.log2(float(w) / float(self.eta))))
            return ReferenceQFT(w, k_max=k_max)
        return LeanQFT.from_bundle(self.bundle_doc, w, self.policy, core=self.core, depth=self.depth, rules=self.rules,
                                   stop_tsr=self.stop_tsr, phase_policy=self.phase_policy,
                                   pp_convention=self.pp_convention, pp_version=self.pp_version)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        n, ww, wc = self.n, self.w_work, self.w_carry
        counts: Dict[Bloq, SymbolicInt] = {}

        def add(b: Bloq, c: SymbolicInt) -> None:
            counts[b] = counts.get(b, 0) + c

        # U1 and U5†: H⊗work, controlled phase product (data|carry, work), IQFT(work)
        for a in (n, wc):
            add(Hadamard(), ww)
            if self.phase_policy is not None:
                from ..policy_shor import leaf_cells, legacy_controls, policy_cpp
                if not legacy_controls(self.pp_convention):
                    add(policy_cpp(self.phase_policy, a, ww, self.pp_convention), 1)   # leaves controlled
                    add(self._qft(ww), 1)
                    continue
                cpp = self._pp(a, ww)
                leaves = leaf_cells(cpp)            # LEGACY: the single-k pipeline's realisation, below
            else:
                cpp = self._pp(a, ww)
                leaves = cpp.total_czpow()
            add(cpp, 1)                             # the phase product (its CZPow leaves + adders)
            add(And(), leaves)                      # control: x&z → ancilla, CZPow(ctrl, ancilla), uncompute
            add(And().adjoint(), leaves)
            add(self._qft(ww), 1)                   # IQFT(work) (adjoint has the same count)
        # U2: QFT(carry) ; PhaseProd(work, carry) ; IQFT(carry)
        add(self._qft(wc), 2)
        add(self._pp(ww, wc), 1)
        # U3: CMP_GE_CONST, CSUB_CONST on dataCarry
        add(GreaterThanConstant(wc, max(self.N, 1)), 1)
        add(AddK(QUInt(wc), k=-max(self.N, 1)).controlled(), 1)
        # U4: CMP_LT_NW on (dataCarry, work)
        add(LessThanConstant(wc + ww, max(self.N, 1)), 1)
        return counts

    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], n: int, eta: float = 1e-3,
                    N: int = 0, policy: str = "tight", qft: str = "lean", core: str = "onelevel",
                    depth: str = "lean", rules: Optional[Any] = None, stop_tsr: float = 11.0,
                    phase_policy: Optional[Any] = None, pp_convention: Optional[Any] = None,
                    pp_version: str = "B") -> "LeanCtrlModMul":
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        return cls(k=doc["k"], n=n, eta=eta, N=N, policy=policy, qft=qft, core=core, depth=depth, stop_tsr=stop_tsr,
                   bundle_doc=doc, rules=rules, phase_policy=phase_policy, pp_convention=pp_convention,
                   pp_version=pp_version)

    def __str__(self) -> str:
        return f"LeanCtrlModMul(k={self.k}, n={self.n}, eta={self.eta})"


# ------------------------------------------------------------------ Phase C: IR-lowered counterpart (C1 = cost-structure V)
def concrete_ir_bloq(bundle: Union[Bundle, Dict[str, Any]], n: int, c: int, N: int, eta: float = 1e-3,
                     refresh: bool = False):
    """The Lean-lowered CmodMulInPlaceCore at concrete (n, c, N, η) — the object the priced KMY modmul is
    tied to for the cost-structure differential (C1). Returns (bloq, ir_document).

    `emit.lower_cmodmul` (E5 `forshor_emit cmodmul`) prints the U1..U5 gate list as a shor.lowgate.ir/v1
    document (phase products via the recursive single-k plan, QFTs via the E4 rule, comparators as
    concrete arithmetic). REAL-LEAN only — raises EmitError without the binary (no mirror). Same legacy
    backend as every other primitive parses it."""
    from .. import paths
    from ..emit import lower_cmodmul
    doc = bundle.doc if isinstance(bundle, Bundle) else bundle
    k = doc["k"]
    ir_doc = lower_cmodmul(k, int(n), int(c), int(N), float(eta), refresh=refresh)
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend  # legacy py/
    ir = lowgate_ir.parse_ir(ir_doc)
    return qualtran_backend.build_qualtran_bloq(ir), ir_doc


def modmul_action_status(ir_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Action leg for the modmul. UNLIKE the phase product and QFT, the whole CmodMulInPlaceCore is NOT a
    signed phase product — it is the KMY Alg-1 *approximate* modular multiply, and ForShor proves only an
    APPROXIMATION bound (`modMul_approx_valid_dist_uniform`, ‖approx−ideal‖ ≤ √(2Kη)) relative to the
    UNINSTANTIATED class `IdealCtrlModMulExactSemantics`. So an exact whole-circuit action check is out of
    reach here (that is C2 — instantiating the semantics class, a genuine ForShor proof effort).

    What IS inherited: each internal (C)PhaseProduct is a signed phase product lowered by the E7 path and
    is action-verified by Phase B; each QFT by Phase A. This returns a SKIP with that provenance so the C1
    verifier records the boundary honestly rather than overclaiming a whole-circuit action proof."""
    return {"verdict": "SKIP",
            "reason": "C2 (approximate whole-circuit action) needs IdealCtrlModMulExactSemantics instantiated; "
                      "internal phase-products/QFTs action-verified via Phases B/A",
            "bound": "modMul_approx_valid_dist_uniform: ||approx-ideal|| <= sqrt(2 K eta), K not exhibited"}
