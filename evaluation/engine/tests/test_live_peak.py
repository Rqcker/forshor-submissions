"""The live-qubit peak (`l2q_full.policy_peak`) against the numbers Lean computed for modular swap 001.

Ground truth: ForShor_modular_swaps/swap-001_negation_incrementer/TRADEOFF.md, produced by `#eval` of
`Policy.orderFindingPeakBalanced` (evidence/peak_policies.lean) on ForShor 8e37ce7 for the standard-table policies, with the register
widths x 4096, data 2048 (2049 with its carry bit), work 2347, scratch 4398; and the kernel-checked anchor
(`reference2048PeakBalanced`: constant std-k2, scan widths, 17,291 qubits before the swap and 13,449 after).
"""
import pytest

from site_engine import paper_policies as pp

XW, DW, WW, SW = 4096, 2048, 2347, 4398
BASE = XW + (DW + 1) + WW + SW + 1          # 12,891

#: the replanned best policy of peak_policies.lean (20 bands over std k2..7)
REPLANNED = [(2348, 6), (2200, 7), (2049, 6), (1101, 5), (1026, 6), (551, 5), (513, 4), (405, 5), (332, 4), (294, 3),
             (276, 4), (258, 3), (244, 4), (230, 5), (214, 3), (184, 4), (102, 3), (95, 2), (90, 3), (71, 2)]

#: (policy, width rule) -> extra qubits above the base registers AFTER the swap (Lean #eval, TRADEOFF.md)
AFTER = {("k2", "scan"): 558, ("k3", "scan"): 1126, ("k4", "scan"): 3030, ("k5", "scan"): 5870, ("replanned", "scan"): 7177,
         ("k2", "exact"): 390, ("k3", "exact"): 512, ("k4", "exact"): 628, ("k5", "exact"): 808, ("replanned", "exact"): 580}


def build(name, rule):
    from l2q_full import policy as lpol
    if name == "replanned":
        bands = tuple(lpol.PolicyBand(t, pp.table(k)) for t, k in REPLANNED)
    else:
        bands = (lpol.PolicyBand(0, pp.table(int(name[1:]))),)       # Lean's `standardPolicy k` is one band at threshold 0
    return lpol.with_width_rule(lpol.PhasePolicy(bands, name=name), rule)


@pytest.fixture(scope="module")
def peak():
    from l2q_full import policy_peak
    return policy_peak


@pytest.mark.parametrize("name,rule", sorted(AFTER))
def test_the_new_negation_peak_equals_lean(peak, name, rule):
    assert peak.order_finding_peak(build(name, rule), XW, DW, WW, SW, "incrementer") == AFTER[(name, rule)]


@pytest.mark.parametrize("name,rule", sorted(AFTER))
def test_the_old_negation_peak_is_the_larger_of_the_new_peak_and_4400(peak, name, rule):
    """TRADEOFF.md: before = max(after, 4,400): the old Step-3 entry was `scratch width + 2`, and no standard table negates."""
    assert peak.order_finding_peak(build(name, rule), XW, DW, WW, SW, "legacy") == max(AFTER[(name, rule)], 4400)


def test_the_kernel_checked_anchor(peak):
    P = build("k2", "scan")
    assert peak.order_finding_qubits(P, XW, DW, WW, SW, "legacy") == 17_291
    assert peak.order_finding_qubits(P, XW, DW, WW, SW, "incrementer") == 13_449
    assert BASE + 558 == 13_449 and BASE + 4400 == 17_291


def test_with_4095_exponent_qubits_each_count_is_one_lower(peak):
    P = build("k2", "scan")
    assert peak.order_finding_qubits(P, 4095, DW, WW, SW, "incrementer") == 13_448


def test_at_scan_widths_with_k_5_or_more_the_swap_saves_no_qubits_and_otherwise_it_does(peak):
    saved = {n: peak.order_finding_peak(build(n, "scan"), XW, DW, WW, SW, "legacy") - peak.order_finding_peak(build(n, "scan"), XW, DW, WW, SW, "incrementer")
             for n in ("k2", "k3", "k4", "k5", "replanned")}
    assert saved == {"k2": 3842, "k3": 3274, "k4": 1370, "k5": 0, "replanned": 0}      # TRADEOFF.md: only k < 5 binds at scan widths
    saved_exact = {n: peak.order_finding_peak(build(n, "exact"), XW, DW, WW, SW, "legacy") - peak.order_finding_peak(build(n, "exact"), XW, DW, WW, SW, "incrementer")
                   for n in ("k2", "k3", "k4", "k5", "replanned")}
    assert saved_exact == {"k2": 4010, "k3": 3888, "k4": 3772, "k5": 3592, "replanned": 3820}   # under exact widths it always binds


def test_an_unknown_negation_is_refused(peak):
    with pytest.raises(ValueError):
        peak.order_finding_peak(build("k2", "scan"), XW, DW, WW, SW, "gidney")


def test_step_3_charges_the_swap_constant_or_the_scratch_register_width(peak):
    """Step 3's entry is the swap's whole point: 3 (one unit qubit, two of arithmetic scratch) against `sW + 2`. It never binds for a real
    policy at the new value, so it is checked directly."""
    P = build("k2", "exact")
    assert peak.modmul_phase_peaks(P, DW, WW, SW, "incrementer")[4] == 3
    assert peak.modmul_phase_peaks(P, DW, WW, SW, "legacy")[4] == SW + 2 == 4400


def _with_op(T, op):
    import dataclasses
    from l2q_full import policy as lpol
    return dataclasses.replace(T, ops_frozen=T.ops_frozen + (lpol._freeze_op(op),))


def test_a_table_that_negates_charges_w_plus_1_scratch_before_the_swap_and_1_after(peak):
    """No standard or STO table on the site negates, so this branch is exercised with a table that does."""
    plain = pp.table(2)
    neg = _with_op(plain, {"op": "negate", "i": 0})
    adds_only = plain
    assert any(o["op"] == "addScaled" for o in adds_only.ops) and not any(o["op"] == "negate" for o in adds_only.ops)
    for W in (10, 100, 4398):
        assert peak.table_scratch(neg, W, "legacy") == W + 1
        assert peak.table_scratch(neg, W, "incrementer") == 1
        assert peak.table_scratch(adds_only, W, "legacy") == peak.table_scratch(adds_only, W, "incrementer") == 1
    import dataclasses
    # a table with neither op charges 0
    empty = dataclasses.replace(plain, ops_frozen=tuple(x for x in plain.ops_frozen if dict(x)["op"] not in ("negate", "addScaled")))
    assert peak.table_scratch(empty, 100, "legacy") == peak.table_scratch(empty, 100, "incrementer") == 0


def test_a_policy_whose_tables_negate_gains_peak_qubits_from_the_old_negation(peak):
    from l2q_full import policy as lpol
    neg = _with_op(pp.table(2), {"op": "negate", "i": 0})
    P = lpol.with_width_rule(lpol.PhasePolicy((lpol.PolicyBand(0, neg),), name="negating"), "exact")
    legacy = peak.peak_reserve(P, 2300, 2300, "legacy")
    new = peak.peak_reserve(P, 2300, 2300, "incrementer")
    assert legacy > new, "the old negation's W + 1 scratch must show in a negating table's peak reserve"
