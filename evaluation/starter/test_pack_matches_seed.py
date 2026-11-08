#!/usr/bin/env python3
"""The pack prices every policy of the results seed to exactly the T and Q its rows show.

    python test_pack_matches_seed.py PACK_ZIP SEED_EPOCH_INPUT [--python PY]

Unzips the pack into a temporary directory (so it is checked as a participant gets it, not as built), then for every panel of the
seed at delta = 0.01: writes the panel's policy, runs `starter.py price`, and compares with the board row: T and Q on the PhaseProduct and
PhaseTripleProduct boards (the board's own numbers), and the single-run T and the live-qubit peak on the full board.
Exit status 1 on any difference. Slow only in that it prices ~20 policies.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("zip", type=Path)
ap.add_argument("seed", type=Path)
ap.add_argument("--python", default=sys.executable)
a = ap.parse_args()

seed = json.loads(a.seed.read_text())
rows = {(k.split("/")[0], r["rowId"]): r for k, rs in seed["rows"].items() if k.endswith("/1e-2") for r in rs}
tmp = Path(tempfile.mkdtemp())
zipfile.ZipFile(a.zip).extractall(tmp)
pack = tmp / "forshor-challenge-starter"
bad, checked = [], 0
for key, panel in sorted(seed["panels"].items()):
    if not key.endswith("-1e-2.json"):
        continue
    board, rid = panel["board"], panel["rowId"]
    pj = panel["policyJson"]
    doc = {"name": pj["name"], "mode": pj["mode"], "bands": [{"threshold": b["threshold"], "mode": b["mode"], "k": b["k"],
                                                              "point_order": b["pointOrder"], "operations": b["operations"]} for b in pj["bands"]]}
    f = tmp / f"{board}-{rid}.policy.json"
    f.write_text(json.dumps(doc))
    out = tmp / f"{board}-{rid}.out.json"
    r = subprocess.run([a.python, str(pack / "starter.py"), "price", str(f), "--json", str(out), *([] if board == "full" else ["--no-full"])],
                       cwd=pack, capture_output=True, text=True)
    if r.returncode != 0:
        bad.append(f"{board} {rid}: starter.py failed: {r.stderr[-200:]}")
        continue
    got = json.loads(out.read_text())["policies"][0]
    row = rows[(board, rid)]
    t, q = (got["full_run"]["T"], got["full_run"]["qubits"]) if board == "full" else (got["board"]["T"], got["board"]["qubits"])
    ok = abs(t - row["T"]) <= 1e-9 * row["T"] and q == row["qubits"]
    checked += 1
    if not ok:
        bad.append(f"{board} {rid} ({row['name']}): pack T {t!r} Q {q} != seed row T {row['T']!r} Q {row['qubits']}")
print(f"{checked} panels priced by the pack; {len(bad)} differ from the seed rows")
for b in bad:
    print("  ", b)
sys.exit(1 if bad else 0)
