"""Closed-form widths from the emitted E3 tables. Here `n` gets its first *name*, and it is still symbolic.

    m(n, k) = ⌈n/k⌉                                (limbM, Emit/GeneralEmitter.lean:140)
    W_policy(n, k) = m + β_policy(k)   for m ≥ m₀   (affine tail emitted by E3; table below m₀)

Policies: ``"sym"`` (DefsCore, the fidelity-theorem width) and ``"tight"`` (LowGateIR exact
reachable-value width, the compiled/counted width). The module keeps the two. The figures show the gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union

import sympy

Policy = Literal["sym", "tight"]
POLICIES: tuple[Policy, ...] = ("sym", "tight")

#: THE symbol. All symbolic bloqs and cost expressions in the package use this one object.
n = sympy.Symbol("n", positive=True, integer=True)


@dataclass(frozen=True)
class WidthRule:
    k: int
    policy: Policy
    beta: int
    from_m: int
    table: Dict[int, int]     # m -> W for m < from_m (exact, from the emitted table)

    def at_m(self, m: int) -> int:
        if m >= self.from_m:
            return m + self.beta
        if m in self.table:
            return self.table[m]
        raise ValueError(f"W_{self.policy}(k={self.k}) has no entry for m={m} < {self.from_m}")

    def at_n(self, n_val: int) -> int:
        return self.at_m(-(-n_val // self.k))

    def expr(self, symbol: sympy.Symbol = n, exact_ceiling: bool = True) -> sympy.Expr:
        """Return W as a SymPy expression in n (valid for n ≥ k·from_m)."""
        m = sympy.ceiling(symbol / self.k) if exact_ceiling else symbol / self.k
        return m + self.beta

    @property
    def valid_from_n(self) -> int:
        return self.k * self.from_m


def _as_bundle_and_section(source: Dict[str, Any]) -> tuple:
    """Accept a full bundle or a bare `width` section. Return ``(bundle, section)``.

    Before, call sites gave `doc["width"]`. That worked because the v1 width section had
    `k` and the tables of the two policies. The v2 section has neither: `k` moved to the bundle
    root, and the drop does not emit `tight`. Thus the bundle is now the useful argument. The
    function accepts the two spellings. Thus no call site must change to keep `sym` working.
    """
    # A `Bundle` OBJECT, not its `.doc`. `Bundle.__getitem__` sends the request to `doc[section]`.
    # Thus `"width" in bundle` uses iteration and raises `KeyError: 0`. That is how we found this
    # problem (notebook 04). Callers can correctly hold a Bundle. Thus unwrap it here, so that
    # the call sites do not have to do it.
    doc = getattr(source, "doc", None)
    if isinstance(doc, dict):
        source = doc
    if isinstance(source, dict) and isinstance(source.get("width"), dict):
        return source, source["width"]
    return None, source


def _sym_rule(k: int, section: Dict[str, Any]) -> WidthRule:
    """Return the symbolic (DefsCore `nextWidth`) policy, from the documents of each generation.

    v1: ``affine_sym {affine, beta, from_m}`` + ``uniform [{m, W_sym, W_tight}]``.
    v2: ``affine_tail {beta, m0}``            + ``by_m    [{m, width, limb_width}]``.
    """
    if "by_m" in section:                                           # v2
        table = {int(e["m"]): int(e["width"]) for e in section["by_m"]}
        aff = section.get("affine_tail") or {}
        if not aff:
            return WidthRule(k, "sym", beta=0, from_m=10 ** 9, table=table)
        return WidthRule(k, "sym", beta=int(aff["beta"]), from_m=int(aff["m0"]), table=table)
    aff = section["affine_sym"]                                     # v1
    table = {int(e["m"]): int(e["W_sym"]) for e in section["uniform"]}
    if not aff.get("affine"):
        return WidthRule(k, "sym", beta=0, from_m=10 ** 9, table=table)
    return WidthRule(k, "sym", beta=int(aff["beta"]), from_m=int(aff["from_m"]), table=table)


def _tight_rule(k: int, section: Dict[str, Any], bundle: Optional[Dict[str, Any]]) -> WidthRule:
    """Return the exact reachable-value policy.

    v1 emitted it. v2 does not ("the older tooling's exact reachable-value (\"tight\") width
    scan is not carried over" -- Emit/Symbolic/Width.lean). For a v2 bundle,
    `forshor_qualtran.tight_width` builds it again from the emitted schedule. That module copies
    the `reachableFits` interval arithmetic of Lean, and it gives exactly the v1 tables at k = 2..7.
    """
    if "affine_tight" in section:                                   # v1: use the emitted values
        aff = section["affine_tight"]
        table = {int(e["m"]): int(e["W_tight"]) for e in section["uniform"]}
        if not aff.get("affine"):
            return WidthRule(k, "tight", beta=0, from_m=10 ** 9, table=table)
        return WidthRule(k, "tight", beta=int(aff["beta"]), from_m=int(aff["from_m"]), table=table)
    if bundle is None or "schedule" not in bundle:
        raise ValueError(
            "the tight (exact reachable-value) width is not emitted by the 2026-09-18 drop and "
            "is reconstructed from the schedule, so width_rule needs the WHOLE BUNDLE for "
            "policy='tight', not just its `width` section. Pass `doc`, not `doc['width']`.")
    from .tight_width import tight_rule_from_bundle
    # Closed form, not a scan. The coefficient rows do not depend on m. Thus `beta` and `from_m`
    # are DERIVED (with a proved threshold). They do not come from a scanned window. The
    # `table` below the tail is a small number of exact closed-form evaluations, not a search.
    rule = tight_rule_from_bundle(bundle)
    table = {m: rule.at_m(m) for m in range(1, rule.from_m)}
    return WidthRule(k, "tight", beta=rule.beta, from_m=rule.from_m, table=table)


def width_rule(source: Dict[str, Any], policy: Policy) -> WidthRule:
    """Return the closed-form width rule for `policy`, from a bundle (preferred) or a width section.

    For a v2 document, `policy="tight"` needs the full bundle, because the reconstruction
    reads the schedule. `policy="sym"` works with each of the two.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown width policy {policy!r}; expected one of {POLICIES}")
    bundle, section = _as_bundle_and_section(source)
    k = section.get("k") if isinstance(section, dict) else None
    if k is None and bundle is not None:
        k = bundle.get("k")
    if k is None:
        raise ValueError(
            "width_rule cannot determine k: the v2 width section does not carry it (it moved to "
            "the bundle root). Pass the whole bundle rather than `doc['width']`.")
    k = int(k)
    return _sym_rule(k, section) if policy == "sym" else _tight_rule(k, section, bundle)


def width_gap(source: Dict[str, Any]) -> Dict[str, Any]:
    """Return the number of qubits that the symbolic policy allocates more than the exact reachable width.

    The figures report this quantity, and it is the reason that the module keeps the two policies.
    ForShor notes that the symbolic scan is loose ("54 wasted qubits at k=3"). It also notes that
    a smaller width "would put the emission outside the proved fidelity theorem". Thus the waste is
    on purpose. But it must be visible. It must not be in a qubit count without a statement.
    """
    sym, tight = width_rule(source, "sym"), width_rule(source, "tight")
    return {"k": sym.k, "sym_beta": sym.beta, "tight_beta": tight.beta,
            "beta_gap": sym.beta - tight.beta,
            "sym_from_m": sym.from_m, "tight_from_m": tight.from_m}


def m_of_n(n_val: Union[int, sympy.Expr], k: int):
    if isinstance(n_val, int):
        return -(-n_val // k)
    return sympy.ceiling(n_val / k)


def general_widths(source: Dict[str, Any], policy: Policy) -> Dict[int, int]:
    """Return `w -> W_policy(w)` over the general (non-uniform-limb) width ladder.

    v1 emitted ``general [{w, limb, W_sym, W_tight}]``. v2 emits
    ``by_w [{w, next_width, reserve_x, reserve_z}]`` and no tight column. Thus the function builds
    the tight values again for each `w` from the schedule (memoized, because the ladder goes to w = 96).
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown width policy {policy!r}; expected one of {POLICIES}")
    bundle, section = _as_bundle_and_section(source)
    if "general" in section:                                        # v1: the two columns are emitted
        key = "W_sym" if policy == "sym" else "W_tight"
        return {int(e["w"]): int(e[key]) for e in section["general"]}
    rows = section.get("by_w")
    if rows is None:
        raise ValueError("width section has neither `general` (v1) nor `by_w` (v2)")
    sym = {int(e["w"]): int(e["next_width"]) for e in rows}
    if policy == "sym":
        return sym
    if bundle is None or "schedule" not in bundle:
        raise ValueError(
            "the tight width ladder is reconstructed from the schedule, so general_widths "
            "needs the WHOLE BUNDLE for policy='tight'. Pass `doc`, not `doc['width']`.")
    from .tight_width import tight_width_closed_form
    k = int(bundle["k"])
    ops = bundle["schedule"]["ops"]
    return {w: tight_width_closed_form(ops, k, w) for w in sym}


def uniform_rows(source: Dict[str, Any]) -> List[Dict[str, int]]:
    """Return `[{k, m, n, W_sym, W_tight, gap}]` over the uniform-limb ladder, from each of the two shapes.

    The width figures use this table. The `gap` column is the purpose of the table.
    """
    bundle, section = _as_bundle_and_section(source)
    k = int(section.get("k") or (bundle or {}).get("k"))
    if "uniform" in section:                                        # v1
        return [{"k": k, "m": int(e["m"]), "n": int(e.get("n", k * int(e["m"]))),
                 "W_sym": int(e["W_sym"]), "W_tight": int(e["W_tight"]),
                 "gap": int(e["W_sym"]) - int(e["W_tight"])}
                for e in section["uniform"]]
    sym_rule = width_rule(source, "sym")
    tight_rule = width_rule(source, "tight")
    return [{"k": k, "m": m, "n": k * m,
             "W_sym": sym_rule.at_m(m), "W_tight": tight_rule.at_m(m),
             "gap": sym_rule.at_m(m) - tight_rule.at_m(m)}
            for m in sorted(sym_rule.table)]
