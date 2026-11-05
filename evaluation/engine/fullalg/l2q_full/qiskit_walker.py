"""Importable accessor for the direct Qualtran-Bloq -> Qiskit walker (qiskit/bloq_to_qiskit.py).

This is the faithful arrow HANDOFF_QUALTRAN_QISKIT_PORT.2026-09-03.md's Track A lands: the walker
already exists as a `bloq.decompose_bloq()` graph walker with a full G1 verifier, but before this file
it was reachable only via `forshor_qualtran.ext.qiskit_gates("G1")` as a subprocess. This module makes
`bloq_to_qiskit` a first-class, in-process import, mirroring how `qiskit_bridge._legacy()` imports
`qiskit_backend` from `py/` rather than touching `sys.path` directly.

Standalone: `bloq_to_qiskit.py` and `qiskit_common.py` are vendored in-package under `oneclick_pipeline/
qiskit/` (copied verbatim from `ext/bundle/qiskit/`, which remains the upstream source of truth -- see
`ext/COPY_ROSTER.md`). This module reaches them via `paths.PIPE_ROOT / "qiskit"` directly, NOT through
`forshor_qualtran.ext._ext_paths()` -- the walker path no longer depends on the `ext/` tree at all.

Version pin: the walker's G1 verifier closes only at the frozen Qualtran tree pinned by
`ONECLICK_QUALTRAN` (0.8.0.dev0). NOTE: `qualtran.__version__` is NOT set on that tree's top-level
`qualtran/__init__.py` (it returns "?" via getattr) -- the real string lives in `qualtran._version`.
Reading THAT is the only correct way to check the pin; reading `qualtran.__version__` directly would
silently SKIP every check with a "?" != "0.8.0.dev0" mismatch that has nothing to do with drift.
"""
from __future__ import annotations

import sys
from typing import Any, List, Optional, Sequence, Tuple

from . import paths as _paths

FROZEN_QUALTRAN = "0.8.0.dev0"   # the pin G1 closes at; a drift is a smoke test, not a certificate


class WalkerUnavailable(RuntimeError):
    """Qiskit/Qualtran absent, walker module not importable, or a Qualtran version drift."""


def _walker_env():
    """in-package qiskit/ (vendored bloq_to_qiskit.py + qiskit_common.py) onto sys.path; return
    (bloq_to_qiskit, WalkRefusal, logical_unitary, compare_up_to_global_phase)."""
    try:
        import qiskit  # noqa: F401
        import qualtran  # noqa: F401
    except Exception as e:
        raise WalkerUnavailable(f"qiskit/qualtran not importable: {e}")
    d = str(_paths.PIPE_ROOT / "qiskit")
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        from bloq_to_qiskit import bloq_to_qiskit, WalkRefusal
        from qiskit_common import logical_unitary, compare_up_to_global_phase
    except Exception as e:
        raise WalkerUnavailable(f"walker not importable from oneclick_pipeline/qiskit: {e}")
    return bloq_to_qiskit, WalkRefusal, logical_unitary, compare_up_to_global_phase


def qualtran_version() -> Tuple[bool, str]:
    """(matches_frozen_pin, version_string). Reads `qualtran._version.__version__` directly --
    top-level `qualtran.__version__` is not set on the vendored tree (see module docstring)."""
    try:
        from qualtran._version import __version__ as v  # type: ignore[import]
    except Exception:
        import qualtran
        v = getattr(qualtran, "__version__", "?")
    return (v == FROZEN_QUALTRAN, v)


def walk(bloq, *, endian: str = "big", and_adjoint: str = "unitary",
         max_depth: Optional[int] = None, strict_version: bool = True
         ) -> Tuple[Any, List[Sequence[int]], Sequence[int]]:
    """Faithful Bloq -> Qiskit: (QuantumCircuit, layout_msb_to_lsb, ancillas), `layout` one row of
    physical qubit ids per RIGHT register (feed straight to `qiskit_common.logical_unitary`).

    Raises `WalkerUnavailable` on env/import/version failure. The walker's own `WalkRefusal`
    (cost-only bloqs with no exact circuit; unknown atoms) propagates unchanged -- callers must
    catch it separately from `WalkerUnavailable` and label the result SKIP, not FAIL.
    """
    bloq_to_qiskit, _WalkRefusal, _logical_unitary, _compare = _walker_env()
    ok, v = qualtran_version()
    if strict_version and not ok:
        raise WalkerUnavailable(f"Qualtran {v} != frozen {FROZEN_QUALTRAN}; walker certifies only at the pin")
    return bloq_to_qiskit(bloq, endian=endian, max_depth=max_depth, and_adjoint=and_adjoint)
