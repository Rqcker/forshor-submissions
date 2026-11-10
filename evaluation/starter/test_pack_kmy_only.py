#!/usr/bin/env python3
"""The pack starts from the KMY (2024) tables and gives away nothing of the archive.

    python test_pack_kmy_only.py PACK_ZIP [--python PY]

Run it from a checkout of this repository (it needs the repository's own engine data to know what the archive is). It unzips the pack into a
temporary directory, as a participant gets it, and checks that:

  1. no file of the pack (engine, examples, docs, manifest) contains an archive table digest, an archive submission id or sha, a DP-library id or
     digest, or the id/name of a seeded planner policy (all read from the repository's `evaluation/engine/data`);
  2. the pack's `engine/data` holds exactly the KMY tables (plus its README and the empty `personal_tables/tables.json`), none of the archive's
     data files, and the pack's own library loaders find no archive tables;
  3. every policy file the pack ships (examples, templates) uses KMY's tables only, or is a ForShor printed document / the ForShor template;
  4. the unzipped pack's own commands work: `starter.py examples --check` matches its pinned outputs, `starter.py price` prices a KMY policy and
     `starter.py template` writes a submission file from it.

Exit status 1 on any failure.
"""
import argparse
import glob
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ENGINE = REPO / "evaluation" / "engine"
DATA = ENGINE / "data"

ap = argparse.ArgumentParser()
ap.add_argument("zip", type=Path)
ap.add_argument("--python", default=sys.executable)
a = ap.parse_args()

bad = []


def fail(msg):
    bad.append(msg)


# ---- what the archive is, from the repository's own engine data
sys.path.insert(0, str(ENGINE))
import site_engine  # noqa: E402,F401
from site_engine import library as lib  # noqa: E402
from tcost import tables as tt  # noqa: E402

kmy = {r["table"].digest: r["id"] for r in lib.kmy_tables()}
secrets = {}                                                   # string -> what it is
for s in lib.submissions():
    secrets[s.id] = "archive submission id"
    secrets[s.sha] = "archive submission sha"
    for d in s.tables:
        if d not in kmy:
            secrets[d] = "archive table digest"
for r in lib.personal_tables():
    if r["table"].digest not in kmy:
        secrets[r["table"].digest] = "DP-library table digest"
    secrets[r["id"]] = "DP-library id"
import re  # noqa: E402
for d in sorted(glob.glob(str(DATA / "forshor_submitted" / "S*"))):
    if re.fullmatch(r"S[0-9a-f]{12}", Path(d).name):
        secrets[Path(d).name] = "seeded planner policy id"
for kind in ("sto_archive", "sto_accepted", "sto_submitted"):
    for d in glob.glob(str(DATA / kind / "*")):
        if re.fullmatch(r"P?[0-9a-f]{12,64}", Path(d).name):
            secrets[Path(d).name.lstrip("P")] = "archive directory name"
secrets = {k: v for k, v in secrets.items() if len(k) >= 10}
print(f"looking for {len(secrets)} archive identifiers in the pack")

tmp = Path(tempfile.mkdtemp())
zipfile.ZipFile(a.zip).extractall(tmp)
pack = tmp / "forshor-challenge-starter"

# ---- 1. nothing of the archive anywhere in the pack
files = [f for f in sorted(pack.rglob("*")) if f.is_file()]
for f in files:
    text = f.read_text(errors="ignore")
    for k, what in secrets.items():
        if k in text:
            fail(f"{f.relative_to(pack)}: contains a {what} ({k[:16]}...)")
    if "BestKnown" in f.name:
        fail(f"{f.relative_to(pack)}: BestKnown is the archive's champion list")

# ---- 2. the data directory is KMY's tables only
data = pack / "engine" / "data"
want = {"kmy_tables", "personal_tables", "README.md"}
got = {p.name for p in data.iterdir()}
if got != want:
    fail(f"engine/data holds {sorted(got)}, expected {sorted(want)}")
if json.loads((data / "personal_tables" / "tables.json").read_text()) != []:
    fail("engine/data/personal_tables/tables.json is not an empty list")
if json.loads((data / "kmy_tables" / "operations.json").read_text()) != json.loads((DATA / "kmy_tables" / "operations.json").read_text()):
    fail("the pack's KMY tables differ from the repository's")
probe = subprocess.run([a.python, "-c", (
    "import sys; sys.path.insert(0, 'engine'); import site_engine\n"
    "from site_engine import library as lib\n"
    "print(len(lib.submissions()), len(lib.personal_tables()), len(lib.kmy_tables()), len(lib.champion_tables()))")],
    cwd=pack, capture_output=True, text=True)
if probe.returncode != 0 or probe.stdout.split() != ["0", "0", "3", "0"]:
    fail(f"the pack's library should hold 0 archive submissions, 0 DP tables, 3 KMY tables and 0 champions: {probe.stdout.strip()} {probe.stderr[-200:]}")

# ---- 3. every policy file uses KMY's tables
sys.path.insert(0, str(pack / "engine"))
for f in sorted((pack / "examples").glob("*.policy.json")) + sorted((pack / "templates").glob("*_policy.json")):
    doc = json.loads(f.read_text())
    if "bands" not in doc or not isinstance(doc["bands"], list) or not doc["bands"] or "operations" not in doc["bands"][0]:
        continue                                               # a ForShor printed document: not a site policy
    for P in tt.checked_policies(tt.policy_docs(doc)):
        for b in P.bands:
            if b.table.digest not in kmy:
                fail(f"{f.relative_to(pack)}: a band uses a table that is not one of KMY's")
for lean in ("kmy_pp.Policy.lean", "kmy_ptp.Policy.lean"):
    if not (pack / "examples" / lean).is_file():
        fail(f"examples/{lean} is missing")

# ---- 4. the unzipped pack's own commands
run = lambda *c: subprocess.run([a.python, "starter.py", *c], cwd=pack, capture_output=True, text=True)
r = run("examples", "--check")
if r.returncode != 0 or "every example matches its pinned output" not in r.stdout:
    fail("starter.py examples --check: " + (r.stdout[-400:] + r.stderr[-400:]))
out = tmp / "kmy.json"
r = run("price", "examples/kmy_pp.policy.json", "--json", str(out))
if r.returncode != 0:
    fail("starter.py price on the KMY PhaseProduct policy: " + r.stderr[-300:])
else:
    got = json.loads(out.read_text())["policies"][0]
    rank = got["board"]["rank_on_board"]
    if not rank or rank["rank"] != 2 or rank["of"] != 2:
        fail(f"the KMY policy should tie with the snapshot's one row and rank after it (rank 2 of 2), got {rank}")
r = run("template", "examples/kmy_pp.policy.json")
if r.returncode != 0 or "namespace PolicySubmission" not in r.stdout:
    fail("starter.py template did not write a PhaseProduct submission file: " + r.stderr[-300:])
r = run("template", "examples/kmy_ptp.policy.json")
if r.returncode != 0 or "namespace PTPPolicySubmission" not in r.stdout:
    fail("starter.py template did not write a PhaseTripleProduct submission file: " + r.stderr[-300:])

print(f"{len(files)} files checked; {len(bad)} problems")
for b in bad:
    print("  ", b)
sys.exit(1 if bad else 0)
