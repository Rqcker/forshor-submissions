r"""The family layer (Stage 4 of `PLAN_VERIFIED_PORT_REFACTOR.2026-09-09.md`; angles since TODO-1):
symbolic-size resource estimates that are the CENSUS OF THE PROVED CIRCUIT at every size, not a
model of it.

A family document (`shor_emit family <kind> <k>`, via `emit2.family`) carries

* the canonical phase-product program `ops` and `q = 2k-1`,
* the width rule: `nextWidthOf ops wx wz` as Lean-computed tables (balanced and pairwise), its
  period `k`, and the size `no_trunc_from` from which the PROVED periodic law
  `nextWidthOf (wx + j k) (wz + j k) = nextWidthOf wx wz + j` (`Shor.Family.nextWidthOf_add_mul_period`)
  applies,
* the interpolation coefficients: the inverse interpolation matrix of the canonical points (exact
  rationals) — the l-th recursive child of a node at parent angle φ and limb width W has angle
  φ · coeff_l(W) with coeff_l(W) = Σ_j (2^W)^j · inv[j][l] — plus a Lean-computed table to check
  this evaluator against,
* the census recurrence of the kind (`ppLeaves`/`qftLeaves`/`cmodmulLeaves`/`orderFindingLeaves`),
  each backed by a Lean theorem equating it to `LowGate.leaves` of the lowered circuit
  (`Shor.Family.leaves_lower*`) — leaves carry their exact angles,
* anchors: concrete sizes at which the emitter ran the proved lowering and compared the two censuses
  INSIDE Lean (`agree`).

This module evaluates the recurrence at any size. The only re-implemented pieces are the width scan
(`WidthRule.next`) and the coefficient formula, and neither is trusted: at load each is checked
against every Lean-computed table entry, and the width scan against the proved periodic law; a
mismatch refuses the family. Everything else is exact arithmetic on the leaf multiset.

`family_bloq` turns the census at a size into a Qualtran bloq whose call graph is exactly the leaf
multiset priced by the same per-macro bloqs `py/qualtran_backend.py` uses for the concrete circuit
(`CompactSignedPhaseGrid` with the exact angle for every phase product) — so QECGatesCost of the
family bloq equals that of the concrete IR bloq wherever both exist (`tests/test_family.py`), and
the family bloq extends the estimate to sizes no circuit is unrolled at.

The honest statement (decision D3): T(n) is the Qualtran pricing of a Lean-proved census; the census
is exact at every size and its per-macro T-costs are Qualtran's (named in `trust`).
"""
from __future__ import annotations

import math

from collections import Counter
from fractions import Fraction
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from . import emit2

FAMILY_SCHEMA = "shor.ir/v2-family"

Leaf = Tuple  # ("H",) | ("X",) | ("Prim", tag, w) | ("ShiftL", w, n) | ("ShiftR", w, n) | ("Negate", w)
#               | ("AddScaled", wd, ws, negSrc, shift) | ("Naive_SignedPhaseProd", (num, den), wx, wz)
#               | ("Naive_CSignedPhaseProd", (num, den), wx, wz) | ("RadixReverse", w, m)

AngleLike = Union[Fraction, int, str, Sequence[int]]


class FamilyError(ValueError):
    pass


def angle(a: AngleLike) -> Fraction:
    """An angle in units of π as an exact Fraction, from a Fraction, int, "num/den" or [num, den]."""
    if isinstance(a, Fraction):
        return a
    if isinstance(a, int):
        return Fraction(a)
    if isinstance(a, str):
        return Fraction(a)
    if isinstance(a, (list, tuple)) and len(a) == 2:
        return Fraction(int(a[0]), int(a[1]))
    raise FamilyError(f"not an angle: {a!r}")


def _angle_key(a: Fraction) -> Tuple[int, int]:
    return (a.numerator, a.denominator)


def leaf_key(j: Sequence[Any]) -> Leaf:
    """A wire leaf `[tag, params…]` as a hashable key (nested `[num, den]` angles become tuples)."""
    return tuple(tuple(int(v) for v in x) if isinstance(x, list) else x for x in j)


def census_from_wire(j: Sequence[Sequence[Any]]) -> Counter:
    """`[[leaf, count], …]` -> Counter."""
    c: Counter = Counter()
    for leaf, n in j:
        c[leaf_key(leaf)] += int(n)
    return c


# ----------------------------------------------------------------------------- the width rule
class WidthRule:
    """Mirror of `Shor.Family.nextWidthOf`, checked against the Lean tables at construction."""

    def __init__(self, k: int, ops: List[Dict[str, Any]], width_rule: Dict[str, Any]):
        self.k = int(k)
        self.ops = ops
        self.balanced_table = [int(v) for v in width_rule["balanced_table"]]
        self.pair_table = [[int(v) for v in row] for row in width_rule["pair_table"]]
        self.period = int(width_rule["period"])
        self.no_trunc_from = width_rule.get("no_trunc_from")
        self.pair_no_trunc = [[bool(v) for v in row] for row in width_rule.get("pair_no_trunc", [])]
        self.uncertified: set = set()      # pairs answered by the bare scan, outside every certified region
        if self.period != self.k:
            raise FamilyError(f"period {self.period} != k {self.k}")
        self._check_tables()

    def _scan(self, wx: int, wz: int) -> int:
        k = self.k
        W = min(wx // k, wz // k)
        xw = [W] * k
        zw = [W] * k
        xw[k - 1] = wx - (k - 1) * W
        zw[k - 1] = wz - (k - 1) * W
        xneed, zneed = list(xw), list(zw)
        for op in self.ops:
            kind = op["op"]
            if kind == "shiftL":
                i, n = int(op["i"]), int(op["n"])
                xw[i] += n
                zw[i] += n
            elif kind == "shiftR":
                i, n = int(op["i"]), int(op["n"])
                xw[i] = max(0, xw[i] - n)          # natural subtraction, as in Lean
                zw[i] = max(0, zw[i] - n)
            elif kind == "negate":
                i = int(op["i"])
                xw[i] += 1
                zw[i] += 1
            elif kind == "addScaled":
                d, s_, sh = int(op["dst"]), int(op["src"]), int(op["shift"])
                xw[d] = 1 + max(xw[d], xw[s_] + sh)
                zw[d] = 1 + max(zw[d], zw[s_] + sh)
            elif kind == "phaseProduct":
                pass
            else:  # pragma: no cover
                raise FamilyError(f"unknown op {kind!r} in program")
            for i in range(k):
                if xw[i] > xneed[i]:
                    xneed[i] = xw[i]
                if zw[i] > zneed[i]:
                    zneed[i] = zw[i]
        return 1 + max(max(xneed[i], zneed[i]) for i in range(k))

    def _check_tables(self) -> None:
        for w, v in enumerate(self.balanced_table):
            got = self._scan(w, w)
            if got != v:
                raise FamilyError(f"width-scan mirror disagrees with Lean at balanced w={w}: {got} != {v}")
        for wx, row in enumerate(self.pair_table):
            for wz, v in enumerate(row):
                got = self._scan(wx, wz)
                if got != v:
                    raise FamilyError(f"width-scan mirror disagrees with Lean at ({wx},{wz}): {got} != {v}")
        if self.no_trunc_from is not None:
            w0 = int(self.no_trunc_from)
            for w in range(w0, w0 + 4 * self.k):
                if self._scan(w + self.k, w + self.k) != self._scan(w, w) + 1:
                    raise FamilyError(f"mirror violates the periodic law at w={w}")

    @lru_cache(maxsize=None)
    def next(self, wx: int, wz: int) -> int:
        """`nextWidthOf ops wx wz`. Certified paths, in order: a Lean table entry; a Lean table entry
        plus the PROVED periodic law (`nextWidthOf_add_mul_period`) from a base pair where Lean
        certified `NoTruncScan`; the balanced law beyond `no_trunc_from`. Otherwise the (checked) scan,
        recorded in `uncertified` (TODO-4)."""
        k = self.k
        if wx == wz and wx < len(self.balanced_table):
            return self.balanced_table[wx]
        if wx < len(self.pair_table) and wz < len(self.pair_table[wx]):
            return self.pair_table[wx][wz]
        # reduce (wx, wz) by (j k, j k) into the certified grid, then apply the periodic law
        P = len(self.pair_no_trunc)
        if P:
            j = max(0, (max(wx, wz) - (P - 1) + k - 1) // k)
            bx, bz = wx - j * k, wz - j * k
            if 0 <= bx < P and 0 <= bz < P and self.pair_no_trunc[bx][bz]:
                return self.pair_table[bx][bz] + j
        if wx == wz and self.no_trunc_from is not None and wx >= int(self.no_trunc_from):
            w0 = int(self.no_trunc_from)
            j = (wx - w0) // k
            base = wx - j * k
            if base < len(self.balanced_table):
                return self.balanced_table[base] + j
        self.uncertified.add((wx, wz))
        return self._scan(wx, wz)


# ----------------------------------------------------------------------------- qubit footprint (TODO-2)
class Reserve:
    """Mirror of `RecursivePhaseWorkspace.reserveNeed` and `qftWorkspaceNeed`, checked against the Lean
    tables at construction; gives the allocated qubit footprint of the pp and qft families."""

    def __init__(self, k: int, width: WidthRule, qubits: Dict[str, Any]):
        self.k, self.width = k, width
        self.reserve_table = [(int(a), int(b)) for a, b in qubits["reserve_need_balanced"]]
        self.qft_table = [(int(a), int(b)) for a, b in qubits["qft_workspace_need"]]
        for w, v in enumerate(self.reserve_table):
            if self.reserve(w, w) != v:
                raise FamilyError(f"reserveNeed mirror disagrees with Lean at w={w}: {self.reserve(w, w)} != {v}")
        for w, v in enumerate(self.qft_table):
            if self.qft_need(w) != v:
                raise FamilyError(f"qftWorkspaceNeed mirror disagrees with Lean at w={w}: {self.qft_need(w)} != {v}")

    def _limbs(self, w: int, W: int) -> List[int]:
        return [W] * (self.k - 1) + [w - (self.k - 1) * W]

    @lru_cache(maxsize=None)
    def reserve(self, wx: int, wz: int) -> Tuple[int, int]:
        """`reserveNeed ops wx wz`."""
        k = self.k
        nxt = self.width.next(wx, wz)
        if nxt < max(wx, wz):
            W = min(wx // k, wz // k)
            immx = sum(nxt - l for l in self._limbs(wx, W))
            immz = sum(nxt - l for l in self._limbs(wz, W))
            cx, cz = self.reserve(nxt, nxt)
            return (immx + k * cx, immz + k * cz)
        return (0, 0)

    @lru_cache(maxsize=None)
    def qft_need(self, w: int) -> Tuple[int, int]:
        """`qftWorkspaceNeed ops w`."""
        if w < 2:
            return (0, 0)
        l, r = w // 2, w - w // 2
        p = self.reserve(l + 1, r + 1)
        a, b = self.qft_need(l), self.qft_need(r)
        return (max(1 + p[0], max(a[0], b[0])), max(1 + p[1], max(a[1], b[1])))

    def pp_qubits(self, n: int) -> int:
        """Allocated qubits of the balanced phase-product layout: 2n + both reserves."""
        rx, rz = self.reserve(n, n)
        return 2 * n + rx + rz

    def qft_qubits(self, w: int) -> int:
        """Allocated qubits of the QFT layout: w + both workspace pools."""
        a, b = self.qft_need(w)
        return w + a + b


# ----------------------------------------------------------------------------- coefficients
class Coefficients:
    """`coeffOf k W` from the emitted inverse interpolation matrix, checked against the Lean table."""

    def __init__(self, q: int, coefficients: Dict[str, Any]):
        self.q = q
        self.inverse_rows = [[Fraction(int(a), int(b)) for a, b in row] for row in coefficients["inverse_rows"]]
        if len(self.inverse_rows) != q or any(len(r) != q for r in self.inverse_rows):
            raise FamilyError("inverse matrix is not q × q")
        table = [[Fraction(int(a), int(b)) for a, b in row] for row in coefficients["table"]]
        for W, row in enumerate(table):
            if list(self.at(W)) != list(row):
                raise FamilyError(f"coefficient formula disagrees with Lean at W={W}")

    @lru_cache(maxsize=None)
    def at(self, W: int) -> Tuple[Fraction, ...]:
        b = Fraction(2) ** W
        return tuple(sum((b ** j) * self.inverse_rows[j][l] for j in range(self.q)) for l in range(self.q))


# ----------------------------------------------------------------------------- the recurrences
def step5_constant(c: int, N: int) -> int:
    """`Shor.step5Constant c N`: `(1 + N - cinv) mod N` for the least `cinv < N` with `c·cinv ≡ 1`, else 0.

    T3 (2026-09-13): Lean's definition searches `cinv` over `range N`; a literal transcription is O(N) and never
    returns past ~30-bit N (it stalled every 2048-bit census and wired build). When `gcd(c, N) = 1` the least
    such `cinv` is the unique inverse in `[0, N)`, `pow(c, -1, N)`; otherwise no `cinv` exists and the value is 0.
    `N = 1` has `c·cinv mod 1 = 0 ≠ 1`, so 0 as well. Checked against the brute-force loop in
    `tests/test_t3_leaf_contracts.py`."""
    if N <= 1 or math.gcd(c % N, N) != 1:
        return 0
    cinv = pow(c % N, -1, N)
    return (1 + N - cinv) % N


def step_angles(c: int, N: int, n: int, m: int) -> Tuple[Fraction, Fraction, Fraction]:
    """The three phase angles of `CmodMulInPlaceCore c N` at data width n, work width m."""
    phi1 = Fraction(2 * ((c + N - 1) % N), N)
    phi2 = Fraction(2 * N, 2 ** (m + n + 1))
    phi5 = Fraction(2 * (step5_constant(c, N) % N), N)
    return phi1, phi2, phi5


class Family:
    """One family document, evaluable at any size."""

    def __init__(self, doc: Dict[str, Any]):
        if doc.get("schema") != FAMILY_SCHEMA:
            raise FamilyError(f"not a {FAMILY_SCHEMA} document")
        self.doc = doc
        self.kind = doc["kind"]
        self.k = int(doc["k"])
        self.q = int(doc["q"])
        self.ops = doc["program"]["ops"]
        if sum(1 for op in self.ops if op["op"] == "phaseProduct") != self.q:
            raise FamilyError("program does not have q phase products")
        self.width = WidthRule(self.k, self.ops, doc["width_rule"])
        self.coeff = Coefficients(self.q, doc["coefficients"])
        self.reserve = Reserve(self.k, self.width, doc["qubits"]) if "qubits" in doc else None
        self.theorems = doc.get("theorems", {})
        self._pp: Dict[Tuple[Fraction, int, int], Counter] = {}
        self._cpp: Dict[Tuple[Fraction, int, int], Counter] = {}
        self._qft: Dict[int, Counter] = {}

    # --- body -----------------------------------------------------------------------------
    def _body(self, W: int, phi: Fraction, coeff: Sequence[Fraction], child) -> Counter:
        """`bodyLeaves W phi coeff child 0 ops` as a multiset."""
        out: Counter = Counter()
        n = 0
        for op in self.ops:
            kind = op["op"]
            if kind == "shiftL":
                out[("ShiftL", W, int(op["n"]))] += 2
            elif kind == "shiftR":
                out[("ShiftR", W, int(op["n"]))] += 2
            elif kind == "negate":
                out[("Negate", W)] += 2
            elif kind == "addScaled":
                out[("AddScaled", W, W, bool(op["negSrc"]), int(op["shift"]))] += 2
            elif kind == "phaseProduct":
                if n < self.q:
                    out.update(child(phi * coeff[n]))
                n += 1
        return out

    # --- phase products -------------------------------------------------------------------
    def pp(self, phi: AngleLike, wx: int, wz: int) -> Counter:
        """`ppLeaves ops phi wx wz`."""
        phi = angle(phi)
        key = (phi, wx, wz)
        if key not in self._pp:
            nxt = self.width.next(wx, wz)
            if nxt < max(wx, wz):
                coeff = self.coeff.at(min(wx // self.k, wz // self.k))
                self._pp[key] = self._body(nxt, phi, coeff, lambda th: self.pp(th, nxt, nxt))
            else:
                self._pp[key] = Counter({("Naive_SignedPhaseProd", _angle_key(phi), wx, wz): 1})
        return self._pp[key]

    def cpp(self, phi: AngleLike, wx: int, wz: int) -> Counter:
        """`cppLeaves ops phi wx wz`."""
        phi = angle(phi)
        key = (phi, wx, wz)
        if key not in self._cpp:
            nxt = self.width.next(wx, wz)
            if nxt < max(wx, wz):
                coeff = self.coeff.at(min(wx // self.k, wz // self.k))
                self._cpp[key] = self._body(nxt, phi, coeff, lambda th: self.cpp(th, nxt, nxt))
            else:
                self._cpp[key] = Counter({("Naive_CSignedPhaseProd", _angle_key(phi), wx, wz): 1})
        return self._cpp[key]

    def ladder(self, w: int) -> List[int]:
        """The balanced width ladder from `w` (the recursion depth is its length minus one)."""
        out = [w]
        while True:
            nxt = self.width.next(out[-1], out[-1])
            if nxt < out[-1]:
                out.append(nxt)
            else:
                return out

    # --- qft --------------------------------------------------------------------------------
    def qft(self, w: int) -> Counter:
        """`qftLeaves ops w`."""
        if w not in self._qft:
            if w == 0:
                c = Counter()
            elif w == 1:
                c = Counter({("H",): 1})
            else:
                l, r = w // 2, w - w // 2
                c = Counter()
                c.update(self.qft(r))
                c.update(self.pp(Fraction(2, 2 ** w), l + 1, r + 1))
                c.update(self.qft(l))
                c[("RadixReverse", w, l)] += 1
            self._qft[w] = c
        return self._qft[w]

    # --- Shor -------------------------------------------------------------------------------
    def cmodmul(self, c: int, N: int, n: int, m: int) -> Counter:
        """`cmodmulLeaves ops c N n m` (n data bits, m work bits)."""
        phi1, phi2, phi5 = step_angles(c, N, n, m)
        out: Counter = Counter()
        out[("H",)] += 2 * m
        out.update(self.cpp(phi1, n + 1, m + 1))
        out.update(self.qft(m))
        out.update(self.qft(m))
        out.update(self.qft(n + 1))
        out.update(self.qft(n + 1))
        out.update(self.pp(phi2, m + 1, n + 2))
        out[("Prim", "CMP_GE_CONST", n + 1)] += 1
        out[("Prim", "CSUB_CONST", n + 1)] += 1
        out[("Prim", "CMP_LT_NW", n + 1 + m)] += 1
        out.update(self.cpp(phi5, n + 2, m + 1))
        return out

    def modexp(self, a: int, N: int, n: int, m: int, L: int) -> Counter:
        out: Counter = Counter()
        for e in range(L):
            out.update(self.cmodmul(pow(a, 2 ** e, N), N, n, m))
        return out

    def orderfinding(self, a: int, N: int, n: int, m: int, L: int) -> Counter:
        out: Counter = Counter()
        out[("H",)] += L
        if n != 0:
            out[("X",)] += 1
        out.update(self.modexp(a, N, n, m, L))
        out.update(self.qft(L))
        return out

    # --- dispatch -----------------------------------------------------------------------------
    def qubits(self, **params: Any) -> Optional[int]:
        """Allocated qubit footprint of the layout at `params` (pp, qft), or None where the layout is
        a search (cmodmul, orderfinding: per-document only)."""
        if self.reserve is None:
            return None
        if self.kind == "signed_phase_product":
            return self.reserve.pp_qubits(params["n"])
        if self.kind == "qft":
            return self.reserve.qft_qubits(params["w"])
        return None

    def census(self, **params: Any) -> Counter:
        """The census of this family at the given size parameters."""
        if self.kind == "signed_phase_product":
            return self.pp(params["phi"], params["n"], params["n"])
        if self.kind == "qft":
            return self.qft(params["w"])
        if self.kind == "cmodmul":
            return self.cmodmul(params["c"], params["N"], params["n"], params["m"])
        if self.kind == "orderfinding":
            return self.orderfinding(params["a"], params["N"], params["n"], params["m"], params["L"])
        raise FamilyError(f"unknown kind {self.kind!r}")

    @staticmethod
    def params_of(anchor_params: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for p, v in anchor_params.items():
            out[p] = angle(v) if p == "phi" else int(v)
        return out

    def check_anchors(self) -> List[Dict[str, Any]]:
        """Re-evaluate every Lean anchor here: the Lean `agree` flag, and this evaluator's census must
        equal the Lean-computed family census AND the circuit census."""
        out = []
        for a in self.doc["anchors"]:
            if a.get("refused"):
                out.append({"params": a["params"], "verdict": "REFUSED"})
                continue
            mine = self.census(**self.params_of(a["params"]))
            lean_family = census_from_wire(a["census_family"])
            circuit = census_from_wire(a["census_circuit"])
            ok = bool(a["agree"]) and mine == lean_family and mine == circuit
            out.append({"params": a["params"], "lean_agree": a["agree"], "python_eq_lean_family": mine == lean_family,
                        "python_eq_circuit": mine == circuit, "verdict": "PASS" if ok else "FAIL"})
        return out


def degenerate_multipliers(a: int, N: int, L: int) -> List[int]:
    """Exponent positions e < L whose multiplier a^(2^e) mod N is 1. Such a step's controlled phase
    products have angle 0; since leaves carry their angles, the family prices them exactly as the
    concrete compiler does (a zero-angle grid is empty) — kept as a diagnostic."""
    return [e for e in range(L) if pow(a, 2 ** e, N) == 1]


def load(kind: str, k: int, *, refresh: bool = False) -> Family:
    """Load (emitting if needed) the family document of `kind` in {pp, qft, cmodmul, shor} for arity k."""
    return Family(emit2.family(kind, k, refresh=refresh))


def lean_gate_count(census: Counter) -> int:
    """`Shor.Family.Leaf.shorCost` summed over a census (the Lean cost model, `shorGateCostModel`)."""
    def ripple(w: int) -> int:
        return 9 * w + 2

    def linear(w: int) -> int:
        return 20 * w + 10

    total = 0
    for leaf, n in census.items():
        tag = leaf[0]
        if tag in ("H", "X"):
            c = 1
        elif tag == "Prim":
            c = linear(leaf[2]) if leaf[1] in ("CMP_GE_CONST", "CSUB_CONST", "CMP_LT_NW") else 1
        elif tag in ("ShiftL", "ShiftR"):
            c = 0
        elif tag == "Negate":
            c = leaf[1] + ripple(leaf[1])
        elif tag == "AddScaled":
            c = ripple(leaf[1])
        elif tag == "Naive_SignedPhaseProd":
            c = leaf[2] * leaf[3]
        elif tag == "Naive_CSignedPhaseProd":
            c = 5 * leaf[2] * leaf[3]
        elif tag == "RadixReverse":
            c = 3 * (leaf[2] // 2)
        else:  # pragma: no cover
            raise FamilyError(f"unknown leaf {leaf!r}")
        total += c * n
    return total


# ----------------------------------------------------------------------------- Qualtran
def _bloqs_for_leaf(leaf: Leaf, n: int, N: Optional[int], nd: Optional[int] = None) -> Dict[Any, int]:
    """The Qualtran bloqs one leaf stands for, with the SAME classes `py/qualtran_backend.py` uses for
    the concrete circuit (so the two cost models agree on every leaf, angles included)."""
    from qualtran.bloqs.arithmetic import AddK, GreaterThanConstant
    from qualtran.bloqs.basic_gates import Hadamard, TwoBitSwap, XGate
    from qualtran import QUInt

    from . import paths
    paths.setup_legacy_sys_path()
    from forshor_qualtran_lowgate import ForShorAddScaled, ForShorNegate, ForShorShiftL, ForShorShiftR  # noqa: E402
    from qualtran_backend import CompactSignedPhaseGrid, CmpLtNW  # noqa: E402

    tag = leaf[0]
    if tag == "H":
        return {Hadamard(): n}
    if tag == "X":
        return {XGate(): n}
    if tag == "ShiftL":
        return {ForShorShiftL(leaf[1], leaf[2]): n}
    if tag == "ShiftR":
        return {ForShorShiftR(leaf[1], leaf[2]): n}
    if tag == "Negate":
        return {ForShorNegate(leaf[1]): n}
    if tag == "AddScaled":
        return {ForShorAddScaled(leaf[1], leaf[2], bool(leaf[3]), leaf[4]): n}
    if tag in ("Naive_SignedPhaseProd", "Naive_CSignedPhaseProd"):
        (num, den), wx, wz = leaf[1], leaf[2], leaf[3]
        return {CompactSignedPhaseGrid(wx, wz, num, den, controlled=(tag == "Naive_CSignedPhaseProd")): n}
    if tag == "RadixReverse":
        w, m = leaf[1], leaf[2]
        swaps = 0 if m == 0 or m == w else m * (w - 1)
        return {TwoBitSwap(): n * swaps} if swaps else {}
    if tag == "Prim":
        w = leaf[2]
        const = int(N) if N is not None else (1 << (w - 1)) + 1
        if leaf[1] == "CMP_GE_CONST":
            return {GreaterThanConstant(w, const - 1): n}
        if leaf[1] == "CMP_LT_NW":
            # T2c (2026-09-13): the faithful cross-register comparator; `nd` = dataCarry width (n + 1) from
            # the family's own parameters, `w - nd` = work width
            if nd is None:
                raise FamilyError("CMP_LT_NW pricing needs the dataCarry width (family params n)")
            return {CmpLtNW(nd, w - nd, const): n}
        if leaf[1] == "CSUB_CONST":
            return {AddK(QUInt(w), k=-const).controlled(): n}
        raise FamilyError(f"unpriced primitive {leaf[1]!r}")
    raise FamilyError(f"unknown leaf {leaf!r}")


def family_bloq(fam: Family, N: Optional[int] = None, **params: Any):
    """A Qualtran bloq whose call graph is the family census at `params`."""
    import attrs
    from qualtran import Bloq, QAny, Register, Signature
    from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

    full = dict(params)
    if N is not None and fam.kind in ("cmodmul", "orderfinding"):
        full.setdefault("N", N)
    census = fam.census(**full)
    label = f"{fam.kind}(k={fam.k}, " + ", ".join(f"{p}={v}" for p, v in sorted(full.items(), key=lambda kv: kv[0])) + ")"
    mod = N if N is not None else full.get("N")

    @attrs.frozen
    class LeanFamilyBloq(Bloq):
        """The census of the proved circuit family at one size, as a Qualtran bloq."""
        name: str
        n_qubits: int

        @property
        def signature(self) -> Signature:
            return Signature([Register("q", QAny(max(1, self.n_qubits)))])

        def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
            counts: Dict[Any, int] = {}
            nd = int(full["n"]) + 1 if "n" in full else None
            for leaf, cnt in census.items():
                for b, c in _bloqs_for_leaf(leaf, cnt, mod, nd).items():
                    counts[b] = counts.get(b, 0) + c
            return counts

        def __str__(self) -> str:
            return f"LeanFamilyBloq[{self.name}]"

    width = max([int(v) for p, v in params.items() if p in ("n", "m", "L", "w")] + [1])
    return LeanFamilyBloq(label, 2 * width)


def cost(fam: Family, N: Optional[int] = None, **params: Any):
    """`QECGatesCost` of the family at `params`."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    return get_cost_value(family_bloq(fam, N, **params), QECGatesCost())
