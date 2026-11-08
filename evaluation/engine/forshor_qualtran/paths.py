"""Paths: the ForShor tree, the built emitters, the frozen schedules, the artifacts.

The module uses the folder-level ``env_paths.py`` (the historical single source of truth), and
it adds the ``forshor_emit`` binary. These environment variables override the defaults:

    ONECLICK_FORSHOR        ForShor tree
    ONECLICK_LAKE           lake executable
    ONECLICK_PHASE_EMIT     phase_emit binary
    ONECLICK_FORSHOR_EMIT   forshor_emit binary
    ONECLICK_QUALTRAN       frozen Qualtran tree (necessary for an install outside a checkout)

INSTALL-SAFE. In a repository checkout, ``env_paths.py`` is next to
the package, and it stays the single source of truth. In an install at a different location (a wheel,
a new venv), the file is not there. Then the module gets each path from the environment variables
above, or else from the defaults of the checkout. It does not fail at import with FileNotFoundError.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional

PKG_ROOT = Path(__file__).resolve().parent
PIPE_ROOT = PKG_ROOT.parent


def _env_paths():
    """Return `env_paths.py` from the checkout, or an equivalent that the environment gives."""
    src = PIPE_ROOT / "env_paths.py"
    if src.is_file():
        spec = importlib.util.spec_from_file_location("env_paths", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    return _InstalledPaths()


class _InstalledPaths:
    """The subset of `env_paths` that this package reads, for an install without the checkout."""

    def __init__(self):
        env = os.environ
        self.QUALTRAN_TREE = Path(env.get("ONECLICK_QUALTRAN",
                                          PIPE_ROOT / "vendor" / "Qualtran-main" / "Qualtran-main"))
        self.FORSHOR_TREE = Path(env.get("ONECLICK_FORSHOR", PIPE_ROOT / "lean" / "ForShor-main"))
        self.ARTIFACTS = Path(env.get("ONECLICK_ARTIFACTS", PIPE_ROOT / "fixtures"))
        self.SCHED_FROZEN = PIPE_ROOT / "schedules" / "frozen_reference"
        self.PY_DIR, self.QISKIT_DIR = PIPE_ROOT / "py", PIPE_ROOT / "qiskit"

    def lake_executable(self):
        import shutil
        cand = os.environ.get("ONECLICK_LAKE")
        if cand:
            return cand
        if shutil.which("lake"):
            return "lake"
        elan = Path.home() / ".elan" / "bin" / "lake"
        return str(elan) if elan.exists() else None

    def setup_sys_path(self):
        for p in (self.QISKIT_DIR, self.PY_DIR, self.QUALTRAN_TREE):
            if p.is_dir() and str(p) not in sys.path:
                sys.path.insert(0, str(p))


_EP = _env_paths()

FORSHOR_TREE: Path = _EP.FORSHOR_TREE
QUALTRAN_TREE: Path = _EP.QUALTRAN_TREE
SCHED_FROZEN: Path = _EP.SCHED_FROZEN
ARTIFACTS: Path = _EP.ARTIFACTS
LEAN_ARTIFACTS: Path = ARTIFACTS / "lean"
ESTIMATE_ARTIFACTS: Path = ARTIFACTS / "estimate"
#: In the 2026-09-18 drop, the source of the v2 emitter is at `FastMultiplication/Emit/`. It moved
#: out of `ShorVerification/`. Our own retired emitter was installed there before.
EMIT_SRC_DIR: Path = FORSHOR_TREE / "FastMultiplication" / "Emit"

#: The location where we BUILD the v2 tree. The reference tree holds only its 250 locked source
#: files. `scripts/forshor_ref_lock.py buildtree` clones them to a short root. The reason: MAX_PATH
#: limits `lean.exe`, and three olean targets do not fit at the depth of the reference tree
#: (PLAN section 8.8). Set ONECLICK_FORSHOR_BUILD to override.
FORSHOR_BUILD_TREE: Path = Path(os.environ.get("ONECLICK_FORSHOR_BUILD", "C:/fsb"))
MIRROR_LEAN_DIR: Path = PIPE_ROOT / "lean"


def lake_executable() -> Optional[str]:
    return _EP.lake_executable()


def _binary(env_var: str, name: str) -> Optional[Path]:
    cand = os.environ.get(env_var)
    if cand:
        p = Path(cand)
        return p if p.is_file() else None
    for suffix in ("", ".exe"):
        p = FORSHOR_TREE / ".lake" / "build" / "bin" / f"{name}{suffix}"
        if p.is_file():
            return p
    return None


def forshor_emit_binary() -> Optional[Path]:
    """Return the built ``forshor_emit`` executable, or None.

    The function looks in this order: ``ONECLICK_FORSHOR_EMIT``; the v2 **build clone**
    (``FORSHOR_BUILD_TREE``); then ``FORSHOR_TREE``. The build clone is first on purpose. After
    the 2026-09-18 cutover, we want the v2 binary, and an old v1 binary can still exist in an older
    ``FORSHOR_TREE``. Because the code found that old binary and gave no message, the test suite
    used the retired emitter for one day (PLAN section 8.13).
    """
    cand = os.environ.get("ONECLICK_FORSHOR_EMIT")
    if cand:
        p = Path(cand)
        return p if p.is_file() else None
    for tree in (FORSHOR_BUILD_TREE, FORSHOR_TREE):
        for suffix in ("", ".exe"):
            p = tree / ".lake" / "build" / "bin" / f"forshor_emit{suffix}"
            if p.is_file():
                return p
    return None


def forshor_emit_is_v2() -> Optional[bool]:
    """Return True if the found binary is the v2 emitter. The function runs the binary to find out.

    The two emitters have the same executable name, but their CLI and output format are fully
    different. Thus this function checks the behaviour, not a path. Only the v2 usage text shows the
    `shor` subcommand and the `--table` flag. Return ``None`` if the function finds no binary.
    """
    b = forshor_emit_binary()
    if b is None:
        return None
    import subprocess
    try:
        r = subprocess.run([str(b)], capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    usage = (r.stdout or "") + (r.stderr or "")
    return "forshor_emit shor <k> <a> <N> <m>" in usage or "--table standard" in usage


def phase_emit_binary() -> Optional[Path]:
    return _binary("ONECLICK_PHASE_EMIT", "phase_emit")


def setup_legacy_sys_path() -> None:
    """Let Python import the legacy ``py/`` and ``qiskit/`` modules (for differential tests)."""
    _EP.setup_sys_path()
    for extra in ("skeletons",):
        s = str(PIPE_ROOT / extra)
        if s not in sys.path:
            sys.path.insert(0, s)
