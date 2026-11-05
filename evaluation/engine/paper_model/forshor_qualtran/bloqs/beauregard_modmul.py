"""beauregard_modmul.py — the Beauregard (2003) controlled modular multiply, as a drop-in for the
KMY `LeanCtrlModMul` at the single substitution seam `LeanModExp.modmul()`.

STATUS: qualtran-dependent (wraps `skeletons/shor_graph/beauregard.py`); import/runtime path is a Mac
Run-All step (needs qualtran + the frozen Qualtran tree). Pure-Python precision is via
`py/precision_policy.k_max_for`. Authored 2026-09-08 per `docs/history/PLAN_METHOD_SUBSTITUTION.2026-09-08.md`
staging step 2. ACTION-VERIFIED 2026-09-11 (see `tests/test_beauregard_modmul_wrapper.py`):
`build_composite_bloq` gives this class ITS OWN decomposition (`adder="fourier"` only — a
passthrough to `self._cua()`, which shares this class's own `_a`/`_kmax`, not hand-picked args) so
it is exhaustively `tensor_contract()`-verified as the actual object substituted at
`LeanModExp.modmul()`, not just its `CUa` ingredient in isolation. `precision_policy.k_max_for` is
now enforced (not just advisory) when `k_max` is passed explicitly. `adder="ripple"` remains
cost-only (`RippleModAdd` has no decomposition) and fails loudly, not silently, if decomposition or
`.adjoint()` is attempted.

WHAT IT IS. `BeauregardCtrlModMul(n, N, delta)` presents the SAME interface `LeanModExp` reads off its
per-step multiplier — `w_work` (nominal work width, for the composite signature), `qubit_footprint`,
`build_call_graph`, `signature` — but its cost comes from the Beauregard branch: it wraps
`shor_graph.beauregard.CUa` (Fig 7, in-place controlled `|c⟩|x⟩ ↦ |c⟩|(a·x) mod N⟩`), which QECGatesCost
prices through its own decomposition (n modular QFT-adders sandwiched by a textbook QFT). This is the
only method-specific object; everything upstream (the QPE frame, tbits-fold, IQFT in
`LeanModExp`/`LeanOrderFinding`) is reused verbatim, so the swap is minimal.

PRECISION. Beauregard's AQFT truncation `k_max` is taken from the SAME budget δ that drives KMY's
η=δ/n²: `k_max = precision_policy.k_max_for(n, δ)`. Matched accuracy across methods.

MULTIPLIER a. `CUa`'s gate count is independent of the specific multiplier (n modular adders regardless
of the constant), matching `LeanCtrlModMul`'s "every step has this cost" convention, so a representative
coprime `a` is chosen (smallest a≥2 with gcd(a,N)=1). N must be concrete and > 1 (the caller passes
N=(1<<n)-1, as the KMY path does); `CUa` needs `a⁻¹ mod N`.

HONESTY. This is a textbook Qualtran construction (quant-ph/0205095), NOT Lean-certified — unlike the
KMY `LeanCtrlModMul`. A resource estimate that selects `method="beauregard"` is a comparison point, not
a certification. Action-verification (above) closes the "is this object even correct" question for
`adder="fourier"`; it does not and cannot make this Lean-certified — that distinction is unchanged.
"""
from __future__ import annotations

import math
from functools import cached_property
from math import gcd
from typing import Optional

import attrs

try:
    from qualtran import Bloq, QBit, QUInt, Register, Signature
    from qualtran.resource_counting import BloqCountDictT, SympySymbolAllocator
except Exception:  # pragma: no cover - qualtran absent (sandbox); real use is the target machine Run-All
    Bloq = object  # type: ignore

ADDERS = ("fourier", "ripple")


def _representative_coprime(N: int) -> int:
    """Smallest a ≥ 2 with gcd(a, N) = 1 (cost is a-independent; a⁻¹ must exist)."""
    a = 2
    while gcd(a, int(N)) != 1:
        a += 1
    return a


def _k_max_for(n: int, delta: float) -> int:
    """Beauregard AQFT cutoff from the shared budget δ (parity with precision_policy.k_max_for)."""
    try:
        import importlib
        import sys
        from pathlib import Path
        pydir = str(Path(__file__).resolve().parents[2] / "py")
        if pydir not in sys.path:
            sys.path.insert(0, pydir)
        return int(importlib.import_module("precision_policy").k_max_for(int(n), float(delta)))
    except Exception:
        eps = float(delta) / float(n) ** 2                 # δ/n²  (same formula, inlined fallback)
        return max(1, math.ceil(math.log2(float(n) / eps)))


def _cua_cls():
    """Locate `shor_graph.beauregard.CUa` (skeletons/ on sys.path, as paths.setup_legacy_sys_path does)."""
    import importlib
    import sys
    from pathlib import Path
    sk = str(Path(__file__).resolve().parents[2] / "skeletons")   # oneclick_pipeline/skeletons
    if sk not in sys.path:
        sys.path.insert(0, sk)
    return importlib.import_module("shor_graph.beauregard").CUa


@attrs.frozen
class BeauregardCtrlModMul(Bloq):
    """Beauregard controlled modular multiply, interface-compatible with `LeanCtrlModMul`."""
    n: int
    N: int = 0
    delta: float = 1e-2
    a: Optional[int] = None
    k_max: Optional[int] = None
    adder: str = "fourier"          # modular-adder role: "fourier" (Draper PhiAddMod via CUa; real
                                    # decomposition) | "ripple" (Toffoli RippleModAdd; cost-attested)
    allow_unsafe_k_max: bool = False   # deliberate stress-testing escape hatch, see __attrs_post_init__

    def __attrs_post_init__(self):
        if int(self.N) <= 1:
            raise ValueError(f"BeauregardCtrlModMul needs a concrete modulus N>1 (got {self.N}); "
                             "the KMY path uses N=(1<<n)-1 — pass the same.")
        if self.adder not in ADDERS:
            raise ValueError(f"BeauregardCtrlModMul adder={self.adder!r} unknown (fourier | ripple)")
        # 2026-09-11 audit finding: the "matched accuracy" claim between method="kmy" (η=δ/n²) and
        # method="beauregard" (k_max) depends on k_max always being precision_policy.k_max_for(n,
        # delta) -- but k_max was a free parameter with NO enforcement, so an explicit k_max outside
        # what the policy would certify (or a plain mistake) would silently produce a resource-
        # estimate row that LOOKS matched-accuracy (same PROVENANCE, same δ column) but isn't. Only
        # enforced HERE (the actual substitution-seam wrapper the comparability claim is about) —
        # deliberately NOT in CUa/PhiAdd/CMultMod themselves (skeletons/shor_graph/beauregard.py),
        # whose own tests (e.g. test_k_max_angle_rule_beats_index_rule) legitimately construct those
        # classes with deliberately-small k_max to demonstrate truncation behavior; that general-
        # purpose flexibility should not be lost.
        if self.k_max is not None and not self.allow_unsafe_k_max:
            required = _k_max_for(int(self.n), float(self.delta))
            if int(self.k_max) < required:
                raise ValueError(
                    f"BeauregardCtrlModMul k_max={self.k_max} is below precision_policy.k_max_for("
                    f"n={self.n}, delta={self.delta})={required} -- this would silently break the "
                    f"'matched accuracy' claim against method='kmy'. Pass k_max=None to derive it "
                    f"from delta (the production default), or allow_unsafe_k_max=True if this is "
                    f"deliberate stress-testing, not a resource estimate."
                )

    @cached_property
    def _a(self) -> int:
        return int(self.a) if self.a is not None else _representative_coprime(int(self.N))

    @cached_property
    def _kmax(self) -> int:
        return int(self.k_max) if self.k_max is not None else _k_max_for(int(self.n), float(self.delta))

    def _cua(self):
        CUa = _cua_cls()
        return CUa(a=self._a, mod=int(self.N), n=int(self.n), k_max=self._kmax)

    # --- interface LeanModExp reads (KMY-compatible) ------------------------------------------------
    @property
    def w_work(self) -> int:
        """Nominal 'work' width for LeanModExp's composite signature: the accumulator b(n+1) + flag f."""
        return int(self.n) + 2

    @property
    def qubit_footprint(self) -> int:
        """Fourier: CUa footprint ctrl(1)+x(n)+b(n+1)+f(1) = 2n+3 (Beauregard 2003). Ripple: 2n+2 — the
        cited Takahashi-Kunihiro 2006 / HRS 2017 Toffoli width (one fewer; no Fourier overflow qubit).
        The 2n+3 vs 2n+2 gap IS the cited Fourier-vs-ripple qubit distinction (baseline_reference
        QUBIT_MODELS)."""
        return 2 * int(self.n) + (3 if self.adder == "fourier" else 2)

    @cached_property
    def signature(self) -> "Signature":
        # Beauregard-native registers; the cost flows through build_call_graph, not these labels.
        return Signature([Register("ctrl", QBit()), Register("x", QUInt(int(self.n))),
                          Register("b", QUInt(int(self.n) + 1)), Register("f", QBit())])

    def build_composite_bloq(self, bb, **soqs):
        """2026-09-11: give the actual method_compare.py substitution-seam object a real
        decomposition, closing the audit finding that BeauregardCtrlModMul was cost-only (no
        build_composite_bloq anywhere), so it could never be tensor_contract()-verified as a
        composed bloq -- only its ingredients (CUa, called directly with literal args bypassing
        this class's own a-selection/k_max-threading/adder-role logic) were ever action-verified.

        "fourier": a pure passthrough to `self._cua()` -- CUa's signature (ctrl/x/b/f, same widths)
        is IDENTICAL to this class's own `signature`, so no register plumbing is needed; the point
        of this method is to route through THIS object's own `_a`/`_kmax` (which CUa called
        directly does not exercise), not to reimplement anything CUa already does.

        "ripple": raises DecomposeNotImplementedError -- RippleModAdd (bloqs/adders.py), which
        `build_call_graph` prices this branch through, is itself a cost-attested leaf with NO
        gate-level decomposition (see its own docstring); giving the ripple branch a real
        decomposition needs RippleModAdd to have one first, which is out of scope here.
        """
        if self.adder != "fourier":
            from qualtran import DecomposeNotImplementedError
            raise DecomposeNotImplementedError(
                f"{self}: adder='ripple' has no decomposition (RippleModAdd is cost-attested only, "
                "not Lean-verified and not gate-level-decomposable yet) -- only adder='fourier' "
                "(CUa) can be tensor_contract()-verified as a composed bloq today."
            )
        out = bb.add_d(self._cua(), ctrl=soqs["ctrl"], x=soqs["x"], b=soqs["b"], f=soqs["f"])
        return {"ctrl": out["ctrl"], "x": out["x"], "b": out["b"], "f": out["f"]}

    def build_call_graph(self, ssa: "SympySymbolAllocator") -> "BloqCountDictT":
        # ADDER-ROLE SUBSTITUTION POINT (a sub-block of the modular multiply):
        #   "fourier" → delegate to CUa; QECGatesCost recurses into its real QFT-adder decomposition
        #               (the PhiAddMod Fourier adder inside CMultMod).
        #   "ripple"  → the SAME controlled-modular-multiply structure with the QFT-basis adder replaced
        #               by the computational-basis RippleModAdd (Toffoli), and NO surrounding QFT — the
        #               defining ripple-vs-Fourier difference. One controlled modmul = 2n modular
        #               additions (n forward CMULT(a) + n reverse CMULT(a⁻¹)†) + n controlled-SWAPs
        #               (Beauregard eq. 3 in-place trick). Cost-attested; see bloqs/adders.py.
        if self.adder == "fourier":
            return {self._cua(): 1}
        from .adders import RippleModAdd
        from qualtran.bloqs.basic_gates import TwoBitCSwap
        n = int(self.n)
        return {RippleModAdd(n=n, mod=int(self.N), n_ctrl=2): 2 * n, TwoBitCSwap(): n}

    def __str__(self) -> str:
        return f"BeauregardCtrlModMul(n={self.n}, N={self.N}, adder={self.adder}, k_max={self._kmax})"
