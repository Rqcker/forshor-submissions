#!/usr/bin/env python3
"""env_paths.py — machine paths and sys.path setup for oneclick_pipeline.

Single source of truth for where the frozen Qualtran tree, the ForShor Lean tree, and
the `lake` executable live. Overridable via environment variables; defaults resolve
relative to this folder's position on disk (no machine-specific paths), so the same
file works unchanged on any machine or sandbox mount.

    ONECLICK_QUALTRAN  path to Qualtran-main/Qualtran-main (frozen 0.8.0.dev0 tree)
    ONECLICK_FORSHOR   path to ForShor-main/ForShor-main (built Lean tree)
    ONECLICK_LAKE      path to the lake executable (default: "lake" on PATH,
                       falling back to ~/.elan/bin/lake if that exists)
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PIPE_ROOT = Path(__file__).resolve().parent
PY_DIR = PIPE_ROOT / "py"
QISKIT_DIR = PIPE_ROOT / "qiskit"
SCHED_LIVE = PIPE_ROOT / "schedules" / "live"
SCHED_FROZEN = PIPE_ROOT / "schedules" / "frozen_reference"
# Override (2026-09-10, docs/history/RESULT_IMPORT_REGRESSIONS_AND_FIXES.2026-09-10.md §4): a real
# subprocess that shouldn't write into the shared artifacts/lean/ cache (e.g. a pytest test spawning
# `python -m forshor_qualtran.cli emit` at a small, non-representative scale) can point itself at an
# isolated directory via ONECLICK_ARTIFACTS instead -- monkeypatch.setattr doesn't reach a child
# process's own module state, so this is the subprocess-safe equivalent of that in-process pattern.
ARTIFACTS = Path(os.environ.get("ONECLICK_ARTIFACTS", str(PIPE_ROOT / "artifacts")))
CURVES = PIPE_ROOT / "curves"

# Vault layout: <vault>/Qualtran-main/Qualtran-main with PIPE_ROOT at
# <vault>/Cryptography/shor_qualtran/oneclick_pipeline (a full checkout) or
# <mnt>/shor_qualtran/oneclick_pipeline (sandbox mounts) — probe both depths.
def _default_qualtran() -> Path:
    # VENDORED FIRST. `forshor_qualtran/__init__.py` refuses to run against a pip-installed
    # qualtran -- every cost number here is pinned to one frozen revision, and a different one
    # would silently change every figure. That check is right, but it also means a clone of this
    # repository cannot run unless the frozen tree is somewhere on disk. Probing an in-repo
    # `vendor/Qualtran-main/Qualtran-main` first makes the repository standalone; when it is
    # absent (the working tree has no `vendor/`) this falls through to the vault layout below and
    # nothing changes.
    vendored = PIPE_ROOT / "vendor" / "Qualtran-main" / "Qualtran-main"
    if (vendored / "qualtran").is_dir():
        return vendored
    for anc in (PIPE_ROOT.parents[2], PIPE_ROOT.parents[1]):
        cand = anc / "Qualtran-main" / "Qualtran-main"
        if (cand / "qualtran").is_dir():
            return cand
    return PIPE_ROOT.parents[2] / "Qualtran-main" / "Qualtran-main"


QUALTRAN_TREE = Path(os.environ.get("ONECLICK_QUALTRAN", _default_qualtran()))


# ForShor tree: the VENDORED in-folder copy (standalone-port layout, 2026-09-01) is
# preferred; the sibling vault layout (shor_qualtran/lean/...) is the fallback so the
# in-vault install keeps working unchanged. A `leanforshor`/`leanforshor_copy` tree
# checked out beside `oneclick_pipeline` (i.e. at PIPE_ROOT.parent — the "same level of
# file path") is also probed, for a portable, non-vault checkout. ONECLICK_FORSHOR
# overrides all of these.
#: The v2 emitter's source directory, relative to a tree root. Its presence is what
#: distinguishes a v2 drop from the retired v1 tree.
EMIT_DIR_REL = Path("FastMultiplication/Emit")


def _forshor_reference_drop() -> Path | None:
    """The collaborator's read-only reference drop, if one is present.

    After the 2026-09-18 cutover this outranks every heuristic below. It must, because those
    heuristics prefer a tree that has a BUILT binary, and this one deliberately has none: its
    250 source files are locked read-only and `scripts/forshor_ref_lock.py buildtree` clones
    them to a short root to build (`FORSHOR_BUILD_TREE`, MAX_PATH -- PLAN section 8.8). Left to
    the old rule, the retired v1 tree at C:/leanforshor wins on the strength of its stale
    binary and the whole stack silently keeps reading the emitter we just replaced.

    Drops are named `ForShor_<MMDDYYYY>`; the newest by name wins, and the real tree root is the
    nested `ForShor-main/` that the GitHub zip unpacks to.
    """
    drops = sorted(PIPE_ROOT.glob("ForShor_[0-9]*"), reverse=True)
    for d in drops:
        for root in (d / "ForShor-main", d):
            if (root / "lakefile.lean").is_file():
                return root
    return None


def _default_forshor() -> Path:
    ref = _forshor_reference_drop()
    if ref is not None:
        return ref
    candidates = (PIPE_ROOT / "lean" / "ForShor-main" / "ForShor-main",
                  PIPE_ROOT.parent / "lean" / "ForShor-main" / "ForShor-main",
                  PIPE_ROOT.parent / "leanforshor_copy",
                  PIPE_ROOT.parent / "leanforshor",
                  PIPE_ROOT.parent / "leanforshor" / "ForShor-main" / "ForShor-main")
    # Prefer a tree with an actually-built forshor_emit binary over one that merely has a
    # lakefile.lean — an unbuilt vendored copy otherwise silently wins and every REAL-LEAN
    # path degrades to EmitError/SKIP (2026-09-01 incident; regressed again 2026-09-08 drop).
    for cand in candidates:
        for suffix in ("", ".exe"):
            if (cand / ".lake" / "build" / "bin" / f"forshor_emit{suffix}").is_file():
                return cand
    for cand in candidates:
        if (cand / "lakefile.lean").is_file():
            return cand
    return PIPE_ROOT / "lean" / "ForShor-main" / "ForShor-main"


def _resolve_forshor() -> Path:
    """`ONECLICK_FORSHOR` wins -- unless it names a tree that has been superseded.

    This machine carries a user-scope `ONECLICK_FORSHOR=C:/leanforshor` left over from the v1
    install. Honouring it blindly after the cutover points the whole stack at the retired
    emitter while a reference drop sits right here, which is the same failure as PLAN section
    8.13 wearing a different hat and is invisible until someone diffs a gate count.

    So: an override that lacks the v2 `FastMultiplication/Emit/` sources, when a drop that has
    them is present, is treated as stale. The drop wins and we say so on stderr rather than
    quietly doing either thing. Set ONECLICK_FORSHOR_STRICT=1 to force the override through.
    """
    override = os.environ.get("ONECLICK_FORSHOR")
    if not override:
        return _default_forshor()
    tree = Path(override)
    if os.environ.get("ONECLICK_FORSHOR_STRICT") == "1":
        return tree
    ref = _forshor_reference_drop()
    if ref is not None and not (tree / EMIT_DIR_REL).is_dir() and (ref / EMIT_DIR_REL).is_dir():
        print(f"env_paths: ignoring stale ONECLICK_FORSHOR={tree} (no {EMIT_DIR_REL}); "
              f"using reference drop {ref}. ONECLICK_FORSHOR_STRICT=1 overrides.",
              file=sys.stderr)
        return ref
    return tree


FORSHOR_TREE = _resolve_forshor()

# v1 installed our own emitter under `ShorVerification/Emit/`. The v2 drop ships its own at
# `FastMultiplication/Emit/` and has no ScheduleEmit.lean at all (ask L5). Both spellings are
# recorded so a caller can tell "moved" from "absent".
SCHEDULE_EMIT_REL = Path("FastMultiplication/ShorVerification/Emit/ScheduleEmit.lean")


def lake_executable() -> str | None:
    """The lake executable, or None if none is resolvable."""
    cand = os.environ.get("ONECLICK_LAKE")
    if cand:
        return cand if Path(cand).exists() or shutil.which(cand) else None
    if shutil.which("lake"):
        return "lake"
    elan = Path.home() / ".elan" / "bin" / ("lake.exe" if os.name == "nt" else "lake")
    return str(elan) if elan.exists() else None


def setup_sys_path() -> None:
    """Prepend the Qualtran tree and this folder's script dirs to sys.path."""
    for p in (QISKIT_DIR, PY_DIR, QUALTRAN_TREE):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def subprocess_env() -> dict:
    """os.environ with PYTHONPATH set the same way setup_sys_path sets sys.path."""
    env = dict(os.environ)
    parts = [str(QUALTRAN_TREE), str(PY_DIR), str(QISKIT_DIR)]
    old = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(parts + ([old] if old else []))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def report() -> dict:
    """Preflight facts: paths, existence, versions. Import failures are recorded, not raised."""
    info: dict = {
        "pipe_root": str(PIPE_ROOT),
        "python": sys.version.split()[0],
        "qualtran_tree": {"path": str(QUALTRAN_TREE), "exists": QUALTRAN_TREE.is_dir()},
        "forshor_tree": {"path": str(FORSHOR_TREE), "exists": FORSHOR_TREE.is_dir()},
        "lake": lake_executable(),
    }
    setup_sys_path()
    for mod in ("numpy", "qiskit", "qualtran", "cirq"):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            loc = getattr(m, "__file__", "?")
            info[mod] = {"version": ver, "file": loc}
        except Exception as exc:  # noqa: BLE001 — preflight records, never raises
            info[mod] = {"error": f"{type(exc).__name__}: {exc}"}
    return info


if __name__ == "__main__":
    import json
    print(json.dumps(report(), indent=2))
