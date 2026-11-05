"""MergedTwiddle: a QFT twiddle exp(2 pi i x z / 2^t) as ONE catalyst addition per row (Amy §VI.C).

Rows run over the SHORTER register (call it the control register, bits i = 0..r_s-1, LSB first);
the longer one (bits j = 0..r_l-1) is the addend. Row i contributes x_i · frac(z · 2^(i-t)):
bit z_j has turn weight 2^(j+i-t), i.e. it lands on catalyst position p = depth - j with
depth = t - i (p = 1 is the half-turn bit, catalyst[0]).

    kept window      j in [j_lo, j_hi],  j_lo = max(0, depth - b),  j_hi = min(r_l - 1, depth - 1)
                     (bits with p <= 0 are whole turns: dropped EXACTLY)
    slice length     L_i = min(b, depth)          (catalyst[0:L_i]; the window's LSB sits on p = L_i)
    live bits        m_i = j_hi - j_lo + 1        (clipped at 0)
    rounding         if depth > b the bits j < j_lo are dropped; the first dropped bit
                     j_c = depth - b - 1 (if 0 <= j_c < r_l) enters as the adder's carry-in, which is
                     exactly round-half-up to b bits (round_b in tcount/test_catalysis.py). Error per
                     rounded row <= pi 2^-b (operator norm of the phase).

Each row is one `CtrlAddIntoCatalyst(L_i, m_i, carry_in)`; there are NO rotations anywhere
(QECGatesCost.rotation == 0). And count per row = m_i + [carry] + L_i - 1.

Relation to the pricing contract `tcount.catalysis._twiddle_rows(r1, r2, t, b)[0]` = sum_i (L_i + m_i)
with m_i^contract = clip(r_l - max(0, depth - b), 0, r_l):
    rounded row with a carry bit        exactly L + m          (delta 0)
    unrounded row (depth <= b)          L + m - 1              (delta -1: mod-2^L adder, no carry-in)
    row whose whole value rounds to 0   0                      (delta -b; only when depth - b > r_l,
                                                                i.e. r_s - i > b for t = r1 + r2)
    t < r1 + r2 (bits past the half-turn) m clipped at depth-1  (extra negative delta)
so `merged_twiddle_ands(...) - contract = -(#unrounded rows) - b·(#vanishing rows) (- whole-turn clip)`
<= 0 always: the contract is a (tight within r_s) upper bound. See `twiddle_and_delta`.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Dict, List, Optional, Tuple

import attrs
import numpy as np

from qualtran import Bloq, BloqBuilder, QUInt, Register, Signature, Soquet, SoquetT

from .catalyst import CtrlAddIntoCatalyst, ctrl_add_ands

__all__ = [
    "MergedTwiddle",
    "TwiddleRow",
    "twiddle_row_plan",
    "merged_twiddle_ands",
    "contract_twiddle_ands",
    "twiddle_and_delta",
    "merged_twiddle_model_turns",
]


@dataclass(frozen=True)
class TwiddleRow:
    i: int                 # control bit (LSB index in the shorter register)
    depth: int             # t - i
    L: int                 # slice length (catalyst[0:L])
    j_lo: int              # lowest kept addend bit
    m: int                 # kept addend bits j_lo .. j_lo+m-1
    carry_j: Optional[int]  # addend bit fed to the carry-in (rounding), or None
    rounded: bool          # depth > b

    @property
    def active(self) -> bool:
        return self.m > 0 or self.carry_j is not None

    @property
    def ands(self) -> int:
        return ctrl_add_ands(self.L, self.m, self.carry_j is not None) if self.active else 0


def twiddle_row_plan(r1: int, r2: int, t: int, b: int) -> List[TwiddleRow]:
    """The realised rows (over the shorter register) of MergedTwiddle(r1, r2, t, b)."""
    rs, rl = (r1, r2) if r1 <= r2 else (r2, r1)
    rows = []
    for i in range(rs):
        depth = t - i
        if depth <= 0:
            rows.append(TwiddleRow(i, depth, 0, 0, 0, None, False))
            continue
        L = min(b, depth)
        j_lo = max(0, depth - b)
        j_hi = min(rl - 1, depth - 1)
        m = max(0, j_hi - j_lo + 1)
        jc = depth - b - 1
        carry = jc if (depth > b and 0 <= jc < rl) else None
        rows.append(TwiddleRow(i, depth, L, j_lo, m, carry, depth > b))
    return rows


def merged_twiddle_ands(r1: int, r2: int, t: int, b: int) -> int:
    """Exact QECGatesCost And count of MergedTwiddle(r1, r2, t, b)."""
    return sum(r.ands for r in twiddle_row_plan(r1, r2, t, b))


def contract_twiddle_ands(r1: int, r2: int, t: int, b: int) -> Tuple[int, int]:
    """Re-statement of tcount.catalysis._twiddle_rows (Ands, rounded rows), kept dependency-free."""
    rs, rl = (r1, r2) if r1 <= r2 else (r2, r1)
    ands = rounded = 0
    for i in range(rs):
        depth = t - i
        L = min(b, depth)
        m = min(max(rl - max(0, depth - b), 0), rl)
        ands += L + m
        rounded += depth > b
    return ands, rounded


def twiddle_and_delta(r1: int, r2: int, t: int, b: int) -> int:
    """merged_twiddle_ands - contract (<= 0 when t >= max(r1, r2))."""
    return merged_twiddle_ands(r1, r2, t, b) - contract_twiddle_ands(r1, r2, t, b)[0]


def merged_twiddle_model_turns(x: int, z: int, r1: int, r2: int, t: int, b: int) -> int:
    """The integer v (mod 2^b) that MergedTwiddle adds to the catalyst on basis state |x, z>;
    the realised phase is exp(+2 pi i v / 2^b). Equals sum_i x_i · round_b(frac(z 2^(i-t))) · 2^b."""
    ctl, add = (x, z) if r1 <= r2 else (z, x)
    v = 0
    for r in twiddle_row_plan(r1, r2, t, b):
        if not r.active or not (ctl >> r.i & 1):
            continue
        w = (add >> r.j_lo) & ((1 << r.m) - 1) if r.m else 0
        c = (add >> r.carry_j & 1) if r.carry_j is not None else 0
        v += (w + c) << (b - r.L)
    return v % (2 ** b)


@attrs.frozen
class MergedTwiddle(Bloq):
    """exp(+2 pi i · x·z / 2^t) (rows rounded to b bits when t - i > b) via a b-qubit catalyst.

    Registers (all THRU):
        x: QUInt(r1), z: QUInt(r2)  -- big-endian Qualtran ints, x = sum_i x_i 2^i.
        catalyst: QUInt(b)          -- must hold |Phi_b> from `CatalystPrep(b)`; returned unchanged.
    Decomposition: one CtrlAddIntoCatalyst per active row, control = bit i of the shorter register,
    addend = the kept window of the longer register, phase = catalyst[0:L_i], carry-in = the first
    dropped bit when the row rounds. Zero rotations. Ands = `merged_twiddle_ands(r1, r2, t, b)`.
    The phase is exact when no row rounds (t - i <= b for every row); otherwise the error is at most
    (#rounded rows)·pi·2^-b in operator norm.
    """

    r1: int
    r2: int
    t: int
    b: int

    def __attrs_post_init__(self):
        if min(self.r1, self.r2) < 1 or self.b < 1:
            raise ValueError("r1, r2, b must be >= 1")

    @cached_property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.r1)), Register("z", QUInt(self.r2)),
                          Register("catalyst", QUInt(self.b))])

    @cached_property
    def rows(self) -> List[TwiddleRow]:
        return twiddle_row_plan(self.r1, self.r2, self.t, self.b)

    @property
    def n_ands(self) -> int:
        return merged_twiddle_ands(self.r1, self.r2, self.t, self.b)

    @property
    def n_rounded_rows(self) -> int:
        return sum(r.rounded for r in self.rows)

    def on_classical_vals(self, x, z, catalyst) -> Dict[str, int]:
        v = merged_twiddle_model_turns(int(x), int(z), self.r1, self.r2, self.t, self.b)
        return {"x": x, "z": z, "catalyst": (int(catalyst) + v) % (2 ** self.b)}

    def _row_bloq(self, r: TwiddleRow) -> CtrlAddIntoCatalyst:
        return CtrlAddIntoCatalyst(r.L, r.m, r.carry_j is not None)

    def build_composite_bloq(self, bb: BloqBuilder, x: Soquet, z: Soquet, catalyst: Soquet
                             ) -> Dict[str, SoquetT]:
        swap = self.r1 > self.r2
        rs, rl = (self.r2, self.r1) if swap else (self.r1, self.r2)
        cs = list(bb.split(z if swap else x))[::-1]           # LSB first
        ls = list(bb.split(x if swap else z))[::-1]
        cat = list(bb.split(catalyst))                         # MSB first
        for r in self.rows:
            if not r.active:
                continue
            kw = {"ctrl": cs[r.i]}
            if r.m:
                win = ls[r.j_lo:r.j_lo + r.m]
                kw["x"] = bb.join(np.array(win[::-1]), dtype=QUInt(r.m))
            if r.carry_j is not None:
                kw["cin"] = ls[r.carry_j]
            kw["phase"] = bb.join(np.array(cat[:r.L]), dtype=QUInt(r.L))
            out = bb.add_d(self._row_bloq(r), **kw)
            cs[r.i] = out["ctrl"]
            if r.m:
                ls[r.j_lo:r.j_lo + r.m] = list(bb.split(out["x"]))[::-1]
            if r.carry_j is not None:
                ls[r.carry_j] = out["cin"]
            cat[:r.L] = list(bb.split(out["phase"]))
        cj = bb.join(np.array(cs[::-1]), dtype=QUInt(rs))
        lj = bb.join(np.array(ls[::-1]), dtype=QUInt(rl))
        catj = bb.join(np.array(cat), dtype=QUInt(self.b))
        if swap:
            return {"x": lj, "z": cj, "catalyst": catj}
        return {"x": cj, "z": lj, "catalyst": catj}

    def build_call_graph(self, ssa) -> Dict[Bloq, int]:
        cg: Dict[Bloq, int] = {}
        for r in self.rows:
            if r.active:
                bl = self._row_bloq(r)
                cg[bl] = cg.get(bl, 0) + 1
        return cg

    def __str__(self):
        return f"MergedTwiddle({self.r1},{self.r2},t={self.t},b={self.b})"
