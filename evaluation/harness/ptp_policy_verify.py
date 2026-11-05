#!/usr/bin/env python3
"""The PTP policy harness of the site (PLAN_v2 Stage 13): a PhaseTripleProduct banded policy through the PTP suite's
Lean checker at EXACT widths (`ShorTableOptimization-ptp-suite/scripts/check_ptp_policy.py --widths exact`).

    ./run.sh ptp-policy <policy.json> [--install]          # one site PTP policy JSON
    ./run.sh ptp-policy --seeds [S.. ...] [--install]      # the seeded PTP policies, one after another

The build tree is `$PTP_POLICY_BUILD` (default `../ShorTableOptimization-ptp-suite`, a built tree with `.lake`). The
checker writes its generated modules into its own tree, so each run uses a copy-on-write clone in
`.cache/ptp_policy_work` (`cp -cR`, keeps `.lake`); the suite itself is never written to.

What a pass proves (the suite's HEADLINE and HEADLINE_EXACT theorems, kernel-replayed one module per process, axioms
within {propext, Classical.choice, Quot.sound}, statements of `correct`, `correct_wrapped` and `correct_exact` pinned):
the PTP check (every band's table meets the PTP table contract, thresholds decrease), the chain contracts at the target
width under the exact widths, `ladder_exact` (the exact ladder at the target width, by the kernel), `correct`
(evalPhase n φ x = φ·x0·x1·x2 at every n), and `correct_exact` (the register semantics at the exact widths gives φ·xyz on
operands fitting n), `exact_minimal`, `exact_le_scan`. The suite also cross-checks its Python `ptp_exact_width` against
the Lean report. Not proved: anything circuit-level (no PTP circuit exists; the register model is by analogy with PP).

Memory and processes: the whole check runs under the suite's `scripts/memcap.py` at 8 GB; the checker caps each stage at
8 GB itself; it runs under `harness/pgroup_stage.py` (each stage in its own process group, killed on a timeout); it waits
while two or more `lean` processes run, and runs one policy at a time.

`--install` (seeded records only) pins the result in `engine/data/forshor_submitted/seed_exact_runs.json` and the checker's
record in `seed_exact/<id>.json`, then regenerates the seed records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sto_policy_verify as spv  # noqa: E402  (pin, seeds, waiting rule, input conversion)

ROOT = HERE.parent
WORK = ROOT / ".cache" / "ptp_policy_work"
DEFAULT_BUILD = ROOT.parent / "ShorTableOptimization-ptp-suite"
TARGET_WIDTH = 2048
CAP_GB = 8.0
STAGE_TIMEOUT_S = 900
RUN_TIMEOUT_S = 3600
TOOL = "harness/ptp_policy_verify.py"
SOURCES = "TableGeneration/RecursiveCost/PTP"


class HarnessError(Exception):
    pass


def build_dir() -> Path:
    return Path(os.environ.get("PTP_POLICY_BUILD", str(DEFAULT_BUILD))).resolve()


def tree_digest(build: Path) -> str:
    h = hashlib.sha256()
    files = sorted(p for p in (build / SOURCES).rglob("*.lean") if "/Instances/PTPSubmitted" not in str(p))
    files += [build / "scripts" / n for n in ("check_ptp_policy.py", "ptp_policy_json_to_lean.py", "ptp_exact_width.py",
                                             "memcap.py", "PTPPolicyReport.lean")] + [build / "scripts" / "trusted" / "ReplayPrefix.lean"]
    for f in files:
        if f.is_file():
            h.update(str(f.relative_to(build)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def check(policy_path: Path, build: Optional[Path] = None, target_width: int = TARGET_WIDTH,
          stage_timeout: int = STAGE_TIMEOUT_S, run_timeout: int = RUN_TIMEOUT_S, cap_gb: float = CAP_GB) -> Dict[str, Any]:
    build = build or build_dir()
    if not (build / "scripts" / "check_ptp_policy.py").is_file():
        raise HarnessError(f"no PTP suite at {build} (set PTP_POLICY_BUILD)")
    raw = policy_path.read_bytes()
    conv = spv.to_mirror(json.loads(raw))
    if conv["mode"] != "PhaseTripleProduct":
        raise HarnessError(f"the PTP harness takes PhaseTripleProduct policies, got {conv['mode']}")
    conv_bytes = (json.dumps(conv, indent=1) + "\n").encode()
    spv.wait_lean_slot()
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    subprocess.run(["cp", "-cR", str(build), str(WORK)], check=True)
    clone_s = round(time.monotonic() - t0, 1)
    tmp = Path(tempfile.mkdtemp(prefix="ptp_policy_"))
    inp, rec_path, mem_path = tmp / "policy.ptp.json", tmp / "record.json", tmp / "outer.mem.json"
    inp.write_bytes(conv_bytes)
    cmd = [sys.executable, str(WORK / "scripts" / "memcap.py"), "--cap-gb", str(cap_gb), "--timeout", str(run_timeout),
           "--log", str(mem_path), "--",
           sys.executable, str(spv.PGROUP), str(WORK / "scripts" / "check_ptp_policy.py"), str(inp),
           "--target-width", str(target_width), "--widths", "exact", "--out", str(rec_path),
           "--cap-gb", str(cap_gb), "--timeout", str(stage_timeout)]
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=run_timeout + 120, start_new_session=True)
    wall = round(time.monotonic() - t0, 1)
    outer = json.loads(mem_path.read_text()) if mem_path.is_file() and mem_path.stat().st_size else {}
    rec = json.loads(rec_path.read_text()) if rec_path.is_file() and rec_path.stat().st_size else None
    res: Dict[str, Any] = {
        "tool": TOOL, "checker": "ptp-suite", "widths": "exact",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": conv["mode"], "bands": [[b["threshold"], b["k"]] for b in conv["bands"]],
        "target_width": target_width, "site_policy_sha256": hashlib.sha256(raw).hexdigest(),
        "mirror_input_sha256": hashlib.sha256(conv_bytes).hexdigest(),
        "mirror_tree_sha256": tree_digest(build), "mirror_build": str(build),
        "lean_toolchain": (build / "lean-toolchain").read_text().strip() if (build / "lean-toolchain").is_file() else None,
        "cap_gb": cap_gb, "seconds": wall, "clone_seconds": clone_s, "peak_gb": outer.get("peak_gb"),
        "outer_outcome": outer.get("outcome"), "stage_timeout_s": stage_timeout,
        "process_groups": "harness/pgroup_stage.py (each stage in its own process group, killed on timeout)",
    }
    if rec is None:
        res.update({"verdict": "error", "stage": "harness",
                    "reason": f"no record from check_ptp_policy ({outer.get('outcome') or f'exit {p.returncode}'})",
                    "detail_tail": (p.stdout + p.stderr)[-1500:]})
        return res
    rej = rec.get("rejection") or {}
    rep = rec.get("report") or {}
    audit = rec.get("audit") or {}
    res.update({
        "verdict": rec["verdict"], "stage": rej.get("stage"), "reason": rej.get("reason"),
        "stage_seconds": rec.get("seconds", {}),
        "replay": [{"module": m["module"].rsplit(".", 1)[-1], "verdict": m["verdict"], "decls": m.get("decls"),
                    "peak_gb": m.get("peak_gb")} for m in rec.get("replay", [])],
        "ladder": rep.get("ladder"),
        "exact_ladder": rep.get("ladder_exact"),
        "python_exact_ladder": rec.get("python_exact_ladder"),
        "exact_ladder_kernel_proved": bool(audit.get("ladder_exact")),
        "axioms": sorted({a for h in audit.values() for a in h["axioms"]}) if audit else None,
        "statement_pinned": bool(audit),
        "lean_sha256": rec.get("lean_sha256"),
    })
    if str(outer.get("outcome") or "finished").startswith("killed"):
        res["verdict"], res["stage"], res["reason"] = "killed", res["stage"] or "harness", f"killed by memcap: {outer.get('outcome')}"
    res["mirror"] = rec
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("policy", nargs="?", help="a site PTP policy JSON")
    ap.add_argument("--seeds", nargs="*", default=None, help="run seeded PTP records (all, or these ids), one after another")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--target-width", type=int, default=TARGET_WIDTH)
    ap.add_argument("--timeout", type=int, default=STAGE_TIMEOUT_S)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if (a.policy is None) == (a.seeds is None):
        ap.error("give one policy JSON or --seeds")
    paths = [Path(a.policy)] if a.policy else spv.seed_paths(a.seeds, "PhaseTripleProduct")
    rc = 0
    for path in paths:
        sid = spv.seed_id_of(path)
        if a.install and sid is None:
            print(f"{path}: --install takes a seeded record's site_policy.json only", file=sys.stderr)
            return 2
        try:
            r = check(path, target_width=a.target_width, stage_timeout=a.timeout)
        except HarnessError as e:
            print(f"{path}: {e}", file=sys.stderr)
            return 2
        if a.install:
            spv.install(sid, r, "exact")
        if a.out and a.policy:
            Path(a.out).write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n")
        if a.seeds is not None:
            print(spv._line(sid, r), flush=True)
        else:
            print(json.dumps({k: v for k, v in r.items() if k != "mirror"}, indent=1, ensure_ascii=False))
        rc = rc or (0 if r["verdict"] == "accepted" else 1)
    if a.install:
        spv.regenerate()
    return rc


if __name__ == "__main__":
    sys.exit(main())
