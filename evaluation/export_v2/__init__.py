"""Exporter: the engine's output as contract-v2 documents (forshor-site contracts/v2/DESIGN.md).

It produces ledger-level inputs only (model, rows without ranks, benchmarks, curves, panels, evidence). Ranks, boards, the
dashboard and the snapshot id are derived by forshor-site's one shared implementation, so there is no second ranking here.
Everything that touches the engine is imported lazily, so mapping and evidence logic run without it."""
