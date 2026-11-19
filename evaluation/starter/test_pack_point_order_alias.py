#!/usr/bin/env python3
"""`starter.py` accepts the site's `pointOrder` spelling as well as `point_order`.

    python test_pack_point_order_alias.py PACK_DIR [--python PY]

PACK_DIR is the built `forshor-challenge-starter/` (as `test_pack_docs.py` takes it). The site's panel files (`policyJson`) spell a band's key
`pointOrder`; the pack's format spells it `point_order`. Takes the pack's PP and PTP templates, writes each with the key spelled both ways, prices
both with `starter.py price --no-full`, and checks that the two outputs are identical. Also checks that a band carrying both spellings with
different values is refused, and that a policy with neither is still refused. Exit status 1 on any failure.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("pack", type=Path)
ap.add_argument("--python", default=sys.executable)
a = ap.parse_args()
a.pack = a.pack.resolve()

tmp =Path(tempfile.mkdtemp())
bad = []


def run(policy: dict, name: str):
    f = tmp / f"{name}.json"
    f.write_text(json.dumps(policy))
    out = tmp / f"{name}.out.json"
    r = subprocess.run([a.python, "-I", str(a.pack / "starter.py"), "price", str(f), "--no-full", "--json", str(out)],
                       capture_output=True, text=True, cwd=a.pack)
    return r, (json.loads(out.read_text()) if r.returncode == 0 and out.exists() else None)


def respell(policy: dict, key_from: str, key_to: str) -> dict:
    p = json.loads(json.dumps(policy))
    for b in p["bands"]:
        b[key_to] = b.pop(key_from)
    return p


for template in ("pp_policy.json", "ptp_policy.json"):
    base = json.loads((a.pack / "templates" / template).read_text())
    snake_run, snake = run(base, f"{template}.snake")
    camel_run, camel = run(respell(base, "point_order", "pointOrder"), f"{template}.camel")
    if snake is None:
        bad.append(f"{template}: the snake_case template does not price: {snake_run.stderr.strip()[-200:]}")
        continue
    if camel is None:
        bad.append(f"{template}: the pointOrder spelling does not price: {camel_run.stderr.strip()[-200:]}")
        continue
    for o in (snake, camel):
        o["input"] = None            # the file name differs
    if snake != camel:
        bad.append(f"{template}: the two spellings priced differently")

    conflict = respell(base, "point_order", "pointOrder")
    conflict["bands"][0]["point_order"] = base["bands"][0]["point_order"][:-1]
    r, _ = run(conflict, f"{template}.conflict")
    if r.returncode == 0 or "pointOrder" not in (r.stdout + r.stderr):
        bad.append(f"{template}: a band with both spellings and different values was not refused")

    neither = respell(base, "point_order", "something_else")
    r, _ = run(neither, f"{template}.neither")
    if r.returncode == 0:
        bad.append(f"{template}: a band with no point order was accepted")

print("point_order alias: " + ("FAILED\n  " + "\n  ".join(bad) if bad else "ok (pointOrder and point_order price identically; a conflict and a missing key are refused)"))
sys.exit(1 if bad else 0)
