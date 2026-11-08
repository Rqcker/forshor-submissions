r"""Score a ForShor policy submission (Stage 5): ``trial_count x`` the single-run cost of its native IR.

    python -m forshor_qualtran.policy_score policy.json [--out score.json]

ForShor's ``score`` field: "trial_count x (single-run gate count of the IR below, as measured by
Qualtran)". This reads ForShor's own ``forshor_policy_submission`` output and prices it:

1. **Refuse, never repair.** Any check that fails stops with :class:`ScoreRefused` (exit code 2):
   - *schema*: ``forshor.policy-submission/v1``, a non-null ``ir`` of schema ``forshor.ir/v1`` with
     every required template, one body per band and flavour, each of its table's arity;
   - *binding*: every band body runs its own band's table (`policy_ir.verify_native_binding`);
   - *version*: the benchmark is ``bits`` with ForShor's representative modulus ``2^(bits-1)``, a
     positive integer ``trial_count`` and precision ``m``; the ``shor`` template has the signature this
     pricing was validated against.
2. **Price** the ``shor`` template at ``n = bits`` (`policy_shor_price.PolicyShorCore`): the census in
   closed form in ``n``, every site a policy bloq, nothing flattened. The headline is the QUALTRAN
   tier, with LEAN_FAITHFUL as the cross-check. Rotations are Ross–Selinger at
   ``eps = delta_rot / R_total``.
3. **Score** = ``trial_count x T`` (headline). The Qualtran gate counts of the same bloq
   (`QECGatesCost`) are reported alongside, and so are ForShor's Lean resource channels.

The single-run cost is *not* constant on ``[2^(bits-1), 2^bits)`` (ForShor's trial count is): a
modulus in the upper half of the binade has one more exponent bit. The score is at the representative
modulus, which is in the lower half, and ``upper_half`` reports the other case.

Claim tier: an evaluation at ``n = bits`` of a structure validated against ForShor's IR at small
``N`` (`tests/test_policy_shor_price.py`). The IR is ForShor's "equal to the real circuit at sampled
widths", not a theorem, and the ``shor`` template is the approximate Algorithm-1 core.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union

from . import cost_conventions as cc
from . import policy as pol
from . import policy_ir as pi
from . import policy_shor_price as pr
from . import policy_shor_sym as sy

SCORE_SCHEMA = "forshor_qualtran.policy-score/v1"
IR_SCHEMA = "forshor.ir/v1"
#: `policy_shor_ir.shor_env`'s interface: the signature the census and pricing were validated against.
SHOR_W_PARAMS = ("a", "N", "xW", "xCap", "yW", "yCap", "workW", "workCap", "scratchW", "scratchCap")
SHOR_R_PARAMS = ("x", "y", "work", "scratch", "flag")


class ScoreRefused(ValueError):
    """The document failed a schema, binding or version check. Nothing was priced."""

    def __init__(self, check: str, msg: str):
        super().__init__(f"[{check}] {msg}")
        self.check = check


def _digits(v: Any, what: str) -> int:
    s = str(v)
    if not s.isdigit() or int(s) <= 0:
        raise ScoreRefused("version", f"{what} must be a positive integer, got {v!r}")
    return int(s)


def check_submission(sub: Dict[str, Any]) -> Dict[str, Any]:
    """Every refusal check, in order. Returns ``{native, bits, m, trial_count, binding}``."""
    try:
        nat = pi.load_native(sub)
    except (pol.PolicyError, ValueError, KeyError, TypeError) as e:
        raise ScoreRefused("schema", f"{type(e).__name__}: {e}") from None     # incl. an `ir` not forshor.ir/v1
    t = nat.doc.template("shor")
    if (tuple(t.wParams), tuple(t.rParams)) != (SHOR_W_PARAMS, SHOR_R_PARAMS):
        raise ScoreRefused("version", f"`shor` template signature {t.wParams} / {t.rParams} is not the "
                                      f"validated {SHOR_W_PARAMS} / {SHOR_R_PARAMS}")
    bench, prec = sub.get("benchmark"), sub.get("precision")
    if not isinstance(bench, dict) or not isinstance(prec, dict):
        raise ScoreRefused("version", "submission lacks `benchmark` or `precision`")
    bits = _digits(bench.get("bits"), "benchmark.bits")
    if not str(bench.get("modulus_expr", "")).startswith(f"2^{bits - 1}"):
        raise ScoreRefused("version", f"benchmark modulus {bench.get('modulus_expr')!r} is not the "
                                      f"representative 2^{bits - 1}")
    trial = _digits(bench.get("trial_count"), "benchmark.trial_count")
    m = _digits(prec.get("m"), "precision.m")
    try:
        binding = pi.verify_native_binding(nat)
    except pi.NativeIRError as e:
        raise ScoreRefused("binding", str(e)) from None
    return {"native": nat, "bits": bits, "m": m, "trial_count": trial, "binding": binding}


def _qec(bloq) -> Dict[str, int]:
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    return {k: int(v) for k, v in get_cost_value(bloq, QECGatesCost()).asdict().items()}


def score(sub: Union[Dict[str, Any], str, Path], *, delta: Optional[float] = None) -> Dict[str, Any]:
    """The score document for one submission (deterministic: no timestamp)."""
    raw = sub if isinstance(sub, dict) else json.loads(Path(sub).read_text(encoding="utf-8"))
    chk = check_submission(raw)
    nat, bits, trial = chk["native"], chk["bits"], chk["trial_count"]
    head_spec = cc.QUALTRAN if delta is None else cc.QUALTRAN.with_(delta=delta)
    cross_spec = cc.LEAN_FAITHFUL if delta is None else cc.LEAN_FAITHFUL.with_(delta=delta)
    N = 1 << (bits - 1)
    conv = sy.Convention.for_modulus(N, chk["m"])
    core = pr.PolicyShorCore.from_native(nat, bits, conv, head_spec)
    head, cross = pr.price(core, head_spec), pr.price(core, cross_spec)
    upper = pr.price(pr.PolicyShorCore.from_native(
        nat, bits, sy.Convention(popcount=1, extra_bits=conv.extra_bits, upper_binade=True), head_spec))
    qec = _qec(pr.PolicyShorCore.from_native(nat, bits, conv, head_spec.with_(r_context=head["rotations"])))
    canon = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema": SCORE_SCHEMA,
        "input": {"submission_schema": raw.get("schema"), "ir_schema": IR_SCHEMA,
                  "sha256_canonical": hashlib.sha256(canon).hexdigest()},
        "policy": [{"index": i, "threshold": b.threshold, "k": b.table.k, "table_sha256": pol.table_sha256(b.table)}
                   for i, b in enumerate(nat.policy.bands)],
        "checks": {"schema": "ok", "version": "ok", "binding": chk["binding"]},
        "instance": {"bits": bits, "modulus": f"2^{bits - 1}", "m": str(chk["m"]),
                     "extra_bits": conv.extra_bits, "popcount": conv.popcount,
                     "widths": dict(core.widths), "layout_qubits": core.layout_qubits()},
        "single_run": {
            "headline": {"tier": head_spec.name, **{k: v for k, v in head.items() if k != "convention"},
                         "qualtran_gate_counts": qec},
            "cross_check": {"tier": cross_spec.name, **{k: v for k, v in cross.items() if k != "convention"}},
            "lean_resources": core.lean_resources(),
            "conventions": {"headline": head["convention"], "cross_check": cross["convention"]},
        },
        "score": {"metric": "trial_count x single-run T (headline tier; rotations Ross-Selinger at "
                            "eps = delta_rot / R_total)",
                  "trial_count": str(trial), "single_run_T": head["T"], "value": trial * head["T"],
                  "cross_check_value": trial * cross["T"]},
        "upper_half": {"note": f"a modulus in [2^({bits}-1/2), 2^{bits}) has a {2 * bits}-bit exponent register; "
                               "ForShor's trial count is the same there, the single-run T is not",
                       "single_run_T": upper["T"], "value": trial * upper["T"]},
        "claim_tier": "evaluation at n = bits of a structure validated against ForShor's native IR at small N; "
                      "the IR is 'equal to the real circuit at sampled widths', not a theorem; the shor "
                      "template is the approximate Algorithm-1 core",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("submission", type=Path, help="ForShor's policy.json (forshor_policy_submission output)")
    ap.add_argument("--out", type=Path, help="write the score document here (default: stdout)")
    ap.add_argument("--delta", type=float, help="total error budget (default: the tier's, 0.01)")
    a = ap.parse_args(argv)
    try:
        doc = score(a.submission, delta=a.delta)
    except ScoreRefused as e:
        print(f"REFUSED {e}", file=sys.stderr)
        return 2
    text = json.dumps(doc, indent=1, sort_keys=True)
    if a.out:
        a.out.write_text(text + "\n")
        s = doc["score"]
        print(f"score {s['value']:.6e} = {s['trial_count']} x {s['single_run_T']:.6e} T  (-> {a.out})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
