"""Cost as a property of the wired program (T4 of TODO_ARTIFACT_INFRASTRUCTURE.2026-09-11.md).

The estimate and the artifact are one object: a `program.Program` (frame + ONE step template + schedule + IQFT)
is walked into its census parts, and every census key is priced by the T3 leaf catalogue
(`leaf_contracts.build_catalogue`) under an explicit toggle vector. Nothing here re-derives a symbolic curve.

    T_total(L) = T_frame + L * T_step + T_iqft(L)

(the step template is multiplier-symbolic: its U1 / U5 grids are generic-angle rows priced identically for
every multiplier; `expand=True` prices the fully expanded program instead and must agree unless a multiplier
is degenerate, see `family.degenerate_multipliers`).

Lowerings: `gadget` (per-cell phase-gradient gadget), `windowed` (QROM windows into the shared gradient),
`qualtran` (the frozen tree's concrete bloqs -- CZPow cells as rotations at `TS_PER_ROTATION` -- the comparison
line `family.cost` uses; `qualtran_gate_counts` is exactly `family_bloq`'s call graph summed).

And-pair contract: `constants.T_PER_AND_PAIR` (8 T, unitary uncompute -- the 14 Sep 2026 decision); the
measured column (4 T) is reported alongside, labelled.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import constants
from . import family as F
from . import leaf_contracts as LCn
from . import leaf_currency as LC
from . import program as P

__all__ = ["Toggles", "PartCost", "ProgramCost", "build_program", "program_census", "price_census",
           "qualtran_gate_counts", "cost", "cost_table", "LOWERINGS", "STOPS"]

LOWERINGS = ("gadget", "windowed", "qualtran")
STOPS = ("cost_optimal", "R0")


@dataclass(frozen=True)
class Toggles:
    """The toggle vector a cost is published with (registry names in brackets)."""
    stop: str = "cost_optimal"          # [recursion_rule] "R0" = Lean's full recursion; "cost_optimal" = v2 width rule + currency stop
    lowering: str = "windowed"          # [rotation_lowering / windowed_lookup] gadget | windowed | qualtran
    b: int = 40                         # [phase_grad_bits_b]
    window: Optional[Tuple[int, int]] = None   # [windowed_lookup] None -> the measured best for b
    small_exact: bool = True            # [small_resolution_exact]
    drop_rule: bool = True              # [leaf_drop_rule]
    and_pair: str = "unitary"           # [and_uncompute] unitary (8 T) | measured (4 T)
    currency: str = "model11"           # stop-rule currency: model11 | gadget | windowed
    ts_per_rotation: int = constants.TS_PER_ROTATION   # qualtran lowering only

    def resolved_window(self) -> Tuple[int, int]:
        if self.window is not None:
            return tuple(self.window)
        return tuple(LCn.choose_window(self.b, and_pair=LCn.AndPair(self.and_pair))["best"])

    def vector(self) -> Dict[str, Any]:
        d = asdict(self)
        d["window"] = list(self.resolved_window()) if self.lowering == "windowed" else None
        d["pair_t"] = LCn.AndPair(self.and_pair).t
        return d

    def label(self) -> str:
        w = f" {self.resolved_window()[0]}x{self.resolved_window()[1]}" if self.lowering == "windowed" else ""
        return (f"{self.stop} / {self.lowering}{w} / b={self.b} / small_exact={'on' if self.small_exact else 'off'} / "
                f"drop={'on' if self.drop_rule else 'off'} / And pair {self.and_pair} ({LCn.AndPair(self.and_pair).t} T)")


@dataclass
class PartCost:
    name: str
    keys: int
    leaves: int
    rows: int
    t: int                       # at the toggle's And-pair contract
    t_measured: int              # the 4 T column (comparison)
    t_unitary: int               # the 8 T column
    error: float
    provenance: Dict[str, int]   # price basis -> T (at the toggle's contract)
    unpaired_rows: int
    built_s: float


@dataclass
class ProgramCost:
    toggles: Dict[str, Any]
    label: str
    params: Dict[str, Any]
    parts: Dict[str, PartCost]
    t_total: int
    t_total_measured: int
    t_total_unitary: int
    error_total: float
    formula: str
    gate_counts: Optional[Dict[str, int]] = None      # qualtran lowering: summed GateCounts of the expanded program
    notes: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# --------------------------------------------------------------------------------------------- program
def _currency(name: str, b: int, and_pair: str, window: Optional[Tuple[int, int]]):
    pair_t = LCn.AndPair(and_pair).t
    if name == "model11":
        return LC.ModelCurrency(constants.TS_PER_ROTATION)
    if name == "gadget":
        return LC.GadgetCurrency(b, pair_t)
    if name == "windowed":
        wx, wz = window or tuple(LCn.choose_window(b, and_pair=LCn.AndPair(and_pair))["best"])
        return LC.WindowedCurrency(wx, wz, b, pair_t)
    raise ValueError(f"unknown currency {name!r}")


def build_program(fam: F.Family, a: int, N: int, n: int, m: int, L: int, toggles: Toggles) -> P.Program:
    """The wired program under the toggle's recursion rule (the stop is a predicate in the family walk)."""
    if toggles.stop == "R0":
        wiring = P.Wiring(fam)
    elif toggles.stop == "cost_optimal":
        cur = _currency(toggles.currency, toggles.b, toggles.and_pair, toggles.window)
        wiring = P.Wiring(fam, stop=P.cost_optimal_stop(fam, cur))
    else:
        raise ValueError(f"unknown stop {toggles.stop!r}")
    return P.Program(wiring, a, N, n, m, L)


def program_census(prog: P.Program) -> Dict[str, Counter]:
    """`frame`, `step` (the multiplier-symbolic template), `iqft` -- the three stored pieces."""
    return {"frame": P.census(P.seq(prog.frame)), "step": P.census(P.seq(prog.template.body)),
            "iqft": P.census(P.seq(prog.iqft))}


# --------------------------------------------------------------------------------------------- pricing
def _t_of_counts(counts: Tuple[int, int, int, int, int], pair_t: int, tsr: int) -> int:
    a, tf, t, rot, _cl = counts
    return int(t + pair_t * a + 4 * tf + tsr * rot)


def qualtran_gate_counts(census, N: Optional[int], nd: Optional[int], *, exact: bool = False):
    """The frozen tree's `GateCounts` of a census through the SAME bloqs `family._bloqs_for_leaf` prices
    (`CompactSignedPhaseGrid` for grids, the arithmetic and prim bloqs) -- `family_bloq`'s call graph summed.
    `exact=True` runs `QECGatesCost` on every bloq (the comparison with `family.cost`; slow for a 2048-bit
    `CmpLtNW`); otherwise `leaf_contracts._qec_counts` (representative comparator: exact in every non-Clifford column)."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    from qualtran.resource_counting import GateCounts
    total = GateCounts()
    for key, mult in (census.items() if hasattr(census, "items") else census):
        key = tuple(key)
        if key[0] in ("H", "X", "RadixReverse"):
            for b, c in F._bloqs_for_leaf(key, int(mult), N, nd).items():
                total = total + get_cost_value(b, QECGatesCost()) * c
            continue
        for b, c in F._bloqs_for_leaf(key, 1, N, nd).items():
            if exact:
                gc = get_cost_value(b, QECGatesCost())
            else:
                a, tf, t, rot, cl = LCn._qec_counts(b)
                gc = GateCounts(t=t, toffoli=tf, and_bloq=a, rotation=rot, clifford=cl)
            total = total + gc * (c * int(mult))
    return total


def price_census(census, toggles: Toggles, *, N: Optional[int], nd: Optional[int], name: str = "") -> PartCost:
    t0 = time.time()
    items = list(census.items()) if hasattr(census, "items") else list(census)
    keys, leaves = len(items), sum(int(m) for _, m in items)
    pair = LCn.AndPair(toggles.and_pair)
    if toggles.lowering == "qualtran":
        gc = qualtran_gate_counts(items, N, nd)
        tup = (int(gc.and_bloq), int(gc.toffoli), int(gc.t), int(gc.rotation), int(gc.clifford))
        t = _t_of_counts(tup, pair.t, toggles.ts_per_rotation)
        return PartCost(name, keys, leaves, keys, t, _t_of_counts(tup, 4, toggles.ts_per_rotation),
                        _t_of_counts(tup, 8, toggles.ts_per_rotation), float("nan"),
                        {f"Qualtran concrete bloqs, rotations at {toggles.ts_per_rotation} T": t}, 0, time.time() - t0)
    rows = LCn.build_catalogue(items, b=toggles.b, N=N, nd=nd, lowering=toggles.lowering,
                               window=toggles.resolved_window() if toggles.lowering == "windowed" else (2, 2),
                               small_exact=toggles.small_exact, and_pair=pair, drop_rule=toggles.drop_rule)
    summ = LCn.catalogue_summary(rows)
    prov: Counter = Counter()
    for r in rows:
        basis = r.certificate.get("price_basis") if r.family == "phase_grid" else None
        if basis is None:
            if r.family == "phase_grid":
                basis = "exact per cell / per table"
            elif r.family in ("arithmetic", "prim"):
                scopes = {b.get("scope", "").split(":")[0] for b in r.certificate.get("bloqs", [])}
                basis = "QECGatesCost, pairing " + ("/".join(sorted(scopes)) or "n/a")
            else:
                basis = "Clifford"
        prov[basis] += r.t_total
    t_at, t_measured, t_unit = summ["t"], summ["t_measured_and"], summ["t_unitary_and"]
    return PartCost(name, keys, leaves, summ["rows"], int(t_at), int(t_measured), int(t_unit), float(summ["error_bound"]),
                    dict(prov), int(summ["rows_without_pairing"]), time.time() - t0)


def cost(fam: F.Family, a: int, N: int, n: int, m: int, L: int, toggles: Toggles = Toggles(), *,
         expand: bool = False, prog: Optional[P.Program] = None,
         census: Optional[Dict[str, Counter]] = None) -> ProgramCost:
    """The T-count of the order-finding program `(a, N, n, m, L)` under `toggles`, from its wired census
    (`census` = a `program_census` result lets callers share one wiring across toggles without keeping the tree)."""
    t0 = time.time()
    if census is None:
        prog = prog if prog is not None else build_program(fam, a, N, n, m, L, toggles)
    nd = n + 1
    parts: Dict[str, PartCost] = {}
    notes: List[str] = []
    if expand:
        if prog is None:
            prog = build_program(fam, a, N, n, m, L, toggles)
        cen = P.census(prog.expand())
        parts["program"] = price_census(cen, toggles, N=N, nd=nd, name="program (expanded)")
        t_total, t_m, t_u = parts["program"].t, parts["program"].t_measured, parts["program"].t_unitary
        err = parts["program"].error
        formula = "T(expanded program)"
    else:
        cp = census if census is not None else program_census(prog)
        prog = None                   # the wired tree (~450 MB at 2048 bits) is not needed once its censuses are taken
        import gc
        gc.collect()
        for name in ("frame", "step", "iqft"):
            parts[name] = price_census(cp[name], toggles, N=N, nd=nd, name=name)
        t_total = parts["frame"].t + L * parts["step"].t + parts["iqft"].t
        t_m = parts["frame"].t_measured + L * parts["step"].t_measured + parts["iqft"].t_measured
        t_u = parts["frame"].t_unitary + L * parts["step"].t_unitary + parts["iqft"].t_unitary
        err = parts["frame"].error + L * parts["step"].error + parts["iqft"].error
        formula = f"T_frame + {L} * T_step + T_iqft"
        deg = F.degenerate_multipliers(a, N, L)
        if deg:
            notes.append(f"degenerate multipliers at steps {deg}: the expanded program prices those steps lower")
    gc = None
    if toggles.lowering == "qualtran" and (expand or L <= 64):
        if prog is None:
            prog = build_program(fam, a, N, n, m, L, toggles)
        g = qualtran_gate_counts(P.census(prog.expand()), N, nd)
        if g is not None:
            gc = {"t": int(g.t), "toffoli": int(g.toffoli), "and_bloq": int(g.and_bloq), "rotation": int(g.rotation),
                  "clifford": int(g.clifford)}
    return ProgramCost(toggles.vector(), toggles.label(), {"a": a, "N_bits": N.bit_length(), "n": n, "m": m, "L": L},
                       parts, int(t_total), int(t_m), int(t_u), float(err), formula, gc,
                       notes + [f"built in {time.time() - t0:.1f}s"])


def cost_table(fam: F.Family, a: int, N: int, n: int, m: int, L: int, toggle_list: Sequence[Toggles]) -> List[Dict[str, Any]]:
    """One row per toggle vector; programs are shared across toggles with the same recursion rule."""
    censuses: Dict[Tuple[str, str, int, str], Dict[str, Counter]] = {}
    out = []
    for tg in toggle_list:
        key = (tg.stop, tg.currency if tg.stop != "R0" else "-", tg.b if tg.currency != "model11" else 0, tg.and_pair if tg.currency != "model11" else "-")
        if key not in censuses:
            censuses[key] = program_census(build_program(fam, a, N, n, m, L, tg))   # the program itself is dropped
        c = cost(fam, a, N, n, m, L, tg, census=censuses[key])
        row = {"label": c.label, "toggles": c.toggles, "T": c.t_total, "T_measured_pairs": c.t_total_measured,
               "T_unitary_pairs": c.t_total_unitary, "T_step": c.parts["step"].t, "T_iqft": c.parts["iqft"].t,
               "error_bound": c.error_total, "provenance": {k: v.provenance for k, v in c.parts.items()},
               "unpaired_rows": sum(v.unpaired_rows for v in c.parts.values()), "notes": c.notes,
               "census": {k: {"keys": v.keys, "leaves": v.leaves} for k, v in c.parts.items()}}
        out.append(row)
    return out
