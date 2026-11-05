"""Get the T count of formally verified tables (for a given error threshold).

    # tables proved by Lean in a built ShorTableOptimization checkout (`lake build` done):
    python -m tcost.tcount --lean <repo> --mode PhaseProduct --k 3 [--k 5 ...] [--n 2048] [--delta 0.01]

    # tables from archived verifier output (operations.json `targets`):
    python -m tcost.tcount --operations results/policies/<id>/operations.json --mode PhaseProduct --k 3

    # a whole width-banded policy:
    python -m tcost.tcount --policy policy.json [--n 2048] [--eps 1e-12]

How tcount uses the given tables (`--recursion`, input of one table or of more tables):
    best      (default) the cheapest policy from the given tables and the schoolbook leaf. At each width,
              tcount uses the given table (or the leaf) that gives the minimum T. If you give more than
              one table, k changes with depth.
    top       the (single) table at only the top level, with schoolbook children
    constant  the (single) table at each level while it shrinks the width (ForShor `constPolicy`)

Error threshold: `--delta` is the total budget for rotation synthesis, divided evenly over the R rotations
(eps = delta / R, the Lean-To-Qualtran convention). Alternatively, `--eps` fixes the precision of each rotation.
T = t + 4*(And + Toffoli + CSwap) + R * round(3.075*log2(1/eps) + 0.86). The costs come from Qualtran. They
are symbolic in n (the output stores the closed form) and evaluated at n.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import tables as T
from .currency import DEFAULT_DELTA, ts_per_rotation_eps
from .lean import LeanOutputError
from .score import DEFAULT_N, score_policy
from .tables import Band, Policy, Table


def policy_for(tables: List[Table], mode: str, recursion: str, n: int, delta: float,
               fixed_tpr: Optional[int] = None) -> Policy:
    tabs = [t for t in tables if t.mode == mode]
    if not tabs:
        raise SystemExit(f"no {mode} table given")
    if recursion == "best":
        from .planner import optimal_policy
        return optimal_policy(tabs, mode, n, delta, name=f"best({','.join(str(t.k) for t in tabs)})",
                              fixed_tpr=fixed_tpr)[0]
    if len(tabs) != 1:
        raise SystemExit(f"--recursion {recursion} takes exactly one table, got {len(tabs)}")
    t = tabs[0]
    if recursion == "constant":
        return Policy.constant(t, name=f"constant(k={t.k})")
    if recursion == "top":
        return Policy(mode, (Band(n, t),), name=f"top(k={t.k})")
    raise SystemExit(f"unknown --recursion {recursion}")


def _unit_interval(text: str) -> float:
    v = float(text)
    if not 0 < v < 1:
        raise argparse.ArgumentTypeError(f"must be in (0, 1), got {text}")
    return v


def _width(text: str) -> int:
    v = int(text)
    if v < 2:
        raise argparse.ArgumentTypeError(f"must be an integer >= 2, got {text}")
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tcost.tcount", description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--lean", help="built ShorTableOptimization checkout: extract the verified generate mode k")
    src.add_argument("--operations", help="archived operations.json (verifier output)")
    src.add_argument("--policy", help="policy file: tcost policy JSON, a {policy}/{policies} wrapper, an archived "
                     "operations.json with a recursion policy, or the verifier's table-generation-tcount.json")
    ap.add_argument("--mode", choices=T.MODES)
    ap.add_argument("--k", type=int, action="append", help="table arity; repeat to feed several tables")
    ap.add_argument("--recursion", choices=("best", "top", "constant"), default="best")
    ap.add_argument("--n", type=_width, default=DEFAULT_N)
    thr = ap.add_mutually_exclusive_group()
    thr.add_argument("--delta", type=_unit_interval, default=None,
                     help=f"total rotation-synthesis budget, 0 < delta < 1 (default {DEFAULT_DELTA})")
    thr.add_argument("--eps", type=_unit_interval, default=None, help="per-rotation synthesis precision, 0 < eps < 1")
    ap.add_argument("--out", help="write the full JSON report here")
    a = ap.parse_args(argv)

    if a.policy:
        try:
            policies = T.load_policies(a.policy)
        except T.TableError as e:
            raise SystemExit(f"{a.policy}: {e}")
        source = {"policy": a.policy}
    else:
        if not a.mode or not a.k:
            ap.error("--mode and at least one --k are required with --lean / --operations")
        wanted = [(a.mode, k) for k in a.k]
        if a.lean:
            # Stop here. Otherwise lake gets and builds the dependencies silently.
            if not (Path(a.lean) / ".lake" / "build").is_dir():
                raise SystemExit(f"{a.lean} is not built. Run `lake exe cache get && lake build TableGeneration` there.")
            from .lean import extract_tables
            try:
                found, run = extract_tables(Path(a.lean), wanted)
            except LeanOutputError as e:
                raise SystemExit(f"Lean extraction output rejected: {e}")
            missing = [w for w in wanted if w not in found]
            if missing:
                print(run["output"][-3000:], file=sys.stderr)
                raise SystemExit(f"Lean extraction failed for {missing} (is the checkout built with `lake build`?)")
            tabs = [Table.from_json(found[w]) for w in wanted]
            source = {"lean": str(Path(a.lean).resolve()),
                      "submission_handles": {f"{m}:{k}": found[(m, k)]["submission_handles"] for m, k in wanted}}
        else:
            d = json.load(open(a.operations))
            by = {(t["mode"], t["k"]): t for t in d["targets"]}
            missing = [w for w in wanted if w not in by]
            if missing:
                raise SystemExit(f"{a.operations} has no table for {missing}")
            tabs = [Table.from_json(by[w]) for w in wanted]
            source = {"operations": a.operations, "policy_id": d.get("policy_id")}
        fixed_tpr = ts_per_rotation_eps(a.eps) if a.eps is not None else None
        policies = [policy_for(tabs, a.mode, a.recursion, a.n, a.delta if a.delta is not None else DEFAULT_DELTA,
                               fixed_tpr)]

    reports = []
    for policy in policies:
        report = score_policy(policy, a.n, a.delta, with_range=True, eps=a.eps)
        report["policy_json"] = policy.to_json()
        report["source"] = source
        c, tb = report["gate_counts"], report["t_breakdown"]
        ladder = " -> ".join(f"{s['width']}:{s['k'] if s['k'] is not None else 'leaf'}" for s in report["ladder"])
        print(f"{report['mode']}  n={report['n']}  T = {report['t_count']:,}  ({policy.name or 'unnamed'})")
        budget = f"delta = {tb['delta']:.3g}" if tb["delta"] is not None else "fixed eps"
        eps_txt = f"{tb['rotation_eps']:.3e}" if tb["rotation_eps"] is not None else "n/a"
        print(f"  rotations {c['rotation']:,} x {tb['ts_per_rotation']} T (eps = {eps_txt}, {budget});  "
              f"And {c['and']:,}  Toffoli {c['toffoli']:,}  T {c['t']:,}")
        print(f"  Clifford {c['clifford']:,}  CNOT {c['cnot']:,}  measurements {c['measurement']:,}")
        print(f"  ladder (width:k) {ladder};  closed form valid for n in {report['closed_form']['valid_n']}")
        reports.append(report)
    if a.out:
        Path(a.out).write_text(json.dumps(reports[0] if len(reports) == 1 else reports, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
