"""Which planner rows are listed (D9): every frontier row, plus the single-arity baseline policies of each board family at every delta."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # evaluation/
from export_v2.epoch import BASELINE_CREATED_AT, first_per_policy, list_rows  # noqa: E402

DELTAS = ("1e-2", "3e-4", "1e-4", "1e-6")


def row(rid, digest, number, created="2026-10-01T00:00:00Z"):
    return {"id": rid, "digest": digest, "createdAt": created, "pr": None, "policy": object(), "number": number}


def baseline(digest, board, number):
    return {"id": digest[:12], "digest": digest, "createdAt": BASELINE_CREATED_AT, "pr": None, "policy": object(), "number": number,
            "board": board, "baseline": True}


def frontier():
    rows = {("pp", d): [row("s1", "A" * 64, 1), row("s2", "B" * 64, 2)] for d in DELTAS}
    rows.update({("ptp", d): [row("s1", "C" * 64, 1)] for d in DELTAS})
    return rows


def test_every_frontier_row_is_listed_and_every_baseline_of_the_board_family_at_every_delta():
    bl = [baseline("D" * 64, "pp", 22), baseline("E" * 64, "pp", 23), baseline("F" * 64, "ptp", 24)]
    listed = list_rows(frontier(), bl)
    for d in DELTAS:
        assert [r["number"] for r in listed[("pp", d)]] == [1, 2, 22, 23], d
        assert [r["number"] for r in listed[("ptp", d)]] == [1, 24], d


def test_a_baseline_never_appears_on_the_other_board_family():
    listed = list_rows(frontier(), [baseline("D" * 64, "pp", 22), baseline("F" * 64, "ptp", 24)])
    assert all(r["digest"] != "F" * 64 for d in DELTAS for r in listed[("pp", d)])
    assert all(r["digest"] != "D" * 64 for d in DELTAS for r in listed[("ptp", d)])


def test_no_baselines_lists_exactly_the_frontier():
    f = frontier()
    listed = list_rows(f, [])
    assert {k: [r["id"] for r in v] for k, v in listed.items()} == {k: [r["id"] for r in v] for k, v in f.items()}


def test_a_baseline_is_listed_at_a_delta_where_the_board_has_no_frontier_rows():
    f = frontier()
    f[("pp", "1e-2")] = []
    listed = list_rows(f, [baseline("D" * 64, "pp", 22)])
    assert [r["number"] for r in listed[("pp", "1e-2")]] == [22]


def test_ids_and_numbers_stay_unique_on_every_board_and_delta():
    bl = [baseline(c * 64, "pp", 22 + i) for i, c in enumerate("DEG")]
    for key, rows in list_rows(frontier(), bl).items():
        ids = [r["id"] for r in rows]
        numbers = [r["number"] for r in rows]
        assert len(ids) == len(set(ids)), key
        assert len(numbers) == len(set(numbers)), key


def test_baselines_have_a_fixed_creation_time_so_the_export_is_reproducible():
    assert BASELINE_CREATED_AT == "2026-11-06T00:00:00Z"


def test_a_policy_repeated_across_submissions_is_listed_once_as_its_earliest_row():
    """The real shape at 1e-2: the planner's pick did not change for sixteen submissions in a row."""
    same = [row(f"s{i}", "A" * 64, i, created=f"2026-10-{i:02d}T00:00:00Z") for i in (5, 3, 4, 9)]   # deliberately not in time order
    other = row("s7", "B" * 64, 7, created="2026-10-07T00:00:00Z")
    got = first_per_policy(same + [other])
    assert sorted(r["number"] for r in got) == [3, 7], "A once (its earliest, 3), and B"
    listed = list_rows({("pp", "1e-2"): same + [other]}, [])
    assert sorted(r["number"] for r in listed[("pp", "1e-2")]) == [3, 7]


def test_distinct_policies_with_equal_numbers_elsewhere_are_all_kept_and_baselines_are_never_merged_with_frontier_rows():
    rows = {("pp", "1e-2"): [row("s1", "A" * 64, 1), row("s2", "B" * 64, 2), row("s3", "A" * 64, 3)]}
    listed = list_rows(rows, [baseline("C" * 64, "pp", 22)])
    assert [r["number"] for r in listed[("pp", "1e-2")]] == [1, 2, 22]
