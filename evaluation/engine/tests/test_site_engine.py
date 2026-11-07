"""Phase A checks (PLAN.md): pins reproduce, the two counting paths agree on PP and PTP, delta behaves, intake
rejects bad files, and the conventions are the site's (PP digital-product leaf, PTP digital triple-product leaf since
Stage 17 (grid leaf as cross-check), negated source = one
adder, eps = 0.5 delta / R, unrounded T per rotation, And = 4 T; PLAN_v2 Stage 5)."""
from __future__ import annotations

import copy
import json

import pytest

from site_engine import DATA, crosscheck as xc, frontier as fr, intake, library as lib
from tcost import planner as tp, score as ts, tables as tt

PP, PTP = tt.PAIR, tt.TRIPLE
N = 2048


def _opt(key, mode, n=N, delta=0.01, widths="exact"):
    return tp.optimal_policy(lib.milestone_tables(key, mode), mode, n, delta, widths=widths)[0]


# ------------------------------------------------------------------------------------------------ pins
# Re-pinned in Stage 13 (2026-10-28), reason: the PP and PTP boards plan and price at EXACT widths. The Stage 5 pins at
# ForShor's scanned widths are kept as the labelled cross-check (second value).
# Re-pinned in Stage 17 (2026-10-28), reason: the PTP board's leaf is the digital triple product. The Stage 13 PTP pins
# (grid leaf) are kept as the grid-leaf cross-check (GRID: planned and priced with leaf="grid", exact and scanned).
GRID_REF = {PTP: 14_242_595_585.181328}


@pytest.mark.parametrize("mode,want,scan", [(PP, 13_951_779.839838771, 19_046_850.546099674),
                                            (PTP, 138_815_365.68695214, 208_455_468.20555332)])
def test_reference_pins(mode, want, scan):
    P = lib.sto_reference_policies()[mode]
    assert ts.score_policy(P, N, 0.01)["t_count"] == pytest.approx(want, rel=1e-12)
    assert ts.score_policy(P.with_widths("scan"), N, 0.01)["t_count"] == pytest.approx(scan, rel=1e-12)
    if mode in GRID_REF:
        assert ts.score_policy(P, N, 0.01, leaf="grid")["t_count"] == pytest.approx(GRID_REF[mode], rel=1e-12)


@pytest.mark.parametrize("key,mode,want,scan", [
    ("champions", PP, 10_313_595.780228866, 15_714_490.242757222), ("all_sto", PP, 10_077_971.172425058, 13_254_468.055088544),
    ("dp", PP, 9_678_616.0160081, 13_254_468.055088544),
    ("champions", PTP, 93_520_337.58681431, 117_682_741.8227932), ("all_sto", PTP, 73_588_390.26055944, 117_682_741.8227932),
    ("dp", PTP, 73_588_390.26055944, 114_279_439.57441962)])
def test_library_pins(key, mode, want, scan):
    assert ts.score_policy(_opt(key, mode), N, 0.01)["t_count"] == pytest.approx(want, rel=1e-12)
    assert ts.score_policy(_opt(key, mode, widths="scan"), N, 0.01)["t_count"] == pytest.approx(scan, rel=1e-12)


# Stage 17: the Stage 13 PTP pins, reproduced as the grid-leaf cross-check (planned and priced with leaf="grid")
@pytest.mark.parametrize("key,want,scan", [("champions", 3_312_422_191.9647865, 15_693_043_332.862968),
                                           ("all_sto", 2_575_548_521.975925, 15_693_043_332.862968),
                                           ("dp", 2_574_587_561.975925, 12_068_904_014.841936),
                                           ("kmy", 5_608_558_646.543741, 38_357_893_759.1274)])
def test_ptp_grid_leaf_pins_are_the_stage13_numbers(key, want, scan):
    for widths, v in (("exact", want), ("scan", scan)):
        P = tp.optimal_policy(lib.milestone_tables(key, PTP), PTP, N, 0.01, widths=widths, leaf="grid")[0]
        assert ts.score_policy(P, N, 0.01, leaf="grid")["t_count"] == pytest.approx(v, rel=1e-12)


def test_library_sizes():
    subs = lib.submissions()
    assert len(subs) == 21 and sum(s.accepted for s in subs) == 18
    assert len(lib.personal_tables()) == 182
    assert len(lib.champion_tables()) == 30


# ------------------------------------------------------------------------------------------------ cross-check
CASES = [("reference", m) for m in (PP, PTP)] + [(k, m) for k in ("all_sto", "dp") for m in (PP, PTP)]


@pytest.mark.parametrize("key,mode", CASES)
@pytest.mark.parametrize("n", [512, 1500, 2048, 4096])
@pytest.mark.parametrize("delta", [1e-2, 1e-4, 1e-6])
def test_engines_agree(key, mode, n, delta):
    P = lib.sto_reference_policies()[mode] if key == "reference" else _opt(key, mode, n, delta)
    r = xc.cross_check(P, n, delta)
    assert r["agree"], r


def test_tcost_concrete_oracle_matches_symbolic_ptp():
    """tcost's own concrete PolicyPhaseProduct bloq equals the symbolic score for the PTP reference (a third count)."""
    from tcost import bloqs as tb, counts as tco
    P = lib.sto_reference_policies()[PTP]
    assert ts.score_policy(P, 300)["gate_counts"] == tco.gate_counts(tb.PolicyPhaseProduct(P, 300))


# ------------------------------------------------------------------------------------------------ conventions
def test_one_currency():
    import sto_currency
    from forshor_qualtran import cost_conventions as cc, rotation
    from tcost import currency
    assert currency.ts_per_rotation is sto_currency.ts_per_rotation
    assert not hasattr(rotation.RotationBudget(), "rotation_fraction")
    # Stage 17 re-pin (not a Stage 18 change): the PTP board's leaf is the digital triple product (was "grid")
    assert cc.LEAVES == ("grid", "product") and cc.leaf_of("PhaseProduct") == "product" and cc.leaf_of("PhaseTripleProduct") == "product"
    assert cc.SITE.negsrc == "subtract" and sto_currency.ROTATION_FRACTION == 0.5
    assert sto_currency.TS_PER_TOFFOLI == 4
    for R in (1, 10, 785_565, 10**9):
        for d in (1e-2, 1e-6):
            assert cc.SITE.with_(delta=d).t_per_rotation(R) == sto_currency.ts_per_rotation(d, R)
            assert sto_currency.ts_per_rotation(d, R) == pytest.approx(3.075 * __import__("math").log2(2 * R / d) + 0.86, rel=1e-12)


def test_reference_breakdown():
    """At ForShor's scanned widths (Stage 5 pin, kept as the cross-check in Stage 13):
    19,046,850.5 = 4 x (428,318 And + 3,073,950 Toffoli) + 68,310 x 73.7488 T rotations, eps = 0.005 / 68,310.
    The ladder 2048:6 -> 385:3 -> 140:2 -> 73:2 -> 40:2 -> 23:leaf has 1,485 leaves of width 23, 2 x 23 rotations each.
    At exact widths (the board): 2048:6 -> 364:2 -> 184:2 -> 94:2 -> 49:2 -> 26:leaf, 891 leaves of width 26."""
    e = ts.score_policy(lib.sto_reference_policies()[PP], N, 0.01)
    assert (e["gate_counts"]["and"], e["gate_counts"]["toffoli"], e["gate_counts"]["rotation"]) == (290_730, 2_362_932, 891 * 52)
    assert (e["leaf_calls"], e["leaf_width"]) == (891, 26)
    s = ts.score_policy(lib.sto_reference_policies()[PP].with_widths("scan"), N, 0.01)
    g, b = s["gate_counts"], s["t_breakdown"]
    assert (g["and"], g["toffoli"], g["rotation"]) == (428_318, 3_073_950, 68_310 == 1485 * 46 and 68_310)
    assert b["ts_per_rotation"] == pytest.approx(3.075 * __import__("math").log2(68_310 / 0.005) + 0.86)
    assert b["rotation_eps"] == pytest.approx(0.005 / 68_310)
    assert 4 * (g["and"] + g["toffoli"]) + g["rotation"] * b["ts_per_rotation"] == pytest.approx(19_046_850.546099674)


@pytest.mark.parametrize("mode", [PP, PTP])
def test_delta_moves_T(mode):
    P = lib.sto_reference_policies()[mode]
    t = [ts.score_policy(P, N, d)["t_count"] for d in (1e-2, 1e-4, 1e-6)]
    assert t[0] < t[1] < t[2]
    s = ts.score_policy(P, N, 1e-2)
    R = s["gate_counts"]["rotation"]
    # only the rotation term moves: 3.075 * log2(100) ~ 20.4 T per rotation per 2 decades
    assert t[1] - t[0] == pytest.approx(R * (ts.score_policy(P, N, 1e-4)["t_breakdown"]["ts_per_rotation"] - s["t_breakdown"]["ts_per_rotation"]), rel=1e-6)


def test_windows_contain_design_point():
    lo, hi = ts.valid_range(lib.sto_reference_policies()[PP], N, 4)
    assert (lo, hi) == (2048, 2158)                  # Stage 13 re-pin (exact widths); scanned widths: (2048, 4378)
    assert ts.valid_range(lib.sto_reference_policies()[PP].with_widths("scan"), N, 4) == (2048, 4378)
    for n in (256, 1024, 2048, 6502):
        p = fr.planned("all_sto", PP, n, 0.01)[1]
        assert p["window"][0] <= n <= p["window"][1]


# ------------------------------------------------------------------------------------------------ intake
def _ref_doc():
    return json.loads((DATA / "reference_policies.json").read_text())["policies"][0]["policy"]


def test_intake_accepts_reference_and_tags_it():
    r = intake.cost(json.dumps(_ref_doc()))
    p = r["policies"][0]
    assert p["point"]["T"] == pytest.approx(13_951_779.839838771, rel=1e-12) and p["cross_check"]["agree"]   # exact (Stage 13)
    # the reference policy is built from accepted (Lean-verified) STO tables; the bands are a pasted arrangement
    assert p["tags"][0] == "Tables Lean-verified" and p["trust"]["level"] == "lean"
    assert "pasted: bands not Lean-checked" in p["tags"]


BAD = {
    "empty": lambda d: "",
    "not json": lambda d: "{mode: PhaseProduct",
    "schema-1 tables": lambda d: json.dumps({"targets": []}),
    "no bands": lambda d: json.dumps({**d, "bands": []}),
    "two tables for one k": lambda d: json.dumps({**d, "bands": d["bands"][:2] + [
        {**d["bands"][1], "threshold": 100, "point_order": ["0", "1", "inf", "-1", "2"]}]}),
    "bad thresholds": lambda d: json.dumps({**d, "bands": [d["bands"][1], d["bands"][0]]}),
    "inconsistent bands/band_tables": lambda d: json.dumps({"mode": d["mode"], "band_tables": d["bands"],
                                                            "bands": [{"threshold": 1, "k": 2}]}),
    "broken table program": lambda d: json.dumps({**d, "bands": [{**d["bands"][2], "operations": d["bands"][2]["operations"][:-1]}]}),
    "wrong point count": lambda d: json.dumps({**d, "bands": [{**d["bands"][2], "point_order": ["0", "inf"]}]}),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_intake_rejects(case):
    with pytest.raises(intake.IntakeError) as e:
        intake.cost(BAD[case](copy.deepcopy(_ref_doc())))
    assert len(str(e.value)) > 10


def test_intake_rejects_bad_delta():
    with pytest.raises(ValueError):
        intake.cost(json.dumps(_ref_doc()), delta=0)


# ------------------------------------------------------------------------------------------------ planners
def test_l2q_planner_not_better_than_tcost_planner_pp():
    """Engine 2 has its own exact PP planner (Pareto front over (T_base, R)). Under the shared conventions it must
    not find a cheaper policy than tcost's planner over the same tables. If this fails, tcost's fixed-point
    planner is missing an optimum and the frontier understates the improvement."""
    from forshor_qualtran import cost_conventions as cc, policy_planner as pp
    tabs = lib.milestone_tables("all_sto", PP)
    L = [xc.to_l2q(tt.Policy.constant(T)).bands[0].table for T in tabs]
    L = [type(t)(f"t{i}", t.k, t.points, t.ops_frozen) for i, t in enumerate(L)]
    pl = pp.Planner(L, "t", convention=cc.SITE)
    l2q_T = pl.chain_cost(pl.chain(N))
    # engine 2's exact planner plans at the scan (its own nextWidth): compare at scanned widths (Stage 13)
    tcost_T = ts.score_policy(_opt("all_sto", PP, widths="scan"), N, 0.01)["t_count"]
    assert l2q_T >= tcost_T, (l2q_T, tcost_T)


# ------------------------------------------------------------------------------------------------ full policies
@pytest.mark.parametrize("key,mode", [("reference", PP), ("reference", PTP), ("dp", PP), ("dp", PTP), ("champions", PP)])
def test_policy_view_is_complete_and_round_trips(key, mode):
    from site_engine import policy_view as pv
    v = pv.for_milestone(key, mode, N, 0.01)
    assert v["agree"] and v["T"] == v["engine2_T"]
    # level T shares add up to the total
    assert sum(r["T"] for r in v["levels"]) == v["T"]
    # one table per k, and every k on the ladder has its table with points and operations
    ks = [t["k"] for t in v["tables"]]
    assert len(ks) == len(set(ks))
    assert {r["k"] for r in v["levels"] if r["k"]} <= set(ks)
    for t in v["tables"]:
        assert len(t["points"]) == t["point_count"] and t["operations"]
    # the exported JSON is a valid submission with the same T
    r = intake.cost(json.dumps(v["policy_json"]), N, 0.01)["policies"][0]
    assert r["point"]["T"] == v["T"]


def test_policy_view_leaderboard_row():
    from site_engine import policy_view as pv
    v = pv.for_leaderboard_row("546fea33a7a4", PP, 0.01)
    # Stage 13 re-pin (exact widths); at scanned widths this row was 13,254,468.06 T, k = 6, 4, 2
    assert v["T"] == pytest.approx(10_077_971.172425058, rel=1e-12) and [r["k"] for r in v["levels"]] == [8, 3, 3, None]
    assert v["scan_cross_check"]["T"] > v["T"]


# ------------------------------------------------------------------------------------------------ qubits
@pytest.mark.parametrize("w", [2, 3, 5, 8, 13])
def test_leaf_qubit_count_matches_decomposition(w):
    """The static leaf qubit counts (tcost/bloqs.py, sto_site) equal Qualtran's count of the decomposed leaf."""
    from qualtran.resource_counting import QubitCount, get_cost_value
    from fractions import Fraction
    from tcost.bloqs import PairLeaf, TripleLeaf
    # the grid pair leaf (concrete phi, the one that simulates) and the triple leaf
    for leaf, want in ((PairLeaf(w, w + 1, Fraction(1, 3)), 2 * w + 2), (TripleLeaf(w, w, w), 3 * w + 2)):
        assert leaf.my_static_costs(QubitCount()) == want
        cb = leaf.decompose_bloq()
        assert get_cost_value(cb, QubitCount()) == want


def test_kmy_benchmark():
    """KMY tables (arXiv:2403.18006v4) pass both engines' intake checks; the KMY benchmark at n = 2048."""
    from site_engine import library as lib
    assert {r["id"] for r in lib.kmy_tables()} == {"KMY-PP3", "KMY-PTP3", "KMY-PTP4"}
    for r in lib.kmy_tables():
        xc.to_l2q(tt.Policy.constant(r["table"]))
    # Stage 13 re-pin (exact widths); the scanned-width pins are kept as the cross-check
    assert ts.score_policy(_opt("kmy", PP), N, 0.01)["t_count"] == pytest.approx(12_882_273.822456302, rel=1e-12)
    # Stage 17 re-pin (digital PTP leaf; was 5,608,558,646.5 / 38,357,893,759.1 with the grid leaf, now pinned in
    # test_ptp_grid_leaf_pins_are_the_stage13_numbers)
    assert ts.score_policy(_opt("kmy", PTP), N, 0.01)["t_count"] == pytest.approx(96_146_895.03315397, rel=1e-12)
    assert ts.score_policy(_opt("kmy", PP, widths="scan"), N, 0.01)["t_count"] == pytest.approx(21_648_524.992123913, rel=1e-12)
    assert ts.score_policy(_opt("kmy", PTP, widths="scan"), N, 0.01)["t_count"] == pytest.approx(143_104_808.87460065, rel=1e-12)


# ------------------------------------------------------------------ full order finding (PLAN_v2 Stage 2)
def test_full_reproduces_l2q_policy_score():
    """The vendored pipeline gives Lean-To-Qualtran's own policy_score for ForShor's reference submission."""
    from site_engine import fullalg as fa
    from l2q_full import policy_score as ps
    doc = ps.score(fa.REFERENCE)
    assert doc["score"]["trial_count"] == str(fa.trial_count_2048())
    m = int(json.loads(fa.REFERENCE.read_text())["precision"]["m"])
    import attrs
    from l2q_full import cost_conventions as cc, policy_shor_price as pr, policy_shor_sym as sy
    core = pr.PolicyShorCore.from_native(fa.native(), 2048, sy.Convention.for_modulus(1 << 2047, m), cc.QUALTRAN)
    assert pr.price(core, cc.QUALTRAN)["T"] == pytest.approx(doc["score"]["single_run_T"], rel=1e-12)


def test_full_mirror_and_end_to_end():
    from site_engine import fullalg as fa
    P = _opt("all_sto", PP, widths="scan")              # the full run's policies are planned at scanned widths (Stage 13)
    r = fa.price_full(P, N, 0.01)
    assert r["end_to_end"] == pytest.approx(fa.trial_count_2048() * r["T"], rel=1e-15)
    # Stage 18: priced on the drop-in IR (exact widths, balanced split): 41,954 qubits (was 45,986 at scanned widths,
    # kept as the scan cross-check)
    assert r["qubits"] == 41_954 and r["converged"] and r["width_rule"] == "exact" and r["model"] == fa.MODEL
    assert r["cross_checks"]["forshor_scan_ir"]["qubits"] == 45_986
    assert fa.price_full(P, 1024, 0.01).get("end_to_end") is None          # trial count is ForShor's at n = 2048 only
    with pytest.raises(ValueError):
        fa.to_l2q_full(_opt("dp", PTP))


@pytest.mark.parametrize("key", ["kmy", "reference", "all_sto"])
@pytest.mark.parametrize("w", [300, 2048])
@pytest.mark.parametrize("widths", ["exact", "scan"])
def test_full_seam_phase_product_equals_tcost(key, w, widths):
    """Seam between the two packages: under the digital-product leaf, Qualtran adders, negated source = one Subtract
    (the site's conventions), the l2q_full PolicyPhaseProduct at (w, w) has tcost's T-without-rotations and rotation
    count for the same policy, at the same width rule.
    Stage 18 (rewritten): the full run prices at the ForShor drop-in's exact widths (`forshor_exact_width`, max(tight,
    floor)), and the PP board at STO's exact widths (`tcost/exact`, tightWidth); the seam is checked at exact widths
    (the board's) and, as before, at scanned widths (the cross-check). The two exact rules differ only where ForShor's
    floor (1 + widest initial chunk) binds, at w <= 2 (see test_forshor_exact_width_is_sto_tight_width_or_the_floor);
    every band of these policies starts far above that, so the seam is exact.
    Swap 002: `tcost` (the PP/PTP board engine) still prices a negation the old way (Qualtran `Negate`), so the seam is stated under
    `negate="legacy"` (ForShor `scratch`); under the real default negation a table that negates (KMY's) no longer matches it, see
    test_full_seam_diverges_from_tcost_for_a_negating_table_under_the_new_negation."""
    from site_engine import fullalg as fa
    from l2q_full import cost_conventions as cc
    from l2q_full.bloqs.policy_phase_product import PolicyPhaseProduct
    P = (lib.sto_reference_policies()[PP] if key == "reference" else _opt(key, PP, widths=widths)).with_widths(widths)
    spec = cc.QUALTRAN.with_(leaf="product", leaf_options=("product",), negsrc="subtract", negate="legacy")
    LP = fa.to_l2q_full(P)
    assert {b.table.width_rule for b in LP.bands} == {widths}
    c = cc.split(PolicyPhaseProduct(LP, w, w, convention=spec))
    s = ts.score_policy(P, w, 0.01)
    assert (c.base, c.rot) == (s["t_breakdown"]["t_from_toffoli_class"], s["gate_counts"]["rotation"])


def test_full_seam_diverges_from_tcost_for_a_negating_table_under_the_new_negation():
    """The other half of the seam test: KMY's table negates, `tcost` prices that negation the old way, and the full run (default
    `gidney_circuit`) prices it as complement + the one-clean-ancilla increment with Lean's counts (Toffoli 12w-28 even / 10w-18 odd, 4 T
    each): the two disagree on T by exactly the negations' price difference, recomputed from the call graph's negation sites, and
    agree on the rotation count. (Same test as the export branch's, E1 7e48afc.)"""
    from site_engine import fullalg as fa
    from l2q_full import cost_conventions as cc
    from l2q_full.bloqs.policy_phase_product import PolicyPhaseProduct
    from negation_arith import delta_t, sites_of, t_legacy, t_new
    P = _opt("kmy", PP, widths="exact").with_widths("exact")
    LP = fa.to_l2q_full(P)
    s = ts.score_policy(P, 300, 0.01)
    base = cc.QUALTRAN.with_(leaf="product", leaf_options=("product",), negsrc="subtract")
    old = cc.split(PolicyPhaseProduct(LP, 300, 300, convention=base.with_(negate="legacy")))
    assert base.negate == "gidney_circuit"                                    # the default is the real circuit
    node = PolicyPhaseProduct(LP, 300, 300, convention=base)
    new = cc.split(node)
    assert (old.base, old.rot) == (s["t_breakdown"]["t_from_toffoli_class"], s["gate_counts"]["rotation"])
    assert new.rot == old.rot
    sites = sites_of(node)
    assert sites
    assert new.base - old.base == delta_t(sites, t_legacy, t_new)
    assert new.base > old.base


def test_forshor_exact_width_is_sto_tight_width_or_the_floor():
    """Stage 18, the seam finding: ForShor's exact width (drop-in Compiler/ExactWidth.lean, square operands) is
    max(tightWidth, 1 + widest initial chunk), STO's exact width (Tight.lean) is tightWidth alone. Over every PP table
    of the library and KMY, for w in [1, 600], they differ only at w = 1 and w = 2, by the floor's one bit."""
    import forshor_exact_width as few
    from site_engine import fullalg as fa
    tabs = [T for T in lib.sto_tables() if T.mode == PP] + [r["table"] for r in lib.kmy_tables() if r["table"].mode == PP]
    diff = set()
    for T in tabs:
        L = fa.to_l2q_full(tt.Policy.constant(T)).bands[0].table
        for w in range(1, 601):
            fs, st = L.next_width(w, w), T.exact_width(w)
            assert fs == max(st, few.floor_width(T.k, w, w)), (T.digest, w)
            if fs != st:
                diff.add((T.digest[:10], w, st, fs))
    assert diff == {("af60631769", 1, 1, 2), ("688e65e0f7", 1, 1, 2), ("688e65e0f7", 2, 2, 3)}


# ------------------------------------------------------------------ the paper's order-finding model (Stage 2b)
def _std_tables():
    from site_engine import ENGINE
    out = []
    for k in range(2, 8):
        d = json.loads((ENGINE / "paper_model" / "artifacts" / "lean" / f"k{k}.forshor.json").read_text())
        out.append({"id": f"std-k{k}", "k": k, "ops": d["schedule"]["ops"]})
    return out


def test_paper_model_reproduces_its_stack_and_stored_points():
    """Tables entered as ops price exactly as the Lean bundles do; k2-4 and k = 4 equal the paper stack's stored
    n = 2048 zoom points (artifacts/figure_data_tcount/SHOR_policy_vs_k4_R2model.json)."""
    from site_engine import paper_full as pf
    std = _std_tables()
    r2 = {"widths": "r2"}      # the paper stack's own rule R2: these are its stored points
    jobs = [pf.bundles_job(2048, 0.01, "k2-7", widths="r2"), {"kind": "catalog", "tables": std, "n": 2048, "delta": 0.01, **r2},
            pf.bundles_job(2048, 0.01, "k2-4", widths="r2"), {"kind": "catalog", "tables": std[:3], "n": 2048, "delta": 0.01, **r2},
            {"kind": "k4", "n": 2048, "delta": 0.01, **r2}, pf.bundles_job(1024, 0.01, "k2-7", widths="r2"),
            {"kind": "catalog", "tables": std, "n": 1024, "delta": 0.01, **r2}]
    r = pf.run(jobs)
    for a, b in ((0, 1), (2, 3), (5, 6)):
        assert (r[a]["T"], r[a]["R"], r[a]["qubits"], r[a]["cnot"]) == (r[b]["T"], r[b]["R"], r[b]["qubits"], r[b]["cnot"])
    assert r[2]["T"] == pytest.approx(2445975466905.204, rel=1e-12)
    assert r[4]["T"] == pytest.approx(2611492581191.725, rel=1e-12)
    assert r[0]["T"] == pytest.approx(2309763671532.619, rel=1e-12)   # the paper's catalog, shipped bundles k = 2..7


# ------------------------------------------------------------------ Stage 11: one full-run pricer (audit resolution)
# Audit figures (Claude outputs/forshor_model_audit.md, 3.1), priced at work width 2342 (L2Q's fraction rule); the site now
# uses Lean's 2347 (m2048), which moves them by well under 0.5 %.
AUDIT = {"reference": (4.90e12, 2.59e13), "shipped": (6.75e14, 2.60e15)}


def _ref():
    return lib.sto_reference_policies()[PP]


def _full_cache():
    from site_engine import full_frontier as ff
    return ff.load(0.01) or ff.compute(0.01)


def test_full_cache_board_and_reference():
    from site_engine import fullalg as fa
    c = _full_cache()
    rk = c["leaderboard"]["ranked"]
    assert len(rk) == 17 and all(a["T"] <= b["T"] for a, b in zip(rk, rk[1:]))
    h = {r["key"]: r for r in c["hero"]["milestones"]}
    # the board's reference is STO's policy with its thresholds moved to exact widths (Stage 19)
    assert h["reference"]["T"] == pytest.approx(fa.price_full(lib.reference_policies()[PP], N, 0.01)["T"], rel=1e-12)
    assert h["dp"]["T"] <= h["all_sto"]["T"] and c["tier"] == fa.TIER and "product" in fa.TIER
    assert "paper" not in c["leaderboard"]["rule"] and "digital-product leaf" in c["leaderboard"]["rule"]
    # the paper's printed figure, kept only as a labelled reproduction (R2, version C, no Step 4)
    assert c["paper"]["r2_T_2048"] == pytest.approx(2309763671532.619, rel=1e-12) and "not comparable" in c["paper"]["note"]


def test_full_step4_is_in_the_run():
    """Step 4 (cmpLtNW, Steps.lean:50): per modular multiplication two phase products on (work+1) x (scratch+1) and four
    QFTs on the scratch register; it is the largest part of the reference run.
    Stage 18 (rewritten): on the drop-in IR the Step 4 product is a `balanced_phase_product` CALL on (work+1) x
    (scratch+1); the balanced split (L = 4399 > 3s/2, s = 2348) makes it B = 2 products at (work+1) x (work+1). The run's
    parts are template calls, so Step 4 keeps its rows. The unbalanced census is kept against the scan cross-check."""
    from site_engine import fullalg as fa
    mults = None
    for rule in ("scan", None):
        cen = fa.census(N, rule=rule)
        w = cen["widths"]
        mults = w["xW"]                                 # one controlled modular multiplication per exponent bit
        calls = {(c["site"], c["xw"], c["zw"]): c for c in cen["calls"]}
        sites = {(s["site"], s["xw"], s["zw"]): s["count"] for s in cen["top_sites"]}
        assert sites[("qft", w["scratchW"], 0)] == calls[("qft", w["scratchW"], 0)]["count"] == 4 * mults
        if rule == "scan":
            assert cen["width_rule"] == "scan"
            assert sites[("phase_product", w["workW"] + 1, w["scratchW"] + 1)] == 2 * mults
        else:
            assert cen["width_rule"] == "exact"
            c4 = calls[("balanced_phase_product", w["workW"] + 1, w["scratchW"] + 1)]
            assert c4["count"] == 2 * mults and c4["part"].startswith("Step 4")
            assert c4["expands_to"] == [{"site": "phase_product", "xw": w["workW"] + 1, "zw": w["workW"] + 1, "count": 2}]
            assert ("phase_product", w["workW"] + 1, w["scratchW"] + 1) not in sites
            # Steps 1, 2, 5 (2L <= 3s) pad to (work+1) x (work+1): Step 2 + 2 x Step 4 phase products, Steps 1 + 5 cphase
            assert sites[("phase_product", w["workW"] + 1, w["workW"] + 1)] == mults + 4 * mults
            assert sites[("cphase_product", w["workW"] + 1, w["workW"] + 1)] == 2 * mults
    # Swap 002: the 68.9 % below was pinned under the pre-swap negation (T = 1,933,981,965,830.921). The default `oneQubit` negation adds
    # exactly the run's negation sites' difference (24,570 at w = 4398, all in "the template's own arithmetic", none in Step 4), so Step 4's T
    # is unchanged and its share is that same T over the larger total: recomputed here, not pasted.
    from negation_arith import RUN_NEGATIONS, RUN_WIDTH, delta_t, policy_sites, t_legacy, t_new
    T_LEGACY, SHARE_LEGACY = 1_933_981_965_830.921, 0.6891812047842106
    sites = policy_sites(fa.to_l2q_full(_ref()), N)
    assert sites == {RUN_WIDTH: RUN_NEGATIONS}
    share_now = SHARE_LEGACY * T_LEGACY / (T_LEGACY + delta_t(sites, t_legacy, t_new))
    r0 = fa.price_full(_ref(), N, 0.01, negation="scratch")                  # the pre-swap pricing, still reproduced exactly
    assert sum(p["T"] for k, p in {p["part"]: p for p in r0["parts"]}.items() if k.startswith("Step 4")) / r0["T"] \
        == pytest.approx(SHARE_LEGACY, rel=1e-9)
    for rule, share in (("scan", None), (None, share_now)):
        r = fa.price_full(_ref(), N, 0.01, rule=rule)
        parts = {p["part"]: p for p in r["parts"]}
        step4 = sum(p["T"] for k, p in parts.items() if k.startswith("Step 4"))
        if share is None:
            assert step4 / r["T"] > 0.7
        else:
            # Stage 18 re-pin: Step 4 is 68.9 % (68.75 % under the default negation) of the reference run on the drop-in IR (was > 70 %
            # at scanned widths); still the largest part by far (its QFT on scratch alone is 54.5 %)
            assert step4 / r["T"] == pytest.approx(share, rel=1e-9)
            assert max(r["parts"], key=lambda p: p["T"])["part"] == "Step 4 cmpLtNW: QFT on scratch"
        assert sum(p["T"] for p in r["parts"]) == pytest.approx(r["T"], rel=1e-12)
        assert sum(u["share"] for u in r["usage"]) == pytest.approx(1.0)


def test_full_uses_the_product_leaf_of_the_phase_product_board():
    from site_engine import fullalg as fa
    from l2q_full import cost_conventions as cc
    from l2q_full.bloqs.policy_phase_product import PolicyPhaseProduct
    assert fa.HEADLINE.leaves == ("product",) and fa.HEADLINE.adder == "qualtran" and fa.HEADLINE.negsrc == "subtract"
    LP = fa.to_l2q_full(_ref())
    # Stage 18: the full run's leaf width is the drop-in's exact descent of the balanced Step 2 (2348 x 2348 -> 38; 36 at
    # scanned widths on the unbalanced 2348 x 2050, the cross-check)
    for rule, want in ((None, 38), ("scan", 36)):
        leaf_w = fa.price_full(_ref(), N, 0.01, rule=rule)["ladder"][-1]["W"]
        assert leaf_w == want
        callees = PolicyPhaseProduct(fa.to_l2q_full(_ref(), rule), leaf_w, leaf_w, convention=fa.HEADLINE).build_call_graph(None)
        assert set(callees) == {cc.ProductPhaseLeaf(leaf_w, leaf_w)}
        # the same leaf as tcost's PhaseProduct board, T without rotations and rotations (see test_full_seam_* too), at
        # the same width rule (Stage 18: exact for the board, scan for the cross-check)
        widths = rule or "exact"
        Q = tt.Policy.constant(_ref().bands[-1].table, widths=widths)
        s = ts.score_policy(Q, leaf_w, 0.01)
        c = cc.split(PolicyPhaseProduct(fa.to_l2q_full(Q), leaf_w, leaf_w, convention=fa.HEADLINE))
        assert (c.base, c.rot) == (s["t_breakdown"]["t_from_toffoli_class"], s["gate_counts"]["rotation"])


def test_full_headline_and_grid_leaf_cross_check():
    """Pins (n = 2048, delta = 0.01) and the audit's figures within the work-width correction. The grid-leaf tier is a
    cross-check (the W x W grid leaf of NaiveLeaf.lean:53, Qualtran adders), never the score."""
    from site_engine import fullalg as fa
    # Stage 18 re-pin: the board prices the ForShor drop-in IR (exact widths, balanced split). STO reference 1.934e12
    # (grid leaf 1.044e13); the shipped policy (the drop-in document's own) 3.241e12 (grid leaf 1.011e13).
    # Swap 002: these pins were recorded under the pre-swap negation (`negate="legacy"`, ForShor `scratch`: Qualtran Negate, 4 (w - 1) T each).
    # The default is now `oneQubit`: every expected T below is the recorded legacy pin plus, over the negation sites of THAT run (counted
    # off its call graph), k * (4 * Toffoli(12w-28 | 10w-18) - 4 (w - 1)); `negation="scratch"` must still reproduce the pins themselves.
    from negation_arith import delta_t, policy_sites, t_legacy, t_new
    up = lambda T_legacy, sites: T_legacy + delta_t(sites, t_legacy, t_new)
    ref_b, ref_s = fa.to_l2q_full(_ref()), fa.to_l2q_full(_ref(), "scan")
    nat_b, nat_s = fa.native().policy, fa.native_scan().policy
    b = fa.price_full(_ref(), N, 0.01)
    bs = fa.price_l2q(nat_b, N, 0.01)
    assert fa.price_full(_ref(), N, 0.01, negation="scratch")["T"] == pytest.approx(1_933_981_965_830.921, rel=1e-9)
    assert fa.price_l2q(nat_b, N, 0.01, negation="scratch")["T"] == pytest.approx(3_240_663_026_770.465, rel=1e-9)
    s_ref, s_nat = policy_sites(ref_b, N), policy_sites(nat_b, N)
    assert b["T"] == pytest.approx(up(1_933_981_965_830.921, s_ref), rel=1e-9)
    assert b["lean_grid_leaf_T"] == pytest.approx(up(10_444_344_163_003.582, s_ref), rel=1e-9)
    assert bs["T"] == pytest.approx(up(3_240_663_026_770.465, s_nat), rel=1e-9)
    assert bs["lean_grid_leaf_T"] == pytest.approx(up(10_114_974_620_580.008, s_nat), rel=1e-9)
    for got in (b, bs):
        # "> 3 x" was the legacy ratio (3.12 for the shipped policy); the cheaper-to-price oneQubit negation lowers the grid tier's T less than
        # the headline's share of negations raises it: 2.89 here, still far above the headline
        assert got["lean_grid_leaf_T"] > 2.5 * got["T"] and got["literal_lean_T"] > got["lean_grid_leaf_T"]
    assert b["cross_checks"]["forshor_scan_ir"]["T"] == pytest.approx(up(4_915_468_013_499.388, policy_sites(ref_b, N, "scan")), rel=1e-9)
    assert bs["cross_checks"]["forshor_scan_ir"]["T"] == pytest.approx(up(675_015_907_318_168.6, policy_sites(nat_b, N, "scan")), rel=1e-9)
    # The Stage 11 pins and the audit's figures: upstream ForShor's IR at scanned widths (the scan cross-check, priced
    # as a headline with rule="scan"), the shipped policy on upstream's own document
    r = fa.price_full(_ref(), N, 0.01, rule="scan")
    s = fa.price_l2q(nat_s, N, 0.01, rule="scan")
    assert r["model"] == s["model"] == fa.MODEL_SCAN and r["width_rule"] == "scan"
    s_rs, s_ns = policy_sites(ref_s, N, "scan"), policy_sites(nat_s, N, "scan")
    assert r["T"] == pytest.approx(up(4_915_468_013_499.388, s_rs), rel=1e-9)
    assert r["lean_grid_leaf_T"] == pytest.approx(up(25_914_479_951_314.496, s_rs), rel=1e-9)
    assert s["T"] == pytest.approx(up(675_015_907_318_168.6, s_ns), rel=1e-9)
    assert s["lean_grid_leaf_T"] == pytest.approx(up(2_600_907_726_231_252.0, s_ns), rel=1e-9)
    # The audit's figures (AUDIT) were computed under ForShor's pre-swap negation charge, i.e. `scratch`: they are checked against that
    # pricing (`negation="scratch"`), not against the default `oneQubit` (+12 % for the shipped policy, whose table negates ~1e8 times).
    r_l = fa.price_full(_ref(), N, 0.01, rule="scan", negation="scratch")
    s_l = fa.price_l2q(nat_s, N, 0.01, rule="scan", negation="scratch")
    for got, leg, (head, grid) in ((r, r_l, AUDIT["reference"]), (s, s_l, AUDIT["shipped"])):
        assert leg["T"] == pytest.approx(head, rel=5e-3) and leg["lean_grid_leaf_T"] == pytest.approx(grid, rel=5e-3)
        assert got["lean_grid_leaf_T"] > 3 * got["T"] and got["cross_checks"]["lean_grid_leaf"]["T"] == got["lean_grid_leaf_T"]
        assert got["literal_lean_T"] > got["lean_grid_leaf_T"]
    # rotations: sto_currency (Ross-Selinger mean), never 11 T
    import sto_currency as sc
    for got in (r, b):
        assert got["T_per_rotation"] == pytest.approx(sc.ts_per_rotation(0.01, got["rotations"]), rel=1e-15)


def test_full_work_width_is_leans_and_the_residual_is_small():
    """At n = 2048 the precision is ForShor's m2048 = 2^150 - 3 (Reference2048Headline.lean:80): work = 2347 bits. L2Q's
    fraction rule gives 2342; the five bits leave the static qubit count unchanged and move T by under 0.2 % at scanned
    widths (1.1 % on the Stage 18 drop-in IR)."""
    import attrs
    from site_engine import fullalg as fa
    from l2q_full import policy_shor_ir as psi, policy_shor_price as pr, policy_shor_sym as sy
    assert fa.precision_m(N) == 2 ** 150 - 3 and fa.price_full(_ref(), N, 0.01)["work_width"] == 2347
    m_frac = psi.min_precision_m(1 << (N - 1))
    assert N + psi.algorithm1_extra_bits(m_frac) == 2342
    # Stage 18: on the drop-in IR the five bits move T by 1.10 % (exact widths are not smooth in w: the scratch QFT at
    # 4393 vs 4398 bits descends differently, -2.2 %); at scanned widths (the cross-check) still under 0.2 %. The static
    # qubit count is unchanged under both.
    # Swap 002: the 1.10 % bound was measured under the pre-swap negation (`negate="legacy"`); under the default `oneQubit` every negation is
    # costlier by 4 (Toffoli(w) - (w - 1)) T and the same absolute difference is a slightly smaller share, so: the bounds are asserted for
    # the legacy convention (where they were measured), and the default's value is asserted equal to what the legacy values imply once each
    # run's own negation sites (counted in its call graph) are repriced.
    from negation_arith import delta_t, sites_of, t_legacy, t_new
    for rule, native, lo, hi in ((None, fa.native(), 0.0109, 0.0111), ("scan", fa.native_scan(), 0, 2e-3)):
        LP = fa.to_l2q_full(_ref(), rule)
        conv_frac = sy.Convention.for_modulus(1 << (N - 1), m_frac)
        got = {}
        for neg, impl in (("scratch", "legacy"), (None, "gidney_circuit")):
            hl = fa.HEADLINE.with_(negate=impl)
            c = attrs.evolve(pr.PolicyShorCore.from_native(native, N, conv_frac, hl), policy=LP)
            r = fa.price_full(_ref(), N, 0.01, rule=rule, negation=neg)
            assert pr.layout_qubits(LP, dict(c.widths)) == r["qubits"]
            main = attrs.evolve((fa.core if rule is None else fa.core_scan)(N), policy=LP, convention=fa.HEADLINE)
            got[impl] = (fa._t_of(c, hl, 0.01)["T"], r["T"], sites_of(attrs.evolve(c, convention=fa.HEADLINE)), sites_of(main))
        (frac_l, main_l, s_frac, s_main), (frac_n, main_n, _, _) = got["legacy"], got["gidney_circuit"]
        assert lo < (main_l - frac_l) / main_l < hi                                     # the Stage 18 claim, as measured under the legacy negation
        assert frac_n == pytest.approx(frac_l + delta_t(s_frac, t_legacy, t_new), rel=1e-12)
        assert main_n == pytest.approx(main_l + delta_t(s_main, t_legacy, t_new), rel=1e-12)
        assert 0 <= (main_n - frac_n) / main_n < 1.3 * hi


def _lean_reserve(P, w, widths="scan"):
    """Policy.reserveNeed (PolicyWorkspace.lean), written out independently of l2q_full: at width w the first band with
    threshold <= w picks T; W' = T.child_width(w, widths) (scan: nextSignedWidth; exact: tightWidth, which is the
    drop-in's exact width at every w >= 3); R = sum_i (W' - L_i) + k R(W'), L_i the floor limbs (the top limb takes the
    rest); no table or no shrink -> 0. Square operands (both sides equal)."""
    T = next((b.table for b in P.bands if b.threshold <= w), None)
    if T is None:
        return 0
    W2 = T.child_width(w, widths)
    if not W2 < w:
        return 0
    L = w // T.k
    limbs = [L] * (T.k - 1) + [w - (T.k - 1) * L]
    return sum(max(0, W2 - x) for x in limbs) + T.k * _lean_reserve(P, W2, widths)


def test_qubits_phase_product_lean_static_allocation():
    """D3, one phase product: Q = 2w + 2R(w). Reference: 6,624 at w = 2048 and 6,912 at w = 2107 (the audit's 4.1)."""
    from site_engine import fullalg as fa
    # Stage 18: `pp_lean_static_qubits` follows the policy's width rule: scanned widths when P.widths == "scan" (upstream
    # ForShor's proved layout, the Stage 12 pins below), else the board's rule (the drop-in's exact widths).
    ref_scan = _ref().with_widths("scan")
    for w, want in ((2048, 6624), (2107, 6912)):
        q = fa.pp_lean_static_qubits(ref_scan, w)
        assert q["qubits"] == want and q["reserve_x"] == q["reserve_z"] == _lean_reserve(_ref(), w, "scan")
    for key in ("kmy", "all_sto", "champions"):
        P = _opt(key, PP, widths="scan")
        assert fa.pp_lean_static_qubits(P, N)["reserve_x"] == _lean_reserve(P, N, "scan")
    # Stage 18, exact widths (the drop-in's Policy.reserveNeed): 4,992 at w = 2048, the PP board's own formula value
    q = fa.pp_lean_static_qubits(_ref(), 2048)
    assert q["qubits"] == 4992 and q["reserve_x"] == q["reserve_z"] == _lean_reserve(_ref(), 2048, "exact") == 448
    for key in ("kmy", "all_sto", "champions"):
        P = _opt(key, PP)
        assert fa.pp_lean_static_qubits(P, N)["reserve_x"] == _lean_reserve(P, N, "exact")
    # The audit's 15,048: the retired paper model's register file (12,350, the exponent register counted twice, no Step 4
    # scratch) with the Lean static reserve of its widest phase product (w = 2107) in place of the Stage 10 "2w + 1".
    assert 12_350 + 2 * fa.pp_lean_static_qubits(ref_scan, 2107)["reserve_x"] == 15_048
    from site_engine import policy_view as pv
    assert pv.view(_ref(), N, 0.01)["qubits_lean_static"]["qubits"] == 6624     # Lean static at scanned widths (cross-check)
    assert pv.view(_ref(), N, 0.01)["qubits"] == 4992                            # Stage 13 headline: the formula at exact widths


def test_qubits_full_run_lean_reference_layout():
    """D3, full run: allocateReferenceLayout (ReferenceLayout.lean). Exponent register once; each reserve the maximum
    over the steps that use it (Step 4's scratch and the QFT workspace included); the concrete layout the IR is validated
    on (policy_shor_ir.reference_layout) places the same number of qubits."""
    from site_engine import fullalg as fa
    from l2q_full import policy_shor_ir as psi
    # Stage 18 re-pin: on the drop-in IR (exact widths) the reference layout is 47,474 (was 56,738 at scanned widths,
    # kept below as the scan cross-check); the shipped policy 43,904 (was 909,794); all-STO 41,954 (was 45,986)
    for rule, want, shipped, all_sto in ((None, 47_474, 43_904, 41_954), ("scan", 56_738, 909_794, 45_986)):
        r = fa.price_full(_ref(), N, 0.01, rule=rule)
        lay = r["layout"]
        assert r["qubits"] == lay["total"] == want
        regs = {g["register"]: g for g in lay["registers"]}
        assert regs["exponent x"]["active"] == 2 * N - 1 and regs["exponent x"]["reserve_set_by"] == "final QFT"
        assert regs["work"]["reserve_set_by"] == "Step 4" and regs["scratch"]["reserve_set_by"] == "Step 4"
        assert sum(g["active"] + g["reserve"] for g in lay["registers"]) + 1 == r["qubits"]
        concrete = psi.reference_layout(fa.to_l2q_full(_ref(), rule), 1 << (N - 1), fa.precision_m(N))
        assert concrete.flag + 1 == r["qubits"]
        native = fa.native() if rule is None else fa.native_scan()
        assert fa.price_l2q(native.policy, N, 0.01, rule=rule)["qubits"] == shipped
        assert fa.price_full(_opt("all_sto", PP, widths="scan"), N, 0.01, rule=rule)["qubits"] == all_sto
    assert fa.price_full(_ref(), N, 0.01)["cross_checks"]["forshor_scan_ir"]["qubits"] == 56_738


def test_full_board_qubits_follow_the_policy():
    """The flat 12,350 is gone: every point's qubits are its own policy's Lean layout (allocateReferenceLayout).
    Stage 18 (rewritten): at the drop-in's exact widths two different policies can share a layout total, so "distinct T
    -> distinct qubits" no longer holds on the board; it is kept on the scan cross-check. Board invariant: one qubit
    count per T, each the point's layout total, and the only shared totals are the two pinned below."""
    c = _full_cache()
    pts = c["leaderboard"]["chronological"] + [next(q for q in v["points"] if q["n"] == N) for v in c["milestones"].values()]
    for rule, get_T, get_q in (("exact", lambda p: p["T"], lambda p: p["qubits"]),
                               ("scan", lambda p: p["cross_checks"]["forshor_scan_ir"]["T"],
                                lambda p: p["cross_checks"]["forshor_scan_ir"]["qubits"])):
        by_T = {}
        for p in pts:
            by_T.setdefault(round(get_T(p), 3), set()).add(get_q(p))
        assert all(len(v) == 1 for v in by_T.values())
        qs = [next(iter(v)) for v in by_T.values()]
        assert 12_350 not in qs
        shared = {q: n for q, n in __import__("collections").Counter(qs).items() if n > 1}
        if rule == "exact":
            # Stage 18 (scan-planned policies): two pairs shared a layout total (36,522 and 42,386). Stage 19 (the PP
            # board's recalculated exact-width policies): no two distinct T share one.
            assert shared == {}
        else:
            assert shared == {}                         # scanned widths: every distinct policy its own count (Stage 12)
    for p in c["leaderboard"]["chronological"]:
        assert p["qubits"] == p["layout"]["total"]


def test_full_records_are_priced_live(tmp_path, monkeypatch):
    """A policy record's stored price is ignored: the board re-prices its policy; a band whose digest does not match its
    table is reported, not priced."""
    from site_engine import full_frontier as ff, fullalg as fa, submitted
    root = tmp_path / submitted.KIND
    P = _ref()
    for rid, bad in (("Sgood", False), ("Sbad", True)):
        (root / rid).mkdir(parents=True)
        bands = [{"threshold": b.threshold, "k": b.table.k, "digest": ("0" * 64 if bad else b.table.digest),
                  "table": b.table.to_json()} for b in P.bands]
        (root / rid / "record.json").write_text(json.dumps({"id": rid, "mode": PP, "created_at": "2026-10-27T00:00:00Z",
                                                            "lean_verified": False, "single_run_T": 1.0, "bands": bands}))
    monkeypatch.setattr(submitted, "DATA", tmp_path)
    r = ff.submitted_board(0.01)
    good = next(p for p in r["policies"] if p["id"] == "Sgood")
    assert good["single_run_T"] == pytest.approx(fa.price_full(P, N, 0.01)["T"], rel=1e-12)
    assert good["stored_single_run_T"] == 1.0 and good["priced_live"] and good["rank"] == 1 and r["count"] == 1
    bad = next(p for p in r["policies"] if p["id"] == "Sbad")
    assert bad["single_run_T"] is None and "digest" in bad["error"]


def test_full_run_label_names_every_table_it_uses():
    """A row's label lists every table the run uses (Steps 1, 2, 4, 5 and every QFT twiddle); rows with the same tables
    at the same widths have the same T."""
    c = _full_cache()
    rows = c["leaderboard"]["chronological"]
    for r in rows:
        used = {t["id"] for t in r["tables_used"]}
        assert set(r["label"].split(" + ")) == used
        assert {x["table"] for u in r["usage"] for x in u["ladder"] if x["k"]} == used
        assert sum(u["share"] for u in r["usage"]) == pytest.approx(1.0)
        assert sum(p["T"] for p in r["parts"]) == pytest.approx(r["T"], rel=1e-12)
    by_set = {}
    for r in rows:
        key = tuple(sorted((t["digest"], tuple(t["widths"])) for t in r["tables_used"]))
        by_set.setdefault(key, set()).add(round(r["T"], 3))
    assert all(len(v) == 1 for v in by_set.values())


# ------------------------------------------------------------------ the full run prices the policy, with each table's own widths
WIDTHS_PROBE = (17, 40, 54, 99, 102, 287, 364, 500, 1000, 2048, 2049, 2107, 2348, 4096, 4120, 4399, 8192)


def test_full_model_child_widths_are_each_tables_own_lean_scan():
    """The full-run pricer gives every PhaseProduct table the child width the PhaseProduct engine gives it, at square
    operands. Stage 18 (rewritten): the board's rule is the drop-in's exact width, which equals the PP board's exact
    width (STO tightWidth) at every probe width (they differ only at w <= 2, test_forshor_exact_width_is_sto_tight_width_
    or_the_floor); the old invariant (each table's own Lean scan nextSignedWidth) holds under the scan rule."""
    from site_engine import fullalg as fa
    tabs = [T for T in lib.sto_tables() if T.mode == PP] + [r["table"] for r in lib.kmy_tables() if r["table"].mode == PP]
    for T in tabs:
        L = fa.to_l2q_full(tt.Policy.constant(T)).bands[0].table
        assert L.width_rule == fa.width_rule() == "exact"
        assert [L.next_width(w, w) for w in WIDTHS_PROBE] == [T.exact_width(w) for w in WIDTHS_PROBE], T.digest
        S = fa.to_l2q_full(tt.Policy.constant(T, widths="scan")).bands[0].table
        assert S.width_rule == "scan"
        assert [S.next_width(w, w) for w in WIDTHS_PROBE] == [T.next_width(w) for w in WIDTHS_PROBE], T.digest


def test_full_board_moves_with_the_phase_product_policy():
    """Regression (Stage 10): rows with different PhaseProduct policies have different full-run counts, and rows
    with the same policy the same count; each row's run follows its policy's bands at the main multiplier.
    Stage 18 (rewritten for the board, kept on the scan cross-check): at the drop-in's exact widths a table's cost is
    fixed by k, its adder and negate counts and its exact child widths (shifts are free, a negated source costs one
    Subtract = one Add), so two different tables can price alike. Board invariant: same policy -> same T, and two rows
    have the same T exactly when their runs have the same cost signature (per table used: k, adders, negates, widths).
    Scan cross-check: different policies -> different T, as before."""
    from site_engine import precompute as pc
    c = _full_cache()
    # Stage 19: the full run prices the PhaseProduct board's own (exact-width) policies (Stage 13 to 18: scan-planned)
    pp = {r["id"]: r for r in (pc.load(PP, 0.01) or pc.compute(PP, 0.01))["leaderboard"]["rows"]}
    rows = c["leaderboard"]["chronological"]
    tabs = {T.digest: T for T in lib.sto_tables()}

    def cost_sig(r):
        out = []
        for t in r["tables_used"]:
            ops = [o[0] for o in tabs[t["digest"]]._ops_json()]
            out.append((t["k"], ops.count("addScaled"), ops.count("negate"), tuple(t["widths"])))
        return tuple(sorted(out))

    by_policy, by_scan, by_sig = {}, {}, {}
    for r in rows:
        sig = tuple((x["width"], x["digest"]) for x in pp[r["id"]]["ladder"])
        by_policy.setdefault(sig, set()).add(round(r["T"], 3))
        by_scan.setdefault(sig, set()).add(round(r["cross_checks"]["forshor_scan_ir"]["T"], 3))
        by_sig.setdefault(cost_sig(r), set()).add(round(r["T"], 3))
    assert all(len(v) == 1 for v in by_policy.values()) and all(len(v) == 1 for v in by_scan.values())
    assert len({next(iter(v)) for v in by_scan.values()}) == len(by_scan)
    assert all(len(v) == 1 for v in by_sig.values()) and len({next(iter(v)) for v in by_sig.values()}) == len(by_sig)
    # the one coincidence on the board: 293d51de2c05 and 54f51b9656d0/7ec7497794d4 (k = 6 tables 1da7752d1e85 and
    # 3265bb656f78: 60 adders each, exact widths 404/381/355 at 2348/2200/2049) price alike; at scanned widths they do not
    assert len(by_policy) - len({next(iter(v)) for v in by_policy.values()}) == 1
    for r in rows:
        bands = [x for x in pp[r["id"]]["ladder"] if x["k"]]
        W = r["ladder"][0]["W"]
        top = next(b for b in bands if b["width"] <= W)
        assert r["ladder"][0]["digest"].startswith(top["digest"])


def test_reference_thresholds_are_moved_to_exact_widths():
    """Stage 19 (owner, 2026-10-29): the site's STO reference keeps STO's tables, in order, with each threshold moved to
    the width the exact descent from n = 2048 reaches. STO's own copy (engine/data/reference_policies.json) is unchanged."""
    sto, site = lib.sto_reference_policies(), lib.reference_policies()
    assert [(b.threshold, b.table.k) for b in sto[PP].bands] == [(2048, 6), (385, 3), (140, 2), (73, 2), (40, 2)]
    assert [(b.threshold, b.table.k) for b in site[PP].bands] == [(2048, 6), (364, 3), (125, 2), (64, 2), (34, 2)]
    assert [(b.threshold, b.table.k) for b in site[PTP].bands] == [(2048, 6), (375, 4), (107, 2), (55, 2)]
    for mode in (PP, PTP):
        assert [b.table.digest for b in site[mode].bands] == [b.table.digest for b in sto[mode].bands]
        bands = site[mode].bands
        for a, b in zip(bands, bands[1:]):
            assert b.threshold == a.table.exact_width(a.threshold)
    assert ts.score_policy(site[PP], N, 0.01)["t_count"] == pytest.approx(13_909_446, abs=1)
    assert ts.score_policy(site[PTP], N, 0.01)["t_count"] == pytest.approx(126_798_925, abs=1)
