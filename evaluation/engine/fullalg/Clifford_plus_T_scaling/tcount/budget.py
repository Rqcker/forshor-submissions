"""The global error budget, its partition, and the tolerance one rotation may spend.

WHY THIS IS A MODULE AND NOT A CONSTANT
---------------------------------------
Every T-count in this project is a function of a precision, and the precision is a *choice* about
how a single global failure probability is divided. Writing `11 T per rotation` hides that choice
and gets the answer wrong: `11` is not a measurement of anything, it is the placeholder
`forshor_qualtran.constants.TS_PER_ROTATION`. The real per-rotation cost at the tolerances this
construction needs is 50-170 T, and it grows with `n`, because the tolerance each rotation may
spend shrinks as the number of rotations grows.

THE PARTITION
-------------
A global budget `delta` (default 0.01) is split:

    delta  =  delta_rot  +  delta_struct
    delta_rot    = rotation_fraction * delta      spent on rotation SYNTHESIS
    delta_struct = (1 - rotation_fraction) * delta   spent on everything else

`delta_struct` is what the KMY construction already spends structurally -- the per-modmul eta of
`py/precision_policy` (eta = delta/n^2) and the K-bit denominator shift -- and it is the number
that must be handed to `price_model_b`, because that is the knob those policies read. `delta_rot`
is then divided equally among the `R` rotations that actually need synthesising:

    eps = delta_rot / R

which is a union bound: R rotations each within `eps` in operator norm put the product within
`R * eps = delta_rot`. Equal division is optimal for this bound when every rotation costs the same
in `log(1/eps)`, which Ross-Selinger says they do.

WHAT `rotation_fraction` SHOULD BE
-----------------------------------
There is no single right answer, which is exactly why it is a parameter. Two values matter:

  * `1.0` reproduces the existing measured census in
    `artifacts/rotation_census/results/` (delta = 0.01 spent entirely on rotations), so a
    calibration ratio computed against those rows must use 1.0 to compare like with like.
  * `0.5` is the honest default for a figure: the structural truncation is real and already
    priced, so giving it none of the budget would double-count the same 0.01.

The difference is small in T and large in honesty: halving `delta_rot` adds exactly
`3.02 * log2(2) ~ 3` T per rotation, about 2% at the tolerances here. It is worth stating rather
than assuming.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

__all__ = ["RotationBudget", "ross_selinger_t", "C_SYN_MEASURED"]

#: Mean T per synthesised rotation, `a * log2(1/eps) + b`.
#:
#: Two independent determinations agree:
#:   * `forshor_qualtran.constants.C_SYN_CERTIFIED = (3.075, 0.86)` -- 17 angles (dyadic 2pi/2^s,
#:     modmul (c-1)/N for N = 15/21/127, seeded random) x eps in 1e-4..1e-16, least squares on the
#:     per-eps mean (scripts/certify_c_syn.py, 2026-09-10).
#:   * measured here, 2026-09-23, on GENERIC non-dyadic residues m/(2N) -- the U1/U5 angle law --
#:     through `ctbp.synth.synthesize_rz` at eps = 1e-7/1e-10/1e-13/1e-17:
#:
#:         log2(1/eps)   23.25   33.22   43.19   56.47
#:         mean T         72.8   101.8   132.2   173.8
#:         Ross-Selinger  72.0   102.1   132.2   172.3
#:
#:     i.e. slope 3.04, intercept 2.1, and a spread across angles of only about +-2% (at eps=1e-17,
#:     T in [172, 176] over the sample). That tight spread is the important part: for a GENERIC
#:     angle the synthesis cost is essentially deterministic given eps, so what actually varies
#:     with (k, n) is the MIX of generic against exactly-representable angles -- see `mix.py`.
C_SYN_MEASURED = (3.075, 0.86)


def ross_selinger_t(eps: float) -> float:
    """Mean T for one synthesised z-rotation at operator-norm tolerance `eps`."""
    if not (0.0 < eps < 1.0):
        raise ValueError(f"eps must be in (0, 1); got {eps!r}")
    a, b = C_SYN_MEASURED
    return a * math.log2(1.0 / eps) + b


@dataclass(frozen=True)
class RotationBudget:
    """A global `delta`, its partition, and the per-rotation tolerance that follows.

    >>> b = RotationBudget(delta=0.01, rotation_fraction=0.5)
    >>> b.delta_rot
    0.005
    >>> round(b.epsilon(1_000_000), 12)
    5e-09
    """

    delta: float = 0.01
    rotation_fraction: float = 0.5

    def __post_init__(self) -> None:
        if not (0.0 < self.delta < 1.0):
            raise ValueError(f"delta must be in (0, 1); got {self.delta!r}")
        if not (0.0 < self.rotation_fraction <= 1.0):
            raise ValueError(
                f"rotation_fraction must be in (0, 1]; got {self.rotation_fraction!r}. "
                f"Zero would leave the rotations no budget at all, which is not a partition "
                f"but a refusal to synthesise.")

    @property
    def delta_rot(self) -> float:
        """The share spent on rotation synthesis."""
        return self.delta * self.rotation_fraction

    @property
    def delta_struct(self) -> float:
        """The share left for structural truncation -- pass THIS to `price_model_b(delta=...)`."""
        return self.delta * (1.0 - self.rotation_fraction)

    def epsilon(self, rotations: float) -> float:
        """Per-rotation tolerance `delta_rot / R` (union bound over `R` rotations)."""
        r = float(rotations)
        if r <= 0:
            raise ValueError(f"rotations must be positive; got {rotations!r}")
        return self.delta_rot / r

    def t_per_rotation(self, rotations: float, mix_ratio: float = 1.0) -> float:
        """Mean T per rotation at this budget.

        `mix_ratio` is the measured correction of `mix.py`: the ratio of the true mean over the
        construction's actual angles to the generic-angle cost `ross_selinger_t(eps)`. It is at
        most 1 and approaches 1 as `n` grows, because the exactly-representable angles are a
        vanishing share of a growing circuit. Passing 1.0 gives the generic-angle cost, which is
        an upper bound on the mean and the right default when no measurement exists.
        """
        if not (0.0 < mix_ratio <= 1.0):
            raise ValueError(f"mix_ratio must be in (0, 1]; got {mix_ratio!r}")
        return mix_ratio * ross_selinger_t(self.epsilon(rotations))

    def describe(self, rotations: Optional[float] = None) -> str:
        line = (f"delta = {self.delta:g}  ->  rotation share {self.rotation_fraction:g} "
                f"(delta_rot = {self.delta_rot:g}, delta_struct = {self.delta_struct:g})")
        if rotations:
            eps = self.epsilon(rotations)
            line += (f"\n  R = {rotations:,.0f} rotations  ->  eps = delta_rot / R = {eps:.3e}"
                     f"  ->  {ross_selinger_t(eps):.1f} T per generic rotation"
                     f"   (the placeholder charges 11)")
        return line
