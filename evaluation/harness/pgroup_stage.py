#!/usr/bin/env python3
"""Run an upstream Lean checker script with every STAGE in its own process group (PLAN_v2 Stage 13, task d).

    python harness/pgroup_stage.py <checker.py> [checker args...]

The bug this fixes (Stage 12): the checkers (ShorTableOptimization's `scripts/check_banded_policy.py`, the PTP suite's
`scripts/check_ptp_policy.py`) run each stage with `subprocess.run(cmd, timeout=...)`. On a timeout that kills only the
direct child (`lake`); its `lean` child keeps running as an orphan (PTP7-e1d7b1c6690f: a hung report stage left a
`lean --run` at 99 % CPU for 43 minutes until the coordinator killed it).

The fix, without editing the upstream scripts: this wrapper imports the checker as a module and gives it a `subprocess`
whose `run` starts every call that has a `timeout` (the stages) in a NEW session/process group. On a timeout, and also
after the stage returns, it kills the stage's whole process group and every descendant it can see (a nested `memcap.py`
puts its own child in another group: those are found through the process tree while memcap is alive). Calls without a
timeout (`pgrep`, `cp`) run unchanged. The checker's own logic, records and exit code are untouched.
"""
from __future__ import annotations

import importlib.util
import os
import signal
import subprocess as _sp
import sys
import types
from pathlib import Path
from typing import List, Set

KILLED: List[dict] = []          # what the wrapper killed (written to stderr as one JSON line at exit, for the record)


def descendants(pid: int) -> Set[int]:
    out = _sp.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True).stdout
    kids: dict = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) == 2:
            kids.setdefault(int(f[1]), []).append(int(f[0]))
    seen, todo = set(), [pid]
    while todo:
        p = todo.pop()
        for c in kids.get(p, []):
            if c not in seen:
                seen.add(c)
                todo.append(c)
    return seen


def pgid_members(pgid: int) -> Set[int]:
    out = _sp.run(["ps", "-A", "-o", "pid=,pgid="], capture_output=True, text=True).stdout
    return {int(f[0]) for f in (l.split() for l in out.splitlines()) if len(f) == 2 and int(f[1]) == pgid}


def kill_group(pgid: int, extra: Set[int] = frozenset(), why: str = "") -> int:
    """SIGKILL the process group `pgid` and the pids in `extra` (and their groups). Returns how many were alive."""
    victims = ((pgid_members(pgid) - {pgid}) | set(extra)) - {os.getpid()}
    groups = {pgid}
    for p in list(victims):
        try:
            groups.add(os.getpgid(p))
        except (ProcessLookupError, PermissionError):
            victims.discard(p)
    groups.discard(os.getpgid(0))
    for g in groups:
        try:
            os.killpg(g, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    for p in victims:
        try:
            os.kill(p, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if victims:
        KILLED.append({"pgid": pgid, "pids": sorted(victims), "why": why})
    return len(victims)


def run_in_group(cmd, *, cwd=None, timeout=None, capture_output=False, text=None, check=False, input=None, **kw):
    """`subprocess.run` with the child in its own session; its group (and visible descendants) killed on a timeout
    and swept after it returns."""
    import tempfile
    # Output goes to temporary files, not pipes: an orphan that inherited the stage's stdout would otherwise keep a
    # pipe open and block the read until the timeout.
    fo, fe = (tempfile.TemporaryFile(), tempfile.TemporaryFile()) if capture_output else (None, None)
    if capture_output:
        kw["stdout"], kw["stderr"] = fo, fe

    def read():
        if not capture_output:
            return None, None
        vals = []
        for f in (fo, fe):
            f.seek(0)
            b = f.read()
            f.close()
            vals.append(b.decode(errors="replace") if text else b)
        return tuple(vals)
    p = _sp.Popen(cmd, cwd=cwd, text=text, start_new_session=True,
                  stdin=_sp.PIPE if input is not None else kw.pop("stdin", None), **kw)
    try:
        if input is not None:
            p.stdin.write(input)
            p.stdin.close()
        p.wait(timeout=timeout)
    except _sp.TimeoutExpired:
        kill_group(p.pid, descendants(p.pid) | {p.pid}, why=f"stage timeout {timeout} s: {' '.join(map(str, cmd))[:200]}")
        p.wait()
        out, err = read()
        raise _sp.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    finally:
        # orphans left in the group after the stage returned (the leader has exited and been reaped, so a process whose
        # pid equals the group id would be a new, unrelated session leader: never touched)
        left = pgid_members(p.pid) - {p.pid}
        if left:
            kill_group(p.pid, left, why="sweep after the stage returned")
    out, err = read()
    if check and p.returncode:
        raise _sp.CalledProcessError(p.returncode, cmd, out, err)
    return _sp.CompletedProcess(cmd, p.returncode, out, err)


def shim() -> types.ModuleType:
    """A stand-in for the `subprocess` module: `run` with a timeout goes through `run_in_group`."""
    m = types.ModuleType("subprocess_pgroup")
    m.__dict__.update({k: getattr(_sp, k) for k in dir(_sp) if not k.startswith("__")})

    def run(*args, **kw):
        if kw.get("timeout") is not None:
            return run_in_group(*args, **kw)
        return _sp.run(*args, **kw)
    m.run = run
    return m


def load_checker(path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(f"checker_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.subprocess = shim()
    return mod


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    script = Path(argv[0]).resolve()
    mod = load_checker(script)
    try:
        return int(mod.main(argv[1:]) or 0)
    finally:
        import json
        print(json.dumps({"pgroup_stage_killed": KILLED}), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
