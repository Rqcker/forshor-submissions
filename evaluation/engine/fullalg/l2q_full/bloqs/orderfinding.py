"""`LeanOrderFinding(k, n)` — Shor order-finding as ForShor's `orderFindingApprox` composition.

ForShor DEFINES the order-finding circuit (Implementation/Shor/Defs.lean:402, `orderFindingApprox`) as

    H_reg(x) ;; initY1(y) ;; modExp(a, N, x, y, work, flag) ;; IQFT(x)

— the QPE Hadamard frame on the exponent register x (Clifford), the |1⟩ initialisation of the data
register y (Clifford), the controlled modular exponentiation (Phase D), and the inverse QFT on x (Phase
A, recursive-twiddle, single k). The exponent width is L = regSize x.active = log2(2·N²) ≈ 2n, and the
lowered gate count is `shorOrderFindingGateCount` (O(n^(2+ε))).

`LeanOrderFinding` composes exactly this. Cost = modexp + IQFT (the frame is Clifford → no And/rotation).
No new lowering primitive: Phase E inherits Phase-D cost-structure (leg C1) and the approximate action
(C2). The COMPOSITION is ForShor's, so verification reads L and the sub-circuits from the Lean
descriptor `emit.lower_orderfinding`; the priced object is tied to that, REAL-LEAN only.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Union

import attrs
from qualtran import Bloq, QUInt, Register, Signature
from qualtran.bloqs.basic_gates import Hadamard, XGate
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from ..emit import Bundle
from .modexp import LeanModExp
from .qft import LeanQFT, ReferenceQFT


def canonical_L(N: int) -> int:
    """L = log2(2·N²) — ForShor's exponent-register width (ShorGateCountBound `_hxWidth`)."""
    return max(1, (2 * N * N).bit_length() - 1)


@attrs.frozen
class LeanOrderFinding(Bloq):
    k: int
    n: int
    a: int = 2
    delta: float = 1e-2
    L: Optional[int] = None               # exponent-register width; default 2n (canonical log2(2N²) ≈ 2n)
    N: int = 0
    policy: str = "sym"
    core: str = "recursive"
    depth: str = "lean"
    qft: str = "lean"                      # modmul-INTERNAL QFT realization (threaded to modexp→modmul)
    iqft: Optional[str] = None             # QPE exponent-register inverse-QFT realization; None → follow `qft`.
                                           # Independent of `qft` (the two QFT sites are decoupled).
    method: str = "kmy"                    # modular-multiply subroutine: "kmy" | "beauregard" (threaded to modexp)
    adder: str = "fourier"                 # Beauregard-only sub-block adder family: "fourier" | "ripple" (threaded)
    gadgets: bool = False                  # prune_trivial (Thm 3.1) on the KMY modmul's internal phase
                                           # products, at representative multiplier c = a mod N (see
                                           # LeanModExp's own docstring GADGETS section for the honesty
                                           # caveat -- this is NOT a per-step-exact real-schedule price).
    phase_bits: Optional[int] = None       # bounded-precision modmul variant (see modmul_w_gadgets.py); threaded
    stop_tsr: float = 11.0                 # versionB stop-rule price; threaded to modexp and the readout QFT
    schedule: str = "representative"       # "representative" (tbits copies of step 0) | "real" (the true
                                           # a^(2^e) mod N schedule, gadgets only); threaded to modexp
    clamp: bool = False                    # modulus clamping (Thm 5.1) on top of gadgets=True; threaded to
                                           # modexp -> modmul_w_gadgets. See recursive_w_gadgets.py (CLAMPING).
    bundle_doc: dict = attrs.field(default=None, eq=False, repr=False)
    rules: Optional[Any] = attrs.field(default=None, eq=False, repr=False)
    #: Optional width-indexed phase policy, threaded to every phase product below (see
    #: `forshor_qualtran.policy_shor`). None = unchanged.
    phase_policy: Optional[Any] = attrs.field(default=None, repr=False)
    pp_convention: Optional[Any] = attrs.field(default=None, repr=False)
    #: core="versionB" only: the `ToomRecursivePhaseProduct` version every phase product and QFT twiddle
    #: is built at. "B" (default) = Toom-k_top at every level, unchanged; "C" = the cheapest loaded arity
    #: at every width under the same rule R2 and stop price -- the optimal width-indexed policy of this model.
    pp_version: str = attrs.field(default="B", repr=False)

    def __attrs_post_init__(self):
        if self.pp_version not in ("B", "C") or (self.pp_version != "B" and self.core != "versionB"):
            raise ValueError(f"pp_version={self.pp_version!r} needs core='versionB' and is 'B' or 'C'")
        if self.bundle_doc is not None and self.bundle_doc.get("k") not in (None, self.k):
            raise ValueError(f"LeanOrderFinding k={self.k} != bundle k={self.bundle_doc.get('k')}: single-k violated")
        if self.core not in ("onelevel", "recursive", "versionB"):
            raise ValueError(f"LeanOrderFinding core={self.core!r} unknown")
        if self.qft not in ("lean", "reference", "reference-aqft"):
            raise ValueError(f"LeanOrderFinding qft={self.qft!r} unknown (lean | reference | reference-aqft)")
        if self.iqft is not None and self.iqft not in ("lean", "reference", "reference-aqft"):
            raise ValueError(f"LeanOrderFinding iqft={self.iqft!r} unknown (lean | reference | reference-aqft | None)")
        if self.adder not in ("fourier", "ripple"):
            raise ValueError(f"LeanOrderFinding adder={self.adder!r} unknown (fourier | ripple)")

    @property
    def exp_width(self) -> int:
        """L — the exponent register width (= tbits of the mod-exp). Default 2n."""
        return int(self.L) if self.L is not None else 2 * self.n

    def modexp(self) -> LeanModExp:
        return LeanModExp.from_bundle(self.bundle_doc, self.n, delta=self.delta, tbits=self.exp_width, N=self.N,
                                      policy=self.policy, core=self.core, depth=self.depth, qft=self.qft,
                                      method=self.method, adder=self.adder, a=self.a, gadgets=self.gadgets,
                                      clamp=self.clamp, phase_bits=self.phase_bits, stop_tsr=self.stop_tsr,
                                      schedule=self.schedule, rules=self.rules,
                                      phase_policy=self.phase_policy, pp_convention=self.pp_convention,
                                      pp_version=self.pp_version)

    def iqft_bloq(self):
        # inverse QFT on the exponent register; adjoint has the same And/rotation count as the QFT.
        # QPE-IQFT realization seam — its OWN `iqft` kwarg, independent of the modmul-internal `qft`
        # (the two QFT sites are decoupled). `iqft=None` follows `qft` for backward compatibility.
        #
        # NAMED `iqft_bloq`, NOT `iqft`: `iqft` is also an `@attrs.frozen` FIELD on this class (the
        # kwarg above). A method sharing a field's name is silently shadowed by attrs' generated slot
        # descriptor for the field — `self.iqft` always resolves to the FIELD (a str/None), never this
        # function, so a same-named method could never actually be called (`self.iqft()` would raise
        # `TypeError: '<field value>' object is not callable` for EVERY instance). Found while wiring
        # gadgets through 2026-09-09: this bug pre-dates that work and is unrelated to it — apparently
        # never triggered because `make_qualtran_pricer` (`engine_export.py`, the only caller that reaches
        # `build_call_graph` on a real `LeanOrderFinding`) is Mac-side-only and had not yet been run
        # end-to-end in this sandbox. Fixed by renaming the method; the field `iqft` is untouched (still
        # the public kwarg name everywhere else).
        #   "lean"           → E4 lowerQFT (Lean-backed)
        #   "reference"      → exact Draper ladder (textbook, NOT Lean)
        #   "reference-aqft" → Coppersmith band at k_max = ⌈log₂(L/η)⌉, η = δ/n² (matched accuracy)
        mode = self.iqft if self.iqft is not None else self.qft
        if mode == "reference":
            return ReferenceQFT(self.exp_width)
        if mode == "reference-aqft":
            import math
            eta_step = float(self.delta) / (float(self.n) ** 2)          # shorEta(δ, n)
            k_max = max(1, math.ceil(math.log2(float(self.exp_width) / eta_step)))
            return ReferenceQFT(self.exp_width, k_max=k_max)
        return LeanQFT.from_bundle(self.bundle_doc, self.exp_width, self.policy, core=self.core, depth=self.depth, rules=self.rules, stop_tsr=self.stop_tsr,
                                   phase_policy=self.phase_policy, pp_convention=self.pp_convention,
                                   pp_version=self.pp_version)

    @property
    def signature(self) -> Signature:
        return Signature([Register("exponent", QUInt(self.exp_width)), Register("data", QUInt(self.n))])

    @property
    def qubit_footprint(self) -> int:
        # exponent register (L) + the mod-exp working registers (the IQFT acts on the exponent register in place)
        return self.exp_width + self.modexp().qubit_footprint

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        # H_reg(x): L Hadamards (Clifford) ; initY1: one X (Clifford) ; modExp ; IQFT(x)
        return {Hadamard(): self.exp_width, XGate(): 1, self.modexp(): 1, self.iqft_bloq(): 1}

    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], n: int, a: int = 2, delta: float = 1e-2,
                    L: Optional[int] = None, N: int = 0, policy: str = "sym", core: str = "recursive",
                    depth: str = "lean", qft: str = "lean", iqft: Optional[str] = None, method: str = "kmy",
                    adder: str = "fourier", gadgets: bool = False,
                    rules: Optional[Any] = None, clamp: bool = False,
                    phase_bits: Optional[int] = None, stop_tsr: float = 11.0,
                    schedule: str = "representative", phase_policy: Optional[Any] = None,
                    pp_convention: Optional[Any] = None, pp_version: str = "B") -> "LeanOrderFinding":
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        return cls(k=doc["k"], n=n, a=a, delta=delta, L=L, N=N, policy=policy, core=core, depth=depth,
                   qft=qft, iqft=iqft, method=method, adder=adder, gadgets=gadgets, clamp=clamp,
                   phase_bits=phase_bits, stop_tsr=stop_tsr, schedule=schedule, bundle_doc=doc, rules=rules,
                   phase_policy=phase_policy, pp_convention=pp_convention, pp_version=pp_version)

    def __str__(self) -> str:
        return f"LeanOrderFinding(k={self.k}, n={self.n}, L={self.exp_width})"


def concrete_ir_bloq(bundle: Union[Bundle, Dict[str, Any]], n: int, a: int, N: int, L: int,
                     delta: float = 1e-2, refresh: bool = False):
    """The Lean order-finding descriptor at concrete (n, a, N, L, δ), and the Qualtran bloq it composes.

    Reads `emit.lower_orderfinding` (REAL-LEAN only; EmitError without the binary). The descriptor carries
    ForShor's exponent width L, the embedded Phase-D modexp descriptor, and the embedded Phase-A IQFT
    lowgate IR — all Lean objects. The returned bloq is H^{⊗L} + X + (composed modexp) + (IQFT bloq).
    Returns (bloq, descriptor)."""
    from .. import paths
    from ..emit import lower_orderfinding

    doc = bundle.doc if isinstance(bundle, Bundle) else bundle
    k = doc["k"]
    desc = lower_orderfinding(k, int(n), int(a), int(N), int(L), float(delta), refresh=refresh)
    lean_L = int(desc["orderfinding"]["exponent_width_L"])
    # the embedded modexp: reuse Phase-D's composer (it reads the nested modexp descriptor's tbits + step)
    modexp_desc = desc["orderfinding"]["modexp"]
    iqft_ir = desc["orderfinding"]["iqft"]
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend  # legacy py/
    # modexp bloq from the nested descriptor (tbits × embedded step template)
    lean_tbits = int(modexp_desc["modexp"]["tbits"])
    step_bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(modexp_desc["modexp"]["step_template"]))
    iqft_bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(iqft_ir))

    @attrs.frozen
    class _ComposedOrderFinding(Bloq):
        L: int
        tbits: int
        step: Any = attrs.field(eq=False)
        iqft: Any = attrs.field(eq=False)

        @property
        def signature(self) -> Signature:
            return Signature([Register("of", QUInt(1))])

        def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
            return {Hadamard(): self.L, XGate(): 1, self.step: self.tbits, self.iqft: 1}

    return _ComposedOrderFinding(L=lean_L, tbits=lean_tbits, step=step_bloq, iqft=iqft_bloq), desc
