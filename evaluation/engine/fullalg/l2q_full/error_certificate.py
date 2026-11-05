"""Error-budget certificate (T5 of TODO_ARTIFACT_INFRASTRUCTURE.2026-09-11.md).

A machine-checkable record that a frozen compilation of the order-finding program is within `eps_total`
(operator norm), by category, with every term traceable to a catalogue row and a multiplicity:

    ||U_exact - U_compiled|| <= sum_rows m_row * eps_row            (telescoping / triangle inequality)

Categories
  gradient_rounding   a cell (gadget) or a window pair (windowed) whose turn was rounded to b bits: <= pi * 2^-b each
  pruning             a cell / pair elided because its b-bit constant is 0 (`leaf_drop_rule`): its true turn, <= pi * 2^-b
  synthesis           the b gradient-preparation words (Ross-Selinger, certified operator norm each), prepared and
                      un-prepared once per gradient register allocation
  frontend_numeric    0: every angle is an exact rational in the wired program and every constant / table is produced
                      by the exact integer generator (`leaf_contracts`); the concrete Qualtran route reduces exponents
                      mod 2 exactly before its only float
  export_numeric      0 for the Clifford+T gate stream (no continuous parameters are exported)

Allocation is RESOLUTION-WEIGHTED: only sites that are actually rounded contribute (an exact dyadic site with
s <= b costs nothing), each at its own bound, with multiplicities from the census -- the uniform policy of
`constants.gradient_bits` (every rotation charged eps = delta / R) is the comparison line. `allocate_b` is the
least b whose certified bound meets the budget.

The checker (`check`) recomputes the category sums and the total from the certificate's own rows with
`fractions.Fraction` (stdlib only), and verifies that every referenced row hash exists in the catalogue it is
checked against.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import family as F
from . import leaf_contracts as LCn
from . import program_cost as PC

SCHEMA = "shor.error_certificate/v1"
CATEGORIES = ("gradient_rounding", "pruning", "synthesis", "frontend_numeric", "export_numeric")


# ------------------------------------------------------------------------------------------ rows
def _part_rows(census, toggles: PC.Toggles, N: int, nd: int):
    return LCn.build_catalogue(list(census.items()), b=toggles.b, N=N, nd=nd, lowering=toggles.lowering,
                               window=toggles.resolved_window() if toggles.lowering == "windowed" else (2, 2),
                               small_exact=toggles.small_exact, and_pair=LCn.AndPair(toggles.and_pair),
                               drop_rule=toggles.drop_rule)


def _row_terms(rows, part: str, repeat: int) -> List[Dict[str, Any]]:
    """One certificate term per catalogue row with a non-zero bound: (hash, part, multiplicity, bound by category)."""
    out = []
    for r in rows:
        if r.family != "phase_grid":
            continue
        by = dict(r.error_by) if r.error_by else ({"gradient_rounding": r.error_each * r.mult} if r.error_each else {})
        if not by or all(v == 0.0 for v in by.values()):
            continue
        # per-key average bound (aggregated rows carry the exact mult-weighted sum; the term stores the sum and mult)
        out.append({"row_hash": r.row_hash, "part": part, "repeat": repeat, "mult": r.mult, "n_keys": r.n_keys,
                    "kind": r.key[0], "widths": [r.key[2], r.key[3]], "lowering": r.lowering,
                    "price_basis": r.certificate.get("price_basis", "exact per cell / per table"),
                    "bound_by_category": {k: float(v) for k, v in by.items() if v}})
    return out


# ------------------------------------------------------------------------------------ certificate
def build(fam: F.Family, a: int, N: int, n: int, m: int, L: int, toggles: PC.Toggles, eps_total: float, *,
          prep_eps: float = 1e-10, gradient_allocations: int = 1, census: Optional[Dict[str, Counter]] = None) -> Dict[str, Any]:
    """The certificate of the program `(a, N, n, m, L)` compiled under `toggles` (its `b` included)."""
    t0 = time.time()
    nd = n + 1
    cen = census if census is not None else PC.program_census(PC.build_program(fam, a, N, n, m, L, toggles))
    terms: List[Dict[str, Any]] = []
    rows_by_part: Dict[str, Any] = {}
    for part, repeat in (("frame", 1), ("step", L), ("iqft", 1)):
        rows = _part_rows(cen[part], toggles, N, nd)
        rows_by_part[part] = rows
        terms += _row_terms(rows, part, repeat)
    cats: Dict[str, float] = {c: 0.0 for c in CATEGORIES}
    for t in terms:
        for c, v in t["bound_by_category"].items():
            cats[c] += v * t["repeat"]
    # synthesis: b prep words, prepared and un-prepared once per gradient allocation
    words = LCn.prep_word_records(toggles.b, prep_eps)
    synth_each = sum(w["eps_achieved"] for w in words)
    cats["synthesis"] = 2.0 * gradient_allocations * synth_each
    synthesis = {"b": toggles.b, "eps_requested_per_word": prep_eps, "sum_eps_achieved": synth_each,
                 "max_eps_achieved": max(w["eps_achieved"] for w in words), "gradient_allocations": gradient_allocations,
                 "t_prep": sum(w["t_count"] for w in words), "words": [{"bit": w["bit"], "kind": w["kind"], "eps_achieved": w["eps_achieved"],
                                                                        "t_count": w["t_count"]} for w in words]}
    cats["frontend_numeric"] = 0.0
    cats["export_numeric"] = 0.0
    total = sum(cats.values())
    cert = {
        "schema": SCHEMA, "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "program": {"a": a, "N_bits": N.bit_length(), "N_sha256": hashlib.sha256(str(N).encode()).hexdigest()[:16], "n": n, "m": m, "L": L},
        "toggles": toggles.vector(), "eps_total": float(eps_total), "b": toggles.b,
        "allocation": "resolution-weighted: only rounded / pruned sites contribute, each at pi*2^-b (rounding) or its true turn (pruning), multiplicities from the census",
        "formula": "bound = sum_terms repeat * sum_cat bound_by_category + synthesis + numeric",
        "categories": {c: {"bound": cats[c]} for c in CATEGORIES},
        "categories_note": {
            "gradient_rounding": "cells (gadget) / window pairs (windowed) rounded to b bits; each <= pi * 2^-b",
            "pruning": "cells / pairs elided by leaf_drop_rule (b-bit constant 0); each contributes its true turn <= pi * 2^-b",
            "synthesis": "the b gradient-preparation words (Ross-Selinger), certify_angle operator norm each, x2 (prep + unprep) x allocations",
            "frontend_numeric": "0: exact rationals throughout; constants / tables from the integer generator; concrete CZPow exponents reduced mod 2 exactly",
            "export_numeric": "0: the Clifford+T stream has no continuous parameters",
        },
        "synthesis": synthesis,
        "terms": terms,
        "census": {p: {"keys": len(cen[p]), "leaves": int(sum(cen[p].values()))} for p in cen},
        "rows": {p: [{"row_hash": r.row_hash, "family": r.family, "mult": r.mult, "n_keys": r.n_keys, "t_each": r.t_each,
                      "error_total": r.error_total} for r in rows] for p, rows in rows_by_part.items()},
        "total_bound": total, "passes": total <= eps_total,
        "built_s": time.time() - t0,
    }
    return cert


def bound_at(fam: F.Family, a: int, N: int, n: int, m: int, L: int, toggles: PC.Toggles, b: int, *,
             census: Optional[Dict[str, Counter]] = None, prep_eps: float = 1e-10) -> float:
    """The certified bound at gradient width b (everything else from `toggles`)."""
    import dataclasses
    tg = dataclasses.replace(toggles, b=b)
    return build(fam, a, N, n, m, L, tg, 1.0, prep_eps=prep_eps, census=census)["total_bound"]


def allocate_b(fam: F.Family, a: int, N: int, n: int, m: int, L: int, toggles: PC.Toggles, eps_total: float, *,
               b_lo: int = 4, b_hi: int = 96, census: Optional[Dict[str, Counter]] = None,
               prep_eps: float = 1e-10) -> Tuple[int, Dict[int, float]]:
    """The least b in [b_lo, b_hi] whose certified bound is <= eps_total (bisection on the monotone bound); returns
    (b, {b: bound} evaluated). The census is computed once (the wiring does not depend on b under the model currency)."""
    cen = census if census is not None else PC.program_census(PC.build_program(fam, a, N, n, m, L, toggles))
    seen: Dict[int, float] = {}

    def f(b):
        if b not in seen:
            seen[b] = bound_at(fam, a, N, n, m, L, toggles, b, census=cen, prep_eps=prep_eps)
        return seen[b]
    if f(b_hi) > eps_total:
        return b_hi, seen
    lo, hi = b_lo, b_hi
    if f(lo) <= eps_total:
        return lo, seen
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if f(mid) <= eps_total:
            hi = mid
        else:
            lo = mid
    return hi, seen


# ---------------------------------------------------------------------------------------- checker
def check(cert: Dict[str, Any], catalogue_rows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Independent recomputation (stdlib only): rebuild every category sum and the total from the certificate's
    own terms with `Fraction`, compare with the stored values, and (given catalogue rows) verify hash closure."""
    cats = {c: Fraction(0) for c in CATEGORIES}
    for t in cert["terms"]:
        rep = Fraction(int(t["repeat"]))
        for c, v in t["bound_by_category"].items():
            cats[c] += rep * Fraction(repr(float(v)))
    synth = cert["synthesis"]
    cats["synthesis"] = Fraction(2) * Fraction(int(synth["gradient_allocations"])) * sum(Fraction(repr(float(w["eps_achieved"]))) for w in synth["words"])
    total = sum(cats.values())
    stored = {c: Fraction(repr(float(cert["categories"][c]["bound"]))) for c in CATEGORIES}
    tol = Fraction(1, 10 ** 9)

    def close(x, y):
        return abs(x - y) <= tol * max(Fraction(1), abs(y))
    per_cat = {c: close(cats[c], stored[c]) for c in CATEGORIES}
    total_ok = close(total, Fraction(repr(float(cert["total_bound"]))))
    passes_ok = (total <= Fraction(repr(float(cert["eps_total"])))) == bool(cert["passes"])
    closure = None
    if catalogue_rows is not None:
        have = {r["row_hash"] for r in catalogue_rows}
        refs = {t["row_hash"] for t in cert["terms"]} | {r["row_hash"] for p in cert["rows"].values() for r in p}
        missing = sorted(refs - have)
        closure = {"referenced": len(refs), "missing": missing, "ok": not missing}
    ok = all(per_cat.values()) and total_ok and passes_ok and (closure is None or closure["ok"])
    return {"ok": ok, "categories_ok": per_cat, "total_ok": total_ok, "passes_ok": passes_ok,
            "recomputed": {c: float(v) for c, v in cats.items()}, "recomputed_total": float(total), "closure": closure}


def save(cert: Dict[str, Any], path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cert, fh, indent=1)


def load(path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
