"""forshor_qualtran: the interface from Lean (ForShor) to Qualtran.

Design invariant (owner decision 2026-09-01, PLAN_LEAN_QUALTRAN_INTERFACE.md §II.1):

    k enters at the Lean level; n enters only at resource estimation.

For each Toom arity k, the Lean executable ``forshor_emit`` prints a bundle that does not contain n
(``forshor.emit/v1``). The bundle has these sections:

- the schedule (E1),
- the interpolation weights as polynomials in the radix b = 2^m (E2),
- the two width policies as affine tails (E3),
- the QFT lowering rule (E4),
- the template for the controlled modular multiply (E5),
- the recursion skeleton (E6).

This package reads that bundle (or its Python mirror, with the label REFERENCE-MIRROR). It builds
Qualtran bloqs whose ``n`` is a SymPy symbol. It puts a value for ``n`` only inside
``get_cost_value`` (estimation) or ``decompose_bloq`` (verification at small n).

Modules kept in the sto_site fork (the rest of Lean-To-Qualtran was removed: the Lean emitter/IR bloqs, the
digital-product and Cuccaro tiers, the width scans)
-------
constants         cost constants (the Ross-Selinger law comes from `sto_currency`)
rotation          eps = delta / R, rounded T per rotation (`sto_currency`)
cost_conventions  the ONE site convention: grid leaf, Qualtran adders; PP and PTP tree pricing
policy            width-banded PhasePolicy and ToomCookTable (nextWidth mirror, dispatcher)
policy_planner    exact PP planner (Pareto front over (T_base, R))
sto_policy        STO policy JSON -> PhasePolicy (with the exact Toom-Cook identity check)
table_catalog     STO point/op conversion, `checked`
mirrors/          Python mirrors of the ForShor width/schedule functions
ir_v1, schemas    readers that `policy` needs
paths             vendored Qualtran location
"""
from __future__ import annotations

__version__ = "0.1.0"

# Bind the frozen Qualtran tree BEFORE a submodule runs its module-level `from qualtran import ...`.
# This comes from a 2026-09-11 audit finding. Without this code, `python -m forshor_qualtran.cli`
# found the pip-installed qualtran and not QUALTRAN_TREE, and it gave no message. That command is the
# documented one-click entry point. The cause: no module in the import chain cli->estimate->bloqs
# called env_paths.setup_sys_path() first. Only tests/conftest.py called it, thus pytest did not show
# the problem.
# Keep this code in the PACKAGE's own __init__.py, not in one entry point. Python always runs the
# __init__.py of a package before its submodules. Thus this code has the same effect for cli.py,
# estimate.py, bloqs/, golden.py, asymptotics.py and rotation_histogram.py, in any import order.
from pathlib import Path as _Path

from . import paths as _paths

_paths.setup_legacy_sys_path()

import qualtran as _qualtran  # noqa: E402 -- import here, immediately after the path fix, to verify the fix

_qt_file = getattr(_qualtran, "__file__", None)
if _qt_file is None or not str(_Path(_qt_file).resolve()).startswith(str(_paths.QUALTRAN_TREE.resolve())):
    raise RuntimeError(
        f"forshor_qualtran requires the frozen Qualtran tree at {_paths.QUALTRAN_TREE}, but "
        f"`import qualtran` resolved to {_qt_file!r} instead (likely the pip-installed package). "
        f"This means QUALTRAN_TREE does not exist, or something imported qualtran before "
        f"forshor_qualtran (in which case the wrong copy is now cached in sys.modules for this "
        f"whole process). Check ONECLICK_QUALTRAN / env_paths.QUALTRAN_TREE."
    )

del _qualtran, _qt_file, _Path
