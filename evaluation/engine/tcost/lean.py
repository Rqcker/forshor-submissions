"""Read formally verified tables out of a built ShorTableOptimization checkout, safely.

All data that the backend gets from Lean comes from the `#eval` output of a source file that the backend writes.
That output is untrusted. A submission can print arbitrary text while Lean evaluates its proved definitions (for
example `dbgTrace`, which is definitionally `f ()` and thus survives `rfl`). Thus the protocol does these steps:

* It tags each line that the backend needs with a new random nonce for each run:
  `TGV1 <nonce> <block> <key>=<value>`, between `TGV1 <nonce> <block> BEGIN` / `END`. Text that is fixed at
  build time cannot contain the nonce. Thus the parser ignores all other lines, also injected look-alikes.
* It parses strictly: each expected block exactly one time, each expected key exactly one time, nothing unexpected.
* It replays the program in Python (`Table.check`, `replay_program_metrics`). It does not trust the printed counts.

`scripts/verifier.py`, `scripts/promote.py`, `tcost.tcount --lean` and
`QualtranCost/scripts/extract_accepted.py` use this module (one implementation).
"""
from __future__ import annotations

import re
import secrets
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

PROTOCOL = "TGV1"

#: The keys of a metrics block for one target (verifier, tcount --lean).
METRIC_KEYS = (
    "mode", "k", "submission_handles", "total_operation_count", "arithmetic_operation_count",
    "phase_product_count", "parallel_phase_product_layer_count", "expected_point_count", "point_count",
    "point_order_count", "generated_points_all_valid", "generated_points_distinct", "points", "point_order",
    "program",
)

OPERATION_PATTERNS = (
    (re.compile(r"\(shiftL, reg=(\d+), by=(\d+)\)"), "shiftL"),
    (re.compile(r"\(shiftR, reg=(\d+), by=(\d+)\)"), "shiftR"),
    (re.compile(r"\(negate, reg=(\d+)\)"), "negate"),
    (re.compile(r"\(addScaled, dst=(\d+), src=(\d+), sign=([+-])1, shift=(\d+)\)"), "addScaled"),
    (re.compile(r"\(phaseProduct, reg=(\d+)\)"), "phaseProduct"),
)


class LeanOutputError(ValueError):
    """The Lean output does not agree with the protocol (missing, duplicated or unexpected content)."""


def new_nonce() -> str:
    return secrets.token_hex(16)


# ---------------------------------------------------------------------------------------------
# Source generation
# ---------------------------------------------------------------------------------------------

class LeanSource:
    """Builds a Lean file whose `#eval` output follows the nonce protocol."""

    def __init__(self, imports: Sequence[str], nonce: Optional[str] = None):
        self.nonce = nonce or new_nonce()
        self.lines = [f"import {m}" for m in imports] + ["", "open TableGeneration",
                                                         "open TableGeneration.Operations", ""]
        self.expected: Dict[str, Tuple[str, ...]] = {}

    def define(self, *lines: str) -> None:
        self.lines.extend(lines)

    def block(self, block_id: str, fields: Sequence[Tuple[str, str]]) -> None:
        """Add one block. `fields` = [(key, Lean expression of type String)]. Block ids and keys must be safe for the
        protocol."""
        if not re.fullmatch(r"[A-Za-z0-9_.:]+", block_id) or block_id in self.expected:
            raise ValueError(f"bad or duplicate block id {block_id!r}")
        tag = f"{PROTOCOL} {self.nonce} {block_id}"
        self.lines.append(f'#eval IO.println "{tag} BEGIN"')
        for key, expr in fields:
            if not re.fullmatch(r"[A-Za-z0-9_.:]+", key):
                raise ValueError(f"bad key {key!r}")
            self.lines.append(f'#eval IO.println ("{tag} {key}=" ++ ({expr}))')
        self.lines.append(f'#eval IO.println "{tag} END"')
        self.expected[block_id] = tuple(k for k, _ in fields)

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def parse_blocks(output: str, nonce: str, expected: Dict[str, Sequence[str]]) -> Dict[str, Dict[str, str]]:
    """Parse the nonce-tagged blocks strictly. If the output does not agree with the protocol, raise
    `LeanOutputError`."""
    prefix = f"{PROTOCOL} {nonce} "
    blocks: Dict[str, Dict[str, str]] = {}
    open_block: Optional[str] = None
    closed: set = set()
    for raw in output.splitlines():
        if not raw.startswith(prefix):
            continue                                       # ignore untagged output (also injections)
        rest = raw[len(prefix):]
        block_id, _, payload = rest.partition(" ")
        if block_id not in expected:
            raise LeanOutputError(f"unexpected block {block_id!r}")
        if payload == "BEGIN":
            if open_block is not None or block_id in blocks:
                raise LeanOutputError(f"block {block_id!r} opened twice or nested")
            open_block, blocks[block_id] = block_id, {}
            continue
        if open_block != block_id:
            raise LeanOutputError(f"line for block {block_id!r} outside it")
        if payload == "END":
            missing = [k for k in expected[block_id] if k not in blocks[block_id]]
            if missing:
                raise LeanOutputError(f"block {block_id!r} is missing {missing}")
            closed.add(block_id)
            open_block = None
            continue
        key, sep, value = payload.partition("=")
        if not sep or key not in expected[block_id]:
            raise LeanOutputError(f"unexpected key {key!r} in block {block_id!r}")
        if key in blocks[block_id]:
            raise LeanOutputError(f"duplicate key {key!r} in block {block_id!r}")
        blocks[block_id][key] = value
    if open_block is not None:
        raise LeanOutputError(f"block {open_block!r} not closed")
    missing = [b for b in expected if b not in closed]
    if missing:
        raise LeanOutputError(f"missing blocks {missing}")
    return blocks


def run_lean(repo: Path, source: LeanSource, runner: Optional[Callable] = None, timeout: int = 1200,
             name: str = "Collect.lean") -> Dict[str, Any]:
    runner = runner or _default_runner
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_text(source.text(), encoding="utf-8")
        return runner(["lake", "env", "lean", str(path)], Path(repo), timeout)


def _default_runner(cmd: List[str], cwd: Path, timeout: int) -> Dict[str, Any]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "output": f"timed out after {timeout} s: {' '.join(cmd)}"}
    return {"returncode": r.returncode, "output": r.stdout + r.stderr}


# ---------------------------------------------------------------------------------------------
# Value parsers (Language.lean pretty-printers) and a Python replay of the printed metrics
# ---------------------------------------------------------------------------------------------

def parse_point_list(raw: Any, field: str) -> List[str]:
    if not isinstance(raw, str) or not raw.startswith("[") or not raw.endswith("]"):
        raise ValueError(f"metric {field} must be a rendered point list")
    inner = raw[1:-1]
    return [] if not inner else inner.split(", ")


def parse_operations(raw: Any) -> List[List[Any]]:
    if not isinstance(raw, str) or not raw.startswith("[") or not raw.endswith("]"):
        raise ValueError("metric program must be a rendered operation list")
    if raw == "[]":
        return []
    if not raw.startswith("[(") or not raw.endswith(")]"):
        raise ValueError("metric program has an invalid operation list")
    operations: List[List[Any]] = []
    for operation in (f"({item})" for item in raw[2:-2].split("), (")):
        for pattern, name in OPERATION_PATTERNS:
            match = pattern.fullmatch(operation)
            if match is None:
                continue
            values: List[Any] = [name]
            for value in match.groups():
                values.append(1 if value == "+" else -1 if value == "-" else int(value))
            operations.append(values)
            break
        else:
            raise ValueError(f"metric program contains an unknown operation: {operation}")
    return operations


def replay_program_metrics(operations: Iterable[Sequence[Any]]) -> Dict[str, int]:
    """Compute the Metrics.lean values again from the program. Do not trust the printed numbers."""
    total = arithmetic = phase = layers = 0
    in_layer = False
    for op in operations:
        total += 1
        if op[0] == "phaseProduct":
            phase += 1
            layers += 0 if in_layer else 1
            in_layer = True
        else:
            arithmetic += 1
            in_layer = False
    return {"total_operation_count": total, "arithmetic_operation_count": arithmetic,
            "phase_product_count": phase, "parallel_phase_product_layer_count": layers}


# ---------------------------------------------------------------------------------------------
# The verified `generate mode k` of a built checkout
# ---------------------------------------------------------------------------------------------

def add_metrics_block(src: LeanSource, index: int, mode: str, k: int) -> str:
    """Add the definitions and one nonce-tagged metrics block for the verified `generate mode k`. Return the block
    id."""
    src.define(
        f"def tgProgram{index} : Prog {k} := generate .{mode} {k} (by decide)",
        f"def tgPoints{index} : List Point := generatedPoints .{mode} {k}",
        f"def tgOrder{index} : List Point := generatePointsInOrder .{mode} {k} (by decide)",
    )
    block_id = f"metrics.{mode}.{k}"
    src.block(block_id, [
        ("mode", f'"{mode}"'),
        ("k", f'"{k}"'),
        ("submission_handles", f"toString (submissionHandles .{mode} {k})"),
        ("total_operation_count", f"toString tgProgram{index}.length"),
        ("arithmetic_operation_count", f"toString (arithmeticOperationCount tgProgram{index})"),
        ("phase_product_count", f"toString (phaseProductCount tgProgram{index})"),
        ("parallel_phase_product_layer_count", f"toString (parallelPhaseProductLayerCount tgProgram{index})"),
        ("expected_point_count", f"toString ((ProductMode.{mode}).pointCount {k})"),
        ("point_count", f"toString tgPoints{index}.length"),
        ("point_order_count", f"toString tgOrder{index}.length"),
        ("generated_points_all_valid", f"toString (tgPoints{index}.all validPoint?)"),
        ("generated_points_distinct", f"toString (decide (tgPoints{index}.map normalizePoint).Nodup)"),
        ("points", f"joinComma (tgPoints{index}.map pointToString)"),
        ("point_order", f"joinComma (tgOrder{index}.map pointToString)"),
        ("program", f"progToString tgProgram{index}"),
    ])
    return block_id


METRICS_IMPORTS = ("TableGeneration.Submission.Defs", "TableGeneration.Submission.Correctness",
                   "TableGeneration.Metrics")


def table_json(block: Dict[str, str]) -> Dict[str, Any]:
    """Convert a metrics block to the table JSON that `tcost.tables.Table.from_json` takes."""
    return {"mode": block["mode"], "k": int(block["k"]),
            "submission_handles": block["submission_handles"] == "true",
            "point_order": parse_point_list(block["point_order"], "point_order"),
            "operations": parse_operations(block["program"])}


def extract_tables(repo: Path, needed: List[Tuple[str, int]], runner: Optional[Callable] = None
                   ) -> Tuple[Dict[Tuple[str, int], Dict[str, Any]], Dict[str, Any]]:
    """Return {(mode, k): table JSON} for each requested target (one Lean process), and the raw record of the run."""
    src = LeanSource(METRICS_IMPORTS)
    ids = {key: add_metrics_block(src, i, *key) for i, key in enumerate(needed)}
    result = run_lean(Path(repo), src, runner, name="CollectTables.lean")
    if result["returncode"] != 0:
        return {}, result
    blocks = parse_blocks(result["output"], src.nonce, src.expected)
    return {key: table_json(blocks[bid]) for key, bid in ids.items()}, result
