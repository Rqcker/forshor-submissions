"""A measured (semiclassical) readout and a full-run QFT design, priced TOGETHER in one census.

This module does not grant publication authority by itself, and it is NOT an epoch input (it
lives in `export_v2` for the reason `qft_overlay` does). It changes neither overlay it composes:
`readout_overlay.py` is a cross-repository byte pin (its sha256 is the published readout `ruleSha256`) and
`qft_overlay.py`'s own fingerprint is carried by every design-only panel, so both are imported, never edited.

WHY THE TWO OVERLAYS COULD NOT SIMPLY BE STACKED
-----------------------------------------------------------------------------------------------------
Each overlay starts from the SAME frozen coherent census `(T_base, R)` of `fullalg.price_full` and returns a
FINAL price `T = T_base' + R' * ts_per_rotation(delta, R')`. The per-rotation rate is nonlinear in the run's
rotation total (`sto_currency`: eps = 0.5 delta / R, T(eps) = 3.075 log2(1/eps) + 0.86), so two finals cannot
be added or chained, and on the exponent register the readout deletes the very QFT a design would band.

WHAT EACH OVERLAY ACTUALLY SUBSTITUTES (read off the code, not the docs)
-----------------------------------------------------------------------------------------------------
Both are census PATCHES followed by ONE re-division of the pool; only the final division made them look like
finals:

* `readout_overlay.project` touches ONE part: the unique exponent IQFT (`site == "qft"`, `xw == xW`,
  `count == 1`). It subtracts that part's `(base, rot)` -- cross-checked against `PolicyQFT(xW)` -- and adds the
  `rounds - 1 = xW - 1` feed-forward phase rotations, priced Ross-Selinger at the run's rate. Qubits become the
  recycled control plus the registers without the exponent plus the multiplier's measured peak.
* `qft_overlay.project` touches the QFT part of each DESIGNED family (`data` at yW+1, `work` at workW,
  `scratch` at scratchW, `exponent` at xW): an `approximate` target replaces that part's `(base, rot)` by a
  Coppersmith-banded `ReferenceQFT(w, k_max)`'s, scaled by the part's count; a `catalytic` backend moves the
  family's surviving rotations onto one shared catalyst whose arm takes the proportional slice
  `deltaArm = pool * R_cat / R_total` of the one pool, with the survivors keeping the run's rate.

THE JOINT RULE
-----------------------------------------------------------------------------------------------------
On the `data`, `work` and `scratch` families the two patches touch DISJOINT parts of the census (distinct
widths, and the readout's part is the one at xW), so they compose by applying both to the census and dividing
the pool ONCE over the combined rotation total:

    TBase_joint = T_base - iqft.base + sum_f (band_f.base - exact_f.base) * count_f
    R_joint     = R      - iqft.rot  + (xW - 1) + sum_f (band_f.rot - exact_f.rot) * count_f
    rate        = ts_per_rotation(delta, R_joint),  eps = 0.5 delta / R_joint
    T_joint     = TBase_joint + (R_joint - R_cat) * rate + catalyst.T
    catalyst    = qft_overlay's arm, with deltaArm = pool * R_cat / R_joint  (the JOINT total: the feed-forward
                  rotations are in the census the pool is partitioned over, and paid Ross-Selinger)
    qubits      = readout_overlay's measured qubits + catalyst b (when a catalyst exists)

The QFT substitutions and the `xW - 1` feed-forward phases feed ONE rotation divisor. The currency is
`qft_overlay`'s: no QFT ceiling is carved out of the pool, because `qft_overlay` does not carve one either (the
readout-off reduction below would otherwise fail). With no design it IS `readout_overlay.project`'s
numbers, and with the readout off it IS `qft_overlay.project`'s, bit for bit
(`pytests/test_readout_qft_overlay.py` pins both reductions against the real engine).

THE QUBIT BOUND. `readout_overlay`'s measured count is the multiplier's peak, and banding only REMOVES a QFT's
phase-product workspace (`qft_overlay`'s own argument for keeping the frozen count as an upper bound), so the
measured count stays an upper bound for a banded run. The catalyst register is an ADDITION and is charged.

THE EXPONENT FAMILY IS REFUSED BY NAME under a measured readout -- any value, `exact` included -- and never
re-interpreted as a choice about the feed-forward phases. Justification, from the code:

  1. The site the family names does not exist in the measured circuit: `readout_overlay.project` requires and
     removes the unique exponent IQFT, so there is no `PolicyQFT(xW)` part left to band or to catalyse.
  2. Re-reading it as "band the feed-forward phases" would be a different construction priced on the wrong
     one: the semiclassical loop applies ONE single-qubit phase per round whatever the band (a truncated
     dyadic angle is still one rotation), so substituting `ReferenceQFT(xW, k_max)` -- a coherent ladder of
     controlled rotations -- would subtract rotations the measured circuit never had. Catalysing them would
     price classically-controlled constant additions as `qft_overlay`'s worst-case CONTROLLED kicks, which is
     not the circuit either. The readout rule already prices every feed-forward phase, at the run's
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
RULE = "readout-qft-compose-v1"
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
    banded = {f: v for f, v in plan.items() if v[1] == "approximate"}
    catalytic = {f: v for f, v in plan.items() if v[0] == "catalytic"}
    touched = sorted(set(banded) | set(catalytic))
    delta = coherent["delta"]

    measured = None
    if readout:
        # The readout's census patch, taken from the pinned rule itself rather than restated: its TBase and
        # rotations ARE the patched census (the IQFT removed, the xW - 1 feed-forward phases added).
        measured = readout_overlay.project(policy, coherent)
        base, rotations = int(measured["TBase"]), int(measured["rotations"])
    else:
        base, rotations = int(coherent["T_base"]), int(coherent["rotations"])
    families, sites = {}, {}

    if touched:
        lp = fa.to_l2q_full(policy, "exact")
        spec = fa.HEADLINE.with_(negate=coherent["negate"])
        from l2q_full.bloqs.qft import ReferenceQFT

        for family, (w, count, row) in qft_overlay._family_sites(fa, coherent, touched).items():
            if measured is not None:
                _require(w != measured["rounds"],
                         f"QFT family {family!r}: its width {w} is the measured register's (xW = "
                         f"{measured['rounds']}), whose QFT the semiclassical readout removes; refusing "
                         f"rather than substituting into a part that is not in the measured circuit")
            exact = fa.cc.split(fa.pr.PolicyQFT(lp, w, spec))
            scaled = exact.scale(count)
            _require(int(row["rotations"]) == scaled.rot
                     and row["T"] == scaled.base + scaled.rot * coherent["T_per_rotation"],
                     f"QFT family {family!r}: the coherent part at width {w} disagrees with the exact cost split "
                     f"of PolicyQFT({w}); the composed rule will not substitute into a census it cannot reproduce")
            band = None
            if family in banded:
                backend, _target, max_error = banded[family]
                eta = qft_overlay.parse_max_error(family, max_error)
                k = int(qft_overlay.aqft_k_max(w, eta))
                band = fa.cc.split(ReferenceQFT(w, k_max=k))
                base += (band.base - exact.base) * count
                rotations += (band.rot - exact.rot) * count
                families[family] = {
                    "width": w, "count": count, "backend": backend, "target": "approximate",
                    "currency": backend, "maxError": max_error, "eta": eta, "kMax": k,
                    "banded": bool(k < w - 1),
                    "exactTBase": int(exact.base), "exactRotations": int(exact.rot),
                    "bandedTBase": int(band.base), "bandedRotations": int(band.rot),
                    "realization": "ReferenceQFT(w, k_max) -- Coppersmith-banded Draper ladder, "
                                   "textbook, NOT the Lean lowerQFT term; it REPLACES the native "
                                   "PolicyQFT template at this site, so the delta is construction + "
                                   "band, not band alone",
                }
            sites[family] = (w, count, row, exact, band)
    for family, (backend, target, _max_error) in plan.items():
        if target == "exact":
            families[family] = {"width": int(qft_overlay.FAMILY_WIDTH[family](coherent["widths"])),
                                "backend": backend, "target": "exact", "currency": backend, "maxError": None,
                                "eta": None, "kMax": None, "banded": False,
                                "count": sites[family][1] if family in sites else None,
                                "exactRotations": int(sites[family][3].rot) if family in sites else None,
                                "realization": "unchanged: the engine's native PolicyQFT template, the "
                                               "exact full ladder (qft.py:446 default k_max=None)"}

    _require(base >= 0 and rotations >= 0,
             f"the composed readout + QFT rule produced a negative census (T_base={base}, rotations={rotations})")

    # ONE division of the pool, over the JOINT rotation total.
    rate = fa.sc.ts_per_rotation(delta, rotations) if rotations else 0.0
    eps = fa.sc.rotation_eps(delta, rotations) if rotations else None
    arm, r_catalysed = None, 0
    if catalytic:
        cs = qft_overlay._catalysis_spec()
        for family in sorted(catalytic):
            n = qft_overlay._catalysed_rotations(family, sites[family], set(banded))
            _require(n >= 1,
                     f"QFT family {family!r}: backend 'catalytic' asks for a catalyst on a family that contributes "
                     f"0 rotations for it to absorb, so there is no arm to price; it is reported, not priced as "
                     f"the untouched default")
            r_catalysed += n
        arm = qft_overlay._catalytic_arm(cs, fa.sc, catalytic, r_catalysed, rotations, delta)
    synthesised = int(rotations) - int(r_catalysed)
    total = base + synthesised * rate
    if arm is not None:
        total += arm["T"]
    return {"measured": measured, "T": total, "TBase": int(base), "rotations": int(rotations),
            "rotationsSynthesised": synthesised, "rotationsCatalysed": int(r_catalysed), "tPerRotation": rate,
            "eps": eps, "families": families, "substituted": sorted(banded), "catalysed": sorted(catalytic),
            "catalyst": arm, "catalystQubits": int(arm["registerQubits"]) if arm is not None else 0}


def project(policy, coherent, design):
    """Price `policy`'s MEASURED run with the QFT design applied, from the frozen coherent result.

    `design` is the compiled per-family design (`full_run_design.design_of`). The answer carries the readout's
    own census patch beside the joint one, so a consumer can re-derive every number from the coherent parts."""
    j = _joint(policy, coherent, design, readout=True)
    m = j["measured"]
    qubits = int(m["qubits"]) + j["catalystQubits"]
    return {
        "mode": MODE, "pricingRule": RULE, "ruleSha256": rule_fingerprint(), "componentRules": component_rules(),
        "epoch": EPOCH, "kMaxSource": qft_overlay.k_max_source(),
        "T": j["T"], "TBase": j["TBase"], "rotations": j["rotations"],
        "rotationsSynthesised": j["rotationsSynthesised"], "rotationsCatalysed": j["rotationsCatalysed"],
        "tPerRotation": j["tPerRotation"], "eps": j["eps"],
        # The readout's half of the census, verbatim from `readout_overlay.project`.
        "rounds": m["rounds"], "iqftTBase": m["iqftTBase"], "iqftRotations": m["iqftRotations"],
        "feedForwardRotations": int(m["rounds"]) - 1,
        "readoutTBase": m["TBase"], "readoutRotations": m["rotations"],
        # The design's half, in `qft_overlay.project`'s per-family shape.
        "families": j["families"], "substituted": j["substituted"], "catalysed": j["catalysed"],
        "catalyst": j["catalyst"], "identity": not j["substituted"] and not j["catalysed"],
        "peakExtra": m["peakExtra"], "readoutQubits": m["qubits"], "catalystQubits": j["catalystQubits"],
        "qubits": qubits,
        "tNote": "T = TBase + rotationsSynthesised * tPerRotation + catalyst.T, with TBase = readoutTBase + "
                 "sum(families: (bandedTBase - exactTBase) * count) and rotations = readoutRotations + "
                 "sum(families: (bandedRotations - exactRotations) * count); tPerRotation and eps divide the "
                 "pool once over that joint total",
        "qubitsNote": "the measured circuit's qubits (readoutQubits) plus catalystQubits. Banding only REMOVES "
                      "phase-product workspace, so the measured count stays an upper bound for it; the shared "
                      "catalyst register is added because it is not",
    }
