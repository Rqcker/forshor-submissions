"""Apples-to-apples pricing: catalysis vs synthesis on the SAME circuit, the SAME error budget and
the SAME QFT structure.

WHAT WAS UNFAIR BEFORE
    Merged catalysis drops or rounds the deep QFT-twiddle rows for free (an implicit approximate QFT
    charged to its own error share), while every synthesis control had to synthesise every site of the
    exact, Toom-decomposed twiddle. Mixed P-kicks were certified with an ensemble (diamond) bound
    although the text fixed the draws per leaf.

THE CONTRACT HERE
    Every method prices the same census (`tcount.leaf_census`) and can use the best realisation it has
    for each component. The total budget delta (TV of the output distribution) splits as
        structure  0.5 delta   arithmetic eta (fixed by the dump) [+ AQFT truncation in regime "aqft"]
        synthesis  0.5 delta   the method's own approximations: rotation synthesis (incl. replacing a
                               rotation by the identity when its angle is below tolerance), catalyst
                               roundings, catalyst preparation
    REGIME "exact"  the QFT is structurally exact (s_A = inf); any twiddle approximation is paid from
                    the synthesis half by every method alike.
    REGIME "aqft"   0.1 delta of the structure half truncates every QFT-twiddle site of resolution
                    s > s_A (one global Coppersmith cutoff, the smallest s_A whose dropped phases sum to
                    <= 0.1 delta). The SAME truncated circuit is then realised by every method.

    FAMILY "det"    no random compiling (nothing needs fresh classical draws per shot):
                        RS-best      Ross-Selinger 3.075 log2(1/eps) + 0.86 per rotation (unitary, op norm)
                        FB-best      fallback 1.03 log2(1/eps_dia) + 5.75 (KLMPP 2023 Table 1, mean T;
                                     measurement + classically controlled correction, TV = 1/2 diamond)
                        cat          merged catalysis, deterministic kicks (mixing=False), prep by fallback
                        hybrid       catalytic twiddles + per leaf the cheaper of catalytic / fallback
    FAMILY "rand"   randomised compiling -- a FRESH, independent draw per shot, per kick, per step; the
                    channel averaged over draws is certified (TV <= 1/2 diamond for the mixed parts):
                        MF-best      mixed fallback 0.53 log2(1/eps_dia) + 4.86 (KLMPP 2023)
                        cat          merged catalysis, mixed classical kicks (mixing=True)
                        hybrid       catalytic twiddles + per leaf the cheaper of catalytic / MF

SYNTHESIS REALISATIONS (per census item, the cheapest at the current eps)
    twiddle PhaseProdUsing(2pi/2^t, r1, r2); site (i, j) has resolution s = t - i - j (2 <= s <= t):
        toom      the production Toom decomposition: rot0 generic rotations (base keeps its T0);
                  exact, so it cannot drop anything
        grid      the undecomposed grid: a kept cell costs its And (4 T) + the rotation
                  (s <= 2 Clifford, s = 3 one T, s >= 4 synthesised); cells beyond the cutoff vanish
        product   p = x*z digitally (4 r1 r2 Ands), one rotation per kept bit of p (resolution t - l)
    leaf ("leaf", wx, ctrl, cls, res, wz):
        grid      the production cells (their Ands stay in the base), one rotation per cell; a
                  controlled leaf folds ctrl AND x_i once per row (wx Ands) instead of an And per cell
                  -- the same fold catalysis gets
        product   p = x*z (4 wx wz Ands, ctrl folded into x: wx Ands), one rotation per bit of p
        dyadic leaves (res known): per-site resolution res - i - j, so s <= 2 sites are Clifford, s = 3 is
        one T -- the exactness catalysis gets for dyadic turns, given to synthesis too
    identity cutoff s_id (synthesis methods, both regimes): twiddle sites/bits of resolution s > s_id are
        synthesised as the identity (angle 2 pi 2^-s, charged to the synthesis half). s_id is optimised.

The shared base T(11) - 11 R and the refunds are those of `forshor_qualtran.catalysis.budget`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Mapping, Optional, Sequence, Tuple

import numpy as np

from . import catalysis as CAT

TWO_PI = 2.0 * math.pi
AND_T = 4.0
INF = 10 ** 9
S_ID_MAX = 160                    # identity cutoffs scanned: every twiddle site finer than this is ~0


def _B():
    return CAT._budget_module()


# ----------------------------------------------------------------------------- per-site synthesis

def rs_site_t(eps_tv: float) -> float:
    """Ross-Selinger T per rotation at operator-norm eps (= its TV share)."""
    return CAT.rs_det_t(1.0, eps_tv)


def mf_site_t(eps_tv: float) -> float:
    """Mixed fallback T per rotation whose TV share is eps_tv (diamond eps = 2 eps_tv)."""
    a, c = CAT.MIXED_FALLBACK
    return a * math.log2(1.0 / (2.0 * eps_tv)) + c


#: KLMPP 2023 (arXiv:2203.10064) Table 1, Clifford+T T-count, mean, diamond eps: fallback [BRS15b improved]
FALLBACK = (1.03, 5.75)


def fb_site_t(eps_tv: float) -> float:
    """Fallback synthesis (KLMPP 2023): a measurement + classically controlled correction, no random
    draws, expected T = 1.03 log2(1/eps_dia) + 5.75 at diamond eps_dia = 2 eps_tv."""
    a, c = FALLBACK
    return a * math.log2(1.0 / (2.0 * eps_tv)) + c


#: the synthesiser each family uses for leaves (hybrid) and the catalyst's prep rotations
FAMILY_SITE_T = {"det": fb_site_t, "rand": mf_site_t}
SYNTH_ROUTES = {"rs_best": ("det", rs_site_t), "fb_best": ("det", fb_site_t), "mf_best": ("rand", mf_site_t)}
FAMILY_MIXING = {"det": False, "rand": True}


# ----------------------------------------------------------------------------- resolution classes

@lru_cache(maxsize=None)
def _diag_counts(r1: int, r2: int) -> np.ndarray:
    """counts[d] = #{(i, j): i < r1, j < r2, i + j = d}, d = 0 .. r1 + r2 - 2."""
    d = np.arange(r1 + r2 - 1, dtype=np.int64)
    lo = np.maximum(0, d - (r2 - 1))
    hi = np.minimum(r1 - 1, d)
    return np.maximum(0, hi - lo + 1).astype(np.float64)


class ResProfile:
    """Sites (or bits) by resolution s = top - d, d = 0..len-1. Prefix sums over s make every cutoff O(1)."""

    def __init__(self, top: int, counts: np.ndarray):
        self.top = int(top)
        s = self.top - np.arange(len(counts))            # descending
        order = np.argsort(s)                            # ascending s
        self.s = s[order].astype(np.int64)
        c = counts[order].astype(np.float64)
        self.cum = np.concatenate([[0.0], np.cumsum(c)])         # cum[i] = sum of c over s < self.s[i]
        with np.errstate(over="ignore", under="ignore"):
            w = c * np.exp2(-self.s.astype(np.float64))
        self.tail = np.concatenate([np.cumsum(w[::-1])[::-1], [0.0]])   # tail[i] = sum over index >= i
        self.total = float(c.sum())

    def _n_le(self, x: int) -> float:
        return float(self.cum[int(np.searchsorted(self.s, x, side="right"))])

    def classes(self, s_cut: int) -> Tuple[float, float, float, float]:
        """(# s <= 2 [Clifford or identity], # s == 3 kept, # 4 <= s <= s_cut, dropped phase TV
        2 pi sum 2^-s over s > max(s_cut, 3))."""
        cliff = self._n_le(2)
        t3 = (self._n_le(3) - cliff) if s_cut >= 3 else 0.0
        syn = max(0.0, self._n_le(s_cut) - self._n_le(3)) if s_cut >= 4 else 0.0
        i = int(np.searchsorted(self.s, max(s_cut, 3), side="right"))
        return cliff, t3, syn, float(TWO_PI * self.tail[i])


@lru_cache(maxsize=None)
def grid_profile(r1: int, r2: int, top: int) -> ResProfile:
    return ResProfile(top, _diag_counts(r1, r2))


@lru_cache(maxsize=None)
def bits_profile(nbits: int, top: int) -> ResProfile:
    """Bits l = 0..nbits-1 of a product register, bit l at resolution top - l."""
    return ResProfile(top, np.ones(nbits, dtype=np.float64))


# ----------------------------------------------------------------------------- AQFT cutoff

def aqft_dropped(census: Mapping[tuple, float], s_A: int) -> float:
    """TV of truncating every QFT-twiddle site of resolution > s_A (operator-norm union bound)."""
    tot = 0.0
    for key, cnt in census.items():
        if key[0] == "tw":
            tot += grid_profile(int(key[1]), int(key[2]), int(key[3])).classes(s_A)[3] * float(cnt)
    return tot


def aqft_cutoff(census: Mapping[tuple, float], budget: float, s_lo: int = 3, s_hi: int = 400) -> int:
    """The smallest global cutoff s_A whose truncation error is <= budget (binary search; monotone)."""
    if not any(k[0] == "tw" for k in census):
        return INF
    lo, hi = s_lo, s_hi
    if aqft_dropped(census, hi) > budget:
        raise ValueError("AQFT budget too small for s_hi")
    while lo < hi:
        mid = (lo + hi) // 2
        if aqft_dropped(census, mid) <= budget:
            hi = mid
        else:
            lo = mid + 1
    return lo


# ----------------------------------------------------------------------------- synthesis routes

@dataclass(frozen=True)
class SynthOption:
    """One realisation of one census item at a given cutoff: T = fixed + c * n_syn; dropped TV."""
    name: str
    fixed_T: float                # T independent of eps (Ands, T gates, minus refunds)
    n_syn: float                  # synthesised rotations
    dropped: float                # TV paid by identity-synthesised sites (synthesis half)


@lru_cache(maxsize=None)
def _tw_options(r1: int, r2: int, t: int, rot0: float, t0: float, s_A: int, s_id: int) -> tuple:
    s_cut = min(s_A, s_id)
    out = []
    if s_A >= t:                                   # Toom is exact: only when nothing is cut structurally
        out.append(SynthOption("toom", 0.0, rot0, 0.0))
    g = grid_profile(r1, r2, t)
    cl, t3, syn, _ = g.classes(s_cut)
    drop_syn = g.classes(s_cut)[3] - g.classes(s_A)[3]          # beyond s_id but inside the AQFT band
    kept = cl + t3 + syn
    out.append(SynthOption("grid", AND_T * kept + t3 - t0, syn, max(0.0, drop_syn)))
    p = bits_profile(r1 + r2, t)
    cl, t3, syn, _ = p.classes(s_cut)
    drop_syn = p.classes(s_cut)[3] - p.classes(s_A)[3]
    out.append(SynthOption("product", AND_T * 4.0 * r1 * r2 + t3 - t0, syn, max(0.0, drop_syn)))
    return tuple(out)


@lru_cache(maxsize=None)
def _leaf_options(wx: int, wz: int, ctrl: bool, res: Optional[int]) -> tuple:
    ctrl_refund = AND_T * wx * wz if ctrl else 0.0     # the base's per-cell control And
    fold = AND_T * wx if ctrl else 0.0                 # ctrl AND x_i once per row
    cell_refund = AND_T * wx * wz
    if res is None:                                    # generic turn: every site synthesised
        grid = SynthOption("grid", fold - ctrl_refund, float(wx * wz), 0.0)
        prod = SynthOption("product", AND_T * 4.0 * wx * wz + fold - cell_refund - ctrl_refund,
                           float(wx + wz), 0.0)
        return (grid, prod)
    g = grid_profile(wx, wz, res)                      # dyadic: site (i, j) at resolution res - i - j
    cl, t3, syn, _ = g.classes(INF)
    grid = SynthOption("grid", fold - ctrl_refund + t3, syn, 0.0)
    p = bits_profile(wx + wz, res)
    cl, t3, syn, _ = p.classes(INF)
    prod = SynthOption("product", AND_T * 4.0 * wx * wz + fold - cell_refund - ctrl_refund + t3, syn, 0.0)
    return (grid, prod)


@dataclass
class RoutePrice:
    route: str
    family: str
    regime_s_A: int
    delta_T: float                                     # T above the shared base
    meta: Dict[str, float] = field(default_factory=dict)


_OTHER_OPTS = (SynthOption("site", 0.0, 1.0, 0.0),)


def _synth_items(census, s_A: int, s_id: int):
    items = []
    for key, cnt in census.items():
        cnt = float(cnt)
        if cnt <= 0:
            continue
        if key[0] == "tw":
            items.append((cnt, _tw_options(int(key[1]), int(key[2]), int(key[3]), float(key[4]),
                                           float(key[5]), s_A, s_id)))
        elif key[0] == "leaf":
            wx, wz, ctrl, _cls, res = _B().leaf_key_fields(key)
            items.append((cnt, _leaf_options(wx, wz, ctrl, res)))
        elif key[0] == "other":
            items.append((cnt, _OTHER_OPTS))
        else:
            raise ValueError(key)
    return items


def _fixed_point(items, budget: float, site_t, extra_syn: float = 0.0, passes: int = 6):
    """Choose per item the cheapest option at one shared per-rotation eps; eps = (budget - dropped) / R.
    Returns (T, eps, R, dropped, choice counts) or None when infeasible."""
    R = sum(cnt * min(o.n_syn for o in opts) for cnt, opts in items) + extra_syn
    E = 0.0
    best = None
    for _ in range(passes):
        room = budget - E
        if room <= 0:
            return None
        eps = room / max(R, 1.0)
        c = site_t(eps)
        T = 0.0; R_new = extra_syn; E_new = 0.0; ch: Dict[str, float] = {}
        for cnt, opts in items:
            o = min(opts, key=lambda o: o.fixed_T + c * o.n_syn)
            T += cnt * (o.fixed_T + c * o.n_syn)
            R_new += cnt * o.n_syn
            E_new += cnt * o.dropped
            ch[o.name] = ch.get(o.name, 0.0) + cnt
        feasible = E_new + eps * R_new <= budget * (1 + 1e-9)
        if feasible and (best is None or T < best[0]):
            best = (T, eps, R_new, E_new, ch, c)
        if abs(R_new - R) <= 1e-9 * max(R, 1.0) and abs(E_new - E) <= 1e-12:
            break
        R, E = max(R_new, 1.0), E_new
    return best


_SYNTH_ALIAS = {"det": "rs_best", "rand": "mf_best", "rs": "rs_best", "fb": "fb_best", "mf": "mf_best"}


def price_synth(census: Mapping[tuple, float], route: str, delta_syn: float, s_A: int = INF,
                s_ids: Optional[Sequence[int]] = None) -> RoutePrice:
    """rs_best / fb_best / mf_best (aliases: det -> rs, rand -> mf): every item's cheapest realisation, one
    shared eps, the identity cutoff s_id optimised. delta_syn = the synthesis half (TV)."""
    route = _SYNTH_ALIAS.get(route, route)
    family, site_t = SYNTH_ROUTES[route]
    if s_ids is None:
        # the optimum identity cutoff sits where a site's angle meets eps: scan a window around it
        r0 = _fixed_point(_synth_items(census, s_A, INF), delta_syn, site_t)
        s_star = int(math.ceil(math.log2(TWO_PI / r0[1]))) if r0 else S_ID_MAX
        top = min(s_A, S_ID_MAX)
        s_ids = sorted(set([s for s in range(max(4, s_star - 12), min(top, s_star + 12) + 1)] + [INF]))
    best = None
    for s_id in s_ids:
        r = _fixed_point(_synth_items(census, s_A, s_id), delta_syn, site_t)
        if r is not None and (best is None or r[0] < best[0][0]):
            best = (r, s_id)
    if best is None:
        raise ValueError("synthesis budget infeasible")
    (T, eps, R, E, ch, c), s_id = best
    meta = {"eps": eps, "c_site": c, "R_syn": R, "dropped_syn": E, "s_id": float(min(s_id, INF))}
    meta.update({f"n_{k}": v for k, v in ch.items()})
    return RoutePrice(route, family, s_A, T, meta)


# ----------------------------------------------------------------------------- catalysis (merged)

@lru_cache(maxsize=None)
def tw_cat(r1: int, r2: int, t: int, b: int, s_A: int) -> Tuple[int, float]:
    """(native Ands, catalysis op-norm error) of one merged twiddle whose sites beyond s_A are removed
    structurally. s_A >= t: `budget.twiddle_price` unchanged. b < s_A < t: rows are rounded at b as there
    (the rounded value only shrinks, so its error bound holds). s_A <= b: rows add the kept bits exactly
    (floor truncation, no carry-in), catalysis error 0."""
    B = _B()
    if s_A >= t or b < s_A:
        a, e, _ = B.twiddle_price(r1, r2, t, b)
        return a, e
    lo, hi = (r1, r2) if r1 <= r2 else (r2, r1)
    i = np.arange(lo, dtype=np.int64)
    depth = t - i
    L = np.minimum(s_A, depth)
    m = np.clip(hi - np.maximum(0, depth - s_A), 0, hi)
    ands = np.where((m <= 0) | (depth <= 0), 0, m + L - 1)
    return int(ands.sum()), 0.0


def _split_census(census):
    tw = {k: v for k, v in census.items() if k[0] == "tw"}
    rest = {k: v for k, v in census.items() if k[0] != "tw"}
    return tw, rest


SPLITS = ((0.25, 0.25), (0.40, 0.10), (0.45, 0.05), (0.49, 0.01), (0.499, 0.001), (0.10, 0.40))


def _prep_T(b: int, budget: float, site_t) -> float:
    m = max(0, b - 3)
    return (m * site_t(budget / m) + 1.0) if m else 0.0


def _smallest_b(err_of, budget: float, b_min: int, b_max: int) -> Optional[int]:
    """Smallest b in [b_min, b_max] with err_of(b) <= budget; errors are non-increasing in b (tested in
    `forshor_qualtran.catalysis`), so a binary search is exact."""
    if err_of(b_max) > budget:
        return None
    lo, hi = b_min, b_max
    while lo < hi:
        mid = (lo + hi) // 2
        if err_of(mid) <= budget:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _best_widths(tw_at, leaf_at, cat_budget: float, has_tw: bool, b_min: int, b_max: int):
    """Two kick widths into ONE catalyst: twiddle rows at b_tw, leaf/other kicks at
    b_leaf. Adding a constant (or a register window) whose lowest bit sits at 2^-b' into the TOP b'
    qubits of a b-qubit phase gradient is exact for any b' <= b, so each kind of kick can use its own
    width; the catalyst is max(b_tw, b_leaf) qubits. Errors add: e_tw(b_tw) + e_leaf(b_leaf) <= budget.
    Returns (ands, b_tw, b_leaf, err) minimising the native Ands, or None. tw_at(b), leaf_at(b) ->
    (ands, err), errors non-increasing and Ands non-decreasing in b, so b_leaf is scanned from its
    smallest feasible value up to the single-width solution."""
    bl_min = _smallest_b(lambda b: leaf_at(b)[1], cat_budget, b_min, b_max)
    if bl_min is None:
        return None
    b_one = _smallest_b(lambda b: tw_at(b)[1] + leaf_at(b)[1], cat_budget, b_min, b_max)
    hi = b_one if b_one is not None else b_max
    best = None
    for bl in range(bl_min, max(bl_min, hi) + 1):
        aL, eL = leaf_at(bl)
        rem = cat_budget - eL
        if rem < 0:
            continue
        if has_tw:
            bt = _smallest_b(lambda b: tw_at(b)[1], rem, b_min, b_max)
            if bt is None:
                continue
            aT, eT = tw_at(bt)
        else:
            bt, aT, eT = b_min, 0.0, 0.0
        cand = (aT + aL, bt, bl, eT + eL)
        if best is None or cand[0] < best[0] or (cand[0] == best[0] and max(bt, bl) < max(best[1], best[2])):
            best = cand
    return best


def price_cat(census: Mapping[tuple, float], family: str, delta: float, s_A: int = INF,
              splits: Sequence[Tuple[float, float]] = SPLITS, b_min: int = 4, b_max: int = 126,
              single_width: bool = False) -> RoutePrice:
    """Merged catalysis on the (optionally AQFT-truncated) census. The synthesis half is split
    (catalysis, prep) and the split optimised over `splits` (fractions of delta). Kicks deterministic
    (family det) or mixed with fresh draws (family rand); prep rotations synthesised by the family's
    method. Twiddle rows and leaf kicks use their own widths into one catalyst (`_best_widths`);
    `single_width=True` forces one b (the published v2 rule: with s_A = INF and split (0.25, 0.25) it
    equals `price_merged` up to the prep synthesiser)."""
    B = _B()
    mixing = FAMILY_MIXING[family]
    site_t = FAMILY_SITE_T[family]
    tw, rest = _split_census(census)
    refund = B.refund_T(census)
    tw_c: Dict[int, tuple] = {}
    lf_c: Dict[int, object] = {}

    def tw_at(b):
        if b not in tw_c:
            a_tw, e_tw = 0.0, 0.0
            for key, cnt in tw.items():
                a, e = tw_cat(int(key[1]), int(key[2]), int(key[3]), b, s_A)
                a_tw += a * float(cnt); e_tw += e * float(cnt)
            tw_c[b] = (a_tw, e_tw)
        return tw_c[b]

    def cp_at(b):
        if b not in lf_c:
            lf_c[b] = B.price_at(rest, b, mixing=mixing)
        return lf_c[b]

    def leaf_at(b):
        cp = cp_at(b)
        return cp.ands, cp.catalysis_error

    best = None
    for fc, fr in splits:
        cat_budget, rot_budget = fc * delta, fr * delta
        if single_width:
            b = _smallest_b(lambda bb: tw_at(bb)[1] + leaf_at(bb)[1], cat_budget, b_min, b_max)
            if b is None:
                continue
            sol = (tw_at(b)[0] + leaf_at(b)[0], b, b, tw_at(b)[1] + leaf_at(b)[1])
        else:
            sol = _best_widths(tw_at, leaf_at, cat_budget, bool(tw), b_min, b_max)
            if sol is None:
                continue
        ands, bt, bl, err = sol
        b = max(bt, bl)
        prep = _prep_T(b, rot_budget, site_t)
        T = ands * AND_T - refund + prep
        if best is None or T < best.delta_T:
            cp = cp_at(bl)
            best = RoutePrice("cat", family, s_A, T,
                              {"b": b, "b_tw": bt, "b_leaf": bl, "split_cat": fc, "split_rot": fr,
                               "prep_T": prep, "cat_error": err, "tw_ands": tw_at(bt)[0] if tw else 0.0,
                               "leaf_ands": cp.ands, "leaves_Y": cp.chose_Y, "leaves_P": cp.chose_P})
    if best is None:
        raise ValueError("no b meets the catalysis budget")
    return best


def price_hybrid(census: Mapping[tuple, float], family: str, delta: float, s_A: int = INF,
                 splits: Sequence[Tuple[float, float]] = SPLITS, b_min: int = 4, b_max: int = 126,
                 passes: int = 8) -> RoutePrice:
    """Catalytic twiddles + per leaf / `other` item the cheaper of its catalytic realisation (errors into
    the catalysis share, kicks at their own width b_leaf) and its best synthesis realisation (errors into
    the rotation share, together with the b - 3 prep rotations). Fixed point over (widths, eps,
    assignment); eps is recomputed from the options ACTUALLY chosen, so the rotation share
    is never overspent; the split is optimised."""
    B = _B()
    mixing = FAMILY_MIXING[family]
    site_t = FAMILY_SITE_T[family]
    pricer = B._leaf_pricer("leaf")
    tw, rest = _split_census(census)
    refund_tw = B.refund_T(tw)
    keys = list(rest.items())
    syn_opts = {k: _synth_items({k: 1}, INF, INF)[0][1] for k, _ in keys}

    cc_cache: Dict[tuple, tuple] = {}

    def cat_cost(key, b):
        if (key, b) not in cc_cache:
            cc_cache[(key, b)] = _cat_cost(key, b)
        return cc_cache[(key, b)]

    def _cat_cost(key, b):
        """(T above base incl. refunds, TV error, native Ands) of one catalytic item at kick width b."""
        if key[0] == "leaf":
            wx, wz, ctrl, _cls, res = B.leaf_key_fields(key)
            lc = pricer(wx, wz, b, ctrl, res, mixing)
            refund = AND_T * wx * wz * (2 if ctrl else 1)
            return lc.ands * AND_T - refund, lc.err_op + 0.5 * lc.err_diamond, lc.ands
        from forshor_qualtran.catalysis import spec as S
        oc = S.other_site_cost(b, mixing=mixing)
        return oc.ands * AND_T, oc.err_op + 0.5 * oc.err_diamond, oc.ands

    tw_c: Dict[int, tuple] = {}

    def tw_at(b):
        if b not in tw_c:
            a_tw, e_tw = 0.0, 0.0
            for k, c in tw.items():
                a, e = tw_cat(int(k[1]), int(k[2]), int(k[3]), b, s_A)
                a_tw += a * float(c); e_tw += e * float(c)
            tw_c[b] = (a_tw, e_tw)
        return tw_c[b]

    best = None
    for fc, fr in splits:
        cat_budget, rot_budget = fc * delta, fr * delta
        assign = {k: "cat" for k, _ in keys}
        seen = set()
        for _ in range(passes):
            cat_keys = [(k, c) for k, c in keys if assign[k] == "cat"]

            def leaf_at(b, cat_keys=cat_keys):
                a = e = 0.0
                for k, c in cat_keys:
                    _, ee, aa = cat_cost(k, b)
                    a += aa * float(c); e += ee * float(c)
                return a, e
            sol = _best_widths(tw_at, leaf_at, cat_budget, bool(tw), b_min, b_max)
            if sol is None:
                break
            _, bt, bl, _err = sol
            b = max(bt, bl)
            syn_keys = [(k, c) for k, c in keys if assign[k] == "syn"]
            R_act = sum(float(c) * min(o.n_syn for o in syn_opts[k]) for k, c in syn_keys) + max(0, b - 3)
            eps = rot_budget / max(R_act, 1.0)
            ch = {}
            for _it in range(8):
                cst = site_t(eps)
                ch = {k: min(syn_opts[k], key=lambda o: o.fixed_T + cst * o.n_syn) for k, _ in syn_keys}
                R_act = sum(float(c) * ch[k].n_syn for k, c in syn_keys) + max(0, b - 3)
                eps_new = rot_budget / max(R_act, 1.0)
                if abs(eps_new - eps) <= 1e-12 * eps:
                    break
                eps = eps_new
            eps = min(eps, rot_budget / max(R_act, 1.0))          # feasible at the options actually chosen
            cst = site_t(eps)
            T = tw_at(bt)[0] * AND_T - refund_tw + (max(0, b - 3) * cst + 1.0)
            T += sum(float(c) * (ch[k].fixed_T + cst * ch[k].n_syn) for k, c in syn_keys)
            T += sum(float(c) * cat_cost(k, bl)[0] for k, c in cat_keys)
            if best is None or T < best.delta_T:
                best = RoutePrice("hybrid", family, s_A, T,
                                  {"b": b, "b_tw": bt, "b_leaf": bl, "split_cat": fc, "split_rot": fr,
                                   "eps": eps, "c_site": cst, "R_syn": R_act,
                                   "items_syn": sum(float(c) for _, c in syn_keys),
                                   "items_cat": sum(float(c) for _, c in cat_keys)})
            new = {}
            for k, c in keys:
                ct = cat_cost(k, bl)[0]
                so = min(syn_opts[k], key=lambda o: o.fixed_T + cst * o.n_syn)
                new[k] = "syn" if so.fixed_T + cst * so.n_syn < ct else "cat"
            sig = tuple(sorted(new.items()))
            if new == assign or sig in seen:
                break
            seen.add(sig)
            assign = new
    if best is None:
        raise ValueError("hybrid infeasible")
    return best


# ----------------------------------------------------------------------------- one point, every route

ROUTES = ("rs_best", "fb_best", "cat_det", "hybrid_det", "mf_best", "cat_rand", "hybrid_rand")


def price_all(census: Mapping[tuple, float], delta: float = 1e-2, regime: str = "exact",
              aqft_fraction: float = 0.10, routes: Sequence[str] = ROUTES,
              delta_syn: Optional[float] = None) -> Dict[str, RoutePrice]:
    """Every fair route on one census. `delta_syn` overrides the synthesis half (default 0.5 delta) for
    precision sweeps at fixed structure."""
    ds = 0.5 * delta if delta_syn is None else float(delta_syn)
    s_A = aqft_cutoff(census, aqft_fraction * delta) if regime == "aqft" else INF
    out: Dict[str, RoutePrice] = {}
    for r in routes:
        fam = "det" if r.endswith("det") or r in ("rs_best", "fb_best") else "rand"
        if r in SYNTH_ROUTES:
            out[r] = price_synth(census, r, ds, s_A)
        elif r.startswith("cat"):
            out[r] = price_cat(census, fam, 2.0 * ds, s_A)
        else:
            out[r] = price_hybrid(census, fam, 2.0 * ds, s_A)
        out[r].meta["s_A"] = float(min(s_A, INF))
    return out
