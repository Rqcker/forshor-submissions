"""Shared helper for the single-k invariant: walk a Qualtran call graph collecting the Toom-Cook
arity every OUR OWN bloq type carries (`.k` or `.k_top`), restricted to `forshor_qualtran`-defined
types so Qualtran's own library bloqs (e.g. `AddK`/`XorK`, which coincidentally name a constructor
parameter `k` too -- the constant to add, unrelated to arity -- and only surface once `Negate`'s
decomposition is actually walked) can never be misread as a foreign arity.

Centralizing this in one place (2026-09-03) fixes a bug that recurred independently in
`bloqs/qft.py::LeanQFT._twiddle_k` and three separate test files (`test_modmul_lean_verify.py`,
`test_modexp_lean_verify.py`, `test_orderfinding_lean_verify.py`) -- each needed the same
module-scoped restriction, and only `bloqs/qft.py` (fixed the same night the sides=2 doubling that
first exposed this was fixed) had it; the other three carried an unrestricted `hasattr(b, "k")` and
demonstrably misfired once their call graphs actually reached AddK/XorK.
"""
from __future__ import annotations

from typing import Optional


def bloq_arity(b) -> Optional[int]:
    """The arity `b` carries, however it names it (`.k` or `.k_top`) -- or None if `b` isn't one of
    our own types (forshor_qualtran.*), in which case any `.k`/`.k_top` attribute it happens to have
    is a naming coincidence, not a Toom-Cook arity."""
    if not type(b).__module__.startswith("forshor_qualtran"):
        return None
    return getattr(b, "k", getattr(b, "k_top", None))


def collect_arities(top, ssa) -> set:
    """Every arity appearing anywhere in `top`'s full Qualtran call-graph decomposition -- the outer
    object and every descendant reachable via `build_call_graph`. `ssa` is a
    `qualtran.resource_counting.SympySymbolAllocator`, threaded through exactly as `get_cost_value`
    would. A bloq whose `build_call_graph` raises (atomic library bloqs like `And()`, which have no
    further decomposition) is treated as a leaf, not an error."""
    arities: set = set()
    seen: set = set()

    def visit(b) -> None:
        key = str(b)
        if key in seen:
            return
        seen.add(key)
        ka = bloq_arity(b)
        if ka is not None:
            arities.add(ka)
        build = getattr(b, "build_call_graph", None)
        if build is None:
            return
        try:
            graph = build(ssa)
        except Exception:
            return
        for child in graph:
            visit(child)

    visit(top)
    return arities
