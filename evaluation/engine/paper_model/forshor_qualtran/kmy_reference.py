"""KMY reference values — the published Kahanamoku-Meyer & Yao numbers, ready to compare against.

Source: G. D. Kahanamoku-Meyer and N. Y. Yao, "Fast quantum integer multiplication with zero ancillas"
(the paper vendored at uploads/main.tex). Tables: `tab:scaling` (asymptotic exponents), `tab:cq-2048-costs`
(one classical-quantum multiply of 2048-bit numbers), `tab:x2modN-costs` (x² mod N for 1024-bit x, N).

WHY THIS EXISTS. The overarching task is to formally verify (Lean) and build circuits from KMY's
primitives; to check we reproduce their result we need their numbers next to ours. This module is pure
data + the exponent formulas, so a notebook / comparison cell can overlay the KMY points and exponents on
the ForShor/Qualtran curves directly.

HOW TO COMPARE FAIRLY — read before overlaying:
  * COST METRIC. KEEP T-count as the native ForShor/Qualtran metric (QECGatesCost, 11 T/rotation) — it is
    what every other figure in the pipeline uses. KMY's currency (CR_φ, Toffoli, qubits) is an
    INTERMEDIATE comparison step, reported ALONGSIDE T-count, not a replacement: `QECGatesCost` exposes
    `.rotation` (↔ CR_φ), `.and_bloq` (↔ Toffoli, one And per Toffoli), and Clifford. `to_kmy_currency`
    produces that intermediate view so ForShor's counts line up with the KMY table columns; the primary
    number stays T-count.
  * PRECISION. The 2048-bit table is at η = 1e-12 per qubit, with a QFT on each side of the PhaseProduct.
  * BASE CASE. KMY's base case is n_base² CR_φ gates (the schoolbook phase product, Eq. fourier-schoolbook);
    the x² base case is the 2·n_base² CR_φ trick (compute x'² then rotate).
  * RECURSION STRATEGY — the key caveat. KMY choose k PER RECURSIVE CALL by exhaustive search minimizing
    CR_φ (this is `recursion_versions` "C", per-level-optimal). ForShor PROVES single-k (the Lean plan
    realised by `policy="sym"` — "Model B"). The starred exponents below (1.29 / 1.49 / 1.55) are the
    TOP-LEVEL k exponents, not the effective in-practice slope (which is lower because k varies). So:
       - ASYMPTOTICS: overlay `theory_exponent_cq(k) = log_k(2k-1)` (the Lean-proved single-k exponent, the
         same log KMY report) and mark KMY's top-level-k point;
       - VALUES at n=2048/1024: present the Lean-proved single-k ("Model B") first; then APPEND the
         version-C MODEL check (`recursion_versions`, per-level-optimal) — the apples-to-apples
         reproduction of KMY's methodology, clearly a MODEL, not the Lean-proved plan. The Model-B (sym)
         curve sits ABOVE KMY's per-level-optimal numbers by the recursion-strategy gap; state this.

FUTURE WORK (kept distinct, NOT yet composed): the triple-product / x²modN line — the quantum-quantum
`PhaseTripleProduct` and the x²modN proof-of-quantumness (exponent `log_k(3k-2)`, `X2MODN_1024`). ForShor
does not yet emit/verify these; `theory_exponent_qq`/`X2MODN_1024` are provided so the future block has its
KMY targets ready, but the classical-quantum Shor line (`log_k(2k-1)`, `CQ_2048`) is the one composed now.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


# ------------------------------------------------------------------ asymptotic exponents (Table: tab:scaling)
def theory_exponent_cq(k: int) -> float:
    """Classical-quantum multiply exponent O(n^{log_k(2k-1)}) — q = 2k-1 interpolation points."""
    return math.log(2 * k - 1, k)


def theory_exponent_qq(k: int) -> float:
    """Quantum-quantum / triple-product (incl. x²) exponent O(n^{log_k(3k-2)}) — q = 3k-2 points
    (polynomial degree 3(k-1)). NOTE: the tab:scaling *header* prints `log_k(3k-1)`, but that is a typo:
    the paper body (p(b) "uniquely determined by q=3k-2 points"), the proof, and the gate-count theorem
    `G(n)=O(n^{log_k(3k-2)})` all use 3k-2, and the table's own printed values match 3k-2, not 3k-1."""
    return math.log(3 * k - 2, k)


#: alias — squaring x² is the same q = 3k-2 triple-product exponent.
theory_exponent_square = theory_exponent_qq


# ------------------------------------------------------------------ depth exponents (App. "Proof of circuit depth")
def theory_depth_exponent_cq(k: int) -> float:
    """PhaseProduct DEPTH exponent O(n^{log_k 2}) — KMY's LOW-DEPTH construction (App. depth-proof).
    CAVEAT: this is the min-depth / O(n/log n)-ancilla PARALLEL construction (a space-time tradeoff),
    NOT the ancilla-free sequential object ForShor emits (whose depth ~ its gate count). Use only as a
    labeled theory reference on a T-depth figure, never as the ForShor object's slope."""
    return math.log(2, k)


def theory_depth_exponent_qq(k: int) -> float:
    """PhaseTripleProduct DEPTH exponent O(n^{log_k 3}) — same low-depth/parallel caveat as _cq."""
    return math.log(3, k)


#: tab:scaling — the exact exponent values the paper prints (classical-quantum, q = 2k-1).
KMY_SCALING_CQ: Dict[int, float] = {2: 1.58, 5: 1.37, 8: 1.30}   # + schoolbook n² baseline
#: tab:scaling — quantum-quantum / triple product, q = 3k-2 (the values match 3k-2; header typo says 3k-1).
KMY_SCALING_QQ: Dict[int, float] = {2: 2.00, 3: 1.77, 6: 1.55, 9: 1.46}   # + schoolbook n³ baseline


# ------------------------------------------------------------------ Table 1: one c-q multiply, 2048-bit
#: gate counts in MILLIONS; `ancillas` is the extra ancilla count beyond the 2n operand/output qubits.
#: modular multiply, η = 1e-12 per qubit, QFTs on each side. `top_k` = arity at the top of the recursion.
CQ_2048: List[Dict[str, Any]] = [
    {"algorithm": "This work (standard QFT)",      "scaling": "O(n^1.29)*", "top_k": 9,
     "toffoli_M": 0.6, "crphi_M": 0.3, "hxcnot_M": 1.9, "ancillas": 79,   "this_work": True},
    {"algorithm": "This work (phase gradient QFT)", "scaling": "O(n^1.29)*", "top_k": 9,
     "toffoli_M": 0.9, "crphi_M": 0.1, "hxcnot_M": 3.2, "ancillas": 80,   "this_work": True},
    {"algorithm": "Karatsuba (Gidney windowed)",    "scaling": "O(n^1.58)",  "top_k": None,
     "toffoli_M": 5.6, "crphi_M": None, "hxcnot_M": 34.0, "ancillas": 12730, "this_work": False},
    {"algorithm": "Windowed (Gidney)",              "scaling": "O(n^2/log^2 n)", "top_k": None,
     "toffoli_M": 1.8, "crphi_M": None, "hxcnot_M": 2.5, "ancillas": 4106, "this_work": False},
    {"algorithm": "Schoolbook (Gidney)",            "scaling": "O(n^2)",     "top_k": None,
     "toffoli_M": 6.4, "crphi_M": None, "hxcnot_M": 38.0, "ancillas": 1,   "this_work": False},
]
CQ_2048_META = {"n": 2048, "operation": "classical-quantum modular multiply", "eta": 1e-12,
                "units": "gate counts in millions; ancillas beyond the 2n register qubits",
                "qubit_formula_inplace_modmul": "2n + O(log(1/eta))",
                "qubit_formula_factoring": "2n + O(log n)"}


# ------------------------------------------------------------------ Table 2: x² mod N, 1024-bit
#: gate counts in MILLIONS; `qubits_total` is the full qubit count (not just ancillas).
X2MODN_1024: List[Dict[str, Any]] = [
    {"algorithm": "This work, fast",      "scaling": "O(n^1.49)*", "top_k": 8,
     "toffoli_M": 0.6, "crphi_M": 0.7, "measmt_M": 0.4, "hxcnot_M": 0.8, "qubits_total": 2937, "this_work": True},
    {"algorithm": "This work, balanced",  "scaling": "O(n^1.49)*", "top_k": 8,
     "toffoli_M": 0.7, "crphi_M": 0.7, "measmt_M": 0.4, "hxcnot_M": 1.1, "qubits_total": 2140, "this_work": True},
    {"algorithm": "This work, narrow",    "scaling": "O(n^1.55)*", "top_k": 6,
     "toffoli_M": 1.9, "crphi_M": 1.4, "measmt_M": 1.0, "hxcnot_M": 2.7, "qubits_total": 1583, "this_work": True},
    {"algorithm": "Prev. Fourier 1 (KMY 2022)", "scaling": "O(n^3)",     "top_k": None,
     "toffoli_M": None, "crphi_M": 539.0, "measmt_M": None, "hxcnot_M": None, "qubits_total": 1025, "this_work": False},
    {"algorithm": "Prev. Fourier 2 (KMY 2022)", "scaling": "O(n^2 log n)", "top_k": None,
     "toffoli_M": None, "crphi_M": 35.0, "measmt_M": None, "hxcnot_M": None, "qubits_total": 2062, "this_work": False},
    {"algorithm": "Digital Karatsuba (KMY 2022)", "scaling": "O(n^1.58)", "top_k": None,
     "toffoli_M": 1.6, "crphi_M": None, "measmt_M": 1.1, "hxcnot_M": 1.6, "qubits_total": 6801, "this_work": False},
    {"algorithm": "Digital Schoolbook (KMY 2022)", "scaling": "O(n^2)",   "top_k": None,
     "toffoli_M": 3.5, "crphi_M": None, "measmt_M": 2.2, "hxcnot_M": 2.9, "qubits_total": 4097, "this_work": False},
]
X2MODN_1024_META = {"n": 1024, "operation": "x^2 mod N (proof of quantumness)",
                    "units": "gate counts in millions; qubits_total is the full count",
                    "note": "narrow reuses an n/2 register twice -> ~3n/2 qubits; garbage measured away for free"}


# ------------------------------------------------------------------ methodology constants
KMY_METHOD = {
    "cost_function": "number of CR_phi (controlled phase rotation) gates",
    "k_strategy": "per-level exhaustive search over k, minimizing CR_phi (recursion_versions 'C'); "
                  "ForShor proves single-k ('B') — see module docstring",
    "eval_points": "0, inf, then -1, 1, then -1/w, 1/w, -w, w (w a power of 2); coefficients are bit shifts",
    "pair_cost": "a +/- w_l pair: 2k-1 additions to compute+uncompute; an unpaired w_l: 2k-2 additions",
    "base_case_cq": "n_base^2 CR_phi gates (schoolbook, Eq. fourier-schoolbook)",
    "base_case_x2": "2 n_base^2 CR_phi (compute x'^2 then rotate; carry bits <= log2 n_base)",
    "qq_k2_note": "the quantum-quantum k=2 algorithm is O(n^2) with worse constants than the base case; "
                  "KMY skip k=2 for q-q and go straight to the base case from k=3",
    "eta_2048": 1e-12,
    "zenodo": "https://zenodo.org/doi/10.5281/zenodo.10871109",
    "space_time_tradeoff": "TWO regimes: (1) ancilla-free SEQUENTIAL — 2n+O(log 1/eta) qubits, depth ~ "
                           "gate count O(n^{log_k(2k-1)}) [this is what ForShor emits/verifies]; (2) "
                           "min-depth PARALLEL — depth O(n^{log_k 2}) using O(n/log n) ancillas (App. "
                           "depth-proof). Depth figures must say which regime; ForShor is regime (1).",
}


# ------------------------------------------------------------------ comparison helpers
def to_kmy_currency(qec_cost) -> Dict[str, float]:
    """Map a Qualtran `QECGatesCost` value onto KMY's columns: rotation ↔ CR_φ, And ↔ Toffoli, plus
    Clifford (H/X/CNOT). Report these ALONGSIDE the native T-count (not instead of it) — the intermediate
    view that lines up with the KMY table columns; the primary reported number stays T-count."""
    return {"crphi": float(getattr(qec_cost, "rotation", 0)),
            "toffoli": float(getattr(qec_cost, "and_bloq", 0)),
            "clifford": float(getattr(qec_cost, "clifford", 0) or 0)}


def anchor(problem: str, which: str = "standard QFT") -> Optional[Dict[str, Any]]:
    """The KMY reference row to compare a ForShor estimate against.
    problem: 'cq2048' (2048-bit c-q modular multiply) | 'x2mod1024' (1024-bit x² mod N)."""
    table = CQ_2048 if problem == "cq2048" else X2MODN_1024 if problem == "x2mod1024" else None
    if table is None:
        return None
    return next((r for r in table if r["this_work"] and which.lower() in r["algorithm"].lower()),
                next((r for r in table if r["this_work"]), None))


def scaling_table() -> List[Dict[str, Any]]:
    """The tab:scaling exponents, computed and cross-checked against the paper's printed values."""
    out = []
    for k in sorted(set(KMY_SCALING_CQ) | {3, 4, 6, 9}):
        row = {"k": k, "cq_exponent_log_k(2k-1)": round(theory_exponent_cq(k), 4),
               "qq_square_exponent_log_k(3k-2)": round(theory_exponent_qq(k), 4)}
        if k in KMY_SCALING_CQ:
            row["paper_cq"] = KMY_SCALING_CQ[k]
        if k in KMY_SCALING_QQ:
            row["paper_qq"] = KMY_SCALING_QQ[k]
        out.append(row)
    return out
