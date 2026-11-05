"""Helper module for `00_subroutine_composition.ipynb` — the ONLY new code the notebook depends on.

Everything here routes through the already-tested `forshor_qualtran` API (no re-derivation of pricing):

  * pricing            = ``get_cost_value(bloq, QECGatesCost())`` → ``total_t_count`` / ``rotation`` /
                         ``and_bloq`` / ``clifford`` (+ ``QubitCount`` for logical qubits);
  * Model-B objects    = the single-k, Lean-proved recursion (``ToomRecursivePhaseProduct`` version "B",
                         ``LeanQFT/LeanCtrlModMul/LeanModExp`` ``core="versionB"``), exactly as
                         ``asymptotics.bloq_for`` builds them;
  * per-level-optimal  = ``ToomRecursivePhaseProduct`` version "C" (per-level-optimal k). NOTE: the current
                         API has NO version-C core for the COMPOSED bloqs (QFT/modmul/modexp accept only
                         onelevel|recursive|versionB), so ``price_version_c`` prices the recursive
                         PhaseProduct at version C — the genuine per-level-optimal asymptote — and raises a
                         clean, catchable error for the composed primitives rather than fabricating one.
  * exponent fits      = ``golden.fit_stats`` (re-exported as ``asymptotics.fit_stats``);
  * KMY currency       = ``kmy_reference.to_kmy_currency`` (rotation↔CR_φ, and_bloq↔Toffoli; T-count native).

The T-depth here is the **in-place (sequential) construction** we emit — NOT KMY's optimal parallel/low
depth (that is a different point on the space–time tradeoff, drawn only as a labelled reference line).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---- package API (all already tested; the notebook never re-implements pricing) ------------------
from forshor_qualtran import constants, widths
from forshor_qualtran.golden import fit_stats                      # (rows, n_lo, n_hi, e_theory) -> dict
from forshor_qualtran.recursion_versions import RuleSet, ToomRecursivePhaseProduct, theory_exponent
from forshor_qualtran import kmy_reference

from qualtran.resource_counting import QECGatesCost, SympySymbolAllocator, get_cost_value
from qualtran.symbolics import smax
try:                                                               # QubitCount lives here across versions
    from qualtran.resource_counting import QubitCount
except Exception:                                                  # pragma: no cover - version drift guard
    QubitCount = None

N_SYM = widths.n                                                   # the single symbolic operand width


# ================================================================ 1.1b cheap qubit-count  ==========
# PERFORMANCE FIX 2026-09-03: `get_cost_value(bloq, QubitCount())` tries `bloq.decompose_bloq()` FIRST
# (qualtran's own "most accurate" strategy) before falling back to a cheap signature+callee estimate.
# For `qualtran.bloqs.arithmetic.Add`/`Negate` — THIRD-PARTY bloqs this codebase's PhaseProduct/QFT/
# multiplier/modmul/modexp objects all embed via `build_call_graph` — `decompose_bloq()` succeeds (they
# implement it) and materializes an actual W-qubit-wide circuit graph, which is CATASTROPHICALLY slow
# at the widths this notebook sweeps to (isolated and confirmed: `Add(QInt(2048)).decompose_bloq()`
# alone hangs past 60s, with NOTHING of this package's own code involved). `QECGatesCost`/T-count never
# hits this because it uses `Add.build_call_graph()` — a cheap closed form, `{And(): n-1, ...}` — not
# decomposition; only `QubitCount`'s decompose-first preference does.
#
# `_cheap_qubit_count` reaches the SAME estimate `QubitCount`'s own fallback branch would give (max of
# the signature width and every callee's footprint) but ALWAYS via `build_call_graph` (cheap for every
# bloq actually appearing here — confirmed for Add/Negate/LeanPhaseProduct/ToomRecursivePhaseProduct/
# LeanQFT), never via decomposition. This is not a new estimation strategy: it is qualtran's own
# documented "likely an under-estimate... reasonable" fallback (`QubitCount.compute`'s docstring),
# just reached without ever paying for the expensive branch. Where this package's own composed bloqs
# (`LeanPhaseProduct`, `ToomRecursivePhaseProduct`, `LeanCtrlModMul`, `LeanModExp`,
# `ToomCookMultiplier`, ...) already expose an exact closed-form `.qubit_footprint`, that is used
# directly and the recursion stops there.
def _cheap_qubit_count(bloq, _memo: Optional[dict] = None):
    """Qubit-count estimate for `bloq`, without ever calling qualtran's decompose-first `QubitCount()`.
    See the module note above for why. Memoized within one top-level call (repeated sub-bloqs, e.g. the
    same twiddle width appearing at multiple QFT split levels, are only walked once)."""
    memo = {} if _memo is None else _memo
    try:
        key = bloq
        cached = key in memo
    except TypeError:                                  # unhashable bloq (shouldn't happen; be safe)
        key, cached = None, False
    if cached:
        return memo[key]
    if hasattr(bloq, "qubit_footprint"):
        val = bloq.qubit_footprint
    else:
        val = bloq.signature.n_qubits()
        try:
            callees = bloq.build_call_graph(SympySymbolAllocator())
        except Exception:
            callees = {}
        for callee, _mult in (callees.items() if isinstance(callees, dict) else callees):
            val = smax(val, _cheap_qubit_count(callee, memo))
    if key is not None:
        memo[key] = val
    return val


# ================================================================ 1.1  n_grid  ====================
def n_grid(lo: int = 4, hi: int = 50000, per_decade: int = 24,
           fit_window: Tuple[int, int] = (40000, 50000), fit_points: int = 8) -> List[int]:
    """Log-spaced integer sweep grid, UNIONED with `fit_points` points across `fit_window` so the
    asymptotic fit is stable in the narrow band. Deduped, ascending, pure stdlib. (Mirrors nb_config.)"""
    lo, hi = int(lo), int(hi)
    steps = max(2, int(round(per_decade * math.log10(hi / lo))))
    vals = {int(round(lo * (hi / lo) ** (i / steps))) for i in range(steps + 1)}
    fw_lo, fw_hi = int(fit_window[0]), int(fit_window[1])
    vals |= {int(round(fw_lo + i * (fw_hi - fw_lo) / (fit_points - 1))) for i in range(fit_points)}
    return [v for v in sorted(vals) if lo <= v <= hi]


# ================================================================ 1.2  price  =====================
_WHAT = ("t", "qubits", "rotation", "and", "clifford", "toffoli")


def price(bloq, n: Optional[int] = None, what: str = "t",
          ts_per_rotation: float = constants.TS_PER_ROTATION) -> float:
    """Scalar cost of a priced bloq. `what`: 't' (total_t_count), 'qubits', 'rotation' (↔CR_φ),
    'and'/'toffoli' (↔Toffoli), 'clifford'. If the bloq is symbolic in `widths.n`, pass `n` to substitute.
    A concrete-by-construction bloq (Toom* objects are built at a fixed width) ignores `n`."""
    if what not in _WHAT:
        raise ValueError(f"what={what!r} not in {_WHAT}")
    if what == "qubits":
        val = _cheap_qubit_count(bloq)
        return float(_subs(val, n))
    qec = get_cost_value(bloq, QECGatesCost())
    if what == "t":
        val = qec.total_t_count(ts_per_rotation=ts_per_rotation)
    elif what == "rotation":
        val = qec.rotation
    elif what in ("and", "toffoli"):
        val = qec.and_bloq
    else:  # clifford
        val = qec.clifford
    return float(_subs(val, n))


def _subs(val, n: Optional[int]) -> float:
    """Substitute widths.n -> n if `val` is a sympy expression carrying it; else return as-is."""
    try:
        free = getattr(val, "free_symbols", set())
    except Exception:
        free = set()
    if n is not None and free:
        val = val.subs(N_SYM, int(n))
    return val


# ================================================================ 1.x  Model-B / version-C objects =
# Mirrors asymptotics.bloq_for, with `version` threaded where the API supports it.
def _rules(bundles: Dict[int, Any]) -> RuleSet:
    """Build a RuleSet from a {k: Bundle} dict (k=2 must be present — version A/C need it)."""
    return RuleSet(list(bundles.values()))


def _precision_policy():
    """Lazy import of `py/precision_policy` (pure stdlib; avoids pulling qualtran)."""
    import importlib, sys
    from pathlib import Path
    pydir = str(Path(__file__).resolve().parent.parent / "py")
    if pydir not in sys.path:
        sys.path.insert(0, pydir)
    return importlib.import_module("precision_policy")


def eta_for(n: int, policy: str = "appropriate", delta: float = 1e-2, fixed_eta: float = 1e-12) -> float:
    """Public accessor for the per-modmul precision η under `policy` (`py/precision_policy.eta_for`).
    Use in cells that build a bloq directly via `_bloq_b` so they honour the same toggle as
    `price_model_b`: e.g. `H._bloq_b(prim, rules, k, n, H.eta_for(n, PRECISION_POLICY, DELTA))`."""
    return _precision_policy().eta_for(int(n), policy=policy, delta=delta, fixed_eta=fixed_eta)


def price_model_b(prim: str, rules: RuleSet, k: int, n: int, *, eta: Optional[float] = None,
                  precision: str = "appropriate", delta: float = 1e-2, fixed_eta: float = 1e-12,
                  method: str = "kmy", what: str = "t") -> float:
    """Model B (single-k, Lean-proved) cost of `prim` at (k, n). `prim ∈ {PP,QFT,MULT,MODMUL,MODEXP,SHOR}`.

    Per-modmul precision η is chosen by `precision` (`py/precision_policy.eta_for`):
      * "appropriate" (default) → η = shorEta(δ, n) = δ/n²  — the Lean-defined, n-scaling precision;
      * "fixed"                 → η = `fixed_eta`           — an n-independent pin (legacy).
    The SAME η drives every stage: MODMUL takes `eta=η`, and MODEXP/SHOR take `delta=η·n²` so their
    internal `shorEta` reproduces exactly η. Passing `eta=` explicitly overrides the policy for
    MODMUL and leaves MODEXP/SHOR on their own δ=1e-2 (backward-compatible legacy path)."""
    n = int(n)
    if eta is None:
        PP = _precision_policy()
        eta = PP.eta_for(n, policy=precision, delta=delta, fixed_eta=fixed_eta)
        delta_eff = PP.composed_delta(n, policy=precision, delta=delta, fixed_eta=fixed_eta)
    else:
        delta_eff = 1e-2   # legacy: explicit eta drives MODMUL only; composed stages keep δ=1e-2
    return price(_bloq_b(prim, rules, k, n, eta, delta_eff, method), n, what)


def _bloq_b(prim: str, rules: RuleSet, k: int, n: int, eta: float, delta: float = 1e-2, method: str = "kmy"):
    from forshor_qualtran.bloqs import LeanCtrlModMul, LeanModExp, LeanQFT, ToomCookMultiplier
    from forshor_qualtran.bloqs.orderfinding import LeanOrderFinding
    b = rules.bundles[k]
    if prim == "PP":
        return ToomRecursivePhaseProduct.from_rules(rules, n, k, "B", "kmy")
    if prim == "QFT":
        return LeanQFT.from_bundle(b, n, "tight", core="versionB", rules=rules)
    if prim == "MULT":
        return ToomCookMultiplier.from_rules(rules, b.doc, n)
    if prim == "MODMUL":
        # SUBSTITUTION POINT (standalone modmul): KMY LeanCtrlModMul | Beauregard drop-in.
        if method == "beauregard":
            from forshor_qualtran.bloqs.beauregard_modmul import BeauregardCtrlModMul
            return BeauregardCtrlModMul(n=n, N=(1 << n) - 1, delta=delta)
        return LeanCtrlModMul.from_bundle(b, n, eta=eta, N=(1 << n) - 1, policy="tight",
                                          core="versionB", qft="lean", rules=rules)
    if prim == "MODEXP":
        # δ chosen so LeanModExp's own shorEta(δ,n) == the policy η (delta = η·n²); default δ=1e-2.
        # `method` swaps ONLY the per-step modular multiply; the tbits-fold frame is unchanged.
        return LeanModExp.from_bundle(b, n, delta=delta, N=(1 << n) - 1, policy="tight",
                                      core="versionB", qft="lean", method=method, rules=rules)
    if prim == "SHOR":
        # Phase E: H_reg(x) ;; initY1(y) ;; modExp ;; IQFT(x) -- composed cost of Phase D (modexp) +
        # Phase A (IQFT); frame is Clifford. `method` swaps ONLY the modmul; frame/IQFT unchanged.
        return LeanOrderFinding.from_bundle(b, n, delta=delta, N=(1 << n) - 1, policy="tight",
                                            core="versionB", qft="lean", method=method, rules=rules)
    raise ValueError(f"unknown primitive {prim!r}")


class VersionCUnavailable(RuntimeError):
    """Raised when per-level-optimal (version C) pricing is requested for a composed primitive whose
    current API exposes no version-C core. Callers catch this and report it honestly (no fabrication)."""


def price_version_c(prim: str, rules: RuleSet, k: int, n: int, *, what: str = "t") -> float:
    """Per-level-optimal (version C) cost. Only the recursive PhaseProduct (`prim="PP"`) has a real
    version-C priced object in the current API; composed primitives raise `VersionCUnavailable`."""
    if prim != "PP":
        raise VersionCUnavailable(
            f"{prim}: no version-C core in the current API "
            f"(QFT/modmul/modexp accept onelevel|recursive|versionB only). "
            f"Per-level-optimal is shown at the PhaseProduct level (B2).")
    return price(ToomRecursivePhaseProduct.from_rules(rules, int(n), k, "C", "kmy"), int(n), what)


# ================================================================ 1.3  plot_resource_sweep  =======
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def plot_resource_sweep(price_fn: Callable[[int, int], float], ks: Sequence[int], ylabel: str, *,
                        kind: str = "t", title: str = "", subtitle: Optional[str] = None,
                        baseline: Optional[Callable[[int], float]] = None, baseline_label: str = "naive",
                        dashed: Sequence[int] = (1024, 2048, 4096, 8192),
                        n_grid: Optional[Sequence[int]] = None,
                        measured: Optional[Sequence[Dict[str, Any]]] = None,
                        theory: Optional[Callable[[int], float]] = None, theory_label: str = "KMY asymptote",
                        fit_window: Tuple[int, int] = (40000, 50000),
                        xlabel: str = "n (operand bits)"):
    """One log–log curve per k of `price_fn(k, n)` over `n_grid`; vertical dashed reference lines at
    `dashed`; optional dotted-black `baseline(n)`; optional thin-dashed per-k `theory(k)` asymptote fitted
    to the curve's own intercept in `fit_window`; optional `measured` overlay markers. `subtitle` is drawn
    under the title (used for 'model, not Lean-verified' / 'in-place depth ...'). Returns the Figure.
    The caller saves it to artifacts/subroutines/."""
    plt = _plt()
    ng = list(n_grid) if n_grid is not None else globals()["n_grid"]()
    cmap = plt.get_cmap("viridis")
    col = {k: cmap(i / max(1, len(ks) - 1)) for i, k in enumerate(ks)}
    fig, ax = plt.subplots(figsize=(9.5, 6.5))

    for k in ks:
        xs, ys = [], []
        for n in ng:
            try:
                v = float(price_fn(k, n))
            except Exception:
                v = float("nan")
            if v == v and v > 0:                          # finite & positive
                xs.append(n); ys.append(v)
        if not xs:
            continue
        legend = f"k={k}"
        # per-k fit in the (asymptotic) window
        st = fit_stats([{"n": x, "T": y} for x, y in zip(xs, ys)], fit_window[0], fit_window[1],
                       theory(k) if theory else theory_exponent(k))
        if st:
            legend = (f"k={k}:  fit n^{st['e_fit']:.3f} (R²={st['R2_fit']:.3f})"
                      f"  |  KMY n^{st['e_theory']:.3f} (R²_th={st['R2_theory']:.3f})")
        ax.loglog(xs, ys, marker="o", ms=3, lw=1.4, color=col[k], label=legend)
        # thin-dashed theory asymptote across the whole x-range (fitted intercept)
        if theory is not None and st is not None:
            c_th = st["C_theory"]; e_th = st["e_theory"]
            ax.loglog(ng, [c_th * (x ** e_th) for x in ng], ls=(0, (5, 4)), lw=0.9, color=col[k], alpha=0.7)

    if theory is not None:                                # one proxy handle for the per-k thin-dashed family
        ax.plot([], [], ls=(0, (5, 4)), lw=0.9, color="0.4", label=theory_label + " (thin dashed per k; exponent in each k's label)")
    if baseline is not None:
        ax.loglog(ng, [baseline(x) for x in ng], ls=":", lw=1.4, color="black", label=baseline_label)
    if measured:
        mx = [r["n"] for r in measured if r.get("n") and r.get("y")]
        my = [r["y"] for r in measured if r.get("n") and r.get("y")]
        if mx:
            ax.scatter(mx, my, marker="x", s=42, color="crimson", zorder=5, label="measured")

    for d in dashed:
        ax.axvline(d, ls="--", lw=0.7, color="0.6", zorder=0)
        ax.text(d, ax.get_ylim()[0], f" {d}", fontsize=7, color="0.5", va="bottom", rotation=90)

    ax.set_xlabel(xlabel); ax.set_ylabel(ylabel)
    ax.grid(True, which="both", ls=":", alpha=0.35)
    ax.legend(fontsize=7.5, loc="upper left")
    ttl = title
    if subtitle:
        ax.set_title(subtitle, fontsize=9, color="0.35", loc="left", pad=2)
        fig.suptitle(ttl, fontsize=12, x=0.02, ha="left")
    else:
        ax.set_title(ttl, fontsize=12, loc="left")
    fig.tight_layout()
    return fig


# ================================================================ 1.4  depth_active (IN-PLACE)  ====
def depth_active(bloq, n: Optional[int] = None,
                 ts_per_rotation: float = constants.TS_PER_ROTATION) -> Dict[str, float]:
    """Leading-order T-depth and active volume of the emitted **in-place (sequential)** object.

    In the ancilla-free sequential construction the circuit is one long chain, so its T-depth is, to
    leading order, its T-count (depth ~ gate count). Active volume (Litinski) ≈ logical-qubits × T-depth.
    This is explicitly NOT KMY's optimal parallel/low depth (a different construction with O(n/log n)
    ancillas) — that appears only as a reference line on figure (c). Returns leading-order floats and the
    layer breakdown used, so the notebook can print an auditable walkthrough."""
    qec = get_cost_value(bloq, QECGatesCost())
    rot = float(_subs(qec.rotation, n))
    andc = float(_subs(qec.and_bloq, n))
    cliff = float(_subs(qec.clifford, n))
    t_total = float(_subs(qec.total_t_count(ts_per_rotation=ts_per_rotation), n))
    t_depth = t_total                                   # in-place: sequential chain, depth ~ T-count
    try:
        qubits = price(bloq, n, "qubits")
    except Exception:
        qubits = float("nan")
    active = qubits * t_depth if qubits == qubits else float("nan")
    return {"t_depth": t_depth, "active_volume": active, "qubits": qubits,
            "layers_rotation": rot, "layers_and": andc, "layers_clifford": cliff,
            "ts_per_rotation": float(ts_per_rotation)}


def tdepth_walkthrough(label: str, bloq, n: int) -> str:
    """One plain-text block (per k) showing exactly how the plotted in-place T-depth was reached — for the
    SAME-CELL printout beside figure (c). Numbers come from the same priced object the figure uses."""
    d = depth_active(bloq, n)
    return (f"  {label} @ n={n}:  rotations(CR_φ)={d['layers_rotation']:.3g}  "
            f"And(Toffoli)={d['layers_and']:.3g}  Clifford={d['layers_clifford']:.3g}\n"
            f"      T_depth ≈ total_t_count (in-place sequential chain) "
            f"= {d['layers_rotation']:.3g}·{d['ts_per_rotation']:.1f}(T/rot) + And·T "
            f"→ T_depth = {d['t_depth']:.4g},  active = qubits({d['qubits']:.3g})·T_depth "
            f"= {d['active_volume']:.4g}")


# ================================================================ 1.5  exponent_table  ============
def exponent_table(price_fn: Callable[[int, int], float], ks: Sequence[int],
                   e_theory_fn: Callable[[int], float], window: Tuple[int, int],
                   n_grid: Optional[Sequence[int]] = None) -> List[Dict[str, Any]]:
    """Per k: e_fit, R2_fit, e_theory, R2_theory, Δ = e_fit − e_theory (via golden.fit_stats). Rows are
    plain dicts (wrap in pandas.DataFrame in the notebook)."""
    ng = list(n_grid) if n_grid is not None else globals()["n_grid"]()
    out: List[Dict[str, Any]] = []
    for k in ks:
        rows = []
        for n in ng:
            try:
                v = float(price_fn(k, n))
            except Exception:
                continue
            if v == v and v > 0:
                rows.append({"n": n, "T": v})
        st = fit_stats(rows, window[0], window[1], float(e_theory_fn(k)))
        if st is None:
            out.append({"k": k, "e_fit": None, "R2_fit": None, "e_theory": round(float(e_theory_fn(k)), 4),
                        "R2_theory": None, "delta": None, "n_points": 0})
        else:
            out.append({"k": k, "e_fit": round(st["e_fit"], 4), "R2_fit": round(st["R2_fit"], 4),
                        "e_theory": round(st["e_theory"], 4), "R2_theory": round(st["R2_theory"], 4),
                        "delta": round(st["e_fit"] - st["e_theory"], 4), "n_points": st["n_points"]})
    return out


# ================================================================ 1.6  kmy_value_row  =============
def kmy_value_row(bloq, n: int, label: str) -> Dict[str, Any]:
    """T-count (native) + KMY currency (CR_φ, Toffoli, Clifford) + qubits for a priced object at n —
    the row placed beside kmy_reference.anchor(...) for a values comparison."""
    qec = get_cost_value(bloq, QECGatesCost())
    cur = kmy_reference.to_kmy_currency(qec)
    try:
        qubits = price(bloq, n, "qubits")
    except Exception:
        qubits = None
    return {"which": label, "n": n,
            "T_count": float(_subs(qec.total_t_count(ts_per_rotation=constants.TS_PER_ROTATION), n)),
            "CR_phi": _num(cur.get("crphi"), n), "Toffoli": _num(cur.get("toffoli"), n),
            "Clifford": _num(cur.get("clifford"), n), "qubits": qubits}


def _num(v, n):
    if v is None:
        return None
    return float(_subs(v, n))


# ================================================================ 1.7  self-check  ================
def _selfcheck(bundles: Dict[int, Any], k: int = 2) -> Dict[str, Any]:
    """Smoke test on a bundle dict (mirror is fine): price a PP object, fit an exponent table, get a
    depth/active pair. Returns a dict of facts; raises if anything is structurally wrong."""
    rules = _rules(bundles)
    fig = plot_resource_sweep(lambda kk, nn: price_model_b("PP", rules, kk, nn), [k],
                              ylabel="T-count", title="selfcheck", subtitle="model, not Lean-verified",
                              n_grid=n_grid(4, 512, per_decade=8), dashed=(256,), theory=theory_exponent)
    tbl = exponent_table(lambda kk, nn: price_model_b("PP", rules, kk, nn), [k],
                         kmy_reference.theory_exponent_cq, (128, 512), n_grid=n_grid(4, 512, per_decade=8))
    da = depth_active(ToomRecursivePhaseProduct.from_rules(rules, 64, k, "B", "kmy"), 64)
    ok = (fig is not None and len(tbl) == 1 and set(tbl[0]) >= {"e_fit", "R2_fit", "e_theory", "R2_theory"}
          and da["t_depth"] == da["t_depth"])
    return {"ok": bool(ok), "table_row": tbl[0], "depth_active": da}
