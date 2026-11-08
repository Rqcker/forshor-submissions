"""The seeded policy records on the boards (PLAN_v2 Stage 11, STO mirror): `submitted.seeded()` priced LIVE.

    PP / PTP boards   `board(mode, delta)`: each seeded record of the mode priced with tcost (engine 1, cross-checked by
                      engine 2) at n = 2048, ranked by T; benchmark rows (STO reference, KMY) are listed apart and never
                      ranked.
    full-run board    `full_frontier.submitted_board` prices the PP records with `fullalg`; it uses `summary` here for
                      each record's trust and provenance.

A record stores no T. Each row carries its policy trust level (`trust.policy_trust`: ForShor policy-verified, Policy
Lean-verified at exact widths (STO mirror / PTP suite), or tables only), its table trust (live, `trust.of_digests`), its provenance and the
flag `seeded_from_planner` (the page shows a "seeded from planner outputs" badge).
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import frontier as fr
from . import library as lib
from . import submitted
from . import trust
from tcost import tables as tt

SEEDED_BADGE = "seeded from planner outputs"
RULE = ("lowest T of one {mode} at n = 2048 (tcost, Qualtran digital-product leaf for PP, digital triple-product leaf "
        "for PTP), priced live")


def policy_of(rec: Dict[str, Any]) -> tt.Policy:
    """The record's policy from its site_policy.json; every band must match the record's stated digest."""
    P = tt.load_policies(rec["site_policy_path"])[0]
    got = [(b.threshold, b.table.digest) for b in P.bands]
    want = [(b["threshold"], b["digest"]) for b in rec["bands"]]
    if got != want:
        raise ValueError(f"{rec['id']}: site_policy.json does not match the record's bands")
    return P


def _provenance(rec: Dict[str, Any]) -> Dict[str, Any]:
    prov = rec["provenance"]
    occ = prov["occurrences"]
    rows = sorted({o["row"] for o in occ if o["source"] == "leaderboard"})
    ms = sorted({o["milestone"] for o in occ if o["source"] == "milestone"})
    deltas = sorted({o["delta"] for o in occ}, reverse=True)
    return {"planner": prov["planner"], "first_seen": prov["first_seen"], "occurrences": len(occ),
            "leaderboard_rows": rows, "milestones": ms, "deltas": deltas,
            "tables": [{"id": t["id"], "k": t["k"], "kind": t["kind"]} for t in prov["tables"]]}


def summary(rec: Dict[str, Any]) -> Dict[str, Any]:
    """What a board row shows about a seeded record besides its price: trust, provenance, badge."""
    pt = trust.policy_trust(rec)
    tl = trust.of_digests(b["digest"] for b in rec["bands"])
    ttag, tkind = trust.tag(tl)
    return {"seeded_from_planner": True, "badge": SEEDED_BADGE, "flags": rec["flags"],
            "policy_trust": pt, "table_trust": {**tl, "tag": ttag, "kind": tkind},
            "provenance": _provenance(rec)}


def board(mode: str, delta: float) -> Dict[str, Any]:
    """Every seeded record of `mode`, priced live at n = 2048 and ranked; the benchmarks apart, unranked."""
    delta = fr.check_delta(delta)
    rows: List[Dict[str, Any]] = []
    for rec in submitted.seeded():
        if rec["mode"] != mode:
            continue
        row = {"id": rec["id"], "mode": mode, "created_at": rec.get("created_at"),
               "bands": [{k: v for k, v in b.items() if k != "table"} for b in rec["bands"]], **summary(rec)}
        try:
            p = fr.point(policy_of(rec), fr.HERO_N, delta, with_qubits=False)
        except Exception as e:  # noqa: BLE001  (shown, never hidden)
            row.update(T=None, error=str(e))
            rows.append(row)
            continue
        row.update(T=p["T"], ladder_str=p["ladder_str"], label=p["label"], agree=p["agree"], engine2_T=p["engine2_T"],
                   rotations=p["rotations"], toffoli=p["toffoli"], n=fr.HERO_N, delta=delta, priced_live=True,
                   **({"grid_leaf_T": p["grid_leaf"]["T"], "grid_leaf_agree": p["grid_leaf"]["agree"]}
                      if "grid_leaf" in p else {}))
        rows.append(row)
    ok = sorted((r for r in rows if r.get("T") is not None), key=lambda r: (r["T"], r["id"]))
    for i, r in enumerate(ok, start=1):
        r["rank"] = i
    bad = [r for r in rows if r.get("T") is None]
    ref = fr.point(lib.reference_policies()[mode], fr.HERO_N, delta, with_qubits=False)
    kmy = fr.planned("kmy", mode, fr.HERO_N, delta)[1]
    bench = [{"key": "reference", "label": "STO reference policy", "T": ref["T"], "ladder_str": ref["ladder_str"],
              "note": "benchmark: STO's fixed reference policy, not a submission; not ranked"},
             {"key": "kmy", "label": "KMY benchmark (Kahanamoku-Meyer and Yao tables)", "T": kmy["T"],
              "ladder_str": kmy["ladder_str"], "note": "benchmark: the best policy over the KMY tables at this delta; not ranked"}]
    counts = {l: sum(1 for r in rows if r["policy_trust"]["level"] == l) for l in trust.POLICY_LEVELS}
    name = "PhaseProduct" if mode == tt.PAIR else "PhaseTripleProduct"
    note = (f"{len(ok)} policy records seeded from planner outputs, priced live at n = 2048 (delta = {delta:g}): "
            f"{counts['forshor']} ForShor policy-verified, {counts['sto-mirror']} policy Lean-verified at exact widths (STO mirror / PTP suite) "
            f"only, {counts['tables']} tables only." if rows else f"No {name} policy record yet.")
    return {"mode": mode, "n": fr.HERO_N, "delta": delta, "policies": ok + bad, "count": len(ok),
            "policy_levels": counts, "benchmarks": bench, "rule": RULE.format(mode=name), "note": note,
            "policy_trust_text": trust.explain_policy(), "policy_trust_tags": trust.POLICY_TAGS}


# ---------------------------------------------------------------------------------------------- full-run board
def record_trust(r: Dict[str, Any]) -> Dict[str, Any]:
    """Trust and provenance of a full-run board row. A seeded record: `summary` (its pinned ForShor and STO-mirror runs).
    A record written by the ForShor harness: ForShor policy-verified if `lean_verified`, else tables only."""
    if r.get("seeded_from_planner") and r.get("provenance"):
        return summary(r)
    lv = "forshor" if r.get("lean_verified") is True else "tables"
    return {"seeded_from_planner": False, "policy_trust": {"level": lv, "tag": trust.POLICY_TAGS[lv],
                                                           "kind": trust.POLICY_KIND[lv], "text": trust.explain_policy()[lv]}}


def full_benchmarks(delta: float) -> List[Dict[str, Any]]:
    """The non-competing rows of the full-run board: the STO reference and the KMY policy priced live with the same
    pricer, and the paper's printed figure (not a count of the circuit Lean compiles; not comparable)."""
    from . import full_frontier as ff
    from . import fullalg as fa
    ref = fa.price_full(lib.reference_policies()[tt.PAIR], fa.HERO_N, delta, detail=False)
    kmy = fa.price_full(fr.planned("kmy", tt.PAIR, fa.HERO_N, delta, "scan")[0], fa.HERO_N, delta, detail=False)
    return [{"key": "reference", "label": "STO reference policy", "T": ref["T"], "qubits": ref["qubits"],
             "ladder_str": ref["ladder_str"], "note": "benchmark: STO's fixed reference policy, priced live; not ranked"},
            {"key": "kmy", "label": "KMY benchmark (Kahanamoku-Meyer and Yao tables)", "T": kmy["T"], "qubits": kmy["qubits"],
             "ladder_str": kmy["ladder_str"],
             "note": "benchmark: the best PhaseProduct policy over the KMY tables, priced live; not ranked"},
            {"key": "paper", "label": "The paper's printed figure", "T": ff.PAPER_PRINTED_T_2048, "qubits": None,
             "ladder_str": None,
             "note": "printed in the paper (retired paper model: R2 widths, no Step 4); not a count of the circuit Lean "
                     "compiles and not comparable with the rows; not ranked"}]


def full_board(delta: float) -> Dict[str, Any]:
    """`/api/full/submitted`: `full_frontier.submitted_board` (every PP record priced live with `fullalg`), each row with
    its policy trust level, table trust and provenance, plus the benchmark rows (apart, unranked)."""
    from . import full_frontier as ff
    delta = fr.check_delta(delta)
    b = ff.submitted_board(delta)
    raw = {r["id"]: r for r in ff._records()}
    for row in b["policies"]:
        r = raw.get(row["id"], row)
        for k in ("provenance", "trust", "lean", "sto_mirror"):
            row.pop(k, None)
        row.update(record_trust(r))
    ok = [r for r in b["policies"] if r.get("single_run_T") is not None]
    levels = {l: sum(1 for r in ok if r["policy_trust"]["level"] == l) for l in trust.POLICY_LEVELS}
    seeded = sum(1 for r in ok if r.get("seeded_from_planner"))
    if ok:
        b["note"] = (f"{len(ok)} policy records ({seeded} seeded from planner outputs), priced live at n = 2048 "
                     f"(delta = {delta:g}) with the site's full-run pricer: {levels['forshor']} ForShor policy-verified, "
                     f"{levels['sto-mirror']} policy Lean-verified at exact widths (STO mirror / PTP suite) only, {levels['tables']} tables only.")
    b.update(policy_levels=levels, seeded=seeded, benchmarks=full_benchmarks(delta),
             policy_trust_text=trust.explain_policy(), policy_trust_tags=trust.POLICY_TAGS)
    return b
