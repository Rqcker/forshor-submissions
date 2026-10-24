"""Read a bundle section that the 2026-09-18 drop renamed, from the documents of the two generations.

The v2 bundle kept the schema string `forshor.emit/v1`, but it changed the field names of all
sections (ask L1). Thus a consumer that reads the document directly fails at one key after the
other, and each failure seems to have no relation to the previous one. These accessors are the only
location that handles a rename. Thus the next rename is a one-line change here, and you do not have
to search through the bloqs.

The adapters for widths are in :mod:`forshor_qualtran.widths` (`width_rule`, `general_widths`,
`uniform_rows`). The reason: they must also build the tight policy again, and the drop does not
emit it now. All other adapters are in this module.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, Tuple


def _section(source: Any, name: str) -> Dict[str, Any]:
    """Accept a `Bundle`, a full bundle document, or the named section."""
    doc = getattr(source, "doc", None)
    if isinstance(doc, dict):
        source = doc
    inner = source.get(name)
    return inner if isinstance(inner, dict) else source


def coeff_polys(source: Dict[str, Any]) -> Tuple[Tuple[Fraction, ...], ...]:
    """Return the coefficient polynomials of the Toom-Cook interpolation, as exact rationals.

    v1: ``coeff_poly.polys`` -- a list of polynomials, each a list of ``{num, den}``.
    v2: ``coeff_poly.rows``  -- ``[{l, coeffs: [{num, den}]}]``, ordered by `l`.

    The values are identical in the two generations. Only the envelope changed. The function sorts
    the rows by `l` and does not use the file order. The reason: the index selects the child phase
    that a recursion level gets. A read in a different order gives an incorrect circuit with exactly
    the same cost, and that circuit passes all checks that use counts.
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
    """Return `coeff_poly.degenerate_m`. Its name is the same in the two generations."""
    return _section(source, "coeff_poly").get("degenerate_m")


def schedule_census(source: Dict[str, Any]) -> Dict[str, Any]:
    """Return `schedule.census`, with the split v1 fields `addScaled` / `addScaled_negSrc` made consistent.

    v2 gives one total for `addScaled` and `adderClassTotal`, and it marks the sign on each op.
    v1 gave `addScaled` (all of them) and `addScaled_negSrc` (the number with a negated source).
    Callers need the two numbers. Thus, if the document does not give the negSrc count, the
    function counts it from the ops.
    """
    sec = _section(source, "schedule")
    census = dict(sec.get("census") or {})
    if census.get("addScaled_negSrc") is None and "ops" in sec:
        census["addScaled_negSrc"] = sum(
            1 for o in sec["ops"] if o.get("op") == "addScaled" and o.get("negSrc"))
    # v1 called the adder-class total `A_k`, and it also gave `ops_total`. v2 calls the first
    # `adderClassTotal`, and it does not give the second. The function gives the two spellings.
    # Thus a consumer for each generation reads the same number and does not get a KeyError.
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
    """Return the schedule in the shape that `py/schedule_expand` expects, from each generation.

    `schedule_expand.build_document_from_schedule` needs `k`, `ops`, `points`, and the
    provenance strings that it writes into its output document. The v1 schedule section had all
    of them. The v2 section has only `census`, `ops`, `points`, `q`, `resources_at_width`. In v2,
    `k` moved to the bundle root, and the provenance became an object with one key for each section name.

    This is more important than a rename. This expansion lets us build a circuit WITHOUT a request
    to Lean for a concrete register size. The schedule does not contain n. Python expands it at
    the requested `n`, and Qualtran builds the bloq. Without this path, we must use a concrete
    Lean emission, and the interface must prevent exactly that.
    """
    doc = getattr(source, "doc", None)
    if isinstance(doc, dict):
        source = doc
    sch = source.get("schedule", source)
    if "k" in sch and "decl" in sch:                       # v1: the shape is already correct
        return dict(sch)
    prov = (source.get("provenance") or {})
    table = source.get("table", "standard")
    table_note = prov.get("table", "") if isinstance(prov, dict) else str(prov)
    return {
        # `shor.toomsched/v1` is the shape of this VIEW. It is not a claim about the source document.
        # The consumer (`py/toomsched.validate`) checks the string and then the content. Thus, if the
        # v2 ops/points do not really agree with the schema, the validation rejects them here. It does
        # not let a schedule with an incorrect label through.
        "schema": "shor.toomsched/v1",
        "mode": sch.get("mode", "PhaseProduct"),
        "k": int(source.get("k", sch.get("k"))),
        "q": sch.get("q", (sch.get("census") or {}).get("phaseProduct")),
        "ops": sch["ops"],
        "points": sch["points"],
        "decl": f"Shor.{'standardLoweringSetup' if table == 'standard' else 'Table_Generation.generate'}",
        "source_file": "FastMultiplication/Emit/Symbolic/Bundle.lean",
        # `toomsched.validate` sets this vocabulary. "lean-emitted" is the correct member, because
        # the ops and points are the output of the emitter. We did not build them again.
        "provenance": "lean-emitted",
        "provenance_note": f"forshor_emit bundle --table {table}; {table_note}",
    }


def qft_plan_rows(source: Any) -> list:
    """Return the Cooley-Tukey split rows: v1 `qft_plan.splits`, v2 `qft_plan.rows`.

    The content is the same, with a different key: one row for each width `w`, which gives the
    `left`/`right` split. The v2 rows also have `qft_phi`, `radix_reverse_cost` and the
    workspace needs of each side.
    """
    sec = _section(source, "qft_plan")
    return list(sec.get("rows", sec.get("splits", [])) or [])


def modmul_plan_rows(source: Any) -> list:
    """Return v1 `modmul_plan.steps`. The 2026-09-18 drop REPLACED this section with `shor_plan`.
    `shor_plan` gives different data (register widths for each (n, m), not the U1..U5 step list).
    Thus v2 has no equivalent, and the function returns an empty list. The caller must then
    say that the section is not available. It must not print a zero, because a zero is misleading.
    """
    doc = getattr(source, "doc", None)
    if isinstance(doc, dict):
        source = doc
    return list(((source.get("modmul_plan") or {}).get("steps")) or [])
