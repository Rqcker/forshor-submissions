#!/usr/bin/env python3
"""No stale reference survives in what a participant reads. Usage: python test_pack_docs.py PACK_DIR  (the built forshor-challenge-starter/)."""
import sys
from pathlib import Path

STALE = ["FastMultiplication", "qubitCount", "f9adbe9", "10fe620", "run.sh", "probe-scan", "<LIB>", "PTP suite", "ptp-suite", "policy-mirror",
         "November 1 to December 1", "static-layout formula at exact widths", "sto_site"]
ALLOWED = {"harness/policy_to_template.py": ["FastMultiplication", "qubitCount"]}   # a comment saying the old name no longer exists
pack = Path(sys.argv[1])
bad = []
for f in sorted(pack.rglob("*")):
    rel = str(f.relative_to(pack))
    if not f.is_file() or rel.startswith("engine/") or rel.startswith("examples/expected/") or rel == "MANIFEST.json" or f.suffix in {".zip", ".pyc", ".json"}:   # .json: data (a ForShor-printed document's own text)
        continue
    text = f.read_text(errors="ignore")
    for w in STALE:
        if w in text and w not in ALLOWED.get(rel, []):
            bad.append(f"{rel}: {w!r}")
for f in sorted(pack.rglob("*.md")):
    rel = str(f.relative_to(pack))
    if rel.startswith("engine/"):
        continue
    for n, line in enumerate(f.read_text().splitlines(), 1):
        if line.endswith("|") and not line.startswith("|") and not line.startswith(("`", " ", ">", "-", "*")):
            bad.append(f"{rel}:{n}: a table row that does not start with '|' (an orphan fragment): {line[:60]!r}")
print(f"{len(bad)} stale references")
for b in bad:
    print("  ", b)
sys.exit(1 if bad else 0)
