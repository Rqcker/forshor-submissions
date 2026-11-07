# Site pricing engine (vendored from the `sto_site` demo)

The engine behind the demo's boards: it prices PhaseProduct (PP) and PhaseTripleProduct (PTP) **policies**
(banded recursions over interpolation tables) at exact register widths, prices the full order-finding run on
ForShor's IR, plans the frontier curves, and builds the leaderboards. All numbers are Qualtran gate counts
converted to T with Ross-Selinger rotations (`sto_currency.py` is the one definition). Lean proves tables and
policies; this engine's counts are Python and are **not proved in Lean**.

This is a faithful copy of the demo's `engine/` (model `sto-site-v3`) plus its `harness/` (now at
`evaluation/harness/`). The tree layout is deliberate: the cached frontiers in `cache/` are keyed by a fingerprint of
`data/`, `tcost/`, `forshor_qualtran/`, `fullalg/` and `paper_model/`, so moving or trimming those directories makes
every cache look stale and recomputing costs hours (about 20 minutes per delta for PTP alone).

| Path | Content |
| --- | --- |
| `sto_currency.py` | The one T currency both counting engines import |
| `tcost/` | Engine 1: symbolic Qualtran bloq per policy chain, planner, policy readers |
| `forshor_qualtran/` | Engine 2: concrete tree walk (PP and PTP), exact PP planner |
| `fullalg/` | Full order finding on ForShor's IR (vendored L2Q `l2q_full`, reference documents) |
| `site_engine/` | Library, cross-check, frontier, full-policy view, intake, precompute, report |
| `paper_model/` | Reproduces the paper's printed 2.31e12 T only; prices nothing on the boards |
| `data/` | STO archive (21), accepted (18), reference policies, DP library, seeds and their run records |
| `cache/` | Derived frontier JSON (fingerprinted; never hand-edit; `python -m site_engine.precompute`) |
| `vendor/Qualtran-main/` | Frozen Qualtran 0.8.0.dev0. Both engines **require** this tree |
| `tests/` | Engine tests (`-m "not lean"` needs no Lean build) |

## Relationship to `../estimator`

`../estimator` (the pinned `forshor-resource-estimation` copy and `../estimate.py`) is what the live pipeline runs today
for **table** submissions. It also defines top-level packages named `tcost` and `forshor_qualtran`, which are *different
code*, so the two must never be imported in one process. Nothing here is wired into a workflow yet; the old path is
untouched and its golden tests still apply to it. Cutover to this engine comes with policy intake.

## Negation method (ForShor swap 002)

A ForShor policy carries `negation` = `oneQubit` (default) or `scratch`. Stage 5 of `harness/forshor_verify.py` prices with the
method of the document Lean printed (`fullalg/l2q_full` through `site_engine/fullalg.py`: `oneQubit` -> `gidney_circuit`, `scratch`
-> `legacy`, `cost_conventions.NEGATION_IMPL`), and `check_negation` rejects a printed method that differs from the `negation`
key of the submitted policy JSON (no key = `oneQubit`). `paper_model/` and `forshor_qualtran/` do not price stage 5 and are
unchanged. `DEFAULT_NEGATE` is `gidney_circuit` (= `oneQubit`, as in ForShor eb00c5a, the export branch and the L2Q package) and
`forshor_verify.REQUIRE_PRINTED_NEGATION` is true: since the pin is eb00c5a every printed document carries `negation`, so a document
without it comes from an older tree (6d55ad9 / 10fe620, Cuccaro charge = `scratch`) and is an error. The default is consulted only for
a submitted policy JSON without the key (= `oneQubit`). The harness's `.cache/forshor_latest_build` (10fe620) is therefore too old
for `forshor_verify` until it is rebuilt at eb00c5a.

## Run

```sh
python3.12 -m venv .venv && .venv/bin/pip install -r evaluation/engine/requirements.txt
cd evaluation/engine
../../.venv/bin/python -m pytest tests -q -m "not lean"      # about 27 minutes
../../.venv/bin/python -m site_engine.precompute              # rebuild stale caches only
```

Result on 2026-11-05 (demo venv, Python 3.12.14, vendored Qualtran), full run before the skip list below existed:
**591 passed, 11 skipped, 12 failed, 7 errors** in 27 minutes. All 19 failures and errors are checks of the demo's own page,
backend or `run.sh` (`test_trust.py` and parts of `test_sto_mirror.py` / `test_stage13.py`), which are not vendored; they are
now skipped by exact node id in `tests/conftest.py` (confirmed: those 19 skip). The full suite was not re-run after that
edit. The page-wording rules those tests encode (for instance that the page never says the cost is proved in Lean) have to be
re-checked against the new page when the frontend is ported.

## Wiring into CI (not done; known hazards)

* `../requirements.lock` has no `frozendict`, which the engine needs (`requirements.txt` pins 2.4.7). Adding it changes
  the environment cache key.
* `../../.github/actions/estimator-environment` exports `ONECLICK_QUALTRAN` pointing at the **pip** Qualtran 0.7.0.
  `site_engine` only uses `setdefault`, so under that action Engine 2 would be pointed at the wrong tree. A step that
  runs the engine must set both `ONECLICK_QUALTRAN` and `TCOST_QUALTRAN_PATH` to `vendor/Qualtran-main/Qualtran-main`.
  The engine refuses to start on a mismatch rather than mixing trees. Running beside the pip package is untested.
* Policy checks (`../../scripts/check_banded_policy.py`, `check_ptp_policy.py`) replace the demo's Lean harnesses in
  `../harness/`, which expect sibling build trees that do not exist here (`STO_POLICY_BUILD`, `PTP_POLICY_BUILD`,
  a ForShor tree). The tests that need those trees are the `lean`-marked ones.

## Provenance

Copied on 2026-11-05 from `~/Desktop/github_packages/sto_site/{engine,harness}` (demo package of 2026-10-29, Stage 18
documentation; the demo has no git history). Left out: bytecode caches and five demo-layout tests
(`test_harness_forshor`, `test_harness_sto`, `test_starter_pack`, `test_submit`, `test_display_text`), which exercise the
demo's backend, starter pack and Lean harness trees.
Some pinned run records in `data/` and `tests/data/` contain absolute paths of the machine that produced them
(`/Users/joshua/...`); they are inside the cache fingerprint, so scrubbing them invalidates every cached frontier.
