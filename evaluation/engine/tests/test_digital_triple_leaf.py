"""Stage 17 (2026-10-28): the digital PhaseTripleProduct leaf (`tcost.bloqs.DigitalTripleLeaf`) and its use as the PTP
board's leaf.

(a) basis-state phases: the decomposed digital leaf (every sub-bloq flattened down to And, CNOT, X and Qualtran adders;
    only the rotations kept as `PhaseBit`) against the decomposed grid leaf `TripleLeaf`, exhaustively over all signed
    inputs at several (wx, wy, wz); the operands come back unchanged and every ancilla clean (`Free` and And^dagger
    raise on a dirty qubit in Qualtran's classical simulation);
(b) the operator norm: the leaf tensor-contracted to its unitary on the clean-ancilla subspace (`Free` = <0|) against
    diag(exp(i pi phi x y z)), ||U - D||_2 ~ 0;
(c) both engines agree on T, rotations and Toffoli-class (And) counts of whole PTP policies, and on the grid-leaf
    cross-check;
plus the leaf's costs (symbolic = concrete = engine 2, Qualtran's own `Product`), its qubit count, the register size
argument, and the board, seeds and Lean runs moved to the new leaf.
"""
from __future__ import annotations

import cmath
import hashlib
import itertools
import json
import math
from fractions import Fraction

import numpy as np
import pytest
from qualtran import QUInt
from qualtran.bloqs.arithmetic import CAdd, Product
from qualtran.resource_counting import QECGatesCost, QubitCount, get_cost_value
from qualtran.simulation.classical_sim import do_phased_classical_simulation

from forshor_qualtran import cost_conventions as cc
from site_engine import crosscheck as xc, frontier as fr, library as lib, policy_view as pv, seed_policies as sp
from site_engine import submitted
from tcost import bloqs as tb, planner as tp, score as ts, tables as tt

PTP = tt.TRIPLE
PHI = Fraction(3, 7)                     # non-dyadic: no cell or bit is accidentally Clifford
SEEDS = lib.DATA / "forshor_submitted"


def _signed(u, w):
    return u - (1 << w) if u >> (w - 1) else u


def _want(phi, x, y, z):
    return cmath.exp(1j * math.pi * tb.fold(phi * x * y * z))


def _sim(bloq):
    keep = (tb.PhaseBit, tb.PhaseCell)
    return bloq.decompose_bloq().flatten(lambda bi: not isinstance(bi.bloq, keep))


def _inputs(wx, wy, wz):
    r = lambda w: range(-(1 << (w - 1)), 1 << (w - 1))
    return itertools.product(r(wx), r(wy), r(wz))


# ------------------------------------------------------------------------------------------------ (a) basis-state phases
@pytest.mark.parametrize("w", [(1, 1, 1), (2, 1, 1), (1, 2, 3), (3, 1, 2), (2, 2, 2), (3, 2, 1), (2, 3, 3), (3, 3, 3), (4, 2, 2)])
@pytest.mark.parametrize("phi", [PHI, Fraction(1, 1 << 5)])
def test_digital_leaf_phase_equals_the_grid_leaf_on_every_signed_input(w, phi):
    dig, grid = _sim(tb.DigitalTripleLeaf(*w, phi)), _sim(tb.TripleLeaf(*w, phi))
    n = 0
    for x, y, z in _inputs(*w):
        d = {"x": x, "y": y, "z": z}
        out, ph = do_phased_classical_simulation(dig, d)          # raises if an ancilla is freed dirty
        out_g, ph_g = do_phased_classical_simulation(grid, d)
        assert {k: int(v) for k, v in out.items()} == d == {k: int(v) for k, v in out_g.items()}
        assert abs(ph - ph_g) < 1e-9 and abs(ph - _want(phi, x, y, z)) < 1e-9, (w, d)
        n += 1
    assert n == 1 << sum(w)


def test_a_dirty_ancilla_is_caught():
    """The witnesses refuse a wrong uncompute: `UProduct` given a result that is not a*b, and a product register left
    dirty (`Free` of a non-zero qubit)."""
    cb = _sim(tb.UProduct(2, 3, uncompute=True))
    with pytest.raises(Exception):
        do_phased_classical_simulation(cb, {"a": 3, "b": 5, "result": 14})
    assert do_phased_classical_simulation(cb, {"a": 3, "b": 5, "result": 15})[0] == {"a": 3, "b": 5}


@pytest.mark.parametrize("na,nb", [(1, 1), (1, 4), (2, 3), (3, 2), (3, 3), (2, 5)])
def test_uproduct_witness_multiplies_and_csub_subtracts(na, nb):
    cb = _sim(tb.UProduct(na, nb))
    for a in range(1 << na):
        for b in range(1 << nb):
            out, _ = do_phased_classical_simulation(cb, {"a": a, "b": b})
            assert (int(out["a"]), int(out["b"]), int(out["result"])) == (a, b, a * b)
    cs = _sim(tb.CSubW(nb))
    for c, a, b in itertools.product((0, 1), range(1 << nb), range(1 << nb)):
        out, _ = do_phased_classical_simulation(cs, {"ctrl": c, "a": a, "b": b})
        assert int(out["b"]) == (b - c * a) % (1 << nb) and int(out["a"]) == a


# ------------------------------------------------------------------------------------------------ (b) operator norm
@pytest.mark.parametrize("w", [(1, 1, 1), (2, 1, 1), (2, 2, 1), (1, 2, 3), (2, 2, 2), (3, 2, 2), (3, 3, 3), (4, 3, 3), (4, 4, 4)])
def test_operator_norm_against_the_diagonal_phase(w):
    U = tb.DigitalTripleLeaf(*w, PHI).tensor_contract()       # every ancilla allocated and freed inside: <0| ... |0>
    wx, wy, wz = w
    N = 1 << sum(w)
    D = np.zeros(N, dtype=complex)
    for i in range(N):
        x, y, z = _signed(i >> (wy + wz), wx), _signed((i >> wz) & ((1 << wy) - 1), wy), _signed(i & ((1 << wz) - 1), wz)
        D[i] = _want(PHI, x, y, z)
    assert U.shape == (N, N)
    assert np.linalg.norm(U - np.diag(D), 2) < 1e-10


def test_grid_and_digital_unitaries_agree():
    a = tb.DigitalTripleLeaf(2, 2, 2, PHI).tensor_contract()
    b = tb.TripleLeaf(2, 2, 2, PHI).tensor_contract()
    assert np.linalg.norm(a - b, 2) < 1e-10


# ------------------------------------------------------------------------------------------------ sizing
@pytest.mark.parametrize("w", list(itertools.product(range(1, 5), repeat=3)))
def test_xyz_fits_w_minus_2_signed_bits_and_not_fewer(w):
    """No wraparound: |x y z| <= 2^(W-3) with the bound reached only by -2^(W-3), so x y z fits a signed (W-2)-bit
    register (the rotated bits), the (wx + wy)-bit t holds x y, and the W-bit product register holds x y z."""
    W = sum(w)
    vals = [x * y * z for x, y, z in _inputs(*w)]
    lo, hi = -(1 << (W - 3)), (1 << (W - 3)) - 1
    assert min(vals) == lo and max(vals) <= hi                     # fits W - 2 signed bits ...
    assert W < 4 or min(vals) < -(1 << (W - 4))                     # ... and W - 3 would not (the minimum is reached)
    assert tb.digital_triple_rotation_bits(*w) == W - 2
    xy = [x * y for x, y, _ in _inputs(w[0], w[1], 1)]
    assert -(1 << (w[0] + w[1] - 1)) <= min(xy) and max(xy) < 1 << (w[0] + w[1] - 1)


# ------------------------------------------------------------------------------------------------ costs and qubits
@pytest.mark.parametrize("a,b", [(1, 2), (5, 10), (26, 52), (54, 108), (3, 7), (7, 3), (2048, 4096)])
def test_uproduct_toffolis_are_qualtrans_product(a, b):
    assert get_cost_value(Product(a, b), QECGatesCost()).toffoli == tb.uproduct_toffolis(a, b)


def test_uproduct_witness_and_count_equals_qualtrans_product():
    """The witness's compute half uses exactly as many Ands as Qualtran's `Product` has Toffolis (rows over the
    narrower operand)."""
    from tcost.counts import gate_counts
    for a, b in [(2, 3), (3, 6), (5, 10)]:
        g = gate_counts(tb.UProduct(a, b).decompose_bloq())
        assert g["and"] + g["toffoli"] == tb.uproduct_toffolis(a, b)


@pytest.mark.parametrize("w", list(range(2, 30)) + [50, 54, 100, 2048])
def test_leaf_engines_agree_and_rotations_are_3w_minus_2(w):
    lc = tp.leaf_counts(PTP, w)                                     # engine 1: compiled symbolic closed form
    e2 = cc.leaf_cost(w, w, "product", wy=w)                       # engine 2: Qualtran Product and CAdd
    assert lc["rotation"] == e2.rot == 3 * w - 2
    assert 4 * (lc["and"] + lc["toffoli"] + lc["cswap"]) + lc["t"] == e2.base
    # closed form: 2 [w(2w-1) + 2w(2w-1)] Toffolis + CAdd(m) and its inverse at m = w, w, w, 2w (2m - 1 Ands each)
    assert e2.base == 4 * (6 * w * (2 * w - 1) + 2 * (3 * (2 * w - 1) + (4 * w - 1)))
    from tcost.counts import gate_counts
    if w <= 20:
        assert gate_counts(tb.DigitalTripleLeaf(w, w, w)) == lc


def test_leaf_qubits_equal_qualtrans_count_of_the_decomposition():
    for w in itertools.product(range(1, 6), repeat=3):
        assert get_cost_value(tb.DigitalTripleLeaf(*w).decompose_bloq(), QubitCount()) == tb.digital_triple_qubits(*w), w
    assert tb.DigitalTripleLeaf(26, 26, 26).my_static_costs(QubitCount()) == 12 * 26
    assert get_cost_value(CAdd(QUInt(5), QUInt(5)), QubitCount()) == 20


def test_grid_leaf_tcost_and_engine2_agree():
    for w in (2, 9, 26):
        g = tp.leaf_counts(PTP, w, "grid")
        e2 = cc.leaf_cost(w, w, "grid", wy=w)
        assert g["rotation"] == e2.rot == w ** 3 and 4 * (g["and"] + g["toffoli"]) == e2.base


# ------------------------------------------------------------------------------------------------ (c) whole policies
def _e2_toffoli_class(P: tt.Policy, n: int) -> int:
    """Engine 2's Toffoli-class count (And + Toffoli + CSwap) of the whole tree, from its own callees."""
    L = xc.to_l2q(P)
    tot, nodes, w = 0, 1, n

    def tc(callees):
        s = 0
        for b, k in callees.items():
            g = get_cost_value(b, QECGatesCost())
            s += k * int(g.and_bloq + g.toffoli + g.cswap)
        return s
    while True:
        st = L.step(w, w)
        if st is None:
            return tot + nodes * tc(cc.leaf_callees(w, w, "product", wy=w))
        T, w2 = st
        tot += nodes * tc(cc.level_callees(dict(cc.census_key(T)), w2, cc.SITE, 3))
        nodes *= cc.point_count(PTP, T.k)
        w = w2


def _ptp_policies():
    out = {"reference": lib.sto_reference_policies()[PTP], "kmy": fr.planned("kmy", PTP, 2048, 0.01)[0]}
    for d in sorted(SEEDS.glob("S*")):
        r = json.loads((d / "record.json").read_text())
        if r.get("mode") == PTP:
            out[r["id"]] = tt.load_policies(d / "site_policy.json")[0]
    return out


@pytest.mark.parametrize("delta", [1e-2, 1e-6])
def test_both_engines_agree_on_whole_ptp_policies(delta):
    pols = _ptp_policies()
    assert len(pols) == 10
    for name, P in pols.items():
        s = ts.score_policy(P, 2048, delta)
        cx = xc.cross_check(P, 2048, delta, tcost_score=s)
        assert cx["agree"], (name, cx["checks"])
        g = s["gate_counts"]
        assert g["t"] == 0 and 4 * (g["and"] + g["toffoli"] + g["cswap"]) == cx["engine2"]["t_base"]
        assert g["and"] + g["toffoli"] + g["cswap"] == _e2_toffoli_class(P, 2048), name
        gl = fr.grid_leaf_cross_check(P, 2048, delta)
        assert gl["agree"] and gl["label"] == "grid leaf" and gl["T"] > s["t_count"], name


def test_seed_s6b1cc169fad2_old_and_new():
    """The worked example: its Stage 13 policy (grid-leaf thresholds) priced with the grid leaf is the old board number;
    the same policy with the digital leaf, and its Stage 17 re-plan, are far cheaper."""
    old = tt.load_policies(SEEDS / "S6b1cc169fad2" / "site_policy.pre_digital.json")[0]
    g = ts.score_policy(old, 2048, 0.01, leaf="grid")
    assert g["t_count"] == pytest.approx(3_456_334_702.4517255, rel=1e-12)
    assert (g["gate_counts"]["rotation"], g["gate_counts"]["and"]) == (32_659_200, 38_459_997)
    assert ts.score_policy(old, 2048, 0.01)["t_count"] == pytest.approx(300_512_040.50431883, rel=1e-12)
    new = tt.load_policies(SEEDS / "S6b1cc169fad2" / "site_policy.json")[0]
    assert ts.score_policy(new, 2048, 0.01)["t_count"] == pytest.approx(99_002_337.58378407, rel=1e-12)


# ------------------------------------------------------------------------------------------------ board, seeds, Lean
def test_the_ptp_board_uses_the_digital_leaf_and_shows_the_grid_leaf_as_a_cross_check():
    P = lib.sto_reference_policies()[PTP]
    p = fr.point(P, 2048, 0.01)
    assert p["T"] == pytest.approx(138_815_365.68695214, rel=1e-12)
    # the grid-leaf cross-check of the STO reference is the board's pre-Stage-17 headline (Stage 13, exact widths)
    assert p["grid_leaf"]["T"] == pytest.approx(14_242_595_585.181328, rel=1e-12)
    v = pv.view(P, 2048, 0.01)
    assert v["levels"][-1]["table"] == "digital triple-product leaf"
    assert v["grid_leaf_cross_check"]["label"] == "grid leaf" and 0 < v["leaf_share"] < 1
    assert pv.view(lib.sto_reference_policies()[tt.PAIR], 2048, 0.01)["grid_leaf_cross_check"] is None


def test_tcost_refuses_a_grid_leaf_for_pp():
    with pytest.raises(ValueError):
        tb._leaf_for(tt.PAIR, 8, None, "grid")


PTP_SEEDS = ("S1c115b37fd2c", "S6b1cc169fad2", "S7cb2f58aff1a", "S80e6a121410d", "Sb756111e4055", "Sba5f50010bd4",
             "Se1cc13af8310", "Sfb4e5db96f73")


def test_ptp_seeds_keep_their_ids_and_are_replanned_under_the_digital_leaf():
    for sid in PTP_SEEDS:
        r = json.loads((SEEDS / sid / "record.json").read_text())
        assert r["mode"] == PTP and r["replanned"]["note"] == sp.REPLAN_NOTE_PTP
        new = tt.load_policies(SEEDS / sid / "site_policy.json")[0]
        s13 = tt.load_policies(SEEDS / sid / "site_policy.pre_digital.json")[0]
        old = tt.load_policies(SEEDS / sid / "site_policy.pre_exact.json")[0]
        assert sp.policy_id(old.with_widths("scan")) == sid                   # the id is still the seeded planner output
        want = sp.replan(old.with_widths("scan"))
        assert [(b.threshold, b.table.digest) for b in new.bands] == [(b.threshold, b.table.digest) for b in want.bands]
        h = r["history"][1]
        assert h["leaf"] == "grid" and h["site_policy"] == "site_policy.pre_digital.json"
        assert [(b["threshold"], b["digest"]) for b in h["bands"]] == [(b.threshold, b.table.digest) for b in s13.bands]
        assert h["sto_mirror"]["status"] == "accepted"                         # the Stage 13 PTP suite run, kept
        assert ts.score_policy(new, 2048, 0.01)["t_count"] <= ts.score_policy(s13, 2048, 0.01)["t_count"] * (1 + 1e-12)


def test_replanned_ptp_seeds_have_accepted_lean_runs_and_the_stage13_runs_are_history():
    pin = submitted.seed_exact_runs()
    for sid in PTP_SEEDS:
        run = pin["runs"][sid]
        sha = hashlib.sha256((SEEDS / sid / "site_policy.json").read_bytes()).hexdigest()
        assert run["site_policy_sha256"] == sha and run["verdict"] == "accepted" and run["checker"] == "ptp-suite"
        old = pin[sp.STAGE13_PTP_KEY][sid]
        sha13 = hashlib.sha256((SEEDS / sid / "site_policy.pre_digital.json").read_bytes()).hexdigest()
        assert old["site_policy_sha256"] == sha13 and old["verdict"] == "accepted"
        assert (SEEDS / old["evidence"]).is_file()
