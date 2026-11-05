"""How much cheaper the construction's real angles are than a generic angle at the same tolerance.

THE ONE NUMBER THIS MODULE PRODUCES
-----------------------------------
`mix_ratio(k, n)`: the ratio of the TRUE mean T per rotation, over the angles this construction
actually contains, to the cost of a generic angle at the same `eps`. It is at most 1, and it is
below 1 only because some angles are exactly representable in Clifford+T and cost far less than
Ross-Selinger's word.

    true mean T per rotation  =  mix_ratio(k, n) * ross_selinger_t(eps)

WHY IT IS NOT A FREE PARAMETER
------------------------------
Two facts, both measured, pin it:

1. For a GENERIC (non-dyadic) angle the synthesis cost is essentially deterministic given `eps`.
   Measured 2026-09-23 on residues m/(2N) -- the U1/U5 angle law -- through `ctbp.synth`:
   at eps = 1e-17 the T-count over the sample lay in [172, 176] against a mean of 173.8, a spread
   of about +-2%. So nothing interesting varies on the synthesis side.

2. What varies is the MIX. The construction's angle multiset is part exactly-representable
   (dyadic turns c/2^s from the U2 leaves and every QFT twiddle, exact in Clifford+T for small s)
   and part generic (the U1/U5 modular-multiply leaves, tau = +- r*k(c)*2^(i+j)/(2N), which are
   integer multiples of 1/(2N) and non-dyadic for a generic modulus). The exact share is a
   FIXED-SIZE population sitting inside a circuit that grows like n^{2+eps}, so it is diluted
   away as n grows.

MEASURED, AND THE LAW THAT FOLLOWS
----------------------------------
From `artifacts/rotation_census/results/` (real pygridsynth runs, delta = 0.01 spent entirely on
rotations, k = 2), comparing the measured mean against `budget.ross_selinger_t` at that run's own
eps:

    n            4       6       8      10      12      14      32
    R       28,203 114,793  1.88e6  2.39e6  2.79e6  3.73e6  2.38e7
    measured  47.76   57.91   72.78   76.31   78.90   82.24   95.16
    generic   66.75   72.98   85.38   86.44   87.13   88.42   96.64
    ratio    0.7155  0.7935  0.8524  0.8828  0.9055  0.9301  0.9846

The shortfall closes as a power law -- least squares on log(1 - ratio) against log n:

    1 - mix_ratio  =  2.573 * n^(-1.406)          R^2 = 0.964  (in log space)

which extrapolates to

    n          64       256      2048      8192     50000
    1-ratio  7.4e-3   1.1e-3   5.7e-5    8.1e-6   6.4e-7

So the exact-angle discount itself dies away quickly: by n ~ 100 it is under a percent, and by
n = 2048 the law puts it at 6e-5. **Direct sampling at large n disagrees with that extrapolation,
and the next section says why -- the residual there is a different effect and about 0.4%, not
6e-5.** Either way the conclusion for a figure is the same and it is the point of this module:
this ratio is a sub-percent correction at the widths that matter, while replacing the placeholder
`11 T per rotation` with `ross_selinger_t(delta_rot / R)` -- 130-170 T at n = 2048..50000 -- is a
factor of 12-15. Get the tolerance right first; the mix is a refinement on top.

TWO REGIMES, AND THEY ARE NOT THE SAME PHYSICS
----------------------------------------------
`mix_ratio` is defined as one thing -- true mean T per rotation divided by the model
`ross_selinger_t(eps)` -- but what makes it depart from 1 differs by regime, and the two must not
be conflated in a caption:

  * **small n (the census, n <= 32).** The departure is a real EXACT-ANGLE DISCOUNT: a
    fixed-size population of dyadic U2/QFT twiddles, cheap or free in Clifford+T, is a large
    share of a small circuit. Ratio 0.72 at n = 4.
  * **large n (sampled, n >= 2048).** Direct measurement gives ratio ~= 0.995-0.997, not the
    ~0.99994 the small-n law extrapolates to. The difference is NOT exact angles -- it is the
    residual of the `c_syn` least-squares fit itself: the true mean over generic angles sits
    about 0.4% below `3.075*log2(1/eps) + 0.86` at these tolerances. Measured 2026-09-23,
    M = 2000 samples per cell:

        k=2  n=2048   mean 130.53 +- 0.04   model 130.90   ratio 0.9971
        k=2  n=4096   mean 138.33 +- 0.05   model 138.88   ratio 0.9961
        k=2  n=8192   mean 146.23 +- 0.05   model 146.84   ratio 0.9958
        k=2  n=16384  mean 154.08 +- 0.05   model 154.81   ratio 0.9953

    So the small-n law OVERSTATES the ratio at large n, and the sampled value is preferred
    wherever one exists -- which is what `mix_ratio` does. The practical size of the correction
    either way is under half a percent; what would have been wrong by an order of magnitude is
    the placeholder, not this.

ON SAMPLE SIZE
--------------
The default in `measure.py` is 100,000 because that was the request. It is worth recording what
it buys: the spread across angles is about +-2% of the mean, so the standard error is
2%/sqrt(M) -- about 0.13 T at M = 240, 0.05 T at M = 2,000, 0.006 T at M = 100,000. All three
pin the mean far tighter than the 0.4% gap between the measurement and the model. A large sample
characterises the TAIL (the worst-case angle), not the mean.

TWO HONEST CAVEATS
------------------
* The census counts PHASE_GRID cells of the frozen CTBP construction; `QECGatesCost.rotation`
  counts Qualtran's rotation-classified bloqs. They are close but not the same object (28,203 vs
  22,547 at k=2, n=4 -- a factor 1.25). Transferring a ratio measured on one to the other assumes
  the two populations have the same exact/generic composition. That assumption is doing almost no
  work where the figures live, because the ratio is 1 there; it would matter at small n.
* Every row of the SMALL-n census is k = 2, so the exact-angle law `1 - ratio = A n^(-B)` is
  fitted at one arity only. That extrapolation across k has now been TESTED rather than assumed:
  `measure.py` sampled k = 2..7 across the whole ladder (36 cells, 2000 angles each, 2026-09-23)
  and the ratio came out k-independent to within the sampling error --

      n         2048    4096    8192   16384   32768   50000
      k=2     0.9971  0.9961  0.9958  0.9953  0.9947  0.9942
      k=7     0.9976  0.9966  0.9953  0.9951  0.9943  0.9943

  -- which is what one expects of a quantity that is a fit residual rather than a structural
  property. The measured mean T per rotation over the same grid runs 129.3 to 166.6, decreasing
  slightly with k because a larger arity has fewer rotations and therefore a looser eps.
  `mix_ratio` still reports its `source`, so a fitted value is never mistaken for a measured one.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .budget import ross_selinger_t

__all__ = ["CENSUS_DIR", "MixRatio", "census_rows", "fit_gap_law", "mix_ratio",
           "interpolate_sampled", "GAP_LAW"]

CENSUS_DIR = (Path(__file__).resolve().parent.parent / "artifacts" / "rotation_census")

#: `1 - ratio = A * n^(-B)`, least squares on the k = 2 census above. Recomputed by
#: `fit_gap_law()` whenever rows are added; this literal is the value at the time of writing and
#: is what `mix_ratio` falls back to when the census directory is absent.
GAP_LAW: Tuple[float, float] = (2.5729, 1.4057)


@dataclass(frozen=True)
class MixRatio:
    """A ratio and, just as importantly, where it came from."""
    k: int
    n: int
    ratio: float
    source: str            # "sampled" | "interpolated" | "measured" | "fitted"
    detail: str = ""

    @property
    def is_measured(self) -> bool:
        return self.source in ("measured", "sampled", "interpolated")


def census_rows(census_dir: Path = CENSUS_DIR) -> List[Dict[str, Any]]:
    """Measured (k, n, R, eps, mean T per rotation, ratio) rows from the rotation census."""
    results = census_dir / "results"
    if not results.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for d in sorted(results.glob("k*_n*")):
        p = d / "summary.json"
        if not p.is_file():
            continue
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
            R, T = int(s["R"]), float(s["T_rot"])
            eps = float(Fraction(s["eps"])) if isinstance(s["eps"], str) else float(s["eps"])
            if R <= 0 or eps <= 0:
                continue
            mean = T / R
            rows.append({"k": int(s["k"]), "n": int(s["n"]), "R": R, "eps": eps,
                         "mean_T_per_rotation": mean, "generic": ross_selinger_t(eps),
                         "ratio": mean / ross_selinger_t(eps), "mode": s.get("mode"),
                         "delta": s.get("delta")})
        except Exception:
            continue
    return sorted(rows, key=lambda r: (r["k"], r["n"]))


def fit_gap_law(rows: Optional[List[Dict[str, Any]]] = None) -> Tuple[float, float]:
    """`(A, B)` in `1 - ratio = A * n^(-B)`, fitted in log space over `rows`."""
    rows = rows if rows is not None else census_rows()
    pts = [(r["n"], 1.0 - r["ratio"]) for r in rows if 0.0 < r["ratio"] < 1.0]
    if len(pts) < 3:
        return GAP_LAW
    xs = [math.log(n) for n, _ in pts]
    ys = [math.log(g) for _, g in pts]
    nn = len(xs)
    mx, my = sum(xs) / nn, sum(ys) / nn
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return GAP_LAW
    slope = sxy / sxx
    return math.exp(my - slope * mx), -slope


def interpolate_sampled(k: int, n: int,
                        sampled: Dict[Tuple[int, int], float]) -> Optional[Tuple[float, str]]:
    """Piecewise-linear interpolation of the sampled ratio in `log n`, at fixed `k`.

    The measured cells sit on a log ladder (2048, 4096, ..., 50000) which does not coincide with
    the plotting grid, so without this almost every plotted point would fall back to the fitted
    law even though it is bracketed by two measurements. Interpolating in `log n` is the natural
    choice: the ratio tracks `log2(1/eps)`, and `eps = delta_rot / R` with `R` a power of `n`, so
    the quantity really is close to linear in `log n` (measured k=2: 0.9971, 0.9961, 0.9958,
    0.9953, 0.9947, 0.9942 across the ladder -- smooth and monotone).

    Returns `None` outside the measured range rather than extrapolating: past the ends the fitted
    law is the honest fallback, and a linear extrapolation of a ratio can leave `(0, 1]`.
    """
    pts = sorted((nn, v) for (kk, nn), v in sampled.items() if kk == int(k))
    if len(pts) < 2:
        return None
    n = float(n)
    if n < pts[0][0] or n > pts[-1][0]:
        return None
    for (n0, v0), (n1, v1) in zip(pts, pts[1:]):
        if n0 <= n <= n1:
            if n1 == n0:
                return v0, f"sampled at n={n0:g}"
            w = (math.log(n) - math.log(n0)) / (math.log(n1) - math.log(n0))
            return v0 + w * (v1 - v0), f"interpolated in log n between n={n0:g} and n={n1:g}"
    return None


def mix_ratio(k: int, n: int, *, rows: Optional[List[Dict[str, Any]]] = None,
              sampled: Optional[Dict[Tuple[int, int], float]] = None) -> MixRatio:
    """The exact-angle discount at `(k, n)`, preferring measurement over extrapolation.

    Order of preference: a sampled value for this exact cell (`measure.py`); an interpolation
    between two sampled cells that bracket it; a census row for this exact cell; the fitted power
    law. Every result is bounded into `(0, 1]` -- an extrapolation must never claim a rotation is
    free, nor cheaper than exact.
    """
    if sampled and (int(k), int(n)) in sampled:
        v = float(sampled[(int(k), int(n))])
        return MixRatio(k, n, min(max(v, 1e-6), 1.0), "sampled",
                        "gridsynth over a sample of this cell's own angles")
    if sampled:
        got = interpolate_sampled(k, n, sampled)
        if got is not None:
            v, how = got
            return MixRatio(k, n, min(max(v, 1e-6), 1.0), "interpolated", how)
    rows = rows if rows is not None else census_rows()
    for r in rows:
        if r["k"] == int(k) and r["n"] == int(n):
            return MixRatio(k, n, min(max(r["ratio"], 1e-6), 1.0), "measured",
                            f"rotation census, mode={r.get('mode')}, delta={r.get('delta')}")
    A, B = fit_gap_law(rows)
    gap = A * float(n) ** (-B)
    ratio = min(max(1.0 - gap, 1e-6), 1.0)
    return MixRatio(k, n, ratio, "fitted",
                    f"1 - ratio = {A:.4g} * n^(-{B:.4g}) from the k=2 census; "
                    f"gap = {gap:.3g} at n = {n}")
