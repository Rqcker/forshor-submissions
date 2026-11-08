r"""Width-indexed (varying-k) phase products inside the Shor stack — the policy hook's glue.

`LeanQFT`, `LeanCtrlModMul`, `LeanModExp` and `LeanOrderFinding` take an optional
``phase_policy: PhasePolicy`` (+ ``pp_convention``). With it, every phase product they would build
as `LeanRecursivePhaseProduct` / `LeanAsymRecursivePhaseProduct` / `QftRecursiveTwiddle` (the
``core="recursive"`` objects) is built here instead as a `PolicyPhaseProduct` — the table chosen per
recursion level by width, exactly as ForShor's `Policy.signedPlanOf`. With ``phase_policy=None``
(the default) nothing changes.

Two realisations of the controlled phase product (cmodmul steps U1/U5†):

* ``LEGACY`` convention — mirrors the existing single-k pipeline exactly: the uncontrolled phase
  product plus one `And`/`And†` pair per leaf cell (``LeanCtrlModMul.build_call_graph``). Used by the
  faithfulness gate: ``constPolicy(T)`` + ``LEGACY`` reproduces the single-k pipeline's counts.
* any other convention (``QUALTRAN`` headline, ``LEAN_FAITHFUL`` cross-check) — `CPolicyPhaseProduct`,
  which controls only the leaves (grid: one And per row; product leaf: controlled rotations).

QFT twiddles recurse from the zero-extended pair ``(left+1, right+1)`` (`QftRecursiveTwiddle`'s
pricing fix), so the policy twiddle is ``PolicyPhaseProduct(P, left+1, right+1)``.

Rotation pricing at the Shor level: inside order finding, every rotation of the whole circuit
shares the one global budget, so the honest rotation context is the WHOLE circuit's rotation count
``R_total`` (``eps = delta_rot / R_total``), not a single phase product's own R. :func:`shor_t`
prices that way, and :func:`replan_fixed_point` re-plans the policy with
``rotation_context = R_total`` until the policy (and hence ``R_total``) stops changing.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import cost_conventions as cc
from .policy import PhasePolicy, PolicyBand, ToomCookTable


# ============================================================================ the hook
def resolve_convention(convention: Optional[cc.ConventionSpec]) -> cc.ConventionSpec:
    """A policy without an explicit convention is priced in the headline tier."""
    return convention if convention is not None else cc.QUALTRAN


def legacy_controls(convention: Optional[cc.ConventionSpec]) -> bool:
    """Whether the controlled phase product is realised the single-k pipeline's way
    (uncontrolled PP + And pair per leaf cell): exactly for the LEGACY convention."""
    return resolve_convention(convention).name == cc.LEGACY.name


def policy_pp(policy: PhasePolicy, wx: int, wz: int, convention: Optional[cc.ConventionSpec] = None):
    """The uncontrolled phase product on (wx, wz) under `policy`."""
    from .bloqs.policy_phase_product import PolicyPhaseProduct
    return PolicyPhaseProduct(policy, int(wx), int(wz), convention=resolve_convention(convention))


def policy_cpp(policy: PhasePolicy, wx: int, wz: int, convention: Optional[cc.ConventionSpec] = None):
    """The controlled phase product on (wx, wz) under `policy` (non-LEGACY realisation)."""
    from .bloqs.policy_phase_product import CPolicyPhaseProduct
    return CPolicyPhaseProduct(policy, int(wx), int(wz), convention=resolve_convention(convention))


def leaf_cells(pp) -> int:
    """Total leaf-grid cells of a phase-product bloq: `total_czpow()` for the single-k bloqs, and the
    same count for a `PolicyPhaseProduct` (nodes × leaf width², or wx·wz if the root is the leaf)."""
    if hasattr(pp, "total_czpow"):
        return int(pp.total_czpow())
    wx, wz, nodes = pp.wx, pp.wz_, 1
    while True:
        s = pp.policy.step(wx, wz)
        if s is None:
            return nodes * wx * wz
        T, w2 = s
        nodes *= 2 * T.k - 1
        wx = wz = w2


def pp_extra_qubits(pp, a: int, b: int, convention: Optional[cc.ConventionSpec]) -> int:
    """Workspace a phase product adds beyond its two operand registers and one And ancilla, in the
    terms `LeanCtrlModMul.qubit_footprint` uses (its formula adds operands and +1 itself)."""
    if legacy_controls(convention):
        rx, rz = pp.reserve_qubits()
        return rx + rz
    return max(0, int(pp.qubits()) - int(a) - int(b) - 1)


# ============================================================================ policies
def table_from_bundle(doc: Dict[str, Any], name: Optional[str] = None) -> ToomCookTable:
    """The Toom-Cook table a `forshor.emit/v1` bundle prices (`schedule.ops`/`points`)."""
    sch = doc["schedule"]
    return ToomCookTable.from_json(name or f"k{doc['k']}bundle",
                                   {"k": doc["k"], "points": sch["points"], "ops": sch["ops"]})


def one_band(table: ToomCookTable, cutoff: int) -> PhasePolicy:
    """A single table with a leaf cutoff — a valid one-band ForShor policy."""
    return PhasePolicy((PolicyBand(int(cutoff), table),), name=f"oneband:{table.name}@{cutoff}")


# ============================================================================ Shor-level pricing
def shor_split(bloq) -> cc.TCost:
    """`(rotation-free T, rotations)` of a whole Shor-stack bloq (Qualtran QECGatesCost)."""
    return cc.split(bloq)


def shor_t(bloq, convention: Optional[cc.ConventionSpec] = None) -> Dict[str, float]:
    """T of a whole circuit, every rotation synthesised at eps = delta_rot / R_total (the whole
    circuit's rotation count — the honest context when phase products sit inside Shor)."""
    spec = resolve_convention(convention).with_(r_context=None)
    c = shor_split(bloq)
    t_rot = spec.t_per_rotation(c.rot) if c.rot else 0.0
    return {"T": c.base + c.rot * t_rot, "T_base": float(c.base), "rotations": float(c.rot),
            "T_per_rotation": t_rot}


def order_finding(bundle_doc: Dict[str, Any], n: int, *, phase_policy: Optional[PhasePolicy] = None,
                  convention: Optional[cc.ConventionSpec] = None, delta: float = 1e-2, N: int = 0):
    """`LeanOrderFinding` at width n in the recursive (Lean-plan) configuration, optionally with a
    phase policy substituted for every phase product. `N` = 0 is the generic modulus."""
    from .bloqs.orderfinding import LeanOrderFinding
    return LeanOrderFinding.from_bundle(bundle_doc, n, delta=delta, N=N, policy="sym", core="recursive",
                                        depth="lean", qft="lean", phase_policy=phase_policy,
                                        pp_convention=convention)


def pp_widths(of_bloq) -> List[Tuple[int, int, int]]:
    """`[(wx, wz, multiplicity)]` of every phase-product site inside one order-finding instance —
    the widths a policy is actually consulted at (modmul U1/U5†/U2 and every QFT twiddle)."""
    mm = of_bloq.modexp().modmul()
    steps = of_bloq.modexp().n_steps
    n, ww, wc = mm.n, mm.w_work, mm.w_carry
    sites: Dict[Tuple[int, int], int] = {}

    def put(a, b, m):
        sites[(a, b)] = sites.get((a, b), 0) + m

    for a in (n, wc):
        put(a, ww, steps)
    put(ww, wc, steps)

    def qft_sites(w, mult):
        stack = [w]
        while stack:
            v = stack.pop()
            if v < 2:
                continue
            left, right = v // 2, v - v // 2
            put(left + 1, right + 1, mult)
            stack += [left, right]
    qft_sites(ww, 2 * steps)          # U1, U5† IQFT(work)
    qft_sites(wc, 2 * steps)          # U2 QFT/IQFT(carry)
    qft_sites(of_bloq.exp_width, 1)   # the QPE readout
    return sorted((a, b, m) for (a, b), m in sites.items())


def replan_fixed_point(catalog: Sequence[ToomCookTable], bundle_doc: Dict[str, Any], n: int,
                       convention: Optional[cc.ConventionSpec] = None, max_iter: int = 6,
                       w_max: Optional[int] = None) -> Dict[str, Any]:
    """Plan a policy for a whole order-finding instance.

    With the rotation context fixed at ``R`` the planner's T is additive, so its per-width choice is
    simultaneously optimal for every phase-product site; the policy is that per-width choice map
    turned into bands (:func:`policy_from_choice_map`). The whole circuit's ``R_total`` depends on the
    policy, so iterate: plan at R → price the whole circuit → R_total → re-plan, until the policy
    repeats (a fixed point) or ``max_iter``. Returns the history."""
    from .policy_planner import Planner
    spec = resolve_convention(convention)
    of0 = order_finding(bundle_doc, n)
    w_top = w_max or max(max(a, b) for a, b, _ in pp_widths(of0))
    r_ctx = shor_split(order_finding(bundle_doc, n, phase_policy=None)).rot or 1.0
    history, seen = [], {}
    P = None
    for it in range(max_iter):
        pl = Planner(catalog, "t", convention=spec, rotation_context=float(r_ctx))
        P = policy_from_choice_map(pl, w_top, name=f"shor_opt_n{n}_it{it}")
        key = tuple((b.threshold, b.table.name) for b in P.bands)
        priced = shor_t(order_finding(bundle_doc, n, phase_policy=P, convention=spec), spec)
        history.append({"iter": it, "r_context": float(r_ctx), "bands": [list(k) for k in key],
                        **priced})
        if key in seen:
            break
        seen[key] = it
        r_ctx = priced["rotations"]
    return {"policy": P, "history": history, "converged": len(history) >= 2 and
            [tuple(map(tuple, h["bands"])) for h in history][-1] == [tuple(map(tuple, h["bands"])) for h in history][-2]}


def plan_reachable(catalog: Sequence[ToomCookTable], bundle_doc: Dict[str, Any], n: int,
                   convention: Optional[cc.ConventionSpec] = None, max_iter: int = 4,
                   of_kwargs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`replan_fixed_point` on the widths the phase-product sites actually reach.

    At a fixed rotation context the planner's per-width choice is optimal for every site at once; here
    it is read only along the optimal chains from each site's width (the planner's lazy front visits a
    few hundred widths, not every width up to n), and turned into bands: a band at the lowest width of
    each run of one table, widest first, so the leaf region is below the lowest table width. Rectangular
    sites and a band's reach between chain widths make that an approximation of the per-width map; the
    caller refines against the exact whole circuit. Re-planned at the priced R_total until the bands
    repeat; returns the cheapest priced iterate."""
    from .policy_planner import Planner
    spec = resolve_convention(convention)
    kw = of_kwargs or {}
    of0 = order_finding(bundle_doc, n, **kw)
    sites = sorted({max(a, b) for a, b, _ in pp_widths(of0)}, reverse=True)
    r_ctx = shor_split(order_finding(bundle_doc, n, phase_policy=None, **kw)).rot or 1.0
    best, seen, history = None, set(), []
    for it in range(max_iter):
        pl = Planner(catalog, "t", convention=spec, rotation_context=float(r_ctx))
        assign: Dict[int, Optional[str]] = {}
        for w in sites:
            for wi, tn in pl.chain(w):
                assign.setdefault(wi, tn)
        runs: List[Tuple[int, str]] = []
        for w, tn in sorted(((w, tn) for w, tn in assign.items() if tn), reverse=True):
            if runs and runs[-1][1] == tn:
                runs[-1] = (w, tn)
            else:
                runs.append((w, tn))
        P = PhasePolicy(tuple(PolicyBand(w, pl.catalog[tn]) for w, tn in runs), name=f"reach_n{n}_it{it}")
        key = tuple(runs)
        priced = shor_t(order_finding(bundle_doc, n, phase_policy=P, convention=spec, **kw), spec)
        history.append({"iter": it, "r_context": float(r_ctx), "bands": [list(r) for r in runs], **priced})
        if best is None or priced["T"] < best[1]["T"]:
            best = (P, priced)
        if key in seen:
            break
        seen.add(key)
        r_ctx = priced["rotations"]
    return {"policy": best[0], "history": history}


def policy_from_choice_map(planner, w_max: int, name: str = "planned") -> PhasePolicy:
    """Bands from the planner's per-width optimal top-level choice over 1..w_max.

    A policy is a step function: widths below the last threshold are the leaf, and above it every
    width has a table. A per-width map can also prefer the leaf at isolated widths *above* a table
    width; a band cannot say "leaf here", so such widths take the table of the band they fall in
    (that table either shrinks — a small regret, reported by the caller's pricing — or does not
    shrink, which ForShor's `Stops` already turns into a leaf). Bands are the maximal runs of the
    resulting map, widest first."""
    cm = planner.choice_map(w_max)                  # index i -> width i+1
    first_table = next((i for i, t in enumerate(cm) if t is not None), None)
    if first_table is None:
        return PhasePolicy((), name=name)
    filled = list(cm)
    for i in range(first_table + 1, len(filled)):
        if filled[i] is None:
            filled[i] = filled[i - 1]
    runs: List[Tuple[int, str]] = []                 # (threshold width, table)
    for i in range(first_table, len(filled)):
        t = filled[i]
        if not runs or runs[-1][1] != t:
            runs.append((i + 1, t))
    bands = tuple(PolicyBand(thr, planner.catalog[t]) for thr, t in reversed(runs))
    return PhasePolicy(bands, name=name)


# ============================================================================ exact refinement
def refine(policy: PhasePolicy, catalog: Sequence[ToomCookTable], bundle_doc: Dict[str, Any], n: int,
           convention: Optional[cc.ConventionSpec] = None, passes: int = 4,
           steps: Sequence[int] = (1, 2, 4, 8, 16, 32, 64, 128), of_kwargs: Optional[Dict[str, Any]] = None
           ) -> Dict[str, Any]:
    """Coordinate descent on a banded policy against the EXACT whole-circuit T (every candidate is
    priced as a full order-finding instance, rotations at R_total). Moves: shift one threshold by
    ±step (keeping strict decrease), swap one band's table for any catalog table, delete a band,
    add a band of any table at a midpoint. Accepts strict improvements; a local optimum, but it can
    never end worse than its seed."""
    spec = resolve_convention(convention)
    cache: Dict[Tuple, float] = {}

    def key(bands):
        return tuple((b.threshold, b.table.name) for b in bands)

    def price(bands) -> float:
        k = key(bands)
        if k not in cache:
            P = PhasePolicy(tuple(bands), name="refine")
            cache[k] = shor_t(order_finding(bundle_doc, n, phase_policy=P, convention=spec,
                                            **(of_kwargs or {})), spec)["T"]
        return cache[k]

    def valid(bands) -> bool:
        ts = [b.threshold for b in bands]
        return all(t >= 0 for t in ts) and all(a > b for a, b in zip(ts, ts[1:]))

    cur = list(policy.bands)
    best = price(cur)
    by_name = {t.name: t for t in catalog}
    for _ in range(passes):
        improved = False
        cands = []
        for i, b in enumerate(cur):
            for st in steps:
                for sgn in (-1, 1):
                    nb = cur[:i] + [PolicyBand(b.threshold + sgn * st, b.table)] + cur[i + 1:]
                    cands.append(nb)
            for t in by_name.values():
                if t.name != b.table.name:
                    cands.append(cur[:i] + [PolicyBand(b.threshold, t)] + cur[i + 1:])
            cands.append(cur[:i] + cur[i + 1:])
        edges = [10 ** 6] + [b.threshold for b in cur] + [0]
        for i in range(len(edges) - 1):
            mid = (edges[i] + edges[i + 1]) // 2 if i else max(edges[1] * 2, 64)
            for t in by_name.values():
                cands.append(cur[:i] + [PolicyBand(mid, t)] + cur[i:])
        for nb in cands:
            if not valid(nb):
                continue
            v = price(nb)
            if v < best * (1 - 1e-12):
                best, cur, improved = v, nb, True
        if not improved:
            break
    # Simplify: drop any band whose removal does not raise T (bands no phase-product site reaches
    # are cost-inert — the per-width map's parity alternations are of this kind). Then, for a band
    # run of one table broken only by inert bands, the merge above already joins them.
    changed = True
    while changed:
        changed = False
        for i in range(len(cur)):
            nb = cur[:i] + cur[i + 1:]
            if valid(nb) and price(nb) <= best:
                cur, best, changed = nb, price(nb), True
                break
    return {"policy": PhasePolicy(tuple(cur), name=f"refined_n{n}"), "T": best, "evaluations": len(cache)}


# ============================================================================ results
def best_one_band(catalog: Sequence[ToomCookTable], bundle_doc: Dict[str, Any], n: int,
                  convention: Optional[cc.ConventionSpec] = None, span: int = 12) -> Dict[str, Any]:
    """Best single table *with* a leaf cutoff for a whole order-finding instance.

    Per table: plan the cutoff with the planner restricted to that table at the whole circuit's
    rotation context (the planner's first width where the table is chosen), then price the
    exact whole circuit at every cutoff within ``±span`` of it and keep the cheapest."""
    from .policy_planner import Planner
    spec = resolve_convention(convention)
    best = None
    for T in catalog:
        P0 = PhasePolicy.const(T)
        r = shor_split(order_finding(bundle_doc, n, phase_policy=P0, convention=spec)).rot or 1.0
        pl = Planner([T], "t", convention=spec, rotation_context=float(r))
        w_top = max(max(a, b) for a, b, _ in pp_widths(order_finding(bundle_doc, n)))
        cm = pl.choice_map(w_top)
        c0 = next((i + 1 for i, t in enumerate(cm) if t is not None), w_top + 1)
        for c in sorted({max(0, c0 + d) for d in range(-span, span + 1)} | {0}):
            P = one_band(T, c)
            priced = shor_t(order_finding(bundle_doc, n, phase_policy=P, convention=spec), spec)
            if best is None or priced["T"] < best["T"]:
                best = {"table": T.name, "k": T.k, "cutoff": c, "policy": P, **priced}
    return best


def results(catalog: Sequence[ToomCookTable], bundle_docs: Dict[int, Dict[str, Any]],
            ns: Iterable[int] = (256, 512, 1024, 2048)) -> Dict[str, Any]:
    """Order-finding T and qubits per n: the existing single-k pipeline as shipped, best single table
    (constPolicy), best one-band (table + cutoff), the re-planned optimal policy (fixed point), in the
    QUALTRAN headline tier, plus the LEAN_FAITHFUL cross-check of the last two. Rotations priced at the
    whole circuit's R (see module docstring)."""
    host = bundle_docs[2]
    out: Dict[str, Any] = {}
    for n in ns:
        row: Dict[str, Any] = {}
        # existing pipeline, unchanged code, best k (its own structure; rotations priced the same way)
        ex = {}
        for k, doc in sorted(bundle_docs.items()):
            of = order_finding(doc, n)
            ex[k] = {**shor_t(of, cc.QUALTRAN), "qubits": int(of.qubit_footprint)}
        kb = min(ex, key=lambda k: ex[k]["T"])
        row["existing_pipeline_best_k"] = {"k": kb, **ex[kb]}
        row["existing_pipeline_all_k"] = {str(k): v["T"] for k, v in ex.items()}
        for tier in (cc.QUALTRAN, cc.LEAN_FAITHFUL):
            tr: Dict[str, Any] = {}
            singles = {}
            for T in catalog:
                of = order_finding(host, n, phase_policy=PhasePolicy.const(T), convention=tier)
                singles[T.name] = {**shor_t(of, tier), "qubits": int(of.qubit_footprint)}
            sb = min(singles, key=lambda s: singles[s]["T"])
            tr["single_table"] = {"table": sb, "k": next(t.k for t in catalog if t.name == sb), **singles[sb]}
            of = order_finding(host, n, phase_policy=PhasePolicy((), name="all_leaf"), convention=tier)
            tr["schoolbook"] = {**shor_t(of, tier), "qubits": int(of.qubit_footprint)}
            ob = best_one_band(catalog, host, n, tier)
            of = order_finding(host, n, phase_policy=ob["policy"], convention=tier)
            tr["one_band"] = {k: v for k, v in ob.items() if k != "policy"}
            tr["one_band"]["qubits"] = int(of.qubit_footprint)
            fp = replan_fixed_point(catalog, host, n, tier)
            seeds = {"all_leaf": PhasePolicy((), name="all_leaf"), "one_band": ob["policy"],
                     "planner_fixed_point": fp["policy"]}
            refined = {nm: refine(P0, catalog, host, n, tier) for nm, P0 in seeds.items()}
            win = min(refined, key=lambda nm: refined[nm]["T"])
            P = refined[win]["policy"]
            of = order_finding(host, n, phase_policy=P, convention=tier)
            tr["optimal"] = {"bands": [[b.threshold, b.table.name] for b in P.bands],
                             **shor_t(of, tier), "qubits": int(of.qubit_footprint),
                             "method": "best of exact whole-circuit coordinate descent from seeds "
                                       "(all-leaf, best one-band, planner fixed point); a local optimum",
                             "winning_seed": win,
                             "seed_results": {nm: {"T": r["T"], "evaluations": r["evaluations"],
                                                   "bands": [[b.threshold, b.table.name] for b in r["policy"].bands]}
                                              for nm, r in refined.items()},
                             "planner_fixed_point": {"converged": fp["converged"], "iterations": fp["history"]}}
            # The best one-band policy is the min over the one-band search AND every one-band policy the
            # refinement reached (the refined optimum itself may be one band) — otherwise the
            # varying-k gain would be measured against a sub-optimal one-band comparator.
            for nm, rr in refined.items():
                if len(rr["policy"].bands) == 1 and rr["T"] < tr["one_band"]["T"]:
                    b0 = rr["policy"].bands[0]
                    of1 = order_finding(host, n, phase_policy=rr["policy"], convention=tier)
                    tr["one_band"] = {"table": b0.table.name, "k": b0.table.k, "cutoff": b0.threshold,
                                      **shor_t(of1, tier), "qubits": int(of1.qubit_footprint),
                                      "found_by": f"refinement from seed {nm}"}
            tr["varying_k_gain_vs_one_band"] = tr["one_band"]["T"] / tr["optimal"]["T"]
            tr["gain_vs_single_table"] = tr["single_table"]["T"] / tr["optimal"]["T"]
            tr["gain_vs_schoolbook"] = tr["schoolbook"]["T"] / tr["optimal"]["T"]
            row[tier.name] = tr
        out[str(n)] = row
    return out


def main(argv=None) -> int:
    import argparse
    import glob
    import json
    from pathlib import Path
    from . import paths, policy_planner, policy_provenance
    ap = argparse.ArgumentParser(description="Shor-level results for width-indexed phase-product policies")
    ap.add_argument("--out", type=Path, default=paths.PIPE_ROOT / "artifacts" / "policy_shor")
    ap.add_argument("--n", type=int, nargs="*", default=[256, 512, 1024, 2048])
    a = ap.parse_args(argv)
    import sys
    # Qualtran's QubitCount decomposes a wide controlled Add recursively (one frame per bit); the
    # bloq's qubit model asks it for adder ancillas at the top widths, which exceeds the default limit.
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 200000))
    import threading
    threading.stack_size(512 * 1024 * 1024)
    root = paths.PIPE_ROOT
    catalog = policy_planner.load_catalog(sorted(glob.glob(str(root / "artifacts" / "policy_figures" / "tables"
                                                                / "table_k*.standard.json"))))
    docs = {k: json.loads((root / "artifacts" / "lean" / f"k{k}.forshor.json").read_text()) for k in range(2, 8)
            if (root / "artifacts" / "lean" / f"k{k}.forshor.json").is_file()}
    res = results(catalog, docs, a.n)
    doc = {"schema": "forshor_qualtran.policy_shor/v1",
           "conventions": {t.name: t.to_json() for t in (cc.QUALTRAN, cc.LEAN_FAITHFUL)},
           "rotation_context": "whole order-finding circuit (eps = delta_rot / R_total)",
           "catalog": [t.name for t in catalog],
           "bundle_tables_equal_catalog": {str(k): any(table_from_bundle(d).ops_frozen == t.ops_frozen and
                                                       table_from_bundle(d).points == t.points for t in catalog)
                                           for k, d in docs.items()},
           "provenance": policy_provenance.provenance_block(),
           "results": res}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "results.json").write_text(json.dumps(doc, indent=1, default=float))
    print(json.dumps({n: {t: {"opt": r[t]["optimal"]["T"], "one_band": r[t]["one_band"]["T"],
                              "single": r[t]["single_table"]["T"], "gain": r[t]["varying_k_gain_vs_one_band"]}
                          for t in ("qualtran", "lean_faithful")} | {"existing": r["existing_pipeline_best_k"]["T"]}
                      for n, r in res.items()}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    import threading as _th
    _th.stack_size(512 * 1024 * 1024)
    _box = {}
    _t = _th.Thread(target=lambda: _box.setdefault("rc", main()))
    _t.start(); _t.join()
    raise SystemExit(_box.get("rc", 1))
