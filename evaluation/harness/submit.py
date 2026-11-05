"""One submission end to end (PLAN_v2 Stage 16): a full policy (tables + thresholds) through ShorTableOptimization's
verification and ForShor's build, with the two required to agree.

  1. STO: the policy mirror (PP) or the PTP suite (PTP) at exact widths (`sto_policy_verify` / `ptp_policy_verify`):
     tables against STO's contract, thresholds, the exact ladder proved by the kernel.
  2. ForShor (PP only): the policy as a ForShor PolicyTemplate, built in the drop-in tree (`.cache/forshor_dropin_ab`:
     ForShor 10fe620 + checkpoint A+B, kernel-checked C1-C4 and run-time IR extraction, so k = 5/6 and 3+ bands pass),
     `forshor_policy_submission` printing policy.json (`forshor_verify.run`).
  3. Agreement: the policy ForShor printed has the submitted bands, and its own IR prices to the site's full-run price
     of the submitted policy (`native.conversion_agrees`).

Prices reported: the PP/PTP board's T (exact widths, Python) and, for PP, the full order-finding run on ForShor's IR.

    ./run.sh submit <site_policy.json> [--out summary.json] [--forshor TREE]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(HERE))

import forshor_verify as fv  # noqa: E402  (binds the vendored Qualtran first)
import ptp_policy_verify as ppv  # noqa: E402
import sto_policy_verify as spv  # noqa: E402
from site_engine import intake  # noqa: E402
from tcost import tables as tt  # noqa: E402

DROPIN = ROOT / ".cache" / "forshor_dropin_full"   # Stage 18: exact widths + balanced split (--balanced --probe-scan)
PP, PTP = "PhaseProduct", "PhaseTripleProduct"


def submit(path: Path, forshor: Path = DROPIN, delta: float = 0.01) -> Dict[str, Any]:
    text = path.read_text()
    doc = json.loads(text)
    t0 = time.time()
    board = intake.cost(text, delta=delta)
    pol = board["policies"][0]
    mode = pol.get("mode") or tt.load_policies(path)[0].mode
    out: Dict[str, Any] = {"policy": str(path), "mode": mode, "delta": delta,
                           "board": {"T": pol["point"]["T"], "ladder": pol["point"].get("ladder"),
                                     "widths_notice": board.get("widths_notice")}}
    t = time.time()
    spv.wait_lean_slot(limit=3)                       # the machine-wide limit (3 lean processes), not "no other lean"
    sto = (ppv.check(path) if mode == PTP else spv.check(path, widths="exact", guard_busy=False))
    out["sto"] = {k: sto.get(k) for k in ("verdict", "stage", "reason", "exact_ladder", "exact_ladder_kernel_proved", "seconds",
                                          "peak_gb")}
    out["sto"]["wall_s"] = round(time.time() - t, 1)
    if mode == PTP or sto.get("verdict") != "accepted":
        out["verdict"] = "sto-" + str(sto.get("verdict")) if sto.get("verdict") != "accepted" else "accepted (PTP: no ForShor circuit)"
        out["wall_s"] = round(time.time() - t0, 1)
        return out
    t = time.time()
    try:
        fs = fv.run(path, forshor=forshor, from_policy=True, delta=delta)
    except fv.HarnessError as e:
        out["forshor"] = {"verdict": "rejected", "stage": e.stage, "reason": e.detail, "wall_s": round(time.time() - t, 1)}
        out["verdict"] = "forshor-rejected"
        out["wall_s"] = round(time.time() - t0, 1)
        return out
    submitted = [(int(b.threshold), int(b.table.k)) for b in tt.load_policies(path)[0].bands]
    printed = [tuple(map(int, b)) for b in fs["bands"]]
    native = fs.get("native") or {}
    out["forshor"] = {"verdict": fs["status"], "tree": str(forshor), "commit": (fs.get("forshor") or {}).get("commit"),
                      "seconds": fs.get("seconds"), "wall_s": round(time.time() - t, 1),
                      "full_run_T": fs.get("single_run_T"), "T_document": native.get("T_document"),
                      "T_converted_policy": native.get("T_converted_policy")}
    out["agreement"] = {"bands": printed == submitted, "price": bool(native.get("conversion_agrees")),
                        "submitted_bands": submitted, "forshor_bands": printed}
    ok = fs["status"] == "verified" and all(out["agreement"][k] for k in ("bands", "price"))
    out["verdict"] = "accepted" if ok else "disagreement"
    out["wall_s"] = round(time.time() - t0, 1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("policy", type=Path, help="a site policy JSON (tables + thresholds)")
    ap.add_argument("--forshor", type=Path, default=Path(os.environ.get("FORSHOR_DROPIN", DROPIN)))
    ap.add_argument("--delta", type=float, default=0.01)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    r = submit(a.policy, a.forshor, a.delta)
    s = json.dumps(r, indent=1, default=str)
    if a.out:
        a.out.write_text(s + "\n")
    print(s)
    return 0 if r["verdict"].startswith("accepted") else 1


if __name__ == "__main__":
    sys.exit(main())
