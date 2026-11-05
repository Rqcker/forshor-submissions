"""The numbers the site shows: the T-vs-n frontier per milestone, the n = 2048 hero numbers, the leaderboard.

Conventions (PLAN_v2 Stage 5, Stage 17): PP digital-product leaf, PTP digital triple-product leaf (the PTP grid leaf
is a labelled cross-check column), And = 4 T, eps = 0.5 delta / R, Ross-Selinger mean unrounded. One point per n:
the cheapest policy valid at that n. A library milestone is re-planned (tcost planner) at every n of the grid;
the planned policy is valid on its closed-form window [lo, hi] (which contains n). A fixed policy (the STO
reference, or a pasted one) has one closed-form window around its design width and gets no point outside it.
Every point is also counted by engine 2 (crosscheck.py); `agree` records the result.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from . import DEFAULT_DELTA, SITE_MODEL_VERSION, TRUST_TAGS
from . import crosscheck as xc
from . import library as lib
from . import trust
from tcost import planner as tp
from tcost import score as ts
from tcost import tables as tt

HERO_N = 2048
#: Four points per octave from 2^8 to 2^13 (includes 2048 exactly).
N_GRID: Tuple[int, ...] = tuple(sorted({int(round(2 ** (8 + i / 4))) for i in range(21)} | {HERO_N}))
WINDOW_SPAN = 4


def check_delta(delta: float) -> float:
    d = float(delta)
    if not (math.isfinite(d) and 1e-15 <= d < 1):
        raise ValueError(f"delta must be in [1e-15, 1); got {delta!r}")
    return d


def _ladder(P: tt.Policy, n: int) -> List[Dict[str, Any]]:
    origin = lib.table_origin()
    chain = tt.chain_for(P, n)
    rows, w = [], n
    for T in chain:
        rows.append({"width": w, "k": T.k, "table": origin.get(T.digest, "pasted"), "digest": T.digest[:12]})
        w = T.child_width(w, P.widths)
    rows.append({"width": w, "k": None, "table": "leaf", "digest": None})
    return rows


def ladder_str(ladder: List[Dict[str, Any]]) -> str:
    return " -> ".join(f"{r['width']}:{r['k'] if r['k'] else 'leaf'}" for r in ladder)


#: The chart tabs. Every tab plots the SAME policies (the T-optimal policy at each n); only the y value changes.
METRICS = ("T", "qubits", "cnot", "rotations", "toffoli")

import threading as _threading  # noqa: E402
_STACK_LOCK = _threading.Lock()


@lru_cache(maxsize=8192)
def qubits(P: tt.Policy, n: int) -> int:
    """Peak live logical qubits of the circuit P describes at n: Qualtran `QubitCount` on tcost's concrete
    `PolicyPhaseProduct` (operand registers, limb extensions to the child width, adder and And ancillas).
    Independent of delta. Counted by engine 1 only; engine 2 has no qubit model."""
    import sys
    import threading
    from qualtran.resource_counting import QubitCount, get_cost_value
    from tcost.bloqs import PolicyPhaseProduct
    # Qualtran walks the decomposition of Add(QInt(W)) recursively, about one frame per bit. Run it on a thread
    # with a large stack: server worker threads (uvicorn's pool; 512 KB by default on macOS) would overflow.
    if sys.getrecursionlimit() < 50_000:
        sys.setrecursionlimit(50_000)
    box: Dict[str, Any] = {}

    def run():
        try:
            box["q"] = int(get_cost_value(PolicyPhaseProduct(P, n), QubitCount()))
        except BaseException as e:  # noqa: BLE001  (re-raised in the caller)
            box["e"] = e

    with _STACK_LOCK:
        old = threading.stack_size(512 * 1024 * 1024)
        try:
            t = threading.Thread(target=run, name="qubitcount")
            t.start()
        finally:
            threading.stack_size(old)
    t.join()
    if "e" in box:
        raise box["e"]
    return box["q"]


#: Stage 13: the PP headline under exact widths. The formula is Lean's (2w + Rx + Rz, R(w) = immediateNeed + k R(W'),
#: Policy.reserveNeed); Lean proves that layout at the SCANNED child widths only, so at exact widths it is a formula,
#: not a Lean-proved layout. The Lean-proved count of the same policy at scanned widths is the labelled cross-check.
QUBITS_LABEL_PP = "static-layout formula at exact widths (not Lean-proved)"
QUBITS_RULE_PP = ("static layout 2w + Rx + Rz evaluated at the exact child widths (Lean's Policy.reserveNeed recurrence, "
                  "PolicyWorkspace.lean); Lean proves this layout at the scanned widths only, so at exact widths it is a "
                  "formula, not a Lean-proved layout. Cross-checks: the Lean-proved static layout of the same policy at "
                  "ForShor scanned widths (qubits_lean_static_scan) and Qualtran's QubitCount peak (qubits_qualtran_peak)")
QUBITS_RULE_PP_SCAN = ("Lean static layout 2w + Rx + Rz (Policy.reserveNeed, PolicyWorkspace.lean) at ForShor scanned "
                       "widths, the proved allocation")
QUBITS_RULE_PTP = ("Qualtran QubitCount peak (tcost decomposition, exact widths); Lean has no PhaseTripleProduct layout, so "
                   "there is no Lean count to show")


@lru_cache(maxsize=8192)
def static_reserve(P: tt.Policy, n: int) -> Dict[str, int]:
    """2n + Rx + Rz under the policy's width rule. Scan: Lean's static allocation (l2q_full `Policy.reserveNeed`, the
    proved layout). Exact: the same recurrence with the exact child widths (engine 2's `reserve_need`), a formula."""
    if P.widths == "scan":
        from . import fullalg as fa
        return dict(fa.pp_lean_static_qubits(P, n))
    rx, rz = xc.to_l2q(P).reserve_need(int(n), int(n))
    return {"w": int(n), "reserve_x": rx, "reserve_z": rz, "qubits": 2 * int(n) + rx + rz}


@lru_cache(maxsize=8192)
def lean_static_qubits(P: tt.Policy, n: int) -> int:
    """One phase product of width n: 2n + Rx + Rz under the policy's width rule (`static_reserve`). Lean-proved only
    for P.widths == "scan"."""
    return int(static_reserve(P, n)["qubits"])


def point(P: tt.Policy, n: int, delta: float, check: bool = True, with_qubits: bool = True,
          known_qubits: Optional[int] = None) -> Dict[str, Any]:
    """Score policy P at n with engine 1, its closed-form window, the engine-2 cross-check, and the gate metrics
    the chart tabs plot (qubits, CNOTs, rotations, Toffolis)."""
    s = ts.score_policy(P, n, delta, with_range=True)
    g = s["gate_counts"]
    lad = _ladder(P, n)
    ids = list(dict.fromkeys(r["table"] for r in lad if r["k"]))
    out = {"n": n, "widths": P.widths, "T": s["t_count"], "ladder": lad, "ladder_str": ladder_str(lad), "label": " + ".join(ids) or "leaf",
           "window": s["closed_form"]["valid_n"], "rotations": g["rotation"],
           "ts_per_rotation": s["t_breakdown"]["ts_per_rotation"], "and": g["and"],
           "t_from_and": s["t_breakdown"]["t_from_toffoli_class"], "eps": s["t_breakdown"]["rotation_eps"],
           # Toffoli-class gates: Qualtran reports And separately (Toffoli = 0 here); an And is a Toffoli onto |0>.
           "toffoli": g["and"] + g["toffoli"] + g["cswap"], "cnot": g["cnot"], "clifford": g["clifford"],
           "measurement": g["measurement"]}
    if with_qubits:
        # Stage 12 (decision B): the board's rule. PhaseProduct: the headline is Lean's proved static allocation
        # 2w + Rx + Rz (Policy.reserveNeed); Qualtran's QubitCount peak is a labelled cross-check. PhaseTripleProduct:
        # Lean has no layout, so Qualtran's peak is the only count, labelled as such.
        qq = known_qubits if known_qubits is not None else qubits(P, n)
        out["qubits_qualtran_peak"] = qq
        if P.mode == tt.PAIR:
            out["qubits"] = lean_static_qubits(P, n)
            out["qubits_rule"] = QUBITS_RULE_PP if P.widths == "exact" else QUBITS_RULE_PP_SCAN
            if P.widths == "exact":
                out["qubits_label"] = QUBITS_LABEL_PP
                out["qubits_lean_static_scan"] = lean_static_qubits(P.with_widths("scan"), n)
        else:
            out["qubits"] = qq
            out["qubits_rule"] = QUBITS_RULE_PTP
    if check:
        cx = xc.cross_check(P, n, delta, tcost_score=s)
        out["agree"] = cx["agree"]
        out["engine2_T"] = cx["engine2"]["T"]
        # engine 2 prices the non-rotation part as 4 T per Toffoli-class gate (+ raw T, which is 0 here)
        out["checks"] = {"T": cx["checks"]["T"], "rotations": cx["checks"]["rotations"],
                         "toffoli": g["t"] == 0 and 4 * out["toffoli"] == cx["engine2"]["t_base"],
                         "cnot": None, "qubits": None}
    if P.mode == tt.TRIPLE:
        out["grid_leaf"] = grid_leaf_cross_check(P, n, delta)
    return out


#: Stage 17 (2026-10-28): the PhaseTripleProduct board's leaf is the digital triple product; the grid leaf it replaced
#: is a labelled cross-check column, never the score.
GRID_LEAF_LABEL = "grid leaf"
GRID_LEAF_TEXT = ("The same policy (same bands, same exact widths) with the schoolbook grid leaf the PhaseTripleProduct "
                  "board used before 2026-10-28: per (x, y) bit pair one shared And, then per (x, y, z) bit triple And + one "
                  "rotation + And^dagger. A labelled cross-check, never the score; the policy is not re-planned for it.")


def grid_leaf_cross_check(P: tt.Policy, n: int, delta: float) -> Dict[str, Any]:
    """PhaseTripleProduct only: the policy priced with the grid leaf by both engines (tcost `TripleLeaf`, engine 2
    `leaf="grid"`)."""
    from forshor_qualtran import cost_conventions as cc
    s = ts.score_policy(P, n, delta, leaf="grid")
    spec = cc.SITE.with_(delta=delta)
    c = cc.tree_cost(xc.to_l2q(P), n, n, spec, leaf="grid", mode=P.mode)
    g = s["gate_counts"]
    return {"label": GRID_LEAF_LABEL, "text": GRID_LEAF_TEXT, "T": s["t_count"], "rotations": g["rotation"],
            "and": g["and"], "toffoli": g["and"] + g["toffoli"] + g["cswap"], "engine2_T": spec.price(c),
            "agree": spec.price(c) == s["t_count"] and c.rot == g["rotation"]}


@lru_cache(maxsize=4096)
def planned(key: str, mode: str, n: int, delta: float, widths: str = "exact") -> Tuple[tt.Policy, Dict[str, Any]]:
    tabs = lib.milestone_tables(key, mode)
    P, _ = tp.optimal_policy(tabs, mode, n, delta, name=f"{key}@{n}", widths=widths)
    return P, point(P, n, delta)


def fixed_points(P: tt.Policy, delta: float, design_n: int = HERO_N) -> Dict[str, Any]:
    """Points of a fixed policy: the grid widths inside its closed-form window at design_n (plus design_n)."""
    lo, hi = ts.valid_range(P, design_n, WINDOW_SPAN)
    ns = sorted({n for n in N_GRID if lo <= n <= hi} | {design_n})
    return {"window": [lo, hi], "points": [point(P, n, delta) for n in ns]}


@lru_cache(maxsize=64)
def reference_points(mode: str, delta: float, widths: str = "exact") -> Dict[str, Any]:
    return fixed_points(lib.reference_policies()[mode].with_widths(widths), delta)


def curve(mode: str, delta: float = DEFAULT_DELTA) -> Dict[str, Any]:
    delta = check_delta(delta)
    out: Dict[str, Any] = {"mode": mode, "delta": delta, "grid": list(N_GRID), "milestones": {}}
    ref = reference_points(mode, delta)
    out["milestones"]["reference"] = {"points": ref["points"], "window": ref["window"], "fixed": True}
    for key, _, _ in lib.PLANNED:
        out["milestones"][key] = {"points": [planned(key, mode, n, delta)[1] for n in N_GRID], "fixed": False}
    out["hero"] = hero(mode, delta)
    return out


def hero(mode: str, delta: float = DEFAULT_DELTA) -> Dict[str, Any]:
    delta = check_delta(delta)
    ref = point(lib.reference_policies()[mode], HERO_N, delta)
    kmy = planned("kmy", mode, HERO_N, delta)[1]
    rows = []
    for key, label, _ in lib.MILESTONES:
        p = ref if key == "reference" else planned(key, mode, HERO_N, delta)[1]
        rows.append({"key": key, "T": p["T"], "ladder_str": p["ladder_str"],
                     "label": "STO reference" if key == "reference" else p["label"], "agree": p["agree"],
                     "below_reference_pct": 100.0 * (ref["T"] - p["T"]) / ref["T"],
                     "below_kmy_pct": 100.0 * (kmy["T"] - p["T"]) / kmy["T"], "delta_kmy": p["T"] - kmy["T"]})
    return {"n": HERO_N, "delta": delta, "milestones": rows}


@lru_cache(maxsize=64)
def leaderboard(mode: str, delta: float = DEFAULT_DELTA, widths: str = "exact") -> Dict[str, Any]:
    """Chronological frontier advances at n = 2048. Row i: the optimal policy over every STO table submitted up
    to and including submission i (tables of this mode only); the move is the drop against the best before it.
    The starting point is the leaf alone (no table)."""
    delta = check_delta(delta)
    P0, _ = tp.optimal_policy([], mode, HERO_N, delta, name="schoolbook", widths=widths)
    prev_start = None
    prev = point(P0, HERO_N, delta, with_qubits=False)
    prev_start = prev["T"]
    rows = []
    subs = lib.submissions()
    for i, s in enumerate(subs, start=1):
        if not any(T.mode == mode for T in s.tables.values()):
            continue
        tabs = [T for T in lib.sto_tables(upto=i) if T.mode == mode]
        P, _ = tp.optimal_policy(tabs, mode, HERO_N, delta, name=f"upto:{s.id}", widths=widths)
        p = point(P, HERO_N, delta, with_qubits=False)
        move = prev["T"] - p["T"]
        rows.append({"id": s.id, "sha": s.sha, "accepted": s.accepted, "targets": s.targets(), "mode": mode,
                     "created_at": s.created_at,
                     "ladder": p["ladder"], "ladder_str": p["ladder_str"], "label": p["label"], "T": p["T"],
                     "move": move, "move_pct": 100.0 * move / prev["T"] if prev["T"] else 0.0,
                     **({"grid_leaf_T": p["grid_leaf"]["T"]} if "grid_leaf" in p else {}),
                     "advance": move > 0, "agree": p["agree"], "pr": s.pr, "ref": s.ref, "harness": s.harness})
        if move > 0:
            prev = p
    return {"mode": mode, "n": HERO_N, "delta": delta, "widths": widths,
            "start": {"label": "leaf only (no table)", "T": prev_start}, "rows": rows}


def meta() -> Dict[str, Any]:
    from sto_currency import CONVENTIONS, C_SYN_CERTIFIED, TS_PER_TOFFOLI
    return {"model": SITE_MODEL_VERSION, "conventions": CONVENTIONS, "ts_per_and": TS_PER_TOFFOLI,
            "ross_selinger": list(C_SYN_CERTIFIED), "default_delta": DEFAULT_DELTA, "hero_n": HERO_N,
            "grid": list(N_GRID), "trust_tags": list(TRUST_TAGS), "metrics": list(METRICS), "cache_schema": 3,
            "milestones": [{"key": k, "label": l, "detail": d} for k, l, d in lib.MILESTONES],
            "table_trust": trust.id_levels(), "trust_text": trust.explain(),
            # Stage 13: the PP and PTP boards price at exact widths; the full order-finding run keeps the scan
            "widths": {"boards": "exact", "full_run": "scan", "qubits_label_pp": QUBITS_LABEL_PP,
                       "rule": ("exact: the least width at which every reachable intermediate register value and every "
                                "child operand fits, over the floor-limb box (ShorTableOptimization exact_width / PTP "
                                "suite ptp_tight_width, Lean Tight.lean / PTP Exact.lean); a declared width ladder is "
                                "ignored; scanned widths are a labelled cross-check")},
            # display names: bnchmk_sub_no<N>, N = place in date order (PLAN_v2 8)
            "submission_numbers": {s.id: i for i, s in enumerate(lib.submissions(), start=1)}}


def policies(delta: float = DEFAULT_DELTA) -> Dict[str, Any]:
    """Every policy and table set the site knows, with T at 2048 and valid-n windows."""
    delta = check_delta(delta)
    refs = []
    for mode, P in lib.reference_policies().items():
        p = point(P, HERO_N, delta)
        refs.append({"mode": mode, "T": p["T"], "ladder_str": p["ladder_str"], "window": p["window"], "agree": p["agree"]})
    subs = [{"id": s.id, "sha": s.sha, "created_at": s.created_at, "accepted": s.accepted, "targets": s.targets(),
             "pr": s.pr, "ref": s.ref, "tables": len(s.tables), "harness": s.harness} for s in lib.submissions()]
    dp = lib.personal_tables()
    status: Dict[str, int] = {}
    for r in dp:
        status[r["status"]] = status.get(r["status"], 0) + 1
    planned_at = []
    from . import precompute as pc                   # the cached frontier when valid (planning PTP afresh takes minutes)
    cached = {mode: pc.load(mode, delta) for mode in lib.MODES}
    for key, label, _ in lib.PLANNED:
        for mode in lib.MODES:
            c = cached.get(mode)
            p = (next(q for q in c["milestones"][key]["points"] if q["n"] == HERO_N) if c
                 else planned(key, mode, HERO_N, delta)[1])
            planned_at.append({"key": key, "label": label, "mode": mode, "T": p["T"], "ladder_str": p["ladder_str"],
                               "window": p["window"], "tables": p["label"], "agree": p["agree"]})
    return {"reference": refs, "archive": subs, "accepted_count": sum(s["accepted"] for s in subs),
            "dp_library": {"tables": len(dp), "status": status, "used": len(lib.dp_tables())},
            "milestone_policies": planned_at, "delta": delta}
