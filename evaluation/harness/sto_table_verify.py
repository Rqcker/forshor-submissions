#!/usr/bin/env python3
"""Per-table entry point of the STO policy mirror (PLAN_v2 Stage 12, decision A): every table the site trusts only at
level `replay` or `ci` is run through the Lean mirror's kernel-checked `PolicyTable.check`, as a ONE-BAND policy.

    ./run.sh sto-table --list                     # the tables that would be checked (level replay or ci)
    ./run.sh sto-table [DIGEST12 ...] [--install] # check them (all, or these), one Lean run at a time

Why a one-band policy proves the table. `BandedPolicy.check P = sortedDesc thresholds && ∀ band, band.table.check
P.mode`; with one band the first conjunct is trivially true, so `accepted : policy.check = true` (by `decide +kernel`) is
exactly `PolicyTable.check mode T = true`, and `PolicyTable.check_iff` (TableGeneration/RecursiveCost/BandedPolicy/
Defs.lean) shows that is `2 ≤ k ∧ ValidPointList mode k pts ∧ ProgConsumesPtsSafe … State.start_state ops pts`, STO's
`GeneratorPolicy` contract for one table (`ofGenerator_check`). The band's threshold is the target width 2048, so
`chain_contracts 2048` is one step (2048 -> the child width -> leaf). The kernel replay, axiom audit and statement pin
are the mirror's (`scripts/check_banded_policy.py`), unchanged.

A table is promoted to `lean` (checker "STO mirror") only when its pinned run here is `accepted` (the kernel reduced
the check, both modules `replayed`, axioms within {propext, Classical.choice, Quot.sound}, statement pinned) AND the
pinned one-band policy's sha256 equals the one this file regenerates from the table now (else `stale`). The site reads
only the pin (`engine/data/table_sto_mirror_runs.json`), so trust stays deterministic.

Memory (shared machine): before each run it waits while two or more `lean` processes run (`pgrep -x lean`), then runs
the mirror under `scripts/memcap.py` at 8 GB on the whole check and on each per-module replay. Never several at once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = ROOT / "engine"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ENGINE))

import sto_policy_verify as spv  # noqa: E402

PIN = ENGINE / "data" / "table_sto_mirror_runs.json"
EVIDENCE = ENGINE / "data" / "table_sto_mirror"
CAP_GB = 8.0
THRESHOLD = 2048
MAX_LEAN = 2                   # wait while this many `lean` processes run


def one_band(T) -> Dict[str, Any]:
    """The table as a one-band mirror policy (deterministic: same table -> same bytes)."""
    tj = T.to_json()
    return {"name": f"table {T.digest}", "mode": tj["mode"],
            "bands": [{"threshold": THRESHOLD, "k": tj["k"], "point_order": tj["point_order"],
                       "operations": tj["operations"]}]}


def policy_bytes(T) -> bytes:
    return (json.dumps(one_band(T), indent=1) + "\n").encode()


def all_tables() -> Dict[str, Dict[str, Any]]:
    """digest -> {table, ids} over every table the site knows (STO submissions, DP library, KMY)."""
    from site_engine import library as lib
    out: Dict[str, Dict[str, Any]] = {}
    for s in lib.submissions():
        for d, T in s.tables.items():
            out.setdefault(d, {"table": T, "ids": []})["ids"].append(s.id)
    for r in list(lib.personal_tables()) + list(lib.kmy_tables()):
        out.setdefault(r["table"].digest, {"table": r["table"], "ids": []})["ids"].append(r["id"])
    return out


def targets(include_promoted: bool = True) -> List[Dict[str, Any]]:
    """Tables at level `replay` or `ci` WITHOUT this pin (the levels the other checkers give them), sorted."""
    from site_engine import trust
    out = []
    for d, e in all_tables().items():
        lv = trust.level_of_digest(d, use_table_mirror=False)
        if lv in ("replay", "ci"):
            T = e["table"]
            out.append({"digest": d, "mode": T.mode, "k": T.k, "ids": e["ids"], "old_level": lv, "table": T})
    return sorted(out, key=lambda r: (r["mode"], r["k"], r["digest"]))


def lean_count() -> int:
    p = subprocess.run(["pgrep", "-x", "lean"], capture_output=True, text=True)
    return len(p.stdout.split())


def wait_for_slot(poll: int = 20) -> None:
    waited = 0
    while lean_count() >= MAX_LEAN:
        if waited % 300 == 0:
            print(f"  waiting: {lean_count()} lean processes running", flush=True)
        time.sleep(poll)
        waited += poll


def load_pin() -> Dict[str, Any]:
    return json.loads(PIN.read_text()) if PIN.is_file() else {"runs": {}}


def install(t: Dict[str, Any], res: Dict[str, Any], pol: bytes) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    d = t["digest"]
    (EVIDENCE / f"{d[:16]}.policy.json").write_bytes(pol)
    rec = res.get("mirror")
    if rec is not None:
        (EVIDENCE / f"{d[:16]}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    pin = load_pin()
    keep = ("verdict", "stage", "reason", "mode", "bands", "target_width", "site_policy_sha256", "mirror_input_sha256",
            "mirror_tree_sha256", "mirror_build", "lean_toolchain", "cap_gb", "seconds", "peak_gb", "outer_outcome",
            "replay", "axioms", "statement_pinned", "lean_sha256", "checked_at", "tool")
    pin["runs"][d] = {"digest": d, "ids": t["ids"], "k": t["k"], "old_level": t["old_level"],
                      **{k: res.get(k) for k in keep},
                      "evidence": f"table_sto_mirror/{d[:16]}.json" if rec is not None else None,
                      "policy": f"table_sto_mirror/{d[:16]}.policy.json",
                      "command": f"./run.sh sto-table {d[:12]} --install"}
    pin["runs"] = dict(sorted(pin["runs"].items()))
    pin["note"] = ("Per-table runs of harness/sto_table_verify.py: each table the site held at level replay or ci, as a "
                   "one-band policy (threshold 2048) through the ShorTableOptimization policy mirror "
                   "(scripts/check_banded_policy.py, frozen copy). verdict accepted = the kernel reduced "
                   "`policy.check = true` (one band: exactly PolicyTable.check, = STO's table contract by check_iff) and "
                   "chain_contracts 2048 by decide +kernel, both modules replayed by the trusted kernel replay, axioms "
                   "within {propext, Classical.choice, Quot.sound}, statement of `correct` pinned. site_policy_sha256 is "
                   "the sha256 of the one-band policy; trust.py promotes a table only when it equals the regenerated one.")
    PIN.write_text(json.dumps(pin, indent=1, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("digests", nargs="*", help="digest prefixes (default: every replay/ci table)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--skip-pinned", action="store_true", help="skip tables already accepted in the pin")
    ap.add_argument("--cap-gb", type=float, default=CAP_GB)
    a = ap.parse_args(argv)
    ts = targets()
    if a.digests:
        ts = [t for t in ts if any(t["digest"].startswith(p) for p in a.digests)]
    if a.list:
        for t in ts:
            print(t["digest"][:12], t["mode"], t["k"], t["old_level"], ",".join(t["ids"]))
        print(len(ts), "tables")
        return 0
    build = spv.build_dir()
    pinned = load_pin()["runs"]
    rc = 0
    for i, t in enumerate(ts, 1):
        if a.skip_pinned and (pinned.get(t["digest"]) or {}).get("verdict") == "accepted":
            continue
        pol = policy_bytes(t["table"])
        tmp = ROOT / ".cache" / "sto_table_inputs"
        tmp.mkdir(parents=True, exist_ok=True)
        f = tmp / f"{t['digest'][:16]}.policy.json"
        f.write_bytes(pol)
        wait_for_slot()
        res = spv.check(f, build=build, cap_gb=a.cap_gb, guard_busy=False)
        if a.install:
            install(t, res, pol)
        why = f" [{res.get('stage')}: {res.get('reason')}]" if res["verdict"] != "accepted" else ""
        print(f"[{i}/{len(ts)}] {t['digest'][:12]} {t['mode']} k={t['k']} {','.join(t['ids'])}: {res['verdict']}{why} "
              f"{res['seconds']} s peak {res.get('peak_gb')} GB", flush=True)
        rc = rc or (0 if res["verdict"] == "accepted" else 1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
