"""Paths — the ForShor tree, the built emitters, frozen schedules, artifacts.

Delegates to the folder-level ``env_paths.py`` (the historical single source of truth) and
adds the ``forshor_emit`` binary. Environment overrides:

    ONECLICK_FORSHOR        ForShor tree
    ONECLICK_LAKE           lake executable
    ONECLICK_PHASE_EMIT     phase_emit binary
    ONECLICK_FORSHOR_EMIT   forshor_emit binary
    ONECLICK_QUALTRAN       frozen Qualtran tree (required when installed outside a checkout)
    ONECLICK_TCOUNT         `Clifford_plus_T_scaling` directory holding the `tcount` package

INSTALL-SAFE (roundtable 2026-10-12, C-11). In a repository checkout ``env_paths.py`` sits beside
the package and stays the single source of truth. Installed elsewhere (a wheel, a fresh venv) it
is absent; the module then resolves every path from the environment variables above and
otherwise from in-checkout defaults, instead of failing at import with FileNotFoundError.
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
    """`env_paths.py` from the checkout, or an equivalent built from the environment."""
    src = PIPE_ROOT / "env_paths.py"
    if src.is_file():
        spec = importlib.util.spec_from_file_location("env_paths", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    return _InstalledPaths()


class _InstalledPaths:
    """The subset of `env_paths` this package reads, for an install without the checkout."""

    def __init__(self):
        env = os.environ
        self.QUALTRAN_TREE = Path(env.get("ONECLICK_QUALTRAN",
                                          PIPE_ROOT / "vendor" / "Qualtran-main" / "Qualtran-main"))
        self.FORSHOR_TREE = Path(env.get("ONECLICK_FORSHOR", PIPE_ROOT / "lean" / "ForShor-main"))
        self.ARTIFACTS = Path(env.get("ONECLICK_ARTIFACTS", PIPE_ROOT / "artifacts"))
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
#: The v2 emitter's source lives at `FastMultiplication/Emit/` in the 2026-09-18 drop -- it
#: moved out from under `ShorVerification/`, where our own retired emitter used to be installed.
EMIT_SRC_DIR: Path = FORSHOR_TREE / "FastMultiplication" / "Emit"

#: Where the v2 tree is BUILT. The reference tree itself holds only its 250 locked source
#: files; `scripts/forshor_ref_lock.py buildtree` clones them to a short root because
#: `lean.exe` is bound by MAX_PATH and three olean targets do not fit at the reference tree's
#: depth (PLAN section 8.8). Override with ONECLICK_FORSHOR_BUILD.
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
    """The built ``forshor_emit`` executable, or None.

    Probes, in order: ``ONECLICK_FORSHOR_EMIT``; the v2 **build clone**
    (``FORSHOR_BUILD_TREE``); then ``FORSHOR_TREE`` itself. The build clone comes first
    deliberately -- after the 2026-09-18 cutover the v2 binary is the one we want, and a stale
    v1 binary may still exist under an older ``FORSHOR_TREE``. Resolving that one silently is
    exactly how the test suite spent a day exercising the retired emitter (PLAN section 8.13).
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
    """Whether the resolved binary is the v2 emitter, by asking it.

    The two share an executable name and differ entirely in CLI and output format, so this
    checks behaviour rather than a path: only the v2 usage text mentions the `shor` subcommand
    and the `--table` flag. ``None`` when no binary resolves.
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
    """Make the legacy ``py/`` and ``qiskit/`` modules importable (for differential tests)."""
    _EP.setup_sys_path()
    for extra in ("skeletons",):
        s = str(PIPE_ROOT / extra)
        if s not in sys.path:
            sys.path.insert(0, s)


def tcount_root() -> Path:
    """The directory holding the `tcount` package (`Clifford_plus_T_scaling/`)."""
    return Path(os.environ.get("ONECLICK_TCOUNT", PIPE_ROOT / "Clifford_plus_T_scaling"))


def tcount_module(name: str = "budget"):
    """Import `tcount.<name>` — the project's Clifford+T rotation cost model.

    Resolution: an installed `tcount` package (pyproject maps it from `Clifford_plus_T_scaling/`),
    else `tcount_root()` on sys.path. A clear error names both when neither works.
    """
    import importlib
    try:
        return importlib.import_module(f"tcount.{name}")
    except ImportError:
        root = tcount_root()
        if not (root / "tcount" / "__init__.py").is_file():
            raise ImportError(
                f"cannot import tcount.{name}: no installed `tcount` package and no "
                f"{root / 'tcount'} (set ONECLICK_TCOUNT to the Clifford_plus_T_scaling directory)")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return importlib.import_module(f"tcount.{name}")
