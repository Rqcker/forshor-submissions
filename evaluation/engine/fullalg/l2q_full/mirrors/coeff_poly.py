"""E2 mirror — interpolation weights as exact polynomials in the radix b = 2^m.

c_l(b) = Σ_j (M⁻¹)_{l,j} b^j with M_{j,l} = interpEntry(pt_l, j). Exact ``Fraction`` Gauss–Jordan;
P1 (M·M⁻¹ = I), P2 (evaluation identity at m = 1..8), P3 (distinct points) are blocking, exactly
as in Emit/ForshorEmit/CoeffPoly.lean.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List

from .. import schemas
from .schedule import q_of, raw_table

CITES = ["Shor.gaussJordanInverseRows", "Shor.cramerCoeffFromPtsWidth",
         "Shor.interpCoeff_correct"]

Point = Dict[str, Any]


def interp_entry(k: int, pt: Point, j: int) -> Fraction:
    """`int t ↦ t^j`; `frac c ↦ c^(2k-2-j)` — Shor.interpEntry / gen_weights_general.interp_entry."""
    if pt["kind"] == "int":
        return Fraction(pt["z"]) ** j
    return Fraction(pt["m"]) ** (2 * k - 2 - j)


def _solve_multi(M: List[List[Fraction]], B: List[List[Fraction]]) -> List[List[Fraction]]:
    n, r = len(M), len(B[0])
    A = [list(M[i]) + list(B[i]) for i in range(n)]
    for col in range(n):
        piv = next((row for row in range(col, n) if A[row][col] != 0), None)
        if piv is None:
            raise ValueError("singular interpolation system — points are not unisolvent")
        A[col], A[piv] = A[piv], A[col]
        inv = A[col][col]
        A[col] = [v / inv for v in A[col]]
        for row in range(n):
            if row != col and A[row][col] != 0:
                f = A[row][col]
                A[row] = [a - f * b for a, b in zip(A[row], A[col])]
    return [A[i][n:n + r] for i in range(n)]


def poly_eval(cs: List[Fraction], x: Fraction) -> Fraction:
    acc = Fraction(0)
    for c in reversed(cs):
        acc = c + x * acc
    return acc


def coeff_polys(k: int, pts: List[Point]) -> List[List[Fraction]]:
    """Rows of M⁻¹: polys[l][j] = coefficient of b^j in c_l(b). Blocking P1/P2/P3."""
    q = q_of(k)
    if len(pts) != q:
        raise ValueError(f"need q = {q} points, got {len(pts)}")
    keys = [(p["kind"], p.get("z", p.get("m"))) for p in pts]
    if len(set(keys)) != q:
        raise ValueError("duplicate point")
    M = [[interp_entry(k, pts[l], j) for l in range(q)] for j in range(q)]
    I = [[Fraction(int(i == j)) for j in range(q)] for i in range(q)]
    minv = _solve_multi(M, I)
    # P1
    for i in range(q):
        for j in range(q):
            s = sum((M[i][t] * minv[t][j] for t in range(q)), Fraction(0))
            if s != Fraction(int(i == j)):
                raise ValueError(f"P1 failed at ({i},{j})")
    # P2
    for m in range(1, 9):
        b = Fraction(2) ** m
        for j in range(q):
            acc = sum((poly_eval(minv[l], b) * interp_entry(k, pts[l], j) for l in range(q)), Fraction(0))
            if acc != b ** j:
                raise ValueError(f"P2 failed at m={m}, j={j}")
    return minv


def phases(k: int, m: int, phi: Fraction, pts: List[Point] | None = None) -> List[Fraction]:
    """c_l(2^m)·φ in circuit order — must equal ``phase_emit k m φ``."""
    if pts is None:
        pts = raw_table(k)[0]["points"]
    polys = coeff_polys(k, pts)
    b = Fraction(2) ** m
    return [poly_eval(p, b) * phi for p in polys]


def degenerate_ms(pts: List[Point], m_max: int = 40) -> List[int]:
    ints = {p["z"] for p in pts if p["kind"] == "int"}
    return [m for m in range(1, m_max + 1) if (2 ** m) in ints]


def _rat(x: Fraction) -> Dict[str, int]:
    return {"num": x.numerator, "den": x.denominator}


def _rat_str(x: Fraction) -> str:
    return f"{x.numerator}/{x.denominator}"


def section(k: int) -> Dict[str, Any]:
    tab, _ = raw_table(k)
    pts = tab["points"]
    polys = coeff_polys(k, pts)
    q = q_of(k)
    return {
        "schema": schemas.FORSHOR_COEFF_POLY, "k": k, "q": q,
        "variable": "b = 2^m (radix; m = limb width = ceil(n/k))",
        "degree": q - 1,
        "points": [dict(p) for p in pts],
        "polys": [[_rat(c) for c in p] for p in polys],
        "polys_note": "polys[l][j] = coefficient of b^j in c_l(b); leaf l in circuit order (expectedRow(points[l]))",
        "sample_eval_phi1": [{"m": m, "phases": [_rat_str(poly_eval(p, Fraction(2) ** m)) for p in polys]}
                             for m in range(1, 9)],
        "sample_eval_note": "phases at phi = 1 (units of pi): must equal `phase_emit k m 1/1` byte-for-byte",
        "degenerate_m": degenerate_ms(pts, 40),
        "degenerate_note": "at these m the radix equals an integer point: c is a selector; refuse as acceptance points",
        "checks": {"P1_inverse_identity": True, "P2_eval_identity_m": list(range(1, 9)),
                   "P3_points_distinct": True},
        "cites": CITES,
    }
