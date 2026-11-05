"""The exact reachable-value ("tight") register width, reconstructed from the emitted schedule.

WHY THIS FILE EXISTS
--------------------
ForShor carries two width policies, and they are not the same object:

  * **sym** -- `RecursivePhaseWorkspace.nextWidth`, the over-approximating `updateWidthState`
    scan. This is the width the fidelity theorems are stated at.
  * **tight** -- the least width at which every *reachable* intermediate value fits. This is
    the width the circuit actually occupies, and the width Qualtran counts.

ForShor itself records that the symbolic policy is loose ("54 wasted qubits at k=3") and that
narrowing it "would put the emission outside the proved fidelity theorem". Both matter, and the
gap between them is a reported quantity -- quoting only `sym` overstates the register, quoting
only `tight` claims a width no theorem covers.

The v1 emitter emitted both. The 2026-09-18 drop emits only `sym`, and says so:

    "Only the symbolic policy is emitted; the older tooling's exact reachable-value ("tight")
     width scan is not carried over (nothing on this repository's data path uses it)."
    -- FastMultiplication/Emit/Symbolic/Width.lean

So it is reconstructed here. **This is not a mirror of an unemitted Lean quantity.** The input
is the schedule the drop *does* emit (`bundle.schedule.ops`); what this module adds is interval
arithmetic over that schedule, which is our own computation and always was -- `reachableFits`
lived in our `Emit/LowGateIR.lean`, not in the collaborator's proof. The limb split it needs
(`phaseLimbWidthOfWidth`, `phaseSplitLogicalWidth`) is still in the drop and is floor division.

WHY INTERVAL ARITHMETIC IS EXACT HERE (LowGateIR.lean's own argument)
---------------------------------------------------------------------
Register `i` holds a coefficient row `c`, denoting `sum_j c_j * v_j` where `v_j` is limb `j` of
the operand. The limbs vary INDEPENDENTLY over their own ranges, so

    max over the operand box of  sum_j c_j v_j  =  sum_j max (c_j * v_j)

is *attained*, not merely bounded. So a `True` here proves the width suffices, and a `False`
means the circuit genuinely wraps. This is why the answer is "tight" and not "an upper bound".

FIDELITY
--------
Every definition below is a transcription of a named Lean definition, cited at its site. The
reconstruction is validated against the v1 emitter's own `W_tight` tables for k = 2..7 in
`tests/test_tight_width.py` -- those tables are ground truth, produced by the Lean code this
file re-folds, and they are reproduced exactly.
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
    """The schedule could not be executed, or no width was found within the fuel bound."""


# --------------------------------------------------------------------- the op semantics
def start_state(k: int) -> State:
    """`State.start_state`: register `i` holds `x_i` (Registers.lean:61)."""
    return tuple(tuple(1 if j == i else 0 for j in range(k)) for i in range(k))


def _set_reg(sigma: State, i: int, r: Register) -> State:
    return tuple(r if idx == i else row for idx, row in enumerate(sigma))


def apply_op(sigma: State, op: Dict[str, Any]) -> Optional[State]:
    """`applyOp?` (Language.lean:23). ``None`` exactly where Lean returns ``none``.

    The only partial case is `shiftR`, which fails unless every coefficient is divisible by
    `2^n` -- Lean's `Register.shiftR?`. Nothing here may silently round.
    """
    kind = op["op"]
    if kind == "phaseProduct":
        return sigma                                        # no effect on the coefficients
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
        # Lean tests `r j % m = 0` over ℤ, where `%` is Euclidean and thus non-negative; for a
        # power of two that agrees with Python's `%` on sign, so `c % m` is the same predicate.
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
    """`Emit.limbRange` (LowGateIR.lean:542): lower limbs unsigned, the top limb signed."""
    if j + 1 == k:
        return (-(2 ** (w - 1)), 2 ** (w - 1) - 1)
    return (0, 2 ** w - 1)


def limb_widths(k: int, w: int) -> List[int]:
    """`phaseSplitLogicalWidth w (phaseLimbWidthOfWidth w k) k` (Layout.lean:89, 110).

    Floor division, deliberately: the lower `k-1` limbs get `w // k` and the top limb absorbs
    the remainder. w=5, k=4 gives 1, 1, 1, 2.
    """
    base = w // k
    return [base] * (k - 1) + [w - (k - 1) * base]


def limb_ranges(k: int, w: int) -> List[Range]:
    """`limbRangesOf` (ForshorEmit/Width.lean:42)."""
    widths = limb_widths(k, w)
    return [limb_range(k, j, widths[j]) for j in range(k)]


def row_range(r: Register, rs: Sequence[Range]) -> Range:
    """`rowRange` (LowGateIR.lean:546). Exact, by the independence argument in the header."""
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
    """`reachableFits` (LowGateIR.lean:568): EVERY intermediate state must fit `w`.

    Note the order Lean uses -- the state is checked *before* each op and once more at the end,
    so the start state and the final state are both included. Checking only after each op would
    miss a start state that already overflows.
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
    """A hashable, order-preserving digest of a schedule, for memoization."""
    return tuple((o["op"], o.get("i"), o.get("n"), o.get("dst"), o.get("src"),
                  bool(o.get("negSrc")), o.get("shift")) for o in ops)


def _ops_from_key(ops_key: tuple) -> List[Dict[str, Any]]:
    """Inverse of :func:`_ops_key`."""
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
    """`tightWidth` (ForshorEmit/Width.lean:53): the least `w' >= 1` that every reachable value
    fits, for square operands of width `w`.

    `sym_width` bounds the search the way Lean does (`symWidth ops w + 8`). It is only a fuel
    bound -- pass it when you have it so a pathological schedule fails instead of looping, and
    leave it out to search without a ceiling derived from a number we may not have.
    """
    limit = fuel if fuel is not None else ((sym_width + 8) if sym_width is not None else 4096)
    return _tight_width_cached(_ops_key(ops), k, w, limit)


@dataclass(frozen=True)
class TightTable:
    """`m -> W_tight(k*m)` over a uniform-limb ladder, plus the detected affine tail."""
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

    The least `m0` such that `W(m) = m + beta` for every scanned `m >= m0`, with `beta` taken
    from the largest scanned `m`. ``(None, None)`` when the table is empty or `beta` is
    negative, matching Lean's `none`.
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
    """Scan `m = 1..m_max` at `w = k*m`, the uniform-limb ladder the width tables use."""
    table = {m: tight_width_closed_form(ops, k, k * m) for m in range(1, m_max + 1)}
    from_m, beta = affine_tail(sorted(table.items()))
    return TightTable(k, table, beta, from_m)


def tight_table_from_bundle(bundle_doc: Dict[str, Any], m_max: Optional[int] = None) -> TightTable:
    """Build the tight table from an emitted `forshor.emit/v1` bundle.

    Reads the schedule the drop emits and the `sym` widths it emits beside it; computes nothing
    from a frozen constant. Works for either `--table` -- and must be built from the SAME table
    the rest of the numbers came from, since the schedule differs between them.
    """
    k = int(bundle_doc["k"])
    ops = bundle_doc["schedule"]["ops"]
    from .widths import sym_tables
    by_m = sym_tables(k, bundle_doc["width"])[0]
    return tight_table(ops, k, m_max or max(by_m), sym_by_m=by_m)


# ===================================================================== the closed form
# The scan above is faithful to Lean, and it is how the reconstruction was validated. But it
# is not how the width should be *computed*, and it cannot reach the widths that matter.
#
# The coefficient rows are independent of m. `start_state` is the identity matrix and every op
# -- `shiftL n`, `shiftR n`, `negate`, `addScaled shift` -- moves integers around using shifts
# that are constants baked into the schedule. Nothing in that recursion mentions `w` or `m`.
# Only the LIMB RANGES depend on the width. So the schedule can be run once, symbolically, and
# each reachable row's exact range written down as a closed form in the limb widths:
#
#     hi(r)  =  P*2^Wl + |t|*2^(Wt-1) + (-P - max(t, 0))
#     -lo(r) = -N*2^Wl + |t|*2^(Wt-1) + ( N - max(-t, 0))
#
# where P = sum of positive coefficients over the lower limbs, N = sum of negative ones, and
# t is the top-limb coefficient (the top limb is the signed one). Both are affine in the two
# powers, with integer coefficients that do not depend on m, and `-N >= 0` so both sides are
# non-negative-leading. The width then follows directly:
#
#     W_tight = 1 + ceil(log2( max over all reachable rows of max(-lo, hi+1) ))
#
# No search, exact at any m -- including m ~ 2^150, the level the 2048-bit headline runs at,
# where the scan cannot even represent the candidate widths, let alone iterate to one.

#: One demand term `a*2^Wl + b*2^(Wt-1) + c`, from one row and one side (upper or lower).
Demand = Tuple[int, int, int]


@lru_cache(maxsize=512)
def schedule_rows(ops_key: tuple, k: int) -> Tuple[Register, ...]:
    """Every coefficient row reachable at any point in the schedule, de-duplicated.

    `reachableFits` checks the state before each op and once at the end, so this collects the
    start state, each intermediate, and the final one. Order is irrelevant -- the width is a
    max over all of them -- so they are de-duplicated, which is a large saving: k=3's 22 states
    hold only 11 distinct rows.
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
    """The upper and lower demands of one row, as `(a, b, c)` in `a*2^Wl + b*2^(Wt-1) + c`.

    Derived in the block above. Both are returned in "how much magnitude must the register
    hold" form -- the lower side is returned as `-lo`, not `lo` -- so the caller takes a plain
    maximum over everything and never has to track signs again.
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
    """`ceil(log2 x)` for x >= 1, exactly, in big integers."""
    return (x - 1).bit_length()


def tight_width_closed_form(ops: Sequence[Dict[str, Any]], k: int, w: int) -> int:
    """`W_tight` at operand width `w`, evaluated in closed form. No search.

    Agrees with :func:`tight_width` everywhere; this is the one to call. It is O(#distinct
    rows) big-integer arithmetic rather than O(candidate widths x schedule length), and it is
    defined at values of `w` the scan cannot reach.
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

    On that ladder every limb has width exactly `m` (`k*m // k = m`, and the top limb absorbs
    `k*m - (k-1)*m = m`), so each demand collapses to `alpha * 2^(m-1) + c` with
    `alpha = 2a + b`. `beta` and `from_m` are then DERIVED, with the threshold proved below,
    rather than detected by scanning a window and hoping the tail continues.
    """
    k: int
    demands: Tuple[Tuple[int, int], ...]     # (alpha, c), one per row per side
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
        """`W_tight` as a SymPy expression in `n`, valid for `n >= k*from_m`."""
        import sympy
        s = symbol if symbol is not None else sympy.Symbol("n", positive=True, integer=True)
        return sympy.ceiling(s / self.k) + self.beta

    @property
    def valid_from_n(self) -> int:
        return self.k * self.from_m


def tight_rule(ops: Sequence[Dict[str, Any]], k: int) -> TightRule:
    """Derive the closed form, including the exact `m` from which it is affine.

    THE THRESHOLD, since an affine tail asserted from a scanned window is an extrapolation and
    this one should not be. Write the demand set as `{alpha_i * 2^(m-1) + c_i}` and let
    `A = max alpha_i`, `C = max{c_i : alpha_i = A}`, `c_max = max c_i`.

      1. A row with `alpha < A` is dominated once `2^(m-1) > c_max - C`, since then
         `(A-1)*2^(m-1) + c_max < A*2^(m-1) + C`. So beyond that, `D(m) = A*2^(m-1) + C`.
      2. Once additionally `2^(m-1) > |C|`, we have `D(m)/2^(m-1) = A + C/2^(m-1)` lying in
         `(A-1, A+1)`, so `ceil(log2 D(m)) = m - 1 + e` with `e = ceil(log2(A+1))` when `C > 0`
         and `A` is a power of two, and `e = ceil(log2 A)` otherwise.

    Hence `W(m) = m + beta` with `beta = e` for every `m` past both thresholds -- a derived
    statement about all `m`, not a pattern observed up to `m_max`.
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

    # threshold 1: lower-alpha rows are dominated
    gap = c_max - c_top
    m1 = 1 + max(0, _ceil_log2(gap + 1)) if gap >= 0 else 1
    # threshold 2: the constant cannot move ceil(log2) across a bracket
    m2 = 1 + max(0, _ceil_log2(abs(c_top) + 1)) if c_top != 0 else 1
    from_m = max(1, m1, m2)

    e = _ceil_log2(top + 1) if (c_top > 0 and top & (top - 1) == 0) else _ceil_log2(top)
    beta = e

    # The derivation gives a sufficient threshold, not the least one. Walk `from_m` down while
    # the closed form still agrees with the exact evaluation, so the reported `from_m` is the
    # smallest value that actually holds -- this is what makes it comparable with the v1
    # emitter's own `affine_tight.from_m`, which was found by scanning.
    probe = TightRule(k, demands, beta, from_m=1 << 62)     # force exact evaluation
    while from_m > 1 and probe.at_m(from_m - 1) == (from_m - 1) + beta:
        from_m -= 1
    return TightRule(k, demands, beta, from_m)


def tight_rule_from_bundle(bundle_doc: Dict[str, Any]) -> TightRule:
    """The closed form, from an emitted `forshor.emit/v1` bundle's schedule."""
    return tight_rule(bundle_doc["schedule"]["ops"], int(bundle_doc["k"]))
