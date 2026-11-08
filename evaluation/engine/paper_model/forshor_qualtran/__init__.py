"""forshor_qualtran — the Lean (ForShor) → Qualtran interface.

Design invariant (owner decision 2026-09-01, PLAN_LEAN_QUALTRAN_INTERFACE.md §II.1):

    k enters at the Lean level; n enters only at resource estimation.

The Lean executable ``forshor_emit`` prints, per Toom arity k, an n-free bundle
(``forshor.emit/v1``): the schedule (E1), the interpolation weights as polynomials in the
radix b = 2^m (E2), both width policies as affine tails (E3), the QFT lowering rule (E4), the
controlled modular-multiply template (E5), and the recursion skeleton (E6). This package
consumes that bundle (or its Python mirror, labelled REFERENCE-MIRROR), builds Qualtran
bloqs whose ``n`` is a SymPy symbol, and substitutes ``n`` only inside
``get_cost_value`` (estimation) or ``decompose_bloq`` (small-n verification).

Modules
-------
constants   the cost constants, defined once
schemas     every schema string, defined once
paths       ForShor tree / binaries / artifacts (delegates to env_paths.py)
provenance  Provenance and Tier enums; the provenance table
mirrors/    Python mirrors of E1–E6 (exact arithmetic; differential-tested against Lean)
emit        run the real binary or the mirror; cross-check them
ir          typed bundle
widths      closed forms W_sym(n), W_tight(n) from the emitted affine tails
ir_v1       reader + interpreter for ForShor's extracted `forshor.ir/v1` templates
policy      width-indexed (varying-k) policies: compose per-band IR exactly as Policy.signedPlanOf
bloqs/      symbolic-n Qualtran bloqs
estimate    cost sweeps, crossover tables, provenance/traceability tables, figures
cli         one click
"""
from __future__ import annotations

__version__ = "0.1.0"

# Bind the frozen Qualtran tree BEFORE any submodule's module-level `from qualtran import ...`
# runs (2026-09-11 audit finding: without this, `python -m forshor_qualtran.cli` -- the
# documented one-click entry point -- silently resolved the pip-installed qualtran instead of
# QUALTRAN_TREE, because nothing in the cli->estimate->bloqs import cascade called
# env_paths.setup_sys_path() first; only tests/conftest.py did, which is why pytest never
# surfaced it. This must live in the PACKAGE's own __init__.py, not in any one entry point --
# Python always runs a package's __init__.py before any of its submodules, so this reaches
# cli.py, estimate.py, bloqs/, golden.py, asymptotics.py and rotation_histogram.py uniformly,
# regardless of which one a caller imports first.
from pathlib import Path as _Path

from . import paths as _paths

_paths.setup_legacy_sys_path()

import qualtran as _qualtran  # noqa: E402 -- must import here, right after the path fix, to verify it

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
