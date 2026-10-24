"""Rotation prices: Ross-Selinger only.

Each rotation is a synthesised z-rotation. The Ross-Selinger law gives its price:

    T(eps) = 3.075 * log2(1/eps) + 0.86          (`constants.C_SYN_CERTIFIED`)

The law uses the tolerance eps = delta_rot / R for each rotation. There is a global budget `delta`
(default 0.01). The fraction `rotation_fraction` (default 0.5) of it goes to synthesis, divided
equally (union bound) over the R rotations of the priced object. There is no flat price for each
rotation, no discount for the angle mix and no other synthesiser.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .constants import C_SYN_CERTIFIED

__all__ = ["RotationBudget", "ross_selinger_t"]


def ross_selinger_t(eps: float) -> float:
    """Return the mean T for one synthesised z-rotation at the operator-norm tolerance `eps`."""
    if not 0.0 < eps < 1.0:
        raise ValueError(f"eps must be in (0, 1); got {eps!r}")
    a, b = C_SYN_CERTIFIED
    return a * math.log2(1.0 / eps) + b


@dataclass(frozen=True)
class RotationBudget:
    """A global `delta`, the fraction `rotation_fraction` of it for synthesis, and eps = delta_rot / R."""
    delta: float = 0.01
    rotation_fraction: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 < self.delta < 1.0:
            raise ValueError(f"delta must be in (0, 1); got {self.delta!r}")
        if not 0.0 < self.rotation_fraction <= 1.0:
            raise ValueError(f"rotation_fraction must be in (0, 1]; got {self.rotation_fraction!r}")

    @property
    def delta_rot(self) -> float:
        return self.delta * self.rotation_fraction

    def epsilon(self, rotations: float) -> float:
        if float(rotations) <= 0:
            raise ValueError(f"rotations must be positive; got {rotations!r}")
        return self.delta_rot / float(rotations)

    def t_per_rotation(self, rotations: float) -> float:
        """Return the Ross-Selinger T for each rotation, if the priced object has `rotations` rotations."""
        return ross_selinger_t(self.epsilon(rotations))
