#!/usr/bin/env python3
"""Single-source guard for PhaseTripleProduct.

The PTP proofs live in ForShor (`Implementation/PhaseTripleProduct`, namespace `Shor.PTP`). This fork
holds only the PTP planner (`TableGeneration/RecursiveCost/PTPPlan/`), its seeds and the policy-checker
harness. This check fails if the fork starts carrying its own copy again:

  1. no `TableGeneration/RecursiveCost/PTP/` folder (the old duplicate suite) or PTP.lean umbrella;
  2. no Lean file under TableGeneration/ declares the PTP vocabulary itself (`PTPTable`, `PTPPolicy`,
     `PTPBand`, `ptpTightWidth`, `PolicyImpl`, `ptpNode`, ... as a def/structure/theorem; seeds and the
     planner USE them, they do not define them);
  3. no `sorry`, `native_decide` or `axiom` in the planner or its seeds (a seed is a proof);
  4. the modules the checker generates (PTPCheck/PTPSubmitted*.lean) are git-ignored scratch, not tracked.

Exit 0 on success.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RC = ROOT / "TableGeneration" / "RecursiveCost"
PLAN = RC / "PTPPlan"
bad = []

if (RC / "PTP").exists():
    bad.append("TableGeneration/RecursiveCost/PTP/ exists: the PTP proofs live in ForShor now")
if (RC / "PTP.lean").exists():
    bad.append("TableGeneration/RecursiveCost/PTP.lean exists: the PTP umbrella lives in ForShor now")

VOCAB = ["PTPTable", "PTPPolicy", "PTPBand", "ptpTightWidth", "childWidth3", "PolicyImpl", "PolicyImplX", "PolicyImplD",
         "ptpNode", "ptpLeafSandwich", "SplitLayout", "ptpResources", "ptpQubits", "evalPhase", "evalPhaseTight",
         "node_identity", "ptp_gate_full", "ptp_gate_full_exact", "ptp_lowerExact_correct"]
DECL = re.compile(r"^\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+|noncomputable\s+)*"
                  r"(?:theorem|lemma|def|abbrev|structure|class|inductive|instance)\s+([A-Za-z_.0-9']+)")
for f in sorted((ROOT / "TableGeneration").rglob("*.lean")):
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        m = DECL.match(line)
        if m and m.group(1).split(".")[-1] in VOCAB and "PolicyMirror" not in str(f) and "BandedPolicy" not in str(f):
            bad.append(f"{f.relative_to(ROOT)}:{i}: declares `{m.group(1)}`: PTP definitions belong to ForShor (Shor.PTP)")

BANNED = re.compile(r"(?<![A-Za-z0-9_'.])(sorry|native_decide)(?![A-Za-z0-9_'])|^\s*axiom\s", re.M)
for f in sorted(PLAN.rglob("*.lean")) if PLAN.exists() else []:
    text = re.sub(r"/-.*?-/", "", f.read_text(encoding="utf-8"), flags=re.S)
    text = re.sub(r"--[^\n]*", "", text)
    for m in BANNED.finditer(text):
        bad.append(f"{f.relative_to(ROOT)}: forbidden `{m.group(0).strip()}`")

tracked = subprocess.run(["git", "ls-files", "TableGeneration/RecursiveCost/PTPCheck"], cwd=ROOT,
                         capture_output=True, text=True).stdout.split()
for t in tracked:
    if "PTPSubmitted" in t:
        bad.append(f"{t} is tracked: the checker's generated modules must stay git-ignored")

if bad:
    print("check_ptp_single_source: FAIL")
    for b in bad:
        print("  " + b)
    sys.exit(1)
n_seeds = len(list((PLAN / "PlanSeeds").glob("*.lean"))) if PLAN.exists() else 0
print(f"check_ptp_single_source: ok (no PTP definitions in the fork; planner + {n_seeds} seeds checked)")
