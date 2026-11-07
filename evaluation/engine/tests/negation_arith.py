"""First-principles arithmetic of the negation convention, shared by the tests that pin totals under the default method.

ForShor's `negateResources` (the `oneQubit` NegationMethod; engine `gidney_circuit`, the default): Toffoli 12w-28 (even w) / 10w-18 (odd w),
truncating at 0. The repo's currency is 4 T per Toffoli (`sto_currency.TS_PER_TOFFOLI`). The legacy negation (ForShor `scratch`) is priced
by the engine as Qualtran's `Negate(QInt(w))` (one w-bit Gidney adder, w - 1 ANDs) under the `qualtran` adders, and as complement + Cuccaro adder
(2w - 3 Toffoli) under the `cuccaro` adders (the literal-Lean tier).
Nothing here calls the pricing engine except `sites_of`, which only COUNTS negation bloqs in a call graph; the prices are these formulas.
(Same arithmetic as the export branch's `negation_arith.py`, E1 7e48afc; its price-swapping context manager is not needed here:
`fullalg.price_*` take the method as an argument.)"""
import attrs

import site_engine.fullalg as fa  # noqa: F401  (puts engine/fullalg on sys.path before l2q_full is imported)
from l2q_full import cost_conventions as cc
from l2q_full import policy as lpol
from l2q_full import policy_ir as pi
from l2q_full import policy_shor_price as pr
from qualtran.resource_counting import get_bloq_call_graph

TS_PER_TOFFOLI = 4
#: 24,570 negations per run for a policy with no negating table, all on the 4398-qubit scratch register.
RUN_NEGATIONS = 24_570
RUN_WIDTH = 4398


def toffoli_lean(w):
    return max(0, 12 * w - 28) if w % 2 == 0 else max(0, 10 * w - 18)


def t_new(w):
    """T of one `oneQubit` negation: 4 per Toffoli."""
    return TS_PER_TOFFOLI * toffoli_lean(w)


def t_legacy(w):
    """T of one `scratch` negation under the `qualtran` adders (Negate = w - 1 ANDs)."""
    return TS_PER_TOFFOLI * (w - 1)


def t_legacy_cuccaro(w):
    """T of one `scratch` negation under the `cuccaro` adders (2w - 3 Toffoli)."""
    return TS_PER_TOFFOLI * max(0, 2 * w - 3)


#: What the real negation adds to the T of a run whose only negations are the 24,570 at w = 4398 (a policy with no negating table)
REAL_DELTA = RUN_NEGATIONS * (t_new(RUN_WIDTH) - t_legacy(RUN_WIDTH))


def sites_of(bloq):
    """{width: count} of the negations in a bloq's call graph, read off the bloqs, not off their prices. The bloq must be built
    under a `gidney_circuit` convention (the default), whose negations are `IncrementerNegate`."""
    # by class name: the engine can be imported as `l2q_full` (site_engine) or as `fullalg.l2q_full` (some tests): two module objects
    is_neg = lambda b: type(b).__name__ == "IncrementerNegate"
    _, sigma = get_bloq_call_graph(bloq, keep=is_neg)
    return {b.w: k for b, k in sigma.items() if is_neg(b)}


def policy_sites(l2q_policy, n, rule="board", spec=None):
    """Negation sites of the whole order-finding run at modulus bits n under an l2q_full policy (`price_l2q`'s own bloq)."""
    LP = lpol.with_width_rule(l2q_policy, fa.width_rule() if rule == "board" else "scan")
    core = fa.core if rule == "board" else fa.core_scan
    return sites_of(attrs.evolve(core(int(n)), policy=LP, convention=(spec or fa.HEADLINE).with_(negate="gidney_circuit")))


def document_sites(doc, n, spec=None):
    """Negation sites of the run `price_document` prices (a ForShor document's own IR census)."""
    sy = fa.sy
    conv = sy.Convention.for_modulus(1 << (int(n) - 1), fa.precision_m(n))
    hl = (spec or fa.HEADLINE).with_(negate="gidney_circuit")
    return sites_of(pr.PolicyShorCore.from_native(pi.load_native(doc), int(n), conv, hl))


def delta_t(sites, price_from, price_to):
    """T change of swapping every negation site's price from one formula to the other."""
    return sum(k * (price_to(w) - price_from(w)) for w, k in sites.items())
