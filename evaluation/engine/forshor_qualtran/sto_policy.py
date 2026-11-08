"""From a ShorTableOptimization policy to a Lean-To-Qualtran `PhasePolicy` (PhaseProduct mode).

ShorTableOptimization (`tcost` format, `Policy.to_json`, archived `operations.json` targets) writes a
table as `point_order` strings ('0', 'inf', '-4', '1/2') and `operations` arrays
(`["addScaled", dst, src, +-1, shift]`, ...). The L2Q `ToomCookTable` holds the same data as ForShor
`Point`s and op dicts. The DP-catalog readers convert the points and ops
(`table_catalog.point_from_sto` / `op_from_sto`; the catalog comes from STO). `table_catalog.checked`
is a gate for each table: the cheap ForShor checks C1/C2/C4 plus the exact Toom-Cook identity. Thus the
module rejects a point order that does not agree with the consumption order of the program. It does not
give such a table an incorrect price without a message.

PhaseTripleProduct has no L2Q bloq. Use `tcost` to price it (`tcost.bloqs`, `tcost.score`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Union

from .policy import PhasePolicy, PolicyBand, PolicyError, ToomCookTable
from .table_catalog import checked, op_from_sto, point_from_sto

PAIR = "PhaseProduct"


def table_from_sto(d: Dict[str, Any], name: str) -> ToomCookTable:
    pts = [point_from_sto(p) for p in d["point_order"]]
    return checked(ToomCookTable.from_json(name, {"k": int(d["k"]), "points": pts,
                                                  "ops": [op_from_sto(o) for o in d["operations"]]}))


def policy_from_sto(src: Union[str, Path, Dict[str, Any]]) -> PhasePolicy:
    """Convert a tcost policy (JSON path or dict; a `{"policy": ...}` wrapper is permitted) to a `PhasePolicy`."""
    d = json.loads(Path(src).read_text()) if isinstance(src, (str, Path)) else src
    if isinstance(d.get("policy"), dict):
        d = d["policy"]
    mode = str(d.get("mode", PAIR))
    if mode != PAIR:
        raise PolicyError(f"L2Q PolicyPhaseProduct prices {PAIR} only, got {mode!r} (use tcost for it)")
    bands, names = [], {}
    for i, b in enumerate(d["bands"]):
        tab = dict(b.get("table", b))
        if str(tab.get("mode", mode)) != mode:
            raise PolicyError(f"band {i}: table mode {tab.get('mode')!r} != policy mode {mode!r}")
        T = table_from_sto(tab, f"band{i}-k{int(tab['k'])}")
        key = (T.k, T.points, T.ops_frozen)
        T = names.setdefault(key, T)           # one table for more than one band keeps one name
        bands.append(PolicyBand(int(b["threshold"]), T))
    return PhasePolicy(tuple(bands), name=str(d.get("name", "sto-policy")))
