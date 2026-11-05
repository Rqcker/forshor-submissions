"""`ToomCookMultiplier(n, w_out)` — the full Toom-Cook multiplication |x⟩|0⟩ → |x⟩|c·x mod 2^{w_out}⟩ as the KMY / ForShor
structure uses it: H^{⊗w_out} on the output register, one recursive PhaseProduct between x and the output register with
the classical multiplier c folded into the phase (φ = 2π c / 2^{w_out}), then IQFT(w_out).  This is exactly step U1 of
ForShor's `CmodMulInPlaceCore` (H_reg ; CPhaseProdUsing ; IQFT) without the control — so `CmodMulInPlaceCore` contains three
such multiplications (two controlled) plus the modular-reduction primitives; that is why it is the top object.

Cost model: H (Clifford) ; PhaseProduct = `ToomRecursivePhaseProduct(version B, rule R2)` at width max(n, w_out) + 1 (the
zero-extension ForShor's `_pp` convention uses) ; IQFT = `LeanQFT(core="versionB")` (E4 Cooley–Tukey structure with version-B
twiddles).  Theory: T = Θ(n^{log_k(2k−1)}) — the phase product and the QFT twiddles share the exponent.

Exact-action check (tier C, small n): `multiplier_unitary_check(n, w_out, c)` builds H^{⊗w}, the exact diagonal
exp(2πi c x z / 2^w) and the exact inverse DFT with numpy and verifies |x⟩|0⟩ ↦ |x⟩|c·x mod 2^w⟩ for every x.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import attrs
import numpy as np
from qualtran import Bloq, QUInt, Register, Signature
from qualtran.bloqs.basic_gates import Hadamard
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from .qft import LeanQFT


@attrs.frozen
class ToomCookMultiplier(Bloq):
    k: int
    n: int
    w_out: int
    bundle_doc: Optional[dict] = attrs.field(default=None, eq=False, repr=False)
    rules: Optional[Any] = attrs.field(default=None, eq=False, repr=False)

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.n)), Register("out", QUInt(self.w_out))])

    @property
    def pp_width(self) -> int:
        return max(self.n, self.w_out) + 1

    def _pp(self) -> Bloq:
        from ..recursion_versions import ToomRecursivePhaseProduct
        assert self.rules is not None
        return ToomRecursivePhaseProduct.from_rules(self.rules, self.pp_width, self.k, "B", "kmy")

    def _iqft(self) -> Bloq:
        return LeanQFT.from_bundle(self.bundle_doc, self.w_out, "tight", core="versionB", rules=self.rules)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        return {Hadamard(): self.w_out, self._pp(): 1, self._iqft(): 1}

    @property
    def qubit_footprint(self) -> int:
        pp = self._pp()
        return self.n + self.w_out + (pp.qubit_footprint - 2 * pp.w)   # operands + the phase product's padding + And ancilla

    @classmethod
    def from_rules(cls, rules, bundle_doc: dict, n: int, w_out: Optional[int] = None) -> "ToomCookMultiplier":
        return cls(k=bundle_doc["k"], n=n, w_out=w_out or n, bundle_doc=bundle_doc, rules=rules)

    def __str__(self) -> str:
        return f"ToomCookMultiplier(k={self.k}, n={self.n}, w_out={self.w_out})"


def concrete_ir_bloq(bundle, n: int, c: int, w_out: int, refresh: bool = False):
    """The Lean Toom-Cook multiply descriptor at concrete (n, c, w), and the Qualtran bloq it composes.

    Reads `emit.lower_multiply` (REAL-LEAN only; EmitError without the binary). The descriptor embeds the
    Phase-B PhaseProduct IR (interpolation coefficients + the proved recursion — all
    ForShor's) and the Phase-A IQFT IR. The returned bloq is H^{⊗w} + (PP IR bloq) + (IQFT IR bloq), so
    the priced multiply is tied to a Lean object whose coefficients and recursion come from ForShor.
    Returns (bloq, descriptor)."""
    from .. import paths
    from ..emit import lower_multiply

    doc = bundle.doc if isinstance(bundle, dict) else bundle.doc
    k = doc["k"]
    desc = lower_multiply(k, int(n), int(c), int(w_out), refresh=refresh)
    pp_ir = desc["multiply"]["phase_product"]
    iqft_ir = desc["multiply"]["iqft"]
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend  # legacy py/
    pp_bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(pp_ir))
    iqft_bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(iqft_ir))

    @attrs.frozen
    class _ComposedMultiply(Bloq):
        w: int
        pp: Any = attrs.field(eq=False)
        iqft: Any = attrs.field(eq=False)

        @property
        def signature(self) -> Signature:
            return Signature([Register("mul", QUInt(1))])

        def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
            return {Hadamard(): self.w, self.pp: 1, self.iqft: 1}

    return _ComposedMultiply(w=int(w_out), pp=pp_bloq, iqft=iqft_bloq), desc


def multiplier_unitary_check(n: int, w_out: int, c: int) -> Dict[str, Any]:
    """Exact numpy realisation of H^{⊗w} ; exp(2πi c·x·z/2^w) ; IDFT_w on |x⟩|0⟩ for every x < 2^n; returns the max
    infidelity against |x⟩|c·x mod 2^w⟩ (tier C — a numerical identity, no Lean object)."""
    W = 2 ** w_out
    dft = np.exp(2j * np.pi * np.outer(np.arange(W), np.arange(W)) / W) / np.sqrt(W)
    worst = 0.0
    for x in range(2 ** n):
        psi = np.ones(W) / np.sqrt(W)                                   # H^{⊗w}|0⟩
        psi = psi * np.exp(2j * np.pi * c * x * np.arange(W) / W)       # the phase product with c folded into φ
        psi = dft.conj().T @ psi                                        # IQFT
        target = np.zeros(W); target[(c * x) % W] = 1.0
        worst = max(worst, 1.0 - abs(np.vdot(target, psi)) ** 2)
    # worst accumulates as np.float64, so `worst < 1e-10` is np.bool_, not Python bool -- wrap it so
    # callers doing `r["ok"] is True` (identity, not truthiness) get a real match (2026-09-09 regression).
    return {"n": n, "w_out": w_out, "c": c, "max_infidelity": float(worst), "ok": bool(worst < 1e-10)}
