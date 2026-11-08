"""The T-per-rotation actually used by a figure, and where each value came from.

THE REPLACEMENT IS EXACT, NOT A REFIT
-------------------------------------
`QECGatesCost.total_t_count(ts_per_rotation=x)` is affine in `x`:

    T(x)  =  T_base  +  R * x

so a curve priced at the placeholder `x = 11` can be corrected without re-pricing anything:

    T_base  =  T(11) - 11 * R
    T_true  =  T_base + R * t_per_rotation

Only `R` is needed alongside the existing curve, and `R` is itself a closed form the cost model
returns instantly even at n = 50,000 (`price_model_b(..., what="rotation")`, 0.08 s). This is a
POST-PROCESSING step on data already computed, exactly as asked -- no sweep is re-run.

WHERE `t_per_rotation` COMES FROM
---------------------------------
    t_per_rotation(k, n)  =  mix_ratio(k, n) * ross_selinger_t(delta_rot / R(k, n))

with the mix ratio measured where a measurement exists and extrapolated otherwise (`mix.py`
records which). At every n these figures plot the ratio is within 1e-4 of 1, so the correction
that matters is the tolerance, not the mix.

WHAT IT DOES TO THE ASYMPTOTICS
-------------------------------
This changes the exponent story, and the change is the reason the correction is worth making.
With a constant charge per rotation the T-count inherits the rotation count's exponent:

    T  ~  R  ~  n^{1 + log_k(2k-1)}                      (order-finding, modular exponentiation)
          R  ~  n^{log_k(2k-1)}                          (multiply, controlled modmul, QFT)

With the honest charge the tolerance itself depends on n, because `eps = delta_rot / R`:

    T  ~  R * [a log2(1/eps) + b]
       =  R * [a log2(R / delta_rot) + b]
       ~  n^{e} * log(1/eps),            e = the same exponent as above

since `log2(R/delta_rot) = e*log2(n) + log2(1/delta_rot) + O(1)`. So the asymptotic form to fit
is `n^{2+eps} log(1/eps)` for order-finding and modular exponentiation, and
`n^{1+eps} log(1/eps)` for multiplication, QFT and the phase product -- one extra logarithm in
each case, and NOT a change of exponent. The polynomial part is untouched; what the placeholder
was hiding is a logarithmic factor and a constant of order 12-15x.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .budget import RotationBudget, ross_selinger_t
from .mix import MixRatio, mix_ratio

__all__ = ["RotationCost", "load_sampled", "rotation_cost", "correct_t",
           "asymptotic_form", "fit_log_power_law"]

MEASURED_JSONL = (Path(__file__).resolve().parent.parent / "artifacts" / "tcount"
                  / "measured.jsonl")

#: stage -> the polynomial exponent rule, and the LaTeX form of the corrected asymptotics.
_FORMS = {
    "mult": (r"n^{1+\epsilon}\log(1/\epsilon)", "one Toom-Cook multiply"),
    "exp":  (r"n^{2+\epsilon}\log(1/\epsilon)", "Theta(n) multiplies"),
}


@dataclass(frozen=True)
class RotationCost:
    k: int
    n: int
    rotations: float
    eps: float
    t_per_rotation: float
    mix: MixRatio
    generic_T: float

    @property
    def source(self) -> str:
        return self.mix.source

    def line(self) -> str:
        return (f"k={self.k} n={self.n:>6}  R={self.rotations:.4e}  eps={self.eps:.3e}  "
                f"T/rot = {self.t_per_rotation:.2f}  (generic {self.generic_T:.2f}, "
                f"mix {self.mix.ratio:.6f} [{self.mix.source}])")


def load_sampled(path: Path = MEASURED_JSONL) -> Dict[Tuple[int, int], float]:
    """`{(k, n): ratio_to_model}` from a `measure.py` run, latest row per cell wins."""
    out: Dict[Tuple[int, int], float] = {}
    if not path.is_file():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
            out[(int(r["k"]), int(r["n"]))] = float(r["ratio_to_model"])
        except Exception:
            continue
    return out


def rotation_cost(k: int, n: int, rotations: float, budget: RotationBudget, *,
                  sampled: Optional[Dict[Tuple[int, int], float]] = None,
                  rows: Optional[List[Dict[str, Any]]] = None) -> RotationCost:
    """T per rotation at `(k, n)` under `budget`, given that cell's rotation count."""
    eps = budget.epsilon(rotations)
    generic = ross_selinger_t(eps)
    m = mix_ratio(k, n, rows=rows, sampled=sampled)
    return RotationCost(k=k, n=n, rotations=float(rotations), eps=eps,
                        t_per_rotation=m.ratio * generic, mix=m, generic_T=generic)


def correct_t(t_at_placeholder: float, rotations: float, t_per_rotation: float,
              placeholder: float = 11.0) -> float:
    """Replace the placeholder per-rotation charge in an already-computed T-count.

    `total_t_count` is affine in the per-rotation charge, so this is exact, not an approximation:
    subtract what the placeholder contributed and add what synthesis really costs.
    """
    t_base = float(t_at_placeholder) - float(placeholder) * float(rotations)
    if t_base < 0:
        # Would mean the rotations alone exceed the reported total -- the inputs do not belong
        # to the same curve. Better to say so than to plot a negative baseline.
        raise ValueError(
            f"T_base came out negative ({t_base:.4g}): T({placeholder}) = {t_at_placeholder:.4g} "
            f"but {placeholder} x R = {placeholder * rotations:.4g}. The T-count and the rotation "
            f"count are not from the same pricing call.")
    return t_base + float(rotations) * float(t_per_rotation)


def asymptotic_form(rule: str) -> Tuple[str, str]:
    """`(latex, why)` for the corrected asymptotic form of a stage with this exponent rule."""
    if rule not in _FORMS:
        raise ValueError(f"rule {rule!r} not in {sorted(_FORMS)}")
    return _FORMS[rule]


def fit_log_power_law(ns, ts, exponent: float, delta_rot: float, rotations,
                      window: Tuple[float, float]) -> Optional[Dict[str, float]]:
    """Fit `T = C * n^exponent * log2(1/eps(n))` -- one free constant, and report its R^2.

    The model being tested is the corrected asymptotic form, so the log factor is carried
    explicitly rather than absorbed into a fitted exponent: fitting `T` against `n` alone would
    return an exponent slightly above the true one and invite the reader to conclude the
    polynomial order is higher than it is. Here the polynomial part is FIXED at the value the
    construction's analysis gives, the logarithm is computed from each point's own `eps`, and the
    only fitted quantity is the constant -- so `R^2` measures whether the data follow the
    predicted shape.
    """
    lo, hi = window
    xs, ys = [], []
    for n, t, r in zip(ns, ts, rotations):
        if not (lo <= n <= hi) or t <= 0 or r <= 0:
            continue
        eps = delta_rot / float(r)
        basis = (float(n) ** exponent) * math.log2(1.0 / eps)
        if basis > 0:
            xs.append(math.log(basis))
            ys.append(math.log(float(t)))
    if len(xs) < 3:
        return None
    nn = len(xs)
    log_c = sum(y - x for x, y in zip(xs, ys)) / nn          # fixed unit slope in log-log
    ss_tot = sum((y - sum(ys) / nn) ** 2 for y in ys)
    ss_res = sum((y - (x + log_c)) ** 2 for x, y in zip(xs, ys))
    return {"C": math.exp(log_c), "exponent": exponent,
            "R2": 1.0 - ss_res / ss_tot if ss_tot else float("nan"), "n_points": nn}
