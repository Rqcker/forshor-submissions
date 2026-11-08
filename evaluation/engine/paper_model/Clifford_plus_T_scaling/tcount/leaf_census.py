"""Leaf / twiddle census of the production bloqs, for pricing MERGED catalysis (Amy et al. §VI.C).

`rotation_histogram` counts rotation SITES by resolution. A merged catalytic realisation prices
whole phase-product GRIDS instead: every site of a w x w grid carries the same turn tau (times the
bit weights), so row i's phase is x_i · frac(tau·2^i·z) and the grid becomes w controlled additions
into the catalyst (Amy et al., arXiv:2305.07720 p.29: "each sequence of decrements can be seen as an
inverse adder"). This census records, per priced circuit:

  twiddle   one entry per LeanQFT twiddle PhaseProdUsing(2pi/2^t, left, right): (left, right, t) -- a
            pure turn; priced UNDECOMPOSED under merging (Toom children carry non-pure weights, which
            destroy the shifted-window property), so the Toom decomposition's own T (adders + cell
            Ands, at 0 T per rotation) is recorded for refund from the shared base.
  leaf      one entry per phase-product leaf grid (U1/U2/U5 of the modmul, the multiplier's PP):
            ("leaf", wx, ctrl, cls, res, wz). Controlled leaves (U1/U5) carry an extra And per cell in
            the base. TURN CLASS (v2, decision D3):
              cls "U1U5"    generic kappa/N turns (N odd), res None
              cls "U2"      dyadic, root turn N / 2^(w_work + w_carry) (resolution w_work + w_carry)
              cls "MULT"    dyadic, root turn c / 2^w_out (resolution <= w_out)
              cls "generic" a phase product of unknown turn, res None
            `res` is an UPPER BOUND on the leaf's own resolution: a Toom child's turn is the parent's
            times the integer c_l(2^m) (m = ceil(W/k)), so it loses v2(c_l(2^m)) bits (to 0: identity).
            The loss per child comes from the rules' bundle coefficient rows AND the frozen schedule
            mirror, matched in sorted order and taking the smaller (safe for either point set).
            Leaves are square in this pipeline (wx == wz); asymmetric leaves would stay distinct keys.
            Old 3-tuple keys ("leaf", w, ctrl) still price (as generic, square).
  other     rotations outside both (priced per site).

Invariant (checked by the caller): sum twiddle cells + sum leaf wx*wz + other == QECGatesCost.rotation.
"""
from __future__ import annotations

import math
from collections import Counter
from fractions import Fraction
from functools import lru_cache
from typing import Callable, Dict, List, Optional, Tuple

import forshor_qualtran  # noqa: F401  -- binds the frozen Qualtran tree before `qualtran` is imported
from qualtran import Bloq
from qualtran.resource_counting import CostKey, QECGatesCost, get_bloq_callee_counts, get_cost_value

TW = "tw"        # ("tw", left, right, t, tw_rot, tw_T0)   tw_T0 = twiddle PP T at 0 T/rotation
LEAF = "leaf"    # ("leaf", wx, ctrl, cls, res, wz)   (v1: ("leaf", w, ctrl))
OTHER = "other"  # ("other",)


def _t0(bloq: Bloq, cache: dict) -> Tuple[int, int]:
    """(T at 0 T per rotation, rotations) of a bloq -- its non-rotation T and its site count."""
    qec = get_cost_value(bloq, QECGatesCost(), costs_cache=cache)
    return int(qec.total_t_count(ts_per_rotation=0)), int(qec.rotation)


class LeafCensus(CostKey[dict]):
    def __init__(self, qec_cache: dict | None = None):
        self.qec_cache = qec_cache if qec_cache is not None else {}

    def compute(self, bloq: Bloq, get_callee_cost: Callable[[Bloq], dict]) -> dict:
        from qualtran.bloqs.basic_gates import CZPowGate, ZPowGate
        from qualtran.resource_counting.classify_bloqs import bloq_is_rotation
        from forshor_qualtran.bloqs.modmul import LeanCtrlModMul
        from forshor_qualtran.bloqs.multiplier import ToomCookMultiplier
        from forshor_qualtran.bloqs.qft import LeanQFT
        from forshor_qualtran.recursion_versions import ToomRecursivePhaseProduct

        if isinstance(bloq, LeanQFT):
            if bloq.w <= 1:
                return {}
            left, right = bloq.w // 2, bloq.w - bloq.w // 2
            total: Counter = Counter()
            for sub in (bloq._sub(right), bloq._sub(left)):
                total.update(get_callee_cost(sub))
            tw = _twiddle_bloq(bloq, left, right)
            t0, rot = _t0(tw, self.qec_cache)
            total[(TW, left, right, int(bloq.w), rot, t0)] += 1
            return dict(total)
        if isinstance(bloq, ToomRecursivePhaseProduct):
            return dict(_pp_leaves(bloq, False, "generic", None))
        if isinstance(bloq, LeanCtrlModMul) and bloq.core == "versionB":
            # U1, U5 (controlled) and U2 are all `_pp(.., w_work)` at size w_work+1, so Qualtran merges
            # them into ONE callee of multiplicity 3; two of those three carry the extra control And.
            # U1/U5 turns are kappa/N (generic); U2's is N / 2^(w_work + w_carry) (dyadic, D3).
            total = Counter()
            n_pp = 0
            for callee, mult in get_bloq_callee_counts(bloq):
                if isinstance(callee, ToomRecursivePhaseProduct):
                    n_pp += int(mult)
                    total.update({k: v * 2 for k, v in _pp_leaves(callee, True, "U1U5", None).items()})
                    u2 = _pp_leaves(callee, False, "U2", int(bloq.w_work) + int(bloq.w_carry))
                    total.update({k: v * (int(mult) - 2) for k, v in u2.items()})
                    continue
                sub = get_callee_cost(callee)
                for key, cnt in sub.items():
                    total[key] += cnt * mult
            if n_pp != 3:
                raise RuntimeError(f"LeanCtrlModMul call graph changed: {n_pp} phase products, expected 3")
            return dict(total)
        if isinstance(bloq, ToomCookMultiplier):
            # the multiplier's PP carries phi = 2 pi c / 2^w_out: dyadic, resolution <= w_out (D3)
            total = Counter()
            for callee, mult in get_bloq_callee_counts(bloq):
                if isinstance(callee, ToomRecursivePhaseProduct):
                    sub = _pp_leaves(callee, False, "MULT", int(bloq.w_out))
                else:
                    sub = get_callee_cost(callee)
                for key, cnt in sub.items():
                    total[key] += cnt * mult
            return dict(total)
        if isinstance(bloq, (CZPowGate, ZPowGate)) or bloq_is_rotation(bloq):
            r = int(get_cost_value(bloq, QECGatesCost(), costs_cache=self.qec_cache).rotation)
            return {(OTHER,): r} if r else {}
        total = Counter()
        for callee, mult in get_bloq_callee_counts(bloq):
            for key, cnt in get_callee_cost(callee).items():
                total[key] += cnt * mult
        return dict(total)

    def zero(self) -> dict:
        return {}

    def __str__(self) -> str:
        return "leaf census"


def _twiddle_bloq(qft, left: int, right: int) -> Bloq:
    """The Toom phase product LeanQFT.versionB prices for this twiddle (same construction as qft.py)."""
    from forshor_qualtran.recursion_versions import ToomRecursivePhaseProduct
    size = max(left, right) + 1
    return ToomRecursivePhaseProduct.from_rules(qft.rules, size, qft.k, "B", "kmy", tsr=qft.stop_tsr)


def _v2(c: int) -> Optional[int]:
    """2-adic valuation of a nonzero integer; None for 0 (the child's turn is 0)."""
    if c == 0:
        return None
    c = abs(c)
    return (c & -c).bit_length() - 1


def _poly_eval(coeffs, x: Fraction) -> Fraction:
    acc = Fraction(0)
    for c in reversed(list(coeffs)):
        acc = Fraction(c) + x * acc
    return acc


@lru_cache(maxsize=None)
def _mirror_polys(k: int):
    from forshor_qualtran.mirrors import coeff_poly as CP, schedule as SC
    return tuple(tuple(p) for p in CP.coeff_polys(k, SC.raw_table(k)[0]["points"]))


def _bundle_polys(rules, k: int):
    try:
        rows = rules.bundles[k].doc["coeff_poly"]["rows"]
        return tuple(tuple(Fraction(int(c["num"]), int(c["den"])) for c in r["coeffs"])
                     for r in sorted(rows, key=lambda r: int(r["l"])))
    except Exception:                                                  # noqa: BLE001
        return None


def child_losses(rules, k: int, m: int) -> List[Optional[int]]:
    """Bits of resolution each of the q children loses at limb width m: v2(c_l(2^m)) (None = the child's
    turn vanishes). Both coefficient sources (the rules' bundle and the frozen schedule mirror) are
    evaluated; their sorted loss lists are combined elementwise by the SMALLER loss, so the result is a
    valid upper bound on every child's resolution whichever point set the wired recursion uses."""
    x = Fraction(2) ** m
    lists = []
    for polys in (_bundle_polys(rules, k), _mirror_polys(k)):
        if polys is None:
            continue
        vals = [_poly_eval(p, x) for p in polys]
        if any(v.denominator != 1 for v in vals):
            raise RuntimeError(f"Toom weight not an integer at k={k}, m={m}")
        lists.append(sorted((_v2(int(v)) for v in vals), key=lambda v: math.inf if v is None else v))
    if not lists:
        raise RuntimeError(f"no Toom coefficient rows for k={k}")
    out = lists[0]
    for other in lists[1:]:
        if len(other) != len(out):
            raise RuntimeError(f"coefficient sources disagree on q at k={k}")
        out = [a if b is None else (b if a is None else min(a, b)) for a, b in zip(out, other)]
    return out


def leaf_resolutions(pp, root_res: Optional[int]) -> Tuple[int, Dict[Optional[int], int]]:
    """(leaf width W, {resolution upper bound: leaf count}) of a ToomRecursivePhaseProduct whose root turn
    has resolution <= root_res (None: generic, every leaf generic)."""
    rows = pp.rules.tree(pp.w, pp.version, pp.k_top, pp.rule, pp.tsr)
    dist: Dict[Optional[int], int] = {root_res: 1}
    for row in rows:
        if row["stop"]:
            if sum(dist.values()) != int(row["nodes"]):
                raise RuntimeError("resolution walk lost leaves")
            return int(row["W"]), dist
        if root_res is None:
            dist = {None: sum(dist.values()) * int(pp.rules.data[row["k"]].q)}
            continue
        k, W = int(row["k"]), int(row["W"])
        losses = child_losses(pp.rules, k, -(-W // k))           # versionB / kmy: m = ceil(W / k)
        nxt: Dict[Optional[int], int] = {}
        for r, c in dist.items():
            for v in losses:
                rr = 0 if (v is None or r == 0) else max(0, r - v)
                nxt[rr] = nxt.get(rr, 0) + c
        dist = nxt
    raise RuntimeError("tree without a stop row")


def _pp_leaves(pp, ctrl: bool, cls: str = "generic", root_res: Optional[int] = None) -> Counter:
    out: Counter = Counter()
    W, dist = leaf_resolutions(pp, root_res)
    for r, c in dist.items():
        out[(LEAF, W, bool(ctrl), cls, r, W)] += int(c)
    return out


def leaf_fields(key: tuple) -> Tuple[int, int, bool, str, Optional[int]]:
    """(wx, wz, ctrl, cls, res) of a leaf key, v1 3-tuples included (square, generic)."""
    wx, ctrl = int(key[1]), bool(key[2])
    cls = str(key[3]) if len(key) > 3 and key[3] is not None else "generic"
    res = None if len(key) <= 4 or key[4] is None else int(key[4])
    wz = int(key[5]) if len(key) > 5 and key[5] is not None else wx
    return wx, wz, ctrl, cls, res


def census(bloq: Bloq, qec_cache: dict | None = None, cache: dict | None = None) -> Dict[tuple, int]:
    return get_cost_value(bloq, LeafCensus(qec_cache), costs_cache=cache if cache is not None else {})


def cells(c: Dict[tuple, int]) -> Tuple[int, int, int]:
    """(twiddle sites, leaf sites, other sites)."""
    tw = sum(k[4] * v for k, v in c.items() if k[0] == TW)
    lf = sum(leaf_fields(k)[0] * leaf_fields(k)[1] * v for k, v in c.items() if k[0] == LEAF)
    ot = sum(v for k, v in c.items() if k[0] == OTHER)
    return tw, lf, ot


def to_json(c: Dict[tuple, int]) -> list:
    return [[list(k), v] for k, v in sorted(c.items(), key=lambda kv: str(kv[0]))]


def from_json(rows: list) -> Dict[tuple, int]:
    return {tuple(k): v for k, v in rows}
