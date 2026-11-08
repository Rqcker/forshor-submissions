"""Merged catalysis bloqs (Amy et al., arXiv:2305.07720 §VI.C p.29): rotations sharing a target
become ONE adder into a phase-gradient catalyst.

Primitives (catalyst.py) and the merged QFT twiddle (twiddle.py) are re-exported here. The leaf and
spec modules (``leaf.py``, ``spec.py``) are NOT imported eagerly; import them explicitly.

Sign convention: the catalyst ``QUInt(b)`` holds |Phi_b> = sum_k e^{-2pi i k/2^b}|k>
(`CatalystPrep`); adding v into its top L qubits kicks back exp(+2 pi i v / 2^L). catalyst[0] is the
half-turn (most-significant) bit.
"""
from .catalyst import (
    AddWithCarryIn,
    CatalystPrep,
    CtrlAddConstIntoCatalyst,
    CtrlAddIntoCatalyst,
    catalyst_phase_turns,
    ctrl_add_ands,
    ctrl_add_const_ands,
)
from .twiddle import (
    MergedTwiddle,
    TwiddleRow,
    contract_twiddle_ands,
    merged_twiddle_ands,
    merged_twiddle_model_turns,
    twiddle_and_delta,
    twiddle_row_plan,
)

__all__ = [
    "AddWithCarryIn",
    "CatalystPrep",
    "CtrlAddConstIntoCatalyst",
    "CtrlAddIntoCatalyst",
    "catalyst_phase_turns",
    "ctrl_add_ands",
    "ctrl_add_const_ands",
    "MergedTwiddle",
    "TwiddleRow",
    "contract_twiddle_ands",
    "merged_twiddle_ands",
    "merged_twiddle_model_turns",
    "twiddle_and_delta",
    "twiddle_row_plan",
]
