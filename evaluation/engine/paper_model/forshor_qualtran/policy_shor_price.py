r"""The native ``shor`` template priced under its own policy (Stage 4b), nothing flattened.

`policy_shor_sym` gives the template's census in closed form in ``n``: the leaves ``shor`` itself
performs and its *sites* ``{(template, xw, zw): multiplicity}``. This module prices that census:

* :class:`PolicyQFT` is the IR's ``qft`` as a Qualtran bloq. Its call graph is the template's own:
  ``qft(w) = qft(right); twiddle; qft(left); RadixReverse(w, left)`` with the twiddle the policy phase
  product on the zero-extended pair ``(left+1, right+1)``. ``RadixReverse`` is ``left // 2``
  ``TwoBitSwap`` (ForShor's ``3 * (m div 2)`` CNOTs), and a width-1 QFT is one ``H``. It is the same
  construction as ``LeanQFT(phase_policy=P)`` but needs no single-k host bundle.
* :class:`PolicyShorCore` is one Shor instance: the census evaluated at ``nbits``, every leaf as a
  Qualtran bloq of the convention (``Add``/``Subtract``/``Negate`` or ForShor's Cuccaro adders), every
  site as `PolicyPhaseProduct` / `CPolicyPhaseProduct` / :class:`PolicyQFT`. Qualtran walks the call
  graph, so ``n = 2048`` costs a few seconds and no circuit is built.
* :func:`price` turns the call graph into T. Every rotation of the whole run shares one synthesis
  budget, so a rotation costs Ross–Selinger at ``eps = delta_rot / R_total``, where ``R_total`` is the
  whole run's rotation count. Leaf realisations (the QUALTRAN tier picks grid or product per tree on
  price) are re-chosen at that context until ``R_total`` stops changing.

Two views, as for the phase product:

* *cost*: the Qualtran call graph under a `cost_conventions` spec. QUALTRAN is the headline tier and
  LEAN_FAITHFUL the cross-check;
* *meaning*: :meth:`PolicyShorCore.lean_resources` predicts ForShor's ``LowGate.resources`` (``h, x,
  cnot, toffoli, rz``) of the instantiated template. `tests/test_policy_shor_price.py` requires it to
  equal ``lowgate_resources`` of the flat circuit `policy_shor_ir.instantiate_shor` builds.

Tier of the claim: the IR is ForShor's "equal to the real circuit at sampled widths", and the Shor
template is the approximate Algorithm-1 core. Matching it at small ``N`` validates the structure (the
census and the site pricing). It does not validate a 2048-bit gate count; that number is an
evaluation of the validated structure at ``n = 2048``.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import attrs
from qualtran import Bloq, QBit, QInt, QUInt, Register, Signature
from qualtran.resource_counting import BloqCountDictT, QubitCount, SympySymbolAllocator

from . import cost_conventions as cc
from . import policy_shor_ir as psi
from . import policy_shor_sym as sy
from .bloqs.policy_phase_product import CPolicyPhaseProduct, PolicyPhaseProduct, _norm_policy
from .policy import PhasePolicy

#: `LowGate.resources` channels (``cleanAnc`` is a peak, not additive, and is not modelled here).
CHANNELS = ("h", "x", "cnot", "toffoli", "rz")
SITE_TEMPLATES = sy.SITE_TEMPLATES


def _zero() -> Dict[str, int]:
    return {k: 0 for k in CHANNELS}


def _acc(out: Dict[str, int], res: Dict[str, int], m: int = 1) -> None:
    for k in CHANNELS:
        out[k] += m * res.get(k, 0)


# ============================================================================ the QFT
@lru_cache(maxsize=1 << 14)
def _qft_lean(policy: PhasePolicy, w: int) -> Tuple[int, ...]:
    if w < 2:
        return tuple(w if k == "h" else 0 for k in CHANNELS)
    left, right = w // 2, w - w // 2
    out = _zero()
    _acc(out, PolicyPhaseProduct(policy, left + 1, right + 1).lean_resources())
    out["cnot"] += 3 * (left // 2)                     # RadixReverse(w, left)
    for sub in (left, right):
        _acc(out, dict(zip(CHANNELS, _qft_lean(policy, sub))))
    return tuple(out[k] for k in CHANNELS)


def qft_lean_resources(policy: PhasePolicy, w: int) -> Dict[str, int]:
    """``LowGate.resources`` of the IR's ``qft`` at width ``w`` under ``policy``."""
    return dict(zip(CHANNELS, _qft_lean(_norm_policy(policy), int(w))))


@attrs.frozen
class PolicyQFT(Bloq):
    """The native ``qft`` template at width ``w``: twiddles are policy phase products."""
    policy: PhasePolicy = attrs.field(converter=_norm_policy)
    w: int = attrs.field(converter=int)
    convention: cc.ConventionSpec = cc.QUALTRAN

    @property
    def signature(self) -> Signature:
        return Signature([Register("r", QUInt(max(1, self.w)))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.basic_gates import Hadamard, TwoBitSwap
        if self.w == 0:
            return {}
        if self.w == 1:
            return {Hadamard(): 1}
        left, right = self.w // 2, self.w - self.w // 2
        out: Counter = Counter()
        out[attrs.evolve(self, w=right)] += 1
        out[attrs.evolve(self, w=left)] += 1
        out[PolicyPhaseProduct(self.policy, left + 1, right + 1, convention=self.convention)] += 1
        if left // 2:
            out[TwoBitSwap()] += left // 2
        return dict(out)

    def lean_resources(self) -> Dict[str, int]:
        return qft_lean_resources(self.policy, self.w)

    def __str__(self) -> str:
        return f"PolicyQFT({self.policy.name}, {self.w})"


# ============================================================================ sites
def site_bloq(policy: PhasePolicy, site: Tuple[str, int, int], spec: cc.ConventionSpec) -> Bloq:
    name, a, b = site
    if name == "phase_product":
        return PolicyPhaseProduct(policy, a, b, convention=spec)
    if name == "cphase_product":
        return CPolicyPhaseProduct(policy, a, b, convention=spec)
    if name == "qft":
        return PolicyQFT(policy, a, spec)
    raise ValueError(f"not a priced site: {name}")


def site_lean_resources(policy: PhasePolicy, site: Tuple[str, int, int]) -> Dict[str, int]:
    name, a, b = site
    if name == "qft":
        return qft_lean_resources(policy, a)
    cls = CPolicyPhaseProduct if name == "cphase_product" else PolicyPhaseProduct
    return cls(policy, a, b).lean_resources()


# ============================================================================ the layout
def layout_need(policy: PhasePolicy, widths: Dict[str, int]) -> Dict[str, int]:
    """Reserve sizes ``allocateReferenceLayout`` gives each register (`policy_shor_ir.reference_layout`)."""
    need = psi.workspace_need(policy, widths["xW"], widths["yW"], widths["workW"], widths["scratchW"])
    return {"x": need["exponent"], "y": max(2, need["data"]), "work": max(1, need["auxiliary"]),
            "scratch": max(1, need["scratch"])}


def layout_qubits(policy: PhasePolicy, widths: Dict[str, int]) -> int:
    """Qubits ``allocateReferenceLayout`` places: four registers with their reserves, and the flag."""
    r = layout_need(policy, widths)
    return widths["xW"] + widths["yW"] + widths["workW"] + widths["scratchW"] + sum(r.values()) + 1


# ============================================================================ one Shor instance
def _items(c: Counter) -> Tuple:
    return tuple(sorted((k, int(v)) for k, v in c.items() if v))


@attrs.frozen
class PolicyShorCore(Bloq):
    """The native ``shor`` template at one instance (``nbits`` and a :class:`policy_shor_sym.Convention`),
    as a Qualtran bloq. Build with :meth:`from_native`."""
    policy: PhasePolicy = attrs.field(converter=_norm_policy)
    nbits: int
    h: int
    x: int
    cnot: int
    toffoli: int
    add_scaled: Tuple[Tuple[int, int], ...]
    add_scaled_neg: Tuple[Tuple[int, int], ...]
    negate: Tuple[Tuple[int, int], ...]
    sites: Tuple[Tuple[Tuple[str, int, int], int], ...]
    widths: Tuple[Tuple[str, int], ...]
    convention: cc.ConventionSpec = cc.QUALTRAN

    @classmethod
    def from_native(cls, nat, nbits: int, conv: sy.Convention,
                    convention: cc.ConventionSpec = cc.QUALTRAN) -> "PolicyShorCore":
        c = sy.shor_census(nat.doc, conv).at(int(nbits))
        lw = sy.layout_widths(conv, int(nbits))
        return cls(nat.policy, int(nbits), c["h"], c["x"], c["cnot"], c["toffoli"],
                   _items(c["add_scaled"]), _items(c["add_scaled_neg"]), _items(c["negate"]),
                   _items(c["sites"]), tuple(sorted((k, int(v)) for k, v in lw.items())), convention)

    @property
    def signature(self) -> Signature:
        w = dict(self.widths)
        return Signature([Register("x", QUInt(w["xW"])), Register("y", QUInt(w["yW"])),
                          Register("work", QUInt(w["workW"])), Register("scratch", QUInt(w["scratchW"])),
                          Register("flag", QBit())])

    # ---- cost view
    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        from qualtran.bloqs.arithmetic import Add, Negate, Subtract
        from qualtran.bloqs.basic_gates import CNOT, Hadamard, Toffoli, XGate
        spec = self.convention
        out: Counter = Counter()

        def put(b, k):
            if k:
                out[b] += k

        put(Hadamard(), self.h)
        put(XGate(), self.x)
        put(CNOT(), self.cnot)
        put(Toffoli(), self.toffoli)
        neg = dict(self.add_scaled_neg)
        for w, k in self.add_scaled:
            kn = neg.get(w, 0)
            if spec.adder == "cuccaro":                 # Lean charges one adder whatever the sign
                put(cc.CuccaroAdder(w), k)
                if spec.negsrc == "negate_add_negate":
                    put(cc.CuccaroAdder(w, negate=True), 2 * kn)
            elif spec.negsrc == "subtract":
                put(Add(QInt(w)), k - kn)
                put(Subtract(QInt(w)), kn)
            else:
                put(Add(QInt(w)), k)
                put(Negate(QInt(w)), 2 * kn)
        for w, k in self.negate:
            put(cc.CuccaroAdder(w, negate=True) if spec.adder == "cuccaro" else Negate(QInt(w)), k)
        for site, k in self.sites:
            put(site_bloq(self.policy, site, spec), k)
        return dict(out)

    # ---- meaning view
    def lean_resources(self) -> Dict[str, int]:
        """``LowGate.resources`` of the instantiated ``shor`` template (`ir_v1.lowgate_resources` rules:
        ``AddScaled`` = ``cuccaroModAddResources (width dst)``, ``Negate`` = ``negateResources``)."""
        out = _zero()
        _acc(out, {"h": self.h, "x": self.x, "cnot": self.cnot, "toffoli": self.toffoli})
        for w, k in self.add_scaled:
            _acc(out, cc.cuccaro_resources(w), k)
        for w, k in self.negate:
            _acc(out, cc.negate_resources(w), k)
        for site, k in self.sites:
            _acc(out, site_lean_resources(self.policy, site), k)
        return out

    def layout_qubits(self) -> int:
        return layout_qubits(self.policy, dict(self.widths))

    def my_static_costs(self, cost_key):
        if isinstance(cost_key, QubitCount):
            return self.layout_qubits()
        return NotImplemented

    def __str__(self) -> str:
        return f"PolicyShorCore({self.policy.name}, n={self.nbits})"


# ============================================================================ pricing
def price(core: PolicyShorCore, spec: Optional[cc.ConventionSpec] = None, *, max_iter: int = 6) -> Dict[str, Any]:
    """T of one run of ``core`` under ``spec`` (default: ``core.convention``).

    ``T = T_base + R_total * t_RS(eps)`` with ``eps = delta * rotation_fraction / R_total``. Leaves
    are re-chosen at ``r_context = R_total`` until ``R_total`` is a fixed point (``converged``)."""
    base = core.convention if spec is None else spec
    ctx: Optional[int] = None
    history = []
    for _ in range(max_iter):
        s = base.with_(r_context=ctx)
        c = cc.split(attrs.evolve(core, convention=s))
        history.append(c.rot)
        if c.rot == ctx:
            break
        ctx = c.rot
    converged = len(history) >= 2 and history[-1] == history[-2]
    s = base.with_(r_context=c.rot)
    t_rot = s.t_per_rotation(c.rot) if c.rot else 0.0
    return {"T": c.base + c.rot * t_rot, "T_base": int(c.base), "rotations": int(c.rot),
            "T_per_rotation": t_rot, "eps_per_rotation": s.budget.epsilon(c.rot) if c.rot else None,
            "converged": converged, "r_context_history": history, "convention": base.to_json()}
