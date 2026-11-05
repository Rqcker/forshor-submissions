#!/usr/bin/env python3
"""The STO Lean harness of the site (PLAN_v2 Stage 3.1): a submission goes through Lean, then through Python.

    python harness/sto_verify.py verify  <submission> [--sto DIR] [--out DIR] [--install] [--refresh]
    python harness/sto_verify.py preflight <submission> [--sto DIR]

`<submission>` is a directory laid out like the STO repository (`TableGeneration/Submission/Policy.lean` and
helpers under `TableGeneration/Submission/Policy/`), a directory that holds `Policy.lean` (and `Policy/`), or one
`Policy.lean` file. PhaseProduct and PhaseTripleProduct are one harness with two modes: the submission handles any
subset of the 30 configured targets (mode x k = 2..16).

Stages (each fails closed and says which one failed):

  0 intake      Only `Policy.lean` and `Policy/**.lean` may be submitted (the verifier's own allowlist: the
                changed-file check is skipped when there is no git history, so the harness repeats it). No links,
                no non-UTF-8, bounded count and size. The submission gets a content id (sha256 of path + content).
  1 stage       A copy-on-write clone of the trusted STO build tree, with the template reset and the submission
                laid over it. Nothing runs in the trusted tree, and no run sees another run's build products.
  2 preflight   STO's `scripts/verifier.py --preflight-only`: structure, banned tokens, imports, namespace.
  3 lean        STO's `scripts/verifier.py`: syntax gate, `lake build`, axiom check, target metrics. This is the
                acceptance test of the benchmark and the only place that decides "Lean-verified".
  4 extract     The tables, as the verifier scores them (`generate`, `generatedPoints`, `generatePointsInOrder` of
                `Submission.Defs`), through the nonce-protected protocol of `tcost.lean`. The printed output is
                data, not trusted: it is replayed in Python.
  5 python      For each table: `tcost` replay (C1, C3, C4); the exact Toom-Cook identity (PP: the
                Lean-To-Qualtran check; PTP: `site_engine.ptp_identity`); and the metrics that Lean printed must
                equal the metrics replayed in Python and the verifier's own results.
  6 record      `<out>/P<id>/` in the archive format that `site_engine.library` reads (`operations.json`,
                `results.json`), plus `provenance.json`, the verifier artifacts and the source. `--install` copies
                it to `engine/data/sto_submitted/`.

Running this executes the submitter's Lean on this machine, after the verifier's scanner and syntax gate. For a
submission that you do not trust, use a disposable VM or container; the hosted pipeline (PLAN.md) does that in CI.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = ROOT / "engine"
sys.path.insert(0, str(ENGINE))

import site_engine  # noqa: E402,F401  (binds the vendored Qualtran before anything else)
from forshor_qualtran.policy import PolicyError, ToomCookTable  # noqa: E402
from forshor_qualtran.table_catalog import checked, op_from_sto, point_from_sto  # noqa: E402
from site_engine.ptp_identity import verify_ptp_table  # noqa: E402
from tcost import lean as tl  # noqa: E402
from tcost import tables as tt  # noqa: E402

DEFAULT_STO = ROOT / ".cache" / "sto_main_build"
DEFAULT_OUT = ROOT / ".cache" / "harness_runs"
INSTALL_DIR = ENGINE / "data" / "sto_submitted"
POLICY_REL = "TableGeneration/Submission/Policy.lean"
HELPER_REL = "TableGeneration/Submission/Policy/"
MAX_FILES = 200
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 4_000_000
PREFLIGHT_TIMEOUT = 300
VERIFY_TIMEOUT = 3000
EXTRACT_TIMEOUT = 1800
#: What the submitter's Lean may see. Everything else (tokens, keys, cloud credentials) is dropped.
ENV_ALLOW = ("PATH", "HOME", "ELAN_HOME", "LAKE_HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "SHELL")


class HarnessError(Exception):
    """A stage failed. `stage` names it; `detail` is for a human."""

    def __init__(self, stage: str, detail: str):
        super().__init__(f"[{stage}] {detail}")
        self.stage, self.detail = stage, detail


# ---------------------------------------------------------------------------------------------- stage 0
def collect_submission(src: Path) -> Dict[str, bytes]:
    """Return {repo-relative path: bytes} of the submission, after the allowlist and the bounds."""
    src = Path(src)
    if not src.exists():
        raise HarnessError("intake", f"{src} does not exist")
    files: Dict[str, bytes] = {}
    if src.is_file():
        if src.suffix != ".lean":
            raise HarnessError("intake", "a single-file submission must be a .lean file (Policy.lean)")
        found = [(POLICY_REL, src)]
    else:
        base = src / "TableGeneration" / "Submission" if (src / "TableGeneration").is_dir() else src
        found = []
        for p in sorted(base.rglob("*")):
            if p.is_symlink():
                raise HarnessError("intake", f"{p.relative_to(src)} is a symbolic link")
            if p.is_dir():
                continue
            rel = p.relative_to(base).as_posix()
            found.append((f"TableGeneration/Submission/{rel}", p))
    for rel, p in found:
        if not (rel == POLICY_REL or (rel.startswith(HELPER_REL) and rel.endswith(".lean"))):
            raise HarnessError("intake", f"{rel} is not admitted: only Policy.lean and Policy/**.lean may be "
                                         "submitted")
        if p.is_symlink():
            raise HarnessError("intake", f"{rel} is a symbolic link")
        data = p.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise HarnessError("intake", f"{rel} is larger than {MAX_FILE_BYTES} bytes")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise HarnessError("intake", f"{rel} is not valid UTF-8")
        if b"\x00" in data:
            raise HarnessError("intake", f"{rel} contains a NUL byte")
        files[rel] = data
    if POLICY_REL not in files:
        raise HarnessError("intake", f"{POLICY_REL} is missing")
    if len(files) > MAX_FILES:
        raise HarnessError("intake", f"{len(files)} files exceed the limit of {MAX_FILES}")
    if sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise HarnessError("intake", f"the submission is larger than {MAX_TOTAL_BYTES} bytes")
    return files


def content_id(files: Dict[str, bytes]) -> str:
    """sha256 over the sorted (path, sha256(content)) lines: the same files give the same id."""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(f"{rel}\t{hashlib.sha256(files[rel]).hexdigest()}\n".encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------- stage 1
def _clone(src: Path, dst: Path) -> None:
    """Copy-on-write clone where the file system supports it (APFS, btrfs, XFS); a plain copy otherwise."""
    flags = ["-Rc"] if sys.platform == "darwin" else ["-a", "--reflink=auto"]
    r = subprocess.run(["cp", *flags, str(src), str(dst)], capture_output=True, text=True)
    if r.returncode != 0:
        raise HarnessError("stage", f"clone failed: {r.stderr.strip()[-400:]}")


def stage(sto: Path, files: Dict[str, bytes], work: Path) -> Path:
    if not (Path(sto) / "TableGeneration" / "Submission" / "Defs.lean").is_file():
        raise HarnessError("stage", f"{sto} is not an STO checkout (TableGeneration/Submission/Defs.lean missing)")
    if work.exists():
        shutil.rmtree(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    _clone(Path(sto), work)
    (work / POLICY_REL).unlink(missing_ok=True)
    shutil.rmtree(work / HELPER_REL, ignore_errors=True)
    for rel, data in files.items():
        dest = work / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return work


def _env() -> Dict[str, str]:
    env = {k: os.environ[k] for k in ENV_ALLOW if k in os.environ}
    env.setdefault("PATH", "/usr/bin:/bin")
    return env


def _run(cmd: List[str], cwd: Path, timeout: int, log: Optional[Path] = None) -> Tuple[int, str]:
    """Run in its own process group, so a timeout kills the whole `lake`/`lean` tree."""
    p = subprocess.Popen(cmd, cwd=cwd, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        out, code = (out or "") + f"\nTIMEOUT after {timeout} s", 124
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(out, encoding="utf-8")
    return code, out


# ---------------------------------------------------------------------------------------------- stages 2 and 3
def _load_result(art: Path, name: str) -> Optional[Dict[str, Any]]:
    f = art / name
    return json.loads(f.read_text()) if f.is_file() else None


def failed_checks(result: Optional[Dict[str, Any]]) -> List[str]:
    if not result:
        return ["the verifier wrote no result file"]
    return [f"{c['name']}: {str(c.get('details', ''))[:300]}" for c in result.get("checks", [])
            if c.get("status") == "failure"]


def preflight(work: Path, out: Path) -> Dict[str, Any]:
    art = out / "artifacts-preflight"
    code, _ = _run([sys.executable, "scripts/verifier.py", "--out-dir", str(art), "--preflight-only"], work,
                   PREFLIGHT_TIMEOUT, out / "logs" / "preflight.log")
    res = _load_result(art, "table-generation-submission-result.json")
    if code != 0 or not res or res.get("status") != "success":
        raise HarnessError("preflight", "; ".join(failed_checks(res)) or f"verifier exited {code}")
    return res


def lean_verify(work: Path, out: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    art = out / "artifacts"
    code, _ = _run([sys.executable, "scripts/verifier.py", "--out-dir", str(art)], work, VERIFY_TIMEOUT,
                   out / "logs" / "verifier.log")
    res = _load_result(art, "table-generation-submission-result.json")
    if code != 0 or not res or res.get("status") != "success":
        raise HarnessError("lean", "; ".join(failed_checks(res)) or f"verifier exited {code}")
    bench = _load_result(art, "table-generation-results.json")
    if not bench or not bench.get("results"):
        raise HarnessError("lean", "the verifier produced no scored target (a submission must handle at least one)")
    return res, bench


# ---------------------------------------------------------------------------------------------- stage 4
def extract(work: Path, out: Path, handled: List[Tuple[str, int]]) -> List[Dict[str, Any]]:
    """The tables of the handled targets, from the built tree, through the nonce-protected protocol."""
    src = tl.LeanSource(tl.METRICS_IMPORTS)
    ids = {key: tl.add_metrics_block(src, i, *key) for i, key in enumerate(handled)}
    with_dir = out / "logs"
    with_dir.mkdir(parents=True, exist_ok=True)
    res = tl.run_lean(work, src, runner=lambda cmd, cwd, t: dict(zip(("returncode", "output"), _run(cmd, cwd, t))),
                      timeout=EXTRACT_TIMEOUT, name="CollectTables.lean")
    (with_dir / "extract.log").write_text(res["output"], encoding="utf-8")
    if res["returncode"] != 0:
        raise HarnessError("extract", res["output"][-1500:])
    try:
        blocks = tl.parse_blocks(res["output"], src.nonce, src.expected)
    except tl.LeanOutputError as e:
        raise HarnessError("extract", f"the Lean output broke the protocol: {e}")
    rows = []
    for key in handled:
        b = blocks[ids[key]]
        if b["submission_handles"] != "true":
            raise HarnessError("extract", f"{key} is scored but the extraction says it is not handled")
        rows.append({"block": b, "mode": key[0], "k": key[1]})
    return rows


# ---------------------------------------------------------------------------------------------- stage 5
def python_verify(T: tt.Table) -> None:
    """The site's Python verification of one table: the same checks for PP and PTP (see site_engine.ptp_identity)."""
    if T.mode == tt.PAIR:
        checked(ToomCookTable.from_json(f"PP k={T.k} {T.digest[:12]}", {
            "k": T.k, "points": [point_from_sto(tt.point_str(p)) for p in T.point_order],
            "ops": [op_from_sto(tt.op_json(o)) for o in T.ops]}))
    else:
        verify_ptp_table(T)


def check_tables(rows: List[Dict[str, Any]], bench: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Replay, verify and cross-check every extracted table. Return (targets, per-table checks)."""
    weights = bench["benchmark"]["weights"]
    scored = {(r["mode"], int(r["k"])): r for r in bench["results"]}
    targets, checks = [], []
    for row in rows:
        b, mode, k = row["block"], row["mode"], row["k"]
        try:
            t = tl.table_json(b)
            generated = tl.parse_point_list(b["points"], "points")
            T = tt.Table.from_json({k_: v for k_, v in t.items() if k_ != "submission_handles"})
            python_verify(T)
        except (tt.TableError, PolicyError, ValueError, KeyError) as e:
            raise HarnessError("python", f"{mode} k={k}: {e}")
        replay = tl.replay_program_metrics([list(o) for o in T.ops])
        lean_says = {m: int(b[m]) for m in replay}
        if replay != lean_says:
            raise HarnessError("python", f"{mode} k={k}: Lean printed {lean_says}, Python replayed {replay}")
        s = scored[(mode, k)]
        if any(int(s[m]) != replay[m] for m in replay):
            raise HarnessError("python", f"{mode} k={k}: the verifier's result {s} differs from the replay {replay}")
        weighted = (weights["arithmetic_operation_count"] * replay["arithmetic_operation_count"]
                    + weights["parallel_phase_product_layer_count"] * replay["parallel_phase_product_layer_count"])
        if int(s["weighted_cost"]) != weighted:
            raise HarnessError("python", f"{mode} k={k}: weighted cost {s['weighted_cost']} != {weighted}")
        targets.append({"mode": mode, "k": k, "generated_points": generated,
                        "point_order": t["point_order"], "operations": t["operations"]})
        checks.append({"mode": mode, "k": k, "digest": T.digest, "tcost_replay": True,
                       "toom_cook_identity": True, "lean_metrics_equal_replay": True, "weighted_cost": weighted})
    return targets, checks


# ---------------------------------------------------------------------------------------------- stage 6
def _version(cmd: List[str], cwd: Path) -> str:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""


def _tree_digest(sto: Path) -> str:
    """Identify the trusted tree: hash of the verifier, the protected files and the manifest."""
    h = hashlib.sha256()
    for rel in ("scripts/verifier.py", "leaderboard/config.json", "lake-manifest.json", "lean-toolchain",
                "TableGeneration/Submission/Defs.lean", "TableGeneration/Submission/Correctness.lean"):
        h.update(rel.encode() + (sto / rel).read_bytes())
    return h.hexdigest()


def record(out: Path, files: Dict[str, bytes], cid: str, sto: Path, res: Dict[str, Any], bench: Dict[str, Any],
           targets: List[Dict[str, Any]], checks: List[Dict[str, Any]], seconds: Dict[str, float]) -> Path:
    pid = f"P{cid[:12]}"
    d = out / pid
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    (d / "operations.json").write_text(json.dumps(
        {"policy_id": pid, "source": f"Lean (STO verifier; content sha256 {cid})", "targets": targets}, indent=1))
    results = {"benchmark": bench["benchmark"], "results": bench["results"], "schema_version": 2,
               "submission": {"created_at": now, "head_ref": "local", "pr_number": "", "repository": "local",
                              "head_sha": cid, "id_kind": "content-sha256"}}
    (d / "results.json").write_text(json.dumps(results, indent=1, sort_keys=True))
    manifest = json.loads((sto / "lake-manifest.json").read_text())
    prov = {
        "schema": "sto-site-harness/1", "id": pid, "content_sha256": cid, "created_at": now,
        "trust": {"lean_verified": True, "verifier": "STO scripts/verifier.py", "python_verified": True,
                  "scope": "STO submission contract: the accepted axioms are propext alone"},
        "sto_tree_sha256": _tree_digest(sto),
        "lean": _version(["lean", "--version"], sto),
        "lake_manifest": {p["name"]: p["rev"] for p in manifest.get("packages", [])},
        "verifier_checks": [{"name": c["name"], "status": c["status"]} for c in res.get("checks", [])],
        "targets": [{"mode": t["mode"], "k": t["k"]} for t in targets],
        "tables": checks,
        "files": {rel: hashlib.sha256(data).hexdigest() for rel, data in sorted(files.items())},
        "seconds": seconds,
    }
    (d / "provenance.json").write_text(json.dumps(prov, indent=1))
    src = d / "source"
    shutil.rmtree(src, ignore_errors=True)
    for rel, data in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data)
    for name in ("artifacts", "logs"):
        if (out / name).is_dir():
            shutil.rmtree(d / name, ignore_errors=True)
            shutil.copytree(out / name, d / name)
    return d


def install(d: Path, dest: Path = INSTALL_DIR) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / d.name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir()
    for f in ("operations.json", "results.json", "provenance.json"):
        shutil.copy2(d / f, target / f)
    return target


# ---------------------------------------------------------------------------------------------- driver
def run(submission: Path, sto: Path = DEFAULT_STO, out_root: Path = DEFAULT_OUT, keep_work: bool = False,
        preflight_only: bool = False) -> Dict[str, Any]:
    """Run all stages. Return a summary dict; raise HarnessError at the first failing stage."""
    t0 = time.time()
    secs: Dict[str, float] = {}
    files = collect_submission(submission)
    cid = content_id(files)
    out = out_root / f"run-{cid[:12]}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out_root / f"work-{cid[:12]}"
    try:
        stage(sto, files, work)
        secs["stage"] = round(time.time() - t0, 1)
        t = time.time()
        preflight(work, out)
        secs["preflight"] = round(time.time() - t, 1)
        if preflight_only:
            return {"id": f"P{cid[:12]}", "status": "preflight-ok", "seconds": secs}
        t = time.time()
        res, bench = lean_verify(work, out)
        secs["lean"] = round(time.time() - t, 1)
        t = time.time()
        handled = [(r["mode"], int(r["k"])) for r in bench["results"]]
        rows = extract(work, out, handled)
        secs["extract"] = round(time.time() - t, 1)
        t = time.time()
        targets, checks = check_tables(rows, bench)
        secs["python"] = round(time.time() - t, 1)
        d = record(out, files, cid, Path(sto), res, bench, targets, checks, secs)
        return {"id": d.name, "status": "verified", "dir": str(d), "targets": [(t["mode"], t["k"]) for t in targets],
                "seconds": secs}
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("verify", "preflight"):
        p = sub.add_parser(name)
        p.add_argument("submission", type=Path)
        p.add_argument("--sto", type=Path, default=Path(os.environ.get("STO_BUILD", DEFAULT_STO)))
        p.add_argument("--out", type=Path, default=DEFAULT_OUT)
        p.add_argument("--keep-work", action="store_true", help="keep the staged tree for debugging")
        if name == "verify":
            p.add_argument("--install", action="store_true", help="copy the record to engine/data/sto_submitted/")
            p.add_argument("--refresh", action="store_true",
                           help="with --install: rebuild the frontier caches (a few minutes)")
    a = ap.parse_args(argv)
    try:
        s = run(a.submission, a.sto, a.out, a.keep_work, preflight_only=a.cmd == "preflight")
    except HarnessError as e:
        print(f"REJECTED at stage '{e.stage}': {e.detail}", file=sys.stderr)
        return 1
    print(json.dumps(s, indent=1))
    if a.cmd == "verify" and getattr(a, "install", False):
        print(f"installed -> {install(Path(s['dir']))}")
        if a.refresh:
            for mod in ("site_engine.precompute", "site_engine.full_frontier"):
                subprocess.run([sys.executable, "-m", mod], cwd=ENGINE, check=False)
        else:
            print("caches are stale until: ./run.sh precompute && (cd engine && python -m site_engine.full_frontier)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
