"""Leaf currencies for the recursion stop rule (T1 of TODO_ARTIFACT_INFRASTRUCTURE.2026-09-11.md).

`RuleSet.decision(w, version, k_top, rule, root, ts_per_rotation)` chooses between one more Toom level and a
w x w leaf grid by comparing the level's adder cost with `leaf_T(w)`. Historically the leaf was priced with
Qualtran's heuristic `w^2 * (4 + ts_per_rotation)`. The frozen artifact prices leaves as they are EMITTED, so
the stop rule must be evaluable under the same currency. A currency is a frozen object with `.leaf(w) -> T`;
`RuleSet.leaf_T` asks it directly, and it is part of every cached decision's identity.

    ModelCurrency(tsr)              w^2 * (4 + tsr)                        the historical heuristic
    GadgetCurrency(b)               w^2 * pair_t * b                       per-leaf gadget, angle-generic bound
    WindowedCurrency(wx, wz, b)     And count of one window pair (leaf_contracts, structural-zero witness) at pair_t
                                    per pair, times ceil(w/wx)*ceil(w/wz)

`pair_t` is `constants.T_PER_AND_PAIR` (8, unitary uncompute -- the 14 Sep 2026 decision); pass 4 for the measured
convention.

`GadgetCurrency` is an upper bound: it prices every leaf as a full b-bit addition; dyadic leaves of resolution
s < b cost 4·s and pure turns beyond b+1 cost 0, so an angle-aware currency would sit below it. It is the
right currency for the U1/U5 (denominator-N) grids, which are generic at every leaf. `WindowedCurrency` is
exact for the windowed leaf (its tables are angle-independent in count; all-zero tables are rare for a generic
angle and are ignored here).
"""
from __future__ import annotations

from functools import lru_cache

import attrs

from . import constants


@attrs.frozen
class ModelCurrency:
    tsr: float = constants.TS_PER_ROTATION

    def leaf(self, w: int) -> float:
        return w * w * (constants.TS_PER_TOFFOLI + self.tsr)


@attrs.frozen
class GadgetCurrency:
    """The per-leaf phase-gradient gadget as emitted: one And pair per added bit, b bits per generic leaf."""
    b: int
    pair_t: int = constants.T_PER_AND_PAIR

    def leaf(self, w: int) -> float:
        return w * w * self.pair_t * self.b


@attrs.frozen
class WindowedCurrency:
    """One `wx x wz` window pair (2 QROM + Add(b) into the shared gradient) priced by its And count
    (`leaf_contracts._windowed_pair_cost`, structural-zero witness table) at `pair_t` per pair."""
    window_x: int
    window_z: int
    b: int
    pair_t: int = constants.T_PER_AND_PAIR

    def leaf(self, w: int) -> float:
        return _windowed_leaf_T(w, self.window_x, self.window_z, self.b, self.pair_t)


@lru_cache(maxsize=None)
def _windowed_pair_T(wx: int, wz: int, b: int, pair_t: int = constants.T_PER_AND_PAIR) -> float:
    """T of ONE full window pair (a wx x wz grid is exactly one pair): And pairs from the unary-iteration rule on
    a generic table (equal to the frozen tree's QECGatesCost, see tests/test_t3_leaf_contracts.py), `pair_t` each.
    Width-independent."""
    from .leaf_contracts import _windowed_pair_cost
    a, tf, td = _windowed_pair_cost(wx, wz, b, False)
    return float(pair_t * (a + tf) + td)


def _windowed_leaf_T(w: int, wx: int, wz: int, b: int, pair_t: int = constants.T_PER_AND_PAIR) -> float:
    """ceil(w/wx) * ceil(w/wz) full pairs at the per-pair cost (edge windows are narrower and cheaper; pricing
    them as full pairs keeps this an upper bound)."""
    pairs = -(-w // wx) * -(-w // wz)
    return pairs * _windowed_pair_T(wx, wz, b, pair_t)


def stop_widths(rules, k: int, currency, version: str = "B", rule: str = "kmy",
                widths=(64, 128, 256, 512, 1024, 2048, 4096)) -> dict:
    """{w: (levels, leaf_W, leaf_grids)} of the cost-optimal tree under `currency` -- the table T1 publishes."""
    out = {}
    for w in widths:
        t = rules.tree(w, version, k, rule, currency)
        leaf = t[-1]
        out[w] = (len(t) - 1, leaf["W"], leaf["nodes"])
    return out
