"""Exact ("tight") child widths, PLAN_v2 Stage 13: the least width at which every reachable intermediate register value
and every child operand fits, over the floor-limb box (no padding).

    exact_child_width(mode, k, ops, w)      # ops: the site/STO op list (tuples or lists), w: operand width

PhaseProduct: `exact_width.exact_width` (vendored from ShorTableOptimization-policy-mirror, Lean Tight.lean).
PhaseTripleProduct: `ptp_exact_width.ptp_tight_width` (vendored from ShorTableOptimization-ptp-suite, Lean PTP/Exact.lean).
Both files are verbatim copies (SOURCES.json has the sha256). The only addition here is a cache of each table's reachable
coefficient rows: the vendored module's row function is replaced by a memoised wrapper of itself, so the vendored
`exact_width` / `ptp_tight_width` still run unchanged and depend on w only through the limb box.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Sequence

from . import exact_width as _pp
from . import ptp_exact_width as _ptp

PAIR, TRIPLE = "PhaseProduct", "PhaseTripleProduct"

_pp_rows = _pp.reach_rows
_ptp_rows = _ptp._rows_reached


@lru_cache(maxsize=4096)
def _pp_rows_key(k, ops):
    return frozenset(_pp_rows(k, ops))


@lru_cache(maxsize=4096)
def _ptp_rows_key(k, ops):
    return tuple(tuple(r) for r in _ptp_rows(k, [list(o) for o in ops]))


def _pp_rows_cached(k, ops):
    return _pp_rows_key(k, tuple(tuple(o) for o in ops))


def _ptp_rows_cached(k, ops):
    return _ptp_rows_key(k, tuple(tuple(o) for o in ops))


_pp.reach_rows = _pp_rows_cached          # exact_width() looks the name up at call time
_ptp._rows_reached = _ptp_rows_cached


def _key(ops: Sequence) -> tuple:
    return tuple(tuple(o) for o in ops)


@lru_cache(maxsize=1 << 18)
def _cached(mode: str, k: int, ops: tuple, w: int) -> int:
    if mode == PAIR:
        return int(_pp.exact_width(k, ops, w))
    if mode == TRIPLE:
        return int(_ptp.ptp_tight_width(k, ops, w))
    raise ValueError(f"unknown mode {mode!r}")


def exact_child_width(mode: str, k: int, ops: Sequence, w: int) -> int:
    """The exact child width of one table at operand width w (all operands of width w)."""
    return _cached(mode, int(k), _key(ops), int(w))


def exact_ladder(mode: str, policy_json: dict, n: int):
    """The vendored ladder function of the mode, on a site policy JSON: [(width, k or None), ...]."""
    if mode == PAIR:
        return [(r["width"], r["k"]) for r in _pp.exact_ladder(policy_json, n)]
    return [tuple(x) for x in _ptp.exact_ladder(policy_json, n)]


def ptp_scan_width(k: int, ops: Sequence, w: int) -> int:
    """The PTP suite's own scan (`childWidth`; negate adds no bit), for labelled comparison only."""
    return int(_ptp.ptp_scan_width(k, [list(o) for o in ops], w))
