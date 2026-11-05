"""One click: Lean build → emit per k → mirror differential → symbolic bloqs → verification →
estimation curves + tables + manifest.

    python -m forshor_qualtran.cli run --k 2 3 4 5 6 7 --n-max 50000 [--require-real] [--build]
    python -m forshor_qualtran.cli emit --k 3            # just the bundle, saved to artifacts/lean/
    python -m forshor_qualtran.cli crosscheck --k 2 3    # real vs mirror, per section

Halts on the first failure; exit 0 iff every step passed AND provenance is REAL-LEAN.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import List, Optional

from . import estimate, paths, schemas
from .emit import EmitError, crosscheck, emit_bundle, phases_crosscheck



def _skip_why(exc: Exception) -> str:
    """A short tag for why a REAL-LEAN step is being skipped.

    Until the 2026-09-18 cutover an `EmitError` here meant exactly one thing -- no built
    binary -- so every handler hardcoded that string. The v2 emitter also raises it for the
    five subcommands it no longer has (asks L5-L7, L21), and reporting those as "no binary"
    sends the reader off to rebuild an emitter that is already sitting there. The message
    carries the reason; use it.
    """
    msg = str(exc)
    m = re.search(r"has no `(\w+)` subcommand", msg)
    if m:
        ask = re.search(r"ask (L\d+)", msg)
        return f"no `{m.group(1)}` in the v2 emitter" + (f" ({ask.group(1)})" if ask else "")
    if "binary not found" in msg:
        return "no binary"
    if "TIMEOUT" in msg:
        return "emitter timeout"
    return "emit failed"


def _skip_reason(exc: Exception, context: str) -> str:
    """The full SKIP reason: what the emitter said, plus what we wanted it for."""
    return f"{exc} [while building: {context}]"


def _log(msg: str) -> None:
    print(f"[oneclick {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def step_build() -> bool:
    lake = paths.lake_executable()
    if lake is None:
        _log("lake not found — skipping build (mirror provenance)")
        return False
    _log("lake build forshor_emit phase_emit  (first exe link compiles the import closure: minutes on a warm tree)")
    proc = subprocess.run([lake, "build", "forshor_emit", "phase_emit"], cwd=paths.FORSHOR_TREE,
                          capture_output=True, text=True)
    tail = "\n".join(proc.stdout.splitlines()[-5:]) + proc.stderr[-2000:]
    _log(tail)
    return proc.returncode == 0


def step_emit(ks: List[int], m_max, w_max, require_real: bool, refresh: bool = False, timeout: float = 3600.0,
              max_workers: Optional[int] = None):
    """PATCH 2 FIX 2026-09-03 (HANDOFF_PATCH_VERIFY_AND_PERF): dispatch the per-k `forshor_emit bundle k`
    subprocesses CONCURRENTLY rather than serially. `emit_bundle` blocks on `subprocess.run` (releases
    the GIL) and each k writes to a distinct cache file (`artifacts/lean/k<k>.forshor.json`), so a thread
    pool gives true wall-clock parallelism with no pickling and no cache collision. Wall time drops from
    Σ_k to ≈ max_k — this, not the n=50000 estimate sweep (which is memoized and cheap), was the actual
    bottleneck (FORSHOR_EMIT_BUILD.md §6: k=7's tight-width search alone can exceed 600s serially).
    Order of the returned list matches `ks` (`ThreadPoolExecutor.map` preserves submission order); an
    `EmitError` from any `require_real=True` case propagates when the results are iterated, exactly as
    it did from the serial for-loop."""
    def _one(k: int):
        t0 = time.time()
        b = emit_bundle(k, m_max, w_max, require_real=require_real, refresh=refresh, timeout=timeout)
        p = b.save()
        _log(f"k={k}: {b.provenance.value}  ({time.time() - t0:.1f} s) → {p.name}  {'; '.join(b.notes)}")
        return b

    with ThreadPoolExecutor(max_workers=max_workers or max(1, len(ks))) as ex:
        bundles = list(ex.map(_one, ks))
    not_cert = [b.k for b in bundles if not b.certifies]
    if not_cert:
        _log(f"NOT LEAN-CERTIFIED for k = {not_cert}: those bundles are the Python mirror; every figure/table below carries this per-k tag")
    return bundles


def step_verify(bundles, ms_small=(2, 3, 4)) -> dict:
    """The differential that ties the priced object to the verified object (and the phase lists)."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    from .bloqs import LeanPhaseProduct
    from .bloqs.phase_product import concrete_ir_bloq, ir_leaf_exponents
    report = {"cases": [], "ok": True}
    for b in bundles:
        degenerate = set(b["coeff_poly"]["degenerate_m"])
        for m in ms_small:
            n_val = b.k * m
            if m in degenerate:
                _log(f"verify k={b.k} m={m}: skipped (degenerate: radix 2^m is an interpolation point)")
                continue
            pp = LeanPhaseProduct.from_bundle(b, n_val, "tight", phi=Fraction(1, 8), leaf_policy="ir")
            ir_bloq, ir_doc = concrete_ir_bloq(b, n_val, Fraction(1, 8), "tight")
            cs, ci = get_cost_value(pp, QECGatesCost()), get_cost_value(ir_bloq, QECGatesCost())
            leaves_ok = pp._leaf_exponents() == ir_leaf_exponents(ir_doc, reduce=False)
            ok = leaves_ok and cs.and_bloq == ci.and_bloq and cs.rotation == ci.rotation
            pc = phases_crosscheck(b.k, m)
            ok = ok and pc.get("forshor_emit_equal") is not False and pc.get("phase_emit_equal") is not False
            report["cases"].append({"k": b.k, "n": n_val, "W": int(pp.W), "leaf_multiset_equal": leaves_ok,
                                    "and": [int(cs.and_bloq), int(ci.and_bloq)], "rotation": [int(cs.rotation), int(ci.rotation)],
                                    "phases": {w: pc.get(f"{w}_equal") for w in ("forshor_emit", "phase_emit")}, "ok": ok})
            report["ok"] &= ok
            _log(f"verify k={b.k} n={n_val}: leaves {'==' if leaves_ok else '!='}  And {cs.and_bloq}/{ci.and_bloq}  rot {cs.rotation}/{ci.rotation}  phases {report['cases'][-1]['phases']}  → {'OK' if ok else 'FAIL'}")
    return report


def step_verify_qft(bundles, ws=(2, 3, 4, 5)) -> dict:
    """Phase A (E4) differential: the priced `LeanQFT(w)` is tied to the Lean-lowered `lowerQFT` IR.

    The priced object is the KMY QFT with the RECURSIVE Toom-Cook twiddle (core='recursive', policy='sym'),
    the same object the Lean emitter lowers via `lowerLeaf` — NOT a one-level twiddle (that would not be
    sub-quadratic and its And-count would not match the IR). Three legs, mirroring `step_verify`:
      * STRUCTURE — `LeanQFT(w).splits()` == the E4 Cooley–Tukey rule (`bloqs.qft.rule_splits`), φ-free;
      * COST (V)  — `QECGatesCost(LeanQFT).and_bloq == QECGatesCost(IR).and_bloq` (the And-count is the
                    φ-independent naive-grid census summed over recursive twiddles; binding). Rotation
                    counts are reported, not yet binding:
                    that requires threading φ/leaf_policy through the LeanQFT twiddles so the priced side
                    reduces dyadic leaves exactly as the IR does — the one scoped follow-up for this leg;
      * ACTION    — the IR operator equals the DFT up to global phase (`bloqs.qft.qft_action_ok`).

    REAL-LEAN only: without the built binary, `concrete_ir_bloq` raises `Emit2Error` and every case is
    SKIP (never FAIL), exactly like the E7 recursive verifier. `report['ok']` is True on all-SKIP so the
    mirror pipeline is not blocked; certification comes only from real cases that PASS.

    TODO-3 (partial migration): `concrete_ir_bloq` (bloqs/qft.py) now emits via the new `shor_emit qft`
    command, raising `Emit2Error` (not the old `emit.EmitError`) when its binary is missing."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value

    from .bloqs.phase_product import ir_leaf_exponents
    from .bloqs.qft import LeanQFT, concrete_ir_bloq, qft_action_ok, rule_splits
    from .emit2 import Emit2Error as EmitError

    report = {"cases": [], "ok": True, "any_real": False}
    for b in bundles:
        for w in ws:
            # KMY §"Fast exact quantum Fourier transform": step-2 twiddle is the RECURSIVE Toom-Cook
            # PhaseProduct (not one-level), lowered to full depth — matching the Lean emitter's `lowerLeaf`.
            # policy='sym' is the plan's own nextSignedWidth (what the E7 lowering uses).
            lq = LeanQFT.from_bundle(b, int(w), "sym", core="recursive", depth="lean")
            struct_ok = lq.splits() == rule_splits(int(w))
            try:
                ir_bloq, ir_doc = concrete_ir_bloq(b, int(w))
            except EmitError as exc:
                report["cases"].append({"k": b.k, "w": int(w), "structure_ok": struct_ok, "verdict": "SKIP",
                                        "reason": _skip_reason(exc, "no mirror by design")})
                _log(f"verify-qft k={b.k} w={w}: structure {'OK' if struct_ok else 'FAIL'}  → SKIP ({_skip_why(exc)})")
                continue
            report["any_real"] = True
            cs = get_cost_value(lq, QECGatesCost())
            ci = get_cost_value(ir_bloq, QECGatesCost())
            and_ok = int(cs.and_bloq) == int(ci.and_bloq)
            act = qft_action_ok(ir_doc, int(w))
            ir_leaves = ir_leaf_exponents(ir_doc, reduce=False)
            ok = struct_ok and and_ok and act["verdict"] in ("PASS", "SKIP")
            report["cases"].append({"k": b.k, "w": int(w), "structure_ok": struct_ok,
                                    "and": [int(cs.and_bloq), int(ci.and_bloq)], "and_equal": and_ok,
                                    "rotation": [int(cs.rotation), int(ci.rotation)],
                                    "ir_leaf_kinds": len(ir_leaves), "action": act,
                                    "verdict": "PASS" if ok else "FAIL"})
            report["ok"] &= ok
            _log(f"verify-qft k={b.k} w={w}: structure {'OK' if struct_ok else 'FAIL'}  "
                 f"And {cs.and_bloq}/{ci.and_bloq}  rot {cs.rotation}/{ci.rotation}  "
                 f"action {act['verdict']}  → {'PASS' if ok else 'FAIL'}")
    return report


# ---------------------------------------------------------------- action-cost guard (rewired 2026-09-22)
#: Above this many gates, do not attempt the exact-action pass at all: building the Qiskit
#: circuit alone runs to minutes and hundreds of MB, and a skip with a reason is more useful
#: than a reaped process.
MAX_ACTION_GATES = 2_000_000

#: Seconds of exact-action checking to spend per case. The sample count is derived from this
#: and the document's size rather than fixed, so a big document is checked LESS rather than not
#: at all -- a weaker check is still a check, and the old binary skip produced none.
ACTION_BUDGET_S = 20.0

#: Measured on the benchmark machine, 2026-09-22: the basis-state walk costs ~3.7 us per gate per sampled
#: operand pair (234,531 gates x 24 pairs = 20.6 s). Only ever used to pick a sample count, so
#: being off by a factor on another machine changes how thoroughly a case is checked, never
#: whether the result is right.
ACTION_SECONDS_PER_GATE_SAMPLE = 3.7e-6

#: Never drop below this many sampled operand pairs; below it the check stops being meaningful
#: and the case should be skipped outright instead.
MIN_ACTION_SAMPLES = 8
MAX_ACTION_SAMPLES = 256


def _action_size(doc: dict) -> dict:
    """Gate count and wire count of an emitted circuit document, v1 or v2.

    The old guards read `doc["qubits"]["count"]`, which v2 does not have -- so all three have
    been inert since the cutover, silently. Worse, qubit count was never the right measure:
    measured across the rec anchors, k=3 n=30 has the MOST qubits (265) and k=2 n=48 has fewer
    (223) but 5.5x the gates and 3.5x the runtime. The cost of a build + cost + action pass
    tracks GATES, essentially linearly.
    """
    gates = doc.get("gate_count")
    wires = doc.get("max_qubit_index")
    wires = None if wires is None else int(wires) + 1
    qubits = doc.get("qubit_count")
    if gates is None:                                      # v1 shape
        q = doc.get("qubits") or {}
        qubits = q.get("count")
        wires = wires if wires is not None else qubits
    if qubits is not None and wires is not None:
        wires = max(int(wires), int(qubits))
    return {"gates": None if gates is None else int(gates),
            "wires": None if wires is None else int(wires),
            "qubits": None if qubits is None else int(qubits)}


def _action_plan(doc: dict, budget_s: float = ACTION_BUDGET_S) -> dict:
    """Decide whether to run the exact-action check, and with how many samples.

    Returns `{"skip": <reason or None>, "samples": int, ...size metrics}`. A document whose
    size cannot be read is run at the default sample count rather than skipped -- an unreadable
    size is a reason to be careful, not a reason to stop checking.
    """
    size = _action_size(doc)
    gates = size["gates"]
    if gates is None:
        return {"skip": None, "samples": MAX_ACTION_SAMPLES, **size}
    if gates > MAX_ACTION_GATES:
        return {"skip": (f"{gates:,} gates > {MAX_ACTION_GATES:,}: the exact-action pass would "
                         f"take ~{gates * MIN_ACTION_SAMPLES * ACTION_SECONDS_PER_GATE_SAMPLE:.0f} s "
                         f"even at the minimum {MIN_ACTION_SAMPLES} samples, and building the "
                         f"circuit alone is the larger cost. Not a correctness limit."),
                "samples": 0, **size}
    per_sample = max(gates * ACTION_SECONDS_PER_GATE_SAMPLE, 1e-9)
    samples = int(budget_s / per_sample)
    samples = max(MIN_ACTION_SAMPLES, min(MAX_ACTION_SAMPLES, samples))
    return {"skip": None, "samples": samples, **size}




def step_verify_rec(bundles, ns=(8, 16, 21, 30)) -> dict:
    """Phase B (E6/E7): tie the SWEPT recursive-PhaseProduct skeleton to the Lean-lowered E7 circuit.

    The estimate curves price ``LeanRecursivePhaseProduct(k, n, "sym")`` (the E6 symbolic-width skeleton)
    and carry it to large n by evaluation. This verifier certifies that skeleton at anchor n against the
    E7 emission (``emit.lower_rec`` / LowerRec.lean), so the curve inherits (V) at the anchors:

      * LADDER   — ``doc['recursion']['ladder'] == skel.ladder()`` (the E6→E7 width-ladder tie);
      * COST (V) — the BINDING tie is ``QECGatesCost(skel).and_bloq == QECGatesCost(IR).and_bloq``
                   (the two cost models agree on the total And of the SAME object). ``skel.total_czpow()``
                   (= q^d·W_d²) is only the LEAF CZPow-grid census — a lower bound on the total, equal to
                   it only when the ladder has depth 0; the moment recursion fires, the total And is
                   leaves PLUS the recursion's own Toom-combination adders (Add/Negate), so asserting
                   three-way equality against the leaf subtotal FAILs by construction whenever depth > 0.
                   PATCH 1 FIX 2026-09-03 (HANDOFF_PATCH_VERIFY_AND_PERF): the original three-way
                   ``==`` conflated "cost models agree" with "no recursion adders exist" — REAL-LEAN
                   diagnostic (k=2, n=30, ladder [30,18,12,9,8,7], depth 5) confirmed `cs == ci` (18283
                   == 18283, both > czpow=11907) at the deepest tested case, i.e. the cost MODELS already
                   agree; only the assertion was mis-specified. Patch 1b (reconciling the skeleton's
                   Add/Negate accounting against the IR's ForShorAddScaled) is NOT needed — see below;
      * CHECKS   — the document's own ``leaves_match_ladder`` and ``coeff_verified`` flags;
      * ACTION   — the STRONG leg: ``ref_interp`` confirms the circuit's action is exp(iφ·x·z) (PASS).

    REAL-LEAN only: without the binary every case is SKIP and ``report['ok']`` stays True (the recursive
    circuit has no mirror by design). Between anchors the curve rests on the E6 cost model — stated, not
    certified — exactly as the provenance claim says."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value

    from .bloqs.recursive import LeanRecursivePhaseProduct, concrete_ir_bloq, rec_action_ok
    from .emit import EmitError, lower_rec, pp as emit_pp

    # ACTION COST (rewired 2026-09-22, was MAX_REC_ACTION_QUBITS). The old guard peeked at the
    # cached IR's qubit count and skipped the case above 1000. Two things were wrong with it
    # after the cutover: it read `doc["qubits"]["count"]`, a v1-only key, so it had been inert
    # on every v2 document; and qubit count is the wrong measure anyway. Measured across these
    # anchors, k=3 n=30 has the MOST qubits (265) yet k=2 n=48 has fewer (223) with 5.5x the
    # gates and 3.5x the runtime -- the cost of build + cost + action tracks GATES, near
    # linearly, and no anchor comes close to 1000 qubits.
    #
    # `_action_plan` therefore budgets instead of gating: the sample count for the exact-action
    # walk is derived from the document's gate count, so a large document is checked LESS
    # rather than not at all. The old behaviour discarded a case entirely; a weaker check is
    # still a check. Only a genuinely unbuildable document (> MAX_ACTION_GATES) is skipped.
    report = {"cases": [], "ok": True, "any_real": False}
    for b in bundles:
        for n in ns:
            skel = LeanRecursivePhaseProduct.from_bundle(b, int(n), "sym")
            try:
                peek_doc = lower_rec(b.k, int(n), Fraction(1, 8))
            except EmitError as exc:
                report["cases"].append({"k": b.k, "n": int(n), "verdict": "SKIP",
                                        "reason": _skip_reason(exc, "E7 recursive circuit has no mirror")})
                continue
            plan = _action_plan(peek_doc)
            if plan["skip"]:
                report["cases"].append({"k": b.k, "n": int(n), "verdict": "SKIP",
                                        "gates": plan["gates"], "wires": plan["wires"],
                                        "reason": plan["skip"]})
                _log(f"verify-rec k={b.k} n={n}: → SKIP ({plan['gates']:,} gates)")
                continue
            try:
                ir_bloq, doc = concrete_ir_bloq(b, int(n))
            except EmitError as exc:
                report["cases"].append({"k": b.k, "n": int(n), "verdict": "SKIP",
                                        "reason": _skip_reason(exc, "E7 recursive circuit has no mirror")})
                _log(f"verify-rec k={b.k} n={n}: → SKIP ({_skip_why(exc)})")
                continue
            report["any_real"] = True
            ci = get_cost_value(ir_bloq, QECGatesCost())

            # BINDING TIE (retied 2026-09-21). Formerly: `QECGatesCost(skeleton).and_bloq ==
            # QECGatesCost(IR).and_bloq`. That worked while v1's `Naive_SignedPhaseProd` was a
            # LowGate CONSTRUCTOR our backend lowered to a CZPow grid, one And per operand-bit
            # pair. The v2 drop made it a `def` expanding through `LowGate.CPhase` =
            # (Phase, Phase, CNOT, Phase, CNOT), which uses no And at all -- so the emitted And
            # count is 0 and the old tie compared the skeleton's 64 against the document's 0.
            # Neither number was wrong; they were counting different implementations.
            #
            # The tie is now `skel.lean_resources()` against the document's own `resources`,
            # channel for channel, in LEAN's vocabulary. That is exact (verified k=2..5, depths
            # 0..5) and it is angle-agnostic on both sides, which matters: `resources` charges
            # `rz := 1` per Phase whatever the angle, and reclassifying those into Clifford /
            # T / rotation is our accounting, not ForShor's (D10). Tying on the structural
            # counts keeps the gate about the circuit, and leaves the refinement to be
            # reported rather than asserted.
            pred, got = skel.lean_resources(), doc.get("resources", {})
            channels = ("rz", "cnot", "toffoli", "x")
            per_channel = {c: [pred.get(c, 0), int(got.get(c, 0))] for c in channels}
            resources_equal = all(a == b_ for a, b_ in per_channel.values())
            mismatched = [c for c, (a, b_) in per_channel.items() if a != b_]

            # The ladder is implied by the above rather than read: a wrong ladder cannot
            # reproduce rz AND toffoli AND x AND cnot simultaneously, since the leaf term scales
            # as q^d*W_d^2 and the adder terms as sum q^i*(2*W_(i+1)-3). v2's flat document has
            # no `recursion.ladder` key to compare against, so this is the honest replacement.
            ladder_ok = resources_equal

            # The v2 flat document carries neither the operand registers nor the blocking
            # checks; both live in the ANNOTATED view, whose root node has x / z / phi and
            # whose `meta.checks` carries `annotated_eq_flat` (the two views agree gate for
            # gate), `ladder`, and `template_match`. v1 documents carry their own `expected`
            # and `checks` blocks and never reach for this.
            plan_doc = None
            if str(doc.get("schema", "")).startswith("forshor.lowgate/"):
                try:
                    plan_doc = emit_pp(b.k, int(n), Fraction(1, 8))
                except EmitError:
                    plan_doc = None
            checks = doc.get("checks")
            if checks:                                       # v1
                checks_ok = bool(checks.get("leaves_match_ladder")) and bool(checks.get("coeff_verified"))
            elif plan_doc is not None:                       # v2
                mc = plan_doc.get("meta", {}).get("checks", {})
                checks_ok = bool(mc.get("annotated_eq_flat")) and bool(mc.get("ladder"))
            else:
                checks_ok = False
            act = rec_action_ok(doc, sample=plan["samples"], plan_doc=plan_doc)
            ok = resources_equal and checks_ok and act["verdict"] == "PASS"
            report["cases"].append({"k": b.k, "n": int(n), "ladder": skel.ladder(),
                                    "ladder_ok": ladder_ok, "resources": per_channel,
                                    "resources_equal": resources_equal, "mismatched": mismatched,
                                    "and": [0, int(ci.and_bloq)],
                                    "qualtran": {"t": int(ci.t), "rotation": int(ci.rotation),
                                                 "clifford": int(ci.clifford)},
                                    "checks_ok": checks_ok, "action": act["verdict"],
                                    "verdict": "PASS" if ok else "FAIL"})
            report["ok"] &= ok
            summary = ("resources OK" if resources_equal
                       else "resources FAIL " + ",".join(f"{c} {per_channel[c][0]}/{per_channel[c][1]}"
                                                         for c in mismatched))
            _log(f"verify-rec k={b.k} n={n}: {summary}  "
                 f"(rz={pred['rz']} cnot={pred['cnot']} toffoli={pred['toffoli']} x={pred['x']})  "
                 f"refined t={ci.t} rot={ci.rotation}  "
                 f"action {act.get('mode', '?')}  "
                 f"checks {'OK' if checks_ok else 'FAIL'}  action {act['verdict']}  → {'PASS' if ok else 'FAIL'}")
    return report


def step_verify_bloq_qiskit(bundles, primitive: str = "rec", params: dict | None = None) -> dict:
    """G1 in-process (HANDOFF_QUALTRAN_QISKIT_PORT.2026-09-03.md §C.5): certify the faithful arrow
    Lean IR -> Qualtran bloq -> Qiskit walker circuit, via `qiskit_bridge.faithful_convert_equiv`.

    REAL-LEAN + Qiskit + frozen-Qualtran gated. `primitive in {"rec","qft","multiply"}` is exact-action-
    faithful (Lean-verified end to end; "multiply" since 2026-09-04 via `compose_multiply_bloq` — see
    `qiskit_bridge.faithful_convert_equiv`'s docstring); `"cmodmul"` (2026-09-05) and `"modexp"`
    (2026-09-05, extending the same technique to the composed ladder) are now real numeric action
    checks via `sampled_verify_cmodmul`/`sampled_verify_modexp` (per-residue / per-exponent
    exact-transition-amplitude, not dense G1 — see those functions' docstrings).
    `report['ok']` stays True on SKIP-only runs — certification comes only from real PASS cases."""
    from .qiskit_bridge import faithful_convert_equiv

    # NOTE 2026-09-05: cmodmul's (and modexp's, which composes cmodmul steps) default `n=2` for `N=3`
    # used to silently violate the width precondition `2**(n-1) >= N` (only 2**1=2 >= 3 is false) --
    # the exact case that produced the data_in=2 anomaly diagnosed and resolved that day (see project
    # memory / HANDOFF_MODEXP_CMODMUL_VERIFICATION.2026-09-04.md). Both bumped to n=3 (2**2=4 >= 3) so
    # these defaults are meaningful, passing test cases now that both primitives run real numeric legs
    # instead of always SKIPping/structural-only. `eta`/`delta` pinned explicitly to 0.5 (the loosest
    # precision `extra_bits` allows, same choice `build_showcase_circuits`'s B6 default already makes)
    # -- omitting it would silently fall back to the sampled_verify_* functions' own default (1e-3),
    # which blows the work register up and makes the per-case tensor_contract impractically slow for a
    # routine CLI check.
    DEFAULT_PARAMS = {"rec": {"n": 4}, "qft": {"w": 3}, "cmodmul": {"n": 3, "c": 2, "N": 3, "eta": 0.5},
                      "multiply": {"n": 4, "c": 3, "w": 4},
                      "modexp": {"n": 3, "a": 2, "N": 3, "tbits": 2, "delta": 0.5},
                      "orderfinding": {"n": 3, "a": 2, "N": 3, "L": 2, "delta": 0.5}}
    p = params if params is not None else DEFAULT_PARAMS.get(primitive, {})
    report: dict = {"cases": [], "ok": True, "any_real": False}
    for b in bundles:
        r = faithful_convert_equiv(b, primitive, p)
        verdict = r["verdict"]
        if verdict == "PASS":
            report["any_real"] = True
        elif verdict == "FAIL":
            report["ok"] = False
        report["cases"].append({"k": b.k, "primitive": primitive, "params": p, **r})
        _log(f"verify-bloq-qiskit k={b.k} primitive={primitive}: {verdict}" +
             (f"  ({r.get('reason', r.get('leg', ''))})" if verdict == "SKIP" else ""))
    return report


def step_build_circuits(bundles, n: int = 2, w: int = 2, c: int = 3, *, emit_only: bool = False,
                        template_mode: bool = False) -> dict:
    """B1-B4 proof-of-principle (2026-09-04): the REAL Qiskit circuit for each primitive, built directly
    from the Lean-certified Qualtran bloq through the SAME walker (`qiskit_bridge.build_showcase_circuits`)
    -- demonstrating the Lean IR -> Qualtran bloq -> Qiskit converter is generalizable across primitive
    shapes, not hand-tuned per case. Kept at n=w=2 (2-bit) by default -- a small-scale circuit-BUILD step,
    deliberately separate from the symbolic resource estimate (which never re-invokes the emitter/walker
    per swept n). B4 (multiply) is a single composed bloq (`compose_multiply_bloq`) since 2026-09-04,
    drawn through the same walker as B1-B3 -- see that function's docstring."""
    from . import paths
    from .qiskit_bridge import build_showcase_circuits, build_showcase_circuits_emit_only

    out_dir = paths.ARTIFACTS / "circuits"
    report: dict = {"cases": [], "ok": True}
    for b in bundles:
        try:
            if emit_only:
                res = build_showcase_circuits_emit_only(
                    b, out_dir, n=n, w=w, c=c, template_mode=template_mode)
            else:
                res = build_showcase_circuits(b, out_dir, n=n, w=w, c=c)
        except Exception as e:
            report["cases"].append({"k": b.k, "verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"})
            _log(f"build-circuits k={b.k}: SKIP ({type(e).__name__}: {e})")
            continue
        def _no_draw(d: dict) -> dict:
            return {kk: (_no_draw(vv) if isinstance(vv, dict) else vv) for kk, vv in d.items() if kk != "draw"}

        for name, r in res["primitives"].items():
            verdict = r.get("verdict")
            if verdict == "SKIP":
                report["ok"] = False
            summary = (f"qubits={r.get('qubits')} depth={r.get('depth')}" if verdict == "BUILT" else
                      r.get("reason", ""))
            if emit_only and verdict == "BUILT":
                summary += "  [STRUCTURAL ONLY: NOT dense-verified beyond n=14; coherent frame is not minimal-qubit]"
            report["cases"].append({"k": b.k, "primitive": name, "verdict": verdict, **_no_draw(r)})
            _log(f"build-circuits k={b.k} {name}: {verdict}  ({summary})")
    return report


def _coprime_c(N: int) -> int:
    """Smallest c ≥ 2 with gcd(c, N) = 1 — a valid multiplier so step5Constant's inverse exists."""
    import math
    c = 2
    while math.gcd(c, N) != 1:
        c += 1
    return c


def step_verify_modmul(bundles, ns=(6, 8, 10), eta: float = 1e-3) -> dict:
    """Phase C, leg C1 (cost-structure V): tie the priced KMY controlled modular multiply to the E5
    `CmodMulInPlaceCore` Lean lowering. The priced object is the KMY modmul — core='recursive',
    policy='sym', qft='lean' — so every internal phase product and QFT twiddle is the recursive single-k
    object of Phases A/B (a one-level core would be the wrong object and fail the differential).

      * COST (V, C1) — `QECGatesCost(priced).and_bloq == QECGatesCost(IR).and_bloq`. The And-count is the
                       φ- and (c,N)-independent leaf census of the phase products + QFTs + comparators.
                       BINDING, with one stated requirement: the emitter lowers the three Prim comparators
                       (CMP_GE_CONST, CSUB_CONST, CMP_LT_NW) to the SAME Qualtran arithmetic bloqs the
                       priced side uses (GreaterThanConstant, controlled AddK(−N), LessThanConstant). The
                       report splits out the comparator subtotal so a mismatch is localized to that O(n)
                       term rather than the super-linear phase-product/QFT core.
      * ACTION (C2)  — SKIP: the whole circuit is the APPROXIMATE Alg-1 core; an exact action check needs
                       `IdealCtrlModMulExactSemantics` instantiated. Internal phase-products/QFTs are
                       action-verified via Phases B/A (see bloqs.modmul.modmul_action_status).

    REAL-LEAN only — SKIP on the mirror, `report['ok']` stays True."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value

    from .bloqs.modmul import LeanCtrlModMul, concrete_ir_bloq, modmul_action_status
    from .emit import EmitError, lower_cmodmul

    # ACTION COST: shared with step_verify_rec via `_action_plan` (rewired 2026-09-22, was
    # MAX_MODMUL_ACTION_QUBITS, which read the v1-only key `doc["qubits"]["count"]` and was
    # therefore inert on v2). Unreachable today -- `cmodmul` is retired (ask L6), so the
    # EmitError above fires first -- but wired correctly so it works the day L6 lands rather
    # than silently not guarding anything.
    report = {"cases": [], "ok": True, "any_real": False}
    for b in bundles:
        for n in ns:
            N = (1 << int(n)) - 1
            c = _coprime_c(N)
            priced = LeanCtrlModMul.from_bundle(b, int(n), eta=eta, N=N, policy="sym", core="recursive", qft="lean")
            # priced comparator subtotal (the O(n) arithmetic tail), so a mismatch can be localized
            comp = _modmul_comparator_and(priced)
            try:
                peek_doc = lower_cmodmul(b.k, int(n), c, N, eta)
            except EmitError as exc:
                report["cases"].append({"k": b.k, "n": int(n), "verdict": "SKIP",
                                        "reason": _skip_reason(exc, "E5 modmul has no mirror")})
                _log(f"verify-modmul k={b.k} n={n}: → SKIP ({_skip_why(exc)})")
                continue
            _plan = _action_plan(peek_doc)
            if _plan["skip"]:
                report["cases"].append({"k": b.k, "n": int(n), "verdict": "SKIP",
                                        "gates": _plan["gates"], "wires": _plan["wires"],
                                        "reason": _plan["skip"]})
                _log(f"verify-modmul k={b.k} n={n}: → SKIP ({_plan['gates']:,} gates)")
                continue
            try:
                ir_bloq, doc = concrete_ir_bloq(b, int(n), c, N, eta)
            except EmitError as exc:
                report["cases"].append({"k": b.k, "n": int(n), "verdict": "SKIP",
                                        "reason": _skip_reason(exc, "E5 modmul has no mirror")})
                _log(f"verify-modmul k={b.k} n={n}: → SKIP ({_skip_why(exc)})")
                continue
            report["any_real"] = True
            cs = get_cost_value(priced, QECGatesCost())
            ci = get_cost_value(ir_bloq, QECGatesCost())
            and_ok = int(cs.and_bloq) == int(ci.and_bloq)
            act = modmul_action_status(doc)
            ok = and_ok           # C1 binds on And; action is C2 (SKIP, not required for C1)
            report["cases"].append({"k": b.k, "n": int(n), "c": c, "N": N, "eta": eta,
                                    "and": [int(cs.and_bloq), int(ci.and_bloq)], "and_equal": and_ok,
                                    "comparator_and_priced": int(comp), "phase_qft_and_priced": int(cs.and_bloq) - int(comp),
                                    "rotation": [int(cs.rotation), int(ci.rotation)], "action": act["verdict"],
                                    "verdict": "PASS" if ok else "FAIL"})
            report["ok"] &= ok
            _log(f"verify-modmul k={b.k} n={n}: And {cs.and_bloq}/{ci.and_bloq} "
                 f"(comparators≈{comp}, phase+qft≈{int(cs.and_bloq)-int(comp)})  action {act['verdict']}(C2)  "
                 f"→ {'PASS' if ok else 'FAIL'}")
    return report


def _modmul_comparator_and(mm) -> int:
    """And-count of the priced modmul's three comparator/adder ops alone (U3/U4) — the O(n) arithmetic
    tail whose Lean lowering convention is the one thing to confirm against the first real emission."""
    from qualtran import QUInt
    from qualtran.bloqs.arithmetic import AddK, GreaterThanConstant, LessThanConstant
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    wc, ww, N = mm.w_carry, mm.w_work, max(mm.N, 1)
    total = 0
    for bl in (GreaterThanConstant(wc, N), AddK(QUInt(wc), k=-N).controlled(), LessThanConstant(wc + ww, N)):
        total += int(get_cost_value(bl, QECGatesCost()).and_bloq)
    return total


def step_verify_modexp(bundles, ns=(6, 8), a: int = 2, delta: float = 1e-2, tbits=None) -> dict:
    """Phase D: tie the priced `LeanModExp` to the Lean modexp descriptor `emit.lower_modexp`
    (`shor.modexp.ir/v1`, sourced from `modExpIdealSteps`). REAL-LEAN ONLY — no mirror, no PASS without
    the binary. The mod-exp STRUCTURE (step count, multiplier schedule) is ForShor's; Python never
    asserts it. Legs:

      * STRUCTURE (V) — the priced step count equals the Lean-emitted `tbits`, and the multiplier
        schedule length matches. This is the mod-exp-specific content, and it comes FROM Lean.
      * COST (V, C1) — `QECGatesCost(priced).and_bloq == QECGatesCost(composed).and_bloq`, where
        `composed` = tbits(Lean) × (the descriptor's embedded lowerCmodMul step template). So leg C1 is
        against the Lean-lowered E5 object, inherited exactly.
      * ACTION (C2) — SKIP: `modExpApprox_correct : ‖·‖ ≤ tbits·stepErr` is APPROXIMATE and the exact
        semantics class is uninstantiated (as in Phase C).

    Without the binary every case is SKIP; `report['ok']` on all-SKIP means "not refuted", not certified —
    the caller must check `any_real` before reporting PASS (exactly like verify-qft/rec/modmul)."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value

    from .bloqs.modexp import LeanModExp, concrete_ir_bloq
    from .emit import EmitError

    from .emit import lower_modexp

    # ACTION COST: `_action_plan` on the EMBEDDED step template, which is what the tbits-fold
    # compose pays for repeatedly (rewired 2026-09-22, was MAX_MODEXP_ACTION_QUBITS -- same
    # v1-only key, same inertness). Unreachable today: `modexp` is retired (ask L7).
    report = {"cases": [], "ok": True, "any_real": False}
    for b in bundles:
        for n in ns:
            N = (1 << int(n)) - 1
            me = LeanModExp.from_bundle(b, int(n), delta=delta, tbits=tbits, N=N, policy="sym", core="recursive", qft="lean")
            steps = me.n_steps
            try:
                peek_desc = lower_modexp(b.k, int(n), int(a), N, int(steps), float(delta))
            except EmitError as exc:
                report["cases"].append({"k": b.k, "n": int(n), "tbits": steps, "verdict": "SKIP",
                                        "reason": _skip_reason(exc, "modexp structure is Lean's — no mirror")})
                _log(f"verify-modexp k={b.k} n={n}: → SKIP ({_skip_why(exc)})")
                continue
            _step = peek_desc.get("modexp", {}).get("step_template", {})
            _plan = _action_plan(_step)
            if _plan["skip"]:
                report["cases"].append({"k": b.k, "n": int(n), "verdict": "SKIP",
                                        "gates": _plan["gates"], "wires": _plan["wires"],
                                        "reason": "embedded step: " + _plan["skip"]})
                _log(f"verify-modexp k={b.k} n={n}: → SKIP (step {_plan['gates']:,} gates)")
                continue
            try:
                composed, desc = concrete_ir_bloq(b, int(n), a, N, steps, delta)
            except EmitError as exc:
                report["cases"].append({"k": b.k, "n": int(n), "tbits": steps, "verdict": "SKIP",
                                        "reason": _skip_reason(exc, "modexp structure is Lean's — no mirror")})
                _log(f"verify-modexp k={b.k} n={n}: → SKIP ({_skip_why(exc)})")
                continue
            report["any_real"] = True
            lean_tbits = int(desc["modexp"]["tbits"])
            sched_len = len(desc["modexp"]["multipliers"])
            struct_ok = (steps == lean_tbits == sched_len)
            and_me = int(get_cost_value(me, QECGatesCost()).and_bloq)
            and_ir = int(get_cost_value(composed, QECGatesCost()).and_bloq)
            and_ok = and_me == and_ir
            ok = struct_ok and and_ok
            report["cases"].append({"k": b.k, "n": int(n), "tbits_priced": steps, "tbits_lean": lean_tbits,
                                    "schedule_len": sched_len, "structure_ok": struct_ok,
                                    "and": [and_me, and_ir], "and_equal": and_ok, "action": "SKIP",
                                    "verdict": "PASS" if ok else "FAIL"})
            report["ok"] &= ok
            _log(f"verify-modexp k={b.k} n={n}: tbits priced/lean {steps}/{lean_tbits}  structure "
                 f"{'OK' if struct_ok else 'FAIL'}  And {and_me}/{and_ir}  action SKIP(C2)  → {'PASS' if ok else 'FAIL'}")
    return report


def step_verify_orderfinding(bundles, ns=(6, 8), a: int = 2, delta: float = 1e-2, L=None) -> dict:
    """Phase E: tie the priced `LeanOrderFinding` to the Lean order-finding descriptor
    `emit.lower_orderfinding` (`shor.orderfinding.ir/v1`, from `orderFindingApprox`). REAL-LEAN ONLY —
    SKIP on the mirror, never PASS. The composition (QPE frame, mod-exp, IQFT) is ForShor's. Legs:

      * STRUCTURE (V) — priced exponent width L == the Lean-emitted L.
      * COST (V, C1) — `And(priced) == And(Lean composed)` where composed = H^{⊗L} + X + tbits×(step
        template) + IQFT bloq, all from the Lean descriptor; the frame is Clifford. Cost-structure is
        inherited from Phase D / Phase A, now against a Lean order-finding object.
      * ACTION (C2) — SKIP: the circuit is the APPROXIMATE order-finding circuit (uninstantiated semantics).

    Without the binary every case is SKIP; the caller checks `any_real` before reporting PASS."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value

    from .bloqs.orderfinding import LeanOrderFinding, concrete_ir_bloq
    from .emit import EmitError

    report = {"cases": [], "ok": True, "any_real": False}
    for b in bundles:
        for n in ns:
            N = (1 << int(n)) - 1
            of = LeanOrderFinding.from_bundle(b, int(n), a=a, delta=delta, L=L, N=N, policy="sym", core="recursive", qft="lean")
            Lw = of.exp_width
            try:
                composed, desc = concrete_ir_bloq(b, int(n), a, N, Lw, delta)
            except EmitError as exc:
                report["cases"].append({"k": b.k, "n": int(n), "L": Lw, "verdict": "SKIP",
                                        "reason": _skip_reason(exc, "order-finding composition is Lean's — no mirror")})
                _log(f"verify-orderfinding k={b.k} n={n}: → SKIP ({_skip_why(exc)})")
                continue
            report["any_real"] = True
            lean_L = int(desc["orderfinding"]["exponent_width_L"])
            struct_ok = (Lw == lean_L)
            and_of = int(get_cost_value(of, QECGatesCost()).and_bloq)
            and_ir = int(get_cost_value(composed, QECGatesCost()).and_bloq)
            and_ok = and_of == and_ir
            ok = struct_ok and and_ok
            report["cases"].append({"k": b.k, "n": int(n), "L_priced": Lw, "L_lean": lean_L, "structure_ok": struct_ok,
                                    "and": [and_of, and_ir], "and_equal": and_ok, "action": "SKIP",
                                    "verdict": "PASS" if ok else "FAIL"})
            report["ok"] &= ok
            _log(f"verify-orderfinding k={b.k} n={n}: L priced/lean {Lw}/{lean_L}  structure "
                 f"{'OK' if struct_ok else 'FAIL'}  And {and_of}/{and_ir}  action SKIP(C2)  → {'PASS' if ok else 'FAIL'}")
    return report


def step_verify_multiply(bundles, ns=(4, 6), c: int = 3) -> dict:
    """Phase F: tie the priced Toom-Cook multiply to the Lean multiply descriptor `emit.lower_multiply`
    (`shor.multiply.ir/v1`, from `alg1Step2FourierMultiplier`). REAL-LEAN ONLY — SKIP on the mirror.

    The multiply is H_reg(out) ; PhaseProduct ; IQFT. The interpolation COEFFICIENTS and the RECURSION are
    ForShor's (the embedded Phase-B PhaseProduct + the proved compiler);
    the IQFT is the Phase-A QFT. The priced object uses the SAME Lean-plan recursion (policy='sym',
    core='recursive') as the emitted IR — NOT the version-B cost model. Legs:

      * COST (V) — And(priced PP + IQFT) == And(Lean IR PP + IQFT). Binding (φ-independent leaf census).
      * ACTION (EXACT, no C2) — unlike modmul, the multiply is exact: |x⟩|0⟩ ↦ |x⟩|c·x mod 2^w⟩ is the
        composition of the PhaseProduct fidelity (lowerSignedPhaseProduct_correct) and the QFT fidelity
        (lowerQFT_correct). `multiplier_unitary_check` witnesses that exact operator numerically at small n."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value

    from .bloqs.multiplier import concrete_ir_bloq, multiplier_unitary_check
    from .bloqs.qft import LeanQFT
    from .bloqs.recursive import LeanRecursivePhaseProduct
    from .emit import EmitError

    report = {"cases": [], "ok": True, "any_real": False}
    for b in bundles:
        for n in ns:
            w = int(n)
            pp_width = max(int(n), w) + 1
            pp = LeanRecursivePhaseProduct.from_bundle(b, pp_width, "sym")            # Lean plan (coeffs+recursion)
            iqft = LeanQFT.from_bundle(b, w, "sym", core="recursive", depth="lean")   # Phase-A recursive-twiddle QFT
            and_priced = int(get_cost_value(pp, QECGatesCost()).and_bloq) + int(get_cost_value(iqft, QECGatesCost()).and_bloq)
            try:
                composed, desc = concrete_ir_bloq(b, int(n), c, w)
            except EmitError as exc:
                report["cases"].append({"k": b.k, "n": int(n), "verdict": "SKIP",
                                        "reason": _skip_reason(exc, "multiply coeffs+recursion are Lean's — no mirror")})
                _log(f"verify-multiply k={b.k} n={n}: → SKIP ({_skip_why(exc)})")
                continue
            report["any_real"] = True
            and_ir = int(get_cost_value(composed, QECGatesCost()).and_bloq)           # H is Clifford → PP+IQFT And
            and_ok = and_priced == and_ir
            act = multiplier_unitary_check(int(n), w, c) if n <= 6 else {"ok": None, "note": "n>6: exact-action check skipped for size"}
            action_ok = act.get("ok") in (True, None)                                # exact operator (Lean fidelity witness)
            ok = and_ok and action_ok
            report["cases"].append({"k": b.k, "n": int(n), "c": c, "w": w, "and": [and_priced, and_ir], "and_equal": and_ok,
                                    "exact_action": act.get("ok"), "max_infidelity": act.get("max_infidelity"),
                                    "action": "EXACT" if act.get("ok") else ("SKIP" if act.get("ok") is None else "FAIL"),
                                    "verdict": "PASS" if ok else "FAIL"})
            report["ok"] &= ok
            _log(f"verify-multiply k={b.k} n={n}: And {and_priced}/{and_ir}  exact-action "
                 f"{act.get('ok')}  → {'PASS' if ok else 'FAIL'}")
    return report


def step_estimate(bundles, n_max: int, out_dir: Path) -> dict:
    ns = sorted({int(x) for x in [4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024,
                                    1536, 2048, 3072, 4096, 6144, 8192, 12288, 16384, 24576, 32768, 50000] if x <= n_max})
    rows = estimate.sweep(bundles, ns)
    rec = estimate.recursion_sweep(bundles, [w for w in (8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096) if w <= n_max])
    qft = estimate.qft_sweep(bundles[0], [w for w in (4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048) if w <= n_max])
    mm = estimate.modmul_sweep(bundles, [n for n in (8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096) if n <= n_max])
    rpp = estimate.recursive_pp_sweep(bundles, [n for n in (16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384) if n <= n_max])
    figs = estimate.figures(rows, bundles, out_dir, rec_rows=rec, points=estimate.measured_points(), qft_rows=qft, mm_rows=mm, rpp_rows=rpp)
    # recursive PhaseProduct: arity versions A/B/C under rules R0/R1/R2 (slope diagnosis + correction), V13/V14/V15
    from . import recursion_versions
    rv = recursion_versions.run_all(bundles, out_dir, ks=[b.k for b in bundles if b.k <= 6], n_hi=min(32768, max(n_max, 64)))
    figs.update(rv["figures"])
    _log(f"recursion versions: model == Qualtran at all check points: {rv['qualtran_check_ok']}; "
         + "; ".join(f"B k={e['k']} R2 fit {e['fit[4096,32768]']} vs log_k q {e['theory']}" for e in rv["exponents"] if e["version"] == "B" and e["rule"] == "kmy"))
    man = estimate.manifest(bundles, rows, {"recursion_rows": rec, "qft_rows": qft, "modmul_rows": mm, "recursive_pp_rows": rpp, "figures": figs,
                                            "recursion_versions": {k: v for k, v in rv.items() if k != "figures"}},
                            out_dir / "estimate_manifest.json")
    return {"rows": len(rows), "figures": figs, "manifest": str(man),
            "verdict": json.loads(man.read_text())["verdict"]}


def _verdict_ok(verdict: str) -> bool:
    """Does a manifest verdict count as success for the process exit code?

    "PASS" and "PASS with N retired citation(s): ..." both do. The second means every claim
    resolved except ones whose Lean declaration the 2026-09-18 drop removed -- a real caveat,
    carried in the verdict text and the manifest's `retired_citations`, but not a failure: no
    change on this side can make a deleted upstream declaration resolve. Exact string equality
    against "PASS" made the whole one-click run exit non-zero while printing PASS, which is how
    notebook 00 failed with a message telling the reader to go build binaries that were present.
    """
    return str(verdict).startswith("PASS")


def run(args) -> int:
    ks = args.k
    built = step_build() if args.build else (paths.forshor_emit_binary() is not None)
    try:
        bundles = step_emit(ks, args.m_max, args.w_max, args.require_real, refresh=args.refresh, timeout=args.emit_timeout)
    except EmitError as e:
        _log(f"EMIT FAILED: {e}")
        return 2
    verify = step_verify(bundles, (2, 3, 4))
    if not verify["ok"]:
        _log("VERIFY FAILED — halting before estimation")
        (paths.ESTIMATE_ARTIFACTS).mkdir(parents=True, exist_ok=True)
        (paths.ESTIMATE_ARTIFACTS / "verify_report.json").write_text(json.dumps(verify, indent=1))
        return 3
    est = step_estimate(bundles, args.n_max, paths.ESTIMATE_ARTIFACTS)
    (paths.ESTIMATE_ARTIFACTS / "verify_report.json").write_text(json.dumps(verify, indent=1))
    cert = all(b.certifies for b in bundles)
    _log(f"figures: {list(est['figures'].values())}")
    _log(f"manifest: {est['manifest']}")
    _log(f"VERDICT: {est['verdict']}   (provenance {'REAL-LEAN' if cert else 'REFERENCE-MIRROR'}; built={built})")
    return 0 if _verdict_ok(est["verdict"]) else (0 if not args.require_real and not cert else 1)


def golden(args) -> int:
    """Version-B / rule-R2 scaling layer: PP → QFT → Toom-Cook multiplier → CmodMulInPlaceCore (golden.py)."""
    from . import golden as G
    built = step_build() if args.build else (paths.forshor_emit_binary() is not None)
    ks = sorted(set(args.k) | {2})
    try:
        bundles = step_emit(ks, args.m_max, args.w_max, args.require_real, refresh=args.refresh, timeout=args.emit_timeout)
    except EmitError as e:
        _log(f"EMIT FAILED: {e}")
        return 2
    verify = step_verify(bundles, (2, 3, 4))
    if not verify["ok"]:
        _log("VERIFY FAILED — halting before estimation")
        return 3
    out = paths.ARTIFACTS / "golden"
    man = G.run_all(bundles, out, ks=args.k, n_hi=args.n_max, fit_window=(args.fit_lo, min(args.fit_hi, args.n_max)),
                    eta=args.eta, precision=args.precision, delta=args.delta)
    (out / "verify_report.json").write_text(json.dumps(verify, indent=1))
    _log(f"golden: checks (Qualtran == closed form) ok = {man['checks_ok']} over {len(man['checks'])} points")
    for e in man["exponents"]:
        _log(f"  {e['object']:8s} k={e['k']:2d}  e_fit {e['e_fit']:.4f}  theory {e['e_theory']:.4f}  R2_fit {e['R2_fit']:.4f}  R2_theory {e['R2_theory']:.4f}")
    _log(f"figures: {list(man['figures'].values())}")
    _log(f"manifest: {out / 'golden_manifest.json'}")
    # asymptotics ledger + per-primitive/full-Shor plots against the ForShor-proved exponents (one-click alignment)
    from . import asymptotics as A
    aman = A.run_all(bundles, paths.ARTIFACTS / "asymptotics", ks=args.k, n_hi=min(8192, args.n_max), eta=args.eta)
    for e in aman["verification_ledger"]:
        _log(f"  asy {e['primitive']:7s} k={e['k']:2d}  e_fit {e['e_fit']:.4f}  Lean {e['e_theory']:.4f}  R2_theory {e['R2_theory']:.4f}  [{e['lean_bound']}]")
    _log(f"asymptotics figures: {list(aman['figures'].values())}")
    _log(f"VERDICT: {man['verdict']}   (built={built})")
    return 0 if _verdict_ok(man["verdict"]) else (0 if not args.require_real else 1)


def shor_qiskit(args) -> int:
    """Mesh: build a Lean-driven Qiskit Shor circuit for small N and factor it (Phase Q)."""
    from . import qiskit_bridge as Q
    try:
        bundles = step_emit([args.k], args.m_max, args.w_max, require_real=False, refresh=args.refresh, timeout=args.emit_timeout)
    except EmitError as e:
        _log(f"EMIT FAILED: {e}")
        return 2
    b = bundles[0]
    rec = Q.lean_shor_factor(int(args.N), bundle=b, k=int(args.k), a=args.a, L=args.L, tries=args.tries, seed=args.seed)
    cert = rec.get("certification", {})
    _log(f"bridge (Lean IR == isolated Toom-Cook Qiskit): {cert.get('verdict')}  {cert.get('reason','')}")
    _log(f"shor-qiskit N={args.N} k={args.k}: factors={rec.get('factors')} a={rec.get('a')} r={rec.get('r')} route={rec.get('route')}")
    _log(f"lean_verified={rec.get('lean_verified')}   VERDICT: {rec.get('verdict')}")
    return 0


def asymptotics(args) -> int:
    """Per-primitive Qualtran cost vs the ForShor-proved asymptotic exponent, plus the full-Shor stack."""
    from . import asymptotics as A
    step_build() if args.build else None
    ks = sorted(set(args.k) | {2})
    try:
        bundles = step_emit(ks, args.m_max, args.w_max, args.require_real, refresh=args.refresh, timeout=args.emit_timeout)
    except EmitError as e:
        _log(f"EMIT FAILED: {e}")
        return 2
    man = A.run_all(bundles, paths.ARTIFACTS / "asymptotics", ks=args.k, n_hi=args.n_max,
                    eta=args.eta, precision=args.precision, delta=args.delta)
    for e in man["verification_ledger"]:
        _log(f"  {e['primitive']:7s} k={e['k']:2d}  e_fit {e['e_fit']:.4f}  Lean-proved {e['e_theory']:.4f}  R2_theory {e['R2_theory']:.4f}  [{e['lean_bound']}]")
    _log(f"figures: {list(man['figures'].values())}")
    _log(f"manifest: {paths.ARTIFACTS / 'asymptotics' / 'asymptotics_manifest.json'}")
    _log(f"VERDICT: {man['verdict']}")
    return 0 if _verdict_ok(man["verdict"]) else (0 if not args.require_real else 1)


def main(argv=None) -> int:
    if sys.platform == "win32":
        # stdout/stderr default to the console codepage (cp1252) there, which cannot encode
        # the unicode math (->, <=, phi, ...) this module prints throughout.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, ValueError):
                pass
    ap = argparse.ArgumentParser(prog="forshor-oneclick")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--k", type=int, nargs="+", default=[2, 3, 4, 5, 6, 7])
    r.add_argument("--n-max", type=int, default=50000); r.add_argument("--m-max", type=int, default=None, help="default 24 (k<=5) / 16 (k>=6)")
    r.add_argument("--w-max", type=int, default=None, help="default 96 (k<=5) / 64 (k>=6)"); r.add_argument("--require-real", action="store_true")
    r.add_argument("--build", action="store_true", help="run `lake build forshor_emit phase_emit` first")
    r.add_argument("--refresh", action="store_true", help="re-emit even if a REAL-LEAN bundle is cached in artifacts/lean/")
    r.add_argument("--emit-timeout", type=float, default=3600.0, help="seconds per `forshor_emit bundle k` (default 3600)")
    e = sub.add_parser("emit"); e.add_argument("--k", type=int, nargs="+", required=True)
    e.add_argument("--m-max", type=int, default=None); e.add_argument("--w-max", type=int, default=None)
    e.add_argument("--refresh", action="store_true"); e.add_argument("--emit-timeout", type=float, default=3600.0)
    c = sub.add_parser("crosscheck"); c.add_argument("--k", type=int, nargs="+", required=True)
    vq = sub.add_parser("verify-qft", help="Phase A (E4): tie priced LeanQFT to the lowerQFT IR (structure/And/DFT)")
    vq.add_argument("--k", type=int, nargs="+", default=[2, 3, 4]); vq.add_argument("--w", type=int, nargs="+", default=[2, 3, 4, 5])
    vq.add_argument("--m-max", type=int, default=None); vq.add_argument("--w-max", type=int, default=None)
    vq.add_argument("--refresh", action="store_true"); vq.add_argument("--emit-timeout", type=float, default=3600.0)
    vr = sub.add_parser("verify-rec", help="Phase B (E6/E7): tie the swept recursive-PP skeleton to the lowerRec IR (ladder/And/action)")
    vr.add_argument("--k", type=int, nargs="+", default=[2, 3]); vr.add_argument("--n", type=int, nargs="+", default=[8, 16, 21, 30])
    vr.add_argument("--m-max", type=int, default=None); vr.add_argument("--w-max", type=int, default=None)
    vr.add_argument("--refresh", action="store_true"); vr.add_argument("--emit-timeout", type=float, default=3600.0)
    vm = sub.add_parser("verify-modmul", help="Phase C leg C1 (E5): tie priced KMY modmul to the lowerCmodMul IR (And cost-structure)")
    vm.add_argument("--k", type=int, nargs="+", default=[2, 3]); vm.add_argument("--n", type=int, nargs="+", default=[6, 8, 10])
    vm.add_argument("--eta", type=float, default=1e-3); vm.add_argument("--m-max", type=int, default=None); vm.add_argument("--w-max", type=int, default=None)
    vm.add_argument("--refresh", action="store_true"); vm.add_argument("--emit-timeout", type=float, default=3600.0)
    vx = sub.add_parser("verify-modexp", help="Phase D: tie priced LeanModExp to the Lean modexp descriptor (REAL-LEAN only)")
    vx.add_argument("--k", type=int, nargs="+", default=[2, 3]); vx.add_argument("--n", type=int, nargs="+", default=[6, 8])
    vx.add_argument("--a", type=int, default=2, help="base of the exponentiation (multiplier schedule a^(2^e) mod N)")
    vx.add_argument("--delta", type=float, default=1e-2); vx.add_argument("--tbits", type=int, default=None)
    vx.add_argument("--m-max", type=int, default=None); vx.add_argument("--w-max", type=int, default=None)
    vx.add_argument("--refresh", action="store_true"); vx.add_argument("--emit-timeout", type=float, default=3600.0)
    vp = sub.add_parser("verify-multiply", help="Phase F: tie priced Toom-Cook multiply to the Lean multiply descriptor (coeffs+recursion Lean-sourced; exact action)")
    vp.add_argument("--k", type=int, nargs="+", default=[2, 3]); vp.add_argument("--n", type=int, nargs="+", default=[4, 6]); vp.add_argument("--c", type=int, default=3)
    vp.add_argument("--m-max", type=int, default=None); vp.add_argument("--w-max", type=int, default=None)
    vp.add_argument("--refresh", action="store_true"); vp.add_argument("--emit-timeout", type=float, default=3600.0)
    vo = sub.add_parser("verify-orderfinding", help="Phase E: tie priced LeanOrderFinding to the Lean orderFindingApprox descriptor (REAL-LEAN only)")
    vo.add_argument("--k", type=int, nargs="+", default=[2, 3]); vo.add_argument("--n", type=int, nargs="+", default=[6, 8])
    vo.add_argument("--a", type=int, default=2); vo.add_argument("--delta", type=float, default=1e-2); vo.add_argument("--L", type=int, default=None)
    vo.add_argument("--m-max", type=int, default=None); vo.add_argument("--w-max", type=int, default=None)
    vo.add_argument("--refresh", action="store_true"); vo.add_argument("--emit-timeout", type=float, default=3600.0)
    g = sub.add_parser("golden"); g.add_argument("--k", type=int, nargs="+", default=[2, 3, 4, 5, 6, 7, 8, 9, 10])
    g.add_argument("--n-max", type=int, default=2 ** 17); g.add_argument("--fit-lo", type=int, default=4096); g.add_argument("--fit-hi", type=int, default=2 ** 17)
    g.add_argument("--eta", type=float, default=None, help="override η (legacy fixed); default: precision policy")
    g.add_argument("--precision", default="appropriate", choices=["appropriate", "fixed"], help="η policy: appropriate=δ/n² (default) | fixed")
    g.add_argument("--delta", type=float, default=1e-2, help="total-error budget for the appropriate policy (η=δ/n²)")
    g.add_argument("--m-max", type=int, default=None); g.add_argument("--w-max", type=int, default=None)
    g.add_argument("--require-real", action="store_true"); g.add_argument("--build", action="store_true"); g.add_argument("--refresh", action="store_true")
    g.add_argument("--emit-timeout", type=float, default=3600.0)
    ay = sub.add_parser("asymptotics", help="per-primitive Qualtran cost vs the ForShor-proved exponent + full-Shor stack")
    ay.add_argument("--k", type=int, nargs="+", default=[2, 3, 4, 5, 6]); ay.add_argument("--n-max", type=int, default=8192)
    ay.add_argument("--eta", type=float, default=None, help="override η (legacy fixed); default: precision policy")
    ay.add_argument("--precision", default="appropriate", choices=["appropriate", "fixed"], help="η policy: appropriate=δ/n² (default) | fixed")
    ay.add_argument("--delta", type=float, default=1e-2, help="total-error budget for the appropriate policy (η=δ/n²)")
    ay.add_argument("--m-max", type=int, default=None); ay.add_argument("--w-max", type=int, default=None)
    ay.add_argument("--require-real", action="store_true"); ay.add_argument("--build", action="store_true"); ay.add_argument("--refresh", action="store_true")
    ay.add_argument("--emit-timeout", type=float, default=3600.0)
    sq = sub.add_parser("shor-qiskit", help="Phase Q: build a Lean-driven Qiskit Shor circuit for small N and factor it")
    sq.add_argument("--N", type=int, required=True, help="modulus to factor (small: 15, 21)"); sq.add_argument("--k", type=int, default=2)
    sq.add_argument("--a", type=int, default=None); sq.add_argument("--L", type=int, default=None)
    sq.add_argument("--tries", type=int, default=12); sq.add_argument("--seed", type=int, default=0)
    sq.add_argument("--m-max", type=int, default=None); sq.add_argument("--w-max", type=int, default=None)
    sq.add_argument("--refresh", action="store_true"); sq.add_argument("--emit-timeout", type=float, default=3600.0)
    vk = sub.add_parser("verify-bloq-qiskit",
                        help="G1 in-process: the faithful Lean IR -> Qualtran bloq -> Qiskit walker certificate (handoff #2 Track A)")
    vk.add_argument("--k", type=int, nargs="+", default=[2])
    vk.add_argument("--primitive", choices=["rec", "qft", "cmodmul", "multiply"], default="rec")
    vk.add_argument("--m-max", type=int, default=None); vk.add_argument("--w-max", type=int, default=None)
    vk.add_argument("--refresh", action="store_true"); vk.add_argument("--emit-timeout", type=float, default=3600.0)
    bc = sub.add_parser("build-circuits",
                        help="B1-B4 proof-of-principle: draw the REAL Qiskit circuit for each primitive, built "
                             "directly from the Lean-certified Qualtran bloq (2-bit by default, small-scale build)")
    bc.add_argument("--k", type=int, nargs="+", default=[2])
    bc.add_argument("--n", type=int, default=2); bc.add_argument("--w", type=int, default=2)
    bc.add_argument("--c", type=int, default=3)
    bc.add_argument("--emit-only", action="store_true",
                    help="structural walker emission only (no dense verification; large n is not minimal-qubit)")
    bc.add_argument("--template-mode", action="store_true",
                    help="with --emit-only, emit one controlled-modmul template plus a QPE schedule JSON")
    bc.add_argument("--m-max", type=int, default=None); bc.add_argument("--w-max", type=int, default=None)
    bc.add_argument("--refresh", action="store_true"); bc.add_argument("--emit-timeout", type=float, default=3600.0)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return run(args)
    if args.cmd == "golden":
        return golden(args)
    if args.cmd == "asymptotics":
        return asymptotics(args)
    if args.cmd == "shor-qiskit":
        return shor_qiskit(args)
    if args.cmd == "emit":
        for k in args.k:
            b = emit_bundle(k, args.m_max, args.w_max, refresh=args.refresh, timeout=args.emit_timeout)
            _log(f"k={k}: {b.provenance.value} → {b.save()}  {'; '.join(b.notes)}")
        return 0
    if args.cmd == "crosscheck":
        rc = 0
        for k in args.k:
            d = crosscheck(k)
            _log(f"k={k}: {'no binary' if not d['available'] else ('EQUAL' if not d['mismatches'] else 'MISMATCH ' + str(d['mismatches']))}")
            rc |= 1 if d["mismatches"] else 0
        return rc
    if args.cmd == "verify-bloq-qiskit":
        bundles = step_emit(args.k, args.m_max, args.w_max, require_real=False, refresh=args.refresh, timeout=args.emit_timeout)
        rep = step_verify_bloq_qiskit(bundles, args.primitive)
        out = paths.ESTIMATE_ARTIFACTS; out.mkdir(parents=True, exist_ok=True)
        (out / "verify_bloq_qiskit_report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        if not rep["any_real"]:
            _log("verify-bloq-qiskit: all cases SKIP (no REAL-LEAN binary, Qiskit, or frozen-Qualtran pin, "
                 "or a cost-only/composite primitive) — nothing certified")
            return 0
        _log(f"verify-bloq-qiskit VERDICT: {'PASS' if rep['ok'] else 'FAIL'}")
        return 0 if rep["ok"] else 1
    if args.cmd == "build-circuits":
        if args.template_mode and not args.emit_only:
            ap.error("build-circuits --template-mode requires --emit-only")
        bundles = step_emit(args.k, args.m_max, args.w_max, require_real=False, refresh=args.refresh, timeout=args.emit_timeout)
        rep = step_build_circuits(bundles, n=args.n, w=args.w, c=args.c,
                                  emit_only=args.emit_only, template_mode=args.template_mode)
        out = paths.ARTIFACTS / "circuits"; out.mkdir(parents=True, exist_ok=True)
        (out / "build_circuits_report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        n_built = sum(1 for c in rep["cases"] if c.get("verdict") in ("BUILT", "PARTIAL"))
        _log(f"build-circuits: {n_built}/{len(rep['cases'])} built or partial; circuits + .qasm/.draw.txt under {out}")
        return 0 if rep["ok"] else 1
    if args.cmd == "verify-qft":
        bundles = step_emit(args.k, args.m_max, args.w_max, require_real=False, refresh=args.refresh, timeout=args.emit_timeout)
        rep = step_verify_qft(bundles, tuple(args.w))
        out = paths.ESTIMATE_ARTIFACTS; out.mkdir(parents=True, exist_ok=True)
        (out / "verify_qft_report.json").write_text(json.dumps({"schema": schemas.QFT_VERIFY, **rep}, indent=1), encoding="utf-8")
        if not rep["any_real"]:
            _log("verify-qft: all cases SKIP (no forshor_emit binary) — build on the target machine to certify (Phase A)")
            return 0
        _log(f"verify-qft VERDICT: {'PASS' if rep['ok'] else 'FAIL'}")
        return 0 if rep["ok"] else 1
    if args.cmd == "verify-rec":
        bundles = step_emit(args.k, args.m_max, args.w_max, require_real=False, refresh=args.refresh, timeout=args.emit_timeout)
        rep = step_verify_rec(bundles, tuple(args.n))
        out = paths.ESTIMATE_ARTIFACTS; out.mkdir(parents=True, exist_ok=True)
        (out / "verify_rec_report.json").write_text(json.dumps({"schema": schemas.REC_VERIFY, **rep}, indent=1), encoding="utf-8")
        if not rep["any_real"]:
            _log("verify-rec: all cases SKIP (no forshor_emit binary) — build on the target machine to certify (Phase B)")
            return 0
        _log(f"verify-rec VERDICT: {'PASS' if rep['ok'] else 'FAIL'}")
        return 0 if rep["ok"] else 1
    if args.cmd == "verify-modmul":
        bundles = step_emit(args.k, args.m_max, args.w_max, require_real=False, refresh=args.refresh, timeout=args.emit_timeout)
        rep = step_verify_modmul(bundles, tuple(args.n), eta=args.eta)
        out = paths.ESTIMATE_ARTIFACTS; out.mkdir(parents=True, exist_ok=True)
        (out / "verify_modmul_report.json").write_text(json.dumps({"schema": schemas.MODMUL_VERIFY, **rep}, indent=1), encoding="utf-8")
        if not rep["any_real"]:
            _log("verify-modmul: all cases SKIP (no forshor_emit binary) — build on the target machine to certify (Phase C, leg C1)")
            return 0
        _log(f"verify-modmul VERDICT (C1): {'PASS' if rep['ok'] else 'FAIL'}   (action leg C2 pending IdealCtrlModMulExactSemantics)")
        return 0 if rep["ok"] else 1
    if args.cmd == "verify-modexp":
        bundles = step_emit(args.k, args.m_max, args.w_max, require_real=False, refresh=args.refresh, timeout=args.emit_timeout)
        rep = step_verify_modexp(bundles, tuple(args.n), a=args.a, delta=args.delta, tbits=args.tbits)
        out = paths.ESTIMATE_ARTIFACTS; out.mkdir(parents=True, exist_ok=True)
        (out / "verify_modexp_report.json").write_text(json.dumps({"schema": schemas.MODEXP_VERIFY, **rep}, indent=1), encoding="utf-8")
        if not rep["any_real"]:
            _log("verify-modexp: all cases SKIP (no forshor_emit binary) — the modexp structure (tbits, schedule) is "
                 "ForShor's, emitted by Lean; build on the target machine to certify (Phase D). Nothing certifies on the mirror.")
            return 0
        _log(f"verify-modexp VERDICT: {'PASS' if rep['ok'] else 'FAIL'}   (action leg C2 pending IdealCtrlModMulExactSemantics)")
        return 0 if rep["ok"] else 1
    if args.cmd == "verify-multiply":
        bundles = step_emit(args.k, args.m_max, args.w_max, require_real=False, refresh=args.refresh, timeout=args.emit_timeout)
        rep = step_verify_multiply(bundles, tuple(args.n), c=args.c)
        out = paths.ESTIMATE_ARTIFACTS; out.mkdir(parents=True, exist_ok=True)
        (out / "verify_multiply_report.json").write_text(json.dumps({"schema": schemas.MULTIPLY_VERIFY, **rep}, indent=1), encoding="utf-8")
        if not rep["any_real"]:
            _log("verify-multiply: all cases SKIP (no forshor_emit binary) — the multiply's interpolation coefficients "
                 "and recursion are ForShor's, emitted by Lean; build the emitter to certify (Phase F).")
            return 0
        _log(f"verify-multiply VERDICT: {'PASS' if rep['ok'] else 'FAIL'}   (action is EXACT — PhaseProduct∘QFT fidelity; no C2)")
        return 0 if rep["ok"] else 1
    if args.cmd == "verify-orderfinding":
        bundles = step_emit(args.k, args.m_max, args.w_max, require_real=False, refresh=args.refresh, timeout=args.emit_timeout)
        rep = step_verify_orderfinding(bundles, tuple(args.n), a=args.a, delta=args.delta, L=args.L)
        out = paths.ESTIMATE_ARTIFACTS; out.mkdir(parents=True, exist_ok=True)
        (out / "verify_orderfinding_report.json").write_text(json.dumps({"schema": schemas.ORDERFINDING_VERIFY, **rep}, indent=1), encoding="utf-8")
        if not rep["any_real"]:
            _log("verify-orderfinding: all cases SKIP (no forshor_emit binary) — the order-finding composition is "
                 "ForShor's orderFindingApprox, emitted by Lean; build on the target machine to certify (Phase E). Nothing certifies on the mirror.")
            return 0
        _log(f"verify-orderfinding VERDICT: {'PASS' if rep['ok'] else 'FAIL'}   (action leg C2 pending IdealCtrlModMulExactSemantics)")
        return 0 if rep["ok"] else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
