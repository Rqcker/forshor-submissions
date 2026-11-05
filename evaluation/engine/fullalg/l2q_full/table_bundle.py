"""A bundle built from a catalogue table: the same document `forshor_emit bundle <k>` has the shape of, with every
section (schedule, coefficient polynomials, widths, QFT plan, recursion) computed from ONE Toom-Cook table.

Why: `forshor_emit bundle` emits ForShor's `standard` table only. The cheaper tables (the DP catalogue's Plan A and
champion tables, `fixtures/dp_tables/`) have no emitter command, but their admissibility (C1-C4) is proved by
`decide +kernel` in the catalogue build (`forshor_qualtran.policy_catalog`, certificates under
`artifacts/policy_catalog/`). A bundle built here lets the width/recursion/pricing code run on such a table.

The result is labelled REFERENCE-MIRROR, never REAL-LEAN: it is derived in Python from the table, not emitted by
Lean, so no verdict computed from it may PASS (the repository's standing rule for mirror documents).

Example: `planA-paired-k{k}` for k = 2..6 reproduces, bit for bit, the numbers computed on the frozen
2026-08-27 `generate` schedules (the ladder `[4096, 1044, 281, 91, 45, 32, 28, 27]` at k = 4, n = 4096).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator

from .emit import Bundle
from .mirrors import coeff_poly, qft_plan, recursion, schedule, width
from .mirrors.bundle import mirror_bundle, strip_private
from .policy import ToomCookTable
from .provenance import Provenance

_USERS = (schedule, coeff_poly, width, qft_plan, recursion)       # the mirror modules that call `raw_table`


def _point_json(p) -> Dict[str, Any]:
    kind, v = p
    return {"kind": "int", "z": int(v)} if kind == "int" else {"kind": "frac", "m": int(v)}


@contextmanager
def _table_for(k: int, table: ToomCookTable) -> Iterator[None]:
    """Make the mirror modules read `table` as arity `k`'s schedule, and restore them afterwards."""
    original = schedule.raw_table
    doc = {"ops": [dict(o) for o in table.ops], "points": [_point_json(p) for p in table.points]}

    def raw_table(kk: int):
        return (doc, f"catalogue:{table.name}") if kk == k else original(kk)

    saved = [(m, m.raw_table) for m in _USERS if hasattr(m, "raw_table")]
    for m, _ in saved:
        m.raw_table = raw_table
    try:
        yield
    finally:
        for m, fn in saved:
            m.raw_table = fn


def bundle_from_table(table: ToomCookTable, m_max: int = 24, w_max: int = 96) -> Bundle:
    """`forshor_emit bundle`-shaped document for `table`, REFERENCE-MIRROR provenance."""
    table.check()
    with _table_for(table.k, table):
        doc = strip_private(mirror_bundle(table.k, m_max, w_max))
    doc["table"] = table.name
    return Bundle(table.k, doc, Provenance.REFERENCE_MIRROR,
                  notes=[f"derived from catalogue table {table.name!r}, not emitted by forshor_emit "
                         "(admissibility is the catalogue build's certificate, not this document's)"])


def catalogue_bundles(ks=(2, 3, 4, 5, 6), family: str = "planA-paired", m_max: int = 24, w_max: int = 96):
    """`{k: Bundle}` from the DP catalogue's `<family>-k<k>` tables (default: the paired Plan A tables, which are
    certified in the catalogue build and numerically identical to the frozen `generate` schedules)."""
    from . import table_catalog
    cat = {t.name: t for t in table_catalog.load_dp_catalog()}
    return {k: bundle_from_table(cat[f"{family}-k{k}"], m_max, w_max) for k in ks}


def frozen_bundle(k: int, m_max: int = 24, w_max: int = 96) -> Bundle:
    """The frozen REFERENCE-MIRROR bundle for arity `k`: every section computed from the frozen 2026-08-27 `generate`
    schedule that `mirrors.schedule.raw_table` serves. The single-k recursion bloqs read their op structure from that
    same reference, so this is the bundle on which they and a bundle-based model (the resolution twin, the
    census) are describing one table."""
    return Bundle(k, strip_private(mirror_bundle(k, m_max, w_max)), Provenance.REFERENCE_MIRROR,
                  notes=["frozen mirror bundle: the `generate`-table reference the single-k bloqs read their ops from"])
