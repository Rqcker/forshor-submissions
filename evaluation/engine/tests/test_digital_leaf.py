"""PLAN_v2 Stage 5: the PhaseProduct digital-product leaf, the one-adder negated source, and the currency
(eps = 0.5 delta / R, unrounded). Reproduces the colleague's plan at n = 2048 to the unit."""
from __future__ import annotations

import math

import pytest

import sto_currency
from forshor_qualtran import cost_conventions as cc
from site_engine import crosscheck as xc, library as lib
from tcost import bloqs as tb, planner as tp, score as ts, tables as tt
from qualtran import QInt, QUInt
from qualtran.bloqs.arithmetic import CAdd, Product, Subtract
from qualtran.resource_counting import QECGatesCost, QubitCount, get_cost_value

PP = tt.PAIR


@pytest.mark.parametrize("a,b", [(1, 1), (2, 2), (5, 5), (8, 8), (23, 23), (54, 54), (3, 7), (7, 3), (2, 9)])
def test_product_toffoli_formula_matches_qualtran(a, b):
    """`Product` cannot take a symbolic width, so the leaf states its Toffoli count; here it is checked at concrete widths."""
    assert get_cost_value(Product(a, b), QECGatesCost()).toffoli == tb._product_toffolis(a, b)


@pytest.mark.parametrize("w", list(range(2, 40)) + [54, 100])
def test_leaf_engines_agree_and_rotations_are_two_per_bit(w):
    """Engine 1 (symbolic leaf form) and engine 2 (Qualtran `Product` and `CAdd`s) cost the leaf identically."""
    lc = tp.leaf_counts(PP, w)
    e2 = cc.leaf_cost(w, w, "product")
    assert lc["rotation"] == e2.rot == 2 * w
    assert 4 * (lc["and"] + lc["toffoli"] + lc["cswap"]) + lc["t"] == e2.base


def test_leaf_54_is_47936_t_and_108_rotations():
    e2 = cc.leaf_cost(54, 54, "product")
    assert (e2.base, e2.rot) == (47_936, 108)


def test_grid_leaf_is_still_available_and_ptp_uses_the_digital_triple_leaf():
    # Stage 17 re-pin (2026-10-28): PTP's leaf is the digital triple product; the grid stays as the cross-check (was
    # leaf_of(PTP) == "grid" and a triple "product" leaf raised)
    assert cc.leaf_cost(23, 23, "grid") == cc.TCost(4 * 529, 529)
    assert cc.leaf_of("PhaseTripleProduct") == "product"
    assert cc.leaf_cost(4, 4, "product", wy=4).rot == 3 * 4 - 2
    assert cc.leaf_cost(4, 4, "grid", wy=4) == cc.TCost(4 * (16 + 64), 64)   # 16 shared Ands, 64 CZPowGate cells


def test_product_leaf_static_qubits():
    """2 (wx + wz) operand and product qubits, plus the 2w - 1 ancillas of the `CAdd` in flight (a `CAdd(w)` is 4w qubits)."""
    for w in (2, 5, 23, 54):
        assert get_cost_value(CAdd(QUInt(w), QUInt(w)), QubitCount()) == 4 * w
        assert get_cost_value(Product(w, w), QubitCount()) == 4 * w
        assert tb.PairLeaf(w, w).my_static_costs(QubitCount()) == 6 * w - 1


@pytest.mark.parametrize("W", [2, 3, 5])
def test_add_neg_src_classical_action_and_cost(W):
    b = tb.AddNegSrcW(W)
    lo, hi = -(1 << (W - 1)), 1 << (W - 1)
    for a in range(lo, hi):
        for c in range(lo, hi):
            v = (c - a) % (1 << W)
            assert b.on_classical_vals(a, c) == {"a": a, "b": v - (1 << W) if v >= 1 << (W - 1) else v}
    assert b.build_call_graph(None) == {Subtract(QInt(W)): 1}


def _ids(i, k):
    subs = {s.id: s for s in lib.submissions()}
    return [t for t in subs[i].tables.values() if t.k == k and t.mode == PP][0]


def test_the_colleagues_plan_is_reproduced_to_the_unit():
    """2048 -> 364 -> 102 -> 54, then 231 base cases multiplied directly: 13,254,468 T, 24,948 rotations at 69.28 T,
    11,526,064 T of circuit; level costs 174,240 / 213,312 / 65,296 and 231 x 47,936."""
    T6, T4, T2 = _ids("293d51de2c05", 6), _ids("546fea33a7a4", 4), _ids("6f7d25984ff8", 2)
    # The colleague's plan uses ForShor's scanned widths (364 -> 102 -> 54): this is the Stage 5 cost-model check, so it is
    # priced at scanned widths (Stage 13: the boards use exact widths; this pin is unchanged).
    P = tt.Policy(mode=PP, bands=(tt.Band(2048, T6), tt.Band(364, T4), tt.Band(60, T2)), name="colleague", widths="scan")
    s = ts.score_policy(P, 2048, 0.01)
    assert [(r["width"], r["k"]) for r in s["ladder"]] == [(2048, 6), (364, 4), (102, 2), (54, None)]
    assert s["gate_counts"]["rotation"] == 24_948
    assert s["t_breakdown"]["t_from_toffoli_class"] == 11_526_064 == 174_240 + 213_312 + 65_296 + 231 * 47_936
    assert s["t_breakdown"]["ts_per_rotation"] == pytest.approx(69.28026515506433, rel=1e-12)
    assert s["t_count"] == pytest.approx(13_254_468.055088544, rel=1e-12)
    assert round(s["t_count"]) == 13_254_468
    r = xc.cross_check(P, 2048, 0.01)
    assert r["agree"], r
    for T, w2, nodes, want in ((T6, 364, 1, 174_240), (T4, 102, 11, 213_312), (T2, 54, 77, 65_296)):
        assert cc.level_cost(cc.census_key(T), w2, cc.SITE, 2).base * nodes == want


def test_currency_is_half_delta_unrounded():
    assert sto_currency.ROTATION_FRACTION == 0.5 and sto_currency.NEGSRC == "subtract"
    # Stage 17 re-pin: the PTP leaf moved numbers (was tcost-v3 / sto-site-v2)
    assert sto_currency.MODEL_VERSION == "tcost-v4" and sto_currency.SITE_MODEL_VERSION == "sto-site-v3"
    t = sto_currency.t_count({"t": 0, "toffoli": 0, "and": 10, "cswap": 0, "rotation": 24_948}, delta=0.01)
    assert t["rotation_eps"] == pytest.approx(0.005 / 24_948)
    assert t["ts_per_rotation"] == pytest.approx(3.075 * math.log2(24_948 / 0.005) + 0.86, rel=1e-12)
    assert t["ts_per_rotation"] != round(t["ts_per_rotation"])
    assert t["t_count"] == pytest.approx(40 + 24_948 * t["ts_per_rotation"], rel=1e-12)
