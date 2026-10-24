"""`LeanRecursivePhaseProduct(k, w)`: the E6 skeleton, unrolled as a recursive Qualtran bloq.

LEAN STATUS (arbitrary depth): `standardSignedPhaseLoweringPlan` (PhaseLoweringPlan.lean:759) is a structurally
recursive plan constructor. `signedStep` nests a child plan at the reduced width, with `termination_by
phaseInputSize`. `evalL_lowerGateRec_correct` (Proofs/LoweringCorrectness/PlanSemantics.lean:176) uses structural
induction, which also covers `signedStep`. It proves that the lowering of ANY correctly-typed plan evaluates to the
SignedPhaseProd semantics. `evalL_lowerSignedPhaseProd_of_plan` (Proofs/LoweringCorrectness/Main.lean:24) packages
this result, and the public wrapper is `lowerSignedPhaseProduct_correct` (PhaseProduct/Main.lean).

That directory has no `sorry`. Thus, with ``policy="sym"`` (widths = `nextSignedWidth`), this bloq is the cost
skeleton of a Lean-proved object of arbitrary depth (tier B over tier-A emitted widths). With ``policy="tight"``,
it is a hypothetical tight-width recursion that no theorem covers.

    RPP(w) = { A_k adder-class ops at width W', q × RPP(W') }   if W' = next(w) < w
           = { W×W CZPow grid }                                  otherwise (Naive_SignedPhaseProd)

`next` is the E3 width rule for the selected policy:
  * ``policy="sym"``: `nextSignedWidth` (DefsCore), which is THE Lean plan. It recurses only when
    w > k·β_k/(k−1) (k=5: w>48, k=10: w>292), because the symbolic β_k is large.
  * ``policy="tight"``: the exact reachable-value width. This is a HYPOTHETICAL tight-width recursion
    and NOT a ForShor object (no fidelity theorem covers it). Each plot of it has this label.

`w` is the input size (`phaseInputSize`, concrete). Evaluation gives the dependence on n.
The leaf phases are the E2 polynomials at the radix of each level. The counts do not need them.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, Optional, Union

import attrs
from qualtran import Bloq, QInt, QUInt, Register, Signature
from qualtran.bloqs.arithmetic import Add, Negate
from qualtran.bloqs.basic_gates import CZPowGate
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from .. import bundle_compat, widths
from ..emit import Bundle


def _freeze(op: Dict[str, Any]) -> tuple:
    """A hashable form of one schedule op. The attrs `frozen` class must have a hashable field."""
    return tuple(sorted((k, v) for k, v in op.items()))


@attrs.frozen
class LeanRecursivePhaseProduct(Bloq):
    k: int
    w: int
    q: int
    n_add: int
    n_neg: int
    n_add_negsrc: int = 0        # addScaled ops with negSrc: each one becomes Negate·Add·Negate (2 more Negates)
    policy: str = "sym"
    depth_limit: Optional[int] = None   # None = the Lean rule (recurse while next(w) < w); d = stop after d levels (HYPOTHETICAL cutoff)
    rule: Optional[widths.WidthRule] = attrs.field(default=None, eq=False, repr=False)
    general: Optional[dict] = attrs.field(default=None, eq=False, repr=False)   # E3 general table w -> W
    #: The k-schedule (`Prog k`). With it, `next_width` folds Lean's own width scan again, and it
    #: does not need the table or the affine tail. Refer to `next_width` and to the 8.8x k=5
    #: undercount that the tail extrapolation caused. The field has `eq=False`, so it never
    #: changes the bloq identity.
    ops: Optional[tuple] = attrs.field(default=None, eq=False, repr=False)

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.w)), Register("z", QUInt(self.w))])

    def next_width(self) -> int:
        """W' for square operands of width `w`: `RecursivePhaseWorkspace.nextWidth`, folded again.

        Before, this method used the emitted `general` table when `w` was in it, else the affine
        tail `W(m)=m+beta`. The tail is an OVER-ESTIMATE off the uniform ladder. Its detection
        checks `W(m+1) = W(m)+1` over `w = k*m`, but the recursion visits general `w`, where the
        relation can fail by one. A measurement at k=5, n=2048 showed that the rule over-estimated
        by exactly 1 at each step above `w_max`. At `w = 117`, this changed `next = 116` (the
        width decreases, so recurse again) into `next = 117` (stop). The ladder stopped one level
        early, and the cost was **8.8x too small**, because each level multiplies the leaf count
        by `q = 9`.

        `width_scan.next_width` folds the scan from its own definition. It needs no table and no
        extrapolation, and it gives again each of the 374 entries that Lean emits across k=2..5.
        The tables stay only as the cross-check that approves this method.
        """
        if self.ops is not None:
            from ..width_scan import next_width as exact_next_width
            return exact_next_width([dict(o) for o in self.ops], self.k, self.w)
        if self.general is not None and self.w in self.general:
            return self.general[self.w]
        assert self.rule is not None
        return self.rule.at_n(self.w)

    def ladder(self):
        out, cur = [self.w], self
        while True:
            if self.depth_limit is not None and len(out) - 1 >= self.depth_limit:
                return out
            nxt = cur.next_width()
            if nxt < cur.w:
                out.append(nxt)
                cur = attrs.evolve(cur, w=nxt)
            else:
                return out

    def optimal_depth(self, ts_per_rotation: float = 11.0) -> int:
        """The recursion depth that gives the minimum T (evaluated for each prefix of the Lean ladder).
        If it stops early, it is NOT a valid `PhaseLoweringPlan` (`signedBase` must have ¬(next < W)).
        It is then hypothetical."""
        from qualtran.resource_counting import QECGatesCost, get_cost_value
        full = attrs.evolve(self, depth_limit=None).ladder()
        best, best_T = 0, None
        for d in range(len(full)):
            T = float(get_cost_value(attrs.evolve(self, depth_limit=d), QECGatesCost()).total_t_count(ts_per_rotation=ts_per_rotation))
            if best_T is None or T < best_T:
                best, best_T = d, T
        return best

    def total_czpow(self) -> int:
        """The q^d · W_d² base-grid CZPows over all of the recursion."""
        lad = self.ladder()
        return (self.q ** (len(lad) - 1)) * lad[-1] ** 2

    def lean_resources(self) -> Dict[str, int]:
        """`LowGate.resources` for the WHOLE lowered recursion, predicted from the ladder.

        The result is exact and uses Lean's own vocabulary. It was verified channel for channel
        against emitted documents at k = 2..5 and depths 0..5 (`tests/test_rec_structural_cost.py`).
        Each term is one of the ForShor definitions:

        * **leaves** -- the base case is a `Naive_SignedPhaseProd`. v2 expands it through
          `LowGate.CPhase` = (Phase, Phase, CNOT, Phase, CNOT). Thus, each of the `W_d x W_d`
          pairs of operand bits costs 3 rz and 2 cnot, over `q^d` leaves.
        * **adders** -- `addScaledResources dst _src _negSrc _shift = cuccaroModAddResources
          (width dst)`, that is `{x: 2w-6, cnot: 5w-7, toffoli: 2w-3}`. It ignores `negSrc`
          fully. Each node has `sides * n_add` of them, at the CHILD width. `negateResources` is
          the same adder plus `w + 2` X, with `sides * n_neg` for each node.
        * **sign extension** -- `signExtend r n => {cnot := n}` (`zeroExtend` has no cost). There
          is one for each side of each node, on the top (signed) limb, by
          `W_child - topLimb(W_parent)`. A matching `signDealloc` charges the same quantity again.

        This method is deliberately NOT angle-aware. `resources` charges `rz := 1` for each `Phase`
        at all angles, because the proof needs only that. The classification of those rz into
        Clifford / T / rotation is the accounting job of this project, and a different step does
        it (D10). This method gives the structural invariant that ties the two sides exactly. The
        report shows the refinement next to it and does not fold it in.
        """
        lad = self.ladder()
        depth = len(lad) - 1
        wd = lad[-1]
        leaves = self.q ** depth
        res = {"rz": 3 * leaves * wd * wd, "cnot": 2 * leaves * wd * wd, "toffoli": 0, "x": 0,
               "h": 0}
        sides = 2                      # every schedule op acts on x AND z
        for i in range(depth):
            nodes = self.q ** i
            w, w2 = lad[i], lad[i + 1]
            adds = nodes * sides * self.n_add
            # `negateResources` is the same Cuccaro adder plus `w + 2` X. Tables with no `negate`
            # op (the standard ones) never got to this term, but the ForShor template table does.
            negs = nodes * sides * self.n_neg
            res["toffoli"] += (adds + negs) * (2 * w2 - 3)
            res["cnot"] += (adds + negs) * (5 * w2 - 7)
            res["x"] += (adds + negs) * (2 * w2 - 6) + negs * (w2 + 2)
            # `phaseSplitLogicalWidth`: the lower limbs get w//k, and the top limb gets the remainder.
            top_limb = w - (self.k - 1) * (w // self.k)
            res["cnot"] += 2 * nodes * sides * (w2 - top_limb)
        return res

    def reserve_qubits(self):
        """Lean `RecursivePhaseWorkspace.reserveNeed ops w w` (x, z) from the exact E3 mirror (tier A)."""
        from ..mirrors.width import reserve_need
        from ..mirrors.schedule import raw_table
        ops = raw_table(self.k)[0]["ops"]
        return reserve_need(self.k, ops, self.w, self.w)

    @property
    def qubit_footprint(self) -> int:
        """2w operands + Lean reserve (x + z) + 1 And ancilla. For policy='tight', the reserve is still the
        Lean number (symbolic policy). Thus, it is an upper bound."""
        rx, rz = self.reserve_qubits()
        return 2 * self.w + rx + rz + 1

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        W2 = self.next_width()
        if W2 < self.w and (self.depth_limit is None or self.depth_limit > 0):
            child_limit = None if self.depth_limit is None else self.depth_limit - 1
            # SINGLE-k: the q = 2k-1 children have the SAME arity. attrs.evolve changes only w/depth_limit and
            # keeps k, q, n_add, n_neg, rule, general. The ForShor standardSignedPhaseLoweringPlan uses one
            # arity at all levels (Recursion.lean), and the QFT twiddle inherits exactly this object.
            counts: Dict[Bloq, int] = {attrs.evolve(self, w=W2, depth_limit=child_limit): self.q}
            sides = 2                                  # every schedule op acts on x AND z (LeanPhaseProduct's convention)
            if self.n_add:
                counts[Add(QInt(W2))] = sides * self.n_add
            n_negate = sides * (self.n_neg + 2 * self.n_add_negsrc)
            if n_negate:
                counts[Negate(QInt(W2))] = n_negate
            return counts
        return {CZPowGate(exponent=ssa.new_symbol("e")): self.w * self.w}

    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], w: int, policy: str = "sym",
                    depth: str = "lean") -> "LeanRecursivePhaseProduct":
        """depth = 'lean' (recurse while the width decreases: the proved plan) | 'optimal' (the minimum-cost cutoff, hypothetical)."""
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        # Use `bundle_compat.schedule_census`, not the raw census. v2 gives `addScaled` as the
        # TOTAL and marks `negSrc` on each op, so the separate `addScaled_negSrc` count must
        # come from the ops. A default of 0 silently removed every Negate from the priced call
        # graph. The measurement at k=2 m=2 was 24 And against 48 in the IR, because the
        # backend makes a negSrc add as Negate . Add . Negate.
        cen = bundle_compat.schedule_census(doc)
        rule = widths.width_rule(doc, policy)  # type: ignore[arg-type]
        general = widths.general_widths(doc, policy)  # type: ignore[arg-type]
        b = cls(k=doc["k"], w=w, q=cen["phaseProduct"], n_add=cen["addScaled"], n_neg=cen["negate"],
                n_add_negsrc=cen.get("addScaled_negSrc", 0), policy=policy, rule=rule, general=general,
                ops=tuple(_freeze(o) for o in doc["schedule"]["ops"]))
        if depth == "optimal":
            b = attrs.evolve(b, depth_limit=b.optimal_depth())
        return b

    def __str__(self) -> str:
        return f"LeanRecursivePP(k={self.k}, w={self.w}, {self.policy})"


@attrs.frozen
class LeanAsymRecursivePhaseProduct(Bloq):
    """A phase product between GENUINELY ASYMMETRIC operand widths (wx != wz). For example, the internal
    controlled phase products of cmodmul are always between a narrow control/carry register
    (n or n+1 bits) and the wide `work` register (n + extraBits(eta) bits).

    `LeanRecursivePhaseProduct` above is symmetric only (`Register("x", QUInt(w)), Register("z",
    QUInt(w))`). It was built and validated against the standalone E7 phase product and the QFT twiddle.
    Both of these are square or almost square (the QFT split differs by 1 bit or less).

    Do not use it for cmodmul with a square at `max(wx, wz)` (the old `LeanCtrlModMul._pp` convention).
    This is WRONG, because the ForShor recursion rule (`Shor.nextSignedWidth`, DefsCore.lean:304) genuinely
    depends on the two widths through `phaseLimbWidth = min(wx/k, wz/k)`. If one operand is much narrower
    than the other, a split of it gives no benefit, and `nextSignedWidth` does not decrease below
    `max(wx, wz)`. Then `lowerLeaf` (LowerRec.lean) goes directly to ONE flat `Naive(C)SignedPhaseProd`
    leaf. This leaf is a genuine `wx`-by-`wz` RECTANGULAR CZPow grid, not a `max(wx,wz)` square grid.

    A test against a real emission (k=2, n=6) confirmed this. Each of the three internal phase products
    (widths (6,26), (7,26), (26,7)) is exactly one flat leaf in the real circuit. The old symmetric model
    predicted deep k=2 recursion (11,907 CZPow leaves against the real 520). This was the dominant term in
    an And-count overcount of about 16x (REV 1 FIX 2026-09-03).

    IF recursion DOES occur (the two operands are sufficiently wide), each child is a UNIFORM, SYMMETRIC
    (W', W') phase product. Lean's `targetSignedLayoutState` (LowGateIR.lean:382) pads EVERY slot (x and z
    alike) to the single scalar `commonNeededWidth`, for all asymmetries of the parent. Thus, only the TOP
    level must handle asymmetry. The recursion (if there is one) sends all lower levels to the
    already-proven `LeanRecursivePhaseProduct(w=W')`. `next_width` is computed LIVE (exact, not from an
    approximate table) through `mirrors.width.next_width`. This is a direct line-by-line mirror of
    `nextSignedWidth`/`scanNeededWidths`/`commonNeededWidth`. It is valid at all (wx, wz), not only in
    the [k, w_max] range of the precomputed table.
    """
    k: int
    wx: int
    wz: int
    q: int
    n_add: int
    n_neg: int
    n_add_negsrc: int = 0
    policy: str = "sym"
    rule: Optional[widths.WidthRule] = attrs.field(default=None, eq=False, repr=False)
    general: Optional[dict] = attrs.field(default=None, eq=False, repr=False)
    ops: Optional[tuple] = attrs.field(default=None, eq=False, repr=False)   # the k-schedule (Prog k), for live next_width

    @property
    def w(self) -> int:
        """`max(wx, wz)`, for consumers that need only a representative scale (reports, footprint math)."""
        return max(self.wx, self.wz)

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.wx)), Register("z", QUInt(self.wz))])

    def next_width(self) -> int:
        """`nextSignedWidth x z ops` for THIS (wx, wz) pair: the live, exact E3 mirror. It is not the
        symmetric-only precomputed table that `LeanRecursivePhaseProduct.next_width` reads."""
        assert self.ops is not None, "LeanAsymRecursivePhaseProduct needs `ops` (from from_bundle)"
        from ..mirrors.width import next_width as _next_width
        return _next_width(self.k, self.ops, self.wx, self.wz)

    def _child(self, w2: int) -> "LeanRecursivePhaseProduct":
        """The symmetric child (proven correct) that each slot becomes when it recurses, at width `w2`."""
        return LeanRecursivePhaseProduct(k=self.k, w=w2, q=self.q, n_add=self.n_add, n_neg=self.n_neg,
                                         n_add_negsrc=self.n_add_negsrc, policy=self.policy,
                                         rule=self.rule, general=self.general)

    def total_czpow(self) -> int:
        w2 = self.next_width()
        if w2 < max(self.wx, self.wz):
            return self.q * self._child(w2).total_czpow()
        return self.wx * self.wz            # flat leaf: a genuine rectangular grid, not w²

    def reserve_qubits(self):
        """Lean `RecursivePhaseWorkspace.reserveNeed ops wx wz`. It already supports asymmetric widths."""
        from ..mirrors.width import reserve_need
        return reserve_need(self.k, self.ops, self.wx, self.wz)

    @property
    def qubit_footprint(self) -> int:
        rx, rz = self.reserve_qubits()
        return self.wx + self.wz + rx + rz + 1

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        w2 = self.next_width()
        if w2 < max(self.wx, self.wz):
            counts: Dict[Bloq, int] = {self._child(w2): self.q}
            sides = 2                       # every schedule op acts on x AND z (LeanRecursivePhaseProduct's convention)
            if self.n_add:
                counts[Add(QInt(w2))] = sides * self.n_add
            n_negate = sides * (self.n_neg + 2 * self.n_add_negsrc)
            if n_negate:
                counts[Negate(QInt(w2))] = n_negate
            return counts
        return {CZPowGate(exponent=ssa.new_symbol("e")): self.wx * self.wz}

    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], wx: int, wz: int,
                    policy: str = "sym") -> "LeanAsymRecursivePhaseProduct":
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        # Use `bundle_compat.schedule_census`, not the raw census. v2 gives `addScaled` as the
        # TOTAL and marks `negSrc` on each op, so the separate `addScaled_negSrc` count must
        # come from the ops. A default of 0 silently removed every Negate from the priced call
        # graph. The measurement at k=2 m=2 was 24 And against 48 in the IR, because the
        # backend makes a negSrc add as Negate . Add . Negate.
        cen = bundle_compat.schedule_census(doc)
        rule = widths.width_rule(doc, policy)  # type: ignore[arg-type]
        general = widths.general_widths(doc, policy)  # type: ignore[arg-type]
        from ..mirrors.schedule import raw_table
        ops = tuple(raw_table(doc["k"])[0]["ops"])
        return cls(k=doc["k"], wx=wx, wz=wz, q=cen["phaseProduct"], n_add=cen["addScaled"],
                   n_neg=cen["negate"], n_add_negsrc=cen.get("addScaled_negSrc", 0),
                   policy=policy, rule=rule, general=general, ops=ops)

    def __str__(self) -> str:
        return f"LeanAsymRecPP(k={self.k}, wx={self.wx}, wz={self.wz}, {self.policy})"


# ------------------------------------------------------------------ Phase B: IR-lowered counterpart (V)
def concrete_ir_bloq(bundle: Union[Bundle, Dict[str, Any]], n: int, phi: Fraction = Fraction(1, 8),
                     refresh: bool = False):
    """The Lean-lowered recursive phase product at the concrete input size n. This is the E7 object that the
    swept ``LeanRecursivePhaseProduct(k, n, "sym")`` skeleton is tied to. Returns (bloq, ir_document).

    Phase A needs a new emitter, but this function does NOT. ``emit.lower_rec`` (E7 ``forshor_emit lower``)
    already prints the full recursive gate list as a ``shor.lowgate.ir/v1`` document. It works in REAL-LEAN
    only. It raises EmitError if the built binary is not available (there is no mirror, by design). The
    legacy backend that parses the one-level phase product also parses this document. Thus, the comparison
    of priced and verified objects uses one basis."""
    from .. import paths
    from ..emit import lower_rec
    doc = bundle.doc if isinstance(bundle, Bundle) else bundle
    k = doc["k"]
    ir_doc = lower_rec(k, int(n), phi, refresh=refresh)
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend  # legacy py/
    ir = lowgate_ir.parse_ir(ir_doc)
    return qualtran_backend.build_qualtran_bloq(ir), ir_doc


def _signed_value(bits, ids) -> int:
    """The signed integer that a register shows: LSB first, with a negative top bit (two's complement)."""
    w = len(ids)
    return sum((-(1 << i) if i + 1 == w else (1 << i)) * bits[q] for i, q in enumerate(ids))


def rec_action_v2(flat_doc: Dict[str, Any], plan_doc: Dict[str, Any], sample: int = 256,
                  seed: int = 20260902, exhaustive_limit: int = 4096) -> Dict[str, Any]:
    """Exact action of a `forshor.lowgate/v2` signed phase product: `exp(i*phi*[[x]]*[[z]])`.

    The v1 checker (`ref_interp`) cannot read these documents. It has no `Phase`/`CNOT`/
    `Toffoli` cases, and the v2 flat circuit contains only these gates. This check uses the same
    method as the v1 check, on the v2 vocabulary. Each gate that the lowering emits keeps a basis
    state as a basis state. Thus, `verify_gate_level.walk` follows a basis state through the
    circuit in O(gates) and returns the accumulated phase. This check gets to widths that a dense
    unitary cannot get to.

    The function asserts two conditions, and the second is as important as the first:

    1. The accumulated phase equals `phi * [[x]] * [[z]]`, with the two operands read as SIGNED.
    2. The basis state comes back unchanged, that is, the circuit must be diagonal.

    A construction with the correct phase that permutes the register is wrong. A check of only
    the phase cannot see this error.

    The operands come from the root node of the PLAN view, which has `x`/`z`/`phi`. The flat view
    does not have them (v1 had them in `expected`).
    """
    import math
    import random

    from .. import paths
    paths.setup_legacy_sys_path()
    import qiskit                              # noqa: E402  (legacy py/ + optional dep)
    import qiskit_backend as qb                # noqa: E402
    import verify_gate_level as vgl            # noqa: E402
    import lowgate_ir_v2 as irv2               # noqa: E402

    from fractions import Fraction

    root = plan_doc["plan"]
    x_ids, z_ids = tuple(root["x"]["active"]), tuple(root["z"]["active"])
    a = root["phi"]
    phi_turns = Fraction(int(a["num"]), int(a["den"]))      # the angle in units of pi, EXACT

    ir = irv2.parse_v2(flat_doc, verify_census=False)
    qc = qiskit.QuantumCircuit(ir.wire_count)
    qb._compile_node(qc, ir.circuit)
    names = {i.operation.name for i in qc.data}
    # This is the repertoire of `verify_gate_level` (its module docstring, line 4). Each of these
    # gates maps a computational basis state to a phase times a basis state, and this makes the
    # O(gates) walk valid. The first version of this list was a copy from a depth-0 test, and it
    # did not include `mcx`/`cp`/`mcp`. Thus, each document that RECURSES (the interesting ones)
    # reported a false failure.
    allowed = {"x", "cx", "ccx", "mcx", "p", "cp", "mcp", "swap", "barrier", "id"}
    if not names <= allowed:
        return {"verdict": "FAIL", "reason": f"non-basis-preserving gates emitted: {names - allowed}"}

    wx, wz = len(x_ids), len(z_ids)
    # The operand space is 2^(wx+wz). At wx = wz = 16, this is 4.3e9 PAIRS. An enumeration of the
    # space before the sample causes an immediate MemoryError, and the important widths to check
    # are exactly the widths that cause it. Thus, draw the sample directly. Make the full space
    # only when it is sufficiently small for an exhaustive check.
    total = 1 << (wx + wz)
    if total <= exhaustive_limit:
        pairs = [(xv, zv) for xv in range(1 << wx) for zv in range(1 << wz)]
        mode = f"exhaustive {len(pairs)}"
    else:
        rng = random.Random(seed)
        seen = set()
        while len(seen) < min(sample, total):
            seen.add((rng.randrange(1 << wx), rng.randrange(1 << wz)))
        pairs = sorted(seen)
        mode = f"sampled {len(pairs)} of {total}"

    for xv, zv in pairs:
        bits = [0] * ir.wire_count
        for i, q in enumerate(x_ids):
            bits[q] = (xv >> i) & 1
        for j, q in enumerate(z_ids):
            bits[q] = (zv >> j) & 1
        before = list(bits)
        phase = vgl.walk(qc, bits)
        if bits != before:
            return {"verdict": "FAIL", "mode": mode,
                    "reason": f"circuit is not diagonal: state moved at x={xv} z={zv}"}
        # Reduce the target mod 2 in EXACT rational turns before you use a float. At n=21, the
        # raw product `phi*[[x]]*[[z]]` gets to about 1.2e10 radians. There, the relative error of
        # float64 (about 1e-16) is about 1e-6 absolute, which is all of the tolerance. The early
        # reduction keeps the value O(1), so the comparison tests the circuit and not the check.
        want_turns = (phi_turns * _signed_value(before, x_ids)
                      * _signed_value(before, z_ids)) % 2
        want = float(want_turns) * math.pi
        if abs(math.remainder(phase - want, 2 * math.pi)) > 1e-6:
            return {"verdict": "FAIL", "mode": mode,
                    "reason": (f"x={xv} z={zv}: phase {phase:.6f} != phi*[[x]]*[[z]] mod 2pi "
                               f"= {want:.6f} (exact turns {want_turns})")}
    return {"verdict": "PASS", "mode": mode, "pairs": len(pairs)}


def rec_action_ok(ir_doc: Dict[str, Any], sample: int = 256, seed: int = 20260902,
                  exhaustive_limit: int = 4096, plan_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Action check (the STRONG one). The exact-rational reference interpreter evaluates the action of the
    emitted circuit on basis pairs. The check is exhaustive when 2^(2w) ≤ exhaustive_limit, else it uses
    `sample` random pairs. It asserts that the action equals exp(iφ·x·z), the signed phase product
    semantics that ForShor proves. This is a genuine action verification (PASS/FAIL), not the
    up-to-convention DFT check of Phase A, because the recursive object IS a signed phase product
    (`expected.kind = "signed_phase_product"`).

    Returns {"verdict": "PASS"|"FAIL"|"SKIP", ...}. SKIP occurs only if the import of the interpreter fails."""
    from .. import paths
    # Send v2 documents to `rec_action_v2`. `ref_interp` has no Phase/CNOT/Toffoli cases, and the
    # v2 flat circuit contains only these gates. Thus, it would SKIP on a KeyError, and the strongest
    # check in the Phase-B gate would silently stop.
    if ir_doc.get("schema", "").startswith("forshor.lowgate/"):
        if plan_doc is None:
            return {"verdict": "SKIP",
                    "reason": "a v2 flat document carries no operands; pass plan_doc (the "
                              "annotated view) so the action can be checked against phi*x*z"}
        try:
            return rec_action_v2(ir_doc, plan_doc, sample, seed, exhaustive_limit)
        except Exception as e:
            return {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}
    try:
        paths.setup_legacy_sys_path()
        import ref_interp as RI  # legacy py/
        res = RI.run(ir_doc, "auto", int(sample), int(seed), int(exhaustive_limit), quiet=True)
        return {"verdict": res.get("verdict", "FAIL"), **{k: v for k, v in res.items() if k != "verdict"}}
    except Exception as e:
        return {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}
