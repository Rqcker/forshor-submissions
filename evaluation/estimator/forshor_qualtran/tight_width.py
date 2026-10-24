"""The exact reachable-value ("tight") register width, built again from the emitted schedule.

WHY THIS FILE EXISTS
--------------------
ForShor has two width policies, and they are not the same object:

  * **sym** -- `RecursivePhaseWorkspace.nextWidth`, the `updateWidthState` scan, which gives too
    large a value. The fidelity theorems use this width.
  * **tight** -- the least width at which each *reachable* intermediate value fits. The circuit
    really uses this width, and Qualtran counts this width.

ForShor records that the symbolic policy is loose ("54 wasted qubits at k=3"). It also records that
a smaller width "would put the emission outside the proved fidelity theorem". The two widths are
important, and we report the gap between them. If you give only `sym`, the register seems larger
than it is. If you give only `tight`, you claim a width that no theorem covers.

The v1 emitter emitted the two. The 2026-09-18 drop emits only `sym`, and it says this:

    "Only the symbolic policy is emitted; the older tooling's exact reachable-value ("tight")
     width scan is not carried over (nothing on this repository's data path uses it)."
    -- FastMultiplication/Emit/Symbolic/Width.lean

Thus this module builds it again. **This is not a mirror of a Lean quantity that is not emitted.**
The input is the schedule that the drop *does* emit (`bundle.schedule.ops`). This module adds
interval arithmetic over that schedule. That arithmetic is our own computation, and it always was:
`reachableFits` was in our `Emit/LowGateIR.lean`, not in the proof of the collaborator. The limb
split that it needs (`phaseLimbWidthOfWidth`, `phaseSplitLogicalWidth`) is still in the drop, and
it is floor division.

WHY INTERVAL ARITHMETIC IS EXACT HERE (the argument of LowGateIR.lean)
----------------------------------------------------------------------
Register `i` holds a coefficient row `c`. The row means `sum_j c_j * v_j`, where `v_j` is limb `j`
of the operand. The limbs change INDEPENDENTLY over their own ranges. Thus

    max over the operand box of  sum_j c_j v_j  =  sum_j max (c_j * v_j)

is *attained*. It is not only a bound. Thus a `True` here proves that the width is sufficient,
and a `False` means that the circuit really wraps. For this reason, the answer is "tight" and
not "an upper bound".

FIDELITY
--------
Each definition below is a copy of a named Lean definition, with a reference at its location.
`tests/test_tight_width.py` validates the result against the `W_tight` tables of the v1 emitter
for k = 2..7. These tables are ground truth: the Lean code that this file copies produced them.
The module gives exactly the same tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: A coefficient row: `Register k = Fin k -> Int` (Registers.lean:19).
Register = Tuple[int, ...]
#: `State k = Fin k -> Register k` (Registers.lean:22).
State = Tuple[Register, ...]
#: `(lo, hi)`, inclusive.
Range = Tuple[int, int]


class TightWidthError(ValueError):
    """The schedule cannot run, or the search found no width within the fuel bound."""


# --------------------------------------------------------------------- the op semantics
def start_state(k: int) -> State:
    """`State.start_state`: register `i` holds `x_i` (Registers.lean:61)."""
    return tuple(tuple(1 if j == i else 0 for j in range(k)) for i in range(k))


def _set_reg(sigma: State, i: int, r: Register) -> State:
    return tuple(r if idx == i else row for idx, row in enumerate(sigma))


def apply_op(sigma: State, op: Dict[str, Any]) -> Optional[State]:
    """`applyOp?` (Language.lean:23). Return ``None`` exactly where Lean returns ``none``.

    The only partial case is `shiftR`. It fails if a coefficient is not divisible by
    `2^n` (the Lean `Register.shiftR?`). Nothing here can round without a message.
    """
    kind = op["op"]
    if kind == "phaseProduct":
        return sigma                                        # the coefficients do not change
    if kind == "negate":
        i = int(op["i"])
        return _set_reg(sigma, i, tuple(-c for c in sigma[i]))
    if kind == "shiftL":
        i, n = int(op["i"]), int(op["n"])
        return _set_reg(sigma, i, tuple(c * (1 << n) for c in sigma[i]))
    if kind == "shiftR":
        i, n = int(op["i"]), int(op["n"])
        m = 1 << n
        row = sigma[i]
        # Lean tests `r j % m = 0` over ℤ, where `%` is Euclidean and thus non-negative. For a
        # power of two, this agrees with the Python `%` on sign. Thus `c % m` is the same predicate.
        if any(c % m != 0 for c in row):
            return None
        return _set_reg(sigma, i, tuple(c // m for c in row))
    if kind == "addScaled":
        dst, src = int(op["dst"]), int(op["src"])
        sgn = -1 if op.get("negSrc") else 1
        sh = 1 << int(op.get("shift", 0))
        return _set_reg(sigma, dst,
                        tuple(d + sgn * s * sh for d, s in zip(sigma[dst], sigma[src])))
    raise TightWidthError(f"unknown schedule op {kind!r}; the reconstruction must not guess")


# --------------------------------------------------------------------- the ranges
def limb_range(k: int, j: int, w: int) -> Range:
    """`Emit.limbRange` (LowGateIR.lean:542): the lower limbs are unsigned, the top limb is signed."""
    if j + 1 == k:
        return (-(2 ** (w - 1)), 2 ** (w - 1) - 1)
    return (0, 2 ** w - 1)


def limb_widths(k: int, w: int) -> List[int]:
    """`phaseSplitLogicalWidth w (phaseLimbWidthOfWidth w k) k` (Layout.lean:89, 110).

    This is floor division, on purpose. The lower `k-1` limbs get `w // k`, and the top limb gets
    the remainder. w=5, k=4 gives 1, 1, 1, 2.
    """
    base = w // k
    return [base] * (k - 1) + [w - (k - 1) * base]


def limb_ranges(k: int, w: int) -> List[Range]:
    """`limbRangesOf` (ForshorEmit/Width.lean:42)."""
    widths = limb_widths(k, w)
    return [limb_range(k, j, widths[j]) for j in range(k)]


def row_range(r: Register, rs: Sequence[Range]) -> Range:
    """`rowRange` (LowGateIR.lean:546). It is exact because of the independence argument in the header."""
    lo = hi = 0
    for coeff, (rlo, rhi) in zip(r, rs):
        a, b = coeff * rlo, coeff * rhi
        lo += min(a, b)
        hi += max(a, b)
    return (lo, hi)


def fits_signed(w: int, lo: int, hi: int) -> bool:
    """`fitsSignedRange` (LowGateIR.lean:558)."""
    return -(2 ** (w - 1)) <= lo and hi <= 2 ** (w - 1) - 1


def state_fits(rs: Sequence[Range], w: int, sigma: State) -> bool:
    """`stateFits` (LowGateIR.lean:562)."""
    return all(fits_signed(w, *row_range(row, rs)) for row in sigma)


def reachable_fits(rs: Sequence[Range], w: int, ops: Sequence[Dict[str, Any]],
                   sigma: Optional[State] = None) -> bool:
    """`reachableFits` (LowGateIR.lean:568): EACH intermediate state must fit `w`.

    Note the order that Lean uses. Lean checks the state *before* each op and one more time at the
    end. Thus the check includes the start state and the final state. A check only after each op
    does not find a start state that already overflows.
    """
    if sigma is None:
        sigma = start_state(len(rs))
    for op in ops:
        if not state_fits(rs, w, sigma):
            return False
        nxt = apply_op(sigma, op)
        if nxt is None:
            return False
        sigma = nxt
    return state_fits(rs, w, sigma)


# --------------------------------------------------------------------- the search
def _ops_key(ops: Sequence[Dict[str, Any]]) -> tuple:
    """Return a hashable digest of a schedule that keeps the order, for memoization."""
    return tuple((o["op"], o.get("i"), o.get("n"), o.get("dst"), o.get("src"),
                  bool(o.get("negSrc")), o.get("shift")) for o in ops)


def _ops_from_key(ops_key: tuple) -> List[Dict[str, Any]]:
    """The inverse of :func:`_ops_key`."""
    return [{"op": o[0], "i": o[1], "n": o[2], "dst": o[3], "src": o[4],
             "negSrc": o[5], "shift": o[6]} for o in ops_key]


@lru_cache(maxsize=4096)
def _tight_width_cached(ops_key: tuple, k: int, w: int, limit: int) -> int:
    ops = _ops_from_key(ops_key)
    rs = limb_ranges(k, w)
    for candidate in range(1, limit + 1):
        if reachable_fits(rs, candidate, ops):
            return candidate
    raise TightWidthError(
        f"tight width search failed at k={k}, w={w} within {limit} candidates. Either the "
        f"schedule contains an inexact shiftR (Lean's `none` case) or no width in range holds "
        f"every reachable value.")


def tight_width(ops: Sequence[Dict[str, Any]], k: int, w: int,
                sym_width: Optional[int] = None, fuel: Optional[int] = None) -> int:
    """`tightWidth` (ForshorEmit/Width.lean:53): the least `w' >= 1` that each reachable value
    fits in, for square operands of width `w`.

    `sym_width` limits the search as Lean does (`symWidth ops w + 8`). It is only a fuel bound.
    If you have it, give it. Then a pathological schedule fails and does not loop. If you do not
    give it, the search has no ceiling that comes from a number that we possibly do not have.
    """
    limit = fuel if fuel is not None else ((sym_width + 8) if sym_width is not None else 4096)
    return _tight_width_cached(_ops_key(ops), k, w, limit)


@dataclass(frozen=True)
class TightTable:
    """Return `m -> W_tight(k*m)` over a uniform-limb ladder, and the affine tail that the function finds."""
    k: int
    table: Dict[int, int]
    beta: Optional[int]
    from_m: Optional[int]

    def at_m(self, m: int) -> int:
        if m in self.table:
            return self.table[m]
        if self.beta is not None and self.from_m is not None and m >= self.from_m:
            return m + self.beta
        raise ValueError(f"W_tight(k={self.k}) has no entry for m={m} and no affine tail covers it")


def affine_tail(widths: Sequence[Tuple[int, int]]) -> Tuple[Optional[int], Optional[int]]:
    """`Shor.affineTail` (Emit/Symbolic/Width.lean:39), returned as ``(from_m, beta)``.

    The least `m0` such that `W(m) = m + beta` for each scanned `m >= m0`. `beta` comes from
    the largest scanned `m`. Return ``(None, None)`` if the table is empty or `beta` is
    negative. This agrees with the Lean `none`.
    """
    if not widths:
        return (None, None)
    ordered = sorted(widths)
    m_last, w_last = ordered[-1]
    beta = w_last - m_last
    if beta < 0:
        return (None, None)
    m0 = m_last
    for m, w in reversed(ordered[:-1]):
        if w - m != beta:
            break
        m0 = m
    return (m0, beta)


def tight_table(ops: Sequence[Dict[str, Any]], k: int, m_max: int,
                sym_by_m: Optional[Dict[int, int]] = None) -> TightTable:
    """Scan `m = 1..m_max` at `w = k*m`, the uniform-limb ladder that the width tables use."""
    table = {m: tight_width_closed_form(ops, k, k * m) for m in range(1, m_max + 1)}
    from_m, beta = affine_tail(sorted(table.items()))
    return TightTable(k, table, beta, from_m)


def tight_table_from_bundle(bundle_doc: Dict[str, Any], m_max: Optional[int] = None) -> TightTable:
    """Build the tight table from an emitted `forshor.emit/v1` bundle.

    The function reads the schedule that the drop emits and the `sym` widths that it emits next to
    it. It computes nothing from a frozen constant. It works for each `--table`. Build it from the
    SAME table as the other numbers, because the schedule is different for each table.
    """
    k = int(bundle_doc["k"])
    ops = bundle_doc["schedule"]["ops"]
    by_m = {int(e["m"]): int(e["width"]) for e in bundle_doc["width"]["by_m"]}
    return tight_table(ops, k, m_max or max(by_m), sym_by_m=by_m)


# ===================================================================== the closed form
# The scan above is faithful to Lean, and we used it to validate the reconstruction. But do not
# use it to *compute* the width, because it cannot reach the important widths.
#
# The coefficient rows do not depend on m. `start_state` is the identity matrix. Each op
# (`shiftL n`, `shiftR n`, `negate`, `addScaled shift`) moves integers with shifts that are
# constants in the schedule. Nothing in that recursion contains `w` or `m`. Only the LIMB RANGES
# depend on the width. Thus we can run the schedule one time, symbolically, and write the exact
# range of each reachable row as a closed form in the limb widths:
#
#     hi(r)  =  P*2^Wl + |t|*2^(Wt-1) + (-P - max(t, 0))
#     -lo(r) = -N*2^Wl + |t|*2^(Wt-1) + ( N - max(-t, 0))
#
# where P = sum of positive coefficients over the lower limbs, N = sum of negative coefficients,
# and t is the top-limb coefficient (the top limb is the signed limb). The two are affine in the two
# powers, with integer coefficients that do not depend on m. `-N >= 0`, thus the leading terms of
# the two sides are non-negative. Then the width is directly:
#
#     W_tight = 1 + ceil(log2( max over all reachable rows of max(-lo, hi+1) ))
#
# There is no search, and the result is exact at each m. This includes m ~ 2^150, the level of the
# 2048-bit headline. At that level, the scan cannot even hold the candidate widths in memory.

#: One demand term `a*2^Wl + b*2^(Wt-1) + c`, from one row and one side (upper or lower).
Demand = Tuple[int, int, int]


@lru_cache(maxsize=512)
def schedule_rows(ops_key: tuple, k: int) -> Tuple[Register, ...]:
    """Return all coefficient rows that are reachable at a point in the schedule, without duplicates.

    `reachableFits` checks the state before each op and one time at the end. Thus this function
    collects the start state, each intermediate state, and the final state. The order is not
    important, because the width is a max over all of them. Thus the function removes duplicates,
    and this saves much work: the 22 states of k=3 hold only 11 distinct rows.
    """
    ops = _ops_from_key(ops_key)
    sigma = start_state(k)
    seen = {row for row in sigma}
    for op in ops:
        nxt = apply_op(sigma, op)
        if nxt is None:
            raise TightWidthError(
                "the schedule contains an inexact shiftR (Lean's `Register.shiftR?` returns "
                "`none` here), so it has no reachable-value width at any width")
        sigma = nxt
        seen.update(sigma)
    return tuple(sorted(seen))


def row_demands(r: Register, k: int) -> Tuple[Demand, Demand]:
    """Return the upper and lower demands of one row, as `(a, b, c)` in `a*2^Wl + b*2^(Wt-1) + c`.

    The block above gives the derivation. The function returns the two values as the magnitude
    that the register must hold. Thus it returns the lower side as `-lo`, not `lo`. The caller
    then takes a plain maximum over all values, and it does not have to monitor signs again.
    """
    lower = r[:k - 1]
    pos = sum(c for c in lower if c > 0)
    neg = sum(c for c in lower if c < 0)
    t = r[k - 1]
    hi_plus_one = (pos, abs(t), -pos - max(t, 0) + 1)
    neg_lo = (-neg, abs(t), neg - max(-t, 0))
    return hi_plus_one, neg_lo


def _eval_demand(d: Demand, w_lower: int, w_top: int) -> int:
    a, b, c = d
    return a * (1 << w_lower) + b * (1 << (w_top - 1)) + c


def _ceil_log2(x: int) -> int:
    """Return `ceil(log2 x)` for x >= 1, exactly, in big integers."""
    return (x - 1).bit_length()


def tight_width_closed_form(ops: Sequence[Dict[str, Any]], k: int, w: int) -> int:
    """Return `W_tight` at operand width `w`, from the closed form. There is no search.

    The result agrees with :func:`tight_width` at all widths. Call this function. It uses
    O(#distinct rows) big-integer arithmetic, not O(candidate widths x schedule length). It is
    also defined at values of `w` that the scan cannot reach.
    """
    widths = limb_widths(k, w)
    w_lower, w_top = widths[0], widths[-1]
    rows = schedule_rows(_ops_key(ops), k)
    demand = 0
    for r in rows:
        for d in row_demands(r, k):
            demand = max(demand, _eval_demand(d, w_lower, w_top))
    return max(1, 1 + _ceil_log2(demand)) if demand > 0 else 1


@dataclass(frozen=True)
class TightRule:
    """`W_tight` on the uniform-limb ladder `w = k*m`, as a closed form in `m`.

    On that ladder, each limb has width exactly `m` (`k*m // k = m`, and the top limb gets
    `k*m - (k-1)*m = m`). Thus each demand becomes `alpha * 2^(m-1) + c` with `alpha = 2a + b`.
    Then `beta` and `from_m` are DERIVED, with the threshold proved below. They do not come from
    a scan of a window with the hope that the tail continues.
    """
    k: int
    demands: Tuple[Tuple[int, int], ...]     # (alpha, c), one for each row and side
    beta: int
    from_m: int

    def at_m(self, m: int) -> int:
        if m < 1:
            raise ValueError(f"m must be at least 1, got {m}")
        if m >= self.from_m:
            return m + self.beta
        demand = max((a * (1 << (m - 1)) + c for a, c in self.demands), default=0)
        return max(1, 1 + _ceil_log2(demand)) if demand > 0 else 1

    def at_n(self, n: int) -> int:
        return self.at_m(-(-n // self.k))

    def expr(self, symbol=None):
        """Return `W_tight` as a SymPy expression in `n`. It is valid for `n >= k*from_m`."""
        import sympy
        s = symbol if symbol is not None else sympy.Symbol("n", positive=True, integer=True)
        return sympy.ceiling(s / self.k) + self.beta

    @property
    def valid_from_n(self) -> int:
        return self.k * self.from_m


def tight_rule(ops: Sequence[Dict[str, Any]], k: int) -> TightRule:
    """Derive the closed form, and the exact `m` from which it is affine.

    THE THRESHOLD. An affine tail from a scanned window is an extrapolation, and this tail must not
    be one. Write the demand set as `{alpha_i * 2^(m-1) + c_i}`, and let
    `A = max alpha_i`, `C = max{c_i : alpha_i = A}`, `c_max = max c_i`.

      1. A row with `alpha < A` is dominated after `2^(m-1) > c_max - C`, because then
         `(A-1)*2^(m-1) + c_max < A*2^(m-1) + C`. Thus, after that point, `D(m) = A*2^(m-1) + C`.
      2. If also `2^(m-1) > |C|`, then `D(m)/2^(m-1) = A + C/2^(m-1)` is in `(A-1, A+1)`.
         Thus `ceil(log2 D(m)) = m - 1 + e`, with `e = ceil(log2(A+1))` if `C > 0`
         and `A` is a power of two, and `e = ceil(log2 A)` if not.

    Thus `W(m) = m + beta` with `beta = e` for each `m` after the two thresholds. This is a
    derived statement about all `m`. It is not a pattern that we saw up to `m_max`.
    """
    rows = schedule_rows(_ops_key(ops), k)
    demands = []
    for r in rows:
        for a, b, c in row_demands(r, k):
            demands.append((2 * a + b, c))
    demands = tuple(sorted(set(demands)))
    if not demands or max(a for a, _ in demands) <= 0:
        return TightRule(k, demands, beta=1, from_m=1)

    top = max(a for a, _ in demands)
    c_top = max(c for a, c in demands if a == top)
    c_max = max(c for _, c in demands)

    # threshold 1: the rows with a lower alpha are dominated
    gap = c_max - c_top
    m1 = 1 + max(0, _ceil_log2(gap + 1)) if gap >= 0 else 1
    # threshold 2: the constant cannot move ceil(log2) into a different bracket
    m2 = 1 + max(0, _ceil_log2(abs(c_top) + 1)) if c_top != 0 else 1
    from_m = max(1, m1, m2)

    e = _ceil_log2(top + 1) if (c_top > 0 and top & (top - 1) == 0) else _ceil_log2(top)
    beta = e

    # The derivation gives a sufficient threshold, not the least threshold. Decrease `from_m` while
    # the closed form still agrees with the exact evaluation. Thus the reported `from_m` is the
    # smallest value that is really true. This lets us compare it with the `affine_tight.from_m`
    # of the v1 emitter, which came from a scan.
    probe = TightRule(k, demands, beta, from_m=1 << 62)     # make the evaluation exact
    while from_m > 1 and probe.at_m(from_m - 1) == (from_m - 1) + beta:
        from_m -= 1
    return TightRule(k, demands, beta, from_m)


def tight_rule_from_bundle(bundle_doc: Dict[str, Any]) -> TightRule:
    """Return the closed form, from the schedule of an emitted `forshor.emit/v1` bundle."""
    return tight_rule(bundle_doc["schedule"]["ops"], int(bundle_doc["k"]))
