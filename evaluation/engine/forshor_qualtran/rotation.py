"""Rotation prices: Ross-Selinger only, from the sto_site shared currency (`sto_currency`).

sto_site change (PLAN.md): eps = delta / R. delta is the error budget of ONE phase product (PP or PTP) and
R is that object's own rotation count. The full delta goes to synthesis: `rotation_fraction` is removed
(Lean-To-Qualtran used 0.5, i.e. eps = 0.005 / R). The T per rotation is the rounded Ross-Selinger mean,
round(3.075 log2(1/eps) + 0.86), exactly as tcost prices it.
"""
from __future__ import annotations

from dataclasses import dataclass

from sto_currency import DEFAULT_DELTA, ross_selinger_t, ts_per_rotation

__all__ = ["RotationBudget", "ross_selinger_t"]


@dataclass(frozen=True)
class RotationBudget:
    """A budget `delta` for one priced phase product, divided evenly over its R rotations."""
    delta: float = DEFAULT_DELTA

    def __post_init__(self) -> None:
        if not 0.0 < self.delta < 1.0:
            raise ValueError(f"delta must be in (0, 1); got {self.delta!r}")

    def epsilon(self, rotations: float) -> float:
        if float(rotations) <= 0:
            raise ValueError(f"rotations must be positive; got {rotations!r}")
        return self.delta / float(rotations)

    def t_per_rotation(self, rotations: float) -> int:
        """Return the rounded Ross-Selinger T per rotation for an object with `rotations` rotations."""
        return ts_per_rotation(self.delta, int(rotations))
