"""Price one policy with the engine: the raw views `mapping.panel_of` turns into panels."""
from __future__ import annotations

from typing import Any, Dict, Iterable

from . import engine
from .mapping import DELTA_VALUES

N = 2048
FULL_KEYS = ("n", "T", "T_base", "rotations", "T_per_rotation", "eps", "toffoli", "clifford", "cnot", "qubits", "layout",
             "qubits_layout", "qubits_peak", "peak_base_registers", "peak_extra", "negate", "ladder_str",
             "label", "main_label", "parts", "cross_checks", "lean_grid_leaf_T", "literal_lean_T", "work_width", "widths", "m_bits",
             "precision_rule", "census_trust", "trial_count", "end_to_end")


def _all_tables(eng: Any, P: Any) -> list:
    """Every distinct table of the policy (by digest), arity descending, ties in band order. The engine's view keeps one table per
    arity, which drops tables when two bands use different tables of the same `k` (PR #132: 11 tables, 7 arities), while its trust
    counts every distinct table, and the contract requires the two to agree. For a policy with one table per arity this is the
    engine's own list, in the same order, so panels already published are unchanged. Kept here, outside `evaluation/engine`, so the
    fix does not move `engineRevision` (and so the epoch)."""
    seen: Dict[str, Any] = {}
    for b in P.bands:
        seen.setdefault(b.table.digest, b.table)
    return [eng.policy_view.table_view(t) for t in sorted(seen.values(), key=lambda t: -t.k)]


def _fix_bands_used(view: Dict[str, Any]) -> None:
    """`bands[].used_at_n` from the levels the view itself priced: a band is used at n when its table is on the chain. The engine
    derives it from one table per arity, so a second table of the same k that is on the chain was marked unused. Same meaning,
    computed by digest; kept outside `evaluation/engine` for the same reason as `_all_tables`."""
    used = {l["digest"] for l in view["levels"] if l.get("digest")}
    for b in view["bands"]:
        b["used_at_n"] = b["digest"] in used


def price_views(policy: Any, title: str, delta_key: str, boards: Iterable[str], *, measured: bool = False) -> Dict[str, Dict[str, Any]]:
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
            if measured:
                from .readout_overlay import project
                view["full"]["readout"] = project(P, full)
            view["usage"], view["tables_used"] = full.get("usage"), full.get("tables_used")
            view["tables"] = _all_tables(eng, P)
            _fix_bands_used(view)
            out[board] = view
        else:
            want = eng.tables.PAIR if board == "pp" else eng.tables.TRIPLE
            if P.mode != want:
                raise ValueError(f"a {P.mode} policy does not belong on the {board} board")
            view = eng.policy_view.view(P, N, delta, title)
            view["tables"] = _all_tables(eng, P)
            _fix_bands_used(view)
            out[board] = view
    return out
