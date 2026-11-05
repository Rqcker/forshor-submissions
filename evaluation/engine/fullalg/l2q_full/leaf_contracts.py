"""Leaf lowering contracts (T3 of TODO_ARTIFACT_INFRASTRUCTURE.2026-09-11.md).

Every leaf the wired program (`program.py`) can reach has a Clifford+T realisation here with an explicit
contract: what it computes, what it costs, what error it introduces, and what it assumes of its
neighbours (the And-pair certificate). Nothing in this module depends on `n`; a catalogue is built from a
family census (`program.census` / `Family.census`) and the toggles of the registry.

Conventions (all exact, stdlib integers; `Fraction` only in the reference checks):

  * a phase-product cell on bits (i, j) of x, z with signed weights X = w_x(i), Z = w_z(j) applies
        exp(i * pi * phi * X * Z)  =  exp(2 * pi * i * turn),   turn = ((phi * X * Z) / 2) mod 1
    with `phi = phi_num / phi_den` the leaf angle in units of pi (the emitter's `phi`);
  * the b-bit gradient constant of a turn is  T_b(turn) = round_half_even(2^b * turn) mod 2^b
    (`WindowedPhaseGrid._table` and `_gadget_leaf_local` both round this way: Python's `round` on a
    `Fraction` is exact half-even);
  * the dyadic resolution s of a turn is the exponent of its reduced denominator (0 for the identity,
    None for a non-dyadic turn);
  * an And pair is UNITARY (owner decision, 14 Sep 2026): the 13-gate 4-T compute word and its exact inverse,
    4 + 4 = 8 T per pair (`constants.T_PER_AND_PAIR`), the default of `AndPair()` and of every price below.
    The measured uncompute (H, measure, classically controlled CZ; 0 T, so 4 T per pair -- what the frozen
    Qualtran tree's `QECGatesCost.total_t_count` assumes) is legal only for a pair matched inside one definition
    (the pairing certificate) and is kept as a labelled comparison column (`AndPair("measured")`). The `ctbp/`
    freeze lowers And-dagger unitarily as well.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field, asdict
from fractions import Fraction
from functools import lru_cache
from math import gcd
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "signed_weight", "round_half_even", "turn_fraction", "cell_constant", "cell_resolution",
    "window_pairs", "window_table",
    "AND_WORD", "AND_DG_WORD", "CZ_WORD", "CS_WORD", "CT_WORD", "inverse_word", "word_t_count", "word_pairing",
    "AndPair", "EXACT_SMALL_T", "exact_small_word", "CellRealization", "classify_cell",
    "GridRealization", "grid_realization", "windowed_realization", "choose_window", "is_generic_angle", "generic_grid_bound",
    "prep_word_records", "pairing_certificate", "qrom_and_count", "CatalogueRow", "build_catalogue", "catalogue_summary",
]

TWO_PI = 2 * math.pi


# ======================================================================================= exact integer core
def signed_weight(width: int, i: int) -> int:
    """Two's-complement weight of bit `i` (0 = LSB) of a `width`-bit register; the top bit is negative
    (`lowgate_ir.signed_bit_weight_lsb`, restated here so the generator is stdlib-only)."""
    if not 0 <= i < width:
        raise ValueError(f"bit {i} outside a {width}-bit register")
    return -(1 << i) if i == width - 1 else (1 << i)


def round_half_even(num: int, den: int) -> int:
    """Nearest integer to num/den, ties to even -- exactly what `round(Fraction(num, den))` returns."""
    if den <= 0:
        raise ValueError("den must be positive")
    q, r = divmod(num, den)          # r in [0, den)
    twice = 2 * r
    if twice > den or (twice == den and (q & 1)):
        return q + 1
    return q


def turn_fraction(phi_num: int, phi_den: int, X: int, Z: int) -> Tuple[int, int]:
    """`((phi * X * Z) / 2) mod 1` as a reduced (num, den), 0 <= num < den. A power-of-two denominator (every
    QFT / U2 angle, up to thousands of bits) reduces by a bit scan instead of a big-integer gcd."""
    if phi_den <= 0:
        raise ValueError("phi_den must be positive")
    den = 2 * phi_den
    num = (phi_num * X * Z) % den
    if num == 0:
        return 0, 1
    if den & (den - 1) == 0:
        v = (num & -num).bit_length() - 1          # 2-adic valuation of num
        return num >> v, den >> v
    g = gcd(num, den)
    return num // g, den // g


def cell_constant(phi_num: int, phi_den: int, X: int, Z: int, b: int) -> int:
    """The b-bit gradient constant `round_half_even(2^b * turn) mod 2^b` of one cell (integer arithmetic)."""
    num, den = turn_fraction(phi_num, phi_den, X, Z)
    return round_half_even(num << b, den) % (1 << b)


def cell_resolution(phi_num: int, phi_den: int, X: int, Z: int) -> Optional[int]:
    """Dyadic resolution of the cell's turn: 0 for the identity, s if turn = odd / 2^s, None if non-dyadic."""
    num, den = turn_fraction(phi_num, phi_den, X, Z)
    if num == 0:
        return 0
    if den & (den - 1):
        return None
    return den.bit_length() - 1


def window_pairs(x_width: int, z_width: int, wx: int, wz: int) -> List[Tuple[int, int, int, int]]:
    """`(ox, nx, oz, nz)` for every window pair, LSB-first, matching `WindowedPhaseGrid._windows`."""
    xs = [(o, min(wx, x_width - o)) for o in range(0, x_width, wx)]
    zs = [(o, min(wz, z_width - o)) for o in range(0, z_width, wz)]
    return [(ox, nx, oz, nz) for ox, nx in xs for oz, nz in zs]


def window_table(phi_num: int, phi_den: int, x_width: int, z_width: int,
                 ox: int, nx: int, oz: int, nz: int, b: int) -> List[List[int]]:
    """The QROM table of one window pair: `tab[a][c] = T_b(turn(X_A(a), Z_B(c)))`, zero when either window
    value is zero -- the table-generator SPEC `WindowedPhaseGrid._table` must reproduce."""
    wxs = [signed_weight(x_width, ox + t) for t in range(nx)]
    wzs = [signed_weight(z_width, oz + t) for t in range(nz)]
    tab = [[0] * (1 << nz) for _ in range(1 << nx)]
    for a in range(1 << nx):
        X = sum(wxs[t] for t in range(nx) if (a >> t) & 1)
        if X == 0:
            continue
        for c in range(1 << nz):
            Z = sum(wzs[t] for t in range(nz) if (c >> t) & 1)
            if Z == 0:
                continue
            tab[a][c] = cell_constant(phi_num, phi_den, X, Z, b)
    return tab


# ================================================================================================ words
# A word is a tuple of gates `(name, *qubits)` over the alphabet below. `AND` / `AND_DG` are macros for the
# 13-gate 4-T compute template (Qualtran `And`, extracted gate for gate) and its exact inverse; `MEAS_UNCOMPUTE`
# is the measurement-assisted uncompute (H, measure, CZ on the controls if 1) with 0 T; `ADD(s)` is the
# s-bit ripple addition into the gradient's top s bits (a macro priced through Qualtran's `Add`).
CLIFFORD_T_ALPHABET = ("H", "S", "SDG", "T", "TDG", "X", "Z", "CX", "CZ")
T_GATES = ("T", "TDG")
_INVERSE = {"H": "H", "S": "SDG", "SDG": "S", "T": "TDG", "TDG": "T", "X": "X", "Z": "Z", "CX": "CX", "CZ": "CZ"}

# Qualtran's `And` compute on (c0, c1, t) with t clean: 13 gates, 4 T.
AND_WORD: Tuple[Tuple[Any, ...], ...] = (
    ("H", 2), ("T", 2), ("CX", 0, 2), ("CX", 1, 2), ("CX", 2, 0), ("CX", 2, 1),
    ("TDG", 0), ("TDG", 1), ("T", 2), ("CX", 2, 0), ("CX", 2, 1), ("H", 2), ("S", 2),
)


def inverse_word(word: Sequence[Tuple[Any, ...]]) -> Tuple[Tuple[Any, ...], ...]:
    return tuple((_INVERSE[g[0]],) + tuple(g[1:]) for g in reversed(word))


AND_DG_WORD = inverse_word(AND_WORD)                       # unitary uncompute, 4 T
CZ_WORD: Tuple[Tuple[Any, ...], ...] = (("CZ", 0, 1),)      # cp(pi)      exact, 0 T
CS_WORD: Tuple[Tuple[Any, ...], ...] = (("T", 0), ("T", 1), ("CX", 0, 1), ("TDG", 1), ("CX", 0, 1))   # cp(pi/2), 3 T
# cp(pi/4) with a clean ancilla 2: And(0,1 -> 2), T on the ancilla, And-dagger (measured: 5 T; unitary: 9 T)
CT_WORD: Tuple[Tuple[Any, ...], ...] = (("AND", 0, 1, 2), ("T", 2), ("AND_DG", 0, 1, 2))
EXACT_SMALL_T = {1: 0, 2: 3, 3: 5}                         # CZ / CS / CT at the MEASURED And price (classification base)


def exact_small_word(s: int, num: int = 1) -> Tuple[Tuple[Any, ...], ...]:
    """The exact word for the turn `num / 2^s` (num odd), s = 1, 2, 3, on (x, z) [+ ancilla 2 for s = 3]:
    s = 1: CZ; s = 2: CS (num = 1) or CS-dagger (num = 3), 3 T ancilla-free; s = 3: And -> ZPow(num / 4) on
    the ancilla (one T with Cliffords: T, S.T, Z.T, T-dagger for num = 1, 3, 5, 7) -> And-dagger."""
    num %= 1 << s
    if s == 1:
        return CZ_WORD
    if s == 2:
        return CS_WORD if num == 1 else (("TDG", 0), ("TDG", 1), ("CX", 0, 1), ("T", 1), ("CX", 0, 1))
    if s == 3:
        mid = {1: (("T", 2),), 3: (("S", 2), ("T", 2)), 5: (("Z", 2), ("T", 2)), 7: (("TDG", 2),)}[num]
        return (("AND", 0, 1, 2),) + mid + (("AND_DG", 0, 1, 2),)
    raise ValueError("exact small words exist for s = 1, 2, 3 only")


def word_t_count(word: Sequence[Tuple[Any, ...]], and_pair: "AndPair | None" = None) -> int:
    """T gates of a word, macros expanded: AND = 4, AND_DG = 0 (measured) or 4 (unitary), MEAS_UNCOMPUTE = 0."""
    ap = and_pair or AndPair()
    t = 0
    for g in word:
        if g[0] in T_GATES:
            t += 1
        elif g[0] == "AND":
            t += 4
        elif g[0] == "AND_DG":
            t += ap.t_uncompute
        elif g[0] == "ADD":
            t += ap.t * (int(g[1]) - 1)   # Qualtran Add(QUInt(s)): s-1 And pairs, priced like every other pair
    return t


def word_pairing(word: Sequence[Tuple[Any, ...]]) -> Dict[str, Any]:
    """LIFO pairing of AND / AND_DG macros on the same (c0, c1, t): every And must be undone by an And-dagger
    on the same wires with the ancilla untouched except as a control in between."""
    stack: List[Tuple[int, ...]] = []
    unpaired = 0
    touched_as_target = 0
    for g in word:
        if g[0] == "AND":
            stack.append(tuple(g[1:]))
        elif g[0] in ("AND_DG", "MEAS_UNCOMPUTE"):
            if not stack or stack[-1] != tuple(g[1:]):
                unpaired += 1
            else:
                stack.pop()
        elif stack and g[0] != "ADD":
            anc = {s[2] for s in stack}
            # a live ancilla may only be a CONTROL or receive a diagonal phase (Z, S, T and adjoints -- the CT word);
            # a non-diagonal single-qubit gate on it, or a CX / CZ-as-target write, breaks the contract
            if (len(g) == 2 and g[1] in anc and g[0] not in ("Z", "S", "SDG", "T", "TDG")) or                     (g[0] == "CX" and g[2] in anc):
                touched_as_target += 1
    return {"open": len(stack), "unpaired": unpaired + len(stack), "ancilla_written_between": touched_as_target,
            "ok": not stack and unpaired == 0 and touched_as_target == 0}


@dataclass(frozen=True)
class AndPair:
    """The And-pair contract. `unitary` (default, the 14 Sep 2026 decision): the exact inverse word, 4 T, no
    certificate needed -- 8 T per pair. `measured`: compute 4 T, uncompute by H + measurement + classically
    controlled CZ (0 T) -- legal only for a matched pair (`pairing_certificate`); the comparison column."""
    uncompute: str = "unitary"

    def __post_init__(self):
        if self.uncompute not in ("measured", "unitary"):
            raise ValueError("uncompute must be 'measured' or 'unitary'")

    @property
    def t_uncompute(self) -> int:
        return 0 if self.uncompute == "measured" else 4

    @property
    def t(self) -> int:
        return 4 + self.t_uncompute

    @property
    def uncompute_word(self) -> Tuple[Tuple[Any, ...], ...]:
        return (("MEAS_UNCOMPUTE", 0, 1, 2),) if self.uncompute == "measured" else AND_DG_WORD


# =================================================================================== cell classification
@dataclass(frozen=True)
class CellRealization:
    kind: str            # identity | cz | cs | ct | gadget | rounded_gadget
    s: int               # bits added into the gradient (gadget kinds), else 0
    const: int           # gradient constant (s-bit for `gadget`, b-bit for `rounded_gadget`)
    t: int               # T price of the two-qubit cell (measured And pairs)
    error: float         # operator-norm bound on this cell's error
    resolution: Optional[int]
    t_measured: int = -1  # the measured-pair base price (equals `t` unless the cell was re-priced at another contract)

    @property
    def base(self) -> int:
        return self.t_measured if self.t_measured >= 0 else self.t

    def controlled_t(self, and_pair: AndPair) -> int:
        """A controlled cell is And(x, z) -> the cell on (ctrl, ancilla) -> And-dagger: one more pair."""
        return 0 if self.kind == "identity" else self.t_at(and_pair) + and_pair.t

    @property
    def pairs(self) -> int:
        """And pairs of the two-qubit cell: 0 for identity / CZ / CS, 1 for CT, s for the gadget kinds."""
        return {"identity": 0, "cz": 0, "cs": 0, "ct": 1}.get(self.kind, self.s)

    def t_at(self, and_pair: AndPair) -> int:
        """The cell's T price at another And-pair contract (the classification itself does not change)."""
        return self.base + (and_pair.t - 4) * self.pairs


def classify_cell(phi_num: int, phi_den: int, X: int, Z: int, b: int, *, small_exact: bool = True,
                  and_pair: AndPair = AndPair(), drop_rule: bool = True) -> CellRealization:
    """The realisation of one cell under the toggles: exact CZ / CS / CT words for s = 1, 2, 3
    (`small_resolution_exact`), the s-bit gadget for dyadic s <= b, the b-bit rounded gadget otherwise; a
    constant that rounds to zero is elided (`leaf_drop_rule = rounded_constant_zero`), which also covers the
    pure turns beyond b + 1. Prices: 4 T per And pair (measured) as `rotation_histogram.gradient_price`."""
    num, den = turn_fraction(phi_num, phi_den, X, Z)
    s = cell_resolution(phi_num, phi_den, X, Z)
    if s == 0:
        c = CellRealization("identity", 0, 0, 0, 0.0, 0)
    elif s is not None and s <= b:
        if small_exact and s in EXACT_SMALL_T:
            kind = {1: "cz", 2: "cs", 3: "ct"}[s]
            c = CellRealization(kind, 0, num << (b - s), EXACT_SMALL_T[s], 0.0, s)
        else:
            c = CellRealization("gadget", s, num, 4 * s, 0.0, s)
    else:
        const = round_half_even(num << b, den) % (1 << b)
        turn = num / den
        err = TWO_PI * abs(turn - const / (1 << b)) if const else TWO_PI * min(turn, 1 - turn)
        if const == 0 and drop_rule:
            c = CellRealization("identity", 0, 0, 0, err, s)          # `leaf_drop_rule = rounded_constant_zero`
        else:
            c = CellRealization("rounded_gadget", b, const, 4 * b, err, s)   # drop rule off: the no-op add is still emitted
    return c if and_pair.t == 4 else CellRealization(c.kind, c.s, c.const, c.t_at(and_pair), c.error, c.resolution, c.t)


# ==================================================================================== grid realisations
@dataclass
class GridRealization:
    lowering: str                  # gadget | windowed
    x_width: int
    z_width: int
    controlled: bool
    b: int
    cells: int
    kinds: Dict[str, int]
    t: int                         # at the And-pair contract the realisation was built with
    t_unitary: int                 # at the unitary And price (8 T per pair)
    error: float                   # summed operator-norm bound
    exact: bool
    detail: Dict[str, Any] = field(default_factory=dict)
    t_measured: int = 0            # at the measured And price (4 T per pair) -- the comparison column
    error_by: Dict[str, float] = field(default_factory=dict)   # {"gradient_rounding": .., "pruning": ..}; sums to `error`


def _cell_groups(x_width: int, z_width: int) -> Iterable[Tuple[int, int, int]]:
    """`(X, Z, multiplicity)` over all cells, grouped by (i + j, top-bit pattern) -- the closed form
    `CompactSignedPhaseGrid.build_call_graph` uses; O(width) groups instead of O(width^2) cells."""
    xw, zw = x_width, z_width
    if xw == 0 or zw == 0:
        return
    # neither top bit: i in [0, xw-2], j in [0, zw-2], grouped by s = i + j
    if xw >= 2 and zw >= 2:
        for s in range(0, (xw - 2) + (zw - 2) + 1):
            lo, hi = max(0, s - (zw - 2)), min(xw - 2, s)
            if hi >= lo:
                yield (1 << s), 1, hi - lo + 1          # X*Z = 2^s; pass as X = 2^s, Z = 1
    # x top bit with every non-top z bit; z top bit with every non-top x bit
    for j in range(0, zw - 1):
        yield -(1 << (xw - 1)), (1 << j), 1
    for i in range(0, xw - 1):
        yield (1 << i), -(1 << (zw - 1)), 1
    yield -(1 << (xw - 1)), -(1 << (zw - 1)), 1


def grid_realization(phi_num: int, phi_den: int, x_width: int, z_width: int, b: int, *, controlled: bool = False,
                     small_exact: bool = True, and_pair: AndPair = AndPair(), drop_rule: bool = True) -> GridRealization:
    """The per-cell gadget lowering of one leaf grid: kind histogram, T at both And prices, error bound."""
    kinds: Counter = Counter()
    t = t_u = t_m = 0
    err_round = err_prune = 0.0
    unitary, measured = AndPair("unitary"), AndPair("measured")
    for X, Z, mult in _cell_groups(x_width, z_width):
        c = classify_cell(phi_num, phi_den, X, Z, b, small_exact=small_exact, drop_rule=drop_rule, and_pair=measured)
        kinds[c.kind] += mult
        t += mult * (c.controlled_t(and_pair) if controlled else c.t_at(and_pair))
        t_u += mult * (c.controlled_t(unitary) if controlled else c.t_at(unitary))
        t_m += mult * (c.controlled_t(measured) if controlled else c.t)
        if c.error:
            if c.kind == "identity":
                err_prune += mult * c.error          # a non-identity turn whose constant rounded to 0 (elided)
            else:
                err_round += mult * c.error          # rounded to b bits (or the drop rule off: a zero constant added)
    cells = x_width * z_width
    err = err_round + err_prune
    return GridRealization("gadget", x_width, z_width, controlled, b, cells, dict(kinds), t, t_u, err, err == 0.0, {}, t_m,
                           {"gradient_rounding": err_round, "pruning": err_prune})


def is_generic_angle(phi_num: int, phi_den: int, b: int, margin: int = 16) -> bool:
    """A non-dyadic angle whose reduced denominator exceeds 2^(b + margin): every cell is then a non-dyadic turn
    whose b-bit constant is generic (the U1 / U5 site angles, denominator N). The bound priced for such a grid
    is verified by sampling in `tests/test_t3_leaf_contracts.py`."""
    g = gcd(abs(phi_num), phi_den) or 1
    den = phi_den // g
    return bool(den & (den - 1)) and den.bit_length() > b + margin


def generic_grid_bound(x_width: int, z_width: int, b: int, *, controlled: bool = False,
                       and_pair: AndPair = AndPair()) -> GridRealization:
    """The gadget price of a generic-angle grid without touching its angle: every cell `rounded_gadget` (an
    upper bound -- a cell whose constant rounds to zero is elided and would only lower it), error bound one
    rounding per cell."""
    cells = x_width * z_width
    per = b * and_pair.t + (and_pair.t if controlled else 0)
    per_u = 8 * b + (8 if controlled else 0)
    per_m = 4 * b + (4 if controlled else 0)
    e = cells * TWO_PI * 2.0 ** -(b + 1)
    return GridRealization("gadget", x_width, z_width, controlled, b, cells, {"rounded_gadget": cells},
                           cells * per, cells * per_u, e, False,
                           {"price_basis": "generic-angle bound"}, cells * per_m, {"gradient_rounding": e, "pruning": 0.0})


def qrom_and_count(data, num_controls: int = 0) -> int:
    """The And count of Qualtran's `QROM.build_from_data(data)` decomposition, from the unary-iteration rule
    (`qualtran.bloqs.multiplexers.unary_iteration_bloq`): nested coherent for-loops over the data axes, an
    implicit segment tree per loop with one And per internal node whose two halves both intersect the
    iteration range, a `break_early` leaf wherever the remaining data slice is constant, the outermost loop
    with no controls spending no And at its root (the selection bit itself controls the halves), and a
    table that is constant everywhere loading with no iteration at all. `data` is a nested list (rows of
    ints, or a list of such tables for a leading control axis). Validated against `QECGatesCost` on random
    structured tables in `tests/test_t3_leaf_contracts.py`."""
    def depth(d):
        return 1 + depth(d[0]) if isinstance(d, (list, tuple)) else 0

    def flat(d):
        if isinstance(d, (list, tuple)):
            for x in d:
                yield from flat(x)
        else:
            yield d

    def constant(d) -> bool:
        it = flat(d)
        first = next(it, None)
        return all(x == first for x in it)

    ndim = depth(data)

    def shape(d):
        return [len(d)] + (shape(d[0]) if isinstance(d[0], (list, tuple)) else [])
    dims = shape(data)

    def seg(l, r, l_iter, r_iter, be, leaves):
        if l >= r_iter or l_iter >= r:
            return 0
        if l_iter <= l < r <= r_iter and (l == r - 1 or be(l, r)):
            leaves.append(l)
            return 0
        m = (l + r) >> 1
        if r_iter <= m:
            return seg(l, m, l_iter, r_iter, be, leaves)
        if l_iter >= m:
            return seg(m, r, l_iter, r_iter, be, leaves)
        return 1 + seg(l, m, l_iter, r_iter, be, leaves) + seg(m, r, l_iter, r_iter, be, leaves)

    def zero_control(nbits, r_iter, be, leaves):
        l, r = 0, 1 << nbits
        m = (l + r) >> 1
        if r_iter <= m:
            return zero_control(nbits - 1, r_iter, be, leaves)
        return seg(l, m, 0, r_iter, be, leaves) + seg(m, r, 0, r_iter, be, leaves)

    def loop(level, sub, controls):
        """And count of the loop over axis `level` of `sub` (the data slice for the current prefix)."""
        if level == ndim:
            return 0
        n = dims[level]
        nbits = max(1, (n - 1).bit_length())

        def be(l, r):
            return constant(sub[l:r])
        leaves: List[int] = []
        if controls == 0:
            ands = zero_control(nbits, n, be, leaves)
        else:
            ands = seg(0, 1 << nbits, 0, n, be, leaves)
        for i in leaves:
            ands += loop(level + 1, sub[i], 1)
        return ands

    if constant(data):
        return 0 if num_controls <= 1 else 1
    return loop(0, data, num_controls)


@lru_cache(maxsize=100_000)
def _qrom_add_cost(tab_key: Tuple[Tuple[int, ...], ...], b: int, controlled: bool) -> Tuple[int, int, int]:
    """`(and_pairs, toffolis, t)` of ONE window pair realised as 2 QROM(table) + Add(b) into the shared gradient:
    `2 * qrom_and_count(table) + (b - 1)` And pairs (Qualtran's `Add(QUInt(b))` ripple), no Toffoli, no direct T.
    A controlled pair stacks a zero slice under a leading control axis. `_qrom_add_cost_qec` is the same price
    read off the frozen tree's `QECGatesCost`, kept for the conformance test."""
    tab = [list(r) for r in tab_key]
    data = [[[0] * len(tab[0]) for _ in tab], tab] if controlled else tab
    return 2 * qrom_and_count(data) + (b - 1), 0, 0


@lru_cache(maxsize=None)
def _qrom_add_cost_qec(tab_key: Tuple[Tuple[int, ...], ...], b: int, controlled: bool) -> Tuple[int, int, int]:
    import numpy as np
    from qualtran import QUInt
    from qualtran.bloqs.arithmetic import Add
    from qualtran.bloqs.data_loading.qrom import QROM
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    tab = np.array(tab_key, dtype=np.int64)
    if controlled:
        tab = np.stack([np.zeros_like(tab), tab])
    q = get_cost_value(QROM.build_from_data(tab, target_bitsizes=(b,)), QECGatesCost())
    a = get_cost_value(Add(QUInt(b), QUInt(b)), QECGatesCost())
    return int(2 * q.and_bloq + a.and_bloq), int(2 * q.toffoli + a.toffoli), int(2 * q.t + a.t)


@lru_cache(maxsize=None)
def _windowed_pair_cost(nx: int, nz: int, b: int, controlled: bool, structural_zeros: bool = True) -> Tuple[int, int, int]:
    """Price of ONE `nx x nz` window pair for a GENERIC table: entries distinct and non-zero except the structural
    zeros of a phase-product window (row a = 0 and column c = 0, where X or Z is 0), which is exactly the table
    of any non-dyadic angle with a denominator far beyond 2^b (Qualtran's QROM prunes only constant contiguous
    ranges). `structural_zeros=False` is the full-entropy upper bound. Width-independent; used by
    `choose_window`, the stop-rule currency and the generic-angle rows of the catalogue."""
    n = 1 << (nx + nz)
    vals = [(37 * k + 11) % ((1 << b) - 1) + 1 for k in range(n)]
    tab = [list(vals[a * (1 << nz):(a + 1) * (1 << nz)]) for a in range(1 << nx)]
    if structural_zeros:
        for c in range(1 << nz):
            tab[0][c] = 0
        for a in range(1 << nx):
            tab[a][0] = 0
    return _qrom_add_cost(tuple(tuple(r) for r in tab), b, controlled)


def windowed_realization(phi_num: int, phi_den: int, x_width: int, z_width: int, b: int, wx: int, wz: int, *,
                         controlled: bool = False, and_pair: AndPair = AndPair(), generic: bool = False,
                         drop_rule: bool = True) -> GridRealization:
    """The windowed lowering of one leaf grid: per non-trivial window pair 2 QROM + 1 Add into the shared
    gradient; exact when every cell of the pair is dyadic with s <= b, else one rounding per pair."""
    pairs = window_pairs(x_width, z_width, wx, wz)
    nontrivial = exact_pairs = 0
    and_pairs = toff = t_direct = 0
    err = 0.0
    shapes: Counter = Counter()
    if generic or is_generic_angle(phi_num, phi_den, b):
        # every pair non-trivial, every table generic: structural witness per shape, one rounding per pair
        for ox, nx, oz, nz in pairs:
            a, tf, td = _windowed_pair_cost(nx, nz, b, controlled)
            and_pairs += a
            toff += tf
            t_direct += td
            shapes[(nx, nz)] += 1
        nontrivial = len(pairs)
        err = nontrivial * TWO_PI * 2.0 ** -(b + 1)
        t = and_pairs * and_pair.t + toff * and_pair.t + t_direct
        t_u = and_pairs * 8 + toff * 8 + t_direct
        return GridRealization("windowed", x_width, z_width, controlled, b, x_width * z_width,
                               {"pairs": len(pairs), "nontrivial": nontrivial, "exact_pairs": 0}, t, t_u, err, False,
                               {"window": [wx, wz], "shapes": {f"{k[0]}x{k[1]}": v for k, v in shapes.items()},
                                "and_pairs": and_pairs, "toffolis": toff, "t_direct": t_direct,
                                "price_basis": "generic-angle witness (structural zeros)"},
                               and_pairs * 4 + toff * 4 + t_direct, {"gradient_rounding": err, "pruning": 0.0})
    err_prune = 0.0
    for ox, nx, oz, nz in pairs:
        tab = window_table(phi_num, phi_den, x_width, z_width, ox, nx, oz, nz, b)
        if drop_rule and not any(any(row) for row in tab):
            # the drop rule at window granularity: the pair's phases are all below the rounding threshold
            wxs_ = [signed_weight(x_width, ox + k) for k in range(nx)]
            wzs_ = [signed_weight(z_width, oz + k) for k in range(nz)]
            worst_ = 0.0
            for aa in range(1 << nx):
                X = sum(wxs_[k] for k in range(nx) if (aa >> k) & 1)
                if X == 0:
                    continue
                for cc in range(1 << nz):
                    Z = sum(wzs_[k] for k in range(nz) if (cc >> k) & 1)
                    if Z == 0:
                        continue
                    num_, den_ = turn_fraction(phi_num, phi_den, X, Z)
                    if num_:
                        d_ = num_ / den_
                        worst_ = max(worst_, TWO_PI * min(d_, 1.0 - d_))
            err_prune += worst_
            continue
        nontrivial += 1
        shapes[(nx, nz)] += 1
        a, tf, td = _qrom_add_cost(tuple(tuple(row) for row in tab), b, controlled)
        and_pairs += a
        toff += tf
        t_direct += td
        exact = True
        worst = 0.0
        wxs = [signed_weight(x_width, ox + k) for k in range(nx)]
        wzs = [signed_weight(z_width, oz + k) for k in range(nz)]
        for aa in range(1 << nx):
            X = sum(wxs[k] for k in range(nx) if (aa >> k) & 1)
            if X == 0:
                continue
            for cc in range(1 << nz):
                Z = sum(wzs[k] for k in range(nz) if (cc >> k) & 1)
                if Z == 0:
                    continue
                s = cell_resolution(phi_num, phi_den, X, Z)
                if s is None or s > b:
                    exact = False
                    num, den = turn_fraction(phi_num, phi_den, X, Z)
                    d = abs(num / den - tab[aa][cc] / (1 << b)) % 1.0
                    worst = max(worst, TWO_PI * min(d, 1.0 - d))      # circular distance: a turn near 1 rounds to 0
        if exact:
            exact_pairs += 1
        else:
            err += worst
    t = and_pairs * and_pair.t + toff * and_pair.t + t_direct
    t_u = and_pairs * 8 + toff * 8 + t_direct
    total_err = err + err_prune
    return GridRealization("windowed", x_width, z_width, controlled, b, x_width * z_width,
                           {"pairs": len(pairs), "nontrivial": nontrivial, "exact_pairs": exact_pairs},
                           t, t_u, total_err, total_err == 0.0,
                           {"window": [wx, wz], "shapes": {f"{k[0]}x{k[1]}": v for k, v in shapes.items()},
                            "and_pairs": and_pairs, "toffolis": toff, "t_direct": t_direct},
                           and_pairs * 4 + toff * 4 + t_direct, {"gradient_rounding": err, "pruning": err_prune})


def choose_window(b: int, sizes: Iterable[int] = (1, 2, 3, 4), controlled: bool = False,
                  and_pair: AndPair = AndPair()) -> Dict[str, Any]:
    """Measured T per cell of one full window pair for every `wx x wz`; the cheapest is the default."""
    table = {}
    best = None
    for wx in sizes:
        for wz in sizes:
            a, tf, td = _windowed_pair_cost(wx, wz, b, controlled)
            t = a * and_pair.t + tf * and_pair.t + td
            per_cell = t / (wx * wz)
            table[f"{wx}x{wz}"] = {"t_pair": t, "t_per_cell": per_cell}
            if best is None or per_cell < best[1]:
                best = ((wx, wz), per_cell)
    return {"b": b, "controlled": controlled, "and_pair": and_pair.uncompute, "table": table,
            "best": list(best[0]), "best_t_per_cell": best[1], "gadget_t_per_cell": 4 * b + (and_pair.t - 4) * b}


# ================================================================================ gradient preparation
def prep_word_records(b: int, eps: float) -> List[Dict[str, Any]]:
    """The b words that prepare the gradient state: bit i (0 = MSB) gets H then ZPow(-1 / 2^i), which is
    Z / S-dagger / T-dagger exactly for i = 0, 1, 2 and Rz(-pi / 2^i) through pygridsynth (Ross-Selinger) for
    i >= 3, each with `gridsynth_certify.certify_angle`'s operator-norm record at `eps`. Matches Qualtran's
    `PhaseGradientState(bitsize=b)` decomposition (`PhaseGradientUnitary(exponent=-1)`: Z(pow=-2^-i))."""
    from . import gridsynth_certify as GC
    out = []
    for i in range(b):
        exponent = Fraction(-1, 1 << i)
        rec: Dict[str, Any] = {"bit": i, "zpow_exponent": [exponent.numerator, exponent.denominator]}
        if i == 0:
            rec.update(kind="exact", word=[["Z", 0]], t_count=0, eps_requested=0.0, eps_achieved=0.0)
        elif i == 1:
            rec.update(kind="exact", word=[["SDG", 0]], t_count=0, eps_requested=0.0, eps_achieved=0.0)
        elif i == 2:
            rec.update(kind="exact", word=[["TDG", 0]], t_count=1, eps_requested=0.0, eps_achieved=0.0)
        else:
            theta = math.pi * float(exponent)         # ZPow(e) = e^{i pi e / 2} Rz(pi e)
            cert = GC.certify_angle(theta, eps)
            from . import clifford_t_synthesis as CT
            _qc, gate_string, t_count = CT.gridsynth_clifford_t_for_rz(theta, eps)
            rec.update(kind="gridsynth", rz_theta=theta, gate_string=gate_string, t_count=t_count,
                       eps_requested=float(eps), eps_achieved=float(cert["eps_achieved"]),
                       global_phase_pi_over_2=float(exponent) / 2)
        out.append(rec)
    return out


# ================================================================================= pairing certificate
def _flatten(bloq):
    cb = bloq.as_composite_bloq()
    try:
        return cb.flatten()
    except Exception:
        return cb.flatten(lambda binst: True)


def _is_and(bloq) -> Optional[str]:
    from qualtran.bloqs.mcmt import And
    if isinstance(bloq, And):
        return "uncompute" if bloq.uncompute else "compute"
    return None


def pairing_certificate(bloq, *, max_instances: int = 400_000) -> Dict[str, Any]:
    """Flatten a Qualtran bloq to its primitive instances and follow every compute-And's ancilla forward
    through the connection graph until an uncompute-And takes it back (paired), it is freed (unpaired), or it
    enters a Join (merged -- counted, never silently accepted). Also classifies every Toffoli site."""
    from qualtran.bloqs.basic_gates import Toffoli
    from qualtran.bloqs.bookkeeping import Join, Free, Split
    from qualtran.bloqs.mcmt import MultiAnd
    from qualtran import Side

    cb = _flatten(bloq)
    binsts = list(cb.bloq_instances)
    if len(binsts) > max_instances:
        return {"mode": "too_large", "instances": len(binsts)}
    # successor map: (binst, reg name, idx) -> (binst, reg, idx)
    succ = {}
    for c in cb.connections:
        succ[(c.left.binst, c.left.reg.name, c.left.idx)] = (c.right.binst, c.right.reg, c.right.idx)
    counts = Counter()
    paired = unpaired = merged = 0
    multiand = 0
    for bi in binsts:
        kind = _is_and(bi.bloq)
        if kind == "compute":
            counts["and_compute"] += 1
            # follow the target soquet forward
            cur = succ.get((bi, "target", ()))
            steps = 0
            outcome = "unpaired"
            bit: Optional[int] = None                         # set while the ancilla rides inside a Join'd register
            while cur is not None and steps < len(binsts) + 1:
                nb, reg, idx = cur
                steps += 1
                b2 = getattr(nb, "bloq", None)
                if b2 is None:                                # DanglingT (right side of the composite)
                    outcome = "escaped"
                    break
                k2 = _is_and(b2)
                if bit is None and k2 == "uncompute" and reg.name == "target":
                    outcome = "paired"
                    break
                if isinstance(b2, Free):
                    outcome = "unpaired"
                    break
                if isinstance(b2, Join):
                    # bookkeeping only: remember which bit of the joined register we are, keep following
                    if bit is not None or len(idx) != 1:
                        outcome = "merged"
                        break
                    bit = int(idx[0])
                    cur = succ.get((nb, "reg", ()))
                    continue
                if isinstance(b2, Split):
                    if bit is None:
                        outcome = "merged"
                        break
                    cur = succ.get((nb, "reg", (bit,)))
                    bit = None
                    continue
                if bit is not None:
                    outcome = "merged"                        # a real bloq acted on the joined register
                    break
                if reg.side != Side.THRU:
                    outcome = "consumed"
                    break
                cur = succ.get((nb, reg.name, idx))
            if outcome == "paired":
                paired += 1
            elif outcome == "merged":
                merged += 1
            else:
                unpaired += 1
        elif kind == "uncompute":
            counts["and_uncompute"] += 1
        elif isinstance(bi.bloq, Toffoli):
            counts["toffoli"] += 1
        elif isinstance(bi.bloq, MultiAnd):
            multiand += 1
        else:
            counts[type(bi.bloq).__name__] += 1
    return {"mode": "flatten", "instances": len(binsts), "and_compute": counts["and_compute"],
            "and_uncompute": counts["and_uncompute"], "toffoli": counts["toffoli"], "multiand": multiand,
            "paired": paired, "unpaired": unpaired, "merged": merged,
            "ok": unpaired == 0 and merged == 0 and counts["and_compute"] == counts["and_uncompute"]}


# ========================================================================================== catalogue
@dataclass
class CatalogueRow:
    key: Tuple[Any, ...]          # a representative census key (phase-grid rows aggregate many keys)
    mult: int
    family: str                  # phase_grid | arithmetic | prim | clifford
    lowering: str
    t_each: int                  # at the catalogue's And-pair contract
    t_each_unitary: int          # 8 T per pair
    error_each: float
    exact: bool
    certificate: Dict[str, Any]
    contract: str
    n_keys: int = 1
    t_each_measured: int = 0     # 4 T per pair (comparison column)
    error_acc: Optional[float] = None   # aggregated rows: the mult-weighted error sum over every merged key
    error_by: Dict[str, float] = field(default_factory=dict)   # mult-weighted error by category over every merged key

    @property
    def row_hash(self) -> str:
        """Stable identity of the realisation this row stands for (family, lowering, contract, price columns, kinds)."""
        import hashlib
        kinds = self.certificate.get("kinds", {})
        basis = self.certificate.get("price_basis", "")
        key = (self.family, self.lowering, str(self.key[0]), str(self.key[2:]) if self.family == "phase_grid" else str(self.key),
               self.t_each, self.t_each_unitary, self.t_each_measured, str(sorted(kinds.items())), basis, self.contract)
        return hashlib.sha256(repr(key).encode()).hexdigest()[:16]

    @property
    def t_total(self) -> int:
        return self.t_each * self.mult

    @property
    def t_total_unitary(self) -> int:
        return self.t_each_unitary * self.mult

    @property
    def t_total_measured(self) -> int:
        return self.t_each_measured * self.mult

    @property
    def error_total(self) -> float:
        return self.error_acc if self.error_acc is not None else self.error_each * self.mult


def _qec_counts(bloq) -> Tuple[int, int, int, int, int]:
    """`(and, toffoli, t, rotation, clifford)` from the frozen tree's `QECGatesCost`. `CmpLtNW` is priced from
    ONE representative controlled adder times its multiplicity plus the comparator: its |work| adders differ
    only in the constant `N << j`, and And / Toffoli / T counts of `AddK` do not depend on the constant (only
    the Clifford load does), so the concrete cost is exact in every non-Clifford column and the catalogue does
    not pay for 3 072 distinct 5 000-bit adder decompositions at 2048 bits."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    from . import paths
    paths.setup_legacy_sys_path()
    from qualtran_backend import CmpLtNW  # noqa: E402
    if isinstance(bloq, CmpLtNW):
        from qualtran import QUInt
        from qualtran.bloqs.arithmetic import AddK, GreaterThan
        wa = bloq.width
        add = get_cost_value(AddK(QUInt(wa), k=bloq.N).controlled(), QECGatesCost())
        cmp_ = get_cost_value(GreaterThan(wa, wa), QECGatesCost())
        m = 2 * bloq.m
        return (int(m * add.and_bloq + cmp_.and_bloq), int(m * add.toffoli + cmp_.toffoli), int(m * add.t + cmp_.t),
                int(m * add.rotation + cmp_.rotation), int(m * add.clifford + cmp_.clifford))
    c = get_cost_value(bloq, QECGatesCost())
    return int(c.and_bloq), int(c.toffoli), int(c.t), int(c.rotation), int(c.clifford)


def _witness(bloq):
    """A small instance of the same bloq class (widths shrunk) for a flatten-level pairing check when the
    real instance is too large to flatten."""
    from . import paths
    paths.setup_legacy_sys_path()
    from forshor_qualtran_lowgate import ForShorAddScaled, ForShorNegate, ForShorShiftL, ForShorShiftR  # noqa
    from qualtran_backend import CmpLtNW  # noqa
    from qualtran.bloqs.arithmetic import AddK, GreaterThanConstant
    from qualtran import QUInt
    from qualtran.bloqs.mcmt.controlled_via_and import ControlledViaAnd
    from qualtran import Controlled
    if isinstance(bloq, CmpLtNW):
        return CmpLtNW(3, 3, 5)
    if isinstance(bloq, GreaterThanConstant):
        return GreaterThanConstant(6, 21)
    if isinstance(bloq, (Controlled, ControlledViaAnd)):
        sub = bloq.subbloq
        if isinstance(sub, AddK):
            return type(bloq)(AddK(QUInt(6), k=(1 if sub.k > 0 else -1) * 21), bloq.ctrl_spec) \
                if hasattr(bloq, "ctrl_spec") else AddK(QUInt(6), k=-21).controlled()
    if isinstance(bloq, ForShorAddScaled):
        return ForShorAddScaled(6, 4, bloq.neg_src, min(int(bloq.shift), 2))
    if isinstance(bloq, ForShorNegate):
        return ForShorNegate(6)
    if isinstance(bloq, ForShorShiftL):
        return ForShorShiftL(6, min(int(bloq.shift), 2))
    if isinstance(bloq, ForShorShiftR):
        return ForShorShiftR(6, min(int(bloq.shift), 2))
    return None


def build_catalogue(census: Iterable[Tuple[Tuple[Any, ...], int]], *, b: int, N: Optional[int], nd: Optional[int],
                    lowering: str = "gadget", window: Tuple[int, int] = (2, 2), small_exact: bool = True,
                    and_pair: AndPair = AndPair(), flatten_limit: int = 400, drop_rule: bool = True) -> List[CatalogueRow]:
    """One row per distinct census key. Phase grids are priced analytically from the cell classification
    (`gadget`) or the measured window-pair cost (`windowed`); arithmetic leaves and prims are the SAME
    Qualtran bloqs `family._bloqs_for_leaf` prices, with a flatten-level pairing certificate when the instance
    is small enough and a same-class witness otherwise (the row says which). `flatten_limit` is in QEC leaf
    gates: Qualtran's `CompositeBloq.flatten` is quadratic in soquet hashing (a 400-bit adder takes ~35 s), so
    the default keeps the flattened certificate to narrow instances and lets the witness carry the wide ones."""
    from . import family as F
    rows: List[CatalogueRow] = []
    cert_cache: Dict[Any, Dict[str, Any]] = {}
    grid_rows: Dict[Tuple[Any, ...], CatalogueRow] = {}
    generic_cache: Dict[Tuple[bool, int, int], GridRealization] = {}
    for key, mult in census:
        key = tuple(key)
        tag = key[0]
        if tag in ("Naive_SignedPhaseProd", "Naive_CSignedPhaseProd"):
            (num, den), wx, wz = key[1], key[2], key[3]
            num, den = int(num), int(den)
            ctrl = tag == "Naive_CSignedPhaseProd"
            generic = is_generic_angle(num, den, b)
            if lowering == "windowed":
                if generic:
                    gk = (ctrl, wx, wz)
                    if gk not in generic_cache:
                        generic_cache[gk] = windowed_realization(num, den, wx, wz, b, window[0], window[1], controlled=ctrl,
                                                                 and_pair=and_pair, generic=True)
                    g = generic_cache[gk]
                else:
                    g = windowed_realization(num, den, wx, wz, b, window[0], window[1], controlled=ctrl, and_pair=and_pair,
                                             drop_rule=drop_rule)
                contract = (f"windowed {window[0]}x{window[1]}: per non-trivial pair QROM(T_b table) -> Add(b) into the shared "
                            f"gradient -> QROM; exact iff every cell has s <= b, else one rounding per pair (<= 2pi 2^-(b+1))")
            else:
                if generic:
                    gk = (ctrl, wx, wz)
                    if gk not in generic_cache:
                        generic_cache[gk] = generic_grid_bound(wx, wz, b, controlled=ctrl, and_pair=and_pair)
                    g = generic_cache[gk]
                else:
                    g = grid_realization(num, den, wx, wz, b, controlled=ctrl, small_exact=small_exact, and_pair=and_pair,
                                         drop_rule=drop_rule)
                contract = ("per cell: identity | CZ | CS | CT (exact words) | And -> Add(s) into the shared gradient -> And-dagger; "
                            "non-dyadic or s > b rounded once to b bits (<= 2pi 2^-(b+1)); rounded-zero cells elided; "
                            "controlled cells wrap one more And pair")
            sig = (g.lowering, ctrl, wx, wz, tuple(sorted(g.kinds.items())), g.t, g.t_unitary, g.exact,
                   g.detail.get("price_basis", "exact per cell"))
            row = grid_rows.get(sig)
            if row is None:
                row = CatalogueRow(key, 0, "phase_grid", g.lowering, g.t, g.t_unitary, g.error, g.exact,
                                   {"cells": g.cells, "kinds": g.kinds, **g.detail}, contract, 0, g.t_measured, 0.0)
                grid_rows[sig] = row
                rows.append(row)
            row.mult += mult
            row.n_keys += 1
            row.error_acc += g.error * mult          # merged keys share price and kinds, not their error bound
            for cat, e in g.error_by.items():
                row.error_by[cat] = row.error_by.get(cat, 0.0) + e * mult
            continue
        if tag in ("H", "X"):
            rows.append(CatalogueRow(key, mult, "clifford", "native", 0, 0, 0.0, True, {}, "Clifford, exact"))
            continue
        if tag == "RadixReverse":
            w, m = key[1], key[2]
            swaps = 0 if m == 0 or m == w else m * (w - 1)
            rows.append(CatalogueRow(key, mult, "clifford", "swaps", 0, 0, 0.0, True, {"swaps": swaps}, "SWAP network, exact"))
            continue
        bloqs = F._bloqs_for_leaf(key, 1, N, nd)
        if not bloqs:
            rows.append(CatalogueRow(key, mult, "clifford", "none", 0, 0, 0.0, True, {}, "no gates"))
            continue
        t_each = t_each_u = t_each_m = 0
        cert: Dict[str, Any] = {"bloqs": []}
        ok = True
        for bloq, m_ in bloqs.items():
            a, tf, td, rot, cl = _qec_counts(bloq)
            if rot:
                raise ValueError(f"leaf {key} prices a rotation; arithmetic leaves must be Clifford+T exact")
            t_each += m_ * (a * and_pair.t + tf * and_pair.t + td)
            t_each_u += m_ * (a * 8 + tf * 8 + td)
            t_each_m += m_ * (a * 4 + tf * 4 + td)
            ck = repr(bloq)
            if ck not in cert_cache:
                leaves = a + tf + td + cl
                if leaves <= flatten_limit:
                    c = pairing_certificate(bloq)
                    c["scope"] = "instance"
                else:
                    wb = _witness(bloq)
                    c = pairing_certificate(wb) if wb is not None else {"mode": "none", "ok": False}
                    c["scope"] = f"witness:{wb!r}" if wb is not None else "none"
                c["qec"] = {"and": a, "toffoli": tf, "t": td, "clifford": cl}
                cert_cache[ck] = c
            c = cert_cache[ck]
            ok = ok and bool(c.get("ok"))
            cert["bloqs"].append({"bloq": str(bloq), "mult": m_, **c})
        cert["ok"] = ok
        fam = "prim" if tag == "Prim" else "arithmetic"
        rows.append(CatalogueRow(key, mult, fam, "qualtran", t_each, t_each_u, 0.0, True, cert,
                                 "ripple arithmetic from Qualtran And ladders; every And paired in the definition; exact",
                                 1, t_each_m))
    return rows


def catalogue_summary(rows: Sequence[CatalogueRow]) -> Dict[str, Any]:
    t = sum(r.t_total for r in rows)
    tu = sum(r.t_total_unitary for r in rows)
    tm = sum(r.t_total_measured for r in rows)
    err = sum(r.error_total for r in rows)
    fam: Counter = Counter()
    for r in rows:
        fam[r.family] += r.t_total
    unpaired = sum(1 for r in rows if r.family in ("arithmetic", "prim") and not r.certificate.get("ok"))
    return {"rows": len(rows), "keys": sum(r.n_keys for r in rows), "leaves": sum(r.mult for r in rows),
            "t": t, "t_measured_and": tm, "t_unitary_and": tu,
            "error_bound": err, "t_by_family": dict(fam), "rows_without_pairing": unpaired,
            "generic_rows": sum(1 for r in rows if "generic" in str(r.certificate.get("price_basis", ""))),
            "phase_grid_kinds": dict(sum((Counter({k: v * r.mult for k, v in r.certificate.get("kinds", {}).items()})
                                          for r in rows if r.family == "phase_grid"), Counter()))}


def rows_to_json(rows: Sequence[CatalogueRow]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        d = asdict(r)
        d["key"] = [list(v) if isinstance(v, tuple) else v for v in r.key]
        d["t_total"] = r.t_total
        d["t_total_unitary"] = r.t_total_unitary
        d["t_total_measured"] = r.t_total_measured
        d["error_total"] = r.error_total
        d["row_hash"] = r.row_hash
        d["error_total"] = r.error_total
        out.append(d)
    return out


def _main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="T3 leaf catalogue from a census JSON (program.census items)")
    ap.add_argument("census")
    ap.add_argument("--b", type=int, default=40)
    ap.add_argument("--lowering", choices=["gadget", "windowed"], default="gadget")
    ap.add_argument("--window", type=int, nargs=2, default=(2, 2))
    ap.add_argument("--and-uncompute", choices=["measured", "unitary"], default="measured")
    ap.add_argument("--N", type=int, default=None)
    ap.add_argument("--nd", type=int, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    doc = json.load(open(a.census, encoding="utf-8"))
    items = doc["template_census"] if isinstance(doc, dict) else doc
    items = [(tuple(tuple(v) if isinstance(v, list) else v for v in k), m) for k, m in items]
    rows = build_catalogue(items, b=a.b, N=a.N, nd=a.nd, lowering=a.lowering, window=tuple(a.window),
                           and_pair=AndPair(a.and_uncompute))
    summ = catalogue_summary(rows)
    print(json.dumps(summ, indent=1))
    if a.out:
        json.dump({"summary": summ, "rows": rows_to_json(rows),
                   "options": {"b": a.b, "lowering": a.lowering, "window": list(a.window), "and_uncompute": a.and_uncompute}},
                  open(a.out, "w", encoding="utf-8"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
