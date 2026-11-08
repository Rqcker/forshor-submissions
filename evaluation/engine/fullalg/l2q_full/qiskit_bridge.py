"""Qualtran → Qiskit mesh: concrete Qiskit circuits driven by the Lean-emitted IR.

The workflow, end to end:

    resource estimation (vary k)  →  pick a small n  →  Lean emit (forshor_emit)  →  shor.lowgate.ir
        →  py/qiskit_backend.build_qiskit_circuit  →  concrete Qiskit circuit  →  compose Shor  →  factor N

Every arithmetic circuit here is built FROM the Lean IR (`emit.lower_*` → `shor.lowgate.ir/v1` →
`qiskit_backend`), so the resulting Qiskit circuit is Lean-driven — not an independent reconstruction.
`bridge_equiv` certifies that the Lean-IR controlled-modmul circuit and an isolated hand-built
Toom-Cook Qiskit circuit realise the SAME unitary at small n (used by a few cmodmul-only checks).
End-to-end order-finding/factoring (`lean_shor_factor`) no longer routes through that bridge: it runs
ForShor's own composed circuit (`compose_orderfinding_bloq`) directly, verified via
`lean_order_distribution`'s closed-form classical-ladder + exact-IQFT computation (see that
function's docstring) rather than an independent hand-built engine.

REAL-LEAN + Qiskit gated: without the built `forshor_emit` binary (no mirror) or without Qiskit
installed, the functions raise `BridgeUnavailable` and the CLI/tests report SKIP — nothing certifies on
the mirror, and nothing is claimed without the arithmetic actually being built.

KMY: the arithmetic is the recursive single-k Toom-Cook phase product (Phase B) inside the QFT-conjugated
multiply (Phase F) inside the controlled modular multiply (Phase C), laddered into modular exponentiation
(Phase D) under the QPE frame + inverse QFT (Phase E). Small n (2–4 bit N) is simulable end to end.
"""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class BridgeUnavailable(RuntimeError):
    """Raised when Qiskit is not installed or the forshor_emit binary is absent (no mirror)."""


_EMIT_ONLY_NOTE = (
    "STRUCTURALLY FAITHFUL walker-built emit-only output; NOT dense-verified beyond n=14. "
    "The coherent frame is NOT a minimal-qubit RSA/Shor circuit."
)


def write_qasm3_stream(qc, path: Path) -> int:
    """Stream a complete Qiskit circuit as OpenQASM 3 to ``path``.

    This uses Qiskit's supported :func:`qiskit.qasm3.dump` stream API, rather than
    :func:`qiskit.qasm3.dumps`, so this bridge never creates a second, giant Python
    string containing the whole QASM artifact.  It emits the full Qiskit OpenQASM 3
    dialect (including Qiskit's generated declarations/definitions), not a local
    partial-QASM reimplementation.  The walker still currently returns one complete
    ``QuantumCircuit``; it has no streaming/counting interface, so this bounds output
    serialization memory only, not the walker's circuit-object memory.

    Returns the final byte length.  Callers must have already performed any desired
    call-graph preflight before asking the walker to materialize ``qc``.
    """
    from qiskit import qasm3

    path = Path(path)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        qasm3.dump(qc, stream)
    return path.stat().st_size


def _fast_leaf_count(bloq, memo: Dict[int, Tuple[int, Any]]) -> int:
    """The total-leaf-occurrence count `sum(Bloq.call_graph()[1].values())` would give, computed
    without ever going through Qualtran's own STRUCTURALLY-memoized call graph.

    WHY: profiled directly (a real composed order-finding bloq, n=8) -- `Bloq.call_graph()` memoizes
    nodes by structural hash/equality (`bloq in g`, `Counter(binst.bloq for binst in
    cbloq.bloq_instances)`), so two attrs-equal-but-distinct-Python-object sub-bloqs collapse into one
    graph node. But a real composed circuit's per-QPE-round sub-bloq (~40k internal instances each, one
    per round, DIFFERENT concrete multiplier constant per round so they never structurally collapse
    with each other) makes `CompositeBloq.__hash__`/`__eq__` themselves cost O(internal-instance-count)
    -- repeatedly hashing/comparing those large composites (once per Counter insertion, once per graph
    membership check, ...) is what actually consumed the 200s+/>6GB this function used to spend, NOT
    `CompactSignedPhaseGrid`'s own per-leaf cost (already separately fixed, see its `build_call_graph`
    docstring -- both fixes are needed together: without that one, `_fast_leaf_count` would still call
    an O(width^2) method on every one of the ~10^5 distinct-by-identity grid leaves below).

    THE FIX: memoize by Python object IDENTITY (`id()`, O(1) regardless of a composite's size) instead
    of structural equality. `id()`-as-dict-key has a well-known pitfall -- once a locally-constructed
    Bloq is garbage-collected, its address can be reused by an unrelated LATER object, causing a false
    cache hit -- so `memo` stores `(count, bloq)` and keeps a STRONG REFERENCE to every visited bloq
    alive for the whole call, not just its count. Two attrs-equal composites that are different Python
    objects are walked separately (not deduplicated) -- more redundant work than Qualtran's structural
    sharing, but each individual walk is now itself fast (no hashing cost), and the redundancy here is
    bounded by the QPE round count, not astronomical. Mirrors `Bloq.call_graph()`'s own base cases:
    `DecomposeNotImplementedError`/`DecomposeTypeError` (or an empty callee dict) mean `bloq` IS a
    leaf, contributing 1 -- any OTHER exception propagates (matching `ignore_decomp_failure=False`
    semantics), so a real bug surfaces as `BridgeUnavailable` below rather than silently undercounting.

    VERIFIED (not assumed) against `Bloq.call_graph()`'s own total, exactly, at n=5 (134447==134447)
    and n=6 (466918==466918) -- `tests/test_qualtran_backend_compact_phase_grid.py` carries this
    regression. Measured speedup already visible at that tiny scale: n=5 14.9s->1.0s, n=6 68.1s->2.3s
    (growing with n); at the real n=16/L=16 scale `Bloq.call_graph()` never finished (>6GB RSS, killed)
    while this function completes -- see `docs/history/RESULT_QUALTRAN_BACKEND_ADJOINT_FIX.2026-09-08.md`."""
    from qualtran import DecomposeNotImplementedError, DecomposeTypeError

    key = id(bloq)
    if key in memo:
        return memo[key][0]
    try:
        callee_counts = bloq.build_call_graph(None)
    except (DecomposeNotImplementedError, DecomposeTypeError):
        memo[key] = (1, bloq)
        return 1
    if not callee_counts:
        memo[key] = (1, bloq)
        return 1
    items = callee_counts.items() if isinstance(callee_counts, dict) else callee_counts
    total = sum(int(mult) * _fast_leaf_count(callee, memo) for callee, mult in items)
    memo[key] = (total, bloq)
    return total


def preflight_bloq_emission(bloq, *, bytes_per_leaf_operation: int = 32) -> Dict[str, Any]:
    """Estimate emission scale from a Qualtran call graph, before ``walk`` builds gates.

    ``estimated_gate_count`` is the EXACT sum of fully-expanded *leaf Bloq calls* (see
    `_fast_leaf_count`'s docstring for why this can be computed fast without Qualtran's own
    `Bloq.call_graph()`, which never finishes on a real composed order-finding bloq) -- not a claimed
    exact OpenQASM instruction count: one leaf can lower to more than one Qiskit instruction. The
    file-size estimate deliberately uses the stated ``bytes_per_leaf_operation`` heuristic for
    planning only. This is the strongest preflight the present walker API permits;
    :mod:`qiskit_walker` exposes neither a gate-count-only walk nor an incremental gate iterator.
    """
    try:
        qubits = int(bloq.signature.n_qubits())
        count = _fast_leaf_count(bloq, {})
    except Exception as exc:
        raise BridgeUnavailable(
            "cannot preflight emission from the Bloq call graph; the walker has no "
            f"count-only fallback ({type(exc).__name__}: {exc})"
        ) from exc
    if count < 0 or bytes_per_leaf_operation < 1:
        raise ValueError("call-graph count and bytes_per_leaf_operation must be positive")
    estimated_bytes = 64 + count * int(bytes_per_leaf_operation)
    return {
        "estimated_qubits": qubits,
        "estimated_gate_count": count,
        "gate_count_kind": "fully-expanded call-graph leaf Bloq operations; not exact QASM instructions",
        "estimated_qasm_bytes": estimated_bytes,
        "file_size_estimate_kind": f"64-byte header + {bytes_per_leaf_operation} bytes per leaf operation",
        "preflight_source": "_fast_leaf_count (identity-memoized Bloq.build_call_graph() walk; exact "
                            "total, equivalent to Bloq.call_graph() but scales to a real composed "
                            "bloq) before qiskit_walker.walk()",
    }


def _write_template_sidecar(out_dir: Path, *, name: str, template_qc, ancillas, preflight: Dict[str, Any],
                            schedule: List[Dict[str, Any]], parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Write one streamed controlled-modmul template and its honest QPE schedule sidecar."""
    out_dir.mkdir(parents=True, exist_ok=True)
    qasm_path = out_dir / f"{name}.qasm3"
    qasm_bytes = write_qasm3_stream(template_qc, qasm_path)
    sidecar_path = out_dir / f"{name}.schedule.json"
    payload = {
        "mode": "template",
        "note": "The actual repeating structure, not a full unroll.",
        "honesty": _EMIT_ONLY_NOTE,
        "template_qasm": str(qasm_path),
        "template": {
            "kind": "one controlled-a^(2^j) modular-multiplication step",
            "qubits": template_qc.num_qubits,
            "ancilla": len(ancillas),
            "gate_counts": dict(template_qc.count_ops()),
            "depth": template_qc.depth(),
            "qasm_bytes": qasm_bytes,
            "preflight": preflight,
        },
        "qpe_schedule": schedule,
        "parameters": parameters,
    }
    sidecar_path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return {"qasm_path": str(qasm_path), "schedule_path": str(sidecar_path), **payload["template"]}


def _write_emit_only_preflight(out_dir: Path, name: str, preflight: Dict[str, Any],
                               parameters: Dict[str, Any], mode: str) -> str:
    """Persist the no-Qiskit-circuit preflight before the walker is invoked."""
    path = out_dir / f"{name}.preflight.json"
    path.write_text(json.dumps({
        "mode": mode,
        "honesty": _EMIT_ONLY_NOTE,
        "parameters": parameters,
        "preflight": preflight,
    }, indent=1, default=str), encoding="utf-8")
    return str(path)


def _require_qiskit():
    try:
        import qiskit  # noqa: F401
    except Exception as e:  # pragma: no cover - environment dependent
        raise BridgeUnavailable(f"qiskit not importable: {e}")
    return __import__("qiskit")


def _legacy():
    """Put py/ (qiskit_backend, lowgate_ir) and ext/qiskit_shor on sys.path; return the modules."""
    import sys

    from . import paths
    paths.setup_legacy_sys_path()
    ext = paths.PIPE_ROOT / "ext" / "qiskit_shor"
    if str(ext) not in sys.path:
        sys.path.insert(0, str(ext))
    import lowgate_ir  # py/
    import qiskit_backend  # py/
    return lowgate_ir, qiskit_backend


# ------------------------------------------------------------------ core bridge: Lean IR → Qiskit
def ir_to_qiskit(ir_doc: Dict[str, Any]):
    """Compile a `shor.lowgate.ir/v1` document into a concrete Qiskit circuit directly (via
    py/qiskit_backend), bypassing Qualtran entirely. This is an INDEPENDENTLY-AUTHORED lowering, not
    derived from the Qualtran bloq -- per handoff #2's "Lean informs Qualtran, Qualtran informs Qiskit,
    nothing invented" principle it is demoted to (a) `faithful_convert_equiv`'s auxiliary cross-check
    leg and (b) `multiply_qiskit`'s temporary builder (no bloq-composer exists yet to route multiply
    through the walker -- see that function). `cmodmul_qiskit`/`qft_qiskit` no longer route through
    this; they use `bloq_to_qiskit_circuit` (the Qualtran-derived path) below."""
    _require_qiskit()
    lowgate_ir, qiskit_backend = _legacy()
    return qiskit_backend.build_qiskit_circuit(lowgate_ir.parse_ir(ir_doc))


# ------------------------------------------------------------------ the faithful arrow: Lean IR -> Qualtran bloq -> Qiskit
def bloq_to_qiskit_circuit(ir_doc: Dict[str, Any]):
    """Compile a `shor.lowgate.ir/v1` document to Qiskit THROUGH the Qualtran bloq -- the faithful arrow
    HANDOFF_QUALTRAN_QISKIT_PORT.2026-09-03.md §C.2 asks for, and now the PRIMARY builder for
    `cmodmul_qiskit`/`qft_qiskit`:
        build_qualtran_bloq(parse_ir(ir_doc))  ->  qiskit_walker.walk(bloq)
    Returns `(qc, layout, ancillas)` (the walker's own return shape). `ir_to_qiskit` remains only as
    the independent auxiliary cross-check (and multiply's temporary builder, pending a bloq composer).

    Raises `BridgeUnavailable` if Qiskit/the walker/the frozen-Qualtran pin are unavailable. The
    walker's own `WalkRefusal` (cost-only bloqs with no exact circuit at all -- e.g. modexp/
    orderfinding, ForShor's C/D/E ceiling) propagates UNCHANGED -- callers must catch it separately and
    label the result SKIP, not FAIL. As of 2026-09-03, cmodmul WALKS (qualtran_patches gives
    GreaterThanConstant a real decomposition; the walker's CompositeBloq-as-subbloq case is fixed) --
    its ceiling is now the DENSE verification width (~121 qubits even at n=2), not a refusal.
    """
    _require_qiskit()
    lowgate_ir, _qiskit_backend = _legacy()
    import qualtran_backend  # py/ (already on sys.path via _legacy()'s setup_legacy_sys_path())
    from . import qiskit_walker as W
    bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(ir_doc))
    try:
        return W.walk(bloq)
    except W.WalkerUnavailable as e:
        raise BridgeUnavailable(str(e))


def _emit_primitive(k: int, primitive: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """A single `shor.lowgate.ir/v1` document for one of the three primitives whose Lean emission IS
    already a single IR document (`bloq_to_qiskit_circuit` needs exactly one). "multiply" is
    deliberately NOT handled here -- see `faithful_convert_equiv`'s comment on why.

    TODO-3 (partial migration): "qft" now routes through the new `shor_emit` emitter
    (`emit2.lower_qft`) -- the old `shor.lowgate.ir/v1` schema requires zero blocking checks for a
    `qft`-kind document, so `ir_v2.to_legacy_doc`'s output is already a complete, valid document.
    "rec" and "cmodmul" stay on the old emitter: `signed_phase_product` (what "rec" produces) DOES
    require five blocking checks in the old schema (`count_matches_q_k`, `run_ok`, `width_bound_ok`,
    `coeff_verified`, `layout_disjoint`, see `py/lowgate_ir.py`'s `REQUIRED_CHECKS`) that
    `to_legacy_doc` does not reconstruct (confirmed live: `parse_ir` raises `IRSchemaError` on the
    migrated document) -- `emit2.lower_rec` exists but is not yet a safe drop-in until that gap is
    closed; "cmodmul" has no established eta->m sizing rule (see `emit2.lower_rec`'s module comment)."""
    from .emit import lower_cmodmul, lower_rec
    from .emit2 import lower_qft
    if primitive == "rec":
        return lower_rec(k, int(params["n"]), Fraction(*params.get("phi", (1, 8))))
    if primitive == "qft":
        return lower_qft(k, int(params["w"]), Fraction(1, 1))
    if primitive == "cmodmul":
        return lower_cmodmul(k, int(params["n"]), int(params["c"]), int(params["N"]), float(params.get("eta", 1e-3)))
    raise KeyError(primitive)


def faithful_convert_equiv(bundle, primitive: str, params: Dict[str, Any], *, tol: float = 1e-9,
                           strict_version: bool = True) -> Dict[str, Any]:
    """The three-way faithful-conversion certificate (G1 ladder, in-process), up to global phase:

        primary  (G1 DENSE): logical_unitary( walk(build_qualtran_bloq(ir)) )  ==  bloq.tensor_contract()
        auxiliary          : Operator( walk(...) )  ~=  Operator( ir_to_qiskit(ir) )

    REAL-LEAN + Qiskit + frozen-Qualtran gated -> SKIP (never FAIL) when any of those, or the walker's
    own WalkRefusal (a cost-only bloq with no exact circuit), makes the check inapplicable.

    Primitives: "rec" (exact, Phase B), "qft" (exact, Phase A), "cmodmul" (Phase C -- since 2026-09-05
    routed through `sampled_verify_cmodmul`'s per-residue exact-transition-amplitude leg instead of the
    dense G1 leg below, which is infeasible past MAX_DENSE_N for cmodmul's real qubit count; the caller
    MUST pass a width-safe `n` (`2**(n-1) >= N`, checked and reported, not enforced -- see that
    function's docstring for why n too small silently corrupts the result instead of raising).
    "multiply" (Phase F, C.4 step 2)
    IS wired here since 2026-09-04, via `compose_multiply_bloq` -- its Lean emission is a COMPOSITE of
    two separate IR documents (phase_product + iqft) plus an explicit Hadamard frame, so there is no
    single `shor.lowgate.ir/v1` document to pass to `_emit_primitive`; the "multiply" branch below
    builds the composed bloq directly instead and runs the SAME walker-vs-tensor_contract primary leg
    as every other primitive (`compose_multiply_bloq`'s own docstring carries the separate, already-
    verified `|x>|0> -> |x>|c*x mod 2^w>` action check -- this PASS is about the WALKER faithfully
    reproducing that composed bloq's action, not a re-derivation of the multiply math).
    """
    import numpy as np

    from . import qiskit_walker as W
    from .emit import EmitError

    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    k = int(doc["k"])

    if primitive == "multiply":
        from .emit import lower_multiply
        # defaults match cli.py's step_verify_bloq_qiskit DEFAULT_PARAMS["multiply"]
        try:
            n_ = int(params.get("n", 4)); c_ = int(params.get("c", 3)); w_ = int(params.get("w", 4))
            desc = lower_multiply(k, n_, c_, w_)
        except EmitError as e:
            return {"verdict": "SKIP", "primitive": primitive, "reason": f"emit unavailable (no REAL-LEAN binary?): {e}"}
        try:
            bloq, meta = compose_multiply_bloq(desc, n_)
        except BridgeUnavailable as e:
            return {"verdict": "SKIP", "primitive": primitive, "reason": str(e)}
        except Exception as e:
            return {"verdict": "SKIP", "primitive": primitive, "reason": f"compose_multiply_bloq unavailable: {type(e).__name__}: {e}"}
        try:
            bloq_to_qiskit, WalkRefusal, logical_unitary, compare = W._walker_env()
            ok, ver = W.qualtran_version()
            if strict_version and not ok:
                return {"verdict": "SKIP", "primitive": primitive, "reason": f"Qualtran {ver} != frozen {W.FROZEN_QUALTRAN}"}
            qc_w, lay, anc = bloq_to_qiskit(bloq)
        except W.WalkerUnavailable as e:
            return {"verdict": "SKIP", "primitive": primitive, "reason": str(e)}
        except Exception as e:
            if type(e).__name__ == "WalkRefusal":
                return {"verdict": "SKIP", "primitive": primitive, "leg": "refused",
                        "reason": f"WalkRefusal (cost-only sub-bloq, no exact circuit): {e}"}
            raise
        MAX_DENSE_N = 14
        if qc_w.num_qubits > MAX_DENSE_N:
            return {"verdict": "SKIP", "primitive": primitive, "leg": "dense_ceiling",
                    "reason": f"{qc_w.num_qubits} qubits > {MAX_DENSE_N}: dense tensor_contract would be memory-unsafe"}
        U_w = logical_unitary(qc_w, lay)
        V = np.asarray(bloq.tensor_contract())
        primary_ok, primary_err, _ = compare(U_w, V, tol)
        return {"verdict": "PASS" if primary_ok else "FAIL", "primitive": primitive, "k": k, "params": params,
                "primary_walker_vs_tensor": {"ok": bool(primary_ok), "max_err": primary_err,
                                             "qubits": qc_w.num_qubits, "ancilla": len(anc), "qualtran": ver},
                "composer_meta": meta,
                "action_tier": ("exact (composed: phase_product + adjoint(iqft); action independently "
                                "verified against |x>|0> -> |x>|c*x mod 2^w> 2026-09-04, see "
                                "compose_multiply_bloq docstring)")}

    if primitive == "modexp":
        # Sampled/exact-transition-amplitude leg (2026-09-05, extends sampled_verify_cmodmul's
        # technique to the COMPOSED modexp ladder): the dense/walker G1 leg previously here always
        # hit dense_ceiling at any realistic size (a single cmodmul step alone is ~39+ qubits), so
        # this is the real numeric ACTION check for modexp now -- not structural-only, unlike this
        # primitive's action_tier note used to say.
        n_ = int(params.get("n", 3)); a_ = int(params.get("a", 2)); N_ = int(params.get("N", 3))
        tbits_ = int(params.get("tbits", 2)); delta_ = float(params.get("delta", 0.5))
        r = sampled_verify_modexp(bundle, n_, a_, N_, tbits_, delta_)
        if r["verdict"] == "SKIP":
            return r
        r["action_tier"] = ("sampled exact-transition-amplitude (per-exponent, flag-maximized) -- see "
                             "sampled_verify_modexp docstring; width_precondition_ok must be True for "
                             "this result to be meaningful (same signed-phase-product width precondition "
                             "as cmodmul, since each step IS a lower_cmodmul call)")
        return r

    if primitive == "orderfinding":
        # Numeric leg (2026-09-05, `sampled_verify_orderfinding`): closes the "not yet built" gap
        # this branch used to always SKIP on. Order-finding's correctness statement is a PROBABILITY
        # DISTRIBUTION over the exponent register x (QPE's whole point, peaked at multiples of
        # 2**L/r for the true order r), not a single classical transition like cmodmul/modexp -- see
        # that function's docstring for the state-vector technique used and the two things it checks
        # (peak concentration + continued-fraction order recovery).
        n_ = int(params.get("n", 3)); a_ = int(params.get("a", 2)); N_ = int(params.get("N", 3))
        L_ = params.get("L"); L_ = int(L_) if L_ is not None else None
        delta_ = float(params.get("delta", 0.5))
        r = sampled_verify_orderfinding(bundle, n_, a_, N_, L_, delta_)
        if r["verdict"] == "SKIP":
            return r
        # Auxiliary structural leg, kept for continuity with every other primitive's "walker reaches
        # a real Qiskit circuit" evidence -- not load-bearing for the verdict (the numeric leg above
        # is), but a real, cheap, independent sanity check worth keeping. Reports WHY it didn't run
        # rather than silently returning null: calling `sampled_verify_orderfinding` first (which
        # imports `qualtran_backend` via `paths.setup_legacy_sys_path()`) can shift `sys.path` so a
        # DIFFERENT installed Qualtran resolves here than standalone (observed: 0.8.0.dev0 standalone
        # vs 0.7.0 after that import) -- the walker's own version-pin safety then correctly refuses,
        # a real (if surprising) environment fragility, not a bug in this check or in the verdict.
        walked_note = None
        try:
            bloq, _meta = compose_orderfinding_bloq(bundle, n_, a_, N_, L_, delta_)
            bloq_to_qiskit, WalkRefusal, logical_unitary, compare = W._walker_env()
            ok, ver = W.qualtran_version()
            if strict_version and not ok:
                walked_note = {"skipped": True,
                                "reason": (f"Qualtran {ver} != frozen {W.FROZEN_QUALTRAN} at this point in "
                                           "the process -- likely a sys.path shift from importing "
                                           "qualtran_backend earlier in this same call; auxiliary only, "
                                           "does not affect the numeric verdict above")}
            else:
                qc_w, lay, anc = bloq_to_qiskit(bloq)
                walked_note = {"qubits": qc_w.num_qubits, "ancilla": len(anc), "qualtran": ver}
        except Exception as e:
            walked_note = {"skipped": True, "reason": f"{type(e).__name__}: {e}"}
        r["k"] = k
        r["walked"] = walked_note
        r["action_tier"] = ("sampled QPE probability-distribution check (peak concentration + "
                             "continued-fraction order recovery) -- see sampled_verify_orderfinding "
                             "docstring; width_precondition_ok must be True for this result to be "
                             "meaningful (same signed-phase-product width precondition as cmodmul/"
                             "modexp, since each modexp step is a lower_cmodmul call)")
        return r

    if primitive == "cmodmul":
        # Sampled/exact-transition-amplitude leg (2026-09-05, closes HANDOFF_MODEXP_CMODMUL_
        # VERIFICATION.2026-09-04.md Part 2): the dense G1 leg below is infeasible past MAX_DENSE_N
        # (cmodmul is 30+ qubits at any realistic n), so this is the real numeric ACTION check for
        # cmodmul -- not cost/structure only, unlike this primitive's action_tier note used to say.
        n_ = int(params.get("n", 3)); c_ = int(params.get("c", 2)); N_ = int(params.get("N", 3))
        eta_ = float(params.get("eta", 1e-3))
        r = sampled_verify_cmodmul(bundle, n_, c_, N_, eta_)
        r["action_tier"] = ("sampled exact-transition-amplitude (per-residue, flag-maximized) -- see "
                             "sampled_verify_cmodmul docstring; width_precondition_ok must be True for "
                             "this result to be meaningful (n too small for N corrupts the SIGNED "
                             "phase-product data operand, per the 2026-09-05 memory resolution)")
        return r

    try:
        ir = _emit_primitive(k, primitive, params)
    except KeyError:
        return {"verdict": "SKIP", "primitive": primitive, "reason": f"unknown primitive {primitive!r}"}
    except EmitError as e:
        return {"verdict": "SKIP", "primitive": primitive, "reason": f"emit unavailable (no REAL-LEAN binary?): {e}"}

    try:
        lowgate_ir, _qb = _legacy()
        import qualtran_backend
        bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(ir))
    except BridgeUnavailable as e:
        return {"verdict": "SKIP", "primitive": primitive, "reason": str(e)}
    except Exception as e:
        return {"verdict": "SKIP", "primitive": primitive, "reason": f"qualtran_backend/bloq build unavailable: {type(e).__name__}: {e}"}

    try:
        bloq_to_qiskit, WalkRefusal, logical_unitary, compare = W._walker_env()
        ok, ver = W.qualtran_version()
        if strict_version and not ok:
            return {"verdict": "SKIP", "primitive": primitive, "reason": f"Qualtran {ver} != frozen {W.FROZEN_QUALTRAN}"}
        qc_w, lay, anc = bloq_to_qiskit(bloq)
    except W.WalkerUnavailable as e:
        return {"verdict": "SKIP", "primitive": primitive, "reason": str(e)}
    except Exception as e:
        # WalkRefusal is a subclass caught here too (cost-only bloqs, e.g. cmodmul -- EXPECTED, not a bug)
        if type(e).__name__ == "WalkRefusal":
            return {"verdict": "SKIP", "primitive": primitive, "leg": "refused",
                    "reason": f"WalkRefusal (cost-only bloq, no exact circuit -- expected for C/D/E): {e}"}
        raise

    MAX_DENSE_N = 14   # (1<<14)**2 * 16 bytes ~= 4 GiB; same conservative ceiling as bloqs/qft.py
    if qc_w.num_qubits > MAX_DENSE_N:
        return {"verdict": "SKIP", "primitive": primitive, "leg": "dense_ceiling",
                "reason": (f"{qc_w.num_qubits} qubits > {MAX_DENSE_N}: dense operator/tensor_contract "
                           "would be memory-unsafe to even attempt (no sampled/DIAGONAL leg wired for "
                           "this primitive yet -- handoff #2 C.3's G1 ladder)"),
                "register_map_note": "walker now reaches this bloq (no WalkRefusal) -- the Qualtran-"
                                     "derived circuit exists; only the DENSE verification leg is too wide"}
    U_w = logical_unitary(qc_w, lay)
    V = np.asarray(bloq.tensor_contract())
    primary_ok, primary_err, _ = compare(U_w, V, tol)

    # §D TEETH: a deliberately reversed layout must MISMATCH -- proves the DENSE PASS above is load-bearing
    # on the actual endianness convention (IR lsb_first / Qualtran QInt msb-first), not an accidental
    # symmetry of the test bloq. Mirrors ext/qiskit_shor/verify_bloq_to_qiskit.py's own TEETH case (G1).
    endian_teeth = {"checked": False}
    try:
        reversed_lay = [list(reversed(row)) for row in lay]
        U_w_reversed = logical_unitary(qc_w, reversed_lay)
        mismatch_ok, mismatch_err, _ = compare(U_w_reversed, V, tol)
        endian_teeth = {"checked": True, "reversed_layout_mismatches": bool(not mismatch_ok),
                        "reversed_layout_err": mismatch_err}
    except Exception as e:
        endian_teeth = {"checked": False, "reason": f"{type(e).__name__}: {e}"}

    aux: Dict[str, Any] = {"verdict": "SKIP", "reason": "layout convention pending (handoff #2 §D)"}
    try:
        from qiskit.quantum_info import Operator
        qc_ir = ir_to_qiskit(ir)
        if qc_ir.num_qubits == qc_w.num_qubits:
            ok2, err2, _ = compare(np.asarray(Operator(qc_w).data), np.asarray(Operator(qc_ir).data), tol)
            aux = {"verdict": "PASS" if ok2 else "FAIL", "err": err2, "qubits": qc_ir.num_qubits}
    except Exception as e:
        aux = {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}

    and_adjoint = getattr(qc_w, "metadata", None)
    and_adjoint = (and_adjoint or {}).get("and_adjoint", "unitary")

    return {"verdict": "PASS" if primary_ok else "FAIL", "primitive": primitive, "k": k, "params": params,
            "primary_walker_vs_tensor": {"ok": bool(primary_ok), "max_err": primary_err,
                                         "qubits": qc_w.num_qubits, "ancilla": len(anc), "qualtran": ver},
            "auxiliary_walker_vs_ir": aux,
            "endianness_teeth": endian_teeth,
            "and_adjoint": and_adjoint,
            "and_ledger_note": ("walker circuits are pure-unitary And/And-uncompute (ccx); this T-ledger is "
                                "NOT Qualtran's measurement-based `And` ledger and must never be conflated "
                                "with the resource-curve numbers from handoff #1's `estimate` module "
                                "(handoff #2 §D)"),
            "qualtran_frozen_pin": {"version": ver, "matches_frozen": ok, "frozen": W.FROZEN_QUALTRAN},
            "action_tier": ("exact (Lean)" if primitive in ("rec", "qft")
                            else "cost/structure only (C/D/E; whole-circuit action numeric, not Lean)")}


# ------------------------------------------------------------------ per-primitive circuits, from the Lean emit layer
def cmodmul_qiskit(bundle, n: int, c: int, N: int, eta: float = 1e-3, refresh: bool = False):
    """Controlled modular multiply (Phase C, CmodMulInPlaceCore) as a Qiskit circuit -- Lean IR ->
    Qualtran bloq -> Qiskit walker (`bloq_to_qiskit_circuit`), not the independent `ir_to_qiskit`
    lowering. Raises the walker's `WalkRefusal` unchanged if some sub-bloq truly can't be walked
    (callers must label that SKIP, not FAIL) -- as of 2026-09-03 cmodmul itself walks fully."""
    from .emit import EmitError, lower_cmodmul
    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    try:
        ir = lower_cmodmul(doc["k"], int(n), int(c), int(N), float(eta), refresh=refresh)
    except EmitError as e:
        raise BridgeUnavailable(str(e))
    qc, _layout, _ancillas = bloq_to_qiskit_circuit(ir)
    return qc, ir


def qft_qiskit(bundle, w: int, refresh: bool = False):
    """The KMY recursive-twiddle QFT (Phase A) as a Qiskit circuit -- Lean IR -> Qualtran bloq -> Qiskit
    walker (`bloq_to_qiskit_circuit`), not the independent `ir_to_qiskit` lowering.

    TODO-3 (partial migration): now emitted via the new `shor_emit qft` command (`emit2.lower_qft`)."""
    from .emit2 import Emit2Error, lower_qft
    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    try:
        ir = lower_qft(doc["k"], int(w), Fraction(1, 1), refresh=refresh)
    except Emit2Error as e:
        raise BridgeUnavailable(str(e))
    qc, _layout, _ancillas = bloq_to_qiskit_circuit(ir)
    return qc, ir


def compose_multiply_bloq(desc: Dict[str, Any], n: int):
    """Build the single, faithful Qualtran Bloq for the Toom-Cook multiply (Phase F: `H_reg(out) ;;
    PhaseProduct(2*pi*c/2^w) ;; IQFT(out)`, `|x>|0> -> |x>|c*x mod 2^w>`), composed directly from
    `lower_multiply`'s two IR documents (`phase_product`, `iqft`) using THEIR OWN operand/register
    metadata for wiring -- never guessed positions (the bug the old positional `qc.compose` below had,
    see git history / HANDOFF notes: the two IR documents each number their qubits from 0 in their OWN
    local space and do not share a physical layout).

    Wiring (all LSB-first, per `qualtran_backend`'s own convention):
      * phase_product's `operands.x.active` is `pp_width` wide: the low `n` bits are the real x input
        (untouched, diagonal gate only), the top `pp_width - n` bit(s) are a zero-initialised
        sign-safety pad (`pp_width = max(n, w_out) + 1`: the "+1" is exactly the headroom a two's-
        complement SIGNED phase product needs to represent an UNSIGNED n-bit value with no numeric
        change -- zero-extension, not a copy of x's own top bit).
      * phase_product's `operands.z.active` is likewise `pp_width` wide: the low `w_out` bits are the
        "out" register (Hadamard'd by THIS function before phase_product runs -- `out` starts as an
        allocated-but-uninitialised register, not part of phase_product's own IR); the top bit(s) are
        the same zero-safety pad.
      * `iqft`'s `register` (its own local ids) is wired 1:1, in order, onto phase_product's `z`'s low
        `w_out` ids (both LSB-first) -- that's the shared "out" register. `iqft`'s remaining local ids
        (its own internal twiddle-recursion scratch) get fresh composed wires.

    Verified 2026-09-04 (`tensor_contract`, exact, cross-checked at (k=2,n=2,c=3,w=2) and
    (k=2,n=3,c=5,w=3)): the "iqft" IR document (`Shor.lowerQFT`) is mathematically a FORWARD QFT --
    amplitude(input=v, output=j) = 2^(-w/2) * exp(+2*pi*i*j*v / 2^w), matching the textbook definition
    to machine precision, with clean (all-|0>) ancilla -- so composing it AS-IS gives `|x>|-c*x mod
    2^w>` (negated). The correct composition uses its `.adjoint()` (the true inverse QFT): verified
    exact and deterministic (amplitude magnitude 1, single output term) for EVERY x, with every
    scratch/ancilla wire (both operands' sign-safety pads, and iqft's own internal twiddle scratch)
    returning cleanly to |0>. `bloq.tensor_contract()`'s matrix is REVERSED-endian in its own flat `q`
    register (linear-index bit `(qubit_count-1-wid)` = wire `wid`'s value, confirmed by direct
    calibration against a single X gate on wire1 of a 4-qubit register) -- callers doing their own
    tensor-level checks on this bloq must use that convention, not the naive `idx>>wid`.

    Returns `(bloq, meta)`: `bloq` is the single flat-`q` Qualtran Bloq (matching B1-B3's convention --
    no named x/out sub-registers, since `qualtran_backend` always flattens); `meta` gives the composed
    bloq's own wire positions for `x` (real `n` bits, LSB-first) and `out` (real `w_out` bits,
    LSB-first) so a caller can identify the load-bearing wires among the flat register."""
    from . import paths
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend
    from qualtran import BloqBuilder, QBit, Register, Signature
    from qualtran.bloqs.basic_gates import Hadamard
    import numpy as np

    pp_ir_doc = desc["multiply"]["phase_product"]
    iqft_ir_doc = desc["multiply"]["iqft"]
    w_out = int(desc["multiply"]["w_out"])
    n = int(n)

    pp_bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(pp_ir_doc))
    iqft_bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(iqft_ir_doc))

    x_active = pp_ir_doc["operands"]["x"]["active"]
    z_active = pp_ir_doc["operands"]["z"]["active"]
    pp_n = int(pp_ir_doc["qubits"]["count"])
    # Below the recursion floor (small n), phase_product's whole qubit space IS x.active + z.active
    # (reserve empty). Once the recursive Toom-Cook construction fires (larger n), it also allocates
    # per-operand RESERVE qubits -- internal recursion scratch, not part of the logical x/z registers
    # but still real physical wires this bloq's flat q register spans. Found 2026-09-04 at k=2,n=12,
    # w=12 (recursion depth 3, ladder [13,10,8,7]): pp qubit_count=112 but len(x.active)+len(z.active)
    # =13+13=26 -- x.reserve/z.reserve (43 ids each) account for the other 86. The composer's wiring
    # (pp_in = wires[0:pp_n], identity-mapped) does not care about the active/reserve split -- it just
    # needs every local id 0..pp_n-1 accounted for by SOMETHING, so reserve qubits round-trip through
    # pp_bloq exactly like any other of its internal wires (their cleanliness is pp_bloq's own proven
    # responsibility, same trust already placed in the sign-safety pad bits below).
    x_reserve = pp_ir_doc["operands"]["x"].get("reserve", [])
    z_reserve = pp_ir_doc["operands"]["z"].get("reserve", [])
    accounted = set(x_active) | set(x_reserve) | set(z_active) | set(z_reserve)
    if accounted != set(range(pp_n)):
        raise AssertionError(f"phase_product qubit ids {sorted(accounted)} != range({pp_n}) -- "
                             f"operand active+reserve metadata does not cover the full qubit space")
    x_real, x_scratch = x_active[:n], x_active[n:]
    z_real, z_scratch = z_active[:w_out], z_active[w_out:]

    iqft_register = iqft_ir_doc["register"]
    iqft_n = int(iqft_ir_doc["qubits"]["count"])
    if len(iqft_register) != w_out:
        raise AssertionError(f"iqft register width {len(iqft_register)} != w_out {w_out}")
    iqft_anc_local = [j for j in range(iqft_n) if j not in iqft_register]

    total_n = pp_n + len(iqft_anc_local)
    iqft_local_to_composed = {}
    for i, r in enumerate(iqft_register):
        iqft_local_to_composed[r] = z_real[i]
    for offset, j in enumerate(iqft_anc_local):
        iqft_local_to_composed[j] = pp_n + offset

    bb, initial = BloqBuilder.from_signature(Signature([Register("q", QBit(), shape=(total_n,))]))
    wires = list(initial["q"])
    for wid in z_real:                                              # H_reg(out) -- real out bits only
        wires[wid] = bb.add(Hadamard(), q=wires[wid])
    pp_in = np.asarray([wires[j] for j in range(pp_n)], dtype=object)
    pp_out = bb.add(pp_bloq, q=pp_in)
    for j in range(pp_n):
        wires[j] = pp_out[j]
    iqft_in = np.asarray([wires[iqft_local_to_composed[j]] for j in range(iqft_n)], dtype=object)
    iqft_out = bb.add(iqft_bloq.adjoint(), q=iqft_in)               # adjoint: the TRUE inverse QFT -- see docstring
    for j in range(iqft_n):
        wires[iqft_local_to_composed[j]] = iqft_out[j]
    bloq = bb.finalize(q=np.asarray(wires, dtype=object))

    meta = {"x_wires": tuple(x_real), "out_wires": tuple(z_real),
            "x_scratch_wires": tuple(x_scratch), "z_scratch_wires": tuple(z_scratch),
            "iqft_ancilla_wires": tuple(iqft_local_to_composed[j] for j in iqft_anc_local),
            "total_qubits": total_n, "n": n, "w_out": w_out}
    return bloq, meta


def multiply_qiskit(bundle, n: int, c: int, w: int, refresh: bool = False):
    """The Toom-Cook multiply (Phase F: H ; PhaseProduct ; IQFT) as a Qiskit circuit, from the Lean IR
    -- Lean IR -> Qualtran bloq (`compose_multiply_bloq`, index-metadata-driven, adjoint(iqft) --
    verified exact against `|x>|0> -> |x>|c*x mod 2^w>` for every x, see that function's docstring) ->
    Qiskit walker (`qiskit_walker.walk`). Fixed 2026-09-04 -- this used to be a KNOWN-BROKEN positional
    `qc.compose`; `compose_multiply_bloq` replaces that with the real bloq-level composer."""
    from .emit import EmitError, lower_multiply
    from . import qiskit_walker as W
    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    try:
        desc = lower_multiply(doc["k"], int(n), int(c), int(w), refresh=refresh)
    except EmitError as e:
        raise BridgeUnavailable(str(e))
    bloq, meta = compose_multiply_bloq(desc, int(n))
    try:
        qc, layout, ancillas = W.walk(bloq)
    except W.WalkerUnavailable as e:
        raise BridgeUnavailable(str(e))
    return qc, desc


# ------------------------------------------------------------------ §D: the CmodMul <-> Shor register map
def cmodmul_register_map(n: int, eta: float = 1e-3) -> Dict[str, Any]:
    """The `MMRegs` qubit layout `ModMulEmitLower.lean::mmRegs` actually builds, reconstructed here from
    the SAME formula (not re-derived independently — see the header comment at
    `ModMulEmitLower.lean:136-137`, "Registers laid out linearly: ctrl [0], data [1,1+n) with a 1-bit
    OWNED reserve at [1+n], work [2+n, 2+n+ww), flag [last]"). `ww = n + extra_bits(eta)`
    (`forshor_qualtran.bloqs.modmul.extra_bits`, itself pinned bit-for-bit against Lean's `extraBits`
    per that module's docstring). `data_carry` = `data_active + data_reserve` (`data.grow 1`), the
    (n+1)-wide register U2/U3/U4/U5 operate on.

    This is the qubit-map half of handoff #2 §D's CmodMul<->Shor pin. The OTHER half -- comparing this
    layout against an isolated hand-built `CmodMulInPlaceCore` Qiskit circuit -- is NOT closed: no such
    builder exists yet (`ext/qiskit_shor/toomcook_modmult_qiskit.py` has `build_toom_modmult_toffoli` /
    `build_toom_modmult_fourier`, direct Toom-Cook MULTIPLY builders -- a structurally different
    algorithm family from KMY Algorithm-1's 5-step H;CPhaseProd;IQFT controlled-modmul core). Writing
    that builder is real circuit-authoring feature work, not a convention to pin; `bridge_equiv` below
    reports this precisely rather than a generic SKIP."""
    from .bloqs.modmul import extra_bits
    ww = int(n) + extra_bits(float(eta))
    ctrl = 0
    data_active = (1, 1 + int(n))          # half-open [1, 1+n)
    data_reserve = (1 + int(n), 2 + int(n))
    work = (2 + int(n), 2 + int(n) + ww)
    flag = 2 + int(n) + ww
    ws_base = flag + 1
    return {"ctrl": ctrl, "data_active": data_active, "data_reserve": data_reserve,
            "data_carry": (data_active[0], data_reserve[1]), "work": work, "flag": flag,
            "ws_base": ws_base, "n": int(n), "ww": ww, "eta": float(eta)}


def sampled_verify_cmodmul(bundle, n: int, c: int, N: int, eta: float = 1e-3, *,
                            data_values: Optional[Sequence[int]] = None,
                            ctrl_values: Sequence[int] = (0, 1),
                            tol: float = 0.9, refresh: bool = False) -> Dict[str, Any]:
    """Sampled/exact-transition-amplitude verification of `CmodMulInPlaceCore`'s action, closing the
    dense-verification gap flagged in HANDOFF_MODEXP_CMODMUL_VERIFICATION.2026-09-04.md Part 2
    (`MAX_DENSE_N=14` vs cmodmul's real 30+ qubits). Uses the IntState/IntEffect scalar-transition-
    amplitude technique found in that investigation (bind EVERY wire to a fixed classical input AND a
    fixed classical expected output via `ZeroState`/`OneState`/`ZeroEffect`/`OneEffect`, then
    `tensor_contract()` -- a single scalar amplitude, no exponential storage, confirmed fast even at
    39+ qubits) -- generalized here into a reusable check instead of a one-off diagnostic script.

    CALLER'S RESPONSIBILITY, not checked here: `n` must satisfy `2**(n-1) >= N` (checked and reported
    in the result under `width_precondition_ok`, but NOT enforced/raised) -- the 2026-09-05 resolution
    of the `data_in=2` anomaly found that `CmodMulInPlaceCore`'s data operand is read through the
    SIGNED phase-product convention shared with Phase B (`lowgate_ir.py::signed_bit_weight_lsb`, top
    bit negative), so an `n` too small for `N` (satisfying only the documented `N <= ASize(data)` =
    `2**n`) silently corrupts the top half of the residue domain. This function will faithfully report
    FAIL for those residues rather than raising -- that FAIL is a real signal to pick a larger `n`, not
    a bug in this checker.

    For each `(ctrl, data_in)` with `data_in` defaulting to every residue `0..N-1` (exhaustive; pass
    `data_values` to sample instead) and `ctrl` in `ctrl_values` (default both 0 and 1): checks the
    transition amplitude from the classical input (ctrl, data=data_in, work=0, flag=0, all internal
    recursion scratch=0) to the classical output (ctrl unchanged, data=(c*data_in)%N if ctrl else
    data_in, work=0, all internal recursion scratch=0), maximizing over `flag`'s two possible resting
    values (0 or 1 -- genuinely case-dependent per `ModularExponentiation/Defs.lean` step4's own
    comment, not a bug; see the U3/U4 boundary trace in the 2026-09-05 memory entry). A case PASSes
    iff the best-flag probability >= `tol` (default 0.9 -- an empirically-set margin matching what
    n=3/eta=0.5 measures directly, 0.965-0.983; NOT derived from the Lean proof's own unexhibited
    approximation constant K). Overall verdict is PASS iff every case passes.

    Returns {"verdict": PASS|FAIL|SKIP, "width_precondition_ok": bool, "qubits": int, "cases": [...]}."""
    from . import paths
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend
    from qualtran import BloqBuilder, QBit, Register, Signature, Side
    from qualtran.bloqs.basic_gates import ZeroState, OneState, ZeroEffect, OneEffect
    import numpy as np
    from .emit import EmitError, lower_cmodmul

    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    k = int(doc["k"])
    n = int(n); c = int(c); N = int(N)

    if data_values is None:
        data_values = list(range(N))

    try:
        ir_doc = lower_cmodmul(k, n, c, N, eta, refresh=refresh)
    except EmitError as e:
        return {"verdict": "SKIP", "primitive": "cmodmul", "reason": str(e)}

    qubit_count = int(ir_doc["qubits"]["count"])
    reg = cmodmul_register_map(n, eta)
    ctrl_q = reg["ctrl"]
    data_lo, data_hi = reg["data_active"]
    res_lo, res_hi = reg["data_reserve"]
    work_lo, work_hi = reg["work"]
    flag_q = reg["flag"]

    bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(ir_doc))

    def case_probs(ctrl_bit: int, data_in: int, expected_out: int) -> Tuple[float, float]:
        """(prob(flag_out=0), prob(flag_out=1)) in ONE tensor_contract, not two.

        Keeps `ctrl` AND `flag` as free RIGHT legs rather than closing every wire via an effect: a
        fully closed (zero-exposed-leg) network hits a real quimb limitation on this circuit at n>=3
        ("index appears more than twice" -- a hyperedge from a deeply-recursing And/CNOT uncompute
        pattern once k=2's recursion actually fires), confirmed by reproducing it directly. `ctrl` is
        provably unchanged by every gate here (only ever a diagonal control in CPhaseProdUsing), so
        exposing it costs nothing and doubles as a sanity check (the ctrl!=ctrl_bit half of the output
        must be ~0). Exposing `flag` too reads off BOTH flag hypotheses from one contraction instead of
        rebuilding/recontracting the whole (~12s at n=3) circuit twice.
        """
        bb, _ = BloqBuilder.from_signature(Signature(
            [Register("ctrl_out", QBit(), side=Side.RIGHT), Register("flag_out", QBit(), side=Side.RIGHT)]
        ))
        wires = [None] * qubit_count
        for w in range(qubit_count):
            if w == ctrl_q:
                wires[w] = bb.add(OneState() if ctrl_bit else ZeroState())
            elif data_lo <= w < data_hi:
                bit = (data_in >> (w - data_lo)) & 1
                wires[w] = bb.add(OneState() if bit else ZeroState())
            else:
                wires[w] = bb.add(ZeroState())  # reserve, work, flag, and all recursion scratch start clean
        q_out = bb.add(bloq, q=np.asarray(wires, dtype=object))
        ctrl_out_wire = flag_out_wire = None
        for w in range(qubit_count):
            if w == ctrl_q:
                ctrl_out_wire = q_out[w]
                continue
            if w == flag_q:
                flag_out_wire = q_out[w]
                continue
            if data_lo <= w < data_hi:
                exp_bit = (expected_out >> (w - data_lo)) & 1
            else:
                exp_bit = 0  # reserve, work, and recursion scratch expected clean again
            bb.add(OneEffect() if exp_bit else ZeroEffect(), q=q_out[w])
        sv = np.asarray(bb.finalize(ctrl_out=ctrl_out_wire, flag_out=flag_out_wire).tensor_contract())
        sv = sv.reshape(2, 2)  # [ctrl_out, flag_out]
        other = float(np.sum(np.abs(sv[1 - ctrl_bit, :]) ** 2))
        if other > 1e-6:
            raise AssertionError(f"ctrl leaked: prob(ctrl={1 - ctrl_bit})={other} (expected ~0)")
        return float(abs(sv[ctrl_bit, 0]) ** 2), float(abs(sv[ctrl_bit, 1]) ** 2)

    cases = []
    all_pass = True
    for ctrl_bit in ctrl_values:
        for data_in in data_values:
            expected_out = (c * data_in) % N if ctrl_bit else data_in
            probs = case_probs(ctrl_bit, data_in, expected_out)
            best_flag = int(np.argmax(probs))
            prob = float(probs[best_flag])
            ok = prob >= tol
            all_pass = all_pass and ok
            cases.append({"ctrl": ctrl_bit, "data_in": data_in, "expected_data_out": expected_out,
                          "flag_out": best_flag, "prob": prob, "pass": ok})

    return {"verdict": "PASS" if all_pass else "FAIL", "primitive": "cmodmul", "k": k,
            "params": {"n": n, "c": c, "N": N, "eta": eta}, "tol": tol,
            "width_precondition_ok": bool((1 << (n - 1)) >= N), "qubits": qubit_count, "cases": cases}


def compose_modexp_bloq(bundle, n: int, a: int, N: int, tbits: int, delta: float = 1e-2):
    """Build the single, faithful Qualtran Bloq for modular exponentiation (Phase D:
    `Shor.modExpIdealSteps` = `X-init(y) ;; prod_{e<tbits} ctrl-e CmodMulInPlaceCore(mult_e, N)`),
    composed from `lower_modexp`'s own Lean-computed multiplier schedule (`multipliers[e] = a^(2^e) %
    N` -- read from the IR, never recomputed in Python, per this phase's own "composition STRUCTURE is
    ForShor's, not Python's" rule) and `tbits` real per-step `lower_cmodmul` IR documents. `lower_modexp`
    embeds only ONE representative `step_template` (for the first step) -- structurally identical
    across steps (its own "And-count is (c,N)-free" claim, confirmed directly: qubit_count is identical
    across different multiplier values at fixed (n,N,eta)) but with genuinely different internal phase
    constants per step, so each step needs its own real `lower_cmodmul` call, not a naive reuse of the
    template.

    Wiring uses `cmodmul_register_map` (n, eta) -- the ALREADY-established formula this codebase uses
    for cmodmul's logical layout (`ctrl=[0]`, `data_active=[1,1+n)`, everything from `ws_base` on is
    that step's own internal QFT/phase-product recursion scratch) -- same n/eta for every step, so the
    SAME register_map applies to all `tbits` steps:
      * `data_active` (the running accumulator `y`, X-initialised to 1 before step 0, LSB-first) is
        wired to the SAME composed wires across ALL steps -- the one register every step updates in
        place, exactly the KMY repeated-squaring accumulator.
      * `ctrl` (each step's single control qubit -- CmodMulInPlaceCore's controlled-phase-products are
        ALREADY controlled inside its own IR; this composer does not add extra control logic, only
        exposes that existing control wire externally) is wired to a DISTINCT composed "exponent
        register" qubit `x_e`, one per step -- these are the eventual QPE-side control qubits, exposed
        here as plain external wires (matching how B1-B4's composers exposed the flat `q` register
        without inventing named sub-registers).
      * everything else (`data_reserve`, `work`, `flag`, all internal scratch beyond `ws_base`) gets
        FRESH composed wires, distinct per step -- each step's own scratch, trusted clean by the same
        reasoning already established for phase_product/QFT/multiply's internal recursion ancilla.

    NOT verified via exact tensor_contract/statevector simulation, unlike the multiply composer --
    even the SMALLEST cmodmul step found (n=2, N=3, eta=0.5, the most aggressively truncated precision
    this codebase's `extra_bits` formula allows) is already 39 qubits, and cmodmul's internal QFT/
    phase-product recursion overhead does not shrink meaningfully smaller at any realistic (n,N,eta) --
    2^39 is far beyond dense `tensor_contract` or even a single dense state vector (~8.8 TB), the SAME
    structural ceiling `faithful_convert_equiv`'s existing cmodmul leg already documents (dense
    infeasible past ~14 qubits; needs a sampled/diagonal leg, not a dense one -- pre-existing,
    already-flagged, unresolved scope, not introduced by this composer). This composer is therefore
    verified STRUCTURALLY (every physical qubit of every step accounted for by
    ctrl/data_active/"everything else", asserted, no gaps, no guessed positions -- the same
    completeness check used for phase_product's active+reserve accounting in `compose_multiply_bloq`)
    but NOT numerically at any in-budget scale.

    Returns `(bloq, meta)`: `bloq` is the single flat-`q` Qualtran Bloq (matching B1-B4's convention).
    `meta` gives the composed wire positions for the exponent register (`ctrl_wires`, one per step,
    e=0..tbits-1) and the accumulator register (`data_wires`, `n` wide, LSB-first)."""
    from . import paths
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend
    from qualtran import BloqBuilder, QBit, Register, Signature
    from qualtran.bloqs.basic_gates import XGate
    import numpy as np
    from .emit import lower_cmodmul, lower_modexp

    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    k = int(doc["k"])
    n = int(n)
    tbits = int(tbits)

    modexp_doc = lower_modexp(k, n, a, N, tbits, delta)
    multipliers = modexp_doc["modexp"]["multipliers"]
    eta_frac = modexp_doc["modexp"]["eta"]
    eta = float(eta_frac["num"]) / float(eta_frac["den"])
    if len(multipliers) != tbits:
        raise AssertionError(f"modexp multiplier schedule length {len(multipliers)} != tbits {tbits}")

    step_docs = [lower_cmodmul(k, n, int(c), int(N), eta) for c in multipliers]
    step_bloqs = [qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(d)) for d in step_docs]
    reg_map = cmodmul_register_map(n, eta)  # same (n, eta) -> same logical layout for every step

    ctrl_local = reg_map["ctrl"]
    data_lo, data_hi = reg_map["data_active"]  # [data_lo, data_hi) = [1, 1+n)
    ws_base = reg_map["ws_base"]

    # composed layout: [exponent x_0..x_{tbits-1}] [accumulator y_0..y_{n-1}] [per-step scratch...]
    ctrl_wires = list(range(tbits))
    data_wires = list(range(tbits, tbits + n))
    total_n = tbits + n

    step_local_to_composed = []
    for e, sdoc in enumerate(step_docs):
        qn = int(sdoc["qubits"]["count"])
        if qn <= ws_base:
            raise AssertionError(f"step {e}: qubit_count {qn} <= ws_base {ws_base} -- register_map assumption broken")
        mapping = {ctrl_local: ctrl_wires[e]}
        for i, j in enumerate(range(data_lo, data_hi)):
            mapping[j] = data_wires[i]
        for j in range(qn):
            if j in mapping:
                continue
            mapping[j] = total_n
            total_n += 1
        if set(mapping.keys()) != set(range(qn)):
            raise AssertionError(f"step {e}: mapping does not cover local qubit range 0..{qn - 1}")
        step_local_to_composed.append(mapping)

    bb, initial = BloqBuilder.from_signature(Signature([Register("q", QBit(), shape=(total_n,))]))
    wires = list(initial["q"])

    wires[data_wires[0]] = bb.add(XGate(), q=wires[data_wires[0]])  # X-init accumulator y to |1> (LSB-first)

    for sbloq, mapping in zip(step_bloqs, step_local_to_composed):
        qn = len(mapping)
        step_in = np.asarray([wires[mapping[j]] for j in range(qn)], dtype=object)
        step_out = bb.add(sbloq, q=step_in)
        for j in range(qn):
            wires[mapping[j]] = step_out[j]

    bloq = bb.finalize(q=np.asarray(wires, dtype=object))
    flag_local = reg_map["flag"]
    flag_wires = tuple(mapping[flag_local] for mapping in step_local_to_composed)
    meta = {"ctrl_wires": tuple(ctrl_wires), "data_wires": tuple(data_wires),
            "flag_wires": flag_wires,  # one per step, composed-wire position of that step's own `flag`
            "total_qubits": total_n, "n": n, "a": a, "N": N, "tbits": tbits,
            "multipliers": list(multipliers), "eta": eta}
    return bloq, meta


def sampled_verify_modexp(bundle, n: int, a: int, N: int, tbits: int, delta: float = 1e-2, *,
                           exponents: Optional[Sequence[int]] = None, tol: float = 0.9) -> Dict[str, Any]:
    """Sampled/exact-transition-amplitude verification of the COMPOSED modexp ladder
    (`compose_modexp_bloq`), extending `sampled_verify_cmodmul`'s technique (2026-09-05) to close the
    "structural only, not numerical" gap that function's own docstring flags. Same IntState/IntEffect
    scalar-transition-amplitude idea, generalized to `tbits` exponent/flag qubits at once: expose EVERY
    `ctrl_wires[e]` and `flag_wires[e]` (from `compose_modexp_bloq`'s meta) as free RIGHT legs in a
    SINGLE tensor_contract per exponent value `E` (reading off all `2**tbits` flag-hypotheses from one
    contraction, not one per hypothesis -- the same folding trick that halved cmodmul's own call count).

    CALLER'S RESPONSIBILITY, not checked here: same width precondition as `sampled_verify_cmodmul`
    (`2**(n-1) >= N`) -- each step IS a `lower_cmodmul` call, so it inherits that primitive's exact
    same signed-phase-product data-operand requirement. Reported under `width_precondition_ok`, not
    enforced/raised.

    For each exponent value `E` in `exponents` (default: every value `0..2**tbits-1`, i.e. every
    control-register basis state -- feasible since `tbits` is necessarily small, this is the QPE control
    register): sets `ctrl_wires[e] = bit e of E` (the accumulator `y` is always X-initialised to 1 by
    the composer itself, not a variable input here), and checks the transition amplitude to `data_wires
    = a**E % N` (Python's own modpow -- comparing the WHOLE composed ladder's action against the
    textbook target, not re-deriving it from the per-step multipliers `lower_modexp` already computed),
    maximizing over the `2**tbits` possible flag-resting-value combinations (one case-dependent flag
    per step, same reasoning as cmodmul's own flag). PASS per-case iff the best-flag probability >= tol
    (default 0.9, same margin `sampled_verify_cmodmul` uses).

    Returns {"verdict": PASS|FAIL, "width_precondition_ok": bool, "qubits": int, "cases": [...]}."""
    from qualtran import BloqBuilder, QBit, Register, Signature, Side
    from qualtran.bloqs.basic_gates import ZeroState, OneState, ZeroEffect, OneEffect
    import numpy as np
    from .emit import EmitError

    n = int(n); a = int(a); N = int(N); tbits = int(tbits)
    try:
        bloq, meta = compose_modexp_bloq(bundle, n, a, N, tbits, delta)
    except EmitError as e:
        return {"verdict": "SKIP", "primitive": "modexp", "reason": str(e)}
    qubit_count = int(meta["total_qubits"])
    ctrl_wires = list(meta["ctrl_wires"])
    data_wires = list(meta["data_wires"])
    flag_wires = list(meta["flag_wires"])
    ctrl_set, data_set, flag_set = set(ctrl_wires), set(data_wires), set(flag_wires)

    if exponents is None:
        exponents = list(range(1 << tbits))

    def case_flag_probs(E: int, expected_out: int) -> np.ndarray:
        regs = ([Register(f"ctrl_out_{e}", QBit(), side=Side.RIGHT) for e in range(tbits)] +
                [Register(f"flag_out_{e}", QBit(), side=Side.RIGHT) for e in range(tbits)])
        bb, _ = BloqBuilder.from_signature(Signature(regs))
        wires = [None] * qubit_count
        for w in range(qubit_count):
            if w in ctrl_set:
                bit = (E >> ctrl_wires.index(w)) & 1
                wires[w] = bb.add(OneState() if bit else ZeroState())
            else:
                wires[w] = bb.add(ZeroState())  # data (X-init happens inside the bloq), flag, scratch
        q_out = bb.add(bloq, q=np.asarray(wires, dtype=object))
        outs: Dict[str, Any] = {}
        for w in range(qubit_count):
            if w in ctrl_set:
                outs[f"ctrl_out_{ctrl_wires.index(w)}"] = q_out[w]
                continue
            if w in flag_set:
                outs[f"flag_out_{flag_wires.index(w)}"] = q_out[w]
                continue
            exp_bit = (expected_out >> data_wires.index(w)) & 1 if w in data_set else 0
            bb.add(OneEffect() if exp_bit else ZeroEffect(), q=q_out[w])
        sv = np.asarray(bb.finalize(**outs).tensor_contract()).reshape((2,) * (2 * tbits))
        ctrl_idx = tuple((E >> e) & 1 for e in range(tbits))
        block = sv[ctrl_idx]  # shape (2,)*tbits over flag bits
        leaked = float(np.sum(np.abs(sv) ** 2) - np.sum(np.abs(block) ** 2))
        if leaked > 1e-6:
            raise AssertionError(f"ctrl leaked: prob(ctrl!=E={E})={leaked} (expected ~0)")
        return np.abs(block) ** 2

    cases = []
    all_pass = True
    for E in exponents:
        expected_out = pow(a, E, N)
        flag_probs = case_flag_probs(E, expected_out)
        best_idx = np.unravel_index(int(np.argmax(flag_probs)), flag_probs.shape)
        prob = float(flag_probs[best_idx])
        ok = prob >= tol
        all_pass = all_pass and ok
        cases.append({"E": E, "expected_data_out": expected_out, "flag_out": list(map(int, best_idx)),
                      "prob": prob, "pass": ok})

    return {"verdict": "PASS" if all_pass else "FAIL", "primitive": "modexp",
            "params": {"n": n, "a": a, "N": N, "tbits": tbits, "delta": delta}, "tol": tol,
            "width_precondition_ok": bool((1 << (n - 1)) >= N), "qubits": qubit_count, "cases": cases}


def compose_orderfinding_bloq(bundle, n: int, a: int, N: int, L: Optional[int] = None, delta: float = 1e-2):
    """Build the single, faithful Qualtran Bloq for Shor order-finding (Phase E:
    `Shor.orderFindingApprox` = `H_reg(x) ;; initY1(y) ;; modExp(a,N,x,y,work,flag) ;; IQFT(x)`,
    `Implementation/Shor/Defs.lean:402`).

    2026-09-05: what the 2026-09-04 coverage map flagged as a blocking DESIGN DECISION ("extract phase
    estimation as a standalone primitive, or only build it as part of full Shor") turns out to already
    be resolved by ForShor's own construction, not something this composer has to decide: the QPE
    Hadamard frame (`H_reg(x)`) and `initY1(y)` are both Clifford/trivial, and `initY1` is ALREADY what
    `compose_modexp_bloq` does internally (X-init the accumulator to 1) -- there is no separately
    meaningful "phase estimation" circuit here beyond a Hadamard frame wrapped around the
    ALREADY-composable modexp (Phase D) and IQFT (Phase A). This composer therefore just wires those
    THREE already-working pieces together, mirroring `compose_multiply_bloq`'s own
    H + PhaseProduct + `adjoint(IQFT)` pattern (same "iqft" primitive, same adjoint-needed finding):
      * `H` on every one of the `L` exponent-register (`x`) wires, BEFORE the modexp ladder --
        `compose_modexp_bloq`'s own `ctrl_wires` are exactly `x` (already documented in that function's
        docstring as "the eventual QPE-side control qubits, exposed here as plain external wires").
      * `compose_modexp_bloq(bundle, n, a, N, tbits=L, delta)` reused AS-IS (not reimplemented) --
        its `data_wires`/`flag_wires` meta pass straight through.
      * `iqft_bloq.adjoint()` (from `lower_qft(k, L, Fraction(1,1))`, the SAME "iqft is a forward QFT,
        needs .adjoint()" finding `compose_multiply_bloq` established 2026-09-04) on the SAME `x` wires,
        AFTER the modexp ladder -- wired via the SAME operand-index-driven technique (never guessed
        positions) `compose_multiply_bloq` uses for its own iqft leg.

    `lower_orderfinding` is called first purely as a REAL-LEAN existence/consistency check (confirms `L`
    matches ForShor's own `exponentWidth`/`canonical_L` choice and that the descriptor is emittable) --
    the actual bloq is built from `compose_modexp_bloq`/`lower_qft` directly rather than by parsing
    `lower_orderfinding`'s embedded `modexp`/`iqft` sub-documents, which is safe because Lean's emission
    is pure/deterministic: calling `lower_modexp`/`lower_qft` again with the IDENTICAL (k,n,a,N,L,delta)
    reproduces the same embedded documents byte-for-byte (same artifact-cache keying by parameters).

This composer is verified both STRUCTURALLY (every physical qubit of `x`, modexp's own wires, and
    iqft's ancilla accounted for; wiring-completeness style consistent with every other composer in
    this file) AND NUMERICALLY, since 2026-09-08, via `sampled_verify_orderfinding`/
    `lean_order_distribution` -- order-finding's own correctness statement is a PROBABILITY
    DISTRIBUTION over `x` (peaked near multiples of `2^L/r` for the multiplicative order `r` of `a`
    mod `N`), not a single classical transition like cmodmul/modexp's sampled legs, so it is NOT
    verified via `tensor_contract()` on this bloq directly (that hits a hard scaling wall by L=3/n=5
    already) but via a closed-form classical ladder + exact IQFT computation
    (`lean_order_distribution`) that reads the SAME underlying Lean emission's multiplier schedule
    this function's own `meta["multipliers"]` carries -- via a direct, lightweight
    `emit.lower_orderfinding` call, not by calling this function (which additionally builds the full
    Qualtran bloq graph this computation doesn't need) -- see `lean_order_distribution`'s docstring
    for the full derivation.

    Returns `(bloq, meta)`: `bloq` is the single flat-`q` Qualtran Bloq (matching every other composer's
    convention). `meta` gives `x_wires` (the `L`-wide exponent/QPE register, LSB-first), `data_wires`
    (the `n`-wide accumulator, passed through from `compose_modexp_bloq`), `flag_wires` (one per modexp
    step, passed through), and `iqft_ancilla_wires` (iqft's own internal twiddle-recursion scratch)."""
    from . import paths
    paths.setup_legacy_sys_path()
    import lowgate_ir, qualtran_backend
    from qualtran import BloqBuilder, QBit, Register, Signature
    from qualtran.bloqs.basic_gates import Hadamard
    from fractions import Fraction
    import numpy as np
    from .emit import EmitError, lower_orderfinding, lower_qft
    from .bloqs.orderfinding import canonical_L

    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    k = int(doc["k"])
    n = int(n); a = int(a); N = int(N)
    L = int(L) if L is not None else canonical_L(N)

    try:
        of_doc = lower_orderfinding(k, n, a, N, L, delta)  # REAL-LEAN existence/consistency check only
    except EmitError as e:
        raise BridgeUnavailable(str(e))
    L_lean = int(of_doc["orderfinding"]["exponent_width_L"])
    if L_lean != L:
        raise AssertionError(f"L mismatch: passed {L}, Lean's own descriptor emitted {L_lean}")

    modexp_bloq, modexp_meta = compose_modexp_bloq(bundle, n, a, N, L, delta)
    x_wires = list(modexp_meta["ctrl_wires"])  # the L-wide exponent/QPE register
    modexp_n = int(modexp_meta["total_qubits"])

    iqft_doc = lower_qft(k, L, Fraction(1, 1))
    iqft_bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(iqft_doc))
    iqft_register = iqft_doc["register"]
    if len(iqft_register) != L:
        raise AssertionError(f"iqft register width {len(iqft_register)} != L {L}")
    iqft_n = int(iqft_doc["qubits"]["count"])
    iqft_anc_local = [j for j in range(iqft_n) if j not in iqft_register]

    total_n = modexp_n + len(iqft_anc_local)
    iqft_local_to_composed = {}
    for i, r in enumerate(iqft_register):
        iqft_local_to_composed[r] = x_wires[i]
    for offset, j in enumerate(iqft_anc_local):
        iqft_local_to_composed[j] = modexp_n + offset

    bb, initial = BloqBuilder.from_signature(Signature([Register("q", QBit(), shape=(total_n,))]))
    wires = list(initial["q"])
    for wid in x_wires:                                             # H_reg(x) -- the QPE frame
        wires[wid] = bb.add(Hadamard(), q=wires[wid])
    modexp_in = np.asarray([wires[j] for j in range(modexp_n)], dtype=object)
    modexp_out = bb.add(modexp_bloq, q=modexp_in)
    for j in range(modexp_n):
        wires[j] = modexp_out[j]
    iqft_in = np.asarray([wires[iqft_local_to_composed[j]] for j in range(iqft_n)], dtype=object)
    iqft_out = bb.add(iqft_bloq.adjoint(), q=iqft_in)               # adjoint: the TRUE inverse QFT
    for j in range(iqft_n):
        wires[iqft_local_to_composed[j]] = iqft_out[j]
    bloq = bb.finalize(q=np.asarray(wires, dtype=object))

    meta = {"x_wires": tuple(x_wires), "data_wires": tuple(modexp_meta["data_wires"]),
            "flag_wires": tuple(modexp_meta["flag_wires"]),
            "iqft_ancilla_wires": tuple(iqft_local_to_composed[j] for j in iqft_anc_local),
            "total_qubits": total_n, "L": L, "n": n, "a": a, "N": N, "delta": delta,
            "multipliers": modexp_meta["multipliers"]}
    return bloq, meta


def _multiplicative_order(a: int, N: int) -> int:
    """The least r>=1 with a**r % N == 1. Raises if a is not coprime to N (no order exists)."""
    import math
    if math.gcd(a, N) != 1:
        raise AssertionError(f"a={a} is not coprime to N={N}; no multiplicative order exists")
    r, cur = 1, a % N
    while cur != 1:
        cur = (cur * a) % N
        r += 1
        if r > N:
            raise AssertionError(f"order of a={a} mod N={N} not found within N={N} steps (unexpected)")
    return r


def lean_order_distribution(bundle, n: int, a: int, N: int, L: Optional[int] = None,
                            delta: float = 1e-2, *, precision: int = 40) -> Dict[str, Any]:
    """The QPE readout distribution `P(x)` for ForShor's OWN composed order-finding circuit
    (`compose_orderfinding_bloq` / `Shor.orderFindingApprox`, `Implementation/Shor/Defs.lean:402`),
    computed WITHOUT tensor-contracting the circuit -- the trick `sampled_verify_orderfinding` used
    to use (dense `tensor_contract()` over the full composed circuit) hits a hard wall: at `L=3, n=5`
    (456 qubits) cotengra's contraction-path optimizer already needs a 2^44-element intermediate
    tensor.

    THE TRICK (exploited from `ext/qiskit_shor/toomcook_order_finding_qiskit.toom_order_distribution`,
    which uses it for the independent hand-built circuit): order-finding's own definition chains
    controlled multiplications by CLASSICALLY KNOWN constants --
    `modExpApproxStepsValid` (`ModularExponentiation/Defs.lean:429-451`) applies, at step `e`,
    `CmodMulInPlaceCore c N ctrl data work flag` with `c := (a ^ (2 ^ e)) % N`. So for a computational
    basis input `|e>` on the exponent register (accumulator starting at 1), the ladder's classical
    action is exactly `w(e) = a^e mod N` -- computable by chaining the known multipliers, with NO
    dependence on the modexp step's own qubit count (the thing that actually blows up). This is
    ForShor's own reason "we don't need the controlled-modmul CIRCUIT for this" -- the multiplier
    schedule alone (read directly from `emit.lower_orderfinding`'s embedded modexp descriptor --
    the SAME value `compose_orderfinding_bloq`'s `meta["multipliers"]` carries, never recomputed --
    see `compose_modexp_bloq`'s docstring) already carries everything needed.

    THE IQFT: applied as the EXACT matrix `Minv[y,e] = exp(-2*pi*i*y*e/2**L) / sqrt(2**L)`, built
    from SymPy roots of unity (not extracted numerically from the Qualtran `iqft_bloq`). This is the
    adjoint of ForShor's own measured forward-QFT amplitude convention
    (`amplitude(input=v,output=j) = 2^(-w/2)*exp(+2*pi*i*j*v/2^w)`, independently verified to machine
    precision in an earlier session -- natural bit order, no permutation needed) and is backed by
    `lowerQFT_correct` (`QFT/Main.lean:17`, sorry-free) -- i.e. this substitutes the ALREADY-PROVEN
    closed form for the Lean IQFT's action instead of re-deriving it by simulating the (ancilla-
    bearing) `iqft_bloq` itself.

    Roots of unity are computed exactly ONCE per residue (`D = 2**L` of them, not `D**2`) via
    `sympy.exp`/`sympy.N` at `precision` decimal digits, then reused via `(y*e) % D` indexing for the
    O(D^2) aggregation (done in NumPy, not SymPy, for tractability at realistic `L`) -- keeping the
    VALUES exact-by-construction while keeping the bulk arithmetic fast.

    Deliberately calls `emit.lower_orderfinding` DIRECTLY (a pure Lean IR emission, milliseconds)
    rather than `compose_orderfinding_bloq` (which additionally builds the full Qualtran bloq graph
    for every one of the `L` real cmodmul steps -- ~2 MINUTES even at `L=4, n=5` in practice, purely
    from constructing circuit objects this function never uses). This is the concrete instance of
    "we don't need the controlled-modmul CIRCUIT for this": only the multiplier schedule the Lean
    descriptor already carries is needed. Consequently `total_qubits` is not reported here (would
    require the full composer's own wiring accounting to state correctly) -- see
    `compose_orderfinding_bloq` for that if actually needed.

    Returns {"dist": np.ndarray (len 2**L), "probs_x": {x: prob} (sparse, >1e-12), "L": L, "n": n,
    "a": a, "N": N, "multipliers": [...], "w": [w(0), w(1), ...]}."""
    import numpy as np
    import sympy
    from . import paths as _paths
    _paths.setup_legacy_sys_path()  # qualtran must be on sys.path before importing .bloqs (below)
    from .emit import lower_orderfinding
    from .bloqs.orderfinding import canonical_L

    n = int(n); a = int(a); N = int(N)
    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    k = int(doc["k"])
    L_use = int(L) if L is not None else canonical_L(N)
    desc = lower_orderfinding(k, n, a, N, L_use, delta)  # raises EmitError if no forshor_emit binary
    L = int(desc["orderfinding"]["exponent_width_L"])
    multipliers = [int(c) for c in desc["orderfinding"]["modexp"]["modexp"]["multipliers"]]
    if len(multipliers) != L:
        raise AssertionError(f"multiplier schedule length {len(multipliers)} != L {L}")

    D = 1 << L
    w = [0] * D
    for e in range(D):
        acc = 1
        for i in range(L):
            if (e >> i) & 1:
                acc = (acc * multipliers[i]) % N
        expected = pow(a, e, N)
        if acc != expected:
            raise AssertionError(
                f"Lean multiplier ladder mismatch at e={e}: chaining the emitted multipliers gives "
                f"{acc}, but a^e mod N = {expected} -- the Lean-emitted schedule no longer matches "
                f"Shor.orderFindingApprox's own modExpApproxStepsValid definition")
        w[e] = acc

    # D-th roots of unity, computed exactly once each (SymPy exact -> high-precision numeric).
    two_pi_i = 2 * sympy.pi * sympy.I
    zeta = np.array(
        [complex(sympy.N(sympy.exp(-two_pi_i * sympy.Integer(kk) / D), precision)) for kk in range(D)],
        dtype=complex)

    y_idx = np.arange(D).reshape(D, 1)
    e_idx = np.arange(D).reshape(1, D)
    Minv = zeta[(y_idx * e_idx) % D] / np.sqrt(D)          # exact-rooted D x D IQFT matrix

    data_dim = 1 << n
    psi = np.zeros((D, data_dim), dtype=complex)
    for e in range(D):
        psi[e, w[e]] = 1.0 / np.sqrt(D)
    post = Minv @ psi
    dist = np.sum(np.abs(post) ** 2, axis=1)

    probs_x = {x: float(p) for x, p in enumerate(dist) if p > 1e-12}
    return {"dist": dist, "probs_x": probs_x, "L": L, "n": n, "a": a, "N": N,
            "multipliers": multipliers, "w": w}


def _orderfinding_verdict(a: int, N: int, L: int, probs_x: Dict[int, float], *, tol: float = 0.9,
                          peak_window: int = 1) -> Dict[str, Any]:
    """Shared acceptance bar for a `lean_order_distribution`-shaped `probs_x`: PASS iff (1) mass
    `>= tol` sits within `peak_window` bins of the theoretically-predicted peaks
    `round(s*2**L/r)` for the true order `r`, and (2) continued-fraction recovery on the dominant
    measured values actually recovers `r`. Factored out so `sampled_verify_orderfinding` and
    `lean_shor_factor` apply IDENTICAL acceptance criteria to a distribution they may have computed
    once and shared, rather than two independent redefinitions.

    `peak_window=1` (not 0): when `r` does not evenly divide `2**L` (the general case -- only
    power-of-2 orders land exactly on a bin), QPE's actual guarantee is concentration NEAR each
    ideal peak, not exactly on it -- textbook off-grid leakage, confirmed empirically 2026-09-08
    (N=21, a=2, r=6, L=9: exact-bin peak_mass=0.79, but the immediate neighbors carry the rest --
    a +-1 window recovers 0.93). Measuring exactly-on-bin mass would make `lean_verified` spuriously
    False for a correctly-behaving circuit whenever the order happens not to be a power of 2 -- this
    was never exercised before because the pre-existing `tensor_contract()` wall prevented this
    check from ever completing at a realistic `L`."""
    from fractions import Fraction

    r = _multiplicative_order(a, N)
    D = 1 << L
    expected_peaks = sorted({round(s * D / r) % D for s in range(r)})
    windowed_peaks = sorted({(p + d) % D for p in expected_peaks
                             for d in range(-peak_window, peak_window + 1)})
    peak_mass = sum(probs_x.get(x, 0.0) for x in windowed_peaks)

    recovered_orders = set()
    for x, p in probs_x.items():
        if p < 1e-6:
            continue
        frac = Fraction(x, D).limit_denominator(N)
        if frac.denominator > 0:
            recovered_orders.add(frac.denominator)
    order_recovered = r in recovered_orders

    ok = (peak_mass >= tol) and order_recovered
    return {"verdict": "PASS" if ok else "FAIL", "true_order": r, "expected_peaks": expected_peaks,
            "peak_window": peak_window, "peak_mass": peak_mass,
            "recovered_orders": sorted(recovered_orders), "order_recovered": order_recovered}


def sampled_verify_orderfinding(bundle, n: int, a: int, N: int, L: Optional[int] = None,
                                 delta: float = 1e-2, *, tol: float = 0.9) -> Dict[str, Any]:
    """Numeric verification of `compose_orderfinding_bloq`'s QPE action, via
    `lean_order_distribution`'s closed-form ladder+IQFT computation (see that function's docstring
    for the full derivation and why this replaced a `tensor_contract()` approach that hit a hard
    scaling wall at L=3/n=5 already).

    Verifies TWO things, both required for PASS (`_orderfinding_verdict`, shared with
    `lean_shor_factor`'s own certification):
      1. `P(x)` is concentrated (>= `tol` total mass) on the theoretically-predicted peak locations
         `round(s * 2**L / r)` for `s = 0..r-1` (`r` computed directly via `_multiplicative_order`).
      2. Continued-fraction recovery (`fractions.Fraction(x, 2**L).limit_denominator(N)`) applied to
         the DOMINANT measured `x` values actually recovers `r`.

    CALLER'S RESPONSIBILITY, not checked here: same width precondition as cmodmul/modexp
    (`2**(n-1) >= N`); `L` should be large enough that `2**L` is a good rational-approximation host
    for `r` (small/power-of-2 `r` are exact; the continued-fraction step handles the general case).

    Returns {"verdict": PASS|FAIL|SKIP, "true_order": r, "probs_x": {x: prob}, "expected_peaks": [...],
    "peak_mass": float, "total_prob": float, "recovered_orders": [...], "order_recovered": bool}."""
    from .emit import EmitError

    n = int(n); a = int(a); N = int(N)
    try:
        result = lean_order_distribution(bundle, n, a, N, L, delta)
    except EmitError as e:
        return {"verdict": "SKIP", "primitive": "orderfinding", "reason": str(e)}

    L_ = int(result["L"])
    probs_x = result["probs_x"]
    verdict = _orderfinding_verdict(a, N, L_, probs_x, tol=tol)

    return {"primitive": "orderfinding", "params": {"n": n, "a": a, "N": N, "L": L_, "delta": delta},
            "tol": tol, "width_precondition_ok": bool((1 << (n - 1)) >= N),
            "probs_x": probs_x,
            "total_prob": float(sum(probs_x.values())), **verdict}


# ------------------------------------------------------------------ certification bridge: Lean IR == isolated Qiskit
def bridge_equiv(bundle, n: int, c: int, N: int, eta: float = 1e-3, tol: float = 1e-9) -> Dict[str, Any]:
    """Certify that the Lean-IR-driven controlled modular multiply and the isolated hand-built Toom-Cook
    Qiskit circuit realise the SAME unitary at small n — the link that would make the tested end-to-end
    factoring Lean-verified. `lean_qc` is now `cmodmul_qiskit`'s Qualtran-derived circuit (Lean IR ->
    Qualtran bloq -> Qiskit walker), not the independent `ir_to_qiskit` lowering. The comparison target
    (`ext/qiskit_shor/toomcook_modmult_qiskit.build_controlled_modmul`) is itself an independently
    hand-authored circuit, not Qualtran-derived -- and per handoff #2 §D it does not exist yet anyway
    (see `cmodmul_register_map`'s docstring), so this always degrades to SKIP today regardless of
    `lean_qc`'s source. Returns {"verdict": PASS|FAIL|SKIP, ...}; SKIP if Qiskit/binary absent, if
    `cmodmul_qiskit` raises (WalkRefusal or a compiler gap), or if the isolated builder is missing."""
    reg_map = cmodmul_register_map(n, eta)
    try:
        _require_qiskit()
        lean_qc, _ = cmodmul_qiskit(bundle, n, c, N, eta)
    except BridgeUnavailable as e:
        return {"verdict": "SKIP", "reason": str(e), "register_map": reg_map}
    except Exception as e:
        # e.g. NotImplementedError from py/qiskit_backend._compile_node on an unregistered Prim tag
        # (CMP_GE_CONST/CSUB_CONST/CMP_LT_NW) -- never let a compiler gap crash the caller.
        return {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}", "register_map": reg_map}
    try:
        from qiskit.quantum_info import Operator

        _legacy()  # ext/qiskit_shor onto sys.path (paths.setup_legacy_sys_path + ext insert)
        import toomcook_modmult_qiskit as TM  # ext/qiskit_shor/
        iso = TM.build_controlled_modmul(c, N, n) if hasattr(TM, "build_controlled_modmul") else None
        if iso is None:
            return {"verdict": "SKIP", "register_map": reg_map,
                    "reason": ("no isolated `build_controlled_modmul` exists to bridge against (handoff #2 "
                               "§D): ext/qiskit_shor/toomcook_modmult_qiskit.py has build_toom_modmult_toffoli"
                               "/build_toom_modmult_fourier -- direct Toom-Cook MULTIPLY builders, a "
                               "structurally different algorithm from KMY Algorithm-1's 5-step "
                               "H;CPhaseProd;IQFT controlled-modmul core. The qubit-map HALF of the pin is "
                               "closed (see register_map, reconstructed from ModMulEmitLower.lean:136-137's "
                               "own layout formula); the isolated-builder half needs new circuit-authoring "
                               "work, not just a convention fix -- never a false PASS in the meantime")}
        iso_qc = iso[0] if isinstance(iso, tuple) else iso
        same = Operator(lean_qc).equiv(Operator(iso_qc), atol=tol) if lean_qc.num_qubits == iso_qc.num_qubits else False
        return {"verdict": "PASS" if same else "FAIL", "n": int(n), "c": int(c), "N": int(N),
                "lean_qubits": lean_qc.num_qubits, "iso_qubits": iso_qc.num_qubits, "register_map": reg_map}
    except Exception as e:  # pragma: no cover - convention/env dependent
        return {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e} (bridge convention to pin against the first real emission)"}


# ------------------------------------------------------------------ full Shor: Lean-driven, small n
def lean_shor_factor(N: int, bundle=None, k: int = 2, a: Optional[int] = None, L: Optional[int] = None,
                     delta: float = 1e-2, tries: int = 12, seed: int = 0) -> Dict[str, Any]:
    """Factor N end-to-end using ForShor's OWN composed order-finding circuit
    (`compose_orderfinding_bloq` / `Shor.orderFindingApprox`), not an independent hand-built engine.

    2026-09-08 rewrite: previously delegated the actual factoring run to
    `ext/qiskit_shor/toomcook_shor_qiskit.shor_factor` (a fully independent hand-authored circuit),
    with `lean_verified` claimed only via `bridge_equiv` -- a bridge to an isolated controlled-modmul
    circuit that (a) has no `build_controlled_modmul` implementation to compare against at all (see
    `bridge_equiv`'s own docstring) and (b) even if it did, certified a synthetic `N = 2^certify_n-1`
    stand-in, never the real `N` being factored -- so `lean_verified` was essentially always False.
    Rewritten to mirror `toomcook_shor_qiskit.shor_factor`'s own loop structure (base selection,
    `gcd(a,N)` short-circuit, retries) but source the QPE distribution from
    `lean_order_distribution` (this module) instead of `toom_order_distribution` -- the classical
    order-recovery/factor-extraction helpers (`_order_from_distribution`, `factors_from_order`) are
    REUSED directly from `toomcook_shor_qiskit`, not reimplemented, since that post-processing is
    pure classical number theory, engine-independent. `lean_verified` is now an honest per-run
    certification (`_orderfinding_verdict`, shared with `sampled_verify_orderfinding`) against
    ForShor's real composed circuit at the ACTUAL `(a, N, L)` being factored.

    `n` (data-register width) is `N.bit_length() + 1`, which always satisfies the `2^(n-1) >= N`
    precondition `compose_modexp_bloq`/`compose_orderfinding_bloq` need (`N.bit_length()` alone is
    NOT always enough -- e.g. N=15 needs n=5, not 4). `L` defaults to
    `bloqs.orderfinding.canonical_L(N)`, ForShor's own "standard QPE readout width" choice. REAL-LEAN
    + Qiskit gated (`lean_order_distribution`'s Lean emission call) -> SKIP otherwise."""
    import math
    import random
    from . import paths as _paths
    _paths.setup_legacy_sys_path()  # qualtran must be on sys.path before importing .bloqs (below)
    from .bloqs.orderfinding import canonical_L
    from .emit import EmitError

    N = int(N)
    if N % 2 == 0:
        # `lean_verified` was True here. Nothing Lean-derived runs on this path -- N is even, the
        # factors fall out of arithmetic, and `_orderfinding_verdict` (the thing `lean_verified`
        # is documented to mean) is never called. Reporting True made a check that did not run
        # read as a check that passed, on the project's headline claim. The FACTORS are correct
        # and the verdict stays PASS; what is false is that Lean certified anything about them.
        return {"N": N, "factors": (2, N // 2), "a": None, "r": None, "route": "even",
                "lean_verified": False, "certification": None,
                "why_not_certified": "classical short-circuit (N even): no order-finding ran, so "
                                     "there is no Lean certification to report",
                "verdict": "PASS (classical short-circuit — not Lean-certified)"}
    if bundle is None:
        return {"verdict": "SKIP", "reason": "no bundle for certification", "N": N, "lean_verified": False}

    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    bundle_k = int(doc["k"])
    if int(k) != bundle_k:
        raise AssertionError(f"lean_shor_factor k={k} != bundle k={bundle_k}: single-k violated")

    try:
        _require_qiskit()
        _legacy()
        import toomcook_shor_qiskit as S  # ext/qiskit_shor/ -- reused for classical post-processing only
    except Exception as e:
        return {"verdict": "SKIP", "reason": f"qiskit/ext unavailable: {e}", "N": N, "lean_verified": False}

    n = N.bit_length() + 1
    L_use = int(L) if L is not None else canonical_L(N)
    rng = random.Random(seed)
    bases = [int(a)] if a is not None else [rng.randrange(2, N) for _ in range(tries)]

    attempts = []
    for base in bases:
        g = math.gcd(base, N)
        if g > 1:
            # Same correction as the even branch: a lucky base makes this pure number theory.
            # It is a genuine factorization and worth keeping -- Shor's algorithm has this
            # short-circuit by design -- but no circuit is built and nothing is certified, so
            # `lean_verified` must not say True. This fires more often than it looks: when the
            # caller passes no `a`, the bases are random, and notebook 07's N = 21 drew a = 14
            # (gcd 7), taking this path and previously reporting `lean_verified: True`.
            return {"N": N, "factors": (g, N // g), "a": base, "r": None, "route": "gcd(a,N)",
                    "L": L_use, "attempts": attempts, "lean_verified": False,
                    "certification": None,
                    "why_not_certified": f"classical short-circuit (gcd({base},{N}) = {g} > 1): "
                                         f"no order-finding ran, so there is no Lean "
                                         f"certification to report",
                    "verdict": "PASS (classical short-circuit — not Lean-certified)"}
        try:
            result = lean_order_distribution(bundle, n, base, N, L_use, delta)
        except EmitError as e:
            return {"verdict": "SKIP", "reason": str(e), "N": N, "lean_verified": False,
                    "attempts": attempts}
        r = S._order_from_distribution(base, N, L_use, result["dist"])
        attempts.append({"a": base, "r": r})
        if r is None:
            continue
        fac = S.factors_from_order(base, N, r)
        if fac:
            verify = _orderfinding_verdict(base, N, L_use, result["probs_x"])
            lean_verified = verify["verdict"] == "PASS"
            return {"N": N, "factors": tuple(sorted(fac)), "a": base, "r": r, "L": L_use,
                    "route": "order-finding", "attempts": attempts, "certification": verify,
                    "lean_verified": lean_verified,
                    "verdict": "PASS" if lean_verified else "FACTORED (order-finding check did not PASS)"}
    return {"N": N, "factors": None, "a": None, "r": None, "route": "exhausted",
            "L": L_use, "attempts": attempts, "lean_verified": False, "verdict": "no factor"}


# ------------------------------------------------------------------ B1-B4 showcase: draw the real circuits
def build_showcase_circuits(bundle, out_dir: Path, *, n: int = 2, w: int = 2, c: int = 3,
                            phi: Fraction = Fraction(1, 8)) -> Dict[str, Any]:
    """B1-B4 proof-of-principle: each primitive's REAL Qiskit circuit, built directly from the
    Lean-certified Qualtran bloq through the SAME walker (`bloq_to_qiskit_circuit` -- Lean IR ->
    Qualtran bloq -> Qiskit, recursing through the bloq's own decomposition) -- demonstrating the
    converter is generalizable across primitive shapes, not hand-tuned per case. No independent
    reconstruction anywhere in this path (`ir_to_qiskit` is not used).

    Kept at n=w=2 (2-bit) throughout by default, deliberately: this is a small-scale circuit-build step,
    separate from and downstream of the symbolic resource estimate (which stays free in n via the bloq's
    own closed-form cost -- it never re-invokes the emitter or the walker per swept n). Saves, per
    circuit: an OpenQASM 3 file, an ASCII `circuit.draw()` text file, and a JSON-safe summary (qubits,
    ancilla, gate counts, depth).

    B4 (multiply) is a single composed Qualtran bloq since 2026-09-04, via `compose_multiply_bloq`
    (phase_product's two operands + `iqft`'s own adjoint, wired from the IR documents' own operand/
    register metadata -- not the old positional composition `multiply_qiskit` used to have, and not two
    separately-drawn pieces). Its action is independently verified exact (`|x>|0> -> |x>|c*x mod 2^w>`
    for every x, tensor_contract, see that function's docstring) -- this is a real, single, faithful
    multiply circuit, drawn through the SAME walker as B1-B3."""
    from .emit import lower_multiply, lower_qft, lower_rec, lower_cmodmul
    from . import qiskit_walker as W

    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    k = int(doc["k"])
    out_dir.mkdir(parents=True, exist_ok=True)

    def _save_qc(name: str, qc, ancillas) -> Dict[str, Any]:
        # QASM3, not QASM2: qc.draw() and the walker emit `cp` (controlled-phase), and Qiskit's own
        # qasm2.dumps()/qelib1.inc pair is inconsistent about it (dumps() emits `cp`, but qelib1.inc
        # does not define it, so qasm2.loads() on our own qasm2.dumps() output fails to re-parse --
        # checked directly). qasm3.dumps()/stdgates.inc round-trips correctly.
        from qiskit import qasm3
        draw_text = str(qc.draw(output="text"))
        qasm_path = out_dir / f"{name}.qasm3"
        draw_path = out_dir / f"{name}.draw.txt"
        # Deliberately retain dumps() for the legacy default path.  Besides retaining
        # its historical output bytes, this isolates the opt-in emit-only streaming
        # behavior below from established B1-B7 callers.
        qasm_path.write_text(qasm3.dumps(qc), encoding="utf-8")
        draw_path.write_text(draw_text, encoding="utf-8")
        return {"qubits": qc.num_qubits, "ancilla": len(ancillas), "gate_counts": dict(qc.count_ops()),
                "depth": qc.depth(), "qasm_path": str(qasm_path), "draw_path": str(draw_path),
                "draw": draw_text}

    def _save(name: str, ir_doc: Dict[str, Any]) -> Dict[str, Any]:
        qc, _layout, ancillas = bloq_to_qiskit_circuit(ir_doc)
        return _save_qc(name, qc, ancillas)

    results: Dict[str, Any] = {"k": k, "n": n, "w": w, "c": c, "primitives": {}}

    try:  # B1 -- one-level PhaseProduct (Lean IR -> Qualtran -> Qiskit, via schedule_expand)
        # `forshor_qualtran.bloqs.phase_product` imports `qualtran` at module level; unlike
        # `emit.py`'s lowering functions (used by B2-B7 below), nothing else on this path
        # guarantees the vendored Qualtran tree is already on sys.path by the time this import
        # runs (found directly: this raised `ModuleNotFoundError: No module named 'qualtran'`
        # in an otherwise-working session where B2-B7 all imported qualtran fine).
        from . import paths as _paths
        _paths.setup_legacy_sys_path()
        from .bloqs.phase_product import concrete_ir_bloq as pp_concrete_ir_bloq
        _bloq, ir_pp = pp_concrete_ir_bloq(bundle, n, phi, "tight")
        results["primitives"]["B1_phase_product"] = {"verdict": "BUILT", **_save(f"k{k}_B1_phase_product", ir_pp)}
    except Exception as e:
        results["primitives"]["B1_phase_product"] = {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}

    try:  # B2 -- Recursive PhaseProduct
        ir_rec = lower_rec(k, n, phi)
        results["primitives"]["B2_recursive_phase_product"] = {"verdict": "BUILT", **_save(f"k{k}_B2_recursive_phase_product", ir_rec)}
    except Exception as e:
        results["primitives"]["B2_recursive_phase_product"] = {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}

    try:  # B3 -- QFT (recursive twiddle)
        ir_qft = lower_qft(k, w, Fraction(1, 1))
        results["primitives"]["B3_qft"] = {"verdict": "BUILT", **_save(f"k{k}_B3_qft", ir_qft)}
    except Exception as e:
        results["primitives"]["B3_qft"] = {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}

    try:  # B4 -- Toom-Cook multiply: single composed bloq (compose_multiply_bloq), same walker as B1-B3
        desc = lower_multiply(k, n, c, w)
        mul_bloq, mul_meta = compose_multiply_bloq(desc, n)
        qc, _layout, ancillas = W.walk(mul_bloq)
        results["primitives"]["B4_multiply"] = {"verdict": "BUILT", "composer_meta": mul_meta,
                                                 **_save_qc(f"k{k}_B4_multiply", qc, ancillas)}
    except Exception as e:
        results["primitives"]["B4_multiply"] = {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}

    try:  # B5 -- Controlled modular multiply: same lowering path sampled_verify_cmodmul uses
        # (lower_cmodmul -> parse_ir -> build_qualtran_bloq), walked through the SAME W.walk as B4/B6/B7
        # rather than re-deriving a circuit -- this is the exact bloq the 2026-09-05 width-precondition
        # fix and sampled_verify_cmodmul check numerically; drawing it here adds no new construction path.
        from . import paths as _paths
        _paths.setup_legacy_sys_path()
        import lowgate_ir as _lowgate_ir, qualtran_backend as _qualtran_backend
        cm_n, cm_c, cm_N, cm_eta = 3, 2, 3, 0.5
        cm_ir_doc = lower_cmodmul(k, cm_n, cm_c, cm_N, cm_eta)
        cmodmul_bloq = _qualtran_backend.build_qualtran_bloq(_lowgate_ir.parse_ir(cm_ir_doc))
        qc, _layout, ancillas = W.walk(cmodmul_bloq)
        results["primitives"]["B5_cmodmul"] = {
            "verdict": "BUILT", "register_map": cmodmul_register_map(cm_n, cm_eta),
            **_save_qc(f"k{k}_B5_cmodmul", qc, ancillas)}
    except Exception as e:
        results["primitives"]["B5_cmodmul"] = {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}

    try:  # B6 -- Modular exponentiation: single composed bloq (compose_modexp_bloq), same walker
        # kept at the smallest viable size deliberately (see compose_modexp_bloq docstring): a single
        # cmodmul step is already ~39+ qubits (its own internal QFT/phase-product recursion scratch),
        # unlike B1-B4 this cannot be shown at n=w=2 scale and stay small -- n=3,N=3,tbits=2 (2 steps)
        # is the smallest case with a real Lean-computed multiplier schedule of length > 1 that ALSO
        # satisfies the width precondition `2**(n-1) >= N` (n=2 does not for N=3 -- see the 2026-09-05
        # memory resolution; showcasing a mathematically-wrong-by-construction circuit here would be
        # misleading even though this function only draws it, it doesn't numerically verify it).
        me_n, me_a, me_N, me_tbits, me_delta = 3, 2, 3, 2, 0.5
        modexp_bloq, modexp_meta = compose_modexp_bloq(bundle, me_n, me_a, me_N, me_tbits, me_delta)
        qc, _layout, ancillas = W.walk(modexp_bloq)
        results["primitives"]["B6_modexp"] = {"verdict": "BUILT", "composer_meta": modexp_meta,
                                              **_save_qc(f"k{k}_B6_modexp", qc, ancillas)}
    except Exception as e:
        results["primitives"]["B6_modexp"] = {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}

    try:  # B7 -- Order-finding / full Shor: compose_orderfinding_bloq (H-frame + B6 + IQFT), same walker
        of_n, of_a, of_N, of_L, of_delta = 3, 2, 3, 2, 0.5
        of_bloq, of_meta = compose_orderfinding_bloq(bundle, of_n, of_a, of_N, of_L, of_delta)
        qc, _layout, ancillas = W.walk(of_bloq)
        results["primitives"]["B7_orderfinding"] = {"verdict": "BUILT", "composer_meta": of_meta,
                                                     **_save_qc(f"k{k}_B7_orderfinding", qc, ancillas)}
    except Exception as e:
        results["primitives"]["B7_orderfinding"] = {"verdict": "SKIP", "reason": f"{type(e).__name__}: {e}"}

    return results


def build_showcase_circuits_emit_only(bundle, out_dir: Path, *, n: int, w: Optional[int] = None,
                                      c: Optional[int] = None, a: int = 2, N: Optional[int] = None,
                                      L: Optional[int] = None, delta: float = 1e-2,
                                      template_mode: bool = False) -> Dict[str, Any]:
    """Emit real walker-built order-finding without a dense-simulation leg.

    This separate, opt-in API leaves :func:`build_showcase_circuits`' legacy defaults
    and return shape intact.  It requires REAL-LEAN descriptors (no mirror fallback),
    never calls ``tensor_contract``/``Operator``/statevector verification, and labels
    all output as structurally faithful but NOT dense-verified beyond n=14.  Its
    coherent frame is NOT the minimal-qubit RSA/Shor circuit.

    ``template_mode`` emits one real controlled-``a^(2^j)`` modular-multiplication
    template (round zero) and a JSON QPE schedule.  It is the actual repeating
    structure, not a full unroll; the schedule retains each round's distinct
    Lean-derived multiplier.  A real caller supplies a REAL-LEAN bundle and explicit
    ``N``/``L`` when targeting a particular instance.  ``w``/``c`` are retained only
    for CLI symmetry with the legacy showcase and are recorded, not used.
    """
    from . import qiskit_walker as W
    from .emit import lower_cmodmul, lower_modexp

    doc = bundle.doc if hasattr(bundle, "doc") else bundle
    k = int(doc["k"])
    n = int(n)
    if n < 3:
        raise ValueError("emit-only order-finding needs n >= 3")
    # A deterministic, odd demo modulus lets the CLI expose just --n.  Production
    # callers should supply their actual N and L; both are recorded below.
    N = int(N) if N is not None else (1 << (n - 1)) - 1
    L = int(L) if L is not None else n
    a = int(a)
    if N < 3 or N >= (1 << (n - 1)):
        raise ValueError("N must satisfy 3 <= N < 2**(n - 1) for the emitted data width")

    parameters = {"k": k, "n": n, "w": w, "c": c, "a": a, "N": N, "L": L, "delta": delta}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Lean supplies schedule and precision without constructing the whole circuit.
    modexp_doc = lower_modexp(k, n, a, N, L, delta)
    modexp = modexp_doc["modexp"]
    multipliers = [int(value) for value in modexp["multipliers"]]
    eta_doc = modexp["eta"]
    eta = float(eta_doc["num"]) / float(eta_doc["den"])
    if len(multipliers) != L:
        raise AssertionError(f"Lean multiplier schedule length {len(multipliers)} != L {L}")
    schedule = [
        {"round": j, "template_id": "controlled_modmul_step", "controlled_power": 1 << j,
         "multiplier": multiplier}
        for j, multiplier in enumerate(multipliers)
    ]
    common = {
        "k": k, "n": n, "w": w, "c": c, "parameters": parameters,
        "mode": "template" if template_mode else "emit_only_full_unroll",
        "note": "The actual repeating structure, not a full unroll." if template_mode else _EMIT_ONLY_NOTE,
        "honesty": _EMIT_ONLY_NOTE, "dense_verified": False,
        "coherent_frame_minimal_qubit": False, "qpe_schedule": schedule,
    }

    if template_mode:
        from . import paths as _paths
        _paths.setup_legacy_sys_path()
        import lowgate_ir, qualtran_backend

        template_ir = lower_cmodmul(k, n, multipliers[0], N, eta)
        template_bloq = qualtran_backend.build_qualtran_bloq(lowgate_ir.parse_ir(template_ir))
        preflight = preflight_bloq_emission(template_bloq)
        name = f"k{k}_emit_only_n{n}_controlled_modmul_template"
        preflight_path = _write_emit_only_preflight(out_dir, name, preflight, parameters, "template")
        template_qc, _layout, ancillas = W.walk(template_bloq)
        artifact = _write_template_sidecar(
            out_dir, name=name, template_qc=template_qc, ancillas=ancillas, preflight=preflight,
            schedule=schedule, parameters=parameters,
        )
        return {**common, "primitives": {"B7_orderfinding_template": {
            "verdict": "BUILT", "template_round": 0, "template_multiplier": multipliers[0],
            "preflight_path": preflight_path, **artifact,
        }}}

    # Structural graph first, then call-graph preflight, then walker gates.  No dense leg.
    orderfinding_bloq, composer_meta = compose_orderfinding_bloq(bundle, n, a, N, L, delta)
    preflight = preflight_bloq_emission(orderfinding_bloq)
    name = f"k{k}_emit_only_n{n}_orderfinding"
    preflight_path = _write_emit_only_preflight(out_dir, name, preflight, parameters, "emit_only_full_unroll")
    qc, _layout, ancillas = W.walk(orderfinding_bloq)
    qasm_path = out_dir / f"{name}.qasm3"
    qasm_bytes = write_qasm3_stream(qc, qasm_path)
    primitive = {
        "verdict": "BUILT", "composer_meta": composer_meta, "preflight": preflight,
        "preflight_path": preflight_path,
        "qubits": qc.num_qubits, "ancilla": len(ancillas), "gate_counts": dict(qc.count_ops()),
        "depth": qc.depth(), "qasm_path": str(qasm_path), "qasm_bytes": qasm_bytes,
        "note": _EMIT_ONLY_NOTE,
    }
    return {**common, "primitives": {"B7_orderfinding_emit_only": primitive}}
