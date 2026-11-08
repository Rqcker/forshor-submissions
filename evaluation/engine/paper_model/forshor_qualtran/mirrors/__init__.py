"""Python mirrors of the six `forshor_emit` sections (E1–E6).

Each mirror reproduces, with exact integer/rational arithmetic, the JSON the Lean emitter
prints, so that (a) the pipeline runs with no Lean toolchain under REFERENCE-MIRROR
provenance and (b) when the binary exists, ``emit.crosscheck`` proves the two agree. The
mirrors are the *definitions the Lean code is checked against*, never a substitute for it.
"""
from __future__ import annotations

from .bundle import mirror_bundle, mirror_section  # noqa: F401
