# Examples

`python starter.py examples --check` prices each example and compares the result with `expected/`. The outputs in `expected/` are written by the pack builder from the pack's own
engine, so they always go with this engine and match what the results boards show for the same policy (the builder's test prices every policy of the results seed through the unzipped
pack and compares T and Q with the boards' rows). All runs use n = 2048 and δ = 0.01. The numbers are in `expected/*.json`; this file does not quote them, so it cannot go stale.

| File | What it is |
|---|---|
| `sto_reference_pp.policy.json` | STO's reference PhaseProduct policy (bands 2048:k6, 385:k3, 140:k2, 73:k2, 40:k2). Priced on the PP board and the full order-finding board. |
| `seed_pp_S707dd51bf951.policy.json` | A seeded PhaseProduct policy, re-planned under exact widths (one band, k = 3). Priced on both boards. |
| `seed_pp_S707dd51bf951.Policy.lean` | The same policy as a submission file, `TableGeneration/Submission/Policy.lean` (`starter.py template`), for ForShor at `21e129a`. |
| `seed_ptp_S80e6a121410d.policy.json` | A seeded PhaseTripleProduct policy (one band, k = 4), priced on the PTP board. |
| `seed_ptp_S80e6a121410d.Policy.lean` | The same policy as a PhaseTripleProduct submission file (`starter.py template`; `namespace PTPPolicySubmission`), for ForShor `main` from `349849e`. |
| `forshor_dropin.policy.json` | The board's reference document: ForShor `21e129a`'s printed `policy.json` for the shipped template, `lake exe forshor_policy_submission --balanced` (exact widths, balanced split, trial count 386), printed 2026-11-08. It carries `negation` (`oneQubit`) and the per-method `negate_charge`. |
| `forshor_upstream_scan.policy.json` | ForShor `21e129a`'s printed document for the same template at its default width scan (scanned widths, no balanced split): priced on upstream's IR, the cross-check. |
| `declared_ladder_ignored.policy.json` | The STO reference with a `declared_ladder`: the same prices, plus the notice that the board ignores the ladder. |
| `board_snapshot.json` | The rows of the results boards at δ = 0.01 when the pack was built (name, T, Q, Q × T, whether the row has STO mirror evidence): what `starter.py price` ranks against. The page shows the live boards. |

Each `expected/*.json` has, for a policy: the board's T and exact ladder, Q (the live-qubit peak) and the score Q × T, the engines' agreement, the scanned-width cross-check and, for a
PhaseProduct policy, the full run (T, Q, score, main ladder, trial count and the labelled cross-checks, which are not the score).

Lean status: the pack does not run Lean. These example policies have **not** been run through the Lean harnesses at the pinned ForShor commit; all 15 policies the results seed lists were, and all 15 were
accepted (13 by the harness's default path, two PhaseTripleProduct policies with its `--fast` mode; `harness/LEAN_CHECKS.md` says what the runs took). `starter.py price` prices a policy as written, at the thresholds in the file, as the site's intake does. The table names in the
ladders (STO archive id, DP id, KMY id, or "pasted") come from the library snapshot in `engine/data`.
