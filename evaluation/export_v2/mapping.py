"""Engine output -> contract-v2 documents. The Python twin of forshor-site's `scripts/fixture-v2.ts`: that file builds the
fixture from the same raw engine views, and `pytests/test_mapping.py` checks this module reproduces its panels exactly."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

Raw = Dict[str, Any]
BOARDS = ("full", "pp", "ptp")
DELTA_KEYS = ("1e-2", "3e-4", "1e-4", "1e-6")
DELTA_VALUES = {"1e-2": 0.01, "3e-4": 3e-4, "1e-4": 1e-4, "1e-6": 1e-6}


def ladder_of(label: str) -> List[Dict[str, Optional[int]]]:
    """'2048:8 -> 279:3 -> 35:leaf' -> [{width, k}], with k None for the base case."""
    out = []
    for part in label.split(" -> "):
        width, k = part.split(":")
        out.append({"width": int(width), "k": None if k == "leaf" else int(k)})
    return out


def _static_layout(s: Optional[Raw]) -> Optional[Raw]:
    return None if not s else {"w": s["w"], "reserveX": s["reserve_x"], "reserveZ": s["reserve_z"], "qubits": s["qubits"]}


def _full_check(c: Raw) -> Raw:
    return {"T": c["T"], "TBase": c["T_base"], "rotations": c["rotations"], "tPerRotation": c["T_per_rotation"],
            "eps": c["eps"], "label": c["label"]}


def _table_step(x: Raw) -> Raw:
    return {"W": x["W"], "k": x.get("k"), "table": x.get("table"), "digest": x.get("digest")}


# The full run's qubit count is the live-qubit peak, not the static layout. The same strings are in forshor-site's scripts/fixture-v2.ts
# (PEAK_LABEL, PEAK_RULE); the golden test holds them equal once golden.json has been regenerated from that builder.
#
# The text depends on ONE per-row fact, `leanPeak`: has Lean's kernel evaluated the peak for THIS policy (same method, same width rule as the
# epoch's)? It defaults to False, so every row keeps "Lean has not evaluated it on this policy" until a later step switches it for the rows
# the A13 manifest covers; that step passes `lean_peak=True` and edits no text.
PIN = "eb00c5adc72832469a5f223fbec6c8b75745d2dd"   # the ForShor merge commit of PR #79 (a later step fills it in)
_PEAK_LABEL_HEAD = "live-qubit peak of the balanced order-finding circuit (a port of ForShor's orderFindingQubitsU, "
_PEAK_RULE_HEAD = (
    "The registers (exponent, data with its carry bit, work, scratch) and the flag, 12,890 qubits at n = 2048, plus the largest number of "
    "extra qubits any step of the unconditionally balanced order-finding circuit needs: ForShor's Policy.orderFindingQubitsU "
    "(QubitCount/Statement.lean). A negation is the register's complement plus Gidney's one-clean-ancilla increment gate (ForShor's `oneQubit` "
    "negation method, the model's `negation: \"incrementer\"`), charged as ONE clean qubit with Lean's counts per w-bit negation: Toffoli "
    "12w - 28 (even w) or 10w - 18 (odd w), CNOT 17w - 22 or 17w - 5, X 5w - 3 or 5w + 1 (exact for w >= 7, upper bounds below), at ForShor "
    + PIN + ", so the Step 3 scratch no longer binds. Computed by a Python port that reproduces Lean's evaluation for ForShor's "
    "standard-table policies and Lean's kernel-checked anchor (13,449 qubits for constant k = 2 at scanned widths, 4096 exponent qubits); ")
_PEAK_RULE_TAIL = " It is not the static layout allocation, which is kept in layout.total."


def peak_label(lean_peak: bool = False) -> str:
    return _PEAK_LABEL_HEAD + ("Lean-evaluated on this policy)" if lean_peak else "not Lean-evaluated on this policy)")


def peak_rule(lean_peak: bool = False) -> str:
    return _PEAK_RULE_HEAD + ("Lean's kernel has evaluated this policy's peak, and the port equals it." if lean_peak
                              else "Lean has not evaluated it on this policy.") + _PEAK_RULE_TAIL


PEAK_LABEL = peak_label()   # the default (leanPeak = False) texts
PEAK_RULE = peak_rule()

# A PhaseTripleProduct panel's Q (view["qubits_label"] / view["qubits_rule"], the engine's wording) says "formula, not Lean-proved". For a
# board row whose Q EQUALS the kernel-checked ForShor peak (`leanPeak` = True; epoch.LEAN_PEAK["ptp"]) the wording changes, and only the wording.
PTP_LEAN_LABEL = "live-qubit peak (equals ForShor's kernel-checked upper bound ptp_peak_qubits_exact)"
_PTP_FORMULA_SENTENCE = "It is a formula, not a Lean-proved count."


def ptp_lean_rule(engine_rule: str, row_id: str) -> str:
    """The engine's PhaseTripleProduct rule with its 'formula, not Lean-proved' sentence replaced by the Lean statement for this board row."""
    if engine_rule.count(_PTP_FORMULA_SENTENCE) != 1:
        raise ValueError("the engine's PhaseTripleProduct qubit rule no longer has the sentence this wording replaces")
    return engine_rule.replace(_PTP_FORMULA_SENTENCE, (
        "For this policy the number equals ForShor's ptpPeakQubits = 3n + ptpWsTD + ptpCleanAncD at the exact widths (n = 2048), which the "
        f"kernel-checked theorem Peak.Board.Row{row_id}.peak_oneQubit_exact (ForShor {PIN}) states as an upper bound on the qubit count of a "
        "circuit following this policy: a bound the framework proves on an existential circuit, not a count of a circuit built here. No "
        "table of this policy negates, so ForShor's scratch method gives the same number."))


def full_run_of(view: Raw, lean_peak: bool = False) -> Raw:
    f = view["full"]
    cc = f["cross_checks"]
    return {
        "T": f["T"], "TBase": f["T_base"], "rotations": f["rotations"], "tPerRotation": f["T_per_rotation"], "eps": f["eps"],
        "toffoli": f["toffoli"], "clifford": f["clifford"], "cnot": f["cnot"],
        "qubits": f["qubits_peak"], "qubitsLayout": f["qubits_layout"], "peakExtra": f["peak_extra"],
        "qubitsLabel": peak_label(lean_peak), "qubitsRule": peak_rule(lean_peak),
        "ladder": ladder_of(f["ladder_str"]), "ladderLabel": f["ladder_str"], "label": f["label"], "mainLabel": f["main_label"],
        "layout": {
            "total": f["layout"]["total"], "flag": f["layout"]["flag"], "rule": f["layout"]["rule"],
            "registers": [{"register": r["register"], "active": r["active"], "reserve": r["reserve"], "reserveSetBy": r["reserve_set_by"]}
                          for r in f["layout"]["registers"]],
        },
        "parts": [{
            "part": p["part"], "site": p["site"], "xw": p.get("xw"), "zw": p.get("zw"), "count": p["count"], "T": p["T"],
            "rotations": p["rotations"], "leanStaticQubits": p.get("lean_static_qubits"), "reserve": p.get("reserve"),
            "share": p["share"],
            "balancedInto": ([{"site": b["site"], "xw": b["xw"], "zw": b["zw"], "count": b["count"]} for b in p["balanced_into"]]
                             if p.get("balanced_into") else None),
            "ladder": [_table_step(x) for x in p["ladder"]] if p.get("ladder") else None,
        } for p in f["parts"]],
        "crossChecks": {
            "leanGridLeaf": _full_check(cc["lean_grid_leaf"]),
            "literalLean": _full_check(cc["literal_lean"]),
            "forshorScanIr": {**_full_check(cc["forshor_scan_ir"]), "qubits": cc["forshor_scan_ir"]["qubits"],
                              "ladderLabel": cc["forshor_scan_ir"]["ladder_str"]},
        },
        "leanGridLeafT": f["lean_grid_leaf_T"], "literalLeanT": f["literal_lean_T"],
        "workWidth": f["work_width"], "widths": f["widths"], "mBits": f["m_bits"], "precisionRule": f["precision_rule"],
        "censusTrust": f["census_trust"], "trialCount": f["trial_count"], "endToEnd": f["end_to_end"],
        "usage": [{"W": str(u["W"]), "xw": u["xw"], "zw": u["zw"], "site": u["site"], "count": u["count"], "T": u["T"],
                   "share": u["share"], "ladderLabel": u["ladder_str"], "ladder": [_table_step(x) for x in u["ladder"]]}
                  for u in view["usage"]],
        "tablesUsed": [{"id": t["id"], "digest": t["digest"], "k": t["k"], "widths": t["widths"], "roots": t["roots"]}
                       for t in view["tables_used"]],
    }


def panel_of(view: Raw, *, epoch: str, row_id: str, board: str, delta: str, verification: Raw, lean_peak: bool = False) -> Raw:
    """One `forshor.policy/v2` document from an engine `policy_view` (plus `full`, `usage`, `tables_used` for the full board).
    `lean_peak`: Lean's kernel has evaluated this policy's qubit peak (per row; False until a manifest says so); only the qubit label and
    rule depend on it: the full run's (PhaseProduct) and, on the PhaseTripleProduct board, the panel's own."""
    qubits_label, qubits_rule = view["qubits_label"], view["qubits_rule"]
    if lean_peak and board == "ptp":
        qubits_label, qubits_rule = PTP_LEAN_LABEL, ptp_lean_rule(qubits_rule, row_id)
    sc, gl = view.get("scan_cross_check"), view.get("grid_leaf_cross_check")
    tb = view["t_breakdown"]
    return {
        "schema": "forshor.policy/v2", "epoch": epoch, "rowId": row_id, "board": board, "delta": delta,
        "title": view["title"], "T": view["T"], "ladder": ladder_of(view["ladder_str"]), "ladderLabel": view["ladder_str"],
        "depth": view["depth"], "leafWidth": view["leaf_width"], "leafCalls": view["leaf_calls"], "leafShare": view["leaf_share"],
        "widthRule": view["width_rule"], "widths": view["widths"], "window": view["window"], "agree": view["agree"],
        "engine2T": view["engine2_T"], "qubits": view["qubits"], "qubitsLabel": qubits_label,
        "qubitsRule": qubits_rule, "qubitsStatic": _static_layout(view.get("qubits_static")),
        "qubitsLeanStatic": _static_layout(view.get("qubits_lean_static")),
        "qubitsQualtranPeak": view.get("qubits_qualtran_peak"),
        "levels": [{
            "level": l["level"], "width": l["width"], "k": l.get("k"), "table": l["table"], "digest": l.get("digest"),
            "childWidth": l.get("child_width"), "children": l.get("children"), "nodes": l["nodes"], "adders": l.get("adders"),
            "rotations": l["rotations"], "T": l["T"],
        } for l in view["levels"]],
        "bands": [{"threshold": b["threshold"], "k": b["k"], "table": b["table"], "digest": b["digest"], "usedAtN": b["used_at_n"]}
                  for b in view["bands"]],
        "tables": [{
            "k": t["k"], "digest": t["digest"], "points": t["points"], "operations": t["operations"], "pointCount": t["point_count"],
            "census": t["census"], "weightedCost": t["weighted_cost"],
            "source": {"id": t["source"]["id"], "kind": t["source"]["kind"], "status": t["source"]["status"],
                       "pr": t["source"].get("pr"), "createdAt": t["source"].get("created_at")},
            "checkLevel": t["level"], "checkers": t["checkers"], "checkedBy": t["checked_by"],
        } for t in view["tables"]],
        "gateCounts": view["gate_counts"],
        "tBreakdown": {"tCount": tb["t_count"], "tFromToffoliClass": tb["t_from_toffoli_class"], "tFromRotations": tb["t_from_rotations"],
                       "tsPerRotation": tb["ts_per_rotation"], "rotationEps": tb["rotation_eps"], "delta": tb["delta"]},
        "tableTrust": view["trust"],
        "scanCrossCheck": None if not sc else {
            "label": sc["label"], "text": sc["text"], "T": sc["T"], "rotations": sc["rotations"], "ladderLabel": sc["ladder_str"],
            "agree": sc["agree"], "engine2T": sc["engine2_T"], "qubitsLeanStatic": sc.get("qubits_lean_static"),
            "qubitsRule": sc.get("qubits_rule")},
        "gridLeafCrossCheck": None if not gl else {
            "label": gl["label"], "text": gl["text"], "T": gl["T"], "rotations": gl["rotations"], "and": gl["and"],
            "toffoli": gl["toffoli"], "engine2T": gl["engine2_T"], "agree": gl["agree"]},
        "policyJson": {
            "name": view["policy_json"]["name"], "mode": view["policy_json"]["mode"],
            "bands": [{"threshold": b["threshold"], "mode": b["mode"], "k": b["k"], "pointOrder": b["point_order"],
                       "operations": b["operations"]} for b in view["policy_json"]["bands"]],
        },
        "verification": verification,
        "costCheck": {"claim": "counted-in-python-not-proved-in-lean", "engines": ["tcost", "forshor_qualtran"], "agree": bool(view["agree"])},
        "fullRun": full_run_of(view, lean_peak) if board == "full" else None,
    }


def trust_of(verification: Raw) -> Raw:
    """The row's trust summary, derived from (and equal to) the evidence."""
    fp = verification.get("forshorPolicy")
    return {"stoMirror": "accepted" if verification.get("stoMirror") else "none",
            "forshorPolicy": "none" if not fp else ("verified" if fp["verified"] else "not-verified"),
            "cost": "python-not-proved-in-lean"}


def row_of(panel: Raw, *, origin: str, name: str, created_at: str, pull_request_url: Optional[str], submission_id: Optional[str],
           detail: str) -> Raw:
    """A ledger row (no rank): the numbers a board shows, taken from the panel they came from."""
    full = panel["fullRun"]
    label = full["ladderLabel"] if full else panel["ladderLabel"]
    return {
        "rowId": panel["rowId"], "origin": origin, "name": name, "T": full["T"] if full else panel["T"],
        "qubits": full["qubits"] if full else panel["qubits"], "ladder": ladder_of(label), "ladderLabel": label,
        "createdAt": created_at, "pullRequestUrl": pull_request_url, "submissionId": submission_id,
        "trust": trust_of(panel["verification"]), "detail": detail,
    }


def panel_path(epoch: str, row_id: str, board: str, delta: str) -> str:
    return f"/results/v2/e/{epoch}/policies/{row_id}/{board}-{delta}.json"


def chart_path(epoch: str, board: str, delta: str) -> str:
    return f"/results/v2/e/{epoch}/library/chart/{board}-{delta}.json"


def chart_of(series: Raw, *, epoch: str, board: str, delta: str) -> Raw:
    """The reference library for one board and delta, cut to what a chart draws."""
    return {"schema": "forshor.chart/v2", "epoch": epoch, "board": board, "delta": delta, "series": {
        name: [{"n": p["n"], "T": p["T"], "ladderLabel": p.get("ladder_str")} for p in series.get(name, [])]
        for name in ("reference", "kmy", "champions", "all_sto")}}


def benchmarks_of(series: Raw, n: int = 2048) -> List[Raw]:
    out = []
    for key, label, note in (("kmy", "KMY benchmark", "The explicit tables of Kahanamoku-Meyer and Yao, priced by the same engine."),
                             ("reference", "STO reference policy", "ShorTableOptimization's reference policy, priced by the same engine.")):
        p = next((x for x in series.get(key, []) if x["n"] == n), None)
        if p:
            out.append({"id": key, "label": label, "T": p["T"], "ladderLabel": p.get("ladder_str"), "note": note})
    return out
