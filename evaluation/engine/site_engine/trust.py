"""What is verified, and how: the trust level of every table, and of every policy built from tables (PLAN_v2 3.3).

Lean proves TABLES (an STO submission is a table generator with proofs; accepted means the verifier built it).
The site's T counts are Python (Qualtran), not proved in Lean. So the honest tag of a number is the weakest trust
level among the tables its policy uses, and the cost is always labelled as counted in Python.

Levels, strongest first:

  lean    Lean-verified, and this site's own tooling saw it: an accepted STO policy whose tables this site's harness
          re-extracted from a Lean build (`engine/data/sto_replay.json`, from `./run.sh replay`), a submission run
          through `harness/sto_verify.py`, a DP-library table with status kernel (Lean kernel) or full_verifier (STO's
          verifier), a table of a ForShor banded policy verified by `harness/forshor_verify.py`.
  ci      Lean-verified by STO's CI when it was submitted (archived result on `submission-results`); the tables are
          replay-checked here, but this site did not re-extract them from Lean. This includes an accepted policy whose
          Lean source is not in STO main, so the replay could not rebuild it (P651fc8ae389d, PLAN_v2 3.6 and 5.1 C6).
  replay  Checked by Python replay only: the KMY transcription and DP-library tables with status `replay`.
  none    Unknown to the site: a pasted table that is not in any library.

A table of a ForShor banded policy that this site's ForShor harness verified (`./run.sh forshor`, or a seeded planner
policy run by `seed_policies verify`: status lean-ok, conversion check passed, ForShor's printed policy equal to the
submitted one) is `lean` whatever its source: ForShor's kernel checked its C1 to C4. This promotes the KMY transcription
KMY-PP3 (seeded policy S707dd51bf951, stock budget), which is otherwise `replay`.

POLICY levels (a banded policy, not a table), strongest first. They are separate from the table levels above and are
never merged into one tag:

  forshor     ForShor policy-verified: ForShor's `ShorPolicySubmission` (C1 to C4 per band, sorted thresholds, the
              circuit-level `policy_submission_correct`) built and printed by this site's ForShor harness.
  sto-mirror  Policy Lean-verified at exact widths (STO mirror / PTP suite): PP through the ShorTableOptimization
              policy mirror (`./run.sh sto-policy`), PTP through the PTP suite (`./run.sh ptp-policy`), at EXACT widths
              since Stage 13: the kernel checked STO's table contract per band, the thresholds, the exact ladder at 2048,
              that the exact widths are the least overflow-free widths, and that the register semantics at the exact
              widths is correct (PP correct_tight, PTP correct_exact). Not circuit-level; PTP's register model is by
              analogy with PP. Weaker than `forshor` (circuit-level, at ForShor's scanned widths). Label renamed
              2026-10-28 (was "Policy algebra Lean-verified (STO mirror)"); the level key `sto-mirror` is unchanged.
  tables      Tables only: no Lean check of the policy as a whole; the tables carry their own level.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Tuple

from . import library as lib
from . import submitted

LEVELS = ("lean", "ci", "replay", "none")
_RANK = {l: i for i, l in enumerate(LEVELS)}
DP_LEAN = lib.VERIFIED                      # {"kernel", "full_verifier"}

NO_TABLES = "No tables (leaf only)"


REPLAY_PIN = lib.DATA / "sto_replay.json"
TABLE_MIRROR_PIN = lib.DATA / "table_sto_mirror_runs.json"   # per-table STO mirror runs (Stage 12, decision A)
MIRROR_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
RECORD_PREFIX = "forshor:"                  # ForShor record ids live in their own namespace (never a table id)


@lru_cache(maxsize=1)
def replayed() -> frozenset:
    """Archive ids (12 hex) of the accepted STO policies this site's harness re-extracted from Lean (PLAN_v2 3.6)."""
    if not REPLAY_PIN.is_file():
        return frozenset()
    doc = json.loads(REPLAY_PIN.read_text())
    return frozenset(r["id"].lstrip("P")[:12] for r in doc["policies"] if r["ok"] and not r["missing"] and not r["extra"])


def _one_band_sha(T) -> str:
    """sha256 of the one-band mirror policy of table T, exactly as harness/sto_table_verify.py writes it."""
    import hashlib
    tj = T.to_json()
    doc = {"name": f"table {T.digest}", "mode": tj["mode"],
           "bands": [{"threshold": 2048, "k": tj["k"], "point_order": tj["point_order"], "operations": tj["operations"]}]}
    return hashlib.sha256((json.dumps(doc, indent=1) + "\n").encode()).hexdigest()


def _mirror_run_ok(r: Dict[str, Any]) -> bool:
    return (r.get("verdict") == "accepted" and bool(r.get("statement_pinned"))
            and set(r.get("axioms") or ["?"]) <= MIRROR_AXIOMS
            and bool(r.get("replay")) and all(m.get("verdict") == "replayed" for m in r["replay"]))


@lru_cache(maxsize=1)
def table_mirror_runs() -> Dict[str, Dict[str, Any]]:
    """digest -> pinned per-table STO mirror run (`harness/sto_table_verify.py`), with `status`: `accepted` (promotes the
    table to `lean`), `stale` (the table's one-band policy no longer hashes to the pinned one) or the run's verdict."""
    if not TABLE_MIRROR_PIN.is_file():
        return {}
    runs = json.loads(TABLE_MIRROR_PIN.read_text()).get("runs", {})
    tabs: Dict[str, Any] = {}
    for s in lib.submissions():
        tabs.update(s.tables)
    for r in list(lib.personal_tables()) + list(lib.kmy_tables()):
        tabs.setdefault(r["table"].digest, r["table"])
    out = {}
    for d, r in runs.items():
        T = tabs.get(d)
        if T is None:
            status = "unknown table"
        elif r.get("site_policy_sha256") != _one_band_sha(T):
            status = "stale"
        elif _mirror_run_ok(r):
            status = "accepted"
        else:
            status = r.get("verdict") or "error"
        out[d] = {**r, "status": status}
    return out


def table_mirror_digests() -> frozenset:
    """Tables whose own one-band STO mirror run passed (kernel-checked STO table contract): level `lean`."""
    return frozenset(d for d, r in table_mirror_runs().items() if r["status"] == "accepted")


def submission_level(s, use_table_mirror: bool = True) -> str:
    """`lean` if this site saw the tables come out of Lean (its harness, or the replay of an accepted policy), or if
    every one of its tables is `lean` by content (another lean source, or its own STO mirror run); otherwise `ci`:
    Lean-verified by STO's CI when submitted, replay-checked here."""
    if s.harness or (s.accepted and s.id in replayed()):
        return "lean"
    if use_table_mirror and s.tables and all(level_of_digest(d) == "lean" for d in s.tables):
        return "lean"
    return "ci"


@lru_cache(maxsize=1)
def id_levels() -> Dict[str, str]:
    """Library id -> level, for every archive ID, DP id and KMY id the site knows."""
    out: Dict[str, str] = {}
    for s in lib.submissions():
        out[s.id] = submission_level(s)
    for r in lib.personal_tables():
        out[r["id"]] = "lean" if r["status"] in DP_LEAN else "replay"
    for r in lib.kmy_tables():
        out[r["id"]] = "replay"
    for r in submitted.records():                 # ForShor banded policies verified by this site's harness
        out[RECORD_PREFIX + r["id"]] = "lean"
    promoted = set(submitted.table_digests())     # tables kernel-checked by ForShor in a verified policy (KMY-PP3)
    promoted |= table_mirror_digests()            # tables kernel-checked by their own one-band STO mirror run
    for r in list(lib.personal_tables()) + list(lib.kmy_tables()):
        if r["table"].digest in promoted:
            out[r["id"]] = "lean"
    return out


def clear() -> None:
    id_levels.cache_clear()
    replayed.cache_clear()
    table_mirror_runs.cache_clear()


def level_of_id(table_id: str) -> str:
    return id_levels().get(table_id, "none")


def level_of_digest(digest: str, use_table_mirror: bool = True) -> str:
    """The level of a table by content. A table that appears in several sources gets the strongest of them.
    `use_table_mirror=False` ignores the per-table STO mirror pin (the level the other checkers give)."""
    best = "none"
    for s in lib.submissions():
        if digest in s.tables:
            lv = submission_level(s, use_table_mirror=False)
            if _RANK[lv] < _RANK[best]:
                best = lv
    for r in lib.personal_tables():
        if r["table"].digest == digest:
            lv = "lean" if r["status"] in DP_LEAN else "replay"
            if _RANK[lv] < _RANK[best]:
                best = lv
    for r in lib.kmy_tables():
        if r["table"].digest == digest and _RANK["replay"] < _RANK[best]:
            best = "replay"
    if digest in submitted.table_digests():       # kernel-checked C1 to C4 by ForShor's verifier
        best = "lean"
    if use_table_mirror and digest in table_mirror_digests():   # kernel-checked STO contract, one-band mirror run
        best = "lean"
    return best


#: Checker names (the page shows these strings): which tool verified a table at its level.
CHECKERS = {
    "sto-ci": "STO CI (archived result)",
    "sto-replay": "this site's STO replay (re-extracted from a Lean build)",
    "sto-harness": "this site's STO harness",
    "dp-lean": "DP library: Lean kernel / STO full verifier",
    "forshor": "ForShor policy run on this site (C1 to C4 kernel-checked)",
    "sto-mirror": "STO mirror, one-band PolicyTable.check (kernel-replayed)",
    "python-replay": "Python replay only",
}


def checkers_of_digest(digest: str) -> List[str]:
    """Every checker that verified this table (keys of CHECKERS), strongest evidence first."""
    out: List[str] = []
    for s in lib.submissions():
        if digest in s.tables:
            if s.harness:
                out.append("sto-harness")
            if s.accepted and s.id in replayed():
                out.append("sto-replay")
            out.append("sto-ci")
    for r in lib.personal_tables():
        if r["table"].digest == digest:
            out.append("dp-lean" if r["status"] in DP_LEAN else "python-replay")
    for r in lib.kmy_tables():
        if r["table"].digest == digest:
            out.append("python-replay")
    if digest in submitted.table_digests():
        out.append("forshor")
    if digest in table_mirror_digests():
        out.append("sto-mirror")
    order = list(CHECKERS)
    return sorted(dict.fromkeys(out), key=order.index)


def checker_text(digest: str) -> str:
    cs = checkers_of_digest(digest)
    return "; ".join(CHECKERS[c] for c in cs) if cs else "no checker (pasted)"


def aggregate(levels: Iterable[str]) -> Dict[str, Any]:
    """The weakest level and the counts. An empty list (the leaf alone) has nothing to verify: `lean`."""
    ls = list(levels)
    counts = {l: ls.count(l) for l in LEVELS}
    weakest = max(ls, key=_RANK.__getitem__) if ls else "lean"
    return {"level": weakest, "counts": counts, "total": len(ls)}


def label_ids(label: str) -> List[str]:
    """The table ids of a point's label (`a + b + c`, or `leaf`)."""
    return [t.strip() for t in (label or "").split("+") if t.strip() and t.strip() != "leaf"]


def of_label(label: str) -> Dict[str, Any]:
    return aggregate(level_of_id(t) for t in label_ids(label))


def of_digests(digests: Iterable[str]) -> Dict[str, Any]:
    return aggregate(level_of_digest(d) for d in dict.fromkeys(digests))


def tag(agg: Dict[str, Any]) -> Tuple[str, str]:
    """(text, kind) for the page: kind is `ok`, `warn` or `bad`."""
    c, n = agg["counts"], agg["total"]
    if n == 0:
        return NO_TABLES, "ok"
    lvl = agg["level"]
    if lvl == "lean":
        return "Tables Lean-verified", "ok"
    if lvl == "ci":
        return f"Tables Lean-verified ({c['ci']} of {n} by STO CI archive)", "ok"
    if lvl == "replay":
        return f"{c['replay']} of {n} tables replay-checked only (not Lean)", "warn"
    return (f"{c['none']} of {n} tables unknown: not verified", "warn") if c["none"] < n else ("pasted: not verified", "warn")


#: The pricing rule (Stage 12, decision C as revised by the owner): T is Qualtran's; Lean proves structure, widths
#: and tables; Lean's own gate census is a labelled cross-check. One source of wording for the panel and the docs.
PRICING_TEXT = ("The T count is the Qualtran costing of ForShor's IR (Lean -> IR -> dictionary -> Qualtran bloqs): "
                "Qualtran/Gidney adders (Add and Subtract, 4(w - 1) T), the Qualtran digital-product leaf for "
                "PhaseProduct (the digital triple-product leaf for PhaseTripleProduct; its grid leaf is a labelled cross-check), and rotations at the Ross-Selinger price "
                "3.075 log2(1/eps) + 0.86 T. Lean proves the structure, the widths and the tables. Lean's own gate census "
                "(Cuccaro adders, the grid leaf) is shown as a labelled cross-check, not the score.")


def explain() -> Dict[str, str]:
    """The text the page shows for each level (one source of wording)."""
    return {
        "lean": "Lean-verified, by one of these checkers (each table names its own): an accepted STO policy whose tables "
                "this site re-extracted from a Lean build (STO replay), a submission run through this site's STO harness, "
                "a DP table with status kernel or full_verifier, a table of a banded policy that passed ForShor's policy "
                "harness here (C1 to C4 kernel-checked; KMY-PP3), or a table whose own one-band policy passed the STO "
                "policy mirror (the kernel reduced PolicyTable.check, which is STO's table contract, and the modules were "
                "kernel-replayed; this is how 50 of the 51 former replay-only or STO-CI-only tables, KMY-PTP3/4 among "
                "them, are Lean-verified since 2026-10-28).",
        "ci": "Lean-verified by STO's CI when it was submitted (archived result). Replay-checked here; not re-extracted "
              "from Lean, and not (yet) run through the STO mirror.",
        "replay": "Checked by Python replay only (no Lean run on this site has checked it).",
        "none": "Not in any library: a pasted table. Its operations replay and pass the identity check, but nothing proves it in Lean.",
        "cost": "The T count is counted in Python by Qualtran. Lean proves the tables, not the count.",
        "pricing": PRICING_TEXT,
    }


# ---------------------------------------------------------------------------------------------- policy levels
POLICY_LEVELS = ("forshor", "sto-mirror", "tables")

#: The tag of each policy level (one source of wording; the page shows these strings as they are).
POLICY_TAGS = {
    "forshor": "ForShor policy-verified",
    "sto-mirror": "Policy Lean-verified at exact widths (STO mirror / PTP suite)",
    "tables": "Tables only: policy not Lean-verified",
}
POLICY_KIND = {"forshor": "ok", "sto-mirror": "mid", "tables": "warn"}


def explain_policy() -> Dict[str, str]:
    """What each policy level proves and does not (the policy panel and the boards show this text)."""
    return {
        "forshor": "ForShor policy-verified: ForShor's ShorPolicySubmission built on this site's ForShor harness "
                   "(ForShor 10fe620, Lean v4.36.0-rc1, since 2026-10-28; the earlier ForShor-main runs are superseded): "
                   "Lean's kernel checked every band's table (C1 to C4) and the thresholds, and ForShor's "
                   "policy_submission_correct covers the compiled circuit, at ForShor's scanned widths. The IR the price "
                   "reads is matched to the circuit at sampled widths only. PhaseProduct only.",
        "sto-mirror": "Policy Lean-verified at exact widths (STO mirror / PTP suite): Lean's kernel checked, at the "
                      "EXACT widths this board prices, that the register semantics is correct (the wrapping fixed-width "
                      "registers give φ·∏xs on operands that fit: PP correct_tight, PTP correct_exact), that the exact "
                      "widths are the least overflow-free widths (no reachable register value or child operand overflows "
                      "below them; the policy's exact ladder at n = 2048 is proved by the kernel), that every band's table "
                      "satisfies STO's table contract, and that the thresholds decrease; the recursive evaluation equals "
                      "φ·∏xs at every width (PhaseProduct φab, PhaseTripleProduct φabc). PhaseProduct is checked by "
                      "ShorTableOptimization's policy mirror, PhaseTripleProduct by the PTP suite. It is not a "
                      "circuit-level check (no gate-level equivalence with the priced Qualtran circuit or with ForShor's "
                      "lowering; the +1 root and reserve qubits are not modelled), and PhaseTripleProduct's register model "
                      "is by analogy with PhaseProduct. Weaker than ForShor policy-verified (circuit-level, at ForShor's "
                      "scanned widths).",
        "tables": "Tables only: nothing in Lean checks this policy as a whole. Its tables carry their own trust level.",
    }


def policy_trust(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The policy trust of a seeded record (`seed_policies`): the strongest policy level its pinned runs support, with
    the tag, the kind (`ok` / `mid` / `warn`), the explanation and what each harness said."""
    lean, mirror = rec.get("lean") or {}, rec.get("sto_mirror") or {}
    if lean.get("policy_verified"):
        level = "forshor"
    elif mirror.get("status") == "accepted":
        level = "sto-mirror"
    else:
        level = "tables"
    text = POLICY_TAGS[level]
    if level == "forshor" and lean.get("budget") == "override":
        text += " (site override of the heartbeat cap)"
    explain = explain_policy()[level]
    if level == "forshor" and lean.get("variant") == "replanned":
        # Stage 15: a PP seed re-planned under exact widths that passed ForShor's harness as re-planned
        text += " (at ForShor's scanned widths)"
        explain += (" This is the policy as re-planned under exact widths (Stage 13): ForShor checked its tables and "
                    "thresholds and its circuit at ForShor's SCANNED widths; the PhaseProduct board prices it at EXACT "
                    "widths, which ForShor does not check (the STO mirror run covers the exact widths: register semantics, "
                    "least overflow-free widths, not circuit-level).")
    if level == "forshor" and lean.get("forshor"):
        explain += f" Run: {lean['forshor']}."
    return {"level": level, "tag": text, "kind": POLICY_KIND[level], "text": explain,
            "forshor": {"status": lean.get("status"), "budget": lean.get("budget"), "reason": lean.get("reason"),
                        "variant": lean.get("variant"), "run_on": lean.get("forshor"), "superseded": lean.get("superseded")},
            "sto_mirror": {k: mirror.get(k) for k in ("status", "stage", "reason", "seconds", "peak_gb", "ladder_str",
                                                      "ladder_matches_site")}}
