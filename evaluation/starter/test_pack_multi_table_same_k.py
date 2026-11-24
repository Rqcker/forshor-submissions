#!/usr/bin/env python3
"""Different bands may use different tables of the same k (PR #132: 22 bands, 11 tables, 7 values of k).

    python test_pack_multi_table_same_k.py PACK_DIR [--python PY]

PACK_DIR is the built `forshor-challenge-starter/` (as `test_pack_docs.py` takes it). The policy beside this file is PR #132's, as published
on the boards. Checks that `starter.py price` prices it to the published numbers (board T and Q, full-run T and Q), that `starter.py template`
writes all 11 tables and 22 bands, and that the same policy with ONE of its two k = 7 tables broken (one operation changed) is still refused.
Exit status 1 on any failure.
"""
import argparse
import copy
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("pack", type=Path)
ap.add_argument("--python", default=sys.executable)
a = ap.parse_args()
POLICY = Path(__file__).with_suffix(".policy.json")
# PR #132's published panels (pp and full, delta 1e-2)
BOARD_T, BOARD_Q, FULL_T, FULL_Q = 9806942.783612985, 4429, 1537953357118.8672, 13454
failures = []


def run(*args):
    return subprocess.run([a.python, "-I", str(a.pack / "starter.py"), *map(str, args)], capture_output=True, text=True)


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    out = tmp / "price.json"
    r = run("price", POLICY, "--json", out)
    if r.returncode != 0:
        failures.append(f"price refused PR #132's policy: {(r.stderr or r.stdout)[-400:]}")
    else:
        p = json.loads(out.read_text())["policies"][0]
        got = (p["board"]["T"], p["board"]["qubits"], p["full_run"]["T"], p["full_run"]["qubits"])
        if got != (BOARD_T, BOARD_Q, FULL_T, FULL_Q):
            failures.append(f"price: got (board T, Q, full T, Q) = {got}, published {(BOARD_T, BOARD_Q, FULL_T, FULL_Q)}")

    lean = tmp / "Policy.lean"
    r = run("template", POLICY, "--out", lean)
    if r.returncode != 0:
        failures.append(f"template refused PR #132's policy: {(r.stderr or r.stdout)[-400:]}")
    else:
        text = lean.read_text()
        tables = set(re.findall(r"^def (t\d+) : ShorLoweringSetup", text, re.M))
        bands = re.search(r"bands := \[(.*?)\]", text, re.S)
        nbands = len(re.findall(r"\(\d+, t\d+\)", bands.group(1))) if bands else 0
        if (len(tables), nbands) != (11, 22):
            failures.append(f"template: {len(tables)} tables and {nbands} bands, expected 11 and 22")

    # break ONE of the two k = 7 tables (the band at 1175, off the 2048 ladder): one addScaled shift changed
    doc = json.loads(POLICY.read_text())
    bad = copy.deepcopy(doc)
    band = next(b for b in bad["bands"] if b["threshold"] == 1175)
    i = next(j for j, op in enumerate(band["operations"]) if op[0] == "addScaled")
    band["operations"][i] = band["operations"][i][:4] + [band["operations"][i][4] + 1]
    (tmp / "bad.json").write_text(json.dumps(bad))
    r = run("price", tmp / "bad.json", "--no-full", "--json", tmp / "bad.price.json")
    if r.returncode == 0:
        failures.append("price accepted the policy with one of its two k = 7 tables broken")

for f in failures:
    print("FAIL:", f)
print("ok" if not failures else f"{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
