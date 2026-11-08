#!/usr/bin/env python3
"""[SUPERSEDED as the figure by scripts/manuscript_proven_policy_shor.py, which uses ForShor-proven policies.]
Order finding, T vs n: the manuscript's k = 4 curve against the best R2-model policy at every n.

    python scripts/manuscript_policy_shor.py check            # the faithfulness gate only (~1 min)
    python scripts/manuscript_policy_shor.py dump [--workers 8]
    python scripts/manuscript_policy_shor.py plot

THE k = 4 CURVE IS NOT RECOMPUTED. It is read from `artifacts/figure_data_tcount/SHOR_T_vs_n.json`
(the data behind `artifacts/manuscript_tcount/fig_shor_T_vs_n_tcount.png`) and priced by the same
`corrected_curve` that drew that figure.

THE POLICY CURVE IS PRICED IN THE SAME STACK. That curve is `LeanOrderFinding(core="versionB")`: every
phase product and QFT twiddle a `ToomRecursivePhaseProduct` under rule R2 (tight widths, operands
zero-extended to a multiple of k, cost-optimal stop), the tree re-chosen per n over `STOP_LADDER` for
the lowest honest T. The only thing changed here is `pp_version`: "B" puts Toom-4 at every level, "C"
puts the cheapest loaded arity at every width -- under R2 at a given stop price that is the optimal
width-indexed policy (the decision at width w depends on w alone, so every site shares one policy).
Everything else -- the host bundle (k = 4: QFT split rule, modmul template), eta, the error budget, the
stop-price ladder, the per-rotation synthesis cost including the k = 4 angle-mix ratio -- is identical.

Gate (`check`, also run first by `dump`): the stack with pp_version="B" at each point's recorded stop
price reproduces the stored k = 4 T(11) and rotation count exactly. Best policy = min(version-C trees
over the ladder, the k = 4 point itself), so it is never above the k = 4 curve.

Scope: R2-rule policies, the cost model the manuscript curve uses -- not plans ForShor proves (those
use the proved signedPlan widths; see artifacts/policy_shor/). Catalogs: k = 2..7 (the manuscript's
arities; the k = 5..7 bundles predate ForShor's current standard tables, roundtable C-20) and k = 2..4
(bundle tables equal to ForShor's current ones).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT / "scripts"), str(ROOT / "notebooks"), str(ROOT / "py"), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import manuscript_figures_tcount as RSFIG                                  # noqa: E402
from tcount.budget import RotationBudget                                   # noqa: E402
from tcount.mix import census_rows                                         # noqa: E402
from tcount.model import correct_t, load_sampled, rotation_cost            # noqa: E402

HOST_K = 4
SRC = RSFIG.DATA_DIR / "SHOR_T_vs_n.json"
DATA = ROOT / "artifacts" / "figure_data_tcount" / "SHOR_policy_vs_k4_R2model.json"
OUT = ROOT / "artifacts" / "manuscript_tcount"
CATALOGS = {"k2-7": (2, 3, 4, 5, 6, 7), "k2-4": (2, 3, 4)}
GATE_EVERY = 8                      # gate every 8th sampled n (13 points incl. both ends)

_W: Dict[str, Any] = {}


def _setup() -> None:
    """Exactly `manuscript_figures_tcount.dump`'s environment (cwd, paths, bundles, precision)."""
    os.chdir(ROOT)
    import nb_config as C
    import notebook_helpers as H
    from forshor_qualtran import emit
    from forshor_qualtran.recursion_versions import RuleSet
    bundles = {k: emit.emit_bundle(k) for k in C.KS}
    budget = RotationBudget(delta=0.01, rotation_fraction=0.5)
    d_struct = budget.delta_struct if budget.delta_struct > 0 else budget.delta
    _W.update(H=H, bundles=bundles, budget=budget, d_struct=d_struct,
              precision=getattr(C, "PRECISION_POLICY", None),
              rules={name: RuleSet([bundles[k] for k in ks]) for name, ks in CATALOGS.items()},
              rules_all=RuleSet(list(bundles.values())), sampled=load_sampled(), rows=census_rows())


def _of(n: int, tsr: float, version: str, rules):
    """`manuscript_figures_tcount_catalysis.build("SHOR", ...)` with `pp_version` exposed."""
    from forshor_qualtran.bloqs.orderfinding import LeanOrderFinding
    H, PP = _W["H"], _W["H"]._precision_policy()
    delta_eff = PP.composed_delta(n, policy=_W["precision"], delta=_W["d_struct"])
    return LeanOrderFinding.from_bundle(rules.bundles[HOST_K], n, delta=delta_eff, N=(1 << n) - 1,
                                        policy="tight", core="versionB", qft="lean", rules=rules,
                                        stop_tsr=tsr, pp_version=version)


def _honest(n: int, t11: float, r: float) -> float:
    rc = rotation_cost(HOST_K, n, r, _W["budget"], sampled=_W["sampled"], rows=_W["rows"])
    return correct_t(t11, r, rc.t_per_rotation, RSFIG.PLACEHOLDER)


def _ladder(of, rules, tsr: float) -> List[Tuple[int, Any]]:
    """The (width, arity) descent of the modmul's main phase product -- the policy as a ladder."""
    mm = of.modexp().modmul()
    w = max(mm.n, mm.w_work) + 1
    return [(r["W"], r["k"]) for r in rules.tree(w, "C", HOST_K, "kmy", tsr)]


def _gate_point(args) -> Dict[str, Any]:
    n, tsr, t_ref, r_ref = args
    H = _W["H"]
    of = _of(n, tsr, "B", _W["rules_all"])
    t11, r = H.price(of, n, "t"), H.price(of, n, "rotation")
    return {"n": n, "tsr": tsr, "T11": t11, "R": r, "T11_ref": t_ref, "R_ref": r_ref,
            "ok": t11 == t_ref and r == r_ref}


def _policy_point(args) -> Dict[str, Any]:
    n, cat = args
    H, rules = _W["H"], _W["rules"][cat]
    best = None
    for tsr in RSFIG.STOP_LADDER:
        of = _of(n, tsr, "C", rules)
        t11, r = H.price(of, n, "t"), H.price(of, n, "rotation")
        if not (t11 == t11 and t11 > 0 and r == r and r > 0):
            continue
        try:
            t = _honest(n, t11, r)
        except ValueError:
            continue
        if best is None or t < best["T"]:
            best = {"n": n, "catalog": cat, "T": t, "T11": t11, "R": r, "stop_tsr": tsr,
                    "ladder": _ladder(of, rules, tsr)}
    return best


def _k4_point(n: int) -> Dict[str, Any]:
    """k = 4 at every level at an n off the sampled grid: `_point_reopt`'s method (best honest T over
    the stop-price ladder), the same one that produced every stored k = 4 point."""
    H, rules, best = _W["H"], _W["rules_all"], None
    for tsr in RSFIG.STOP_LADDER:
        of = _of(n, tsr, "B", rules)
        t11, r = H.price(of, n, "t"), H.price(of, n, "rotation")
        if not (t11 == t11 and t11 > 0 and r == r and r > 0):
            continue
        t = _honest(n, t11, r)
        if best is None or t < best["T"]:
            mm = of.modexp().modmul()
            w = max(mm.n, mm.w_work) + 1
            best = {"n": n, "T": t, "T11": t11, "R": r, "stop_tsr": tsr,
                    "ladder": [(q["W"], q["k"]) for q in rules.tree(w, "B", HOST_K, "kmy", tsr)]}
    return best


def zoom_points(n: int) -> Dict[str, Any]:
    """Exact points at the inset's n: k = 4 and the best policy per catalog (never above k = 4)."""
    _setup()
    k4 = _k4_point(n)
    out = {"n": n, "k4": k4}
    for cat in CATALOGS:
        p = _policy_point((n, cat))
        out[cat] = p if p["T"] < k4["T"] else dict(k4, catalog=cat, from_k4=True)
    return out


def _pool(workers: int):
    return get_context("spawn").Pool(workers, initializer=_setup)   # fork is unsafe on macOS once ObjC is loaded


def _k4() -> Dict[str, Any]:
    doc = json.loads(SRC.read_text(encoding="utf-8"))
    budget = RotationBudget(delta=doc["budget"]["delta"], rotation_fraction=doc["budget"]["rotation_fraction"])
    cc = RSFIG.corrected_curve(doc, HOST_K, budget, load_sampled(), census_rows())
    raw = doc["curves"][str(HOST_K)]
    return {"doc": doc, "curve": cc, "raw": {n: (t, r, s) for n, t, r, s in
                                              zip(raw["n"], raw["T_at_placeholder"], raw["rotations"], raw["stop_tsr"])}}


def gate(workers: int) -> List[Dict[str, Any]]:
    raw = _k4()["raw"]
    ns = sorted(raw)
    pick = sorted(set(ns[::GATE_EVERY]) | {ns[-1]})
    jobs = [(n, raw[n][2], raw[n][0], raw[n][1]) for n in pick]
    with _pool(min(workers, len(jobs))) as p:
        res = p.map(_gate_point, jobs)
    for g in res:
        print(f"gate n={g['n']:6d} tsr={g['tsr']:6g}  T11 {g['T11']:.6e} vs {g['T11_ref']:.6e}  "
              f"R {g['R']:.6e} vs {g['R_ref']:.6e}  {'OK' if g['ok'] else 'MISMATCH'}")
    if not all(g["ok"] for g in res):
        raise SystemExit("gate failed: the stack does not reproduce the stored k = 4 points")
    return res


def dump(workers: int) -> None:
    t0 = time.time()
    g = gate(workers)
    k4 = _k4()
    ns = list(k4["curve"]["n"])
    jobs = [(n, cat) for cat in CATALOGS for n in ns]
    with _pool(workers) as p:
        pts = p.map(_policy_point, jobs, chunksize=1)
    k4T = dict(zip(k4["curve"]["n"], k4["curve"]["T"]))
    out: Dict[str, Any] = {}
    for cat in CATALOGS:
        rows = []
        for pt in (q for q in pts if q and q["catalog"] == cat):
            n = pt["n"]
            if k4T[n] <= pt["T"]:          # the constant-k4 policy is itself a candidate
                t11, r, s = k4["raw"][n]
                pt = {"n": n, "catalog": cat, "T": k4T[n], "T11": t11, "R": r, "stop_tsr": s,
                      "ladder": "k4 at every level (the manuscript point)", "from_k4": True}
            rows.append(pt)
        out[cat] = sorted(rows, key=lambda q: q["n"])
    doc = {"schema": "forshor.figure_data_policy_shor/v1", "stage": "SHOR", "host_k": HOST_K,
           "source": str(SRC.relative_to(ROOT)), "rule": "R2 (kmy)", "stop_ladder": list(RSFIG.STOP_LADDER),
           "budget": k4["doc"]["budget"], "catalogs": {c: list(v) for c, v in CATALOGS.items()},
           "gate": g, "k4": {"n": k4["curve"]["n"], "T": k4["curve"]["T"]}, "policy": out,
           "zoom": zoom_points(RSFIG.ZOOM_N),
           "seconds": round(time.time() - t0, 1)}
    DATA.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"-> {DATA.relative_to(ROOT)}  ({len(ns)} n x {len(CATALOGS)} catalogs, {doc['seconds']} s)")


def plot() -> None:
    plt = RSFIG._style()
    doc = json.loads(DATA.read_text(encoding="utf-8"))
    k4n, k4T = doc["k4"]["n"], doc["k4"]["T"]
    pol = {c: doc["policy"][c] for c in doc["policy"]}
    col4 = plt.get_cmap("viridis")(2 / 5)                     # k = 4's colour in the manuscript figure
    fig, (ax, axr) = plt.subplots(2, 1, figsize=(9.8, 8.6), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.25], "hspace": 0.06})
    ax.loglog(k4n, k4T, marker="o", ms=3.2, lw=1.8, color=col4, label=r"$k=4$ at every level (manuscript curve)")
    ax.loglog([q["n"] for q in pol["k2-7"]], [q["T"] for q in pol["k2-7"]], marker="s", ms=3.0, lw=1.6,
              color="tab:red", label=r"best policy per $n$, tables $k\in\{2..7\}$")
    ax.loglog([q["n"] for q in pol["k2-4"]], [q["T"] for q in pol["k2-4"]], ls=(0, (4, 2)), lw=1.4,
              color="tab:orange", label=r"best policy per $n$, tables $k\in\{2,3,4\}$")
    for d in (1024, 2048, 4096, 8192):
        ax.axvline(d, ls="--", lw=0.7, color="0.72", zorder=0)
        axr.axvline(d, ls="--", lw=0.7, color="0.72", zorder=0)
    ax.set_ylabel("logical T-count", fontweight="bold")
    ax.grid(True, which="both", ls=":", alpha=0.32)
    ax.legend(loc="upper left", framealpha=0.92)
    k4d = dict(zip(k4n, k4T))
    for c, sty in (("k2-7", dict(color="tab:red", marker="s", ms=3.0, lw=1.6)),
                   ("k2-4", dict(color="tab:orange", ls=(0, (4, 2)), lw=1.4))):
        xs = [q["n"] for q in pol[c]]
        axr.semilogx(xs, [k4d[q["n"]] / q["T"] for q in pol[c]], **sty)
    axr.axhline(1.0, color=col4, lw=1.2)
    axr.set_ylabel(r"$T_{k=4}\,/\,T_{\rm policy}$", fontweight="bold")
    axr.set_xlabel("$n$ (operand bits)", fontweight="bold")
    axr.grid(True, which="both", ls=":", alpha=0.32)
    RSFIG._bold_ticks(ax)
    RSFIG._bold_ticks(axr)
    fig.tight_layout()

    # inset: zoom near n = ZOOM_N, bottom-right of the T panel (the curves run bottom-left to top-right,
    # so this corner is empty); same window and styling as the manuscript figure's inset
    lo, hi = RSFIG.ZOOM_N / 1.7, RSFIG.ZOOM_N * 1.7
    axin = ax.inset_axes([0.545, 0.06, 0.43, 0.34])
    series = [(k4n, k4T, dict(marker="o", ms=3.0, lw=1.6, color=col4)),
              ([q["n"] for q in pol["k2-7"]], [q["T"] for q in pol["k2-7"]],
               dict(marker="s", ms=3.0, lw=1.6, color="tab:red")),
              ([q["n"] for q in pol["k2-4"]], [q["T"] for q in pol["k2-4"]],
               dict(ls=(0, (4, 2)), lw=1.4, color="tab:orange"))]
    ylo, yhi = math.inf, 0.0
    for xs, ys, sty in series:
        pts = [(x, y) for x, y in zip(xs, ys) if lo <= x <= hi]
        axin.loglog([p[0] for p in pts], [p[1] for p in pts], **sty)
        ylo, yhi = min(ylo, *(p[1] for p in pts)), max(yhi, *(p[1] for p in pts))
    axin.set_ylim(ylo / 10 ** 1.0, yhi * 1.12)     # empty band below the curves for the recursion note
    axin.axvline(RSFIG.ZOOM_N, ls="--", lw=1.0, color="0.45")
    z = doc["zoom"]
    for key, sty in (("k4", dict(marker="o", color=col4)), ("k2-7", dict(marker="*", color="tab:red")),
                     ("k2-4", dict(marker="D", color="tab:orange"))):
        axin.loglog([z["n"]], [z[key]["T"]], ls="none", ms=(11 if key == "k2-7" else 6), mec="black",
                    mew=0.7, zorder=5, **sty)

    def _seq(p):
        return r" $\to$ ".join(f"{w}:" + ("leaf" if k is None else f"k{k}") for w, k in p["ladder"])
    note = (rf"$n={z['n']}$, recursion (width:table)" "\n"
            + rf"$k\in\{{2..7\}}$: {_seq(z['k2-7'])}  ($\times${z['k4']['T'] / z['k2-7']['T']:.2f})" "\n"
            + rf"$k\in\{{2,3,4\}}$: {_seq(z['k2-4'])}  ($\times${z['k4']['T'] / z['k2-4']['T']:.2f})" "\n"
            + rf"$k=4$: {_seq(z['k4'])}")
    axin.text(0.5, 0.03, note, transform=axin.transAxes, ha="center", va="bottom", fontsize=6.1,
              linespacing=1.35, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="0.6", alpha=0.95), zorder=6)
    axin.set_title(rf"zoom: $n \approx {RSFIG.ZOOM_N}$", fontsize=11, fontweight="bold", pad=3)
    axin.tick_params(labelsize=9)
    RSFIG._bold_ticks(axin)
    axin.grid(True, which="both", ls=":", alpha=0.35)
    for s in axin.spines.values():
        s.set_linewidth(1.1)
    axin.patch.set_alpha(0.96)
    for ext in ("pdf", "png"):
        p = OUT / f"fig_shor_T_vs_n_policy_vs_k4_R2model.{ext}"
        fig.savefig(p, bbox_inches="tight")
        print(f"-> {p.relative_to(ROOT)}")
    plt.close(fig)
    with (OUT / "tcount_shor_policy_vs_k4_R2model.csv").open("w", encoding="utf-8") as fh:
        fh.write("catalog,n,T_k4,T_policy,gain,rotations,stop_tsr,policy_ladder\n")
        for c in pol:
            for q in pol[c]:
                lad = q["ladder"] if isinstance(q["ladder"], str) else " ".join(
                    f"{w}:{'leaf' if k is None else 'k' + str(k)}" for w, k in q["ladder"])
                fh.write(f"{c},{q['n']},{k4d[q['n']]:.6e},{q['T']:.6e},{k4d[q['n']] / q['T']:.4f},"
                         f"{q['R']:.6e},{q['stop_tsr']:g},{lad}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["check", "dump", "plot", "both"])
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    a = ap.parse_args(argv)
    if a.action == "check":
        gate(a.workers)
    if a.action in ("dump", "both"):
        dump(a.workers)
    if a.action in ("plot", "both"):
        plot()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
