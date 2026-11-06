"""Which planner rows are listed (D9): every frontier row, plus every checked policy at the deltas where the planner did not pick it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # evaluation/
from export_v2.epoch import listed_rows  # noqa: E402

DELTAS = ("1e-2", "3e-4", "1e-4", "1e-6")


def row(rid, digest, created):
    return {"id": rid, "digest": digest, "createdAt": created, "pr": None, "policy": object()}


def frontier():
    """The real shape: the checked PP policy V is the planner's pick only at 1e-4 and 1e-6; U (unchecked) wins at 1e-2 and 3e-4."""
    rows = {("pp", d): [row("u1", "U", "2026-10-01T00:00:00Z")] for d in DELTAS}
    rows[("pp", "1e-4")] = [row("v1", "V", "2026-08-20T00:00:00Z"), row("u1", "U", "2026-10-01T00:00:00Z")]
    rows[("pp", "1e-6")] = [row("v1", "V", "2026-08-20T00:00:00Z")]
    for d in DELTAS:
        rows[("ptp", d)] = [row("t1", "T", "2026-08-01T00:00:00Z"), row("x1", "X", "2026-10-02T00:00:00Z")]
    return rows


def digests(listed, key):
    return [r["digest"] for r in listed[key]]


def test_every_frontier_row_is_listed_whether_or_not_it_was_checked():
    listed, _ = listed_rows(frontier(), {"V": "pp", "T": "ptp"})
    assert "U" in digests(listed, ("pp", "1e-2")) and "U" in digests(listed, ("pp", "1e-4"))
    assert digests(listed, ("ptp", "1e-2")) == ["T", "X"]


def test_a_checked_policy_is_added_at_every_delta_where_the_planner_did_not_pick_it():
    listed, added = listed_rows(frontier(), {"V": "pp", "T": "ptp"})
    for d in DELTAS:
        assert "V" in digests(listed, ("pp", d)), d
    assert added == 2   # V at 1e-2 and 3e-4; T was on the frontier everywhere
    assert [r["added"] for r in listed[("pp", "1e-2")]] == [False, True], "the planner's own row first, V added after it"


def test_an_added_row_takes_the_identity_of_the_earliest_frontier_row_of_that_policy():
    listed, _ = listed_rows(frontier(), {"V": "pp"})
    added = [r for r in listed[("pp", "1e-2")] if r["added"]][0]
    assert (added["id"], added["createdAt"]) == ("v1", "2026-08-20T00:00:00Z")
    assert not any(r["added"] for r in listed[("pp", "1e-4")]), "a frontier row is not marked added"


def test_an_unchecked_policy_is_listed_only_where_the_planner_picked_it_never_added_elsewhere():
    listed, added = listed_rows(frontier(), {"V": "pp"})
    assert digests(listed, ("pp", "1e-6")).count("U") == 0, "U was not the pick at 1e-6, and unchecked policies are not added"
    assert all(not r["added"] for rows in listed.values() for r in rows if r["digest"] in ("U", "X"))
    assert added == 2


def test_nothing_checked_lists_exactly_the_frontier_and_adds_nothing():
    f = frontier()
    listed, added = listed_rows(f, {})
    assert added == 0
    assert {k: [r["id"] for r in v] for k, v in listed.items()} == {k: [r["id"] for r in v] for k, v in f.items()}


def test_a_checked_policy_is_only_added_to_its_own_board_family():
    listed, _ = listed_rows(frontier(), {"V": "pp", "T": "ptp"})
    assert all(r["digest"] != "T" for d in DELTAS for r in listed[("pp", d)])
    assert all(r["digest"] != "V" for d in DELTAS for r in listed[("ptp", d)])


def test_several_frontier_rows_of_one_policy_are_all_kept():
    rows = frontier()
    rows[("pp", "1e-2")] = [row("v1", "V", "2026-08-20T00:00:00Z"), row("v2", "V", "2026-09-01T00:00:00Z")]
    listed, _ = listed_rows(rows, {"V": "pp"})
    assert [r["id"] for r in listed[("pp", "1e-2")]] == ["v1", "v2"]


def test_an_added_row_whose_id_is_taken_at_that_delta_gets_its_own_digest_prefix_so_ids_stay_unique():
    """The real collision: a frontier row id names a submission event, so the planner's own pick at 1e-2 and the checked policy's first
    appearance can be the same id."""
    rows = frontier()
    digest_v = "ab" * 32
    rows[("pp", "1e-2")] = [row("v1", "U", "2026-08-20T00:00:00Z")]          # the planner's pick at 1e-2 has the id the checked policy first had
    rows[("pp", "1e-4")] = [row("v1", digest_v, "2026-08-20T00:00:00Z")]
    rows[("pp", "3e-4")] = [row("u1", "U", "2026-10-01T00:00:00Z")]          # no clash here
    rows[("pp", "1e-6")] = [row("u2", "U", "2026-10-01T00:00:00Z")]
    listed, _ = listed_rows(rows, {digest_v: "pp"})
    at_default = listed[("pp", "1e-2")]
    assert [r["id"] for r in at_default] == ["v1", digest_v[:12]]
    assert at_default[1]["foundAt"] == "v1", "the original id is kept for the panel's title"
    for key, rs in listed.items():
        ids = [r["id"] for r in rs]
        assert len(ids) == len(set(ids)), f"duplicate row id on {key}"
    # where there is no clash the natural id is used
    assert [r["id"] for r in listed[("pp", "3e-4")]] == ["u1", "v1"]
