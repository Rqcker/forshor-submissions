"""A measured (semiclassical) readout and a full-run QFT design, priced TOGETHER in one census.

This module does not grant publication authority by itself, and it is NOT an epoch input (it
lives in `export_v2` for the reason `qft_overlay` does). It changes neither overlay it composes:
`readout_overlay.py` is a cross-repository byte pin (its sha256 is the published readout `ruleSha256`) and
`qft_overlay.py`'s own fingerprint is carried by every design-only panel, so both are imported, never edited.

WHY THE TWO OVERLAYS COULD NOT SIMPLY BE STACKED
-----------------------------------------------------------------------------------------------------
Each overlay starts from the SAME frozen coherent census `(T_base, R)` of `fullalg.price_full` and returns a
FINAL price `T = T_base' + R' * rate(R')`. The per-rotation rate is nonlinear in the run's rotation total
(`sto_currency`: eps = budget / R, T(eps) = 3.075 log2(1/eps) + 0.86), so two finals cannot
be added or chained, and on the exponent register the readout deletes the very QFT a design would band.

WHAT EACH OVERLAY ACTUALLY SUBSTITUTES (read off the code, not the docs)
-----------------------------------------------------------------------------------------------------
Both are census PATCHES followed by ONE division of the pool; only the final division made them look like
finals:

* `readout_overlay.project` touches ONE part: the unique exponent IQFT (`site == "qft"`, `xw == xW`,
  `count == 1`). It subtracts that part's `(base, rot)` -- cross-checked against `PolicyQFT(xW)` -- and adds the
  `rounds - 1 = xW - 1` feed-forward phase rotations, priced Ross-Selinger at the run's rate. Qubits become the
  recycled control plus the registers without the exponent plus the multiplier's measured peak.
* `qft_overlay.price_design` touches the QFT part of each DESIGNED family (`data` at yW+1, `work` at workW,
  `scratch` at scratchW): a Ross-Selinger approximate family is replaced by its closed-form band, a catalytic
  family by its merged twiddle rows (no rotations), and the families' error allowances (plus the gradient's
  preparation budget when a catalyst exists) are taken out of the pool before it is divided.

THE JOINT RULE
-----------------------------------------------------------------------------------------------------
On the `data`, `work` and `scratch` families the two patches touch DISJOINT parts of the census (distinct
widths, and the readout's part is the one at xW), so they compose by applying both to the census and dividing
the pool ONCE over the combined rotation total. This module does not restate the design's rule: it hands the
readout's patched census to `qft_overlay.price_design`, the same function `qft_overlay.project` prices with:

    TBase_joint = readoutTBase + sum_f (designed_f.base - exact_f.base) * count_f
    R_joint     = readoutRotations + sum_f (designed_f.rot - exact_f.rot) * count_f
    rest        = pool - sum allowances - prep,   eps = rest / R_joint   (the feed-forward phases are in R_joint)
    T_joint     = TBase_joint + R_joint * ts_per_rotation_eps(eps) + catalyst.T
    qubits      = readout_overlay's measured qubits + catalyst b + any merged-row workspace beyond its peak extra

With no design it IS `readout_overlay.project`'s numbers, and with the readout off it IS `qft_overlay.project`'s,
bit for bit (`pytests/test_readout_qft_overlay.py` pins both reductions against the real engine).

THE QUBIT BOUND. `readout_overlay`'s measured count is the multiplier's peak, and banding only REMOVES a QFT's
workspace, so the measured count stays an upper bound for a banded run. The catalyst register is an ADDITION
and is charged, and so is any merged-row workspace beyond the measured peak extra.

THE EXPONENT FAMILY IS REFUSED BY NAME under a measured readout -- any value, `exact` included -- and never
re-interpreted as a choice about the feed-forward phases. Justification, from the code:

  1. The site the family names does not exist in the measured circuit: `readout_overlay.project` requires and
     removes the unique exponent IQFT, so there is no `PolicyQFT(xW)` part left to band or to catalyse.
  2. Re-reading it as "band the feed-forward phases" would be a different construction priced on the wrong
     one: the semiclassical loop applies ONE single-qubit phase per round whatever the band (a truncated
     dyadic angle is still one rotation), so substituting `ReferenceQFT(xW, k_max)` -- a coherent ladder of
     controlled rotations -- would subtract rotations the measured circuit never had. Catalysing them would
     price classically-controlled constant phases as `qft_overlay`'s merged twiddle rows, which is not the
     circuit either. The readout rule already prices every feed-forward phase, at the run's
     Ross-Selinger rate, with no catalyst and no scratch.
  3. `exact` is not a harmless no-op here: for `qft_overlay` it means "leave the exponent QFT as the engine
     priced it", and under a semiclassical readout that QFT is not in the circuit, so the request names a
     choice about a site that does not exist. It is reported rather than silently dropped.

A design family whose width coincides with the readout's register (xW) is refused for the same reason: its
part would be the one the readout removed.

FAIL CLOSED. Every refusal is a `ReadoutQftRefused` (a `qft_overlay.QftOverlayRefused`, so a `ValueError`)
naming the family and the value. Nothing here is priced by either overlay alone.
"""
from hashlib import sha256
from pathlib import Path

from . import engine, epoch, qft_overlay, readout_overlay
from .canon import epoch_of
from .mapping import DELTA_VALUES

EPOCH = "ab1f8887b1b50e56"
RULE = "readout-qft-compose-v2"
MODE = "semiclassical-readout-with-qft-design"


class ReadoutQftRefused(qft_overlay.QftOverlayRefused):
    """A measured readout + QFT design request this rule will not price. It is never priced by either overlay alone."""


def _refuse(message):
    raise ReadoutQftRefused(message)


def _require(condition, message):
    if not condition:
        _refuse(message)


def rule_fingerprint():
    """This rule's own identity: the sha256 of these bytes, as `qft_overlay.rule_fingerprint` is of its own."""
    return sha256(Path(__file__).read_bytes()).hexdigest()


def component_rules():
    """The fingerprints of the two rules this one composes. A composed price moves when either moves, so the
    panel carries both beside this module's own."""
    return {"readout": readout_overlay.rule_fingerprint(), "qft": qft_overlay.rule_fingerprint()}


def check_design_under_readout(design):
    """Refuse, by name, a design a measured readout cannot honour; return `qft_overlay.parse_design`'s plan.

    The value rules stay `qft_overlay`'s (one validator in the repo). This adds the one cross-field rule a
    semiclassical readout imposes: no `exponent` key at all (see the module docstring)."""
    plan = qft_overlay.parse_design(design)
    if design is not None and "exponent" in design:
        _refuse(f"QFT family 'exponent' ({design['exponent']!r}) cannot be priced under a semiclassical "
                f"readout: the readout replaces the exponent IQFT by measured feed-forward phases, so there is "
                f"no exponent QFT left to band or catalyse, and the feed-forward phases are a different "
                f"construction priced by the readout rule. Re-submit without 'exponent' (or without the "
                f"measured readout)")
    for family, (_backend, target, max_error) in plan.items():
        if target == "approximate":
            qft_overlay.parse_max_error(family, max_error)
    qft_overlay.check_budget(plan)
    return plan


def design_of_measured(selection):
    """The engine `design` for a compiled full-run selection on a MEASURED document, or None for no design.

    Applies the readout's cross-field rule to the RAW selection body first: `full_run_design.design_of` collapses a
    design that asks for nothing (every family exact Ross-Selinger) to None, so `{"exponent": exact}` alone would
    otherwise reach pricing as "no design" and the `exponent` key would be dropped instead of refused. Then converts
    through `design_of` (topology gate, profile, phase-product and rotation-backend refusals, value rules) and checks
    the converted design again. Raises `ValueError` by name."""
    from . import full_run_design

    if isinstance(selection, dict):
        body = selection.get(full_run_design.DESIGN_KEY)
        qft = body.get(full_run_design.QFT_KEY) if isinstance(body, dict) else None
        if isinstance(qft, dict) and "exponent" in qft:
            check_design_under_readout({"exponent": qft["exponent"]})
    design = full_run_design.design_of(selection)
    if design is not None:
        check_design_under_readout(design)
    return design


def _joint(policy, coherent, design, *, readout):
    """The joint census and price. `readout=False` is `qft_overlay.project`'s rule exactly, `design=None` with
    `readout=True` is `readout_overlay.project`'s; the tests hold both reductions bit for bit."""
    eng = engine.load()
    fa = eng.fullalg
    _require(epoch_of(epoch.model_of()) == EPOCH,
             f"the composed readout + QFT rule is qualified against engine model {EPOCH} only; this tree's "
             f"model is {epoch_of(epoch.model_of())}, so the combination is reported as unpriced")
    _require(coherent["n"] == fa.HERO_N and coherent["width_rule"] == "exact"
             and coherent["negate"] == fa.HEADLINE.negate and coherent["delta"] in DELTA_VALUES.values(),
             "the composed readout + QFT rule requires the current exact reference price")
    _require("parts" in coherent and coherent["parts"],
             "the composed readout + QFT rule needs the per-part census; price with detail=True")
    _require("qubits_peak" in coherent and "qubits" in coherent,
             "the composed readout + QFT rule restates a qubit total, so it needs the coherent result's own "
             "`qubits_peak` and `qubits`")

    plan = check_design_under_readout(design) if readout else qft_overlay.parse_design(design)
    measured = None
    if readout:
        # The readout's census patch, taken from the pinned rule itself rather than restated: its TBase and
        # rotations ARE the patched census (the IQFT removed, the xW - 1 feed-forward phases added).
        measured = readout_overlay.project(policy, coherent)
        base, rotations, peak_extra = int(measured["TBase"]), int(measured["rotations"]), measured["peakExtra"]
    else:
        base, rotations, peak_extra = int(coherent["T_base"]), int(coherent["rotations"]), coherent["peak_extra"]
    j = qft_overlay.price_design(policy, coherent, plan, base=base, rotations=rotations, peak_extra=peak_extra,
                                 refuse=_refuse, skip_width=measured["rounds"] if measured else None)
    return dict(j, measured=measured)


def project(policy, coherent, design):
    """Price `policy`'s MEASURED run with the QFT design applied, from the frozen coherent result.

    `design` is the compiled per-family design (`full_run_design.design_of`). The answer carries the readout's
    own census patch beside the joint one, so a consumer can re-derive every number from the coherent parts."""
    j = _joint(policy, coherent, design, readout=True)
    m = j["measured"]
    qubits = int(m["qubits"]) + j["catalystQubits"] + j["workspaceQubits"]
    return {
        "mode": MODE, "pricingRule": RULE, "ruleSha256": rule_fingerprint(), "componentRules": component_rules(),
        "epoch": EPOCH,
        "T": j["T"], "TBase": j["TBase"], "rotations": j["rotations"], "tPerRotation": j["tPerRotation"], "eps": j["eps"],
        # The readout's half of the census, verbatim from `readout_overlay.project`.
        "rounds": m["rounds"], "iqftTBase": m["iqftTBase"], "iqftRotations": m["iqftRotations"],
        "feedForwardRotations": int(m["rounds"]) - 1,
        "readoutTBase": m["TBase"], "readoutRotations": m["rotations"],
        # The design's half, in `qft_overlay.project`'s per-family shape.
        "families": j["families"], "substituted": j["substituted"], "catalysed": j["catalysed"],
        "catalyst": j["catalyst"], "identity": j["identity"], "budget": j["budget"],
        "peakExtra": m["peakExtra"], "readoutQubits": m["qubits"], "catalystQubits": j["catalystQubits"],
        "workspaceQubits": j["workspaceQubits"], "qubits": qubits,
        "tNote": "T = TBase + rotations * tPerRotation + catalyst.T, with TBase = readoutTBase + sum(families: (designed "
                 "base - exactTBase) * count) and rotations = readoutRotations + sum(families: (designed rotations - "
                 "exactRotations) * count); tPerRotation divides the synthesis budget (the pool less the families' "
                 "allowances and the gradient's preparation) once over that joint total",
        "qubitsNote": "the measured circuit's qubits (readoutQubits) plus catalystQubits and workspaceQubits. Banding "
                      "only REMOVES workspace, so the measured count stays an upper bound for it",
    }
