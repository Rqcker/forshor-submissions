#!/usr/bin/env python3
"""ForShor Challenge starter pack: price a POLICY locally with the challenge site's engine, and prepare it for Lean.

    python starter.py price POLICY.json [--n 2048] [--delta 0.01] [--no-full] [--json OUT.json]
        PP or PTP banded policy (site policy JSON). Board T at EXACT widths (the board derives them; a declared
        width ladder is ignored, with a notice), both engines, the exact ladder; Q, the live-qubit peak the boards
        rank by, and the score Q x T, with where it would rank on the board's snapshot taken when the pack was built;
        ForShor's scanned widths as a labelled cross-check; for PP also the full order-finding run on ForShor's
        exact-width IR (exact register widths, balanced split for unequal operands), with upstream ForShor's
        scanned-width IR as a cross-check. A policy that asks for a negation method other than the default is refused.
    python starter.py forshor-doc policy.json [--delta 0.01] [--json OUT.json]
        ForShor's printed policy document (`lake exe forshor_policy_submission --balanced > policy.json`): the full run priced from the document's own IR and from the converted policy; the two must
        agree (the site's harness stage 5). An exact-width document ("exact" width rule) is checked on the board's IR, an
        upstream (scanned-width) document on upstream's IR, the board's cross-check.
    python starter.py template POLICY.json [--out Policy.lean] [--forshor FORSHOR_DIR | --lib ForShor]
        a PP site policy written as the submission file, TableGeneration/Submission/Policy.lean (ForShor's PolicyTemplate format). The imports name
        ForShor's Lean library, `ForShor`. `--forshor` checks it against your ForShor checkout.
    python starter.py lean-input POLICY.json [--out policy.lean-input.json]
        the input document of the policy check (PP) / the PhaseTripleProduct harness (PTP) Lean checkers.
    python starter.py examples [--check]
        price every example; --check compares with the pinned outputs in examples/expected/.

Lean decides correctness; nothing here runs Lean. The T counts are Qualtran's (the site's engine, vendored in
engine/, sources and sha256 in MANIFEST.json); they are counted in Python, not proved in Lean.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
ENGINE = Path(os.environ.get("FORSHOR_STARTER_ENGINE", HERE / "engine")).resolve()
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(HERE / "harness"))

import site_engine  # noqa: E402,F401  (binds the vendored Qualtran before anything imports qualtran)
from site_engine import DEFAULT_DELTA, SITE_MODEL_VERSION  # noqa: E402
from site_engine import crosscheck as xc  # noqa: E402
from site_engine import frontier as fr  # noqa: E402
from site_engine import library as lib  # noqa: E402
from tcost import score as ts  # noqa: E402
from tcost import tables as tt  # noqa: E402
from policy_options import check_coherent_export, check_table_options  # noqa: E402

N = 2048
SCHEMA = "forshor-starter.price/1"
EXAMPLES = HERE / "examples"

#: The site's intake (`site_engine/intake.py`, PLAN_v2 Stage 13): keys a submitter might use to declare widths. The
#: PP and PTP boards derive exact widths themselves and ignore these; never a rejection. Kept identical (tested).
WIDTH_KEYS = ("declared_ladder", "ladder", "width_ladder", "widths", "child_width", "child_widths", "exact_ladder",
              "scan_ladder", "ladder_2048", "width_rule")
IGNORED_WIDTHS_NOTICE = ("This policy declares a width ladder or widths ({keys}). The board ignores them: it derives the "
                         "exact widths itself (the least width at which every reachable register value and child operand "
                         "fits, floor limbs) and prices at those.")
#: The site's label for Q (`site_engine/frontier.py`, `policy_view.py`): the live-qubit peak, a formula over the policy's widths. The board
#: shows a more specific label for the nine PhaseProduct and six PhaseTripleProduct policies whose peak ForShor has checked in Lean.
QUBITS_LABEL = fr.QUBITS_LABEL_PEAK
BOARD_SNAPSHOT = EXAMPLES / "board_snapshot.json"
TRUST = ("T counted in Python (Qualtran), not proved in Lean; Q is the live-qubit peak, a formula over the policy's widths, not a Lean-proved "
         "count for your own policy. Lean certifies a policy's tables, thresholds and widths: through the policy check for PhaseProduct, "
         "and through ForShor's PhaseTripleProduct checks for PhaseTripleProduct; for the full run through ForShor's own check. "
         "The full-run census comes from ForShor's IR, matched to the compiled circuit at sampled widths, not proved at n = 2048.")


class StarterError(ValueError):
    pass


# ------------------------------------------------------------------------------------------------- helpers
def declared_width_keys(doc: Any) -> List[str]:
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


def accept_point_order_alias(x: Any, where: str = "policy") -> Any:
    """Return a copy of `x` in which every `pointOrder` key is spelled `point_order`.

    The site's panel files (`policyJson`) spell a band's key `pointOrder`; the engine and this pack's format spell it
    `point_order`. Both are accepted so a policy downloaded from the site prices as it is. A band that carries both
    spellings with different values is refused, because the pack cannot know which one is meant.
    """
    if isinstance(x, list):
        return [accept_point_order_alias(v, where) for v in x]
    if not isinstance(x, dict):
        return x
    out = {k: accept_point_order_alias(v, where) for k, v in x.items()}
    if "pointOrder" in out:
        camel = out.pop("pointOrder")
        if "point_order" in out and out["point_order"] != camel:
            raise StarterError(f"{where}: a band has both `pointOrder` and `point_order` with different values; keep one")
        out.setdefault("point_order", camel)
    return out


def accepted_policies(docs) -> list:
    """`Policy.from_json` of each doc (each band's table is checked as it is read), refusing what the boards refuse: no policy, or a
    policy with no bands (Lean reads `[]` as "not entered"; `Policy.leaf` is the schoolbook baseline). Unlike the engine's
    `checked_policies` it allows different tables of the same k in different bands, as the boards and both Lean checks do."""
    pols = [tt.Policy.from_json(d) for d in docs]
    if not pols:
        raise tt.TableError("no policy in the document")
    for p in pols:
        if not p.bands:
            raise tt.TableError(f"{p.name or p.mode}: no bands (not entered); Policy.leaf is the schoolbook baseline")
    return pols


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StarterError(f"{path}: not valid JSON: {e.msg} (line {e.lineno})") from None
    except UnicodeDecodeError:
        raise StarterError(f"{path}: file must be UTF-8") from None
    except OSError as e:
        raise StarterError(f"cannot read {path}: {e.strerror}") from None


def load(path: Path) -> tuple:
    doc = accept_point_order_alias(read_json(path), str(path))
    check_negation(doc)
    try:
        check_table_options(doc)
        pols = accepted_policies(tt.policy_docs(doc))
    except tt.TableError as e:
        raise StarterError(f"{path}: not an accepted policy: {e}")
    except (KeyError, TypeError, ValueError) as e:
        raise StarterError(f"{path}: malformed policy: {type(e).__name__}: {e}")
    return doc, pols


def ladder(P: tt.Policy, n: int) -> List[Dict[str, Any]]:
    """Level by level: width, k, table id (archive id, DP id, KMY id, or "pasted"). As the site's frontier."""
    origin = lib.table_origin()
    rows, w = [], n
    for T in tt.chain_for(P, n):
        rows.append({"width": w, "k": T.k, "table": origin.get(T.digest, "pasted"), "digest": T.digest[:12]})
        w = T.child_width(w, P.widths)
    rows.append({"width": w, "k": None, "table": "leaf", "digest": None})
    return rows


def ladder_str(rows) -> str:
    return " -> ".join(f"{r['width']}:{r['k'] if r['k'] else 'leaf'}" for r in rows)


def board_price(P: tt.Policy, n: int, delta: float) -> Dict[str, Any]:
    s = ts.score_policy(P, n, delta)
    cx = xc.cross_check(P, n, delta, tcost_score=s)
    g, tb = s["gate_counts"], s["t_breakdown"]
    rows = ladder(P, n)
    out = {"widths": P.widths, "T": s["t_count"], "ladder": [[r["width"], r["k"]] for r in rows],
           "ladder_str": ladder_str(rows), "tables": [r["table"] for r in rows if r["k"]],
           "rotations": g["rotation"], "toffoli_class": g["and"] + g["toffoli"] + g["cswap"],
           "ts_per_rotation": tb["ts_per_rotation"], "eps": tb["rotation_eps"],
           "engine2_T": cx["engine2"]["T"], "engines_agree": cx["agree"]}
    out["qubits"] = fr.live_peak_qubits(P, int(n))
    out["qubits_label"] = QUBITS_LABEL
    out["score"] = out["qubits"] * out["T"]
    if P.mode == tt.PAIR:
        rx, rz = xc.to_l2q(P).reserve_need(int(n), int(n))
        out["qubits_static_layout"] = 2 * int(n) + rx + rz       # a cross-check: every child slot gets a private workspace
    return out


def full_price(P: tt.Policy, n: int, delta: float, rule: Optional[str] = None) -> Dict[str, Any]:
    """The site's one full-run pricer (`site_engine.fullalg.price_full`). Default: the board, ForShor's exact-width IR
    (exact widths, balanced split), with upstream ForShor's scanned-width IR as a labelled cross-check.
    ``rule="scan"``: upstream's IR as the headline (what an upstream document is checked on)."""
    from site_engine import fullalg as fa
    r = fa.price_full(P, n, delta, detail=False, rule=rule)
    keep = ("T", "rotations", "T_per_rotation", "eps", "toffoli", "cnot", "ladder_str", "work_width",
            "m_bits", "lean_grid_leaf_T", "literal_lean_T", "trial_count", "end_to_end")
    out = {k: r[k] for k in keep if k in r}
    out.update(qubits=r["qubits_peak"], qubits_label=QUBITS_LABEL, qubits_static_layout=r["qubits_layout"], peak_extra=r["peak_extra"],
               negation=r["negate"], score=r["qubits_peak"] * r["T"])
    out.update(model=r["model"], tier=r["tier"], census_trust=r["census_trust"], width_rule=r["width_rule"],
               ir=("ForShor IR at exact widths (balanced split)" if r["width_rule"] != "scan"
                   else "upstream ForShor IR (scanned widths, no balanced split)"))
    sc = r.get("cross_checks", {}).get("forshor_scan_ir")
    if sc:
        out["forshor_scan_ir"] = {k: sc[k] for k in ("T", "qubits", "ladder_str", "label")}
    return out


def rank_on_board(board: str, score: float, n: int, delta: float) -> Optional[Dict[str, Any]]:
    """Where `score` (Q x T) would stand among the rows of the results board's snapshot taken when this pack was built (delta = 0.01, n = 2048
    only; the live board is the page). Lower is better; a tie ranks with the earlier row, as the board does."""
    if n != N or delta != DEFAULT_DELTA or not BOARD_SNAPSHOT.is_file():
        return None
    snap = json.loads(BOARD_SNAPSHOT.read_text())
    rows = snap["boards"].get(board) or []
    if not rows:
        return None
    ahead = sum(1 for r in rows if r["score"] <= score)
    return {"rank": ahead + 1, "of": len(rows) + 1, "best_score": min(r["score"] for r in rows), "snapshot": snap["built"],
            "note": "against the board's rows at the pack's build date; the page shows the live board"}


def price_policy(P: tt.Policy, n: int, delta: float, full: bool) -> Dict[str, Any]:
    try:
        xc.to_l2q(P)                    # the site's intake: exact Toom-Cook identity of every table (PP and PTP)
    except Exception as e:  # noqa: BLE001
        raise StarterError(f"{P.name or P.mode}: a table fails the Toom-Cook check: {e}")
    E = P.with_widths("exact")
    rec = {"name": P.name, "mode": P.mode,
           "bands": [{"threshold": b.threshold, "k": b.table.k, "digest": b.table.digest[:12]} for b in P.bands],
           "board": board_price(E, n, delta)}
    rec["board"]["rank_on_board"] = rank_on_board("pp" if P.mode == tt.PAIR else "ptp", rec["board"]["score"], n, delta)
    S = P.with_widths("scan")
    sc = board_price(S, n, delta)
    rec["scan_cross_check"] = {"label": "ForShor scanned widths: a cross-check, not the board's price",
                               **{k: sc[k] for k in ("T", "ladder_str", "engine2_T", "engines_agree")}}
    if P.mode == tt.PAIR:
        rec["scan_cross_check"]["qubits_lean_static"] = sc["qubits_static_layout"]
        if full:
            rec["full_run"] = full_price(P, n, delta)
            rec["full_run"]["rank_on_board"] = rank_on_board("full", rec["full_run"]["score"], n, delta)
            kmax = max((b.table.k for b in P.bands), default=0)
            rec["full_run"]["forshor_check"] = (
                {"possible": True, "note": "every band has k <= 8: `starter.py template` writes the submission file Policy.lean for it"} if kmax <= 8 else
                {"possible": False, "note": f"a band has k = {kmax}: submissions take 1 < k <= 8, so this policy is priced on the full board but cannot be submitted as a Lean file"})
    return rec


def engine_info() -> Dict[str, Any]:
    man = HERE / "MANIFEST.json"
    return {"site_model": SITE_MODEL_VERSION,
            "manifest_sha256": hashlib.sha256(man.read_bytes()).hexdigest() if man.is_file() else None}


NEGATION_REFUSAL = ("the policy asks for negation {m!r}. ForShor's policies carry an optional `negation` (`oneQubit`, the default, or `scratch`), but "
                    "the boards and this pack price every policy under the default `oneQubit`, so a number for any other method would be wrong. "
                    "Remove the key (or set it to \"oneQubit\").")


def check_negation(doc: Any) -> None:
    def walk(x):
        if isinstance(x, dict):
            if "negation" in x and x["negation"] != "oneQubit":
                raise StarterError(NEGATION_REFUSAL.format(m=x["negation"]))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(doc)


def price(path: Path, n: int = N, delta: float = DEFAULT_DELTA, full: bool = True) -> Dict[str, Any]:
    doc, pols = load(path)
    keys = declared_width_keys(doc)
    return {"schema": SCHEMA, "input": Path(path).name, "n": n, "delta": delta,
            "declared_widths_ignored": keys,
            "widths_notice": IGNORED_WIDTHS_NOTICE.format(keys=", ".join(keys)) if keys else None,
            "policies": [price_policy(P, n, delta, full) for P in pols], "trust": TRUST}


# --------------------------------------------------------------------------------------- ForShor documents
FORSHOR_SCHEMA = "forshor.policy-submission/v1"


def _pt(p: Dict[str, Any]) -> str:
    """ForShor point dict -> site spelling (as the site's harness/forshor_verify.py). frac 0 is infinity."""
    if p["kind"] == "int":
        return str(int(p["z"]))
    m = int(p["m"])
    return "inf" if m == 0 else f"{1 if m > 0 else -1}/{abs(m)}"


def policy_from_doc(doc: Dict[str, Any], name: str = "forshor-policy") -> tt.Policy:
    bands = [tt.Band(int(b["threshold"]), tt.Table.from_json({"mode": tt.PAIR, "k": int(b["k"]),
                                                              "point_order": [_pt(p) for p in b["points"]],
                                                              "operations": b["ops"]})) for b in doc["bands"]]
    return tt.Policy(mode=tt.PAIR, bands=tuple(bands), name=name)


def forshor_doc(path: Path, delta: float = DEFAULT_DELTA) -> Dict[str, Any]:
    from site_engine import fullalg as fa
    doc = read_json(path)
    if not isinstance(doc, dict):
        raise StarterError(f"{path}: expected a ForShor policy document (JSON object)")
    if doc.get("schema") != FORSHOR_SCHEMA:
        raise StarterError(f"{path}: schema {doc.get('schema')!r} is not {FORSHOR_SCHEMA!r} (a ForShor policy document)")
    try:
        check_coherent_export(doc)
    except ValueError as error:
        raise StarterError(f"{path}: {error}") from error
    check_negation(doc)
    rule = fa.doc_width_rule(doc)          # "exact": exact-width document (board IR); "scan": the default scan (cross-check IR)
    ref = json.loads((fa.REFERENCE_SCAN if rule == "scan" else fa.REFERENCE).read_text())
    fixed = {"benchmark": ("bits", "trial_count"), "precision": ("eta", "m")}
    diffs = [f"{s}.{f}: {doc.get(s, {}).get(f)!r} != {ref[s][f]!r}" for s, fs in fixed.items() for f in fs
             if doc.get(s, {}).get(f) != ref[s][f]]
    P = policy_from_doc(doc, Path(path).stem)
    for b in P.bands:
        if not 2 <= b.table.k <= 8:
            raise StarterError(f"band k = {b.table.k}: submissions take 1 < k <= 8")
    xc.to_l2q(P)
    r = full_price(P, N, delta, rule=rule)
    if rule != "scan":
        r["rank_on_board"] = rank_on_board("full", r["score"], N, delta)
    t_doc = float(fa.price_document(doc, N, delta)["T"])
    return {"schema": SCHEMA, "input": Path(path).name, "n": N, "delta": delta, "width_rule": rule,
            "fixed_values_match_forshor": not diffs, "fixed_value_differences": diffs,
            "bands": [{"threshold": b.threshold, "k": b.table.k, "digest": b.table.digest[:12]} for b in P.bands],
            "full_run": r, "T_document": t_doc, "conversion_agrees": t_doc == float(r["T"]), "trust": TRUST}


# ------------------------------------------------------------------------------------------- Lean inputs
def submission_policy(path: Path) -> tt.Policy:
    _, pols = load(path)
    if len(pols) != 1:
        raise StarterError(f"{path}: expected one policy, found {len(pols)}")
    policy = pols[0]
    # Keep in sync with the hosted intake and the publisher's results contract.
    if len(policy.bands) > 256:
        raise StarterError(f"{path}: a submission may contain at most 256 bands; found {len(policy.bands)}")
    for band in policy.bands:
        if not 2 <= band.table.k <= 8:
            raise StarterError(f"{path}: band at threshold {band.threshold} has k = {band.table.k}; submissions take 2 <= k <= 8")
    return policy


def lean_input(path: Path) -> Dict[str, Any]:
    """The policy check / PhaseTripleProduct harness input: mode, name, bands {threshold, k, point_order, operations} (as the site's
    harness/sto_policy_verify.to_mirror; nothing else is copied, so a declared ladder never reaches Lean)."""
    P = submission_policy(path)
    return {"name": str(P.name)[:200], "mode": P.mode,
            "bands": [{"threshold": b["threshold"], "k": b["k"], "point_order": b["point_order"], "operations": b["operations"]}
                      for b in P.to_json()["bands"]]}


def template(path: Path, lib: Optional[str] = None, forshor: Optional[Path] = None) -> str:
    import policy_to_template as ptt
    policy = submission_policy(path)
    try:
        name = ptt.library_of(forshor) if forshor else (lib or ptt.DEFAULT_LIB)
        return ptt.template_text(policy, name)
    except ptt.TemplateError as e:
        raise StarterError(f"cannot write a ForShor policy: {e}")


# ------------------------------------------------------------------------------------------------ report
def _f(x) -> str:
    return "-" if x is None else f"{x:,.0f}" if isinstance(x, (int, float)) else str(x)


def show(r: Dict[str, Any]) -> None:
    print(f"{r['input']}: n = {r['n']}, delta = {r['delta']}")
    if r.get("widths_notice"):
        print("  NOTICE  " + r["widths_notice"])
    for p in r.get("policies", []):
        b = p["board"]
        print(f"\n{p['name'] or '(unnamed)'} · {p['mode']} · bands " +
              ", ".join(f"{x['threshold']}:k{x['k']}" for x in p["bands"]))
        print(f"  board (exact widths)     {_f(b['T'])} T   ladder {b['ladder_str']}")
        print(f"                           rotations {_f(b['rotations'])} x {b['ts_per_rotation']:.4f} T, "
              f"Toffoli-class {_f(b['toffoli_class'])} x 4 T; engine 2 {_f(b['engine2_T'])} T "
              f"({'agree' if b['engines_agree'] else 'DISAGREE'})")
        print(f"                           Q {_f(b['qubits'])} qubits ({b['qubits_label']}); score Q x T = {b['score']:.6e}"
              + (f"; static layout {_f(b['qubits_static_layout'])}" if "qubits_static_layout" in b else ""))
        _show_rank(b.get("rank_on_board"), "this board")
        s = p["scan_cross_check"]
        print(f"  cross-check (scanned)    {_f(s['T'])} T   ladder {s['ladder_str']}" +
              (f"   Lean static qubits {_f(s['qubits_lean_static'])}" if "qubits_lean_static" in s else ""))
        if "full_run" in p:
            _show_full(p["full_run"])
    if "T_document" in r:
        print("  bands " + ", ".join(f"{x['threshold']}:k{x['k']}" for x in r["bands"]))
        if not r["fixed_values_match_forshor"]:
            print("  WARNING fixed values differ from ForShor's current document: " + "; ".join(r["fixed_value_differences"]))
        _show_full(r["full_run"])
        print(f"  document priced from its own IR: {_f(r['T_document'])} T "
              f"({'agrees' if r['conversion_agrees'] else 'DIFFERS'} with the converted policy)")
    print("\n" + r["trust"])


def _show_rank(r: Optional[Dict[str, Any]], what: str) -> None:
    if r:
        print(f"                           would rank {r['rank']} of {r['of']} on {what} (best score then {r['best_score']:.6e}; "
              f"board snapshot {r['snapshot']}; the page shows the live board)")


def _show_full(f: Dict[str, Any]) -> None:
    print(f"  full order finding       single run {_f(f['T'])} T, Q {_f(f['qubits'])} qubits ({f.get('qubits_label', QUBITS_LABEL)}; "
          f"static layout {_f(f.get('qubits_static_layout'))}), score Q x T = {f['score']:.6e}, work width {f.get('work_width')}")
    _show_rank(f.get("rank_on_board"), "the full order-finding board")
    if f.get("forshor_check") and not f["forshor_check"]["possible"]:
        print("                           NOTE " + f["forshor_check"]["note"])
    print(f"                           main ladder {f['ladder_str']} ({f.get('ir', '')})")
    if "end_to_end" in f:
        print(f"                           trials {f['trial_count']} -> end to end {f['end_to_end']:.6e} T")
    print(f"                           cross-checks (not the score): Lean grid leaf {_f(f['lean_grid_leaf_T'])} T, "
          f"literal Lean (Cuccaro) {_f(f['literal_lean_T'])} T")
    if "forshor_scan_ir" in f:
        s = f["forshor_scan_ir"]
        print(f"                           cross-check, upstream ForShor IR (scanned widths, no split): {_f(s['T'])} T, "
              f"{_f(s['qubits'])} qubits, ladder {s['ladder_str']}")


# ------------------------------------------------------------------------------------------------ examples
def example_list() -> List[Dict[str, Any]]:
    return json.loads((EXAMPLES / "examples.json").read_text())["examples"]


def run_example(e: Dict[str, Any]) -> Dict[str, Any]:
    p = EXAMPLES / e["file"]
    return forshor_doc(p) if e["kind"] == "forshor-doc" else price(p, full=e.get("full", True))


def _close(a, b, path="") -> List[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = [f"{path}.{k}: missing" for k in b if k not in a]
        for k in b:
            if k in a:
                out += _close(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        return [m for i, (x, y) in enumerate(zip(a, b)) for m in _close(x, y, f"{path}[{i}]")]
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-12):
            return []
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


IGNORED_IN_CHECK = ("trust",)


def examples(check: bool, write: bool = False) -> int:
    bad = 0
    for e in example_list():
        r = run_example(e)
        if write:                    # the pack builder pins the outputs (dist/build_starter_pack.py --pin)
            (EXAMPLES / "expected").mkdir(exist_ok=True)
            (EXAMPLES / "expected" / e["expected"]).write_text(json.dumps(r, indent=1) + "\n")
        show(r)
        if check:
            want = json.loads((EXAMPLES / "expected" / e["expected"]).read_text())
            diff = _close({k: v for k, v in r.items() if k not in IGNORED_IN_CHECK},
                          {k: v for k, v in want.items() if k not in IGNORED_IN_CHECK})
            print(f"  => {'MATCHES' if not diff else 'DIFFERS FROM'} examples/expected/{e['expected']}")
            for d in diff[:20]:
                print("     " + d)
            bad += bool(diff)
        print("-" * 100)
    if check:
        print("every example matches its pinned output" if not bad else f"{bad} example(s) differ")
    return 1 if bad else 0


# ---------------------------------------------------------------------------------------------------- main
def _emit(obj: Any, out: Optional[str]) -> None:
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=1) + "\n"
    if out:
        Path(out).write_text(text)
        print(f"wrote {out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("price", help="price a PP or PTP banded policy (exact widths; PP: full run too)")
    a1.add_argument("policy", type=Path)
    a1.add_argument("--n", type=int, default=N)
    a1.add_argument("--delta", type=float, default=DEFAULT_DELTA)
    a1.add_argument("--no-full", action="store_true", help="skip the full order-finding run (PP)")
    a1.add_argument("--json", help="also write the result as JSON here")
    a2 = sub.add_parser("forshor-doc", help="price ForShor's printed policy.json (full run)")
    a2.add_argument("doc", type=Path)
    a2.add_argument("--delta", type=float, default=DEFAULT_DELTA)
    a2.add_argument("--json")
    a3 = sub.add_parser("template", help="a PP policy as the submission file Policy.lean (ForShor's PolicyTemplate format)")
    a3.add_argument("policy", type=Path)
    a3.add_argument("--out")
    g3 = a3.add_mutually_exclusive_group()
    g3.add_argument("--lib", choices=("ForShor",), help="ForShor's Lean library name (ForShor; the only one at the pinned commit)")
    g3.add_argument("--forshor", type=Path, help="your ForShor checkout; the library name is read off it")
    a4 = sub.add_parser("lean-input", help="the policy check / PhaseTripleProduct harness input document")
    a4.add_argument("policy", type=Path)
    a4.add_argument("--out")
    a5 = sub.add_parser("examples", help="price every example (--check: compare with examples/expected/)")
    a5.add_argument("--check", action="store_true")
    a5.add_argument("--write-expected", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    try:
        if a.cmd in {"price", "forshor-doc"} and not (math.isfinite(a.delta) and 1e-15 <= a.delta < 1):
            raise StarterError("delta must be in [1e-15, 1)")
        if a.cmd == "price":
            if not 16 <= a.n <= 1 << 15:
                raise StarterError("n must be in [16, 32768]")
            r = price(a.policy, a.n, a.delta, full=not a.no_full)
            show(r)
            if a.json:
                _emit(r, a.json)
        elif a.cmd == "forshor-doc":
            try:
                r = forshor_doc(a.doc, a.delta)
            except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError) as e:
                raise StarterError(f"{a.doc}: malformed or unsupported ForShor policy document: {e}") from None
            show(r)
            if a.json:
                _emit(r, a.json)
            return 0 if r["conversion_agrees"] else 1
        elif a.cmd == "template":
            _emit(template(a.policy, a.lib, a.forshor), a.out)
        elif a.cmd == "lean-input":
            _emit(lean_input(a.policy), a.out)
        elif a.cmd == "examples":
            return examples(a.check, a.write_expected)
    except StarterError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
