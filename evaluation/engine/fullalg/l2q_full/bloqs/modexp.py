"""`LeanModExp(k, n)` — modular exponentiation as the tbits-fold composition of the Phase-C core.

ForShor DEFINES modular exponentiation (Implementation/ModularExponentiation/Defs.lean:314,
`modExpIdealSteps`) as a recursion over the exponent register: for each control qubit at position e,
apply one controlled modular multiply by ``a^(2^e) mod N`` to the data register, then recurse. So

    ModExp(n) = ∏_{e=0}^{tbits-1} CmodMulInPlaceCore(a^(2^e) mod N, N)   (each controlled by exp bit e)

with per-step precision ``η = shorEta(δ, n) = δ / n²`` (Shor_GateCount.lean) so that the hybrid bound
``modExpApprox_correct : ‖·‖ ≤ tbits · stepErr`` holds. The multipliers ``a^(2^e) mod N`` differ per
step but N is fixed, and the Qualtran And-count of `CmodMulInPlaceCore` is (c,N)-independent (Phase C),
so every step has the SAME cost: the composition is exactly ``tbits`` copies of the Phase-C modmul plus
O(n) Clifford glue (the |1⟩ initialisation).

The composition STRUCTURE is NOT Python's to assert — it is ForShor's. Verification therefore reads the
step count and schedule from the Lean descriptor `emit.lower_modexp` (`shor.modexp.ir/v1`, sourced from
`modExpIdealSteps`); the priced object below is tied to that Lean IR, REAL-LEAN only, and the verdict is
SKIP (never PASS) without the binary. `tbits` and `n_steps` default to 2n for ESTIMATION, but that value
is confirmed against the Lean-emitted `tbits` at verification — Python never certifies the structure.
Leg C1 (cost-structure) is inherited from the embedded step template; action is C2 (approximate,
uninstantiated semantics) as in Phase C.

SINGLE-k: the one representative modmul (hence every internal phase product and QFT twiddle) is the same
arity k as this bloq.

GADGETS (wired 2026-09-09, see `bloqs/modmul_w_gadgets.py`'s own docstring for the underlying mechanism
and its honesty caveats): the "every step has the SAME cost" claim above is a property of the UNPRUNED
algorithm only. `prune_trivial` gadget pricing (Thm 3.1) is genuinely `c`-dependent (which CZPow leaves
are literally-zero angles depends on the concrete controlled multiplier `c = a^(2^e) mod N`, which
DIFFERS across the real `tbits`-step schedule) -- so a `gadgets=True` price is NOT the true aggregate
T-count of a specific `(a, N, L)` order-finding instance's real, varying schedule. `modmul()` instead
prices `tbits` copies of ONE REAL, concrete step -- `c = a mod N` (this bloq's own `a`, step `e=0` of the
real schedule; a genuine value that instance would actually use, not an arbitrary placeholder) -- exactly
mirroring how the unpruned baseline already prices `tbits` copies of one representative step. Read a
`gadgets=True` curve as "cost if every step pruned the way step 0 does", not a per-step-exact sum over
the true schedule; the latter would need `emit.lower_modexp`'s real multiplier list threaded through
(bigger, separate work, not attempted here -- see `docs/history/RESULT_GADGETS_WIRED.2026-09-09.md`).
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Union

import attrs
from qualtran import Bloq, QBit, QUInt, Register, Signature
from qualtran.bloqs.basic_gates import XGate
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from ..emit import Bundle
from .modmul import LeanCtrlModMul


def shor_eta(delta: float, n: int) -> float:
    """`Shor.shorEta δ n = δ / n²` — the per-modular-multiplication precision Shor uses so that the
    tbits-fold hybrid error stays bounded (`modExpApprox_correct`)."""
    return float(delta) / float(n) ** 2


@attrs.frozen
class LeanModExp(Bloq):
    k: int
    n: int
    delta: float = 1e-2                  # target accuracy; per-step η = shorEta(δ, n) = δ/n²
    tbits: Optional[int] = None          # #exponent bits / #controlled modmuls; default 2n (canonical Shor).
                                         # ForShor's ShorGateCountLayout allows n ≤ tbits ≤ 2n.
    N: int = 0
    policy: str = "sym"
    core: str = "recursive"              # the KMY object by default (recursive single-k phase products + QFTs)
    depth: str = "lean"
    qft: str = "lean"
    method: str = "kmy"                   # modular-multiply subroutine: "kmy" (LeanCtrlModMul) |
                                          # "beauregard" (BeauregardCtrlModMul). The ONLY swap point;
                                          # the QPE frame, tbits-fold and IQFT are method-agnostic.
    adder: str = "fourier"                # Beauregard-only sub-block: modular adder family
                                          # "fourier" (Draper, via CUa) | "ripple" (Toffoli). Ignored by KMY.
    a: int = 2                            # the modexp base; only consumed by gadgets=True (see module
                                          # docstring's GADGETS section) -- the unpruned baseline is
                                          # genuinely `a`-independent, so `a` changes nothing when
                                          # gadgets=False (kept as a field so LeanOrderFinding's own `a`
                                          # threads straight through without a separate kwarg).
    gadgets: bool = False                 # apply prune_trivial (Thm 3.1) to the KMY modmul's internal
                                          # phase products, at a representative concrete step (see module
                                          # docstring). No-op when method="beauregard" (no gadget-aware
                                          # Beauregard variant exists).
    phase_bits: Optional[int] = None      # BOUNDED-PRECISION VARIANT: round every modmul phase-product turn
                                          # to b bits (see modmul_w_gadgets.py); a different unitary below
                                          # LeanCtrlModMulWithGadgets.phase_bits_sound(). Needs gadgets=True.
    stop_tsr: float = 11.0                # versionB stop-rule price (threaded to the modmul and its QFTs)
    clamp: bool = False                   # additionally apply modulus clamping (Thm 5.1) to dyadic
                                          # phase-product nodes; needs gadgets=True. See
                                          # bloqs/recursive_w_gadgets.py (CLAMPING) for the statement,
                                          # the conservative boundary and the measured null result.
    schedule: str = "representative"      # STEP SCHEDULE (item 9, 2026-09-10). "representative": tbits copies
                                          # of step 0 (c = a mod N) -- the module-docstring convention.
                                          # "real": the true Lean schedule c_e = a^(2^e) mod N, one priced
                                          # modmul per DISTINCT multiplier with its multiplicity, so a
                                          # gadgets=True price is the exact per-step aggregate of THIS
                                          # (a, N, tbits) instance. Identical cost when gadgets=False (the
                                          # unpruned modmul is (c, N)-free), so the field is a no-op there.
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
            raise ValueError(f"LeanModExp k={self.k} != bundle k={self.bundle_doc.get('k')}: single-k violated")
        if self.schedule not in ("representative", "real"):
            raise ValueError(f"LeanModExp schedule={self.schedule!r} unknown (representative | real)")
        if self.schedule == "real" and self.gadgets and self.method == "kmy" and not self.N:
            raise ValueError("LeanModExp schedule='real' needs a real modulus N to compute a^(2^e) mod N")
        if self.core not in ("onelevel", "recursive", "versionB"):
            raise ValueError(f"LeanModExp core={self.core!r} unknown")
        if self.method not in ("kmy", "beauregard"):
            raise ValueError(f"LeanModExp method={self.method!r} unknown (kmy | beauregard)")
        if self.qft not in ("lean", "reference", "reference-aqft"):
            raise ValueError(f"LeanModExp qft={self.qft!r} unknown (lean | reference | reference-aqft)")
        if self.adder not in ("fourier", "ripple"):
            raise ValueError(f"LeanModExp adder={self.adder!r} unknown (fourier | ripple)")
        if self.gadgets and self.method == "kmy" and not self.N:
            raise ValueError("LeanModExp gadgets=True (method='kmy') needs a real modulus N (got N=0, "
                             "the generic-pricing placeholder) -- phi1/phi2/phi5 all depend on N, and "
                             "the representative multiplier c = a mod N needs a real N to reduce against")
        if self.clamp and not self.gadgets:
            raise ValueError("LeanModExp clamp=True needs gadgets=True (clamping acts on the same concrete "
                             "angles pruning enumerates)")

    @property
    def eta(self) -> float:
        return shor_eta(self.delta, self.n)

    @property
    def n_steps(self) -> int:
        """tbits — number of controlled modular multiplies (one per exponent bit). Default 2n."""
        return int(self.tbits) if self.tbits is not None else 2 * self.n

    def multipliers(self) -> list:
        """The Lean schedule `modExpIdealSteps` prices: multipliers[e] = a^(2^e) mod N for e = 0..tbits-1
        (the same list `emit.lower_modexp` carries and `qiskit_bridge.compose_modexp_bloq` asserts)."""
        N = int(self.N) if self.N else (1 << self.n) - 1
        return [pow(int(self.a), 1 << e, N) for e in range(self.n_steps)]

    def uses_real_schedule(self) -> bool:
        return self.schedule == "real" and self.gadgets and self.method == "kmy"

    def steps(self) -> list:
        """[(priced modmul, multiplicity)] -- one entry (representative x tbits) unless the real schedule is
        on, in which case one entry per distinct multiplier of `multipliers()`."""
        if not self.uses_real_schedule():
            return [(self.modmul(), self.n_steps)]
        from collections import Counter
        return [(self.modmul_for(c), mult) for c, mult in sorted(Counter(self.multipliers()).items())]

    def modmul_for(self, c: int):
        """The gadget-priced controlled modular multiply by the concrete multiplier c."""
        from .modmul_w_gadgets import LeanCtrlModMulWithGadgets
        return LeanCtrlModMulWithGadgets.from_bundle(
            self.bundle_doc, self.n, eta=self.eta, N=self.N, policy=self.policy, qft=self.qft,
            core=self.core, depth=self.depth, rules=self.rules, gadgets=True, c=int(c) % int(self.N), clamp=self.clamp,
            phase_bits=self.phase_bits, stop_tsr=self.stop_tsr)

    def modmul(self):
        """The single representative per-step controlled modular multiply — the SUBSTITUTION POINT.

        method="kmy", gadgets=False → KMY `LeanCtrlModMul` (η = shorEta; every step has this cost,
                                       And (c,N)-free).
        method="kmy", gadgets=True  → `LeanCtrlModMulWithGadgets` (module docstring's GADGETS section),
                                       priced at the REAL representative multiplier `c = a mod N` (step
                                       e=0 of this instance's actual schedule) — a genuinely `c`-dependent
                                       price, unlike the line above.
        method="beauregard"         → `BeauregardCtrlModMul` (Fig 7 CUa; k_max from the SAME budget δ), a
                                       drop-in with the KMY-compatible interface (w_work / qubit_footprint
                                       / build_call_graph). `gadgets` is a no-op here — no gadget-aware
                                       Beauregard variant exists.
        Nothing else in LeanModExp changes."""
        if self.phase_policy is not None and (self.method != "kmy" or self.gadgets):
            raise ValueError("phase_policy is wired for the plain KMY modmul only (method='kmy', gadgets=False)")
        if self.method == "beauregard":
            from .beauregard_modmul import BeauregardCtrlModMul
            N = self.N if self.N else (1 << self.n) - 1     # match the KMY path's default modulus
            return BeauregardCtrlModMul(n=self.n, N=N, delta=self.delta, adder=self.adder)
        if self.gadgets:
            return self.modmul_for(int(self.a) % int(self.N))
        return LeanCtrlModMul.from_bundle(self.bundle_doc, self.n, eta=self.eta, N=self.N, policy=self.policy,
                                          qft=self.qft, core=self.core, depth=self.depth, rules=self.rules,
                                          stop_tsr=self.stop_tsr, phase_policy=self.phase_policy,
                                          pp_convention=self.pp_convention, pp_version=self.pp_version)

    @property
    def signature(self) -> Signature:
        mm = self.modmul()
        return Signature([Register("exponent", QUInt(self.n_steps)), Register("data", QUInt(self.n)),
                          Register("carry", QBit()), Register("work", QUInt(mm.w_work)), Register("flag", QBit())])

    @property
    def qubit_footprint(self) -> int:
        """exponent register (tbits) + the modmul registers, which are REUSED across all steps."""
        return self.n_steps + self.modmul().qubit_footprint

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        # tbits controlled modmuls (multiplier a^(2^e) mod N differs per step; cost identical UNLESS
        # gadgets=True, in which case this still prices tbits copies of ONE representative step's cost
        # -- see module docstring's GADGETS section) + Clifford init.
        counts: Dict[Any, int] = {}
        for bloq, mult in self.steps():
            counts[bloq] = counts.get(bloq, 0) + int(mult)
        counts[XGate()] = 1
        return counts

    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], n: int, delta: float = 1e-2,
                    tbits: Optional[int] = None, N: int = 0, policy: str = "sym", core: str = "recursive",
                    depth: str = "lean", qft: str = "lean", method: str = "kmy",
                    adder: str = "fourier", a: int = 2, gadgets: bool = False,
                    rules: Optional[Any] = None, clamp: bool = False,
                    phase_bits: Optional[int] = None, stop_tsr: float = 11.0,
                    schedule: str = "representative", phase_policy: Optional[Any] = None,
                    pp_convention: Optional[Any] = None, pp_version: str = "B") -> "LeanModExp":
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        return cls(k=doc["k"], n=n, delta=delta, tbits=tbits, N=N, policy=policy, core=core, depth=depth,
                   qft=qft, method=method, adder=adder, a=a, gadgets=gadgets, clamp=clamp, phase_bits=phase_bits,
                   stop_tsr=stop_tsr, schedule=schedule, bundle_doc=doc, rules=rules,
                   phase_policy=phase_policy, pp_convention=pp_convention, pp_version=pp_version)

    def __str__(self) -> str:
        return f"LeanModExp(k={self.k}, n={self.n}, tbits={self.n_steps})"


def concrete_ir_bloq(bundle: Union[Bundle, Dict[str, Any]], n: int, a: int, N: int, tbits: int,
                     delta: float = 1e-2, refresh: bool = False):
    """The Lean modexp descriptor at concrete (n, a, N, tbits, δ), and the Qualtran bloq it composes.

    Reads `emit.lower_modexp` (REAL-LEAN only; EmitError without the binary — no mirror, by design). The
    descriptor carries ForShor's own step count `tbits`, multiplier schedule, and one embedded
    `lowerCmodMul` step template. The returned bloq is `tbits × (step template bloq)`: tbits from Lean,
    the step from the Lean-lowered E5 IR (so leg C1 is against a Lean object, not a Python assumption).
    Returns (bloq, descriptor)."""
    from .. import paths
    from ..emit import lower_modexp

    doc = bundle.doc if isinstance(bundle, Bundle) else bundle
    k = doc["k"]
    desc = lower_modexp(k, int(n), int(a), int(N), int(tbits), float(delta), refresh=refresh)
    lean_tbits = int(desc["modexp"]["tbits"])
    step_ir = desc["modexp"]["step_template"]          # an embedded shor.lowgate.ir/v1 document
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend  # legacy py/
    step_bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(step_ir))

    @attrs.frozen
    class _ComposedModExp(Bloq):
        steps: int
        step: Any = attrs.field(eq=False)

        @property
        def signature(self) -> Signature:
            return Signature([Register("modexp", QUInt(1))])

        def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
            return {self.step: self.steps, XGate(): 1}

    return _ComposedModExp(steps=lean_tbits, step=step_bloq), desc
