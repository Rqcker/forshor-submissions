"""The table and policy library of the site: STO archive submissions, the 18 Lean-extracted accepted policies,
the STO reference policies and the DP library. All of it is read from engine/data (copied from
sto_policy_cost/examples on 2026-10-22). Regenerate from source; never hand-copy numbers."""
from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import DATA
from tcost import tables as tt

PAIR, TRIPLE = tt.PAIR, tt.TRIPLE
MODES = (PAIR, TRIPLE)
VERIFIED = {"kernel", "full_verifier"}
#: Directories of engine/data that hold STO submissions (`<id>/operations.json` + `results.json`), oldest source first.
SUBMISSION_KINDS = ("sto_archive", "sto_accepted", "sto_submitted")


@dataclass
class Submission:
    """One STO submission (an archive ID): its tables, by digest, and its provenance."""
    id: str                       # 12-hex short id
    sha: str
    created_at: Optional[str]
    accepted: bool                # among the 18 accepted policies extracted from Lean
    pr: Optional[str]
    ref: Optional[str]
    tables: Dict[str, tt.Table] = field(default_factory=dict)
    weighted: Dict[Tuple[str, int], int] = field(default_factory=dict)
    harness: bool = False         # verified by this site's STO harness (harness/sto_verify.py), not in the archive

    def targets(self) -> List[str]:
        keys = sorted({(T.mode != PAIR, T.k) for T in self.tables.values()})
        return [f"{'PTP' if tri else 'PP'}{k}" for tri, k in keys]


@lru_cache(maxsize=1)
def submissions() -> Tuple[Submission, ...]:
    """The 21 archived submissions, oldest first (`results.json` -> submission.created_at). Tables from the
    Lean-extracted accepted copy and from the archive are merged by digest (they are identical where both exist).
    `sto_submitted` holds the records that `harness/sto_verify.py --install` wrote: Lean-verified by STO's verifier
    and replayed in Python, in the same layout, ordered by the time they were verified."""
    subs: Dict[str, Submission] = {}
    for kind in SUBMISSION_KINDS:
        for f in sorted(glob.glob(str(DATA / kind / "*" / "operations.json"))):
            d = Path(f).parent
            sha = d.name.lstrip("P")
            sid = sha[:12]
            res = json.loads((d / "results.json").read_text())
            meta = res.get("submission") or {}
            s = subs.get(sid)
            if s is None:
                s = subs[sid] = Submission(sid, sha, None, False, None, None)
            if len(sha) > len(s.sha):
                s.sha = sha
            s.created_at = s.created_at or meta.get("created_at")
            s.pr = s.pr or meta.get("pr_number")
            s.ref = s.ref or meta.get("head_ref")
            s.accepted = s.accepted or kind in ("sto_accepted", "sto_submitted")
            s.harness = s.harness or kind == "sto_submitted"
            for r in res.get("results", []):
                s.weighted[(r["mode"], int(r["k"]))] = r["weighted_cost"]
            for t in json.loads(Path(f).read_text())["targets"]:
                T = tt.Table.from_json(t)
                s.tables.setdefault(T.digest, T)
    return tuple(sorted(subs.values(), key=lambda s: (s.created_at or "9999", s.id)))


@lru_cache(maxsize=1)
def table_origin() -> Dict[str, str]:
    """digest -> the archive ID that first submitted that table (oldest created_at); DP tables -> their DP id."""
    out: Dict[str, str] = {}
    for s in submissions():
        for dg in s.tables:
            out.setdefault(dg, s.id)
    for r in personal_tables():
        out.setdefault(r["table"].digest, r["id"])
    for r in kmy_tables():
        out.setdefault(r["table"].digest, r["id"])
    return out


@lru_cache(maxsize=1)
def personal_tables() -> Tuple[dict, ...]:
    """The DP library (examples/personal_tables/tables.json, 182 tables). `status` says how it was checked."""
    out = []
    for e in json.loads((DATA / "personal_tables" / "tables.json").read_text()):
        ops = [[o[0], o[1], o[2], -1 if o[3] is True else 1 if o[3] is False else o[3], o[4]]
               if o[0] == "addScaled" else o for o in e["prog"]]
        T = tt.Table.from_json({"mode": e["mode"], "k": e["k"], "point_order": e["points"], "operations": ops})
        out.append({"id": e["id"], "table": T, "weighted": e["metrics"]["weighted"], "status": e["status"]})
    return tuple(out)


@lru_cache(maxsize=1)
def sto_reference_policies() -> Dict[str, tt.Policy]:
    """STO's reference PP and PTP policies exactly as STO ships them (scripts/fixtures/reference_policies.json of STO;
    the copy in engine/data is unchanged). Their thresholds were set at ForShor's scanned widths."""
    return {P.mode: P for P in tt.load_policies(DATA / "reference_policies.json")}


#: The width at which the reference's thresholds are re-derived (the boards' n).
REFERENCE_N = 2048


def at_exact_thresholds(P: tt.Policy, n: int = REFERENCE_N) -> tt.Policy:
    """The same tables, in the same order, with each band's threshold moved to the width the EXACT descent from n
    reaches (Stage 19, owner's decision 2026-10-29). STO set the thresholds at scanned widths; at exact widths some
    bands are never reached (PP: 2048 -> 364 skips the k = 3 band at 385). A band whose table no longer shrinks the
    width, and every band after it, is dropped."""
    bands, w = [], int(n)
    for b in P.bands:
        w2 = b.table.exact_width(w)
        bands.append(tt.Band(w, b.table))
        if w2 >= w:
            break
        w = w2
    return tt.Policy(mode=P.mode, bands=tuple(bands), name=(P.name or "sto-reference") + " (thresholds at exact widths)")


def reference_policies() -> Dict[str, tt.Policy]:
    """The site's STO reference policies: STO's own tables, thresholds moved to the exact widths
    (`at_exact_thresholds`). STO's shipped thresholds: `sto_reference_policies`."""
    return {m: at_exact_thresholds(P) for m, P in sto_reference_policies().items()}


def _dedup(tables) -> List[tt.Table]:
    seen: Dict[str, tt.Table] = {}
    for T in tables:
        seen.setdefault(T.digest, T)
    return list(seen.values())


def sto_tables(upto: Optional[int] = None) -> List[tt.Table]:
    """All STO tables, optionally only those of the first `upto` submissions (chronological)."""
    subs = submissions() if upto is None else submissions()[:upto]
    return _dedup(T for s in subs for T in s.tables.values())


def champion_tables() -> List[tt.Table]:
    """For each (mode, k), the archived table with the lowest STO weighted cost: the per-target champions of STO
    main (`BestKnown.lean`). Ties go to the first file in the order sto_policy_cost uses (archive dirs sorted by
    name, then the accepted dirs), which reproduces BestKnown.lean (pinned in tests: PP 64,895,163 at n=2048)."""
    best: Dict[Tuple[str, int], Tuple[int, tt.Table]] = {}
    for kind in SUBMISSION_KINDS:
        for f in sorted(glob.glob(str(DATA / kind / "*" / "operations.json"))):
            res = json.loads((Path(f).parent / "results.json").read_text())["results"]
            wc = {(r["mode"], int(r["k"])): r["weighted_cost"] for r in res}
            for t in json.loads(Path(f).read_text())["targets"]:
                T = tt.Table.from_json(t)
                w = wc.get((T.mode, T.k))
                key = (T.mode, T.k)
                if w is not None and (key not in best or w < best[key][0]):
                    best[key] = (w, T)
    return [v[1] for v in best.values()]


def dp_tables() -> List[tt.Table]:
    """DP-library tables that the Lean kernel or STO's full verifier checked (replay-only tables excluded)."""
    return [r["table"] for r in personal_tables() if r["status"] in VERIFIED]


#: The milestones of the slider, in order. "reference" is a fixed policy; the others are libraries that the
#: tcost planner optimises over at each n. PLANNED lists those.
MILESTONES = (
    ("kmy", "KMY benchmark", "Optimal policy over the explicit tables of Kahanamoku-Meyer and Yao (arXiv:2403.18006v4): "
            "PP k = 3; PTP k = 3, 4."),
    ("reference", "STO reference policy", "The reference PP / PTP policy that STO ships (its tables), with each "
                  "threshold moved to the width the exact descent from n = 2048 reaches."),
    ("champions", "Archive champions", "Optimal policy over the per-target champion tables (BestKnown.lean)."),
    ("all_sto", "All-STO optimum", "Optimal policy over every STO table: 21 archived submissions, 18 accepted."),
    ("dp", "+ DP library", "All STO tables plus the Lean-verified DP-library tables."),
)


PLANNED = tuple(m for m in MILESTONES if m[0] != "reference")
KMY_SOURCE = "Kahanamoku-Meyer and Yao, arXiv:2403.18006v4, Appendix 'Parallel sequences'"


@lru_cache(maxsize=1)
def kmy_tables() -> Tuple[dict, ...]:
    """The three explicit KMY tables (transcribed and replay-checked by sto_policy_cost/scripts/kmy_tables.py; the
    PTP k = 4 table carries three documented repairs). IDs: KMY-PP3, KMY-PTP3, KMY-PTP4."""
    out = []
    for t in json.loads((DATA / "kmy_tables" / "operations.json").read_text())["targets"]:
        T = tt.Table.from_json(t)
        out.append({"id": f"KMY-{'PP' if T.mode == PAIR else 'PTP'}{T.k}", "table": T})
    return tuple(out)


def milestone_tables(key: str, mode: str) -> List[tt.Table]:
    if key == "kmy":
        tabs = [r["table"] for r in kmy_tables()]
    elif key == "champions":
        tabs = champion_tables()
    elif key == "all_sto":
        tabs = sto_tables()
    elif key == "dp":
        tabs = _dedup(list(sto_tables()) + dp_tables())
    else:
        raise KeyError(key)
    return [T for T in tabs if T.mode == mode]
