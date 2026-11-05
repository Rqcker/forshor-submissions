"""Threads the copied sibling modules (ext/, see ext/COPY_ROSTER.md) into the one-click line.

Each function is self-contained, states its provenance tier, and degrades to a labelled SKIP
when a dependency (qiskit, quimb) is absent — it never fabricates a number.

    measured_sweep()          ext/data/mult_subroutine.2026-08-27.jsonl → rows (EMPIRICAL, 263/264 exact vs QECGatesCost)
    figure_measured_overlay() V1' : Lean-structured symbolic curves + the measured lanes, per arity
    qft_routes()              Qualtran textbook / approximate / phase-gradient / QFTViaPhaseProduct / LeanQFT / reference ladder
    phase_depth()             ext/skeletons/phase_depth_census (t_model) → histogram + gadget benefit at n = 64..2048
    order_finding_cost()      ext/skeletons/full_orderfinding_cost → full logical T, gadgets ON vs OFF
    reversible_modexp_line()  ext/shared/reversible_modexp → independent Toffoli count & depth vs n (+ self-check vs pow)
    full_multiply_cost()      ext/bundle/qualtran FourierToomMultiply / ToomCookMultiply / ZeroAnc (needs qiskit)
    qiskit_gates()            verify_bloq_to_qiskit, tledger_reconcile, Toom-engine Shor factoring 15/21 (needs qiskit)
    gallery()                 ext/images/decomp + briefs, bundle examples
"""
from __future__ import annotations

import importlib
import json
import math
from functools import lru_cache
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import constants, paths
from .provenance import Tier

EXT = paths.PIPE_ROOT / "ext"
DATA = EXT / "data"
IMAGES = EXT / "images"


def _ext_paths() -> None:
    for d in (EXT / "skeletons", EXT / "shared", EXT / "bundle" / "qualtran", EXT / "bundle" / "qiskit",
              EXT / "qiskit_shor", paths.PIPE_ROOT / "skeletons", paths.PIPE_ROOT / "skeletons" / "shor_graph",
              paths.PIPE_ROOT / "py", paths.PIPE_ROOT / "qiskit"):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))


def _have(mod: str, attr: Optional[str] = None) -> bool:
    """True iff `mod` imports AND (if given) exposes `attr` — guards against the legacy `qiskit/` FOLDER
    being picked up as a namespace package when the real qiskit is not installed."""
    try:
        m = importlib.import_module(mod)
        return attr is None or hasattr(m, attr)
    except Exception:
        return False


def have_qiskit() -> bool:
    return _have("qiskit", "QuantumCircuit")


# ------------------------------------------------------------------ measured sweep (DATA)
ARITY_K = {"direct": None, "karatsuba": 2, "toom3": 3, "toom4": 4, "toom5": 5, "toom6": 6, "toom7": 7, "toom8": 8}


def measured_sweep(lane: str = "off", mode: str = "exact") -> List[Dict[str, Any]]:
    """Rows of the 2026-08-27 measured sweep. THE COSTED OBJECT IS THE PHASEPRODUCT SUBROUTINE
    (|x>|y> → exp(2πi·turn·xy)|x>|y>), NOT a full multiply (MULT_SUBROUTINE_RESULTS §0): the KMY skeleton
    `PhaseProduct` with one top Toom-k split, Karatsuba below, recursed to 2-bit base cases, at the deep
    dyadic turn (0x9E3779B9|1)/2^n with exact (mod-2) leaf classification; T at 11 T/rotation =
    `t_total__qualtran_default`; T-depth is a MODEL.
    Provenance: EMPIRICAL — `verify_mult_cost_mirror_witness.json`: 263/264 exact vs QECGatesCost/QubitCount."""
    p = DATA / "mult_subroutine.2026-08-27.jsonl"
    out = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("lane") == lane and r.get("mode") == mode and r.get("ok") and r.get("n", 0) > 0:
                out.append({"arity": r["arity"], "k": ARITY_K.get(r["arity"], r.get("k")), "n": r["n"],
                            "qubits": r["qubits"], "and": r["and_bloq"], "rotation": r["rotation"],
                            "T": r["t_total__qualtran_default"],
                            "T_eps1e-11": r.get("t_total__gridsynth_eps1e-11"),
                            "T_depth": r.get("t_depth_parallel__qualtran_default")})
    return out


def measured_report(lane: str = "off", mode: str = "exact") -> Dict[str, Any]:
    """Crossovers and fitted slopes from the stdlib reporter (ext/skeletons/mult_subroutine_report.py)."""
    _ext_paths()
    import mult_subroutine_report as R
    rows = R.load(DATA / "mult_subroutine.2026-08-27.jsonl")
    idx = R.index(rows)
    ns = [16, 64, 256, 1024, 2048, 4096, 8192]
    arities = [a for a in ARITY_K]
    out: Dict[str, Any] = {"lane": lane, "mode": mode}
    for metric in ("tcount", "qubits", "tdepth"):
        try:
            win = R.winners(idx, lane, mode, metric, ns, arities)
            out[metric] = {"winners": win, "crossovers": R.crossovers(win),
                           "slopes": {a: R.slope(idx, lane, mode, metric, a, ns) for a in arities}}
        except Exception as exc:  # reporter internals may differ per metric
            out[metric] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def figure_measured_overlay(bundles, out_dir: Path, lanes: Sequence[str] = ("off", "gadget")) -> Path:
    """V1': two panels. Left — 11 T/rotation: the Lean-structured symbolic T(n) (tight, one-level)
    with the measured lane=off skeleton multiply. Right — ε-aware synthesis (gridsynth ε=1e-11, tier E4
    fits from the sweep): lane=off vs lane=gadget, where the phase-gradient gadget actually pays."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from . import estimate

    out_dir.mkdir(parents=True, exist_ok=True)
    rows = estimate.sweep(bundles, [16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192], policies=("tight",))
    cs = constants.c_syn(1e-11)
    rows_eps = estimate.sweep(bundles, [16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192], policies=("tight",),
                              ts_per_rotation=cs)
    fig, axs = plt.subplots(1, 2, figsize=(15, 6))
    cmap = plt.get_cmap("viridis")
    ks = sorted({b.k for b in bundles})
    b_of = {b.k: b for b in bundles}.get
    col = {k: cmap(i / max(1, len(ks) - 1)) for i, k in enumerate(ks)}
    marks = {"off": "o", "opt": "s", "gadget": "^"}
    for ax, rws, key, title in ((axs[0], rows, "T", "11 T per rotation (Qualtran default)"),
                                (axs[1], rows_eps, "T_eps1e-11", f"ε-aware synthesis: c_syn(1e-11) = {cs:.0f} T per rotation")):
        for k in ks:
            pts = sorted((r["n"], r["T"]) for r in rws if r["k"] == k)
            ax.plot(*zip(*pts), "-", color=col[k], label=f"k={k} PP(n) generic φ" + ("" if getattr(b_of(k), "certifies", True) else " [MIRROR]"))
        use_lanes = ("off",) if key == "T" else lanes
        for lane in use_lanes:
            for r in measured_sweep(lane, "exact"):
                if r["k"] in col and r.get(key) is not None:
                    ax.plot(r["n"], r[key], marks[lane], color=col[r["k"]], mfc="none", ms=5)
            ax.plot([], [], marks[lane], color="gray", mfc="none", label=f"measured recursive skeleton, lane={lane}")
        ns = sorted({r["n"] for r in rws})
        ax.plot(ns, [constants.SCHOOLBOOK_PREFACTOR * n * n for n in ns], "k:", label="schoolbook 15n²")
        ax.set_xscale("log"); ax.set_yscale("log"); ax.grid(True, which="both", alpha=.3)
        ax.set_xlabel("n (bits)"); ax.set_ylabel("T count"); ax.set_title(title, fontsize=10)
        ax.legend(fontsize=7, ncol=2)
    fig.suptitle("V1b — PP(n) one-level curves vs the measured recursive PhaseProduct skeleton (ext/data 2026-08-27)", fontsize=10)
    f = out_dir / "V1b_measured_overlay.png"
    fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return f


# ------------------------------------------------------------------ QFT routes (Qualtran)
def qft_routes(bundle, ws: Sequence[int] = (8, 16, 32, 64), eps: float = 1e-10) -> List[Dict[str, Any]]:
    """T per route at c_syn(ε) T/rotation (as QFT_ROUTE_COMPARISON does) AND at 11 T/rotation."""
    _ext_paths()
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    from qualtran.bloqs.qft.qft_text_book import QFTTextBook
    from qualtran.bloqs.qft.approximate_qft import ApproximateQFT
    from qualtran.bloqs.qft.qft_phase_gradient import QFTPhaseGradient
    from .bloqs import LeanQFT, ReferenceQFT
    try:
        from kmy_qft_via_phaseproduct import QFTViaPhaseProduct
    except Exception:
        QFTViaPhaseProduct = None
    cs = constants.c_syn(eps)
    rows = []
    for w in ws:
        routes = {"A textbook": QFTTextBook(w),
                  "B approx(log2)": ApproximateQFT(w, max(math.ceil(math.log2(w)), 1)),
                  "C phase-gradient": QFTPhaseGradient(w),
                  "E reference ladder (oneclick)": ReferenceQFT(w),
                  "F LeanQFT (E4 rule, tight W)": LeanQFT.from_bundle(bundle, w, "tight")}
        if QFTViaPhaseProduct is not None:
            routes["D QFTViaPhaseProduct (skeleton twin)"] = QFTViaPhaseProduct(w, "balanced")
        for name, bl in routes.items():
            try:
                d = get_cost_value(bl, QECGatesCost()).asdict()
                T11 = 4 * d.get("and_bloq", 0) + 4 * d.get("toffoli", 0) + d.get("t", 0) + 11 * d.get("rotation", 0)
                Teps = 4 * d.get("and_bloq", 0) + 4 * d.get("toffoli", 0) + d.get("t", 0) + cs * d.get("rotation", 0)
                rows.append({"w": w, "route": name, "rotation": int(d.get("rotation", 0)), "and": int(d.get("and_bloq", 0)),
                             "T_11": float(T11), f"T_eps{eps:.0e}": float(Teps), "exact": not name.startswith("B")})
            except Exception as exc:
                rows.append({"w": w, "route": name, "error": f"{type(exc).__name__}: {str(exc)[:60]}"})
    return rows


# ------------------------------------------------------------------ phase-depth census
def phase_depth(ns: Sequence[int] = (64, 256, 1024, 2048), eps: float = 1e-10, cutoff: Optional[int] = 40) -> List[Dict[str, Any]]:
    """Exact depth histogram of the multiply's non-Clifford sites (t_model.thist) and the gadget benefit."""
    _ext_paths()
    import t_model
    import phase_depth_census as P
    out = []
    for n in ns:
        full = t_model.thist(n, 2, Fraction(P.A, 1 << n) % 1)
        h = full if cutoff is None else P.banded(full, cutoff)
        a = P.analyse(h, eps)
        a.update({"n": n, "cutoff": cutoff, "kept_fraction": (a["R"] / sum(full.values())) if a["R"] else 0.0,
                  "hist": dict(sorted(h.items()))})
        out.append(a)
    return out


def figure_phase_depth(rows: Sequence[Dict[str, Any]], out_dir: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for r in rows:
        h = r["hist"]
        if h:
            ax.plot(list(h.keys()), list(h.values()), "-o", ms=3, label=f"n={r['n']}: median {r['median']}, max {r['max']}")
    ax.set_xlabel("phase depth t (rotation angle 2π c / 2^t)"); ax.set_ylabel("sites"); ax.set_yscale("log")
    ax.set_title(f"V8 — phase-depth census, banded cutoff {rows[0]['cutoff']}", fontsize=9)
    ax.legend(fontsize=8); ax.grid(alpha=.3)
    f = out_dir / "V8_phase_depth_census.png"
    fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return f


# ------------------------------------------------------------------ order finding (L4)
def order_finding_cost(ns: Sequence[int] = (1024, 2048, 3072, 4096), eps: float = 1e-10, M: int = 2, R: int = 1) -> List[Dict[str, Any]]:
    """Full order-finding logical T, gadgets ON vs OFF (ext/skeletons/full_orderfinding_cost, anchored
    to KMY's measured n=2048 estimator run; the ON/OFF RATIO is the robust output, absolute counts are
    order-of-magnitude). Tier: MODEL anchored on EMPIRICAL."""
    _ext_paths()
    import full_orderfinding_cost as F
    rows = []
    for n in ns:
        r = F.full_cost(n, eps, M, R)
        r["n"] = n; r["eps"] = eps
        rows.append(r)
    return rows


# ------------------------------------------------------------------ independent Toffoli line
#: Largest `n` `reversible_modexp_line` will attempt. It BUILDS A CONCRETE GATE-LEVEL CIRCUIT and
#: then runs a permutation simulator over it, so its cost grows like n^2.6 in time and with the
#: gate count in memory. Measured on the benchmark machine, 2026-09-22:
#:
#:     n        4     8     12     16     20     24     28     32      64
#:     time  0.08s 0.58s  1.49s  3.48s  6.48s 12.4s  18.8s  27.3s    >120s
#:     toffoli 25k  162k   503k   1.14M  2.17M  3.69M  5.79M  8.56M   --   (9.9 GB, unfinished)
#:
#: The function's own default is `ns=(4, 6, 8, 12, 16)`, i.e. it was always meant for tiny n.
#: Notebook 00 was calling it once per point of a grid that runs to n = 50,000, where the circuit
#: would have ~10^13 gates -- that is what drove the notebook to 8.6 GB and a 70-minute timeout.
#: A reference LINE on a log axis needs a handful of small-n points, not the whole grid.
REVERSIBLE_MODEXP_MAX_N = 32


@lru_cache(maxsize=256)
def _reversible_modexp_row(n: int, a: int, N: Optional[int]) -> Dict[str, Any]:
    """One row, memoized: several figures ask for the same `n`, and each build is seconds."""
    import reversible_modexp as RM
    Nn = N or ((1 << n) - 1 if n > 2 else 3)
    if math.gcd(a, Nn) != 1:
        Nn -= 2
    circ, regs, layout = RM.build_modexp(a, Nn, n)
    dec = RM.decompose_to_toffoli(circ)
    summ = dec.summary()
    depth = RM.toffoli_depth(dec)
    ok = True
    for x in (0, 1, 3, (1 << n) - 1):
        got, info = RM.run_modexp(a, Nn, n, x)
        if got != pow(a, x, Nn) or not info["ancilla_clean"] or not info["x_preserved"]:
            ok = False
    return {"n": n, "a": a, "N": Nn, "qubits": regs.size + (dec.n_qubits - circ.n_qubits),
            "toffoli": summ["toffoli_2ctrl"], "cnot": summ["cnot_1ctrl"], "toffoli_depth": depth,
            "selfcheck_vs_pow_and_clean": ok}


def reversible_modexp_line(ns: Sequence[int] = (4, 6, 8, 12, 16), a: int = 7,
                           N: Optional[int] = None,
                           max_n: int = REVERSIBLE_MODEXP_MAX_N) -> List[Dict[str, Any]]:
    """Stdlib reversible modexp (ext/shared/reversible_modexp): Toffoli count and Toffoli depth vs n,
    with a permutation-simulator self-check against pow(a, x, N) at each n (independent of Qualtran).

    REFUSES `n > max_n` rather than trying. This is a concrete circuit builder, not a closed form:
    at n = 64 it had consumed 9.9 GB and not finished. Raising here means a caller that wraps it in
    `try/except` (the notebooks do, to draw it as an optional baseline) simply gets no point beyond
    the cap, instead of exhausting the machine. Pass a larger `max_n` deliberately if you mean it.
    """
    _ext_paths()
    rows = []
    for n in ns:
        if n > max_n:
            raise ValueError(
                f"reversible_modexp_line: n={n} exceeds max_n={max_n}. This builds a CONCRETE "
                f"gate-level circuit and simulates it; cost grows ~n^2.6 (n=32 is 27 s and 8.6M "
                f"Toffolis, n=64 exceeded 9.9 GB without finishing). It is a small-n reference "
                f"line -- its own default is ns=(4, 6, 8, 12, 16). Raise max_n only on purpose.")
        rows.append(_reversible_modexp_row(int(n), int(a), N))
    return rows




# ------------------------------------------------------------------ full multiply (bundle, needs qiskit)
def full_multiply_cost(ks: Sequence[int] = (2, 3), ns: Sequence[int] = (4, 6, 8, 12, 16)) -> List[Dict[str, Any]]:
    """QECGatesCost of the bundle's Qualtran full-multiply bloqs (FourierToomMultiply = the in-place
    QFT·cPP·IQFT Draper accumulate; ToomCookMultiply = out-of-place Toffoli). Tier: EMPIRICAL. Needs qiskit
    (the bundle shares its Toom tables with the Qiskit port)."""
    _ext_paths()
    if not have_qiskit():
        return [{"skipped": "qiskit not importable — run on the target machine"}]
    from qualtran.resource_counting import QECGatesCost, QubitCount, get_cost_value
    from fourier_multiply_qualtran import FourierToomMultiply
    from toomcook_multiply_qualtran import ToomCookMultiply
    rows = []
    for k in ks:
        for n in ns:
            if k >= 3 and n < k:
                continue
            for name, bl in (("FourierToomMultiply", FourierToomMultiply(n, k)), ("ToomCookMultiply", ToomCookMultiply(n, k))):
                d = get_cost_value(bl, QECGatesCost())
                rows.append({"bloq": name, "k": k, "n": n, "T": float(d.total_t_count()),
                             "qubits": int(get_cost_value(bl, QubitCount())), "rotation": int(d.rotation), "and": int(d.and_bloq)})
    return rows


# ------------------------------------------------------------------ qiskit gates
def _run_py(script: Path, args: Sequence[str] = (), timeout: int = 1800) -> Dict[str, Any]:
    _ext_paths()
    env = dict(**__import__("os").environ)
    env["PYTHONPATH"] = __import__("os").pathsep.join(sys.path)
    t0 = time.time()
    p = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, timeout=timeout, env=env)
    return {"script": script.name, "rc": p.returncode, "secs": round(time.time() - t0, 1),
            "tail": "\n".join((p.stdout + p.stderr).splitlines()[-12:])}


def qiskit_gates(which: Sequence[str] = ("G1", "TLEDGER", "SHOR")) -> List[Dict[str, Any]]:
    """G1 = verify_bloq_to_qiskit (the walker gate), TLEDGER = walker T-ledger == QECGatesCost bijection,
    SHOR = Toom-engine order finding + factoring 15 and 21. Skipped without qiskit."""
    if not have_qiskit():
        return [{"gate": w, "skipped": "qiskit not importable — run on the target machine"} for w in which]
    scripts = {"G1": EXT / "qiskit_shor" / "verify_bloq_to_qiskit.py",
               "TLEDGER": EXT / "bundle" / "qiskit" / "tledger_reconcile.py",
               "SHOR": EXT / "qiskit_shor" / "verify_toomcook_shor_qiskit.py",
               "ORDER": EXT / "qiskit_shor" / "verify_toomcook_order_finding_qiskit.py",
               "QFT": EXT / "qiskit_shor" / "verify_qft_via_phaseproduct_qiskit.py"}
    return [{"gate": w, **_run_py(scripts[w])} for w in which if w in scripts]


# ------------------------------------------------------------------ gallery
def gallery() -> List[Dict[str, str]]:
    """Decomposition images with their prose briefs (ext/images), plus bundle walker examples."""
    items = []
    for png in sorted((IMAGES / "decomp").glob("*.png")):
        brief = IMAGES / "decomp_briefs" / (png.stem + ".md")
        items.append({"image": str(png), "brief": str(brief) if brief.exists() else "",
                      "caption": png.stem.replace("_", " ") + (" (in-place, pad qubit)" if "inplace" in png.stem else " (out-of-place skeleton)")})
    for png in sorted((EXT / "bundle" / "examples").glob("*.png")):
        items.append({"image": str(png), "brief": "", "caption": "bundle example: " + png.stem.replace("_", " ")})
    return items


def provenance_rows() -> List[Dict[str, str]]:
    return [
        {"piece": "measured skeleton sweep (ext/data)", "tier": Tier.EMPIRICAL.value, "evidence": "verify_mult_cost_mirror 263/264 exact vs QECGatesCost/QubitCount; T-depth is a model"},
        {"piece": "QFT route table", "tier": Tier.EMPIRICAL.value, "evidence": "QECGatesCost on Qualtran/skeleton/oneclick bloqs; exactness per QFT_ROUTE_COMPARISON"},
        {"piece": "phase-depth census", "tier": Tier.MODEL.value, "evidence": "t_model.thist validated against measured R/TS (PHASE_DEPTH_CENSUS)"},
        {"piece": "full order-finding T", "tier": Tier.MODEL.value, "evidence": "anchored to KMY's measured n=2048 estimator run; wrapper (L, M, R) a convention; ratio robust"},
        {"piece": "reversible modexp Toffoli/depth", "tier": Tier.NUMERIC.value, "evidence": "stdlib permutation simulator self-check vs pow"},
        {"piece": "full-multiply Qualtran bloqs", "tier": Tier.EMPIRICAL.value, "evidence": "bundle verifiers (walker vs tensor_contract 0.0e+00; in-place action)"},
        {"piece": "Toom-engine Shor factoring 15, 21", "tier": Tier.NUMERIC.value, "evidence": "verify_toomcook_shor_qiskit (r = true order; both tracks)"},
    ]


# ------------------------------------------------------------------ reconciliation: why the markers sit below the curves
def dyadic_reconciliation(bundles, ns: Sequence[int] = (16, 32, 64, 128, 256, 512, 1024)) -> List[Dict[str, Any]]:
    """Apples to apples at each n, per k: (1) Lean one-level, generic φ — every one of the q·W² leaf pairs a
    rotation (the upper bound the curves draw); (2) Lean one-level, EXACT classification at the sweep's dyadic
    turn; (3) measured skeleton at arity k (recursive, same turn family); (4) measured `direct` n×n grid
    (un-recursed) — the like-for-like check of (2)."""
    from . import estimate
    meas = {(r["arity"], r["n"]): r for r in measured_sweep("off", "exact")}
    rows = []
    for b in bundles:
        arity = "karatsuba" if b.k == 2 else f"toom{b.k}"
        for n in ns:
            ex = estimate.exact_dyadic_counts(b, n, "tight")
            mk = meas.get((arity, n)); md = meas.get(("direct", n))
            rows.append({"k": b.k, "n": n, "W": ex["W"], "pairs": ex["pairs"],
                         "rot_generic": ex["pairs"], "rot_exact_dyadic": ex["rotation"], "t_exact_dyadic": ex["t"],
                         "T_generic": ex["T_generic"], "T_exact_dyadic": ex["T_exact_dyadic"],
                         "rot_measured_skeleton": mk["rotation"] if mk else None, "T_measured_skeleton": mk["T"] if mk else None,
                         "rot_measured_direct": md["rotation"] if md else None, "T_measured_direct": md["T"] if md else None})
    return rows


def figure_dyadic_reconciliation(bundles, out_dir: Path, ns: Sequence[int] = (16, 32, 64, 128, 256, 512, 1024)) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = dyadic_reconciliation(bundles, ns)
    ks = sorted({r["k"] for r in rows})
    fig, axs = plt.subplots(1, len(ks), figsize=(6.5 * len(ks), 5), squeeze=False)
    for ax, k in zip(axs[0], ks):
        rk = [r for r in rows if r["k"] == k]
        x = [r["n"] for r in rk]
        ax.plot(x, [r["T_generic"] for r in rk], "-", lw=2, label="one-level, generic φ (solid)")
        ax.plot(x, [r["T_exact_dyadic"] for r in rk], "--", lw=2, label="one-level, exact dyadic leaves (dashed)")
        ax.plot(x, [r["T_measured_direct"] for r in rk], "s", mfc="none", label="measured direct grid (squares)")
        ax.plot(x, [r["T_measured_skeleton"] for r in rk], "o", mfc="none", label=f"measured recursive skeleton, arity {k} (circles)")
        ax.set_xscale("log"); ax.set_yscale("log"); ax.grid(True, which="both", alpha=.3)
        ax.set_xlabel("n"); ax.set_ylabel("T (11 T/rotation, 4 T/And)"); ax.set_title(f"k = {k}")
        ax.legend(fontsize=7)
    fig.suptitle("V1c — PP(n) at the dyadic turn: one-level (generic / exact leaves) vs measured (direct / recursive)", fontsize=11)
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / "V1c_dyadic_reconciliation.png"
    fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return f
