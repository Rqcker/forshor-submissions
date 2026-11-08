"""Asymptotics — every primitive priced by Qualtran, plotted against the exponent ForShor PROVES.

Each KMY/Shor primitive has a gate-count bound proved in ForShor (all `sorry`-free, ∃C∃n₀ form). This
module sweeps the Qualtran cost of each priced object (version B: Toom-k at every level — the single-k
KMY recursion, rule R2), fits a power law, and overlays the Lean-proved exponent so the plots *show* the
correct asymptotics. The per-primitive Lean citation is carried into the manifest as a verification
ledger — the "everything Lean-sourced is Lean-verified" record.

    exponent per primitive (in the operand/register width, then in n for the composed objects):
      PP, QFT, MODMUL : e = log_k(2k-1)               PhaseProductGateCountBound / QFTGateCountBound /
                                                       CPhaseProductGateCountBound
      MODEXP, SHOR    : e = 1 + log_k(2k-1)           tbits = O(n) controlled multiplies; the O(n^{2+ε})
                                                       envelope is ShorGateCountBound (e → 2 as k → ∞)

Version B (`recursion_versions`) is what ForShor's plan proves is single-k; the objects here are the
Phase A–E bloqs built on it, so the curves are the Lean-structured KMY asymptotics, not a hand model.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from qualtran.resource_counting import QECGatesCost, get_cost_value

from . import constants
from .bloqs import LeanCtrlModMul, LeanModExp, LeanOrderFinding, LeanQFT
from .golden import fit_stats
from .recursion_versions import RuleSet, ToomRecursivePhaseProduct, dense_ns, theory_exponent

#: primitive → (human description, is-composed [exponent 1+log_k q], Lean bound theorem justifying the line)
PRIMITIVES: Dict[str, Tuple[str, bool, str]] = {
    "PP":     ("recursive Toom-Cook PhaseProduct PP_B(n)", False,
               "Shor.PhaseProductGateCountBound (Implementation/GateCount/Definitions.lean:26)"),
    "QFT":    ("KMY fast exact QFT (recursive twiddle)", False,
               "Shor.QFTGateCountBound (Definitions.lean:184; qftGateCountBound_of_programOK)"),
    "MULT":   ("Toom-Cook multiply |x⟩|0⟩→|x⟩|c·x⟩ (H ; PhaseProduct ; IQFT)", False,
               "Shor.PhaseProductGateCountBound + Shor.MasterTheoremProof (masterMixed/masterGeom → n^{log_k(2k-1)})"),
    "MODMUL": ("controlled modular multiply CmodMulInPlaceCore", False,
               "Shor.CPhaseProductGateCountBound (Definitions.lean:160)"),
    "MODEXP": ("modular exponentiation (tbits controlled multiplies)", True,
               "Shor.modExpApprox_correct + Shor.tbits (O(n) × modmul)"),
    "SHOR":   ("full Shor order-finding (QPE ; modexp ; IQFT)", True,
               "Shor.ShorGateCountBound / exists_k_shorGateCountBound_of_programOK (shorGateRate = n^{2+ε})"),
}
ORDER: Tuple[str, ...] = ("PP", "QFT", "MULT", "MODMUL", "MODEXP", "SHOR")


def theory_exp(prim: str, k: int) -> float:
    """The Lean-proved asymptotic exponent for `prim` at arity k."""
    base = theory_exponent(k)                    # log_k(2k-1)
    return base + (1.0 if PRIMITIVES[prim][1] else 0.0)


# ------------------------------------------------------------------ priced objects (version B, single k)
def _precision_policy():
    """Lazy import of `py/precision_policy` (pure stdlib; single source of truth for η)."""
    import importlib
    import sys as _sys
    from pathlib import Path as _Path
    pydir = str(_Path(__file__).resolve().parent.parent / "py")
    if pydir not in _sys.path:
        _sys.path.insert(0, pydir)
    return importlib.import_module("precision_policy")


def bloq_for(prim: str, rules: RuleSet, k: int, n: int, eta: Optional[float] = None, *,
             precision: str = "appropriate", delta: float = 1e-2, fixed_eta: float = 1e-12):
    """Version-B priced bloq for `prim` at (k, n). Per-modmul precision η follows `precision`
    (`py/precision_policy`): "appropriate" → η = δ/n² (Lean shorEta, n-scaling; default); "fixed" →
    η = `fixed_eta`. The same η drives MODMUL (eta=η) and MODEXP/SHOR (δ = η·n²). Passing `eta=`
    explicitly overrides for MODMUL and leaves MODEXP/SHOR on δ=1e-2 (legacy)."""
    n = int(n)
    if eta is None:
        PP = _precision_policy()
        eta = PP.eta_for(n, policy=precision, delta=delta, fixed_eta=fixed_eta)
        delta_eff = PP.composed_delta(n, policy=precision, delta=delta, fixed_eta=fixed_eta)
    else:
        delta_eff = 1e-2
    b = rules.bundles[k]
    if prim == "PP":
        return ToomRecursivePhaseProduct.from_rules(rules, n, k, "B", "kmy")
    if prim == "QFT":
        return LeanQFT.from_bundle(b, n, "tight", core="versionB", rules=rules)
    if prim == "MULT":
        from .bloqs import ToomCookMultiplier
        return ToomCookMultiplier.from_rules(rules, b.doc, n)
    if prim == "MODMUL":
        return LeanCtrlModMul.from_bundle(b, n, eta=eta, N=(1 << n) - 1, policy="tight", core="versionB", qft="lean", rules=rules)
    if prim == "MODEXP":
        return LeanModExp.from_bundle(b, n, delta=delta_eff, N=(1 << n) - 1, policy="tight", core="versionB", qft="lean", rules=rules)
    if prim == "SHOR":
        return LeanOrderFinding.from_bundle(b, n, a=2, delta=delta_eff, N=(1 << n) - 1, policy="tight", core="versionB", qft="lean", rules=rules)
    raise ValueError(prim)


def _T(bloq, tsr: float = constants.TS_PER_ROTATION) -> float:
    return float(get_cost_value(bloq, QECGatesCost()).total_t_count(ts_per_rotation=tsr))


def sweep(rules: RuleSet, ks: Sequence[int], ns: Sequence[int], prims: Sequence[str] = ORDER,
          eta: Optional[float] = None, *, precision: str = "appropriate", delta: float = 1e-2) -> List[Dict[str, Any]]:
    rows = []
    for prim in prims:
        for k in ks:
            for n in ns:
                rows.append({"primitive": prim, "k": k, "n": int(n),
                             "T": _T(bloq_for(prim, rules, k, int(n), eta, precision=precision, delta=delta)),
                             "provenance": rules.bundles[k].provenance.value})
    return rows


def exponent_ledger(rows: Sequence[Dict[str, Any]], fit_window: Tuple[int, int], ks: Sequence[int],
                    prims: Sequence[str] = ORDER) -> List[Dict[str, Any]]:
    """Per (primitive, k): fitted exponent vs the Lean-proved exponent, with the citation. R²_theory is
    how well the PROVED exponent explains the Qualtran data (intercept fitted)."""
    out = []
    for prim in prims:
        for k in ks:
            sub = [{"n": r["n"], "T": r["T"]} for r in rows if r["primitive"] == prim and r["k"] == k]
            st = fit_stats(sub, *fit_window, theory_exp(prim, k))
            if st:
                out.append({"primitive": prim, "k": k, "lean_bound": PRIMITIVES[prim][2],
                            **{key: (round(v, 4) if isinstance(v, float) else v) for key, v in st.items()}})
    return out


# ------------------------------------------------------------------ figures
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def figure_primitive(rows: Sequence[Dict[str, Any]], rules: RuleSet, ks: Sequence[int], prim: str,
                     out_dir: Path, name: str, fit_window: Tuple[int, int], cert_tag: str) -> str:
    """Points = Qualtran cost of the version-B primitive; solid line = least-squares power law; dashed =
    the LEAN-PROVED exponent (intercept fitted). Legend carries fitted e, R²_theory, and the citation."""
    plt = _plt()
    cmap = plt.get_cmap("viridis"); col = {k: cmap(i / max(1, len(ks) - 1)) for i, k in enumerate(ks)}
    fig, ax = plt.subplots(figsize=(9.5, 6.5))
    sub_all = [r for r in rows if r["primitive"] == prim]
    ns_all = sorted({r["n"] for r in sub_all})
    if not ns_all:
        plt.close(fig); return ""
    xs = np.array([ns_all[0], ns_all[-1]], dtype=float)
    ax.plot(ns_all, [constants.SCHOOLBOOK_PREFACTOR * n * n for n in ns_all], "k:", lw=1.5, label="schoolbook n² (ref.)")
    for k in ks:
        sub = [{"n": r["n"], "T": r["T"]} for r in sub_all if r["k"] == k]
        pts = sorted((r["n"], r["T"]) for r in sub)
        if not pts:
            continue
        tag = "" if rules.bundles[k].certifies else " [MIRROR]"
        et = theory_exp(prim, k)
        ax.plot(*zip(*pts), "o", color=col[k], ms=3, alpha=.9)
        st = fit_stats(sub, *fit_window, et)
        if st:
            ax.plot(xs, st["C"] * xs ** st["e_fit"], "-", color=col[k], lw=1.1,
                    label=f"k={k}{tag}: fit n^{st['e_fit']:.3f}  |  Lean n^{et:.3f}  (R²_theory={st['R2_theory']:.4f})")
            ax.plot(xs, st["C_theory"] * xs ** et, "--", color=col[k], lw=0.9, alpha=.7)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("n (bits)" if prim != "QFT" else "w (bits)"); ax.set_ylabel("T (11 T/rotation)")
    ax.grid(True, which="both", alpha=.3)
    ax.legend(fontsize=7, loc="upper left",
              title=f"{PRIMITIVES[prim][0]}\nsolid: fit · dashed: Lean-proved exponent · {PRIMITIVES[prim][2]}", title_fontsize=6.5)
    fig.suptitle(f"{name} — {prim}: Qualtran cost vs the ForShor-proved asymptotic  [{cert_tag}]", fontsize=9)
    f = out_dir / f"{name}_{prim}.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return str(f)


def figure_stack(rows: Sequence[Dict[str, Any]], k: int, out_dir: Path, name: str, cert_tag: str) -> str:
    """The full Shor composition at one k: PP → QFT → modmul → modexp → order-finding, overlaid, showing
    how the exponent climbs by the composition (each layer adds its Lean-proved factor)."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10, 6.5))
    cmap = plt.get_cmap("plasma"); col = {p: cmap(i / (len(ORDER) - 1)) for i, p in enumerate(ORDER)}
    for prim in ORDER:
        pts = sorted((r["n"], r["T"]) for r in rows if r["primitive"] == prim and r["k"] == k)
        if pts:
            ax.plot(*zip(*pts), "-o", ms=3, color=col[prim],
                    label=f"{prim}: {PRIMITIVES[prim][0]}  (Lean n^{theory_exp(prim, k):.3f})")
    ns_all = sorted({r["n"] for r in rows})
    if ns_all:
        ax.plot(ns_all, [constants.SCHOOLBOOK_PREFACTOR * n * n for n in ns_all], "k:", lw=1.5, label="schoolbook n²")
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("n (bits)"); ax.set_ylabel("T (11 T/rotation)")
    ax.grid(True, which="both", alpha=.3); ax.legend(fontsize=8, loc="upper left")
    fig.suptitle(f"{name} — full Shor composition at k={k}: PP → QFT → modmul → modexp → order-finding  [{cert_tag}]", fontsize=9)
    f = out_dir / f"{name}_shor_stack_k{k}.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return str(f)


def figure_exponents(ledger: Sequence[Dict[str, Any]], ks: Sequence[int], out_dir: Path, name: str, cert_tag: str) -> str:
    """Fitted vs Lean-proved exponent, all primitives, vs k — with the n² and 2+ε reference lines."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9, 6))
    marks = {"PP": "o", "QFT": "s", "MULT": "v", "MODMUL": "D", "MODEXP": "^", "SHOR": "*"}
    for prim in ORDER:
        fit = [(e["k"], e["e_fit"]) for e in ledger if e["primitive"] == prim]
        if fit:
            ax.plot(*zip(*fit), marks[prim], ms=8, mfc="none", label=f"{prim}: fit")
        th = [(k, theory_exp(prim, k)) for k in ks]
        ax.plot(*zip(*th), "--", lw=0.8, alpha=.6)
    ax.axhline(2.0, color="k", ls=":", lw=1, label="n² / Shor n^{2+ε} envelope")
    ax.set_xlabel("k"); ax.set_ylabel("exponent of T"); ax.set_xticks(list(ks)); ax.grid(alpha=.3)
    ax.legend(fontsize=8, ncol=2); ax.set_ylim(1.0, 3.0)
    fig.suptitle(f"{name} — fitted vs ForShor-proved exponents, all primitives  [{cert_tag}]", fontsize=9)
    f = out_dir / f"{name}_exponents_vs_k.png"; fig.savefig(f, dpi=140, bbox_inches="tight"); plt.close(fig)
    return str(f)


# ------------------------------------------------------------------ one call
def run_all(bundles: Sequence[Any], out_dir: Path, ks: Sequence[int] = (2, 3, 4, 5, 6),
            n_hi: int = 8192, fit_window: Optional[Tuple[int, int]] = None, eta: Optional[float] = None,
            *, precision: str = "appropriate", delta: float = 1e-2) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rules = RuleSet([b for b in bundles if b.k in ks or b.k == 2])
    ks = [k for k in ks if k in rules.data]
    ns = dense_ns(64, n_hi)
    fit_window = fit_window or (max(256, n_hi // 8), n_hi)
    rows = sweep(rules, ks, ns, ORDER, eta, precision=precision, delta=delta)
    ledger = exponent_ledger(rows, fit_window, ks)
    mir = [k for k in ks if not rules.bundles[k].certifies]
    cert = "REAL-LEAN" if not mir else "NOT Lean-certified for k=" + ",".join(map(str, mir))
    figs: Dict[str, str] = {}
    for i, prim in enumerate(ORDER, 1):
        figs[f"A{i}"] = figure_primitive(rows, rules, ks, prim, out_dir, f"A{i}", fit_window, cert)
    figs["A6"] = figure_exponents(ledger, ks, out_dir, "A6", cert)
    figs["A7"] = figure_stack(rows, ks[0], out_dir, "A7", cert)
    man = {
        "schema": "forshor.asymptotics/v1",
        "note": "each primitive's dashed line is the exponent ForShor PROVES; R²_theory says how well it explains the Qualtran cost",
        "primitives": {p: {"description": PRIMITIVES[p][0], "composed": PRIMITIVES[p][1], "lean_bound": PRIMITIVES[p][2]} for p in ORDER},
        "ks": ks, "n_grid": ns, "fit_window": list(fit_window),
        "precision_policy": precision, "delta": delta,
        "eta": ("policy: %s (delta/n^2)" % precision if eta is None else eta),
        "verification_ledger": ledger, "figures": figs, "certification": cert,
        "provenance": {k: rules.bundles[k].provenance.value for k in ks},
        "verdict": "PASS" if not mir else "NOT CERTIFIED: mirror provenance",
    }
    (out_dir / "asymptotics_manifest.json").write_text(json.dumps(man, indent=1, default=str), encoding="utf-8")
    return man
