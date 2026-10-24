"""Cost constants. This module is the only location where they are defined.

Only Ross-Selinger synthesis (`forshor_qualtran.rotation`) gives the price of a rotation. It uses the
measured law for the mean T count below. This package has no flat price for each rotation.
"""
from __future__ import annotations

#: The Ross-Selinger mean T for each synthesised rotation: a * log2(1/eps) + b. A check against pygridsynth
#: (2026-09-10) certified it. The check used 17 angles (dyadic 2pi/2^s, modmul-style (c-1)/N for N = 15/21/127,
#: seeded random) x eps in 1e-4..1e-16. The values are a least-squares fit of the mean T count for each eps.
C_SYN_CERTIFIED = (3.075, 0.86)

#: The ForShor `rippleAdderGateBound` (Lean gate model). The recursion mirror uses it.
LEAN_RIPPLE_ADDER = lambda w: 9 * w + 2  # noqa: E731
