"""The per-family QFT design -- construction x synthesis backend -- priced on the frozen coherent engine.

This module does not grant publication authority by itself, and it is NOT an epoch
input. It lives outside `evaluation/engine` for the same reason `pricing._all_tables` does -- so that
pricing a new design axis does not move `engineRevision` (`epoch.py:91`, `git log -1 -- evaluation/engine`)
nor `dataFingerprint` (`full_frontier.py:43-50`, a hash of the SOURCE BYTES of `site_engine/fullalg.py`
and of every file under `engine/fullalg` and `engine/paper_model`). Both move on ANY engine edit,
including a comment-only one, so an additive `design=` parameter on `price_full` could not have been
default-preserving at the epoch level however carefully it was written. Measured: appending one comment
line to `site_engine/fullalg.py` moves the epoch from ab1f8887b1b50e56 to d3bda33d5ddd5dca.

THE FAMILIES AND THE TWO AXES
-----------------------------------------------------------------------------------------------------
The engine prices every `qft` site as `policy_shor_price.PolicyQFT(policy, w, spec)`: the native Lean
Cooley-Tukey template. `PolicyQFT(w)` is `PolicyQFT(w - w//2)`, `PolicyQFT(w//2)`, one twiddle
`PolicyPhaseProduct(w//2 + 1, w - w//2 + 1)` and Clifford swaps. This overlay does not touch that dispatch:
like `readout_overlay`, it enumerates the QFT sites itself and substitutes each designed family's census, the
whole template's (its exact split is cross-checked against the frozen part first).

`target` and `backend` are orthogonal, and the four corners are four constructions:

  ross_selinger / exact        the native template, untouched (the engine's own price, nothing substituted).
  ross_selinger / approximate  a Coppersmith-banded Draper ladder `ReferenceQFT(w, k_max)` (in-epoch,
                               `l2q_full/bloqs/qft.py`), its surviving rotations synthesised Ross-Selinger.
  catalytic / approximate      the SAME band as the Ross-Selinger arm, then each target's kept phases merged
                               into ONE controlled addition into a shared phase-gradient register (Nam, Su &
                               Maslov, npj Quantum Inf. 6, 26 (2020), the phase-gradient AQFT; Gidney,
                               Quantum 2, 74 (2018), the adder). Target j's kept phases CP(pi 2^-d), d = 1..m_j,
                               m_j = min(k_max, w - 1 - j), multiply to exp(2 pi i x_j V / 2^(m_j + 1)) with
                               V = sum_d x_{j+d} 2^(m_j - d) the value its controls already hold: one
                               `CtrlAddIntoCatalyst(m_j + 1, m_j)` into the gradient's top m_j + 1 bits
                               (`catalysis/catalyst.ctrl_add_ands` = 2 m_j Ands: m_j control Ands and an
                               (m_j + 1)-bit Gidney adder). The turns are dyadic of resolution <= k_max + 1, so
                               on a gradient of b >= k_max + 1 bits every addition is EXACT: no rounding, no
                               rotation, and the family's whole allowance goes to the band.
  catalytic / exact            the same construction with no band (k_max = w - 1), so the family's error is 0
                               and the register is as wide as the QFT (b = w).

THE ERROR ALLOWANCE IS THE FAMILY'S TOTAL
-----------------------------------------------------------------------------------------------------
`max_error` is the family's TOTAL operator-norm allowance over ALL of its calls in the run (data and work
run 2 x 4095 times, scratch 4 x 4095), not a per-call ceiling:

  banded     depth = the smallest d with 2 PI_UP * calls * omitted_turns(w, d) <= allowance, and
             k_max = depth - 1 (`ReferenceQFT` keeps the phases of turn 2^-(j+1) for j <= k_max).
             omitted_turns(w, d) = sum over s > d of (w - s + 1) 2^-s is the sum of the dropped angles.
             This rule is the same for both backends: a catalytic family is banded exactly as a
             Ross-Selinger one, and its merged additions add no error of their own.

PI_UP is a rational upper bound on pi, so the bound is an exact Fraction.

The allowances are then taken OUT of the run's synthesis pool before it is divided:

  pool  = ROTATION_FRACTION * delta                        (sto_currency: half of delta)
  prep  = min(pool / 10, (pool - sum allowances) / 2)      when a catalyst exists, else 0
  rest  = pool - sum allowances - prep                     (refused by name when <= 0)
  eps   = rest / R', rounded down to a float               over the run's rotation total after substitution
  T     = TBase' + R' * ts_per_rotation_eps(eps) + catalyst_prep_T(b, prep)

so sum(family error bounds) + prep + R' * eps <= pool <= delta holds for every accepted design
(`budget.ledger` reports each term). With no family designed nothing is carved and the rate is the engine's
own `ts_per_rotation(delta, R)`, the same float expression the engine evaluated: the default stays
bit-identical.

THE BAND IS COUNTED IN CLOSED FORM
-----------------------------------------------------------------------------------------------------
`cost_conventions.split(ReferenceQFT(w, k))` counts the phases of distance 40 to 60 as Clifford (their And
is charged, their rotation is not). A family-total band reaches that depth, so the band's census is the
closed form instead: rotations = sum_{d=3..k}(w - d), base T = 4 sum_{d=1..k}(w - d) + (w - 2). The engine's
base is required to equal the closed form, and its rotation count may only fall short of it.

A catalytic family's census is closed form too: Ands = sum_j ctrl_add_ands(m_j + 1, m_j) = 2 N_cp with
N_cp = k (2w - k - 1) / 2 the kept phases, so 8 N_cp T per call, 0 rotations, plus w Hadamards and the
adders' CNOTs (Clifford). The engine's own And count is summed row by row and required to equal 2 N_cp.

QUBITS
-----------------------------------------------------------------------------------------------------
The shared gradient register is b = max_f (k_max_f + 1) qubits, live for the whole run, and is added once
to Q. Beyond it, a merged addition holds `m + L - 1` = 2 m_j temporary qubits (its control Ands and the adder's
carries), at most 2 k_max per call. That can be MORE than the native template needed at the same site (on some
policies a QFT site's extra grows), so the qubits are re-derived from the engine's own live-qubit timeline
(`policy_peak.modmul_phase_peaks`: the multiplication's eight sequential phases, three of them the data, work and
scratch QFTs; the coherent run adds the exponent QFT, `qft_peak(xW)`). The timeline must reproduce the run's peak
extra; each designed QFT site's extra is then replaced -- a catalytic family's by its 2 k_max, a Ross-Selinger band's
by max(native, 1) (its ladder needs one And temporary at a time) -- and whatever the new maximum exceeds the frozen
peak extra by is added (`workspaceQubits`). On the offered designs the merged additions' temporaries stay under the
run's existing peak at every QFT site (the peak is set by a phase-product step), so they add no qubits; an exact
catalytic family's 2 (w - 1) does exceed it, and the excess is charged.

SCOPE. QFT families only: `data`, `work`, `scratch`, `exponent`. The four phase-product steps --
`step1`, `step2`, `step4` and `step5` -- are fields of the same compiled selector record but are not
priced here; a design mapping carrying any of them is REFUSED, not quietly ignored.

FAIL CLOSED. An unrecognised backend, an unparseable `max_error`, an out-of-scope selector, a family this
overlay cannot honour, or allowances that leave no synthesis budget RAISE and name the family and the
offending value. A request that cannot be honoured is reported, never priced at the default.
"""
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
import math
import re

from . import engine, epoch
from .canon import epoch_of
from .mapping import DELTA_VALUES

EPOCH = "ab1f8887b1b50e56"
RULE = "qft-design-axis-v4"

#: The QFT register families this overlay prices, and the layout width each one's QFT site runs at
#: (`site_engine/fullalg.py:187-199` `_site_label`: "QFT on data+1", "QFT on work", "Step 4 cmpLtNW:
#: QFT on scratch", "final QFT on the exponent register").
FAMILY_WIDTH = {"data": lambda w: w["yW"] + 1, "work": lambda w: w["workW"],
                "scratch": lambda w: w["scratchW"], "exponent": lambda w: w["xW"]}
FAMILIES = tuple(FAMILY_WIDTH)
BACKENDS = ("ross_selinger", "catalytic")
TARGETS = ("exact", "approximate")

#: Selector fields of the same compiled record that this overlay must not price (see SCOPE above).
#: All four phase-product steps, the same set `full_run_design.PHASE_STEPS` refuses on the explicit path, so
#: each one is refused by name as a phase-product step rather than as an unrecognised family.
OUT_OF_SCOPE = ("step1", "step2", "step4", "step5")

#: Rational upper bound for pi (pi < 3.14159265358979323846264338328), as in `primitive_overlay`.
PI_UP = Fraction("3.14159265358979323846264338328")
#: The smallest catalyst `catalyst_prep_T` prices with a genuine rotation (`b - 3 >= 1`).
B_MIN = 4
#: How the shared register's width follows from the families.
B_RULE = ("the widest catalytic family's band plus one: b = max over the catalytic families of k_max + 1, on which "
          "every merged addition is exact (k_max = width - 1 for an exact family)")
#: How a catalytic family's kept phases are applied.
MERGE_RULE = ("per call, for each target j one CtrlAddIntoCatalyst(m_j + 1, m_j) of 2 m_j Ands into the gradient's top "
              "m_j + 1 bits, m_j = min(k_max, w - 1 - j): 8 N_cp T with N_cp = k_max (2 w - k_max - 1) / 2, no rotation, "
              "no rounding; workspace 2 k_max")
BAND_RULE = ("k_max = depth - 1, depth the smallest d with 2 PI_UP calls omitted_turns(w, d) <= the family's allowance; "
             "rotations sum_{d=3..k}(w - d), base T 4 sum_{d=1..k}(w - d) + (w - 2)")
#: The share of the pool reserved for preparing the gradient, and its cap (the research ledger's own).
PREP_RULE = "min(pool / 10, (pool - allowances) / 2) when a catalyst exists, else 0"

#: `max_error` arrives as the exact string form the compiled selector emits: `<units>e-<scale>`, both
#: non-negative decimal integers, e.g. "1e-6" or "25e-9". Nothing else is accepted -- not a float, not
#: "1.5e-6", not "1E-6", not "1e+6", not a padded or signed variant.
MAX_ERROR_RE = re.compile(r"([0-9]+)e-([0-9]+)")


class QftOverlayRefused(ValueError):
    """A QFT design request this overlay will not price. It is reported, never priced at the default."""


def _refuse(message):
    raise QftOverlayRefused(message)


def _require(condition, message):
    if not condition:
        _refuse(message)


def rule_fingerprint():
    """This overlay's own identity. The epoch fingerprint cannot cover `export_v2`, so a receipt that
    carries an overlay price must carry the bytes of the rule that produced it."""
    return sha256(Path(__file__).read_bytes()).hexdigest()


def _catalysis():
    """`l2q_full.catalysis.spec` and `.catalyst`, the in-epoch catalysis closed forms (`catalyst_prep_T`,
    `ctrl_add_ands`). Importable once `engine.load()` has put `fullalg` on `sys.path`. Reading engine modules does
    not move the epoch."""
    engine.load()
    try:
        from l2q_full.catalysis import catalyst, spec
    except ImportError as error:                     # pragma: no cover - the vendored tree always has it
        _refuse(f"backend 'catalytic' needs the in-epoch l2q_full.catalysis modules, which did not import "
                f"({error}); it is reported as unpriced, never priced as 'ross_selinger'")
    return spec, catalyst


# ----------------------------------------------------------------------------- the error allowance


def allowance_of(family, value):
    """`<units>e-<scale>` as an exact Fraction in (0, 1), or refuse naming the family and the value."""
    _require(isinstance(value, str) and MAX_ERROR_RE.fullmatch(value),
             f"QFT family {family!r}: max_error {value!r} is not the compiled form '<units>e-<scale>' "
             f"(two non-negative decimal integers, e.g. '1e-6'); it is not priced by guesswork")
    units, scale = MAX_ERROR_RE.fullmatch(value).groups()
    allowance = Fraction(int(units), 10 ** int(scale))
    _require(0 < allowance < 1,
             f"QFT family {family!r}: max_error {value!r} is {float(allowance)!r}, which is not an error "
             f"allowance in (0, 1)")
    return allowance


def parse_max_error(family, value):
    """The family's allowance as a float (the `eta` a panel shows), refusing as `allowance_of` does."""
    allowance_of(family, value)
    units, scale = MAX_ERROR_RE.fullmatch(value).groups()
    return int(units) * 10.0 ** -int(scale)


def _upper_float(x):
    """The smallest float >= the Fraction `x`, so a reported bound never rounds below the exact one."""
    f = float(x)
    return f if Fraction(f) >= x else math.nextafter(f, math.inf)


def _lower_float(x):
    """The largest float <= the Fraction `x`, so the per-rotation tolerance never spends more than its budget."""
    f = float(x)
    return f if Fraction(f) <= x else math.nextafter(f, -math.inf)


# ----------------------------------------------------------------------------- the banded construction


def omitted_turns(w, depth):
    """Sum of the turns `ReferenceQFT` drops below `depth`: for s > depth there are w - s + 1 phases of 2^-s."""
    m = w - depth
    return Fraction(((m - 1) << m) + 1, 1 << w) if m > 0 else Fraction(0)


def band_error(w, calls, depth):
    """Operator-norm bound of `calls` banded QFTs of width `w` at `depth`: 2 PI_UP calls omitted_turns."""
    return 2 * PI_UP * calls * omitted_turns(w, depth)


def band_depth(w, calls, allowance):
    """The smallest depth in [1, w] whose band error over all `calls` fits `allowance` (a bisection; the error
    falls with depth and is 0 at depth w)."""
    lo, hi = 1, w
    while lo < hi:
        mid = (lo + hi) // 2
        if band_error(w, calls, mid) <= allowance:
            hi = mid
        else:
            lo = mid + 1
    return lo


def band_census(w, k):
    """`(base T, rotations)` of one `ReferenceQFT(w, k_max=k)`, in closed form: every controlled phase of
    distance d <= k is one And (4 T), d = 2 adds one T gate, and d >= 3 is one synthesised rotation."""
    k = min(k, w - 1)
    controlled = sum(w - d for d in range(1, k + 1))
    rotations = sum(w - d for d in range(3, k + 1))
    return 4 * controlled + (w - 2 if k >= 2 else 0), rotations


# ----------------------------------------------------------------------------- the banded merged construction


def merged_plan(w, k):
    """`{ands, workspace}` of one banded merged QFT of width `w` keeping distances 1..k: per target j one
    `CtrlAddIntoCatalyst(m_j + 1, m_j)`, m_j = min(k, w - 1 - j), its Ands from the engine's own `ctrl_add_ands`
    (w - k targets keep all k controls, and one target keeps each m = 1..k-1); workspace = the widest addition's
    temporaries `m + L - 1`."""
    _cs, cat = _catalysis()
    k = min(k, w - 1)
    ands = (w - k) * cat.ctrl_add_ands(k + 1, k, False) + sum(cat.ctrl_add_ands(m + 1, m, False) for m in range(1, k))
    _require(ands == k * (2 * w - k - 1),
             f"the merged additions of a width-{w} band at k_max = {k} count {ands} Ands, not 2 N_cp = {k * (2 * w - k - 1)}")
    return {"ands": ands, "workspace": k + (k + 1) - 1}


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
                 f"QFT design names the phase-product step {field!r}, which is refused: this design path prices "
                 f"the QFT families {list(FAMILIES)} only; re-submit without {field!r}")
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
                     f"{dict(value).get('max_error')!r}); an exact QFT has no allowance")
            max_error = None
        out[family] = (backend, target, max_error)
    return out


def check_budget(plan, delta_key=None):
    """Refuse, naming the families and their values, a plan whose allowances leave no synthesis budget at the
    design's delta (`mapping.DESIGN_DELTA` by default): the allowances must sum to less than the pool."""
    from .mapping import DESIGN_DELTA

    key = DESIGN_DELTA if delta_key is None else delta_key
    named = {f: v[2] for f, v in plan.items() if v[1] == "approximate"}
    claimed = sum((allowance_of(f, e) for f, e in named.items()), Fraction(0))
    pool = Fraction(1, 2) * Fraction(key)
    _require(claimed < pool,
             f"QFT design: the families' max_error allowances {named} sum to {float(claimed)!r}, which leaves no "
             f"synthesis budget out of the pool {float(pool)!r} (half of delta {key}); it is refused, not priced")
    return plan


# ----------------------------------------------------------------------------- the price


def _family_sites(fa, coherent, families):
    """`{family: (w, count, row)}` for the families being substituted, from the frozen coherent parts.

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


def _delta_exact(delta):
    keys = [k for k, v in DELTA_VALUES.items() if v == delta]
    _require(len(keys) == 1, f"QFT design: delta {delta!r} is not one of the boards' deltas")
    return keys[0], Fraction(keys[0])


def _check_reference(fa, coherent, label):
    _require(epoch_of(epoch.model_of()) == EPOCH,
             f"{label} is qualified against engine model {EPOCH} only; this tree's model is "
             f"{epoch_of(epoch.model_of())}, so the design is reported as unpriced")
    _require(coherent["n"] == fa.HERO_N and coherent["width_rule"] == "exact"
             and coherent["negate"] == fa.HEADLINE.negate and coherent["delta"] in DELTA_VALUES.values(),
             f"{label} requires the current exact reference price")
    _require("parts" in coherent and coherent["parts"], f"{label} needs the per-part census; price with detail=True")
    _require("qubits_peak" in coherent and "qubits" in coherent,
             f"{label} restates a qubit total (the catalyst register is charged to it), so it needs the coherent "
             f"result's own `qubits_peak` and `qubits`")


#: The index of each family's QFT among `policy_peak.modmul_phase_peaks`' eight sequential phases.
SITE_PHASE = {"work": 1, "data": 2, "scratch": 5}


def _site_extras(fa, policy, coherent, measured):
    """`{site: native extra qubits}` from the engine's live-qubit timeline: the multiplication's eight phases, plus the
    exponent QFT on the coherent run (the semiclassical readout removes it). Keys are the families' names for the four
    QFT sites and the phase index otherwise."""
    lp = fa.to_l2q_full(policy, "exact")
    w = coherent["widths"]
    phases = fa.ppk.modmul_phase_peaks(lp, w["yW"], w["workW"], w["scratchW"], coherent["negate"])
    sites = {next((f for f, i in SITE_PHASE.items() if i == j), j): int(v) for j, v in enumerate(phases)}
    if not measured:
        sites["exponent"] = int(fa.ppk.qft_peak(lp, w["xW"], coherent["negate"]))
    return sites


def site_excess(sites, peak_extra, workspace, need=_require):
    """The qubits a design adds beyond the run's peak extra: each designed QFT site's extra replaced (`workspace[family]`:
    a catalytic family's 2 k_max merged-addition temporaries, or None for a Ross-Selinger band, max(native, 1)), the new
    maximum over the sequential sites, less the frozen peak extra. The native timeline must reproduce that peak extra."""
    need(max(sites.values()) == int(peak_extra),
         f"QFT design: the live-qubit timeline's peak {max(sites.values())} is not the run's peak extra {peak_extra}; "
         f"the design's qubits cannot be re-derived, so it is reported as unpriced")
    new = dict(sites)
    for family, ws in workspace.items():
        need(family in new, f"QFT family {family!r} has no site on the live-qubit timeline")
        new[family] = max(new[family], 1) if ws is None else ws
    return max(0, max(new.values()) - int(peak_extra))


def price_design(policy, coherent, plan, *, base, rotations, peak_extra, refuse=_refuse, skip_width=None):
    """The design's census patch and the ONE division of the pool, from a starting census `(base, rotations)`.

    `qft_overlay.project` starts from the coherent census; `readout_qft_overlay` starts from the readout's
    patched one and passes `skip_width` (the measured register, whose QFT the readout removed). Returns the
    priced families, the new census, the budget ledger, the catalyst and the qubit charges."""
    def need(condition, message):
        if not condition:
            refuse(message)

    eng = engine.load()
    fa = eng.fullalg
    touched = sorted(plan)
    delta = coherent["delta"]
    delta_key, delta_exact = _delta_exact(delta)
    families, error_bounds = {}, {}
    claimed, b_needed, catalysed, site_workspace = Fraction(0), 0, [], {}

    for family, (backend, target, max_error) in plan.items():
        if backend == "ross_selinger" and target == "exact":
            families[family] = {"width": int(FAMILY_WIDTH[family](coherent["widths"])), "backend": backend,
                                "target": "exact", "construction": "native", "maxError": None,
                                "realization": "unchanged: the engine's native PolicyQFT template"}
    designed = [f for f in touched if f not in families]
    if designed:
        lp = fa.to_l2q_full(policy, "exact")
        spec = fa.HEADLINE.with_(negate=coherent["negate"])
        from l2q_full.bloqs.qft import ReferenceQFT

        for family, (w, count, row) in _family_sites(fa, coherent, designed).items():
            if skip_width is not None:
                need(w != skip_width,
                     f"QFT family {family!r}: its width {w} is the measured register's (xW = {skip_width}), whose "
                     f"QFT the semiclassical readout removes; refusing rather than substituting into a part that is "
                     f"not in the measured circuit")
            backend, target, max_error = plan[family]
            # The engine's own price for this site, recomputed from the in-epoch bloq and cross-checked against
            # the frozen part row in the engine's own float association (`fullalg.parts`: `split(bloq).scale(k)`,
            # then `base + rot * tpr`), the like-for-like check `readout_overlay` makes.
            exact = fa.cc.split(fa.pr.PolicyQFT(lp, w, spec))
            scaled = exact.scale(count)
            need(int(row["rotations"]) == scaled.rot and row["T"] == scaled.base + scaled.rot * coherent["T_per_rotation"],
                 f"QFT family {family!r}: the coherent part at width {w} disagrees with the exact cost split of "
                 f"PolicyQFT({w}); the overlay will not substitute into a census it cannot reproduce")
            allowance = allowance_of(family, max_error) if target == "approximate" else Fraction(0)
            entry = {"width": w, "count": count, "backend": backend, "target": target,
                     "maxError": max_error, "allowance": str(allowance),
                     "exactTBase": int(exact.base), "exactRotations": int(exact.rot)}
            if backend == "ross_selinger":
                depth = band_depth(w, count, allowance)
                k = depth - 1
                need(k >= 1, f"QFT family {family!r}: max_error {max_error!r} admits a band of depth {depth}, "
                             f"which keeps no controlled phase; it is reported, not priced")
                band_base, band_rot = band_census(w, k)
                engine_band = fa.cc.split(ReferenceQFT(w, k_max=k))
                need(int(engine_band.base) == band_base and int(engine_band.rot) <= band_rot,
                     f"QFT family {family!r}: ReferenceQFT({w}, k_max={k}) splits as ({engine_band.base}, "
                     f"{engine_band.rot}), which the closed-form band ({band_base}, {band_rot}) does not cover")
                error = band_error(w, count, depth)
                base += (band_base - exact.base) * count
                rotations += (band_rot - exact.rot) * count
                site_workspace[family] = None             # the ladder: one And temporary at a time
                entry.update(construction="banded", kMax=k, banded=bool(k < w - 1),
                             bandedTBase=band_base, bandedRotations=band_rot,
                             realization="ReferenceQFT(w, k_max): a Coppersmith-banded Draper ladder replacing the "
                                         "native template at this site; rotations counted in closed form")
            else:
                # Banded exactly as the Ross-Selinger arm (an exact family keeps every distance: allowance 0, depth w),
                # then each target's kept phases are one exact addition into the shared gradient.
                depth = band_depth(w, count, allowance)
                k = depth - 1
                need(k >= 1, f"QFT family {family!r}: max_error {max_error!r} admits a band of depth {depth}, "
                             f"which keeps no controlled phase; it is reported, not priced")
                p = merged_plan(w, k)
                error = band_error(w, count, depth)
                base += (4 * p["ands"] - exact.base) * count
                rotations -= exact.rot * count
                b_needed = max(b_needed, k + 1)
                site_workspace[family] = p["workspace"]
                catalysed.append(family)
                entry.update(construction="bandedMerged", kMax=k, banded=bool(k < w - 1), b=k + 1,
                             mergedAnds=p["ands"], mergedTBase=4 * p["ands"], workspaceQubits=p["workspace"],
                             realization="the band's kept phases, per target one CtrlAddIntoCatalyst(m + 1, m) into the "
                                         "shared gradient's top m + 1 bits, exact on b = k_max + 1 bits; no rotation")
            need(error <= allowance,
                 f"QFT family {family!r}: its error bound {float(error)!r} exceeds its max_error {max_error!r}")
            error_bounds[family] = error
            claimed += allowance
            entry["errorBound"] = _upper_float(error)
            families[family] = entry

    need(base >= 0 and rotations >= 1,
         f"QFT design produced an invalid census (T_base={base}, rotations={rotations})")
    identity = not designed
    catalysed = sorted(catalysed)
    pool = Fraction(fa.sc.ROTATION_FRACTION) * delta_exact
    if identity:
        # Nothing carved: the engine's own division, so the default is the identical float expression.
        rate = fa.sc.ts_per_rotation(delta, rotations)
        eps = fa.sc.rotation_eps(delta, rotations)
        prep = rest = None
    else:
        prep = min(pool / 10, (pool - claimed) / 2) if catalysed else Fraction(0)
        rest = pool - claimed - prep
        named = {f: plan[f][2] for f in designed if plan[f][1] == "approximate"}
        need(rest > 0,
             f"QFT design: the families' max_error allowances {named} sum to {float(claimed)!r}, which leaves no "
             f"synthesis budget out of the pool {float(pool)!r} (half of delta {delta_key}); it is refused, not priced")
        eps = _lower_float(rest / rotations)
        rate = fa.sc.ts_per_rotation_eps(eps)
        ledger = sum(error_bounds.values(), Fraction(0)) + prep + rotations * Fraction(eps)
        need(ledger <= pool <= delta_exact,
             f"QFT design: its error ledger {float(ledger)!r} exceeds the pool {float(pool)!r}")

    catalyst, prep_t = None, 0.0
    if catalysed:
        cs, _cat = _catalysis()
        b = b_needed
        need(b >= B_MIN, f"QFT families {catalysed}: a catalyst of {b} qubits is below {B_MIN}")
        prep_rotations = b - 3
        need(0 < prep / prep_rotations < 1,
             f"QFT families {catalysed}: a catalyst of width {b} would prepare {prep_rotations} rotations against "
             f"a budget of {float(prep)!r}, which is not a usable per-rotation tolerance")
        prep_t = float(cs.catalyst_prep_T(b, float(prep)))
        need(prep_t > 0.0, f"QFT families {catalysed}: catalyst_prep_T({b}) is {prep_t!r}")
        catalyst = {"backend": "catalytic", "families": catalysed, "b": b, "bRule": B_RULE, "mergeRule": MERGE_RULE,
                    "registerQubits": b, "prepBudget": str(prep), "prepRotations": prep_rotations, "prepT": prep_t,
                    "T": prep_t,
                    "spec": "l2q_full/catalysis/catalyst.py CtrlAddIntoCatalyst, ctrl_add_ands; spec.py catalyst_prep_T",
                    "realization": "one shared PhaseGradientState(b), prepared once (b - 3 Ross-Selinger rotations "
                                   "against the prep budget, plus 1 T), live for the whole run and restored after "
                                   "every addition"}
    total = base + rotations * rate + prep_t
    extra = 0
    if designed:
        extra = site_excess(_site_extras(fa, policy, coherent, skip_width is not None), peak_extra, site_workspace, need)
    budget = None
    if not identity:
        budget = {"delta": delta_key, "pool": str(pool), "allowances": str(claimed), "prepBudget": str(prep),
                  "synthesisBudget": str(rest), "prepRule": PREP_RULE, "bandRule": BAND_RULE,
                  "ledger": {"qftApproximation": _upper_float(sum(error_bounds.values(), Fraction(0))),
                             "gradientPrepare": float(prep), "synthesis": float(rest)}}
    return {"T": total, "TBase": int(base), "rotations": int(rotations), "tPerRotation": rate, "eps": eps,
            "families": families, "substituted": sorted(designed), "catalysed": catalysed, "catalyst": catalyst,
            "identity": identity, "budget": budget, "catalystQubits": catalyst["b"] if catalyst else 0,
            "workspaceQubits": extra}


def project(policy, coherent, design=None):
    """Price `policy`'s run with the QFT design applied to the frozen coherent result.

    `design=None`, an all-exact Ross-Selinger design, and a design whose families are all absent or None are
    BIT-IDENTICAL to `coherent`: nothing is substituted or carved, so the division returns the same rate and
    `base + rotations * rate` is the same float expression the engine evaluated."""
    fa = engine.load().fullalg
    _check_reference(fa, coherent, "QFT overlay")
    plan = parse_design(design)
    j = price_design(policy, coherent, plan, base=int(coherent["T_base"]), rotations=int(coherent["rotations"]),
                     peak_extra=coherent["peak_extra"])
    added = j["catalystQubits"] + j["workspaceQubits"]
    peak = int(coherent["qubits_peak"]) + added
    return {
        "mode": "qft-design-axis", "pricingRule": RULE, "ruleSha256": rule_fingerprint(), "epoch": EPOCH,
        "T": j["T"], "TBase": j["TBase"], "rotations": j["rotations"], "tPerRotation": j["tPerRotation"],
        "eps": j["eps"], "families": j["families"], "substituted": j["substituted"], "catalysed": j["catalysed"],
        "catalyst": j["catalyst"], "identity": j["identity"], "budget": j["budget"],
        "tNote": "T = TBase + rotations * tPerRotation + catalyst.T; tPerRotation divides the synthesis budget "
                 "(the pool less the families' allowances and the gradient's preparation) over the rotations",
        "catalystQubits": j["catalystQubits"], "workspaceQubits": j["workspaceQubits"],
        "qubitsPeak": peak, "qubitsUpperBound": int(coherent["qubits"]) + added, "qubitTimesT": peak * j["T"],
        "qubitsNote": "the engine's frozen peak and layout plus the shared catalyst register, plus whatever the design's QFT "
                      "sites (a catalytic family's 2 k_max addition temporaries per call) add to the run's live-qubit peak; "
                      "where they stay under the peak set by the other steps they add nothing.",
        # `mapping.ranked_price_of` ranks the projection's `qubits` when it offers one: only when the design adds
        # qubits (a catalyst), and then the PEAK, the unit of the panel's own `qubits`.
        **({"qubits": peak} if added else {}),
    }
