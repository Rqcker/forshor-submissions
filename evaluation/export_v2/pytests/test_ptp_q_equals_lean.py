"""The board's PhaseTripleProduct Q equals ForShor's Lean-proved single-product peak (swap 002, decision D-E).

Run from evaluation/: python -m pytest export_v2/pytests/test_ptp_q_equals_lean.py --runslow -q

ForShor's `ptp_peak_qubits_exact` (PhaseTripleProduct/Proofs/Peak/BoardNumerals/Row<id>.lean, kernel-checked at ForShor
`eb00c5adc72832469a5f223fbec6c8b75745d2dd`, unchanged in the epoch pin 21e129aeefc0fd1bbb8fa63402f5799af2490788) bounds the framework `qubitCount` of a circuit following each board policy by
`3n + ptpWsTD + ptpCleanAncD`. The engine's live-qubit peak adds the clean scratch to the named qubits in the same way
(`policy_peak.chain_peak_extra`, three operands), so the two must agree row by row. The numbers below are copied from the
kernel-checked theorems `peak_oneQubit_exact` of the six board rows (no board policy negates, so `scratch` gives the same).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))

LEAN_EXACT_PEAK = {          # ForShor Row<id>.peak_oneQubit_exact, n = 2048
    "82eaf805f71c": 6405,
    "6f7d25984ff8": 7095,
    "85fbe1296fbd": 6261,
    "265a0f53acbc": 6342,
    "2ed7dc90d130": 6573,
    "c733baf3cd08": 6828,
}


@pytest.mark.slow
def test_board_ptp_q_is_the_lean_proved_peak():
    from export_v2 import epoch
    from site_engine import frontier as fr

    rows = epoch.list_rows(epoch.planner_rows(), epoch.baseline_policies())[("ptp", "1e-2")]
    got = {r["id"]: fr.live_peak_qubits(r["policy"], 2048) for r in rows}
    assert got == LEAN_EXACT_PEAK, got
