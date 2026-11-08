"""Machine-checked provenance for policy results (roundtable 2026-10-12, C-10 / EVD-3).

Before this, every policy artifact named its sources as hand-typed strings ("ForShor cdca809" —
the checkout has no `.git`, so nothing checked it). :func:`provenance_block` replaces those with
content hashes computed from the trees themselves, plus the cost-model constants and library
versions a number depends on. It contains **no timestamp**, so the same inputs give a
byte-identical block and regenerated artifacts can be diffed.

    forshor      sha256 over every `*.lean` under the ForShor checkout (sorted relative paths +
                 contents), and the file count
    probe        sha256 of `lean/PolicyProbe/Main.lean` (the source of the Lean fixtures)
    qualtran     sha256 over the frozen Qualtran tree's `*.py` (the tree every cost is pinned to)
    tcount       the Ross–Selinger constants `C_SYN_MEASURED` read from the module, and its sha
    versions     python, qualtran, sympy, numpy
    script       the calling script's name and sha256 (pass ``script=__file__``)
"""
from __future__ import annotations

import hashlib
import os
import platform
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from . import paths

def _default_forshor_tree() -> Path:
    """The ForShor tree the fixtures were built from: the vendored drop this repository ships
    (`ForShor_<date>/ForShor-main`), so a clone records the same source. Overridable with
    `ONECLICK_POLICY_FORSHOR`."""
    import env_paths
    drop = env_paths._forshor_reference_drop()
    return drop if drop is not None else paths.PIPE_ROOT.parent / "ForShor-main"


DEFAULT_FORSHOR = Path(os.environ.get("ONECLICK_POLICY_FORSHOR") or _default_forshor_tree())


def _tree_sha(root: Path, patterns: Iterable[str], skip_dirs=(".lake", "__pycache__", ".git")) -> Dict[str, Any]:
    """sha256 over (relative path, contents) of matching files, sorted — order-independent."""
    if not root.is_dir():
        return {"path": str(root), "present": False}
    h, n = hashlib.sha256(), 0
    files = sorted(p for pat in patterns for p in root.rglob(pat)
                   if p.is_file() and not any(part in skip_dirs for part in p.relative_to(root).parts))
    for p in files:
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode() + b"\0" + p.read_bytes() + b"\0")
        n += 1
    return {"present": True, "files": n, "sha256": h.hexdigest()}


def _file_sha(p: Path) -> Optional[str]:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def _version(mod: str) -> Optional[str]:
    try:
        m = __import__(mod)
    except Exception:  # noqa: BLE001 — provenance records, never raises
        return None
    v = getattr(m, "__version__", None)
    if v is None:                       # the frozen Qualtran tree keeps it in `_version.py`
        vf = Path(getattr(m, "__file__", "")).parent / "_version.py"
        if vf.is_file():
            ns: Dict[str, Any] = {}
            exec(vf.read_text(), ns)  # noqa: S102 — a one-line `__version__ = "…"` file
            v = ns.get("__version__")
    return str(v) if v is not None else "?"


def provenance_block(*, script: Optional[str] = None, forshor: Optional[Path] = None,
                     extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A deterministic, timestamp-free provenance dict (see module docstring)."""
    fs = Path(forshor) if forshor is not None else DEFAULT_FORSHOR
    probe_src = paths.PIPE_ROOT / "lean" / "PolicyProbe" / "Main.lean"
    tc = {}
    try:
        b = paths.tcount_module("budget")
        tc = {"C_SYN_MEASURED": list(b.C_SYN_MEASURED), "budget_sha256": _file_sha(Path(b.__file__))}
    except ImportError as e:
        tc = {"error": str(e)}
    out: Dict[str, Any] = {
        "schema": "forshor_qualtran.provenance/v1",
        "forshor": {"path_name": fs.name, **_tree_sha(fs, ("*.lean",))},
        "probe_source_sha256": _file_sha(probe_src),
        "qualtran": {"path_name": paths.QUALTRAN_TREE.name,
                     **_tree_sha(paths.QUALTRAN_TREE / "qualtran", ("*.py",))},
        "tcount": tc,
        "versions": {"python": platform.python_version(), "qualtran": _version("qualtran"),
                     "sympy": _version("sympy"), "numpy": _version("numpy")},
    }
    if script:
        sp = Path(script)
        out["script"] = {"name": sp.name, "sha256": _file_sha(sp)}
    if extra:
        out["extra"] = extra
    return out
