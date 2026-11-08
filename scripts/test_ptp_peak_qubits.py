#!/usr/bin/env python3
"""`ptp_exact_width.ptp_peak_qubits` against ForShor's kernel-evaluated `ptpPeakQubits` numerals.

Stdlib only, no Lean. Run: python3 -I scripts/test_ptp_peak_qubits.py

Oracles (numbers computed by ForShor, not here): the six PTP board rows (scripts/tests/ptp_peak_oracle.json, from
swap-002 shard S5c), `policies/ref_ptp_2048.json` (ForShor `Peak/Numerals.lean`: scan ws 1352 peak 7497, exact ws 638
peak 6783, no `negate` in any table) and the same policy with `negate x1; negate x1` appended to the top table
(`refNeg_peak_*`: oneQubit 7497 / 6783, scratch 7895 / 7158 with clean 399 / 376).
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ptp_exact_width as pw  # noqa: E402

ORACLE = json.loads((ROOT / "scripts/tests/ptp_peak_oracle.json").read_text())
REF = json.loads((ROOT / "policies/ref_ptp_2048.json").read_text())


class PeakQubits(unittest.TestCase):
    def test_six_board_rows_both_methods_both_rules(self):
        self.assertEqual(len(ORACLE["rows"]), 6)
        for rid, row in ORACLE["rows"].items():
            for key, exp in row["expected"].items():
                m, r = key.split("|")
                self.assertEqual(pw.ptp_peak_qubits(row["policy"], 2048, r, m), exp, f"{rid} {key}")

    def test_board_rows_peak_is_the_lean_exact_numbers(self):
        got = {rid: pw.ptp_peak_qubits(row["policy"], 2048, "exact")["peak"] for rid, row in ORACLE["rows"].items()}
        self.assertEqual(sorted(got.values()), [6261, 6342, 6405, 6573, 6828, 7095])

    def test_reference_ptp_policy(self):
        self.assertEqual(pw.ptp_peak_qubits(REF, 2048, "scan"), {"ws": 1352, "clean": 1, "peak": 7497})
        self.assertEqual(pw.ptp_peak_qubits(REF, 2048, "exact"), {"ws": 638, "clean": 1, "peak": 6783})
        for m in ("oneQubit", "scratch"):  # no negate in any table: the method cannot matter
            self.assertEqual(pw.ptp_peak_qubits(REF, 2048, "exact", m)["peak"], 6783)

    def test_negating_variant_scratch_costs_the_widest_register(self):
        d = copy.deepcopy(REF)
        d["bands"][0]["operations"] += [["negate", 1], ["negate", 1]]
        self.assertEqual(pw.ptp_peak_qubits(d, 2048, "scan", "oneQubit")["peak"], 7497)
        self.assertEqual(pw.ptp_peak_qubits(d, 2048, "exact", "oneQubit")["peak"], 6783)
        self.assertEqual(pw.ptp_peak_qubits(d, 2048, "scan", "scratch"), {"ws": 1352, "clean": 399, "peak": 7895})
        self.assertEqual(pw.ptp_peak_qubits(d, 2048, "exact", "scratch"), {"ws": 638, "clean": 376, "peak": 7158})

    def test_bad_arguments(self):
        with self.assertRaises(ValueError):
            pw.ptp_peak_qubits(REF, 2048, "tight")
        with self.assertRaises(ValueError):
            pw.ptp_peak_qubits(REF, 2048, "exact", "incrementer")


if __name__ == "__main__":
    unittest.main()
