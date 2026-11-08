"""The paper's order-finding model (ForShor paper, "Translation to Qualtran"; Lean-To-Qualtran
`scripts/manuscript_policy_shor.py`), driven from the site. The model runs in a subprocess
(`engine/paper_model/site_worker.py`, which keeps the original package name `forshor_qualtran`).

What a "policy" means here (PLAN_v2 Stage 10, replacing Stage 2b): a policy is tables with width thresholds (paper,
Definition "policy"). Every phase product of the run, at whatever width it has, is split by the table of the first band
with threshold <= its width, and its children have that table's own child width W_T(w), the Lean width scan on floor
limbs (paper eq. TPP; the width the harness proves and the PhaseProduct board uses). A leaderboard row and a library
milestone are priced as the policy the PhaseProduct board chose for them. Stage 2b priced instead "the cheapest table at
every width" (version C) with the paper model's R2 widths, which ignore a table's program: tables that differ only in
their shifts and the order of their additions priced the same, so most PhaseProduct records left the full count unmoved.
R2 survives only as `widths="r2"`, to reproduce the paper's printed figure.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import ENGINE, QUALTRAN_TREE
from tcost import tables as tt

WORKER = ENGINE / "paper_model" / "site_worker.py"
MODEL = ("ForShor paper model: LeanOrderFinding(core='versionB'); every phase product split by the policy's table for its "
         "width, children at that table's own Lean-scanned width W_T(w) (floor limbs, paper eq. TPP); host k = 4, "
         "delta split 0.5, sampled-angle Ross-Selinger rotation cost")
#: Child-width rule sent to the worker: "scan" (each table's own nextSignedWidth; the default and the site's model) or
#: "r2" (the paper model's tight widths on zero-extended limbs; only to reproduce the paper's printed figure).
WIDTHS = "scan"


def table_entry(T: tt.Table, tid: str) -> Dict[str, Any]:
    from forshor_qualtran.table_catalog import op_from_sto      # the site fork's converter (pure)
    return {"id": tid, "k": T.k, "ops": [op_from_sto(o) for o in T.to_json()["operations"]]}


_TABLES: Dict[str, tt.Table] = {}


def _tid(T: tt.Table) -> str:
    """Worker-side table id: the digest (several tables share one archive ID); mapped back by `annotate`."""
    _TABLES[T.digest] = T
    return T.digest


def catalog_job(tables: Sequence[tt.Table], n: int, delta: float, cnot: bool = True, widths: str = WIDTHS) -> Dict[str, Any]:
    """Version C over a set of tables: the cheapest table at every width. Not a policy (it can use two tables of one
    arity); the site prices policies with `policy_job` and keeps this for the reference series and the starter pack."""
    return {"kind": "catalog", "n": int(n), "delta": float(delta), "cnot": cnot, "widths": widths,
            "tables": [table_entry(T, _tid(T)) for T in tables]}


def policy_job(P: tt.Policy, n: int, delta: float, cnot: bool = True, widths: str = WIDTHS) -> Dict[str, Any]:
    """A policy, priced as written: at every width the first band with threshold <= width chooses the table, its
    children have that table's own width W_T(w), and below the lowest band (or where a table does not shrink the
    width) the product is a leaf grid."""
    if P.mode != tt.PAIR:
        raise ValueError("the order-finding circuit uses phase products only (no PhaseTripleProduct)")
    return {"kind": "policy", "n": int(n), "delta": float(delta), "cnot": cnot, "widths": widths,
            "bands": [{"threshold": b.threshold, **table_entry(b.table, _tid(b.table))} for b in P.bands]}


def map_job(tables: Sequence[tt.Table], decisions: Sequence[Sequence[Any]], stop_tsr: float, n: int, delta: float,
            cnot: bool = True, usage: bool = True, widths: str = WIDTHS) -> Dict[str, Any]:
    """Re-price a full-run decision map (`decision_map`): every width -> table digest, or None for the leaf grid."""
    return {"kind": "map", "n": int(n), "delta": float(delta), "cnot": cnot, "usage": usage, "stop_tsr": float(stop_tsr),
            "widths": widths,
            "tables": [table_entry(T, _tid(T)) for T in tables], "decisions": [[int(W), d] for W, d in decisions]}


def bundles_job(n: int, delta: float, catalog: str = "k2-7", cnot: bool = True, widths: str = WIDTHS) -> Dict[str, Any]:
    return {"kind": "bundles", "catalog": catalog, "n": int(n), "delta": float(delta), "cnot": cnot, "widths": widths}


def annotate(r: Dict[str, Any], trial_count: Optional[int] = None) -> Dict[str, Any]:  # trial_count: unused, kept for callers
    """Map digests back to archive / DP / KMY IDs and add the derived fields the page shows."""
    from . import library as lib
    origin = lib.table_origin()
    if not r.get("ok"):
        raise RuntimeError(r.get("error"))
    for row in r["ladder"]:
        dg = row["table"]
        row["digest"] = dg if dg in _TABLES else None
        row["table"] = origin.get(dg, "pasted") if dg in _TABLES else row["table"]
    lad = r["ladder"]
    if "usage" in r:
        _annotate_usage(r, origin)
    r["ladder_str"] = " -> ".join(f"{x['W']}:{x['k'] if x['k'] else 'leaf'}" for x in lad)
    # The label names EVERY table the run uses (all phase products and QFT twiddles), not only the main ladder's.
    # IDs are archive submissions (one submission can ship tables for several k), so each appears once.
    r["label"] = (" + ".join(dict.fromkeys(t["id"] for t in r["tables_used"])) if "tables_used" in r
                  else " + ".join(dict.fromkeys(x["table"] for x in lad if x["k"]))) or "leaf"
    r["main_label"] = " + ".join(dict.fromkeys(x["table"] for x in lad if x["k"])) or "leaf"
    r["rotations"] = int(r.pop("R"))
    r["T_base"] = r["T11"] - 11.0 * r["rotations"]
    r["T_per_rotation"] = r.pop("t_per_rotation")
    r["toffoli"] = int(r["toffoli"])
    r["qubits"] = int(r["qubits"])
    r.pop("ok", None)
    return r


def _annotate_usage(r: Dict[str, Any], origin: Dict[str, str]) -> None:
    """Every phase product of the run -> origin IDs, the tables it uses (with their share of the phase-product T11),
    and the decision map (width -> table or leaf) that reproduces the run exactly (`map_job`)."""
    dec: Dict[int, Optional[str]] = {}
    share: Dict[str, Dict[str, Any]] = {}
    total = sum(u["T_total"] for u in r["usage"]) or 1.0
    for u in r["usage"]:
        rows = u["ladder"]
        for i, x in enumerate(rows):
            dg = x["table"] if x["k"] else None
            if x["W"] in dec and dec[x["W"]] != dg:
                raise RuntimeError(f"two decisions at width {x['W']}: {dec[x['W']]} and {dg}")
            dec[x["W"]] = dg
            x["digest"] = dg if dg in _TABLES else None
            x["table"] = origin.get(dg, "pasted") if dg in _TABLES else x["table"]
        u["ladder_str"] = " -> ".join(f"{x['W']}:{x['k'] if x['k'] else 'leaf'}" for x in rows)
        u["share"] = u["T_total"] / total
        for x in rows:
            if x["k"]:
                e = share.setdefault(x["digest"] or x["table"], {"id": x["table"], "digest": x["digest"], "k": x["k"],
                                                                 "widths": set(), "roots": 0})
                e["widths"].add(x["W"])
        for dg in dict.fromkeys(x["digest"] or x["table"] for x in rows if x["k"]):
            share[dg]["roots"] += u["count"]
    r["tables_used"] = sorted(({**e, "widths": sorted(e["widths"], reverse=True)} for e in share.values()),
                              key=lambda e: (-e["k"], e["id"]))
    r["decision_map"] = [[W, dec[W]] for W in sorted(dec, reverse=True)]


def decision_doc(r: Dict[str, Any], title: str) -> Dict[str, Any]:
    """The downloadable full-run policy: tables + decision map + the counts it must reproduce."""
    tabs = []
    for t in r["tables_used"]:
        T = _TABLES.get(t["digest"]) or table_by_digest(t["digest"])
        if T is None:
            raise RuntimeError(f"table {t['id']} ({t['digest']}) is not in the site library")
        tabs.append({"id": t["id"], "digest": T.digest, **T.to_json()})
    return {"format": "forshor-full-decision-map/2", "title": title, "model": MODEL, "widths": WIDTHS, "n": r["n"], "delta": r["delta"],
            "stop_tsr": r["stop_tsr"], "tables": tabs,
            "decisions": [{"width": W, "table": d} for W, d in r["decision_map"]],
            "expected": {k: r[k] for k in ("T", "T11", "rotations", "toffoli", "clifford", "cnot", "qubits") if k in r},
            "reproduce": "cd engine && python -m site_engine.paper_full reproduce <this file>"}


def reproduce(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Re-price a downloaded decision map and compare with the counts it states."""
    tabs = [tt.Table.from_json({k: v for k, v in t.items() if k not in ("id", "digest")}) for t in doc["tables"]]
    for t, T in zip(doc["tables"], tabs):
        if T.digest != t["digest"]:
            raise ValueError(f"table {t['id']}: digest {T.digest} != stated {t['digest']}")
    r = run([map_job(tabs, [(d["width"], d["table"]) for d in doc["decisions"]], doc["stop_tsr"], doc["n"], doc["delta"],
                     widths=doc.get("widths", "r2"))])[0]   # format /1 maps were priced with R2
    if not r.get("ok"):
        raise RuntimeError(r.get("error"))
    exp = doc.get("expected", {})
    got = {"T": r["T"], "T11": r["T11"], "rotations": int(r["R"]), "toffoli": int(r["toffoli"]), "clifford": r["clifford"],
           "cnot": r.get("cnot"), "qubits": int(r["qubits"])}
    return {"got": got, "expected": exp,
            "match": all(k not in exp or exp[k] is None or abs(got[k] - exp[k]) <= 1e-9 * max(1.0, abs(exp[k])) for k in got)}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Re-price a full-run decision map downloaded from the site.")
    ap.add_argument("cmd", choices=["reproduce"])
    ap.add_argument("file")
    a = ap.parse_args(argv)
    res = reproduce(json.loads(open(a.file).read()))
    for k, v in res["got"].items():
        print(f"{k:10s} {v!r:>24}   stated {res['expected'].get(k)!r}")
    print("MATCH" if res["match"] else "MISMATCH")
    return 0 if res["match"] else 1



def ladder_policy(r: Dict[str, Any], name: str) -> Optional[tt.Policy]:
    """The main modular-multiply descent as a banded policy: level i's table for widths above level i+1's width.
    (The model chooses the cheapest table at every width; this is that choice along the main ladder.)"""
    for x in r["ladder"]:
        if x.get("digest") and x["digest"] not in _TABLES:
            T = table_by_digest(x["digest"])
            if T is not None:
                _TABLES[T.digest] = T
    lad = [x for x in r["ladder"] if x["k"] and x.get("digest") in _TABLES]
    if not lad:
        return None
    bands = []
    for i, x in enumerate(lad):
        nxt = r["ladder"][i + 1]["W"] if i + 1 < len(r["ladder"]) else 0
        bands.append(tt.Band(int(nxt) + 1, _TABLES[x["digest"]]))
    return tt.Policy(mode=tt.PAIR, bands=tuple(bands), name=name)


def run(jobs: List[Dict[str, Any]], timeout: float = 900) -> List[Dict[str, Any]]:
    env = dict(os.environ, ONECLICK_QUALTRAN=str(QUALTRAN_TREE))
    r = subprocess.run([sys.executable, "-W", "ignore", str(WORKER)], input=json.dumps({"jobs": jobs}),
                       capture_output=True, text=True, env=env, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"paper-model worker failed: {r.stderr[-2000:]}")
    return json.loads(r.stdout)["results"]


def run_parallel(jobs: List[Dict[str, Any]], workers: Optional[int] = None) -> List[Dict[str, Any]]:
    """Split the jobs over several worker processes (each loads the model once)."""
    from concurrent.futures import ThreadPoolExecutor
    workers = max(1, min(len(jobs), workers or max(1, (os.cpu_count() or 2) - 1)))
    chunks = [jobs[i::workers] for i in range(workers)]
    with ThreadPoolExecutor(workers) as ex:
        parts = list(ex.map(run, chunks))
    out: List[Optional[Dict[str, Any]]] = [None] * len(jobs)
    for w, part in enumerate(parts):
        for j, res in enumerate(part):
            out[w + j * workers] = res
    return out  # type: ignore[return-value]


def table_by_digest(dg: str) -> Optional[tt.Table]:
    from . import library as lib
    for T in list(lib.sto_tables()) + [r["table"] for r in lib.personal_tables()] + [r["table"] for r in lib.kmy_tables()]:
        if T.digest == dg:
            return T
    return None


if __name__ == "__main__":
    raise SystemExit(main())
