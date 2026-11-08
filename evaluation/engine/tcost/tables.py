"""Tables and policies: the parser, the replay of the Lean semantics, the width scan, the interpolation coefficients.

This module is pure Python (no Qualtran). Each rule below transcribes a Lean definition:

  * table semantics        TableGeneration/Basic.lean, Language.lean (`run?`, `expectedRow`)
  * point count            Spec.lean `ProductMode.pointCount` (2k-1 pair, 3k-2 triple)
  * width scan             RecursiveCost/ForShor/Layout.lean + Widths.lean (`nextSignedWidth`),
                           as folded again in Lean-To-Qualtran `forshor_qualtran/width_scan.py`;
                           made general from 2 operand sets (x, z) to `arity` sets (x, y, z)
  * interpolation          ForShor `interpEntry` / `phaseCoeffFromPts` (Coefficients.lean), as in
                           Lean-To-Qualtran `mirrors/coeff_poly.py`; made general from q = 2k-1 to
                           q = pointCount(mode, k)
  * policy                 ForShor Framework/Policy.lean `ShorLoweringPolicy.choose` / `Stops`
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PAIR, TRIPLE = "PhaseProduct", "PhaseTripleProduct"
MODES = (PAIR, TRIPLE)
#: Width rules (PLAN_v2 Stage 13). "exact": the PP/PTP boards' rule (Tight.lean / PTP Exact.lean, floor limbs, no
#: padding). "scan": STO's `nextSignedWidth` scan, the labelled cross-check and the full order-finding run's rule.
WIDTH_RULES = ("exact", "scan")

Point = Tuple[str, int]          # ("int", z) | ("frac", m); frac 0 = infinity, frac m = 1/m
Op = Tuple                       # ("shiftL", i, n) | ("shiftR", i, n) | ("negate", i)
                                 # | ("addScaled", dst, src, negSrc: bool, shift) | ("phaseProduct", i)


class TableError(ValueError):
    pass


#: Input bounds. The check runs BEFORE each replay, so adversarial tables fail fast and do not use all the memory.
#: (A shift of s bits makes the replay build s-bit integers.) The largest shift in the archive is 315, and its
#: largest |point exponent| is 21. The bounds give much room for legitimate tables.
MAX_K = 64
MAX_SHIFT = 4096
MAX_POINT_EXP = 1024
MAX_OPS = 1 << 16


def arity(mode: str) -> int:
    """Return the number of operand registers that the phase acts on."""
    if mode == PAIR:
        return 2
    if mode == TRIPLE:
        return 3
    raise TableError(f"unknown mode {mode!r}")


def point_count(mode: str, k: int) -> int:
    return 2 * k - 1 if arity(mode) == 2 else 3 * k - 2


def parse_point(s: Any) -> Point:
    """Convert an archive spelling to a Point: '0', '-4', 'inf', '1/2', '-1/8'. This mirrors `normalizePoint`
    (frac +-1 -> int)."""
    if isinstance(s, (list, tuple)):
        kind, v = s
        return normalize_point((str(kind), int(v)))
    s = str(s).strip()
    try:
        if s == "inf":
            return ("frac", 0)
        if "/" in s:
            num, den = s.split("/")
            if int(num) not in (1, -1):
                raise TableError(f"point {s!r}: only 1/m and -1/m are table points")
            return normalize_point(("frac", int(num) * int(den)))
        return ("int", int(s))
    except ValueError as e:
        raise TableError(f"unreadable point {s!r}") from e


def normalize_point(p: Point) -> Point:
    if p[0] == "frac" and p[1] in (1, -1):
        return ("int", p[1])
    return p


def point_str(p: Point) -> str:
    if p[0] == "int":
        return str(p[1])
    if p[1] == 0:
        return "inf"
    return f"{'-' if p[1] < 0 else ''}1/{abs(p[1])}"


def parse_op(o: Any) -> Op:
    """Convert the array form of the archive `operations.json`, or the Lean-style dict form, to a canonical tuple."""
    if isinstance(o, dict):
        kind = o["op"]
        if kind in ("shiftL", "shiftR"):
            return (kind, int(o["i"]), int(o["n"]))
        if kind in ("negate", "phaseProduct"):
            return (kind, int(o["i"]))
        if kind == "addScaled":
            return (kind, int(o["dst"]), int(o["src"]), bool(o.get("negSrc", False)), int(o.get("shift", 0)))
        raise TableError(f"unknown op {o!r}")
    kind = o[0]
    if kind in ("shiftL", "shiftR"):
        return (kind, int(o[1]), int(o[2]))
    if kind in ("negate", "phaseProduct"):
        return (kind, int(o[1]))
    if kind == "addScaled":
        sign = int(o[3])
        if sign not in (1, -1):
            raise TableError(f"addScaled source_sign must be +-1, got {sign}")
        return (kind, int(o[1]), int(o[2]), sign == -1, int(o[4]))
    raise TableError(f"unknown op {o!r}")


def op_json(op: Op) -> list:
    if op[0] == "addScaled":
        return ["addScaled", op[1], op[2], -1 if op[3] else 1, op[4]]
    return list(op)


@dataclass(frozen=True)
class Table:
    """One Toom-Cook table: a `Prog k` and the points that its phase products consume, in order."""
    mode: str
    k: int
    point_order: Tuple[Point, ...]
    ops: Tuple[Op, ...]

    # ---- identity: a content digest. Thus the hash is deterministic across processes (PYTHONHASHSEED), it stays
    #      the same after pickle copies the table into worker processes, and it costs O(1) after the first call ---
    def __hash__(self) -> int:
        h = self.__dict__.get("_hash")
        if h is None:
            h = int(self.digest[:16], 16)
            object.__setattr__(self, "_hash", h)
        return h

    @property
    def digest(self) -> str:
        d = self.__dict__.get("_digest")
        if d is None:
            d = hashlib.sha256(json.dumps(self.to_json(), sort_keys=True).encode()).hexdigest()
            object.__setattr__(self, "_digest", d)
        return d

    # ---- construction ------------------------------------------------------------------
    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Table":
        """Parse and replay-check one table. `point_order` is the consumption order (Lean
        `generatePointsInOrder`). `operations` is the program. Each malformed input raises `TableError`."""
        if not isinstance(d, dict):
            raise TableError("a table must be a JSON object")
        for key in ("mode", "k", "point_order", "operations"):
            if key not in d:
                raise TableError(f"table is missing {key!r}")
        order, ops = d["point_order"], d["operations"]
        if not isinstance(order, list) or not isinstance(ops, list) or not ops:
            raise TableError("point_order and operations must be lists (operations non-empty)")
        if len(ops) > MAX_OPS:
            raise TableError(f"{len(ops)} operations exceeds the bound {MAX_OPS}")
        try:
            mode, k = str(d["mode"]), int(d["k"])
            arity(mode)
            parsed_ops = tuple(parse_op(o) for o in ops)
        except (TypeError, ValueError, IndexError, KeyError) as e:
            raise TableError(f"malformed table: {e}") from e
        t = cls(mode=mode, k=k, point_order=tuple(parse_point(p) for p in order), ops=parsed_ops)
        t.check()
        return t

    def to_json(self) -> Dict[str, Any]:
        return {"mode": self.mode, "k": self.k, "point_order": [point_str(p) for p in self.point_order],
                "operations": [op_json(o) for o in self.ops]}

    # ---- census ------------------------------------------------------------------------
    @property
    def q(self) -> int:
        return point_count(self.mode, self.k)

    @property
    def arity(self) -> int:
        return arity(self.mode)

    def census(self) -> Dict[str, int]:
        c = {"shiftL": 0, "shiftR": 0, "negate": 0, "addScaled": 0, "addScaled_negSrc": 0,
             "addScaled_shifted": 0, "phaseProduct": 0}
        for op in self.ops:
            c[op[0]] += 1
            if op[0] == "addScaled":
                c["addScaled_negSrc"] += int(op[3])
                c["addScaled_shifted"] += int(op[4] > 0)
        return c

    # ---- replay of the Lean semantics (independent of Lean; C1, C3, C4 of the contract) ----
    def check(self) -> None:
        """Replay `run?` on the symbolic start state. Make sure that each phase product meets its point.

        C1: q points, distinct after normalization. C3: the l-th `phaseProduct i` finds register i
        equal to `expectedRow(point_order[l])`. C4: the program returns to the start state. (C2,
        unisolvence, is checked when the coefficients are inverted.) The Lean verifier already
        proves all of this for accepted tables. The replay protects the JSON round trip.
        """
        k = self.k
        if not 2 <= k <= MAX_K:
            raise TableError(f"k must be in [2, {MAX_K}], got {k}")
        q = self.q
        if len(self.point_order) != q:
            raise TableError(f"{self.mode} k={k} needs {q} points, got {len(self.point_order)}")
        if len(set(self.point_order)) != q:
            raise TableError("duplicate points")
        for p in self.point_order:
            # This is only a magnitude bound (memory safety of the replay and the interpolation). The contract of a
            # benchmark sets the points that it admits. The Lean spec of ShorTableOptimization limits them to 0,
            # inf and +-2^e (Lean and the verifier check this). The standard tables of ForShor also use -3, for
            # example.
            if abs(p[1]).bit_length() > MAX_POINT_EXP:
                raise TableError(f"point {point_str(p)} exceeds 2^{MAX_POINT_EXP} in magnitude")
        for op in self.ops:                              # check the structural bounds before the replay
            kind = op[0]
            for idx in op[1:3] if kind == "addScaled" else op[1:2]:
                if not 0 <= idx < k:
                    raise TableError(f"register index {idx} out of range in {op}")
            amount = op[2] if kind in ("shiftL", "shiftR") else op[4] if kind == "addScaled" else 0
            if not 0 <= amount <= MAX_SHIFT:
                raise TableError(f"shift amount {amount} outside [0, {MAX_SHIFT}] in {op}")
            if kind == "addScaled" and op[1] == op[2]:
                raise TableError(f"addScaled with dst == src is not a Lean op (OpSafe): {op}")
        state = [[int(i == j) for j in range(k)] for i in range(k)]
        consumed = 0
        for op in self.ops:
            kind = op[0]
            if kind == "shiftL":
                state[op[1]] = [v << op[2] for v in state[op[1]]]
            elif kind == "shiftR":
                if any(v % (1 << op[2]) for v in state[op[1]]):
                    raise TableError(f"shiftR on a non-divisible register: {op}")
                state[op[1]] = [v >> op[2] for v in state[op[1]]]
            elif kind == "negate":
                state[op[1]] = [-v for v in state[op[1]]]
            elif kind == "addScaled":
                _, d, s, neg, sh = op
                sign = -1 if neg else 1
                state[d] = [a + sign * b * (1 << sh) for a, b in zip(state[d], state[s])]
            elif kind == "phaseProduct":
                if consumed >= q:
                    raise TableError("more phase products than points")
                want = expected_row(k, self.point_order[consumed])
                if state[op[1]] != want:
                    raise TableError(f"phaseProduct #{consumed} on r{op[1]}: register {state[op[1]]} "
                                     f"!= expectedRow({point_str(self.point_order[consumed])}) = {want}")
                consumed += 1
        if consumed != q:
            raise TableError(f"{consumed} phase products for {q} points")
        if state != [[int(i == j) for j in range(k)] for i in range(k)]:
            raise TableError("program does not return to the start state")

    # ---- widths --------------------------------------------------------------------------
    def next_width(self, w: int) -> int:
        """The SCAN: `nextSignedWidth` for square operands of width w, from the closed form (`width_form`).
        `_next_width` is the literal Lean scan. It stays as the reference for the certification of the form.
        Since PLAN_v2 Stage 13 the PP and PTP boards price at `exact_width`; the scan is their labelled cross-check
        ("ForShor scanned widths") and the full order-finding run's rule."""
        return next_width_form(self, w)

    scan_width = next_width

    def exact_width(self, w: int) -> int:
        """The EXACT child width (Stage 13): the least width at which every reachable intermediate register value and
        every child operand fits, over the floor-limb box at operand width w (no padding). PhaseProduct:
        ShorTableOptimization's `exact_width` (Lean Tight.lean `PolicyTable.tightWidth`); PhaseTripleProduct: the PTP
        suite's `ptp_tight_width` (Lean PTP/Exact.lean). Both vendored verbatim in `tcost/exact`."""
        from .exact import exact_child_width
        return exact_child_width(self.mode, self.k, self._ops_json(), w)

    def child_width(self, w: int, widths: str = "exact") -> int:
        """The child width under a width rule: "exact" (the PP/PTP boards) or "scan" (`next_width`)."""
        if widths == "exact":
            return self.exact_width(w)
        if widths == "scan":
            return self.next_width(w)
        raise TableError(f"unknown width rule {widths!r} (expected one of {WIDTH_RULES})")

    def _ops_json(self) -> tuple:
        o = self.__dict__.get("_opsj")
        if o is None:
            o = tuple(tuple(op_json(x)) for x in self.ops)
            object.__setattr__(self, "_opsj", o)
        return o

    def limb_widths(self, w: int) -> List[int]:
        limb = w // self.k
        return [(w - i * limb) if i + 1 == self.k else limb for i in range(self.k)]

    # ---- coefficients ----------------------------------------------------------------------
    def coeff_polys(self) -> Tuple[Tuple[Fraction, ...], ...]:
        return _coeff_polys(self.mode, self.k, self.point_order)

    def phase_coeffs(self, limb_width: int) -> List[Fraction]:
        """Return c_l(2^limb) for each phase product, in consumption order: sum_l c_l * prod_ops(p_l) = prod(B)."""
        b = Fraction(2) ** limb_width
        return [_poly_eval(p, b) for p in self.coeff_polys()]


def expected_row(k: int, p: Point) -> List[int]:
    """`expectedRow`: int z -> z^j ; frac m -> m^(k-1-j)."""
    if p[0] == "int":
        return [p[1] ** j for j in range(k)]
    return [p[1] ** (k - 1 - j) for j in range(k)]


def _next_width(ops: Tuple[Op, ...], k: int, w: int) -> int:
    """The literal Lean scan (`scanNeededWidths` + `commonNeededWidth`) on one operand set. Square operands give
    the same scan on each side. The certification of `width_form` uses this function as the reference."""
    limb = w // k
    st = [(w - i * limb) if i + 1 == k else limb for i in range(k)]
    need = max(st)
    for op in ops:
        kind = op[0]
        if kind == "phaseProduct":
            continue
        if kind == "shiftL":
            st[op[1]] += op[2]
        elif kind == "shiftR":
            st[op[1]] = max(0, st[op[1]] - op[2])
        elif kind == "negate":
            st[op[1]] += 1
        elif kind == "addScaled":
            st[op[1]] = 1 + max(st[op[1]], st[op[2]] + op[4])
        need = max(need, st[op[1]])
    return 1 + need


def _interp_entry(q: int, p: Point, j: int) -> Fraction:
    """`interpEntry` at q points: int t -> t^j ; frac c -> c^(q-1-j) (frac 0 = infinity)."""
    if p[0] == "int":
        return Fraction(p[1]) ** j
    return Fraction(p[1]) ** (q - 1 - j)


def _poly_eval(cs: Sequence[Fraction], x: Fraction) -> Fraction:
    acc = Fraction(0)
    for c in reversed(cs):
        acc = c + x * acc
    return acc


@lru_cache(maxsize=4096)
def _coeff_polys(mode: str, k: int, pts: Tuple[Point, ...]) -> Tuple[Tuple[Fraction, ...], ...]:
    """Return the rows of M^-1 with M[j][l] = interpEntry(p_l, j): polys[l][j] = coeff of b^j in c_l(b).

    This is an exact Gauss-Jordan elimination, with the P1 (inverse identity) and P2 (evaluation identity) checks
    of Lean-To-Qualtran `coeff_poly.py`. Here, these checks also certify C2 (unisolvence).
    """
    q = point_count(mode, k)
    M = [[_interp_entry(q, pts[l], j) for l in range(q)] for j in range(q)]
    A = [list(M[i]) + [Fraction(int(i == j)) for j in range(q)] for i in range(q)]
    for col in range(q):
        piv = next((r for r in range(col, q) if A[r][col] != 0), None)
        if piv is None:
            raise TableError("points are not unisolvent (C2)")
        A[col], A[piv] = A[piv], A[col]
        inv = A[col][col]
        A[col] = [v / inv for v in A[col]]
        for r in range(q):
            if r != col and A[r][col] != 0:
                f = A[r][col]
                A[r] = [a - f * b for a, b in zip(A[r], A[col])]
    minv = [A[i][q:] for i in range(q)]
    for m in range(1, 6):                                                    # P2
        b = Fraction(2) ** m
        for j in range(q):
            if sum(_poly_eval(minv[l], b) * _interp_entry(q, pts[l], j) for l in range(q)) != b ** j:
                raise TableError(f"interpolation identity failed at m={m}, j={j}")
    return tuple(tuple(r) for r in minv)


# ---------------------------------------------------------------------------------------------
# Policies (ForShor `ShorLoweringPolicy`)
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Band:
    threshold: int
    table: Table


@dataclass(frozen=True)
class Policy:
    """Width bands, widest first, with strictly decreasing thresholds. One mode for the full recursion."""
    mode: str
    bands: Tuple[Band, ...]
    name: str = field(default="", compare=False)
    #: The width rule the policy is priced under. Not part of a submission: the PP and PTP boards always derive exact
    #: widths themselves (Stage 13); "scan" is the labelled cross-check and the full run's planner.
    widths: str = "exact"

    def __post_init__(self):
        if self.widths not in WIDTH_RULES:
            raise TableError(f"unknown width rule {self.widths!r}")
        ths = [b.threshold for b in self.bands]
        if any(a <= b for a, b in zip(ths, ths[1:])):
            raise TableError(f"band thresholds must strictly decrease, got {ths}")
        for b in self.bands:
            if b.table.mode != self.mode:
                raise TableError(f"band table mode {b.table.mode} != policy mode {self.mode}")

    def choose(self, w: int) -> Optional[Table]:
        """Return the table of the first band with threshold <= w. None = leaf."""
        for b in self.bands:
            if b.threshold <= w:
                return b.table
        return None

    def step(self, w: int) -> Optional[Tuple[Table, int]]:
        """Return (table, child width) if this node recurses. If not, return None (ForShor `Stops`: no table, or no
        shrink)."""
        t = self.choose(w)
        if t is None:
            return None
        w2 = t.child_width(w, self.widths)
        return (t, w2) if w2 < w else None

    def with_widths(self, widths: str) -> "Policy":
        """The same bands under another width rule."""
        return self if widths == self.widths else Policy(self.mode, self.bands, self.name, widths)

    def ladder(self, n: int) -> List[Tuple[int, Optional[int]]]:
        """Return [(width, k or None for leaf), ...] from the top."""
        out, w = [], n
        while True:
            s = self.step(w)
            if s is None:
                out.append((w, None))
                return out
            out.append((w, s[0].k))
            w = s[1]

    @classmethod
    def from_json(cls, d: Dict[str, Any], widths: str = "exact") -> "Policy":
        """Parse a policy. A width ladder or width rule inside the document is NOT read: the width rule is the
        board's (`widths`), never the submitter's (`declared_ladder` reports what was ignored)."""
        mode = str(d["mode"])
        bands = []
        for b in d["bands"]:
            tab = dict(b.get("table", b))
            tab.setdefault("mode", mode)
            th = b["threshold"]
            if type(th) is not int or th < 0:
                raise TableError(f"band threshold must be a non-negative integer, got {th!r}")
            bands.append(Band(th, Table.from_json(tab)))
        return cls(mode=mode, bands=tuple(bands), name=str(d.get("name", "")), widths=widths)

    def to_json(self) -> Dict[str, Any]:
        return {"name": self.name, "mode": self.mode,
                "bands": [{"threshold": b.threshold, **b.table.to_json()} for b in self.bands]}

    @classmethod
    def constant(cls, table: Table, name: str = "", widths: str = "exact") -> "Policy":
        return cls(mode=table.mode, bands=(Band(0, table),), name=name, widths=widths)

    @classmethod
    def leaf(cls, mode: str) -> "Policy":
        return cls(mode=mode, bands=(), name="schoolbook-leaf")


POLICY_WRAPPERS = ("policies", "policy", "recursion", "band_tables")


def policy_docs(doc: Any) -> List[Dict[str, Any]]:
    """Return all policies in a JSON document as tcost policy dicts (the input of `Policy.from_json`). The document
    can be a tcost policy, `{"policy": ...}`, `{"policies": [...]}`, a list of these, an archived `operations.json`
    (schema 2, `recursion`), or the `table-generation-tcount.json` of the verifier (scores with `band_tables`)."""
    if isinstance(doc, list):
        return [p for item in doc for p in policy_docs(item)]
    if not isinstance(doc, dict):
        raise TableError(f"a policy document must be a JSON object or a list, got {type(doc).__name__}")
    # a bare policy can have a string "policy" label
    keys = [k for k in POLICY_WRAPPERS if k in doc and not (k == "policy" and isinstance(doc[k], str) and "bands" in doc)]
    if len(keys) > 1:
        raise TableError(f"ambiguous policy document: it has {keys}")
    key = keys[0] if keys else None
    inner = doc.get(key)
    if key in ("policies", "policy"):
        if not isinstance(inner, (dict, list)):
            raise TableError(f'"{key}" must be an object or a list, got {type(inner).__name__}')
        return policy_docs(inner)
    if key == "recursion":
        if not isinstance(inner, list) or not all(isinstance(r, dict) for r in inner):
            raise TableError('"recursion" must be a list of policies')
        pid = str(doc.get("policy_id", ""))[:12]
        return [{**r, "name": r.get("name") or f"{pid} {r.get('mode')}".strip()} for r in inner]
    if key == "band_tables":
        declared = doc.get("bands")
        if declared is not None and [(b.get("threshold"), b.get("k")) for b in declared] != \
                [(t.get("threshold"), t.get("k")) for t in inner]:
            raise TableError(f"{doc.get('mode')}: bands do not match band_tables")
        return [{"mode": doc.get("mode"), "bands": inner, "name": str(doc.get("policy") or doc.get("mode"))}]
    if "targets" in doc:
        raise TableError("operations.json without a recursion policy (schema 1): it holds tables, not a policy")
    if "mode" not in doc or "bands" not in doc:
        raise TableError('not a policy: needs "mode" and "bands" (or one of the wrappers ' + ", ".join(POLICY_WRAPPERS) + ")")
    return [doc]


def checked_policies(docs: Sequence[Dict[str, Any]]) -> List[Policy]:
    """Return `Policy.from_json` of each doc. Reject the same items that the leaderboard rejects: no policy, no
    bands (Lean reads `[]` as "not entered"; `Policy.leaf` is the schoolbook baseline), and two tables for one k
    (a `Recursion.lean` band names only k)."""
    policies = [Policy.from_json(d) for d in docs]
    if not policies:
        raise TableError("no policy in the document")
    for p in policies:
        if not p.bands:
            raise TableError(f"{p.name or p.mode}: no bands (not entered); Policy.leaf is the schoolbook baseline")
        by_k: Dict[int, str] = {}
        for b in p.bands:
            if by_k.setdefault(b.table.k, b.table.digest) != b.table.digest:
                raise TableError(f"{p.name or p.mode}: two different tables for k={b.table.k} "
                                 "(a Recursion.lean band names only k, so this is not submittable)")
    return policies


def load_policies(path: str | Path) -> List[Policy]:
    return checked_policies(policy_docs(json.loads(Path(path).read_text())))


def load_archive_tables(paths: Iterable[str]) -> List[Tuple[str, Table]]:
    """Return [(policy_id, Table)] from archived `operations.json` files (schema 1)."""
    out = []
    for p in paths:
        d = json.load(open(p))
        for t in d["targets"]:
            out.append((d.get("policy_id", p), Table.from_json(t)))
    return out


# ---------------------------------------------------------------------------------------------
# Symbolic widths (sympy): the same scan with w as an expression, so the costs are closed forms in n
# ---------------------------------------------------------------------------------------------

def sym_limb_widths(k: int, w):
    """Return (lower-limb width, top-limb width) = (floor(w/k), w - (k-1)*floor(w/k)) as sympy expressions."""
    import sympy
    L = sympy.floor(w / k)
    return L, w - (k - 1) * L


#: The sentinel for an absent term in width forms. It is much less than all reachable widths, and integer
#: arithmetic stays exact.
ABSENT = -(1 << 60)


def _present(c: int) -> bool:
    return c > ABSENT // 2


def _canon(f):
    """Remove the dominated terms of max(T + cT, L + cL, c0). Always T >= L (T - L = w mod k), so L + cL
    stays only if cL > cT."""
    cT, cL, c0 = f
    return (cT, cL if cL > cT else ABSENT, c0)


@lru_cache(maxsize=4096)
def width_form(table: Table) -> Tuple[int, int, int]:
    """`nextSignedWidth` in canonical form: W' = 1 + max(T + cT, L + cL, c0), with
    L = floor(w/k) the lower-limb width and T = w - (k-1)*L the top-limb width.

    Each slot width that the scan makes is such a max. (shiftL/negate/addScaled add constants and take maxima,
    and the Nat-truncated shiftR adds a 0 term.) Thus the scan runs on three integers for each slot. The form is
    exact for each w, and the closed form has three terms for all table lengths.
    """
    k = table.k
    cur = [(ABSENT, 0, ABSENT)] * (k - 1) + [(0, ABSENT, ABSENT)]   # lower limbs = L, top limb = T

    def add(f, c):
        return tuple(x + c for x in f)

    def mx(*fs):
        return _canon(tuple(max(f[i] for f in fs) for i in range(3)))

    need = mx(*cur)
    for op in table.ops:
        kind = op[0]
        if kind == "phaseProduct":
            continue
        if kind == "shiftL":
            cur[op[1]] = add(cur[op[1]], op[2])
        elif kind == "shiftR":
            f = add(cur[op[1]], -op[2])
            cur[op[1]] = _canon((f[0], f[1], max(f[2], 0)))      # Nat truncation: max(0, x - n)
        elif kind == "negate":
            cur[op[1]] = add(cur[op[1]], 1)
        elif kind == "addScaled":
            cur[op[1]] = add(mx(cur[op[1]], add(cur[op[2]], op[4])), 1)
        need = mx(need, cur[op[1]])
    return need


def sym_next_width(table: Table, w):
    """Return `nextSignedWidth(w)` as a closed form: 1 + Max(T + cT, L + cL, c0), without the dominated terms."""
    import sympy
    L, top = sym_limb_widths(table.k, w)
    cT, cL, c0 = width_form(table)
    terms = [top + cT] + ([L + cL] if _present(cL) else []) + ([sympy.Integer(c0)] if _present(c0) else [])
    return 1 + (sympy.Max(*terms) if len(terms) > 1 else terms[0])


def next_width_form(table: Table, w: int) -> int:
    """Evaluate the closed form as an integer. The result equals `Table.next_width` (tcost.certify checks this)."""
    L = w // table.k
    T = w - (table.k - 1) * L
    cT, cL, c0 = width_form(table)
    return 1 + max(T + cT, L + cL, c0)


def chain_for(policy: "Policy", n: int) -> Tuple[Table, ...]:
    """Return the tables that a policy uses level by level from width n (the shape for a symbolic cost)."""
    out, w = [], n
    while True:
        s = policy.step(w)
        if s is None:
            return tuple(out)
        out.append(s[0])
        w = s[1]
