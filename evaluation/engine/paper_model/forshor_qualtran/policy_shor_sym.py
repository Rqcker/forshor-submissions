r"""The `shor` template's skeleton, walked symbolically: the Shor-level census with ``n`` a SymPy symbol.

`policy_shor_ir` instantiates ``shor`` at a small instance, which is how the IR is *validated*. This
module is how it is *priced*: nothing is unrolled, no register is built, and no policy is consulted.
It abstract-interprets the template body over symbolic widths, and returns what the body itself
contains, in closed form in ``n``:

* **leaf census**: counts of ``H``, ``X``, ``CNOT`` and the width-indexed ``AddScaled`` / ``Negate``
  that ``shor`` itself performs (the adders and comparators of the modular multiplier);
* **sites**: ``{(template, xw, zw): multiplicity}`` for every ``phase_product`` / ``cphase_product`` /
  ``qft`` call, with widths as expressions in ``n``. These are the decision points a policy is priced
  at; pricing them (symbolically, per ladder regime) is the next layer, not this one.

Why this is sound to do: the body of ``shor`` is one outer loop over the ``xW`` exponent bits whose body
mentions the loop variable only through *angles* (``modpow``) and single-qubit picks
(``x.active[e]``), never through a width. So one symbolic pass of the body, times ``xW``, is the whole
circuit. The one loop that does read its variable in a guard, ``testBit N i`` (the modulus constant
copied onto the comparator), is priced by the modulus's popcount (:class:`Convention`).

Registers are abstract: a register is ``(active width, reserve size)``, i.e. exactly what a width
expression can ask of it. Subtraction is plain ``a - b`` (Lean's truncated ``-`` is the same wherever
the circuit is well-formed; the validation tests compare against the instantiated circuit at every
width they use, which is what would catch a place where it is not).

Validation only claims what it checks: `tests/test_policy_shor_sym.py` requires this census to equal the
instantiated skeleton (`policy_shor_ir`'s stub-calls walk) at every instance it is run on.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import sympy as sp

from . import ir_v1

n = sp.Symbol("n", positive=True, integer=True)      # bits of the modulus (data register width)

#: Templates whose calls are sites (priced elsewhere), not descended into.
SITE_TEMPLATES = ("phase_product", "cphase_product", "qft")


class SymbolicWalkError(ValueError):
    pass


# ============================================================================ conventions
@dataclass(frozen=True)
class Convention:
    """What the template leaves to the instance.

    ``popcount``: how many bits of the modulus are set, for the ``testBit N i`` loops (one CNOT each).
    ForShor's benchmark modulus is the representative ``2^(bits-1)`` (`PolicyMain.benchmarkModulus`),
    popcount 1; an average modulus has about half its bits set.
    ``extra_bits``: `algorithm1ExtraBitsNat m`, the work register's slack over the data register.
    ``upper_binade``: whether ``N >= 2^(nbits-1/2)``, i.e. ``2N^2 >= 2^(2*nbits)``; then the exponent
    register is ``2*nbits`` bits instead of ``2*nbits - 1``. The benchmark modulus is in the lower half.
    """
    popcount: Any = 1
    extra_bits: int = 4
    upper_binade: bool = False
    name: str = "benchmark-power-of-two"

    @classmethod
    def for_modulus(cls, N: int, m: int) -> "Convention":
        """The convention a concrete modulus ``N`` (precision parameter ``m``) fixes; pair it with
        ``nbits = N.bit_length()``."""
        from .policy_shor_ir import algorithm1_extra_bits
        nb, pc = N.bit_length(), bin(N).count("1")
        label = f"N={N}" if nb <= 64 else f"N: {nb} bits, popcount {pc}"   # no 1000-digit names
        return cls(popcount=pc, extra_bits=algorithm1_extra_bits(m),
                   upper_binade=(2 * N * N).bit_length() - 1 == 2 * nb, name=f"{label}, m: {m.bit_length()} bits")


def layout_widths(conv: Convention, nbits=n) -> Dict[str, Any]:
    """The reference layout's active widths for an ``nbits``-bit modulus ``N``:
    `referenceXWidth = log2(2N^2)` (``2*nbits - 1``, or ``2*nbits`` in the upper half of the binade),
    `referenceDataWidth = log2(2N) = nbits`, `referenceWorkWidth = data + extra`,
    `referenceScratchWidth = 2 + max(data+1+work, log2(N+1)+1+work)` with `log2(N+1) <= nbits`, so the
    max is the first term."""
    dw = nbits
    ww = dw + conv.extra_bits
    xw = 2 * nbits - (0 if conv.upper_binade else 1)
    return {"xW": xw, "yW": dw, "workW": ww, "scratchW": 2 + (dw + 1) + ww}


# ============================================================================ the abstract walk
@dataclass(frozen=True)
class SReg:
    """An abstract `ExtReg`: active width and reserve size."""
    w: Any
    c: Any


@dataclass
class Census:
    h: Any = 0
    x: Any = 0
    cnot: Any = 0
    toffoli: Any = 0
    add_scaled: Counter = None      # {dst width: count}, every AddScaled
    add_scaled_neg: Counter = None  # {dst width: count}, the subset with a negated source (negSrc)
    negate: Counter = None          # {width: count}
    sites: Counter = None           # {(template, xw, zw): count}; qft has zw = 0

    COUNTERS = ("add_scaled", "add_scaled_neg", "negate", "sites")

    def __post_init__(self):
        for f in self.COUNTERS:
            if getattr(self, f) is None:
                setattr(self, f, Counter())

    def scale(self, k) -> "Census":
        out = Census(self.h * k, self.x * k, self.cnot * k, self.toffoli * k)
        for f in self.COUNTERS:
            setattr(out, f, Counter({w: sp.expand(v * k) for w, v in getattr(self, f).items()}))
        return out

    def add(self, o: "Census") -> None:
        self.h += o.h
        self.x += o.x
        self.cnot += o.cnot
        self.toffoli += o.toffoli
        for f in self.COUNTERS:
            dst = getattr(self, f)
            for k, v in getattr(o, f).items():
                dst[k] = sp.expand(dst.get(k, 0) + v)

    @staticmethod
    def _key(f, k, g):
        return tuple([k[0], *[g(x) for x in k[1:]]]) if f == "sites" else g(k)

    def simplified(self) -> "Census":
        f = sp.expand
        out = Census(f(self.h), f(self.x), f(self.cnot), f(self.toffoli))
        for c in self.COUNTERS:
            setattr(out, c, Counter({self._key(c, k, f): f(v) for k, v in getattr(self, c).items()}))
        return out

    def at(self, nv: int) -> Dict[str, Any]:
        """The census at ``n = nv``, in :func:`skeleton_census`'s shape. Distinct symbolic widths can
        coincide at one n (e.g. two registers of equal width), so counters accumulate."""
        ev = lambda e: int(sp.sympify(e).subs(n, nv))

        def acc(items) -> Counter:
            out = Counter()
            for k, v in items:
                out[k] += ev(v)
            return +out
        out: Dict[str, Any] = {"h": ev(self.h), "x": ev(self.x), "cnot": ev(self.cnot), "toffoli": ev(self.toffoli)}
        for c in self.COUNTERS:
            out[c] = acc((self._key(c, k, ev), v) for k, v in getattr(self, c).items())
        return out


class _Walker:
    def __init__(self, doc: ir_v1.Doc, conv: Convention):
        self.doc, self.conv = doc, conv

    # ---- widths
    def w(self, env, e):
        t = e[0]
        if t == "var":
            if e[1] not in env["w"]:
                raise SymbolicWalkError(f"unbound width var {e[1]}")
            return env["w"][e[1]]
        if t == "lit":
            return sp.Integer(e[1])
        if t == "opaque":
            # table/policy-level functions stay unevaluated: only caps and QFT workspaces use them
            return sp.Function(e[1])(*[self.w(env, a) for a in e[2]])
        a, b = self.w(env, e[1]), self.w(env, e[2])
        if t == "add":
            return a + b
        if t == "sub":
            return a - b
        if t == "mul":
            return a * b
        if t == "div":
            return sp.floor(sp.Rational(1) * a / b) if b != 0 else sp.Integer(0)
        if t == "max":
            return sp.Max(a, b)
        if t == "min":
            return sp.Min(a, b)
        raise SymbolicWalkError(f"unknown WExpr {t}")

    # ---- registers
    def r(self, env, e) -> SReg:
        t = e[0]
        if t == "var":
            return env["r"][e[1]]
        if t in ("activeSlice", "reserveSlice"):
            lo, hi = self.w(env, e[2]), self.w(env, e[3])
            self.r(env, e[1])
            return SReg(hi - lo, sp.Integer(0))
        if t == "ext":
            a, b = self.r(env, e[1]), self.r(env, e[2])
            return SReg(a.w, b.w)
        if t == "qubit":
            return SReg(sp.Integer(1), sp.Integer(0))
        if t == "grow":
            rv, k = self.r(env, e[1]), self.w(env, e[2])
            return SReg(rv.w + k, rv.c - k)
        raise SymbolicWalkError(f"unknown RegExpr {t}")

    # ---- guards
    def guard(self, env, g) -> bool:
        a, b = self.w(env, g[1]), self.w(env, g[2])
        d = sp.simplify(a - b)
        if g[0] == "eq":
            if d == 0:
                return True
            if d.is_positive or d.is_negative:
                return False
        elif g[0] in ("lt", "le"):
            if d.is_negative or (g[0] == "le" and d == 0):
                return True
            if d.is_positive or (g[0] == "lt" and d == 0):
                return False
        raise SymbolicWalkError(f"cannot decide guard {g} symbolically (difference {d})")

    # ---- nodes
    def node(self, env, nd) -> Census:
        t = nd[0]
        c = Census()
        if t == "seq":
            for b in nd[1]:
                c.add(self.node(env, b))
        elif t == "adj":
            c.add(self.node(env, nd[1]))
        elif t == "cond":
            g = nd[1]
            if g[0] == "testBit":
                raise SymbolicWalkError("testBit outside a loop over the modulus bits")
            c.add(self.node(env, nd[2] if self.guard(env, g) else nd[3]))
        elif t == "loop":
            var, lo, hi, body = nd[1], self.w(env, nd[2]), self.w(env, nd[3]), nd[4]
            if _is_testbit_body(body, var):
                inner = self.node(env, body[2])                 # the "bit set" branch
                c.add(inner.scale(self.conv.popcount))
            else:
                i = sp.Symbol(f"_{var}")
                inner = self.node({**env, "w": {**env["w"], var: i}}, body)
                self._require_var_free(inner, i, var)
                c.add(inner.scale(hi - lo))
        elif t == "call":
            if nd[1] not in SITE_TEMPLATES:
                raise SymbolicWalkError(f"call to {nd[1]}: only {SITE_TEMPLATES} are priced as sites")
            wv = [self.w(env, x) for x in nd[2]]
            c.sites[(nd[1], wv[0], sp.Integer(0) if nd[1] == "qft" else wv[1])] += 1
        elif t == "op":
            name = nd[1]
            regs = [self.r(env, x) for x in nd[2]]
            if name == "H":
                c.h += 1
            elif name == "X":
                c.x += 1
            elif name == "CNOT":
                c.cnot += 1
            elif name == "Toffoli":
                c.toffoli += 1
            elif name == "AddScaled":
                c.add_scaled[sp.expand(regs[0].w)] += 1
                if any(nd[5]):                                  # flags = (negSrc,)
                    c.add_scaled_neg[sp.expand(regs[0].w)] += 1
            elif name == "Negate":
                c.negate[sp.expand(regs[0].w)] += 1
            elif name in ("zeroExtend", "zeroDealloc", "signExtend", "signDealloc", "id", "ShiftL", "ShiftR"):
                pass
            else:
                raise SymbolicWalkError(f"no census rule for op {name}")
        else:
            raise SymbolicWalkError(f"unknown Node {t}")
        return c

    @staticmethod
    def _require_var_free(c: Census, i: sp.Symbol, var: str) -> None:
        keys = [k for k in (*c.add_scaled, *c.negate)] + [w for s in c.sites for w in s[1:]]
        if any(i in sp.sympify(k).free_symbols for k in keys):
            raise SymbolicWalkError(f"loop variable {var} reaches a width: the body is not iteration-invariant")


def _is_id(nd) -> bool:
    return nd == ("seq", ()) or nd == ("op", "id", (), (), None, ())


def _is_testbit_body(body, var) -> bool:
    """`if testBit N var then B else id`: the shape of the modulus-constant copy loops."""
    return body[0] == "cond" and body[1][0] == "testBit" and _is_id(body[3])


def shor_census(doc: ir_v1.Doc, conv: Optional[Convention] = None, nbits=n) -> Census:
    """The ``shor`` template's census with the layout widths for ``nbits`` (a symbol or an integer)."""
    conv = conv or Convention()
    lw = layout_widths(conv, nbits)
    cap = lambda s: sp.Symbol(f"cap_{s}", positive=True, integer=True)
    env = {"w": {"a": sp.Symbol("a"), "N": sp.Symbol("N"), **lw,
                 **{k: cap(k) for k in ("xCap", "yCap", "workCap", "scratchCap")}},
           "r": {"x": SReg(lw["xW"], cap("xCap")), "y": SReg(lw["yW"], cap("yCap")),
                 "work": SReg(lw["workW"], cap("workCap")), "scratch": SReg(lw["scratchW"], cap("scratchCap")),
                 "flag": SReg(sp.Integer(1), sp.Integer(0))}}
    t = doc.template("shor")
    return _Walker(doc, conv).node(env, t.body).simplified()


# ============================================================================ the concrete twin
class _Skeleton(ir_v1.Instantiator):
    """`Instantiator` that does not descend into the priced templates: it records the site and returns
    nothing, so the result is exactly the leaves `shor` itself contains."""

    def __init__(self, doc, oracle):
        super().__init__(doc, oracle)
        self.sites: Counter = Counter()

    def node(self, env, nd, fuel):
        if nd[0] == "call" and nd[1] in SITE_TEMPLATES:
            wv = [self.w(env, x) for x in nd[2]]
            self.sites[(nd[1], wv[0], 0 if nd[1] == "qft" else wv[1])] += 1
            return []
        return super().node(env, nd, fuel)


def skeleton_census(doc: ir_v1.Doc, oracle, lay, a: int, N: int) -> Dict[str, Any]:
    """The same quantities, from instantiating ``shor`` with stub calls at a concrete layout."""
    from .policy_shor_ir import shor_env
    w, r = shor_env(lay, a, N)
    t = doc.template("shor")
    inst = _Skeleton(doc, oracle)
    leaves = inst.node(ir_v1._Env(dict(w), {}, dict(r), t.table), t.body, 800)
    out = {"h": 0, "x": 0, "cnot": 0, "toffoli": 0, "add_scaled": Counter(), "add_scaled_neg": Counter(),
           "negate": Counter()}

    def go(gs):
        for g in gs:
            op = g["op"]
            if op == "adj":
                go(g["body"])
            elif op == "H":
                out["h"] += 1
            elif op == "X":
                out["x"] += 1
            elif op == "CNOT":
                out["cnot"] += 1
            elif op == "Toffoli":
                out["toffoli"] += 1
            elif op == "AddScaled":
                out["add_scaled"][len(g["dst"]["active"])] += 1
                if g["negSrc"]:
                    out["add_scaled_neg"][len(g["dst"]["active"])] += 1
            elif op == "Negate":
                out["negate"][len(g["r"]["active"])] += 1
            elif op in ("zeroExtend", "zeroDealloc"):
                pass
            else:
                raise SymbolicWalkError(f"skeleton has unexpected leaf {op}")
    go(leaves)
    out["sites"] = inst.sites
    return out
