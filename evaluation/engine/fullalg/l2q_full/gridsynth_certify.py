"""Numerically certify the exact gridsynth path used by Clifford+T conversion.

The collector temporarily replaces the ``gridsynth_clifford_t_for_rz`` attribute in
``clifford_t_synthesis``.  Python resolves that module's bare global name at each
call, so this intercepts calls from both splice helpers and from
``full_clifford_t_conversion`` itself while delegating every call to the original
function unchanged.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from unittest.mock import patch

import numpy as np

from . import clifford_t_synthesis as CT


def certify_angle(theta: float, eps: float) -> dict[str, Any]:
    """Measure the raw operator-norm error of production gridsynth for one Rz."""
    from qiskit.circuit.library import RZGate
    from qiskit.quantum_info import Operator

    qc, gate_string, t_count = CT.gridsynth_clifford_t_for_rz(theta, eps)
    # Do not remove global phase: it is deliberately represented by ``W`` in the
    # production circuit and is part of the Rz contract being checked here.
    actual = Operator(qc).data
    expected = RZGate(theta).to_matrix()
    achieved = float(np.linalg.norm(actual - expected, ord=2))
    return {
        "theta": theta,
        "eps_requested": eps,
        "eps_achieved": achieved,
        "t_count": t_count,
        "gate_string_len": len(gate_string),
    }


def certify_circuit_synthesis(theta_eps_pairs: Iterable[tuple[float, float]]) -> dict[str, Any]:
    """Certify distinct synthesis requests and sum their error once per gate site."""
    pairs = list(theta_eps_pairs)
    representatives: dict[tuple[float, float], tuple[float, float]] = {}
    counts: Counter[tuple[float, float]] = Counter()
    for theta, eps in pairs:
        key = (round(theta, 12), eps)
        representatives.setdefault(key, (theta, eps))
        counts[key] += 1

    certified_by_key = {
        key: certify_angle(*representative)
        for key, representative in representatives.items()
    }
    per_angle = sorted(
        certified_by_key.values(), key=lambda item: item["eps_achieved"], reverse=True
    )
    weighted_sum = float(sum(
        counts[key] * certified["eps_achieved"]
        for key, certified in certified_by_key.items()
    ))
    return {
        "n_sites_total": len(pairs),
        "n_distinct_angles": len(certified_by_key),
        "per_angle": per_angle,
        "sum_achieved_eps_total_bound": weighted_sum,
        "max_eps_achieved": max((item["eps_achieved"] for item in per_angle), default=0.0),
        "composability_bound_explanation": (
            "For unitary gate compositions, if ||U_i - U_i'||_2 <= eps_i at each "
            "physical gate site, the standard triangle-inequality/submultiplicativity "
            "argument gives ||prod U_i - prod U_i'||_2 <= sum_i eps_i; this total "
            "weights each distinct certified request by its number of physical sites."
        ),
    }


def collect_gridsynth_calls_from_conversion(qc, gridsynth_eps: float = 1e-6):
    """Run production conversion while recording every production gridsynth request."""
    recorded: list[tuple[float, float]] = []
    original = CT.gridsynth_clifford_t_for_rz

    def recording_gridsynth(theta: float, eps: float):
        recorded.append((theta, eps))
        return original(theta, eps)

    # The target is the defining module's global namespace, which is precisely
    # where its helpers resolve their bare ``gridsynth_clifford_t_for_rz`` name.
    with patch.object(CT, "gridsynth_clifford_t_for_rz", new=recording_gridsynth):
        clifford_t_qc, report = CT.full_clifford_t_conversion(qc, gridsynth_eps=gridsynth_eps)
    return clifford_t_qc, report, recorded


def main() -> dict[str, Any]:
    """Build B5/k=2 by the sweep's production route and write its certification."""
    from l2q_full import emit, qiskit_walker
    from l2q_full import paths

    paths.setup_legacy_sys_path()
    import lowgate_ir
    import qualtran_backend

    ir = emit.lower_cmodmul(2, 3, 2, 3, 0.5)
    bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(ir))
    qc, _layout, _ancillas = qiskit_walker.walk(bloq)
    _converted, _conversion_report, pairs = collect_gridsynth_calls_from_conversion(
        qc, gridsynth_eps=1e-6
    )
    report = certify_circuit_synthesis(pairs)
    output = Path(__file__).resolve().parents[1] / "artifacts" / "gridsynth_certification_B5.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    main()
