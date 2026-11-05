#!/usr/bin/env python3
"""Replay the accepted STO policies through the harness (PLAN_v2 Stage 3.5).

    python harness/replay_accepted.py [--sto DIR] [--only P52ec6fce9b57 ...] [--out DIR]

For each `TableGeneration/Policies/Accepted/P<id>.lean` of the STO tree, rewrite it (and its helper directory) into the
submission layout, run it through `sto_verify.run`, and compare the tables that come out with the archived tables of
`engine/data` by digest. A match means: the harness reproduces, from source, through STO's verifier and the Python
stage, exactly the tables that every number on the site was computed from.

The archive also holds submissions that STO main does not carry as accepted source (for example P651fc8ae389d): they are
listed as `no source` and skipped.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sto_verify as hv  # noqa: E402
from site_engine import DATA  # noqa: E402,F401
from tcost import tables as tt  # noqa: E402


def accepted_to_submission(sto: Path, pid: str, dest: Path) -> Path:
    """Re-namespace an accepted policy (and its helper directory) into the submission layout. References to OTHER
    accepted policies stay: the verifier admits them."""
    old = f"TableGeneration.Policies.Accepted.{pid}"
    new = "TableGeneration.Submission.Policy"
    src = Path(sto) / "TableGeneration" / "Policies" / "Accepted"
    base = dest / "TableGeneration" / "Submission"
    (base / "Policy").mkdir(parents=True, exist_ok=True)
    (base / "Policy.lean").write_text((src / f"{pid}.lean").read_text().replace(old, new))
    if (src / pid).is_dir():
        for f in sorted((src / pid).rglob("*.lean")):
            rel = f.relative_to(src / pid)
            (base / "Policy" / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / "Policy" / rel).write_text(f.read_text().replace(old, new))
    return dest


def accepted_ids(sto: Path) -> List[str]:
    return sorted(p.stem for p in (Path(sto) / "TableGeneration" / "Policies" / "Accepted").glob("P*.lean"))


def archived_digests(pid: str) -> Optional[set]:
    for kind in ("sto_accepted", "sto_archive"):
        f = DATA / kind / pid / "operations.json"
        if f.is_file():
            return {tt.Table.from_json(t).digest for t in json.loads(f.read_text())["targets"]}
    return None


def replay(sto: Path, pid: str, out: Path) -> Dict[str, object]:
    want = archived_digests(pid)
    t0 = time.time()
    sub = accepted_to_submission(sto, pid, out / f"sub-{pid}")
    try:
        r = hv.run(sub, sto, out / "runs")
    except hv.HarnessError as e:
        return {"id": pid, "ok": False, "stage": e.stage, "detail": e.detail[:300], "seconds": round(time.time() - t0)}
    d = Path(r["dir"])
    got = {tt.Table.from_json(t).digest for t in json.loads((d / "operations.json").read_text())["targets"]}
    return {"id": pid, "ok": want is not None and got == want, "tables": len(got), "archived": None if want is None else len(want),
            "missing": None if want is None else len(want - got), "extra": None if want is None else len(got - want),
            "seconds": round(time.time() - t0), "record": r["dir"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sto", type=Path, default=hv.DEFAULT_STO)
    ap.add_argument("--out", type=Path, default=hv.ROOT / ".cache" / "replay")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args(argv)
    ids = a.only or accepted_ids(a.sto)
    rows = []
    for pid in ids:
        row = replay(a.sto, pid, a.out)
        rows.append(row)
        flag = "MATCH " if row["ok"] else "FAILED"
        print(f"{flag} {pid}  {row}", flush=True)
    (a.out).mkdir(parents=True, exist_ok=True)
    (a.out / "replay.json").write_text(json.dumps(rows, indent=1))
    bad = [r for r in rows if not r["ok"]]
    print(f"{len(rows) - len(bad)}/{len(rows)} replayed to the archived tables")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
