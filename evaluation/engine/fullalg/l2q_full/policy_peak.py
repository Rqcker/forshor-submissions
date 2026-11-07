"""The live-qubit peak of the order-finding circuit under a policy.

A port of ForShor's `Implementation/QubitCount/Statement.lean`: `peakReserveD`, `qftPeakD`, `splitPeakOf`, `sitePeakU`,
`modMulPhasePeaksWith` and `orderFindingPeakUD` (the unconditionally balanced circuit, from the register widths alone), plus the
register sum of `Policy.orderFindingQubitsU`. This is the number of qubits live at the circuit's PEAK. It is different from the
static layout allocation (`policy_shor_ir.reference_layout`), which sizes every register's reserve for its own worst step and does
not include a negation's transient scratch.

The negation matters here and only here. ``impl`` is the negation subroutine, as in `cost_conventions.NEGATE_IMPLS`:

* ``legacy``: a `w`-bit constant-one register plus a Cuccaro adder. A table that negates charges `W + 1` scratch per level, and
  Step 3's constant comparison on the scratch register charges `sW + 2` (`sW` = 4398 at n = 2048, so 4,400).
* ``gidney_circuit`` (the default) and ``incrementer`` (the superseded 32 w-Toffoli reading of the same circuit): complement plus
  Gidney's one-clean-ancilla increment gate (ForShor's `oneQubit` NegationMethod). A table that negates charges 1, and Step 3
  charges 3. The two differ in T (see `cost_conventions.negate_resources`), never in qubits, so the peak does not tell them apart.

Natural-number subtraction truncates at 0, as in Lean. The balanced plan is `Balanced.defaultRule`'s, copied from L2Q's
`policy_shor_ir.balanced_default_plan` (the owner's port, cross-checked there against Lean).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional, Tuple

from . import policy as pol

IMPLS = ("legacy", "incrementer", "gidney_circuit")


def _sub(a: int, b: int) -> int:
    return a - b if a > b else 0


def default_plan(L: int, s: int) -> Optional[Tuple[Tuple[Tuple[int, int], ...], int]]:
    """`Balanced.defaultPlan L s`: `(lower blocks [(w, t), ...], top)` or None. `L` is the big width, `s` the small."""
    if s < 2 or L <= s:
        return None
    if 2 * L <= 3 * s:                                  # pad the small operand to L, no lower block
        return (), L
    B = (L + s - 1) // s
    w = s - 1
    top = _sub(L, (B - 1) * w)
    t = max(s, top)
    return tuple((w, t) for _ in range(B - 1)), t


@lru_cache(maxsize=1 << 12)
def _flags_of(ops_frozen) -> Tuple[bool, bool]:
    names = {dict(o).get("op") for o in ops_frozen}
    return "negate" in names, "addScaled" in names


def _flags(T) -> Tuple[bool, bool]:
    """`(negates, adds)` of a table: `TableData.negates` and `TableData.adds`."""
    return _flags_of(T.ops_frozen)


def table_scratch(T, W: int, impl: str) -> int:
    """`TableData.scratch`: the largest scratch a table's arithmetic charges on chunks of width `W`."""
    negates, adds = _flags(T)
    if negates:
        return W + 1 if impl == "legacy" else 1
    return 1 if adds else 0


@lru_cache(maxsize=1 << 16)
def peak_reserve(P: pol.PhasePolicy, wx: int, wz: int, impl: str) -> int:
    """`peakReserveD`: peak extra qubits of one lowered signed phase product on operands of widths `wx` and `wz`. A recursive level
    grows its `k` chunk pairs to the next width `W` (activating `k*W - wx` and `k*W - wz` qubits), then holds the larger of one child's
    peak and its arithmetic's scratch. The chain stops where the policy has no table or the width stops shrinking."""
    n = max(wx, wz)
    T = P.choose(n)
    if T is None:
        return 0
    W = T.next_width(wx, wz)
    if W < n:
        return _sub(T.k * W, wx) + _sub(T.k * W, wz) + max(peak_reserve(P, W, W, impl), table_scratch(T, W, impl))
    return 0


def _qft_split_peak(pp, m: int) -> int:
    return 2 + pp(m // 2 + 1, m - m // 2 + 1) if 2 <= m else 0


def qft_peak(P: pol.PhasePolicy, n: int, impl: str) -> int:
    """`qftPeakD`: peak extra qubits of the lowered QFT on `n` qubits (the largest split down its halving tree)."""
    def pp(a: int, b: int) -> int:
        return peak_reserve(P, a, b, impl)
    best, lo, hi = 0, n, n
    for _ in range(n + 1):                              # Lean's fuel `n + 1`
        best = max(best, _qft_split_peak(pp, lo), _qft_split_peak(pp, hi))
        if hi < 2:
            break
        lo, hi = lo // 2, (hi + 1) // 2
    return best


def _split_peak(pp, lower, top: int, wx: int, wz: int) -> int:
    """`splitPeakOf`: peak extra qubits of a balanced split."""
    best = 0
    for bw, bt in lower:
        best = max(best, _sub(bt, bw) + _sub(bt, wz) + pp(bt, bt))
        wx = _sub(wx, bw)
    return max(best, _sub(top, wx) + _sub(top, wz) + pp(top, top))


def site_peak(P: pol.PhasePolicy, wx: int, wz: int, impl: str) -> int:
    """`sitePeakU`: peak extra qubits of a balanced phase-product site, from the widths alone (the split wherever the rule has a
    plan, the plain product otherwise)."""
    def pp(a: int, b: int) -> int:
        return peak_reserve(P, a, b, impl)
    if wz < wx:
        plan = default_plan(wx, wz)
        return pp(wx, wz) if plan is None else _split_peak(pp, plan[0], plan[1], wx, wz)
    if wx < wz:
        plan = default_plan(wz, wx)
        return pp(wx, wz) if plan is None else _split_peak(pp, plan[0], plan[1], wz, wx)
    return pp(wx, wz)


def modmul_phase_peaks(P: pol.PhasePolicy, dW: int, wW: int, sW: int, impl: str):
    """`modMulPhasePeaksWith`: the eight phases of one controlled modular multiplication."""
    def pp(a: int, b: int) -> int:
        return site_peak(P, a, b, impl)
    def qp(n: int) -> int:
        return qft_peak(P, n, impl)
    return [2 + pp(dW + 1, wW + 1), qp(wW), qp(dW + 1), 2 + pp(wW + 1, dW + 2),
            sW + 2 if impl == "legacy" else 3,     # Step 3's constant comparison and subtraction on the scratch register
            qp(sW), 2 + pp(wW + 1, sW + 1), 2 + pp(dW + 2, wW + 1)]


def order_finding_peak(P: pol.PhasePolicy, xW: int, dW: int, wW: int, sW: int, impl: str = "gidney_circuit") -> int:
    """`orderFindingPeakUD`: peak extra qubits of the unconditionally balanced order-finding circuit."""
    if impl not in IMPLS:
        raise ValueError(f"impl {impl!r} not in {IMPLS}")
    mm = max(modmul_phase_peaks(P, dW, wW, sW, impl)) if 0 < xW else 0
    return max(mm, qft_peak(P, xW, impl))


def order_finding_qubits(P: pol.PhasePolicy, xW: int, dW: int, wW: int, sW: int, impl: str = "gidney_circuit") -> int:
    """`Policy.orderFindingQubitsU`: the exponent, data (with its carry bit), work and scratch registers and the flag, plus the peak."""
    return xW + (dW + 1) + wW + sW + 1 + order_finding_peak(P, xW, dW, wW, sW, impl)


# ---- one product on its own: PhaseProduct (two operands) and PhaseTripleProduct (three) ----------------------------------------

def chain_peak_extra(levels, operands: int, impl: str = "gidney_circuit") -> int:
    """The extra qubits, over the operand registers, at the live peak of ONE lowered product that follows a chain of levels.

    ``levels`` is the chain from the top, one ``(width, k, child_width, negates, adds)`` per recursive level; the product ends in a
    leaf below the last. ``operands`` is 2 for a PhaseProduct and 3 for a PhaseTripleProduct. This is `peak_reserve`'s recursion for
    a square product, and for three operands the shape of ForShor's `ptpWsTD` (PhaseTripleProduct/Proofs/QubitsTight.lean): a level
    grows each of its operands from `width` to `k` chunks of `child_width` (activating `operands * (k*child - width)` qubits) and
    then holds the larger of one child's peak and its arithmetic's scratch, the children sharing one pool. The leaf of a
    PhaseTripleProduct needs its sandwich's two ancillas (`ptpWsTD`'s base case); a PhaseProduct's leaf needs none.

    The scratch is `table_scratch`'s: 1 for a table that adds or negates under the incrementer, `child_width + 1` for a negating
    table under the legacy negation. ForShor proves `ptpWsTD` without the scratch (`LowGate.usedQubits` excludes `cleanAnc`); it is
    included here, as in the order-finding peak, and under the incrementer it never exceeds the leaf's two."""
    if impl not in IMPLS:
        raise ValueError(f"impl {impl!r} not in {IMPLS}")
    if operands not in (2, 3):
        raise ValueError("a product has two operands (PhaseProduct) or three (PhaseTripleProduct)")
    tail = 2 if operands == 3 else 0
    for width, k, child, negates, adds in reversed(list(levels)):
        scratch = (child + 1 if impl == "legacy" else 1) if negates else (1 if adds else 0)
        tail = operands * _sub(k * child, width) + max(tail, scratch)
    return tail
