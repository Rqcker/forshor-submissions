#!/usr/bin/env python3
"""Lean-only checks on the recursive cost model.

These compare Lean against Lean, and no theorem covers them. They used to live
in the JavaScript mirror's test alongside a Lean/JS differential; the mirror was
deleted in Phase 2 and the differential went with it, but these did not, because
nothing else performs them.

What is kept, and why each one is not implied by a proof:

- `--width-scan`   the array scan against the companion's `Function.update`
                   scan. Nothing proves the two agree.
- `--dense-sparse` the dense and sparse planners agree, over a width list that
                   straddles the 32/64/128 boundaries.
- `--reference`    the binary and transition reference candidates agree with
                   themselves (columns 4 and 5 of each line).
- `--catalog`      the archived catalogue is ordered by arity and covers 2..16.
- default mode     two golden plan lines, so a silent change in the plan format
                   or in the costs is caught.

Negation method (swap 002). The oracle takes `--negation=oneQubit|scratch` (default `oneQubit`, ForShor's
real charge: complement plus Gidney's one-clean-ancilla increment). `scratch` is the pre-swap Cuccaro charge
of ForShor 6d55ad9 and is the REGRESSION ORACLE: under `scratch` the oracle must reproduce byte for byte
`scripts/tests/companion-cost-model.scratch.tsv` (the pinned transcript of 6d55ad9) and the published
plans of RECURSIVE_COST_MODEL.md. Under `oneQubit` it must reproduce `companion-cost-model.tsv`. The
negation numbers and the plan of the transition program are RECOMPUTED here in Python from the closed
forms (X/CNOT/Toffoli by parity), not pasted.

`--allocation` is deliberately NOT here: `canonicalAllocationCost_eq` already
proves it.

Stdlib only, no pytest. Run: python3 scripts/test_cost_model_lean.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ORACLE = "scripts/tests/RecursiveCostOracle.lean"
COST_MODEL_MODULE = "TableGeneration.RecursiveCost.Correctness"

# Checked against the oracle on 2026-10-21. Tab-separated, not spaces.
GOLDEN_PLANS = {
    "8": "8\t254\t1\t2\t0\t2:5:test-binary",
    "16": "16\t668\t3\t14\t0\t2:9:test-binary",
}

DENSE_SPARSE_WIDTHS = [str(w) for w in range(0, 33)] + ["63", "64", "65", "127", "128"]
REFERENCE_WIDTHS = [str(w) for w in range(0, 65)]
# Boundary cases, then every width the five published plans in
# RECURSIVE_COST_MODEL.md actually recurse through. The planner evaluates
# `nextBalancedSignedWidth`; the width theorems are about `nextSignedWidth`.
# Nothing in the type system ties the two together, so this list is the only
# thing holding them in step -- and before the plan widths were added it
# covered just 1024 and 2048 of the 24 widths those plans visit, missing every
# interior one. Regenerate after a table is accepted that moves a plan:
#   lake env lean --run scripts/tests/RecursiveCostOracle.lean \
#     --best-known 1024 2048 4096 8192 16384
WIDTH_SCAN_BOUNDARIES = ["0", "1", "2", "8", "16", "63", "64", "65", "128"]
WIDTH_SCAN_PLAN_WIDTHS = [
    "37", "38", "44", "51", "55", "68", "70", "82", "96", "104", "130",
    "189", "225", "267", "291", "369", "712", "1024", "1225", "1621",
    "2048", "4096", "8192", "16384",
]
WIDTH_SCAN_WIDTHS = WIDTH_SCAN_BOUNDARIES + WIDTH_SCAN_PLAN_WIDTHS


# ForShor `negateXGates` etc. (Framework/Gatecount/ResourceModel.lean), natural-number subtraction.
def nsub(a: int, b: int) -> int:
    return max(a - b, 0)


def negate_counts(w: int, method: str) -> tuple[int, int, int, int]:
    """(X, CNOT, Toffoli, clean ancillas) of a w-bit negation."""
    if method == "oneQubit":
        if w % 2 == 0:
            return nsub(5 * w, 3), nsub(17 * w, 22), nsub(12 * w, 28), 1
        return 5 * w + 1, nsub(17 * w, 5), nsub(10 * w, 18), 1
    # scratch: complement, clean constant-one register, Cuccaro modular adder
    x = w + nsub(2 * w, 6) + 2
    return x, nsub(5 * w, 7), nsub(2 * w, 3), w + 1


def negate_total(w: int, method: str) -> int:
    x, c, t, _ = negate_counts(w, method)
    return x + c + t


def add_total(w: int) -> int:
    return nsub(2 * w, 6) + nsub(5 * w, 7) + nsub(2 * w, 3)


def transition_plan_gates(w: int, child: dict[int, int], method: str) -> int:
    """Gate count of the planner's plan for the one-candidate catalogue `test-transitions` (k = 3: negate,
    two addScaled, one phaseProduct), recomputed from the closed forms. `child[w]` is the Lean-reported
    child width (the width scan is not recomputed here)."""
    k = 3
    base = 5 * w * w
    c = child[w]
    if c >= w:
        return base
    limb = min(w // k, w // k)
    top = nsub(w, (k - 1) * limb)
    alloc = 2 * (nsub(c, top) + nsub(c, top))
    arith = 2 * negate_total(c, method) + 2 * 2 * add_total(c)
    return min(base, arith + alloc + 1 * transition_plan_gates(c, child, method))


def oracle(*args: str, timeout: int = 1800) -> str:
    done = subprocess.run(
        ["lake", "env", "lean", "--run", ORACLE, *args],
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if done.returncode != 0:
        raise AssertionError(
            f"oracle {' '.join(args) or '(default)'} exited {done.returncode}\n"
            f"{done.stdout}\n{done.stderr}"
        )
    return done.stdout


def test_cost_model_builds() -> None:
    done = subprocess.run(
        ["lake", "build", COST_MODEL_MODULE], cwd=REPO, text=True, capture_output=True
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_width_scan_agrees() -> None:
    out = oracle("--width-scan", *WIDTH_SCAN_WIDTHS)
    assert "width scan agreement passed" in out, out


def test_dense_and_sparse_planners_agree() -> None:
    out = oracle("--dense-sparse", *DENSE_SPARSE_WIDTHS)
    assert "dense/sparse agreement passed" in out, out


def test_reference_columns_agree() -> None:
    out = oracle("--reference", *REFERENCE_WIDTHS)
    lines = [line for line in out.splitlines() if line.startswith("reference\t")]
    assert len(lines) == 2 * len(REFERENCE_WIDTHS), f"{len(lines)} reference lines"
    for line in lines:
        fields = line.split("\t")
        assert len(fields) == 5, line
        assert fields[3] == fields[4], f"columns 4 and 5 differ: {line}"


def test_catalog_is_ordered_and_covers_every_arity() -> None:
    out = oracle("--catalog")
    arities = [
        int(line.split("\t")[2])
        for line in out.splitlines()
        if line.startswith("candidate\t")
    ]
    assert arities, out
    assert arities == sorted(arities), f"catalogue is not ordered by arity: {arities}"
    assert set(range(2, 17)) <= set(arities), f"missing arities: {sorted(set(range(2, 17)) - set(arities))}"


def test_golden_plan_lines() -> None:
    out = oracle(*GOLDEN_PLANS)
    printed = [line for line in out.splitlines() if line.strip()]
    assert len(printed) == len(GOLDEN_PLANS), out
    for line, width in zip(printed, GOLDEN_PLANS, strict=True):
        assert line == GOLDEN_PLANS[width], f"\n  got      {line!r}\n  expected {GOLDEN_PLANS[width]!r}"


NEGATION_WIDTHS = [str(w) for w in list(range(0, 40)) + [63, 64, 65, 127, 128, 1024, 2048]]


def test_negation_costs_are_recomputed_from_the_closed_forms() -> None:
    for method in ("oneQubit", "scratch"):
        out = oracle(f"--negation={method}", "--costs", *NEGATION_WIDTHS)
        lines = [line.split("\t") for line in out.splitlines() if line.startswith("cost\t")]
        assert len(lines) == len(NEGATION_WIDTHS)
        for fields in lines:
            w = int(fields[1])
            assert int(fields[2]) == add_total(w), (method, w, fields)
            assert int(fields[3]) == negate_total(w, method), (method, w, fields)
    # the numbers the report quotes
    assert [negate_counts(w, "oneQubit")[:3] for w in (8, 9, 2048)] == [
        (37, 114, 68), (46, 148, 72), (10237, 34794, 24548)]
    assert negate_total(10, "scratch") == 10 * 10 - 14


def test_scratch_reproduces_the_6d55ad9_transcript_and_oneQubit_the_new_one() -> None:
    for method, golden in (("scratch", "companion-cost-model.scratch.tsv"), ("oneQubit", "companion-cost-model.tsv")):
        got = oracle(f"--negation={method}", "--costs", "3", "8", "64", "1024", "2048")
        got += oracle(f"--negation={method}", "--reference", "64", "1024", "2048", "4096", "8192", "16384")
        want = (REPO / "scripts/tests" / golden).read_text()
        assert got == want, f"{method}: oracle output differs from scripts/tests/{golden}"


def test_scratch_reproduces_the_published_plans() -> None:
    """RECURSIVE_COST_MODEL.md's published plans (ForShor 6d55ad9 charge); they are unchanged under
    `oneQubit` too, because no archived table that the planner picks at these widths negates."""
    published = {
        "1024": "1024\t1649629\t3\t147\t213",
        "2048": "2048\t4343539\t4\t726\t689",
        "4096": "4096\t11330660\t4\t1628\t2427",
        "8192": "8192\t28618144\t4\t3016\t4888",
        "16384": "16384\t71324077\t4\t4872\t7984",
    }
    out = oracle("--negation=scratch", "--best-known", *published)
    for line, width in zip(out.splitlines(), published, strict=True):
        assert line.startswith(published[width] + "\t"), line


def test_transition_plans_depend_on_the_method_as_the_closed_forms_say() -> None:
    widths = [16, 64, 256, 2048, 4096]
    # child widths from the Lean scan, for every width the recursion can visit (all <= max width)
    child: dict[int, int] = {}
    out = oracle("--reference", *[str(w) for w in range(0, max(widths) + 1)])
    for line in out.splitlines():
        f = line.split("\t")
        if f[1] == "test-transitions":
            child[int(f[2])] = int(f[3])
    need = widths
    for method in ("oneQubit", "scratch"):
        got = oracle(f"--negation={method}", "--transitions", *[str(w) for w in need])
        for line, w in zip(got.splitlines(), need, strict=True):
            assert int(line.split("\t")[1]) == transition_plan_gates(w, child, method), (method, w, line)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"ok    {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {t.__name__}\n{exc}")
    print(f"\n{len(tests) - failures} passed, {failures} failed")
    sys.exit(1 if failures else 0)
