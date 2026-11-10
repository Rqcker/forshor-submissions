"""The PhaseProduct and PhaseTripleProduct boards (`tcost` and the second counting engine) price a negation as the full run does
(2026-11-10): ForShor's `oneQubit` charge, in T and in qubits. Before, they priced Qualtran's `Negate`, which differs in both.

Every expected total here is recomputed from first principles (the negation sites of the policy's own levels, the full run's per-negation T
`negation_arith.t_new`, and the old Qualtran price `4 (w - 1)`), never pasted from an engine output, except the pins that document the
KMY policies' numbers before and after the change.
"""
import pytest
import sympy

from site_engine import fullalg as fa
from site_engine import frontier as fr
from site_engine import library as lib
from site_engine import policy_view
from l2q_full import cost_conventions as cc  # site_engine puts engine/fullalg on sys.path
from l2q_full.bloqs import gidney_increment as gi
from tcost import planner as tp
from tcost import score as ts
from tcost import tables as tt
from tcost.bloqs import NegateW
from tcost.negation import CLEAN_ANCILLA, negate_counts

from negation_arith import t_legacy, t_new

PP, PTP = tt.PAIR, tt.TRIPLE
N = 2048


# ---------------------------------------------------------------------------------------------------- the counts
def test_tcosts_negation_counts_are_the_full_runs_at_every_width():
    """`tcost.negation` repeats the closed forms of `gidney_increment.bound_counts` (ForShor `negateResources`, `oneQubit`) so that `tcost`
    stays importable alone; the two, and the cost convention the full run prices with, agree at every width."""
    for w in range(1, 5001):
        assert negate_counts(w) == gi.bound_counts(w) == cc.negate_resources(w), w
    assert CLEAN_ANCILLA == cc.negate_clean_ancilla(8) == 1


def test_the_symbolic_closed_form_evaluates_to_the_same_integers():
    w = sympy.Symbol("w", positive=True, integer=True)
    sym = negate_counts(w)
    for width in list(range(1, 40)) + [2047, 2048, 4397, 4398]:
        assert {k: int(v.subs(w, width)) for k, v in sym.items()} == negate_counts(width), width


def test_the_counts_are_the_explicit_circuits_from_width_7_and_an_upper_bound_below():
    for w in range(1, 41):
        circuit = gi.counts(gi.negate_gates(w))
        want = {"x": circuit["x"], "cnot": circuit["cnot"], "toffoli": circuit["toffoli"]}
        got = negate_counts(w)
        if w >= 7:
            assert got == want, w
        else:
            assert all(got[k] >= want[k] for k in want), w


def test_the_circuit_the_counts_come_from_negates_and_restores_its_ancilla():
    for w in range(1, 9):
        gi.check(gi.negate_gates(w), w, "negate")                       # exhaustive over the register and the ancilla
    gi.check(gi.negate_gates(64), 64, "negate", vectors=200, seed=1)


# ---------------------------------------------------------------------------------------------------- the bloq
@pytest.mark.parametrize("w", [1, 2, 3, 6, 7, 8, 9, 30, 81, 232, 687, 2048, 4398])
def test_negatew_has_the_call_graph_of_the_full_runs_negation(w):
    """The same leaves with the same counts as the full run's `IncrementerNegate` (the cost seam of the two packages for a negation)."""
    mine = {type(b).__name__: k for b, k in NegateW(w).build_call_graph(None).items()}
    full = {type(b).__name__: k for b, k in cc.negate_bloq(w, cc.QUALTRAN).build_call_graph(None).items()}
    assert mine == full


@pytest.mark.parametrize("w", [8, 64, 698])
def test_the_old_negation_held_2w_minus_1_clean_qubits_and_the_new_one_holds_one(w):
    """The 2 w - 1 clean ancillas of Qualtran's `Negate` (an AddK(1) with a w-bit constant register and an adder's w - 1 ancillas) against
    the one clean qubit of `oneQubit`."""
    from qualtran import QInt
    from qualtran.bloqs.arithmetic import Negate
    from qualtran.resource_counting import QubitCount, get_cost_value
    old = int(get_cost_value(Negate(QInt(w)), QubitCount()))
    new = int(get_cost_value(NegateW(w), QubitCount()))
    assert old == w + (2 * w - 1)
    assert new == w + 1 == w + cc.negate_clean_ancilla(w)


@pytest.mark.parametrize("w", [2, 3, 4, 5, 6])
def test_negatew_declares_twos_complement_negation(w):
    lo, hi = -(1 << (w - 1)), 1 << (w - 1)
    for x in range(lo, hi):
        want = ((-x + hi) % (1 << w)) - hi
        assert int(NegateW(w).on_classical_vals(x=x)["x"]) == want


# ---------------------------------------------------------------------------------------------------- the boards
def kmy_policy(mode):
    P, _ = tp.optimal_policy(lib.milestone_tables("kmy", mode), mode, N, 0.01, name="kmy")
    return P


def negation_sites(P):
    """(child width, number of negations) for each level of P at n = 2048, read off the policy's own levels: nodes x sides x the table's
    `negate` operations."""
    v = policy_view.view(P, N, 0.01, "x")
    sides = 2 if P.mode == PP else 3
    out = []
    for lv in v["levels"]:
        if not lv.get("k"):
            continue
        tab = next(t for t in v["tables"] if t["digest"] == lv["digest"])
        out.append((lv["child_width"], lv["nodes"] * sides * tab["census"]["negate"]))
    return out


#: KMY's policies at n = 2048, delta = 0.01: (non-rotation T before the change, after, rotations, live-peak qubits, Qualtran QubitCount peak
#: before, after). The "before" values are the engine's output with Qualtran's `Negate`; the test derives them from the "after" values by
#: taking the negations' price difference back out.
KMY = {PP: (10_216_464, 11_407_920, 37_500, 4_181, 5_495, 4_808),
       PTP: (93_200_860, 94_684_276, 41_200, 6_339, 7_578, 7_578)}


@pytest.mark.parametrize("mode", [PP, PTP])
def test_the_kmy_boards_price_every_negation_at_the_full_runs_charge(mode):
    before, after, rotations, live_q, _qq_before, qq_after = KMY[mode]
    P = kmy_policy(mode)
    s = ts.score_policy(P, N, 0.01)
    sites = negation_sites(P)
    assert sites and all(k > 0 for _, k in sites), "every KMY table negates"
    assert s["t_breakdown"]["t_from_toffoli_class"] == after
    assert s["gate_counts"]["rotation"] == rotations, "the change adds Toffolis, not rotations"
    # the price difference of the negations alone, recomputed from first principles
    assert after - before == sum(k * (t_new(w) - t_legacy(w)) for w, k in sites)
    # qubits: the board's Q is the live peak (one clean ancilla per negation, already); Qualtran's own peak is the labelled cross-check
    assert fr.live_peak_qubits(P, N) == live_q
    assert fr.qubits(P, N) == qq_after


def test_the_kmy_phaseproduct_total_t_and_the_full_run_pricing():
    """The PhaseProduct board's T for KMY is now 1.4073730e7 (was 1.2882274e7), and the full run's own pricing of the same policy is untouched
    (it always charged the real negation)."""
    P = kmy_policy(PP)
    s = ts.score_policy(P, N, 0.01)
    assert s["t_count"] == pytest.approx(14_073_729.822456302, rel=1e-12)
    assert s["t_count"] - 12_882_273.822456302 == pytest.approx(11_407_920 - 10_216_464, abs=1e-6)
    r = fa.price_full(P, N, 0.01)
    assert r["T"] == pytest.approx(1_933_480_584_947.1123, rel=1e-12) and r["qubits_peak"] == 13_284
