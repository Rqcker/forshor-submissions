"""Full order-finding resource estimation: the site's ONE full-run pricer (PLAN_v2 Stage 11, decision D1).

A PhaseProduct policy is priced inside ForShor's native `shor` template with Lean-To-Qualtran's `PolicyShorCore`
(vendored as `engine/fullalg/l2q_full`; site edits marked "sto_site Stage 16/18"). The census of the template (every
phase product, controlled phase product, QFT, adder and negate of the run, with its operand widths) comes from
ForShor's IR. Since Stage 18 that is the ForShor DROP-IN's document (`fullalg/data/policy.forshor.exact_balanced.json`,
`REFERENCE`): exact register widths (`ir.width_rule`, `forshor_exact_width.py`) and the balanced split for unequal
operands (`balanced_*` calls, expanded through their own IR body). Upstream ForShor's document
(`policy.forshor.json`, `REFERENCE_SCAN`: scanned widths, no split) is the labelled cross-check
`cross_checks.forshor_scan_ir`, and the tier a scan document is checked on (`price_full(..., rule="scan")`).
The width rule is per table (l2q `ToomCookTable.width_rule`); `price_l2q` coerces a policy to the board's rule.
The IR is matched to the compiled circuit at sampled widths only: the census at n = 2048 is an evaluation of that
structure, NOT proved.

What the structure contains (and the retired paper model did not):
  * Step 4, the comparison `cmpLtNW` (ModularExponentiation/Circuit/Steps.lean:50): two phase products on the
    (work + 1) x (scratch + 1) pair and four QFTs on the scratch register per modular multiplication;
  * rectangular operands (a + 1, b + 1) as the Lean layout gives them (PhaseProduct/Gates/Macros.lean);
  * the Lean work width: at n = 2048 the precision is ForShor's `Reference.m2048` = 2^150 - 3, read from the
    reference document, so work = 2048 + algorithm1ExtraBitsNat(m) = 2347 bits. At other n Lean fixes no m; the
    smallest m keeping 99/100 of ForShor's success-bound baseline is used (L2Q `min_precision_m`).

Pricing (the score; one convention, decision D1/D2):
  * leaf: the Qualtran digital product (`ProductPhaseLeaf`; the PhaseProduct board's leaf, `tcost.bloqs`);
  * adders: Qualtran `Add` / `Subtract` / `Negate` (Gidney, 4(w - 1) T per adder); a negated source = one Subtract;
  * rotations: `sto_currency` (Ross-Selinger 3.075 log2(1/eps) + 0.86, unrounded) at eps = 0.5 delta / R_total,
    R_total the whole run's rotation count. Never a flat 11 T.

Cross-checks shown next to the score, never as the score:
  * Lean grid leaf: the same structure with the W x W grid leaf (`Naive_SignedPhaseProd`, NaiveLeaf.lean:53;
    one And + one rotation per cell), Qualtran adders;
  * literal Lean tier: merged-CPhase leaf and ForShor's Cuccaro adders (`cuccaroModAddResources`, 2w - 3 Toffolis),
    the L2Q LEAN_FAITHFUL tier.

Qubits (decision D3): Lean's proved static allocation, `allocateReferenceLayout P inst m`
(Reference/ReferenceLayout.lean): x (exponent, counted once), data, work, scratch, each with the reserve
`Policy.shorWorkspaceNeed` gives it (Shor/Circuit/Workspace.lean), plus the flag. Each reserve is the maximum over
the steps that use that register (Steps 1, 2, 4, 5 and the QFTs, which run one after another), and a phase product's
reserve is `Policy.reserveNeed` (PhaseProduct/Compiler/PolicyWorkspace.lean): R(w) = immediateNeed + k R(W').
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from . import DEFAULT_DELTA, ENGINE
from tcost import tables as tt
import sto_currency as sc

FULL = ENGINE / "fullalg"
if str(FULL) not in sys.path:
    sys.path.insert(0, str(FULL))

import attrs  # noqa: E402
import l2q_full  # noqa: E402,F401  (checks it bound the site's vendored Qualtran)
from l2q_full import cost_conventions as cc  # noqa: E402
from l2q_full import policy as lpol  # noqa: E402
from l2q_full import policy_ir as pi  # noqa: E402
from l2q_full import policy_shor_ir as psi  # noqa: E402
from l2q_full import policy_shor_price as pr  # noqa: E402
from l2q_full import policy_shor_sym as sy  # noqa: E402

#: Stage 18 (2026-10-28): ForShor with the drop-in (ForShor 10fe620 + A+B+C+F, committed as forshor-dropin-2026-10-28
#: 2c3e659): exact register widths (`ir.width_rule`) and the balanced split for unequal operands (`balanced_*` IR
#: templates), printed by `lake exe forshor_policy_submission --balanced --probe-scan` on the shipped template.
REFERENCE = FULL / "data" / "policy.forshor.exact_balanced.json"
#: Upstream ForShor 10fe620's own document (scanned widths, unbalanced IR): the labelled cross-check.
REFERENCE_SCAN = FULL / "data" / "policy.forshor.json"
HERO_N = 2048
MODEL = "native-l2q-product/2-exact-balanced"
#: The model a scan document (upstream ForShor / the stock harness tree) is priced on: REFERENCE_SCAN, scanned widths.
MODEL_SCAN = "native-l2q-product/1-scan"
#: The score's convention: product leaf, Qualtran adders, negSrc = Subtract. Rotations are priced by `sto_currency`.
HEADLINE = cc.QUALTRAN.with_(name="site_product_leaf", leaf="product", leaf_options=("product",), negsrc="subtract")
#: Cross-check: the same structure with Lean's grid leaf (NaiveLeaf.lean:53), Qualtran adders.
GRID_LEAF = cc.QUALTRAN.with_(name="lean_grid_leaf", leaf="grid", leaf_options=("grid",), negsrc="subtract")
#: Cross-check: the literal Lean tier (merged-CPhase leaf, Cuccaro adders).
LITERAL_LEAN = cc.LEAN_FAITHFUL
TIER = ("ForShor native structure with the drop-in (L2Q PolicyShorCore: Step 4 cmpLtNW, Lean work width; exact "
        "register widths; balanced split for unequal operands), "
        "Qualtran digital-product leaf, Qualtran/Gidney adders, rotations 3.075 log2(1/eps) + 0.86 at "
        "eps = 0.5 delta / R_total")
TIER_SCAN = ("upstream ForShor 10fe620 native structure (L2Q PolicyShorCore: Step 4 cmpLtNW, Lean work width; scanned "
             "register widths; no balanced split), Qualtran digital-product leaf, Qualtran/Gidney adders, rotations "
             "3.075 log2(1/eps) + 0.86 at eps = 0.5 delta / R_total")
CENSUS_TRUST = ("census from the ForShor drop-in's IR (policy.forshor.exact_balanced.json), matched to the compiled circuit at sampled widths; "
                "not proved at n = 2048")


@lru_cache(maxsize=1)
def native():
    return pi.load_native(str(REFERENCE))


@lru_cache(maxsize=1)
def native_scan():
    return pi.load_native(str(REFERENCE_SCAN))


@lru_cache(maxsize=1)
def width_rule() -> str:
    """The board's width rule: the reference document's own (`ir.width_rule` present: exact)."""
    return lpol.doc_width_rule(_ref_doc())


@lru_cache(maxsize=1)
def _ref_doc() -> Dict[str, Any]:
    return json.loads(REFERENCE.read_text())


@lru_cache(maxsize=1)
def trial_count_2048() -> int:
    return int(_ref_doc()["benchmark"]["trial_count"])


@lru_cache(maxsize=64)
def precision_m(n: int) -> int:
    """The precision level m. At ForShor's benchmark size (n = 2048) it is the m of ForShor's own policy document,
    `Reference.m2048` = 2^150 - 3 (Reference2048Headline.lean:80), so the work width is Lean's. Elsewhere Lean fixes
    no m: the smallest m keeping 99/100 of ForShor's success-bound baseline at N = 2^(n-1)."""
    doc = _ref_doc()
    if int(n) == int(doc["benchmark"]["bits"]):
        return int(doc["precision"]["m"])
    return int(psi.min_precision_m(1 << (int(n) - 1)))


def precision_rule(n: int) -> str:
    return ("Reference.m2048 = 2^150 - 3 (ForShor's policy.json)" if int(n) == int(_ref_doc()["benchmark"]["bits"])
            else "smallest m keeping 99/100 of ForShor's success bound (L2Q min_precision_m); Lean fixes no m at this n")


@lru_cache(maxsize=64)
def core(n: int) -> "pr.PolicyShorCore":
    conv = sy.Convention.for_modulus(1 << (int(n) - 1), precision_m(n))
    return pr.PolicyShorCore.from_native(native(), int(n), conv, HEADLINE)


def core_scan(n: int) -> "pr.PolicyShorCore":
    """The cross-check's core: upstream ForShor's own IR (scanned widths, unbalanced)."""
    conv = sy.Convention.for_modulus(1 << (int(n) - 1), precision_m(n))
    return pr.PolicyShorCore.from_native(native_scan(), int(n), conv, HEADLINE)


# ---------------------------------------------------------------------------------------------- policies
_NAMES: Dict[str, tt.Table] = {}


def to_l2q_full(P: tt.Policy, rule: Optional[str] = None) -> "lpol.PhasePolicy":
    """A tcost PhaseProduct policy as an l2q_full `PhasePolicy` (one name per distinct table), under ForShor width rule
    ``rule``. Default: "scan" when the policy itself says ``widths == "scan"`` (every caller that asks for ForShor's
    scanned widths, e.g. Lean's static layout at scanned widths), else the board's rule, `width_rule()`."""
    if rule is None and P.widths == "scan":
        rule = "scan"
    from forshor_qualtran.table_catalog import op_from_sto, point_from_sto      # the site fork's converters
    if P.mode != tt.PAIR:
        raise ValueError("the order-finding circuit uses phase products only (no PhaseTripleProduct)")
    bands = []
    for b in P.bands:
        T = b.table
        name = f"k{T.k}-{T.digest[:10]}"
        _NAMES[name] = T
        j = T.to_json()
        L = lpol.ToomCookTable.from_json(name, {"k": T.k, "points": [point_from_sto(p) for p in j["point_order"]],
                                                "ops": [op_from_sto(o) for o in j["operations"]]})
        bands.append(lpol.PolicyBand(int(b.threshold), L))
    LP = lpol.PhasePolicy(tuple(bands), name=P.name or "site-policy")
    rule = width_rule() if rule is None else rule
    return LP if rule == "scan" else lpol.with_width_rule(LP, rule)


def _table_id(name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """l2q table name -> (archive / DP / KMY id, digest)."""
    if name is None:
        return None, None
    T = _NAMES.get(name)
    if T is None:
        return name, None
    from . import library as lib
    return lib.table_origin().get(T.digest, "pasted"), T.digest


# ---------------------------------------------------------------------------------------------- parts of the run
def _site_label(site: Tuple, w: Dict[str, int]) -> str:
    """A site or template call by the Step it is (a balanced call is labelled by its own (unbalanced) widths)."""
    name, a, b = site[0], site[1], site[2]
    base = name[len("balanced_"):] if name.startswith("balanced_") else name
    d, wk, s, x = w["yW"], w["workW"], w["scratchW"], w["xW"]
    labels = {("cphase_product", d + 1, wk + 1): "Step 1: controlled phase product (data+1 x work+1)",
              ("phase_product", wk + 1, d + 2): "Step 2: phase product (work+1 x data+2)",
              ("phase_product", wk + 1, s + 1): "Step 4 cmpLtNW: phase product (work+1 x scratch+1)",
              ("cphase_product", d + 2, wk + 1): "Step 5: controlled phase product (data+2 x work+1)",
              ("qft", d + 1, 0): "QFT on data+1", ("qft", wk, 0): "QFT on work",
              ("qft", s, 0): "Step 4 cmpLtNW: QFT on scratch", ("qft", x, 0): "final QFT on the exponent register"}
    lab = labels.get((base, a, b), f"{base} ({a} x {b})" if base != "qft" else f"QFT on {a} bits")
    return lab + (", balanced" if base != name else "")


def _expand(c: "pr.PolicyShorCore", site: Tuple) -> Tuple[Tuple[Tuple[str, int, int], int], ...]:
    """The plain sites one template call makes (a balanced call through its IR body; a plain site is itself)."""
    return pr.expand_sites(c.policy, ((site, 1),), c.balanced_doc, c.widths)


def _ladder(LP, a: int, b: int) -> List[Dict[str, Any]]:
    out, wx, wz = [], a, b
    while True:
        s = LP.step(wx, wz)
        if s is None:
            out.append({"W": max(wx, wz), "k": None, "table": "leaf", "digest": None})
            return out
        T, w2 = s
        tid, dg = _table_id(T.name)
        out.append({"W": max(wx, wz), "k": T.k, "table": tid, "digest": dg})
        wx = wz = w2


def _qft_twiddles(w: int, mult: int, acc: Counter) -> None:
    """Every twiddle phase product inside qft(w): (left + 1, right + 1) at each split (policy_shor_price.PolicyQFT)."""
    todo = Counter({int(w): mult})
    while todo:
        v = max(todo)
        m = todo.pop(v)
        if v < 2:
            continue
        left, right = v // 2, v - v // 2
        acc[("phase_product", left + 1, right + 1)] += m
        todo[left] += m
        todo[right] += m


def main_shape(c: "pr.PolicyShorCore") -> Tuple[int, int]:
    """The operand widths the main multiplier (Step 2, (work+1) x (data+2)) actually runs at. Stage 18: on the drop-in
    IR Step 2 is a `balanced_phase_product` call, which pads it to (work+1) x (work+1) (2L <= 3s) when the reserves fit;
    its ladder is the one the run descends, not the ladder of the unbalanced shape."""
    w = dict(c.widths)
    a, b = w["workW"] + 1, w["yW"] + 2
    for site, _ in c.raw_sites:
        if site[0] in ("phase_product", "balanced_phase_product") and (site[1], site[2]) == (a, b):
            subs = [s_ for s_, _ in _expand(c, site) if s_[0] == "phase_product"]
            if subs:
                top = max(subs, key=lambda s_: (max(s_[1], s_[2]), s_[1] + s_[2]))
                return top[1], top[2]
    return a, b


def _split(b) -> cc.TCost:
    return cc.split(b)


def parts(c: "pr.PolicyShorCore", tpr: float) -> Dict[str, Any]:
    """The run by part (each call of the template, plus its own arithmetic) and every distinct phase product the run
    contains, each with its count, ladder, T (at the run's rotation price) and share; parts sum to the T.
    Stage 18: a part is a template CALL (`raw_sites`), so Steps 1, 2, 4 and 5 keep their rows when the drop-in's balanced
    split turns the call into other products; `balanced_into` lists the plain sites it makes, `ladder` is the widest's."""
    w = dict(c.widths)
    LP = c.policy
    total = cc.split(c)
    rows, roots = [], Counter()
    site_sum = cc.ZERO
    for site, k in c.raw_sites:
        name, a = site[0], site[1]
        b = site[2] if name != "qft" else 0
        subs = _expand(c, site)
        sc_ = cc.ZERO
        for sub, m in subs:
            sc_ = sc_ + _split(pr.site_bloq(LP, sub, c.convention)).scale(m)
        sc_ = sc_.scale(k)
        site_sum = site_sum + sc_
        Rx, Rz = (pi.qft_workspace_need(LP, a) if name == "qft" else LP.reserve_need(a, b))
        widest = max(subs, key=lambda sm: (max(sm[0][1], sm[0][2]), sm[0][1] + sm[0][2]))[0]
        rows.append({"part": _site_label((name, a, b), w), "site": name, "xw": a, "zw": b, "count": k,
                     "T": sc_.base + sc_.rot * tpr, "rotations": sc_.rot, "lean_static_qubits": a + b + Rx + Rz,
                     "reserve": [Rx, Rz],
                     "balanced_into": ([{"site": s_[0], "xw": s_[1], "zw": s_[2], "count": m} for s_, m in subs]
                                       if name.startswith("balanced_") else None),
                     "ladder": _ladder(LP, widest[1], widest[2]) if widest[0] != "qft" else None})
        for sub, m in subs:
            if sub[0] == "qft":
                _qft_twiddles(sub[1], k * m, roots)
            else:
                roots[sub] += k * m
    own = cc.TCost(total.base - site_sum.base, total.rot - site_sum.rot)
    rows.append({"part": "the template's own arithmetic (Step 3 and Step 4 adders, negates; Clifford gates)",
                 "site": "own", "xw": None, "zw": None, "count": 1, "T": own.base + own.rot * tpr,
                 "rotations": own.rot, "lean_static_qubits": None, "reserve": None, "balanced_into": None, "ladder": None})
    T = total.base + total.rot * tpr
    for r in rows:
        r["share"] = r["T"] / T if T else 0.0
    usage, used = [], {}
    for (name, a, b), k in sorted(roots.items(), key=lambda kv: -max(kv[0][1], kv[0][2])):
        bl = pr.site_bloq(LP, (name, a, b), c.convention)
        s = _split(bl).scale(k)
        lad = _ladder(LP, a, b)
        usage.append({"W": f"{a}x{b}" if a != b else str(a), "xw": a, "zw": b, "site": name, "count": k,
                      "T": s.base + s.rot * tpr, "ladder": lad,
                      "ladder_str": " -> ".join(f"{x['W']}:{x['k'] or 'leaf'}" for x in lad)})
        for x in lad:
            if x["k"]:
                e = used.setdefault(x["digest"] or x["table"], {"id": x["table"], "digest": x["digest"], "k": x["k"],
                                                                "widths": set(), "roots": 0})
                e["widths"].add(x["W"])
        for key in dict.fromkeys(x["digest"] or x["table"] for x in lad if x["k"]):
            used[key]["roots"] += k
    pp_T = sum(u["T"] for u in usage) or 1.0
    for u in usage:
        u["share"] = u["T"] / pp_T
    tables_used = sorted(({**e, "widths": sorted(e["widths"], reverse=True)} for e in used.values()),
                         key=lambda e: (-e["k"], e["id"]))
    return {"parts": rows, "usage": usage, "tables_used": tables_used}


# ---------------------------------------------------------------------------------------------- qubits
def layout(LP, widths: Dict[str, int]) -> Dict[str, Any]:
    """Lean's static allocation `allocateReferenceLayout`: registers, their reserves and what sizes each reserve."""
    need = pr.layout_need(LP, widths)
    dw, ww, sw, xw = widths["yW"], widths["workW"], widths["scratchW"], widths["xW"]
    s1, s2 = LP.reserve_need(dw + 1, ww + 1), LP.reserve_need(ww + 1, dw + 2)
    s4, s5 = LP.reserve_need(ww + 1, sw + 1), LP.reserve_need(dw + 2, ww + 1)
    q = lambda n: sum(pi.qft_workspace_need(LP, n))
    cand = {"x": {"final QFT": q(xw)},
            "y": {"Step 1": 2 + s1[0], "QFT data+1": 1 + q(dw + 1), "Step 2": 2 + s2[1], "Step 5": 2 + s5[0], "floor": 2},
            "work": {"QFT work": q(ww), "Step 1": 1 + s1[1], "Step 2": 1 + s2[0], "Step 4": 1 + s4[0],
                     "Step 5": 1 + s5[1], "floor": 1},
            "scratch": {"QFT scratch": q(sw), "Step 4": 1 + s4[1], "floor": 1}}
    regs = []
    for r, act in (("x", xw), ("y", dw), ("work", ww), ("scratch", sw)):
        by = max(cand[r], key=lambda k: cand[r][k])
        assert cand[r][by] == need[r], (r, cand[r], need[r])
        regs.append({"register": {"x": "exponent x", "y": "data y"}.get(r, r), "active": act, "reserve": need[r],
                     "reserve_set_by": by})
    total = sum(g["active"] + g["reserve"] for g in regs) + 1
    assert total == pr.layout_qubits(LP, widths)
    return {"total": total, "registers": regs, "flag": 1,
            "rule": "allocateReferenceLayout (Reference/ReferenceLayout.lean): each register's reserve is the maximum "
                    "over the steps that use it (Policy.shorWorkspaceNeed); exponent register counted once"}


def pp_lean_static_qubits(P: tt.Policy, w: int, rule: Optional[str] = None) -> Dict[str, int]:
    """One phase product of width w under Lean's static allocation: Q = 2w + Rx + Rz (Policy.reserveNeed), at ForShor's
    scanned widths when ``P.widths == "scan"`` (upstream ForShor's proved layout) and otherwise at the board's rule (the
    drop-in's exact widths, whose reserveNeed the drop-in's policy.json tabulates); ``rule`` overrides both."""
    LP = to_l2q_full(P, rule)
    rx, rz = LP.reserve_need(int(w), int(w))
    return {"w": int(w), "reserve_x": rx, "reserve_z": rz, "qubits": 2 * int(w) + rx + rz}


# ---------------------------------------------------------------------------------------------- pricing
def _gate_counts(c) -> Dict[str, int]:
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    from tcost.counts import CNOTCount
    g = get_cost_value(c, QECGatesCost())
    rot = g.rotation
    return {"and": int(g.and_bloq), "toffoli": int(g.toffoli), "cswap": int(g.cswap), "t": int(g.t),
            "rotation": int(sum(rot.values()) if isinstance(rot, dict) else rot), "clifford": int(g.clifford),
            "measurement": int(g.measurement), "cnot": int(get_cost_value(c, CNOTCount()))}


def _t_of(c, spec, delta: float) -> Dict[str, Any]:
    s = cc.split(attrs.evolve(c, convention=spec))
    tpr = sc.ts_per_rotation(delta, s.rot) if s.rot else 0.0
    return {"T": s.base + s.rot * tpr, "T_base": int(s.base), "rotations": int(s.rot), "T_per_rotation": tpr,
            "eps": sc.rotation_eps(delta, s.rot) if s.rot else None}


def _scan_tier(LP, n: int, delta: float) -> Dict[str, Any]:
    """Upstream ForShor 10fe620's IR (REFERENCE_SCAN: scanned widths, no balanced split), same leaf and adders."""
    LS = lpol.with_width_rule(LP, "scan")
    cs = attrs.evolve(core_scan(int(n)), policy=LS, convention=HEADLINE)
    scan = _t_of(cs, HEADLINE, delta)
    lad = _ladder(LS, *main_shape(cs))
    scan.update(qubits=cs.layout_qubits(), ladder_str=" -> ".join(f"{x['W']}:{x['k'] or 'leaf'}" for x in lad))
    return scan


@lru_cache(maxsize=4096)
def _price(LP, n: int, delta: float, detail: bool, ir: str = "board") -> Dict[str, Any]:
    """``ir``: "board" (the drop-in document REFERENCE, the board's width rule) or "scan" (upstream ForShor's
    REFERENCE_SCAN at scanned widths: what a scan document, e.g. from the stock harness tree, is priced and checked on)."""
    c = attrs.evolve((core if ir == "board" else core_scan)(int(n)), policy=LP, convention=HEADLINE)
    head = _t_of(c, HEADLINE, delta)
    grid = _t_of(c, GRID_LEAF, delta)
    lit = _t_of(c, LITERAL_LEAN, delta)
    g = _gate_counts(c)
    widths = dict(c.widths)
    lay = layout(LP, widths)
    main = _ladder(LP, *main_shape(c))
    checks = {"lean_grid_leaf": {**grid, "label": "same structure, Lean's W x W grid leaf (NaiveLeaf.lean:53), "
                                                  "Qualtran adders: a cross-check, not the score"},
              "literal_lean": {**lit, "label": "literal Lean tier: merged-CPhase leaf, ForShor's Cuccaro adders "
                                               "(2w - 3 Toffolis): a cross-check, not the score"}}
    if ir == "board":
        checks["forshor_scan_ir"] = {**_scan_tier(LP, n, delta),
                                     "label": "upstream ForShor 10fe620's IR: scanned widths, no balanced split "
                                              "(same leaf and adders): a cross-check, not the score"}
    out = {"n": int(n), "delta": float(delta), "model": MODEL if ir == "board" else MODEL_SCAN,
           "tier": TIER if ir == "board" else TIER_SCAN, "census_trust": CENSUS_TRUST,
           **head, "converged": True,
           "qubits": lay["total"], "layout": lay,
           "toffoli": g["and"] + g["toffoli"] + g["cswap"], "cnot": g["cnot"], "clifford": g["clifford"], "gate_counts": g,
           "cross_checks": checks,
           "width_rule": width_rule() if ir == "board" else "scan",
           "lean_grid_leaf_T": grid["T"], "literal_lean_T": lit["T"],
           "m_bits": precision_m(n).bit_length(), "precision_rule": precision_rule(n),
           "work_width": widths["workW"], "widths": widths,
           "ladder": main, "ladder_str": " -> ".join(f"{x['W']}:{x['k'] or 'leaf'}" for x in main)}
    if detail:
        out.update(parts(c, head["T_per_rotation"]))
        ids = [t["id"] for t in out["tables_used"]]
        out["label"] = " + ".join(dict.fromkeys(ids)) or "leaf"
    out["main_label"] = " + ".join(dict.fromkeys(x["table"] for x in main if x["k"])) or "leaf"
    if int(n) == HERO_N:
        out["trial_count"] = trial_count_2048()
        out["end_to_end"] = trial_count_2048() * out["T"]
    return out


def price_l2q(LP: "lpol.PhasePolicy", n: int, delta: float = DEFAULT_DELTA, detail: bool = True,
              rule: Optional[str] = None) -> Dict[str, Any]:
    """Single-run cost of the whole order-finding run with an l2q_full policy (e.g. a ForShor native document's). The
    policy's tables take the board's width rule (Stage 18: a table built elsewhere defaults to "scan", and the board's
    IR is exact; mixing the two would price narrower registers than the IR's layout holds).
    ``rule="scan"`` prices on upstream ForShor's IR at scanned widths instead (the scan cross-check as the headline:
    the tier a scan document, e.g. one from the stock ForShor harness tree, is checked on)."""
    ir = "board" if rule in (None, width_rule()) else "scan"
    if ir == "scan" and rule != "scan":
        raise ValueError(f"unknown width rule {rule!r}")
    LP = lpol.with_width_rule(LP, width_rule() if ir == "board" else "scan")
    return json.loads(json.dumps(_price(LP, int(n), float(delta), bool(detail), ir)))


def price_full(P: tt.Policy, n: int, delta: float = DEFAULT_DELTA, detail: bool = True,
               rule: Optional[str] = None) -> Dict[str, Any]:
    """Single-run cost of the whole order-finding run with phase-product policy P, at modulus bits n (the board's IR
    and width rule; ``rule="scan"``: upstream ForShor's IR at scanned widths, see `price_l2q`)."""
    return price_l2q(to_l2q_full(P), n, delta, detail, rule)


def doc_width_rule(doc: Dict[str, Any]) -> str:
    """A ForShor policy.json's width rule: "exact" (drop-in, `ir.width_rule` present) or "scan" (upstream)."""
    return lpol.doc_width_rule(doc)


def price_document(doc: Dict[str, Any], n: int = HERO_N, delta: float = DEFAULT_DELTA) -> Dict[str, Any]:
    """T of a ForShor policy document (``forshor_policy_submission`` output) with ITS OWN IR census, under the score's
    convention: the harness's conversion check compares it with the converted site policy in the reference census."""
    conv = sy.Convention.for_modulus(1 << (int(n) - 1), precision_m(n))
    c = pr.PolicyShorCore.from_native(pi.load_native(doc), int(n), conv, HEADLINE)
    return _t_of(c, HEADLINE, float(delta))


def census(n: int, top: int = 12, rule: Optional[str] = None) -> Dict[str, Any]:
    """The template at n: layout widths, the template's own gates, its calls (`calls`, policy-independent: Steps 1, 2, 4,
    5 and the QFTs; on the drop-in IR Steps 1, 2, 4, 5 are `balanced_*` calls) and the plain phase-product / QFT sites
    they make (`top_sites`; a balanced call's split depends on the policy's reserves, so these are under the reference
    document's own policy). ``rule="scan"``: upstream ForShor's IR at scanned widths (no balanced calls)."""
    scan = rule == "scan"
    c = core_scan(int(n)) if scan else core(int(n))
    w = dict(c.widths)
    sites = sorted(((name, a, b, k) for (name, a, b), k in c.sites), key=lambda s: -s[3] * max(s[1], 1) * max(s[2], 1))
    kinds: Dict[str, int] = {}
    for name, _, _, k in sites:
        kinds[name] = kinds.get(name, 0) + k
    calls = []
    for site, k in c.raw_sites:
        name, a = site[0], site[1]
        b = site[2] if name != "qft" else 0
        calls.append({"site": name, "xw": a, "zw": b, "count": k, "part": _site_label((name, a, b), w),
                      "expands_to": [{"site": s_[0], "xw": s_[1], "zw": s_[2], "count": m} for s_, m in _expand(c, site)]})
    return {"n": int(n), "widths": w, "m_bits": precision_m(n).bit_length(), "precision_rule": precision_rule(n),
            "trust": CENSUS_TRUST, "width_rule": "scan" if scan else width_rule(),
            "own_gates": {"h": c.h, "x": c.x, "cnot": c.cnot, "toffoli": c.toffoli,
                          "adders": sum(k for _, k in c.add_scaled), "negates": sum(k for _, k in c.negate)},
            "site_kinds": kinds, "distinct_sites": len(sites), "calls": calls,
            "top_sites": [{"site": s[0], "xw": s[1], "zw": s[2], "count": s[3], "part": _site_label(s[:3], w)}
                          for s in sites[:top]]}
