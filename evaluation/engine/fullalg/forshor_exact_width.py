#!/usr/bin/env python3
"""ForShor exact-width drop-in, in Python: the width rule a policy.json carries (`ir.width_rule`).

A transcription of the Lean definitions the compiler uses (no dependency outside the stdlib):

  next_width(k, ops, xw, zw)       = Shor.exactSignedWidthOfWidths ops xw zw     (Compiler/ExactWidth.lean)
                                   = RecursivePhaseWorkspace.nextWidth ops xw zw (by nextSignedWidth_eq_exact, k > 0)
  immediate_need(k, ops, side_w, xw, zw)                                           (Compiler/Workspace.lean)
  PolicyWidths(doc).reserve_need(xw, zw)  = Shor.Policy.reserveNeed P xw zw      (Compiler/PolicyWorkspace.lean)
  PolicyWidths(doc).qft_workspace_need(w) = Policy.qftWorkspaceNeed P w           (QFT/Lowering/Workspace.lean)

`ops` are ForShor op dicts as policy.json prints them (progJson):
  {"op":"shiftL","i":..,"n":..} {"op":"shiftR","i":..,"n":..} {"op":"negate","i":..}
  {"op":"addScaled","dst":..,"src":..,"negSrc":bool,"shift":..} {"op":"phaseProduct","i":..}
(the L2Q / site op dicts use the same keys).

The rule, per call (two operands, rectangular allowed):
  W = min(xw // k, zw // k)                                  (phaseLimbWidth)
  chunk j < k-1 has width W; the top chunk has w - (k-1)W    (phaseSplitLogicalWidth), w = xw resp. zw
  box: lower chunks [0, 2^W - 1]; top chunk [-2^(t-1), 2^(t-1) - 1], [0, 0] if t = 0
  rows: every row of every state the program visits from the identity (stops at an inexact shiftR)
  demand(r) = max(-rowLo, rowHi + 1, 0) over the box;  D = max over rows, over BOTH boxes
  tight = 1 + least e >= 0 with D <= 2^e;  floor = 1 + widest initial chunk (both operands)
  next_width = max(tight, floor)

Site hook (read-only on sto_site; patches module attributes in the calling process only):
    import forshor_exact_width as few
    few.install()          # l2q_full.mirrors.width.next_width (and width_scan.next_width) -> exact
This routes every table opaque the full-run pricer evaluates (nextWidth, reserveNeed_x/z through
PhasePolicy.reserve_need / immediate_need, qftXWork/qftZWork through Policy.reserveNeed) to the exact
rule. `few.crosscheck(doc)` checks this module against the Lean tables a drop-in policy.json carries.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

Row = Tuple[int, ...]


# --------------------------------------------------------------------------------------- the program
def _key(ops: Sequence[Dict[str, Any]]) -> str:
    return json.dumps([dict(o) for o in ops], sort_keys=True)


@lru_cache(maxsize=4096)
def _rows(k: int, ops_key: str) -> Tuple[Row, ...]:
    """`exactReachRows ops`: every row of every visited state (the state before each op and the last)."""
    ops = json.loads(ops_key)
    st = [[1 if j == i else 0 for j in range(k)] for i in range(k)]
    out = {tuple(r) for r in st}
    for o in ops:
        t = o["op"]
        if t == "phaseProduct":
            pass
        elif t == "negate":
            st[o["i"]] = [-c for c in st[o["i"]]]
        elif t == "shiftL":
            st[o["i"]] = [c << o["n"] for c in st[o["i"]]]
        elif t == "shiftR":
            m = 1 << o["n"]
            if any(c % m for c in st[o["i"]]):
                break                      # Lean: applyOp? = none, the visit list ends
            st[o["i"]] = [c // m for c in st[o["i"]]]
        elif t == "addScaled":
            sg = -1 if o["negSrc"] else 1
            st[o["dst"]] = [a + sg * b * (1 << o["shift"]) for a, b in zip(st[o["dst"]], st[o["src"]])]
        else:
            raise ValueError(f"unknown op {o!r}")
        out.update(tuple(r) for r in st)
    return tuple(sorted(out))


def reach_rows(k: int, ops: Sequence[Dict[str, Any]]) -> Tuple[Row, ...]:
    return _rows(int(k), _key(ops))


# --------------------------------------------------------------------------------------- the widths
def limb_width(k: int, xw: int, zw: int) -> int:
    return min(xw // k, zw // k)


def split_width(w: int, W: int, k: int, j: int) -> int:
    return max(0, w - j * W) if j + 1 == k else W


def _box(k: int, w: int, W: int):
    lo, hi = [], []
    for j in range(k):
        t = split_width(w, W, k, j)
        if j + 1 == k:
            lo.append(0 if t == 0 else -(1 << (t - 1))); hi.append(0 if t == 0 else (1 << (t - 1)) - 1)
        else:
            lo.append(0); hi.append((1 << t) - 1)
    return lo, hi


def _demand(r: Row, lo, hi) -> int:
    rlo = sum(min(c * a, c * b) for c, a, b in zip(r, lo, hi))
    rhi = sum(max(c * a, c * b) for c, a, b in zip(r, lo, hi))
    return max(-rlo, rhi + 1, 0)


def tight_width_rows(k: int, rows: Sequence[Row], xw: int, zw: int) -> int:
    W = limb_width(k, xw, zw)
    bx, bz = _box(k, xw, W), _box(k, zw, W)
    D = 0
    for r in rows:
        D = max(D, _demand(r, *bx), _demand(r, *bz))
    e = 0
    while D > (1 << e):
        e += 1
    return 1 + e


def floor_width(k: int, xw: int, zw: int) -> int:
    W = limb_width(k, xw, zw)
    return 1 + max(max(split_width(xw, W, k, j), split_width(zw, W, k, j)) for j in range(k))


@lru_cache(maxsize=1 << 18)
def _next_width(k: int, ops_key: str, xw: int, zw: int) -> int:
    rows = _rows(k, ops_key)
    return max(tight_width_rows(k, rows, xw, zw), floor_width(k, xw, zw))


def next_width(k: int, ops: Sequence[Dict[str, Any]], xw: int, zw: int) -> int:
    """`Shor.exactSignedWidthOfWidths ops xw zw` = ForShor's `nextWidth` in the drop-in."""
    return _next_width(int(k), _key(ops), int(xw), int(zw))


def immediate_need(k: int, ops: Sequence[Dict[str, Any]], side_w: int, xw: int, zw: int) -> int:
    """`RecursivePhaseWorkspace.immediateNeed side ops xw zw`: growth of each chunk to `nextWidth`."""
    W = limb_width(k, xw, zw)
    Wn = next_width(k, ops, xw, zw)
    return sum(max(0, Wn - split_width(side_w, W, k, j)) for j in range(k))


# --------------------------------------------------------------------------------------- a policy
class PolicyWidths:
    """`Policy.reserveNeed` / `qftWorkspaceNeed` for the bands of a policy.json (exact widths)."""

    def __init__(self, doc: Dict[str, Any]):
        self.bands = [(int(b["threshold"]), int(b["k"]), b["ops"]) for b in doc["bands"]]
        self._memo: Dict[Tuple[int, int], Tuple[int, int]] = {}
        self._qmemo: Dict[int, Tuple[int, int]] = {}

    def choose(self, w: int):
        for th, k, ops in self.bands:
            if th <= w:
                return k, ops
        return None

    def next_width(self, xw: int, zw: int) -> Optional[int]:
        c = self.choose(max(xw, zw))
        return None if c is None else next_width(c[0], c[1], xw, zw)

    def reserve_need(self, xw: int, zw: int) -> Tuple[int, int]:
        key = (xw, zw)
        if key in self._memo:
            return self._memo[key]
        c = self.choose(max(xw, zw))
        if c is None:
            v = (0, 0)
        else:
            k, ops = c
            wn = next_width(k, ops, xw, zw)
            if wn < max(xw, zw):
                cx, cz = self.reserve_need(wn, wn)
                v = (immediate_need(k, ops, xw, xw, zw) + k * cx, immediate_need(k, ops, zw, xw, zw) + k * cz)
            else:
                v = (0, 0)
        self._memo[key] = v
        return v

    def qft_workspace_need(self, w: int) -> Tuple[int, int]:
        if w < 2:
            return (0, 0)
        if w in self._qmemo:
            return self._qmemo[w]
        left, right = w // 2, w - w // 2
        px, pz = self.reserve_need(left + 1, right + 1)
        lx, lz = self.qft_workspace_need(left)
        rx, rz = self.qft_workspace_need(right)
        v = (max(1 + px, max(lx, rx)), max(1 + pz, max(lz, rz)))
        self._qmemo[w] = v
        return v


# --------------------------------------------------------------------------------------- checks
def crosscheck(doc: Dict[str, Any], widths: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    """This module against the Lean tables in `doc["ir"]["width_rule"]`: per band, the rows and
    `diag` (nextWidth(w, w), computed by the compiler's own function), and the policy's `reserve_diag`
    (Policy.reserveNeed(w, w)). `widths` restricts the widths (default: every tabulated w)."""
    rule = doc["ir"]["width_rule"]
    assert rule["rule"] == "forshor.exact-width/v1", rule["rule"]
    want = None if widths is None else set(int(w) for w in widths)
    out = {"bands": [], "reserve_checked": 0, "reserve_mismatch": []}
    for wb, b in zip(rule["bands"], doc["bands"]):
        k, ops = int(b["k"]), b["ops"]
        rows_ok = sorted(tuple(r) for r in wb["rows"]) == list(reach_rows(k, ops))
        mism, n = [], 0
        for w, nw in wb["diag"]:
            if want is not None and w not in want:
                continue
            n += 1
            mine = next_width(k, ops, w, w)
            if mine != nw:
                mism.append((w, nw, mine))
        out["bands"].append({"index": wb["index"], "k": k, "rows_equal": rows_ok, "diag_checked": n,
                             "diag_mismatch": mism[:10]})
    P = PolicyWidths(doc)
    for w, rx, rz in rule["reserve_diag"]:
        if want is not None and w not in want:
            continue
        out["reserve_checked"] += 1
        if P.reserve_need(w, w) != (rx, rz):
            out["reserve_mismatch"].append((w, (rx, rz), P.reserve_need(w, w)))
    out["reserve_mismatch"] = out["reserve_mismatch"][:10]
    out["ok"] = all(b["rows_equal"] and not b["diag_mismatch"] for b in out["bands"]) and not out["reserve_mismatch"]
    return out


# --------------------------------------------------------------------------------------- site hook
def install() -> None:
    """Route the site's L2Q width mirror to the exact rule (this process only; no file is edited)."""
    from l2q_full.mirrors import width as wm          # the site's vendored L2Q (engine/fullalg on sys.path)
    wm.next_width = lambda k, ops, wx, wz: next_width(k, ops, wx, wz)
    try:
        from l2q_full import width_scan as ws
        ws.next_width = lambda ops, k, wx, wz=None: next_width(k, ops, wx, wx if wz is None else wz)
        ws._next_width_cached.cache_clear() if hasattr(ws, "_next_width_cached") else None
    except ImportError:
        pass
    try:
        from l2q_full import policy as lp
        lp.wmirror.next_width = wm.next_width
        lp._next_width.cache_clear()
    except (ImportError, AttributeError):
        pass


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        r = crosscheck(json.load(open(p)))
        print(p, json.dumps(r))


def crosscheck_width_table(tab: Dict[str, Any]) -> Dict[str, Any]:
    """This module against `forshor_policy_submission --width-table` output (Lean's own values):
    nextWidth per band and Policy.reserveNeed at every listed (xw, zw) pair (diagonal 1..4400,
    qft split pairs, the rectangular 2048-bit sites)."""
    doc = {"bands": tab["bands"]}
    P = PolicyWidths(doc)
    nb = len(tab["bands"])
    bad, n = [], 0
    for row in tab["rows"]:
        xw, zw = row[0], row[1]
        lean_nw, (rx, rz) = row[2:2 + nb], row[2 + nb:4 + nb]
        mine = [next_width(b["k"], b["ops"], xw, zw) for b in tab["bands"]]
        n += 1
        if mine != lean_nw or P.reserve_need(xw, zw) != (rx, rz):
            bad.append((xw, zw, lean_nw, mine, (rx, rz), P.reserve_need(xw, zw)))
    return {"pairs_checked": n, "mismatches": len(bad), "first": bad[:5]}
