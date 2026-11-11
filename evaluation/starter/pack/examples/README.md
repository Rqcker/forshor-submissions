# Examples

`python starter.py examples --check` prices each example and compares the result with `expected/`. The outputs in `expected/` are written by the pack builder from the pack's own
engine, so they always go with this engine. All runs use n = 2048 and δ = 0.01. The numbers are in `expected/*.json`; this file does not quote them, so it cannot go stale.

The challenge starts from the tables of Kahanamoku-Meyer and Yao (2024), arXiv:2403.18006v4, Appendix "Parallel sequences": PhaseProduct k = 3, PhaseTripleProduct k = 3 and k = 4.
These three tables are the only tables the pack carries, and the policies below are built from them.

| File | What it is |
|---|---|
| `starting_pp.policy.json` | The PhaseProduct starting policy, built from the k = 3 table of Kahanamoku-Meyer and Yao (2024): the planner's optimum over that one table, as site policy JSON. Priced on the PP board and the full order-finding board. |
| `starting_pp.Policy.lean` | The same policy as a submission file, `TableGeneration/Submission/Policy.lean` (`starter.py template`), for ForShor at `21e129a`. |
| `starting_ptp.policy.json` | The PhaseTripleProduct starting policy, built from the k = 3 and k = 4 tables of Kahanamoku-Meyer and Yao (2024): the planner's optimum over those two tables. Priced on the PTP board. |
| `starting_ptp.Policy.lean` | The same policy as a PhaseTripleProduct submission file (`starter.py template`; `namespace PTPPolicySubmission`), for ForShor `main` from `349849e`. |
| `forshor_dropin.policy.json` | ForShor `21e129a`'s printed `policy.json` for its own shipped template, `lake exe forshor_policy_submission --balanced` (exact widths, balanced split, trial count 386), printed 2026-11-08. It carries `negation` (`oneQubit`) and the per-method `negate_charge`. A format example, not a starting policy. |
| `forshor_upstream_scan.policy.json` | ForShor `21e129a`'s printed document for the same template at its default width scan (scanned widths, no balanced split): priced on upstream's IR, the cross-check. |
| `declared_ladder_ignored.policy.json` | The PhaseProduct starting policy with a `declared_ladder`: the same prices, plus the notice that the board ignores the ladder. |
| `summary.tex` | A template for the optional LaTeX summary of a submission, `TableGeneration/Submission/summary.tex` (see `SKILL.md`, "Optional LaTeX Summary"). A small self-contained document in standard LaTeX; no harness compiles it. Replace its text with your own. |
| `board_snapshot.json` | The starting rows of the boards at δ = 0.01 when the pack was built (name, T, Q, Q × T): the starting policies, priced by this pack's engine. It is what `starter.py price` ranks against. The page shows the live boards. |

Each `expected/*.json` has, for a policy: the board's T and exact ladder, Q (the live-qubit peak) and the score Q × T, the engines' agreement, the scanned-width cross-check and, for a
PhaseProduct policy, the full run (T, Q, score, main ladder, trial count and the labelled cross-checks, which are not the score).

Lean status: the pack does not run Lean. These example policies have **not** been run through the Lean harnesses at the pinned ForShor commit by this pack; the challenge page lists the policies that were
checked. `starter.py price` prices a policy as written, at the thresholds in the file, as the site's intake does. The table names in the ladders (KMY id or "pasted") come from the library in `engine/data`,
which holds KMY's three tables and nothing else.
