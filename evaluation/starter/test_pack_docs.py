#!/usr/bin/env python3
"""No stale reference survives in what a participant reads. Usage: python test_pack_docs.py PACK_DIR  (the built forshor-challenge-starter/)."""
import json
import sys
from pathlib import Path

STALE = ["FastMultiplication", "qubitCount", "f9adbe9", "10fe620", "run.sh", "probe-scan", "<LIB>", "PTP suite", "ptp-suite", "policy-mirror",
         "November 1 to December 1", "static-layout formula at exact widths", "sto_site",
         "courtneyjzapata", "port/forshor-21e129a", "ShorTableOptimization_temp", "interim STO fork", "harness/ptp-fast-report",
         "STO mirror", "STO policy mirror", "STO's table contract"]   # the participant-facing name is "policy check" (the site dropped "STO" on 2026-11-14)
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
# the submission file is `Policy.lean`: no file called PolicyTemplate*.lean may ship, and every shipped .lean example must be named Policy.lean or end in .Policy.lean
for f in sorted(pack.rglob("*.lean")):
    rel = str(f.relative_to(pack))
    if not rel.startswith("engine/") and not (f.name == "Policy.lean" or f.name.endswith(".Policy.lean")):
        bad.append(f"{rel}: a Lean file in the pack that is not named Policy.lean")
if not (pack / "templates" / "Policy.lean").exists():
    bad.append("templates/Policy.lean is missing")
# the old wording must not survive: the pack never says the file is submitted as PolicyTemplate.lean, and never quotes the old k <= 6 limit
for w in ("PolicyTemplate.lean`; you", "k ≤ 6 is the supported", "1 < k ≤ 6", "1 < k <= 6", "2 ≤ k ≤ 6"):
    for f in sorted(pack.rglob("*.md")):
        if not str(f.relative_to(pack)).startswith("engine/") and w in f.read_text():
            bad.append(f"{f.relative_to(pack)}: {w!r}")
# no file of the pack (engine data and examples included) names the machine the pack was built on
for f in sorted(pack.rglob("*")):
    if f.is_file() and f.suffix not in {".zip", ".pyc"}:
        text = f.read_text(errors="ignore")
        for w in ("/Users/", "~/Desktop", "/home/joshua"):
            if w in text:
                bad.append(f"{f.relative_to(pack)}: a local path ({w!r})")
# the optional LaTeX summary: a separate file `TableGeneration/Submission/summary.tex`, never a comment inside Policy.lean, and the template is a valid one
template = pack / "examples" / "summary.tex"
if not template.is_file():
    bad.append("examples/summary.tex (the summary template) is missing")
else:
    raw = template.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
        bad.append("examples/summary.tex is not UTF-8")
    if not raw.strip() or len(raw) > 102400:
        bad.append("examples/summary.tex is empty or above the 100 KiB limit")
    if any(c not in "\t\n\r" and ord(c) < 32 or 127 <= ord(c) < 160 for c in text):
        bad.append("examples/summary.tex has a control character")
    for w in ("\\write18", "\\input{", "\\include{", "\\openin", "\\openout", "\\usepackage{minted}", "shell-escape"):
        if w in text:
            bad.append(f"examples/summary.tex uses {w!r}: the template must be self-contained and harmless")
    if "Parallel sequences appendix of Kahanamoku-Meyer and Yao (2024)" not in text:
        bad.append("examples/summary.tex does not cite the tables in the Parallel sequences appendix of Kahanamoku-Meyer and Yao (2024)")
skill = (pack / "SKILL.md").read_text()
for need in ("TableGeneration/Submission/summary.tex", "Optional LaTeX Summary", "100 KiB", "UTF-8", "Nothing compiles, renders or runs it", "a submission needs a policy", "examples/summary.tex"):
    if need not in skill:
        bad.append(f"SKILL.md does not say {need!r}")
for old_way in ("Optional LaTeX Notes", "/-! ... -/", "notes live inside that file", "A separate `.tex`"):
    for f in sorted(pack.rglob("*.md")):
        if not str(f.relative_to(pack)).startswith("engine/") and old_way in f.read_text():
            bad.append(f"{f.relative_to(pack)}: {old_way!r} (the notes are the separate summary.tex now)")
for f in sorted(pack.rglob("*.md")):
    if not str(f.relative_to(pack)).startswith("engine/") and "the only file a pull request may change" in f.read_text():
        bad.append(f"{f.relative_to(pack)}: still says Policy.lean is the only file a pull request may change")
manifest = json.loads((pack / "MANIFEST.json").read_text()) if (pack / "MANIFEST.json").exists() else {"files": []}
if manifest["files"] and "examples/summary.tex" not in {x["path"] for x in manifest["files"]}:
    bad.append("MANIFEST.json does not list examples/summary.tex")
print(f"{len(bad)} stale references")
for b in bad:
    print("  ", b)
sys.exit(1 if bad else 0)
