"""The live-qubit peak of ONE product (PhaseProduct, PhaseTripleProduct): `policy_peak.chain_peak_extra` and
`frontier.live_peak_qubits`, the measure the PP and PTP boards report as Q.

PhaseProduct: the chain recursion must equal `policy_peak.peak_reserve` (ForShor's `peakReserveD`, validated against Lean's #eval in
test_live_peak.py) on the same policy at the same widths. PhaseTripleProduct: the shape of ForShor's `ptpWsTD`
(PhaseTripleProduct/Proofs/QubitsTight.lean), checked here against hand-computed levels."""
import pytest

from site_engine import frontier as fr, fullalg as fa, policy_view as pv   # fullalg puts l2q_full on the path
from l2q_full import policy_peak as ppk
from tcost import tables as tt
from test_site_engine import N, PP, PTP, _opt, _ref


def lv(width, k, child, negates=False, adds=True):
    return (width, k, child, negates, adds)


# ---- the recursion, on levels written by hand ------------------------------------------------------------------------------

def test_a_leaf_only_product_needs_nothing_extra_for_pp_and_two_ancillas_for_ptp():
    assert ppk.chain_peak_extra([], 2) == 0
    assert ppk.chain_peak_extra([], 3) == 2


def test_one_level_pp():
    # width 100 split k = 3 into chunks of 40: both operands grow 100 -> 120, then the adder's one scratch qubit
    assert ppk.chain_peak_extra([lv(100, 3, 40)], 2) == 2 * 20 + 1


def test_one_level_ptp_grows_all_three_operands_and_adds_the_scratch_to_the_named_qubits():
    # 3 * (3*40 - 100) = 60 named qubits of growth, the leaf's 2 sandwich ancillas (named), and the node's 1 clean scratch qubit ON TOP:
    # ForShor's `ptp_peak_qubits` is 3n + ptpWsTD + ptpCleanAncD (swap 002), not the shared-slot count max(2, 1)
    assert ppk.chain_peak_extra([lv(100, 3, 40)], 3) == 60 + 2 + 1
    assert ppk.chain_peak_extra([lv(100, 3, 40, negates=False, adds=False)], 3) == 60 + 2     # no arithmetic, no scratch


def test_ptp_scratch_is_the_largest_over_the_levels_not_their_sum():
    # ptpCleanAncD is a max over the nodes: two adding levels still add ONE clean qubit
    two = ppk.chain_peak_extra([lv(1000, 4, 300), lv(300, 3, 120)], 3)
    assert two == 3 * (4 * 300 - 1000) + 3 * (3 * 120 - 300) + 2 + 1
    # the legacy negation holds a register as wide as the node's child: the widest negating node's child_width + 1
    legacy = ppk.chain_peak_extra([lv(1000, 4, 300, negates=True), lv(300, 3, 120, negates=True)], 3, "legacy")
    assert legacy == 3 * 200 + 3 * 60 + 2 + 301


def test_children_share_one_pool_so_levels_add_their_growth_and_take_the_larger_tail():
    two = ppk.chain_peak_extra([lv(1000, 4, 300), lv(300, 3, 120)], 2)
    assert two == 2 * (4 * 300 - 1000) + (2 * (3 * 120 - 300) + 1)


def test_the_incrementer_charges_one_scratch_qubit_and_the_old_negation_a_register():
    levels = [lv(1000, 4, 300, negates=True, adds=True)]
    assert ppk.chain_peak_extra(levels, 2, "gidney_circuit") == 2 * 200 + 1
    assert ppk.chain_peak_extra(levels, 2, "legacy") == 2 * 200 + 301           # child_width + 1


def test_a_table_with_no_arithmetic_has_no_scratch():
    assert ppk.chain_peak_extra([lv(100, 3, 40, negates=False, adds=False)], 2) == 2 * 20


def test_widths_that_do_not_grow_the_chunks_truncate_at_zero_as_in_lean():
    assert ppk.chain_peak_extra([lv(100, 2, 40)], 2) == 0 + 1                   # 2*40 = 80 < 100


def test_bad_arguments_are_refused():
    with pytest.raises(ValueError):
        ppk.chain_peak_extra([], 4)
    with pytest.raises(ValueError):
        ppk.chain_peak_extra([], 2, "nope")


# ---- PhaseProduct on real policies: equal to peak_reserve through the L2Q conversion ---------------------------------------------

@pytest.mark.parametrize("key", ["kmy", "all_sto", "champions"])
@pytest.mark.parametrize("rule", ["exact", "scan"])
def test_pp_live_peak_equals_peak_reserve_and_is_below_the_static_layout(key, rule):
    P = _opt(key, PP, widths=rule)
    live = fr.live_peak_qubits(P, N)
    ref = 2 * N + ppk.peak_reserve(fa.to_l2q_full(P, rule=rule), N, N, "gidney_circuit")
    assert live == ref
    assert live <= fr.lean_static_qubits(P, N)


def test_the_reference_policy_at_exact_widths():
    # ladder 2048 -> 364 -> 184 -> 94 -> 49 -> 26: the static layout is 4,992; the shared pool brings the peak to 4,399
    assert fr.live_peak_qubits(_ref(), N) == 4399


# ---- PhaseTripleProduct on real policies ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["kmy", "all_sto", "champions"])
def test_ptp_live_peak_is_three_operands_plus_the_chain(key):
    P = _opt(key, PTP)
    levels = fr._levels(P, N)
    assert levels, "a recursive policy"
    assert fr.live_peak_qubits(P, N) == 3 * N + ppk.chain_peak_extra(levels, 3)
    # the peak is the operands, every level's growth, the leaf's 2 sandwich ancillas, and the incrementer's one clean scratch
    # qubit if any level adds or negates (ForShor's `ptp_peak_qubits`: named qubits plus the resource model's clean scratch)
    grown = sum(3 * max(k * c - w, 0) for (w, k, c, _n, _a) in levels)
    scratch = 1 if any(n or a for (_w, _k, _c, n, a) in levels) else 0
    assert fr.live_peak_qubits(P, N) == 3 * N + grown + 2 + scratch


def test_the_legacy_negation_cannot_lower_the_peak():
    for key in ("kmy", "all_sto"):
        for mode in (PP, PTP):
            levels = fr._levels(_opt(key, mode), N)
            operands = 2 if mode == PP else 3
            assert ppk.chain_peak_extra(levels, operands, "legacy") >= ppk.chain_peak_extra(levels, operands, "gidney_circuit")


# ---- what the view reports ----------------------------------------------------------------------------------------------------

def test_the_view_reports_the_live_peak_with_its_label_and_keeps_the_other_counts():
    for mode, rule in ((PP, fr.QUBITS_RULE_PEAK_PP), (PTP, fr.QUBITS_RULE_PEAK_PTP)):
        P = _opt("champions", mode)
        v = pv.view(P, N, 0.01)
        assert v["qubits"] == fr.live_peak_qubits(P, N)
        assert v["qubits_label"] == fr.QUBITS_LABEL_PEAK and v["qubits_rule"] == rule
        assert "not Lean-proved" in v["qubits_label"] and "formula, not a Lean-proved count" in v["qubits_rule"]
        assert v["qubits_qualtran_peak"] == fr.qubits(P, N)
    pp_view = pv.view(_opt("champions", PP), N, 0.01)
    assert pp_view["qubits_static"]["qubits"] >= pp_view["qubits"]
