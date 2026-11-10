"""Modular swap 001 (negation = complement + one-clean-ancilla incrementer, ForShor `eb00c5adc72832469a5f223fbec6c8b75745d2dd`) as the site's engine prices it.

The default negation is `gidney_circuit` = ForShor's `oneQubit` NegationMethod: the counts of Gidney's one-ancilla increment circuit
(Toffoli 12w-28 even w / 10w-18 odd w, CNOT 17w-22 / 17w-5, X 5w-3 / 5w+1), which is what Lean's `negateResources` charges. The engine's
`incrementer` (a flat 32 w Toffoli) is the SUPERSEDED first draft of PR #79; it stays selectable and is tested only as such. `legacy` =
ForShor's `scratch` method. Every expected total below is recomputed here from those counts, 4 T per Toffoli and the call graph's own count
of negations: none is a pasted engine output.

Ground truth: ForShor_modular_swaps/swap-001_negation_incrementer (TRADEOFF.md, DECISIONS.md) and swap-002 (Emit/Json/Common.lean `negateChargeAt`)."""
import pytest

from site_engine import fullalg as fa
from site_engine import library as lib
from l2q_full import cost_conventions as cc  # site_engine.fullalg has put engine/fullalg on sys.path
from tcost import tables as tt

from negation_arith import (negation, DRAFT_DELTA, REAL_DELTA, RUN_NEGATIONS, delta_t, run_sites, sites_of, t_draft, t_legacy, t_new,  # noqa: F401
                            toffoli_lean)

PP = tt.PAIR
N = 2048
XW, DW, WW, SW = 4095, 2048, 2347, 4398


def reference():
    return lib.sto_reference_policies()[PP].with_widths("exact")


def price(impl, policy=None):
    with negation(impl):
        return fa.price_full(policy or reference(), N, 0.01, detail=False)


def test_the_real_circuit_is_the_default_and_the_32w_draft_is_not():
    assert fa.HEADLINE.negate == "gidney_circuit" != "incrementer"
    assert cc.DEFAULT_NEGATE == "gidney_circuit" and "incrementer" in cc.NEGATE_IMPLS     # the draft stays selectable, as superseded
    assert fa.price_full(reference(), N, 0.01, detail=False)["negate"] == "gidney_circuit"


def test_the_arithmetic_behind_the_expected_totals():
    """The constants this file derives, so a reader can check them by hand (the swap logs say +4.75e9 T for the standard policy,
    and +1.3399396920e10 for the superseded draft)."""
    assert t_new(4398) == 210_992 and t_legacy(4398) == 17_588 and t_draft(4398) == 562_944
    assert REAL_DELTA == 24_570 * (210_992 - 17_588) == 4_751_936_280
    assert DRAFT_DELTA == 13_399_396_920


def test_a_policy_with_no_negating_table_pays_exactly_the_real_negation_constant_in_T():
    old, new = price("legacy"), price("gidney_circuit")
    assert new["T"] - old["T"] == pytest.approx(REAL_DELTA, abs=1e-3)
    assert new["rotations"] == old["rotations"], "the swap adds Toffolis, not rotations"
    assert new["T_base"] - old["T_base"] == pytest.approx(REAL_DELTA, abs=1e-3)


def test_the_superseded_32w_draft_is_selectable_overcharges_and_has_the_same_qubits():
    old, real, draft = price("legacy"), price("gidney_circuit"), price("incrementer")
    assert draft["T"] - old["T"] == pytest.approx(DRAFT_DELTA, abs=1e-3)
    assert draft["T"] > real["T"]
    for key in ("qubits_peak", "peak_extra", "qubits_layout"):
        assert draft[key] == real[key], key


def test_the_old_negation_still_reproduces_the_pre_swap_headline_exactly():
    """D18: `legacy` reproduces the old pricing bit for bit (the Stage 18 pin of the STO reference at n = 2048, delta = 0.01)."""
    assert price("legacy")["T"] == pytest.approx(1_933_981_965_830.921, rel=1e-12)


def test_the_static_layout_is_unchanged_by_the_swap_and_the_live_peak_is_far_below_it():
    old, new = price("legacy"), price("gidney_circuit")
    assert old["qubits_layout"] == new["qubits_layout"] == new["qubits"], "the static layout allocation never contained a negation's scratch"
    assert new["qubits_peak"] == new["peak_base_registers"] + new["peak_extra"]
    assert new["peak_base_registers"] == XW + (DW + 1) + WW + SW + 1 == 12_890
    assert new["qubits_peak"] < new["qubits_layout"] / 3


def test_the_swap_saves_4400_minus_the_new_extra_in_live_qubits_when_the_scratch_register_binds():
    """TRADEOFF.md: before = max(after, 4,400). Under exact widths the old negation's 4,400 always binds."""
    old, new = price("legacy"), price("gidney_circuit")
    assert old["peak_extra"] == 4400
    assert new["peak_extra"] < 4400
    assert old["qubits_peak"] - new["qubits_peak"] == 4400 - new["peak_extra"]


def test_a_policy_whose_table_negates_pays_exactly_its_negation_sites_at_the_real_counts():
    """ForShor's shipped policy has a negating table. Its extra T is the sum over its negation sites (width w, count k, from the call
    graph) of k * (the real negation's T at w - the legacy negation's T at w): recomputed here, then asserted equal to the engine's."""
    P = fa.native().policy
    with negation("legacy"):
        old = fa.price_l2q(P, N, 0.01, detail=False)
    with negation("gidney_circuit"):
        new = fa.price_l2q(P, N, 0.01, detail=False)
        sites = run_sites(P, N)
    assert sites[4398] == RUN_NEGATIONS and sum(sites.values()) > 10 ** 8, "the table's own negations dominate the count"
    expected = delta_t(sites, t_legacy, t_new)
    assert new["T"] - old["T"] == pytest.approx(expected, rel=1e-12, abs=1e-3)
    assert expected > REAL_DELTA
    with negation("incrementer"):
        draft = fa.price_l2q(P, N, 0.01, detail=False)
    assert draft["T"] - old["T"] == pytest.approx(delta_t(sites, t_legacy, t_draft), rel=1e-12)
    assert draft["T"] > new["T"]


def test_exactly_three_library_tables_negate_and_the_sto_reference_policy_uses_none_of_them():
    """Three accepted library tables negate (one PhaseTripleProduct k = 2 and two PhaseProduct k = 6), and the STO reference policy uses none.
    (Until 2026-11-10 the PhaseProduct and PhaseTripleProduct boards, priced by tcost, charged a negation the old way (D24) and so only agreed
    with the full run for policies whose tables do not negate; tcost now charges the full run's `oneQubit` negation, so they agree for every
    policy: tests/test_negation_counts.py. The exporter's guard that refuses a policy with a negating table is a separate decision.)"""
    tables = lib.sto_tables(upto=len(lib.submissions()))
    negating = sorted((t.mode, t.k) for t in tables if t.census()["negate"])
    assert negating == [(PP, 6), (PP, 6), (tt.TRIPLE, 2)]
    assert all(b.table.census()["negate"] == 0 for b in reference().bands)


# ---- the counts themselves, against ForShor's `negateChargeAt` examples (Emit/Json/Common.lean; the CI smoke step asserts the same table)
FORSHOR_ONE_QUBIT = {8: {"x": 37, "cnot": 114, "toffoli": 68}, 9: {"x": 46, "cnot": 148, "toffoli": 72},
                     2048: {"x": 10237, "cnot": 34794, "toffoli": 24548}}
FORSHOR_SCRATCH_2048 = {"x": 6140, "cnot": 10233, "toffoli": 4093}


@pytest.mark.parametrize("w", sorted(FORSHOR_ONE_QUBIT))
def test_negate_counts_equal_forshors_one_qubit_charge(w):
    """Epoch `negation: "incrementer"` = ForShor `oneQubit` = engine `gidney_circuit` (the default), one clean qubit."""
    assert cc.negate_resources(w) == cc.negate_resources(w, "gidney_circuit") == FORSHOR_ONE_QUBIT[w]
    assert cc.negate_clean_ancilla(w) == cc.negate_clean_ancilla(w, "gidney_circuit") == 1
    # and through the bloq the pricing path uses: the bloq's call graph carries exactly these counts
    got = {type(b).__name__: k for b, k in cc.negate_bloq(w, fa.HEADLINE).build_call_graph(None).items()}
    assert got == {"Toffoli": FORSHOR_ONE_QUBIT[w]["toffoli"], "CNOT": FORSHOR_ONE_QUBIT[w]["cnot"], "XGate": FORSHOR_ONE_QUBIT[w]["x"]}


def test_negate_counts_equal_forshors_scratch_charge_at_2048():
    """ForShor `scratch` = engine `legacy`: a 2048-bit constant-one register (2049 clean qubits) plus a Cuccaro adder."""
    assert cc.negate_resources(2048, "legacy") == FORSHOR_SCRATCH_2048
    assert cc.negate_clean_ancilla(2048, "legacy") == 2049


@pytest.mark.parametrize("w", [8, 9, 2048])
def test_the_32w_draft_is_not_forshors_charge(w):
    assert cc.negate_resources(w, "incrementer") == {"x": w, "cnot": 0, "toffoli": 32 * w} != FORSHOR_ONE_QUBIT[w]
