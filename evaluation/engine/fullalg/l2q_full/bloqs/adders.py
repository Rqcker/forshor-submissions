"""adders.py — the modular-adder role as a swappable sub-block (S2 of PLAN_INTERACTIVE_ENGINE).

The Beauregard controlled modular multiply is built from `n` controlled MODULAR ADDITIONS. Which adder
primitive fills that slot is the `adder` role in `engine_registry`:

  "fourier"  Draper QFT-basis phi-adder — Beauregard's `PhiAddMod` (Fig 5), used inside `CUa`. It is a
             REAL Qualtran decomposition (skeletons/shor_graph/beauregard.py); the modmul that uses it is
             priced structurally via `BeauregardCtrlModMul(adder="fourier")` → `CUa`. Draper 2000
             (arXiv:quant-ph/0008033). Textbook, NOT Lean.
  "ripple"   Ripple-carry / Toffoli modular adder (Cuccaro 2004 / Takahashi-Kunihiro 2006 / HRS 2017).
             Computational-basis, NO surrounding QFT — the structural difference from the Fourier family.
             Realized here as the cost-attested `RippleModAdd` leaf below. Textbook, NOT Lean.

HONESTY. `RippleModAdd` is a **cost-attested leaf** (like the skeleton's `SemiclassicalReadout`): it
declares a T-count from the CITED model in `py/baseline_reference.py` and has no gate-level
decomposition, so any estimate that uses it is ATTESTED, not VERIFIED. Its per-addition cost is the
cited HRS controlled-modular-multiply T-count (`baseline_reference.t_ripple("MODMUL", n)`) divided over
the `2n` modular additions that make up one controlled modular multiply (n in the forward `CMULT(a)`,
n in the reverse `CMULT(a⁻¹)†` — Beauregard eq. 3 / HRS §4), so that composing `2n` of them reproduces
the cited modmul total exactly. This is a transparent ATTRIBUTION of a cited quantity, not a new
constant. Ripple arithmetic is Toffoli-based and rotation-synthesis-free, so the cost is
ε-independent (HRS §5.1) — encoded as `rotation=0`.

Runtime: qualtran is a Mac Run-All dependency (as for `beauregard_modmul.py`); this module py-compiles
without it (lazy/guarded imports) and the T-model math is pure `baseline_reference` (stdlib+sympy).
"""
from __future__ import annotations

import math
from functools import cached_property

import attrs

try:
    from qualtran import Bloq, QBit, QUInt, Register, Signature
except Exception:  # pragma: no cover - qualtran absent (sandbox); real use is the target machine Run-All
    Bloq = object  # type: ignore


def _baseline():
    """Import `py/baseline_reference.py` (the cited ripple/Fourier T + qubit models)."""
    import importlib
    import sys
    from pathlib import Path
    pydir = str(Path(__file__).resolve().parents[2] / "py")   # oneclick_pipeline/py
    if pydir not in sys.path:
        sys.path.insert(0, pydir)
    return importlib.import_module("baseline_reference")


def ripple_adder_T(n: int) -> float:
    """Attested per-modular-addition T-count: the cited controlled-modular-multiply T
    (`baseline_reference.t_ripple("MODMUL", n)`) shared over its 2n constituent modular additions.
    Pure model (no qualtran); falls back to the inline HRS leading form if the import is unavailable."""
    n = int(n)
    try:
        modmul_T = float(_baseline().t_ripple("MODMUL", n))
    except Exception:
        # HRS §4: T_mult(n) = 32 n² log₂n Toffoli × 4 T/Toffoli (baseline_reference.TS_PER_TOFFOLI = 4)
        modmul_T = 4.0 * 32.0 * n * n * math.log2(max(n, 2))
    return modmul_T / (2.0 * max(n, 1))                        # 2n additions per controlled modmul


@attrs.frozen
class RippleModAdd(Bloq):
    """Cost-attested ripple-carry (Toffoli) controlled modular adder on an (n+1)-bit accumulator.

    Drop-in for Beauregard's Fourier `PhiAddMod` inside the modular multiply: same doubly-controlled
    modular-addition role, computational-basis instead of Fourier-basis. Cost from the cited HRS model
    (see module docstring) — ATTESTED, textbook, NOT Lean, ε-independent (Toffoli arithmetic)."""
    n: int                         # operand bit-width; accumulator is n+1
    mod: int = 0
    n_ctrl: int = 2                # Beauregard's modular adder is doubly controlled (ctrl ⊗ x_i)
    uncompute: bool = False

    @cached_property
    def signature(self) -> "Signature":
        regs = [Register(f"ctrl{i}", QBit()) for i in range(self.n_ctrl)]
        regs.append(Register("acc", QUInt(int(self.n) + 1)))
        return Signature(regs)

    @property
    def qubit_footprint(self) -> int:
        # (n+1) accumulator + 1 ripple-carry ancilla (Cuccaro MAJ/UMA) + the n_ctrl control lines.
        # Not on LeanModExp's critical path — the composed modmul footprint (2n+2) is authoritative
        # (BeauregardCtrlModMul); this is the local leaf width.
        return int(self.n) + 2 + int(self.n_ctrl)

    def my_static_costs(self, cost_key):
        """Attested QECGatesCost: `ripple_adder_T(n)` T-gates, no synthesized rotations."""
        try:
            from qualtran.resource_counting import GateCounts, QECGatesCost
        except Exception:  # pragma: no cover
            return NotImplemented
        if isinstance(cost_key, QECGatesCost):
            return GateCounts(t=int(round(ripple_adder_T(int(self.n)))), rotation=0)
        return NotImplemented

    def adjoint(self) -> "RippleModAdd":
        return attrs.evolve(self, uncompute=not self.uncompute)

    def __str__(self) -> str:
        return f"RippleModAdd(n={self.n}){'†' if self.uncompute else ''}"


#: the two adder-role methods → how they are realized (documentation surfaced by the registry).
ADDER_METHODS = {
    "fourier": "Draper QFT-basis phi-adder (Beauregard PhiAddMod, inside CUa) — real Qualtran "
               "decomposition; Draper arXiv:quant-ph/0008033. Textbook, NOT Lean.",
    "ripple":  "Ripple-carry / Toffoli modular adder (RippleModAdd, cost-attested from HRS "
               "arXiv:1611.07995). Textbook, NOT Lean, ATTESTED, ε-independent.",
}
