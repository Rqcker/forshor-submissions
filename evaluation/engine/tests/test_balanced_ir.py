"""The balanced unequal-operand split (ForShor-split, Emit/Balanced) as read from ForShor's IR.

`balanced_phase_product` / `balanced_cphase_product` calls are expanded by instantiating the template's own IR body
under the priced policy (`policy_shor_price.expand_sites`); the board itself still prices the unbalanced IR."""
import json
from pathlib import Path

import pytest

from fullalg.l2q_full import policy_ir as pi
from fullalg.l2q_full import policy_shor_price as pr
from fullalg.l2q_full import policy_shor_sym as sy
from site_engine import fullalg as F

import negation_arith as na

DOCS = Path(__file__).parent / "data" / "balanced"


def _doc(name):
    return json.loads((DOCS / f"{name}.balanced.policy.json").read_text())


def _core(doc):
    conv = sy.Convention.for_modulus(1 << 2047, F.precision_m(2048))
    return pr.PolicyShorCore.from_native(pi.load_native(doc), 2048, conv, F.HEADLINE)


#: pinned under the pre-swap negation (Qualtran Negate = 4(w-1) T per site, ForShor `scratch`)
LEGACY_T = {"sto_reference": 2.567853e12, "shipped": 5.380873e14}


@pytest.mark.parametrize("name, T", sorted(LEGACY_T.items()))
def test_balanced_ir_prices_through_the_dictionary(name, T):
    """The default negation is `oneQubit`: the legacy pin plus, over the document's own negation sites (call graph of its core),
    k * (4 * Toffoli(12w-28 | 10w-18) - 4(w-1)); recomputed, not pasted."""
    expected = T + na.delta_t(na.sites_of(_core(_doc(name))), na.t_legacy, na.t_new)
    assert F.price_document(_doc(name), 2048)["T"] == pytest.approx(expected, rel=1e-6)
    assert F.price_document({**_doc(name), "negation": "scratch"}, 2048)["T"] == pytest.approx(T, rel=1e-6)


@pytest.mark.parametrize("name", ["sto_reference", "shipped"])
def test_every_phase_product_site_is_square_after_expansion(name):
    sites = dict(s for s in _core(_doc(name)).sites if s[0][0] != "qft")
    # Steps 1 and 5 (controlled, padded) and Step 2 (padded) once per modmul; Step 4 twice per modmul, two blocks each
    assert sites == {("cphase_product", 2348, 2348): 2 * 4095, ("phase_product", 2348, 2348): 4095 + 2 * 2 * 4095}


@pytest.mark.parametrize("name", ["sto_reference", "shipped"])
def test_the_unbalanced_ir_of_the_same_document_is_the_old_price(name):
    d = _doc(name)
    ub = {**d, "ir": d["ir_unbalanced"]}
    c = _core(ub)
    assert all(s[0] not in sy.BALANCED_TEMPLATES for s, _ in c.raw_sites)
    assert F.price_document(ub, 2048)["T"] > F.price_document(d, 2048)["T"]


def test_balancing_keeps_the_layout():
    d = _doc("sto_reference")
    assert _core(d).layout_qubits() == _core({**d, "ir": d["ir_unbalanced"]}).layout_qubits() == 56738
