"""Python mirrors of the six `forshor_emit` sections (E1–E6).

Each mirror uses exact integer/rational arithmetic to make the same JSON that the Lean emitter
prints. This has two purposes:

(a) The pipeline can run without a Lean toolchain, with REFERENCE-MIRROR provenance.
(b) If the binary exists, ``emit.crosscheck`` proves that the two outputs agree.

The mirrors are the *definitions that we check the Lean code against*. They never replace the Lean code.
"""
from __future__ import annotations

from .bundle import mirror_bundle, mirror_section  # noqa: F401
