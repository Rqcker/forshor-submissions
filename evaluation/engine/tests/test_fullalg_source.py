"""Copied from Lean-To-Qualtran-main_full/tests/test_policy_shor_price.py (package renamed to l2q_full; the
reference native document read from engine/fullalg/data). The QFT-vs-LeanQFT test needs a single-k host bundle
that sto_site does not ship and is skipped.

The native `shor` template priced under its policy (Stage 4b): census x site pricing == the flat IR.

`policy_shor_price.PolicyShorCore` is the census of `policy_shor_sym` at one instance, with every
phase-product / QFT site priced by the policy bloqs. Its Lean-faithful view must equal
`LowGate.resources` of the flat circuit `policy_shor_ir.instantiate_shor` builds from ForShor's native
IR, channel for channel, and its qubit count must equal the layout Lean allocates. Then the Qualtran
view is the same call graph with Qualtran callees, priced at eps = delta_rot / R_total.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import attrs
import pytest

import site_engine.fullalg  # noqa: F401  (puts engine/fullalg on sys.path before l2q_full is imported)
from l2q_full import cost_conventions as cc, ir_v1, policy as pol, policy_ir as pi
from l2q_full import policy_shor_ir as ps, policy_shor_price as pr, policy_shor_sym as sy

ROOT = Path(__file__).resolve().parents[1] / "fullalg"


@pytest.fixture(scope="module")
def nat():
    return pi.load_native(ROOT / "data" / "policy.forshor.json")


def _core(nat, N, m, spec=cc.QUALTRAN):
    return pr.PolicyShorCore.from_native(nat, N.bit_length(), sy.Convention.for_modulus(N, m), spec)


# ------------------------------------------------------------------ the meaning view == the flat IR
INSTANCES = [(15, 0, 2), (21, 1, 2), (32, 1, 3), (35, 2, 2), (64, 0, 5), (143, 1, 2)]


@pytest.mark.parametrize("N,m,a", INSTANCES)
def test_lean_resources_equal_the_instantiated_shor_circuit(nat, N, m, a):
    got = _core(nat, N, m).lean_resources()
    want = ir_v1.lowgate_resources(ps.instantiate_shor(nat, a, N, m))
    assert got == want


@pytest.mark.parametrize("N,m,a", INSTANCES)
def test_qubits_equal_lean_allocate_reference_layout(nat, N, m, a):
    lay = ps.reference_layout(nat.policy, N, m)
    assert _core(nat, N, m).layout_qubits() == lay.flag + 1


@pytest.mark.parametrize("w", [1, 2, 3, 9, 16, 25, 29, 41])
def test_policy_qft_lean_resources_equal_the_native_qft(nat, w):
    assert pr.qft_lean_resources(nat.policy, w) == ir_v1.lowgate_resources(pi.instantiate_qft(nat, w))


# ------------------------------------------------------------------ the cost view
def test_lean_faithful_rotation_free_t_is_four_per_lean_toffoli(nat):
    """Under LEAN_FAITHFUL every T outside a rotation is a Toffoli of ForShor's model (Cuccaro adders,
    CCPhase cells), so the two views agree on the Toffoli channel exactly."""
    for N, m, _ in INSTANCES:
        core = _core(nat, N, m, cc.LEAN_FAITHFUL)
        assert cc.split(core).base == 4 * core.lean_resources()["toffoli"]


@pytest.mark.parametrize("w", [2, 9, 25, 41, 77])
def test_policy_qft_prices_as_lean_qft_with_the_same_policy(nat, w):
    """Same construction as `LeanQFT(phase_policy=P)` (which needs a single-k host bundle): equal T."""
    if not (ROOT / "artifacts" / "lean" / "k2.forshor.json").is_file():
        pytest.skip("needs Lean-To-Qualtran's single-k host bundle (artifacts/lean/k2.forshor.json)")
    from l2q_full.bloqs.qft import LeanQFT
    host = json.loads((ROOT / "artifacts" / "lean" / "k2.forshor.json").read_text())
    for spec in (cc.QUALTRAN, cc.LEAN_FAITHFUL):
        ref = LeanQFT.from_bundle(host, w, "sym", core="recursive", phase_policy=nat.policy, pp_convention=spec)
        assert cc.split(pr.PolicyQFT(nat.policy, w, spec)) == cc.split(ref)


def test_sites_are_the_policy_bloqs(nat):
    core = _core(nat, 143, 1)
    from l2q_full.bloqs.policy_phase_product import CPolicyPhaseProduct, PolicyPhaseProduct
    kinds = {type(pr.site_bloq(nat.policy, s, cc.QUALTRAN)) for s, _ in core.sites}
    assert kinds == {PolicyPhaseProduct, CPolicyPhaseProduct, pr.PolicyQFT}


def test_census_is_policy_independent_only_the_sites_reprice(nat):
    """The template calls the dispatchers, so another policy changes the sites' prices, not the census."""
    D, _, _ = pol.load_policy_submission(ROOT / "data" / "dp2048.policy.json")
    a = _core(nat, 143, 1)
    b = attrs.evolve(a, policy=D)
    assert (a.h, a.x, a.cnot, a.add_scaled, a.negate, a.sites) == (b.h, b.x, b.cnot, b.add_scaled, b.negate, b.sites)


# ------------------------------------------------------------------ rotation pricing
@pytest.mark.parametrize("spec", [cc.QUALTRAN, cc.LEAN_FAITHFUL], ids=lambda s: s.name)
def test_price_is_ross_selinger_at_the_whole_run_budget(nat, spec):
    """T = T_base + R (3.075 log2(1/eps) + 0.86), eps = delta * rotation_fraction / R_total, R_total the
    whole run's rotation count (never a flat per-rotation charge). Checked from first principles."""
    core = pr.PolicyShorCore.from_native(nat, 2048, sy.Convention(extra_bits=299), spec)
    r = pr.price(core)
    assert r["converged"]
    R = r["rotations"]
    eps = spec.delta * spec.rotation_fraction / R
    assert math.isclose(r["eps_per_rotation"], eps, rel_tol=1e-12)
    assert math.isclose(r["T"], r["T_base"] + R * (3.075 * math.log2(1 / eps) + 0.86), rel_tol=1e-9)
    assert r["T_per_rotation"] > 11                                   # not the flat 11-T charge


def test_upper_binade_moduli_cost_more(nat):
    lo = pr.PolicyShorCore.from_native(nat, 2048, sy.Convention(extra_bits=299))
    hi = pr.PolicyShorCore.from_native(nat, 2048, sy.Convention(extra_bits=299, upper_binade=True))
    assert pr.price(hi)["T"] > pr.price(lo)["T"]
