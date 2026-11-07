"""Modular swap 001 (negation = complement + one-clean-ancilla incrementer, ForShor 8e37ce7) as the site's engine prices it.

Ground truth: ForShor_modular_swaps/swap-001_negation_incrementer (TRADEOFF.md, DECISIONS.md D17 to D29)."""
import contextlib

import pytest

from site_engine import fullalg as fa
from site_engine import library as lib
from tcost import tables as tt

PP = tt.PAIR
N = 2048
#: 24,570 negations per run, all at width 4398: each now costs 32 w Toffoli (128 w T) instead of 2 w - 3 Toffoli (8 w - 12 T)
SWAP001_T = 13_399_396_920
XW, DW, WW, SW = 4095, 2048, 2347, 4398


@contextlib.contextmanager
def negation(impl):
    names = ("HEADLINE", "GRID_LEAF", "LITERAL_LEAN")
    saved = {n: getattr(fa, n) for n in names}
    for n in names:
        setattr(fa, n, saved[n].with_(negate=impl))
    fa._price.cache_clear()
    try:
        yield
    finally:
        for n in names:
            setattr(fa, n, saved[n])
        fa._price.cache_clear()


def reference():
    return lib.sto_reference_policies()[PP].with_widths("exact")


def price(impl, policy=None):
    with negation(impl):
        return fa.price_full(policy or reference(), N, 0.01, detail=False)


def test_the_new_negation_is_the_default():
    assert fa.HEADLINE.negate == "incrementer"
    assert fa.price_full(reference(), N, 0.01, detail=False)["negate"] == "incrementer"


def test_a_policy_with_no_negating_table_pays_exactly_the_swap_constant_in_T():
    old, new = price("legacy"), price("incrementer")
    assert new["T"] - old["T"] == pytest.approx(SWAP001_T, abs=1e-3)
    assert new["rotations"] == old["rotations"], "the swap adds Toffolis, not rotations"
    assert new["T_base"] - old["T_base"] == pytest.approx(SWAP001_T, abs=1e-3)


def test_the_old_negation_still_reproduces_the_pre_swap_headline_exactly():
    """D18: `legacy` reproduces the old pricing bit for bit (the Stage 18 pin of the STO reference at n = 2048, delta = 0.01)."""
    assert price("legacy")["T"] == pytest.approx(1_933_981_965_830.921, rel=1e-12)


def test_the_static_layout_is_unchanged_by_the_swap_and_the_live_peak_is_far_below_it():
    old, new = price("legacy"), price("incrementer")
    assert old["qubits_layout"] == new["qubits_layout"] == new["qubits"], "the static layout allocation never contained a negation's scratch"
    assert new["qubits_peak"] == new["peak_base_registers"] + new["peak_extra"]
    assert new["peak_base_registers"] == XW + (DW + 1) + WW + SW + 1 == 12_890
    assert new["qubits_peak"] < new["qubits_layout"] / 3


def test_the_swap_saves_4400_minus_the_new_extra_in_live_qubits_when_the_scratch_register_binds():
    """TRADEOFF.md: before = max(after, 4,400). Under exact widths the old negation's 4,400 always binds."""
    old, new = price("legacy"), price("incrementer")
    assert old["peak_extra"] == 4400
    assert new["peak_extra"] < 4400
    assert old["qubits_peak"] - new["qubits_peak"] == 4400 - new["peak_extra"]


def test_a_policy_whose_table_negates_pays_far_more_for_the_swap():
    """ForShor's shipped policy has a negating table: +46.8 % T (TRADEOFF.md: 'tables with negate ops pay far more')."""
    P = fa.native().policy
    with negation("legacy"):
        old = fa.price_l2q(P, N, 0.01, detail=False)
    with negation("incrementer"):
        new = fa.price_l2q(P, N, 0.01, detail=False)
    assert new["T"] > old["T"] + SWAP001_T
    assert new["T"] / old["T"] == pytest.approx(1.468, abs=0.01)


def test_exactly_three_library_tables_negate_and_the_sto_reference_policy_uses_none_of_them():
    """The PhaseProduct and PhaseTripleProduct boards are priced by tcost, which still prices a negation the old way (D24), so they only
    agree with the full run for policies whose tables do not negate. Three accepted library tables do negate (one PhaseTripleProduct
    k = 2 and two PhaseProduct k = 6), and the STO reference policy uses none; the exporter refuses any listed policy that does."""
    tables = lib.sto_tables(upto=len(lib.submissions()))
    negating = sorted((t.mode, t.k) for t in tables if t.census()["negate"])
    assert negating == [(PP, 6), (PP, 6), (tt.TRIPLE, 2)]
    assert all(b.table.census()["negate"] == 0 for b in reference().bands)
