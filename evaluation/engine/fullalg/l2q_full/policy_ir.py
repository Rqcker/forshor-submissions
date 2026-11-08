r"""ForShor's native policy IR (`forshor.policy-submission/v1`, field ``ir``), read as it is emitted.

Until ForShor emitted an IR for a policy, :func:`policy.compose` built one from per-table documents
and dropped the ``qft``, ``shor_gate`` and ``shor`` templates. ForShor now ships the whole document
(`Emit/IR/README.md`, "A policy document"):

* ``phase_product`` / ``cphase_product`` are **dispatchers** (a guard chain on
  ``threshold_i <= max(xw, zw)``, proved equal to ``choose``), falling through to ``naive_leaf``;
* ``phase_product_<i>`` / ``cphase_product_<i>`` are band ``i``'s bodies, whose recursive child is a
  call back to the dispatcher;
* ``qft``, ``shor_gate``, ``shor`` reach the phase product through the dispatcher.

Two conventions are specific to that document and are what this module supplies, because the
document itself carries neither:

1. **Band identity.** A band body's ``nextWidth`` is its own band's table. The template name's
   ``_<i>`` suffix says which band, and :func:`tag_bands` turns it into ``Template.table`` (the
   band names are ``policy.band_names``, i.e. ``band<i>``), exactly what ``compose`` did by hand.
2. **``coeff`` index.** ``coeff``'s point index is a position in the bands' interpolation point
   lists *concatenated*, band 0 first: band ``i`` starts at ``sum_{j<i} q(k_j)``
   (`Registry.coeffOffset`). :class:`NativeOracle` subtracts the offset before asking the band's table.

``reserveNeed_x/z`` and (new here) ``qftXWork`` / ``qftZWork`` are policy-level: the QFT budget is
``Policy.qftWorkspaceNeed`` = ``qftWorkspaceNeedOf (Policy.reserveNeed P)`` (`QFT/Lowering/Workspace.lean`).

**Scope: validation only.** Everything here *instantiates* the IR at a concrete width. It is the
ground truth a symbolic bloq is checked against at small widths; it must never build a priced object
(priced bloqs keep ``n`` symbolic, see ``PLAN`` in the audit notes). Nothing is a theorem: ForShor's
own tier for the IR is "equal to the real circuit at sampled widths".
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from . import ir_v1, policy as pol, schemas

#: Band-body template families: ``<family>_<i>``.
BAND_FAMILIES = ("phase_product", "cphase_product")
_BAND_RE = re.compile(r"^(phase_product|cphase_product)_(\d+)$")

#: Templates a native policy document must contain (the single-table document's set).
REQUIRED = ("phase_product", "cphase_product", "naive_leaf", "naive_cleaf", "qft", "shor_gate", "shor")


class NativeIRError(pol.PolicyError):
    pass


# ============================================================================ the document
class NativePolicy:
    """A policy and the native IR ForShor emitted for it, tied together.

    ``policy`` is built from the submission's own ``bands`` (so thresholds, ``k``, points and ops
    are ForShor's); ``doc`` is the IR with every band body tagged with its band's table name.
    """

    def __init__(self, policy: pol.PhasePolicy, doc: ir_v1.Doc, info: Dict[str, Any]):
        self.policy = policy
        self.doc = doc
        self.info = info

    @property
    def names(self) -> List[str]:
        return [b.table.name for b in self.policy.bands]

    @property
    def offsets(self) -> List[int]:
        return coeff_offsets(self.policy)

    def oracle(self) -> "NativeOracle":
        return NativeOracle(self.policy)


def coeff_offsets(policy: pol.PhasePolicy) -> List[int]:
    """``offsets[i] = sum_{j<i} q(k_j)``: where band ``i``'s indices start in the concatenation."""
    out, acc = [], 0
    for b in policy.bands:
        out.append(acc)
        acc += pol.q(b.table.k)
    return out


def tag_bands(doc: ir_v1.Doc, names: Sequence[str]) -> ir_v1.Doc:
    """Set ``Template.table`` on every band body from its ``_<i>`` suffix. Refuses a document whose
    band-body set is not exactly ``{phase_product_i, cphase_product_i : i < len(names)}``."""
    seen: Dict[str, set] = {f: set() for f in BAND_FAMILIES}
    templates: Dict[str, ir_v1.Template] = {}
    for name, t in doc.templates.items():
        m = _BAND_RE.match(name)
        if m is None:
            templates[name] = t
            continue
        fam, i = m.group(1), int(m.group(2))
        if i >= len(names):
            raise NativeIRError(f"{name}: band index {i} but the policy has {len(names)} bands")
        seen[fam].add(i)
        templates[name] = replace(t, table=names[i])
    want = set(range(len(names)))
    for fam, got in seen.items():
        if got != want:
            raise NativeIRError(f"{fam}: band bodies {sorted(got)}, expected {sorted(want)}")
    return replace(doc, templates=templates)


def _check_shape(P: pol.PhasePolicy, doc: ir_v1.Doc) -> None:
    missing = [t for t in REQUIRED if t not in doc.templates]
    if missing:
        raise NativeIRError(f"native policy IR lacks templates {missing}")
    if doc.entry != "phase_product":
        raise NativeIRError(f"entry is {doc.entry!r}, expected phase_product")
    for fam in BAND_FAMILIES:
        for i, b in enumerate(P.bands):
            t = doc.templates[f"{fam}_{i}"]
            # a step body calls the *dispatcher* once per product coefficient (q k children)
            n = len(pol._calls(t.body, fam))
            if n != pol.q(b.table.k):
                raise NativeIRError(f"{t.name}: {n} recursive calls, band {i} has q k = {pol.q(b.table.k)} "
                                    "- the IR band does not belong to this table")
            if (t.wParams, t.aParams) != (doc.templates[fam].wParams, doc.templates[fam].aParams):
                raise NativeIRError(f"{t.name}: parameters differ from the dispatcher's")


def load_native(sub: Union[Dict[str, Any], str, Path]) -> NativePolicy:
    """Read ForShor's ``forshor_policy_submission`` output, IR included.

    Refuses (never repairs): a wrong schema, a submission with ``ir: null``, a band-body set that is
    not one body per band and flavour, a body whose recursion arity is not its table's ``q k``."""
    if not isinstance(sub, dict):
        sub = json.loads(Path(sub).read_text(encoding="utf-8"))
    P, _, info = pol.load_policy_submission(sub)       # schema, C1, ordering, table data
    if sub.get("ir") is None:
        raise NativeIRError("policy submission has no native IR (`ir` is null); use policy.compose")
    rule = pol.doc_width_rule(sub)                     # sto_site Stage 18: the document's own width rule
    if rule != "scan":
        P = pol.with_width_rule(P, rule)
    info = {**info, "width_rule": rule}
    doc = ir_v1.parse_doc(sub)
    doc = tag_bands(doc, [b.table.name for b in P.bands])
    _check_shape(P, doc)
    return NativePolicy(P, doc, info)


# ============================================================================ the oracle
@lru_cache(maxsize=1 << 14)
def _qft_need(P: pol.PhasePolicy, w: int) -> Tuple[int, int]:
    """``Policy.qftWorkspaceNeed P w`` (``qftWorkspaceNeedOf (Policy.reserveNeed P)``)."""
    if w < 2:
        return (0, 0)
    left, right = w // 2, w - w // 2
    px, pz = P.reserve_need(left + 1, right + 1)
    lx, lz = _qft_need(P, left)
    rx, rz = _qft_need(P, right)
    return (max(1 + px, max(lx, rx)), max(1 + pz, max(lz, rz)))


def qft_workspace_need(P: pol.PhasePolicy, w: int) -> Tuple[int, int]:
    return _qft_need(P, int(w))


class NativeOracle(pol.PolicyOracle):
    """:class:`policy.PolicyOracle` for the native document: ``coeff`` takes the concatenated index,
    and the two QFT workspace opaques are policy-level."""

    def __init__(self, policy: pol.PhasePolicy):
        super().__init__(policy, default=None)     # `default` stays None for a multi-band policy
        self._names = [b.table.name for b in policy.bands]
        self._offsets = coeff_offsets(policy)

    def opaque_w(self, fn, args, table):
        if fn in ("qftXWork", "qftZWork") and len(args) == 1:
            need = qft_workspace_need(self.policy, args[0])
            return need[0] if fn == "qftXWork" else need[1]
        return super().opaque_w(fn, args, table)

    def coeff(self, l, m, table):
        if table is None and len(self._names) == 1:
            table = self._names[0]            # a single-table document: one band, offset 0
        if table is None or table not in self._names:
            return None                       # an untagged template has no band to resolve against
        i = self._names.index(table)
        local = l - self._offsets[i]
        if not 0 <= local < pol.q(self.policy.bands[i].table.k):
            return None                       # an index outside the band's own slice is a bug upstream
        return super().coeff(local, m, table)


# ============================================================================ binding
class _OneLevel(ir_v1.Instantiator):
    """Instantiates one band body and records its calls to the dispatchers instead of descending."""

    def __init__(self, doc, oracle):
        super().__init__(doc, oracle)
        self.calls: List[Tuple[str, Tuple[int, ...], Tuple[Fraction, ...]]] = []

    def node(self, env, n, fuel):
        if n[0] == "call" and n[1] in BAND_FAMILIES:
            self.calls.append((n[1], tuple(self.w(env, x) for x in n[2]), tuple(self.a(env, x) for x in n[3])))
            return []
        return super().node(env, n, fuel)


def _leaf_counts(leaves) -> Dict[str, List[int]]:
    out: Dict[str, List[int]] = {"AddScaled": [], "Negate": []}

    def go(gs):
        for g in gs:
            if g["op"] == "adj":
                go(g["body"])
            elif g["op"] == "AddScaled":
                out["AddScaled"].append(len(g["dst"]["active"]))
            elif g["op"] == "Negate":
                out["Negate"].append(len(g["r"]["active"]))
    go(leaves)
    return out


def verify_native_binding(np_: NativePolicy, *, phi: Fraction = Fraction(1, 1 << 20)) -> List[Dict[str, Any]]:
    """Check that every band body of the native document runs *its own band's table*, one level deep.

    For band ``i`` at its threshold width ``w`` (the narrowest width the dispatcher routes to it), the
    body ``(c)phase_product_i`` is instantiated with its recursive calls recorded, not expanded, and
    must match the table: ``q k`` child calls, each at the policy's child width ``nextWidth``, the
    ``l``-th at angle ``phi * coeff_l(limb)``; ``2 * #addScaled`` adders and ``2 * #negate`` negations,
    all at the child width. Arity alone (:func:`load_native`) cannot tell two tables with the same
    ``k`` apart. This can, and costs ``O(w)`` at any threshold. Raises :class:`NativeIRError`."""
    P, out = np_.policy, []
    for i, b in enumerate(P.bands):
        T, w = b.table, max(b.threshold, 2)
        s = P.step(w, w)
        if s is None or s[0] is not T:
            out.append({"band": i, "width": w, "checked": False, "reason": "band does not recurse at its threshold"})
            continue
        _, w2 = s
        limb = w // T.k
        cen = T.census()
        for fam in BAND_FAMILIES:
            x, z, ctrl = pol.pp_registers(P, w, 1)
            r = {"x": x, "z": z}
            if fam == "cphase_product":
                r["ctrl"] = ir_v1.ExtReg((ctrl,))
            t = np_.doc.template(f"{fam}_{i}")
            inst = _OneLevel(np_.doc, np_.oracle())
            env = ir_v1._Env({"xw": x.width, "zw": z.width, "xCap": x.capacity, "zCap": z.capacity},
                             {"phi": Fraction(phi)}, r, t.table)
            leaves = _leaf_counts(inst.node(env, t.body, 2))
            want_angles = [Fraction(phi) * T.coeff(l, limb) for l in range(pol.q(T.k))]
            bad = []
            if [c[0] for c in inst.calls] != [fam] * pol.q(T.k):
                bad.append(f"child calls {[c[0] for c in inst.calls]}")
            if any(c[1][:2] != (w2, w2) for c in inst.calls):
                bad.append(f"child widths {sorted({c[1][:2] for c in inst.calls})}, policy says {(w2, w2)}")
            if [c[2][0] for c in inst.calls] != want_angles:
                bad.append("child angles are not the table's interpolation weights")
            if sorted(leaves["AddScaled"]) != [w2] * (2 * cen["addScaled"]):
                bad.append(f"{len(leaves['AddScaled'])} adders, table has {2 * cen['addScaled']} at width {w2}")
            if sorted(leaves["Negate"]) != [w2] * (2 * cen["negate"]):
                bad.append(f"{len(leaves['Negate'])} negations, table has {2 * cen['negate']}")
            if bad:
                raise NativeIRError(f"{t.name} does not run band {i}'s table at width {w}: " + "; ".join(bad))
        out.append({"band": i, "width": w, "child_width": w2, "checked": True})
    return out


# ============================================================================ instantiation (validation)
def instantiate_pp(np_: NativePolicy, n: int, phi: Fraction, *, wz: Optional[int] = None, slack: int = 1,
                   controlled: bool = False, fuel: int = 600) -> List[Dict[str, Any]]:
    """The (controlled) signed phase product at ``(n, wz)`` from the native dispatcher, flat leaves.
    Registers are ``policy.pp_registers`` (`Shor.ppRegisters` with the policy-level reserve)."""
    x, z, ctrl = pol.pp_registers(np_.policy, n, slack, wz)
    w = {"xw": x.width, "zw": z.width, "xCap": x.capacity, "zCap": z.capacity}
    r = {"x": x, "z": z}
    if controlled:
        r["ctrl"] = ir_v1.ExtReg((ctrl,))
    return ir_v1.instantiate(np_.doc, "cphase_product" if controlled else "phase_product", np_.oracle(),
                             w=w, a={"phi": Fraction(phi)}, r=r, fuel=fuel)


def qft_registers(P: pol.PhasePolicy, w: int) -> Tuple[ir_v1.ExtReg, ir_v1.ExtReg, ir_v1.ExtReg]:
    """``(r, xWork, zWork)`` for ``qft`` at width ``w``: `policyQftRegister` (active ``[0, w)``,
    reserve ``[w, w + need.x + need.z)``) and the two disjoint reserve prefixes
    (`qftXWorkOf` / `qftZWorkOf`)."""
    nx, nz = qft_workspace_need(P, w)
    res = tuple(range(w, w + nx + nz))
    r = ir_v1.ExtReg(tuple(range(w)), res)
    return r, ir_v1.ExtReg(res[:nx]), ir_v1.ExtReg(res[nx:nx + nz])


def instantiate_qft(np_: NativePolicy, w: int, *, fuel: int = 600) -> List[Dict[str, Any]]:
    """The native ``qft`` template at width ``w``, flat leaves."""
    r, xw, zw = qft_registers(np_.policy, w)
    return ir_v1.instantiate(np_.doc, "qft", np_.oracle(),
                             w={"w": r.width, "xWorkW": xw.width, "zWorkW": zw.width}, a={},
                             r={"r": r, "xWork": xw, "zWork": zw}, fuel=fuel)
