"""The full policy behind any number on the site: bands, the recursion level by level, one table per k (points
and operations), where T goes, and a submittable JSON that round-trips through intake."""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import DEFAULT_DELTA
from . import crosscheck as xc
from . import frontier as fr
from . import library as lib
from . import trust
from forshor_qualtran import cost_conventions as cc
from tcost import score as ts
from tcost import tables as tt


def _source(digest: str) -> Dict[str, Any]:
    sid = lib.table_origin().get(digest)
    if sid is None:
        return {"id": "pasted", "kind": "pasted", "status": "not verified"}
    for s in lib.submissions():
        if s.id == sid:
            if s.harness:
                return {"id": sid, "kind": "STO submission (site harness)", "status": "Lean-verified (STO verifier) and "
                        "replayed in Python", "pr": None, "created_at": s.created_at}
            return {"id": sid, "kind": "STO archive", "status": "accepted (Lean-extracted)" if s.accepted else "archived",
                    "pr": s.pr, "created_at": s.created_at}
    for r in lib.personal_tables():
        if r["id"] == sid:
            return {"id": sid, "kind": "DP library", "status": r["status"]}
    for r in lib.kmy_tables():
        if r["id"] == sid:
            from . import submitted
            by = [s for s, ds in submitted.seed_forshor_verified().items() if digest in ds]
            st = "replay-checked transcription" + (f"; Lean-verified (C1 to C4) by the ForShor policy run of {', '.join(by)}"
                                                   if by else "")
            return {"id": sid, "kind": "KMY benchmark", "status": st, "pr": None}
    return {"id": sid, "kind": "unknown", "status": ""}


def _weighted(T: tt.Table) -> Optional[int]:
    for s in lib.submissions():
        if T.digest in s.tables:
            w = s.weighted.get((T.mode, T.k))
            if w is not None:
                return w
    for r in lib.personal_tables():
        if r["table"].digest == T.digest:
            return r["weighted"]
    return None


def trust_view(digests) -> Dict[str, Any]:
    """Trust of a set of tables: the weakest level, the counts, and the tag the page shows."""
    agg = trust.of_digests(digests)
    text, kind = trust.tag(agg)
    return {**agg, "tag": text, "kind": kind}


def table_view(T: tt.Table) -> Dict[str, Any]:
    j = T.to_json()
    return {"k": T.k, "digest": T.digest[:12], "points": j["point_order"], "operations": j["operations"],
            "point_count": T.q, "census": T.census(), "weighted_cost": _weighted(T), "source": _source(T.digest),
            "level": trust.level_of_digest(T.digest), "checkers": trust.checkers_of_digest(T.digest),
            "checked_by": trust.checker_text(T.digest)}


def levels(P: tt.Policy, n: int, delta: float) -> Dict[str, Any]:
    """Per level: width, table, child width, children per node, nodes, and the T this level costs
    (engine 2's per-node pricing; the rotation price uses the R of the whole tree)."""
    L = xc.to_l2q(P)
    spec = cc.SITE.with_(delta=delta)
    sides = cc.SIDES[P.mode]
    total = cc.tree_cost(L, n, n, spec, mode=P.mode)
    tpr = spec.t_per_rotation(total.rot) if total.rot else 0
    rows, w, nodes, k_tables = [], n, 1, {}
    for T in tt.chain_for(P, n):
        w2 = T.child_width(w, P.widths)
        lt = xc.to_l2q(tt.Policy.constant(T)).bands[0].table
        c = cc.level_cost(cc.census_key(lt), w2, spec, sides).scale(nodes)
        rows.append({"level": len(rows), "width": w, "k": T.k, "table": _source(T.digest)["id"], "digest": T.digest[:12],
                     "child_width": w2, "children": T.q, "nodes": nodes, "adders": nodes * sides * T.census()["addScaled"],
                     "rotations": c.rot, "T": c.base + c.rot * tpr})
        k_tables.setdefault(T.k, T)
        nodes *= T.q
        w = w2
    lc = cc.leaf_cost(w, w, cc.leaf_of(P.mode), wy=(w if P.mode == tt.TRIPLE else None)).scale(nodes)
    leaf_T = lc.base + lc.rot * tpr
    rows.append({"level": len(rows), "width": w, "k": None,
                 "table": ("product leaf" if P.mode == tt.PAIR else "digital triple-product leaf"), "digest": None,
                 "child_width": None, "children": None, "nodes": nodes, "adders": 0, "rotations": lc.rot, "T": leaf_T})
    T = spec.price(total)
    return {"rows": rows, "T": T, "ts_per_rotation": tpr, "tables_used": k_tables,
            "leaf_share": leaf_T / T if T else None}


def _lean_static(P: tt.Policy, n: int) -> Optional[Dict[str, int]]:
    """PhaseProduct only: Lean's static allocation of one phase product of width n, Q = 2n + Rx + Rz with
    (Rx, Rz) = Policy.reserveNeed (PhaseProduct/Compiler/PolicyWorkspace.lean), AT FORSHOR SCANNED WIDTHS (the only
    widths where Lean proves the layout)."""
    if P.mode != tt.PAIR:
        return None
    from . import fullalg as fa
    return fa.pp_lean_static_qubits(P.with_widths("scan"), n)


#: The labelled scanned-width cross-check in the policy panel (Stage 13).
SCAN_LABEL = "ForShor scanned widths"
SCAN_TEXT = ("The same policy priced at ForShor's scanned widths (STO nextSignedWidth, the rule the boards used before "
             "2026-10-28; the full order-finding run prices the ForShor drop-in's exact widths since Stage 18): a labelled "
             "cross-check, never the score.")
WIDTH_RULE_TEXT = ("Exact widths: at every node the child width is the least width at which every reachable intermediate "
                   "register value and every child operand fits, over the floor-limb box (no padding). PhaseProduct: "
                   "ShorTableOptimization's exact_width (Lean Tight.lean: the least overflow-free width, never above the "
                   "scan); PhaseTripleProduct: the PTP suite's ptp_tight_width (Lean PTP/Exact.lean). The board derives "
                   "these widths itself; a submitted width ladder is ignored. Python equals Lean is cross-checked "
                   "(34 policies, every seed's Lean run), not proved.")


def scan_cross_check(P: tt.Policy, n: int, delta: float) -> Dict[str, Any]:
    S = P.with_widths("scan")
    s = ts.score_policy(S, n, delta)
    cx = xc.cross_check(S, n, delta, tcost_score=s)
    out = {"label": SCAN_LABEL, "text": SCAN_TEXT, "T": s["t_count"], "rotations": s["gate_counts"]["rotation"],
           "ladder_str": fr.ladder_str(fr._ladder(S, n)), "agree": cx["agree"], "engine2_T": cx["engine2"]["T"]}
    if P.mode == tt.PAIR:
        out["qubits_lean_static"] = fr.lean_static_qubits(S, n)
        out["qubits_rule"] = fr.QUBITS_RULE_PP_SCAN
    return out


def view(P: tt.Policy, n: int = fr.HERO_N, delta: float = DEFAULT_DELTA, title: str = "") -> Dict[str, Any]:
    delta = fr.check_delta(delta)
    s = ts.score_policy(P, n, delta, with_range=True)
    lv = levels(P, n, delta)
    by_k: Dict[int, tt.Table] = {}
    for b in P.bands:
        by_k.setdefault(b.table.k, b.table)
    used = {T.digest for T in lv["tables_used"].values()}
    bands = [{"threshold": b.threshold, "k": b.table.k, "table": _source(b.table.digest)["id"],
              "digest": b.table.digest[:12], "used_at_n": b.table.digest in used} for b in P.bands]
    doc = P.to_json()
    doc["name"] = title or P.name or doc["name"]
    return {"trust": trust_view(b.table.digest for b in P.bands), "title": doc["name"], "mode": P.mode, "n": n, "delta": delta, "T": s["t_count"],
            "engine2_T": lv["T"], "agree": lv["T"] == s["t_count"],
            "window": s["closed_form"]["valid_n"], "ladder_str": fr.ladder_str(fr._ladder(P, n)),
            "depth": s["recursion_depth"], "leaf_width": s["leaf_width"], "leaf_calls": s["leaf_calls"],
            "gate_counts": s["gate_counts"], "t_breakdown": s["t_breakdown"],
            "widths": P.widths, "width_rule": WIDTH_RULE_TEXT if P.widths == "exact" else fr.QUBITS_RULE_PP_SCAN,
            "qubits": fr.lean_static_qubits(P, n) if P.mode == tt.PAIR else fr.qubits(P, n),
            "qubits_label": (fr.QUBITS_LABEL_PP if P.widths == "exact" else "Lean static")
            if P.mode == tt.PAIR else "Qualtran peak",
            "qubits_static": fr.static_reserve(P, n) if P.mode == tt.PAIR else None,
            "qubits_qualtran_peak": fr.qubits(P, n),
            "qubits_rule": (fr.QUBITS_RULE_PP if P.widths == "exact" else fr.QUBITS_RULE_PP_SCAN)
            if P.mode == tt.PAIR else fr.QUBITS_RULE_PTP,
            "qubits_lean_static": _lean_static(P, n),
            "scan_cross_check": scan_cross_check(P, n, delta) if P.widths == "exact" else None,
            "leaf_share": lv["leaf_share"],
            "grid_leaf_cross_check": fr.grid_leaf_cross_check(P, n, delta) if P.mode == tt.TRIPLE else None,
            "bands": bands, "levels": lv["rows"],
            "tables": [table_view(by_k[k]) for k in sorted(by_k, reverse=True)],
            "policy_json": doc}


def for_milestone(key: str, mode: str, n: int, delta: float, policy_json: Optional[dict] = None) -> Dict[str, Any]:
    label = dict((k, l) for k, l, _ in lib.MILESTONES)[key]
    if key == "reference":
        P = lib.reference_policies()[mode]
        return view(P, n, delta, f"STO reference {mode}")
    P = tt.Policy.from_json(policy_json) if policy_json else fr.planned(key, mode, n, delta)[0]
    return view(P, n, delta, f"{label} optimum, {mode}, planned at n = {n}, delta = {delta:g}")


def for_leaderboard_row(sid: str, mode: str, delta: float) -> Dict[str, Any]:
    from tcost import planner as tp
    subs = lib.submissions()
    i = next((i for i, s in enumerate(subs, start=1) if s.id == sid), None)
    if i is None:
        raise KeyError(sid)
    tabs = [T for T in lib.sto_tables(upto=i) if T.mode == mode]
    P, _ = tp.optimal_policy(tabs, mode, fr.HERO_N, delta)
    return view(P, fr.HERO_N, delta, f"STO frontier after {sid}, {mode}, n = 2048, delta = {delta:g}")


def with_full(v: Dict[str, Any], P: tt.Policy) -> Dict[str, Any]:
    """Add the full order-finding cost of the same policy at the same n (PhaseProduct only)."""
    if P.mode != tt.PAIR:
        return v
    from . import fullalg as fa
    v["full"] = fa.price_full(P, v["n"], v["delta"])
    v["full"]["tier"] = fa.TIER
    v["census"] = fa.census(v["n"])
    return v


def policy_for(key: Optional[str], mode: str, n: int, delta: float, policy_json: Optional[dict] = None,
               row: Optional[str] = None) -> tt.Policy:
    from tcost import planner as tp
    if row:
        subs = lib.submissions()
        i = next((i for i, s in enumerate(subs, start=1) if s.id == row), None)
        if i is None:
            raise KeyError(row)
        P, _ = tp.optimal_policy([T for T in lib.sto_tables(upto=i) if T.mode == mode], mode, fr.HERO_N, delta)
        return P
    if key == "reference":
        return lib.reference_policies()[mode]
    return tt.Policy.from_json(policy_json) if policy_json else fr.planned(key, mode, n, delta)[0]
