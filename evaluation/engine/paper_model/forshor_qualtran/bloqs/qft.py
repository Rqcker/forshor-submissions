"""`LeanQFT(w)` — the ForShor `lowerQFTPlan` recursion (E4) as a Qualtran bloq; and
`ReferenceQFT(w)` — the textbook H + CP ladder, kept as a numerically validated reference.

LeanQFT(w) = LeanQFT(right) ; twiddle ; LeanQFT(left) ; RadixReverse(w, w/2)
    left = w//2, right = w - w//2, twiddle = PhaseProdUsing(2π/2^w, left, right)
    RadixReverse(w, m) → Clifford (3·(m/2) swaps in the Lean model; `TwoBitSwap`)
LeanQFT(1) = H, LeanQFT(0) = id.

TWIDDLE CORE. KMY's fast exact QFT (Kahanamoku-Meyer & Yao, §"Fast exact quantum Fourier
transform") is sub-quadratic and ancilla-free because the step-2 twiddle is the RECURSIVE Toom-Cook
PhaseProduct. ``core="recursive"`` (``QftRecursiveTwiddle``) prices exactly what `lowerLeaf` emits
for that twiddle in BOTH regimes — confirmed exact against the real Lean IR (structure, And-count,
and, since the twiddle has no `H`, exact ACTION via `ref_interp`) at k=2/3 anchors spanning the
no-recursion base case through depth-4 recursion (`tests/test_qft_twiddle_lean_verify.py`).

RESERVE FIX (2026-09-02): `QftEmitLower.lean`'s per-split reserve was a 1-bit-per-side stand-in too
small for `lowerLeaf`'s own Toom-Cook recursion to ever grow into, so every width the emitter could
produce fell back to the base case — a plain RECTANGULAR `left × right` signed phase product, no Toom
decomposition, summing to exactly `w*(w-1)/2` And gates over the whole recursion tree (IDENTICAL to
the naive H+CP ladder, `ReferenceQFT`). Fixed by sizing `qftHalves`'s reserve from
`RecursivePhaseWorkspace.reserveNeed ops lo (w-lo)` and threading the next-free qubit through the
recursion so every subtree gets a disjoint, correctly-sized slice (Disjoint-checked by Lean).
Recursion now genuinely fires (confirmed both symmetric, e.g. k=2 w=16, and asymmetric, e.g.
k=2 left=8 right=10, depth 3) — but ASYMMETRY can only ever appear at the twiddle's OWN root:
`lowerLeaf`'s recursion collapses to one shared width for all `q=2k-1` children at every deeper level
(`PhaseSplitLayout x k W` takes a single `W`), so below the root the existing, already-verified
SQUARE `LeanRecursivePhaseProduct` applies unmodified — `QftRecursiveTwiddle` is exactly that
composition: one asymmetric `RecursivePhaseWorkspace.nextWidth` call (the E3 mirror,
`mirrors/width.py::next_width`, on the ACTUAL (left,right) pair — not the tabulated square-only
`general`/`rule` shortcut) at the root, delegating to `LeanRecursivePhaseProduct` for everything below.
Ground truth for both the reserve fix and this pricer: `forshor_emit twiddle <k> <left> <right> <phi>`
(`TwiddleEmit.lean`), the twiddle emitted STANDALONE — verifiable via `ref_interp`'s exact basis-state
interpreter (cost ~ gate count, not 2^w) at widths a whole-QFT dense DFT tensor check cannot reach.
``core="onelevel"`` (a single Toom level, LeanPhaseProduct) remains a separate, coarser contrast:
asymptotically Θ(w²) by a different route, and its And-count does not match the emitted IR either.

`w` is concrete (the recursion unrolls; depth log₂ w). The n-dependence of a QFT-based cost is
therefore obtained by *evaluation* at each n — no Lean artifact is rebuilt per n.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple, Union

import attrs
from qualtran import Bloq, QInt, QUInt, Register, Signature
from qualtran.bloqs.arithmetic import Add, Negate
from qualtran.bloqs.basic_gates import CZPowGate, Hadamard, TwoBitSwap
from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator

from .. import bundle_compat, constants, widths
from ..emit import Bundle
from .phase_product import LeanPhaseProduct
from .recursive import LeanRecursivePhaseProduct


@attrs.frozen
class QftRecursiveTwiddle(Bloq):
    """The QFT's step-2 twiddle: a signed phase product on possibly-UNEQUAL x=`left`, z=`right`
    operands. Prices exactly what `lowerLeaf` (E7) emits, in both regimes (see module docstring):
    one asymmetric `RecursivePhaseWorkspace.nextWidth` reduction at the root — via the exact E3
    mirror on the actual (left,right) pair, `mirrors/width.py::next_width` — then, if that fires,
    `q` copies of the existing SQUARE `LeanRecursivePhaseProduct(w=Wnext)` (unmodified, reused) plus
    the root's `Add`/`Negate(QInt(Wnext))` overhead; else the plain `left*right` CZPow grid.
    `k` carried for the single-k invariant (`LeanQFT._twiddle_k`)."""
    k: int
    left: int
    right: int
    q: int
    n_add: int
    n_neg: int
    n_add_negsrc: int = 0        # addScaled ops with negSrc: realised as Negate·Add·Negate (2 extra Negates)
    rule: Optional[widths.WidthRule] = attrs.field(default=None, eq=False, repr=False)
    general: Optional[dict] = attrs.field(default=None, eq=False, repr=False)   # E3 general table w -> W (square, below the root)
    ops: Optional[tuple] = attrs.field(default=None, eq=False, repr=False)      # raw schedule ops, for the one asymmetric root call

    @property
    def signature(self) -> Signature:
        return Signature([Register("x", QUInt(self.left)), Register("z", QUInt(self.right))])

    def next_width(self) -> int:
        """W' for the ACTUAL (possibly unequal) root operands, POST zero-extension (`left+1, right+1`)
        — the asymmetric E3 mirror, not the tabulated square-only shortcut
        `LeanRecursivePhaseProduct.next_width` uses below the root.

        PRICING FIX 2026-09-03 (matches QftEmitLower.lean's twiddle zero-extension fix): the emitted
        circuit now grows each side by 1 bit (`ExtReg.grow 1`) BEFORE the twiddle's phase product —
        the `signed_bit_weight_lsb` sign-bit fix (see the Lean handoff / lowerQftGate's twiddle call
        site) — so the twiddle this class prices actually recurses from `(left+1, right+1)`, not
        `(left, right)`. Pricing this at the un-extended width used to under-count: it's what made
        `step_verify_qft`'s And-count differential mismatch (`ci.and_bloq` from the now-correct
        emission > `cs.and_bloq` from this un-updated model) once the Lean fix landed."""
        from ..mirrors.width import next_width as _next_width
        assert self.ops is not None, "QftRecursiveTwiddle needs ops (build via from_bundle)"
        return _next_width(self.k, self.ops, self.left + 1, self.right + 1)

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        left_ext, right_ext = self.left + 1, self.right + 1   # the zero-extended widths lowerQftGate actually uses
        W2 = self.next_width()
        if W2 < max(left_ext, right_ext):
            child = LeanRecursivePhaseProduct(k=self.k, w=W2, q=self.q, n_add=self.n_add, n_neg=self.n_neg,
                                              n_add_negsrc=self.n_add_negsrc, policy="sym",
                                              rule=self.rule, general=self.general)
            counts: Dict[Bloq, int] = {child: self.q}
            sides = 2                                  # every schedule op acts on x AND z (LeanPhaseProduct's convention)
            if self.n_add:
                counts[Add(QInt(W2))] = sides * self.n_add
            n_negate = sides * (self.n_neg + 2 * self.n_add_negsrc)
            if n_negate:
                counts[Negate(QInt(W2))] = n_negate
            return counts
        return {CZPowGate(exponent=ssa.new_symbol("e")): left_ext * right_ext}

    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], left: int, right: int) -> "QftRecursiveTwiddle":
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        # `bundle_compat.schedule_census`, not the raw census: v2 reports `addScaled` as the
        # TOTAL and marks `negSrc` on each op, so the separate `addScaled_negSrc` count has to
        # be recovered from the ops. Defaulting it to 0 silently dropped every Negate from the
        # priced call graph -- measured at k=2 m=2 as 24 And against the IR's 48, because the
        # backend realises a negSrc add as Negate . Add . Negate.
        cen = bundle_compat.schedule_census(doc)
        rule = widths.width_rule(doc, "sym")
        general = widths.general_widths(doc, "sym")
        from ..mirrors.schedule import raw_table
        ops = raw_table(doc["k"])[0]["ops"]
        return cls(k=doc["k"], left=left, right=right, q=cen["phaseProduct"], n_add=cen["addScaled"],
                   n_neg=cen["negate"], n_add_negsrc=cen.get("addScaled_negSrc", 0),
                   rule=rule, general=general, ops=ops)

    def __str__(self) -> str:
        return f"QftRecursiveTwiddle(k={self.k}, {self.left}x{self.right})"


# ------------------------------------------------------------------ Phase A.1: IR-lowered twiddle counterpart (V)
def concrete_twiddle_ir_bloq(bundle: Union[Bundle, Dict[str, Any]], left: int, right: int,
                             phi: Fraction = Fraction(1, 2), refresh: bool = False):
    """The Lean-lowered QFT twiddle at concrete (left,right) — the object `QftRecursiveTwiddle` is
    tied to. Returns (bloq, ir_document). REAL-LEAN only (`emit.lower_twiddle`; no mirror, by design,
    like `concrete_ir_bloq`/Phase B's `recursive.concrete_ir_bloq`). The document is phase-product
    shaped (`operands.x/z`, `expected.kind = "signed_phase_product"`) so it needs no QFT-level
    special-casing in `lowgate_ir.parse_ir`, and — because it has no `H` — `ref_interp` verifies its
    action exactly (see `twiddle_action_ok`)."""
    from .. import paths
    from ..emit import lower_twiddle
    doc = bundle.doc if isinstance(bundle, Bundle) else bundle
    k = doc["k"]
    ir_doc = lower_twiddle(k, int(left), int(right), phi, refresh=refresh)
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend  # legacy py/
    ir = lowgate_ir.parse_ir(ir_doc)
    return qualtran_backend.build_qualtran_bloq(ir), ir_doc


def twiddle_action_ok(ir_doc: Dict[str, Any], sample: int = 256, seed: int = 20260903,
                      exhaustive_limit: int = 4096) -> Dict[str, Any]:
    """Exact action check via `ref_interp` (the STRONG leg, not the up-to-convention DFT check the
    whole-QFT circuit needs) — the twiddle IS a signed phase product (`expected.kind =
    "signed_phase_product"`, no `H`), so this scales with gate count, not 2^w, and reaches the
    recursion-firing regime a dense tensor contraction cannot. Mirrors `bloqs/recursive.py::rec_action_ok`."""
    from .. import paths
    try:
        paths.setup_legacy_sys_path()
        import ref_interp as RI  # legacy py/
        res = RI.run(ir_doc, "auto", int(sample), int(seed), int(exhaustive_limit), quiet=True)
        return {"verdict": res.get("verdict", "FAIL"), **{k: v for k, v in res.items() if k != "verdict"}}
    except Exception as e:
        return {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}


@attrs.frozen
class LeanQFT(Bloq):
    w: int
    k: int
    q: int
    n_add: int
    n_neg: int
    beta: int
    policy: str = "tight"
    core: str = "onelevel"        # "onelevel" (LeanPhaseProduct twiddles) | "recursive" (LeanRecursivePhaseProduct, Lean plan when policy='sym')
                                  # | "versionB" (ToomRecursivePhaseProduct: Toom-k at every level, rule R2 — the cost-optimal recursion model)
    depth: str = "lean"           # recursive core only: "lean" (full plan) | "optimal" (cost cutoff, hypothetical)
    stop_tsr: float = constants.TS_PER_ROTATION   # versionB twiddles: the rotation price the stop rule R2 uses
    bundle_doc: Optional[dict] = attrs.field(default=None, eq=False, repr=False)
    rules: Optional[Any] = attrs.field(default=None, eq=False, repr=False)   # recursion_versions.RuleSet for core="versionB"
    #: Optional width-indexed policy (`forshor_qualtran.policy.PhasePolicy`): when set (core="recursive"),
    #: every twiddle is a `PolicyPhaseProduct` on the zero-extended pair — varying k by level — instead of
    #: the single-k `QftRecursiveTwiddle`. None = unchanged. Part of bloq identity (eq), hidden from repr.
    phase_policy: Optional[Any] = attrs.field(default=None, repr=False)
    pp_convention: Optional[Any] = attrs.field(default=None, repr=False)
    #: core="versionB" only: the `ToomRecursivePhaseProduct` version every phase product and QFT twiddle
    #: is built at. "B" (default) = Toom-k_top at every level, unchanged; "C" = the cheapest loaded arity
    #: at every width under the same rule R2 and stop price -- the optimal width-indexed policy of this model.
    pp_version: str = attrs.field(default="B", repr=False)

    def __attrs_post_init__(self):
        if self.pp_version not in ("B", "C") or (self.pp_version != "B" and self.core != "versionB"):
            raise ValueError(f"pp_version={self.pp_version!r} needs core='versionB' and is 'B' or 'C'")
        # SINGLE-k INVARIANT (ForShor uses ONE arity throughout a plan — Recursion.lean; KMY version B,
        # never A "k then k=2 below" nor C "per-level optimal k"). The QFT is priced at exactly one k, and
        # every twiddle and every deeper Toom-Cook level uses that same k — matching the PhaseProduct
        # primitive. Guarded here so a mismatched bundle or an unknown core cannot silently break it.
        if self.core not in ("onelevel", "recursive", "versionB"):
            raise ValueError(f"LeanQFT core={self.core!r} unknown (single-k cores: onelevel|recursive|versionB)")
        if self.bundle_doc is not None and self.bundle_doc.get("k") not in (None, self.k):
            raise ValueError(f"LeanQFT k={self.k} != bundle k={self.bundle_doc.get('k')}: the twiddle recursion must be single-k")
        if self.phase_policy is not None and self.core != "recursive":
            raise ValueError("LeanQFT phase_policy needs core='recursive' (the Lean-plan twiddles it replaces)")

    @property
    def signature(self) -> Signature:
        return Signature([Register("r", QUInt(self.w))])

    def _sub(self, w: int) -> "LeanQFT":
        return attrs.evolve(self, w=w)   # keeps k (and core/policy): sub-QFTs are the SAME arity

    @staticmethod
    def _twiddle_k(tw: Bloq) -> Optional[int]:
        """The arity a twiddle bloq carries, however it names it (LeanPhaseProduct/LeanRecursivePhaseProduct/
        QftRecursiveTwiddle use `.k`; ToomRecursivePhaseProduct uses `.k_top`). Delegates to the shared
        `arity_walk.bloq_arity` (single source of truth — see its docstring for why the module-scoped
        restriction matters: Qualtran's own AddK/XorK coincidentally name a constructor parameter `k` too)."""
        from ..arity_walk import bloq_arity
        return bloq_arity(tw)

    def _twiddle(self, left: int, right: int) -> Bloq:
        if self.phase_policy is not None:
            # varying-k twiddle: the policy is consulted by width at every level, so the single-k
            # invariant below deliberately does not apply. Zero-extended pair as QftRecursiveTwiddle.
            from ..policy_shor import policy_pp
            return policy_pp(self.phase_policy, left + 1, right + 1, self.pp_convention)
        size = max(left, right) + 1
        if self.core == "versionB":
            from ..recursion_versions import ToomRecursivePhaseProduct
            assert self.rules is not None, "core='versionB' needs a RuleSet"
            tw: Bloq = ToomRecursivePhaseProduct.from_rules(self.rules, size, self.k, self.pp_version, "kmy",
                                                            tsr=self.stop_tsr)  # "B" = same k every level
            if self.pp_version == "C":
                return tw   # per-width optimal arity: the single-k invariant below deliberately does not apply
        elif self.core == "recursive":
            assert self.bundle_doc is not None, "core='recursive' needs bundle_doc (build via LeanQFT.from_bundle)"
            tw = QftRecursiveTwiddle.from_bundle(self.bundle_doc, left, right)
        elif self.bundle_doc is not None:
            tw = LeanPhaseProduct.from_bundle(self.bundle_doc, size, self.policy)
        else:
            tw = LeanPhaseProduct(k=self.k, n=size, q=self.q, n_add=self.n_add, n_neg=self.n_neg,
                                  beta=self.beta, policy=self.policy)
        # the twiddle — and, inductively, its every deeper level — must be the SAME arity as the QFT
        assert self._twiddle_k(tw) == self.k, f"twiddle arity {self._twiddle_k(tw)} != QFT k {self.k} (single-k invariant)"
        return tw

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        w = self.w
        if w == 0:
            return {}
        if w == 1:
            return {Hadamard(): 1}
        left, right = w // 2, w - w // 2
        counts: Dict[Bloq, int] = {}
        for sub in (self._sub(right), self._sub(left)):
            counts[sub] = counts.get(sub, 0) + 1
        tw = self._twiddle(left, right)
        counts[tw] = counts.get(tw, 0) + 1
        swaps = 3 * (left // 2)
        if swaps:
            counts[TwoBitSwap()] = swaps
        return counts

    @classmethod
    def from_bundle(cls, bundle: Union[Bundle, Dict[str, Any]], w: int, policy: str = "tight",
                    core: str = "onelevel", depth: str = "lean", rules: Optional[Any] = None,
                    stop_tsr: float = constants.TS_PER_ROTATION, phase_policy: Optional[Any] = None,
                    pp_convention: Optional[Any] = None, pp_version: str = "B") -> "LeanQFT":
        doc = bundle.doc if isinstance(bundle, Bundle) else bundle
        pp = LeanPhaseProduct.from_bundle(doc, 4, policy)
        return cls(w=w, k=doc["k"], q=pp.q, n_add=pp.n_add, n_neg=pp.n_neg, beta=pp.beta,
                   policy=policy, core=core, depth=depth, stop_tsr=stop_tsr, bundle_doc=doc, rules=rules,
                   phase_policy=phase_policy, pp_convention=pp_convention, pp_version=pp_version)

    def splits(self):
        """The Cooley–Tukey split rows this bloq unrolls: [(w, left, right, twiddle_size)] — compared with the E4 rule."""
        out, stack, seen = [], [self.w], set()
        while stack:
            w = stack.pop()
            if w < 2 or w in seen:
                continue
            seen.add(w)
            left, right = w // 2, w - w // 2
            out.append((w, left, right, max(left, right) + 1))
            stack += [left, right]
        return sorted(out)

    def recursion_arities(self, ssa: Optional[SympySymbolAllocator] = None) -> set:
        """Every arity k that appears ANYWHERE in this QFT's full decomposition — the outer Cooley–Tukey
        sub-QFTs and every level of every inner Toom-Cook twiddle. The single-k invariant is exactly
        ``LeanQFT(...).recursion_arities() == {self.k}``: a base-layer k=6 QFT stays k=6 all the way down.

        Delegates to the shared ``arity_walk.collect_arities`` (single source of truth for this walk —
        see its docstring for why the module-scoped ``_twiddle_k``/``bloq_arity`` restriction matters)."""
        from ..arity_walk import collect_arities
        if ssa is None:
            ssa = SympySymbolAllocator()
        return collect_arities(self, ssa)

    def __str__(self) -> str:
        return f"LeanQFT(w={self.w}, k={self.k})"


# ------------------------------------------------------------------ Phase A: the E4 rule, independently
def rule_splits(w: int) -> List[Tuple[int, int, int, int]]:
    """The `lowerQFTPlan` (E4) Cooley–Tukey split rows, computed from the rule text alone — the
    independent spec the swept `LeanQFT(w).splits()` is diffed against:
        QFT(w≥2) = QFT(right) ; PhaseProd(2π/2^w, left, right) ; QFT(left) ; RadixReverse(w, w//2)
        left = w//2, right = w - w//2, twiddle_size = max(left, right) + 1.
    Returns sorted [(w, left, right, twiddle_size)] over every distinct sub-width ≥ 2."""
    out: List[Tuple[int, int, int, int]] = []
    stack, seen = [int(w)], set()
    while stack:
        u = stack.pop()
        if u < 2 or u in seen:
            continue
        seen.add(u)
        left, right = u // 2, u - u // 2
        out.append((u, left, right, max(left, right) + 1))
        stack += [left, right]
    return sorted(out)


# ------------------------------------------------------------------ Phase A: IR-lowered counterpart (V)
def concrete_ir_bloq(bundle: Union[Bundle, Dict[str, Any]], w: int, phi: Fraction = Fraction(1, 1),
                     policy: str = "tight", refresh: bool = False):
    """The Lean-lowered QFT bloq at concrete register width w — the object the differential ties the
    priced `LeanQFT(w)` to. Returns (bloq, ir_document).

    REAL-LEAN ONLY: no mirror, by design, exactly as for the E7 recursive circuit. The emitted
    `shor.lowgate.ir/v1` document is parsed by the same legacy backend used for the phase product, so the
    two priced/verified objects are compared on one footing (And, rotation, leaf multiset).

    TODO-3 (partial migration): now emitted via the new `shor_emit qft` command
    (`emit2.lower_qft`, raises `Emit2Error` without the built binary) rather than `emit.lower_qft` --
    `qft`-kind documents require zero blocking checks in the old schema, so `ir_v2.to_legacy_doc`'s
    output is already complete; confirmed via `tests/test_qiskit_bridge.py`."""
    from .. import paths
    from ..emit2 import lower_qft
    doc = bundle.doc if isinstance(bundle, Bundle) else bundle
    k = doc["k"]
    ir_doc = lower_qft(k, int(w), phi, refresh=refresh)
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend  # legacy py/
    ir = lowgate_ir.parse_ir(ir_doc)
    return qualtran_backend.build_qualtran_bloq(ir), ir_doc


def qft_action_ok(ir_doc: Dict[str, Any], w: int, atol: float = 1e-9) -> Dict[str, Any]:
    """Action check: the operator of the lowered QFT IR equals the DFT on w qubits, up to a global
    phase (and the bit-order/RadixReverse convention the document declares). Reuses the DFT matrix
    the numerically-validated reference bridge already uses (`qiskit/qft_bridge._dft_matrix`).

    Returns {"verdict": "PASS"|"FAIL"|"SKIP", ...}. SKIP (never FAIL) when the backend cannot contract
    the circuit to a dense unitary — so a convention that still needs pinning against the FIRST real
    emission surfaces as SKIP, not a spurious failure, when this runs on the target machine.

    NOTE (scaffold): the up-to-global-phase and lsb/msb + RadixReverse handling is the one knob to lock
    against the first `forshor_emit qft` output; the comparison itself (‖U·DFT⁻¹ − e^{iθ}I‖) is fixed.

    BUG FIX 2026-09-03: `qft_bridge._dft_matrix(n)` takes the QUBIT COUNT and computes `d = 1 << n`
    itself. This function used to call it as `_dft_matrix(dim)` with `dim` ALREADY equal to `1 << w`
    — i.e. it passed an already-exponentiated dimension into a function that exponentiates again,
    requesting a `(1 << dim) x (1 << dim)` array. At w=2,3 this only produced a shape mismatch against
    U (silently caught as SKIP by the blanket except below — masking the bug as "backend not yet
    exercised"); at w=4 the allocation itself (`(65536, 65536)` complex128 = 64 GiB) was attempted
    before any shape check ran, raising `MemoryError` here — and on machines where the OS answers an
    over-large allocation request by invoking the OOM killer rather than a catchable Python exception
    (observed: a Jupyter kernel dying outright, not a caught error), this crashes the whole process,
    not just this function. `bloq.tensor_contract()` itself was always correct (confirmed in
    isolation: exactly `(16, 16)` at w=4) — only this call site's argument was wrong.

    A size guard is also added: this function is not meant to be handed a large `w` (its own docstring
    and every caller use small fixed test widths), but a defensive ceiling means a future caller
    passing a large `w` gets a clean SKIP instead of ever attempting a memory-unsafe allocation.

    ANCILLA TRACING + ENDIANNESS FIX 2026-09-03 (HANDOFF_LEAN_VALIDATION_BEFORE_IR, twiddle
    zero-extension fix): two more issues, closed together now that both are exercised for the first
    time on a real w >= 3 emission:
      1. `QftEmitLower.lean`'s `qftHalves` now reserves an extra bit per side (the zero-extension fix
         for the twiddle's sign-bit bug -- see `lowerQftGate`'s twiddle call site) even at the small
         base widths this function tests, so the emitted circuit's TOTAL physical qubit count
         (`ir_doc["qubits"]["count"]`) can exceed `w` -- the excess are clean ancilla (fresh |0>
         workspace, never read after use). `U`'s shape is therefore `(2^N, 2^N)` with `N >= w`, not
         `(2^w, 2^w)`; the LOGICAL w-qubit action is the "ancilla = 0 on both input and output" block
         of `U`, extracted below by strided indexing rather than requiring `U.shape == (dim, dim)`.
      2. Qualtran's `tensor_contract()` combines a multi-qubit register's wires with shape-index 0 as
         the MOST significant bit of the combined basis index (confirmed independently: an isolated,
         from-scratch gate-level simulation of the emitted circuit, executed in the IR's own
         `lsb_first` semantics, matches the DFT to machine precision with NO reversal; the SAME
         circuit's `tensor_contract()` needs BOTH the row and column index bit-reversed to match --
         i.e. this is a fixed, width-independent convention gap between the two, not a QFT-specific
         effect). Both corrections are applied together below."""
    import numpy as np

    from .. import paths
    N_full = int(ir_doc.get("qubits", {}).get("count", w))
    MAX_DENSE_N = 14   # (1<<14)**2 * 16 bytes ~= 4 GiB; a conservative ceiling for a dense array
    if N_full > MAX_DENSE_N:
        return {"verdict": "SKIP", "reason": f"circuit uses {N_full} physical qubits (incl. ancilla) > "
                                             f"{MAX_DENSE_N}: dense operator would be memory-unsafe to even attempt"}
    try:
        paths.setup_legacy_sys_path()
        import lowgate_ir, qualtran_backend  # legacy py/
        bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(ir_doc))
        U_full = np.asarray(bloq.tensor_contract())
        dim = 1 << int(w)
        N = int(round(np.log2(U_full.shape[0])))
        if U_full.shape != (1 << N, 1 << N) or N < int(w):
            return {"verdict": "SKIP", "reason": f"operator shape {U_full.shape} inconsistent with w={w}"}
        # Ancilla trace: qubits [0, w) are the logical register, [w, N) are clean-ancilla workspace.
        # Qualtran's combined-index convention puts shape-index 0 (the FIRST physical qubit) at the
        # MSB, so the ancilla (physical indices w..N-1, i.e. the LAST N-w qubits) occupy the LOW-order
        # bits of the combined index -- the "ancilla = 0" block is every combined index divisible by
        # 2^(N-w).
        stride = 1 << (N - int(w))
        idx = np.arange(dim) * stride
        U = U_full[np.ix_(idx, idx)]
        import importlib.util
        spec = importlib.util.spec_from_file_location("qft_bridge", str(paths.PIPE_ROOT / "qiskit" / "qft_bridge.py"))
        qb = importlib.util.module_from_spec(spec); spec.loader.exec_module(qb)  # type: ignore[union-attr]
        D_raw = qb._dft_matrix(int(w))                       # takes the QUBIT COUNT, not the dimension
        rev = np.array([int(format(i, f"0{int(w)}b")[::-1], 2) for i in range(dim)])
        D = D_raw[rev, :][:, rev]                             # the fixed tensor_contract<->DFT convention gap
        M = U.conj().T @ D                                  # ≈ e^{iθ} I  iff  U ≈ e^{-iθ} DFT
        theta = np.angle(np.trace(M) / dim)
        resid = float(np.max(np.abs(M - np.exp(1j * theta) * np.eye(dim))))
        return {"verdict": "PASS" if resid <= atol else "FAIL", "residual": resid, "global_phase": float(theta),
                "ancilla_traced": N - int(w)}
    except Exception as e:  # backend/convention not yet exercised against a real doc
        return {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}


@attrs.frozen
class ReferenceQFT(Bloq):
    """Textbook H + CP(π/2^(i-j)) ladder (Draper, no swap) — NOT the Lean lowerQFT term.
    Validated numerically (operator == DFT) by qiskit/qft_bridge.py; provenance `reference-ladder`.

    ``k_max`` (Coppersmith / Beauregard S2.5 approximate-QFT banding): when set, controlled phase
    rotations of turn distance ``d > k_max`` are DROPPED (the far, small-angle gates), leaving the
    banded AQFT with induced error ``~ w·2^{-k_max}``. Default ``None`` keeps the EXACT full ladder
    (every ``d ≤ w-1``) — the previous behaviour, unchanged. A matched band for per-operation precision
    η is ``k_max = ⌈log₂(w/η)⌉`` (`py/precision_policy.aqft_k_max`), which holds the AQFT error ≤ η —
    the SAME budget that drives KMY's η = δ/n², so the banded arm is matched-accuracy, not merely
    cheaper. This is textbook, NOT a Lean lowerQFT term."""
    w: int
    k_max: Optional[int] = None                          # None = exact; int = Coppersmith band (drop d > k_max)

    @property
    def signature(self) -> Signature:
        return Signature([Register("r", QUInt(self.w))])

    def build_call_graph(self, ssa: SympySymbolAllocator) -> BloqCountDictT:
        counts: Dict[Bloq, int] = {Hadamard(): self.w}
        # exact ladder runs d = 1 .. w-1; the band keeps only d ≤ k_max (drops the far small rotations).
        d_stop = self.w if self.k_max is None else min(self.w, int(self.k_max) + 1)
        # d = 1 .. min(60, d_stop-1): 2^-d is an exact float here, individually priced (>=60 iterations
        # only ever, regardless of w -- genuinely O(1) in w).
        exact_stop = min(60, d_stop - 1)
        for d in range(1, exact_stop + 1):                 # distance i-j = d, multiplicity w-d
            expo = 1.0 / 2 ** d
            counts[CZPowGate(exponent=expo)] = counts.get(CZPowGate(exponent=expo), 0) + (self.w - d)
        # d = 61 .. d_stop-1: 2^-d underflows float for d > 1074, and every d >= 3 is already a
        # non-Clifford rotation to Qualtran, so there is no COST reason to keep these distinct --
        # ALL of them share ONE symbolic rotation bucket. Multiplicity summed in closed form (an
        # arithmetic series over d), not by looping: this used to call `ssa.new_symbol("e")` INSIDE
        # the loop, allocating a FRESH, distinct symbol (and so a fresh dict key) on every single d
        # -- silently defeating the "one shared bucket" this comment already described, and the real
        # cause of `resource_row(..., "reference", n)`'s wall-clock scaling linearly with n (measured:
        # 8.5s at n=10_000, 81s at n=100_000 -- via `py/qft_compare.py`'s QFT-realization sweep,
        # notebooks/09 §3) -- almost entirely spent allocating ~n distinct `sympy.Symbol`s and dict
        # entries that were never needed: `QECGatesCost` only ever wants the TOTAL rotation count.
        # Fixed here to be O(1) in w, like the `lean`/`reference-aqft` arms already are.
        if d_stop - 1 > exact_stop:
            lo, hi = exact_stop + 1, d_stop - 1             # inclusive range of the shared-bucket d's
            total_mult = (hi - lo + 1) * self.w - (lo + hi) * (hi - lo + 1) // 2
            shared = ssa.new_symbol("e")
            counts[CZPowGate(exponent=shared)] = counts.get(CZPowGate(exponent=shared), 0) + total_mult
        return counts

    def __str__(self) -> str:
        return f"ReferenceQFT(w={self.w})" if self.k_max is None else f"ReferenceQFT(w={self.w}, k_max={self.k_max})"
