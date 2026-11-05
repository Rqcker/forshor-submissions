"""The PhaseTripleProduct Python verification (`site_engine.ptp_identity`): the counterpart of the PP check
`forshor_qualtran.table_catalog.checked`. Positive: every real PTP table passes. Negative: each way a table can be
wrong is caught, by a check that does not go through `tcost.tables.Table.check`."""
from __future__ import annotations

import dataclasses

import pytest

from forshor_qualtran.policy import PolicyError
from site_engine import crosscheck as xc, library as lib, ptp_identity as pi
from tcost import tables as tt

PTP, PP = tt.TRIPLE, tt.PAIR


def _distinct(tables):
    return list({t.digest: t for t in tables if t.mode == PTP}.values())


REAL = _distinct(lib.sto_tables() + lib.dp_tables() + lib.champion_tables())


def _pick(k: int) -> tt.Table:
    return next(t for t in REAL if t.k == k)


def _bypass(t: tt.Table, **kw) -> tt.Table:
    """Build a table WITHOUT `Table.from_json`, so that tcost's own replay does not run (it would reject it first)."""
    return dataclasses.replace(t, **kw)


def test_real_ptp_tables_exist_for_every_k():
    assert {t.k for t in REAL} == set(range(2, 17))


@pytest.mark.parametrize("t", REAL, ids=lambda t: f"k{t.k}-{t.digest[:8]}")
def test_every_real_ptp_table_passes(t):
    pi.verify_ptp_table(t)


def test_point_count_is_3k_minus_2():
    assert [pi.q3(k) for k in (2, 3, 6, 16)] == [4, 7, 16, 46]
    assert all(len(t.point_order) == pi.q3(t.k) for t in REAL)


# ------------------------------------------------------------------------------------------------ negatives
def test_rejects_a_pp_table():
    pp = next(t for t in lib.sto_tables() if t.mode == PP)
    with pytest.raises(PolicyError, match="takes"):
        pi.verify_ptp_table(pp)


def test_rejects_wrong_point_count():                                   # C1
    t = _pick(3)
    with pytest.raises(PolicyError, match="C1"):
        pi.verify_ptp_table(_bypass(t, point_order=t.point_order[:-1]))


def test_rejects_duplicate_point():                                     # C2
    t = _pick(3)
    pts = list(t.point_order)
    pts[1] = pts[0]
    with pytest.raises(PolicyError, match="C2"):
        pi.verify_ptp_table(_bypass(t, point_order=tuple(pts)))


def test_rejects_program_that_does_not_return_to_start():              # C4
    t = _pick(3)
    ops = list(t.ops)
    last = max(i for i, o in enumerate(ops) if o[0] != "phaseProduct")
    with pytest.raises(PolicyError, match="C4|checkpoint|phase products"):
        pi.verify_ptp_table(_bypass(t, ops=tuple(ops[:last] + ops[last + 1:])))


def test_rejects_swapped_points():                                      # identity: coefficients pair with wrong checkpoints
    t = _pick(4)
    pts = list(t.point_order)
    pts[0], pts[1] = pts[1], pts[0]
    with pytest.raises(PolicyError, match="identity"):
        pi.verify_ptp_table(_bypass(t, point_order=tuple(pts)))


def test_rejects_a_changed_shift():                                     # identity: a wrong scaling in the program
    t = _pick(4)
    ops = list(t.ops)
    i = next(i for i, o in enumerate(ops) if o[0] == "addScaled" and o[4] > 0)
    o = list(ops[i])
    o[4] += 1
    ops[i] = tuple(o)
    with pytest.raises(PolicyError, match="identity|C4"):
        pi.verify_ptp_table(_bypass(t, ops=tuple(ops)))


def test_rejects_a_flipped_sign():
    t = _pick(3)
    ops = list(t.ops)
    i = next(i for i, o in enumerate(ops) if o[0] == "addScaled")
    o = list(ops[i])
    o[3] = 1 - int(o[3]) if isinstance(o[3], int) and o[3] in (0, 1) else -o[3]
    ops[i] = tuple(o)
    with pytest.raises(PolicyError):
        pi.verify_ptp_table(_bypass(t, ops=tuple(ops)))


def test_identity_is_not_vacuous():
    """A wrong coefficient set must fail the identity: perturb one coefficient and recompute the sum."""
    t = _pick(2)
    pts = tuple(pi._key(pi.point_from_sto(tt.point_str(p))) for p in t.point_order)
    c = list(pi._coeffs(pi.q3(t.k), pts, 3))
    # The identity holds with the true coefficients (verify_ptp_table passes) and breaks with a perturbed one.
    pi.verify_ptp_table(t)
    pi._coeffs.cache_clear()
    real = pi._coeffs
    try:
        pi._coeffs = lambda q, p, m: tuple([c[0] + 1] + c[1:])
        with pytest.raises(PolicyError, match="identity"):
            pi.verify_ptp_table(t, ms=(3,))
    finally:
        pi._coeffs = real


# ------------------------------------------------------------------------------------------------ wiring
def test_policy_check_deduplicates_tables():
    P = lib.sto_reference_policies()[PTP]
    done = pi.verify_ptp_policy(P)
    assert len(done) == len({b.table.digest for b in P.bands})


def test_verify_policy_rejects_pp_policy():
    with pytest.raises(PolicyError):
        pi.verify_ptp_policy(lib.sto_reference_policies()[PP])


def test_to_l2q_runs_the_ptp_check():
    """Engine 2's conversion now verifies PTP tables: a table that bypassed tcost's replay is rejected there."""
    P = lib.sto_reference_policies()[PTP]
    xc.to_l2q(P)                                                          # a good policy converts
    b0 = P.bands[-1]
    bad_table = _bypass(b0.table, point_order=tuple(reversed(b0.table.point_order)))
    bad = dataclasses.replace(P, bands=P.bands[:-1] + (dataclasses.replace(b0, table=bad_table),))
    with pytest.raises(PolicyError):
        xc.to_l2q(bad)
