"""The v2 CI client's recovery loop, with fakes for the network, the archive and the (expensive) pricing."""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # evaluation/
from publish import RetryPublication  # noqa: E402
import publish_v2 as P  # noqa: E402

MODEL = {"id": "m", "n": 2048}
KEY = "k" * 64
META = {"evaluationKey": KEY, "headSha": "a" * 40, "runId": "7"}
T0 = dt.datetime(2026, 11, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


def state(generation="g1", updated="2026-11-05T12:00:00.000Z", done=None, model=MODEL, number=22):
    return {"baseGeneration": generation, "epoch": "e", "model": model, "publication": {"snapshotId": "s", "updatedAt": updated},
            "submissionIds": [], "nextNumber": number, "completedEvaluations": done or {}}


class Harness:
    def __init__(self, states, sends):
        self.states, self.sends, self.sent, self.priced, self.slept = list(states), list(sends), [], 0, []

    def fetch(self):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def price(self):
        self.priced += 1
        return {"schema": "forshor.publish/v2", "evaluation": {"headSha": "a" * 40}, "archive": {},
                "priced": [{"row": {"name": "forshor_sub0"}}, {"row": {"name": "forshor_sub0"}}]}

    def send(self, req):
        self.sent.append(req)
        r = self.sends.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def run(self, **kw):
        return P.publish_v2_with_recovery(META, MODEL, self.fetch, self.price, lambda: (["x"], "arch1"), self.send,
                                          sleep=self.slept.append, now=lambda: T0, **kw)


def test_a_completed_evaluation_is_not_repriced_or_sent():
    h = Harness([state(done={KEY: {"headSha": "a" * 40, "runId": "7"}})], [])
    assert h.run() == {"alreadyPublished": True, "evaluationKey": KEY}
    assert h.priced == 0 and h.sent == []


def test_the_same_key_with_another_commit_is_a_collision():
    h = Harness([state(done={KEY: {"headSha": "b" * 40, "runId": "7"}})], [])
    with pytest.raises(ValueError, match="collision"):
        h.run()
    assert h.priced == 0


def test_a_changed_model_is_refused_before_pricing_and_never_retried():
    h = Harness([state(model={"id": "other"})], [])
    with pytest.raises(P.ModelMismatch):
        h.run()
    assert h.priced == 0 and h.sent == [] and h.slept == []


def test_the_request_carries_the_live_generation_archive_view_and_a_later_time():
    h = Harness([state(updated="2026-11-05T12:00:00.000Z")], [{"snapshotId": "s2"}])
    assert h.run() == {"snapshotId": "s2"}
    r = h.sent[0]
    assert r["baseGeneration"] == "g1" and r["evaluationKey"] == KEY
    assert r["archive"]["acceptedIds"] == ["x"] and r["evaluation"]["archiveCommit"] == "arch1"
    assert r["evaluation"]["evaluatedAt"] == "2026-11-05T12:00:00.001Z"   # the clock is not later than the live snapshot: +1 ms


def test_the_time_is_the_clock_when_it_is_later():
    h = Harness([state(updated="2026-11-05T11:00:00.000Z")], [{"snapshotId": "s2"}])
    h.run()
    assert h.sent[0]["evaluation"]["evaluatedAt"] == "2026-11-05T12:00:00.000Z"


def test_a_conflict_rereads_state_and_resends_without_repricing():
    h = Harness([state("g1"), state("g2", updated="2026-11-05T12:30:00.000Z")], [RetryPublication("409"), {"snapshotId": "s3"}])
    assert h.run() == {"snapshotId": "s3"}
    assert h.priced == 1 and h.slept == [1]
    assert [s["baseGeneration"] for s in h.sent] == ["g1", "g2"]
    assert h.sent[1]["evaluation"]["evaluatedAt"] == "2026-11-05T12:30:00.001Z"
    assert h.sent[0] is not h.sent[1]


def test_a_lost_acknowledgement_is_recognised_on_the_next_read():
    h = Harness([state("g1"), state("g2", done={KEY: {"headSha": "a" * 40, "runId": "7"}})], [RetryPublication("timeout")])
    assert h.run() == {"alreadyPublished": True, "evaluationKey": KEY}
    assert len(h.sent) == 1


def test_it_gives_up_after_the_attempts_and_says_why():
    h = Harness([state()], [RetryPublication("409")] * 3)
    with pytest.raises(RetryPublication):
        h.run(attempts=3)
    assert h.slept == [1, 2] and len(h.sent) == 3


def test_every_row_is_named_from_the_live_next_number_and_a_retry_renames_it():
    h = Harness([state("g1", number=22), state("g2", updated="2026-11-05T12:30:00.000Z", number=23)], [RetryPublication("409"), {"snapshotId": "s3"}])
    h.run()
    assert [[p["row"]["name"] for p in s["priced"]] for s in h.sent] == [["forshor_sub22"] * 2, ["forshor_sub23"] * 2]
    assert h.priced == 1, "still priced once"
