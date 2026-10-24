"""Assemble the mirror bundle. Its shape is exactly the shape of `forshor_emit bundle <k>`."""
from __future__ import annotations

from typing import Any, Dict

from .. import schemas
from . import coeff_poly, modmul_plan, qft_plan, recursion, schedule, width

EMITTER_REV = "forshor_emit rev 1 (2026-09-01, DRAFT/UNBUILT)"
SECTIONS = ("schedule", "coeff_poly", "width", "qft_plan", "modmul_plan", "recursion")


def mirror_section(name: str, k: int, m_max: int = 24, w_max: int = 96) -> Dict[str, Any]:
    if name == "schedule":
        return schedule.section(k)
    if name == "coeff_poly":
        return coeff_poly.section(k)
    if name == "width":
        return width.section(k, m_max, w_max)
    if name == "qft_plan":
        return qft_plan.section(k, w_max)
    if name == "modmul_plan":
        return modmul_plan.section()
    if name == "recursion":
        return recursion.section(k, w_max)
    raise ValueError(f"unknown section {name}")


def mirror_bundle(k: int, m_max: int = 24, w_max: int = 96) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "schema": schemas.FORSHOR_EMIT, "k": k, "emitter": EMITTER_REV,
        "provenance": "lean-emitted", "n_free": True,
        "opts": {"m_max": m_max, "w_max": w_max},
    }
    for s in SECTIONS:
        doc[s] = mirror_section(s, k, m_max, w_max)
    return doc


def strip_private(doc: Any) -> Any:
    """Remove the `_mirror_*` bookkeeping keys. Do this before you compare with the Lean output."""
    if isinstance(doc, dict):
        return {k: strip_private(v) for k, v in doc.items() if not str(k).startswith("_mirror_")}
    if isinstance(doc, list):
        return [strip_private(v) for v in doc]
    return doc
