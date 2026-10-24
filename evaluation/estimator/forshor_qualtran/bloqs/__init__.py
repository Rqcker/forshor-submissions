"""Symbolic-n Qualtran bloqs, built from the emitted (n-free) bundle.

``LeanRecursivePhaseProduct(k, n)``: the E6 skeleton, unrolled.
``PolicyPhaseProduct(policy, n)`` / ``CPolicyPhaseProduct``: the same recursion, with the table selected
at each level by width (uncontrolled / controlled; decomposable at concrete n with a ``phi``).

`n` can be an ``int`` (verification) or the SymPy symbol ``widths.n`` of the package (estimation).
The EXCEPTION is the policy bloqs, which need concrete widths. A policy selects its table by the actual
operand width (band thresholds), so it has no single symbolic law. Sweep them at concrete n.
"""
from __future__ import annotations

import importlib

#: name -> submodule. The first access imports it, so `import forshor_qualtran.bloqs` stays fast.
_LAZY = {"LeanRecursivePhaseProduct": "recursive", "LeanAsymRecursivePhaseProduct": "recursive",
         "PolicyPhaseProduct": "policy_phase_product", "CPolicyPhaseProduct": "policy_phase_product"}
__all__ = sorted(_LAZY)


def __getattr__(name):
    if name in _LAZY:
        return getattr(importlib.import_module(f".{_LAZY[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
