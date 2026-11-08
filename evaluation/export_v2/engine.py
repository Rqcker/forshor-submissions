"""The vendored engine, imported lazily. Importing `site_engine` first binds the frozen Qualtran tree; the old estimator
(`evaluation/estimator`) defines packages with the same names and must never be on the path of a process that uses this."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
_loaded = None


def load() -> SimpleNamespace:
    global _loaded
    if _loaded is None:
        if str(ENGINE_DIR) not in sys.path:
            sys.path.insert(0, str(ENGINE_DIR))
        import site_engine  # noqa: F401  (must come first: binds the vendored Qualtran)
        import tcost
        from site_engine import frontier, full_frontier, fullalg, library, policy_view, precompute, seed_policies, seeded_board, submitted
        from sto_currency import SITE_MODEL_VERSION
        from tcost import planner, tables
        _loaded = SimpleNamespace(
            tcost=tcost, frontier=frontier, full_frontier=full_frontier, fullalg=fullalg, library=library, policy_view=policy_view,
            precompute=precompute, seed_policies=seed_policies, seeded_board=seeded_board, submitted=submitted,
            planner=planner, tables=tables, SITE_MODEL_VERSION=SITE_MODEL_VERSION, engine_dir=ENGINE_DIR)
    return _loaded
