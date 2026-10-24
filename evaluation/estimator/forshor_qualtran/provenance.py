"""Provenance: the source that supports each number. It is defined one time, and each artifact carries it.

A verdict is never ``PASS`` on mirror provenance. The mirror exists to let the Python side run
without a Lean toolchain. It does not exist to certify a result (ROADMAP standing discipline).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import List, Optional


class Provenance(str, enum.Enum):
    REAL_LEAN = "REAL-LEAN"               # output of the built ForShor executable
    REFERENCE_MIRROR = "REFERENCE-MIRROR"  # Python mirror; a differential check runs if the binary exists

    @property
    def certifies(self) -> bool:
        return self is Provenance.REAL_LEAN


class Tier(str, enum.Enum):
    """The type of evidence that supports a claim."""
    LEAN_EMITTED = "A: Lean-emitted"        # printed by a ForShor executable from a computable def
    LEAN_CITED = "B: Lean-proved, cited"    # a sorry-free theorem about an object that we realise but cannot print
    NUMERIC = "C: numerically validated"    # operator/state identity to ~1e-15 at small n
    EMPIRICAL = "D: empirically measured"   # Qualtran cost of a bloq; there is no Lean object
    MODEL = "E: model / heuristic"          # closed-form or physical-layer assumption


@dataclass(frozen=True)
class Claim:
    what: str
    tier: Tier
    cites: List[str] = field(default_factory=list)
    evidence: str = ""
    coverage: str = ""
    provenance: Optional[Provenance] = None

    def row(self) -> dict:
        return {"claim": self.what, "tier": self.tier.value, "cites": list(self.cites),
                "evidence": self.evidence, "coverage": self.coverage,
                "provenance": self.provenance.value if self.provenance else ""}


#: The standing tier table for the three interface objects. It is in one location only. The
#: estimate manifest and the notebook print exactly this table.
STANDING_CLAIMS: List[Claim] = [
    Claim("PhaseProduct schedule (k, ops, points, q, A_k)", Tier.LEAN_EMITTED,
          ["Shor.genOpsWithProduct_returns_to_original", "Shor.genOpsWithProduct_ProgConsumesPtsSafe",
           "Shor.lowerSignedPhaseProduct_correct"],
          "forshor_emit schedule == frozen ScheduleEmit tables (k≤7) == python mirror", "k = 2..10"),
    Claim("PhaseProduct weights c_l(b) ∈ Q[b] (n-free)", Tier.LEAN_EMITTED,
          ["Shor.gaussJordanInverseRows", "Shor.cramerCoeffFromPtsWidth"],
          "M·M⁻¹ = I exact; eval(c_l, 2^m)·φ == phase_emit k m φ byte-for-byte", "k = 2..10, m non-degenerate"),
    Claim("Width policies W_sym(n), W_tight(n) as affine tails", Tier.LEAN_EMITTED,
          ["(retired in the 2026-09-18 drop -- see ask L23)", "Shor.commonNeededWidth"],
          "tables from DefsCore nextWidth / LowGateIR reachableFits; python mirror differential", "m ≤ m_max"),
    Claim("QFT lowering rule (recursive PhaseProduct Cooley–Tukey)", Tier.LEAN_EMITTED,
          ["Shor.lowerQFT_correct", "Shor.eval_QFT_split"],
          "rule + split sizes + qftWorkspaceNeed emitted; the LowGate term itself is noncomputable", "w ≤ w_max"),
    Claim("KMY fast exact QFT, action-verified (Phase A / E4 — priced == lowerQFT IR)", Tier.LEAN_EMITTED,
          ["Shor.lowerQFT_correct", "Shor.eval_QFT_split"],
          "DONE 2026-09-02/03 (built on this machine, not just the target machine): `forshor_emit qft k w` emits the "
          "lowerQFT circuit; cli.step_verify_qft ties priced LeanQFT(core='recursive') to it — splits == E4 "
          "rule, And-count equal, operator == DFT up to global phase — PASS, REAL-LEAN, k=2..4 small w. "
          "Reserve-sizing fix: QftEmitLower.lean's reserve was a 1-bit-per-side stand-in too small for "
          "lowerLeaf's own recursion to ever fire; resized from RecursivePhaseWorkspace.reserveNeed and "
          "threaded so every level gets a Disjoint-checked slice. RECURSIVE-TWIDDLE GAP CLOSED (Phase A.1, "
          "2026-09-03): the twiddle is now emitted STANDALONE (`forshor_emit twiddle k left right phi`, "
          "TwiddleEmit.lean, operands.x/z + phase-product-shaped checks, no QFT-level special-casing needed) "
          "and priced by `QftRecursiveTwiddle` — one asymmetric RecursivePhaseWorkspace.nextWidth reduction "
          "at the root (asymmetry can only ever appear there — lowerLeaf's recursion shares one width across "
          "all q children at every deeper level), delegating to the existing square LeanRecursivePhaseProduct "
          "below. And-count EXACT MATCH against real Lean IR at every anchor spanning both regimes (k=2,3; "
          "base case through depth-4 recursion, symmetric and asymmetric) AND exact action PASS via "
          "ref_interp's basis-state interpreter (cost ~ gate count, not 2^w — reaches the recursion-firing "
          "regime a dense DFT tensor check cannot). Found+fixed along the way: LeanRecursivePhaseProduct.build_"
          "call_graph was missing the sides=2 'every op acts on x AND z' doubling LeanPhaseProduct already had "
          "(pre-existing Phase B gap — its own test never actually asserted And-count equality, only ladder/"
          "checks; now fixed and verified n=8,16 exact-match too). `qft_sweep`'s w-up-to-2048 curve now uses "
          "this corrected pricer throughout — evaluated further (k=2, no re-emission needed), the ratio "
          "T_lean_qft/T_ladder peaks ≈13.7x worse at w≈64 (recursion's per-level overhead dominates at "
          "reachable widths, as with the standalone recursive PhaseProduct) then declines monotonically, "
          "crossing below 1 — the KMY QFT genuinely beating the naive ladder — at w≈120,000: a real, honest, "
          "non-extrapolated result from the verified-exact pricer, not yet observed before this fix.",
          "twiddle: k=2,3 anchors spanning base case through depth-4 recursion, And-count exact + ref_interp "
          "action PASS; whole-QFT DFT action: k=2..4, w ≤ 5; crossover point k=2, w≈120,000 (cost-model, "
          "not independently action-verified at that scale)"),
    Claim("QFT reference ladder (H + CP)", Tier.NUMERIC,
          [], "operator == DFT up to global phase (qft_bridge); NOT the Lean lowerQFT term", "w ≤ 8"),
    Claim("Controlled modular multiply — cost-structure verified (Phase C, leg C1 — priced == lowerCmodMul IR)", Tier.LEAN_CITED,
          ["Shor.CmodMulInPlaceCore", "Shor.modMul_approx_valid_dist_uniform", "Shor.modExpApprox_correct",
           "Shor.IdealCtrlModMulExactSemantics", "Shor.Algorithm1Precision"],
          "DONE 2026-09-03 (built on this machine): `forshor_emit cmodmul k n c N eta` emits U1..U5 as a "
          "shor.lowgate.ir/v1 document; cli.step_verify_modmul ties the priced KMY LeanCtrlModMul(core='recursive') "
          "to it on And-count (C1). And-count EXACT MATCH against real Lean IR at k=2,n=6 (5116=5116) and k=3,n=8 "
          "(1892=1892); k=2,n=8 not independently re-confirmed at this tier (the IR-side verification tool's "
          "Python circuit compilation did not finish in tractable time at that scale — a known performance limit "
          "of that tool, not of the priced/estimation path, which computes in milliseconds regardless of n). "
          "Found+fixed along the way (all REV 1, 2026-09-03): (1) MMRegs's workspace accounting discarded the "
          "real next-free-qubit returned by every internal qftOn/cPhaseProd/phaseProd call and reused the padded "
          "total instead, so the real recursion either started past the declared qubit count or had zero reserve "
          "to grow into — fixed by threading the real next-free-qubit sequentially, mirroring QftEmitLower's own "
          "fix (Phase A.1); (2) the U3/U4 comparator Prim nodes embedded the modulus N as a literal qubit index "
          "alongside real qubit ids — parse_ir's touched-qubit check silently misread this whenever N was small "
          "relative to the circuit and hard-crashed once N exceeded it — fixed by moving the constant into the "
          "Prim tag string; qualtran_backend.py's Prim dispatch was ALSO a bare NotImplementedError stub (nothing "
          "had ever priced a real cmodmul circuit before this work), so the GreaterThanConstant / controlled "
          "AddK(-N) / LessThanConstant mapping the module's own docstring specified but never wired in was "
          "implemented; (3) ModMulEmitLower.lean's `extraBits` stand-in for Shor.algorithm1ExtraBits had two "
          "independent bugs (an extra factor of 2 on the ηden term, and an inexact floor-log2-of-a-floored-"
          "quotient heuristic) that together overcounted by 2 bits at η=1/1000 (20 instead of the correct 18, "
          "now matching Python's extra_bits exactly) — replaced with an exact integer least-power-of-2 search; "
          "(4) the DOMINANT bug (a ~16x And-count overcount, found before any of the above three): "
          "LeanCtrlModMul._pp priced EVERY internal phase product as a SYMMETRIC max(widths)-square recursive "
          "Toom-Cook object, but cmodmul's controlled phase products are genuinely asymmetric (a 6-7 bit "
          "control/carry register against a 24-26 bit work register) and ForShor's own recursion decision "
          "(nextSignedWidth, which depends on BOTH widths via phaseLimbWidth = min(wx/k, wz/k)) does not recurse "
          "at all for such lopsided pairs — confirmed by walking a real emission's circuit JSON directly: the "
          "three internal phase products are each exactly one flat rectangular leaf, not a deep recursive "
          "structure. Fixed with a new LeanAsymRecursivePhaseProduct pricer that computes the real recursion "
          "decision LIVE via mirrors.width.next_width (an exact E3 mirror, not the symmetric-only precomputed "
          "table) and — only if recursion genuinely fires — delegates to the existing, already-verified symmetric "
          "LeanRecursivePhaseProduct for the children below (uniformly padded to one common width, per Lean's "
          "own targetSignedLayoutState, LowGateIR.lean:382 — so only the top level needs asymmetric handling). "
          "LEG C2 (exact whole-circuit action) is still NOT verified: the proved bound is APPROXIMATE "
          "(modMul_approx_valid_dist_uniform, √(2Kη), K not exhibited) and IdealCtrlModMulExactSemantics is "
          "UNINSTANTIATED",
          "k=2,n=6 and k=3,n=8: And-count exact match (C1 cost-structure); k=2,n=8 untried at this scale; action = C2 open"),
    Claim("Modular exponentiation — priced == Lean modexp descriptor (Phase D)", Tier.LEAN_CITED,
          ["Shor.modExpIdealSteps", "Shor.modExpApprox_correct", "Shor.shorEta", "Shor.tbits", "Shor.Algorithm1Precision"],
          "DONE 2026-09-03 (built on this machine): `forshor_emit modexp k n a N tbits delta` emits a "
          "shor.modexp.ir/v1 descriptor FROM Shor.modExpIdealSteps — the step count tbits, the multiplier "
          "schedule (a^(2^e))%N, η = shorEta(δ,n)=δ/n², and one embedded lowerCmodMul step template. The "
          "composition structure is ForShor's, NOT Python's. cli.step_verify_modexp ties the priced LeanModExp "
          "to that descriptor: step count == Lean tbits, and And(priced) == And(tbits × Lean step template) "
          "(leg C1, against the Lean E5 object) — inherits the Phase C fix above (the embedded step template IS "
          "a lowerCmodMul document). And-count EXACT MATCH at k=2,n=6 (64416=64416); k=2/3,n=8 not independently "
          "re-confirmed at this tier for the same Python-compile-time reason as Phase C. REAL-LEAN ONLY — SKIP on "
          "the mirror, never PASS. ACTION is C2: modExpApprox_correct is APPROXIMATE (‖·‖ ≤ tbits·stepErr) and "
          "the semantics class is uninstantiated",
          "k=2,n=6: And-count exact match; tbits from Lean (ShorGateCountLayout: n..2n); action = C2 open"),
    Claim("Exact modular multiply (phaseproduct_modmult hybrid)", Tier.NUMERIC,
          [], "exact classical action + accumulate-core statevector ~2e-15; no Lean object", "n ≤ 5"),
    Claim("Recursive Toom-Cook PhaseProduct, arbitrary depth — EMITTED (E7 `forshor_emit lower`, LowerRec.lean)", Tier.LEAN_EMITTED,
          ["Shor.compileOpsToSignedGate", "Shor.compileOpsToSignedGate",
           "Shor.ReserveBudget.ofRequirements", "Shor.PhaseSplitLayout.ofBudget", "Shor.nextSignedWidth", "Shor.gaussJordanInverseRows"],
          "full lowered gate list through the LowGateIR path: bodies by the proved compiler, layouts by the plan's own functions, "
          "coefficients checked per level by coeffVerified; verified by ref_interp exact action + Qualtran; no mirror exists", "verification n (concrete)"),
    Claim("Recursive Toom-Cook PhaseProduct, arbitrary depth (Lean plan, symbolic widths)", Tier.LEAN_CITED,
          ["Shor.standardSignedPhaseLoweringPlan", "Shor.evalL_lowerGateRec_correct",
           "Shor.evalL_lowerSignedPhaseProd_of_plan", "Shor.lowerSignedPhaseProduct_correct",
           "Shor.RecursivePhaseWorkspace.reserveNeed"],
          "sorry-free structural induction over signedStep; skeleton (widths, multiplicities, reserve) emitted (E6); "
          "layouts/phases of the plan not printable; cost is Qualtran on the emitted skeleton", "any depth; w ≤ w_max emitted"),
    Claim("Recursive PhaseProduct swept skeleton == E7 IR at anchors (Phase B — priced == lowerRec)", Tier.LEAN_EMITTED,
          ["Shor.evalL_lowerGateRec_correct", "Shor.lowerSignedPhaseProduct_correct", "Shor.gaussJordanInverseRows"],
          "DONE 2026-09-03 (binary now built on this machine): the swept LeanRecursivePhaseProduct(k,n,'sym') "
          "matches the E7 lowerRec IR exactly at anchors — ladder equal, And-count EXACT (test_contracts.py's "
          "own assertion was previously `>0`, not equality; strengthened and now passes at n=8,16), ref_interp "
          "action = exp(iφ·x·z) PASS. Found+fixed along the way: build_call_graph was missing the sides=2 "
          "'every schedule op acts on x AND z' doubling of n_add/n_neg that LeanPhaseProduct already had — a "
          "real pre-existing gap the un-asserted `>0` check let through undetected. Between anchors the E6 "
          "cost model carries the curve (stated, not certified). No mirror by design",
          "k = 2..3, n ∈ {8,16,21,30} anchors, And-count exact; E6 model between"),
    Claim("Toom-Cook multiplication — priced == Lean multiply descriptor (Phase F — coeffs + recursion Lean-sourced)", Tier.LEAN_EMITTED,
          ["Shor.alg1Step2FourierMultiplier", "Shor.gaussJordanInverseRows", "Shor.interpCoeff_correct",
           "Shor.gaussJordanInverseRows", "Shor.lowerSignedPhaseProduct_correct", "Shor.lowerQFT_correct",
           "Shor.PhaseProductGateCountBound"],
          "PENDING BINARY: `forshor_emit multiply k n c w` emits a shor.multiply.ir/v1 descriptor FROM ForShor's "
          "alg1Step2FourierMultiplier — H_reg(out) ; PhaseProduct(2π·c/2^w) ; IQFT(out). The INTERPOLATION "
          "COEFFICIENTS are ForShor's (embedded Phase-B PhaseProduct: coeffsAt gated by Shor.Emit.coeffVerified per "
          "level; Shor.Emit.mkCoeffs_verified / Shor.interpCoeff_correct), the RECURSION is ForShor's (the proved "
          "compiler aCompileOpsToSignedGateFromLayoutState; MasterTheoremProof envelope O(n^{log_k(2k-1)})), and the "
          "IQFT is Phase A. cli.step_verify_multiply ties priced == IR on And-count. ACTION is EXACT (no C2): "
          "|x⟩|0⟩ ↦ |x⟩|c·x mod 2^w⟩ = lowerSignedPhaseProduct_correct ∘ lowerQFT_correct, witnessed numerically at "
          "small n. REAL-LEAN ONLY — SKIP on the mirror",
          "k=2..3, n≤6 (exact-action witness); coeffs+recursion from ForShor; O(n^{log_k(2k-1)})"),
    Claim("Order-finding / full Shor — priced == Lean orderFindingApprox descriptor (Phase E)", Tier.LEAN_CITED,
          ["Shor.orderFindingApprox", "Shor.shorOrderFindingGateCount", "Shor.ShorGateCountBound",
           "Shor.modExpApprox_correct", "Shor.shorEta"],
          "DONE 2026-09-03 (built on this machine): `forshor_emit orderfinding k n a N L delta` emits a "
          "shor.orderfinding.ir/v1 descriptor FROM Shor.orderFindingApprox — exponent width L = log2(2N²), the "
          "embedded Phase-D modexp descriptor (tbits = L), and the embedded Phase-A IQFT on width L; the QPE "
          "Hadamard frame and |1⟩ init are Clifford. The composition is ForShor's, NOT Python's. "
          "cli.step_verify_orderfinding ties the priced LeanOrderFinding to it: L == Lean L, And(priced) == "
          "And(Lean composed) (leg C1, via Phases D/A) — inherits the Phase C/D fixes above (the embedded modexp "
          "descriptor's step template IS a lowerCmodMul document). And-count EXACT MATCH at k=2,n=6 "
          "(64482=64482); k=2/3,n=8 not independently re-confirmed at this tier for the same Python-compile-time "
          "reason as Phase C/D. REAL-LEAN ONLY — SKIP on the mirror, never PASS. ACTION is C2: the circuit is "
          "APPROXIMATE (modExpApprox_correct) and the semantics class is uninstantiated. Replaces the former "
          "tier-E anchored order-finding model",
          "k=2,n=6: And-count exact match; L from Lean; O(n^{2+ε}) bound; action = C2 open"),
    Claim("Recursive Toom-Cook Qualtran cost (KMY skeleton bloqs)", Tier.EMPIRICAL,
          [], "QECGatesCost of KmyPhaseProductSignedRecursive; cache", "k ≤ 6, w ≤ 48"),
    Claim("Version-B / rule-R2 scaling of PP, QFT, Toom-Cook multiplier, CmodMulInPlaceCore (notebook 02, G1–G6)", Tier.MODEL,
          ["Shor.lowerQFT_correct", "Shor.CmodMulInPlaceCore", "(retired in the 2026-09-18 drop: the exact reachable-value width scan is not carried over -- reconstructed in forshor_qualtran/width_scan.py + tight_width.py, ask L23)", "Shor.genOpsWithProduct_returns_to_original"],
          "priced model over Lean-emitted inputs (E1 schedule, E3 tight widths, E4 QFT rule, E5 template): Toom-k at every level, "
          "zero-extended limbs, cost-optimal stop; Qualtran count == independent closed form at every check point; NOT a PhaseLoweringPlan "
          "ForShor proves (single k per plan; signedBase needs ¬(next<W))", "k = 2..10, n ≤ 2^17"),
]
