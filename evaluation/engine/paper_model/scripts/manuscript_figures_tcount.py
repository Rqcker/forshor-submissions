#!/usr/bin/env python3
"""Publication figures with an HONEST per-rotation T-count (new version of `manuscript_figures.py`).

    python scripts/manuscript_figures_tcount.py dump    # curves + rotation counts -> JSON
    python scripts/manuscript_figures_tcount.py plot    # JSON -> artifacts/manuscript_tcount/*.pdf

WHAT IS DIFFERENT FROM `manuscript_figures.py`
-----------------------------------------------
That script plots `QECGatesCost.total_t_count(ts_per_rotation=11)`. The 11 is
`forshor_qualtran.constants.TS_PER_ROTATION`, a placeholder -- it is not a measurement of
anything. Synthesising one rotation to the tolerance this construction actually needs costs

    eps = delta_rot / R        ->      3.075 * log2(1/eps) + 0.86   T

which at the widths plotted here is **130-170 T, not 11**. The correction is exact rather than a
refit, because `total_t_count` is affine in the per-rotation charge:

    T_base = T(11) - 11*R        T_true = T_base + R * t_per_rotation

so the existing curve plus the rotation count `R` is all that is needed; no sweep is re-run.
`Clifford_plus_T_scaling/tcount/` holds the budget, the measured angle statistics and the model.

THE ERROR BUDGET IS NOW EXPLICIT
--------------------------------
`--delta` (default 0.01) is split by `--rotation-fraction` (default 0.5) into a rotation-synthesis
share and a structural share. The structural share is what is handed to `price_model_b`, because
that is the knob its precision policy reads; the rotation share is divided equally among the `R`
rotations by the union bound. Both appear in the figure's own provenance block.

THE ASYMPTOTIC FORM CHANGES SHAPE, NOT ORDER
---------------------------------------------
Because `eps` itself depends on `n`, the fitted law gains one logarithm:

    order-finding, modular exponentiation     T ~ n^{2+eps} log(1/eps)
    multiply, controlled modmul, QFT, PP      T ~ n^{1+eps} log(1/eps)

The polynomial exponent is unchanged and still fixed by the construction; only the constant is
fitted, and R^2 therefore measures whether the data follow the predicted shape.

BASELINES ARE HELD TO THE SAME STANDARD
----------------------------------------
It would be unfair to price KMY at a matched, n-dependent `eps` while leaving the textbook
Fourier/AQFT line at a fixed published `eps = 1e-10`. Both are drawn: the published-convention
line, and a MATCHED-eps line that spends the same global budget over that construction's own
operation count (`64 n^3 log2 n` rotations for order-finding, HRS sec 4). The ripple-carry line
needs no such treatment -- it synthesises no rotations and is eps-independent by construction.

THE TREE, NOT JUST THE PRICE, IS HONEST FOR SHOR
--------------------------------------------------
The affine correction above re-prices a circuit built under a fixed Toom recursion depth, chosen
by a stop rule that assumes rotations cost the placeholder 11 T rather than the 130-170 T they
actually cost. A tree tuned for the wrong price is not necessarily the cheapest tree once the
honest price is known. For `stage in REOPT_STAGES` (SHOR only, for now), `dump` rebuilds the bloq
at every stop-price tree in `STOP_LADDER` -- the same ladder `tcount_fair_dump.py` uses -- and
keeps whichever tree minimises the HONEST T, not T(11). This is real extra construction cost (one
bloq build per tree, not a re-price of one build), unlike the affine correction; empirically, about
50s per k over the full n-grid x 12-tree ladder (SHOR), so a few minutes for all six arities.
"""
from __future__ import annotations

import argparse
import json
import math
import numpy as np
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PIPE_ROOT = Path(__file__).resolve().parent.parent
CTS_ROOT = PIPE_ROOT / "Clifford_plus_T_scaling"
DATA_DIR = PIPE_ROOT / "artifacts" / "figure_data_tcount"
OUT_DIR = PIPE_ROOT / "artifacts" / "manuscript_tcount"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if str(CTS_ROOT) not in sys.path:
    sys.path.insert(0, str(CTS_ROOT))

from tcount.budget import RotationBudget, ross_selinger_t          # noqa: E402
from tcount.model import (asymptotic_form, correct_t, fit_log_power_law,  # noqa: E402
                          load_sampled, rotation_cost)
from tcount.mix import census_rows                                  # noqa: E402
from tcount.schoolbook import SCHOOLBOOK                            # noqa: E402

STAGES: Dict[str, Dict[str, Any]] = {
    "MULT":   {"routine": "Toom–Cook multiplication",   "rule": "mult"},
    "QFT":    {"routine": "quantum Fourier transform",       "rule": "mult"},
    "MODMUL": {"routine": "controlled modular multiply",     "rule": "mult"},
    "MODEXP": {"routine": "modular exponentiation",          "rule": "exp"},
    "SHOR":   {"routine": "full order-finding",              "rule": "exp"},
}

FIT_WINDOW = (40000, 50000)
ZOOM_N = 2048
PLACEHOLDER = 11.0

#: Stages whose tree is RE-OPTIMISED against the honest per-rotation cost rather than left at the
#: placeholder-tuned default (stop_tsr=11). The recursion-stop rule decides how many Toom levels to
#: build assuming a rotation costs `tsr` T; pricing the result honestly afterward (the affine
#: `T_base + R*t_per_rotation` correction above) still prices a tree that was chosen without knowing
#: rotations actually cost 130-170 T. This sweeps the same stop-price ladder the fair-comparison
#: pipeline (`tcount_fair_dump.py`) uses and keeps, per (k, n), whichever tree minimises the HONEST
#: T rather than the placeholder T(11).
REOPT_STAGES = frozenset({"SHOR"})
STOP_LADDER = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 11.0, 25.0, 60.0, 150.0, 400.0)

#: Baselines kept in the cached JSON but NOT drawn. The schoolbook series was rebuilt from stated
#: components (see `tcount/schoolbook.py`) and that audit stays in the record; it is simply not
#: plotted. Note it was also mis-labelled on the MULT panel, where the underlying number is the
#: plain multiply rather than the in-place modular multiply.
HIDDEN_BASELINES = frozenset({"schoolbook"})

#: Presentation names, applied at plot time so the cached data need not be re-emitted.
LABEL_OVERRIDES = {"beauregard": "Beauregard"}

#: Inset placement [left, bottom, width, height] in axes coordinates. MULT and QFT carry few or no
#: baselines, so nothing crowds their inset and it stays put; everywhere else it drops clear of the
#: curves while keeping its own tick labels above the parent x-axis.
INSET_RECT_DEFAULT = (0.545, 0.105, 0.43, 0.30)
INSET_RECT_UNCROWDED = (0.545, 0.175, 0.43, 0.315)
UNCROWDED_STAGES = frozenset({"MULT", "QFT"})

#: the log ladder the measured cells sit on
LADDER = (2048, 4096, 8192, 16384, 32768, 50000)

#: Where the Beauregard bloq is priced. Pricing is ~n^2 per point and overflows past
#: n = 1024, so a log ladder rather than the full grid -- 7 points is a clean curve on log
#: axes and costs about 13 minutes in total (n = 1024 alone is 9.5 of them).
BEAU_NS = (16, 32, 64, 128, 256, 512, 1024)


def theory_exponent(stage: str, k: int) -> float:
    e = math.log(2 * k - 1, k)
    return e if STAGES[stage]["rule"] == "mult" else 1.0 + e


def textbook_rotations(stage: str, n: int) -> Optional[float]:
    """Rotation count of the textbook Fourier construction (HRS sec 4), for a matched-eps line."""
    ln = math.log2(max(n, 2))
    if stage in ("MODMUL",):
        return 32.0 * n ** 2 * ln
    if stage in ("MODEXP", "SHOR"):
        return 64.0 * n ** 3 * ln
    return None


def _point_reopt(stage: str, rules, k: int, n: int, H, precision, d_struct: float,
                 budget: RotationBudget, sampled, rows):
    """Build `stage`/`k`/`n` at every stop-price tree in `STOP_LADDER` and keep whichever minimises
    the HONEST T rather than the placeholder T(11) (see REOPT_STAGES).
    Returns (t11, r, stop_tsr) for the winning tree, in the same (T_at_placeholder, rotations)
    currency `dump` already stores, so `corrected_curve` downstream needs no changes."""
    import manuscript_figures_tcount_catalysis as MFCAT
    PP = H._precision_policy()
    eta = PP.eta_for(n, policy=precision, delta=d_struct)
    delta_eff = PP.composed_delta(n, policy=precision, delta=d_struct)
    best = None
    for tsr in STOP_LADDER:
        bl = MFCAT.build(stage, rules, k, n, eta, delta_eff, tsr)
        t11 = H.price(bl, n, "t")
        r = H.price(bl, n, "rotation")
        if not (t11 == t11 and t11 > 0 and r == r and r >= 0):
            continue
        if r > 0:
            rc = rotation_cost(k, n, r, budget, sampled=sampled, rows=rows)
            try:
                t_honest = correct_t(t11, r, rc.t_per_rotation, PLACEHOLDER)
            except ValueError:
                continue
        else:
            t_honest = t11 - PLACEHOLDER * r
        if best is None or t_honest < best[0]:
            best = (t_honest, t11, r, tsr)
    if best is None:
        raise RuntimeError(f"no feasible tree for {stage} k={k} n={n}")
    return best[1], best[2], best[3]


# ----------------------------------------------------------------------------- dump

def dump(stages: List[str], budget: RotationBudget, out_dir: Path = DATA_DIR) -> None:
    for p in (str(PIPE_ROOT), str(PIPE_ROOT / "py"), str(PIPE_ROOT / "notebooks")):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.chdir(PIPE_ROOT)
    import nb_config as C
    from nb_config import KS
    import baseline_reference as BR
    import notebook_helpers as H
    from forshor_qualtran import emit
    from forshor_qualtran.recursion_versions import RuleSet

    precision = getattr(C, "PRECISION_POLICY", None)
    bundles = {k: emit.emit_bundle(k) for k in KS}
    rules = RuleSet(list(bundles.values()))
    grid = [int(n) for n in C.N_GRID]
    out_dir.mkdir(parents=True, exist_ok=True)

    # the structural share drives the pricing policy; the rotation share is spent below
    d_struct = budget.delta_struct if budget.delta_struct > 0 else budget.delta
    sampled = rows = None   # loaded lazily -- only REOPT_STAGES need the honest per-point cost

    for stage in stages:
        reopt = stage in REOPT_STAGES
        if reopt and sampled is None:
            sampled, rows = load_sampled(), census_rows()
        curves = {}
        for k in KS:
            ns, ts, rs, tsrs = [], [], [], []
            for n in grid:
                try:
                    if reopt:
                        t, r, tsr = _point_reopt(stage, rules, k, n, H, precision, d_struct,
                                                 budget, sampled, rows)
                    else:
                        t = float(H.price_model_b(stage, rules, k, n, precision=precision,
                                                  delta=d_struct, what="t"))
                        r = float(H.price_model_b(stage, rules, k, n, precision=precision,
                                                  delta=d_struct, what="rotation"))
                        tsr = PLACEHOLDER
                except Exception:
                    continue
                if t == t and t > 0 and r == r and r >= 0:
                    ns.append(n)
                    ts.append(float(t))
                    rs.append(float(r))
                    tsrs.append(float(tsr))
            if ns:
                curves[str(k)] = {"n": ns, "T_at_placeholder": ts, "rotations": rs,
                                  "theory_exponent": theory_exponent(stage, k),
                                  **({"stop_tsr": tsrs} if reopt else {})}

        baselines = {}
        try:
            # DERIVED schoolbook, not `baseline_reference`'s bare `15 n^2`. Audited 2026-09-23:
            # that constant returns the SAME value for a plain multiply and for an IN-PLACE
            # CONTROLLED MODULAR multiply (`multiply_baseline_T(1000) == modmul_baseline_T(1000)`
            # is literally True), and it is smaller than the 2n Cuccaro ripple additions that
            # object is made of before any modular reduction is added -- so it cannot be an
            # in-place count at all. It is a sane PLAIN-multiply constant (1.9x the 2n^2 Toffoli
            # minimum) that was reused for the composed stages, where it lands 67-133x low. That,
            # not coarseness, is why the schoolbook line used to sit BELOW the KMY curves.
            #
            # `tcount.schoolbook` rebuilds it from stated components -- Cuccaro `2n-3` Toffoli
            # adder, VBE 4-adder modular addition, 2n modular additions per in-place controlled
            # modular multiply (n forward + n reverse, this repo's own `bloqs/adders.py`), 4 T
            # per Toffoli -- giving `64 n^2` T per modmul, 4.3x the constant. Its ratio to the
            # cited HRS line is exactly `2 log2 n`, the log factor being a property of HRS's
            # optimised construction rather than of the plain textbook composition; both lines
            # are drawn.
            #
            # Toffoli arithmetic synthesises nothing, so the only rotations here are the
            # order-finding read-out QFT, `w(w-1)/2` on `w = 2n`. Those ARE charged at the
            # matched tolerance, like everything else.
            sb_T = [float(SCHOOLBOOK.T(stage, n)) for n in grid]
            sb_rot = [float(SCHOOLBOOK.rotations(stage, n)) for n in grid]
            # the rotation term is not yet in sb_T; add it at the placeholder so the standard
            # `corrected_baseline` path re-charges it exactly like every other curve
            sb_T = [tt + PLACEHOLDER * rr for tt, rr in zip(sb_T, sb_rot)]
            baselines["schoolbook"] = {
                "n": grid, "T": sb_T, "rotations": sb_rot, "placeholder": PLACEHOLDER,
                "label": "schoolbook, in-place (Cuccaro + VBE, derived)"}
        except Exception:
            pass
        if stage in ("MODMUL", "MODEXP", "SHOR"):
            baselines["ripple"] = {
                "n": grid, "T": [float(BR.t_ripple(stage, n)) for n in grid],
                "label": r"ripple-carry $n^{3}\log n$ (HRS 2017, $\epsilon$-free)"}
            baselines["fourier_1e-10"] = {
                "n": grid, "T": [float(BR.t_fourier(stage, n, 1e-10)) for n in grid],
                "label": r"Fourier/AQFT, published $\epsilon=10^{-10}$"}
            # matched-eps: the same global budget spread over ITS own rotation count
            mt = []
            for n in grid:
                rt = textbook_rotations(stage, n)
                eps_t = budget.epsilon(rt)
                mt.append(float(BR._textbook_ops(stage, n)) * ross_selinger_t(eps_t))
            baselines["fourier_matched"] = {
                "n": grid, "T": mt,
                "label": r"Fourier/AQFT, matched $\epsilon=\delta_{\rm rot}/N_{\rm rot}$"}

            # Beauregard as a CONCRETE Qualtran build, not a closed form -- the same object
            # notebook 08 compares against, priced through `QECGatesCost` and therefore carrying
            # the same placeholder. It is a Draper phase-adder construction, so 42-70% of its T
            # at the placeholder is rotations and the correction is large: measured 3.0x to 6.9x
            # over n = 32..1024. It stops at n = 1024 because past that, vendored Qualtran's
            # `bloqs/rotations/phase_gradient.py:148` computes `exponent / 2**i` and overflows
            # float. The matched-eps Fourier line above is the same construction's closed form
            # and agrees with this one to within 20% where both exist (measured, n = 64..1024),
            # which is what licenses reading that line past the cutoff.
            bn, bt, br = [], [], []
            for nb_ in BEAU_NS:
                try:
                    tt = float(H.price_model_b(stage, rules, KS[0], nb_, precision=precision,
                                               delta=d_struct, method="beauregard", what="t"))
                    rr = float(H.price_model_b(stage, rules, KS[0], nb_, precision=precision,
                                               delta=d_struct, method="beauregard",
                                               what="rotation"))
                except Exception:
                    break
                bn.append(nb_)
                bt.append(tt)
                br.append(rr)
            if bn:
                baselines["beauregard"] = {
                    "n": bn, "T": bt, "rotations": br, "placeholder": PLACEHOLDER,
                    "label": "Beauregard (built bloq)"}

        doc = {
            "schema": "forshor.figure_data_tcount/v1",
            "stage": stage,
            "routine": STAGES[stage]["routine"],
            "exponent_rule": STAGES[stage]["rule"],
            "asymptotic_latex": asymptotic_form(STAGES[stage]["rule"])[0],
            "ks": list(KS),
            "n_grid": grid,
            "fit_window": list(FIT_WINDOW),
            "budget": {"delta": budget.delta, "rotation_fraction": budget.rotation_fraction,
                       "delta_rot": budget.delta_rot, "delta_struct": budget.delta_struct},
            "placeholder_ts_per_rotation": PLACEHOLDER,
            "provenance": {k: bundles[k].provenance.value for k in KS},
            "curves": curves,
            "baselines": baselines,
        }
        path = out_dir / f"{stage}_T_vs_n.json"
        path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        print(f"{stage:7s} -> {path.relative_to(PIPE_ROOT)}  "
              f"({len(curves)} arities, {len(baselines)} baselines, {len(grid)} points)")


# ----------------------------------------------------------------------------- plot

def _style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.dpi": 130, "savefig.dpi": 400, "font.size": 13,
        "axes.titlesize": 18, "axes.titleweight": "bold",
        "axes.labelsize": 16, "axes.labelweight": "bold",
        "xtick.labelsize": 13, "ytick.labelsize": 13, "legend.fontsize": 10.5,
        "axes.linewidth": 1.2, "xtick.major.width": 1.2, "ytick.major.width": 1.2,
    })
    return plt


def _bold_log_formatter(inner):
    """Wrap a log-axis tick formatter so its mathtext renders bold. Log tick labels are mathtext
    (`$\\mathdefault{2\\times10^{3}}$`), which ignores Text.set_fontweight -- so a post-hoc bold does
    nothing on log axes, most visibly on the minor-tick labels of a zoom window narrower than a decade.
    The wrapper keeps the inner formatter's choice of WHICH ticks to label and only swaps the font."""
    from matplotlib.ticker import Formatter

    class _Bold(Formatter):
        def set_axis(self, axis):
            super().set_axis(axis)
            inner.set_axis(axis)

        def set_locs(self, locs):
            inner.set_locs(locs)

        def __call__(self, x, pos=None):
            s = inner(x, pos)
            if s and s.startswith("$") and s.endswith("$"):
                body = s[1:-1].replace(r"\mathdefault", r"\mathbf")
                return "$" + (body if r"\mathbf" in body else r"\mathbf{" + body + "}") + "$"
            return s
    return _Bold()


def _bold_log_ticks(ax, x_minor: bool = True):
    """Bold log tick labels via the formatter (x: major + minor, y: major; y minors keep the default
    style -- bolding them overlapped labels in some stages)."""
    for axis, minor in ((ax.xaxis, x_minor), (ax.yaxis, False)):
        if axis.get_scale() != "log":
            continue
        axis.set_major_formatter(_bold_log_formatter(axis.get_major_formatter()))
        if minor:
            axis.set_minor_formatter(_bold_log_formatter(axis.get_minor_formatter()))


def _bold_ticks(ax):
    _bold_log_ticks(ax)
    for lab in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
        lab.set_fontweight("bold")


def _fit_legend(fig, ax, start: float = 10.5, minimum: float = 6.0, step: float = 0.5) -> float:
    """Shrink the legend font until no plotted data point falls inside the legend box.

    Only lines drawn in data coordinates count (the dashed n = 1024.. verticals are axes-blended and
    ignored). Returns the font size used; stops at `minimum` even if some overlap remains."""
    import numpy as np
    leg = ax.get_legend()
    if leg is None:
        return start
    pts = []
    for ln in ax.get_lines():
        if ln.get_transform() != ax.transData:
            continue
        xy = np.asarray(ln.get_xydata(), float)
        xy = xy[np.all(np.isfinite(xy), axis=1) & np.all(xy > 0, axis=1)]
        if len(xy):
            pts.append(ax.transData.transform(xy))
    if not pts:
        return start
    pts = np.vstack(pts)
    size = start
    while True:
        for t in leg.get_texts():
            t.set_fontsize(size)
        fig.canvas.draw()
        bb = leg.get_window_extent()
        inside = ((pts[:, 0] >= bb.x0) & (pts[:, 0] <= bb.x1) &
                  (pts[:, 1] >= bb.y0) & (pts[:, 1] <= bb.y1))
        if not inside.any() or size - step < minimum - 1e-9:
            return size
        size -= step


def corrected_baseline(b: Dict[str, Any], budget: RotationBudget) -> List[float]:
    """A baseline's T with its OWN rotations re-charged at the matched tolerance.

    A baseline that declares no `rotations` is returned untouched. That is the honest handling of
    the ripple-carry line (eps-free by construction -- it synthesises nothing, every arithmetic
    operation being an exactly-decomposed Toffoli) and of the two Fourier lines, which are already
    built at a stated eps. Applying a rotation correction to a model that does not say how many
    rotations it contains would be inventing a number rather than measuring one.
    """
    rots = b.get("rotations")
    if not rots:
        return list(b["T"])
    ph = float(b.get("placeholder", PLACEHOLDER))
    out = []
    for tt, r in zip(b["T"], rots):
        if r <= 0:
            out.append(float(tt))
            continue
        out.append(correct_t(tt, r, ross_selinger_t(budget.epsilon(r)), ph))
    return out


def corrected_curve(doc: Dict[str, Any], k: int, budget: RotationBudget,
                    sampled, rows) -> Dict[str, Any]:
    """The (k) curve with the placeholder charge replaced by the synthesised cost."""
    c = doc["curves"][str(k)]
    ns, ts, rs = c["n"], c["T_at_placeholder"], c["rotations"]
    out_n, out_t, out_eps, out_tpr, sources = [], [], [], [], []
    for n, t, r in zip(ns, ts, rs):
        if r <= 0:
            continue
        rc = rotation_cost(k, n, r, budget, sampled=sampled, rows=rows)
        try:
            out_t.append(correct_t(t, r, rc.t_per_rotation, PLACEHOLDER))
        except ValueError:
            continue
        out_n.append(n)
        out_eps.append(rc.eps)
        out_tpr.append(rc.t_per_rotation)
        sources.append(rc.source)
    return {"n": out_n, "T": out_t, "eps": out_eps, "t_per_rotation": out_tpr,
            "rotations": [r for r, n in zip(rs, ns) if n in set(out_n)],
            "sources": sources, "exponent": float(c["theory_exponent"])}


def plot_stage(doc: Dict[str, Any], budget: RotationBudget, out_dir: Path,
               zoom_n: int = ZOOM_N, extra: Optional[List[Dict[str, Any]]] = None,
               suffix: str = "", bar_inset: bool = False) -> List[Path]:
    """`extra`: additional series [{n, T, label, style kwargs...}]; `suffix` renames the outputs
    and skips the audit CSV (so the manuscript figure and its table are left untouched)."""
    plt = _style()
    from matplotlib.ticker import LogLocator

    sampled, rows = load_sampled(), census_rows()
    stage = doc["stage"]
    ks = [int(k) for k in doc["ks"] if str(k) in doc["curves"]]
    cmap = plt.get_cmap("viridis")
    col = {k: cmap(i / max(1, len(ks) - 1)) for i, k in enumerate(ks)}
    doc.get("asymptotic_latex", "")

    fig, ax = plt.subplots(figsize=(9.8, 6.8))
    grid = doc["n_grid"]
    curves = {k: corrected_curve(doc, k, budget, sampled, rows) for k in ks}

    for k in ks:
        cc = curves[k]
        e = cc["exponent"]
        st = fit_log_power_law(cc["n"], cc["T"], e, budget.delta_rot, cc["rotations"],
                               doc["fit_window"])
        label = (rf"$k={k}$:  ${st['C']:.2f}\,n^{{{e:.3f}}}\log_2(1/\epsilon)$,  $R^2 = {st['R2']:.3f}$"
                 if st else rf"$k={k}$")
        ax.loglog(cc["n"], cc["T"], marker="o", ms=3.2, lw=1.8, color=col[k], label=label)
        if st:
            fit_y = []
            for n, r in zip(cc["n"], cc["rotations"]):
                eps = budget.delta_rot / r
                fit_y.append(st["C"] * (n ** e) * math.log2(1.0 / eps))
            ax.loglog(cc["n"], fit_y, ls=(0, (5, 4)), lw=1.1, color=col[k], alpha=0.75)

    styles = {
        "schoolbook":      (":", "black", 2.0),
        "ripple":          ((0, (7, 3)), "tab:red", 2.0),
        "fourier_1e-10":   ((0, (1, 1.4)), "tab:purple", 2.2),
        "fourier_matched": ((0, (4, 2, 1, 2)), "tab:orange", 2.0),
        "beauregard":      ((0, (2, 1.5)), "tab:brown", 2.2),
    }
    corrected_baselines = {name: corrected_baseline(b, budget)
                           for name, b in doc["baselines"].items()}
    for name, b in doc["baselines"].items():
        if name in HIDDEN_BASELINES:
            continue
        ls, colr, lw = styles.get(name, ("-.", "tab:brown", 1.8))
        ax.loglog(b["n"], corrected_baselines[name], ls=ls, lw=lw, color=colr,
                  label=LABEL_OVERRIDES.get(name, b["label"]))

    for e in (extra or []):
        sty = dict(e["style"])
        ax.loglog(e["n"], e["T"], label=e["label"], **sty)

    for d in (1024, 2048, 4096, 8192):
        if grid[0] <= d <= grid[-1]:
            ax.axvline(d, ls="--", lw=0.7, color="0.72", zorder=0)

    ax.set_xlabel("$n$ (operand bits)", fontweight="bold")
    ax.set_ylabel("logical T-count", fontweight="bold")
    # No in-plot title -- the caption (artifacts/manuscript_tcount/captions.tex) carries it; a
    # figure with both a title and a caption duplicates the same information twice.
    ax.grid(True, which="both", ls=":", alpha=0.32)
    ax.xaxis.set_major_locator(LogLocator(base=10.0, numticks=12))
    _bold_ticks(ax)
    ax.legend(loc="upper left", framealpha=0.92)

    # The error partition (delta, delta_rot, delta_struct) is stated in the main text and the captions,
    # not on the figure.

    # --- inset: zoom near n = zoom_n, bottom-right, clear of the x-axis --------------------
    lo, hi = zoom_n / 1.7, zoom_n * 1.7
    rect = (INSET_RECT_UNCROWDED if stage in UNCROWDED_STAGES else INSET_RECT_DEFAULT)
    if bar_inset:
        # log-scaled bars at n = zoom_n: one per fixed arity k, plus the best varying-k policy
        def at_zoom(ns, ts):
            return float(np.exp(np.interp(math.log(zoom_n), np.log(ns), np.log(ts))))
        labels = [f"$k={k}$" for k in ks]
        vals = [at_zoom(curves[k]["n"], curves[k]["T"]) for k in ks]
        colors = [col[k] for k in ks]
        for e in (extra or []):
            labels.append("policy")
            vals.append(at_zoom(e["n"], e["T"]))
            colors.append(e["style"].get("color", "black"))
        ymin, ymax = 1e12, 5e12
        axb = ax.inset_axes([0.58, 0.14, 0.40, 0.28])
        axb.bar(range(len(vals)), [v - ymin for v in vals], bottom=ymin, color=colors, width=0.75)
        axb.set_yscale("log")
        axb.set_ylim(ymin, ymax)
        axb.set_xticks(range(len(vals)))
        axb.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
        axb.set_title(rf"$n = {zoom_n}$", fontsize=11, fontweight="bold", pad=3)
        axb.tick_params(labelsize=9)
        axb.grid(True, which="both", axis="y", ls=":", alpha=0.35)
        for s in axb.spines.values():
            s.set_linewidth(1.1)
        axb.patch.set_alpha(0.96)
        axin = None
    else:
        axin = ax.inset_axes(list(rect))
    if axin is not None:
        for k in ks:
            cc = curves[k]
            xs = [x for x in cc["n"] if lo <= x <= hi]
            ys = [y for x, y in zip(cc["n"], cc["T"]) if lo <= x <= hi]
            if xs:
                axin.loglog(xs, ys, marker="o", ms=3.0, lw=1.6, color=col[k])
        for name, b in doc["baselines"].items():
            if name in HIDDEN_BASELINES:
                continue
            ls, colr, lw = styles.get(name, ("-.", "tab:brown", 1.4))
            cb = corrected_baselines[name]
            xs = [x for x in b["n"] if lo <= x <= hi]
            ys = [y for x, y in zip(b["n"], cb) if lo <= x <= hi]
            if xs:
                axin.loglog(xs, ys, ls=ls, lw=lw, color=colr)
        for e in (extra or []):
            pts = [(x, y) for x, y in zip(e["n"], e["T"]) if lo <= x <= hi]
            if pts:
                axin.loglog([q[0] for q in pts], [q[1] for q in pts], **e["style"])
        axin.axvline(zoom_n, ls="--", lw=1.0, color="0.45")
        axin.set_title(rf"zoom: $n \approx {zoom_n}$", fontsize=11, fontweight="bold", pad=3)
        axin.tick_params(labelsize=9)
        _bold_ticks(axin)
        axin.grid(True, which="both", ls=":", alpha=0.35)
        for s in axin.spines.values():
            s.set_linewidth(1.1)
        axin.patch.set_alpha(0.96)

    fig.tight_layout()
    _fit_legend(fig, ax)                       # shrink the legend text if it covers any data
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for ext in ("pdf", "png"):
        p = out_dir / f"fig_{stage.lower()}_T_vs_n_tcount{suffix}.{ext}"
        fig.savefig(p, bbox_inches="tight")
        paths.append(p)
    plt.close(fig)

    if suffix:
        return paths
    # a per-figure table of what each point was charged, so the correction is auditable
    tbl = out_dir / f"tcount_{stage.lower()}.csv"
    with tbl.open("w", encoding="utf-8") as fh:
        fh.write("k,n,rotations,eps,t_per_rotation,mix_source,T_placeholder_11,T_corrected,factor\n")
        for k in ks:
            cc, raw = curves[k], doc["curves"][str(k)]
            lookup = dict(zip(raw["n"], raw["T_at_placeholder"]))
            for n, t, eps, tpr, src, r in zip(cc["n"], cc["T"], cc["eps"], cc["t_per_rotation"],
                                              cc["sources"], cc["rotations"]):
                t11 = lookup.get(n, float("nan"))
                fh.write(f"{k},{n},{r:.6e},{eps:.6e},{tpr:.4f},{src},{t11:.6e},{t:.6e},"
                         f"{(t / t11 if t11 else float('nan')):.4f}\n")
    paths.append(tbl)
    return paths


def plot(stages: List[str], budget: RotationBudget, data_dir: Path = DATA_DIR,
         out_dir: Path = OUT_DIR) -> None:
    for stage in stages:
        src = data_dir / f"{stage}_T_vs_n.json"
        if not src.is_file():
            print(f"{stage:7s} -- no cached data at {src.relative_to(PIPE_ROOT)}; run `dump` first")
            continue
        doc = json.loads(src.read_text(encoding="utf-8"))
        for p in plot_stage(doc, budget, out_dir):
            print(f"{stage:7s} -> {p.relative_to(PIPE_ROOT)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["dump", "plot", "both"])
    ap.add_argument("--stages", nargs="*", default=list(STAGES))
    ap.add_argument("--delta", type=float, default=0.01)
    ap.add_argument("--rotation-fraction", type=float, default=0.5)
    ap.add_argument("--zoom-n", type=int, default=ZOOM_N)
    args = ap.parse_args(argv)
    stages = [s.upper() for s in args.stages]
    bad = [s for s in stages if s not in STAGES]
    if bad:
        ap.error(f"unknown stage(s) {bad}; have {list(STAGES)}")
    budget = RotationBudget(delta=args.delta, rotation_fraction=args.rotation_fraction)
    print(budget.describe(), "\n")
    if args.action in ("dump", "both"):
        dump(stages, budget)
    if args.action in ("plot", "both"):
        plot(stages, budget)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
