"""Every distinct table of a policy is listed in its panel, even when two bands use different tables of the same arity (PR #132:
11 tables, 7 arities). The publisher's contract requires the panel's table list to match its trust count (every distinct table)."""
from types import SimpleNamespace

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
