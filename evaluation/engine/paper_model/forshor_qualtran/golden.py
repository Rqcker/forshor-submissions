"""The optimal-recursion (version B, rule R2) scaling layer: PhaseProduct → QFT → Toom-Cook multiplier → CmodMulInPlaceCore.

Everything here prices ONE rule — Toom-k at every level, tight width, operands zero-extended to k⌈W/k⌉, cost-optimal
stop (`recursion_versions`, rule "kmy", version "B") — through the Lean-emitted structures above it:
  PP      : ToomRecursivePhaseProduct(w = n)                                   theory  Θ(n^{log_k(2k−1)})
  QFT     : LeanQFT(core="versionB")  — E4 Cooley–Tukey rule, version-B twiddles   Θ(w^{log_k(2k−1)})   (master theorem, case 3)
  MULT    : ToomCookMultiplier — H^{⊗n} ; PP_B(n+1) ; IQFT_B(n)  (|x⟩|0⟩ → |x⟩|c·x mod 2^n⟩)   Θ(n^{log_k(2k−1)})
  CMODMUL : LeanCtrlModMul(core="versionB") — the ForShor Alg-1 template at precision η: three multiplications
            (two controlled) + four QFTs + O(n) primitives                          Θ(n^{log_k(2k−1)})
Points are Qualtran counts (`QECGatesCost`, 11 T/rotation); an independent closed-form recurrence is evaluated for each
object and compared at the check points (`checks`).  Fits: least squares in (log n, log T) on the fit window; R²_fit is
the coefficient of determination of the free fit; R²_theory is that of the fixed-slope line C'·n^{e_theory} (intercept
fitted) — the number that says the theory exponent explains the data.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from qualtran.resource_counting import QECGatesCost, get_cost_value

from . import constants
from .bloqs import LeanCtrlModMul, LeanQFT, ToomCookMultiplier
from .emit import Bundle
from .recursion_versions import RuleSet, ToomRecursivePhaseProduct, dense_ns, theory_exponent

OBJECTS: Tuple[str, ...] = ("PP", "QFT", "MULT", "CMODMUL")
OBJECT_DESC = {
    "PP": "recursive PhaseProduct PP_B(n): Toom-k at every level, rule R2",
    "QFT": "QFT_B(w): lowerQFTPlan structure (E4) with version-B twiddles",
    "MULT": "Toom-Cook multiplier |x⟩|0⟩→|x⟩|c·x mod 2^n⟩: H^{⊗n} ; PP_B(n+1) ; IQFT_B(n)",
    "CMODMUL": "CmodMulInPlaceCore template at η: three multiplications (two controlled) + four QFTs + primitives, all version B",
}
#: Lean compile-time note carried into every manifest and printed by the notebook.
LEAN_TIME_NOTE = ("k = 7..10 are included tentatively: their Lean emission (`forshor_emit bundle k`) exceeded 600 s at k = 7 "
                  "with the default tables and is expected to take hours at k = 8..10; run `cli emit --k 7 8 9 10 --emit-timeout 14400` "
                  "once on the target machine (cached afterwards). Until then those k carry the mirror and are labelled NOT Lean-certified.")


def _T(bloq, tsr: float = constants.TS_PER_ROTATION) -> float:
    return float(get_cost_value(bloq, QECGatesCost()).total_t_count(ts_per_rotation=tsr))


# ------------------------------------------------------------------ precision policy (single source)
def _precision_policy():
    """Lazy import of `py/precision_policy` (pure stdlib; the one η definition)."""
    import importlib
    import sys as _sys
    from pathlib import Path as _Path
    pydir = str(_Path(__file__).resolve().parent.parent / "py")
    if pydir not in _sys.path:
        _sys.path.insert(0, pydir)
    return importlib.import_module("precision_policy")


def _eta_at(n: int, eta, precision: str, delta: float) -> float:
    """η at width n: the explicit `eta` if given, else the policy value (appropriate = δ/n²).
    The SAME value must feed `bloq_for` and `cmodmul_closed` so the Qualtran==closed-form check
    stays an apples-to-apples equality."""
    if eta is not None:
        return float(eta)
    return _precision_policy().eta_for(int(n), policy=precision, delta=delta)


# ------------------------------------------------------------------ bloqs per object
def bloq_for(obj: str, rules: RuleSet, k: int, n: int, eta: float = 1e-3):
    b = rules.bundles[k]
    if obj == "PP":
        return ToomRecursivePhaseProduct.from_rules(rules, n, k, "B", "kmy")
    if obj == "QFT":
        return LeanQFT.from_bundle(b, n, "tight", core="versionB", rules=rules)
    if obj == "MULT":
        return ToomCookMultiplier.from_rules(rules, b.doc, n)
    if obj == "CMODMUL":
        return LeanCtrlModMul.from_bundle(b, n, eta=eta, core="versionB", rules=rules)
    raise ValueError(obj)


# ------------------------------------------------------------------ independent closed forms (the check)
def qft_closed(rules: RuleSet, k: int, w: int) -> float:
    """Q(w) = Q(⌊w/2⌋) + Q(⌈w/2⌉) + PP_B(max+1); Q(1) = Q(0) = 0 T (H, swaps are Clifford)."""
    memo: Dict[int, float] = {}

    def go(t: int) -> float:
        if t < 2:
            return 0.0
        if t not in memo:
            l, r = t // 2, t - t // 2
            memo[t] = go(l) + go(r) + rules.t_model(max(l, r) + 1, "B", k, "kmy")
        return memo[t]
    return go(w)


def ladder_T(w: int, tsr: float = constants.TS_PER_ROTATION) -> float:
    """Closed form of `ReferenceQFT(w)`'s Qualtran count: CP(π/2^d) with multiplicity w−d, priced as Qualtran prices
    CZPowGate — exponent 1/2 → 4 T (CS via And), 1/4 → 5 T, every d ≥ 3 → And + rotation = 4 + tsr (checked in tests)."""
    cost = {1: 4.0, 2: 5.0}
    return float(sum((w - d) * cost.get(d, constants.TS_PER_TOFFOLI + tsr) for d in range(1, w)))


def mult_closed(rules: RuleSet, k: int, n: int) -> float:
    return rules.t_model(n + 1, "B", k, "kmy") + qft_closed(rules, k, n)


def cmodmul_closed(rules: RuleSet, k: int, n: int, eta: float = 1e-3) -> float:
    """3 phase products (widths max(n,ww)+1, max(wc,ww)+1, max(ww,wc)+1) + 2 And per CZPow leaf of the two controlled
    ones + 4 QFTs (2×ww, 2×wc) + the U3/U4 primitives priced by Qualtran alone."""
    from qualtran import QUInt
    from qualtran.bloqs.arithmetic import AddK, GreaterThanConstant, LessThanConstant
    from .bloqs.modmul import extra_bits
    ww, wc = n + extra_bits(eta), n + 1
    T = 0.0
    for a in (n, wc):
        pp = ToomRecursivePhaseProduct.from_rules(rules, max(a, ww) + 1, k, "B", "kmy")
        T += rules.t_model(pp.w, "B", k, "kmy") + pp.total_czpow() * constants.TS_PER_TOFFOLI   # And (4 T) + And† (0 T) per leaf
        T += qft_closed(rules, k, ww)
    T += 2 * qft_closed(rules, k, wc) + rules.t_model(max(ww, wc) + 1, "B", k, "kmy")
    T += _T(GreaterThanConstant(wc, 1)) + _T(AddK(QUInt(wc), k=-1).controlled()) + _T(LessThanConstant(wc + ww, 1))
    return T


CLOSED = {"PP": lambda r, k, n: r.t_model(n, "B", k, "kmy"), "QFT": qft_closed, "MULT": mult_closed, "CMODMUL": cmodmul_closed}


# ------------------------------------------------------------------ sweeps, fits
def sweep(rules: RuleSet, ks: Sequence[int], ns: Sequence[int], objects: Sequence[str] = OBJECTS,
          eta: Optional[float] = None, *, precision: str = "appropriate", delta: float = 1e-2) -> List[Dict[str, Any]]:
    rows = []
    for obj in objects:
        for k in ks:
            for n in ns:
                bl = bloq_for(obj, rules, k, int(n), _eta_at(int(n), eta, precision, delta))
                T = rules.t_model(int(n), "B", k, "kmy") if obj == "PP" else _T(bl)
                row = {"object": obj, "k": k, "n": int(n), "T": T, "provenance": rules.bundles[k].provenance.value}
                if obj in ("MULT", "CMODMUL"):
                    row["qubits"] = int(bl.qubit_footprint)
                rows.append(row)
    return rows


def checks(rules: RuleSet, ks: Sequence[int], ns: Sequence[int] = (64, 256, 1024, 4096), objects: Sequence[str] = OBJECTS,
           eta: Optional[float] = None, *, precision: str = "appropriate", delta: float = 1e-2) -> List[Dict[str, Any]]:
    """Qualtran count == independent closed form, per object / k / n."""
    out = []
    for obj in objects:
        for k in ks:
            for n in ns:
                _eta = _eta_at(int(n), eta, precision, delta)   # same η on both sides of the equality
                tq = _T(bloq_for(obj, rules, k, n, _eta))
                tc = CLOSED[obj](rules, k, n) if obj != "CMODMUL" else cmodmul_closed(rules, k, n, _eta)
                out.append({"object": obj, "k": k, "n": n, "T_qualtran": tq, "T_closed": tc, "equal": abs(tq - tc) <= 1e-6 * max(1.0, tq)})
    return out


def fit_stats(rows: Sequence[Dict[str, Any]], n_lo: int, n_hi: int, e_theory: float) -> Optional[Dict[str, float]]:
    pts = sorted((r["n"], r["T"]) for r in rows if n_lo <= r["n"] <= n_hi and r["T"] > 0)
    if len(pts) < 3:
        return None
    x = np.log([p[0] for p in pts]); y = np.log([p[1] for p in pts])
    e, lc = np.polyfit(x, y, 1)
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2_fit = 1.0 - float(np.sum((y - (e * x + lc)) ** 2)) / ss_tot
    lc_th = float(np.mean(y - e_theory * x))                      # intercept of the fixed-slope line
    r2_th = 1.0 - float(np.sum((y - (e_theory * x + lc_th)) ** 2)) / ss_tot
    return {"C": float(math.exp(lc)), "e_fit": float(e), "e_theory": e_theory, "R2_fit": r2_fit,
            "C_theory": float(math.exp(lc_th)), "R2_theory": r2_th, "n_points": len(pts)}


def exponent_summary(rows: Sequence[Dict[str, Any]], fit_window: Tuple[int, int], ks: Sequence[int],
                     objects: Sequence[str] = OBJECTS) -> List[Dict[str, Any]]:
    out = []
    for obj in objects:
        for k in ks:
            st = fit_stats([r for r in rows if r["object"] == obj and r["k"] == k], *fit_window, theory_exponent(k))
            if st:
                out.append({"object": obj, "k": k, **{key: (round(v, 4) if isinstance(v, float) else v) for key, v in st.items()}})
    return out


# ------------------------------------------------------------------ figures (fits only in the legend)
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def figure_fits(rows: Sequence[Dict[str, Any]], rules: RuleSet, ks: Sequence[int], obj: str, out_dir: Path, name: str,
                fit_window: Tuple[int, int], cert_tag: str, ykey: str = "T", reference: Optional[str] = "schoolbook") -> str:
    """Points = Qualtran-priced version-B object on the quarter-octave grid; line = least-squares power law on the fit window,
    drawn over the whole range; legend = the fits only (C, e, R²_fit).  Quadratic reference dotted black."""
    plt = _plt()
    cmap = plt.get_cmap("viridis"); col = {k: cmap(i / max(1, len(ks) - 1)) for i, k in enumerate(ks)}
    fig, ax = plt.subplots(figsize=(9.5, 6.5))
    sub_all = [r for r in rows if r["object"] == obj]
    ns_all = sorted({r["n"] for r in sub_all})
    xs = np.array([ns_all[0], ns_all[-1]], dtype=float)
    if ykey == "T" and reference == "schoolbook":
        ax.plot(ns_all, [constants.SCHOOLBOOK_PREFACTOR * n * n for n in ns_all], "k:", lw=2, label="schoolbook 15·n²")
    elif ykey == "T" and reference == "ladder":
        ax.plot(ns_all, [ladder_T(int(w)) for w in ns_all], "k:", lw=2, label="H + CP ladder (reference QFT)")
    for k in ks:
        sub = [r for r in sub_all if r["k"] == k]
        pts = sorted((r["n"], r[ykey]) for r in sub)
        if not pts:
            continue
        tag = "" if rules.bundles[k].certifies else " [MIRROR]"
        ax.plot(*zip(*pts), "o", color=col[k], ms=3, alpha=.9)
        st = fit_stats([{"n": r["n"], "T": r[ykey]} for r in sub], *fit_window, theory_exponent(k))
        if st:
            ax.plot(xs, st["C"] * xs ** st["e_fit"], "-", color=col[k], lw=1.2,
                    label=f"k={k}{tag}:  {ykey} = {st['C']:.3g}·n^{st['e_fit']:.3f}   (R² = {st['R2_fit']:.5f})")
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("n (bits)" if obj != "QFT" else "w (bits)")
    ax.set_ylabel("T (11 T/rotation)" if ykey == "T" else "qubits (analytic footprint)")
    ax.grid(True, which="both", alpha=.3)
    ax.legend(fontsize=7, loc="upper left", title=f"points: Qualtran count of the version-B object; lines: power-law fit on n∈[{fit_window[0]},{fit_window[1]}]", title_fontsize=7)
    fig.suptitle(f"{name} — {OBJECT_DESC[obj]}  [{cert_tag}]", fontsize=9)
    f = out_dir / f"{name}_{obj}_{ykey}.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return str(f)


def figure_exponents(summary: Sequence[Dict[str, Any]], ks: Sequence[int], out_dir: Path, name: str, cert_tag: str) -> str:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 5.5))
    marks = {"PP": "o", "QFT": "s", "MULT": "^", "CMODMUL": "D"}
    for obj in OBJECTS:
        pts = [(e["k"], e["e_fit"]) for e in summary if e["object"] == obj]
        if pts:
            ax.plot(*zip(*pts), marks[obj], ms=7, mfc="none", label=f"{obj}: fitted exponent")
    ax.plot(list(ks), [theory_exponent(k) for k in ks], "k--", lw=1, label="theory log_k(2k−1)")
    ax.axhline(2.0, color="k", ls=":", lw=1)
    ax.set_xlabel("k"); ax.set_ylabel("exponent of T(n)"); ax.set_xticks(list(ks)); ax.grid(alpha=.3); ax.legend(fontsize=8)
    ax.set_ylim(1.0, 2.1)
    fig.suptitle(f"{name} — fitted exponents vs k, all four objects  [{cert_tag}]", fontsize=9)
    f = out_dir / f"{name}_exponents_vs_k.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return str(f)


# ------------------------------------------------------------------ one call
def run_all(bundles: Sequence[Bundle], out_dir: Path, ks: Sequence[int], n_hi: int = 2 ** 17,
            fit_window: Tuple[int, int] = (4096, 2 ** 17), eta: Optional[float] = None,
            *, precision: str = "appropriate", delta: float = 1e-2) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rules = RuleSet([b for b in bundles if b.k in ks or b.k == 2])
    ks = [k for k in ks if k in rules.data]
    ns = dense_ns(32, n_hi)
    rows = sweep(rules, ks, ns, OBJECTS, eta, precision=precision, delta=delta)
    chk = checks(rules, ks, [n for n in (64, 256, 1024, 4096) if n <= n_hi], OBJECTS, eta,
                 precision=precision, delta=delta)
    summary = exponent_summary(rows, fit_window, ks)
    mir = [k for k in ks if not rules.bundles[k].certifies]
    cert = "REAL-LEAN" if not mir else "NOT Lean-certified for k=" + ",".join(map(str, mir))
    figs = {"G1": figure_fits(rows, rules, ks, "PP", out_dir, "G1", fit_window, cert),
            "G2": figure_fits(rows, rules, ks, "QFT", out_dir, "G2", fit_window, cert, reference="ladder"),
            "G3": figure_fits(rows, rules, ks, "MULT", out_dir, "G3", fit_window, cert),
            "G4": figure_fits(rows, rules, ks, "CMODMUL", out_dir, "G4", fit_window, cert),
            "G5": figure_fits(rows, rules, ks, "CMODMUL", out_dir, "G5", fit_window, cert, ykey="qubits", reference=None),
            "G6": figure_exponents(summary, ks, out_dir, "G6", cert)}
    man = {"schema": "forshor.golden/v1", "rule": "version B (Toom-k at every level), rule R2 (tight width, zero-extended limbs, cost-optimal stop)",
           "objects": OBJECT_DESC, "ks": ks, "n_grid": ns, "fit_window": list(fit_window),
           "precision_policy": precision, "delta": delta,
           "eta": ("policy: %s (delta/n^2)" % precision if eta is None else eta),
           "ts_per_rotation": constants.TS_PER_ROTATION, "rows": rows, "checks": chk, "checks_ok": all(c["equal"] for c in chk),
           "exponents": summary, "figures": figs, "certification": cert, "lean_time_note": LEAN_TIME_NOTE,
           "provenance": {k: rules.bundles[k].provenance.value for k in ks},
           "verdict": ("PASS" if not mir and all(c["equal"] for c in chk) else "NOT CERTIFIED: mirror provenance" if mir else "FAIL: check mismatch")}
    (out_dir / "golden_manifest.json").write_text(json.dumps(man, indent=1, default=str), encoding="utf-8")
    return man
