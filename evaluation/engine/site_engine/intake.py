"""Intake of a pasted policy: parse, validate, cost with both engines. A pasted policy is never shown as verified as
a whole. Its tables are looked up by content: tables that are in a library carry that library's trust level (Lean-verified
for the accepted STO policies and verified DP tables); a table that is not known is "not verified". The bands themselves
(thresholds, which table where) are checked in Python only, so a pasted policy always carries "bands not Lean-checked".

Widths (PLAN_v2 Stage 13): a pasted policy is priced as written (its thresholds are never re-planned) at the EXACT widths
the board derives; any width ladder it declares is ignored and the response says so (`widths_notice`)."""
from __future__ import annotations

import json
from typing import Any, Dict

from . import DEFAULT_DELTA
from . import crosscheck as xc
from . import frontier as fr
from . import trust
from forshor_qualtran.policy import PolicyError
from tcost import tables as tt

MAX_BYTES = 2_000_000
MAX_POLICIES = 8
#: Keys a submitter might use to declare widths. The PP and PTP boards derive exact widths themselves (PLAN_v2 Stage 13)
#: and ignore these; the intake response says so (`widths_notice`). Never a rejection.
WIDTH_KEYS = ("declared_ladder", "ladder", "width_ladder", "widths", "child_width", "child_widths", "exact_ladder",
              "scan_ladder", "ladder_2048", "width_rule")
IGNORED_WIDTHS_NOTICE = ("This policy declares a width ladder or widths ({keys}). The board ignores them: it derives the "
                         "exact widths itself (the least width at which every reachable register value and child operand "
                         "fits, floor limbs) and prices at those.")


def declared_width_keys(doc) -> list:
    """Every width-declaring key anywhere in a submitted document (sorted, distinct)."""
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in WIDTH_KEYS:
                    found.add(k)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(doc)
    return sorted(found)


class IntakeError(ValueError):
    pass


def parse(text: str):
    if not text or not text.strip():
        raise IntakeError("The file is empty.")
    if len(text.encode()) > MAX_BYTES:
        raise IntakeError(f"The file is larger than {MAX_BYTES // 1_000_000} MB.")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise IntakeError(f"Not valid JSON: {e.msg} (line {e.lineno}, column {e.colno}).")
    try:
        docs = tt.policy_docs(doc)
        pols = tt.checked_policies(docs)
    except tt.TableError as e:
        raise IntakeError(f"Not an accepted policy: {e}")
    except (KeyError, TypeError, ValueError) as e:
        raise IntakeError(f"Malformed policy: {type(e).__name__}: {e}")
    if len(pols) > MAX_POLICIES:
        raise IntakeError(f"At most {MAX_POLICIES} policies per file; this file has {len(pols)}.")
    return pols


def cost(text: str, n: int = fr.HERO_N, delta: float = DEFAULT_DELTA) -> Dict[str, Any]:
    delta = fr.check_delta(delta)
    if not isinstance(n, int) or not 16 <= n <= 1 << 15:
        raise IntakeError("n must be an integer in [16, 32768].")
    pols = parse(text)
    keys = declared_width_keys(json.loads(text))
    notice = IGNORED_WIDTHS_NOTICE.format(keys=", ".join(keys)) if keys else None
    out = []
    for P in pols:
        try:
            xc.to_l2q(P)                         # PP: exact Toom-Cook identity of every table (engine 2 intake)
        except PolicyError as e:
            raise IntakeError(f"{P.name or P.mode}: a table fails the Toom-Cook check: {e}")
        p = fr.point(P, n, delta)
        agg = trust.of_digests(b.table.digest for b in P.bands)
        text, kind = trust.tag(agg)
        tr = {**agg, "tag": text, "kind": kind}
        cx = xc.cross_check(P, n, delta)
        fx = fr.fixed_points(P, delta, design_n=n)
        best = None
        try:
            key = "all_sto"                 # the best over submitted tables (no DP library on the page, PLAN_v2 8)
            best = fr.planned(key, P.mode, n, delta)[1]
        except Exception:
            pass
        from .policy_view import view
        out.append({"name": P.name, "mode": P.mode, "n": n, "delta": delta, "widths": P.widths,
                    "declared_widths_ignored": keys, "widths_notice": notice, "point": p, "cross_check": cx,
                    "policy": view(P, n, delta, P.name or f"pasted {P.mode}"),
                    "window": fx["window"], "points": fx["points"],
                    "frontier_T": best["T"] if best else None,
                    "vs_frontier_pct": (100.0 * (p["T"] - best["T"]) / best["T"]) if best else None,
                    "trust": tr, "tags": [tr["tag"], "pasted: bands not Lean-checked",
                                          "counts cross-checked" if cx["agree"] else "engines DISAGREE"]})
        if P.mode == tt.PAIR:
            from . import fullalg as fa
            f = fa.price_full(P, n, delta, detail=False)     # the site's one full-run pricer (Stage 18: the ForShor drop-in IR, exact widths + balanced split)
            out[-1]["full"] = f
    return {"policies": out, "widths_notice": notice, "declared_widths_ignored": keys}
