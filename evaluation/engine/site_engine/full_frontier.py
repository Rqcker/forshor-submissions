"""The full order-finding view's data (PLAN_v2 Stage 11): every number is the site's one full-run pricer, `fullalg`
(ForShor's native structure, L2Q `PolicyShorCore`, with the Qualtran digital-product leaf). The paper model
(`paper_full`) is retired from every headline and leaderboard number; it survives only to reproduce the paper's
printed figure (`paper.r2_T_2048`, labelled, not comparable with the rows).

    python -m site_engine.full_frontier [--delta 0.01 1e-4 1e-6] [--force]

Per library milestone and n, and per leaderboard row: the policy the PhaseProduct board planned (tables + width
thresholds), priced as written over the whole run. The STO reference is a fixed policy. Leaderboard: one row per
submission with PP tables (best policy over every STO table submitted up to it), ranked by lowest single-run T at
n = 2048. At n = 2048 every point also carries the run by part (Steps 1, 2, 4, 5, the QFTs, the template's own
arithmetic) and every distinct phase product of the run with its descent. Policy records
(`engine/data/forshor_submitted`) are priced LIVE (`submitted_board`): no stored price is trusted.

Cache: engine/cache/fullalg_<delta>.json (derived; fingerprinted).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional

from . import DATA, ENGINE, SITE_MODEL_VERSION
from . import frontier as fr
from . import fullalg as fa
from . import library as lib
from . import precompute as pc
from tcost import planner as tp
from tcost import tables as tt

FULL_SCHEMA = 7          # Stage 18: rows carry cross_checks (forshor_scan_ir); parts are template calls
PP = tt.PAIR
#: The figure the paper prints for its best custom policy at n = 2048 (main-latest.tex, Figure TCount caption).
PAPER_PRINTED_T_2048 = 2.31e12
RULE = "lowest single-run T at n = 2048 (full order finding; ForShor native structure, Qualtran digital-product leaf)"


@lru_cache(maxsize=1)
def fingerprint() -> str:
    h = hashlib.sha256(pc.data_fingerprint().encode())
    for p in sorted((ENGINE / "fullalg").rglob("*")) + sorted((ENGINE / "paper_model").rglob("*")):
        if p.is_file() and p.suffix in (".py", ".json", ".jsonl") and "__pycache__" not in p.parts:
            h.update(hashlib.sha256(p.read_bytes()).digest())
    for f in ("paper_full.py", "full_frontier.py", "fullalg.py"):
        h.update(hashlib.sha256((ENGINE / "site_engine" / f).read_bytes()).digest())
    return h.hexdigest()[:16]


def _path(delta: float):
    return pc.CACHE / f"fullalg_{delta:.0e}.json"


def _pp_curve(delta: float) -> Dict[str, Any]:
    """The PhaseProduct curve at EXACT widths, the one the PhaseProduct board shows (Stage 19). The full run prices the
    same recalculated policies (tables and thresholds) as that board, on the ForShor drop-in's IR (exact widths,
    balanced split); the scanned-width price stays each point's `cross_checks.forshor_scan_ir`. Before Stage 19 the
    full run kept the policies planned at scanned widths (Stage 13)."""
    c = pc.load(PP, delta)
    if c is None:
        c = pc.compute(PP, delta)
        pc.save(c)
    return c


def point(P: tt.Policy, n: int, delta: float) -> Dict[str, Any]:
    """One full-run point. The per-part detail is kept at the headline n only (the policy endpoint re-prices others)."""
    return fa.price_full(P, n, delta, detail=(int(n) == fa.HERO_N))


def _r2_paper_figure(delta: float) -> Optional[Dict[str, Any]]:
    """The paper's printed figure, reproduced in the paper model (R2 widths, version C, no Step 4). Labelled
    reproduction only; None when the paper-model worker cannot run."""
    from . import paper_full as pf
    try:
        r = pf.annotate(pf.run([pf.bundles_job(fa.HERO_N, delta, widths="r2")])[0])
    except Exception as e:  # pragma: no cover - the reproduction is optional
        return {"error": str(e)}
    return {"T": r["T"], "ladder": r["ladder_str"]}


def compute(delta: float, workers: Optional[int] = None) -> Dict[str, Any]:  # workers: unused, kept for callers
    delta = fr.check_delta(delta)
    t0 = time.time()
    ppc = _pp_curve(delta)
    grid = list(ppc["grid"])
    pp_pts = {k: {q["n"]: q for q in v["points"]} for k, v in ppc["milestones"].items()}
    ms: Dict[str, Any] = {k: {"fixed": k == "reference", "points": []} for k, _, _ in lib.MILESTONES}
    ms["reference"]["window"] = ppc["milestones"]["reference"]["window"]
    for key, _, _ in lib.PLANNED:
        for n in grid:
            P = tt.Policy.from_json(ppc["policies"][key][str(n)])
            ms[key]["points"].append(point(P, n, delta))
    ref_P = lib.reference_policies()[PP]
    for q in ppc["milestones"]["reference"]["points"]:
        ms["reference"]["points"].append(point(ref_P, q["n"], delta))
    for key, v in ms.items():
        for p in v["points"]:
            q = pp_pts.get(key, {}).get(p["n"])
            p["pp_T"] = q["T"] if q else None
            p["window"] = q["window"] if q else [p["n"], p["n"]]
            p["agree"] = q.get("agree") if q else None
        v["points"].sort(key=lambda q: q["n"])
    at = {k: next(q for q in v["points"] if q["n"] == fa.HERO_N) for k, v in ms.items()}
    kmy = at["kmy"]
    hero = []
    for key, label, _ in lib.MILESTONES:
        q = at[key]
        hero.append({"key": key, "T": q["T"], "qubits": q["qubits"], "lean_grid_leaf_T": q["lean_grid_leaf_T"],
                     "ladder_str": q["ladder_str"], "label": "STO reference" if key == "reference" else q["label"],
                     "agree": q.get("agree"), "below_kmy_pct": 100.0 * (kmy["T"] - q["T"]) / kmy["T"],
                     "pp_T": q["pp_T"]})
    subs = [(i, s) for i, s in enumerate(lib.submissions(), start=1) if any(T.mode == PP for T in s.tables.values())]
    by_id = {r["id"]: r for r in ppc["leaderboard"]["rows"]}
    chron, best = [], None
    for order, (i, s) in enumerate(subs):
        # the row's policy: the one the PhaseProduct board ranks (best over every table submitted up to this row)
        P, _ = tp.optimal_policy([T for T in lib.sto_tables(upto=i) if T.mode == PP], PP, fa.HERO_N, delta, name=f"upto:{s.id}")
        p = point(P, fa.HERO_N, delta)
        adv = best is None or p["T"] < best
        chron.append({"id": s.id, "sha": s.sha, "accepted": s.accepted, "harness": s.harness, "targets": s.targets(),
                      "pr": s.pr, "ref": s.ref, "created_at": s.created_at, "mode": PP, "order": order, "delta": delta,
                      "advance": adv, "pp_T": by_id[s.id]["T"],
                      **{k: p[k] for k in ("T", "qubits", "ladder_str", "label", "main_label", "tables_used", "ladder",
                                           "usage", "parts", "rotations", "T_base", "T_per_rotation", "toffoli",
                                           "clifford", "cnot", "lean_grid_leaf_T", "literal_lean_T", "layout",
                                           "work_width", "width_rule", "cross_checks")}})
        if adv:
            best = p["T"]
    ranked = sorted(chron, key=lambda r: (r["T"], r["order"]))
    for k, r in enumerate(ranked, start=1):
        r["rank"] = k
    shipped = fa.price_l2q(fa.native().policy, fa.HERO_N, delta, detail=False)
    r2 = _r2_paper_figure(delta)
    return {"model": SITE_MODEL_VERSION, "full_model": fa.MODEL, "schema": FULL_SCHEMA, "fingerprint": fingerprint(),
            "delta": delta, "mode": PP, "grid": grid, "tier": fa.TIER, "width_rule": fa.width_rule(), "census_trust": fa.CENSUS_TRUST,
            "milestones": ms, "series": {},
            "hero": {"n": fa.HERO_N, "delta": delta, "milestones": hero},
            "forshor_shipped": {"label": "ForShor's shipped policy (k = 3 from 40, k = 2 from 14)",
                                **{k: shipped[k] for k in ("T", "qubits", "rotations", "lean_grid_leaf_T",
                                                           "literal_lean_T", "ladder_str", "work_width", "width_rule")},
                                "scan_cross_check": shipped["cross_checks"]["forshor_scan_ir"]},
            "paper": {"r2_T_2048": (r2 or {}).get("T"), "r2_ladder": (r2 or {}).get("ladder"),
                      "printed_T_2048": PAPER_PRINTED_T_2048,
                      "note": "Reproduction of the paper's printed Figure TCount figure (2.31e12) in the retired paper "
                              "model: R2 widths, the cheapest table at every width (version C), no Step 4. Not a "
                              "count of the circuit Lean compiles and not comparable with the rows."},
            "leaderboard": {"n": fa.HERO_N, "delta": delta, "chronological": chron, "ranked": ranked, "rule": RULE},
            "seconds": round(time.time() - t0, 1)}


# ---------------------------------------------------------------------------------------------- policy records
def _pt(p: Dict[str, Any]) -> str:
    if p["kind"] == "int":
        return str(int(p["z"]))
    m = int(p["m"])
    return "inf" if m == 0 else f"{1 if m > 0 else -1}/{abs(m)}"


def record_policy(rec: Dict[str, Any]) -> tt.Policy:
    """A record's banded policy, from the tables it carries (tcost JSON `table`, or ForShor `points`/`ops`). A band
    whose stated digest does not match its table is an error."""
    bands = []
    for b in rec["bands"]:
        if b.get("table"):
            T = tt.Table.from_json(b["table"])
        else:
            T = tt.Table.from_json({"mode": PP, "k": int(b["k"]), "point_order": [_pt(p) for p in b["points"]],
                                    "operations": b["ops"]})
        if b.get("digest") and b["digest"] != T.digest:
            raise ValueError(f"band at {b.get('threshold')}: stated digest {b['digest'][:12]} != table {T.digest[:12]}")
        bands.append(tt.Band(int(b["threshold"]), T))
    return tt.Policy(mode=PP, bands=tuple(bands), name=rec.get("id"))


def _records() -> List[Dict[str, Any]]:
    from . import submitted as sb            # its DATA / KIND, so a test can point the board at another directory
    d = getattr(sb, "DATA", DATA) / getattr(sb, "KIND", "forshor_submitted")
    out = []
    for f in sorted(d.glob("*/record.json")) if d.is_dir() else []:
        r = json.loads(f.read_text())
        if r.get("mode", PP) == PP and r.get("bands"):
            out.append(_full_run_view(r, f.parent))
    return out


def _full_run_view(r: Dict[str, Any], d) -> Dict[str, Any]:
    """Stage 13: a seeded record re-planned under exact widths (for the PhaseProduct board) is priced HERE with the
    thresholds it had before (`site_policy.pre_exact.json`, the record's history): the full order-finding run keeps
    ForShor's scanned widths, and this board is unchanged by Stage 13. Its trust is that policy's (its ForShor run and its
    Stage 11 STO-mirror run, at scanned widths)."""
    h = (r.get("history") or [None])[0]
    pre = d / "site_policy.pre_exact.json"
    if not r.get("seeded_from_planner") or h is None or not pre.is_file():
        return r
    P = tt.load_policies(pre)[0]
    bands = [{"threshold": b.threshold, "k": b.table.k, "digest": b.table.digest, "table_id": lib.table_origin().get(b.table.digest),
              "table": b.table.to_json()} for b in P.bands]
    sm = h.get("sto_mirror", r.get("sto_mirror")) or {}
    flags = [f for f in r.get("flags", []) if f not in ("exact-widths-lean-verified", "sto-mirror-algebra-verified")] + \
        (["sto-mirror-algebra-verified"] if sm.get("status") == "accepted" else []) + ["full-run-prices-pre-exact-thresholds"]
    return {**r, "bands": bands, "lean": h.get("lean", r.get("lean")), "sto_mirror": sm, "flags": flags,
            "lean_verified": bool((h.get("lean") or {}).get("policy_verified")),
            "full_run_policy": "site_policy.pre_exact.json (the thresholds before the Stage 13 exact-width re-plan; the "
                               "full run plans at ForShor's scanned widths; since Stage 18 it prices them at the drop-in's exact widths)"}


def submitted_board(delta: float) -> Dict[str, Any]:
    """Every PhaseProduct policy record, priced LIVE with `fullalg` at n = 2048 (a stored price is never used: it is
    kept as `stored_single_run_T` for comparison), ranked by single-run T."""
    delta = fr.check_delta(delta)
    rows = []
    for r in _records():
        row = {k: v for k, v in r.items() if k not in ("bands", "single_run_T", "qubits", "rotations", "native", "model")}
        row["bands"] = [{k: v for k, v in b.items() if k != "table"} for b in r["bands"]]
        row["stored_single_run_T"] = r.get("single_run_T")
        try:
            p = fa.price_full(record_policy(r), fa.HERO_N, delta, detail=False)
        except Exception as e:
            row.update(error=str(e), single_run_T=None)
            rows.append(row)
            continue
        row.update(single_run_T=p["T"], qubits=p["qubits"], rotations=p["rotations"], toffoli=p["toffoli"],
                   lean_grid_leaf_T=p["lean_grid_leaf_T"], literal_lean_T=p["literal_lean_T"],
                   ladder_str=p["ladder_str"], n=fa.HERO_N, delta=delta, model=fa.MODEL, tier=fa.TIER, priced_live=True)
        rows.append(row)
    ok = sorted((r for r in rows if r.get("single_run_T") is not None), key=lambda r: (r["single_run_T"], r.get("created_at", "")))
    for i, r in enumerate(ok, start=1):
        r["rank"] = i
    bad = [r for r in rows if r.get("single_run_T") is None]
    verified = sum(1 for r in ok if r.get("lean_verified") is True)
    note = (f"{len(ok)} policy records, priced live at n = 2048 (delta = {delta:g}) with the site's full-run pricer; "
            f"{verified} of them Lean-verified as a policy by ForShor's harness." if ok else
            "No policy record yet. Submit one with ./run.sh forshor (see Participate).")
    return {"policies": ok + bad, "count": len(ok), "lean_verified": verified, "tier": fa.TIER, "rule": RULE,
            "note": note}


# ---------------------------------------------------------------------------------------------- cache
def load(delta: float) -> Optional[Dict[str, Any]]:
    p = _path(delta)
    if not p.is_file():
        return None
    try:
        c = json.loads(p.read_text())
    except Exception:
        return None
    if c.get("fingerprint") != fingerprint() or c.get("schema") != FULL_SCHEMA:
        return None
    return c


def save(c: Dict[str, Any]):
    pc.CACHE.mkdir(exist_ok=True)
    p = _path(c["delta"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, separators=(",", ":")))
    os.replace(tmp, p)
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--delta", type=float, nargs="*", default=list(pc.PRESET_DELTAS))
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    for d in a.delta:
        if not a.force and load(d):
            print(f"full delta={d:g}: cached"); continue
        c = compute(d, a.workers)
        print(f"full delta={d:g}: {c['seconds']}s -> {save(c)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
