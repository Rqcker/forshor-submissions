"""`RecursivePhaseWorkspace.nextWidth`, re-folded exactly — the symbolic width policy.

WHY THIS EXISTS
---------------
`nextWidth` is what decides the recursion ladder, and therefore the whole cost. The drop emits
it as two TABLES (`width.by_m` over the uniform ladder `w = k*m`, and `width.by_w` over
`w = k..w_max`) plus a detected affine tail `W(m) = m + beta`.

That is enough only while the ladder stays inside the tables. Beyond `w_max` the affine tail
takes over — and **the affine tail is not exact**. Measured at k=3 against the emitted table:

    w=31: table 27, rule 28      w=52: table 34, rule 35      w=76: table 42, rule 43

The rule over-estimates by one at those widths. `Shor.affineTail` detects `W(m+1) = W(m)+1`
over the scanned UNIFORM ladder (`w = k*m`); the recursion, however, visits general `w`, where
the relation can fail. Extrapolating past `w_max` therefore inflates the width, changes how many
levels the ladder takes, and moves the cost — by **5x at k=3** between a `w_max = 16` and a
`w_max = 96` bundle, which is how this was found.

So `nextWidth` is re-folded here from its own definition instead. It is a scan over the
schedule, and the schedule IS emitted, so this needs no table and no extrapolation and is exact
at every `w` — including 2048, where no table could reach.

THE DEFINITION (transcribed, with citations)
--------------------------------------------
    initWidthState x z k              Compiler/Widths.lean:22
        W = phaseLimbWidth x z k = min (x.width / k) (z.width / k)      Layout.lean:96
        xw i = phaseSplitLogicalWidth x.width W k i                     Layout.lean:110
        (the lower k-1 limbs get W; the top limb absorbs w - i*W)

    updateWidthState st op            Layout.lean:50
        shiftL i n    -> xw[i] += n,  zw[i] += n
        shiftR i n    -> xw[i] -= n,  zw[i] -= n          (Nat: truncates at 0)
        negate i      -> xw[i] += 1,  zw[i] += 1
        addScaled d s _negsrc sh ->
                         xw[d] = 1 + max(xw[d], xw[s] + sh)
                         zw[d] = 1 + max(zw[d], zw[s] + sh)
        phaseProduct  -> unchanged

    scanNeededWidths                  Compiler/Widths.lean:36
        running POINTWISE MAX of the state after every op, seeded with the initial state

    commonNeededWidth need            Layout.lean:168
        1 + sup over i of max (xneed i) (zneed i)          -- the +1 is the sign bit

    nextWidth ops wx wz               Compiler/Workspace.lean:259
        nextSignedWidth (widthModelX wx) (widthModelZ wx wz) ops

Note `addScaled` ignores `negSrc` here exactly as it does in the resource model, and that the
scan takes the max over every intermediate state — not just the final one — which is why a
program that returns to its starting widths still demands room for its peak.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Sequence, Tuple

WidthState = Tuple[Tuple[int, ...], Tuple[int, ...]]     # (xw, zw)


class WidthScanError(ValueError):
    pass


def limb_split(w: int, limb: int, k: int) -> List[int]:
    """`phaseSplitLogicalWidth w W k` for every chunk (Layout.lean:110)."""
    return [(w - i * limb) if i + 1 == k else limb for i in range(k)]


def init_width_state(wx: int, wz: int, k: int) -> WidthState:
    """`initWidthState` (Widths.lean:22), via `phaseLimbWidth` = min of the two floor divisions."""
    limb = min(wx // k, wz // k)                                  # Layout.lean:96
    return tuple(limb_split(wx, limb, k)), tuple(limb_split(wz, limb, k))


def update_width_state(st: WidthState, op: Dict[str, Any]) -> WidthState:
    """`updateWidthState` (Layout.lean:50). Nat subtraction truncates at zero."""
    xw, zw = list(st[0]), list(st[1])
    kind = op["op"]
    if kind == "phaseProduct":
        return st
    if kind == "shiftL":
        i, n = int(op["i"]), int(op["n"])
        xw[i] += n
        zw[i] += n
    elif kind == "shiftR":
        i, n = int(op["i"]), int(op["n"])
        xw[i] = max(0, xw[i] - n)
        zw[i] = max(0, zw[i] - n)
    elif kind == "negate":
        i = int(op["i"])
        xw[i] += 1
        zw[i] += 1
    elif kind == "addScaled":
        d, s = int(op["dst"]), int(op["src"])
        sh = int(op.get("shift", 0))
        xw[d] = 1 + max(xw[d], xw[s] + sh)
        zw[d] = 1 + max(zw[d], zw[s] + sh)
    else:
        raise WidthScanError(f"unknown schedule op {kind!r}; the width scan must not guess")
    return tuple(xw), tuple(zw)


def _ops_key(ops: Sequence[Dict[str, Any]]) -> tuple:
    return tuple((o["op"], o.get("i"), o.get("n"), o.get("dst"), o.get("src"), o.get("shift"))
                 for o in ops)


@lru_cache(maxsize=65536)
def _next_width_cached(ops_key: tuple, k: int, wx: int, wz: int) -> int:
    ops = [{"op": o[0], "i": o[1], "n": o[2], "dst": o[3], "src": o[4], "shift": o[5]}
           for o in ops_key]
    cur = init_width_state(wx, wz, k)
    need_x, need_z = list(cur[0]), list(cur[1])                   # seeded with the initial state
    for op in ops:
        cur = update_width_state(cur, op)
        need_x = [max(a, b) for a, b in zip(need_x, cur[0])]
        need_z = [max(a, b) for a, b in zip(need_z, cur[1])]
    return 1 + max(max(a, b) for a, b in zip(need_x, need_z))      # commonNeededWidth


def next_width(ops: Sequence[Dict[str, Any]], k: int, wx: int, wz: int = None) -> int:
    """`RecursivePhaseWorkspace.nextWidth ops wx wz`, exactly. `wz` defaults to `wx` (square)."""
    return _next_width_cached(_ops_key(ops), k, wx, wx if wz is None else wz)


def ladder(ops: Sequence[Dict[str, Any]], k: int, n: int, max_depth: int = 4096) -> List[int]:
    """The recursion ladder from width `n`, by iterating the exact `nextWidth`.

    `compileOpsToSignedGate` recurses while the width strictly shrinks (Workspace.lean:363:
    `if nextWidth ops wx wz < max wx wz`), so the ladder stops at the first width that does not
    decrease. No table, no extrapolation, no `w_max`.
    """
    out = [n]
    for _ in range(max_depth):
        nxt = next_width(ops, k, out[-1])
        if nxt >= out[-1]:
            return out
        out.append(nxt)
    raise WidthScanError(f"ladder did not converge from n={n} at k={k} within {max_depth} levels")


def ladder_from_bundle(bundle_doc: Dict[str, Any], n: int) -> List[int]:
    return ladder(bundle_doc["schedule"]["ops"], int(bundle_doc["k"]), n)
