"""Resource estimation — where `n` finally becomes a number.

    sweep(bundles, ns)        → per (k, policy) T-count and qubit rows from `QECGatesCost`/`QubitCount`
    crossover_table(rows)     → n at which arity k first beats k-1; best k per n
    prefactor_table(bundles)  → leading n² coefficient per (k, policy), from the symbolic expression
    provenance_table()        → the tier table (provenance.STANDING_CLAIMS) + per-bundle provenance
    traceability(bundles)     → every cited Lean name → does it resolve in the vendored tree (grep)
    figures(...)              → V1 T vs n (both policies, measured points overlaid), V2 width gap,
                                V3 best-k map, V6 qubits, V7 recursion depth
    manifest(...)             → forshor.estimate.manifest/v1

All costs come from Qualtran's counters on the symbolic-n bloqs; no hand formulas.
"""
from __future__ import annotations

import json
import math
import re
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import sympy
from qualtran.resource_counting import QECGatesCost, get_cost_value

from . import bundle_compat, constants, paths, schemas, widths
from .bloqs import LeanCtrlModMul, LeanPhaseProduct, LeanQFT, LeanRecursivePhaseProduct, ReferenceQFT
from .emit import Bundle
from .provenance import STANDING_CLAIMS

N_SYM = widths.n


# ------------------------------------------------------------------ symbolic expressions
def phase_product_T_expr(bundle: Bundle, policy: str, ts_per_rotation: float = constants.TS_PER_ROTATION,
                         ts_per_toffoli: float = constants.TS_PER_TOFFOLI) -> Dict[str, Any]:
    """T(n) for the one-level PhaseProduct as a SymPy expression in n (generic φ: every leaf a rotation)."""
    pp = LeanPhaseProduct.from_bundle(bundle, N_SYM, policy)
    gc = get_cost_value(pp, QECGatesCost())
    T = gc.total_t_count(ts_per_rotation=ts_per_rotation, ts_per_toffoli=ts_per_toffoli)
    return {"k": bundle.k, "policy": policy, "W": pp.W, "T_expr": sympy.expand(T),
            "rotations": gc.rotation, "and": gc.and_bloq, "qubits": pp.qubit_footprint,
            "valid_from_n": widths.width_rule(bundle, policy).valid_from_n}


def leading_coefficient(expr: sympy.Expr, k: int) -> float:
    """Coefficient of n² after replacing ⌈n/k⌉ by n/k (continuous leading order)."""
    e = expr.replace(sympy.ceiling, lambda a: a)
    return float(sympy.Poly(sympy.expand(e), N_SYM).coeff_monomial(N_SYM ** 2))


def _v2(x: int) -> int:
    n = 0
    while x and x % 2 == 0:
        x //= 2; n += 1
    return n


def _pairs_le(W: int, s: int) -> int:
    """#{(a,b) ∈ [0,W)² : a+b ≤ s}."""
    if s < 0:
        return 0
    return sum(max(0, min(W, s - a + 1)) for a in range(W))


def exact_dyadic_counts(bundle: Bundle, n_val: int, policy: str = "tight",
                        turn: Optional[Fraction] = None) -> Dict[str, Any]:
    """Exact leaf classification of the one-level PhaseProduct at a concrete dyadic turn.
    Leaf phase c_l·φ (units of π) = odd/2^d (or non-dyadic → every pair a rotation); pair (a,b) of bit
    weights 2^a, 2^b has exponent depth d−a−b: Clifford iff a+b ≥ d−1, T iff a+b = d−2, rotation iff
    a+b ≤ d−3. Default turn = the measured sweep's family (0x9E3779B9|1)/2^n, φ = 2·turn."""
    from fractions import Fraction as _F
    from .mirrors.coeff_poly import poly_eval
    pp = LeanPhaseProduct.from_bundle(bundle, int(n_val), policy)
    m, W = int(pp.m), int(pp.W)
    if turn is None:
        turn = _F(0x9E3779B9 | 1, 1 << int(n_val)) % 1
    phi = 2 * turn
    rot = t = 0
    for poly in pp.coeff_polys:
        c = poly_eval([_F(x) for x in poly], _F(2) ** m) * phi
        num, den = c.numerator, c.denominator
        if den >> _v2(den) != 1:
            rot += W * W
            continue
        d = _v2(den) - _v2(abs(num))
        rot += _pairs_le(W, d - 3)
        t += _pairs_le(W, d - 2) - _pairs_le(W, d - 3)
    allp = pp.q * W * W
    gc = get_cost_value(pp, QECGatesCost())
    and_ = int(gc.and_bloq)                     # every pair is still an And in the IR realisation
    T = 4 * and_ + 11 * rot + t
    return {"k": bundle.k, "n": int(n_val), "W": W, "turn": str(turn), "pairs": allp, "rotation": rot, "t": t,
            "clifford_pairs": allp - rot - t, "and": and_, "T_exact_dyadic": T,
            "T_generic": float(gc.total_t_count())}


# ------------------------------------------------------------------ sweeps (n concrete here)
def _eval(expr: sympy.Expr, n_val: int) -> float:
    return float(expr.subs(N_SYM, n_val))


def sweep(bundles: Sequence[Bundle], ns: Sequence[int], policies: Sequence[str] = widths.POLICIES,
          ts_per_rotation: float = constants.TS_PER_ROTATION) -> List[Dict[str, Any]]:
    """Rows {k, policy, n, T, qubits, W} — the symbolic expression evaluated at each n."""
    rows: List[Dict[str, Any]] = []
    for b in bundles:
        for pol in policies:
            ex = phase_product_T_expr(b, pol, ts_per_rotation)
            rule = widths.width_rule(b, pol)
            for n_val in ns:
                if n_val < ex["valid_from_n"]:
                    continue
                rows.append({"k": b.k, "policy": pol, "n": int(n_val), "T": _eval(ex["T_expr"], n_val),
                             "qubits": _eval(ex["qubits"], n_val), "W": rule.at_n(int(n_val)),
                             "provenance": b.provenance.value})
    return rows


def recursion_sweep(bundles: Sequence[Bundle], ws: Sequence[int], policies: Sequence[str] = ("sym", "tight"),
                    ts_per_rotation: float = constants.TS_PER_ROTATION) -> List[Dict[str, Any]]:
    """The E6 skeleton evaluated: rows {k, policy, w, depth, ladder, T}. policy='tight' is hypothetical."""
    rows = []
    for b in bundles:
        for pol in policies:
            for w in ws:
                r = LeanRecursivePhaseProduct.from_bundle(b, int(w), pol)
                gc = get_cost_value(r, QECGatesCost())
                rows.append({"k": b.k, "policy": pol, "w": int(w), "ladder": r.ladder(), "depth": len(r.ladder()) - 1,
                             "T": float(gc.total_t_count(ts_per_rotation=ts_per_rotation)),
                             "lean": pol == "sym"})
    return rows


def qft_sweep(bundle: Bundle, ws: Sequence[int], policy: str = "tight",
              ts_per_rotation: float = constants.TS_PER_ROTATION) -> List[Dict[str, Any]]:
    """The KMY fast exact QFT (paper §"Fast exact quantum Fourier transform"): the Cleve–Watrous
    balanced recursion whose step-2 twiddle is the RECURSIVE Toom-Cook PhaseProduct — that is what makes
    the QFT sub-quadratic and ancilla-free. `T_lean_qft` is that object (core='recursive', policy='sym',
    the Lean plan). `T_lean_qft_onelevel` keeps the one-level twiddle as a contrast (asymptotically
    quadratic — NOT the KMY QFT). `T_reference_ladder` is the textbook H+CP ladder (numeric reference)."""
    rows = []
    for w in ws:
        lq = LeanQFT.from_bundle(bundle, int(w), "sym", core="recursive", depth="lean")     # KMY sub-quadratic QFT
        lq1 = LeanQFT.from_bundle(bundle, int(w), policy)                                    # one-level twiddle (contrast)
        rq = ReferenceQFT(int(w))
        rows.append({"k": bundle.k, "w": int(w),
                     "T_lean_qft": float(get_cost_value(lq, QECGatesCost()).total_t_count(ts_per_rotation=ts_per_rotation)),
                     "T_lean_qft_onelevel": float(get_cost_value(lq1, QECGatesCost()).total_t_count(ts_per_rotation=ts_per_rotation)),
                     "T_reference_ladder": float(get_cost_value(rq, QECGatesCost()).total_t_count(ts_per_rotation=ts_per_rotation))})
    return rows


def recursive_pp_sweep(bundles: Sequence[Bundle], ns: Sequence[int],
                       ts_per_rotation: float = constants.TS_PER_ROTATION) -> List[Dict[str, Any]]:
    """PP_rec(n): the recursive Toom-Cook PhaseProduct. variant='lean' = the proved arbitrary-depth plan
    (symbolic widths, recurse while next(W) < W — `evalL_lowerGateRec_correct`); variant='optimal' = the same
    ladder cut at the cost-minimising depth (HYPOTHETICAL: `signedBase` requires ¬(next < W), so an early stop
    is not a plan of that inductive type). Qubits = 2n + Lean reserveNeed(x+z) + 1."""
    rows = []
    for b in bundles:
        for n_val in ns:
            for variant in ("lean", "optimal"):
                r = LeanRecursivePhaseProduct.from_bundle(b, int(n_val), "sym", depth=variant)
                gc = get_cost_value(r, QECGatesCost())
                lad = r.ladder()
                rows.append({"k": b.k, "n": int(n_val), "variant": variant, "ladder": lad, "depth": len(lad) - 1,
                             "base_width": lad[-1], "leaves": r.total_czpow(),
                             "T": float(gc.total_t_count(ts_per_rotation=ts_per_rotation)),
                             "qubits": int(r.qubit_footprint), "provenance": b.provenance.value})
    return rows


def modmul_sweep(bundles: Sequence[Bundle], ns: Sequence[int], eta: float = 1e-3, policy: str = "tight",
                 ts_per_rotation: float = constants.TS_PER_ROTATION,
                 cores: Sequence[tuple] = (("onelevel", "lean", "tight"), ("recursive", "lean", "sym"), ("recursive", "optimal", "sym"))) -> List[Dict[str, Any]]:
    """The E5 template (`CmodMulInPlaceCore`, KMY Alg-1 approximate core) evaluated at each n: T from
    QECGatesCost (LeanQFT + LeanPhaseProduct inside), analytic qubit footprint. Tier B (Lean-cited
    structure) over tier A parts; precision η fixes |work| = n + ⌈2 log2(2 + 1/(2η))⌉."""
    rows = []
    for b in bundles:
        for n_val in ns:
            for qft in ("lean", "reference"):
                for core, depth, pol in cores:
                    if qft == "reference" and core != "onelevel":
                        continue
                    mm = LeanCtrlModMul.from_bundle(b, int(n_val), eta=eta, N=(1 << int(n_val)) - 1, policy=pol,
                                                    qft=qft, core=core, depth=depth)
                    gc = get_cost_value(mm, QECGatesCost())
                    rows.append({"k": b.k, "n": int(n_val), "eta": eta, "w_work": mm.w_work, "qft": qft,
                                 "core": core if core == "onelevel" else f"recursive-{depth}", "policy": pol,
                                 "T": float(gc.total_t_count(ts_per_rotation=ts_per_rotation)),
                                 "rotation": float(gc.rotation), "and": float(gc.and_bloq),
                                 "qubits": int(mm.qubit_footprint), "provenance": b.provenance.value})
    return rows


# ------------------------------------------------------------------ tables
def crossover_table(rows: Sequence[Dict[str, Any]], policy: str = "tight") -> Dict[str, Any]:
    """For each k ≥ 3, the least swept n with T_k(n) < T_{k-1}(n); plus best k per n."""
    by_kn: Dict[tuple, float] = {(r["k"], r["n"]): r["T"] for r in rows if r["policy"] == policy}
    ks = sorted({r["k"] for r in rows if r["policy"] == policy})
    ns = sorted({r["n"] for r in rows if r["policy"] == policy})
    cross = {}
    for k in ks:
        if k - 1 not in ks:
            continue
        hit = next((n for n in ns if (k, n) in by_kn and (k - 1, n) in by_kn and by_kn[(k, n)] < by_kn[(k - 1, n)]), None)
        cross[k] = hit
    best = {n: min((k for k in ks if (k, n) in by_kn), key=lambda k: by_kn[(k, n)]) for n in ns}
    return {"policy": policy, "crossover_n": cross, "best_k_by_n": best,
            "rsa": {n: best.get(n) for n in (2048, 3072, 4096) if n in best}}


def prefactor_table(bundles: Sequence[Bundle]) -> List[Dict[str, Any]]:
    out = []
    for b in bundles:
        _cen = bundle_compat.schedule_census(b)
        row: Dict[str, Any] = {"k": b.k, "q": 2 * b.k - 1, "A_k": _cen["A_k"],
                               "textbook_exponent": math.log(2 * b.k - 1, b.k)}
        for pol in widths.POLICIES:
            ex = phase_product_T_expr(b, pol)
            rule = widths.width_rule(b, pol)
            row[f"beta_{pol}"] = rule.beta
            row[f"lead_n2_{pol}"] = leading_coefficient(ex["T_expr"], b.k)
            row[f"qubits_{pol}"] = str(ex["qubits"])
        row["schoolbook_lead"] = constants.SCHOOLBOOK_PREFACTOR
        out.append(row)
    return out


def width_gap_table(bundles: Sequence[Bundle]) -> List[Dict[str, Any]]:
    out = []
    for b in bundles:
        for e in widths.uniform_rows(b.doc if hasattr(b, "doc") else b):
            out.append(dict(e))
    return out


def provenance_table(bundles: Sequence[Bundle]) -> Dict[str, Any]:
    return {"claims": [c.row() for c in STANDING_CLAIMS],
            "bundles": [{"k": b.k, "provenance": b.provenance.value, "binary": str(b.binary) if b.binary else None,
                         "notes": b.notes} for b in bundles],
            "certifies": all(b.certifies for b in bundles)}


def _tree_names() -> str:
    """Concatenated text of every .lean file in the vendored tree (for citation resolution)."""
    root = paths.FORSHOR_TREE / "FastMultiplication"
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*.lean"))


def traceability(bundles: Sequence[Bundle]) -> List[Dict[str, Any]]:
    """Every cited Lean name across the bundles and the standing claims → resolves in the tree?"""
    text = _tree_names()
    cited = set()
    for b in bundles:
        doc = b.doc if hasattr(b, "doc") else b
        # Iterate the sections the document HAS, not a fixed list: v2 dropped `modmul_plan` and
        # added `shor_plan`/`template`, so the hard-coded list raised KeyError on every v2
        # bundle. And v2 moved the citations out of the sections into a top-level `provenance`
        # object keyed by section name -- collect Lean names from both shapes.
        for name, sec in doc.items():
            if isinstance(sec, dict):
                cited.update(sec.get("cites", []) or [])
        prov = doc.get("provenance")
        if isinstance(prov, dict):
            for blurb in prov.values():
                if isinstance(blurb, str):
                    # provenance strings name their Lean declarations inline, e.g.
                    # "Shor.standardLoweringSetup: ... (genOpsWithProduct_ProgConsumesPtsSafe, ...)"
                    cited.update(re.findall(r"[A-Z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_']+)+", blurb))
                    cited.update(re.findall(r"(?:genOpsWithProduct|cramerCoeff|gaussJordan)"
                                            r"[A-Za-z0-9_']*", blurb))
    for c in STANDING_CLAIMS:
        cited.update(c.cites)
    rows = []
    for name in sorted(cited):
        # A citation in parentheses is a note about a declaration that no longer exists (the
        # 2026-09-18 drop retired the v1 emitter's `Shor.Emit.*`), not a name to resolve.
        # Recording it as unresolved would read as a broken reference; recording it as resolved
        # would claim Lean backing that is exactly what was lost.
        if name.startswith("("):
            rows.append({"cite": name, "resolves": None, "kind": "retired-upstream"})
            continue
        parts = name.split(".")
        decl = None
        for cand in (".".join(parts[-2:]), parts[-1]):
            decl = re.search(rf"^\s*(theorem|lemma|def|noncomputable def|class|structure|inductive|abbrev)\s+{re.escape(cand)}\b",
                             text, re.M)
            if decl:
                break
        rows.append({"cite": name, "resolves": decl is not None, "kind": decl.group(1) if decl else None})
    return rows


# ------------------------------------------------------------------ measured points (overlay)
def measured_points() -> List[Dict[str, Any]]:
    """The legacy KMY measurement cache, as overlay points (empirical tier)."""
    p = paths.ARTIFACTS / "kmy_measure_cache.json"
    if not p.is_file():
        return []
    doc = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for key, val in doc.items():
        m = re.match(r"^T:(\d+):(\d+)$", key)
        if m and isinstance(val, dict) and val.get("status", "ok") == "ok" and "value" in val:
            out.append({"k": int(m.group(1)), "w": int(m.group(2)), "T": float(val["value"])})
        elif m and isinstance(val, (int, float)):
            out.append({"k": int(m.group(1)), "w": int(m.group(2)), "T": float(val)})
    return out


# ------------------------------------------------------------------ figures
def figures(rows: Sequence[Dict[str, Any]], bundles: Sequence[Bundle], out_dir: Path,
            rec_rows: Optional[Sequence[Dict[str, Any]]] = None, points: Optional[Sequence[Dict[str, Any]]] = None,
            qft_rows: Optional[Sequence[Dict[str, Any]]] = None, mm_rows: Optional[Sequence[Dict[str, Any]]] = None,
            rpp_rows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    made: Dict[str, str] = {}
    ks = sorted({b.k for b in bundles})
    cmap = plt.get_cmap("viridis")
    col = {k: cmap(i / max(1, len(ks) - 1)) for i, k in enumerate(ks)}
    cert = all(b.certifies for b in bundles)
    prov = {b.k: b.certifies for b in bundles}
    tag = "REAL-LEAN" if cert else "NOT Lean-certified for k=" + ",".join(str(k) for k in ks if not prov[k])
    lab = lambda k: f"k={k}" + ("" if prov[k] else " [MIRROR]")

    # V1 — T vs n, both policies, log-log, measured points overlaid
    fig, ax = plt.subplots(figsize=(9, 6))
    for k in ks:
        for pol, ls in (("tight", "-"), ("sym", "--")):
            pts = sorted([(r["n"], r["T"]) for r in rows if r["k"] == k and r["policy"] == pol])
            if pts:
                ax.plot(*zip(*pts), ls, color=col[k], label=lab(k) if pol == "tight" else None)
    ns = sorted({r["n"] for r in rows})
    if ns:
        ax.plot(ns, [constants.SCHOOLBOOK_PREFACTOR * n * n for n in ns], "k:", label="schoolbook 15n² (full multiply ref.)")
    for p in (points or []):
        ax.plot(p["w"], p["T"], "o", color=col.get(p["k"], "gray"), mfc="none")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("n (operand bits)"); ax.set_ylabel("PP(n): T count of ONE PhaseProduct (QECGatesCost, 11 T/rotation, generic φ)")
    ax.set_title(f"V1 — PP(n), one-level PhaseProduct; solid: tight W, dashed: symbolic W, circles: measured KMY cache  [{tag}]", fontsize=9)
    ax.legend(fontsize=8, ncol=2); ax.grid(True, which="both", alpha=.3)
    f = out_dir / "V1_tgate_vs_n_both_policies.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    made["V1"] = str(f)

    # V2 — width gap
    fig, ax = plt.subplots(figsize=(8, 5))
    for b in bundles:
        u = widths.uniform_rows(b.doc)
        ax.plot([e["m"] for e in u], [e["gap"] for e in u], "-o", color=col[b.k], label=lab(b.k))
    ax.set_xlabel("limb width m = ⌈n/k⌉"); ax.set_ylabel("W_sym − W_tight (qubits per limb)")
    ax.set_title(f"V2 — W_sym − W_tight vs m  [{tag}]", fontsize=9)
    ax.legend(fontsize=8); ax.grid(alpha=.3)
    f = out_dir / "V2_width_gap.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    made["V2"] = str(f)

    # V3 — best k per n (tight)
    ct = crossover_table(rows, "tight")
    fig, ax = plt.subplots(figsize=(8, 3.5))
    bn = sorted(ct["best_k_by_n"].items())
    if bn:
        ax.step([n for n, _ in bn], [k for _, k in bn], where="post")
    ax.set_xscale("log"); ax.set_xlabel("n"); ax.set_ylabel("best arity k (tight width)")
    ax.set_title(f"V3 — best k per n, PP(n), tight W  [{tag}]", fontsize=9); ax.grid(alpha=.3)
    f = out_dir / "V3_best_k_map.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    made["V3"] = str(f)

    # V6 — qubits
    fig, ax = plt.subplots(figsize=(8, 5))
    for k in ks:
        for pol, ls in (("tight", "-"), ("sym", "--")):
            pts = sorted([(r["n"], r["qubits"]) for r in rows if r["k"] == k and r["policy"] == pol])
            if pts:
                ax.plot(*zip(*pts), ls, color=col[k], label=lab(k) if pol == "tight" else None)
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("n"); ax.set_ylabel("qubits = 2n + 2kβ_policy")
    ax.set_title(f"V6 — qubits of one PhaseProduct; solid: tight W, dashed: symbolic W  [{tag}]", fontsize=9)
    ax.legend(fontsize=8, ncol=2); ax.grid(True, which="both", alpha=.3)
    f = out_dir / "V6_qubits_vs_n.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    made["V6"] = str(f)

    # V7 — recursion depth / T of the E6 skeleton
    if rec_rows:
        fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))
        for k in ks:
            for pol, ls in (("sym", "-"), ("tight", "--")):
                pts = sorted([(r["w"], r["depth"]) for r in rec_rows if r["k"] == k and r["policy"] == pol])
                if pts:
                    axs[0].plot(*zip(*pts), ls, color=col[k], label=lab(k) if pol == "sym" else None)
                pts = sorted([(r["w"], r["T"]) for r in rec_rows if r["k"] == k and r["policy"] == pol])
                if pts:
                    axs[1].plot(*zip(*pts), ls, color=col[k])
        axs[0].set_xscale("log"); axs[0].set_xlabel("input width w"); axs[0].set_ylabel("recursion depth d")
        axs[0].set_title(f"V7 — recursion depth; solid: Lean plan (symbolic W), dashed: tight-W recursion (hypothetical)  [{tag}]", fontsize=9)
        axs[1].set_xscale("log"); axs[1].set_yscale("log"); axs[1].set_xlabel("w"); axs[1].set_ylabel("T (skeleton, 11 T/rotation)")
        axs[1].set_title("V7 — recursive PhaseProduct T(w)", fontsize=9)
        axs[0].legend(fontsize=8); axs[0].grid(alpha=.3); axs[1].grid(True, which="both", alpha=.3)
        f = out_dir / "V7_recursion_skeleton.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
        made["V7"] = str(f)

    # V9 — QFT: the Lean rule (E4) vs the reference ladder, T vs register width
    if qft_rows:
        fig, ax = plt.subplots(figsize=(8, 5))
        ws_ = [r["w"] for r in qft_rows]
        ax.plot(ws_, [r["T_lean_qft"] for r in qft_rows], "-o", label=f"KMY fast exact QFT (lowerQFTPlan, k={qft_rows[0]['k']}, RECURSIVE Toom twiddles)")
        if all("T_lean_qft_onelevel" in r for r in qft_rows):
            ax.plot(ws_, [r["T_lean_qft_onelevel"] for r in qft_rows], ":", color="gray", label="one-level twiddle (∼quadratic contrast, not the KMY QFT)")
        ax.plot(ws_, [r["T_reference_ladder"] for r in qft_rows], "--s", label="reference H+CP ladder (numerically validated, not Lean)")
        ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("register width w"); ax.set_ylabel("T (11 T/rotation)")
        ax.set_title(f"V9 — QFT T(w); KMY recursive-twiddle QFT vs one-level vs reference ladder  [{tag}]", fontsize=9); ax.legend(fontsize=8); ax.grid(True, which="both", alpha=.3)
        f = out_dir / "V9_qft_lean_vs_ladder.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
        made["V9"] = str(f)

    # V10 — the Toom-Cook controlled modular multiply (E5 template) T and qubits vs n per k
    if mm_rows:
        fig, axs = plt.subplots(1, 2, figsize=(13, 5))
        for k in ks:
            for qft, ls in (("lean", "-"), ("reference", "--")):
                pts = sorted((r["n"], r["T"]) for r in mm_rows if r["k"] == k and r.get("qft", "lean") == qft and r.get("core", "onelevel") == "onelevel")
                if pts:
                    axs[0].plot(*zip(*pts), ls, color=col[k], label=lab(k) + (" LeanQFT inside" if qft == "lean" else " ladder inside"))
            pts = sorted((r["n"], r["qubits"]) for r in mm_rows if r["k"] == k and r.get("qft", "lean") == "lean" and r.get("core", "onelevel") == "onelevel")
            if pts:
                axs[1].plot(*zip(*pts), "-", color=col[k], label=lab(k))
        ns_ = sorted({r["n"] for r in mm_rows})
        axs[0].plot(ns_, [constants.SCHOOLBOOK_PREFACTOR * n * n for n in ns_], "k:", label="schoolbook 15n² (one multiply)")
        for ax, ylab in ((axs[0], "T (11 T/rotation)"), (axs[1], "qubits (analytic footprint)")):
            ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("n"); ax.set_ylabel(ylab); ax.grid(True, which="both", alpha=.3); ax.legend(fontsize=6, ncol=2)
        eta = mm_rows[0]["eta"]
        fig.suptitle(f"V10 — CmodMulInPlaceCore template, η={eta:g}; solid: LeanQFT inside, dashed: ladder inside  [{tag}]", fontsize=10)
        f = out_dir / "V10_modmul_template_vs_n.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
        made["V10"] = str(f)

    # V11 — PP_rec(n): the Lean-proved arbitrary-depth recursive PhaseProduct vs one-level vs schoolbook
    if rpp_rows:
        fig, axs = plt.subplots(1, 2, figsize=(14, 5.5))
        for k in ks:
            one = sorted((r["n"], r["T"]) for r in rows if r["k"] == k and r["policy"] == "tight")
            if one:
                axs[0].plot(*zip(*one), ":", color=col[k], lw=1.5)
            for variant, ls in (("lean", "-"), ("optimal", "--")):
                pts = sorted((r["n"], r["T"]) for r in rpp_rows if r["k"] == k and r["variant"] == variant)
                if pts:
                    axs[0].plot(*zip(*pts), ls, color=col[k], label=lab(k) + (" Lean plan" if variant == "lean" else " cutoff"))
                pts = sorted((r["n"], r["depth"]) for r in rpp_rows if r["k"] == k and r["variant"] == variant)
                if pts:
                    axs[1].plot(*zip(*pts), ls + "o", color=col[k], ms=3)
        ns_ = sorted({r["n"] for r in rpp_rows})
        axs[0].plot(ns_, [constants.SCHOOLBOOK_PREFACTOR * n * n for n in ns_], "k:", lw=2, label="schoolbook 15n²")
        axs[0].plot([], [], ":", color="gray", label="one-level PP(n), tight W (dotted, per k)")
        axs[0].set_xscale("log"); axs[0].set_yscale("log"); axs[0].set_xlabel("n"); axs[0].set_ylabel("PP_rec(n): T of one recursive PhaseProduct (11 T/rotation)")
        axs[0].set_title("PP_rec(n); solid: Lean plan (full depth), dashed: cost-optimal cutoff, dotted: one-level", fontsize=9)
        axs[0].legend(fontsize=6, ncol=2); axs[0].grid(True, which="both", alpha=.3)
        axs[1].set_xscale("log"); axs[1].set_xlabel("n"); axs[1].set_ylabel("recursion depth d"); axs[1].grid(alpha=.3)
        axs[1].set_title("recursion depth; solid: Lean plan, dashed: cost-optimal cutoff", fontsize=9)
        fig.suptitle(f"V11 — recursive PhaseProduct  [{tag}]", fontsize=10)
        f = out_dir / "V11_recursive_pp_vs_n.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
        made["V11"] = str(f)

    # V12 — integrated multiply costing: recursive core (Lean plan / cutoff) vs one-level, LeanQFT inside, vs schoolbook
    if mm_rows and any(r.get("core", "onelevel") != "onelevel" for r in mm_rows):
        fig, axs = plt.subplots(1, 2, figsize=(14, 5.5))
        styles = {"onelevel": ":", "recursive-lean": "-", "recursive-optimal": "--"}
        names = {"onelevel": "one-level core (tight W)", "recursive-lean": "recursive core, Lean plan (full depth)", "recursive-optimal": "recursive core, cost-optimal cutoff (hyp.)"}
        for k in ks:
            for core, ls in styles.items():
                pts = sorted((r["n"], r["T"]) for r in mm_rows if r["k"] == k and r.get("qft") == "lean" and r.get("core", "onelevel") == core)
                if pts:
                    axs[0].plot(*zip(*pts), ls, color=col[k], label=lab(k) + " " + names[core])
                pts = sorted((r["n"], r["qubits"]) for r in mm_rows if r["k"] == k and r.get("qft") == "lean" and r.get("core", "onelevel") == core)
                if pts:
                    axs[1].plot(*zip(*pts), ls, color=col[k])
        ns_ = sorted({r["n"] for r in mm_rows})
        axs[0].plot(ns_, [constants.SCHOOLBOOK_PREFACTOR * n * n for n in ns_], "k:", lw=2, label="schoolbook 15n² (one multiply)")
        for ax, ylab in ((axs[0], "T (11 T/rotation)"), (axs[1], "qubits (analytic footprint)")):
            ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("n"); ax.set_ylabel(ylab); ax.grid(True, which="both", alpha=.3)
        axs[0].legend(fontsize=6, ncol=2)
        fig.suptitle(f"V12 — CmodMulInPlaceCore template, core variants; solid: recursive Lean plan, dashed: cutoff, dotted: one-level  [{tag}]", fontsize=10)
        f = out_dir / "V12_modmul_integrated_cores.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
        made["V12"] = str(f)
    return made


def provenance_comment(bundles: Sequence[Bundle], what: str = "figure") -> str:
    """One line to print under every figure/table: which k are Lean-certified and which are the mirror."""
    real = [b.k for b in bundles if b.certifies]; mir = [b for b in bundles if not b.certifies]
    if not mir:
        return f"[{what}] Lean-certified: all k = {real} come from the built forshor_emit binary (REAL-LEAN, real == mirror)."
    why = "; ".join(f"k={b.k}: {b.notes[0] if b.notes else 'mirror'}" for b in mir)
    return (f"[{what}] NOT Lean-certified for k = {[b.k for b in mir]} (Python mirror of the Lean emitter; "
            f"{'certified: k=' + str(real) if real else 'no k certified'}). Reason: {why}")


# ------------------------------------------------------------------ manifest
def manifest(bundles: Sequence[Bundle], rows: Sequence[Dict[str, Any]], extra: Dict[str, Any], out_path: Path) -> Path:
    doc = {
        "schema": schemas.ESTIMATE_MANIFEST,
        "stamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_enters_at": "forshor_qualtran.estimate.sweep (get_cost_value(...).subs(n, N)); nothing upstream is n-dependent",
        "cost_source": "qualtran.resource_counting.QECGatesCost / QubitCount on symbolic-n bloqs",
        "constants": {"TS_PER_ROTATION": constants.TS_PER_ROTATION, "TS_PER_TOFFOLI": constants.TS_PER_TOFFOLI,
                      "SCHOOLBOOK_PREFACTOR": constants.SCHOOLBOOK_PREFACTOR},
        "provenance": provenance_table(bundles),
        "prefactors": prefactor_table(bundles),
        "width_gap": width_gap_table(bundles),
        "crossover_tight": crossover_table(rows, "tight"),
        "crossover_sym": crossover_table(rows, "sym"),
        "traceability": traceability(bundles),
        "rows": list(rows),
        **extra,
    }
    # Three outcomes, not two. A citation that RESOLVES is backed; one that does not is a broken
    # reference and a failure; one marked `retired-upstream` is neither -- it names a statement
    # the 2026-09-18 drop removed, so the claim is correctly attributed to something that no
    # longer exists. Failing on those would keep the verdict permanently red for a reason no
    # code change can fix; dropping them would quietly lose the fact that a claim lost its Lean
    # backing. They are carried into the verdict as a named caveat instead.
    unresolved = [c["cite"] for c in doc["traceability"]
                  if c["resolves"] is False
                  or (c["resolves"] is None and c.get("kind") != "retired-upstream")]
    retired = [c["cite"] for c in doc["traceability"] if c.get("kind") == "retired-upstream"]
    doc["retired_citations"] = retired
    if not doc["provenance"]["certifies"]:
        doc["verdict"] = "NOT CERTIFIED: mirror provenance"
    elif unresolved:
        doc["verdict"] = "FAIL: unresolved citations " + ", ".join(unresolved)
    elif retired:
        doc["verdict"] = (f"PASS with {len(retired)} retired citation(s): the 2026-09-18 drop "
                          f"removed the declarations these claims rested on (ask L23). The "
                          f"quantity is reconstructed and validated against the v1 emitter's "
                          f"own tables, but it is not proved in the reference tree.")
    else:
        doc["verdict"] = "PASS"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    return out_path
