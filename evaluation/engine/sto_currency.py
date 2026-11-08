"""sto_site shared T currency. BOTH engines (tcost and forshor_qualtran) import their prices from here.

Site conventions (PLAN_v2 Stage 5, replacing the grid-leaf conventions of PLAN.md): the PhaseProduct leaf is the
digital product (a multiplier, then one rotation per product bit); a negated source costs one adder (Subtract);
And = 4 T; rotation = Ross-Selinger mean 3.075 log2(1/eps) + 0.86, NOT rounded; eps = ROTATION_FRACTION * delta / R,
where delta is the error budget of ONE phase product and R is that object's own rotation count. Half of delta goes
to synthesis (`ROTATION_FRACTION` = 0.5, the L2Q headline tier). No flat 11 T per rotation. PhaseTripleProduct
uses the digital triple-product leaf since Stage 17 (2026-10-28; tcost-v4 / sto-site-v3): p = x*y*z computed exactly
with two Qualtran products, one rotation per bit of p; the grid leaf is the labelled cross-check.

Origin: sto_policy_cost/tcost/currency.py (tcost-v2), changed to the model above (tcost-v3).

    T = t + TS_PER_TOFFOLI * (toffoli + and + cswap) + R * (3.075 * log2(1/eps) + 0.86)

* And / Toffoli / CSwap: 4 T each. This is the `total_t_count` convention of Qualtran, with a measurement-based
  And uncompute. (The ledger currency of Lean-To-Qualtran is 8 T per And pair, with a unitary uncompute. This
  benchmark uses its 4 T comparison column with a measured uncompute. The owner made this decision.)
* Rotations: only Ross-Selinger synthesis, at its measured mean T(eps) = 3.075 log2(1/eps) + 0.86
  (Lean-To-Qualtran `C_SYN_CERTIFIED`, certified against pygridsynth). The T per rotation is NOT rounded.
  eps = ROTATION_FRACTION * delta / R divides that share of the budget evenly over the R rotations.
  Alternatively, the caller gives a fixed eps per rotation.

`leaderboard/config.json -> tcount` must state `MODEL_VERSION` and `CONVENTIONS`. The backend rejects a config
that does not agree (`tcost.schema.validate_tcount`). Increase `MODEL_VERSION` after each change that changes a
stored count or T (the constants here, the bloq constructions, the Qualtran pin).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

#: The version of the full cost model (constructions and currency). Each stored score includes it.
MODEL_VERSION = "tcost-v4"
#: The version of the sto_site conventions (both engines). Bump on any change that moves a number.
SITE_MODEL_VERSION = "sto-site-v3"
#: The leaf of each mode: the digital product for PhaseProduct, the digital triple product for PhaseTripleProduct
#: (Stage 17, 2026-10-28; the schoolbook grid is the labelled PTP cross-check, leaf "grid").
LEAF_BY_MODE = {"PhaseProduct": "product", "PhaseTripleProduct": "product"}
#: Kept for callers that read one name: the PhaseProduct leaf.
LEAF = LEAF_BY_MODE["PhaseProduct"]
#: The share of the error budget delta that goes to rotation synthesis.
ROTATION_FRACTION = 0.5
#: A negated source in `addScaled` costs one adder (Qualtran `Subtract`), not Negate . Add . Negate.
NEGSRC = "subtract"
#: T per Toffoli / And / CSwap.
TS_PER_TOFFOLI = 4
#: The measured law of synthesis: T per rotation = a*log2(1/eps) + b.
C_SYN_CERTIFIED = (3.075, 0.86)
#: The default total budget for rotation synthesis.
DEFAULT_DELTA = 0.01
#: The config must state these conventions again, exactly. The backend validates them: they are not decoration.
CONVENTIONS = {
    "angle": "generic",
    "leaf": "qualtran-product (PP); qualtran-product-triple (PTP)",
    "adder": "qualtran-add-uniform-width",
    "negsrc": NEGSRC,
    "ts_per_toffoli": TS_PER_TOFFOLI,
    "rotation_synthesis": "3.075*log2(1/eps)+0.86 (unrounded), eps=0.5*delta/R",
}


def ross_selinger_t(eps: float) -> float:
    """Return the Ross-Selinger mean T for one synthesized z-rotation at tolerance eps (the only price of a
    rotation)."""
    a, b = C_SYN_CERTIFIED
    return a * math.log2(1.0 / eps) + b


#: The former name of `ross_selinger_t`. It stays for the callers that use it.
c_syn_measured = ross_selinger_t


def ts_per_rotation_eps(eps: float) -> float:
    """Return the T per rotation at a precision eps per rotation, 0 < eps < 1 (not rounded)."""
    if not (isinstance(eps, (int, float)) and 0 < eps < 1):
        raise ValueError(f"rotation precision eps must be in (0, 1), got {eps!r}")
    return max(1.0, ross_selinger_t(eps))


def rotation_eps(delta: float, rotations: int) -> float:
    """Return the precision of each rotation: ROTATION_FRACTION of the budget delta, shared by `rotations`."""
    return ROTATION_FRACTION * float(delta) / max(int(rotations), 1)


def ts_per_rotation(delta: float, rotations: int) -> float:
    """Return the T per rotation if the synthesis share of a budget delta (0 < delta < 1) is divided over `rotations`
    rotations."""
    if not (isinstance(delta, (int, float)) and 0 < delta < 1):
        raise ValueError(f"rotation budget delta must be in (0, 1), got {delta!r}")
    return ts_per_rotation_eps(rotation_eps(delta, rotations))


def t_from_counts(counts: Dict[str, int], tpr: float) -> float:
    """Return T at a given T per rotation. This is the ONE place that writes the currency formula."""
    return (counts["t"] + TS_PER_TOFFOLI * (counts["toffoli"] + counts["and"] + counts["cswap"])
            + counts["rotation"] * tpr)


def t_count(counts: Dict[str, int], delta: Optional[float] = None, eps: Optional[float] = None) -> Dict[str, Any]:
    """Return the ranking currency with its breakdown. Give exactly one of `delta` (budget,
    default) or `eps` (fixed)."""
    if delta is not None and eps is not None:
        raise ValueError("give a rotation budget delta or a per-rotation eps, not both")
    R = counts["rotation"]
    if eps is not None:
        tpr = ts_per_rotation_eps(eps) if R else 0
        rot_eps, budget = eps, None
    else:
        delta = DEFAULT_DELTA if delta is None else delta
        tpr = ts_per_rotation(delta, R) if R else 0
        rot_eps, budget = (rotation_eps(delta, R) if R else None), delta
    t_nonrot = t_from_counts(counts, 0)
    return {"t_count": t_nonrot + R * tpr, "t_from_toffoli_class": t_nonrot, "t_from_rotations": R * tpr,
            "ts_per_rotation": tpr, "rotation_eps": rot_eps, "delta": budget}
