#!/usr/bin/env python3
"""The STO policy-mirror harness of the site (PLAN_v2 Stage 11, "STO mirror"): a banded policy, PP or PTP, through the
Lean mirror `TableGeneration.RecursiveCost.BandedPolicy` of ShorTableOptimization (`scripts/check_banded_policy.py`).

    ./run.sh sto-policy <policy.json> [--install]          # one site policy JSON (the "Download policy JSON" format)
    ./run.sh sto-policy --seeds [S.. ...] [--install]      # the seeded planner policies, one after another
    ... [--widths exact|scan]                              # default exact (Stage 13); scan = the Stage 11 runs

Stage 13 (2026-10-28): the PP and PTP boards price at EXACT widths, so the default is `--widths exact`
(check_banded_policy.py --widths exact of the LIVE mirror: it also proves `correct_tight`, the register semantics at the
exact widths, and `exact_ladder`, the exact ladder at the target width, by the kernel). The seeds pin into
`seed_exact_runs.json` / `seed_exact/` (the Stage 11 scan pin `seed_sto_mirror_runs.json` is kept as history). PTP
seeds go through the PTP suite instead (`harness/ptp_policy_verify.py`, `./run.sh ptp-policy`).

Process groups (Stage 13, task d): the checker runs under `harness/pgroup_stage.py`, which starts each of its stages in
its own process group and kills the group on a stage timeout (before, a timeout killed `lake` and left its `lean`).
Memory (Stage 13): memcap 8 GB on the whole check and on each replay; before starting it WAITS while two or more `lean`
processes run (the shared-machine rule), and one run at a time.

The build tree is `$STO_POLICY_BUILD` (default `../ShorTableOptimization-policy-mirror`, next to this package).

What a pass proves (and bounds the trust wording; the mirror's inquiry/PROOF_AUDIT.md): the kernel checks `policy.check`
(every band's table meets STO's table contract on its consumed-order points, thresholds strictly decrease) and the
theorem `correct : ∀ n φ xs, policy.evalPhase n φ xs = φ · ∏ xs` (PP: φab; PTP: φabc), replayed per module by the
trusted kernel replay, axioms within {propext, Classical.choice, Quot.sound}, statement pinned. That is the ALGEBRA of the
recursive evaluation at every width. It does NOT prove child-width / register-capacity soundness (the mirror's open O1),
it is not circuit-level (the mirror's ForShor bridge is data-level and has had no independent refute pass), and PTP is
defined by analogy with PP (ForShor has no PTP policy). It is weaker than ForShor's `ShorPolicySubmission`
("ForShor policy-verified") and the site never shows the two as the same thing.

Memory: ONE Lean process at a time. The whole check runs under the mirror's `scripts/memcap.py` (10 GB on the process
tree, resident + compressed), and `check_banded_policy.py` replays each module in its own capped process (never several
modules in one process: that grew to 58 GB once). The site's runs are serial by construction.

Input conversion. The site policy JSON (`tcost.tables.Policy.to_json`) already has the mirror's band shape
({"mode", "bands": [{"threshold", "k", "point_order", "operations"}]}); this harness copies those fields only (drops
names, per-band modes and anything else) and records the sha256 of both documents.

`--install` (seeded records only) pins the result in `engine/data/forshor_submitted/seed_sto_mirror_runs.json` and the
mirror's record in `seed_sto_mirror/<id>.json`, then regenerates the seed records (`seed_policies` reads only the pin,
so its output stays deterministic).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = ROOT / "engine"
SEEDS = ENGINE / "data" / "forshor_submitted"
PIN = SEEDS / "seed_sto_mirror_runs.json"            # Stage 11, scanned widths (history)
EVIDENCE = SEEDS / "seed_sto_mirror"
PIN_EXACT = SEEDS / "seed_exact_runs.json"            # Stage 13, exact widths (PP: this harness; PTP: ptp_policy_verify)
EVIDENCE_EXACT = SEEDS / "seed_exact"
PGROUP = HERE / "pgroup_stage.py"
WORK = ROOT / ".cache" / "sto_policy_work"
DEFAULT_BUILD = ROOT.parent / "ShorTableOptimization-policy-mirror"
TARGET_WIDTH = 2048
CAP_GB = 8.0                                    # memcap.py, per process tree (the global watchdog kills at 12 GB)
STAGE_TIMEOUT_S = 900                           # check_banded_policy.py --timeout (per stage)
RUN_TIMEOUT_S = 3600                            # the whole check, outer memcap
MIRROR_SOURCES = "TableGeneration/RecursiveCost/BandedPolicy"
TOOL = "harness/sto_policy_verify.py"


class HarnessError(Exception):
    pass


def build_dir() -> Path:
    return Path(os.environ.get("STO_POLICY_BUILD", str(DEFAULT_BUILD))).resolve()


def to_mirror(doc: Dict[str, Any]) -> Dict[str, Any]:
    """A site policy JSON (or {"policies": [{"policy": ...}]} with one policy) in the mirror's input shape."""
    if "bands" not in doc and "policies" in doc:
        ps = doc["policies"]
        if len(ps) != 1:
            raise HarnessError(f"expected one policy, found {len(ps)}")
        doc = ps[0].get("policy", ps[0])
    if "bands" not in doc and "policy" in doc:
        doc = doc["policy"]
    mode = doc.get("mode")
    if mode not in ("PhaseProduct", "PhaseTripleProduct"):
        raise HarnessError(f"bad mode {mode!r}")
    bands = []
    for i, b in enumerate(doc.get("bands", [])):
        if b.get("mode", mode) != mode:
            raise HarnessError(f"band {i} mode {b.get('mode')!r} differs from policy mode {mode!r}")
        missing = [k for k in ("threshold", "k", "point_order", "operations") if k not in b]
        if missing:
            raise HarnessError(f"band {i} is missing {missing}")
        bands.append({"threshold": b["threshold"], "k": b["k"], "point_order": b["point_order"], "operations": b["operations"]})
    if not bands:
        raise HarnessError("policy has no bands")
    return {"name": str(doc.get("name", ""))[:200], "mode": mode, "bands": bands}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tree_digest(build: Path) -> str:
    """sha256 over the mirror's Lean sources and the harness scripts (what a result depends on, besides Lean/Mathlib)."""
    h = hashlib.sha256()
    files = sorted((build / MIRROR_SOURCES).rglob("*.lean")) + [build / MIRROR_SOURCES.replace("/BandedPolicy", "/BandedPolicy.lean")]
    files += [build / "scripts" / n for n in ("check_banded_policy.py", "policy_json_to_lean.py", "memcap.py",
                                             "BandedPolicyReport.lean")] + [build / "scripts" / "trusted" / "ReplayPrefix.lean"]
    for f in files:
        if f.is_file() and "/Instances/Submitted" not in str(f):
            h.update(str(f.relative_to(build)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def wait_lean_slot(limit: int = 2, poll: float = 10.0) -> None:
    """The machine rule: do not start while `limit` or more `lean` processes run."""
    while len(subprocess.run(["pgrep", "-x", "lean"], capture_output=True, text=True).stdout.split()) >= limit:
        time.sleep(poll)


def other_lean() -> List[str]:
    """Lean/lake processes already running (the site runs one at a time)."""
    out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True).stdout
    return [l.strip() for l in out.splitlines() if l.split() and l.split()[-1].rsplit("/", 1)[-1] in ("lean", "lake")]


def check(policy_path: Path, build: Optional[Path] = None, target_width: int = TARGET_WIDTH,
          stage_timeout: int = STAGE_TIMEOUT_S, run_timeout: int = RUN_TIMEOUT_S, cap_gb: float = CAP_GB,
          workdir: Optional[Path] = None, guard_busy: bool = True, widths: str = "scan") -> Dict[str, Any]:
    """One policy through the mirror, under memcap. Returns the site's summary; the mirror's record is under "mirror".
    `guard_busy=False` skips the "no other Lean process" refusal; the caller then applies its own rule (the per-table
    harness waits while two or more `lean` processes run, the rule for a machine shared with other Lean agents)."""
    build = build or build_dir()
    if not (build / "scripts" / "check_banded_policy.py").is_file():
        raise HarnessError(f"no STO policy mirror at {build} (set STO_POLICY_BUILD)")
    if guard_busy:
        wait_lean_slot()
    raw = policy_path.read_bytes()
    conv = to_mirror(json.loads(raw))
    conv_bytes = (json.dumps(conv, indent=1) + "\n").encode()
    work = workdir or WORK
    work.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="sto_policy_"))
    inp, rec_path, mem_path = tmp / "policy.mirror.json", tmp / "record.json", tmp / "outer.mem.json"
    inp.write_bytes(conv_bytes)
    cmd = [sys.executable, str(build / "scripts" / "memcap.py"), "--cap-gb", str(cap_gb), "--timeout", str(run_timeout),
           "--log", str(mem_path), "--",
           sys.executable, str(PGROUP), str(build / "scripts" / "check_banded_policy.py"), str(inp),
           "--target-width", str(target_width),
           "--workdir", str(work), "--out", str(rec_path), "--cap-gb", str(cap_gb), "--timeout", str(stage_timeout)] + \
        (["--widths", widths] if widths != "scan" else [])
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=build, capture_output=True, text=True, timeout=run_timeout + 120, start_new_session=True)
    wall = round(time.monotonic() - t0, 1)
    outer = json.loads(mem_path.read_text()) if mem_path.is_file() and mem_path.stat().st_size else {}
    rec = json.loads(rec_path.read_text()) if rec_path.is_file() and rec_path.stat().st_size else None
    res: Dict[str, Any] = {
        "tool": TOOL, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": conv["mode"], "bands": [[b["threshold"], b["k"]] for b in conv["bands"]],
        "target_width": target_width, "site_policy_sha256": _sha(raw), "mirror_input_sha256": _sha(conv_bytes),
        "mirror_tree_sha256": tree_digest(build), "mirror_build": str(build),
        "lean_toolchain": (build / "lean-toolchain").read_text().strip() if (build / "lean-toolchain").is_file() else None,
        "cap_gb": cap_gb, "seconds": wall, "peak_gb": outer.get("peak_gb"), "outer_outcome": outer.get("outcome"),
        "widths": widths, "checker": "sto-mirror", "stage_timeout_s": stage_timeout,
        "process_groups": "harness/pgroup_stage.py (each stage in its own process group, killed on timeout)",
    }
    if rec is None:
        why = outer.get("outcome") or f"exit {p.returncode}"
        res.update({"verdict": "error", "stage": "harness", "reason": f"no record from check_banded_policy ({why})",
                    "detail_tail": (p.stdout + p.stderr)[-1500:]})
        return res
    rej = rec.get("rejection") or {}
    res.update({
        "verdict": rec["verdict"], "stage": rej.get("stage"), "reason": rej.get("reason"),
        "stage_seconds": rec.get("seconds", {}),
        "replay": [{"module": m["module"].rsplit(".", 1)[-1], "verdict": m["verdict"], "decls": m.get("decls"),
                    "peak_gb": m.get("peak_gb")} for m in (rec.get("replay") or {}).get("modules", [])],
        "ladder": (rec.get("report") or {}).get("ladder"),
        "exact_ladder": rec.get("exact_ladder"),
        "exact_ladder_kernel_proved": bool((rec.get("audit") or {}).get("exact_ladder")) if widths == "exact" else None,
        "axioms": sorted({a for h in (rec.get("audit") or {}).values() for a in h["axioms"]}) if rec.get("audit") else None,
        "statement_pinned": bool(rec.get("audit")),
        "lean_sha256": rec.get("lean_sha256"),
    })
    if str(outer.get("outcome") or "finished").startswith("killed"):
        # the outer cap/timeout stopped it: a site kill, not a Lean verdict
        res["verdict"], res["stage"], res["reason"] = "killed", res["stage"] or "harness", f"killed by memcap: {outer.get('outcome')}"
    res["mirror"] = rec
    return res


# ---------------------------------------------------------------------------------------------- seeds and the pin
def _pin_paths(widths: str):
    return (PIN_EXACT, EVIDENCE_EXACT) if widths == "exact" else (PIN, EVIDENCE)


def load_pin(widths: str = "scan") -> Dict[str, Any]:
    pin, _ = _pin_paths(widths)
    return json.loads(pin.read_text()) if pin.is_file() else {"runs": {}}


def seed_id_of(path: Path) -> Optional[str]:
    path = path.resolve()
    if path.name == "site_policy.json" and path.parent.parent == SEEDS.resolve():
        r = json.loads((path.parent / "record.json").read_text())
        return r["id"] if r.get("seeded_from_planner") else None
    return None


#: Fields of a run that change from one run to the next without changing its verdict (timings, dates, memory). The
#: pin keeps them, but `deterministic_view` drops them, so a test can compare pins across re-runs.
VOLATILE = ("checked_at", "seconds", "peak_gb", "stage_seconds", "replay", "outer_outcome")


def install(sid: str, res: Dict[str, Any], widths: str = "scan") -> None:
    pin_path, evidence = _pin_paths(widths)
    evidence.mkdir(parents=True, exist_ok=True)
    rec = res.get("mirror")
    if rec is not None:
        (evidence / f"{sid}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    pin = load_pin(widths)
    pin["runs"][sid] = {"id": sid, **{k: v for k, v in res.items() if k != "mirror"},
                        "evidence": f"{evidence.name}/{sid}.json" if rec is not None else None,
                        "command": f"./run.sh {'sto-policy' if res.get('checker', 'sto-mirror') == 'sto-mirror' else 'ptp-policy'} "
                                   f"engine/data/forshor_submitted/{sid}/site_policy.json --install"
                                   + (" --widths scan" if widths == "scan" and res.get("checker", "sto-mirror") == "sto-mirror" else "")}
    pin["runs"] = dict(sorted(pin["runs"].items()))
    if widths == "exact":
        pin["note"] = ("Stage 13 (2026-10-28): Lean runs of the re-planned seeds at EXACT widths. PP: harness/sto_policy_verify.py "
                       "-> ShorTableOptimization-policy-mirror scripts/check_banded_policy.py --widths exact (live mirror); "
                       "PTP: harness/ptp_policy_verify.py -> ShorTableOptimization-ptp-suite scripts/check_ptp_policy.py "
                       "--widths exact. verdict accepted = every stage passed (kernel check, kernel replay per module, "
                       "axioms within {propext, Classical.choice, Quot.sound}, statements pinned) and the exact ladder at "
                       "2048 was proved by the kernel. killed/error = stopped by the site, not a Lean verdict. Timings and "
                       "memory vary between runs; the verdict, shas and ladders are deterministic. The records are written "
                       "by seed_policies.")
        pin_path.write_text(json.dumps(pin, indent=1, ensure_ascii=False) + "\n")
        return
    pin["note"] = ("Outcomes of harness/sto_policy_verify.py (ShorTableOptimization policy mirror, "
                   "scripts/check_banded_policy.py) on the seeded site policies. verdict accepted = the kernel checked "
                   "policy.check and chain_contracts at the target width, `correct` (evalPhase n φ xs = φ·∏xs at every n) "
                   "was replayed per module, axioms within {propext, Classical.choice, Quot.sound}, statement pinned. "
                   "Algebra only: no child-width/register-capacity soundness (mirror open O1), not circuit-level, PTP by "
                   "analogy. killed/error = stopped by the site, not a Lean verdict. The records are written by seed_policies.")
    pin_path.write_text(json.dumps(pin, indent=1, ensure_ascii=False) + "\n")


def regenerate() -> None:
    sys.path.insert(0, str(ENGINE))
    from site_engine import seed_policies as sp
    sp.write(sp.generate())


def seed_paths(ids: List[str], mode: Optional[str] = None) -> List[Path]:
    if ids:
        return [SEEDS / s / "site_policy.json" for s in ids]
    out = []
    for d in sorted(SEEDS.glob("S*")):
        f = d / "record.json"
        if f.is_file():
            r = json.loads(f.read_text())
            if r.get("seeded_from_planner") and (mode is None or r.get("mode") == mode):
                out.append(d / "site_policy.json")
    return out


def _line(sid: str, r: Dict[str, Any]) -> str:
    why = f" [{r['stage']}: {r['reason']}]" if r.get("verdict") != "accepted" else ""
    return f"{sid} {r.get('mode')} {r['verdict']}{why} {r['seconds']} s peak {r.get('peak_gb')} GB"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("policy", nargs="?", help="a site policy JSON")
    ap.add_argument("--seeds", nargs="*", default=None, help="run seeded records (all, or these ids), one after another")
    ap.add_argument("--install", action="store_true", help="pin the result of a seeded record and regenerate the records")
    ap.add_argument("--target-width", type=int, default=TARGET_WIDTH)
    ap.add_argument("--timeout", type=int, default=STAGE_TIMEOUT_S)
    ap.add_argument("--widths", choices=("exact", "scan"), default="exact",
                    help="exact (default, Stage 13: the boards' widths) or scan (the Stage 11 runs)")
    ap.add_argument("--out", help="write the summary JSON here (single policy)")
    a = ap.parse_args(argv)
    if (a.policy is None) == (a.seeds is None):
        ap.error("give one policy JSON or --seeds")
    # PTP seeds at exact widths go through the PTP suite (./run.sh ptp-policy); the mirror keeps PP here
    paths = [Path(a.policy)] if a.policy else seed_paths(a.seeds, "PhaseProduct" if a.widths == "exact" else None)
    rc = 0
    for path in paths:
        sid = seed_id_of(path)
        if a.install and sid is None:
            print(f"{path}: --install takes a seeded record's site_policy.json only (engine/data/forshor_submitted/S*/)",
                  file=sys.stderr)
            return 2
        try:
            r = check(path, target_width=a.target_width, stage_timeout=a.timeout, widths=a.widths)
        except HarnessError as e:
            print(f"{path}: {e}", file=sys.stderr)
            return 2
        if a.install:
            install(sid, r, a.widths)
        if a.out and a.policy:
            Path(a.out).write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n")
        if a.seeds is not None:
            print(_line(sid, r), flush=True)
        else:
            print(json.dumps({k: v for k, v in r.items() if k != "mirror"}, indent=1, ensure_ascii=False))
        rc = rc or (0 if r["verdict"] == "accepted" else 1)
    if a.install:
        regenerate()
    return rc


if __name__ == "__main__":
    sys.exit(main())
