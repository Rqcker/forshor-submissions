"""Toom-Cook table catalogs for policy planning: ForShor's standard tables and the k-sequence DP catalog.

The DP catalog (`fixtures/dp_tables/`) is the ForShor PhaseProduct leaderboard champions
(k = 2..16, points from `champions.tsv`) plus the Plan A tables (one-row / paired / sequential,
k = 2..9, points and polished programs from `library.json`). Its `candidates.json` writes ops as lists
(`["addScaled", dst, src, ±1, shift]`, `["shiftL", i, n]`, ...); they are converted here to the
`forshor.policy-submission/v1` op dicts `ToomCookTable` reads, with the same point conversion
`make_policy_template.py` uses for the Lean template (`inf` -> `frac 0`, `±1/b` -> `frac ±b`).

Every table is checked before use: ForShor's cheap conditions (`ToomCookTable.check`: C1, C2, C4,
checkpoint count) and the Toom-Cook identity itself, exactly over the integers -- for random signed
limbs and several limb widths m, sum_l c_l(2^m) X_l Z_l == x z, where X_l, Z_l are the slots the
l-th `phaseProduct` reads. That identity is what the phase product's correctness rests on, so a
mis-converted table is refused rather than priced. `admissible_by_kernel` is NOT established here:
ForShor's intake decides C1-C4 with `decide +kernel` when a policy using the table is submitted.
"""
from __future__ import annotations

import json
import os
import random
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .mirrors import width as wmirror
from .policy import PolicyError, ToomCookTable

#: The DP planner's tables ship in `fixtures/dp_tables/`; `ONECLICK_DP_TABLES` points at another copy.
DP_TABLES = Path(os.environ.get("ONECLICK_DP_TABLES")
                 or Path(__file__).resolve().parent.parent / "fixtures" / "dp_tables")


def _point(s: str) -> Dict[str, Any]:
    if s == "inf":
        return {"kind": "frac", "m": 0}
    if "/" in s:
        num, den = s.split("/")
        if abs(int(num)) != 1:
            raise PolicyError(f"point {s}: only ±1/b is a ForShor frac point")
        return {"kind": "frac", "m": (-1 if num.startswith("-") else 1) * int(den)}
    return {"kind": "int", "z": int(s)}


def _op(o: Sequence[Any]) -> Dict[str, Any]:
    t = o[0]
    if t == "phaseProduct":
        return {"op": t, "i": int(o[1])}
    if t == "addScaled":
        neg = o[3] is True or o[3] == -1
        if not (neg or o[3] is False or o[3] == 1):
            raise PolicyError(f"addScaled sign {o[3]!r}")
        return {"op": t, "dst": int(o[1]), "src": int(o[2]), "negSrc": bool(neg), "shift": int(o[4])}
    if t in ("shiftL", "shiftR"):
        return {"op": t, "i": int(o[1]), "n": int(o[2])}
    if t == "negate":
        return {"op": t, "i": int(o[1])}
    raise PolicyError(f"unknown op {o!r}")


def verify_identity(T: ToomCookTable, ms: Sequence[int] = (3, 5, 8), trials: int = 6, seed: int = 0) -> None:
    """Exact Toom-Cook identity check (see module docstring); raises PolicyError on failure."""
    states = wmirror.run_rows(T.k, T.ops)
    checkpoints = [(op["i"], states[j]) for j, op in enumerate(T.ops) if op["op"] == "phaseProduct"]
    rng = random.Random(seed)
    for m in ms:
        c = [T.coeff(l, m) for l in range(len(checkpoints))]
        for _ in range(trials):
            xs = [rng.randint(-(1 << m), (1 << m) - 1) for _ in range(T.k)]
            zs = [rng.randint(-(1 << m), (1 << m) - 1) for _ in range(T.k)]
            x = sum(v << (m * j) for j, v in enumerate(xs))
            z = sum(v << (m * j) for j, v in enumerate(zs))
            acc = Fraction(0)
            for cl, (i, st) in zip(c, checkpoints):
                acc += cl * sum(a * b for a, b in zip(st[i], xs)) * sum(a * b for a, b in zip(st[i], zs))
            if acc != x * z:
                raise PolicyError(f"table {T.name}: Toom-Cook identity fails at m={m} ({acc} != {x * z})")


def checked(T: ToomCookTable) -> ToomCookTable:
    T.check()
    verify_identity(T)
    return T


def _champion_points(tables: Path) -> Dict[int, List[Dict[str, Any]]]:
    out = {}
    for line in (tables / "champions.tsv").read_text().splitlines():
        f = line.split("\t")
        if f and f[0] == "PP":
            out[int(f[1])] = [_point(s) for s in f[3].split(";")]
    return out


def load_dp_catalog(tables: Path = DP_TABLES) -> List[ToomCookTable]:
    """The 39 DP candidate tables, converted and checked; name = the DP's `policyId`."""
    cands = json.loads((tables / "candidates.json").read_text())
    champ = _champion_points(tables)
    lib = json.loads((tables / "library.json").read_text())
    out = []
    for c in cands:
        pid, k = c["policyId"], int(c["k"])
        ops = [_op(o) for o in c["operations"]]
        if pid.startswith("champion-"):
            pts = champ[k]
        else:
            _, cand, _k = pid.split("-")
            e = next(e for e in lib if e["mode"] == "PhaseProduct" and int(e["k"]) == k and e["cand"] == cand)
            prog = e["prog"] if isinstance(e["prog"], list) else json.loads(e["prog"].replace("'", '"')
                                                                           .replace("False", "false").replace("True", "true"))
            if [_op(o) for o in prog] != ops:
                raise PolicyError(f"{pid}: library.json program differs from candidates.json")
            raw = e["points"] if isinstance(e["points"], list) else json.loads(e["points"].replace("'", '"'))
            pts = [{"kind": "int", "z": int(v)} if kd == "int" else {"kind": "frac", "m": int(v)} for kd, v in raw]
        out.append(checked(ToomCookTable.from_json(pid, {"k": k, "points": pts, "ops": ops})))
    return out


def load_standard(root: Path, ks: Sequence[int] = (2, 3, 4)) -> List[ToomCookTable]:
    """ForShor's standard tables (`artifacts/policy_figures/tables/table_k*.standard.json`)."""
    from .policy_planner import load_catalog
    files = [str(root / "artifacts" / "policy_figures" / "tables" / f"table_k{k}.standard.json") for k in ks]
    return [checked(T) for T in load_catalog(files)]
