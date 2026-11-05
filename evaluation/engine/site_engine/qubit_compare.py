"""Qualtran's qubit count against Lean's static layout (PLAN_v2 Stage 12, decision B).

Stage 13: this comparison is at ForShor's SCANNED widths (where Lean's static layout is proved), whatever width rule
the board prices at; the PP board's own headline is now the layout formula at exact widths (`frontier.QUBITS_RULE_PP`).
Stage 18: `pp_compare` stays at scanned widths (explicitly: `fullalg.to_l2q_full` now defaults to the board's exact
rule); `full_compare` follows the full-run board (the drop-in IR, exact widths, balanced split) unless rule="scan".

One phase product of width w under a PhaseProduct policy P has four qubit counts:

  lean_static   2w + Rx + Rz, R = `Policy.reserveNeed` (PhaseProduct/Compiler/PolicyWorkspace.lean):
                R(w) = immediateNeed(w) + k * R(W'). The PROVED layout (`Policy.WorkspaceOK` provisions it, and
                `allocateReferenceLayout` places it in the full run). Static: every one of the k (PP: 2k - 1 phase
                products, k slots per side) children of a node gets its own reserve, although they run one after another.
  lean_used     |LowGate.usedQubits| of the literal Lean lowering (l2q `lean_used_qubits`): operands plus the reserve
                qubits the lowering actually activates (limb growth at each node; ONE child's subtree at a time).
                lean_static - lean_used = the static over-provisioning (k - 1 copies of each child reserve, per level).
  qualtran_l2q  Qualtran `QubitCount` of the fullalg bloq (l2q `PolicyPhaseProduct`, the site's convention: product
                leaf, Gidney adders): lean_used + the peak ancilla of the Qualtran realisation, max over the ladder of
                  level: the adder's own ancillas (Add/Subtract(QInt(W')) = 3W' - 1 qubits, so W' - 1 extra) plus
                         the zero padding of a shifted source view (shift bits);
                  leaf:  the product leaf's (wx + wz)-bit product register plus the CAdd's ancillas (CAdd(w) = 4w, so
                         2w extra) or, while `Product` runs, its own 2 max(wx, wz) extra.
  qualtran_tcost Qualtran `QubitCount` of tcost's decomposed `PolicyPhaseProduct` (the PhaseProduct chart's former
                "Total qubits"): the min-max peak of the actual decomposition (each limb extended into a fresh W'
                slot, children in sequence, leaf stated as 2(wx + wz) + 2 max - 1).

The full run: `PolicyShorCore.my_static_costs(QubitCount)` RETURNS Lean's `allocateReferenceLayout`, so Qualtran's
QubitCount on the fullalg bloq is the Lean layout by construction (not an independent count). The independent
Qualtran figure is the sequential peak over the run's steps: the four registers and the flag (bystanders) plus, for
each phase product / QFT site, the Qualtran QubitCount of that site's bloq minus its operand registers (and for the
template's own adders, their ancillas). `full_compare` gives both, and the Lean static layout term by term.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tcost import tables as tt

from . import fullalg as fa


def _l2q_pp(P: tt.Policy, w: int):
    from l2q_full.bloqs.policy_phase_product import PolicyPhaseProduct as LPP
    return LPP(fa.to_l2q_full(P, "scan"), int(w), int(w), convention=fa.HEADLINE)     # scanned widths (module doc)


def _ancilla_terms(LP, w: int) -> Dict[str, Any]:
    """The Qualtran ancilla peak of one phase product, by ladder rung (which rung sets it, and why)."""
    from l2q_full.bloqs import policy_phase_product as m
    spec = fa.HEADLINE
    rungs, wx = [], int(w)
    while True:
        s = LP.step(wx, wx)
        if s is None:
            a = m._qualtran_leaf_ancilla(wx, wx, spec.leaf, False)
            rungs.append({"W": wx, "k": None, "ancilla": a,
                          "what": f"product leaf: {2 * wx}-bit product register + CAdd ancillas ({2 * wx})"})
            break
        T, w2 = s
        a = m._qualtran_level_ancilla(T, w2, spec)
        if any(o["op"] == "negate" for o in T.ops) and a == 2 * w2 - 1:
            what = f"Qualtran Negate(QInt({w2})) at W = {wx}: {2 * w2 - 1} ancillas (its internal Add's constant + carries)"
        else:
            what = f"Gidney Add/Subtract on QInt({w2}) at W = {wx}: {w2 - 1} ancillas + zero padding {a - (w2 - 1)} of a shifted source"
        rungs.append({"W": wx, "k": T.k, "child": w2, "ancilla": a, "what": what})
        wx = w2
    top = max(rungs, key=lambda r: r["ancilla"])
    return {"rungs": rungs, "peak": top["ancilla"], "set_by": top}


def pp_compare(P: tt.Policy, w: int = 2048, with_tcost: bool = True) -> Dict[str, Any]:
    """All four counts of one phase product at width w, and the differences term by term."""
    from . import frontier as fr
    b = _l2q_pp(P, w)
    rx, rz = b.reserve_qubits()
    static = 2 * int(w) + rx + rz
    used = b.lean_used_qubits()
    lean_qc = b.lean_qubit_count()
    q_l2q = b.qubits()
    anc = _ancilla_terms(b.policy, w)
    out = {"w": int(w), "ladder": " -> ".join(f"{x['W']}:{x['k'] or 'leaf'}" for x in anc["rungs"]),
           "lean_static": static, "reserve": [rx, rz], "lean_used": used, "lean_clean_anc": lean_qc - used,
           "lean_qubit_count": lean_qc, "qualtran_l2q": q_l2q, "qualtran_ancilla_peak": anc["peak"],
           "ancilla_set_by": anc["set_by"]["what"], "ancilla_rungs": anc["rungs"],
           "static_over_used": static - used, "l2q_over_used": q_l2q - used}
    assert q_l2q == used + anc["peak"], (q_l2q, used, anc["peak"])
    if with_tcost:
        out["qualtran_tcost"] = fr.qubits(P.with_widths("scan"), int(w))   # Stage 12's comparison: scanned widths
    return out


def peak_extra(LP, wx: int, wz: int, controlled: bool = False) -> int:
    """Qualtran's sequential min-max peak of one (rectangular) phase product, above its operands, by the rules of
    tcost's decomposition (validated: equals Qualtran `QubitCount` of tcost's `PolicyPhaseProduct` for every square
    policy tested). At a node: the limbs grown to W' (sum of W' - limb over both sides) stay live while either the
    node's own arithmetic runs (Gidney Add/Subtract(QInt(W')): W' - 1 ancillas; Negate(QInt(W')): 2W' - 1) or ONE
    child runs (its own peak); children run one after another and free their growth. Leaf: the product leaf, a
    (wx + wz)-bit product register plus the CAdd's 2 max(wx, wz) - 1 ancillas. A controlled product adds 1 (the
    control's And) at the leaf."""
    from l2q_full.bloqs.policy_phase_product import _split_widths
    s = LP.step(int(wx), int(wz))
    if s is None:
        return int(wx) + int(wz) + 2 * max(int(wx), int(wz)) - 1 + (1 if controlled else 0)
    T, w2 = s
    limb = min(int(wx) // T.k, int(wz) // T.k)
    grow = sum(max(0, w2 - a) for a in _split_widths(int(wx), limb, T.k)) + \
        sum(max(0, w2 - a) for a in _split_widths(int(wz), limb, T.k))
    ops = {o["op"] for o in T.ops}
    own = max((w2 - 1) if "addScaled" in ops else 0, (2 * w2 - 1) if "negate" in ops else 0)
    return grow + max(own, peak_extra(LP, w2, w2, controlled))


def _site_extra(LP, site) -> Dict[str, Any]:
    """Qualtran sequential peak of one site of the run above its operand registers (which are among the run's live
    registers): `peak_extra`; a QFT's twiddles act on (l + 1) x (r + 1) operands, one extra qubit per side (Lean's
    1 + R in `qftWorkspaceNeed`); a controlled product's control is a register bit."""
    name, a, b = site
    if name == "qft":
        best, todo = 0, [int(a)]
        while todo:
            v = todo.pop()
            if v < 2:
                continue
            l, r = v // 2, v - v // 2
            best = max(best, 2 + peak_extra(LP, l + 1, r + 1))
            todo += [l, r]
        return {"site": name, "xw": a, "zw": None, "extra": best}
    return {"site": name, "xw": a, "zw": b, "extra": peak_extra(LP, a, b, controlled=name == "cphase_product")}


def full_compare(P: tt.Policy, n: int = 2048, policy_l2q=None, rule: Optional[str] = None) -> Dict[str, Any]:
    """The full run: Lean's static layout (the board's qubits); Qualtran's QubitCount on the fullalg bloq (equal to
    the layout by `PolicyShorCore.my_static_costs`, so not independent); Qualtran's own fallback with that override
    removed (max of the signature and the callees' QubitCount: no bystanders, an under-estimate by Qualtran's own
    docstring); and the sequential Qualtran peak (the four registers and the flag live throughout, plus the largest
    site or adder peak above its operands). ``rule="scan"``: upstream ForShor's IR at scanned widths (the cross-check);
    default: the board's IR and rule."""
    from qualtran.resource_counting import QubitCount, get_cost_value
    import attrs
    from l2q_full import policy as lpol
    scan = rule == "scan"
    LP = policy_l2q if policy_l2q is not None else fa.to_l2q_full(P, "scan" if scan else None)
    LP = lpol.with_width_rule(LP, "scan" if scan else fa.width_rule())
    c = attrs.evolve((fa.core_scan if scan else fa.core)(int(n)), policy=LP, convention=fa.HEADLINE)
    w = dict(c.widths)
    lay = fa.layout(LP, w)
    active = w["xW"] + w["yW"] + w["workW"] + w["scratchW"] + 1
    by_bloq = int(get_cost_value(c, QubitCount()))
    fallback = max([sum(r.total_bits() for r in c.signature)] +
                   [int(get_cost_value(fa.pr.site_bloq(LP, s, fa.HEADLINE), QubitCount())) for s, _ in c.sites])
    rows = [_site_extra(LP, s) for s, _ in c.sites]
    rows += [{"site": "adder", "xw": aw, "zw": None, "extra": aw - 1} for aw, _ in c.add_scaled]
    rows += [{"site": "negate", "xw": aw, "zw": None, "extra": 2 * aw - 1} for aw, _ in c.negate]
    top = max(rows, key=lambda r: r["extra"])
    return {"n": int(n), "lean_static": lay["total"], "layout": lay, "qualtran_bloq": by_bloq,
            "qualtran_fallback": fallback, "active_registers": active, "qualtran_peak": active + top["extra"],
            "peak_set_by": top, "static_over_peak": lay["total"] - active - top["extra"],
            "reserves": {g["register"]: [g["reserve"], g["reserve_set_by"]] for g in lay["registers"]},
            "reserve_total": sum(g["reserve"] for g in lay["registers"])}


# ---------------------------------------------------------------------------------------------- the report
def _board_top(delta: float, top: int = 5):
    from . import full_frontier as ff
    from tcost import planner as tp  # noqa: F401
    from . import library as lib
    import json
    c = ff.load(delta) or json.loads((fa.ENGINE / "cache" / f"fullalg_{delta:.0e}.json").read_text())
    ranked = c["leaderboard"]["ranked"][:top]
    order = {s.id: i for i, s in enumerate(lib.submissions(), start=1)}
    out = []
    for r in ranked:
        P, _ = fa_tp().optimal_policy([T for T in lib.sto_tables(upto=order[r["id"]]) if T.mode == tt.PAIR], tt.PAIR,
                                      fa.HERO_N, delta, name=f"upto:{r['id']}", widths="scan")
        out.append((f"board #{r['rank']} ({r['id']})", P, r))
    return out


def fa_tp():
    from tcost import planner as tp
    return tp


def report(delta: float = 0.01) -> Dict[str, Any]:
    """Every comparison of decision B: PP (seeded PP policies, the PP chart's frontier policies) and the full run
    (STO reference, ForShor shipped, the full board's top 5)."""
    import json
    from . import library as lib, submitted, seeded_board as sb
    from .precompute import load as pc_load
    pp_rows = []
    for rec in submitted.seeded():
        if rec["mode"] != tt.PAIR:
            continue
        r = pp_compare(sb.policy_of(rec), 2048)
        r.pop("ancilla_rungs")
        pp_rows.append({"policy": f"seed {rec['id']}", **r})
    front = []
    c = pc_load(tt.PAIR, delta, "scan") or json.loads((fa.ENGINE / "cache" / f"frontier_PP_scan_{delta:.0e}.json").read_text())
    for key, pols in c["policies"].items():
        for ns, pj in pols.items():
            r = pp_compare(tt.Policy.from_json(pj, widths="scan"), int(ns))
            r.pop("ancilla_rungs")
            front.append({"policy": f"{key}@{ns}", **r})
    ref = pp_compare(lib.reference_policies()[tt.PAIR], 2048)
    ref.pop("ancilla_rungs")
    full = [("STO reference", full_compare(lib.reference_policies()[tt.PAIR]))]
    full.append(("ForShor shipped (k3 >= 40, k2 >= 14)", full_compare(None, policy_l2q=fa.native().policy)))
    for label, P, _ in _board_top(delta):
        full.append((label, full_compare(P)))
    for _, f in full:
        f.pop("layout")
    return {"pp_reference": ref, "pp_seeded": pp_rows, "pp_frontier": front,
            "full": [{"policy": l, **f} for l, f in full]}


def main() -> int:
    import json
    print(json.dumps(report(), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
