r"""Exact basis-state simulator for `forshor.lowgate/v2` circuits — ForShor's own semantics.

Every `LowGate` maps a computational basis state to a phase times a basis state, so a circuit
can be followed one basis state at a time in O(gates). This module transcribes the basis maps
ForShor's `LowGateSemantics` class is stated over (`Framework/Semantics/GateSemantics.lean`,
`LowGateSemantics.lean`), not any backend's realisation of them:

* `Phase q θ` multiplies by `exp(iπθ)` when bit `q` is set — phases are accumulated **exactly**,
  as rationals in units of π, reduced mod 2;
* `AddScaled dst src neg sh`: `dst ← tcMod(dst + (±1)·2^sh·src)` (identity if not disjoint);
* `Negate r`: `r ← tcMod(-r)`;
* `ShiftL`/`ShiftR`: the total reversible completions `signedShiftLWord`/`signedShiftRWord` (equal
  to the ordinary signed shift whenever it does not overflow / divides exactly);
* `signExtend r n` / `signDealloc r n`: complement `r.reserve.take n` iff `r` is negative
  (an involution); `zeroExtend`/`zeroDealloc` are the identity;
* `adj g`: the inverse, gate by gate in reverse order.

Values are two's complement over the register's *active* list (LSB first). Used to check that a
composed policy circuit *is* `exp(iπφ·[[x]]·[[z]])` — diagonal, with the workspace returned to
zero — independently of Lean and of every Qualtran/Qiskit backend.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List, Sequence, Tuple


class SimError(ValueError):
    pass


def _read(bits: int, qs: Sequence[int]) -> int:
    v = 0
    for i, q in enumerate(qs):
        v |= ((bits >> q) & 1) << i
    return v


def _write(bits: int, qs: Sequence[int], v: int) -> int:
    for i, q in enumerate(qs):
        if (v >> i) & 1:
            bits |= 1 << q
        else:
            bits &= ~(1 << q)
    return bits


def tc_decode(w: int, u: int) -> int:
    if w == 0:
        return 0
    return u - (1 << w) if u >> (w - 1) else u


def tc_mod(w: int, z: int) -> int:
    return z % (1 << w)


def signed(bits: int, qs: Sequence[int]) -> int:
    return tc_decode(len(qs), _read(bits, qs))


def _shl_word(w: int, n: int, u: int) -> int:
    if n < w:
        k = w - n
        kept, dropped = u % (1 << k), u >> k
        mask = (1 << n) - 1 if (u >> (k - 1)) & 1 else 0
        return (dropped ^ mask) + (kept << n)
    return u % (1 << w)


def _shr_word(w: int, n: int, u: int) -> int:
    if n < w:
        k = w - n
        kept, syn = u >> n, u % (1 << n)
        mask = (1 << n) - 1 if (u >> (w - 1)) & 1 else 0
        return kept + ((syn ^ mask) << k)
    return u % (1 << w)


def _sign_extend(bits: int, r: Dict[str, List[int]], n: int) -> int:
    new = r["reserve"][:n]
    hi = _read(bits, new)
    if signed(bits, r["active"]) < 0:
        hi = (1 << len(new)) - 1 - hi
    return _write(bits, new, hi)


def _step(bits: int, phase: Fraction, g: Dict[str, Any], inverse: bool) -> Tuple[int, Fraction]:
    op = g["op"]
    if op == "adj":
        return run(g["body"], bits, phase, inverse=not inverse)
    if op == "id":
        return bits, phase
    if op == "Phase":
        if (bits >> g["q"]) & 1:
            a = Fraction(g["angle"]["num"], g["angle"]["den"])
            phase = (phase - a if inverse else phase + a) % 2
        return bits, phase
    if op == "X":
        return bits ^ (1 << g["q"]), phase
    if op == "CNOT":
        if (bits >> g["ctrl"]) & 1:
            bits ^= 1 << g["target"]
        return bits, phase
    if op == "Toffoli":
        if (bits >> g["c1"]) & 1 and (bits >> g["c2"]) & 1:
            bits ^= 1 << g["target"]
        return bits, phase
    if op in ("zeroExtend", "zeroDealloc"):
        return bits, phase
    if op in ("signExtend", "signDealloc"):
        return _sign_extend(bits, g["r"], g["n"]), phase
    if op == "Negate":
        qs = g["r"]["active"]
        return _write(bits, qs, tc_mod(len(qs), -signed(bits, qs))), phase
    if op in ("ShiftL", "ShiftR"):
        qs = g["r"]["active"]
        left = (op == "ShiftL") != inverse
        f = _shl_word if left else _shr_word
        return _write(bits, qs, f(len(qs), g["n"], _read(bits, qs))), phase
    if op == "AddScaled":
        d, s = g["dst"]["active"], g["src"]["active"]
        if set(d) & set(s):
            return bits, phase
        sign = -1 if g["negSrc"] else 1
        if inverse:
            sign = -sign
        v = signed(bits, d) + sign * (1 << g["shift"]) * signed(bits, s)
        return _write(bits, d, tc_mod(len(d), v)), phase
    raise SimError(f"no basis semantics for {op!r} (H is not basis-preserving)")


def run(leaves: Sequence[Dict[str, Any]], bits: int, phase: Fraction = Fraction(0),
        inverse: bool = False) -> Tuple[int, Fraction]:
    """Apply `leaves` (or their inverse) to basis state `bits`; return `(bits', phase/π mod 2)`."""
    seq = reversed(leaves) if inverse else leaves
    for g in seq:
        bits, phase = _step(bits, phase, g, inverse)
    return bits, phase


def check_phase_product(leaves: Sequence[Dict[str, Any]], x: Sequence[int], z: Sequence[int],
                        phi: Fraction, xv: int, zv: int, ctrl: int = None, ctrl_on: bool = True) -> Dict[str, Any]:
    """Run one basis input with `x`, `z` holding the signed values `xv`, `zv` (all else 0) and
    check: state unchanged (diagonal, workspace clean) and phase = φ·xv·zv (mod 2, units of π),
    or 0 when a control qubit is present and off."""
    bits = _write(0, x, tc_mod(len(x), xv))
    bits = _write(bits, z, tc_mod(len(z), zv))
    if ctrl is not None and ctrl_on:
        bits |= 1 << ctrl
    out, ph = run(leaves, bits)
    want = (phi * xv * zv) % 2 if (ctrl is None or ctrl_on) else Fraction(0)
    return {"ok": out == bits and ph == want, "diagonal": out == bits, "phase": ph, "want": want,
            "moved": bin(out ^ bits).count("1")}
