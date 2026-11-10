"""ForShor's `oneQubit` negation charge, as `tcost` and the second counting engine price a `negate` table operation.

The PhaseProduct and PhaseTripleProduct boards used to price a negation as Qualtran's `Negate(QInt(w))`: about 4 (w - 1) T and
2 w - 1 clean ancilla qubits. The full order-finding run prices it as ForShor's `oneQubit` NegationMethod (modular swap 001): the
bitwise complement and Gidney's increment gate with ONE clean ancilla. Both boards now charge the full run's number, so one policy
has one price for a negation wherever it is priced.

The counts are ForShor's `negateResources` for `oneQubit` (Lean `negateXGates`, `negateCnotGates`, `negateToffoliGates`, with Lean's
truncating subtraction):

    w even:  Toffoli 12 w - 28   CNOT 17 w - 22   X 5 w - 3
    w odd :  Toffoli 10 w - 18   CNOT 17 w - 5    X 5 w + 1

They are the closed forms of `fullalg/l2q_full/bloqs/gidney_increment.py` `bound_counts` (equal to the explicit circuit's counts for
w >= 7 and an upper bound below). This module repeats them so `tcost` stays importable without the full-run package;
`tests/test_negation_counts.py` asserts that the two agree at every width from 1 to 5000.

`negate_counts` accepts a Python integer (exact integers) or a sympy expression (a closed form with `Mod` and `Max`, which evaluates to
the same integers once the width symbol is substituted: `tcost.score` builds the counts symbolically and substitutes the level widths).
"""
from __future__ import annotations

import numbers
from typing import Any, Dict

import sympy

#: The clean ancilla qubits a negation holds while it runs: ForShor's `oneQubit` (`negate_clean_ancilla`).
CLEAN_ANCILLA = 1


def negate_counts(w: Any) -> Dict[str, Any]:
    """`{x, cnot, toffoli}` for a `w`-bit negation under `oneQubit`."""
    if isinstance(w, numbers.Integral):
        w = int(w)
        t = lambda a, b: max(0, a - b)
        if w % 2 == 0:
            return {"x": t(5 * w, 3), "cnot": t(17 * w, 22), "toffoli": t(12 * w, 28)}
        return {"x": 5 * w + 1, "cnot": t(17 * w, 5), "toffoli": t(10 * w, 18)}
    w = sympy.sympify(w)
    odd = sympy.Mod(w, 2)
    even = 1 - odd
    t = lambda a, b: sympy.Max(0, a - b)
    return {"x": even * t(5 * w, 3) + odd * (5 * w + 1),
            "cnot": even * t(17 * w, 22) + odd * t(17 * w, 5),
            "toffoli": even * t(12 * w, 28) + odd * t(10 * w, 18)}
