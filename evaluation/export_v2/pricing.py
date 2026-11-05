"""Price one policy with the engine: the raw views `mapping.panel_of` turns into panels."""
from __future__ import annotations

from typing import Any, Dict, Iterable

from . import engine
from .mapping import DELTA_VALUES

N = 2048
FULL_KEYS = ("n", "T", "T_base", "rotations", "T_per_rotation", "eps", "toffoli", "clifford", "cnot", "qubits", "layout", "ladder_str",
             "label", "main_label", "parts", "cross_checks", "lean_grid_leaf_T", "literal_lean_T", "work_width", "widths", "m_bits",
             "precision_rule", "census_trust", "trial_count", "end_to_end")


def price_views(policy: Any, title: str, delta_key: str, boards: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Views for a `tcost` Policy at one delta: 'pp' or 'ptp' is the single-product view; 'full' adds the whole run's pricing.
    The policy is priced at the exact widths the boards use."""
    eng = engine.load()
    delta = DELTA_VALUES[delta_key]
    P = policy.with_widths("exact")
    out: Dict[str, Dict[str, Any]] = {}
    for board in boards:
        if board == "full":
            if P.mode != eng.tables.PAIR:
                raise ValueError("only a PhaseProduct policy is priced on the full order-finding board")
            full = eng.fullalg.price_full(P, N, delta, detail=True)
            view = eng.policy_view.view(P, N, delta, f"{title}, full order finding, n = {N}")
            view["full"] = {k: full.get(k) for k in FULL_KEYS}
            view["usage"], view["tables_used"] = full.get("usage"), full.get("tables_used")
            out[board] = view
        else:
            want = eng.tables.PAIR if board == "pp" else eng.tables.TRIPLE
            if P.mode != want:
                raise ValueError(f"a {P.mode} policy does not belong on the {board} board")
            out[board] = eng.policy_view.view(P, N, delta, title)
    return out
