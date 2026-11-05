#!/usr/bin/env python3
"""Layer discipline for TableGeneration/RecursiveCost/PTP/ (the PTP analogue of ForShor's
scripts/check_layers.py, order check only).

A module may import another module of the PTP folder only if that module comes EARLIER in LAYERS.
Plan*, Instances/* and RecursionWitness are leaf consumers and are exempt as importers. Also
fails on `sorry`, `native_decide` or `axiom` anywhere in the folder (Plan seeds excluded).
Exit 0 on success.
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "TableGeneration/RecursiveCost/PTP"
LAYERS = ["Contract", "Width", "Semantics", "Leaf", "LeafSandwich", "Node", "Census", "Exact",
          "Recursion", "Build", "CensusFull", "ExactNode", "ExactRecursion", "ExactBuild",
          "Threshold", "Assertions", "Main", "Controlled", "Asymptotic", "Unbalanced", "QubitsTight", "StandardModel"]
EXEMPT_DEPS = {"RecursionWitness", "Instances.FaultChallenge"}  # witness modules (outside the root import)
PFX = "TableGeneration.RecursiveCost.PTP."
bad = []
for i, name in enumerate(LAYERS):
    f = ROOT / f"{name}.lean"
    if not f.exists():
        bad.append(f"missing module {name}"); continue
    for m in re.finditer(r"^import\s+" + re.escape(PFX) + r"([A-Za-z0-9_.]+)", f.read_text(), re.M):
        dep = m.group(1)
        if dep not in LAYERS:
            if dep not in EXEMPT_DEPS:
                bad.append(f"{name} imports non-layer PTP module {dep}")
        elif LAYERS.index(dep) >= i:
            bad.append(f"{name} imports {dep}, which is not earlier in the layer order")
for f in ROOT.rglob("*.lean"):
    if "PlanSeeds" in f.parts:
        continue
    t = re.sub(r"/-.*?-/", "", f.read_text(), flags=re.S)
    t = re.sub(r"--.*", "", t)
    for tok in (r"\bsorry\b", r"\bnative_decide\b", r"^axiom\b"):
        if re.search(tok, t, re.M):
            bad.append(f"{f.relative_to(ROOT)}: forbidden token {tok}")
print("\n".join(bad) if bad else f"ok: {len(LAYERS)} layers, no forbidden tokens")
sys.exit(1 if bad else 0)
