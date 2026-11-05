"""`LeanRecursivePhaseProduct(k, w)` — the E6 skeleton unrolled as a recursive Qualtran bloq.

LEAN STATUS (arbitrary depth): `standardSignedPhaseLoweringPlan` (PhaseLoweringPlan.lean:759) is a structurally
recursive plan constructor (`signedStep` nests a child plan at the reduced width, `termination_by phaseInputSize`);
`evalL_lowerGateRec_correct` (Proofs/LoweringCorrectness/PlanSemantics.lean:176) proves by structural induction —
covering `signedStep` — that lowering ANY correctly-typed plan evaluates to the SignedPhaseProd semantics, and
`evalL_lowerSignedPhaseProd_of_plan` (Proofs/LoweringCorrectness/Main.lean:24) packages it; the public wrapper is
`lowerSignedPhaseProduct_correct` (PhaseProduct/Main.lean). No `sorry` in that directory. So with ``policy="sym"``
(widths = `nextSignedWidth`) this bloq is the cost skeleton of a Lean-proved, arbitrary-depth object (tier B over
tier-A emitted widths); ``policy="tight"`` is a hypothetical tight-width recursion with no theorem.

    RPP(w) = { A_k adder-class ops at width W', q × RPP(W') }   if W' = next(w) < w
           = { W×W CZPow grid }                                  otherwise (Naive_SignedPhaseProd)

`next` is the E3 width rule for the chosen policy:
  * ``policy="sym"``  — `nextSignedWidth` (DefsCore): THE Lean plan. Note it fires only when
    w > k·β_k/(k−1) (k=5: w>48, k=10: w>292) because the symbolic β_k is large;
  * ``policy="tight"`` — the exact reachable-value width: a HYPOTHETICAL tight-width recursion,
    NOT a ForShor object (no fidelity theorem covers it); labelled as such wherever it is drawn.

`w` is the input size (`phaseInputSize`, concrete); the n-dependence is obtained by evaluation.
Leaf phases are the E2 polynomials at each level's radix (not needed for counting).
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
    """A hashable form of one schedule op (attrs `frozen` needs the field to be hashable)."""
    return tuple(sorted((k, v) for k, v in op.items()))


@attrs.frozen
class LeanRecursivePhaseProduct(Bloq):
    k: int
    w: int
    q: int
    n_add: int
    n_neg: int
    n_add_negsrc: int = 0        # addScaled ops with negSrc: realised as Negate·Add·Negate (2 extra Negates)
    policy: str = "sym"
    depth_limit: Optional[int] = None   # None = the Lean rule (recurse while next(w) < w); d = stop after d levels (HYPOTHETICAL cutoff)
    rule: Optional[widths.WidthRule] = attrs.field(default=None, eq=False, repr=False)
    general: Optional[dict] = attrs.field(default=None, eq=False, repr=False)   # E3 general table w -> W
    #: The k-schedule (`Prog k`). With it, `next_width` re-folds Lean's own width scan and needs
    #: neither the table nor the affine tail -- see `next_width`, and the 8.8x k=5 undercount
    #: that extrapolating the tail caused. Carried `eq=False` so it never affects bloq identity.
    ops: Optional[tuple] = attrs.field(default=None, eq=False, repr=False)

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.w)), Register("z", QUInt(self.w))])

    def next_width(self) -> int:
        """W' for square operands of width `w`: `RecursivePhaseWorkspace.nextWidth`, re-folded.

        Was: the emitted `general` table when `w` was in it, else the affine tail `W(m)=m+beta`.
        The tail is an OVER-ESTIMATE off the uniform ladder -- it is detected by checking
        `W(m+1) = W(m)+1` over `w = k*m`, and the recursion visits general `w`, where the
        relation can fail by one. Measured at k=5, n=2048: the rule over-estimated by exactly 1
        at every step above `w_max`, and at `w = 117` that turned `next = 116` (shrinks, recurse
        again) into `next = 117` (stop). The ladder ended a level early and the cost came out
        **8.8x too small**, because each level multiplies the leaf count by `q = 9`.

        `width_scan.next_width` folds the scan from its own definition, needs no table and no
        extrapolation, and reproduces every one of the 374 entries Lean emits across k=2..5.
        The tables are kept only as the cross-check that licenses it.
        """
        if self.ops is not None and self.policy == "sym":
            # the exact fold is of the SYMBOLIC rule (`nextWidth`); the tight policy has its own table below
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
        """The recursion depth minimising T (evaluated per prefix of the Lean ladder). NOT a valid
        `PhaseLoweringPlan` when it stops early (`signedBase` demands ¬(next < W)) — a hypothetical."""
        from qualtran.resource_counting import QECGatesCost, get_cost_value
        full = attrs.evolve(self, depth_limit=None).ladder()
        best, best_T = 0, None
        for d in range(len(full)):
            T = float(get_cost_value(attrs.evolve(self, depth_limit=d), QECGatesCost()).total_t_count(ts_per_rotation=ts_per_rotation))
            if best_T is None or T < best_T:
                best, best_T = d, T
        return best

    def total_czpow(self) -> int:
        """q^d · W_d² base-grid CZPows over the whole recursion."""
        lad = self.ladder()
        return (self.q ** (len(lad) - 1)) * lad[-1] ** 2

    def lean_resources(self) -> Dict[str, int]:
        """`LowGate.resources` for the WHOLE lowered recursion, predicted from the ladder.

        Exact, in Lean's own vocabulary, and verified channel-for-channel against emitted
        documents at k = 2..5 and depths 0..5 (`tests/test_rec_structural_cost.py`). Each term
        is one of ForShor's own definitions:

        * **leaves** -- the base case is a `Naive_SignedPhaseProd`, which v2 expands through
          `LowGate.CPhase` = (Phase, Phase, CNOT, Phase, CNOT). So each of the `W_d x W_d`
          operand-bit pairs costs 3 rz and 2 cnot, over `q^d` leaves.
        * **adders** -- `addScaledResources dst _src _negSrc _shift = cuccaroModAddResources
          (width dst)`, i.e. `{x: 2w-6, cnot: 5w-7, toffoli: 2w-3}`, and it ignores `negSrc`
          entirely. `sides * n_add` of them per node, at the CHILD width. `negateResources` is
          the same adder plus `w + 2` X, `sides * n_neg` per node.
        * **sign extension** -- `signExtend r n => {cnot := n}` (`zeroExtend` is free). One per
          side per node, on the top -- signed -- limb, by `W_child - topLimb(W_parent)`, and a
          matching `signDealloc`, which charges the same again.

        NOT angle-aware, deliberately. `resources` charges `rz := 1` per `Phase` whatever the
        angle, because that is all the proof needs; reclassifying those rz into Clifford / T /
        rotation is this project's accounting job and is done separately (D10). This method is
        the structural invariant the two sides can be tied on exactly; the refinement is
        reported beside it, not folded into it.
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
            # op (the standard ones) never reached this term; ForShor's template table does.
            negs = nodes * sides * self.n_neg
            res["toffoli"] += (adds + negs) * (2 * w2 - 3)
            res["cnot"] += (adds + negs) * (5 * w2 - 7)
            res["x"] += (adds + negs) * (2 * w2 - 6) + negs * (w2 + 2)
            # `phaseSplitLogicalWidth`: lower limbs get w//k, the top one absorbs the rest.
            top_limb = w - (self.k - 1) * (w // self.k)
            res["cnot"] += 2 * nodes * sides * (w2 - top_limb)
        return res

    def reserve_qubits(self):
        """Lean `RecursivePhaseWorkspace.reserveNeed ops w w` (x, z) via the exact E3 mirror (tier A)."""
        from ..mirrors.width import reserve_need
        from ..mirrors.schedule import raw_table
        ops = raw_table(self.k)[0]["ops"]
        return reserve_need(self.k, ops, self.w, self.w)

    @property
    def qubit_footprint(self) -> int:
        """2w operands + Lean reserve (x + z) + 1 And ancilla. For policy='tight' the reserve is still the Lean
        (symbolic-policy) number — an upper bound stated as such."""
        rx, rz = self.reserve_qubits()
        return 2 * self.w + rx + rz + 1

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        W2 = self.next_width()
        if W2 < self.w and (self.depth_limit is None or self.depth_limit > 0):
            child_limit = None if self.depth_limit is None else self.depth_limit - 1
            # SINGLE-k: the q = 2k-1 children are the SAME arity (attrs.evolve changes only w/depth_limit,
            # keeping k, q, n_add, n_neg, rule, general) — ForShor's standardSignedPhaseLoweringPlan uses one
            # arity throughout (Recursion.lean), and the QFT twiddle inherits exactly this object.
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
        """depth = 'lean' (recurse while the width shrinks — the proved plan) | 'optimal' (cost-minimising cutoff, hypothetical)."""
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        # `bundle_compat.schedule_census`, not the raw census: v2 reports `addScaled` as the
        # TOTAL and marks `negSrc` on each op, so the separate `addScaled_negSrc` count has to
        # be recovered from the ops. Defaulting it to 0 silently dropped every Negate from the
        # priced call graph -- measured at k=2 m=2 as 24 And against the IR's 48, because the
        # backend realises a negSrc add as Negate . Add . Negate.
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
    """A phase product between GENUINELY ASYMMETRIC operand widths (wx != wz) -- e.g. cmodmul's
    internal controlled phase products, which are always between a narrow control/carry register
    (n or n+1 bits) and the wide `work` register (n + extraBits(eta) bits).

    `LeanRecursivePhaseProduct` above is symmetric-only (`Register("x", QUInt(w)), Register("z",
    QUInt(w))`) -- built and validated against the standalone E7 phase product and the QFT twiddle,
    both of which are square or near-square (the QFT split differs by at most 1 bit). Using it for
    cmodmul by squaring at `max(wx, wz)` (the old `LeanCtrlModMul._pp` convention) is WRONG: ForShor's
    own recursion rule (`Shor.nextSignedWidth`, DefsCore.lean:304) genuinely depends on both widths via
    `phaseLimbWidth = min(wx/k, wz/k)` -- when one operand is much narrower than the other, splitting it
    buys nothing, `nextSignedWidth` does not shrink below `max(wx, wz)`, and `lowerLeaf` (LowerRec.lean)
    falls straight to ONE flat `Naive(C)SignedPhaseProd` leaf: a genuine `wx`-by-`wz` RECTANGULAR CZPow
    grid, not a `max(wx,wz)`-square one. Confirmed empirically against a real emission (k=2, n=6): the
    three internal phase products (widths (6,26), (7,26), (26,7)) are each exactly one flat leaf in the
    real circuit, while the old symmetric model predicted deep k=2 recursion (11,907 CZPow leaves vs the
    real 520) -- the dominant term in a ~16x And-count overcount (REV 1 FIX 2026-09-03).

    IF recursion DOES fire (both operands wide enough), every child is a UNIFORM, SYMMETRIC (W', W')
    phase product: Lean's `targetSignedLayoutState` (LowGateIR.lean:382) pads EVERY slot -- x and z
    alike -- to the single scalar `commonNeededWidth`, regardless of how asymmetric the parent was. So
    only the TOP level needs asymmetric handling; the recursion (if any) delegates to the already-proven
    `LeanRecursivePhaseProduct(w=W')` for everything below. `next_width` is computed LIVE (exact, not
    table-approximated) via `mirrors.width.next_width` -- a direct line-by-line mirror of
    `nextSignedWidth`/`scanNeededWidths`/`commonNeededWidth`, valid at any (wx, wz), not just the
    precomputed table's [k, w_max] range.
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
        """`max(wx, wz)` -- for consumers that only need a representative scale (reporting, footprint math)."""
        return max(self.wx, self.wz)

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.wx)), Register("z", QUInt(self.wz))])

    def next_width(self) -> int:
        """`nextSignedWidth x z ops` for THIS (wx, wz) pair -- the live, exact E3 mirror (not the
        symmetric-only precomputed table `LeanRecursivePhaseProduct.next_width` reads)."""
        assert self.ops is not None, "LeanAsymRecursivePhaseProduct needs `ops` (from from_bundle)"
        from ..mirrors.width import next_width as _next_width
        return _next_width(self.k, self.ops, self.wx, self.wz)

    def _child(self, w2: int) -> "LeanRecursivePhaseProduct":
        """The (proven-correct) symmetric child every recursing slot becomes, at width `w2`."""
        return LeanRecursivePhaseProduct(k=self.k, w=w2, q=self.q, n_add=self.n_add, n_neg=self.n_neg,
                                         n_add_negsrc=self.n_add_negsrc, policy=self.policy,
                                         rule=self.rule, general=self.general)

    def total_czpow(self) -> int:
        w2 = self.next_width()
        if w2 < max(self.wx, self.wz):
            return self.q * self._child(w2).total_czpow()
        return self.wx * self.wz            # flat leaf: a genuine rectangular grid, not w²

    def reserve_qubits(self):
        """Lean `RecursivePhaseWorkspace.reserveNeed ops wx wz` -- already asymmetric-native."""
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
        # `bundle_compat.schedule_census`, not the raw census: v2 reports `addScaled` as the
        # TOTAL and marks `negSrc` on each op, so the separate `addScaled_negSrc` count has to
        # be recovered from the ops. Defaulting it to 0 silently dropped every Negate from the
        # priced call graph -- measured at k=2 m=2 as 24 And against the IR's 48, because the
        # backend realises a negSrc add as Negate . Add . Negate.
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
    """The Lean-lowered recursive phase product at concrete input size n — the E7 object the swept
    ``LeanRecursivePhaseProduct(k, n, "sym")`` skeleton is tied to. Returns (bloq, ir_document).

    Unlike Phase A this needs NO new emitter: ``emit.lower_rec`` (E7 ``forshor_emit lower``) already
    prints the full recursive gate list as a ``shor.lowgate.ir/v1`` document. REAL-LEAN only — raises
    EmitError without the built binary (no mirror, by design). The same legacy backend used for the
    one-level phase product parses it, so priced and verified objects are compared on one footing."""
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
    """The signed integer a register denotes: LSB-first, top bit negative (two's complement)."""
    w = len(ids)
    return sum((-(1 << i) if i + 1 == w else (1 << i)) * bits[q] for i, q in enumerate(ids))


def rec_action_v2(flat_doc: Dict[str, Any], plan_doc: Dict[str, Any], sample: int = 256,
                  seed: int = 20260902, exhaustive_limit: int = 4096) -> Dict[str, Any]:
    """Exact action of a `forshor.lowgate/v2` signed phase product: `exp(i*phi*[[x]]*[[z]])`.

    The v1 checker (`ref_interp`) cannot read these documents -- it has no `Phase`/`CNOT`/
    `Toffoli` cases, and v2's flat circuit is made of nothing else. This is the same check by
    the same means as the v1 one, on the v2 vocabulary: every gate the lowering emits is
    basis-preserving, so `verify_gate_level.walk` follows a basis state through the circuit in
    O(gates) and returns the accumulated phase. That reaches widths a dense unitary cannot.

    Two things are asserted, and the second matters as much as the first: the accumulated phase
    equals `phi * [[x]] * [[z]]` with both operands read as SIGNED, and the basis state comes
    back unchanged -- the circuit must be diagonal. A construction that got the phase right
    while permuting the register would be wrong in a way a phase-only check cannot see.

    Operands come from the PLAN view's root node, which carries `x`/`z`/`phi`; the flat view
    does not (v1 carried them in `expected`).
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
    # `verify_gate_level`'s own repertoire (its module docstring, line 4): every one of these
    # maps a computational basis state to a phase times a basis state, which is what makes the
    # O(gates) walk valid. The first version of this list was copied from a depth-0 test and
    # omitted `mcx`/`cp`/`mcp`, so every RECURSING document -- the interesting ones -- reported
    # a spurious failure.
    allowed = {"x", "cx", "ccx", "mcx", "p", "cp", "mcp", "swap", "barrier", "id"}
    if not names <= allowed:
        return {"verdict": "FAIL", "reason": f"non-basis-preserving gates emitted: {names - allowed}"}

    wx, wz = len(x_ids), len(z_ids)
    # The operand space is 2^(wx+wz), which at wx = wz = 16 is 4.3e9 PAIRS. Enumerating it to
    # then sample from it is an instant MemoryError -- and exactly the widths worth checking are
    # the ones that trigger it. Draw the sample directly instead, and only materialise the
    # space when it is genuinely small enough to check exhaustively.
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
        # Reduce the target mod 2 in EXACT rational turns before touching a float. At n=21 the
        # raw product `phi*[[x]]*[[z]]` reaches ~1.2e10 radians, where float64's ~1e-16 relative
        # error is ~1e-6 absolute -- the whole tolerance. Reducing first keeps the value O(1),
        # so the comparison tests the circuit rather than the arithmetic used to check it.
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
    """Action check — the STRONG one: the exact-rational reference interpreter evaluates the emitted
    circuit's action on basis pairs (exhaustive when 2^(2w) ≤ exhaustive_limit, else `sample` random
    pairs) and asserts it equals exp(iφ·x·z) — the signed phase product semantics ForShor proves. This
    is a genuine action verification (PASS/FAIL), not the up-to-convention DFT check of Phase A, because
    the recursive object IS a signed phase product (`expected.kind = "signed_phase_product"`).

    Returns {"verdict": "PASS"|"FAIL"|"SKIP", ...}. SKIP only if the interpreter cannot be imported."""
    from .. import paths
    # v2 documents go to `rec_action_v2`: `ref_interp` has no Phase/CNOT/Toffoli cases and the
    # v2 flat circuit is made of nothing else, so it would SKIP on a KeyError and the strongest
    # check in the Phase-B gate would silently stop running.
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
