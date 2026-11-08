"""E4 mirror: the `lowerQFTPlan` rule, with the sizes and the workspace demand of each split.

Refer to Emit/ForshorEmit/QftPlan.lean for the derivation from Implementation/QFT/Defs.lean:130.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict

from .. import schemas
from .schedule import raw_table
from .width import qft_workspace_need

CITES = ["Shor.lowerQFT_correct", "Shor.eval_QFT_split", "Shor.qftWorkspaceNeed",
         "Shor.radixReverseGateCount"]

RULE = {
    "base0": "QFT(0) = id",
    "base1": "QFT(1) = H",
    "split": ("QFT(w) = QFT(right) ; PhaseProdUsing(phi = 2*pi/2^w, left, right) ; "
              "QFT(left) ; RadixReverse(w, w/2)   with left = w/2 (low qubits), right = w - w/2"),
    "twiddle": ("PhaseProdUsing lowers as: zeroExtend x 1 ; zeroExtend z 1 ; "
                "SignedPhaseProd (Toom-Cook recursive plan at input size max(left,right)+1) ; dealloc"),
    "gate_alphabet": ["H", "RadixReverse", "SignedPhaseProd(recursive)"],
    "not_in_alphabet": ["CP", "SWAP"],
    "h_count": "w (one per width-1 leaf)",
    "split_count": "w - 1 (internal nodes of the balanced recursion tree)",
}


def split(k: int, ops, w: int) -> Dict[str, Any]:
    left, right = w // 2, w - w // 2
    phi = Fraction(2) / (Fraction(2) ** w)
    x, z = qft_workspace_need(k, ops, w)
    return {
        "w": w, "left": left, "right": right,
        "phi_over_pi": {"num": phi.numerator, "den": phi.denominator},
        "pp_signed_widths": {"x": left + 1, "z": right + 1},
        "pp_input_size": max(left + 1, right + 1),
        "radix_reverse_m": left,
        "radix_reverse_gate_bound": 3 * (left // 2),
        "workspace_need": {"x": x, "z": z},
    }


def section(k: int, w_max: int = 96) -> Dict[str, Any]:
    ops = raw_table(k)[0]["ops"]
    return {
        "schema": schemas.FORSHOR_QFT_PLAN, "k": k, "rule": dict(RULE),
        "splits": [split(k, ops, w) for w in range(2, w_max + 1)],
        "splits_note": "one entry per register width w = 2..wMax; the recursion tree at width w is obtained by following left/right",
        "cites": CITES,
        "provenance": "lean-emitted (rule + sizes + workspace demand); the lowered LowGate term itself is noncomputable in ForShor",
    }
