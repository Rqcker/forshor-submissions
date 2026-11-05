r"""The native policy ``shor`` template, instantiated at a small reference instance.

ForShor's native policy IR contains the whole lowered order-finding circuit as the ``shor`` template,
whose phase products and QFTs reach the policy through the dispatcher (`policy_ir`). This module is
the *validation* half of the Shor-level integration, and nothing in it prices anything:

* :func:`reference_layout` mirrors `Reference/ReferenceLayout.lean` (``allocateReferenceLayout``) and
  `Shor/Circuit/Workspace.lean` (``Policy.shorWorkspaceNeed``): the register widths, reserve sizes
  and qubit placement of the reference Shor circuit for ``(N, m)`` under a policy.
* :func:`instantiate_shor` runs the ``shor`` template on that layout (flat ``forshor.lowgate/v2``).
* :func:`site_census` runs it again with every ``call`` recorded, giving the **sites** the policy is
  actually consulted at: ``{(template, xw, zw): multiplicity}`` for ``phase_product``,
  ``cphase_product`` and ``qft``. The sites are what a symbolic Shor-level bloq must reproduce, and
  they are convention-independent (no adder, leaf or rotation model enters).

Small instances only (the flat circuit has millions of gates by N ~ 10^4). Priced objects stay
symbolic in n; they are checked against the census here, never built from it.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import ir_v1, policy as pol, policy_ir as pi

#: `Reference/ReferencePrecision.lean`: `algorithm1ExtraBitsNat m = Nat.clog 2 ((m + 7)^2) - 2`.
def algorithm1_extra_bits(m: int) -> int:
    v = (m + 7) ** 2
    clog = (v - 1).bit_length()           # Nat.clog 2 v for v >= 1
    return clog - 2


#: ForShor's headline fraction: `headline_success_bound` keeps >= 99/100 of the kappa/(log2 N)^4 baseline.
HEADLINE_FRACTION = (99, 100)
#: `ReferenceShorImplementation.referenceK`.
REFERENCE_K = 2048


def _kappa(dps: int = 60):
    """kappa = 4 e^-2 / pi^2 (`Reference2048Headline.lean`) as an mpmath number at `dps` digits."""
    import mpmath
    with mpmath.workdps(dps):
        return +(4 * mpmath.exp(-2) / mpmath.pi ** 2)


def _dps_for(m: int) -> int:
    """Enough digits to tell m from m - 1 in the bound (their difference is about 1/m relative)."""
    return 40 + len(str(max(1, m)))


def success_bound(m: int, N: int, dps: int = 0):
    """`referenceSuccessProbabilityAt m N` = clamp01(kappa/(log2 N)^4 - 2 tbits sqrt(2 K/(m+3))), with
    tbits = log2(2 N^2) (`ReferenceShorImplementation.lean`), as an mpmath number with enough digits."""
    import mpmath
    L, t = _log2(N), _log2(2 * N * N)
    with mpmath.workdps(dps or _dps_for(m)):
        raw = _kappa(mpmath.mp.dps) / mpmath.mpf(L) ** 4 - 2 * t * mpmath.sqrt(mpmath.mpf(2 * REFERENCE_K) / (m + 3))
        return max(mpmath.mpf(0), min(mpmath.mpf(1), raw))


def baseline(N: int, dps: int = 60):
    """kappa / (log2 N)^4, the ideal single-run bound the fraction is taken of."""
    import mpmath
    with mpmath.workdps(dps):
        return _kappa(dps) / mpmath.mpf(_log2(N)) ** 4


def min_precision_m(N: int, fraction=HEADLINE_FRACTION) -> int:
    """The smallest m with `referenceSuccessProbabilityAt m N >= fraction * kappa/(log2 N)^4`:
    2 t sqrt(2K/(m+3)) <= (1-f) kappa / L^4  <=>  m + 3 >= R := 2K (2 t L^4 / ((1-f) kappa))^2.
    kappa is irrational, so R is not an integer and m = ceil(R) - 3 exactly, once R is computed with more
    digits than it has."""
    import mpmath
    num, den = fraction
    L, t = _log2(N), _log2(2 * N * N)
    if L == 0:
        raise ValueError("N must be at least 2")

    def R(dps):
        with mpmath.workdps(dps):
            return 2 * REFERENCE_K * (mpmath.mpf(2 * t * L ** 4) * den / ((den - num) * _kappa(dps))) ** 2

    digits = int(mpmath.log10(R(60))) + 1
    with mpmath.workdps(digits + 40):
        return max(0, int(mpmath.ceil(R(digits + 40))) - 3)


def _log2(n: int) -> int:
    return n.bit_length() - 1 if n > 0 else 0


@dataclass(frozen=True)
class ShorLayout:
    """`ReferenceShorLayout` plus the widths it was built from."""
    x: ir_v1.ExtReg
    data: ir_v1.ExtReg
    work: ir_v1.ExtReg
    scratch: ir_v1.ExtReg
    flag: int
    need: Dict[str, int]                  # Policy.shorWorkspaceNeed: exponent, data, auxiliary, scratch

    @property
    def widths(self) -> Dict[str, int]:
        return {"x": self.x.width, "data": self.data.width, "work": self.work.width,
                "scratch": self.scratch.width}


def workspace_need(P: pol.PhasePolicy, xw: int, dw: int, ww: int, sw: int) -> Dict[str, int]:
    """`Policy.shorWorkspaceNeed P x data work scratch` (`shorWorkspaceNeedOf`)."""
    def qres(n):
        a, b = pi.qft_workspace_need(P, n)
        return a + b

    s1 = P.reserve_need(dw + 1, ww + 1)
    s2 = P.reserve_need(ww + 1, dw + 2)
    s5 = P.reserve_need(dw + 2, ww + 1)
    s4 = P.reserve_need(ww + 1, sw + 1)
    return {"exponent": qres(xw),
            "data": max(2 + s1[0], 1 + qres(dw + 1), 2 + s2[1], 2 + s5[0]),
            "auxiliary": max(qres(ww), 1 + s1[1], 1 + s2[0], 1 + s4[0], 1 + s5[1]),
            "scratch": max(qres(sw), 1 + s4[1])}


def reference_layout(P: pol.PhasePolicy, N: int, m: int) -> ShorLayout:
    """`allocateReferenceLayout P inst m` for a modulus ``N``: x, data, work, scratch, flag."""
    xw = _log2(2 * N * N)
    dw = _log2(2 * N)
    ww = dw + algorithm1_extra_bits(m)
    sw = 2 + max((dw + 1) + ww, _log2(N + 1) + 1 + ww)
    need = workspace_need(P, xw, dw, ww, sw)
    xr, dr = need["exponent"], max(2, need["data"])
    wr, sr = max(1, need["auxiliary"]), max(1, need["scratch"])

    def reg(start, width, rsize):
        return ir_v1.ExtReg(tuple(range(start, start + width)),
                            tuple(range(start + width, start + width + rsize))), start + width + rsize

    x, cur = reg(0, xw, xr)
    data, cur = reg(cur, dw, dr)
    work, cur = reg(cur, ww, wr)
    scratch, cur = reg(cur, sw, sr)
    return ShorLayout(x, data, work, scratch, cur, need)


def shor_env(lay: ShorLayout, a: int, N: int) -> Tuple[Dict[str, int], Dict[str, ir_v1.ExtReg]]:
    w = {"a": a, "N": N, "xW": lay.x.width, "xCap": lay.x.capacity, "yW": lay.data.width,
         "yCap": lay.data.capacity, "workW": lay.work.width, "workCap": lay.work.capacity,
         "scratchW": lay.scratch.width, "scratchCap": lay.scratch.capacity}
    r = {"x": lay.x, "y": lay.data, "work": lay.work, "scratch": lay.scratch,
         "flag": ir_v1.ExtReg((lay.flag,))}
    return w, r


def instantiate_shor(np_: "pi.NativePolicy", a: int, N: int, m: int, *, fuel: int = 800) -> List[Dict[str, Any]]:
    """The native ``shor`` template at ``(a, N, m)``: flat leaves."""
    lay = reference_layout(np_.policy, N, m)
    w, r = shor_env(lay, a, N)
    return ir_v1.instantiate(np_.doc, "shor", np_.oracle(), w=w, a={}, r=r, fuel=fuel)


class _Tracing(ir_v1.Instantiator):
    """An :class:`ir_v1.Instantiator` that records the *decision points*: every phase-product call
    made from outside the phase-product family (i.e. by ``shor`` or by a ``qft`` twiddle, never a
    band body's own recursive child), and every ``qft`` call made from outside ``qft``. Keyed by
    ``(template, xw, zw)``; ``qft`` has no second width, recorded as 0."""

    PP = ("phase_product", "cphase_product")

    def __init__(self, doc, oracle):
        super().__init__(doc, oracle)
        self.sites: Counter = Counter()
        self._in_pp = 0
        self._in_qft = 0

    def node(self, env, n, fuel):
        if n[0] != "call":
            return super().node(env, n, fuel)
        name = n[1]
        is_pp, is_qft = name in self.PP, name == "qft"
        if (is_pp and not self._in_pp) or (is_qft and not self._in_qft):
            wv = [self.w(env, x) for x in n[2]]
            self.sites[(name, wv[0], 0 if is_qft else wv[1])] += 1
        self._in_pp += is_pp
        self._in_qft += is_qft
        try:
            return super().node(env, n, fuel)
        finally:
            self._in_pp -= is_pp
            self._in_qft -= is_qft


def site_census(np_: "pi.NativePolicy", a: int, N: int, m: int, *, fuel: int = 800
                ) -> Dict[Tuple[str, int, int], int]:
    """``{(template, xw, zw): count}``: the decision points of one ``shor`` instantiation (see
    :class:`_Tracing`). Phase-product sites include the QFT twiddles' ``(l+1, r+1)`` pairs; recursive
    children are not sites, they follow from the policy."""
    lay = reference_layout(np_.policy, N, m)
    w, r = shor_env(lay, a, N)
    t = np_.doc.template("shor")
    inst = _Tracing(np_.doc, np_.oracle())
    env = ir_v1._Env(dict(w), {}, dict(r), t.table)
    inst.node(env, t.body, fuel)
    return dict(inst.sites)
