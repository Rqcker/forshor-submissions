"""The precision schedule of the reference implementation: eta <-> m, and what it certifies.

W7 of PLAN_QUALTRAN_ON_FORSHOR_V2. Each formula here is copied from the 2026-09-18 drop, with a
reference to its definition. This removes the blocker in the module docstring of `emit2.py`:
"no established eta->m sizing rule anywhere in the tree (Lean or Python)". Now there is one.

    Implementation/Reference/ReferencePrecision.lean
        referencePrecision m      = 1 / (m + 3)                      -- noncomputable, in ℝ
        algorithm1ExtraBitsNat m  = Nat.clog 2 ((m+7)^2) - 2         -- computable, in ℕ
        algorithm1ExtraBits_referencePrecision : the two agree

    Implementation/Reference/ReferenceShorImplementation.lean
        referenceK                = 2048                             -- a NUMERAL, not a
                                                                     -- Classical.choose witness
        referenceApproximationErrorAt m N = 2 * tbits * sqrt(2 * (K * referencePrecision m))
        referenceRawSuccessProbabilityAt  = kappa / (Nat.log2 N)^4 - that
        referenceSuccessProbabilityAt     = max 0 (min 1 raw)
        referenceChosenPrecision N        = least m with 0 < that     (Nat.find)
        referenceTrialCount N             = least k with 0.99 <= 1-(1-p)^k

    Framework/Math/ShorDefinition.lean
        kappa = 4 * exp(-2) / pi^2

WHY `m` IS IMPORTANT AND WHY A VERY LARGE `m` CAUSES NO PROBLEM
---------------------------------------------------------------
`m` is the only precision parameter of `forshor_emit shor <k> <a> <N> <m>`, and
`referenceProgramAt_success` is a theorem **for every m**. Thus an emitted circuit at each `m`
has a proved single-run bound. `m` changes the circuit only through `algorithm1ExtraBitsNat`,
that is, **logarithmically**. For a bound of 99 % of the ideal at 2048 bits, the necessary m is
about 2.8e44. This m costs about 294 extra bits of the work register, not 1e44 of a resource.

TWO TRAPS (we measured the two)
-------------------------------
* **m = 0 certifies nothing.** The example in the README (`forshor_emit 2 2 15 0`) clamps to
  `p = 0` for each modulus that we tried. The circuit is still the proved term, but the *bound* is vacuous.
* **`referenceChosenPrecision` is a very weak choice.** `Reference2048Headline.lean` says this about
  itself: it guarantees only a *positive* probability. At 2048 bits it gives p ~ 2.5e-56 and
  ~1.9e56 trials. Use `m_for_fraction_of_ideal(N, 0.99)` instead. The 2048 headline file exists
  to give that value.

PRECISION OF THIS MODULE
------------------------
The module uses `mpmath` if it is available (80 dps), and plain floats if not. The float path is
sufficient for `certified_p` and `trial_count`. But the returned `m` values go to about 1e44, thus
on that path they are accurate only to the about 16 significant digits of float64. This has no
effect on the downstream results, because `extra_bits` changes by 1 or less across that
uncertainty. `exact_arithmetic()` tells which path is active, and the tests check the headline
against the mpmath path.
"""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Optional

try:                                        # pragma: no cover - environment dependent
    from mpmath import mp, mpf, exp as _exp, pi as _pi, sqrt as _sqrt, log as _log
    mp.dps = 80
    _EXACT = True
except ImportError:                         # pragma: no cover
    _EXACT = False
    mpf = float

    def _exp(x):
        return math.exp(x)

    def _sqrt(x):
        return math.sqrt(x)

    def _log(x, b=None):
        return math.log(x) if b is None else math.log(x, b)

    _pi = math.pi


def exact_arithmetic() -> bool:
    """Return True if mpmath computes the closed forms (80 dps), and False on the float fallback."""
    return _EXACT


#: `Shor.referenceK` -- a concrete numeral that comes from Cpe = 512 and Cstep2 = 2pi + 2pi^2 in the
#: error bounds of Algorithm 1. It is explicitly NOT a `Classical.choose` witness.
REFERENCE_K = 2048

#: The number of refinement steps permitted around the closed form. This limit is on purpose.
#: Refer to `m_for_fraction_of_ideal`.
_REFINE_STEPS = 8

#: The explicit choice of `Reference2048Headline.lean`: eta = 2^-150 exactly.
M_2048 = 2 ** 150 - 3


def kappa():
    """`kappa = 4 * exp(-2) / pi^2` (Framework/Math/ShorDefinition.lean:297)."""
    return 4 * _exp(mpf(-2)) / _pi ** 2


# ----------------------------------------------------------------- eta <-> m
def eta_of_m(m: int) -> Fraction:
    """`referencePrecision m = 1 / (m + 3)`, exactly.

    The `+ 3` is on purpose in Lean. It makes `0 < eta < 1/2` true at each level, also at
    `m = 0`, and `eta -> 0` as `m -> infinity`.
    """
    if m < 0:
        raise ValueError(f"precision level must be a natural number, got {m}")
    return Fraction(1, m + 3)


def m_of_eta(eta) -> int:
    """Return the least `m` with `referencePrecision m <= eta`, that is, `ceil(1/eta) - 3`, floored at 0.

    This is the inverse of `eta_of_m` in the important direction. It returns a level with the
    requested precision or better, never a coarser level. A test checks `eta_of_m(m_of_eta(eta)) <= eta`.
    """
    f = Fraction(eta).limit_denominator(10 ** 15)
    if f <= 0:
        raise ValueError(f"eta must be positive, got {eta}")
    m = -(-f.denominator // f.numerator) - 3          # ceil(1/eta) - 3
    return max(m, 0)


def extra_bits(m: int) -> int:
    """`algorithm1ExtraBitsNat m = Nat.clog 2 ((m+7)^2) - 2` -- the number of extra work-register bits
    that the precision level makes necessary, computed without `Real.logb`. It increases as `2*log2(m)`.
    Thus an extremely large `m` has a low cost."""
    if m < 0:
        raise ValueError(f"precision level must be a natural number, got {m}")
    n = (m + 7) ** 2
    clog2 = 0 if n <= 1 else (n - 1).bit_length()     # Nat.clog 2 n = ceil(log2 n)
    return clog2 - 2


# ----------------------------------------------------------------- the certified bound
def nat_log2(n: int) -> int:
    """The Lean `Nat.log2 = Nat.log 2 = floor(log2 n)` for n >= 1, and 0 at n = 0."""
    return n.bit_length() - 1 if n >= 1 else 0


def tbits(N: int) -> int:
    """Return the width of the exponent/output register: `regSize (Reg.interval 0 (Nat.log2 (2*N^2)))`."""
    return nat_log2(2 * N * N)


def ideal_probability(N: int):
    """`kappa / (Nat.log2 N)^4` -- the exact-arithmetic baseline. We measure the approximation
    against it."""
    L = nat_log2(N)
    if L <= 0:
        raise ValueError(f"need N >= 2, got {N}")
    return kappa() / mpf(L) ** 4


def approximation_error(m: int, N: int):
    """`referenceApproximationErrorAt m N = 2 * tbits * sqrt(2 * (K * referencePrecision m))`."""
    return 2 * mpf(tbits(N)) * _sqrt(2 * (mpf(REFERENCE_K) * (mpf(1) / (mpf(m) + 3))))


def certified_p(m: int, N: int):
    """`referenceSuccessProbabilityAt m N` -- the PROVED single-run lower bound at level `m`,
    clamped to [0, 1]. `referenceProgramAt_success` is a theorem for every `m`. Thus this value is
    a statement about the circuit that `forshor_emit shor k a N m` really emits."""
    raw = ideal_probability(N) - approximation_error(m, N)
    return max(mpf(0), min(mpf(1), raw))


def m_for_fraction_of_ideal(N: int, fraction: float = 0.99) -> int:
    """Return the least `m` whose certified bound is at least `fraction` of the ideal baseline.

    `fraction = 0` gives `referenceChosenPrecision` again (strict positivity -- a very weak
    choice). `fraction = 0.99` gives the criterion of `Reference2048Headline.lean`. Use this
    value in a resource claim.

    The function first solves the closed form. Then it evaluates the bound near the result to make
    the result the true least `m`, not the value that the algebra rounded to.
    """
    if not 0 <= fraction < 1:
        raise ValueError(f"fraction must be in [0, 1), got {fraction}")
    # The function uses `fraction` EXACTLY. `Fraction(0.99)` of a float is 0.9899999999999999911182...,
    # not 99/100. At m ~ 1e44, that difference in the 17th digit moves the boundary level by a
    # full step. `limit_denominator` gives back the intended rational.
    frac = Fraction(fraction).limit_denominator(10 ** 9)
    budget = (1 - mpf(frac.numerator) / mpf(frac.denominator)) * ideal_probability(N)
    # error(m) <= budget  <=>  2*tbits*sqrt(4096/(m+3)) <= budget  <=>  m+3 >= 4096*(2*tbits/budget)^2
    need = 4096 * (2 * mpf(tbits(N)) / budget) ** 2
    floor = int(need)
    m = max(floor + (0 if mpf(floor) == need else 1) - 3, 0)

    # The closed form is exact. Thus the refinement corrects only a rounding step. It has a LIMIT
    # on purpose. Here `m` goes to about 1e44, and a `m -= 1` walk without a limit does not finish,
    # even for an overshoot of one part in 1e16. The code stopped here before we added the limit.
    for _ in range(_REFINE_STEPS):
        if m > 0 and approximation_error(m - 1, N) <= budget:
            m -= 1
        else:
            break
    for _ in range(_REFINE_STEPS):
        if approximation_error(m, N) > budget:
            m += 1
        else:
            break
    if approximation_error(m, N) > budget:
        raise ArithmeticError(
            f"m did not converge within {_REFINE_STEPS} refinement steps at N={N.bit_length()} "
            f"bits, fraction={fraction}; the closed form is off by more than rounding "
            f"(exact_arithmetic={exact_arithmetic()})")
    return m


def chosen_precision(N: int) -> int:
    """`referenceChosenPrecision N` -- the least `m` with a strictly positive bound.

    The SUBMITTED program uses this level. It is weak on purpose: at 2048 bits it
    gives p ~ 2.5e-56. Use `m_for_fraction_of_ideal` if possible.
    """
    return m_for_fraction_of_ideal(N, 0.0)


def trial_count(p) -> Optional[int]:
    """`referenceTrialCount` -- the least `k` with `0.99 <= 1 - (1-p)^k`.

    Return `None` for a vacuous bound (`p <= 0`). For that bound, no number of trials is sufficient.

    The function does all comparisons in LOG SPACE. This is not a micro-optimisation. It is
    necessary for a correct result. The condition is

        1 - (1-p)^k >= 0.99   <=>   (1-p)^k <= 0.01   <=>   k * log1p(-p) <= log(0.01)

    and because `log1p(-p) < 0`, that is `k >= log(0.01) / log1p(-p)`.

    A direct evaluation of `(1-p)**k` is incorrect for small `p`. At p ~ 2.5e-56 (the value that
    `chosen_precision` gives at 2048 bits), float64 rounds `1-p` to exactly 1.0. Thus `(1-p)**k`
    is 1.0 for each `k`, and a refinement loop of that type does not stop. The code stopped here
    before we did this correction.
    """
    p = float(p)
    if p <= 0:
        return None
    if p >= 1:
        return 1
    lg = math.log1p(-p)
    target = math.log(0.01)
    k = max(1, math.ceil(target / lg))
    # This has a LIMIT, for the same reason as `m_for_fraction_of_ideal`. At p ~ 2.5e-56 this `k` is
    # about 1.8e56, and a `k -= 1` walk without a limit does not finish. The closed form is exact.
    # Thus only a rounding step can need a correction.
    for _ in range(_REFINE_STEPS):
        if k > 1 and (k - 1) * lg <= target:
            k -= 1
        else:
            break
    for _ in range(_REFINE_STEPS):
        if k * lg > target:
            k += 1
        else:
            break
    return k


def certificate(N: int, m: Optional[int] = None, fraction: float = 0.99) -> dict:
    """Return all values that the leaderboard quantity needs, except the gate count. The gate count
    comes from the `gate_count` field of the emitted document.

    `score = trials * gate_count` (Framework/Submission.lean: the score is
    `trialCount N * (program inst).frameworkGateCount`).
    """
    if m is None:
        m = m_for_fraction_of_ideal(N, fraction)
    p = certified_p(m, N)
    ideal = ideal_probability(N)
    return {
        "N_bits": N.bit_length(),
        "m": m,
        "eta": f"1/{m + 3}",
        "extra_work_bits": extra_bits(m),
        "ideal_probability": float(ideal),
        "certified_p": float(p),
        "fraction_of_ideal": float(p / ideal) if ideal else 0.0,
        "trials": trial_count(p),
        "exact_arithmetic": exact_arithmetic(),
    }
