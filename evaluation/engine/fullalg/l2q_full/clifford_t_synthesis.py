"""Clifford+T conversion of already-built Qiskit circuits (B1-B7), by TWO real methods.

This module runs actual synthesis -- it does not report a cost-model number. It exists because
`Shared/shor_stack/costmodel.py` explicitly documents that its `gridsynth` entry is a FITTED
FORMULA, not a circuit ("MODELS ARE NOT SYNTHESIS. No gridsynth is run and no Clifford+T circuit
is produced."), and because the phase-gradient ("phase accumulator") gadget that already exists
in this codebase (`Shared/shor_graph/_pg_gadget.py`, `kmy_phaseproduct_skeleton.py`'s
`phase_grad_bits`) has only ever been threaded through ONE Qualtran bloq's own recursion, never
applied generically to an already-assembled Qiskit circuit of unknown origin.

Every B1-B7 circuit built by `qiskit_bridge.build_showcase_circuits` uses exactly two
non-Clifford gate families (confirmed by inspecting their `count_ops()`): `cp` (controlled
phase -- every angle here is an exact dyadic turn `2*pi*c/2^s`, by construction of KMY's
PhaseProduct/QFT recursion) and `ccx` (Toffoli, no continuous parameter). Converting to
Clifford+T needs exactly two substitutions:

  * `cp(theta)` -> phase-gradient gadget (EXACT, zero approximation error). Same PHYSICS as
    `Shared/shor_graph/_pg_gadget.py::gadget_leaf` (And -> conditional add into the shared
    `PhaseGradientState` register -> And-adjoint) and Qualtran's own `ZPowConstViaPhaseGradient`
    -- but NOT that function verbatim: `ZPowConstViaPhaseGradient` bottoms out in Qualtran's
    `AddIntoPhaseGrad`, a `cirq.ArithmeticGate` with NO Qualtran-level decomposition (confirmed
    directly: `AddIntoPhaseGrad().decompose_bloq()` raises `DecomposeNotImplementedError`), and
    this codebase's walker (`qiskit/bloq_to_qiskit.py`) deliberately never uses Cirq interop --
    so it refuses that bloq (`WalkRefusal: ... no template and no decomposition`). This module's
    `_gadget_leaf_local`/`_add_const_into_phase_grad` implement the SAME "conditionally load the
    classical constant, add, unload" recipe `ZPowConstViaPhaseGradient`'s own docstring
    describes, but built entirely from primitives the walker already recurses through
    (`qualtran.bloqs.arithmetic.Add`, `Partition`, `CNOT`, `And`) -- verified independently
    against `CPhaseGate` to ~1e-15 for a range of dyadic turns (not assumed correct by analogy;
    see `tests/test_clifford_t_synthesis.py::test_gadget_matches_cphase_exactly`). The WHOLE
    circuit is rebuilt as one Qualtran composite bloq so the shared phase-gradient register
    threads through every site in the original gate order (prepared once by `PhaseGradientState`,
    uncomputed once by its adjoint at the end), then converted to Qiskit through THIS
    codebase's existing walker (`qiskit_walker.walk`) -- the same walker every other circuit in
    the pipeline goes through.
  * `ccx` -> the standard 7-T Toffoli circuit (Clifford hierarchy level 3; no approximation is
    possible or needed regardless of method). Verified against `Operator(CCXGate())`.

For any `cp` whose angle is NOT (to float precision) an exact dyadic turn -- none are expected
from this codebase's circuits, and none were found when this was run against B1-B7, but the
check is real, not assumed -- the site is reported under `non_dyadic_sites` rather than silently
forced through the gadget; `gridsynth_clifford_t_for_rz` is the fallback for such a site (and is
also run standalone in the notebook purely to check the fitted cost formula in
`Shared/shor_stack/costmodel.py` against a real synthesized T-count, via `pygridsynth`
(github project `pygridsynth`, the actual Ross-Selinger algorithm) -- a comparison that
module's own docstring says has never been done).
"""
from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import simcost

__all__ = [
    "toffoli_clifford_t_template", "decompose_all_toffolis",
    "dyadic_resolution", "to_clifford_t_via_phase_gradient",
    "cphase_to_rz_cnot_angles", "gridsynth_clifford_t_for_rz", "gridsynth_demo_synthesis",
    "full_clifford_t_conversion", "gridsynth_only_conversion", "verify_unitary_equivalence",
]

def _add_const_into_phase_grad(bb, ctrl, const: int, s: int, pg, b: int):
    """`phase_grad`'s top-`s` bits += `(ctrl ? const : 0)` mod `2^s`, via the standard
    load-conditionally / add / unload-conditionally recipe (the same one
    `ZPowConstViaPhaseGradient`'s own docstring describes), built only from primitives THIS
    codebase's walker already recurses through (`Add`, `Partition`, `CNOT`) -- see module
    docstring for why `ZPowConstViaPhaseGradient` itself cannot be walked directly.

    `const` is MSB-first over `s` bits (matches Qualtran's big-endian `QUInt` convention, the
    same one `bb.split`/`bb.join` use elsewhere in this codebase, e.g.
    `kmy_phaseproduct_skeleton.py::join_mixed_registers`).
    """
    import numpy as np
    from qualtran import QUInt, Register
    from qualtran.bloqs.arithmetic import Add
    from qualtran.bloqs.basic_gates import CNOT
    from qualtran.bloqs.bookkeeping import Partition

    def _toggle(temp_bits):
        for k in range(s):
            if (const >> (s - 1 - k)) & 1:
                yield k

    temp = bb.allocate(s)
    temp_bits = list(bb.split(temp))
    for k in _toggle(temp_bits):
        ctrl, temp_bits[k] = bb.add(CNOT(), ctrl=ctrl, target=temp_bits[k])
    temp = bb.join(np.array(temp_bits), dtype=QUInt(s))

    if s == b:
        temp, pg = bb.add(Add(QUInt(s), QUInt(b)), a=temp, b=pg)
    else:
        part = Partition(b, (Register("hi", QUInt(s)), Register("lo", QUInt(b - s))))
        hi, lo = bb.add(part, x=pg)
        temp, hi = bb.add(Add(QUInt(s), QUInt(s)), a=temp, b=hi)
        pg = bb.add(part.adjoint(), hi=hi, lo=lo)

    temp_bits = list(bb.split(temp))
    for k in _toggle(temp_bits):
        ctrl, temp_bits[k] = bb.add(CNOT(), ctrl=ctrl, target=temp_bits[k])
    temp = bb.join(np.array(temp_bits), dtype=QUInt(s))
    bb.free(temp)
    return ctrl, pg


def _gadget_leaf_local(bb, qx, qy, e_frac: Fraction, pg, b: int):
    """Apply `e^{i*pi*e_frac}` on `(qx AND qy)` via the shared `b`-bit phase-gradient register.
    Same structure as `Shared/shor_graph/_pg_gadget.py::gadget_leaf` (And -> conditional add ->
    And-adjoint), but the conditional-add step is `_add_const_into_phase_grad` (walker-safe)
    instead of `ZPowConstViaPhaseGradient` (not walkable here -- see module docstring)."""
    import numpy as np
    from qualtran.bloqs.mcmt import And

    half = (e_frac / 2) % 1
    den = half.denominator
    dyadic = (den & (den - 1)) == 0
    s = den.bit_length() - 1
    if not dyadic or s > b:
        # T1 (2026-09-11): a turn finer than the register is ROUNDED to b bits (policy §3: error
        # <= 2^-(b+1) per site, the `gradient_rounding` category), not refused. A constant that rounds to
        # zero is a no-op and is elided by the caller (`_scan_cp_sites`, leaf_drop_bits) or here.
        # T3 (2026-09-13): a NON-DYADIC turn (U1 / U5 sites, denominator N) takes the same rounding path --
        # previously it was truncated to `bit_length(den) - 1` bits, a latent bug never reached from
        # `to_clifford_t_via_phase_gradient` (which routes non-dyadic sites to gridsynth) but wrong for any
        # direct caller and for the `rounded_gadget` leaf contract (`leaf_contracts.classify_cell`).
        s = b
        const = int(round(half * (1 << b))) % (1 << b)
        if const == 0:
            return qx, qy, pg
    else:
        assert s >= 1, f"identity site reached the gadget (s={s})"
        const = int(half * (1 << s))
    (qx, qy), anc = bb.add(And(), ctrl=np.array([qx, qy]))
    anc, pg = _add_const_into_phase_grad(bb, anc, const, s, pg, b)
    qx, qy = bb.add(And().adjoint(), ctrl=np.array([qx, qy]), target=anc)
    return qx, qy, pg


# ======================================================================================
# Toffoli -> Clifford+T (exact, standard 7-T circuit; no approximation possible or needed)
# ======================================================================================

def toffoli_clifford_t_template():
    """The standard exact 7-T Toffoli circuit (Nielsen & Chuang / Selinger 2013), on qubits
    (0, 1, 2) = (control_a, control_b, target). Verified against `Operator(CCXGate())` in
    `tests/test_clifford_t_synthesis.py::test_toffoli_template_matches_ccx`."""
    from qiskit import QuantumCircuit

    qc = QuantumCircuit(3, name="ccx_to_cliffordT")
    a, b, t = 0, 1, 2
    qc.h(t)
    qc.cx(b, t)
    qc.tdg(t)
    qc.cx(a, t)
    qc.t(t)
    qc.cx(b, t)
    qc.tdg(t)
    qc.cx(a, t)
    qc.t(b)
    qc.t(t)
    qc.h(t)
    qc.cx(a, b)
    qc.t(a)
    qc.tdg(b)
    qc.cx(a, b)
    return qc


def decompose_all_toffolis(qc):
    """Return a new circuit with every `ccx` replaced by `toffoli_clifford_t_template`,
    everything else copied through unchanged."""
    from qiskit import QuantumCircuit

    template = toffoli_clifford_t_template()
    out = QuantumCircuit(*qc.qregs, *qc.cregs, name=qc.name)
    for instr in qc.data:
        op = instr.operation
        if op.name == "ccx":
            out.compose(template, qubits=list(instr.qubits), inplace=True)
        else:
            out.append(op, instr.qubits, instr.clbits)
    return out


# ======================================================================================
# cp(theta) -> phase-gradient gadget (exact, for a dyadic turn)
# ======================================================================================

def dyadic_resolution(theta: float, *, max_bits: int = 32, atol: float = 1e-9) -> Optional[int]:
    """The exact resolution `s` of a `cp(theta)` angle, i.e. `theta == 2*pi*c/2^s` (c odd) to
    within `atol`, or None if no such `s <= max_bits` reproduces `theta` that closely. `s == 0`
    means `theta` is an integer multiple of 2*pi (identity).

    `theta` here always originates from an exact `Fraction` turn upstream (KMY's `PhaseProduct`,
    the QFT twiddle ladder) that only became a float when the walker built the Qiskit `cp` gate
    -- so recovering the exact turn from the float is expected to succeed at every site in this
    codebase's circuits, and IS checked rather than assumed: a genuinely non-dyadic angle
    returns None, routing the caller to `gridsynth_clifford_t_for_rz` instead of silently
    mis-firing the gadget on a constant it cannot represent exactly.
    """
    turn = (theta / (2 * math.pi)) % 1.0
    frac = Fraction(turn).limit_denominator(1 << max_bits)
    d = frac.denominator
    if d == 1:
        return 0
    if d & (d - 1) != 0:  # not a power of two
        return None
    if abs(float(frac) - turn) > atol:
        return None
    return d.bit_length() - 1


def _scan_cp_sites(qc, *, max_bits: int, atol: float, leaf_drop_bits: Optional[int] = None):
    """Return (cp_sites, non_dyadic_sites, dropped_sites) in circuit order.

    `leaf_drop_bits = b` is the `leaf_drop_rule = rounded_constant_zero` toggle (registry §B): a `cp` whose
    centred turn satisfies |turn| < 2^-(b+1) would add the constant 0 to a `b`-bit gradient register, so
    it is elided (reported in `dropped_sites`, each an identity up to operator norm 2*pi*2^-(b+1) -- the
    gradient's own rounding error). `None` keeps the historical behaviour (only exact identities skipped).

    `cp_sites`: list of (data_index, qubit_pair_indices, theta, resolution, e_frac). `e_frac` is
    the exact `Fraction` CZPow-style exponent `2*turn` (mod 2) that `gadget_leaf` expects
    (matching `kmy_phaseproduct_skeleton.py::_czpow_exponent`'s convention: `cp(theta)` has the
    SAME action as `CZPowGate(exponent=theta/pi)`, so `e_frac = theta/pi` as an exact Fraction).
    `non_dyadic_sites`: list of (data_index, qubit_pair_indices, theta) that failed the check.
    """
    cp_sites = []
    non_dyadic_sites = []
    dropped_sites = []
    for idx, instr in enumerate(qc.data):
        op = instr.operation
        if op.name not in ("cp", "cphase"):
            continue
        theta = float(op.params[0])
        qidx = tuple(qc.find_bit(q).index for q in instr.qubits)
        if leaf_drop_bits is not None:
            turn_c = (theta / (2 * math.pi)) % 1.0
            turn_c = turn_c - 1.0 if turn_c > 0.5 else turn_c
            if abs(turn_c) < 2.0 ** -(leaf_drop_bits + 1):
                dropped_sites.append((idx, qidx, theta))
                continue
        s = dyadic_resolution(theta, max_bits=max_bits, atol=atol)
        if s is None:
            non_dyadic_sites.append((idx, qidx, theta))
            continue
        if s == 0:
            continue  # exactly identity; nothing to convert
        turn = (theta / (2 * math.pi)) % 1.0
        e_frac = Fraction(2 * Fraction(turn).limit_denominator(1 << max_bits)) % 2
        cp_sites.append((idx, qidx, theta, s, e_frac))
    return cp_sites, non_dyadic_sites, dropped_sites


def to_clifford_t_via_phase_gradient(qc, *, phase_grad_bits: Optional[int] = None,
                                      max_bits: int = 32, atol: float = 1e-9,
                                      leaf_drop_bits: Optional[int] = None
                                      ) -> Tuple[Any, Dict[str, Any]]:
    """Rebuild `qc` as one Qualtran composite bloq with every `cp` routed through the shared
    phase-gradient gadget, then convert to Qiskit via this codebase's own walker. Every other
    gate (`h`, `x`, `cx`, `swap`, `ccx`) is mirrored onto the SAME qubit positions unchanged
    (Toffoli decomposition is a separate, later pass -- `decompose_all_toffolis` -- since it
    needs no gradient register and mixing the two rewrites in one pass would only make this
    harder to verify).

    Returns `(new_qc, report)`. `new_qc` has `qc`'s original qubits (same order, same indices)
    plus `b` extra qubits appended for the shared phase-gradient register. `report`:
    `phase_grad_bits`, `sites_converted`, `non_dyadic_sites` (qubits, theta) for any `cp` this
    could not resolve as exactly dyadic -- never silently dropped or forced through the gadget.
    """
    import numpy as np
    from qualtran import BloqBuilder, QBit, Register, Signature
    from qualtran.bloqs.basic_gates import Hadamard, XGate, CNOT, Toffoli, TwoBitSwap
    from qualtran.bloqs.rotations.phase_gradient import PhaseGradientState

    from . import qiskit_walker as W

    cp_sites, non_dyadic_sites, dropped_sites = _scan_cp_sites(qc, max_bits=max_bits, atol=atol,
                                                               leaf_drop_bits=leaf_drop_bits)
    cp_by_index = {idx: (qidx, s, e_frac) for idx, qidx, theta, s, e_frac in cp_sites}
    dropped_index = {idx for idx, _q, _t in dropped_sites}

    b = phase_grad_bits if phase_grad_bits is not None else max((s for _, s, _ in cp_by_index.values()), default=1)
    b = max(int(b), 1)

    n = qc.num_qubits
    sig = Signature([Register("q", QBit(), shape=(n,))])
    bb, initial = BloqBuilder.from_signature(sig)
    q = list(initial["q"])
    pg = bb.add(PhaseGradientState(bitsize=b))

    sites_rounded = 0
    for idx, instr in enumerate(qc.data):
        op = instr.operation
        qidx = tuple(qc.find_bit(qq).index for qq in instr.qubits)
        if op.name in ("cp", "cphase"):
            if idx in dropped_index:
                continue                    # leaf_drop_rule: rounds to a zero gradient constant
            if idx in cp_by_index:
                _, s, e_frac = cp_by_index[idx]
                if s > b:
                    sites_rounded += 1      # T1: finer than the register -> b-bit rounding inside the gadget
                i, j = qidx
                q[i], q[j], pg = _gadget_leaf_local(bb, q[i], q[j], e_frac, pg, b)
            else:
                theta = float(op.params[0])
                s_check = dyadic_resolution(theta, max_bits=max_bits, atol=atol)
                if s_check != 0:
                    # non-dyadic: NOT converted here (this codebase's own composed circuits DO
                    # contain a small number of these -- e.g. an N-th-root-of-unity phase found
                    # in the cmodmul circuit at N=3, not covered by the dyadic-turn theory the
                    # gadget relies on). Apply the gate UNCHANGED via CZPowGate (which this
                    # codebase's own walker templates directly back to `cp`/`p`) so the rebuilt
                    # circuit stays a FAITHFUL copy of the original rather than silently
                    # dropping the gate -- `full_clifford_t_conversion` splices these through
                    # gridsynth in a later pass instead.
                    from qualtran.bloqs.basic_gates import CZPowGate
                    i, j = qidx
                    q[i], q[j] = bb.add(CZPowGate(exponent=theta / math.pi), q=np.array([q[i], q[j]]))
                # else: s_check == 0, exactly the identity; correctly omitted.
            continue
        if op.name in ("p", "phase"):
            # An UNCONTROLLED single-qubit phase gate — never emitted by this codebase's own KMY/
            # Lean circuits (their arithmetic phases are always CONTROLLED), but Beauregard's
            # `PhiAdd` legitimately emits one for its uncontrolled merged rotations (found while
            # running Beauregard circuits through this converter for the first time). Diagonal on
            # ONE qubit, so it is the exact single-qubit analogue of the non-dyadic `cp` case just
            # above: apply it UNCHANGED via `ZPowGate` (which this codebase's own walker templates
            # back to `p`) rather than attempting a dyadic-exact Z/S/T shortcut here — the SAME
            # later `_splice_gridsynth_for_p_gates` pass `full_clifford_t_conversion` already runs
            # for the gradient-register's own prep rotations handles this uniformly via real
            # `pygridsynth`, dyadic or not.
            (i,) = qidx
            from qualtran.bloqs.basic_gates import ZPowGate as _ZPowGate
            theta = float(op.params[0])
            q[i] = bb.add(_ZPowGate(exponent=theta / math.pi), q=q[i])
            continue
        if op.name == "h":
            (i,) = qidx
            q[i] = bb.add(Hadamard(), q=q[i])
        elif op.name == "x":
            (i,) = qidx
            q[i] = bb.add(XGate(), q=q[i])
        elif op.name == "cx":
            i, j = qidx
            q[i], q[j] = bb.add(CNOT(), ctrl=q[i], target=q[j])
        elif op.name == "swap":
            i, j = qidx
            q[i], q[j] = bb.add(TwoBitSwap(), x=q[i], y=q[j])
        elif op.name == "ccx":
            i, j, k = qidx
            ctrl, q[k] = bb.add(Toffoli(), ctrl=np.array([q[i], q[j]]), target=q[k])
            q[i], q[j] = ctrl[0], ctrl[1]
        else:
            raise ValueError(f"unhandled gate {op.name!r} at data index {idx}; "
                              f"this circuit was expected to contain only h/x/cx/swap/ccx/cp "
                              f"(see module docstring's gate-count inventory)")

    bb.add(PhaseGradientState(bitsize=b).adjoint(), phase_grad=pg)
    cbloq = bb.finalize(q=np.array(q))

    new_qc, _layout, _ancillas = W.walk(cbloq)
    report = {
        "phase_grad_bits": b,
        "sites_converted": len(cp_by_index),
        "non_dyadic_sites": [{"qubits": qidx, "theta": theta} for _, qidx, theta in non_dyadic_sites],
        "sites_dropped": len(dropped_sites),
        "sites_rounded": sites_rounded,
        "leaf_drop_bits": leaf_drop_bits,
        "original_qubits": n,
        "new_qubits": new_qc.num_qubits,
    }
    return new_qc, report


# ======================================================================================
# gridsynth (real Ross-Selinger synthesis, via the `pygridsynth` dependency)
# ======================================================================================

def cphase_to_rz_cnot_angles(theta: float) -> Tuple[float, float]:
    """`CPhase(theta)` == `(Rz(theta/2) tensor Rz(theta/2))` conjugated by two CNOTs with a
    `Rz(-theta/2)` on the target in between -- the standard controlled-phase-to-Rz identity.
    Returns `(rz_outer, rz_inner)` = `(theta/2, -theta/2)`; the caller applies `Rz(rz_outer)` on
    both qubits, `cx`, `Rz(rz_inner)` on the target, `cx`. Verified against `Operator(CPhaseGate)`
    in `tests/test_clifford_t_synthesis.py::test_cphase_rz_cnot_identity`.
    """
    return theta / 2.0, -theta / 2.0


#: (theta, eps) -> (circuit, gate_string, t_count). pygridsynth is deterministic for a given angle and
#: accuracy, and callers only ever `compose` the returned 1-qubit circuit (never mutate it), so memoising
#: is exact. A composed order-finding circuit repeats each of its few hundred distinct angles thousands
#: of times (one per site); without this the conversion is dominated by redundant syntheses (2026-09-10).
_GRIDSYNTH_MEMO: Dict[Tuple[float, float], Tuple[Any, str, int]] = {}


def gridsynth_clifford_t_for_rz(theta: float, eps: float):
    """Synthesize `Rz(theta)` to Clifford+T at accuracy `eps` via the REAL `pygridsynth`
    (Ross-Selinger), returning `(qiskit_circuit_on_1_qubit, gate_string, t_count)`. This is an
    actual synthesized sequence, not the `Shared/shor_stack/costmodel.py` fitted formula.
    Memoised on (theta, eps); see `_GRIDSYNTH_MEMO`."""
    key = (float(theta), float(eps))
    hit = _GRIDSYNTH_MEMO.get(key)
    if hit is not None:
        return hit
    import pygridsynth as gs
    from qiskit import QuantumCircuit

    gate_string = gs.gridsynth_gates(theta, eps)
    qc = QuantumCircuit(1, name=f"gridsynth_rz_eps{eps:g}")
    for ch in gate_string:
        if ch == "T":
            qc.t(0)
        elif ch == "S":
            qc.s(0)
        elif ch == "H":
            qc.h(0)
        elif ch == "X":
            qc.x(0)
        elif ch == "W":
            qc.global_phase += math.pi / 4  # W = global phase e^{i*pi/4}, per gridsynth's own convention
        else:
            raise ValueError(f"unrecognized gridsynth gate letter {ch!r} in {gate_string!r}")
    t_count = gate_string.count("T")
    # `qc` synthesizes Rz(theta) = e^{-i*theta/2} * Phase(theta) (verified directly against
    # `RZGate(theta).to_matrix()` -- see tests/test_clifford_t_synthesis.py). Callers replacing
    # a `p(theta)` gate with this circuit must add `theta/2` to `global_phase` to stay exact
    # (harmless physically either way -- a scalar on the whole state -- but this codebase's
    # walker tracks global phase exactly rather than only up to it).
    _GRIDSYNTH_MEMO[key] = (qc, gate_string, t_count)
    return qc, gate_string, t_count


def gridsynth_demo_synthesis(eps_ladder: Sequence[float] = (1e-2, 1e-4, 1e-6, 1e-8, 1e-10)) -> List[Dict[str, Any]]:
    """Run real `pygridsynth` at a ladder of representative Shor rotation angles/epsilons and
    compare the ACTUAL T-count against `Shared/shor_stack/costmodel.py`'s fitted gridsynth
    formula (`3.02*log2(1/eps) + 1.77`) -- the comparison that module's docstring says has never
    been made ("MODELS ARE NOT SYNTHESIS. No gridsynth is run."). Uses a handful of angles drawn
    from this codebase's own dyadic phases (pi/4, pi/8, pi/16) plus one deliberately non-dyadic
    angle (pi/7) to show the method is not restricted to Shor's own turns.
    """
    import sys
    # `shor_stack.costmodel` lives OUTSIDE this repository in the working layout
    # (`Cryptography/Shared`), which is computed from `parents[3]` rather than searched -- so a
    # clone elsewhere could never find it and this function raised ImportError. An in-repo
    # `vendor/Shared` is therefore probed first, making the repository standalone; the original
    # sibling location stays as the fallback so the working tree is unaffected.
    #
    # The vendored copy is inserted at the FRONT only when it exists, because a local
    # `skeletons/shor_stack` package may already be bound in `sys.modules` from an earlier import
    # -- that package has no `costmodel`, so order matters.
    _here = Path(__file__).resolve()
    _vendored = _here.parents[1] / "vendor" / "Shared"
    for _p in (_vendored, _here.parents[3] / "Shared"):
        if (_p / "shor_stack").is_dir():
            sys.path.insert(0, str(_p))
            break
    # `costmodel`'s gridsynth law loads its coefficients from the qrest registry
    # (`_toolkit/params/cost_laws.yaml`), a sibling of `Cryptography`, not always on PYTHONPATH.
    sys.path.insert(0, str(_here.parents[4] / "_toolkit"))
    sys.modules.pop("shor_stack", None)      # drop a skeletons/ binding, if any
    from shor_stack.costmodel import SynthesisModel, SynthesisUnavailable

    angles = {"pi/4 (dyadic, s=2)": math.pi / 4, "pi/8 (dyadic, s=3)": math.pi / 8,
              "pi/16 (dyadic, s=4)": math.pi / 16, "pi/7 (non-dyadic)": math.pi / 7}
    rows: List[Dict[str, Any]] = []
    for name, theta in angles.items():
        for eps in eps_ladder:
            _qc, gate_string, t_count = gridsynth_clifford_t_for_rz(theta, eps)
            try:
                model_t = SynthesisModel("gridsynth", eps).t_per_rotation()
                ratio = t_count / model_t if model_t else float("nan")
            except SynthesisUnavailable:
                model_t = None
                ratio = None
            rows.append({"angle": name, "theta": theta, "eps": eps,
                         "actual_T": t_count, "model_T_fitted_formula": model_t,
                         "ratio_actual_over_model": ratio,
                         "gate_string_len": len(gate_string)})
    return rows


# ======================================================================================
# top-level entry + verification
# ======================================================================================

def _splice_gridsynth_for_cp_gates(qc, eps: float) -> Tuple[Any, Dict[str, Any]]:
    """Replace every remaining `cp(theta)` (controlled phase left un-gadgeted because `theta`
    was not exactly dyadic) with the standard `Rz(theta/2) / CNOT / Rz(-theta/2) / CNOT`
    identity, each `Rz` synthesized by real `pygridsynth` (`cphase_to_rz_cnot_angles`, verified
    against `Operator(CPhaseGate)` in `tests/test_clifford_t_synthesis.py`). This path exists
    because it is genuinely exercised: this codebase's own composed `cmodmul` circuit contains
    an N-th-root-of-unity phase (`theta = 2*pi/3` at N=3, not a dyadic turn) that the
    phase-accumulator method cannot handle -- found directly by running this conversion, not
    assumed absent."""
    from qiskit import QuantumCircuit

    out = QuantumCircuit(*qc.qregs, *qc.cregs, name=qc.name)
    out.global_phase = qc.global_phase
    sites = 0
    total_t = 0
    for instr in qc.data:
        op = instr.operation
        if op.name in ("cp", "cphase"):
            theta = float(op.params[0])
            rz_outer, rz_inner = cphase_to_rz_cnot_angles(theta)
            outer_qc, _og, outer_t = gridsynth_clifford_t_for_rz(rz_outer, eps)
            inner_qc, _ig, inner_t = gridsynth_clifford_t_for_rz(rz_inner, eps)
            i, j = instr.qubits
            out.compose(outer_qc, qubits=[i], inplace=True)
            out.compose(outer_qc, qubits=[j], inplace=True)
            out.cx(i, j)
            out.compose(inner_qc, qubits=[j], inplace=True)
            out.cx(i, j)
            sites += 1
            total_t += 2 * outer_t + inner_t
        else:
            out.append(op, instr.qubits, instr.clbits)
    return out, {"cp_sites_synthesized": sites, "gridsynth_t_added_cp": total_t}


def _splice_gridsynth_for_p_gates(qc, eps: float) -> Tuple[Any, Dict[str, Any]]:
    """Replace every remaining `p(theta)` (single-qubit phase gate -- NOT `cp`) with a real
    `pygridsynth`-synthesized Clifford+T sequence. These only ever originate from
    `PhaseGradientState`'s own one-time state-prep ladder (`PhaseGradientUnitary`'s
    `Z**(exponent/2**i)` rungs) -- the phase-gradient gadget converts every ARITHMETIC rotation
    to Toffolis, but preparing/unpreparing the shared gradient register itself still costs `b`
    single-qubit rotations each time (confirmed directly: a 2-qubit gadget test circuit walked
    through this codebase's own walker came back with `p` gates, traced to exactly this -- see
    the codebase's own prior note that "the two survivors are the gradient-state preparation
    itself", `forshor_qualtran/ext.py`). This is the genuine, motivated place `gridsynth` earns
    its role alongside the phase-accumulator method: the (many) arithmetic rotations are handled
    exactly and for free by the gadget; the (few, `O(gradient_bits)`) fixed state-prep rotations
    are what real synthesis is for.
    """
    from qiskit import QuantumCircuit

    out = QuantumCircuit(*qc.qregs, *qc.cregs, name=qc.name)
    out.global_phase = qc.global_phase
    p_sites = 0
    total_t = 0
    for instr in qc.data:
        op = instr.operation
        if op.name in ("p", "u1"):
            theta = float(op.params[0])
            rz_qc, _gate_string, t_count = gridsynth_clifford_t_for_rz(theta, eps)
            out.compose(rz_qc, qubits=list(instr.qubits), inplace=True)
            out.global_phase += theta / 2.0  # Rz(theta) = e^{-i*theta/2} * Phase(theta); see
            # gridsynth_clifford_t_for_rz's docstring for the derivation of this correction.
            p_sites += 1
            total_t += t_count
        else:
            out.append(op, instr.qubits, instr.clbits)
    return out, {"p_sites_synthesized": p_sites, "gridsynth_eps": eps, "gridsynth_t_added": total_t}


def budget_eps(qc, delta: float) -> Tuple[float, int]:
    """(eps, sites): the per-rotation gridsynth accuracy for `qc` under the pipeline's BUDGET CONVENTION
    (`constants.rotation_epsilon`): the synthesis budget delta spread evenly over every rotation-bearing
    gate in the circuit (`cp`, `p`, `rz`, `u1`, `crz`)."""
    from . import constants
    counts = qc.count_ops()
    sites = sum(int(counts.get(g, 0)) for g in ('cp', 'p', 'rz', 'u1', 'crz', 'cu1'))
    return constants.rotation_epsilon(delta, sites), sites


def full_clifford_t_conversion(qc, *, phase_grad_bits: Optional[int] = None,
                                gridsynth_eps: Optional[float] = None,
                                delta: Optional[float] = None,
                                leaf_drop_bits: Optional[int] = None) -> Tuple[Any, Dict[str, Any]]:
    """`qc` -> pure Clifford+T, using BOTH real methods for the reason each earns its place:

      1. `to_clifford_t_via_phase_gradient` -- the phase-accumulator method -- converts every
         (dyadic, arithmetic) `cp` to Toffolis exactly, for free.
      2. `_splice_gridsynth_for_p_gates` -- real `pygridsynth` -- synthesizes the O(gradient_bits)
         single-qubit rotations left over from PREPARING the shared gradient register itself
         (step 1 cannot eliminate these; they are not arithmetic sites).
      3. `decompose_all_toffolis` -- the exact 7-T Toffoli circuit for every `ccx` (both the
         ones already in `qc` and the ones the gadget's own And/And-adjoint introduced).

    A non-dyadic `cp` (`report["non_dyadic_sites"]`) is a REAL, observed case, not a
    hypothetical: this codebase's own composed `cmodmul` circuit contains an N-th-root-of-unity
    phase (`theta = 2*pi/3` at N=3) the phase-accumulator method cannot represent, found by
    running this conversion (not assumed absent). `to_clifford_t_via_phase_gradient` leaves such
    a site as an UNCHANGED `cp` gate (never silently dropped -- an earlier version of this
    function did drop it, a real bug, caught by `final_gate_counts` showing zero `cp` remaining
    when `non_dyadic_sites` was non-empty); `_splice_gridsynth_for_cp_gates` below converts any
    that remain via the same real `pygridsynth` path the `p`-gate case uses.
    """
    # eps resolution (2026-09-10): an explicit `gridsynth_eps` wins; else `delta` derives it from the
    # budget convention (eps = delta / #rotation sites); else the legacy 1e-10 default.
    budget = None
    if gridsynth_eps is None:
        if delta is not None:
            gridsynth_eps, sites = budget_eps(qc, delta)
            budget = {"delta": delta, "sites": sites, "eps": gridsynth_eps}
        else:
            gridsynth_eps = 1e-10
    grad_qc, report = to_clifford_t_via_phase_gradient(qc, phase_grad_bits=phase_grad_bits,
                                                       leaf_drop_bits=leaf_drop_bits)
    report["gridsynth_eps"] = gridsynth_eps
    report["epsilon_budget"] = budget

    for site in report["non_dyadic_sites"]:
        rz_outer, rz_inner = cphase_to_rz_cnot_angles(site["theta"])
        _outer_qc, _og, outer_t = gridsynth_clifford_t_for_rz(rz_outer, gridsynth_eps)
        _inner_qc, _ig, inner_t = gridsynth_clifford_t_for_rz(rz_inner, gridsynth_eps)
        site["gridsynth_t_count"] = 2 * outer_t + inner_t
        site["gridsynth_eps"] = gridsynth_eps

    cp_qc, cp_report = _splice_gridsynth_for_cp_gates(grad_qc, gridsynth_eps)
    report.update(cp_report)

    p_qc, p_report = _splice_gridsynth_for_p_gates(cp_qc, gridsynth_eps)
    report.update(p_report)

    clifford_t_qc = decompose_all_toffolis(p_qc)
    report["final_gate_counts"] = dict(clifford_t_qc.count_ops())
    report["final_qubits"] = clifford_t_qc.num_qubits
    report["is_pure_clifford_t"] = set(report["final_gate_counts"]) <= {
        "h", "x", "y", "z", "s", "sdg", "t", "tdg", "cx", "cz", "swap"}
    return clifford_t_qc, report


def gridsynth_only_conversion(qc, *, gridsynth_eps: Optional[float] = None,
                              delta: Optional[float] = None) -> Tuple[Any, Dict[str, Any]]:
    """The "phase-accumulator OFF" baseline for characterizing the gadget's real contribution:
    every `cp` (dyadic or not) goes straight through `_splice_gridsynth_for_cp_gates`'s
    `Rz/CNOT/Rz/CNOT` + real `pygridsynth` identity -- no shared phase-gradient register, no
    extra qubits for it, no free Toffoli conversion of arithmetic rotations. `ccx` still gets
    the same exact 7-T template either way (a Toffoli has no continuous parameter, so neither
    method touches it). Same qubit count as `qc` (no gradient-register qubits added).

    Compare this against `full_clifford_t_conversion`'s output at the SAME `gridsynth_eps` to
    quantify what the phase-accumulator method is actually worth in real T-count/build-time
    terms, not a cost-model estimate of it.
    """
    budget = None
    if gridsynth_eps is None:
        if delta is not None:
            gridsynth_eps, sites = budget_eps(qc, delta)
            budget = {"delta": delta, "sites": sites, "eps": gridsynth_eps}
        else:
            gridsynth_eps = 1e-10
    cp_qc, cp_report = _splice_gridsynth_for_cp_gates(qc, gridsynth_eps)
    clifford_t_qc = decompose_all_toffolis(cp_qc)
    report = dict(cp_report)
    report["gridsynth_eps"] = gridsynth_eps
    report["epsilon_budget"] = budget
    report["phase_grad_bits"] = 0
    report["final_gate_counts"] = dict(clifford_t_qc.count_ops())
    report["final_qubits"] = clifford_t_qc.num_qubits
    report["is_pure_clifford_t"] = set(report["final_gate_counts"]) <= {
        "h", "x", "y", "z", "s", "sdg", "t", "tdg", "cx", "cz", "swap"}
    return clifford_t_qc, report


def verification_cost(original_qc, converted_qc) -> Dict[str, Any]:
    """What `verify_unitary_equivalence(original_qc, converted_qc)` will cost, BEFORE running it.

    The cost driver is the CONVERTED circuit, not the original one, and callers have repeatedly
    gated on the original (see `simcost`'s module docstring for the two live cases). Converting
    appends a phase-gradient register, so a 6-qubit original becomes a 21-qubit simulation and the
    per-basis loop runs 2^6 times over it -- three hours, from a circuit that looks tiny.
    """
    n_orig = original_qc.num_qubits
    n_new = converted_qc.num_qubits
    gates = sum(converted_qc.count_ops().values())
    if n_new == n_orig:
        # dense-Operator branch: 2^n x 2^n, built for BOTH circuits
        cost = simcost.operator_cost(n_orig, gates + sum(original_qc.count_ops().values()))
    else:
        cost = simcost.statevector_cost(n_new, gates, runs=1 << n_orig)
    cost["n_orig"] = n_orig
    cost["n_converted"] = n_new
    return cost


def verify_unitary_equivalence(original_qc, converted_qc, *, extra_qubits_are_ancilla_zero: bool = True,
                                atol: float = 1e-8,
                                max_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Compare `original_qc`'s unitary against `converted_qc` restricted to the original qubits
    with every extra (phase-gradient) qubit fixed at |0> on input AND output -- valid because
    `PhaseGradientState`/its adjoint exactly prepare/uncompute that register to |0> around the
    gadget sites, so the extra qubits are guaranteed back to |0> at the end for ANY input on the
    original qubits. Only feasible at small qubit count (dense `Operator`); the caller is
    responsible for keeping n small (this mirrors every other small-n exact check in this
    codebase, e.g. `qiskit_bridge.sampled_verify_cmodmul`).

    `max_seconds` (default None -- unchanged behaviour: run whatever is asked) makes "keep n small"
    checkable instead of advisory. When the predicted cost exceeds it, NOTHING IS SIMULATED and the
    result is `{"pass": None, "skipped": True, "reason": ...}` carrying the prediction. `pass` is
    None, never True: a check that did not run must not read as a check that succeeded. Gate on
    this rather than on `original_qc.num_qubits` -- the original width does not predict the cost,
    because the conversion adds the phase-gradient register that dominates it (`verification_cost`).
    """
    import numpy as np
    from qiskit.quantum_info import Operator, Statevector

    n_orig = original_qc.num_qubits
    n_new = converted_qc.num_qubits

    cost = verification_cost(original_qc, converted_qc)
    if not simcost.affordable(cost, max_seconds):
        return {"method": cost["method"], "qubits": n_orig, "pass": None, "skipped": True,
                "cost": cost, "reason": simcost.explain(cost, max_seconds)}
    if n_new == n_orig:
        U_orig = Operator(original_qc).data
        U_new = Operator(converted_qc).data
        # compare up to global phase
        idx = np.unravel_index(np.argmax(np.abs(U_orig)), U_orig.shape)
        phase = U_orig[idx] / U_new[idx]
        max_err = float(np.max(np.abs(U_orig - phase * U_new)))
        return {"method": "dense_operator", "qubits": n_orig, "max_abs_error": max_err,
                "pass": max_err < atol}

    extra = n_new - n_orig
    max_err = 0.0
    checked = 0
    for basis in range(2 ** n_orig):
        # Qiskit's `from_label`/display convention is MSB-first-on-the-left, i.e. the LEFTMOST
        # character is the HIGHEST qubit index (verified directly: `X` on qubit 1 starting from
        # '00' yields '10', not '01'). The extra (phase-gradient/ancilla) qubits sit at the
        # HIGHER indices (appended after the original qubits), so they are the LEFT prefix here.
        sv_in = Statevector.from_label("0" * extra + format(basis, f"0{n_orig}b"))
        sv_out = sv_in.evolve(converted_qc)
        probs = sv_out.probabilities_dict()
        leaked = sum(p for bits, p in probs.items() if bits[:extra] != "0" * extra)
        if leaked > atol:
            return {"method": "statevector_per_basis", "qubits": n_orig,
                     "pass": False, "reason": f"phase-grad register did not return to |0> "
                                              f"(leaked prob {leaked:.2e}) for input {basis}"}
        expected = Statevector.from_label(format(basis, f"0{n_orig}b")).evolve(original_qc)
        got = Statevector(sv_out.data.reshape(2 ** extra, 2 ** n_orig)[0, :] /
                           math.sqrt(1 - leaked) if leaked < 1 else sv_out)
        fid = abs(expected.inner(got)) ** 2
        max_err = max(max_err, 1 - fid)
        checked += 1
    return {"method": "statevector_per_basis", "qubits": n_orig, "checked": checked,
            "max_infidelity": max_err, "pass": max_err < atol}
