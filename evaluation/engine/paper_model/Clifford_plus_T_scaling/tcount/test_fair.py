"""Tests for `tcount.fair` (apples-to-apples pricing). Fast: synthetic censuses + one small real census."""
from __future__ import annotations

import gzip
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "Clifford_plus_T_scaling")):
    if p not in sys.path:
        sys.path.insert(0, p)
import forshor_qualtran  # noqa: E402,F401
from forshor_qualtran.catalysis import budget as B  # noqa: E402
from tcount import catalysis as CAT, fair as F, leaf_census as LC  # noqa: E402

INF = F.INF


def _brute_classes(r1, r2, top, s_cut):
    cl = t3 = syn = 0
    drop = 0.0
    for i in range(r1):
        for j in range(r2):
            s = top - i - j
            if s <= 2:
                cl += 1
            elif s == 3 and s_cut >= 3:
                t3 += 1
            elif 4 <= s <= s_cut:
                syn += 1
            elif s > max(s_cut, 3):
                drop += 2 * math.pi * 2.0 ** -s
    return cl, t3, syn, drop


@pytest.mark.parametrize("r1,r2,top", [(1, 1, 2), (3, 5, 8), (7, 4, 11), (6, 6, 3), (5, 9, 30)])
@pytest.mark.parametrize("s_cut", [3, 4, 6, 9, 40, INF])
def test_profile_classes_match_brute_force(r1, r2, top, s_cut):
    got = F.grid_profile(r1, r2, top).classes(s_cut)
    want = _brute_classes(r1, r2, top, s_cut)
    assert got[:3] == pytest.approx(want[:3])
    assert got[3] == pytest.approx(want[3], rel=1e-12, abs=1e-300)


@pytest.mark.parametrize("r1,r2,t", [(3, 5, 8), (17, 17, 34), (100, 101, 201)])
@pytest.mark.parametrize("b", [5, 12, 30, 60])
def test_tw_cat_without_cut_is_budget_twiddle_price(r1, r2, t, b):
    assert F.tw_cat(r1, r2, t, b, INF) == B.twiddle_price(r1, r2, t, b)[:2]


@pytest.mark.parametrize("r1,r2,t", [(17, 17, 34), (100, 101, 201)])
def test_tw_cat_cut_below_b_is_exact_and_cheaper(r1, r2, t):
    for s_A in (6, 10, 20):
        a, e = F.tw_cat(r1, r2, t, 40, s_A)
        assert e == 0.0
        assert a <= B.twiddle_price(r1, r2, t, 40)[0]


def test_aqft_cutoff_is_smallest_feasible():
    cen = {("tw", 50, 50, 100, 1000, 0): 3, ("tw", 20, 21, 41, 100, 0): 5}
    for budget in (1e-3, 1e-6, 1e-9):
        s = F.aqft_cutoff(cen, budget)
        assert F.aqft_dropped(cen, s) <= budget
        assert F.aqft_dropped(cen, s - 1) > budget


def test_synth_best_never_worse_than_published_controls():
    d = json.load(gzip.open(ROOT / "artifacts/figure_data_tcount_fair/exact/SHOR_k2.json.gz", "rt"))
    p = next(p for p in d["points"] if p["n"] == 251)
    cen = LC.from_json(p["trees"]["11.0"]["census"])
    assert F.price_synth(cen, "det", 0.005).delta_T <= CAT.price_rs_restructured(cen, 0.005) * (1 + 1e-9)
    assert F.price_synth(cen, "rand", 0.005).delta_T <= \
        CAT.price_rs_restructured(cen, 0.005, per_rot=CAT.mixed_fallback_t) * (1 + 1e-9)


def test_cat_reproduces_price_merged_without_cut():
    d = json.load(gzip.open(ROOT / "artifacts/figure_data_tcount_fair/exact/SHOR_k2.json.gz", "rt"))
    p = next(p for p in d["points"] if p["n"] == 251)
    cen = LC.from_json(p["trees"]["11.0"]["census"])
    for mixing, fam in ((True, "rand"), (False, "det")):
        m = CAT.price_merged(cen, 0.005, mixing=mixing)
        r = F.price_cat(cen, fam, 0.01, splits=[(0.25, 0.25)], single_width=True)
        assert r.meta["b"] == m.b
        assert r.delta_T - r.meta["prep_T"] == pytest.approx(m.delta_T - m.prep_T, rel=1e-12)


def test_hybrid_no_worse_than_its_parts():
    d = json.load(gzip.open(ROOT / "artifacts/figure_data_tcount_fair/exact/SHOR_k2.json.gz", "rt"))
    p = next(p for p in d["points"] if p["n"] == 251)
    cen = LC.from_json(p["trees"]["11.0"]["census"])
    for fam in ("det", "rand"):
        h = F.price_hybrid(cen, fam, 0.01).delta_T
        c = F.price_cat(cen, fam, 0.01).delta_T
        assert h <= c * (1 + 1e-9)


def test_error_budgets_respected():
    d = json.load(gzip.open(ROOT / "artifacts/figure_data_tcount_fair/aqft/SHOR_k2.json.gz", "rt"))
    p = next(p for p in d["points"] if p["n"] == 251)
    cen = LC.from_json(p["trees"]["11.0"]["census"])
    out = F.price_all(cen, 0.01, "aqft")
    s_A = int(out["rs_best"].meta["s_A"])
    assert F.aqft_dropped(cen, s_A) <= 0.1 * 0.01
    for r in ("rs_best", "fb_best", "mf_best"):
        m = out[r].meta
        assert m["dropped_syn"] + m["eps"] * m["R_syn"] <= 0.005 * (1 + 1e-9)
    for r in ("cat_det", "cat_rand"):
        m = out[r].meta
        assert m["cat_error"] <= m["split_cat"] * 0.01 * (1 + 1e-12)


def test_two_widths_never_worse_than_one():
    d = json.load(gzip.open(ROOT / "artifacts/figure_data_tcount_fair/exact/SHOR_k2.json.gz", "rt"))
    p = next(p for p in d["points"] if p["n"] == 251)
    cen = LC.from_json(p["trees"]["11.0"]["census"])
    for fam in ("det", "rand"):
        two = F.price_cat(cen, fam, 0.01)
        one = F.price_cat(cen, fam, 0.01, single_width=True)
        assert two.delta_T <= one.delta_T * (1 + 1e-12)
        assert two.meta["cat_error"] <= two.meta["split_cat"] * 0.01 * (1 + 1e-12)
        assert two.meta["b"] == max(two.meta["b_tw"], two.meta["b_leaf"])


@pytest.mark.parametrize("stage,n", [("SHOR", 251), ("MULT", 40), ("MODMUL", 124)])
def test_hybrid_rotation_share_never_overspent(stage, n):
    d = json.load(gzip.open(ROOT / f"artifacts/figure_data_tcount_fair/exact/{stage}_k2.json.gz", "rt"))
    p = min(d["points"], key=lambda p: abs(p["n"] - n))
    cen = LC.from_json(p["trees"]["11.0"]["census"])
    for fam in ("det", "rand"):
        m = F.price_hybrid(cen, fam, 0.01).meta
        assert m["eps"] * m["R_syn"] <= m["split_rot"] * 0.01 * (1 + 1e-9)


def test_fallback_constants_and_ordering():
    # KLMPP 2023 Table 1 (Clifford+T, T-count, mean, diamond): fallback 1.03 x + 5.75, mixed fallback 0.53 x + 4.86
    assert F.FALLBACK == (1.03, 5.75) and CAT.MIXED_FALLBACK == (0.53, 4.86)
    for eps in (1e-6, 1e-10, 1e-15):
        assert F.mf_site_t(eps) < F.fb_site_t(eps) < F.rs_site_t(eps)
