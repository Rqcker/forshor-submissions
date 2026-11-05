"""E5 mirror — the `CmodMulInPlaceCore` template (static; strings identical to ModMulPlan.lean).

The Lean-proved multiplier is the KMY Algorithm-1 approximate core; see the Lean file header.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .. import schemas

CITES = ["Shor.CmodMulInPlaceCore", "Shor.modMul_approx_valid_dist_uniform",
         "Shor.modExpApprox_correct", "Shor.IdealCtrlModMulExactSemantics.eval_idealCtrlModMul_good_ket",
         "Shor.IdealCtrlModMulExactSemantics", "Shor.Algorithm1Precision", "Shor.shorGateCostModel"]


def _step(name: str, gates: List[str], phase: Optional[Tuple[str, str]], regs: List[str]) -> Dict[str, Any]:
    d: Dict[str, Any] = {"step": name, "gates": gates, "registers": regs}
    if phase is not None:
        d["phase"] = {"label": phase[0], "formula": phase[1]}
    return d


STEPS = [
    _step("U1 step1", ["H_reg work", "CPhaseProdUsing ctrl phi1 data work", "IQFT work"],
          ("phi1", "2*pi * ((c + N - 1) mod N) / N"), ["ctrl", "data", "work"]),
    _step("U2 step2", ["QFT dataCarry", "PhaseProdUsing phi2 work dataCarry", "IQFT dataCarry"],
          ("phi2", "2*pi * N / 2^(|work| + |dataCarry|)"), ["work", "dataCarry"]),
    _step("U3 step3", ["Prim CMP_GE_CONST [N, flag] ++ dataCarry", "Prim CSUB_CONST [N, flag] ++ dataCarry"],
          None, ["flag", "dataCarry"]),
    _step("U4 step4", ["Prim CMP_LT_NW [N, flag] ++ dataCarry ++ work"], None, ["flag", "dataCarry", "work"]),
    _step("U5 step5", ["adjoint( H_reg work ; CPhaseProdUsing ctrl phi5 dataCarry work ; IQFT work )"],
          ("phi5", "2*pi * (k5 mod N) / N,  k5 = (1 + N - inv(c) mod N) mod N   -- Shor.step5Constant"),
          ["ctrl", "dataCarry", "work"]),
]


def section() -> Dict[str, Any]:
    return {
        "schema": schemas.FORSHOR_MODMUL_PLAN,
        "decl": "Shor.CmodMulInPlaceCore c N ctrl data work flag",
        "source_file": "FastMultiplication/ShorVerification/Implementation/ModularExponentiation/Defs.lean",
        "construction": "KMY Appendix-E Algorithm 1 (fractional / approximate) controlled in-place modular multiply",
        "registers": {
            "data": "n",
            "work": "n + extra(eta)",
            "extra_bits": "ceil(2 * log2(2 + 1/(2*eta)))   -- Shor.algorithm1ExtraBits eta",
            "dataCarry": "n + 1   -- data.grow 1",
            "flag": 1, "ctrl": 1,
            "precision": "Algorithm1Precision eta data work: 0 < eta < 1/2 and |work| = |data| + extra(eta)"},
        "steps": [dict(s) for s in STEPS],
        "phase_products": "each (C)PhaseProdUsing lowers via the Toom-Cook plan of E1-E3 (zeroExtend 1 each side, then the signed recursive plan); each QFT/IQFT via the E4 rule",
        "cost_model": {
            "prim": "CMP_GE_CONST | CSUB_CONST | CMP_LT_NW  ->  20*w + 10  (linearPrimitiveGateBound over the payload qubits)",
            "adder": "9*w + 2  (rippleAdderGateBound)",
            "naive_signed_pp": "|x| * |z|",
            "naive_controlled_pp": "5 * |x| * |z|",
            "radix_reverse": "3 * (m/2)",
            "shifts_alloc": 0},
        "proof_status": {
            "approx_bound": "Shor.modMul_approx_valid_dist_uniform: exists K >= 0, ||approx - ideal|| <= sqrt(2 K eta); K not exhibited",
            "exact_semantics": "Shor.IdealCtrlModMulExactSemantics.eval_idealCtrlModMul_good_ket is relative to class IdealCtrlModMulExactSemantics, which has NO instance in the tree",
            "modexp": "Shor.modExpApprox_correct: ||.|| <= tbits * stepErr",
            "tier": "Lean-cited template (structure computable, phases Real, workspace proof argument); assembly + numerics validated in Python"},
        "cites": CITES,
    }
