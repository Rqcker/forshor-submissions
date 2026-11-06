"""Which planner rows are listed: verified policies at every delta, nothing else (decision D8 plus the delta-independence of evidence)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # evaluation/
from export_v2.epoch import listed_rows  # noqa: E402

DELTAS = ("1e-2", "3e-4", "1e-4", "1e-6")


def row(rid, digest, created):
    return {"id": rid, "digest": digest, "createdAt": created, "pr": None, "policy": object()}


def frontier():
    """The real launch shape: the verified PP policy V is the planner's pick only at 1e-4 and 1e-6; U (unverified) wins at 1e-2 and 3e-4."""
    rows = {("pp", d): [row("u1", "U", "2026-10-01T00:00:00Z")] for d in DELTAS}
    rows[("pp", "1e-4")] = [row("v1", "V", "2026-08-20T00:00:00Z"), row("u1", "U", "2026-10-01T00:00:00Z")]
    rows[("pp", "1e-6")] = [row("v1", "V", "2026-08-20T00:00:00Z")]
    for d in DELTAS:
        rows[("ptp", d)] = [row("t1", "T", "2026-08-01T00:00:00Z"), row("x1", "X", "2026-10-02T00:00:00Z")]
    return rows


def test_a_verified_policy_is_listed_at_every_delta_even_where_the_planner_picked_another():
    listed, added = listed_rows(frontier(), {"V": "pp", "T": "ptp"})
    for d in DELTAS:
        assert [r["digest"] for r in listed[("pp", d)]] == ["V"], d
        assert [r["digest"] for r in listed[("ptp", d)]] == ["T"], d
    assert added == 2   # V at 1e-2 and 3e-4; T was on the frontier everywhere


def test_an_added_row_takes_the_identity_of_the_earliest_frontier_row_of_that_policy():
    listed, _ = listed_rows(frontier(), {"V": "pp"})
    added = [r for r in listed[("pp", "1e-2")] if r["added"]][0]
    assert (added["id"], added["createdAt"]) == ("v1", "2026-08-20T00:00:00Z")
    assert not any(r["added"] for r in listed[("pp", "1e-4")]), "a frontier row is not marked added"


def test_an_unverified_policy_is_never_listed_at_any_delta():
    listed, _ = listed_rows(frontier(), {"V": "pp"})
    assert all(r["digest"] != "U" for rows in listed.values() for r in rows)
    assert listed[("ptp", "1e-2")] == []


def test_nothing_verified_lists_nothing_and_adds_nothing():
    listed, added = listed_rows(frontier(), {})
    assert added == 0 and all(rows == [] for rows in listed.values())


def test_a_verified_policy_is_only_added_to_its_own_board_family():
    listed, _ = listed_rows(frontier(), {"V": "pp", "T": "ptp"})
    assert all(r["digest"] != "T" for d in DELTAS for r in listed[("pp", d)])
    assert all(r["digest"] != "V" for d in DELTAS for r in listed[("ptp", d)])


def test_several_frontier_rows_of_a_verified_policy_are_all_kept():
    rows = frontier()
    rows[("pp", "1e-2")] = [row("v1", "V", "2026-08-20T00:00:00Z"), row("v2", "V", "2026-09-01T00:00:00Z")]
    listed, _ = listed_rows(rows, {"V": "pp"})
    assert [r["id"] for r in listed[("pp", "1e-2")]] == ["v1", "v2"]
