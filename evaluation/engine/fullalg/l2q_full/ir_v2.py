"""`shor.ir/v2` reader — schema-checked, converting to the legacy node shape the backends consume.

The v2 wire format is written by the Lean `shor_emit` binary from the PROVED lowering, through the
verified codec `Shor.Reflect.encode` (round trip `Shor.Reflect.decode_encode`). Circuit nodes are
positional arrays `[tag, field_1, ..., field_n]`; registers are `[q0, q1, ...]` (LSB-first);
`ExtReg` is `[active, reserve]`; angles are `[num, den]`, exact rationals in units of pi.

Two guarantees this module enforces, both mechanical:

* **Schema coverage.** `check_schema(schema)` compares the constructor table the Lean side derives
  from the inductive `Shor.LowGate` (`shor_emit schema`, or a saved `lowgate.schema.json`) against
  `HANDLERS` below. A constructor the Lean side has and this module does not — or the reverse — is a
  hard error at import/check time. Adding a `LowGate` constructor in Lean therefore forces this file
  to change before any document is read.
* **Arity.** Every node is checked against the schema's field count before conversion.

Conversion target: the `shor.lowgate.ir/v1` node dicts (`{"op": ..., ...}`) that `py/lowgate_ir.py`,
`py/qualtran_backend.py` and `py/ref_interp.py` already understand — so the whole Qualtran/Qiskit
backend is reused unchanged. The v1 *document* built by `to_legacy_doc` is loaded with
`require_checks=False`: the emit-time checks v1 required are now theorems about the emitted object
(`document["theorems"]`), not runtime flags.
"""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Sequence

SCHEMA = "shor.ir/v2"


class IRv2Error(ValueError):
    pass


# tag -> (legacy op name, field kinds in wire order)
# field kinds: nat | int | bool | str | natlist | angle | reg | extreg | node | prim
HANDLERS: Dict[str, tuple] = {
    "id": ("id", ()),
    "seq": ("seq", ("node", "node")),
    "adj": ("adj", ("node",)),
    "H": ("H", ("nat",)),
    "X": ("X", ("nat",)),
    "Prim": ("Prim", ("prim",)),
    "ShiftL": ("ShiftL", ("extreg", "nat")),
    "ShiftR": ("ShiftR", ("extreg", "nat")),
    "Negate": ("Negate", ("extreg",)),
    "AddScaled": ("AddScaled", ("extreg", "extreg", "bool", "nat")),
    "Naive_SignedPhaseProd": ("NaiveSignedPhaseProd", ("angle", "extreg", "extreg")),
    "Naive_CSignedPhaseProd": ("NaiveCSignedPhaseProd", ("nat", "angle", "extreg", "extreg")),
    "zeroExtend": ("zeroExtend", ("extreg", "nat")),
    "signExtend": ("signExtend", ("extreg", "nat")),
    "zeroDealloc": ("zeroDealloc", ("extreg", "nat")),
    "signDealloc": ("signDealloc", ("extreg", "nat")),
    "RadixReverse": ("RadixReverse", ("reg", "nat")),
}

# legacy field names per op, in wire order
LEGACY_FIELDS: Dict[str, Sequence[str]] = {
    "id": (), "seq": ("body",), "adj": ("body",), "H": ("q",), "X": ("q",),
    "Prim": ("tag", "args"), "ShiftL": ("r", "n"), "ShiftR": ("r", "n"), "Negate": ("r",),
    "AddScaled": ("dst", "src", "negSrc", "shift"),
    "NaiveSignedPhaseProd": ("phi", "x", "z"), "NaiveCSignedPhaseProd": ("ctrl", "phi", "x", "z"),
    "zeroExtend": ("r", "n"), "signExtend": ("r", "n"), "zeroDealloc": ("r", "n"),
    "signDealloc": ("r", "n"), "RadixReverse": ("r", "m"),
}


# The typed primitive vocabulary `Shor.PrimOp` (Stage 3, decision D4). A `Prim` node's single payload
# is `[ctor, field_1, ..., field_n]`; the table below mirrors the Lean constructors and, like
# HANDLERS, is checked against the Lean-derived `prim_constructors` table by `check_schema`.
#   ctor -> (field kinds in wire order, legacy tag, legacy-args builder)
# Legacy shape (what `py/qualtran_backend._apply_prim` consumes): arithmetic primitives carry their
# constant IN the tag (`"CMP_GE_CONST:N"`) and qubit-only args `[flag, *data(, *work)]`; the
# specification-level ideal multiplies keep the old flat `[c, N(, ctrl), *data]` args.
PRIM_HANDLERS: Dict[str, tuple] = {
    "cmpGeConst": (("nat", "nat", "reg"),
                   lambda N, flag, data: (f"CMP_GE_CONST:{N}", [flag, *data])),
    "csubConst": (("nat", "nat", "reg"),
                  lambda N, flag, data: (f"CSUB_CONST:{N}", [flag, *data])),
    "cmpLtNW": (("nat", "nat", "reg", "reg"),
                # the data width rides on the tag: the backend's faithful `CmpLtNW` needs the data/work split
                lambda N, flag, data, work: (f"CMP_LT_NW:{N}:{len(data)}", [flag, *data, *work])),
    "idealModMul": (("nat", "nat", "reg"),
                    lambda c, N, data: ("IdealModMul", [c, N, *data])),
    "idealCtrlModMul": (("nat", "nat", "reg", "nat"),
                        lambda c, N, data, ctrl: ("IdealCtrlModMul", [c, N, ctrl, *data])),
}


def _check_table(lean_ctors: List[Dict[str, Any]], table: Dict[str, Any], what: str, arity) -> None:
    lean = {c["tag"]: [f["type"] for f in c["fields"]] for c in lean_ctors}
    missing = sorted(set(lean) - set(table))
    extra = sorted(set(table) - set(lean))
    if missing or extra:
        raise IRv2Error(f"{what} schema/handler mismatch: Lean has {missing} that Python lacks; "
                        f"Python has {extra} that Lean lacks — refusing to read any document")
    for tag, entry in table.items():
        n = arity(entry)
        if n != len(lean[tag]):
            raise IRv2Error(f"{what} arity mismatch for {tag}: Lean {len(lean[tag])} fields, Python {n}")


def check_schema(schema: Dict[str, Any]) -> None:
    """Refuse unless the Lean-derived constructor tables (`Shor.LowGate` and `Shor.PrimOp`) and the
    handler tables agree exactly (names and arity)."""
    if schema.get("schema") != SCHEMA or schema.get("type") != "Shor.LowGate":
        raise IRv2Error(f"not a {SCHEMA} schema for Shor.LowGate: {schema.get('schema')!r}/{schema.get('type')!r}")
    _check_table(schema["constructors"], HANDLERS, "LowGate", lambda e: len(e[1]))
    if schema.get("prim_type") != "Shor.PrimOp" or "prim_constructors" not in schema:
        raise IRv2Error("schema lacks the typed primitive table (prim_type/prim_constructors) — "
                        "the Lean side predates the typed Prim constructor; refusing")
    _check_table(schema["prim_constructors"], PRIM_HANDLERS, "PrimOp", lambda e: len(e[0]))


def _nat(j: Any, where: str) -> int:
    if isinstance(j, bool) or not isinstance(j, int) or j < 0:
        raise IRv2Error(f"{where}: expected a natural number, got {j!r}")
    return j


def _int(j: Any, where: str) -> int:
    if isinstance(j, bool) or not isinstance(j, int):
        raise IRv2Error(f"{where}: expected an integer, got {j!r}")
    return j


def _reg(j: Any, where: str) -> List[int]:
    if not isinstance(j, list):
        raise IRv2Error(f"{where}: expected a register array")
    qs = [_nat(q, where) for q in j]
    if len(set(qs)) != len(qs):
        raise IRv2Error(f"{where}: register has repeated qubits {qs}")
    return qs


def _extreg(j: Any, where: str) -> Dict[str, List[int]]:
    if not isinstance(j, list) or len(j) != 2:
        raise IRv2Error(f"{where}: expected [active, reserve]")
    a, r = _reg(j[0], where + ".active"), _reg(j[1], where + ".reserve")
    if set(a) & set(r):
        raise IRv2Error(f"{where}: active/reserve overlap")
    return {"active": a, "reserve": r}


def _angle(j: Any, where: str) -> Dict[str, Any]:
    if not isinstance(j, list) or len(j) != 2:
        raise IRv2Error(f"{where}: expected [num, den]")
    num, den = _int(j[0], where), _nat(j[1], where)
    if den == 0:
        raise IRv2Error(f"{where}: zero denominator")
    f = Fraction(num, den)
    return {"num": f.numerator, "den": f.denominator, "base": "pi"}


def angle_fraction(j: Any) -> Fraction:
    a = _angle(j, "angle")
    return Fraction(a["num"], a["den"])


def _field(kind: str, f: Any, w: str, node_conv=None) -> Any:
    if kind == "nat":
        return _nat(f, w)
    if kind == "int":
        return _int(f, w)
    if kind == "bool":
        if not isinstance(f, bool):
            raise IRv2Error(f"{w}: expected bool")
        return f
    if kind == "str":
        if not isinstance(f, str):
            raise IRv2Error(f"{w}: expected string")
        return f
    if kind == "natlist":
        if not isinstance(f, list):
            raise IRv2Error(f"{w}: expected array")
        return [_nat(x, w) for x in f]
    if kind == "angle":
        return _angle(f, w)
    if kind == "reg":
        return _reg(f, w)
    if kind == "extreg":
        return _extreg(f, w)
    if kind == "prim":
        return prim_to_legacy(f, w)
    if kind == "node":
        return node_conv(f, w)
    raise IRv2Error(f"internal: unknown field kind {kind}")  # pragma: no cover


def prim_to_legacy(j: Any, where: str = "prim") -> tuple:
    """`[ctor, fields...]` (a `Shor.PrimOp`) -> `(legacy_tag, legacy_args)`; unknown ctor -> refuse."""
    if not isinstance(j, list) or not j or not isinstance(j[0], str):
        raise IRv2Error(f"{where}: expected [ctor, ...] for a primitive, got {str(j)[:80]!r}")
    ctor, fields = j[0], j[1:]
    if ctor not in PRIM_HANDLERS:
        raise IRv2Error(f"{where}: unknown primitive constructor {ctor!r} — refusing")
    kinds, build = PRIM_HANDLERS[ctor]
    if len(fields) != len(kinds):
        raise IRv2Error(f"{where}: {ctor} expects {len(kinds)} fields, got {len(fields)}")
    conv = [_field(k, f, f"{where}.{ctor}[{i}]") for i, (k, f) in enumerate(zip(kinds, fields))]
    regs = [c for k, c in zip(kinds, conv) if k == "reg"]
    seen: set = set()
    for r in regs:
        if seen & set(r):
            raise IRv2Error(f"{where}: {ctor} registers overlap")
        seen |= set(r)
    tag, args = build(*conv)
    return tag, list(args)


def to_legacy_node(j: Any, where: str = "circuit") -> Dict[str, Any]:
    """Convert one v2 node (recursively) to the v1 `{"op": ...}` dict shape."""
    if not isinstance(j, list) or not j or not isinstance(j[0], str):
        raise IRv2Error(f"{where}: expected [tag, ...], got {str(j)[:80]!r}")
    tag, fields = j[0], j[1:]
    if tag not in HANDLERS:
        raise IRv2Error(f"{where}: unknown constructor tag {tag!r} — refusing")
    op, kinds = HANDLERS[tag]
    if len(fields) != len(kinds):
        raise IRv2Error(f"{where}: {tag} expects {len(kinds)} fields, got {len(fields)}")
    out: Dict[str, Any] = {"op": op}
    names = LEGACY_FIELDS[op]
    conv = [_field(kind, f, f"{where}.{tag}[{i}]", to_legacy_node)
            for i, (kind, f) in enumerate(zip(kinds, fields))]
    if op == "Prim":
        # Typed primitive (`Shor.PrimOp`): the constant, flag and registers are separate typed fields
        # on the wire; the legacy `"{OP}:{const}"` tag + qubit-only args is rebuilt from them.
        out["tag"], out["args"] = conv[0]
        return out
    if op == "seq":
        out["body"] = conv          # binary on the wire; v1 consumers accept any body length
    elif op == "adj":
        out["body"] = conv[0]
    else:
        for name, value in zip(names, conv):
            out[name] = value
    return out


def leaves(node: Dict[str, Any]):
    """Flatten a legacy node to its non-structural leaves (adjoints kept as wrapped groups)."""
    op = node.get("op")
    if op == "seq":
        for b in node["body"]:
            yield from leaves(b)
    elif op == "id":
        return
    elif op == "adj":
        yield {"op": "adj", "body": list(leaves(node["body"]))}
    else:
        yield node


_KIND_TO_LEGACY = {
    "signed_phase_product": "signed_phase_product",
    "qft": "qft",
    "cmodmul": "cmodmul_approx",
    "orderfinding": "orderfinding",
}


def to_legacy_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """A `shor.lowgate.ir/v1`-shaped document for the existing backends (load with
    `lowgate_ir.parse_ir(..., require_checks=False)`)."""
    if doc.get("schema") != SCHEMA:
        raise IRv2Error(f"expected {SCHEMA}, got {doc.get('schema')!r}")
    if doc.get("bit_order") != "lsb_first":
        raise IRv2Error("v2 is lsb_first only")
    kind = doc["kind"]
    circuit = to_legacy_node(doc["circuit"])
    expected = dict(doc["expected"])
    expected["kind"] = _KIND_TO_LEGACY.get(kind, kind)
    if "phi" in expected:
        expected["phi"] = _angle(expected["phi"], "expected.phi")
    layout = doc.get("layout", {})
    legacy: Dict[str, Any] = {
        "schema": "shor.lowgate.ir/v1",
        "bit_order": "lsb_first",
        "provenance": doc.get("provenance", ""),
        "source": {"k": doc["program"]["k"], "decl": doc["spec"].get("gate", kind),
                   "program": doc["program"]["decl"], "n": doc["spec"].get("n")},
        "qubits": {"count": int(doc["qubits"]["count"]), "ancilla": []},
        "expected": expected,
        "checks": {**{k: v for k, v in doc.get("checks", {}).items()},
                   "limb_width_W": layout.get("limb_width_W")},
        "coeffs": [],
        "circuit": circuit,
        "_v2": {k: doc[k] for k in ("kind", "spec", "program", "census", "theorems", "trust") if k in doc},
    }
    if kind == "signed_phase_product":
        legacy["operands"] = {"x": _extreg(layout["x"], "layout.x"), "z": _extreg(layout["z"], "layout.z")}
        legacy["phi"] = _angle(doc["spec"]["phi"], "spec.phi")
    elif kind == "qft":
        legacy["phi"] = {"num": 1, "den": 1, "base": "pi"}   # v1's `needs_phi` for qft (unused)
    return legacy


def load_v2(path: str | Path) -> Dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise IRv2Error(f"{path}: expected {SCHEMA}")
    return doc


def register_legacy_kinds() -> None:
    """Let `py/lowgate_ir.py` accept the kinds v1 never had (`orderfinding`), with no operand/phi
    block and no v1 emit-time checks (v2 guarantees are theorems, not runtime flags)."""
    from . import paths
    paths.setup_legacy_sys_path()
    import lowgate_ir  # noqa: E402  (legacy module on sys.path)
    lowgate_ir.KIND_REQUIREMENTS.setdefault(
        "orderfinding", {"required_checks": (), "needs_operands": False, "needs_phi": False})
