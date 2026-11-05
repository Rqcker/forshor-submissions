"""Closed-form widths from the emitted E3 tables — the place `n` is first *named*, still symbolic.

    m(n, k) = ⌈n/k⌉                                (limbM, Emit/GeneralEmitter.lean:140)
    W_policy(n, k) = m + β_policy(k)   for m ≥ m₀   (affine tail emitted by E3; table below m₀)

Policies: ``"sym"`` (DefsCore, the fidelity-theorem width) and ``"tight"`` (LowGateIR exact
reachable-value width, the compiled/counted width). Both are carried; figures show the gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union

import sympy

Policy = Literal["sym", "tight"]
POLICIES: tuple[Policy, ...] = ("sym", "tight")

#: THE symbol. Every symbolic bloq and cost expression in the package uses this one object.
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
        """W as a SymPy expression in n (valid for n ≥ k·from_m)."""
        m = sympy.ceiling(symbol / self.k) if exact_ceiling else symbol / self.k
        return m + self.beta

    @property
    def valid_from_n(self) -> int:
        return self.k * self.from_m


def _as_bundle_and_section(source: Dict[str, Any]) -> tuple:
    """Accept either a whole bundle or a bare `width` section; return ``(bundle, section)``.

    Call sites historically passed `doc["width"]`. That worked because the v1 width section
    carried `k` and both policies' tables. The v2 section carries neither -- `k` moved to the
    bundle root and `tight` is not emitted at all -- so the bundle is now the useful argument.
    Both spellings are accepted so no call site is forced to change to keep `sym` working.
    """
    # A `Bundle` OBJECT, not its `.doc`. `Bundle.__getitem__` forwards to `doc[section]`, so
    # `"width" in bundle` falls back to iteration and raises `KeyError: 0` -- which is how this
    # surfaced (notebook 04). Callers legitimately hold a Bundle, so unwrap it here rather than
    # making every call site remember to.
    doc = getattr(source, "doc", None)
    if isinstance(doc, dict):
        source = doc
    if isinstance(source, dict) and isinstance(source.get("width"), dict):
        return source, source["width"]
    return None, source


def sym_tables(k: int, section: Dict[str, Any]):
    """`(by_m, by_w, affine)` of the symbolic policy from a 2026-09-18 (`by_m`/`by_w`/`affine_tail`)
    or a 2026-10-14 ForShor-main (`rows` only) width section.

    ForShor-main's emitter keeps one table, `rows [{w, next_width, reserve_x, reserve_z}]` (the old
    `by_w`), and drops `by_m` and `affine_tail`. Both are exact functions of it: the uniform ladder is
    `by_m[m] = next_width(w = k*m)` (identical to the 09-18 `by_m` at k = 2..4, checked against the
    shipped bundles), and the tail is `Shor.affineTail` over that table. They are derived here, not
    read from Lean, so a consumer cannot mistake them for emitted fields. `affine` is `{beta, m0}` or
    None.
    """
    rows = section.get("by_w", section.get("rows"))
    by_w = {int(e["w"]): int(e["next_width"]) for e in rows} if rows is not None else {}
    if "by_m" in section:
        by_m = {int(e["m"]): int(e["width"]) for e in section["by_m"]}
    else:
        by_m = {w // k: nw for w, nw in sorted(by_w.items()) if w % k == 0 and w // k >= 1}
    aff = section.get("affine_tail")
    if aff is None and "by_m" not in section:
        from .tight_width import affine_tail
        m0, beta = affine_tail(sorted(by_m.items()))
        aff = {"beta": beta, "m0": m0} if beta is not None else None
    return by_m, by_w, (aff or None)


def _sym_rule(k: int, section: Dict[str, Any]) -> WidthRule:
    """The symbolic (DefsCore `nextWidth`) policy, from either document generation.

    v1: ``affine_sym {affine, beta, from_m}`` + ``uniform [{m, W_sym, W_tight}]``.
    v2: ``affine_tail {beta, m0}``            + ``by_m    [{m, width, limb_width}]``.
    """
    if "by_m" in section or "rows" in section:                      # v2 (09-18) or ForShor-main rows
        table, _, aff = sym_tables(k, section)
        aff = aff or {}
        if not aff:
            return WidthRule(k, "sym", beta=0, from_m=10 ** 9, table=table)
        return WidthRule(k, "sym", beta=int(aff["beta"]), from_m=int(aff["m0"]), table=table)
    aff = section["affine_sym"]                                     # v1
    table = {int(e["m"]): int(e["W_sym"]) for e in section["uniform"]}
    if not aff.get("affine"):
        return WidthRule(k, "sym", beta=0, from_m=10 ** 9, table=table)
    return WidthRule(k, "sym", beta=int(aff["beta"]), from_m=int(aff["from_m"]), table=table)


def _tight_rule(k: int, section: Dict[str, Any], bundle: Optional[Dict[str, Any]]) -> WidthRule:
    """The exact reachable-value policy.

    v1 emitted it; v2 does not ("the older tooling's exact reachable-value (\"tight\") width
    scan is not carried over" -- Emit/Symbolic/Width.lean). For a v2 bundle it is reconstructed
    from the emitted schedule by `forshor_qualtran.tight_width`, which re-folds Lean's
    `reachableFits` interval arithmetic and reproduces v1's own tables exactly at k = 2..7.
    """
    if "affine_tight" in section:                                   # v1: use what was emitted
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
    # Closed form, not a scan: the coefficient rows do not depend on m, so `beta` and `from_m`
    # are DERIVED (with a proved threshold) rather than detected over a scanned window. The
    # `table` below the tail is a handful of exact closed-form evaluations, not a search.
    rule = tight_rule_from_bundle(bundle)
    table = {m: rule.at_m(m) for m in range(1, rule.from_m)}
    return WidthRule(k, "tight", beta=rule.beta, from_m=rule.from_m, table=table)


def width_rule(source: Dict[str, Any], policy: Policy) -> WidthRule:
    """The closed-form width rule for `policy`, from a bundle (preferred) or a width section.

    `policy="tight"` requires the whole bundle on a v2 document, because the reconstruction
    reads the schedule. `policy="sym"` works from either.
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
    """How much the symbolic policy over-allocates against the exact reachable width.

    This is the quantity the figures report, and it is the reason both policies are carried.
    ForShor's own note is that the symbolic scan is loose ("54 wasted qubits at k=3") and that
    narrowing it "would put the emission outside the proved fidelity theorem" -- so the waste is
    deliberate, but it should be visible rather than silently folded into a qubit count.
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
    """`w -> W_policy(w)` over the general (non-uniform-limb) width ladder.

    v1 emitted ``general [{w, limb, W_sym, W_tight}]``; v2 emits
    ``by_w [{w, next_width, reserve_x, reserve_z}]`` and no tight column, so the tight values
    are reconstructed per `w` from the schedule (memoized -- the ladder runs to w = 96).
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown width policy {policy!r}; expected one of {POLICIES}")
    bundle, section = _as_bundle_and_section(source)
    if "general" in section:                                        # v1: both columns emitted
        key = "W_sym" if policy == "sym" else "W_tight"
        return {int(e["w"]): int(e[key]) for e in section["general"]}
    if section.get("by_w") is None and section.get("rows") is None:
        raise ValueError("width section has neither `general` (v1), `by_w` (v2) nor `rows`")
    sym = sym_tables(int((bundle or {}).get("k") or section.get("k") or 1), section)[1]
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
    """`[{k, m, n, W_sym, W_tight, gap}]` over the uniform-limb ladder, from either shape.

    This is the table the width figures are drawn from; the `gap` column is the point of it.
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
