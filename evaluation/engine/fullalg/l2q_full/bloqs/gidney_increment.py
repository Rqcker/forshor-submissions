r"""Gidney's n-bit increment gate with ONE borrowed ancilla, built as an explicit gate list.

Source: Craig Gidney, "Constructing Large Increment Gates", 2015
(https://algassert.com/circuits/2015/06/12/Constructing-Large-Increment-Gates.html), and Part 1,
"Constructing Large Controlled Nots", for the multi-controlled NOT (Barenco et al. 1995, Lemmas 7.2/7.3).
The post's figures are images, so the circuit is reconstructed from its text; every claim below is
checked by simulation (`tests/test_gidney_increment.py`), not taken on trust.

What it is
----------
`increment_gates(n)` is a list of NOT / CNOT / Toffoli gates on wires ``0..n-1`` (the register, LSB first)
and wire ``n`` (the single ancilla) that maps ``x -> x + 1 (mod 2^n)`` and leaves the ancilla unchanged.
The ancilla is BORROWED: it may be in any state, including entangled, and is returned exactly. A clean
ancilla is a special case, so charging "one clean qubit" for it is valid (and conservative). No other
wire is used: every sub-circuit borrows the register's own bits.

Construction (all from the post's text)
---------------------------------------
* ``_mcx``: n-controlled NOT with borrowed ancillae. With at least k-2 of them, Barenco's ladder (4(k-2)
  Toffoli); with fewer, split the controls in two around one borrowed bit (4 smaller MCX).
* ``_inc_borrowed``: an m-bit increment given m borrowed bits (m-1 of them a "garbage value" ``g``, one a
  carry ``c``). Subtract ``g`` and ``c`` from the target with a (reversed) Cuccaro ripple adder, complement
  ``g``, subtract again, restore ``g``, flip the target's top bit, and conditionally (on ``c``) complement
  the target before and after. Net effect ``v -> v + 1`` for either value of ``c``; ``g`` and ``c`` return.
* ``_inc``: an m-bit increment with fewer borrowed bits than m. Split the target into a low half L and a
  high half H. Controlled on the (borrowed) ancilla ``a``, apply an increment and a decrement to H, with a
  "toggle" of ``a`` and H whenever L is all ones in between, so H is incremented exactly once iff L is all
  ones; then increment L. The toggle spreads one MCX onto all of H with CNOT pairs (toggle detection).

Gate counts are read off the generated list (`counts`), never from a formula.
"""
from __future__ import annotations

import random
from collections import Counter
from functools import lru_cache
from typing import Dict, List, Sequence, Tuple

Gate = Tuple  # ("X", t) | ("CX", c, t) | ("CCX", c1, c2, t)


# ---------------------------------------------------------------------------- gate-list helpers
def _X(g: List[Gate], t: int) -> None:
    g.append(("X", t))


def _CX(g: List[Gate], c: int, t: int) -> None:
    g.append(("CX", c, t))


def _CCX(g: List[Gate], a: int, b: int, t: int) -> None:
    g.append(("CCX", a, b, t))


# ---------------------------------------------------------------------------- multi-controlled NOT
def _mcx(g: List[Gate], controls: Sequence[int], target: int, borrow: Sequence[int]) -> None:
    """`target ^= AND(controls)`, using ``borrow`` as dirty (restored) ancillae."""
    k = len(controls)
    assert target not in controls and not (set(borrow) & (set(controls) | {target}))
    if k == 0:
        _X(g, target)
    elif k == 1:
        _CX(g, controls[0], target)
    elif k == 2:
        _CCX(g, controls[0], controls[1], target)
    elif len(borrow) >= k - 2:                       # Barenco Lemma 7.2: 4(k-2) Toffoli
        c, a = list(controls), list(borrow[:k - 2])
        up = lambda: [_CCX(g, c[i + 1], a[i - 1], a[i]) for i in range(1, k - 2)]
        down = lambda: [_CCX(g, c[i + 1], a[i - 1], a[i]) for i in range(k - 3, 0, -1)]
        _CCX(g, c[k - 1], a[k - 3], target)
        down()
        _CCX(g, c[0], c[1], a[0])
        up()
        _CCX(g, c[k - 1], a[k - 3], target)
        down()
        _CCX(g, c[0], c[1], a[0])
        up()
    else:                                            # Barenco Lemma 7.3: split around one borrowed bit
        assert len(borrow) >= 1, "mcx needs at least one borrowed bit"
        a, rest = borrow[0], list(borrow[1:])
        m = (k + 1) // 2
        c1, c2 = list(controls[:m]), list(controls[m:])
        for _ in range(2):
            _mcx(g, c1, a, c2 + [target] + rest)
            _mcx(g, c2 + [a], target, c1 + rest)


# ---------------------------------------------------------------------------- Cuccaro adder
def _cuccaro_add(g: List[Gate], c: int, a: Sequence[int], b: Sequence[int]) -> None:
    """`b <- b + a + c` (mod 2^len(b)), ``len(a) == len(b) - 1``; ``a`` and ``c`` are returned unchanged.
    The top bit of ``b`` receives the carry out (Cuccaro, Draper, Kutin, Moulton, quant-ph/0410184)."""
    n = len(a)
    assert len(b) == n + 1
    chain = [c] + list(a)                            # chain[i] is the carry wire into bit i

    def maj(x, y, z):
        _CX(g, z, y)
        _CX(g, z, x)
        _CCX(g, x, y, z)

    def uma(x, y, z):
        _CCX(g, x, y, z)
        _CX(g, z, x)
        _CX(g, x, y)

    for i in range(n):
        maj(chain[i], b[i], a[i])
    _CX(g, a[n - 1], b[n])
    for i in reversed(range(n)):
        uma(chain[i], b[i], a[i])


def _cuccaro_sub(g: List[Gate], c: int, a: Sequence[int], b: Sequence[int]) -> None:
    """`b <- b - a - c`: the adder's gates in reverse order (every gate is its own inverse)."""
    tmp: List[Gate] = []
    _cuccaro_add(tmp, c, a, b)
    g.extend(reversed(tmp))


# ---------------------------------------------------------------------------- increment, m borrowed bits
def _inc_borrowed(g: List[Gate], v: Sequence[int], borrow: Sequence[int]) -> None:
    """`v += 1` given at least len(v) borrowed bits: ``g`` (len(v)-1 of them) and carry ``c`` (one)."""
    m = len(v)
    assert len(borrow) >= m
    gg, c = list(borrow[:m - 1]), borrow[m - 1]
    for t in v:
        _CX(g, c, t)
    _cuccaro_sub(g, c, gg, v)
    for w in gg:
        _X(g, w)
    _cuccaro_sub(g, c, gg, v)
    for w in gg:
        _X(g, w)
    _X(g, v[m - 1])
    for t in v:
        _CX(g, c, t)


# ---------------------------------------------------------------------------- increment, any borrow count
def _inc(g: List[Gate], v: Sequence[int], borrow: Sequence[int]) -> None:
    """`v += 1 (mod 2^len(v))`, borrowing from ``borrow`` (dirty, restored); needs len(borrow) >= 1 once len(v) > 3."""
    m = len(v)
    assert not (set(v) & set(borrow))
    if m <= 3:                                       # tiny: top bit first, each flips iff all lower bits are 1
        for i in range(m - 1, -1, -1):
            _mcx(g, list(v[:i]), v[i], borrow)
        return
    if len(borrow) >= m:
        _inc_borrowed(g, v, borrow)
        return
    if len(borrow) >= m - 1:                         # peel the one MCX that touches every bit
        _mcx(g, list(v[:m - 1]), v[m - 1], borrow)
        _inc(g, v[:m - 1], list(borrow) + [v[m - 1]])
        return
    assert len(borrow) >= 1, "increment needs at least one borrowed bit"
    a, rest = borrow[0], list(borrow[1:])
    h = (m - 1) // 2
    low, high = list(v[:m - h]), list(v[m - h:])

    def cinc():                                      # high += a  (a is the new lowest bit, then un-flip it)
        _inc(g, [a] + high, low + rest)
        _X(g, a)

    def toggle():                                    # if AND(low): flip a and every bit of high
        for t in high:
            _CX(g, a, t)
        _mcx(g, low, a, high + rest)
        for t in high:
            _CX(g, a, t)

    cinc()
    toggle()
    for t in high:                                   # controlled decrement = complement, increment, complement
        _X(g, t)
    cinc()
    for t in high:
        _X(g, t)
    toggle()
    _inc(g, low, high + [a] + rest)


@lru_cache(maxsize=None)
def increment_gates(n: int) -> Tuple[Gate, ...]:
    """The n-bit increment on wires 0..n-1 with the borrowed ancilla on wire n."""
    assert n >= 1
    g: List[Gate] = []
    _inc(g, list(range(n)), [n])
    return tuple(g)


@lru_cache(maxsize=None)
def negate_gates(n: int) -> Tuple[Gate, ...]:
    """`x -> -x (mod 2^n)` as `n` NOTs (the bitwise complement) then the increment; ancilla on wire n."""
    return tuple(("X", i) for i in range(n)) + increment_gates(n)


# ---------------------------------------------------------------------------- exact counts
def exact_counts(n: int, negate: bool = False) -> Dict[str, int]:
    """`x`, `cnot`, `toffoli` of the circuit (the increment, or the negation = `n` NOTs + increment).

    Below n = 7 the gate list is generated. From n = 7 the closed forms below are used; they are NOT assumed:
    `tests/test_gidney_increment.py` compares them with the generated gate list for every n in 7..600 and at
    n = 1000, 2048, 2049, 4096, 4398 (and this was checked for every n up to 1600 when they were fitted).

        n even:  toffoli 12n - 28   cnot 17n - 22   x 4n - 3
        n odd :  toffoli 10n - 18   cnot 17n - 5    x 4n + 1        (negation: x + n)
    """
    if n < 7:
        c = counts(negate_gates(n) if negate else increment_gates(n))
        return {"x": c["x"], "cnot": c["cnot"], "toffoli": c["toffoli"]}
    even = n % 2 == 0
    return {"x": (4 * n - 3 if even else 4 * n + 1) + (n if negate else 0),
            "cnot": 17 * n - 22 if even else 17 * n - 5,
            "toffoli": 12 * n - 28 if even else 10 * n - 18}


def bound_counts(n: int) -> Dict[str, int]:
    """ForShor's `negateResources` charge for a `n`-bit negation (Lean: `negateXGates`, `negateCnotGates`,
    `negateToffoliGates`): the closed forms of :func:`exact_counts` applied at EVERY width with Lean's truncating
    subtraction. Equal to the circuit's real counts for n >= 7 and a valid upper bound below (the tests check both)."""
    t = lambda a, b: max(0, a - b)
    even = n % 2 == 0
    return {"x": t(5 * n, 3) if even else 5 * n + 1,
            "cnot": t(17 * n, 22) if even else t(17 * n, 5),
            "toffoli": t(12 * n, 28) if even else t(10 * n, 18)}


# ---------------------------------------------------------------------------- simulation and counts
def counts(gates: Sequence[Gate]) -> Dict[str, int]:
    c = Counter(t[0] for t in gates)
    return {"x": c["X"], "cnot": c["CX"], "toffoli": c["CCX"], "total": len(gates)}


def wires_used(gates: Sequence[Gate]) -> set:
    return {w for t in gates for w in t[1:]}


def simulate(gates: Sequence[Gate], state: List[int], mask: int) -> List[int]:
    """Bit-sliced classical simulation: ``state[w]`` holds wire ``w`` across many test vectors at once."""
    s = list(state)
    for t in gates:
        if t[0] == "X":
            s[t[1]] ^= mask
        elif t[0] == "CX":
            s[t[2]] ^= s[t[1]]
        else:
            s[t[3]] ^= s[t[1]] & s[t[2]]
    return s


def check(gates: Sequence[Gate], n: int, op: str, vectors: int = 0, seed: int = 0) -> int:
    """Run the circuit on test vectors and compare with `x+1` / `-x`. Exhaustive over the register AND the
    ancilla when ``vectors == 0`` (n <= 14), else ``vectors`` random ones plus the edge cases. Returns the
    number of vectors checked; raises AssertionError on any mismatch or any ancilla not restored."""
    f = (lambda x: (x + 1) % (1 << n)) if op == "inc" else (lambda x: (-x) % (1 << n))
    if vectors == 0:
        assert n <= 14
        xs = [j & ((1 << n) - 1) for j in range(1 << (n + 1))]
        an = [j >> n for j in range(1 << (n + 1))]
    else:
        r = random.Random(seed)
        edge = [0, 1, (1 << n) - 1, (1 << n) - 2, 1 << (n - 1), (1 << (n - 1)) - 1]
        xs = [e % (1 << n) for e in edge] + [r.getrandbits(n) for _ in range(vectors)]
        xs += [(1 << r.randrange(1, n + 1)) - 1 for _ in range(vectors // 4)]       # long runs of 1s: long carries
        an = [r.getrandbits(1) for _ in xs]
        an[: len(edge) * 2] = [0, 1] * len(edge)
    B = len(xs)
    mask = (1 << B) - 1
    state = [0] * (n + 1)
    for j, (x, a) in enumerate(zip(xs, an)):
        for i in range(n):
            state[i] |= ((x >> i) & 1) << j
        state[n] |= a << j
    out = simulate(gates, state, mask)
    for j, (x, a) in enumerate(zip(xs, an)):
        got = sum(((out[i] >> j) & 1) << i for i in range(n))
        assert got == f(x), f"n={n} x={x} anc={a}: got {got}, want {f(x)}"
        assert (out[n] >> j) & 1 == a, f"n={n} x={x}: ancilla not restored"
    return B
