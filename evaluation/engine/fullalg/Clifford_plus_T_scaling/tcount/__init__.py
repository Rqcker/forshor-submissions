"""Accurate Clifford+T cost per rotation, and the figures that use it.

The pipeline's plotted T-counts charge `forshor_qualtran.constants.TS_PER_ROTATION = 11` per
rotation. That number is a placeholder, not a measurement: the real cost of synthesising one
rotation to the tolerance this construction needs is 50-170 T and grows with `n`, because the
tolerance each rotation may spend is the global budget divided by the number of rotations.

    budget.py    delta -> (delta_rot, delta_struct) -> eps = delta_rot / R -> T per rotation
    mix.py       how much cheaper the real angles are than generic ones (measured; ~1 past n~100)
    measure.py   sample the real angle population and synthesise it through pygridsynth
    model.py     the per-(k, n) T-per-rotation used by the figures, and where each value came from

Nothing here modifies `ctbp/` or the existing figure scripts; these are new versions alongside.
"""
from .budget import RotationBudget, ross_selinger_t          # noqa: F401
from .mix import MixRatio, census_rows, fit_gap_law, mix_ratio  # noqa: F401

__all__ = ["RotationBudget", "ross_selinger_t", "MixRatio", "census_rows", "fit_gap_law",
           "mix_ratio"]
