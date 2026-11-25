"""Every distinct table of a policy is listed in its panel, even when two bands use different tables of the same arity (for example,
11 tables over 7 arities). The publisher's contract requires the panel's table list to match its trust count (every distinct table)."""
from types import SimpleNamespace

import pytest

from export_v2.pricing import _all_tables


def _table(k, digest):
    return SimpleNamespace(k=k, digest=digest)


def _policy(*tables):
    return SimpleNamespace(bands=[SimpleNamespace(table=t) for t in tables])


ENG = SimpleNamespace(policy_view=SimpleNamespace(table_view=lambda t: {"k": t.k, "digest": t.digest}))


def test_two_tables_of_one_arity_are_both_listed():
    a7, b7, c8, d5 = _table(7, "a7"), _table(7, "b7"), _table(8, "c8"), _table(5, "d5")
    got = _all_tables(ENG, _policy(c8, a7, b7, a7, d5, c8))
    assert [t["digest"] for t in got] == ["c8", "a7", "b7", "d5"], "one entry per digest, arity descending, ties in band order"


def test_one_table_per_arity_is_the_engines_list():
    # the engine keeps the first table of each arity, arity descending: with one table per arity the lists are the same
    t3, t5, t4 = _table(3, "t3"), _table(5, "t5"), _table(4, "t4")
    got = _all_tables(ENG, _policy(t5, t4, t3, t4))
    assert [t["digest"] for t in got] == ["t5", "t4", "t3"]


def test_used_at_n_comes_from_the_levels_by_digest():
    from export_v2.pricing import _fix_bands_used
    # two k=3 tables both on the chain (levels), a k=5 band off it: the engine's one-table-per-k view marked the second k=3 unused
    view = {"levels": [{"digest": "a3"}, {"digest": "b3"}, {"digest": None}],
            "bands": [{"digest": "c5", "used_at_n": False}, {"digest": "a3", "used_at_n": True}, {"digest": "b3", "used_at_n": False}]}
    _fix_bands_used(view)
    assert [b["used_at_n"] for b in view["bands"]] == [False, True, True]


@pytest.mark.slow
def test_a_policy_with_distinct_k_prices_exactly_as_the_engine_alone():
    """The multi-table fix must be a no-op on every policy already published: each of those has one table per k, and for such a
    policy `price_views` must return the engine's own view, key for key. This is what keeps existing rows, digests and scores
    untouched while `_all_tables`/`_fix_bands_used` are layered on top of `evaluation/engine`, which the epoch pins.

    Fails if `_all_tables` stops reproducing `policy_view.view`'s one-table-per-arity list (its dedupe key, its `-k` sort or the
    stability of that sort), or if `_fix_bands_used` stops agreeing with the engine's `tables_used` derivation of `used_at_n`.
    Either would silently move panels already published."""
    from export_v2 import engine
    from export_v2.mapping import DELTA_VALUES
    from export_v2.pricing import N, price_views
    eng = engine.load()
    rec = next(r for r in eng.submitted.seeded() if r["mode"] == eng.tables.PAIR)
    policy = eng.seeded_board.policy_of(rec)
    priced = policy.with_widths("exact")
    assert len({b.table.k for b in priced.bands}) == len(priced.bands), "fixture must be a one-table-per-k policy"
    # the engine's view with nothing layered on it, at the same n, delta and title `price_views` uses
    alone = eng.policy_view.view(priced, N, DELTA_VALUES["1e-2"], "pin")
    got = price_views(policy, "pin", "1e-2", ["pp"])["pp"]
    assert got["tables"] == alone["tables"], "the overlay changed the table list of a one-table-per-k policy"
    assert got["bands"] == alone["bands"], "the overlay changed used_at_n of a one-table-per-k policy"
    assert got == alone, "the overlay changed the panel of a one-table-per-k policy"
