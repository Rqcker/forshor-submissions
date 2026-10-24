"""A `CompositeBloq.flatten` with linear time, for the frozen Qualtran revision.

`CompositeBloq.flatten_once` in `vendor/` calls `_map_soqs(in_soqs, soq_map)` one time for each bloq
instance. On each call, `_map_soqs` builds a new dict from the full `soq_map` list, and that list
becomes longer. Thus one pass is O(N^2) in the number of instances. A policy phase product at the
QUALTRAN tier at n = 27 has tens of thousands of instances, and the pass does not finish.
This module uses the same algorithm, but it keeps the old -> new soquet map as a dict that it extends
one step at a time. Thus a pass is O(N). We do not patch the vendored tree, because all costs are
pinned to it. The result is the same composite bloq that `flatten` returns.
"""
from __future__ import annotations

from typing import Callable, Dict

import numpy as np
from qualtran import BloqBuilder, CompositeBloq, DecomposeNotImplementedError, DecomposeTypeError
from qualtran._infra.composite_bloq import DidNotFlattenAnythingError


def _add_pairs(flat: Dict, old, new) -> None:
    if BloqBuilder.is_single(old):
        flat[old.item()] = new.item()
        return
    assert old.shape == new.shape, (old.shape, new.shape)
    for o, n in zip(old.reshape(-1), new.reshape(-1)):
        flat[o] = n


def _map(soqs: Dict, flat: Dict) -> Dict:
    vmap = np.vectorize(flat.__getitem__, otypes=[object])
    return {name: (vmap(s) if BloqBuilder.is_ndarray(s) else flat[s.item()]) for name, s in soqs.items()}


def flatten_once(cbloq: CompositeBloq, pred: Callable = lambda binst: True) -> CompositeBloq:
    if len(cbloq.bloq_instances) == 0:
        raise DidNotFlattenAnythingError()
    bb, _ = BloqBuilder.from_signature(cbloq.signature)
    bb._i = max(binst.i for binst in cbloq.bloq_instances) + 1  # pylint: disable=protected-access
    flat: Dict = {}
    for old, new in bb.initial_soq_map(cbloq.signature.lefts()):
        _add_pairs(flat, old, new)
    did_work = False
    for binst, in_soqs, old_out in cbloq.iter_bloqsoqs():
        in_soqs = _map(in_soqs, flat)
        new_out = None
        if pred(binst):
            try:
                new_out = bb.add_from(binst.bloq, **in_soqs)
                did_work = True
            except (DecomposeTypeError, DecomposeNotImplementedError):
                pass
        if new_out is None:
            new_out = tuple(soq for _, soq in bb._add_binst(binst, in_soqs=in_soqs))  # pylint: disable=protected-access
        for o, n in zip(old_out, new_out):
            _add_pairs(flat, o, n)
    if not did_work:
        raise DidNotFlattenAnythingError()
    return bb.finalize(**_map(cbloq.final_soqs(), flat))


def flatten(cbloq: CompositeBloq, pred: Callable = lambda binst: True, max_depth: int = 1_000) -> CompositeBloq:
    """Do `cbloq.flatten(pred)`, with linear time for each pass."""
    for _ in range(max_depth):
        try:
            cbloq = flatten_once(cbloq, pred)
        except DidNotFlattenAnythingError:
            return cbloq
    raise ValueError("Max recursion depth exceeded in `flatten`.")
