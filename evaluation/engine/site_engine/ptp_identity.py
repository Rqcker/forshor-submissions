"""Python verification of a PhaseTripleProduct table: the PTP counterpart of `forshor_qualtran.table_catalog.checked`.

For PhaseProduct, `checked` runs ForShor's cheap checks C1, C2, C4 and the checkpoint count on the table, then the
exact Toom-Cook identity. `ToomCookTable.check` hard-codes q(k) = 2k - 1, so a PTP table cannot go through it. This
module is the same verification at q(k) = 3k - 2.

A PTP table acts on three operands x, y, z of k limbs each. The product X(t)Y(t)Z(t) of their limb polynomials has
degree 3(k - 1), so it needs 3k - 2 points. The program leaves register i holding `expectedRow(p_l)`; a phase product
at point p_l then multiplies the three evaluations. With b = 2^m the interpolation coefficients c_l(b) satisfy

    sum_l c_l(b) * (r_l . xs) * (r_l . ys) * (r_l . zs)  ==  x * y * z       (exact, over Q)

where r_l is the register that the l-th phase product acts on and xs, ys, zs are the limbs. The check draws random
signed limbs for several limb widths m and compares both sides exactly.

What this checks, and what it does not:
  C1  3k - 2 points.                         C2  the points are projectively distinct (unisolvence).
  C4  the program returns to its start state; the number of phase products is 3k - 2.
  ID  the identity above, at m in {3, 5, 8}, 6 random draws each.
It does not prove correctness for all widths (the Lean verifier does), and the identity follows from C3 on a
well-formed table: it is an independent, executable restatement of it. Its value is that it shares no code with
`tcost.tables.Table.check`: it runs on the Lean-To-Qualtran mirror of `run?` (`wmirror.run_rows`).
"""
from __future__ import annotations

import random
from fractions import Fraction
from functools import lru_cache
from typing import Dict, List, Sequence, Tuple

from forshor_qualtran.mirrors import width as wmirror
from forshor_qualtran.policy import PolicyError
from forshor_qualtran.table_catalog import op_from_sto, point_from_sto
from tcost import tables as tt
from tcost.tables import op_json, point_str

MAX_K = 64


def q3(k: int) -> int:
    """`q k = 3k - 2`: one interpolation point for each coefficient of the product of three limb polynomials."""
    return 3 * k - 2


def _key(p: Dict) -> Tuple[str, int]:
    return ("int", int(p["z"])) if p["kind"] == "int" else ("frac", int(p["m"]))


def _interp(q: int, p: Tuple[str, int], j: int) -> Fraction:
    """`interpEntry` at q points: int z -> z^j ; frac c -> c^(q-1-j) (frac 0 is the point at infinity)."""
    kind, v = p
    return Fraction(v) ** j if kind == "int" else Fraction(v) ** (q - 1 - j)


@lru_cache(maxsize=1 << 12)
def _coeffs(q: int, pts: Tuple[Tuple[str, int], ...], m: int) -> Tuple[Fraction, ...]:
    """Solve M^T c = (1, b, ..., b^(q-1)) with M[l][j] = interpEntry(p_l, j), b = 2^m. Exact Gauss-Jordan."""
    b = Fraction(2) ** m
    rows = [[_interp(q, pts[l], j) for l in range(q)] + [b ** j] for j in range(q)]
    for col in range(q):
        piv = next((r for r in range(col, q) if rows[r][col] != 0), None)
        if piv is None:
            raise PolicyError("interpolation matrix is singular (C2 fails)")
        rows[col], rows[piv] = rows[piv], rows[col]
        pv = rows[col][col]
        rows[col] = [v / pv for v in rows[col]]
        for r in range(q):
            if r != col and rows[r][col] != 0:
                f = rows[r][col]
                rows[r] = [a - f * c for a, c in zip(rows[r], rows[col])]
    return tuple(rows[l][q] for l in range(q))


def verify_ptp_table(T: tt.Table, ms: Sequence[int] = (3, 5, 8), trials: int = 6, seed: int = 0) -> None:
    """Raise PolicyError if the PhaseTripleProduct table `T` fails C1, C2, C4, the checkpoint count or the identity."""
    name = f"PTP k={T.k} {T.digest[:12]}"
    if T.mode != tt.TRIPLE:
        raise PolicyError(f"{name}: verify_ptp_table takes {tt.TRIPLE} tables, got {T.mode!r}")
    k = T.k
    if not 2 <= k <= MAX_K:
        raise PolicyError(f"{name}: k must be in [2, {MAX_K}]")
    q = q3(k)
    pts = tuple(_key(point_from_sto(point_str(p))) for p in T.point_order)
    if len(pts) != q:
        raise PolicyError(f"{name}: C1 fails, {len(pts)} points != 3k-2 = {q}")
    coords = [Fraction(v) if kind == "int" else (None if v == 0 else Fraction(1, v)) for kind, v in pts]
    if len(set(map(str, coords))) != q:
        raise PolicyError(f"{name}: C2 fails, points not projectively distinct")
    ops = [op_from_sto(op_json(o)) for o in T.ops]
    try:
        states = wmirror.run_rows(k, ops)
    except ValueError as e:
        raise PolicyError(f"{name}: program does not run: {e}")
    if states[-1] != states[0]:
        raise PolicyError(f"{name}: C4 fails, program does not return to start")
    checkpoints = [(op["i"], states[j]) for j, op in enumerate(ops) if op["op"] == "phaseProduct"]
    if len(checkpoints) != q:
        raise PolicyError(f"{name}: {len(checkpoints)} phase products != 3k-2 = {q}")
    rng = random.Random(seed)
    for m in ms:
        c = _coeffs(q, pts, m)
        for _ in range(trials):
            xs, ys, zs = ([rng.randint(-(1 << m), (1 << m) - 1) for _ in range(k)] for _ in range(3))
            x, y, z = (sum(v << (m * j) for j, v in enumerate(s)) for s in (xs, ys, zs))
            acc = Fraction(0)
            for cl, (i, st) in zip(c, checkpoints):
                acc += (cl * sum(a * b for a, b in zip(st[i], xs)) * sum(a * b for a, b in zip(st[i], ys))
                        * sum(a * b for a, b in zip(st[i], zs)))
            if acc != x * y * z:
                raise PolicyError(f"{name}: Toom-Cook identity fails at m={m} ({acc} != {x * y * z})")


def verify_ptp_policy(P: tt.Policy) -> List[str]:
    """Verify every distinct table of a PhaseTripleProduct policy. Return the digests that were checked."""
    if P.mode != tt.TRIPLE:
        raise PolicyError(f"verify_ptp_policy takes a {tt.TRIPLE} policy, got {P.mode!r}")
    seen: List[str] = []
    for b in P.bands:
        if b.table.digest not in seen:
            verify_ptp_table(b.table)
            seen.append(b.table.digest)
    return seen
