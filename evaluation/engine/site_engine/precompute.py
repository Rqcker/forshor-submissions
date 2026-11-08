"""Compute and cache the frontier (curve, hero, leaderboard) per (mode, delta).

    python -m site_engine.precompute                       # modes PP, PTP at delta 1e-2, 3e-4, 1e-4, 1e-6
    python -m site_engine.precompute --delta 0.001 --mode PhaseProduct

Two width rules (PLAN_v2 Stage 13). The PP and PTP boards are priced and planned at EXACT widths
(frontier_<mode>_<delta>.json). The PhaseProduct curve is also planned at ForShor's SCANNED widths
(frontier_PP_scan_<delta>.json): the labelled cross-check, and the policies the full order-finding view prices
(full_frontier reads only that file, so the full run is unchanged by Stage 13).

The cache (engine/cache/*.json) is derived data. Each file records the model version and a fingerprint of
engine/data; a file whose fingerprint does not match is ignored and recomputed. Never hand-edit it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from . import DATA, ENGINE, SITE_MODEL_VERSION
from . import frontier as fr
from . import library as lib
from tcost import tables as tt

CACHE = ENGINE / "cache"
PRESET_DELTAS = (1e-2, 3e-4, 1e-4, 1e-6)
#: Bump when the cached format changes (2: chart metrics; 3: KMY milestone, below_kmy_pct; 4: width rule, Stage 13).
CACHE_SCHEMA = 4


@lru_cache(maxsize=1)
def data_fingerprint() -> str:
    h = hashlib.sha256()
    for p in sorted(DATA.rglob("*")):
        # `forshor_submitted` holds the board's records (full order finding): nothing in a frontier reads them, so installing
        # one must not invalidate the caches (a recompute costs minutes).
        if p.is_file() and p.name != ".DS_Store" and p.relative_to(DATA).parts[0] != "forshor_submitted":
            h.update(str(p.relative_to(DATA)).encode())
            h.update(hashlib.sha256(p.read_bytes()).digest())
    for p in sorted((ENGINE / "tcost").rglob("*.py")) + sorted((ENGINE / "forshor_qualtran").rglob("*.py")) \
            + [ENGINE / "sto_currency.py"]:      # format changes of the cached points: CACHE_SCHEMA
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()[:16]


def _path(mode: str, delta: float, widths: str = "exact") -> Path:
    tag = ("PP" if mode == tt.PAIR else "PTP") + ("" if widths == "exact" else f"_{widths}")
    return CACHE / f"frontier_{tag}_{delta:.0e}.json"


def _bands_key(pj: Dict[str, Any]) -> str:
    return json.dumps(pj["bands"], sort_keys=True)


def _task(args):
    """Plan at (key, n, delta). Qubits do not depend on delta: reuse them when another cached delta planned the
    same policy (`hint`: bands -> qubits), since QubitCount is the slow part."""
    key, mode, n, delta, hint, widths = args
    from tcost import planner as tp
    P, _ = tp.optimal_policy(lib.milestone_tables(key, mode), mode, n, delta, name=f"{key}@{n}", widths=widths)
    pj = P.to_json()
    p = fr.point(P, n, delta, known_qubits=hint.get(_bands_key(pj)), with_qubits=(widths == "exact"))
    return key, n, pj, p


def _qubit_hints(mode: str, widths: str = "exact") -> Dict[str, Dict[str, int]]:
    """(key@n) -> {bands json: Qualtran qubits} from every cached delta of this mode under the same width rule."""
    out: Dict[str, Dict[str, int]] = {}
    # Qubits depend only on the policy (its bands) and n, so any earlier cache file is a valid hint, even one whose
    # data fingerprint or schema is out of date; the bands JSON is the key.
    for f in sorted(CACHE.glob(f"frontier_{'PP' if mode == tt.PAIR else 'PTP'}_*.json")):
        try:
            c = json.loads(f.read_text())
        except Exception:
            continue
        if "policies" not in c or c.get("widths", "scan") != widths:   # before Stage 13 every cache was scan
            continue
        for key, by_n in c["policies"].items():
            if key not in c.get("milestones", {}):
                continue
            qs = {pt["n"]: pt.get("qubits_qualtran_peak") for pt in c["milestones"][key]["points"]}
            for n, pj in by_n.items():
                if qs.get(int(n)) is not None:
                    out.setdefault(f"{key}@{n}", {})[_bands_key(pj)] = qs[int(n)]
    return out


def compute(mode: str, delta: float, workers: Optional[int] = None, widths: str = "exact") -> Dict[str, Any]:
    """The full frontier for (mode, delta), planned at every grid width; planning runs in a process pool. `widths`:
    "exact" (the boards) or "scan" (the cross-check and the full run's policies; no qubits)."""
    delta = fr.check_delta(delta)
    t0 = time.time()
    hints = _qubit_hints(mode, widths)
    tasks = [(k, mode, n, delta, hints.get(f"{k}@{n}", {}), widths) for k, _, _ in lib.PLANNED for n in fr.N_GRID]
    workers = workers or max(1, min(len(tasks), (os.cpu_count() or 2) - 1))
    results = []
    if workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            results = list(ex.map(_task, tasks, chunksize=2))
    else:
        results = [_task(t) for t in tasks]
    ms: Dict[str, Any] = {}
    pols: Dict[str, Dict[str, Any]] = {}
    for key, n, pj, p in results:
        ms.setdefault(key, {"points": [], "fixed": False})["points"].append(p)
        pols.setdefault(key, {})[str(n)] = pj
    for v in ms.values():
        v["points"].sort(key=lambda p: p["n"])
    ref = fr.reference_points(mode, delta, widths)
    if widths != "exact":
        for p in ref["points"]:
            for k in ("qubits", "qubits_qualtran_peak", "qubits_rule", "qubits_label", "qubits_lean_static_scan"):
                p.pop(k, None)
    out = {"model": SITE_MODEL_VERSION, "schema": CACHE_SCHEMA, "fingerprint": data_fingerprint(), "mode": mode, "delta": delta,
           "widths": widths, "replanned": True, "grid": list(fr.N_GRID),
           "milestones": {"reference": {"points": ref["points"], "window": ref["window"], "fixed": True}, **ms},
           "policies": pols}
    out["hero"] = hero_from(out)
    out["leaderboard"] = fr.leaderboard(mode, delta, widths)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def hero_from(c: Dict[str, Any]) -> Dict[str, Any]:
    def at(key):
        return next(p for p in c["milestones"][key]["points"] if p["n"] == fr.HERO_N)
    ref, kmy = at("reference"), at("kmy")
    rows = []
    for key, label, _ in lib.MILESTONES:
        p = at(key)
        rows.append({"key": key, "T": p["T"], "ladder_str": p["ladder_str"],
                     "label": "STO reference" if key == "reference" else p["label"], "agree": p["agree"],
                     "below_reference_pct": 100.0 * (ref["T"] - p["T"]) / ref["T"],
                     "below_kmy_pct": 100.0 * (kmy["T"] - p["T"]) / kmy["T"], "delta_kmy": p["T"] - kmy["T"]})
    return {"n": fr.HERO_N, "delta": c["delta"], "milestones": rows}


def rescore(base: Dict[str, Any], delta: float) -> Dict[str, Any]:
    """Fast answer for a new delta: keep the policies planned at base['delta'] and re-count them at `delta`
    (both engines). Marked replanned = False; the full re-plan follows."""
    delta = fr.check_delta(delta)
    mode = base["mode"]
    widths = base.get("widths", "exact")
    ms: Dict[str, Any] = {}
    ref = fr.reference_points(mode, delta, widths)
    ms["reference"] = {"points": ref["points"], "window": ref["window"], "fixed": True}
    for key, by_n in base["policies"].items():
        q = {p["n"]: p.get("qubits_qualtran_peak") for p in base["milestones"][key]["points"]}   # same policy -> same qubits
        pts = [fr.point(tt.Policy.from_json(pj, widths=widths), int(n), delta, known_qubits=q.get(int(n)),
                        with_qubits=(widths == "exact"))
               for n, pj in sorted(by_n.items(), key=lambda x: int(x[0]))]
        ms[key] = {"points": pts, "fixed": False}
    out = {"model": SITE_MODEL_VERSION, "schema": CACHE_SCHEMA, "fingerprint": base["fingerprint"], "mode": mode,
           "delta": delta, "widths": widths, "replanned": False, "grid": base["grid"], "milestones": ms}
    out["hero"] = hero_from(out)
    out["leaderboard"] = None
    return out


def load(mode: str, delta: float, widths: str = "exact") -> Optional[Dict[str, Any]]:
    p = _path(mode, delta, widths)
    if not p.is_file():
        return None
    try:
        c = json.loads(p.read_text())
    except Exception:
        return None
    if c.get("fingerprint") != data_fingerprint() or c.get("model") != SITE_MODEL_VERSION \
            or c.get("schema") != CACHE_SCHEMA or c.get("widths") != widths:
        return None
    return c


def save(c: Dict[str, Any]) -> Path:
    CACHE.mkdir(exist_ok=True)
    p = _path(c["mode"], c["delta"], c.get("widths", "exact"))
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, separators=(",", ":")))
    os.replace(tmp, p)
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--delta", type=float, nargs="*", default=list(PRESET_DELTAS))
    ap.add_argument("--mode", nargs="*", default=list(lib.MODES))
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    for mode in a.mode:
        # PP: the scanned-width curve too (the cross-check, and the policies the full order-finding view prices)
        for widths in (("scan", "exact") if mode == tt.PAIR else ("exact",)):
            for d in a.delta:
                if not a.force and load(mode, d, widths):
                    print(f"{mode} {widths} delta={d:g}: cached"); continue
                c = compute(mode, d, a.workers, widths)
                print(f"{mode} {widths} delta={d:g}: {c['seconds']}s -> {save(c)}")
    if tt.PAIR in a.mode:
        # the full order-finding view prices the PhaseProduct board's policies (Stage 11: fullalg, seconds per delta)
        from . import full_frontier as ff
        for d in a.delta:
            if not a.force and ff.load(d):
                print(f"full delta={d:g}: cached"); continue
            c = ff.compute(d)
            print(f"full delta={d:g}: {c['seconds']}s -> {ff.save(c)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
