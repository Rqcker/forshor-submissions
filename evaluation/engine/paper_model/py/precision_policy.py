#!/usr/bin/env python3
"""precision_policy.py — the per-modular-multiplication precision η, as a toggle.

WHY. η is the per-modmul precision that fixes the KMY cmodmul work-register width via
`extra_bits(η) = ⌈2·log₂(2 + 1/(2η))⌉` (Lean `algorithm1ExtraBits`, mirrored in
`forshor_qualtran.bloqs.modmul.extra_bits`). Historically the estimates pinned a single
`η = 1e-12` everywhere (`nb_config.ETA`), which over-provisions massively at small n (78 extra
qubits per cmodmul regardless of n) and is an arbitrary pin even at RSA scale. Meanwhile the
COMPOSED bloqs (`LeanModExp`/`LeanOrderFinding`) already scale η with n via the Lean-defined
`shorEta(δ,n) = δ/n²`. This module makes that appropriate-precision rule a first-class, uniform
toggle so every stage uses the same principled η.

THE RULE (Lean, cited). `Shor.shorEta (δ n) = δ / n²` (`Implementation/GateCount/Shor_GateCount.lean:29`);
`δ` is the TOTAL order-finding error budget, split across the ~n² precision-bearing operations, so
the per-step precision is `δ/n²`. Consequences (proved region around `Shor_GateCount.lean:1228`,
`algorithm1ExtraBits_shorEta_eventually_linear`): the induced extra-bit width is eventually linear
in n and the whole gate count stays under `C·n^{2+ε}`. `shor_eta` here is the exact Python mirror
already in `forshor_qualtran.bloqs.modexp.shor_eta`; re-exported so the policy has one definition.

POLICIES.
  "appropriate"  η = shorEta(δ, n) = δ / n²          (default; scales with n; δ is the knob)
  "fixed"        η = fixed_eta                        (an n-independent pin; the legacy behaviour,
                                                       kept for continuity / worst-case comparison)

Both reduce to the SAME single η(n), so a caller can drive every stage from it: pass η to the
standalone `LeanCtrlModMul` (`eta=`), and pass `δ_eff = η·n²` to `LeanModExp`/`LeanOrderFinding`
(whose own `shorEta(δ_eff, n)` then reproduces exactly that η) — see `composed_delta`.

Pure-stdlib (math only); no qualtran, no Lean. Safe to import anywhere.
"""
from __future__ import annotations

import math

#: default total order-finding error budget for the "appropriate" policy.
DEFAULT_DELTA: float = 1e-2
#: the legacy pinned per-qubit precision (KMY's 2048-bit-target value); the "fixed" policy default.
FIXED_ETA_DEFAULT: float = 1e-12
POLICIES = ("appropriate", "fixed")


def shor_eta(delta: float, n: int) -> float:
    """`Shor.shorEta δ n = δ / n²` — the Lean-defined per-modular-multiplication precision
    (`Shor_GateCount.lean:29`; identical to `forshor_qualtran.bloqs.modexp.shor_eta`)."""
    return float(delta) / (float(n) ** 2)


def extra_bits(eta: float) -> int:
    """`Shor.algorithm1ExtraBits η = ⌈2·log₂(2 + 1/(2η))⌉` — the cmodmul work-register overhead
    induced by precision η (mirror of `forshor_qualtran.bloqs.modmul.extra_bits`)."""
    return math.ceil(2 * math.log2(2 + 1 / (2 * eta)))


def eta_for(n: int, *, policy: str = "appropriate", delta: float = DEFAULT_DELTA,
            fixed_eta: float = FIXED_ETA_DEFAULT) -> float:
    """The per-modmul precision η for register width `n` under `policy`.

      * "appropriate" → `shor_eta(delta, n) = delta / n²`  (n-scaling; principled from budget δ)
      * "fixed"       → `fixed_eta`                          (n-independent pin; legacy)
    """
    if policy == "appropriate":
        return shor_eta(delta, n)
    if policy == "fixed":
        return float(fixed_eta)
    raise ValueError(f"unknown precision policy {policy!r}; expected one of {POLICIES}")


def k_max_for(n: int, delta: float = DEFAULT_DELTA) -> int:
    """Beauregard AQFT truncation exponent for the SAME per-step budget as the appropriate policy.

    Beauregard S2.5 / Coppersmith drop rotations below turn magnitude `2^{-k_max}`; the induced error
    is `~ n·2^{-k_max}`, so `k_max ∈ O(log(n/ε_step))` suffices. Using the matched per-step precision
    `ε_step = shorEta(δ, n) = δ/n²` gives

        k_max_for(n, δ) = max(1, ⌈log₂(n / ε_step)⌉) = max(1, ⌈log₂(n³ / δ)⌉).

    This is the Beauregard-side image of the SAME budget δ that drives KMY's `η = δ/n²`, so the two
    methods run at matched accuracy. Pure math (parity with `skeletons/shor_graph/beauregard.default_k_max`
    evaluated at `eps = δ/n²`); kept here so `precision_policy` is the one precision source."""
    eps_step = shor_eta(delta, n)                       # δ / n²
    return max(1, math.ceil(math.log2(float(n) / eps_step)))


def aqft_k_max(w: int, eta: float) -> int:
    """Coppersmith approximate-QFT band for a width-`w` QFT held to per-operation precision `eta`.

    A QFT banded to keep only controlled rotations of turn distance `d ≤ k_max` has induced error
    `~ w · 2^{-k_max}` (Coppersmith 1994; Beauregard S2.5). Requiring that error `≤ eta` gives

        aqft_k_max(w, η) = max(1, ⌈log₂(w / η)⌉).

    This is the QFT-realization image of the SAME per-step budget that fixes KMY's `η = δ/n²`, so the
    banded reference QFT (`forshor_qualtran.bloqs.qft.ReferenceQFT(w, k_max=...)`) runs matched-accuracy
    against the KMY/Lean arms — the QFT analogue of `k_max_for` for the Beauregard modular multiply.
    (`k_max_for(n, δ)` is precisely `aqft_k_max(n, shor_eta(δ, n))`: the width-n QFT at the per-step η.)
    Pure math; the single source for the QFT precision band."""
    return max(1, math.ceil(math.log2(float(w) / float(eta))))


# -- rotation-synthesis budget (re-exported from forshor_qualtran.constants, the single definition site) --
def rotation_epsilon(delta: float, rotations: int) -> float:
    """Per-rotation synthesis accuracy eps = delta / R. See the BUDGET CONVENTION block in
    `forshor_qualtran/constants.py`: algorithmic error at delta through eta = delta/n^2, synthesis at its own
    delta over the R rotations, combined bound 2*delta."""
    from forshor_qualtran import constants
    return constants.rotation_epsilon(delta, rotations)


def gradient_bits(delta: float, rotations: int) -> int:
    from forshor_qualtran import constants
    return constants.gradient_bits(delta, rotations)


def synthesis_ts_per_rotation(delta: float, rotations: int) -> int:
    from forshor_qualtran import constants
    return constants.synthesis_ts_per_rotation(delta, rotations)


def composed_delta(n: int, *, policy: str = "appropriate", delta: float = DEFAULT_DELTA,
                   fixed_eta: float = FIXED_ETA_DEFAULT) -> float:
    """The `δ_eff` to hand `LeanModExp`/`LeanOrderFinding` so their internal `shorEta(δ_eff, n)`
    equals `eta_for(n, …)` — i.e. `δ_eff = η · n²`. For the "appropriate" policy this is just
    `delta` (since η = δ/n²); for "fixed" it is `fixed_eta · n²`. Lets one policy drive both the
    standalone modmul and the composed stages to the SAME η."""
    return eta_for(n, policy=policy, delta=delta, fixed_eta=fixed_eta) * (float(n) ** 2)


def extra_bits_for(n: int, **kw) -> int:
    """Convenience: the cmodmul work-register overhead at width `n` under the chosen policy."""
    return extra_bits(eta_for(n, **kw))


def describe(n: int, *, policy: str = "appropriate", delta: float = DEFAULT_DELTA,
             fixed_eta: float = FIXED_ETA_DEFAULT) -> dict:
    """One row of provenance for a resource-estimate manifest/table."""
    e = eta_for(n, policy=policy, delta=delta, fixed_eta=fixed_eta)
    return {"n": int(n), "policy": policy, "delta": (delta if policy == "appropriate" else None),
            "eta": e, "extra_bits": extra_bits(e),
            "note": ("shorEta=δ/n² (Lean-defined, n-scaling)" if policy == "appropriate"
                     else "fixed n-independent pin (legacy)")}


if __name__ == "__main__":
    print("Per-modmul precision η and work-register overhead (extra_bits):\n")
    print(f"{'n':>6} {'fixed η=1e-12':>14} {'appropriate δ/n² (δ=1e-2)':>28}")
    print(f"{'':>6} {'extra_bits':>14} {'η':>14} {'extra_bits':>10}")
    for n in (4, 5, 6, 8, 16, 64, 256, 1024, 2048, 4096):
        ap = eta_for(n, policy="appropriate")
        print(f"{n:>6} {extra_bits_for(n, policy='fixed'):>14} {ap:>14.2e} {extra_bits(ap):>10}")
    print("\nAppropriate precision grows only as ~4·log₂n + 2·log₂(1/δ) — n-scaling but slow;")
    print("at small n it collapses the 78-qubit pin (n=5 → 21), at n=2048 it is a principled 56.")
