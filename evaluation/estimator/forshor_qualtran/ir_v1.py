r"""`forshor.ir/v1` reader and interpreter for the extracted, n-free symbolic IR.

ForShor's `forshor_emit template <k>` / `forshor_submission` print the IR that its reflection
extractor reads from the verified lowering chain (`FastMultiplication/Emit/IR/`). The IR is a small
first-order language of parameterised templates (`WExpr`, `AExpr`, `RegExpr`, `Prop'`, `Node`,
`Template`, `Doc`). In the JSON, each expression is a *rendered formula string* (`IR/Json.lean`).
Thus, to read a document, the reader parses those strings back into the same trees.

This module is the Python side of that contract:

* :func:`parse_doc` changes a `forshor.ir/v1` JSON document into typed trees (:class:`Doc`).
  The context sets the type of each parse, as the grammar of the Lean printer must have it.
  `(a / 2)` is `AExpr.div2` when `a` has an angle value, and `AExpr.ratio` when `a` has a width
  value. The angle variables of a template are exactly its `aParams`.
* :func:`instantiate` unrolls a template at concrete widths. It is a line-by-line mirror of
  `evalNode` in `IR/Instantiate.lean`: truncated `ℕ` subtraction, floor division, new callee
  environments (a `call` never sees the bindings of its caller), `fuel` on `call` only,
  right-folded `seq`, and exact rational angles in units of π. It refuses `ext` if the parts are
  not disjoint, and `grow` saturates. `CPhase`/`CCPhase` expand exactly as
  `LowGate.CPhase`/`LowGate.CCPhase` (`PhaseProduct/Gates/NaiveLeaf.lean`) expand them, with the
  qubit-equality branches.

The output is a flat list of `LowGate` leaves in the `forshor.lowgate/v2` JSON vocabulary
(`Emit/Json/LowGateJson.lean`). Thus, an instantiated template diffs directly against
`forshor_emit pp … --flat`, and it runs through the existing v2 backends.

An :class:`Oracle` supplies the opaque functions (`nextWidth`, `reserveNeed_x/z`, `coeff`, …).
A template can have a ``table`` tag (:mod:`policy` sets it when it composes a multi-table
policy). The oracle receives this tag. Thus, one document can contain templates specialised to
*different* Toom-Cook tables.

Nothing in this module is a theorem. Refer to "What is and is not a theorem" in the ForShor
`Emit/README.md`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from . import schemas

SCHEMA = schemas.FORSHOR_IR_V1


class IRv1Error(ValueError):
    pass


# ============================================================================ trees
# The trees are plain tuples, with the Lean constructor name as the tag. Equality is structural, as in Lean's `BEq`.
#
#   WExpr  : ("var", s) ("lit", n) ("add"|"sub"|"mul"|"div"|"max"|"min", a, b) ("opaque", fn, (args,))
#   AExpr  : ("var", s) ("lit", Fraction) ("mul", a, w) ("coeff", phi, l, m) ("div2", a) ("neg", a)
#            ("signedPair", phi, xi, xw, zi, zw) ("qftPhi", m) ("ratio", num, denom)
#   RegExpr: ("var", s) ("activeSlice"|"reserveSlice", r, lo, hi) ("ext", a, b) ("qubit", r, i)
#            ("grow", r, n)
#   Prop'  : ("lt"|"le"|"eq", a, b) ("testBit", n, i)
#   Node   : ("op", name, (regs,), (nats,), angle|None, (flags,)) ("seq", (body,)) ("adj", body)
#            ("cond", guard, then, else) ("call", template, (wArgs,), (aArgs,), (rArgs,))
#            ("loop", var, lo, hi, body)

Tree = tuple


@dataclass(frozen=True)
class Template:
    name: str
    wParams: Tuple[str, ...]
    aParams: Tuple[str, ...]
    rParams: Tuple[str, ...]
    body: Tree
    provenance: str = ""
    #: The Toom-Cook table that the opaque calls of this template (`nextWidth`, `coeff`, …) refer to.
    #: `None` for an extracted single-table document. :mod:`policy` sets it for each band.
    table: Optional[str] = None


@dataclass
class Doc:
    templates: Dict[str, Template]
    opaque: Dict[str, int]
    entry: str
    meta: Dict[str, Any] = field(default_factory=dict)

    def template(self, name: str) -> Template:
        try:
            return self.templates[name]
        except KeyError:
            raise IRv1Error(f"instantiate: unknown template {name}") from None


# ============================================================================ formula parser
_TOKEN = re.compile(r"""
    (?P<angle>-?\d+/\d+·π)            # AExpr.lit, rendered `num/den·π` with no spaces
  | (?P<num>\d+)
  | (?P<op><=|div\b|[-+*/<=(),\[\]:.])
  | (?P<id>[A-Za-z_][A-Za-z0-9_@']*)
  | (?P<ws>\s+)
""", re.VERBOSE)


def _tokenize(s: str) -> List[Tuple[str, str]]:
    out, pos = [], 0
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m:
            raise IRv1Error(f"cannot tokenize formula {s!r} at {pos}: {s[pos:pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group(kind)))
    return out


class _P:
    """Recursive descent over the untyped shape of a rendered formula.

    Generic nodes: ("num", n) ("angle", Fraction) ("id", s) ("app", fn, [args])
    ("bin", op, a, b) ("bin3", a, b, c) ("neg", a) ("slice", base, side, lo, hi) ("index", base, i).
    """

    def __init__(self, s: str):
        self.s, self.toks, self.i = s, _tokenize(s), 0

    def peek(self, k: int = 0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else (None, None)

    def take(self, value: Optional[str] = None):
        tok = self.peek()
        if tok[0] is None or (value is not None and tok[1] != value):
            raise IRv1Error(f"formula {self.s!r}: expected {value!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def parse(self):
        t = self.expr()
        if self.i != len(self.toks):
            raise IRv1Error(f"formula {self.s!r}: trailing tokens {self.toks[self.i:]}")
        return t

    def expr(self):
        kind, val = self.peek()
        if kind == "angle":
            self.take()
            num, den = val[:-2].split("/")
            node = ("angle", Fraction(int(num), int(den)))
        elif kind == "num":
            self.take()
            node = ("num", int(val))
        elif kind == "id":
            self.take()
            if self.peek()[1] == "(":
                self.take("(")
                args = []
                if self.peek()[1] != ")":
                    args.append(self.expr())
                    while self.peek()[1] == ",":
                        self.take(",")
                        args.append(self.expr())
                self.take(")")
                node = ("app", val, args)
            else:
                node = ("id", val)
        elif val == "(":
            self.take("(")
            if self.peek()[1] == "-":                      # `(-a)`: AExpr.neg
                self.take("-")
                node = ("neg", self.expr())
            else:
                a = self.expr()
                if self.peek()[1] == ")" and a[0] == "angle" and a[1] < 0:
                    # `(-1/4·π)` is `neg (lit 1/4)`: the tokenizer read the sign into the literal
                    self.take(")")
                    return ("neg", ("angle", -a[1]))
                op = self.take()[1]
                b = self.expr()
                if self.peek()[1] == "*" and op == "*":     # the `(phi * s(..) * s(..))` of signedPair
                    self.take("*")
                    node = ("bin3", a, b, self.expr())
                else:
                    node = ("bin", op, a, b)
            self.take(")")
        else:
            raise IRv1Error(f"formula {self.s!r}: unexpected token {val!r}")
        # postfix register syntax: `r.active[lo:hi]`, `r.reserve[lo:hi]`, `r[i]`
        while self.peek()[1] in (".", "["):
            if self.peek()[1] == ".":
                self.take(".")
                side = self.take()[1]
                if side not in ("active", "reserve"):
                    raise IRv1Error(f"formula {self.s!r}: unknown register side {side!r}")
                self.take("[")
                lo = self.expr()
                self.take(":")
                hi = self.expr()
                self.take("]")
                node = ("slice", node, side, lo, hi)
            else:
                self.take("[")
                idx = self.expr()
                self.take("]")
                node = ("index", node, idx)
        return node


_WBIN = {"+": "add", "-": "sub", "*": "mul", "div": "div"}


def _to_w(t) -> Tree:
    tag = t[0]
    if tag == "num":
        return ("lit", t[1])
    if tag == "id":
        return ("var", t[1])
    if tag == "bin" and t[1] in _WBIN:
        return (_WBIN[t[1]], _to_w(t[2]), _to_w(t[3]))
    if tag == "app":
        if t[1] in ("max", "min") and len(t[2]) == 2:
            return (t[1], _to_w(t[2][0]), _to_w(t[2][1]))
        return ("opaque", t[1], tuple(_to_w(a) for a in t[2]))
    raise IRv1Error(f"not a width expression: {t!r}")


def _is_angle(t, avars) -> bool:
    tag = t[0]
    if tag == "angle" or tag == "bin3" or tag == "neg":
        return True
    if tag == "id":
        return t[1] in avars
    if tag == "app":
        return t[1] == "qftPhi"
    if tag == "bin":
        # `WExpr` renders floor division as `div`, never as `/`. Thus, a `/` node is always an angle:
        # `AExpr.div2` (`(a / 2)`) or `AExpr.ratio` (`(num / denom)`). A `*` node is `AExpr.mul`
        # exactly when its left factor is an angle (`(a * w)`, `(phi * coeff(..))`). `+`/`-` are
        # width-only. Without the `/` rule, the parser gave `((n / d) * w) / 2` (div2 of a mul of
        # a ratio) the incorrect type of a width ratio.
        if t[1] == "/":
            return True
        if t[1] == "*":
            return _is_angle(t[2], avars)
        return False
    return False


def _to_a(t, avars) -> Tree:
    tag = t[0]
    if tag == "angle":
        return ("lit", t[1])
    if tag == "id":
        if t[1] not in avars:
            raise IRv1Error(f"{t[1]!r} used as an angle but is not an angle parameter {sorted(avars)}")
        return ("var", t[1])
    if tag == "neg":
        return ("neg", _to_a(t[1], avars))
    if tag == "app" and t[1] == "qftPhi" and len(t[2]) == 1:
        return ("qftPhi", _to_w(t[2][0]))
    if tag == "bin3":
        phi, sx, sz = t[1], t[2], t[3]
        for s in (sx, sz):
            if not (s[0] == "app" and s[1] == "signedBitWeight" and len(s[2]) == 2):
                raise IRv1Error(f"malformed signedPair factor {s!r}")
        # the rendering is signedBitWeight(w, i): width first, index second
        return ("signedPair", _to_a(phi, avars), _to_w(sx[2][1]), _to_w(sx[2][0]),
                _to_w(sz[2][1]), _to_w(sz[2][0]))
    if tag == "bin" and t[1] == "*":
        right = t[3]
        if right[0] == "app" and right[1] == "coeff" and len(right[2]) == 2:
            l = right[2][0]
            if l[0] != "num":
                raise IRv1Error(f"coeff index must be a literal, got {l!r}")
            return ("coeff", _to_a(t[2], avars), l[1], _to_w(right[2][1]))
        return ("mul", _to_a(t[2], avars), _to_w(right))
    if tag == "bin" and t[1] == "/":
        if _is_angle(t[2], avars):
            if t[3] != ("num", 2):
                raise IRv1Error(f"angle division by {t[3]!r}: only `/ 2` (div2) exists")
            return ("div2", _to_a(t[2], avars))
        return ("ratio", _to_w(t[2]), _to_w(t[3]))
    raise IRv1Error(f"not an angle expression: {t!r}")


def _to_r(t) -> Tree:
    tag = t[0]
    if tag == "id":
        return ("var", t[1])
    if tag == "slice":
        return ("activeSlice" if t[2] == "active" else "reserveSlice", _to_r(t[1]), _to_w(t[3]), _to_w(t[4]))
    if tag == "index":
        return ("qubit", _to_r(t[1]), _to_w(t[2]))
    if tag == "app" and t[1] == "ext" and len(t[2]) == 2:
        return ("ext", _to_r(t[2][0]), _to_r(t[2][1]))
    if tag == "app" and t[1] == "grow" and len(t[2]) == 2:
        return ("grow", _to_r(t[2][0]), _to_w(t[2][1]))
    raise IRv1Error(f"not a register expression: {t!r}")


_PBIN = {"<": "lt", "<=": "le", "=": "eq"}


def _to_p(t) -> Tree:
    if t[0] == "bin" and t[1] in _PBIN:
        return (_PBIN[t[1]], _to_w(t[2]), _to_w(t[3]))
    if t[0] == "app" and t[1] == "testBit" and len(t[2]) == 2:
        return ("testBit", _to_w(t[2][0]), _to_w(t[2][1]))
    raise IRv1Error(f"not a guard: {t!r}")


def parse_w(s: str) -> Tree:
    return _to_w(_P(s).parse())


def parse_a(s: str, avars: Sequence[str]) -> Tree:
    return _to_a(_P(s).parse(), frozenset(avars))


def parse_r(s: str) -> Tree:
    return _to_r(_P(s).parse())


def parse_p(s: str) -> Tree:
    return _to_p(_P(s).parse())


def _parse_node(j: Dict[str, Any], avars) -> Tree:
    op = j.get("op")
    if op == "seq":
        return ("seq", tuple(_parse_node(b, avars) for b in j["body"]))
    if op == "adj":
        return ("adj", _parse_node(j["body"], avars))
    if op == "cond":
        return ("cond", parse_p(j["guard"]), _parse_node(j["then"], avars), _parse_node(j["else"], avars))
    if op == "call":
        return ("call", j["template"], tuple(parse_w(w) for w in j["wArgs"]),
                tuple(parse_a(a, avars) for a in j["aArgs"]), tuple(parse_r(r) for r in j["rArgs"]))
    if op == "loop":
        return ("loop", j["var"], parse_w(j["lo"]), parse_w(j["hi"]), _parse_node(j["body"], avars))
    if not isinstance(op, str):
        raise IRv1Error(f"node without an op: {str(j)[:120]}")
    angle = j.get("angle")
    return ("op", op, tuple(parse_r(r) for r in j["regs"]), tuple(parse_w(w) for w in j["nats"]),
            None if angle is None else parse_a(angle, avars), tuple(bool(f) for f in j["flags"]))


def parse_template(j: Dict[str, Any]) -> Template:
    a = tuple(j["aParams"])
    return Template(name=j["name"], wParams=tuple(j["wParams"]), aParams=a,
                    rParams=tuple(j["rParams"]), body=_parse_node(j["body"], frozenset(a)),
                    provenance=j.get("provenance", ""))


def find_ir(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The `forshor.ir/v1` object inside the ForShor output. This is the IR itself, a
    `forshor.submission/v1` document (`"ir"`), or a `forshor.emit/v1` bundle (`"template"`)."""
    if doc.get("schema") == SCHEMA:
        return doc
    for key in ("ir", "template"):
        sub = doc.get(key)
        if isinstance(sub, dict) and sub.get("schema") == SCHEMA:
            return sub
    raise IRv1Error(f"no {SCHEMA} document found (schema={doc.get('schema')!r}, keys={sorted(doc)[:8]})")


def parse_doc(doc: Union[Dict[str, Any], str, Path]) -> Doc:
    if not isinstance(doc, dict):
        doc = json.loads(Path(doc).read_text(encoding="utf-8"))
    ir = find_ir(doc)
    templates = {}
    for tj in ir["templates"]:
        t = parse_template(tj)
        if t.name in templates:
            raise IRv1Error(f"duplicate template {t.name}")
        templates[t.name] = t
    if ir["entry"] not in templates:
        raise IRv1Error(f"entry {ir['entry']!r} is not a template")
    return Doc(templates=templates, opaque={o["name"]: int(o["arity"]) for o in ir["opaque"]},
               entry=ir["entry"])


# ============================================================================ interpreter
@dataclass(frozen=True)
class ExtReg:
    """`ExtReg`: ordered qubit lists, not ranges (IR/README "Registers")."""
    active: Tuple[int, ...]
    reserve: Tuple[int, ...] = ()

    @property
    def width(self) -> int:
        return len(self.active)

    @property
    def capacity(self) -> int:
        return len(self.reserve)

    def grow(self, n: int) -> "ExtReg":
        return ExtReg(self.active + self.reserve[:n], self.reserve[n:])

    def json(self) -> Dict[str, List[int]]:
        return {"active": list(self.active), "reserve": list(self.reserve)}


class Oracle:
    """The opaque side of instantiation (`IR.Env.opaqueW` / `IR.Env.coeff`).

    ``table`` is the tag of the calling template (`Template.table`). A single-table oracle ignores
    it. :class:`policy.PolicyOracle` dispatches on it. A ``None`` result is an error, never a default.
    """

    def opaque_w(self, fn: str, args: Sequence[int], table: Optional[str]) -> Optional[int]:
        return _ARITH.get(fn, lambda *_: None)(*args) if fn in _ARITH else None

    def coeff(self, l: int, m: int, table: Optional[str]) -> Optional[Fraction]:
        return None


def _log2(n: int) -> int:
    return n.bit_length() - 1 if n > 0 else 0          # Nat.log2 0 = 0


def _step5(c: int, N: int) -> int:
    for cinv in range(N):
        if (c * cinv) % N == 1 % N:
            return (1 + N - cinv) % N
    return 0


#: The table-independent opaques (IR/README "Opaque functions": "a consumer implements them").
_ARITH: Dict[str, Callable[..., Optional[int]]] = {
    "log2": _log2,
    "mod": lambda m, n: m % n if n else m,             # Nat.mod m 0 = m
    "pow": lambda b, e: b ** e,
    "modpow": lambda b, e, n: pow(b, 1 << e, n) if n else b ** (1 << e),
    "step5Const": _step5,
}


@dataclass
class _Env:
    w: Dict[str, int]
    a: Dict[str, Fraction]
    r: Dict[str, ExtReg]
    table: Optional[str]


def signed_bit_weight(width: int, i: int) -> int:
    """`signedBitWeight` (NaiveLeaf.lean): the top bit is negative."""
    return -(1 << i) if i + 1 == width else (1 << i)


class Instantiator:
    def __init__(self, doc: Doc, oracle: Oracle):
        self.doc, self.oracle = doc, oracle

    # ---- expressions
    def w(self, env: _Env, e: Tree) -> int:
        tag = e[0]
        if tag == "var":
            if e[1] not in env.w:
                raise IRv1Error(f"instantiate: unbound width var {e[1]}")
            return env.w[e[1]]
        if tag == "lit":
            return e[1]
        if tag == "opaque":
            vs = [self.w(env, a) for a in e[2]]
            v = self.oracle.opaque_w(e[1], vs, env.table)
            if v is None:
                raise IRv1Error(f"instantiate: opaque {e[1]} undefined at {vs} (table={env.table})")
            return v
        a, b = self.w(env, e[1]), self.w(env, e[2])
        if tag == "add":
            return a + b
        if tag == "sub":
            return max(0, a - b)                           # ℕ truncated subtraction
        if tag == "mul":
            return a * b
        if tag == "div":
            return a // b if b else 0                      # Lean: n / 0 = 0
        if tag == "max":
            return max(a, b)
        if tag == "min":
            return min(a, b)
        raise IRv1Error(f"unknown WExpr {tag}")

    def a(self, env: _Env, e: Tree) -> Fraction:
        tag = e[0]
        if tag == "var":
            if e[1] not in env.a:
                raise IRv1Error(f"instantiate: unbound angle var {e[1]}")
            return env.a[e[1]]
        if tag == "lit":
            return Fraction(e[1])
        if tag == "mul":
            return self.a(env, e[1]) * self.w(env, e[2])
        if tag == "coeff":
            m = self.w(env, e[3])
            c = self.oracle.coeff(e[2], m, env.table)
            if c is None:
                raise IRv1Error(f"instantiate: coeff undefined at l={e[2]} m={m} (table={env.table})")
            return self.a(env, e[1]) * c
        if tag == "div2":
            return self.a(env, e[1]) / 2
        if tag == "neg":
            return -self.a(env, e[1])
        if tag == "signedPair":
            phi = self.a(env, e[1])
            xi, xw, zi, zw = (self.w(env, x) for x in e[2:6])
            return phi * signed_bit_weight(xw, xi) * signed_bit_weight(zw, zi)
        if tag == "qftPhi":
            return Fraction(2, 2 ** self.w(env, e[1]))
        if tag == "ratio":
            num, den = self.w(env, e[1]), self.w(env, e[2])
            return Fraction(num, den) if den else Fraction(0)
        raise IRv1Error(f"unknown AExpr {tag}")

    def p(self, env: _Env, e: Tree) -> bool:
        a, b = self.w(env, e[1]), self.w(env, e[2])
        if e[0] == "lt":
            return a < b
        if e[0] == "le":
            return a <= b
        if e[0] == "eq":
            return a == b
        if e[0] == "testBit":
            return bool((a >> b) & 1)
        raise IRv1Error(f"unknown Prop' {e[0]}")

    def r(self, env: _Env, e: Tree) -> ExtReg:
        tag = e[0]
        if tag == "var":
            if e[1] not in env.r:
                raise IRv1Error(f"instantiate: unbound register var {e[1]}")
            return env.r[e[1]]
        if tag in ("activeSlice", "reserveSlice"):
            rv = self.r(env, e[1])
            lo, hi = self.w(env, e[2]), self.w(env, e[3])
            src = rv.active if tag == "activeSlice" else rv.reserve
            return ExtReg(src[lo:][:max(0, hi - lo)])
        if tag == "ext":
            a, b = self.r(env, e[1]), self.r(env, e[2])
            if set(a.active) & set(b.active):
                raise IRv1Error("instantiate: ext active/reserve not disjoint")
            return ExtReg(a.active, b.active)
        if tag == "qubit":
            rv, i = self.r(env, e[1]), self.w(env, e[2])
            if i >= rv.width:
                raise IRv1Error(f"instantiate: qubit index {i} out of range")
            return ExtReg((rv.active[i],))
        if tag == "grow":
            return self.r(env, e[1]).grow(self.w(env, e[2]))
        raise IRv1Error(f"unknown RegExpr {tag}")

    # ---- gates
    @staticmethod
    def _qb(name: str, r: ExtReg) -> int:
        if len(r.active) != 1:
            raise IRv1Error(f"instantiate: {name} expected a single-qubit register")
        return r.active[0]

    @staticmethod
    def _angle(a: Fraction) -> Dict[str, Any]:
        return {"num": a.numerator, "den": a.denominator, "unit": "pi"}

    def _cphase(self, c: int, t: int, th: Fraction) -> List[Dict[str, Any]]:
        """`LowGate.CPhase` (NaiveLeaf.lean)."""
        if c == t:
            return [{"op": "Phase", "q": c, "angle": self._angle(th)}]
        return [{"op": "Phase", "q": c, "angle": self._angle(th / 2)},
                {"op": "Phase", "q": t, "angle": self._angle(th / 2)},
                {"op": "CNOT", "ctrl": c, "target": t},
                {"op": "Phase", "q": t, "angle": self._angle(-th / 2)},
                {"op": "CNOT", "ctrl": c, "target": t}]

    def _ccphase(self, a: int, b: int, c: int, th: Fraction) -> List[Dict[str, Any]]:
        """`LowGate.CCPhase` (NaiveLeaf.lean), with all three equality branches."""
        if a == b:
            return self._cphase(a, c, th)
        if a == c or b == c:
            return self._cphase(a, b, th)
        return [*self._cphase(a, b, th / 2),
                {"op": "Phase", "q": c, "angle": self._angle(th / 2)},
                {"op": "Toffoli", "c1": a, "c2": b, "target": c},
                {"op": "Phase", "q": c, "angle": self._angle(-th / 2)},
                {"op": "Toffoli", "c1": a, "c2": b, "target": c}]

    def _build(self, name, regs, nats, angle, flags) -> List[Dict[str, Any]]:
        """`buildLowGate`. Returns the flat leaves (`LowGate.flattenSeq` of the gate)."""
        sig = (len(regs), len(nats), angle is not None, len(flags))
        qb = lambda r: self._qb(name, r)
        if name == "id" and sig == (0, 0, False, 0):
            return []
        if name in ("H", "X") and sig == (1, 0, False, 0):
            return [{"op": name, "q": qb(regs[0])}]
        if name == "Phase" and sig == (1, 0, True, 0):
            return [{"op": "Phase", "q": qb(regs[0]), "angle": self._angle(angle)}]
        if name == "CPhase" and sig == (2, 0, True, 0):
            return self._cphase(qb(regs[0]), qb(regs[1]), angle)
        if name == "CCPhase" and sig == (3, 0, True, 0):
            return self._ccphase(qb(regs[0]), qb(regs[1]), qb(regs[2]), angle)
        if name == "CNOT" and sig == (2, 0, False, 0):
            return [{"op": "CNOT", "ctrl": qb(regs[0]), "target": qb(regs[1])}]
        if name == "Toffoli" and sig == (3, 0, False, 0):
            return [{"op": "Toffoli", "c1": qb(regs[0]), "c2": qb(regs[1]), "target": qb(regs[2])}]
        if name in ("ShiftL", "ShiftR", "zeroExtend", "signExtend", "zeroDealloc", "signDealloc") \
                and sig == (1, 1, False, 0):
            return [{"op": name, "r": regs[0].json(), "n": nats[0]}]
        if name == "Negate" and sig == (1, 0, False, 0):
            return [{"op": "Negate", "r": regs[0].json()}]
        if name == "AddScaled" and sig == (2, 1, False, 1):
            return [{"op": "AddScaled", "dst": regs[0].json(), "src": regs[1].json(),
                     "negSrc": flags[0], "shift": nats[0]}]
        if name == "RadixReverse" and sig == (1, 1, False, 0):
            return [{"op": "RadixReverse", "r": list(regs[0].active), "m": nats[0]}]
        raise IRv1Error(f"instantiate: unrecognised/malformed LowGate op {name} {sig}")

    def node(self, env: _Env, n: Tree, fuel: int) -> List[Dict[str, Any]]:
        """`evalNode`, which returns `LowGate.flattenSeq` of the result. Adjoints stay grouped."""
        tag = n[0]
        if tag == "op":
            regs = [self.r(env, x) for x in n[2]]
            nats = [self.w(env, x) for x in n[3]]
            angle = None if n[4] is None else self.a(env, n[4])
            return self._build(n[1], regs, nats, angle, list(n[5]))
        if tag == "seq":
            out: List[Dict[str, Any]] = []
            for b in n[1]:
                out.extend(self.node(env, b, fuel))
            return out
        if tag == "adj":
            body = self.node(env, n[1], fuel)
            return [{"op": "adj", "body": body}]
        if tag == "cond":
            return self.node(env, n[2] if self.p(env, n[1]) else n[3], fuel)
        if tag == "call":
            if fuel == 0:
                raise IRv1Error(f"instantiate: fuel exhausted at call {n[1]}")
            t = self.doc.template(n[1])
            wv = [self.w(env, x) for x in n[2]]
            av = [self.a(env, x) for x in n[3]]
            rv = [self.r(env, x) for x in n[4]]
            if (len(wv), len(av), len(rv)) != (len(t.wParams), len(t.aParams), len(t.rParams)):
                raise IRv1Error(f"instantiate: call {n[1]} arity mismatch")
            callee = _Env(dict(zip(t.wParams, wv)), dict(zip(t.aParams, av)),
                          dict(zip(t.rParams, rv)), t.table)
            return self.node(callee, t.body, fuel - 1)
        if tag == "loop":
            lo, hi = self.w(env, n[2]), self.w(env, n[3])
            out = []
            for i in range(lo, max(lo, hi)):
                inner = _Env({**env.w, n[1]: i}, env.a, env.r, env.table)
                out.extend(self.node(inner, n[4], fuel))
            return out
        raise IRv1Error(f"unknown Node {tag}")


def instantiate(doc: Doc, template: str, oracle: Oracle, *, w: Dict[str, int],
                a: Optional[Dict[str, Fraction]] = None, r: Optional[Dict[str, ExtReg]] = None,
                fuel: int = 200) -> List[Dict[str, Any]]:
    """`IR.instantiate d t env fuel`, flattened (`LowGate.flattenSeq`) to `forshor.lowgate/v2`
    leaf dicts. The entry template runs in an environment that holds exactly ``w``/``a``/``r``."""
    t = doc.template(template)
    env = _Env(dict(w), {k: Fraction(v) for k, v in (a or {}).items()}, dict(r or {}), t.table)
    return Instantiator(doc, oracle).node(env, t.body, fuel)


# ============================================================================ flat-circuit helpers
def flatten_lowgate(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`LowGate.flattenSeq` over a `forshor.lowgate/v2` circuit. The `seq` bodies are already flat
    there, but this function also normalises nested `seq` under `adj` and `id` leaves in the same way."""
    op = node.get("op")
    if op == "seq":
        return [g for b in node["body"] for g in flatten_lowgate(b)]
    if op == "id":
        return []
    if op == "adj":
        return [{"op": "adj", "body": flatten_lowgate(node["body"])}]
    return [node]


def lowgate_doc(leaves: List[Dict[str, Any]], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Wrap instantiated leaves as a `forshor.lowgate/v2` document (no Lean-computed counts)."""
    return {"schema": "forshor.lowgate/v2", "bit_order": "lsb_first", "angle_unit": "pi",
            "circuit": {"op": "seq", "body": leaves}, "meta": dict(meta or {})}


def _cuccaro(w: int) -> Dict[str, int]:
    """`cuccaroModAddResources w` (ForShor `Gatecount/ResourceModel.lean`), ℕ-truncated."""
    return {"x": max(0, 2 * w - 6), "cnot": max(0, 5 * w - 7), "toffoli": max(0, 2 * w - 3)}


def lowgate_resources(leaves: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """`LowGate.resources shorGateResourceModel` over flat leaves: the `h, x, cnot, toffoli, rz`
    channels (not `cleanAnc`, because its `seq` rule is not additive). An adjoint costs the same as
    its body."""
    out = {"h": 0, "x": 0, "cnot": 0, "toffoli": 0, "rz": 0}

    def add(d):
        for k, v in d.items():
            out[k] += v

    for g in leaves:
        op = g["op"]
        if op == "adj":
            add(lowgate_resources(g["body"]))
        elif op == "H":
            out["h"] += 1
        elif op == "X":
            out["x"] += 1
        elif op == "Phase":
            out["rz"] += 1
        elif op == "CNOT":
            out["cnot"] += 1
        elif op == "Toffoli":
            out["toffoli"] += 1
        elif op == "AddScaled":
            add(_cuccaro(len(g["dst"]["active"])))
        elif op == "Negate":
            w = len(g["r"]["active"])
            c = _cuccaro(w)
            add({"x": w + c["x"] + 2, "cnot": c["cnot"], "toffoli": c["toffoli"]})
        elif op in ("signExtend", "signDealloc"):
            out["cnot"] += g["n"]
        elif op == "RadixReverse":
            out["cnot"] += 3 * (g["m"] // 2)
        elif op in ("ShiftL", "ShiftR", "zeroExtend", "zeroDealloc", "id"):
            pass
        else:
            raise IRv1Error(f"no resource rule for LowGate op {op!r}")
    return out


def census(leaves: Sequence[Dict[str, Any]], adjoint: bool = False) -> Dict[str, int]:
    """Leaf counts by op name. Adjoints are counted by their bodies."""
    out: Dict[str, int] = {}
    for g in leaves:
        if g["op"] == "adj":
            for k, v in census(g["body"], True).items():
                out[k] = out.get(k, 0) + v
        else:
            out[g["op"]] = out.get(g["op"], 0) + 1
    return out
