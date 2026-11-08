"""Symbolic-n Qualtran bloqs built from the emitted (n-free) bundle.

``LeanPhaseProduct(k, n)`` — the one-level Toom-Cook phase product (E1–E3);
``LeanQFT(w)`` — the `lowerQFTPlan` recursion (E4) with LeanPhaseProduct twiddles;
``ReferenceQFT(w)`` — the textbook ladder (numerically validated, not Lean);
``LeanRecursivePhaseProduct(k, n)`` — the E6 skeleton unrolled;
``PolicyPhaseProduct(policy, n)`` / ``CPolicyPhaseProduct`` — the same recursion with the table chosen
per level by width (uncontrolled / controlled; decomposable at concrete n with a ``phi``);
``LeanCtrlModMul(k, n, eta)`` — the E5 five-step template;
``LeanModExp(k, n)`` — modular exponentiation, the tbits-fold composition of the E5 core (Phase D).

`n` may be an ``int`` (verification) or the package's SymPy symbol ``widths.n`` (estimation) —
EXCEPT for the policy bloqs, which need concrete widths: a policy chooses its table by the actual
operand width (band thresholds), so it has no single symbolic law; sweep them at concrete n.
"""
from __future__ import annotations

from .phase_product import LeanPhaseProduct  # noqa: F401
from .qft import LeanQFT, ReferenceQFT  # noqa: F401
from .recursive import LeanRecursivePhaseProduct  # noqa: F401
from .policy_phase_product import CPolicyPhaseProduct, PolicyPhaseProduct  # noqa: F401
from .modmul import LeanCtrlModMul  # noqa: F401
from .modexp import LeanModExp  # noqa: F401
from .orderfinding import LeanOrderFinding  # noqa: F401
from .multiplier import ToomCookMultiplier  # noqa: F401
