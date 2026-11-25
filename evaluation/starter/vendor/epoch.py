"""The engine model this pack's pricing rules are checked against (the starter pack's own copy of `epoch.model_of`).

The published boards identify their engine model by the epoch id, the first 16 hex characters of the model's
canonical sha256 (`canon.epoch_of`). The readout and QFT design rules in this folder refuse to price under any other
model. The repository computes the model from its own checkout, git history included, which the pack does not
have, so the pack builder records two things in `model.json`:

  * `model`: the boards' model, computed by the repository at the commit the pack was built from;
  * `packEngineFingerprint`: the fingerprint of the pack's own `engine/` (`full_frontier.fingerprint()`), taken
    when the pack was built.

`model_of()` returns the recorded model only while the pack's engine still has that fingerprint. If a file the
fingerprint covers has changed, it returns the model with the engine's current fingerprint, whose epoch id is a
different one, and every rule here then refuses to price.
"""
import json
from pathlib import Path

from . import engine

MODEL = Path(__file__).resolve().with_name("model.json")


def model_of():
    recorded = json.loads(MODEL.read_text(encoding="utf-8"))
    live = engine.load().full_frontier.fingerprint()
    if live != recorded["packEngineFingerprint"]:
        return {**recorded["model"], "dataFingerprint": live}
    return recorded["model"]
