"""Read a bundle section that the 2026-09-18 drop renamed, from either document generation.

The v2 bundle kept the schema string `forshor.emit/v1` while rewriting every section's field
names (ask L1). Consumers that index the document directly therefore break one key at a time,
each failure looking unrelated to the last. These accessors are the single place a rename is
absorbed, so the next one is a one-line change here rather than a hunt through the bloqs.

The width-side adapters live in :mod:`forshor_qualtran.widths` (`width_rule`, `general_widths`,
`uniform_rows`) because they also have to reconstruct the tight policy, which the drop no
longer emits; everything else belongs here.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
from typing import Any, Dict, List, Tuple


def _section(source: Any, name: str) -> Dict[str, Any]:
    """Accept a `Bundle`, a whole bundle document, or the named section itself."""
    doc = getattr(source, "doc", None)
    if isinstance(doc, dict):
        source = doc
    inner = source.get(name)
    return inner if isinstance(inner, dict) else source


def coeff_polys(source: Dict[str, Any]) -> Tuple[Tuple[Fraction, ...], ...]:
    """The Toom-Cook interpolation coefficient polynomials, as exact rationals.

    v1: ``coeff_poly.polys`` -- a list of polynomials, each a list of ``{num, den}``.
    v2: ``coeff_poly.rows``  -- ``[{l, coeffs: [{num, den}]}]``, ordered by `l`.

    The values are identical between the two; only the envelope changed. Rows are sorted by
    `l` rather than trusted in file order, because the index is what selects which child phase
    a recursion level gets -- a permuted read would produce a wrong circuit that still costs
    exactly the same, and so would pass every count-based check.
    """
    sec = _section(source, "coeff_poly")
    if "polys" in sec:
        rows = sec["polys"]
    elif "rows" in sec:
        rows = [r["coeffs"] for r in sorted(sec["rows"], key=lambda r: int(r["l"]))]
    else:
        raise KeyError(
            "coeff_poly section has neither `polys` (v1) nor `rows` (v2); "
            f"got keys {sorted(sec)}")
    return tuple(tuple(Fraction(int(c["num"]), int(c["den"])) for c in poly) for poly in rows)


def degenerate_m(source: Dict[str, Any]) -> Any:
    """`coeff_poly.degenerate_m` -- kept the same name across both generations."""
    return _section(source, "coeff_poly").get("degenerate_m")


def schedule_census(source: Dict[str, Any]) -> Dict[str, Any]:
    """`schedule.census`, with v1's split `addScaled` / `addScaled_negSrc` reconciled.

    v2 reports one `addScaled` total plus `adderClassTotal`, and marks the sign on each op; v1
    reported `addScaled` (all of them) and `addScaled_negSrc` (how many were negated). Callers
    want both numbers, so the negSrc count is recovered from the ops when it is not reported.
    """
    sec = _section(source, "schedule")
    census = dict(sec.get("census") or {})
    if census.get("addScaled_negSrc") is None and "ops" in sec:
        census["addScaled_negSrc"] = sum(
            1 for o in sec["ops"] if o.get("op") == "addScaled" and o.get("negSrc"))
    # v1 called the adder-class total `A_k` and also reported `ops_total`; v2 calls the first
    # `adderClassTotal` and omits the second. Both spellings are provided so a consumer written
    # against either generation reads the same number rather than a KeyError.
    if census.get("A_k") is None:
        total = census.get("adderClassTotal")
        if total is None:
            total = int(census.get("addScaled", 0)) + int(census.get("negate", 0))
        census["A_k"] = total
    census.setdefault("adderClassTotal", census["A_k"])
    if census.get("ops_total") is None and "ops" in sec:
        census["ops_total"] = len(sec["ops"])
    return census


def schedule_view(source: Any) -> Dict[str, Any]:
    """The schedule in the shape `py/schedule_expand` expects, from either generation.

    `schedule_expand.build_document_from_schedule` needs `k`, `ops`, `points` plus the
    provenance strings it stamps into the document it writes. v1's schedule section carried all
    of them; v2's carries only `census`, `ops`, `points`, `q`, `resources_at_width` -- `k` moved
    to the bundle root and the provenance became a per-section object keyed by section name.

    This matters more than a rename: that expansion is how a circuit gets built WITHOUT asking
    Lean to instantiate a concrete register size. The schedule is n-free; Python expands it at
    the requested `n` and Qualtran builds the bloq. Losing this path would mean falling back to
    a concrete Lean emission, which is exactly what the interface is meant to avoid.
    """
    doc = getattr(source, "doc", None)
    if isinstance(doc, dict):
        source = doc
    sch = source.get("schedule", source)
    if "k" in sch and "decl" in sch:                       # v1: already the right shape
        return dict(sch)
    prov = (source.get("provenance") or {})
    table = source.get("table", "standard")
    table_note = prov.get("table", "") if isinstance(prov, dict) else str(prov)
    return {
        # `shor.toomsched/v1` is this VIEW's shape, not a claim about the source document: the
        # consumer (`py/toomsched.validate`) checks the string and then the content, so if the
        # v2 ops/points did not genuinely conform, validation would reject it here rather than
        # let a mislabelled schedule through.
        "schema": "shor.toomsched/v1",
        "mode": sch.get("mode", "PhaseProduct"),
        "k": int(source.get("k", sch.get("k"))),
        "q": sch.get("q", (sch.get("census") or {}).get("phaseProduct")),
        "ops": sch["ops"],
        "points": sch["points"],
        "decl": f"Shor.{'standardLoweringSetup' if table == 'standard' else 'Table_Generation.generate'}",
        "source_file": "FastMultiplication/Emit/Symbolic/Bundle.lean",
        # `toomsched.validate` fixes this vocabulary; "lean-emitted" is the accurate member --
        # the ops and points are the emitter's own output, not reconstructed.
        "provenance": "lean-emitted",
        "provenance_note": f"forshor_emit bundle --table {table}; {table_note}",
    }


def qft_plan_rows(source: Any) -> list:
    """The Cooley-Tukey split rows: v1 `qft_plan.splits`, v2 `qft_plan.rows`.

    Same content -- one row per width `w` giving the `left`/`right` split -- under a different
    key. v2 rows also carry `qft_phi`, `radix_reverse_cost` and the per-side workspace needs.
    """
    sec = _section(source, "qft_plan")
    return list(sec.get("rows", sec.get("splits", [])) or [])


def modmul_plan_rows(source: Any) -> list:
    """v1 `modmul_plan.steps`. The 2026-09-18 drop REPLACED this section with `shor_plan`, which
    answers a different question (per-(n, m) register widths, not the U1..U5 step list), so
    there is no v2 equivalent to fall back to -- an empty list, and the caller must say the
    section is unavailable rather than print a misleading zero.
    """
    doc = getattr(source, "doc", None)
    if isinstance(doc, dict):
        source = doc
    return list(((source.get("modmul_plan") or {}).get("steps")) or [])


def complete_schedule(doc: Dict[str, Any]) -> List[str]:
    """Fill `schedule.census` and `schedule.q` from `schedule.ops` when the emitter does not carry them.

    The 2026-10-14 ForShor-main emitter's schedule is `{ops, points}` only. Both fields are exact
    counts over `ops` (checked against the shipped 2026-09-18 bundles at k = 2, 3, 4: identical), so
    they are derived here rather than asked of Lean. Returns the dotted names it derived, for the
    bundle's provenance; an emitter that already supplies them is left alone.
    """
    sch = doc.get("schedule")
    if not isinstance(sch, dict) or "ops" not in sch:
        return []
    derived: List[str] = []
    if "census" not in sch:
        c = Counter(o["op"] for o in sch["ops"])
        census = {name: c[name] for name in ("addScaled", "negate", "phaseProduct", "shiftL", "shiftR")}
        census["adderClassTotal"] = census["addScaled"] + census["negate"]
        sch["census"] = census
        derived.append("schedule.census")
    if "q" not in sch:
        sch["q"] = sch["census"]["phaseProduct"]
        derived.append("schedule.q")
    return derived


def complete_qft_plan(doc: Dict[str, Any]) -> List[str]:
    """Fill the Cooley-Tukey split fields of `qft_plan.rows` when the emitter does not carry them.

    The 2026-10-14 ForShor-main emitter's rows are `{w, x_workspace_need, z_workspace_need}`; the
    2026-09-18 rows also had `left`, `right`, `qft_phi` and `radix_reverse_cost`. Those are fixed by
    `lowerQFTPlan`'s split rule (`left = w // 2`, `right = w - left`, angle `pi / 2^(w-1)`, reverse cost
    `3 * left`), and the rule reproduces every row of the shipped 2026-09-18 bundles at k = 2, 3, 4
    (285 rows, no difference). It is applied for `w >= 2`, the range those bundles covered. Returns the
    dotted name it derived, for the bundle's provenance.
    """
    sec = doc.get("qft_plan")
    rows = sec.get("rows") if isinstance(sec, dict) else None
    if not rows or all("left" in r for r in rows if int(r["w"]) >= 2):
        return []
    for r in rows:
        w = int(r["w"])
        if w < 2 or "left" in r:
            continue
        left = w // 2
        r["left"], r["right"] = left, w - left
        r["qft_phi"] = {"num": 1, "den": 2 ** (w - 1), "unit": "pi"}
        r["radix_reverse_cost"] = 3 * left
    return ["qft_plan.rows[left,right,qft_phi,radix_reverse_cost]"]


def complete(doc: Dict[str, Any]) -> List[str]:
    """Every derivation above, in place; the dotted names of what was derived (not emitted)."""
    derived = complete_schedule(doc) + complete_qft_plan(doc)
    width = doc.get("width")
    if isinstance(width, dict) and "k" not in width and "k" in doc:
        width["k"] = doc["k"]                      # ForShor-main keeps `k` at the bundle root only
        derived.append("width.k")
    derived += complete_width(doc)
    return derived


def _affine_tail(table: Dict[int, int]) -> Any:
    """`Shor.affineTail` over `{m: W(m)}`: the least `m0` from which `W(m) = m + beta`, `beta` taken from the
    largest scanned `m`; None when the table is empty or `beta` is negative (Lean's `none`)."""
    if not table:
        return None
    ordered = sorted(table.items())
    m_last, w_last = ordered[-1]
    beta = w_last - m_last
    if beta < 0:
        return None
    m0 = m_last
    for m, w in reversed(ordered[:-1]):
        if w - m != beta:
            break
        m0 = m
    return {"beta": beta, "m0": m0}


def complete_width(doc: Dict[str, Any]) -> List[str]:
    """Give a ForShor-main width section (`rows` only) the 2026-09-18 tables `by_w`, `by_m`, `affine_tail`.

    `by_w` is `rows` for `w >= k` (where the 2026-09-18 table started); `by_m[m]` is `next_width` at `w = k*m` for `m = 1..opts.m_max` (the ladder the
    2026-09-18 emitter scanned, so the tail is fitted over the same window); `affine_tail` is `Shor.affineTail`
    over it. All three are exact functions of `rows` (identical to the shipped 2026-09-18 tables at k = 2, 3, 4
    where both exist). Derived, not emitted.
    """
    width = doc.get("width")
    if not isinstance(width, dict) or "rows" not in width or "by_m" in width:
        return []
    k = int(doc["k"])
    m_max = int((doc.get("opts") or {}).get("m_max") or 0)
    by_w = {int(r["w"]): int(r["next_width"]) for r in width["rows"]}
    by_m = {m: by_w[k * m] for m in range(1, (m_max or max(by_w) // k) + 1) if k * m in by_w}
    width["by_w"] = [dict(r) for r in width["rows"] if int(r["w"]) >= k]       # the 09-18 table started at w = k
    width["by_m"] = [{"m": m, "width": by_m[m], "limb_width": m} for m in sorted(by_m)]
    aff = _affine_tail(by_m)
    if aff is not None:
        width["affine_tail"] = aff
    return ["width.by_w", "width.by_m", "width.affine_tail"]
