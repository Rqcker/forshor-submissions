"""E3 mirror: the widths as rules without n, for the two policies (DefsCore symbolic; LowGateIR tight).
It also gives the reserve of the recursive workspace (`RecursivePhaseWorkspace.reserveNeed`) and the
QFT workspace demand (`qftWorkspaceNeed`, which the E4 mirror uses).

Each function here is a line-by-line mirror of a computable ForShor `def`. The docstring gives its name.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import schemas
from .schedule import raw_table

Op = Dict[str, Any]
CITES = ["(retired in the 2026-09-18 drop -- see ask L23)", "Shor.commonNeededWidth",
         "Shor.genOpsWithProduct_ProgConsumesPtsSafe"]


# ---------------------------------------------------------------- DefsCore symbolic scan
def phase_limb_width_of_width(w: int, k: int) -> int:
    """`phaseLimbWidthOfWidth w k = w / k` (DefsCore.lean:127)."""
    return w // k


def phase_split_logical_width(w: int, W: int, k: int, i: int) -> int:
    """`phaseSplitLogicalWidth w W k i` (DefsCore.lean:149): the top limb gets the remainder."""
    return w - i * W if i + 1 == k else W


def next_width(k: int, ops: Sequence[Op], wx: int, wz: int) -> int:
    """`RecursivePhaseWorkspace.nextWidth ops wx wz` = `commonNeededWidth (scanNeededWidths x z ops)`
    with `initWidthState` (DefsCore.lean:228, :234-246, :304, :1436)."""
    W = min(wx // k, wz // k)                                  # phaseLimbWidth (min of both sides)
    xw = [phase_split_logical_width(wx, W, k, i) for i in range(k)]
    zw = [phase_split_logical_width(wz, W, k, i) for i in range(k)]
    xneed, zneed = list(xw), list(zw)
    for op in ops:
        t = op["op"]
        if t == "shiftL":
            xw[op["i"]] += op["n"]; zw[op["i"]] += op["n"]
        elif t == "shiftR":
            xw[op["i"]] = max(0, xw[op["i"]] - op["n"]); zw[op["i"]] = max(0, zw[op["i"]] - op["n"])
        elif t == "negate":
            xw[op["i"]] += 1; zw[op["i"]] += 1
        elif t == "addScaled":
            d, s, sh = op["dst"], op["src"], op["shift"]
            xw[d] = 1 + max(xw[d], xw[s] + sh)
            zw[d] = 1 + max(zw[d], zw[s] + sh)
        elif t == "phaseProduct":
            pass
        else:
            raise ValueError(t)
        xneed = [max(a, b) for a, b in zip(xneed, xw)]
        zneed = [max(a, b) for a, b in zip(zneed, zw)]
    return 1 + max(max(a, b) for a, b in zip(xneed, zneed))    # commonNeededWidth


def sym_width(k: int, ops: Sequence[Op], w: int) -> int:
    """For square operands: `symWidth ops w = nextWidth ops w w`."""
    return next_width(k, ops, w, w)


def limb_width(k: int, w: int) -> int:
    """`RecursivePhaseWorkspace.limbWidth k w w`."""
    return min(w // k, w // k)


# ---------------------------------------------------------------- LowGateIR exact scan
def run_rows(k: int, ops: Sequence[Op]) -> List[List[List[int]]]:
    """Return all states that `run?` visits from `State.start_state` (Core/Language.lean:23-40).
    The result includes the start state. An inexact shiftR raises an error."""
    st = [[1 if a == b else 0 for b in range(k)] for a in range(k)]
    visited = [[row[:] for row in st]]
    for op in ops:
        t = op["op"]
        if t == "phaseProduct":
            pass
        elif t == "addScaled":
            s = -1 if op["negSrc"] else 1
            st[op["dst"]] = [a + s * (2 ** op["shift"]) * b for a, b in zip(st[op["dst"]], st[op["src"]])]
        elif t == "shiftL":
            st[op["i"]] = [c * (2 ** op["n"]) for c in st[op["i"]]]
        elif t == "shiftR":
            d = 2 ** op["n"]
            if any(c % d for c in st[op["i"]]):
                raise ValueError(f"inexact shiftR in {op}")
            st[op["i"]] = [c // d for c in st[op["i"]]]
        elif t == "negate":
            st[op["i"]] = [-c for c in st[op["i"]]]
        else:
            raise ValueError(t)
        visited.append([row[:] for row in st])
    return visited


def limb_range(k: int, j: int, w: int) -> Tuple[int, int]:
    """`Emit.limbRange k j w` (LowGateIR.lean:541): the top limb is signed, the lower limbs are unsigned."""
    if j + 1 == k:
        return (-(2 ** (w - 1)), 2 ** (w - 1) - 1)
    return (0, 2 ** w - 1)


def row_range(row: Sequence[int], rs: Sequence[Tuple[int, int]]) -> Tuple[int, int]:
    lo = hi = 0
    for c, (a, b) in zip(row, rs):
        lo += min(c * a, c * b); hi += max(c * a, c * b)
    return lo, hi


def fits_signed(w: int, lo: int, hi: int) -> bool:
    return -(2 ** (w - 1)) <= lo and hi <= 2 ** (w - 1) - 1


def reachable_fits(rs: Sequence[Tuple[int, int]], w: int, visited: Sequence[Sequence[Sequence[int]]]) -> bool:
    """`Emit.reachableFits rs w ops State.start_state` over the states that `run_rows` visited before."""
    return all(fits_signed(w, *row_range(row, rs)) for st in visited for row in st)


def tight_width(k: int, ops: Sequence[Op], w: int, visited=None) -> Optional[int]:
    """Return the least w' ≥ 1 with `reachableFits` for square operands of width `w`. The limb ranges
    come from `phaseSplitLogicalWidth w (w/k) k j`. The fuel is symWidth + 8, as in Width.lean."""
    if visited is None:
        visited = run_rows(k, ops)
    W = phase_limb_width_of_width(w, k)
    rs = [limb_range(k, j, phase_split_logical_width(w, W, k, j)) for j in range(k)]
    fuel = sym_width(k, ops, w) + 8
    for cand in range(1, fuel + 1):
        if reachable_fits(rs, cand, visited):
            return cand
    return None


def exact_width(k: int, ops: Sequence[Op], w: int, visited=None) -> int:
    """Engine 2's exact child width (PLAN_v2 Stage 13): the least w' >= 1 with `reachableFits` over the floor-limb box
    of square operands of width w, the same definition as `tight_width` (LowGateIR `reachableFits`, limb ranges from
    `phaseSplitLogicalWidth w (w/k) k j`), found by bisection (`reachableFits` is monotone in w') instead of a linear
    scan. An independent implementation of the rule engine 1 takes from ShorTableOptimization's `exact_width`."""
    if visited is None:
        visited = run_rows(k, ops)
    W = phase_limb_width_of_width(w, k)
    rs = [limb_range(k, j, phase_split_logical_width(w, W, k, j)) for j in range(k)]
    lo, hi = 1, 2
    while not reachable_fits(rs, hi, visited):
        lo, hi = hi + 1, 2 * hi
    while lo < hi:                       # least hi with fits
        mid = (lo + hi) // 2
        if reachable_fits(rs, mid, visited):
            hi = mid
        else:
            lo = mid + 1
    return hi


# ---------------------------------------------------------------- recursive workspace
def immediate_need(k: int, ops: Sequence[Op], side_w: int, wx: int, wz: int, Wnext: Optional[int] = None) -> int:
    """`RecursivePhaseWorkspace.immediateNeed side ops wx wz` (DefsCore.lean:1475). `Wnext` overrides the child width
    (Stage 13: the same formula evaluated at an exact child width; NOT a Lean-proved layout there)."""
    Wphase = limb_width_pair(k, wx, wz)
    if Wnext is None:
        Wnext = next_width(k, ops, wx, wz)
    return sum(max(0, Wnext - phase_split_logical_width(side_w, Wphase, k, i)) for i in range(k))


def limb_width_pair(k: int, wx: int, wz: int) -> int:
    return min(wx // k, wz // k)


def reserve_need(k: int, ops: Sequence[Op], wx: int, wz: int) -> Tuple[int, int]:
    """`RecursivePhaseWorkspace.reserveNeed ops wx wz` (DefsCore.lean:1516). The value is conservative."""
    Wnext = next_width(k, ops, wx, wz)
    if Wnext < max(wx, wz):
        cx, cz = reserve_need(k, ops, Wnext, Wnext)
        return (immediate_need(k, ops, wx, wx, wz) + k * cx,
                immediate_need(k, ops, wz, wx, wz) + k * cz)
    return (0, 0)


def qft_workspace_need(k: int, ops: Sequence[Op], n: int) -> Tuple[int, int]:
    """`qftWorkspaceNeed ops n` (Implementation/QFT/Defs.lean:434)."""
    @lru_cache(maxsize=None)
    def go(t: int) -> Tuple[int, int]:
        if t < 2:
            return (0, 0)
        left, right = t // 2, t - t // 2
        px, pz = reserve_need(k, ops, left + 1, right + 1)
        lx, lz = go(left)
        rx, rz = go(right)
        return (max(1 + px, max(lx, rx)), max(1 + pz, max(lz, rz)))
    return go(n)


# ---------------------------------------------------------------- tables & affine tails
def affine_tail(f, m_lo: int, m_hi: int) -> Optional[Tuple[int, int]]:
    """Find the least m₀ ≤ m_hi-3 with f(m+1) - f(m) = 1 for all m in [m₀, m_hi).
    Return (m₀, f(m₀) - m₀)."""
    for m0 in range(m_lo, m_hi - 2):
        if all(f(m + 1) == f(m) + 1 for m in range(m0, m_hi)):
            return (m0, f(m0) - m0)
    return None


def _affine_json(t: Optional[Tuple[int, int]]) -> Dict[str, Any]:
    if t is None:
        return {"affine": False}
    return {"affine": True, "from_m": t[0], "beta": t[1], "rule": "W(m) = m + beta  for m >= from_m"}


def reserve_ladder(w_max: int) -> List[int]:
    return [w for w in range(2, w_max + 1) if w <= 16 or w % 8 == 0]


def section(k: int, m_max: int = 24, w_max: int = 96) -> Dict[str, Any]:
    tab, _ = raw_table(k)
    ops = tab["ops"]
    visited = run_rows(k, ops)
    uniform = []
    for m in range(1, m_max + 1):
        w = k * m
        wt = tight_width(k, ops, w, visited)
        if wt is None:
            raise ValueError(f"tight width search failed at k={k}, m={m}")
        uniform.append({"m": m, "n": w, "W_sym": sym_width(k, ops, w), "W_tight": wt})
    general = []
    for w in range(k, w_max + 1):
        wt = tight_width(k, ops, w, visited)
        if wt is None:
            raise ValueError(f"tight width search failed at k={k}, w={w}")
        general.append({"w": w, "limb": limb_width(k, w), "W_sym": sym_width(k, ops, w), "W_tight": wt})
    reserve = []
    for w in reserve_ladder(w_max):
        x, z = reserve_need(k, ops, w, w)
        reserve.append({"w": w, "x": x, "z": z})
    sym_of = {e["m"]: e["W_sym"] for e in uniform}
    tight_of = {e["m"]: e["W_tight"] for e in uniform}
    return {
        "schema": schemas.FORSHOR_WIDTH, "k": k,
        "policies": {
            "symbolic": "DefsCore nextSignedWidth = commonNeededWidth (scanNeededWidths x z ops); the fidelity-theorem width",
            "tight": "Emit/LowGateIR reachableFits: least width with every reachable value in range (exact, attained); the compiled/counted width"},
        "uniform": uniform,
        "uniform_note": "uniform limbs: operand width n = k*m, every limb m bits; this is the n = k*m (padded) instantiation the pipeline uses",
        "affine_sym": _affine_json(affine_tail(lambda m: sym_of.get(m, 0), 1, m_max)),
        "affine_tight": _affine_json(affine_tail(lambda m: tight_of.get(m, 0), 1, m_max)),
        "general": general,
        "general_note": "arbitrary operand width w (top limb absorbs w mod k); residue classes w mod k have their own affine tails",
        "reserve_need": reserve,
        "reserve_note": "RecursivePhaseWorkspace.reserveNeed ops w w: reserve qubits the complete recursive lowering demands per side (conservative, provisions every child)",
        "cites": CITES,
    }
