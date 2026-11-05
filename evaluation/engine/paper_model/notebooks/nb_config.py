"""Shared parameters for `notebooks/00_subroutine_composition.ipynb` — single source of truth.

Dependency-free (stdlib only) so it survives package churn and can be imported by the notebook, the
`notebook_helpers` module (built later), and the preflight checker. The notebook's parameter cell should
`from nb_config import *` and print the values, not redefine them.
"""
from __future__ import annotations

import math
from typing import List

# --- k enters at the Lean level; n only at estimation --------------------------------------------
KS: List[int] = [2, 3, 4, 5, 6, 7]
N_MAX: int = 50000
DASHED = (1024, 2048, 4096, 8192)        # vertical dashed reference lines on every resource plot
ETA: float = 1e-12                       # the "fixed"-policy per-qubit precision (KMY's 2048-bit pin)

# --- precision policy for the per-modular-multiplication η (py/precision_policy.py) --------------
# "appropriate" → η = shorEta(δ, n) = δ/n²  (Lean-defined, scales with n; the principled default)
# "fixed"       → η = ETA                    (n-independent pin; legacy / worst-case comparison)
PRECISION_POLICY: str = "appropriate"
DELTA: float = 1e-2                      # total order-finding error budget for the appropriate policy

# --- run mode: consume the pre-built executables, refuse the mirror ------------------------------
BUILD: bool = False                      # the notebook NEVER builds — see README.md
REQUIRE_REAL: bool = True                # REAL-LEAN or stop (dev-only mirror smoke test flips this to False)

# --- demo instance for the stepwise/validate cells (small, concrete) -----------------------------
DEMO = {"k": 3, "phi_num": 1, "phi_den": 8, "a": 7, "N": 15, "eta": ETA}   # phi in units of pi

# --- where figures/manifests are written ----------------------------------------------------------
ARTIFACT_SUBDIR = "artifacts/subroutines"

# --- the fit window for the exponent tables (log-log least squares) ------------------------------
# Truly asymptotic region. The grid is densified here so the fit has >= 6 points in [40000, 50000].
FIT_WINDOW = (40000, 50000)


def n_grid(lo: int = 4, hi: int = N_MAX, per_decade: int = 24,
           fit_window: tuple = FIT_WINDOW, fit_points: int = 8) -> List[int]:
    """Log-spaced integer sweep grid (4 … 50000), UNIONED with `fit_points` evenly-spaced points across
    `fit_window` so the asymptotic fit is stable in the narrow [40000, 50000] band. Deduped, pure stdlib."""
    lo, hi = int(lo), int(hi)
    steps = max(2, int(round(per_decade * math.log10(hi / lo))))
    vals = {int(round(lo * (hi / lo) ** (i / steps))) for i in range(steps + 1)}
    fw_lo, fw_hi = int(fit_window[0]), int(fit_window[1])
    vals |= {int(round(fw_lo + i * (fw_hi - fw_lo) / (fit_points - 1))) for i in range(fit_points)}
    return [v for v in sorted(vals) if lo <= v <= hi]


N_GRID = n_grid()
FIT_WINDOW_POINTS = [n for n in N_GRID if FIT_WINDOW[0] <= n <= FIT_WINDOW[1]]   # >= 6 by construction
