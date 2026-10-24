r"""Width-indexed lowering policies: the ForShor `ShorLoweringPolicy`, read from its IR.

ForShor (`Framework/Policy.lean`, `PhaseProduct/Lowering/PlanBuilders.lean`) now proves that the
phase-product lowering is correct for a **policy**. A policy is a list of `(threshold, table)` bands
with strictly decreasing thresholds. The operand width of a node selects the table at that node
(`choose n` = the first band with `threshold ≤ n`; `none` is the schoolbook leaf). Thus different
levels of one recursion can use different Toom arities `k`: Toom-3 at the top, Karatsuba at
lower levels, the leaf at the bottom. A single table is the one-band policy at threshold 0
(`constPolicy`).

The IR extractor of ForShor still emits one `forshor.ir/v1` document for each *table* (it reflects
`Policy.signedPlanOf` at `constPolicy`). This module composes these band documents into one
document for the policy. It mirrors the policy dispatcher exactly:

    Policy.signedPlanOf P phi x z hws (P.choose (max xw zw)):
      none   -> signedBase                            (naive leaf)
      some T -> if nextWidth T (xw, zw) < max xw zw
                  then signedStep T  (children: Policy.signedPlanOf at the child width)
                  else signedBase

* The **dispatcher** (`phase_product`, `cphase_product`) is a guard chain on
  `threshold_i <= max(xw, zw)`, widest band first. If no guard is true, it goes to the leaf call.
  This is `choose` (`List.find?`) written as `Node.cond`s.
* The extracted `phase_product` body of each band becomes `phase_product@<band>`. Its recursive
  `call phase_product` still points at the *dispatcher*. Thus a child uses the policy again at
  its own width, and it can use a different table (`childInputSize`/`childWorkspace` in
  `CanonicalStep`).
* The module resolves the opaque functions for each template (`ir_v1.Template.table`):
    - `nextWidth`, `coeff(l, m)` belong to the **table of the band** (the extractor specialised
      them to that table).
    - The **policy-level** recurrence `Policy.reserveNeed` (`Compiler/PolicyWorkspace.lean`)
      resolves `reserveNeed_x`, `reserveNeed_z`. This point is load-bearing. A band body gives
      the reserve of each child the size `(nextWidth - w_j) + reserveNeed(next, next)`. With a
      policy, the need of the child comes from *the table of the child*
      (`requiredXChildReserve P T`), not from the table of the parent. If we resolve it for each
      table, the workspace of each child gets the size for the incorrect recursion.

The module composes only the phase-product templates. The ForShor QFT and Shor lowerings still
use one table (`QFT/Proofs/Lowering/*` instantiate `constPolicy`). Thus the module removes the
`qft`, `shor_gate` and `shor` templates of the band documents. It does not point them at the
policy dispatcher.

`tests/test_policy_phase_product.py` gives the checks and how they work. Nothing here is a theorem.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from . import ir_v1, schemas
from .mirrors import width as wmirror

POLICY_SCHEMA = schemas.POLICY

#: The templates whose body is the recursive step of each table, and the dispatcher name that each keeps.
STEP_TEMPLATES = ("phase_product", "cphase_product")
#: Leaf templates that do not depend on the table. All bands share them
#: (a check makes sure that they are structurally equal).
LEAF_TEMPLATES = ("naive_leaf", "naive_cleaf")
#: The opaques that the table of the band resolves / that the policy-level recurrence resolves.
TABLE_OPAQUES = ("nextWidth",)
POLICY_OPAQUES = ("reserveNeed_x", "reserveNeed_z")


class PolicyError(ValueError):
    pass


def q(k: int) -> int:
    """`q k = 2k - 1`: one interpolation point for each product coefficient."""
    return 2 * k - 1


# ============================================================================ tables
def _point_key(p: Dict[str, Any]) -> Tuple[str, int]:
    if p.get("kind") == "int":
        return ("int", int(p["z"]))
    if p.get("kind") == "frac":
        return ("frac", int(p["m"]))
    raise PolicyError(f"unknown point {p!r} (expected pointJson: kind int/frac)")


def interp_entry(k: int, p: Tuple[str, int], j: int) -> Fraction:
    """`interpEntry k p j` (Framework/ToomCookTable.lean): `z^j` or `c^(q k - 1 - j)`."""
    kind, v = p
    if kind == "int":
        return Fraction(v) ** j
    return Fraction(v) ** (q(k) - 1 - j)


def _freeze_op(o: Dict[str, Any]) -> Tuple[Tuple[str, Any], ...]:
    return tuple(sorted(o.items()))


@dataclass(frozen=True)
class ToomCookTable:
    """The `ToomCookTable` data `(k, pts, ops)`. ForShor must prove C2–C4 (`Admissible`).
    :meth:`check` does only the cheap, decidable parts again, as a consistency guard."""
    name: str
    k: int
    points: Tuple[Tuple[str, int], ...]
    ops_frozen: Tuple[Tuple[Tuple[str, Any], ...], ...]

    @classmethod
    def from_json(cls, name: str, j: Dict[str, Any]) -> "ToomCookTable":
        return cls(name=name, k=int(j["k"]), points=tuple(_point_key(p) for p in j["points"]),
                   ops_frozen=tuple(_freeze_op(o) for o in j["ops"]))

    @property
    def ops(self) -> List[Dict[str, Any]]:
        return [dict(o) for o in self.ops_frozen]

    def census(self) -> Dict[str, int]:
        """Return the op census of each level, in the vocabulary of `LeanRecursivePhaseProduct`."""
        ops = self.ops
        return {"phaseProduct": sum(o["op"] == "phaseProduct" for o in ops),
                "addScaled": sum(o["op"] == "addScaled" for o in ops),
                "addScaled_negSrc": sum(o["op"] == "addScaled" and bool(o.get("negSrc")) for o in ops),
                "negate": sum(o["op"] == "negate" for o in ops),
                "shift": sum(o["op"] in ("shiftL", "shiftR") for o in ops)}

    def check(self) -> None:
        """Check C1 exactly. Check C2 through pairwise projective distinctness (equivalent, from the
        ForShor `goodToomCookPoints_of_distinct`). Run the program to check C4 and the checkpoint count."""
        if self.k < 2:
            raise PolicyError(f"table {self.name}: k = {self.k} < 2")
        if len(self.points) != q(self.k):
            raise PolicyError(f"table {self.name}: C1 fails, {len(self.points)} points != q k = {q(self.k)}")
        coords = [Fraction(v) if kind == "int" else (None if v == 0 else Fraction(1, v))
                  for kind, v in self.points]
        if len(set(map(str, coords))) != len(coords):
            raise PolicyError(f"table {self.name}: C2 fails, points not projectively distinct")
        states = wmirror.run_rows(self.k, self.ops)
        if states[-1] != states[0]:
            raise PolicyError(f"table {self.name}: C4 fails, program does not return to start")
        if self.census()["phaseProduct"] != q(self.k):
            raise PolicyError(f"table {self.name}: {self.census()['phaseProduct']} checkpoints != q k")

    # ---- the opaque functions of the table
    def next_width(self, wx: int, wz: int) -> int:
        """`RecursivePhaseWorkspace.nextWidth T.ops wx wz`."""
        return _next_width(self.k, self.ops_frozen, wx, wz)

    def immediate_need(self, side: str, wx: int, wz: int) -> int:
        """`immediateXNeed`/`immediateZNeed` (Compiler/Workspace.lean)."""
        return wmirror.immediate_need(self.k, self.ops, wx if side == "x" else wz, wx, wz)

    def coeff(self, l: int, m: int) -> Fraction:
        """`cramerCoeffFromPtsWidth k m pts l`: the `c` with `Σ_l c_l·interpEntry(pts_l, j) = b^j`,
        `b = 2^m`, exact over ℚ."""
        return _coeffs(self.k, self.points, m)[l]


@lru_cache(maxsize=1 << 16)
def _next_width(k: int, ops_frozen, wx: int, wz: int) -> int:
    return wmirror.next_width(k, [dict(o) for o in ops_frozen], wx, wz)


@lru_cache(maxsize=1 << 12)
def _coeffs(k: int, points: Tuple[Tuple[str, int], ...], m: int) -> Tuple[Fraction, ...]:
    n = q(k)
    b = Fraction(2) ** m
    # Mᵀ c = (1, b, …, b^(n-1)), M[i][j] = interpEntry(pts_i, j); exact Gauss–Jordan elimination.
    rows = [[interp_entry(k, points[i], j) for i in range(n)] + [b ** j] for j in range(n)]
    for col in range(n):
        piv = next((r for r in range(col, n) if rows[r][col] != 0), None)
        if piv is None:
            raise PolicyError("interpolation matrix is singular (C2 fails)")
        rows[col], rows[piv] = rows[piv], rows[col]
        pv = rows[col][col]
        rows[col] = [x / pv for x in rows[col]]
        for r in range(n):
            if r != col and rows[r][col] != 0:
                f = rows[r][col]
                rows[r] = [a - f * c for a, c in zip(rows[r], rows[col])]
    return tuple(rows[i][n] for i in range(n))


# ============================================================================ the policy
@dataclass(frozen=True)
class PolicyBand:
    threshold: int
    table: ToomCookTable


@dataclass(frozen=True)
class PhasePolicy:
    """`ShorLoweringPolicy`: the widest band is first, and the thresholds strictly decrease."""
    bands: Tuple[PolicyBand, ...]
    name: str = "policy"

    def __post_init__(self):
        ts = [b.threshold for b in self.bands]
        if any(a <= b for a, b in zip(ts, ts[1:])):
            raise PolicyError(f"thresholds must strictly decrease (`sorted`), got {ts}")
        # ForShor lets one table serve more than one band (for example Karatsuba, Toom-3, Karatsuba again).
        # Thus a name identifies a table. Do not use the same name for a different table.
        seen: Dict[str, ToomCookTable] = {}
        for b in self.bands:
            if seen.setdefault(b.table.name, b.table) != b.table:
                raise PolicyError(f"table name {b.table.name!r} is used for two different tables")

    @classmethod
    def const(cls, table: ToomCookTable) -> "PhasePolicy":
        """`constPolicy T`: one band at threshold 0. This is the current single-table submission."""
        return cls((PolicyBand(0, table),), name=f"const:{table.name}")

    def table(self, name: str) -> ToomCookTable:
        for b in self.bands:
            if b.table.name == name:
                return b.table
        raise PolicyError(f"no band uses table {name!r}")

    def choose(self, n: int) -> Optional[ToomCookTable]:
        """`ShorLoweringPolicy.choose`: return the first band with `threshold ≤ n`. `None` is the leaf."""
        for b in self.bands:
            if b.threshold <= n:
                return b.table
        return None

    def step(self, wx: int, wz: int) -> Optional[Tuple[ToomCookTable, int]]:
        """Return the decision of the dispatcher at `(wx, wz)`: `(T, nextWidth)` if this node recurses.
        Else return `None` (`choose = none`, or the chosen table does not shrink the width: `Stops`)."""
        T = self.choose(max(wx, wz))
        if T is None:
            return None
        nxt = T.next_width(wx, wz)
        return (T, nxt) if nxt < max(wx, wz) else None

    def reserve_need(self, wx: int, wz: int) -> Tuple[int, int]:
        """`Policy.reserveNeed P wx wz` (Compiler/PolicyWorkspace.lean)."""
        return _policy_reserve(self, wx, wz)

    def ladder(self, wx: int, wz: Optional[int] = None) -> List[Tuple[int, Optional[str]]]:
        """Return `[(width, table used there), …]` down to the leaf (table `None`)."""
        wz = wx if wz is None else wz
        out: List[Tuple[int, Optional[str]]] = []
        while True:
            s = self.step(wx, wz)
            out.append((max(wx, wz), None if s is None else s[0].name))
            if s is None:
                return out
            wx = wz = s[1]

    def check(self) -> None:
        for b in self.bands:
            b.table.check()

    def to_json(self) -> Dict[str, Any]:
        return {"schema": POLICY_SCHEMA, "name": self.name, "bands": [
            {"threshold": b.threshold, "table": {
                "name": b.table.name, "k": b.table.k,
                "points": [{"kind": kd, "z": v} if kd == "int" else {"kind": kd, "m": v}
                           for kd, v in b.table.points],
                "ops": b.table.ops}} for b in self.bands]}


@lru_cache(maxsize=1 << 16)
def _policy_reserve(P: PhasePolicy, wx: int, wz: int) -> Tuple[int, int]:
    T = P.choose(max(wx, wz))
    if T is None:
        return (0, 0)
    nxt = T.next_width(wx, wz)
    if not nxt < max(wx, wz):
        return (0, 0)
    cx, cz = _policy_reserve(P, nxt, nxt)
    return (T.immediate_need("x", wx, wz) + T.k * cx, T.immediate_need("z", wx, wz) + T.k * cz)


# ============================================================================ the oracle
class PolicyOracle(ir_v1.Oracle):
    """`IR.Env.opaqueW`/`coeff` for a policy document. The band of the calling template resolves the
    table opaques, and the policy resolves `reserveNeed_*`. Templates without a tag (a plain
    single-table document) use ``default``, which is the only band of a constant policy."""

    def __init__(self, policy: PhasePolicy, default: Optional[str] = None):
        self.policy = policy
        if default is None and len(policy.bands) == 1:
            default = policy.bands[0].table.name
        self.default = default

    def _table(self, tag: Optional[str]) -> Optional[ToomCookTable]:
        name = tag if tag is not None else self.default
        return None if name is None else self.policy.table(name)

    def opaque_w(self, fn, args, table):
        if fn in POLICY_OPAQUES and len(args) == 2:
            need = self.policy.reserve_need(args[0], args[1])
            return need[0] if fn == "reserveNeed_x" else need[1]
        if fn in TABLE_OPAQUES and len(args) == 2:
            T = self._table(table)
            return None if T is None else T.next_width(args[0], args[1])
        return super().opaque_w(fn, args, table)

    def coeff(self, l, m, table):
        T = self._table(table)
        if T is None or not 0 <= l < q(T.k):
            return None
        return T.coeff(l, m)


# ============================================================================ composition
def _calls(node: ir_v1.Tree, name: str) -> List[ir_v1.Tree]:
    """Return all `call name …` nodes in a template body."""
    out: List[ir_v1.Tree] = []

    def go(n):
        tag = n[0]
        if tag == "call":
            if n[1] == name:
                out.append(n)
        elif tag == "seq":
            for b in n[1]:
                go(b)
        elif tag == "adj":
            go(n[1])
        elif tag == "cond":
            go(n[2])
            go(n[3])
        elif tag == "loop":
            go(n[4])
    go(node)
    return out


def _leaf_call(step: ir_v1.Template, leaf: str) -> ir_v1.Tree:
    """Return the base-case call of the step template (the `Naive_SignedPhaseProd` of `signedBase`).
    The dispatcher uses it verbatim as its `choose = none` branch. Thus the two bases are literally the same."""
    calls = _calls(step.body, leaf)
    if not calls or any(c != calls[0] for c in calls):
        raise PolicyError(f"{step.name}: expected one base-case call to {leaf}, found {len(calls)} "
                          "distinct — the extracted template has an unexpected shape")
    return calls[0]


def _check_band_shape(band: str, t: ir_v1.Template, k: int) -> None:
    """Make sure that the step body calls the dispatcher one time for each product coefficient (`q k` children).
    A band document for a different arity fails here. Thus it cannot give an incorrect cost."""
    n = len(_calls(t.body, t.name))
    if n != q(k):
        raise PolicyError(f"band {band}: {t.name} recurses {n} times, table has q k = {q(k)} — "
                          "the IR document does not belong to this table")


def table_sha256(T: ToomCookTable) -> str:
    """Return the sha256 of the canonical JSON `(k, points, ops)` of the table.
    This is its identity, independent of the name."""
    import hashlib
    j = {"k": T.k, "points": [list(p) for p in T.points], "ops": T.ops}
    return hashlib.sha256(json.dumps(j, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _probe_width(T: ToomCookTable, w_max: int = 512) -> Optional[int]:
    """Return the smallest width at which the constant policy of `T` recurses. One level is
    sufficient to run each op of the program of the table."""
    C = PhasePolicy.const(T)
    for w in range(2 * T.k, w_max + 1):
        if C.step(w, w) is not None:
            return w
    return None


def verify_binding(T: ToomCookTable, doc: ir_v1.Doc, *, w: Optional[int] = None) -> Dict[str, Any]:
    """Check that `doc` is really the IR of table `T`.

    The function instantiates `doc` with `constPolicy T` at the smallest width that recurses. Then
    it compares the `LowGate.resources` of the flat circuit (h, x, cnot, toffoli, rz) with the
    prediction of `T` (`PolicyPhaseProduct(constPolicy T).lean_resources`, which comes from the op
    census of `T`, not from the IR). A document from a different table of the same arity composes,
    instantiates and even computes the correct phase, but it runs a different program. This check
    finds that problem. If the values do not agree, it raises `PolicyError`.
    The result is cached for each (table, document)."""
    key = table_sha256(T)
    cache = doc.meta.setdefault("_verified_tables", {})
    if key in cache:
        return cache[key]
    from .bloqs.policy_phase_product import PolicyPhaseProduct    # lazy: the bloq module imports this module
    w = w or _probe_width(T)
    if w is None:
        cache[key] = {"table": T.name, "width": None, "checked": False}
        return cache[key]
    C = PhasePolicy.const(T)
    single = compose(C, {T.name: doc}, verify=False)
    got = ir_v1.lowgate_resources(instantiate_pp(single, C, w, Fraction(1, 8)))
    want = PolicyPhaseProduct(C, w).lean_resources()
    bad = {k: (got[k], want[k]) for k in want if got.get(k) != want[k]}
    if bad:
        raise PolicyError(f"IR document does not belong to table {T.name!r} (k={T.k}): at width {w} "
                          f"its circuit's resources differ from the table's {bad} (got, predicted)")
    cache[key] = {"table": T.name, "width": w, "checked": True, "resources": got}
    return cache[key]


def compose(policy: PhasePolicy, band_docs: Dict[str, ir_v1.Doc], *, verify: bool = True,
            leaf_source: Optional[ir_v1.Doc] = None) -> ir_v1.Doc:
    """Return one `ir_v1.Doc` for the full policy, from one extracted document for each band table.

    ``verify`` (default) runs :func:`verify_binding` on each band before the composition. An empty
    policy (`bands = ()`, the admissible all-leaf policy of ForShor) gives a dispatcher that is only
    the leaf. Its leaf templates come from ``leaf_source`` or from a document in ``band_docs``."""
    if not policy.bands:
        src = leaf_source or next(iter(band_docs.values()), None)
        if src is None:
            raise PolicyError("an empty policy needs one IR document (leaf_source) for its leaf templates")
        return _compose_all_leaf(policy, src)
    if set(band_docs) != {b.table.name for b in policy.bands}:
        raise PolicyError(f"need exactly one IR document per band table: have {sorted(band_docs)}, "
                          f"bands {[b.table.name for b in policy.bands]}")
    if verify:
        for b in policy.bands:
            verify_binding(b.table, band_docs[b.table.name])
    templates: Dict[str, ir_v1.Template] = {}
    opaque: Dict[str, int] = {}
    first = band_docs[policy.bands[0].table.name]

    for leaf in LEAF_TEMPLATES:
        versions = [d.templates[leaf] for d in band_docs.values() if leaf in d.templates]
        if versions:
            if any(replace(v, provenance="") != replace(versions[0], provenance="") for v in versions):
                raise PolicyError(f"{leaf} differs between band documents; it should be table-free")
            templates[leaf] = versions[0]

    for step, leaf in zip(STEP_TEMPLATES, LEAF_TEMPLATES):
        if step not in first.templates:
            continue
        proto = first.templates[step]
        base = _leaf_call(proto, leaf)
        chain: ir_v1.Tree = base
        for b in reversed(policy.bands):
            d = band_docs[b.table.name]
            t = d.templates[step]
            if (t.wParams, t.aParams, t.rParams) != (proto.wParams, proto.aParams, proto.rParams):
                raise PolicyError(f"band {b.table.name}: {step} parameters differ")
            _check_band_shape(b.table.name, t, b.table.k)
            banded = f"{step}@{b.table.name}"
            templates[banded] = replace(t, name=banded, table=b.table.name,
                                        provenance=f"{t.provenance} [band {b.table.name}, "
                                                   f"threshold {b.threshold}]")
            call = ("call", banded, tuple(("var", p) for p in t.wParams),
                    tuple(("var", p) for p in t.aParams), tuple(("var", p) for p in t.rParams))
            guard = ("le", ("lit", b.threshold), ("max", ("var", "xw"), ("var", "zw")))
            chain = ("cond", guard, call, chain)
        templates[step] = ir_v1.Template(
            name=step, wParams=proto.wParams, aParams=proto.aParams, rParams=proto.rParams,
            body=chain, table=None,
            provenance=f"composed: Shor.Policy.{'cSignedPlanOf' if step.startswith('c') else 'signedPlanOf'} "
                       f"dispatcher over {[(b.threshold, b.table.name) for b in policy.bands]}")
    for d in band_docs.values():
        for name, ar in d.opaque.items():
            if opaque.setdefault(name, ar) != ar:
                raise PolicyError(f"opaque {name} has different arities across bands")
    if "phase_product" not in templates:
        raise PolicyError("no band document contains phase_product")
    return ir_v1.Doc(templates=templates, opaque=opaque, entry="phase_product",
                     meta={"policy": policy.to_json(),
                           "band_tables": {b.table.name: table_sha256(b.table) for b in policy.bands}})


def _compose_all_leaf(policy: PhasePolicy, src: ir_v1.Doc) -> ir_v1.Doc:
    """`bands = ()`: `choose` is always `none`. Thus the dispatcher is only the base case."""
    templates: Dict[str, ir_v1.Template] = {}
    for step, leaf in zip(STEP_TEMPLATES, LEAF_TEMPLATES):
        if step not in src.templates or leaf not in src.templates:
            continue
        proto = src.templates[step]
        templates[leaf] = src.templates[leaf]
        templates[step] = ir_v1.Template(
            name=step, wParams=proto.wParams, aParams=proto.aParams, rParams=proto.rParams,
            body=_leaf_call(proto, leaf), table=None,
            provenance="composed: empty policy (all leaf), Shor.Policy.signedPlanOf none-branch")
    if "phase_product" not in templates:
        raise PolicyError("leaf source document contains no phase_product")
    return ir_v1.Doc(templates=templates, opaque=dict(src.opaque), entry="phase_product",
                     meta={"policy": policy.to_json(), "band_tables": {}})


# ============================================================================ loading
def table_from_submission(doc: Dict[str, Any], name: str) -> ToomCookTable:
    """Return the `table` block of a `forshor.submission/v1` document."""
    return ToomCookTable.from_json(name, doc["table"])


def load_policy(manifest: Union[Dict[str, Any], str, Path], *,
                verify: bool = True) -> Tuple[PhasePolicy, Dict[str, ir_v1.Doc]]:
    """Read a `forshor_qualtran.policy/v1` manifest:

        {"schema": "forshor_qualtran.policy/v1", "name": "...",
         "bands": [{"threshold": 40, "submission": "toom3.submission.json"},
                   {"threshold": 12, "table": {"name": "k2", "k": 2, "points": [...], "ops": [...]},
                    "ir": "k2.template.json"}]}

    A band is one of these two forms:

    - A `forshor.submission/v1` document (table and IR together). This is the preferred form,
      because then the IR is surely the IR that came from that table.
    - An explicit `table` and an `ir` document (`forshor_emit template <k>` / bundle).

    Paths are relative to the directory of the manifest. Returns the policy and the IR document of
    each band, for :func:`compose`.
    """
    base = Path(".")
    if not isinstance(manifest, dict):
        base = Path(manifest).parent
        manifest = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if manifest.get("schema") != POLICY_SCHEMA:
        raise PolicyError(f"expected {POLICY_SCHEMA}, got {manifest.get('schema')!r}")

    def load(x):
        return x if isinstance(x, dict) else json.loads((base / x).read_text(encoding="utf-8"))

    bands, docs = [], {}
    for i, bj in enumerate(manifest["bands"]):
        if "submission" in bj:
            sub = load(bj["submission"])
            name = bj.get("name") or sub.get("name") or f"band{i}"
            table = table_from_submission(sub, name)
            ir_src = sub
        else:
            tj = bj["table"]
            name = tj.get("name") or bj.get("name") or f"band{i}"
            table = ToomCookTable.from_json(name, tj)
            ir_src = load(bj["ir"])
        bands.append(PolicyBand(int(bj["threshold"]), table))
        docs[name] = ir_v1.parse_doc(ir_src)
    policy = PhasePolicy(tuple(bands), name=manifest.get("name", "policy"))
    policy.check()
    if verify:
        for b in policy.bands:
            verify_binding(b.table, docs[b.table.name])
    return policy, docs


# ============================================================================ the output of ForShor
POLICY_SUBMISSION_SCHEMA = schemas.POLICY_SUBMISSION


def band_names(n_bands: int) -> List[str]:
    """Return the table names for the bands of a submission. The ForShor `PolicyTemplate.lean` calls
    them `band0 …`, widest first. Nothing in `policy.json` gives a table name, thus the band index gives it."""
    return [f"band{i}" for i in range(n_bands)]


def load_policy_submission(sub: Union[Dict[str, Any], str, Path],
                           band_ir: Optional[Union[Dict[str, Any], List[Any], str, Path]] = None, *,
                           verify: bool = True) -> Tuple[PhasePolicy, Dict[str, ir_v1.Doc], Dict[str, Any]]:
    """Read ForShor's `forshor_policy_submission` output (`forshor.policy-submission/v1`).

    ``sub`` is the document, or its path. Its ``bands`` already have all the data that the cost model
    needs: `(threshold, k, points, ops)`. Thus the function builds the :class:`PhasePolicy` from these
    data only, and it needs no IR. ``band_ir`` (optional) is one `forshor.ir/v1` document for each
    band, widest first: a list, a ``{band name or index: doc-or-path}`` dict, or a directory that
    holds `ir_band<i>.json`. If you give it, :func:`verify_binding` binds each document to the table
    of its band. This binding ties the extracted IR to *these* tables, not only to tables of the same arity.

    The function refuses these inputs (it never repairs them):

    - an incorrect schema,
    - thresholds that do not strictly decrease,
    - a band whose declared `k` does not agree with its point count (C1, from `policy.check`),
    - a band IR that does not bind to its table,
    - a missing band IR if ``band_ir`` is given only partially.

    Returns ``(policy, docs, info)``. ``info`` holds the ForShor `shrinks` advisory for each band,
    the precision and the trial count.
    """
    if not isinstance(sub, dict):
        sub = json.loads(Path(sub).read_text(encoding="utf-8"))
    try:
        schemas.expect(sub, POLICY_SUBMISSION_SCHEMA, where="policy submission")
    except schemas.SchemaError as e:
        raise PolicyError(str(e)) from None
    bands_j = sub.get("bands")
    if not isinstance(bands_j, list):
        raise PolicyError("policy submission has no `bands` list")
    names = band_names(len(bands_j))
    bands: List[PolicyBand] = []
    for i, (bj, nm) in enumerate(zip(bands_j, names)):
        for key in ("threshold", "k", "points", "ops"):
            if key not in bj:
                raise PolicyError(f"band {i}: missing `{key}`")
        if bj.get("index", i) != i:
            raise PolicyError(f"band {i}: `index` is {bj['index']}; bands must come widest first")
        T = ToomCookTable.from_json(nm, bj)
        bands.append(PolicyBand(int(bj["threshold"]), T))
    policy = PhasePolicy(tuple(bands), name=sub.get("name", "policy-submission"))
    policy.check()
    docs: Dict[str, ir_v1.Doc] = {}
    if band_ir is not None:
        src: Dict[str, Any] = {}
        if isinstance(band_ir, (str, Path)):
            for i, nm in enumerate(names):
                f = Path(band_ir) / f"ir_{nm}.json"
                if not f.is_file():
                    raise PolicyError(f"band IR directory {band_ir} lacks {f.name}")
                src[nm] = f
        elif isinstance(band_ir, dict):
            src = {(names[k] if isinstance(k, int) else k): v for k, v in band_ir.items()}
        else:
            src = dict(zip(names, band_ir))
        if set(src) != set(names):
            raise PolicyError(f"need one IR document per band {names}, have {sorted(src)}")
        for nm in names:
            docs[nm] = ir_v1.parse_doc(src[nm])
        if verify:
            for b in policy.bands:
                verify_binding(b.table, docs[b.table.name])
    info = {"shrinks": [bj.get("shrinks") for bj in bands_j],
            "precision": sub.get("precision"), "benchmark": sub.get("benchmark"),
            "ir_native": sub.get("ir") is not None, "ir_status": sub.get("ir_status")}
    return policy, docs, info


# ============================================================================ instantiation
def pp_registers(policy: PhasePolicy, n: int, slack: int = 1,
                 wz: Optional[int] = None) -> Tuple[ir_v1.ExtReg, ir_v1.ExtReg, int]:
    """`Shor.ppRegisters` (Emit/Lower/Registers.lean), with the reserve size from
    `Policy.reserveNeed`. This is what `Policy.WorkspaceOK` asks for. Returns `(x, z, ctrl)`."""
    wz = n if wz is None else wz
    nx, nz = policy.reserve_need(n, wz)
    x = ir_v1.ExtReg(tuple(range(n)), tuple(range(n, n + nx + slack)))
    z0 = n + nx + slack
    z = ir_v1.ExtReg(tuple(range(z0, z0 + wz)), tuple(range(z0 + wz, z0 + wz + nz + slack)))
    return x, z, z0 + wz + nz + slack


def instantiate_pp(doc: ir_v1.Doc, policy: PhasePolicy, n: int, phi: Fraction, *,
                   slack: int = 1, controlled: bool = False, fuel: int = 400) -> List[Dict[str, Any]]:
    """Return the (controlled) signed phase product at width `n` with `policy`, as flat
    `forshor.lowgate/v2` leaves."""
    x, z, ctrl = pp_registers(policy, n, slack)
    w = {"xw": x.width, "zw": z.width, "xCap": x.capacity, "zCap": z.capacity}
    r = {"x": x, "z": z}
    if controlled:
        r["ctrl"] = ir_v1.ExtReg((ctrl,))
    return ir_v1.instantiate(doc, "cphase_product" if controlled else "phase_product",
                             PolicyOracle(policy), w=w, a={"phi": Fraction(phi)}, r=r, fuel=fuel)
