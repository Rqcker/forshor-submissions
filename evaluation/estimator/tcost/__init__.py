"""tcost — a Qualtran cost model of gate counts for ShorTableOptimization tables and recursion policies.

The leaderboard ranks with this score. The score is the Qualtran gate count of the recursive phase product that a
policy makes, converted to T. Only Ross-Selinger synthesis gives the price of a rotation. The constructions are
copies from Lean-To-Qualtran (`forshor_qualtran/bloqs/phase_product.py`, `recursive.py`, `width_scan.py`,
`mirrors/coeff_poly.py`, `constants.py`). This package makes them general for:

  * all submitted tables (not only the standard schedules that Lean emits),
  * width-banded policies whose arity k changes from level to level (ForShor `ShorLoweringPolicy`),
  * `PhaseTripleProduct` (ternary phase exp(i*pi*phi*x*y*z), 3k-2 children per level). No upstream code has a
    Qualtran construction for it.

The Qualtran that tcost imports:
  * If `TCOST_QUALTRAN_PATH` is set, tcost uses that tree. The tree must exist.
  * Otherwise, outside CI, tcost uses the vendored tree in this folder (vendor/Qualtran-main/Qualtran-main)
    (0.8.0.dev0). The numbers of Lean-To-Qualtran come from this tree.
  * Otherwise, and ALWAYS under CI (`GITHUB_ACTIONS` or `CI` set), tcost uses the installed `qualtran` package. Under CI, the
    workspace can contain untrusted files. CI pins `qualtran==0.7.0`, which gives identical counts on the
    certified policies.
`qualtran_provenance()` records the Qualtran that tcost actually loaded. Import tcost before qualtran.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_QUALTRAN = (_REPO / "vendor" / "Qualtran-main" / "Qualtran-main")


def _choose_qualtran_path():
    explicit = os.environ.get("TCOST_QUALTRAN_PATH")
    if explicit:
        path = Path(explicit)
        if not (path / "qualtran" / "__init__.py").is_file():
            raise ImportError(f"TCOST_QUALTRAN_PATH={explicit} does not contain a qualtran package")
        return path
    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
        return None
    return _DEFAULT_QUALTRAN if (_DEFAULT_QUALTRAN / "qualtran" / "__init__.py").is_file() else None


QUALTRAN_PATH = _choose_qualtran_path()

if "qualtran" in sys.modules:
    _loaded = Path(sys.modules["qualtran"].__file__).resolve()
    if QUALTRAN_PATH is not None and QUALTRAN_PATH.resolve() not in _loaded.parents:
        raise ImportError(f"qualtran already imported from {_loaded}; import tcost before qualtran "
                          f"so the tree at {QUALTRAN_PATH} is used")
elif QUALTRAN_PATH is not None and str(QUALTRAN_PATH) not in sys.path:
    sys.path.insert(0, str(QUALTRAN_PATH))


def qualtran_provenance() -> dict:
    """Return the Qualtran that is actually loaded: its own `__version__` (not the pip metadata) and its path."""
    import qualtran
    try:
        from qualtran._version import __version__ as version
    except ImportError:
        version = getattr(qualtran, "__version__", "unknown")
    return {"version": version, "path": str(Path(qualtran.__file__).resolve().parent),
            "source": "TCOST_QUALTRAN_PATH" if os.environ.get("TCOST_QUALTRAN_PATH")
            else "vendored-sibling" if QUALTRAN_PATH is not None else "installed"}
