"""First-principles arithmetic of the negation convention, shared by the tests that used to pin pasted totals.

ForShor's `negateResources` (the `oneQubit` NegationMethod; engine `gidney_circuit`, the default): Toffoli 12w-28 (even w) / 10w-18 (odd w),
truncating at 0. The repo's currency is 4 T per Toffoli (`sto_currency.TS_PER_TOFFOLI`). The legacy negation (ForShor `scratch`) is priced
by the engine as Qualtran's `Negate(QInt(w))`, one w-bit Gidney adder, w - 1 ANDs. The superseded draft is a flat 32 w Toffoli.
Nothing here calls the pricing engine except `sites`, which only COUNTS negation bloqs in a call graph; the prices are these formulas."""
import contextlib

import attrs

import site_engine.fullalg as fa  # noqa: F401  (puts engine/fullalg on sys.path before l2q_full is imported)
from l2q_full import cost_conventions as cc
from l2q_full import policy as lpol
from qualtran.resource_counting import get_bloq_call_graph

TS_PER_TOFFOLI = 4
#: 24,570 negations per run for a policy with no negating table (Step 4's difference), all on the 4398-qubit scratch register.
RUN_NEGATIONS = 24_570
RUN_WIDTH = 4398


def toffoli_lean(w):
    return max(0, 12 * w - 28) if w % 2 == 0 else max(0, 10 * w - 18)


def t_new(w):
    return TS_PER_TOFFOLI * toffoli_lean(w)


def t_legacy(w):
    return TS_PER_TOFFOLI * (w - 1)


def t_draft(w):
    return TS_PER_TOFFOLI * 32 * w


#: What the real negation adds to the T of a run whose only negations are the 24,570 at w = 4398 (a policy with no negating table)
REAL_DELTA = RUN_NEGATIONS * (t_new(RUN_WIDTH) - t_legacy(RUN_WIDTH))
#: The same for the superseded 32 w draft (PR #79's first draft: 13,399,396,920)
DRAFT_DELTA = RUN_NEGATIONS * (t_draft(RUN_WIDTH) - t_legacy(RUN_WIDTH))


def sites_of(bloq):
    """{width: count} of the negations in a bloq's call graph, read off the bloqs, not off their prices."""
    _, sigma = get_bloq_call_graph(bloq, keep=lambda b: isinstance(b, cc.IncrementerNegate))
    return {b.w: k for b, k in sigma.items() if isinstance(b, cc.IncrementerNegate)}


def run_sites(policy, n, rule="board"):
    """Negation sites of the whole order-finding run at modulus bits n under an l2q_full policy (`price_l2q`'s own bloq)."""
    LP = lpol.with_width_rule(policy, fa.width_rule() if rule == "board" else "scan")
    core = fa.core if rule == "board" else fa.core_scan
    return sites_of(attrs.evolve(core(int(n)), policy=LP, convention=fa.HEADLINE))


def delta_t(sites, price_from, price_to):
    """T change of swapping every negation site's price from one formula to the other."""
    return sum(k * (price_to(w) - price_from(w)) for w, k in sites.items())


@contextlib.contextmanager
def negation(impl):
    """Run the site's full-run pricer under the negation convention `impl` (`fullalg._price` reads the conventions from module globals, so its
    cache is cleared on both sides)."""
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
