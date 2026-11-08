"""Needs the vendored engine and its Python environment: python -m pytest export_v2/pytests -q --runslow  (from evaluation/).
Cross-language validation additionally needs FORSHOR_SITE_DIR (a forshor-site checkout with node_modules) and node."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).parent))
from export_v2 import canon, evidence, mapping  # noqa: E402
from test_evidence import make_tree  # noqa: E402

SITE = os.environ.get("FORSHOR_SITE_DIR")
pytestmark = pytest.mark.slow


def ts_validate(path: Path) -> dict:
    out = subprocess.run(["npx", "tsx", "scripts/publisher/validate-v2.ts", str(path)], cwd=SITE, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def seed_policy():
    from export_v2 import engine
    eng = engine.load()
    rec = next(r for r in eng.submitted.seeded() if r["mode"] == "PhaseProduct")
    return eng.seeded_board.policy_of(rec).to_json()


@pytest.fixture(scope="module")
def model():
    from export_v2 import epoch
    return epoch.model_of()


def request_for(policy_json, model, store, **over):
    from export_v2 import request
    args = dict(model=model, store=store, head_sha="a" * 40, pull_number=77, github_login="fixture-user", run_id="7700",
                submitted_at="2026-11-05T17:00:00Z", evaluated_at="2026-11-05T18:00:00Z", archive_commit="b" * 40,
                accepted_ids=["a" * 40], evaluation_key="e" * 64, base_generation="1", number=1)
    args.update(over)
    return request.build_request(policy_json, **args)


def test_a_policy_without_valid_evidence_is_not_publishable(seed_policy, model):
    with pytest.raises(ValueError, match="not publishable"):
        request_for(seed_policy, model, evidence.EvidenceStore(None))


def test_a_request_for_a_real_policy_is_complete_consistent_and_accepted_by_the_typescript_validator(tmp_path, seed_policy, model):
    ev = tmp_path / "ev"
    evidence.run_mirror(seed_policy, "pp", make_tree(tmp_path / "t", pin=model["forshor"]["pin"], lean=model["lean"]), ev)   # the live epoch's pins, not the golden file's
    req = request_for(seed_policy, model, evidence.EvidenceStore(ev))
    assert [(p["board"], p["delta"]) for p in req["priced"]] == [(b, d) for d in mapping.DELTA_KEYS for b in ("pp", "full")]
    assert req["submission"]["boards"] == ["pp", "full"]
    assert req["submission"]["policyDigest"] == canon.policy_digest(seed_policy)
    for p in req["priced"]:
        assert p["panel"]["verification"] == req["submission"]["verification"]
        assert p["panel"]["epoch"] == canon.epoch_of(model)
        assert "rank" not in p["row"] and p["row"]["origin"] == "submission" and p["row"]["submissionId"] == "a" * 40
    # a PhaseProduct panel reproduces the engine's own T; the full panel carries the whole run
    pp = next(p for p in req["priced"] if p["board"] == "pp" and p["delta"] == "1e-2")
    assert pp["row"]["T"] == pp["panel"]["T"] and pp["panel"]["fullRun"] is None
    full = next(p for p in req["priced"] if p["board"] == "full" and p["delta"] == "1e-2")
    assert full["row"]["T"] == full["panel"]["fullRun"]["T"]
    out = tmp_path / "request.json"
    out.write_text(json.dumps(req))
    if SITE:
        summary = ts_validate(out)
        assert summary["kind"] == "publish-request" and summary["priced"] == 8 and summary["bytes"] < 400_000
    else:
        pytest.skip("FORSHOR_SITE_DIR not set: built the request but did not run the TypeScript validator")


def test_every_baseline_policy_has_at_least_one_band_and_is_distinct():
    """The harness refuses a policy with no bands ("bands must be a nonempty list"), so a bare-base-case baseline must never be listed."""
    from export_v2 import epoch
    baselines = epoch.baseline_policies()
    assert baselines, "the engine yields baselines"
    assert all(b["policy"].bands for b in baselines)
    assert len({b["digest"] for b in baselines}) == len(baselines)
    assert len({b["number"] for b in baselines}) == len(baselines)


def test_a_policy_whose_table_negates_is_recognised_and_the_listed_ones_are_not():
    """The guard behind the swap: a policy built on a negating library table is flagged, and no baseline is."""
    from export_v2 import epoch, engine
    eng = engine.load()
    tables = eng.library.sto_tables(upto=len(eng.library.submissions()))
    neg = next(t for t in tables if t.mode == eng.tables.PAIR and t.census()["negate"])
    P, _ = eng.planner.optimal_policy([neg], eng.tables.PAIR, 2048, 0.01, name="negating")
    assert epoch.negating_bands(eng.seed_policies.canonical(P)) == [neg.k]
    assert all(epoch.negating_bands(b["policy"]) == [] for b in epoch.baseline_policies())
