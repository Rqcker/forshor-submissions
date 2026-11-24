"""Coppersmith-banded QFT pricing for the compiled QFT design axis, using the frozen coherent engine.

Local qualification only: this module does not grant publication authority, and it is NOT an epoch
input. It lives outside `evaluation/engine` for the same reason `pricing._all_tables` does -- so that
pricing a new design axis does not move `engineRevision` (`epoch.py:91`, `git log -1 -- evaluation/engine`)
nor `dataFingerprint` (`full_frontier.py:43-50`, a hash of the SOURCE BYTES of `site_engine/fullalg.py`
and of every file under `engine/fullalg` and `engine/paper_model`). Both move on ANY engine edit,
including a comment-only one, so an additive `design=` parameter on `price_full` could not have been
default-preserving at the epoch level however carefully it was written. Measured: appending one comment
line to `site_engine/fullalg.py` moves the epoch from ab1f8887b1b50e56 to d3bda33d5ddd5dca.

WHAT IS PRICED, and why it is a like-for-like substitution
-----------------------------------------------------------------------------------------------------
The engine prices every `qft` site as `policy_shor_price.PolicyQFT(policy, w, spec)` -- the native Lean
Cooley-Tukey template whose twiddles are policy phase products (`policy_shor_price.py:87-118`, reached
through the three-way dispatch `site_bloq` at `policy_shor_price.py:121-130`). `PolicyQFT` takes NO
banding parameter: its fields are exactly `policy`, `w`, `convention`.

This overlay does NOT touch that dispatch. Like `readout_overlay.project`, which constructs `PolicyQFT`
directly at `readout_overlay.py:136`, it enumerates the QFT sites itself and constructs the in-epoch
bloq it wants with its own parameters. The bloq it wants is `ReferenceQFT` (`l2q_full/bloqs/qft.py:440`),
whose `k_max` field (`:452`) is ALREADY inside the live epoch, defaults to `None` = the exact full
ladder, and drops controlled rotations of turn distance `d > k_max` through the mechanical guard at
`:461` (`d_stop = w if k_max is None else min(w, k_max + 1)`).

Both bloqs are costed by the SAME functional the engine uses for its own sites,
`cost_conventions.split` (`:215-219`) = `get_cost_value(bloq, QECGatesCost())` reported as
`(rotation-free T, rotation count)`. It is generic over `Bloq` and needs no convention field. The
substitution therefore replaces one `(base, rotations)` pair with another in the same accounting, and
the whole run's synthesis budget is then re-divided over the new rotation total exactly as the engine
divides it (`sto_currency.ts_per_rotation(delta, R_total)`), which is what `readout_overlay.py:142`
does for the readout axis. It is not a second accounting.

A CONSEQUENCE THAT FIXES THE CONTRACT'S SEMANTICS. The exact ladder is not cost-equal to the engine's
Lean template -- at w = 4095 `PolicyQFT` splits as (37_598_400, 178_191) and `ReferenceQFT(4095)` as
(33_533_953, 8_289_333). So `target: "exact"` means "LEAVE THIS FAMILY'S QFT EXACTLY AS THE ENGINE
PRICED IT" (k_max = None, no substitution at all), never "substitute the exact ladder". That is what
makes the all-exact design bit-identical to today's price, and it is what `qft.py:446` already says:
default `None` keeps the exact full ladder, "the previous behaviour, unchanged". Only an `approximate`
target substitutes anything.

SCOPE. QFT families only: `data`, `work`, `scratch`, `exponent`. `step2 := fallback` and
`step4 := selectiveKick` are fields of the same compiled selector record but are not symbolic,
deterministic or closed-form -- they need certified word banks -- and are explicitly out of scope; a
design mapping carrying them is REFUSED, not quietly ignored.

FAIL CLOSED. An unrecognised backend, an unparseable `max_error`, an out-of-scope selector, or a family
this overlay cannot honour RAISES and names the family and the offending value. A request that cannot
be honoured is reported, never priced at the default.
"""
from hashlib import sha256
from pathlib import Path
import math
import re

from . import engine, epoch
from .canon import epoch_of
from .mapping import DELTA_VALUES

EPOCH = "ab1f8887b1b50e56"
RULE = "coppersmith-banded-qft-v1"

#: The QFT register families this overlay prices, and the layout width each one's QFT site runs at
#: (`site_engine/fullalg.py:187-199` `_site_label`: "QFT on data+1", "QFT on work", "Step 4 cmpLtNW:
#: QFT on scratch", "final QFT on the exponent register").
FAMILY_WIDTH = {"data": lambda w: w["yW"] + 1, "work": lambda w: w["workW"],
                "scratch": lambda w: w["scratchW"], "exponent": lambda w: w["xW"]}
FAMILIES = tuple(FAMILY_WIDTH)
BACKENDS = ("ross_selinger", "catalytic")
TARGETS = ("exact", "approximate")

#: Selector fields of the same compiled record that this overlay must not price (see SCOPE above).
OUT_OF_SCOPE = ("step2", "step4")

#: `max_error` arrives as the exact string form the compiled selector emits: `<units>e-<scale>`, both
#: non-negative decimal integers, e.g. "1e-6" or "25e-9". Nothing else is accepted -- not a float, not
#: "1.5e-6", not "1E-6", not "1e+6", not a padded or signed variant.
MAX_ERROR_RE = re.compile(r"([0-9]+)e-([0-9]+)")


class QftOverlayRefused(ValueError):
    """A QFT design request this overlay will not price. Never a fallback to the default price."""


def _refuse(message):
    raise QftOverlayRefused(message)


def _require(condition, message):
    if not condition:
        _refuse(message)


def rule_fingerprint():
    """This overlay's own identity. The epoch fingerprint cannot cover `export_v2`, so a receipt that
    carries an overlay price must carry the bytes of the rule that produced it."""
    return sha256(Path(__file__).read_bytes()).hexdigest()


# ----------------------------------------------------------------------------- the precision band


def _engine_aqft_k_max():
    """`precision_policy.aqft_k_max`, the repo's single source for the QFT precision band, if it can be
    loaded. It lives at `engine/paper_model/py/precision_policy.py:84` -- inside the epoch, and already
    hashed into it -- but `engine/paper_model/py` is NOT on the site engine's `sys.path`, so a plain
    import fails; it is loaded by file path instead. Reading an engine file does not move the epoch.
    Returns None when absent, and `aqft_k_max` then falls back to the mirror below."""
    import importlib.util

    path = Path(engine.load().engine_dir) / "paper_model" / "py" / "precision_policy.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_qft_overlay_precision_policy", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, "aqft_k_max", None)
    except Exception:
        return None


def aqft_k_max(w, eta):
    """Coppersmith band for a width-`w` QFT held to per-operation precision `eta`.

    A QFT banded to keep only controlled rotations of turn distance `d <= k_max` has induced error
    `~ w * 2^-k_max` (Coppersmith 1994; Beauregard S2.5). Requiring that error `<= eta` inverts to

        k_max = max(1, ceil(log2(w / eta))).

    Delegates to the in-epoch `precision_policy.aqft_k_max` when it can be loaded, so there is one
    definition in the repo and not two; the expression here is the identical documented closed form,
    kept as a mirror for the case where `paper_model/py` is not shipped. `k_max_source` reports which
    one priced a receipt, and the tests require the two to agree."""
    engine_form = _engine_aqft_k_max()
    if engine_form is not None:
        return int(engine_form(int(w), float(eta)))
    return max(1, math.ceil(math.log2(float(w) / float(eta))))


def k_max_source():
    return "engine:paper_model/py/precision_policy.aqft_k_max" if _engine_aqft_k_max() is not None \
        else "overlay:documented closed form max(1, ceil(log2(w/eta)))"


def parse_max_error(family, value):
    """`<units>e-<scale>` as a float, or refuse naming the family and the value."""
    _require(isinstance(value, str) and MAX_ERROR_RE.fullmatch(value),
             f"QFT family {family!r}: max_error {value!r} is not the compiled form '<units>e-<scale>' "
             f"(two non-negative decimal integers, e.g. '1e-6'); it is not priced by guesswork")
    units, scale = MAX_ERROR_RE.fullmatch(value).groups()
    eta = int(units) * 10.0 ** -int(scale)
    _require(0.0 < eta < 1.0,
             f"QFT family {family!r}: max_error {value!r} is {eta!r}, which is not a per-operation "
             f"precision in (0, 1)")
    return eta


# ----------------------------------------------------------------------------- the design mapping


def parse_design(design):
    """Validate a design mapping and return `{family: (backend, target, max_error_or_None)}` for the
    families that ask for something, with absent and None families dropped (they mean "unchanged").

    Refuses, naming the family and the value: an out-of-scope selector, an unknown key, a non-mapping
    value, an unrecognised backend, an unrecognised target, a missing or unexpected `max_error`."""
    if design is None:
        return {}
    _require(hasattr(design, "keys") and hasattr(design, "__getitem__"),
             f"QFT design must be a mapping with per-family keys {list(FAMILIES)}; got {type(design).__name__}")
    for field in OUT_OF_SCOPE:
        _require(field not in design,
                 f"QFT overlay refuses the selector field {field!r}: it is not symbolic, deterministic or "
                 f"closed-form and requires separate qualification with a certified word bank. This overlay "
                 f"prices the QFT families {list(FAMILIES)} only; re-submit without {field!r}")
    unknown = sorted(set(design) - set(FAMILIES))
    _require(not unknown,
             f"QFT design has keys this overlay cannot honour: {unknown} (known families: {list(FAMILIES)})")
    out = {}
    for family in FAMILIES:
        if family not in design or design[family] is None:
            continue                                   # absent or None: unchanged from the engine default
        value = design[family]
        _require(hasattr(value, "keys") and hasattr(value, "__getitem__"),
                 f"QFT family {family!r}: expected None or a mapping with 'backend' and 'target'; "
                 f"got {value!r}")
        extra = sorted(set(value) - {"backend", "target", "max_error"})
        _require(not extra, f"QFT family {family!r}: unexpected keys {extra} in {dict(value)!r}")
        _require("backend" in value, f"QFT family {family!r}: no 'backend' in {dict(value)!r}")
        _require("target" in value, f"QFT family {family!r}: no 'target' in {dict(value)!r}")
        backend, target = value["backend"], value["target"]
        _require(backend in BACKENDS,
                 f"QFT family {family!r}: unrecognised backend {backend!r} (known: {list(BACKENDS)}); "
                 f"it is reported, not priced at the default")
        _require(target in TARGETS,
                 f"QFT family {family!r}: unrecognised target {target!r} (known: {list(TARGETS)}); "
                 f"it is reported, not priced at the default")
        if target == "approximate":
            _require("max_error" in value,
                     f"QFT family {family!r}: target 'approximate' needs a 'max_error'; without one there "
                     f"is no band to derive and the request is refused, not priced as exact")
            max_error = value["max_error"]
        else:
            _require("max_error" not in value,
                     f"QFT family {family!r}: target 'exact' takes no 'max_error' (got "
                     f"{dict(value).get('max_error')!r}); an exact QFT has no band")
            max_error = None
        # The catalytic arm is qualified as a closed form in `l2q_full/catalysis/spec.py`
        # (`per_site_b:410`, `other_site_cost:719`, `mixing_diamond_max:603`, `catalyst_prep_T:736` --
        # all pure, no cache and no word bank), but honouring it is a SECOND axis: it replaces
        # Ross-Selinger for the whole run's rotations, needs its own share of the budget to choose the
        # catalyst width b, and adds a shared catalyst register to the qubit count. None of that is
        # this overlay's band. Refused rather than silently priced at the Ross-Selinger default.
        _require(backend != "catalytic",
                 f"QFT family {family!r}: backend 'catalytic' is not wired into this overlay. The "
                 f"closed form exists (catalysis/spec.py per_site_b, other_site_cost, "
                 f"mixing_diamond_max, catalyst_prep_T) but it re-prices every rotation in the run and "
                 f"adds a catalyst register, which is a separate axis from the QFT band; pricing it as "
                 f"{BACKENDS[0]!r} would be a silent default, so it is reported instead")
        out[family] = (backend, target, max_error)
    return out


# ----------------------------------------------------------------------------- the price


def _family_sites(fa, coherent, families):
    """`{family: (w, count)}` for the families being substituted, from the frozen coherent parts.

    Refuses when a family's width is shared with another family (the site could not then be attributed)
    or when the coherent result does not hold exactly one QFT part at that width."""
    widths = coherent["widths"]
    want = {f: int(FAMILY_WIDTH[f](widths)) for f in families}
    for family, w in want.items():
        clash = sorted(g for g, v in want.items() if g != family and v == w)
        _require(not clash,
                 f"QFT family {family!r}: its width {w} is shared with {clash}, so a banded site could "
                 f"not be attributed to one family; refusing rather than guessing")
    out = {}
    for family, w in want.items():
        rows = [p for p in coherent["parts"] if p["site"] == "qft" and p["xw"] == w and p["zw"] == 0]
        _require(len(rows) == 1,
                 f"QFT family {family!r}: the coherent reference holds {len(rows)} QFT parts at width "
                 f"{w}, expected exactly one")
        count = int(rows[0]["count"])
        _require(count >= 1, f"QFT family {family!r}: QFT part at width {w} has count {count}")
        out[family] = (w, count, rows[0])
    return out


def project(policy, coherent, design=None):
    """Price `policy`'s run with the QFT design axis applied to the frozen coherent result.

    `design=None`, an all-exact design, and a design whose families are all absent or None are
    BIT-IDENTICAL to `coherent`: nothing is substituted, the rotation total is unchanged, so the budget
    re-division returns the same rate and `base + rotations * rate` is the same float expression the
    engine evaluated. Only an `approximate` target substitutes a banded `ReferenceQFT`.
    """
    eng = engine.load()
    fa = eng.fullalg
    # EPOCH GUARD (`readout_overlay.py:95`). export_v2 is not an epoch input, so an overlay price is
    # only honest against the engine model it was qualified on. The owner is deliberately NOT moving
    # the epoch, so this holds; if it ever fails, the overlay refuses rather than prices.
    _require(epoch_of(epoch.model_of()) == EPOCH,
             f"QFT overlay is qualified against engine model {EPOCH} only; this tree's model is "
             f"{epoch_of(epoch.model_of())}, so the band is reported as unpriced")
    _require(coherent["n"] == fa.HERO_N and coherent["width_rule"] == "exact"
             and coherent["negate"] == fa.HEADLINE.negate and coherent["delta"] in DELTA_VALUES.values(),
             "QFT overlay requires the current exact reference price")
    _require("parts" in coherent and coherent["parts"],
             "QFT overlay needs the per-part census; price with detail=True")

    plan = parse_design(design)
    banded = {f: v for f, v in plan.items() if v[1] == "approximate"}
    delta = coherent["delta"]
    base = int(coherent["T_base"])
    rotations = int(coherent["rotations"])
    families = {}

    if banded:
        lp = fa.to_l2q_full(policy, "exact")
        spec = fa.HEADLINE.with_(negate=coherent["negate"])
        from l2q_full.bloqs.qft import ReferenceQFT

        for family, (w, count, row) in _family_sites(fa, coherent, banded).items():
            backend, _target, max_error = banded[family]
            eta = parse_max_error(family, max_error)
            k = int(aqft_k_max(w, eta))
            # The engine's own price for this site, recomputed from the in-epoch bloq and cross-checked
            # against the frozen part row -- the like-for-like check `readout_overlay.py:135` makes.
            exact = fa.cc.split(fa.pr.PolicyQFT(lp, w, spec))
            # Scale with the engine's own `TCost.scale` and recombine in the engine's own association
            # (`fullalg.parts`: `sc_ = split(bloq).scale(k)`, then `T = sc_.base + sc_.rot * tpr`).
            # `(base + rot*tpr) * k` is a DIFFERENT float from `base*k + rot*k*tpr` and would reject a
            # census the overlay in fact reproduces exactly.
            scaled = exact.scale(count)
            _require(int(row["rotations"]) == scaled.rot
                     and row["T"] == scaled.base + scaled.rot * coherent["T_per_rotation"],
                     f"QFT family {family!r}: the coherent part at width {w} disagrees with the exact "
                     f"cost split of PolicyQFT({w}); the overlay will not substitute into a census it "
                     f"cannot reproduce")
            band = fa.cc.split(ReferenceQFT(w, k_max=k))
            base += (band.base - exact.base) * count
            rotations += (band.rot - exact.rot) * count
            families[family] = {
                "width": w, "count": count, "backend": backend, "target": "approximate",
                "maxError": max_error, "eta": eta, "kMax": k,
                "banded": bool(k < w - 1),
                "exactTBase": int(exact.base), "exactRotations": int(exact.rot),
                "bandedTBase": int(band.base), "bandedRotations": int(band.rot),
                "realization": "ReferenceQFT(w, k_max) -- Coppersmith-banded Draper ladder, textbook, "
                               "NOT the Lean lowerQFT term; it REPLACES the native PolicyQFT template "
                               "at this site, so the delta is construction + band, not band alone",
            }
    for family, (backend, target, max_error) in plan.items():
        if target == "exact":
            families[family] = {"width": int(FAMILY_WIDTH[family](coherent["widths"])), "backend": backend,
                                "target": "exact", "maxError": None, "eta": None, "kMax": None,
                                "banded": False,
                                "realization": "unchanged: the engine's native PolicyQFT template, the "
                                               "exact full ladder (qft.py:446 default k_max=None)"}

    _require(base >= 0 and rotations >= 0,
             f"QFT overlay produced a negative census (T_base={base}, rotations={rotations})")
    rate = fa.sc.ts_per_rotation(delta, rotations) if rotations else 0.0
    return {
        "mode": "coppersmith-banded-qft", "pricingRule": RULE, "ruleSha256": rule_fingerprint(),
        "epoch": EPOCH, "kMaxSource": k_max_source(),
        "T": base + rotations * rate, "TBase": int(base), "rotations": int(rotations),
        "tPerRotation": rate, "eps": fa.sc.rotation_eps(delta, rotations) if rotations else None,
        "families": families, "substituted": sorted(banded),
        "identity": not banded,
        # Banding removes a QFT's phase-product workspace, so the frozen layout is a sound UPPER bound
        # on the banded run's qubits. The overlay prices T only and does not restate a qubit count it
        # has not modelled.
        "qubitsUpperBound": coherent["qubits"],
        "qubitsNote": "T only; the banded ladder needs no phase-product workspace, so the engine's "
                      "layout total is an upper bound and is not re-derived here",
    }
