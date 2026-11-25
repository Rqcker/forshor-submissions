"""Trusted schema helpers for the T-count ranking. The verifier, the leaderboard and the rescore use this one copy.

This module is pure Python, except `rescore_entry`. That function imports the scorer, which uses Qualtran, lazily.

* `validate_tcount(config)`: the `tcount` block must state the conventions of the code again (`tcost.currency`):
  `model_version == MODEL_VERSION` and each `CONVENTIONS` key equal. n, delta and modes must be in range.
* `fingerprint(tcount)`: a hash of all data that sets a score. Each stored score includes it, and it is the
  staleness key. (A change of delta, n, conventions or model version makes old scores invalid.)
* `policy_result_entry(score, tcount)`: the one shape of a stored score.
* `rescore_entry(entry, tcount)`: the trusted re-score. It builds the policy again from the archived band
  tables (replay-checked, bounded), scores it, and returns the result. The callers reject each mismatch.
* `solo_entry(table, tcount)`: the T score of one table alone (T_solo). This is the T at n of the cheapest policy
  from only this table and the schoolbook leaf. Promotion selects the secondary table library by T_solo.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from .currency import CONVENTIONS, MODEL_VERSION

TCOUNT_FIELDS = ("toffoli", "and", "cswap", "t", "rotation", "clifford", "measurement", "cnot")
VALID_MODES = ("PhaseProduct", "PhaseTripleProduct")


class SchemaError(ValueError):
    pass


def validate_tcount(config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the `tcount` block of `leaderboard/config.json` against the code. Return a normalized copy."""
    tcount = config.get("tcount")
    if not isinstance(tcount, dict):
        raise SchemaError("config tcount must be an object")
    n, delta, modes = tcount.get("n"), tcount.get("delta"), tcount.get("modes")
    if not isinstance(n, int) or isinstance(n, bool) or n < 2:
        raise SchemaError("config tcount.n must be an integer >= 2")
    if not isinstance(delta, (int, float)) or isinstance(delta, bool) or not 0 < delta < 1:
        raise SchemaError("config tcount.delta must be in (0, 1)")
    if not isinstance(modes, list) or not modes or len(set(modes)) != len(modes) or \
            any(m not in VALID_MODES for m in modes):
        raise SchemaError("config tcount.modes must list distinct valid modes")
    if tcount.get("model_version") != MODEL_VERSION:
        raise SchemaError(f"config tcount.model_version must be {MODEL_VERSION!r} (the installed tcost), "
                          f"got {tcount.get('model_version')!r}")
    for key, value in CONVENTIONS.items():
        if tcount.get(key) != value:
            raise SchemaError(f"config tcount.{key} = {tcount.get(key)!r} disagrees with the code ({value!r}); "
                              "conventions are defined in tcost/currency.py")
    k_max = (config.get("target_matrix") or {}).get("k_max")
    if not isinstance(k_max, int) or isinstance(k_max, bool) or k_max < 2:
        raise SchemaError("config target_matrix.k_max must be an integer >= 2")
    out = dict(tcount)
    out["delta"] = float(delta)
    out["k_max"] = k_max
    return out


def fingerprint(tcount: Dict[str, Any]) -> str:
    """Return a hash of each field that sets a score (n, delta, modes, conventions, model version, k_max)."""
    keys = ("n", "delta", "modes", "model_version", "k_max", *CONVENTIONS)
    payload = {k: tcount.get(k) for k in keys}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def policy_result_entry(score: Dict[str, Any], tcount: Dict[str, Any]) -> Dict[str, Any]:
    """Return the stored shape of one policy score for one mode."""
    breakdown = score.get("t_breakdown") or {}
    return {
        "mode": score["mode"],
        "n": score["n"],
        "t_count": score["t_count"],
        **{field: score["gate_counts"][field] for field in TCOUNT_FIELDS},
        "ts_per_rotation": breakdown.get("ts_per_rotation"),
        "rotation_eps": breakdown.get("rotation_eps"),
        "delta": breakdown.get("delta"),
        "ladder": score.get("ladder"),
        "bands": score.get("bands"),
        "closed_form": score.get("closed_form"),
        "model_version": score.get("model_version", tcount.get("model_version")),
        "tcount_fingerprint": fingerprint(tcount),
    }


SOLO_FIELDS = ("t_solo", "t_solo_ladder", "table_digest", "tcount_fingerprint")


def solo_entry(table: Dict[str, Any], tcount: Dict[str, Any]) -> Dict[str, Any]:
    """Return T_solo of one table JSON ({mode, k, point_order, operations}). Replay-check the table first.

    T_solo = T at the configured n and delta of the cheapest policy that uses only this table (at each width where
    it decreases T) and the schoolbook leaf below. This is the recursion cost of the table itself. It is
    independent of all other tables, so the library choice is stable and deterministic."""
    from .planner import optimal_policy
    from .score import score_policy
    from .tables import Table, TableError

    try:
        t = Table.from_json({"mode": table.get("mode"), "k": table.get("k"),
                             "point_order": table.get("point_order"), "operations": table.get("operations")})
    except TableError as e:
        raise SchemaError(f"table {table.get('mode')} k={table.get('k')}: {e}") from e
    if not 2 <= t.k <= tcount["k_max"] or t.mode not in tcount["modes"]:
        raise SchemaError(f"table {t.mode} k={t.k} is outside the configured modes / k range")
    policy, _ = optimal_policy([t], t.mode, tcount["n"], tcount["delta"], name=f"solo k={t.k}")
    score = score_policy(policy, tcount["n"], tcount["delta"])
    return {"t_solo": score["t_count"], "t_solo_ladder": score["ladder"], "table_digest": t.digest,
            "tcount_fingerprint": fingerprint(tcount)}


def rescore_entry(entry: Dict[str, Any], tcount: Dict[str, Any]) -> Dict[str, Any]:
    """Derive a recursion score again from its archived band tables, in the trusted process.

    `entry` = {"mode", "bands": [{"threshold", "k"}], "band_tables": [table JSON with "threshold"]}.
    `Table.from_json` parses and replay-checks each band table (bounded before the replay). The bands must match
    the tables one-to-one, with 2 <= k <= k_max and one table for each k. Then the function scores the policy.
    """
    from .score import score_policy
    from .tables import Band, Policy, Table, TableError

    mode = entry.get("mode")
    if mode not in tcount["modes"]:
        raise SchemaError(f"mode {mode!r} is not configured")
    bands, tables = entry.get("bands"), entry.get("band_tables")
    if not isinstance(bands, list) or not isinstance(tables, list) or not bands or len(bands) != len(tables):
        raise SchemaError(f"{mode}: bands and band_tables must be non-empty and parallel")
    built: List[Band] = []
    for band, tab in zip(bands, tables):
        if not isinstance(band, dict) or not isinstance(tab, dict):
            raise SchemaError(f"{mode}: malformed band")
        k, threshold = band.get("k"), band.get("threshold")
        if type(threshold) is not int or threshold < 0:
            raise SchemaError(f"{mode}: band threshold must be a non-negative integer, got {threshold!r}")
        if not isinstance(k, int) or not 2 <= k <= tcount["k_max"]:
            raise SchemaError(f"{mode}: band k={k!r} outside [2, {tcount['k_max']}]")
        if tab.get("threshold") != threshold or tab.get("k") != k:
            raise SchemaError(f"{mode}: band {band} does not match its table")
        try:
            table = Table.from_json({"mode": mode, "k": k, "point_order": tab.get("point_order"),
                                     "operations": tab.get("operations")})
        except TableError as e:
            raise SchemaError(f"{mode} k={k}: {e}") from e
        built.append(Band(int(threshold), table))
    by_k: Dict[int, str] = {}
    for b in built:
        if by_k.setdefault(b.table.k, b.table.digest) != b.table.digest:
            raise SchemaError(f"{mode}: two different tables for k={b.table.k}")
    try:
        policy = Policy(mode=mode, bands=tuple(built), name=mode)
    except ValueError as e:
        raise SchemaError(f"{mode}: {e}") from e
    score = score_policy(policy, tcount["n"], tcount["delta"], with_range=True)
    score["bands"] = [{"threshold": b["threshold"], "k": b["k"]} for b in bands]
    return score
