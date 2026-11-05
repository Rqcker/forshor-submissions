r"""program.py -- the family layer in WIRING MODE (T2 of TODO_ARTIFACT_INFRASTRUCTURE.2026-09-11.md).

`family.py` walks the Lean-proved census recurrences (`ppLeaves`, `qftLeaves`, `cmodmulLeaves`,
`orderFindingLeaves`) and returns a multiset of leaves. This module walks the SAME recurrences with the
same tables (`WidthRule`, `Coefficients`, `Reserve`, the `ops` program) and returns the WIRED program: the
`shor.lowgate.ir/v1` node tree with concrete register maps, at any size, without the Lean emitter.

    w = Wiring(family.load("cmodmul", 2))           # any kind's document carries the k-tables
    doc = w.pp_doc(Fraction(1, 8), 16)               # == ir_v2.to_legacy_doc(emit2.pp(2, 16, "1/8")) node for node
    doc = w.qft_doc(12)
    doc = w.cmodmul_doc(c=2, N=3, n=3, m=5)
    doc = w.orderfinding_doc(a=3, N=7, n=3, m=5, L=3)

The layout mirrors the v2 emitter (`shor_emit`, the PROVED lowering `Shor.Family.leaves_lower*` is a theorem
about): x, z contiguous with their reserves for a phase product; register + workspace pools for a QFT;
ctrl / data / carry / data-pool / work / work-pool / flag for a modmul step; x (+ pool) / y / carry / pool /
work / pool / flag for order finding. Conformance to the emitter's documents is exact (node for node after
flattening `seq`/`id`) at every cached v2 document -- see `tests/test_t2_wiring.py`.

The recursion stop is a composition-time predicate: `Wiring(fam, stop=lambda wx, wz: ...)` emits a flat
`Naive(C)SignedPhaseProd` leaf wherever `stop(wx, wz)` holds instead of the emitter's next Toom level. With
`stop=None` the walk is the emitter's rule R0 (recurse iff the next width is smaller). The flat leaf and the
block it replaces are the same unitary (Lean `CompilationCorrectness`; `py/ir_recursion_stop.py` re-verifies
that with the exact interpreter on real documents).

Register ids are the emitter's absolute ids (owner decision, T2): the pool-relative form is T7's.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import family as F

Node = Dict[str, Any]
StopRule = Optional[Callable[[int, int], bool]]


# --------------------------------------------------------------------------------------- registers
def reg(active: Sequence[int], reserve: Sequence[int] = ()) -> Dict[str, List[int]]:
    return {"active": [int(q) for q in active], "reserve": [int(q) for q in reserve]}


def extend(r: Dict[str, List[int]], n: int) -> Dict[str, List[int]]:
    """`ExtReg.grow`: the next n reserve bits become the HIGH active bits."""
    if n > len(r["reserve"]):
        raise ValueError(f"cannot extend by {n}: reserve holds {len(r['reserve'])}")
    return reg(r["active"] + r["reserve"][:n], r["reserve"][n:])


def angle(phi: Fraction) -> Dict[str, Any]:
    phi = Fraction(phi)
    return {"num": phi.numerator, "den": phi.denominator, "base": "pi"}


def seq(body: Sequence[Node]) -> Node:
    return {"op": "seq", "body": list(body)}


def adj(body: Sequence[Node]) -> Node:
    return {"op": "adj", "body": seq(body)}


# --------------------------------------------------------------------------------------- the wiring
class Wiring:
    def __init__(self, fam: F.Family, stop: StopRule = None):
        if fam.reserve is None:
            raise F.FamilyError("wiring needs a family document with the `qubits` (reserve) tables")
        self.fam = fam
        self.k, self.q, self.ops = fam.k, fam.q, fam.ops
        self.width, self.coeff, self.reserve = fam.width, fam.coeff, fam.reserve
        self.stop = stop

    # ---- phase products ---------------------------------------------------------------------
    def _limbs(self, w: int, W: int) -> List[int]:
        return [W] * (self.k - 1) + [w - (self.k - 1) * W]

    def pp(self, phi: Fraction, x: Dict[str, List[int]], z: Dict[str, List[int]], ctrl: Optional[int] = None,
           site: Optional[str] = None, coeffprod: Fraction = Fraction(1)) -> List[Node]:
        """`lowerSignedPhaseProdWithWorkspace` (controlled when `ctrl` is given), as a flat op list.

        `site` (optional) names the step angle this product descends from (`phi1`, `phi2`, `phi5`); every
        leaf below then carries `phi_ref = {site, coeff}` with `leaf phi == phi_site * coeff` exactly, so a
        step TEMPLATE can be instantiated for any multiplier by recomputing the site angles
        (`StepTemplate.instantiate`)."""
        phi = Fraction(phi)
        wx, wz = len(x["active"]), len(z["active"])
        nxt = self.width.next(wx, wz)
        if nxt >= max(wx, wz) or (self.stop is not None and self.stop(wx, wz)):
            leaf: Node = {"op": "NaiveCSignedPhaseProd" if ctrl is not None else "NaiveSignedPhaseProd",
                          "phi": angle(phi), "x": reg(x["active"], x["reserve"]), "z": reg(z["active"], z["reserve"])}
            if ctrl is not None:
                leaf["ctrl"] = int(ctrl)
            if site is not None:
                leaf["phi_ref"] = {"site": site, "coeff": [coeffprod.numerator, coeffprod.denominator]}
            return [leaf]
        k = self.k
        W = min(wx // k, wz // k)
        lx, lz = self._limbs(wx, W), self._limbs(wz, W)
        cx, cz = self.reserve.reserve(nxt, nxt)
        xs = self._split(x, lx, nxt, cx)
        zs = self._split(z, lz, nxt, cz)
        out: List[Node] = []
        for i in range(k):
            kind = "signExtend" if i == k - 1 else "zeroExtend"
            out.append({"op": kind, "r": xs[i][0], "n": xs[i][1]})
            out.append({"op": kind, "r": zs[i][0], "n": zs[i][1]})
        xe = [extend(r, n) for r, n in xs]
        ze = [extend(r, n) for r, n in zs]
        coeff = self.coeff.at(W)
        n_pp = 0
        for op in self.ops:
            kind = op["op"]
            if kind == "shiftL" or kind == "shiftR":
                name, i, sh = ("ShiftL" if kind == "shiftL" else "ShiftR"), int(op["i"]), int(op["n"])
                out.append({"op": name, "r": xe[i], "n": sh})
                out.append({"op": name, "r": ze[i], "n": sh})
            elif kind == "negate":
                i = int(op["i"])
                out.append({"op": "Negate", "r": xe[i]})
                out.append({"op": "Negate", "r": ze[i]})
            elif kind == "addScaled":
                d, s, sh, neg = int(op["dst"]), int(op["src"]), int(op["shift"]), bool(op["negSrc"])
                out.append({"op": "AddScaled", "dst": xe[d], "src": xe[s], "negSrc": neg, "shift": sh})
                out.append({"op": "AddScaled", "dst": ze[d], "src": ze[s], "negSrc": neg, "shift": sh})
            elif kind == "phaseProduct":
                if n_pp < self.q:
                    i = int(op.get("i", 0))
                    out.extend(self.pp(phi * coeff[n_pp], xe[i], ze[i], ctrl, site, coeffprod * coeff[n_pp]))
                n_pp += 1
            else:  # pragma: no cover
                raise F.FamilyError(f"unknown op {kind!r} in program")
        for i in reversed(range(k)):
            kind = "signDealloc" if i == k - 1 else "zeroDealloc"
            out.append({"op": kind, "r": zs[i][0], "n": zs[i][1]})
            out.append({"op": kind, "r": xs[i][0], "n": xs[i][1]})
        return out

    def _split(self, r: Dict[str, List[int]], limbs: List[int], nxt: int, child_reserve: int):
        """[(limb extreg, extension)] : limb i owns `active[off:off+l_i]` and the reserve slice
        `reserve[roff : roff + (nxt - l_i) + child_reserve]`; it is extended by `nxt - l_i`."""
        out = []
        aoff = roff = 0
        for l in limbs:
            ext = nxt - l
            need = ext + child_reserve
            if roff + need > len(r["reserve"]):
                raise F.FamilyError(f"reserve too small: need {roff + need}, have {len(r['reserve'])} "
                                    f"(limbs {limbs}, next {nxt}, child reserve {child_reserve})")
            out.append((reg(r["active"][aoff:aoff + l], r["reserve"][roff:roff + need]), ext))
            aoff += l
            roff += need
        return out

    # ---- qft ------------------------------------------------------------------------------------
    def qft(self, r: Sequence[int], ws_x: Sequence[int], ws_z: Sequence[int]) -> List[Node]:
        """`lowerQFT` on register `r` (LSB-first) with the two workspace pools."""
        r = [int(q) for q in r]
        w = len(r)
        if w == 0:
            return []
        if w == 1:
            return [{"op": "H", "q": r[0]}]
        l = w // 2
        lo, hi = r[:l], r[l:]
        px, pz = self.reserve.reserve(l + 1, len(hi) + 1)
        if 1 + px > len(ws_x) or 1 + pz > len(ws_z):
            raise F.FamilyError(f"qft workspace too small at w={w}: need ({1 + px}, {1 + pz}), have ({len(ws_x)}, {len(ws_z)})")
        xe, ze = reg(lo, list(ws_x)[:1 + px]), reg(hi, list(ws_z)[:1 + pz])
        out = self.qft(hi, ws_x, ws_z)
        out.append({"op": "zeroExtend", "r": xe, "n": 1})
        out.append({"op": "zeroExtend", "r": ze, "n": 1})
        out.extend(self.pp(Fraction(2, 2 ** w), extend(xe, 1), extend(ze, 1)))
        out.append({"op": "zeroDealloc", "r": ze, "n": 1})
        out.append({"op": "zeroDealloc", "r": xe, "n": 1})
        out.extend(self.qft(lo, ws_x, ws_z))
        out.append({"op": "RadixReverse", "r": r, "m": l})
        return out

    def qft_pools(self, w: int, pool: Sequence[int]) -> Tuple[List[int], List[int]]:
        a, b = self.reserve.qft_need(w)
        pool = [int(q) for q in pool]
        if a + b > len(pool):
            raise F.FamilyError(f"qft pool too small at w={w}: need {a + b}, have {len(pool)}")
        return pool[:a], pool[a:a + b]

    # ---- modmul step ----------------------------------------------------------------------------
    def pool_sizes(self, n: int, m: int) -> Tuple[int, int]:
        """(data pool, work pool) beyond the carry bit: enough for every QFT workspace and every
        phase-product extension + reserve the step performs (the emitter's firstFit lands on the same
        sizes at the anchors)."""
        R = self.reserve.reserve
        need_d = max(sum(self.reserve.qft_need(n + 1)), 1 + R(n + 1, m + 1)[0], 1 + R(m + 1, n + 2)[1], 1 + R(n + 2, m + 1)[0])
        need_w = max(sum(self.reserve.qft_need(m)), 1 + R(n + 1, m + 1)[1], 1 + R(m + 1, n + 2)[0], 1 + R(n + 2, m + 1)[1])
        return need_d, need_w

    def cmodmul(self, c: int, N: int, ctrl: int, data: Sequence[int], carry: int, pool_d: Sequence[int],
                work: Sequence[int], pool_w: Sequence[int], flag: int) -> List[Node]:
        """`CmodMulInPlaceCore c N` (U1..U5) on the given registers."""
        n, m = len(data), len(work)
        phi1, phi2, phi5 = F.step_angles(c, N, n, m)
        data, work, pool_d, pool_w = list(data), list(work), list(pool_d), list(pool_w)
        dc = data + [int(carry)]
        data_e, dc_e, work_e = reg(data, pool_d), reg(dc, pool_d), reg(work, pool_w)
        wx_d, wz_d = self.qft_pools(n + 1, pool_d)
        wx_w, wz_w = self.qft_pools(m, pool_w)
        h_work = [{"op": "H", "q": q} for q in reversed(work)]

        def ext_pair(a, b):
            return [{"op": "zeroExtend", "r": a, "n": 1}, {"op": "zeroExtend", "r": b, "n": 1}]

        def dealloc_pair(a, b):
            return [{"op": "zeroDealloc", "r": b, "n": 1}, {"op": "zeroDealloc", "r": a, "n": 1}]

        out: List[Node] = []
        # U1: H_reg work ; C-PhaseProd(ctrl; data, work) ; IQFT work
        out += h_work
        out += ext_pair(data_e, work_e)
        out += self.pp(phi1, extend(data_e, 1), extend(work_e, 1), ctrl, "phi1")
        out += dealloc_pair(data_e, work_e)
        out.append(adj(self.qft(work, wx_w, wz_w)))
        # U2: QFT dataCarry ; PhaseProd(work, dataCarry) ; IQFT dataCarry
        out += self.qft(dc, wx_d, wz_d)
        out += ext_pair(work_e, dc_e)
        out += self.pp(phi2, extend(work_e, 1), extend(dc_e, 1), None, "phi2")
        out += dealloc_pair(work_e, dc_e)
        out.append(adj(self.qft(dc, wx_d, wz_d)))
        # U3, U4
        out.append({"op": "Prim", "tag": f"CMP_GE_CONST:{N}", "args": [int(flag)] + dc})
        out.append({"op": "Prim", "tag": f"CSUB_CONST:{N}", "args": [int(flag)] + dc})
        out.append({"op": "Prim", "tag": f"CMP_LT_NW:{N}:{len(dc)}", "args": [int(flag)] + dc + work})
        # U5: adjoint( H_reg work ; C-PhaseProd(ctrl; dataCarry, work) ; IQFT work )
        u5: List[Node] = []
        u5 += h_work
        u5 += ext_pair(dc_e, work_e)
        u5 += self.pp(phi5, extend(dc_e, 1), extend(work_e, 1), ctrl, "phi5")
        u5 += dealloc_pair(dc_e, work_e)
        u5.append(adj(self.qft(work, wx_w, wz_w)))
        out.append(adj(u5))
        return out

    # ---- documents (the emitter's layouts) ------------------------------------------------------
    def pp_doc(self, phi: Fraction, n: int, ctrl: bool = False) -> Node:
        rx, rz = self.reserve.reserve(n, n)
        x = reg(range(0, n), range(2 * n, 2 * n + rx))
        z = reg(range(n, 2 * n), range(2 * n + rx, 2 * n + rx + rz))
        body = self.pp(Fraction(phi), x, z)
        return {"schema": "shor.lowgate.ir/v1", "bit_order": "lsb_first", "provenance": "program.Wiring (family recurrence, wiring mode)",
                "qubits": {"count": touched(seq(body)), "ancilla": []}, "allocated": 2 * n + rx + rz,
                "expected": {"kind": "signed_phase_product", "phi": angle(phi), "signed": True, "x": x["active"], "z": z["active"]},
                "operands": {"x": x, "z": z}, "phi": angle(phi), "checks": {}, "coeffs": [],
                "layout": {"limb_width_W": n // self.k, "reserve_need": [rx, rz], "x": [x["active"], x["reserve"]], "z": [z["active"], z["reserve"]]},
                "circuit": seq(body)}

    def qft_doc(self, w: int) -> Node:
        a, b = self.reserve.qft_need(w)
        r = list(range(w))
        ws_x, ws_z = list(range(w, w + a)), list(range(w + a, w + a + b))
        body = self.qft(r, ws_x, ws_z)
        return {"schema": "shor.lowgate.ir/v1", "bit_order": "lsb_first", "provenance": "program.Wiring (family recurrence, wiring mode)",
                "qubits": {"count": touched(seq(body)), "ancilla": []}, "allocated": w + a + b,
                "expected": {"kind": "qft", "register": r, "w": w}, "phi": {"num": 1, "den": 1, "base": "pi"},
                "checks": {}, "coeffs": [], "layout": {"r": [r, ws_x + ws_z], "workspace_need": [a, b]},
                "circuit": seq(body)}

    def cmodmul_layout(self, n: int, m: int, base: int = 0, ctrl: Optional[int] = None) -> Dict[str, Any]:
        pd, pw = self.pool_sizes(n, m)
        q = base
        if ctrl is None:
            ctrl = q
            q += 1
        data = list(range(q, q + n)); q += n
        carry = q; q += 1
        pool_d = list(range(q, q + pd)); q += pd
        work = list(range(q, q + m)); q += m
        pool_w = list(range(q, q + pw)); q += pw
        flag = q; q += 1
        return {"ctrl": ctrl, "data": data, "carry": carry, "pool_d": pool_d, "work": work, "pool_w": pool_w, "flag": flag, "count": q}

    def cmodmul_doc(self, c: int, N: int, n: int, m: int) -> Node:
        L = self.cmodmul_layout(n, m)
        body = self.cmodmul(c, N, L["ctrl"], L["data"], L["carry"], L["pool_d"], L["work"], L["pool_w"], L["flag"])
        return {"schema": "shor.lowgate.ir/v1", "bit_order": "lsb_first", "provenance": "program.Wiring (family recurrence, wiring mode)",
                "qubits": {"count": touched(seq(body)), "ancilla": []}, "allocated": L["count"],
                "expected": {"kind": "cmodmul_approx", "N": N, "c": c, "ctrl": L["ctrl"], "data": L["data"], "flag": L["flag"], "work": L["work"]},
                "checks": {}, "coeffs": [],
                "layout": {"ctrl": L["ctrl"], "data": [L["data"], [L["carry"]] + L["pool_d"]], "flag": L["flag"], "work": [L["work"], L["pool_w"]]},
                "circuit": seq(body)}

    def orderfinding_doc(self, a: int, N: int, n: int, m: int, L: int) -> Node:
        ax, bz = self.reserve.qft_need(L)
        x = list(range(L))
        xs_x, xs_z = list(range(L, L + ax)), list(range(L + ax, L + ax + bz))
        lay = self.cmodmul_layout(n, m, base=L + ax + bz, ctrl=-1)   # ctrl is per step: x[e]
        body: List[Node] = [{"op": "H", "q": q} for q in reversed(x)]
        if n:
            body.append({"op": "X", "q": lay["data"][0]})
        multipliers = [pow(a, 2 ** e, N) for e in range(L)]
        for e in range(L):
            body += self.cmodmul(multipliers[e], N, x[e], lay["data"], lay["carry"], lay["pool_d"], lay["work"], lay["pool_w"], lay["flag"])
        body.append(adj(self.qft(x, xs_x, xs_z)))
        return {"schema": "shor.lowgate.ir/v1", "bit_order": "lsb_first", "provenance": "program.Wiring (family recurrence, wiring mode)",
                "qubits": {"count": touched(seq(body)), "ancilla": []}, "allocated": lay["count"],
                "expected": {"kind": "orderfinding", "N": N, "a": a, "flag": lay["flag"], "work": lay["work"], "x": x, "y": lay["data"], "L": L},
                "checks": {}, "coeffs": [], "multipliers": multipliers,
                "layout": {"flag": lay["flag"], "work": [lay["work"], lay["pool_w"]], "x": [x, xs_x + xs_z], "y": [lay["data"], [lay["carry"]] + lay["pool_d"]]},
                "circuit": seq(body)}


# --------------------------------------------------------------------------------------- generator form
CTRL_PLACEHOLDER = -1


class StepTemplate:
    """ONE wired modmul step with the control qubit and the multiplier left symbolic: the T7 freeze stores this
    once and the schedule `c_e = a^(2^e) mod N`; `instantiate(e)` rebinds the control to `x[e]` and every
    `phi_ref` leaf to `phi_site(c_e) * coeff`. `Program.expand()` proves the form equal to the flat wiring."""

    def __init__(self, wiring: "Wiring", N: int, n: int, m: int, layout: Dict[str, Any]):
        self.wiring, self.N, self.n, self.m, self.layout = wiring, N, n, m, layout
        c0 = 1 if N == 1 else 2 % N
        self.body = wiring.cmodmul(c0, N, CTRL_PLACEHOLDER, layout["data"], layout["carry"], layout["pool_d"],
                                   layout["work"], layout["pool_w"], layout["flag"])

    def instantiate(self, ctrl: int, c: int) -> List[Node]:
        phi1, phi2, phi5 = F.step_angles(c, self.N, self.n, self.m)
        sites = {"phi1": phi1, "phi2": phi2, "phi5": phi5}

        def bind(nd):
            if isinstance(nd, list):
                return [bind(v) for v in nd]
            if not isinstance(nd, dict):
                return nd
            out = {}
            for k, v in nd.items():
                if k == "ctrl" and v == CTRL_PLACEHOLDER:
                    out[k] = int(ctrl)
                elif k == "body":
                    out[k] = bind(v)
                else:
                    out[k] = v
            if "phi_ref" in nd:
                num, den = nd["phi_ref"]["coeff"]
                out["phi"] = angle(sites[nd["phi_ref"]["site"]] * Fraction(num, den))
            return out
        return bind(self.body)


class Program:
    """The order-finding program in generator form: frame + one step template + schedule (a, N, L) + IQFT."""

    def __init__(self, wiring: "Wiring", a: int, N: int, n: int, m: int, L: int):
        self.wiring, self.a, self.N, self.n, self.m, self.L = wiring, a, N, n, m, L
        ax, bz = wiring.reserve.qft_need(L)
        self.x = list(range(L))
        self.xs_x, self.xs_z = list(range(L, L + ax)), list(range(L + ax, L + ax + bz))
        self.layout = wiring.cmodmul_layout(n, m, base=L + ax + bz, ctrl=CTRL_PLACEHOLDER)
        self.frame: List[Node] = [{"op": "H", "q": q} for q in reversed(self.x)] + ([{"op": "X", "q": self.layout["data"][0]}] if n else [])
        self.template = StepTemplate(wiring, N, n, m, self.layout)
        self.iqft: List[Node] = [adj(wiring.qft(self.x, self.xs_x, self.xs_z))]

    def multiplier(self, e: int) -> int:
        return pow(self.a, 2 ** e, self.N)

    def definitions(self) -> Dict[str, int]:
        """Leaf counts of the stored pieces -- what the artifact holds, independent of L (the IQFT is the
        width-L QFT and grows with L; the step template does not)."""
        return {"frame": len(self.frame), "step_template": leaf_count(seq(self.template.body)),
                "iqft": leaf_count(seq(self.iqft)), "schedule": 3}

    def expand(self) -> Node:
        body = list(self.frame)
        for e in range(self.L):
            body += self.template.instantiate(self.x[e], self.multiplier(e))
        body += self.iqft
        return seq(body)


# --------------------------------------------------------------------------------------- stop rule (v2 tables)
def cost_optimal_stop(fam: F.Family, currency=None, root_cache: Optional[dict] = None) -> Callable[[int, int], bool]:
    """A stop predicate from the FAMILY's own width rule (the v2 lowering), not the legacy bundle's RuleSet:
    at balanced width w, recurse iff `q * T(next) + body(next) < leaf(w)`. `currency` prices a leaf grid
    (`leaf_currency.py`; default the Qualtran heuristic); the body is the per-level arithmetic priced as
    `RuleSet.body_T` does (4 T per adder carry, both sides)."""
    from . import constants
    from .leaf_currency import ModelCurrency
    cur = currency if currency is not None else ModelCurrency(constants.TS_PER_ROTATION)
    n_add = sum(1 for op in fam.ops if op["op"] == "addScaled")
    n_neg = sum(1 for op in fam.ops if op["op"] == "negate")
    n_negsrc = sum(1 for op in fam.ops if op["op"] == "addScaled" and op.get("negSrc"))
    memo: Dict[int, Tuple[float, bool]] = {} if root_cache is None else root_cache

    def T(w: int) -> Tuple[float, bool]:
        """(cost, stops_here)"""
        if w in memo:
            return memo[w]
        leaf = float(cur.leaf(w))
        nxt = fam.width.next(w, w)
        if nxt >= w:
            memo[w] = (leaf, True)
            return memo[w]
        body = 2 * (n_add + n_neg + 2 * n_negsrc) * constants.TS_PER_TOFFOLI * max(0, nxt - 1)
        rec = fam.q * T(nxt)[0] + body
        memo[w] = (min(leaf, rec), leaf <= rec)
        return memo[w]

    return lambda wx, wz: T(max(wx, wz))[1]


def touched(node: Node) -> int:
    """Highest qubit id referenced by any leaf, plus one (the emitter's `qubits.count`)."""
    hi = -1

    def q(i):
        nonlocal hi
        hi = max(hi, int(i))

    def walk(nd):
        if isinstance(nd, list):
            for v in nd:
                walk(v)
            return
        op = nd.get("op")
        if op in ("seq", "adj"):
            walk(nd["body"])
        elif op in ("H", "X"):
            q(nd["q"])
        elif op == "Prim":
            for i in nd["args"]:
                q(i)
        elif op == "RadixReverse":
            for i in nd["r"]:
                q(i)
        elif op == "id":
            return
        else:
            for key in ("r", "dst", "src", "x", "z"):
                if key in nd:
                    for i in nd[key]["active"]:
                        q(i)
                    if op in ("zeroExtend", "signExtend", "zeroDealloc", "signDealloc"):
                        for i in nd[key]["reserve"][:int(nd["n"])]:
                            q(i)
            if "ctrl" in nd:
                q(nd["ctrl"])
    walk(node)
    return hi + 1


# --------------------------------------------------------------------------------------- census + conformance
def census(node: Node) -> Counter:
    """The family-keyed leaf multiset of a legacy node tree (same keys as `family.Family.census`)."""
    out: Counter = Counter()

    def walk(nd):
        if isinstance(nd, list):
            for v in nd:
                walk(v)
            return
        op = nd.get("op")
        if op in ("seq", "adj"):
            walk(nd["body"])
        elif op == "id" or op in ("zeroExtend", "signExtend", "zeroDealloc", "signDealloc"):
            return
        elif op in ("H", "X"):
            out[(op,)] += 1
        elif op == "Prim":
            base = nd["tag"].split(":")[0]
            out[("Prim", base, len(nd["args"]) - 1)] += 1
        elif op in ("ShiftL", "ShiftR"):
            out[(op, len(nd["r"]["active"]), int(nd["n"]))] += 1
        elif op == "Negate":
            out[("Negate", len(nd["r"]["active"]))] += 1
        elif op == "AddScaled":
            out[("AddScaled", len(nd["dst"]["active"]), len(nd["src"]["active"]), bool(nd["negSrc"]), int(nd["shift"]))] += 1
        elif op in ("NaiveSignedPhaseProd", "NaiveCSignedPhaseProd"):
            tag = "Naive_SignedPhaseProd" if op == "NaiveSignedPhaseProd" else "Naive_CSignedPhaseProd"
            out[(tag, (int(nd["phi"]["num"]), int(nd["phi"]["den"])), len(nd["x"]["active"]), len(nd["z"]["active"]))] += 1
        elif op == "RadixReverse":
            out[("RadixReverse", len(nd["r"]), int(nd["m"]))] += 1
        else:
            raise ValueError(f"unknown op {op!r}")
    walk(node)
    return out


def leaf_count(node: Node) -> int:
    """Number of non-structural leaves, looking through `adj` groups."""
    def count(items) -> int:
        return sum(count(x[1]) if isinstance(x, tuple) and x[0] == "adj" else 1 for x in items)
    return count(flatten(node))


def flatten(node: Node) -> List[Any]:
    """Structural leaves in execution order; `adj` groups become `("adj", [...])` markers."""
    out: List[Any] = []
    for leaf in _iter_leaves(node):
        out.append(leaf)
    return out


def _iter_leaves(node):
    op = node.get("op")
    if op == "seq":
        for b in node["body"]:
            yield from _iter_leaves(b)
    elif op == "id":
        return
    elif op == "adj":
        yield ("adj", list(_iter_leaves(node["body"])))
    else:
        yield node


def _canon(leaf, rename: Dict[int, int]):
    """A hashable, id-renamed view of a leaf (ids renamed by first appearance)."""
    def q(i):
        i = int(i)
        if i not in rename:
            rename[i] = len(rename)
        return rename[i]

    def r(e):
        return (tuple(q(i) for i in e["active"]), tuple(q(i) for i in e["reserve"]))

    if isinstance(leaf, tuple) and leaf[0] == "adj":
        return ("adj", tuple(_canon(x, rename) for x in leaf[1]))
    op = leaf["op"]
    if op in ("H", "X"):
        return (op, q(leaf["q"]))
    if op == "Prim":
        return ("Prim", leaf["tag"], tuple(q(i) for i in leaf["args"]))
    if op in ("ShiftL", "ShiftR"):
        return (op, r(leaf["r"]), int(leaf["n"]))
    if op == "Negate":
        return (op, r(leaf["r"]))
    if op == "AddScaled":
        return (op, r(leaf["dst"]), r(leaf["src"]), bool(leaf["negSrc"]), int(leaf["shift"]))
    if op in ("zeroExtend", "signExtend", "zeroDealloc", "signDealloc"):
        return (op, r(leaf["r"]), int(leaf["n"]))
    if op in ("NaiveSignedPhaseProd", "NaiveCSignedPhaseProd"):
        c = (q(leaf["ctrl"]),) if "ctrl" in leaf else ()
        return (op, c, (int(leaf["phi"]["num"]), int(leaf["phi"]["den"])), r(leaf["x"]), r(leaf["z"]))
    if op == "RadixReverse":
        return (op, tuple(q(i) for i in leaf["r"]), int(leaf["m"]))
    raise ValueError(op)


def conformance(mine: Node, theirs: Node, rename: bool = False) -> Dict[str, Any]:
    """Compare two circuits leaf for leaf in execution order. With `rename=False` the register ids must be
    identical; with `rename=True` ids are compared up to a consistent renaming by first appearance."""
    a, b = flatten(mine), flatten(theirs)
    ra: Dict[int, int] = {} if rename else None
    rb: Dict[int, int] = {} if rename else None
    ident = {i: i for i in range(1 << 20)}
    n = min(len(a), len(b))
    for i in range(n):
        ca = _canon(a[i], ra if rename else ident)
        cb = _canon(b[i], rb if rename else ident)
        if ca != cb:
            return {"equal": False, "index": i, "mine": ca, "theirs": cb, "len_mine": len(a), "len_theirs": len(b)}
    if len(a) != len(b):
        return {"equal": False, "index": n, "mine": None, "theirs": None, "len_mine": len(a), "len_theirs": len(b)}
    return {"equal": True, "leaves": len(a)}
