"""The reference implementation's precision schedule: eta <-> m, and what it certifies.

W7 of PLAN_QUALTRAN_ON_FORSHOR_V2. Every formula here is transcribed from the 2026-09-18 drop
and cited at its definition. This closes the blocker recorded in `emit2.py`'s module docstring:
"no established eta->m sizing rule anywhere in the tree (Lean or Python)". There is one now.

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

WHY `m` MATTERS AND WHY A HUGE `m` IS HARMLESS
----------------------------------------------
`m` is the only precision knob `forshor_emit shor <k> <a> <N> <m>` takes, and
`referenceProgramAt_success` is a theorem **for every m** -- so an emitted circuit at any `m`
carries a proved single-run bound. `m` reaches the circuit only through
`algorithm1ExtraBitsNat`, i.e. **logarithmically**: the m needed for a 99 %-of-ideal bound at
2048 bits is ~2.8e44 and costs ~294 extra work-register bits, not 1e44 of anything.

TWO TRAPS, both measured
------------------------
* **m = 0 certifies nothing.** The README's own example (`forshor_emit 2 2 15 0`) clamps to
  `p = 0` for every modulus tried. The circuit is still the proved term; the *bound* is vacuous.
* **`referenceChosenPrecision` is a very weak choice**, as `Reference2048Headline.lean` says of
  itself: it guarantees only a *positive* probability. At 2048 bits it gives p ~ 2.5e-56 and
  ~1.9e56 trials. Use `m_for_fraction_of_ideal(N, 0.99)` instead; that is what the 2048 headline
  file exists to supply.

PRECISION OF THIS MODULE
------------------------
`mpmath` is used when available (80 dps) and plain floats otherwise. The float path is fine for
`certified_p` and `trial_count`; the returned `m` values run to ~1e44 and are therefore only
accurate to float64's ~16 significant digits on that path. That does not matter downstream --
`extra_bits` differs by at most 1 across that uncertainty -- but `exact_arithmetic()` says which
path is in force, and the tests assert the headline against the mpmath path.
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
    """True when mpmath is backing the closed forms (80 dps), False on the float fallback."""
    return _EXACT


#: `Shor.referenceK` -- a concrete numeral traced from Cpe = 512 and Cstep2 = 2pi + 2pi^2 in the
#: Algorithm-1 error bounds, explicitly NOT a `Classical.choose` witness.
REFERENCE_K = 2048

#: Refinement steps allowed around the closed form. Bounded deliberately -- see
#: `m_for_fraction_of_ideal`.
_REFINE_STEPS = 8

#: `Reference2048Headline.lean`'s explicit choice: eta = 2^-150 exactly.
M_2048 = 2 ** 150 - 3


def kappa():
    """`kappa = 4 * exp(-2) / pi^2` (Framework/Math/ShorDefinition.lean:297)."""
    return 4 * _exp(mpf(-2)) / _pi ** 2


# ----------------------------------------------------------------- eta <-> m
def eta_of_m(m: int) -> Fraction:
    """`referencePrecision m = 1 / (m + 3)`, exactly.

    The `+ 3` is deliberate in Lean: it makes `0 < eta < 1/2` hold at every level including
    `m = 0`, while `eta -> 0` as `m -> infinity`.
    """
    if m < 0:
        raise ValueError(f"precision level must be a natural number, got {m}")
    return Fraction(1, m + 3)


def m_of_eta(eta) -> int:
    """The least `m` with `referencePrecision m <= eta`, i.e. `ceil(1/eta) - 3`, floored at 0.

    Inverse of `eta_of_m` in the direction that matters: it returns a level at least as precise
    as asked for, never a coarser one. `eta_of_m(m_of_eta(eta)) <= eta` is a test.
    """
    f = Fraction(eta).limit_denominator(10 ** 15)
    if f <= 0:
        raise ValueError(f"eta must be positive, got {eta}")
    m = -(-f.denominator // f.numerator) - 3          # ceil(1/eta) - 3
    return max(m, 0)


def extra_bits(m: int) -> int:
    """`algorithm1ExtraBitsNat m = Nat.clog 2 ((m+7)^2) - 2` -- the work-register widening the
    precision level forces, computed without `Real.logb`. Grows like `2*log2(m)`, which is why
    an astronomically large `m` is affordable."""
    if m < 0:
        raise ValueError(f"precision level must be a natural number, got {m}")
    n = (m + 7) ** 2
    clog2 = 0 if n <= 1 else (n - 1).bit_length()     # Nat.clog 2 n = ceil(log2 n)
    return clog2 - 2


# ----------------------------------------------------------------- the certified bound
def nat_log2(n: int) -> int:
    """Lean's `Nat.log2 = Nat.log 2 = floor(log2 n)` for n >= 1, and 0 at n = 0."""
    return n.bit_length() - 1 if n >= 1 else 0


def tbits(N: int) -> int:
    """The exponent/output register width: `regSize (Reg.interval 0 (Nat.log2 (2*N^2)))`."""
    return nat_log2(2 * N * N)


def ideal_probability(N: int):
    """`kappa / (Nat.log2 N)^4` -- the exact-arithmetic baseline the approximation is measured
    against."""
    L = nat_log2(N)
    if L <= 0:
        raise ValueError(f"need N >= 2, got {N}")
    return kappa() / mpf(L) ** 4


def approximation_error(m: int, N: int):
    """`referenceApproximationErrorAt m N = 2 * tbits * sqrt(2 * (K * referencePrecision m))`."""
    return 2 * mpf(tbits(N)) * _sqrt(2 * (mpf(REFERENCE_K) * (mpf(1) / (mpf(m) + 3))))


def certified_p(m: int, N: int):
    """`referenceSuccessProbabilityAt m N` -- the PROVED single-run lower bound at level `m`,
    clamped to [0, 1]. `referenceProgramAt_success` is a theorem for every `m`, so this is a
    statement about the circuit `forshor_emit shor k a N m` actually emits."""
    raw = ideal_probability(N) - approximation_error(m, N)
    return max(mpf(0), min(mpf(1), raw))


def m_for_fraction_of_ideal(N: int, fraction: float = 0.99) -> int:
    """Least `m` whose certified bound is at least `fraction` of the ideal baseline.

    `fraction = 0` reproduces `referenceChosenPrecision` (strict positivity -- a very weak
    choice); `fraction = 0.99` reproduces `Reference2048Headline.lean`'s criterion, and is what
    a resource claim should quote.

    Solved in closed form, then tightened by evaluation so the result is the true least `m`
    rather than whatever the algebra rounded to.
    """
    if not 0 <= fraction < 1:
        raise ValueError(f"fraction must be in [0, 1), got {fraction}")
    # `fraction` is taken EXACTLY: `Fraction(0.99)` as a float is 0.9899999999999999911182...,
    # not 99/100, and at m ~ 1e44 that 17th-digit difference moves the boundary level by a
    # whole step. `limit_denominator` recovers the intended rational.
    frac = Fraction(fraction).limit_denominator(10 ** 9)
    budget = (1 - mpf(frac.numerator) / mpf(frac.denominator)) * ideal_probability(N)
    # error(m) <= budget  <=>  2*tbits*sqrt(4096/(m+3)) <= budget  <=>  m+3 >= 4096*(2*tbits/budget)^2
    need = 4096 * (2 * mpf(tbits(N)) / budget) ** 2
    floor = int(need)
    m = max(floor + (0 if mpf(floor) == need else 1) - 3, 0)

    # The closed form is exact, so refinement only ever corrects a rounding step. It is BOUNDED
    # on purpose: `m` runs to ~1e44 here, and an unbounded `m -= 1` walk over an overshoot of
    # even one part in 1e16 would never finish. It hung here before this was bounded.
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

    This is the level the SUBMITTED program uses, and it is deliberately weak: at 2048 bits it
    yields p ~ 2.5e-56. Prefer `m_for_fraction_of_ideal`.
    """
    return m_for_fraction_of_ideal(N, 0.0)


def trial_count(p) -> Optional[int]:
    """`referenceTrialCount` -- least `k` with `0.99 <= 1 - (1-p)^k`.

    `None` for a vacuous bound (`p <= 0`), where no number of trials helps.

    Compared entirely in LOG SPACE, which is not a micro-optimisation but a correctness
    requirement. The condition is

        1 - (1-p)^k >= 0.99   <=>   (1-p)^k <= 0.01   <=>   k * log1p(-p) <= log(0.01)

    and since `log1p(-p) < 0` that is `k >= log(0.01) / log1p(-p)`.

    Evaluating `(1-p)**k` directly instead is wrong for small `p`: at p ~ 2.5e-56 (which is what
    `chosen_precision` yields at 2048 bits) float64 rounds `1-p` to exactly 1.0, so `(1-p)**k`
    is 1.0 for every `k` and a refinement loop written that way never terminates. It hung here
    before this was fixed.
    """
    p = float(p)
    if p <= 0:
        return None
    if p >= 1:
        return 1
    lg = math.log1p(-p)
    target = math.log(0.01)
    k = max(1, math.ceil(target / lg))
    # BOUNDED, for the same reason as `m_for_fraction_of_ideal`: at p ~ 2.5e-56 this `k` is
    # ~1.8e56, and an unbounded `k -= 1` walk would never finish. The closed form is exact, so
    # only a rounding step can ever need correcting.
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
    """Everything the leaderboard quantity needs except the gate count, which comes from the
    emitted document's own `gate_count` field.

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
