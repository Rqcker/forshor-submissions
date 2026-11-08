"""A schoolbook (Toffoli-arithmetic) baseline built from stated components, not a bare constant.

WHY THIS REPLACES `py/baseline_reference.py`'s `15 n^2` FOR THE COMPOSED STAGES
--------------------------------------------------------------------------------
That module prices the schoolbook baseline from one constant,
`symbolic_cost.SCHOOLBOOK_PREFACTOR = 15`, commented only as "stated schoolbook modular-multiply
baseline". Audited 2026-09-23, three things are wrong with using it for the composed stages:

1. **It is the same number for two different objects.** `multiply_baseline_T(n)` and
   `modmul_baseline_T(n)` both return `15 n^2` -- `multiply_baseline_T(1000) ==
   modmul_baseline_T(1000)` is literally `True`. A plain multiply and an IN-PLACE CONTROLLED
   MODULAR multiply cannot cost the same: the latter contains the former twice over, plus a
   modular reduction, plus a control.

2. **It is below the cost of its own constituent additions.** This repo's `bloqs/adders.py`
   states the structure explicitly: one in-place controlled modular multiply is `2n` modular
   additions -- `n` forward in `CMULT(a)` and `n` reverse in `CMULT(a^-1)^dagger` (Beauregard
   eq. 3, HRS sec. 4). At n = 2048, 2n Cuccaro ripple additions alone are 6.71e7 T with NO
   modular reduction at all, against the 6.29e7 T the constant charges for the whole thing.

3. **Consequently it is 67-133x below this repo's own cited ripple-carry model** (0.75%-1.5% of
   `baseline_reference.t_ripple`), which is why the "schoolbook" line plotted *below* the KMY
   curves. That was not coarseness, it was an error of two orders of magnitude.

`15 n^2` IS a defensible number for what it was presumably derived for: a plain, non-modular,
not-in-place schoolbook multiply needs about `2 n^2` Toffolis (n additions into a 2n-bit
accumulator), i.e. `8 n^2` T at 4 T/Toffoli, and `15 n^2` is 1.9x that -- generous but sane. It is
kept here for `MULT` alone, relabelled for what it is.

THE COMPONENTS, EACH STATED
---------------------------
Everything below is a product of named, citable pieces, so a reader can disagree with one factor
rather than with an opaque constant:

    ripple adder            2n - 3 Toffoli      Cuccaro et al. 2004 (quant-ph/0410184); this is
                                                the same count Lean's `cuccaroModAddResources`
                                                declares and the repo already uses.
    modular addition        4 x ripple adder    Vedral-Barenco-Ekert 1996 (quant-ph/9511018):
                                                add; compare against N; conditional subtract;
                                                restore the comparison ancilla.
    in-place ctrl modmul    2n modular adds     n forward + n reverse (bloqs/adders.py's own
                                                statement of Beauregard eq. 3 / HRS sec. 4).
    T per Toffoli           4                   constants.TS_PER_TOFFOLI (measurement-assisted
                                                uncompute). Pass 7 for the exact unitary Toffoli.
    modular exponentiation  2n ctrl modmuls     tbits = 2n, the canonical Shor width both
                                                LeanModExp and LeanOrderFinding use.
    order-finding           modexp + IQFT(2n)   the H/X frame is Clifford; the read-out QFT is
                                                w(w-1)/2 controlled phases on w = 2n qubits.

This gives `64 n^2` T for an in-place controlled modular multiply and `128 n^3` T for modular
exponentiation, against the constant's `15 n^2` and `30 n^3`.

HOW IT COMPARES TO THE CITED HRS LINE, AND WHY BOTH ARE DRAWN
--------------------------------------------------------------
HRS's own count is `32 n^2 log2(n)` Toffoli-equivalents per controlled modular multiply, which at
n = 2048 is about 22x this model. The gap is not a contradiction: HRS price a specific optimised
construction whose operation count carries a genuine `log n`, while this is the textbook
schoolbook composition with no such factor. Drawing both is the point -- one is "what the plain
textbook construction costs", the other "what the best cited Toffoli construction costs" -- and
neither is the bare constant that was there before.

ROTATIONS
---------
Toffoli arithmetic synthesises no rotations, so every term above is eps-independent. The ONLY
rotations in this baseline are the read-out QFT ladder of order-finding, `w(w-1)/2` controlled
phases on `w = 2n`, and those are returned separately so the caller can charge them at the matched
tolerance rather than at a placeholder.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

__all__ = ["SchoolbookModel", "SCHOOLBOOK"]


@dataclass(frozen=True)
class SchoolbookModel:
    """Component-wise schoolbook (Toffoli-arithmetic) cost model."""

    t_per_toffoli: float = 4.0          # constants.TS_PER_TOFFOLI; 7 for the exact unitary Toffoli
    adders_per_modular_add: int = 4     # VBE: add, compare, conditional subtract, restore
    plain_multiply_prefactor: float = 15.0   # the legacy 15 n^2, kept for MULT only

    # ---------------------------------------------------------------- primitives
    def ripple_adder_toffoli(self, n: int) -> float:
        """Cuccaro ripple-carry adder on `n` bits: `2n - 3` Toffolis."""
        return max(1.0, 2.0 * int(n) - 3.0)

    def modular_add_toffoli(self, n: int) -> float:
        """One modular addition = `adders_per_modular_add` ripple adders (VBE)."""
        return self.adders_per_modular_add * self.ripple_adder_toffoli(n)

    # ---------------------------------------------------------------- stages
    def ctrl_modmul_toffoli(self, n: int) -> float:
        """IN-PLACE controlled modular multiply = `2n` modular additions.

        The factor 2 is the in-place structure and is the thing the old constant was missing:
        `n` modular additions realise `CMULT(a)` into an ancilla, a swap exchanges the registers,
        and `n` more realise `CMULT(a^-1)^dagger` to clear it (Beauregard eq. 3). Without the
        second half the result is not in place and is not comparable to `CmodMulInPlaceCore`,
        which is what the KMY side prices.
        """
        return 2.0 * int(n) * self.modular_add_toffoli(n)

    def ctrl_modmul_T(self, n: int) -> float:
        return self.t_per_toffoli * self.ctrl_modmul_toffoli(n)

    def multiply_T(self, n: int) -> float:
        """A PLAIN schoolbook multiply -- not modular, not in place. The legacy `15 n^2`."""
        return self.plain_multiply_prefactor * float(n) ** 2

    def modexp_T(self, n: int, tbits: Optional[int] = None) -> float:
        tb = 2 * int(n) if tbits is None else int(tbits)
        return tb * self.ctrl_modmul_T(n)

    def qft_rotations(self, w: int) -> float:
        """Exact QFT on `w` qubits: `w(w-1)/2` controlled phases (the Hadamards are Clifford)."""
        w = int(w)
        return w * (w - 1) / 2.0

    def orderfinding_T(self, n: int, tbits: Optional[int] = None) -> float:
        """Modexp plus the read-out QFT, the latter EXCLUDING its rotations (charged by the
        caller at the matched tolerance -- see `rotations`)."""
        return self.modexp_T(n, tbits)

    # ---------------------------------------------------------------- dispatch
    def T(self, stage: str, n: int, tbits: Optional[int] = None) -> float:
        """Rotation-free T for `stage`; rotations come from `rotations()`."""
        stage = stage.upper()
        if stage == "MULT":
            return self.multiply_T(n)
        if stage == "MODMUL":
            return self.ctrl_modmul_T(n)
        if stage == "MODEXP":
            return self.modexp_T(n, tbits)
        if stage == "SHOR":
            return self.orderfinding_T(n, tbits)
        raise ValueError(f"no schoolbook model for stage {stage!r}")

    def rotations(self, stage: str, n: int, tbits: Optional[int] = None) -> float:
        """Rotations in `stage`. Toffoli arithmetic has none; only order-finding's read-out QFT."""
        if stage.upper() != "SHOR":
            return 0.0
        tb = 2 * int(n) if tbits is None else int(tbits)
        return self.qft_rotations(tb)

    def describe(self, n: int) -> Dict[str, float]:
        return {
            "ripple_adder_toffoli": self.ripple_adder_toffoli(n),
            "modular_add_toffoli": self.modular_add_toffoli(n),
            "ctrl_modmul_toffoli": self.ctrl_modmul_toffoli(n),
            "ctrl_modmul_T": self.ctrl_modmul_T(n),
            "modexp_T": self.modexp_T(n),
            "shor_rotations": self.rotations("SHOR", n),
            "legacy_15n2": 15.0 * n * n,
            "ratio_to_legacy": self.ctrl_modmul_T(n) / (15.0 * n * n),
        }


#: the model used by the figures
SCHOOLBOOK = SchoolbookModel()
