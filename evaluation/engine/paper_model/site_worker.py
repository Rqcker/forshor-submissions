#!/usr/bin/env python3
"""sto_site worker for the paper's order-finding model (ForShor paper, Section "Translation to Qualtran", Figure TCount).

The model is Lean-To-Qualtran's `scripts/manuscript_policy_shor.py`: `LeanOrderFinding(core="versionB")`, every phase
product and QFT twiddle a `ToomRecursivePhaseProduct` under rule R2 (tight widths, operands zero-extended to a multiple
of k, cost-optimal stop), host bundle k = 4 (QFT split rule, modmul template), the stop-price ladder, the error budget
delta = 0.01 split 0.5, and the per-rotation synthesis cost from sampled angles (`tcount.model.rotation_cost`). This file
adds one thing: the arities the recursion may use can be ANY Toom-Cook tables (STO tables, KMY's, the DP library),
not only the Lean-emitted standard bundles, and a fixed width-banded policy can dictate the choice instead of the
cost-optimal version C. A table enters exactly as a bundle does: q = 2k - 1 children, its op census (addScaled, negate,
negated sources) and its tight width rule (`tight_width.tight_rule` on its own program).

It runs as a subprocess because this tree keeps the package name `forshor_qualtran`, which the site's trimmed fork also
uses. Protocol: one JSON request on stdin, one JSON response on stdout.
    {"jobs": [{"kind": "bundles", "catalog": "k2-7", "n": 2048, "delta": 0.01},
              {"kind": "catalog", "tables": [{"id", "k", "ops"}], "n", "delta"},
              {"kind": "policy", "bands": [{"threshold", "id", "k", "ops"}], "n", "delta"},
              {"kind": "k4", "n", "delta"},
              {"kind": "map", "tables": [...], "decisions": [[W, id or null], ...], "stop_tsr", "n", "delta"}]}
    Any job may set "usage": true to get every phase product in the run with its decisions (`_usage`).
"""
from __future__ import annotations

import copy
import functools
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _p in (ROOT / "scripts", ROOT / "notebooks", ROOT / "py", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("ONECLICK_TCOUNT", str(ROOT / "Clifford_plus_T_scaling"))

from forshor_qualtran import emit  # noqa: E402

emit.emitter_fingerprint = lambda: None          # the shipped bundles only; never probe for a Lean binary

import manuscript_policy_shor as M  # noqa: E402
from forshor_qualtran.recursion_versions import ArityData, RuleSet  # noqa: E402
from forshor_qualtran.tight_width import tight_rule  # noqa: E402
from tcount.budget import RotationBudget  # noqa: E402

HOST_K = M.HOST_K
_STATE = {}

#: CHILD WIDTHS (Stage 10). "scan" (the default): a phase product of width w split by table T has children of width
#: W_T(w) = nextSignedWidth, the Lean width scan of T's own program on floor limbs (ForShor paper, Appendix "charge and
#: width", eq. TPP). This is the width the harness proves, the width a policy's bands are written against, and the width
#: the PhaseProduct board uses. The recursion stops where the policy says (no band, or the table does not shrink the
#: width) or, for a catalog, where a leaf is cheaper. "r2": the paper model's rule R2 (tight reachable-value widths on
#: zero-extended limbs). R2 is not a plan ForShor proves, and it ignores the program, so two tables with the same
#: points and census get the same widths however their shifts and additions are ordered. It is kept only to reproduce
#: the paper's printed figure.
WIDTHS = ("scan", "r2")


def _scan_params(rule: str):
    return ("sym", False, True) if rule == "kmy" else RuleSet._rule_params(rule)


def with_widths(rs, widths: str, fresh: bool = False):
    """`rs` under the requested width rule. A shared RuleSet is copied first, so its memo (keyed by the object) is not
    mixed between the two rules."""
    if widths not in WIDTHS:
        raise ValueError(f"widths must be one of {WIDTHS}, not {widths!r}")
    if widths == "r2":
        return rs
    if not fresh:
        rs = copy.copy(rs)
    rs._rule_params = _scan_params
    return rs


def setup():
    if _STATE:
        return
    M._setup()
    for cat, rules in M._W["rules"].items():
        for k, b in rules.bundles.items():
            if str(b.provenance) != "Provenance.REAL_LEAN" and getattr(b.provenance, "value", "") != "REAL-LEAN":
                raise RuntimeError(f"bundle k={k} is {b.provenance}, not REAL-LEAN (missing artifacts/lean/k{k}.forshor.json?)")
    _STATE["host"] = M._W["rules_all"]


def arity(k: int, ops) -> ArityData:
    """A Toom-Cook table as the R2 model's per-arity data (what `ArityData.from_bundle` reads from a bundle)."""
    rule = tight_rule(ops, int(k))
    cen = {"phaseProduct": 0, "addScaled": 0, "negate": 0, "neg": 0}
    for o in ops:
        if o["op"] in cen:
            cen[o["op"]] += 1
        if o["op"] == "addScaled" and o.get("negSrc"):
            cen["neg"] += 1
    top = max(rule.from_m, 1) + 8
    tab = {m: rule.at_m(m) for m in range(1, top)}
    return ArityData(k=int(k), q=cen["phaseProduct"], n_add=cen["addScaled"], n_neg=cen["negate"],
                     sym_uniform=dict(tab), tight_uniform=tab, sym_general={}, tight_general={},
                     beta_sym=rule.beta, beta_tight=rule.beta, from_sym=rule.from_m, from_tight=rule.from_m,
                     n_add_negsrc=cen["neg"], ops=tuple(tuple(sorted(o.items())) for o in ops))


# Stage 11: the Stage 10 qubit patch (2w + 1 per phase product under the scan rule) is removed. It dropped the extension of
# every limb to the working width W' that ForShor allocates (audit, section 4). The paper model is retired from every
# headline; its qubit counts are the model's own and are not shown. The site's qubits are Lean's static layout
# (site_engine.fullalg.layout).


class TableRuleSet(RuleSet):
    """The host bundles (templates, version B) plus a catalog of tables that version C chooses from; with `policy`
    (``[(threshold, key)]`` widest first) the policy dictates the table at each width instead of the cost minimum."""

    def __init__(self, host: RuleSet, tables, policy=None):
        self.bundles, self.data, self.ks = host.bundles, dict(host.data), list(host.ks)
        self.cat, self.ident = [], {}
        for i, t in enumerate(tables):
            key = 1000 + i
            self.data[key] = arity(t["k"], t["ops"])
            if self.data[key].q != 2 * int(t["k"]) - 1:
                raise ValueError(f"table {t['id']}: {self.data[key].q} phase products, expected 2k - 1 = {2 * int(t['k']) - 1}")
            self.cat.append(key)
            self.ident[key] = (t["id"], int(t["k"]))
        self.policy = policy

    def tree(self, w, version, k_top, rule, ts_per_rotation=None):
        """Rows report the table's arity in "k" (what `qubit_footprint` pads by) and the catalog key in "key"."""
        from forshor_qualtran import constants
        rows = RuleSet.tree(self, w, version, k_top, rule, constants.TS_PER_ROTATION if ts_per_rotation is None else ts_per_rotation)
        for r in rows:
            key = r["k"]
            r["key"] = key
            if key is not None and key in self.ident:
                r["k"] = self.ident[key][1]
        return rows

    def arities_at(self, version, k_top, root):
        return list(self.cat) if version == "C" else RuleSet.arities_at(self, version, k_top, root)

    def _t(self, w, version, k_top, rule, root, tsr):
        if version == "C" and self.policy is not None:
            return self._tp(w, rule, tsr)
        return RuleSet._t(self, w, version, k_top, rule, root, tsr)

    @functools.lru_cache(maxsize=None)
    def _tp(self, w, rule, tsr):
        pol, pad, _ = self._rule_params(rule)
        leaf = self.leaf_T(w, tsr)
        key = next((k for th, k in self.policy if th <= w), None)
        if key is None:
            return leaf, None
        W2 = self.data[key].next_width(w, pol, pad)
        if W2 >= w:
            return leaf, None
        return self.data[key].q * self._tp(W2, rule, tsr)[0] + self.body_T(key, W2), (key, W2)


class MapRuleSet(TableRuleSet):
    """A full-run decision map: every width the run's phase products recurse at, with its table (or the leaf grid).
    This is the exact policy behind a version-C result (`_usage` lists every decision it made), so re-pricing the map
    reproduces that result. A width the map does not cover is an error, never a silent leaf."""

    def __init__(self, host: RuleSet, tables, decisions):
        TableRuleSet.__init__(self, host, tables)
        by_id = {self.ident[key][0]: key for key in self.cat}
        self.map = {}
        for W, tid in decisions:
            if tid is not None and tid not in by_id:
                raise ValueError(f"decision at width {W} names table {tid!r}, which the map does not ship")
            self.map[int(W)] = by_id[tid] if tid is not None else None

    def _t(self, w, version, k_top, rule, root, tsr):
        if version == "C":
            return self._tm(w, rule, tsr)
        return RuleSet._t(self, w, version, k_top, rule, root, tsr)

    @functools.lru_cache(maxsize=None)
    def _tm(self, w, rule, tsr):
        if w not in self.map:
            raise ValueError(f"the decision map has no entry for width {w}")
        pol, pad, _ = self._rule_params(rule)
        leaf = self.leaf_T(w, tsr)
        key = self.map[w]
        if key is None:
            return leaf, None
        W2 = self.data[key].next_width(w, pol, pad)
        if W2 >= w:
            raise ValueError(f"table {self.ident[key][0]} does not shrink width {w}")
        return self.data[key].q * self._tm(W2, rule, tsr)[0] + self.body_T(key, W2), (key, W2)


def _cnots(bloq) -> int:
    """CNOT leaves of the call graph, 6 per And (Qualtran's And Clifford+T word), as the site's tcost counts them."""
    import attrs
    from qualtran import Bloq
    from qualtran.bloqs.basic_gates import CNOT
    from qualtran.bloqs.mcmt import And
    from qualtran.resource_counting import CostKey, get_bloq_callee_counts, get_cost_value

    @attrs.frozen
    class CNOTCount(CostKey[int]):
        def compute(self, b: Bloq, get_callee_cost) -> int:
            if isinstance(b, CNOT):
                return 1
            if isinstance(b, And):
                return 0 if b.uncompute else 6
            return sum(n * get_callee_cost(c) for c, n in get_bloq_callee_counts(b))

        def zero(self) -> int:
            return 0

    return int(get_cost_value(bloq, CNOTCount()))


def _usage(of, rules, tsr):
    """Every recursive phase product in the run (multiplier cores, controlled phase products, QFT twiddles): its root
    width, how many times the run calls it, and its descent with the table chosen at every level. The T it contributes
    is count x (bodies + leaf grids) from `RuleSet.tree`, priced at the stop price; the per-rotation correction is
    applied to the whole run separately, so these shares are the model's T11 shares."""
    import attrs
    from qualtran.resource_counting import CostKey, get_bloq_callee_counts, get_cost_value
    from forshor_qualtran.recursion_versions import ToomRecursivePhaseProduct

    @attrs.frozen
    class RootPP(CostKey[dict]):
        def compute(self, b, get_callee_cost):
            if isinstance(b, ToomRecursivePhaseProduct):
                return {(b.w, b.version, b.k_top, b.rule, b.tsr): 1} if b.root else {}
            out = {}
            for c, n in get_bloq_callee_counts(b):
                for k2, v in get_callee_cost(c).items():
                    out[k2] = out.get(k2, 0) + n * v
            return out

        def zero(self):
            return {}

    roots = get_cost_value(of, RootPP())
    ident = getattr(rules, "ident", {})
    out = []
    for (w, version, k_top, rule, t), cnt in sorted(roots.items(), key=lambda kv: (-kv[0][0], kv[0][1])):
        rows = rules.tree(w, version, k_top, rule, t)
        lad = []
        for row in rows:
            key = row.get("key", row["k"])
            tid, k = ident.get(key, (f"std-k{key}" if key else None, key))
            lad.append({"W": row["W"], "k": k, "table": tid if key else "leaf"})
        per = sum(r["body_T"] for r in rows) + rows[-1]["leaf_T"]
        out.append({"W": int(w), "version": version, "count": int(cnt), "ladder": lad, "T_each": per,
                    "T_total": per * int(cnt)})
    return out


def _price(rules, n: int, delta: float, version: str, tsr_ladder, with_cnot: bool, with_usage: bool = False):
    from forshor_qualtran.bloqs.orderfinding import LeanOrderFinding
    H = M._W["H"]
    PP = H._precision_policy()
    budget = RotationBudget(delta=delta, rotation_fraction=0.5)
    d_struct = budget.delta_struct if budget.delta_struct > 0 else budget.delta
    delta_eff = PP.composed_delta(n, policy=M._W["precision"], delta=d_struct)
    from tcount.model import correct_t, rotation_cost
    best = None
    for tsr in tsr_ladder:
        of = LeanOrderFinding.from_bundle(rules.bundles[HOST_K], n, delta=delta_eff, N=(1 << n) - 1, policy="tight",
                                          core="versionB", qft="lean", rules=rules, stop_tsr=tsr, pp_version=version)
        t11, r = H.price(of, n, "t"), H.price(of, n, "rotation")
        if not (t11 == t11 and t11 > 0 and r == r and r > 0):
            continue
        try:
            rc = rotation_cost(HOST_K, n, r, budget, sampled=M._W["sampled"], rows=M._W["rows"])
            t = correct_t(t11, r, rc.t_per_rotation, M.RSFIG.PLACEHOLDER)
        except ValueError:
            continue
        if best is None or t < best["T"]:
            mm = of.modexp().modmul()
            w = max(mm.n, mm.w_work) + 1
            lad = []
            for row in rules.tree(w, version, HOST_K, "kmy", tsr):
                key = row.get("key", row["k"])
                tid, k = getattr(rules, "ident", {}).get(key, (f"std-k{key}" if key else None, key))
                lad.append({"W": row["W"], "k": k, "table": tid if key else "leaf"})
            best = {"T": t, "T11": t11, "R": r, "t_per_rotation": rc.t_per_rotation, "stop_tsr": tsr, "of": of,
                    "ladder": lad}
    if best is None:
        raise ValueError(f"no admissible tree at n = {n}")
    of = best.pop("of")
    best["toffoli"] = H.price(of, n, "toffoli")
    best["clifford"] = H.price(of, n, "clifford")
    best["qubits"] = H.price(of, n, "qubits")
    if with_cnot:
        best["cnot"] = _cnots(of)
    if with_usage:
        best["usage"] = _usage(of, rules, best["stop_tsr"])
    best.update({"n": n, "delta": delta})
    return best


def run(job):
    setup()
    n, delta = int(job.get("n", 0)), float(job.get("delta", 0.01))
    ladder = list(M.RSFIG.STOP_LADDER)
    cnot = bool(job.get("cnot", True))
    kind = job["kind"]
    wr = job.get("widths", "scan")
    if kind == "bundles":
        return _price(with_widths(M._W["rules"][job["catalog"]], wr), n, delta, "C", ladder, cnot, bool(job.get("usage")))
    if kind == "k4":
        return _price(with_widths(_STATE["host"], wr), n, delta, "B", ladder, cnot)
    if kind == "catalog":
        return _price(with_widths(TableRuleSet(_STATE["host"], job["tables"]), wr, fresh=True), n, delta, "C", ladder, cnot,
                      bool(job.get("usage")))
    if kind == "policy":
        bands = job["bands"]
        rs = TableRuleSet(_STATE["host"], bands, policy=[(int(b["threshold"]), 1000 + i) for i, b in enumerate(bands)])
        return _price(with_widths(rs, wr, fresh=True), n, delta, "C", ladder[:1], cnot, bool(job.get("usage")))   # a fixed policy: the stop price does not choose
    if kind == "map":
        rs = MapRuleSet(_STATE["host"], job["tables"], [(d[0], d[1]) for d in job["decisions"]])
        return _price(with_widths(rs, wr, fresh=True), n, delta, "C", [float(job["stop_tsr"])], cnot, bool(job.get("usage")))
    if kind == "widths":
        # the child width the model gives each table at each width (a check against the PhaseProduct engine)
        rs = with_widths(TableRuleSet(_STATE["host"], job["tables"]), wr, fresh=True)
        pol, pad, _ = rs._rule_params("kmy")
        return {"widths": {rs.ident[key][0]: [rs.data[key].next_width(int(w), pol, pad) for w in job["ws"]] for key in rs.cat}}
    raise ValueError(f"unknown job kind {kind!r}")


def main() -> int:
    req = json.loads(sys.stdin.read())
    out = []
    for job in req["jobs"]:
        try:
            out.append({"ok": True, **run(job)})
        except Exception as e:  # noqa: BLE001
            out.append({"ok": False, "error": f"{type(e).__name__}: {e}"})
    json.dump({"results": out}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
