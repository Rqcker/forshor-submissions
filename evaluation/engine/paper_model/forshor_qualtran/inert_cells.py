r"""Which leaf phase cells of a policy phase product are provably inert — by value-range proof.

A signed phase product's schoolbook leaf on registers `x` (width `wx`) and `z` (width `wz`) is one
`CPhase(x_i, z_j, θ_ij)` per cell `(i, j)`. The cell contributes `θ_ij · x_i · z_j`, so it does
nothing on every input if `x_i` or `z_j` is **constantly 0** at the moment the leaf runs. A bit
that is constantly equal to the register's sign bit (a two's-complement sign copy) is not inert,
but its cells can be **folded** into the sign-bit cell (one rotation with the summed weight).

THE PROOF
---------
Every register value is tracked as an exact integer interval, which is sound (a superset of the
reachable values) at every step:

* top level: `x, z ∈ [-2^(n-1), 2^(n-1) - 1]`;
* a node splits each operand as ForShor does (limb `W = min(wx//k, wz//k)`; lower chunks are the
  unsigned bit fields `[iW, (i+1)W)`, the top chunk is the signed rest). A chunk's interval is
  derived from the operand interval: the top chunk is `v >> a` (monotone, exact); a lower chunk
  `(v >> a) & (2^W - 1)` is monotone wherever `v >> b` is constant, else it is the full range;
* the table's program applies the same integer linear map to the x chunks and to the z chunks
  (`mirrors/width.run_rows` semantics); at each `phaseProduct i` checkpoint the child operand is
  slot `i`'s linear form `Σ c_j · chunk_j`, whose range over the chunk box is exact for the box
  (`Σ c_j · [lo_j, hi_j]`, sign-aware). The child's register width is the node's `nextWidth`;
  the interval is asserted to fit it signed (otherwise the lowering would overflow);
* a leaf with value interval `I` in a width-`w` register: bit `i` is constant iff
  `lo >> i == hi >> i` (then its value is `(lo >> i) & 1`); bit `i < w-1` is a sign copy iff
  `I ⊆ [-2^i, 2^i - 1]` and it is not constant.

Box over-approximation (chunks of one value treated as independent) only widens intervals, so
every bit declared constant really is constant: the inert set is **sound**; it may be incomplete,
which `tests/test_inert_cells.py` measures against simulation.

Eliding inert cells or folding sign copies **changes the circuit ForShor proves correct**. These
are potential optimisations to raise with the ForShor author, not something L2Q applies.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from .policy import PhasePolicy

Interval = Tuple[int, int]


def _limb_split(w: int, limb: int, k: int) -> List[int]:
    return [(w - i * limb) if i + 1 == k else limb for i in range(k)]


def chunk_intervals(I: Interval, w: int, limb: int, k: int) -> List[Interval]:
    """Intervals of the k chunks of a value in `I` held in a width-`w` register."""
    lo, hi = I
    widths = _limb_split(w, limb, k)
    out, a = [], 0
    for idx, cw in enumerate(widths):
        if idx + 1 == k:                                   # signed top chunk: arithmetic shift
            out.append((lo >> a, hi >> a))
        else:
            b, m = a + cw, (1 << cw) - 1
            if (lo >> b) == (hi >> b):                     # monotone on the interval
                out.append(((lo >> a) & m, (hi >> a) & m))
            else:
                out.append((0, m))
        a += cw
    return out


def linear_interval(row: Sequence[int], box: Sequence[Interval]) -> Interval:
    lo = hi = 0
    for c, (a, b) in zip(row, box):
        lo += min(c * a, c * b)
        hi += max(c * a, c * b)
    return lo, hi


def checkpoint_rows(k: int, ops: Sequence[Dict]) -> List[List[int]]:
    """Slot rows (coefficients over the k original chunks) at each `phaseProduct` checkpoint."""
    st = [[1 if a == b else 0 for b in range(k)] for a in range(k)]
    rows = []
    for op in ops:
        t = op["op"]
        if t == "phaseProduct":
            rows.append(list(st[op["i"]]))
        elif t == "addScaled":
            s = (-1 if op["negSrc"] else 1) * (1 << op["shift"])
            st[op["dst"]] = [a + s * b for a, b in zip(st[op["dst"]], st[op["src"]])]
        elif t == "shiftL":
            st[op["i"]] = [c << op["n"] for c in st[op["i"]]]
        elif t == "shiftR":
            d = 1 << op["n"]
            if any(c % d for c in st[op["i"]]):
                raise ValueError(f"inexact shiftR in {op}")
            st[op["i"]] = [c // d for c in st[op["i"]]]
        elif t == "negate":
            st[op["i"]] = [-c for c in st[op["i"]]]
        else:
            raise ValueError(t)
    return rows


@lru_cache(maxsize=None)
def _rows_for(k: int, ops_frozen) -> Tuple[Tuple[int, ...], ...]:
    return tuple(tuple(r) for r in checkpoint_rows(k, [dict(o) for o in ops_frozen]))


def table_rows(T) -> Tuple[Tuple[int, ...], ...]:
    """`checkpoint_rows` of a table, cached."""
    return _rows_for(T.k, T.ops_frozen)


@dataclass(frozen=True)
class Leaf:
    wx: int
    wz: int
    Ix: Interval
    Iz: Interval


def leaves(P: PhasePolicy, n: int, wz: Optional[int] = None) -> Iterator[Leaf]:
    """Every schoolbook leaf of `P` at `(n, wz)`, in the circuit's execution (DFS) order, with the
    proven intervals of its operands."""
    wz = n if wz is None else wz
    top = lambda w: (-(1 << (w - 1)), (1 << (w - 1)) - 1) if w else (0, 0)

    def go(wx, wz_, Ix, Iz):
        s = P.step(wx, wz_)
        if s is None:
            yield Leaf(wx, wz_, Ix, Iz)
            return
        T, w2 = s
        limb = min(wx // T.k, wz_ // T.k)
        bx = chunk_intervals(Ix, wx, limb, T.k)
        bz = chunk_intervals(Iz, wz_, limb, T.k)
        lim = (-(1 << (w2 - 1)), (1 << (w2 - 1)) - 1)
        for row in table_rows(T):
            cx, cz = linear_interval(row, bx), linear_interval(row, bz)
            for c in (cx, cz):
                if not (lim[0] <= c[0] and c[1] <= lim[1]):
                    raise AssertionError(f"slot interval {c} does not fit {w2} signed bits")
            yield from go(w2, w2, cx, cz)
    yield from go(n, wz, top(n), top(wz))


@dataclass(frozen=True)
class BitClass:
    dead: Tuple[int, ...]       # constantly 0
    copy: Tuple[int, ...]       # live, constantly equal to the sign bit
    live: int                   # bits not constantly 0 (incl. sign and copies)

    @property
    def distinct(self) -> int:  # live bits after folding sign copies into the sign bit
        return self.live - len(self.copy)


def classify_bits(I: Interval, w: int) -> BitClass:
    lo, hi = I
    dead, copy = [], []
    for i in range(w):
        const = (lo >> i) == (hi >> i)
        if const and ((lo >> i) & 1) == 0:
            dead.append(i)
        elif i < w - 1 and not const and -(1 << i) <= lo and hi <= (1 << i) - 1:
            copy.append(i)
    return BitClass(tuple(dead), tuple(copy), w - len(dead))


@dataclass
class Census:
    leaves: int = 0
    cells: int = 0              # rotations as priced (one per cell)
    inert: int = 0              # cells with a constantly-0 bit
    after_fold: int = 0         # rotations if inert cells are elided and sign copies folded

    def add(self, lf: Leaf) -> None:
        x, z = classify_bits(lf.Ix, lf.wx), classify_bits(lf.Iz, lf.wz)
        self.leaves += 1
        self.cells += lf.wx * lf.wz
        self.inert += lf.wx * lf.wz - x.live * z.live
        self.after_fold += x.distinct * z.distinct

    @property
    def inert_fraction(self) -> float:
        return self.inert / self.cells if self.cells else 0.0

    @property
    def fold_fraction(self) -> float:
        """Share of rotations removable by eliding inert cells AND folding sign copies."""
        return 1 - self.after_fold / self.cells if self.cells else 0.0


def census(P: PhasePolicy, n: int, wz: Optional[int] = None) -> Census:
    c = Census()
    for lf in leaves(P, n, wz):
        c.add(lf)
    return c


def inert_cell_mask(P: PhasePolicy, n: int) -> List[bool]:
    """Per leaf cell, in circuit order (leaf by leaf, x-bit outer, z-bit inner — the order of
    `naiveSignedPhaseGates`): True if proven inert."""
    out: List[bool] = []
    for lf in leaves(P, n):
        dx, dz = set(classify_bits(lf.Ix, lf.wx).dead), set(classify_bits(lf.Iz, lf.wz).dead)
        out.extend((i in dx) or (j in dz) for i in range(lf.wx) for j in range(lf.wz))
    return out


# ============================================================================ exact (enumerative) check
def _chunks(v: int, widths: Sequence[int]) -> List[int]:
    out, a = [], 0
    for idx, cw in enumerate(widths):
        out.append((v >> a) if idx + 1 == len(widths) else (v >> a) & ((1 << cw) - 1))
        a += cw
    return out


def _ap_or(mask: int, step: int, m: int) -> int:
    """`OR_{x < m} mask << (step * x)` in O(log m) big-integer operations (binary doubling)."""
    out, block, blen, shift = 0, mask, 1, 0
    while m:
        if m & 1:
            out |= block << shift
            shift += step * blen
        block |= block << (step * blen)
        blen *= 2
        m >>= 1
    return out


def _bits_to_set(off: int, mask: int) -> set:
    bits = bin(mask)[:1:-1]                           # LSB first, linear time
    return {off + i for i, ch in enumerate(bits) if ch == "1"}


def _sumset(row: Sequence[int], ranges: Sequence[range]) -> set:
    """`{Σ c_j x_j : x_j ∈ r_j}` exactly (`r_j` contiguous ranges), as a big-integer bitset."""
    off, mask = 0, 1
    for c, r in zip(row, ranges):
        if c == 0 or len(r) == 0:
            continue
        m, step = len(r), abs(c)
        mask = _ap_or(mask, step, m)
        off += min(c * r[0], c * r[-1])
    return _bits_to_set(off, mask)


def exact_leaf_sets(P: PhasePolicy, n: int) -> List[Tuple[int, frozenset]]:
    """Per leaf (DFS order): `(width, exact set of x-side operand values)`. Exact: at the top level
    the chunks of a full-range signed operand are independent full ranges, so each child's set is
    a sumset; below that each node's set is the image of its parent's set. (The z side is
    identical for square nodes: same split, same program.)"""
    out: List[Tuple[int, frozenset]] = []

    def node(w: int, S: Optional[set]):
        s = P.step(w, w)
        if s is None:
            out.append((w, frozenset(S if S is not None else range(-(1 << (w - 1)), 1 << (w - 1)))))
            return
        T, w2 = s
        widths = _limb_split(w, min(w // T.k, w // T.k), T.k)
        rows = table_rows(T)
        if S is None:                                  # top level: independent full chunk ranges
            ranges = [range(0, 1 << cw) for cw in widths[:-1]] + \
                     [range(-(1 << (widths[-1] - 1)), 1 << (widths[-1] - 1))]
            for row in rows:
                node(w2, _sumset(row, ranges))
        else:
            ch = [_chunks(v, widths) for v in S]
            for row in rows:
                node(w2, {sum(c * x for c, x in zip(row, cs)) for cs in ch})

    node(n, None)
    return out


def exact_bit_classes(P: PhasePolicy, n: int) -> List[BitClass]:
    """Per leaf (DFS order), the EXACT dead / sign-copy bits (from :func:`exact_leaf_sets`)."""
    res = []
    for w, S in exact_leaf_sets(P, n):
        m = (1 << w) - 1
        ever_set = ever_diff = 0
        for u in S:
            bits = u & m
            ever_set |= bits
            ever_diff |= bits ^ (m if u < 0 else 0)
        dead = tuple(i for i in range(w) if not (ever_set >> i) & 1)
        copy = tuple(i for i in range(w - 1) if (ever_set >> i) & 1 and not (ever_diff >> i) & 1)
        res.append(BitClass(dead, copy, w - len(dead)))
    return res


# ============================================================================ report
def _t(base: float, rot: int, budget) -> float:
    return base + (rot * budget.t_per_rotation(rot) if rot else 0.0)


def saving_row(name: str, P: PhasePolicy, n: int, total_base: float, total_rot: int, budget) -> Dict:
    """T under the tcount model (rotations at ross_selinger_t(delta_rot / R), R = own rotations)
    as shipped, if inert cells were elided, and if additionally sign copies were folded. Leaf
    cells are priced as Qualtran's CZPow (4 T And + 1 rotation, the shipped grid leaf)."""
    c = census(P, n)
    assert c.cells <= total_rot, "census cells exceed the policy's rotations"
    t0 = _t(total_base, total_rot, budget)
    t1 = _t(total_base - 4 * c.inert, total_rot - c.inert, budget)
    removed = c.cells - c.after_fold
    t2 = _t(total_base - 4 * c.inert, total_rot - removed, budget)
    return {"policy": name, "n": n, "leaves": c.leaves, "rotations": c.cells,
            "inert_cells": c.inert, "inert_frac": round(c.inert_fraction, 4),
            "after_elide_and_fold": c.after_fold, "fold_frac": round(c.fold_fraction, 4),
            "T_shipped": round(t0), "T_elide_inert": round(t1), "T_elide_and_fold": round(t2),
            "saving_elide": round(1 - t1 / t0, 4), "saving_elide_fold": round(1 - t2 / t0, 4)}


def main(argv=None) -> int:
    import argparse, csv, glob, hashlib, json
    from pathlib import Path
    from . import policy_planner as pp
    from .policy import PolicyBand, ToomCookTable
    ap = argparse.ArgumentParser(description="Inert/foldable leaf-cell census of policy phase products")
    root = Path(__file__).resolve().parents[1]
    ap.add_argument("--out", type=Path, default=root / "artifacts" / "policy_inert")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    cat = pp.load_catalog(sorted(glob.glob(str(root / "artifacts/policy_figures/tables/table_k*.standard.json"))))
    by = {t.name: t for t in cat}
    planner = pp.Planner(cat, "t")
    budget = pp.default_budget()
    rows, srcs = [], {}
    for n in (256, 512, 1024, 2048):
        mf = root / f"artifacts/policy_figures/manifests/policy_optimal_T_n{n}.json"
        j = json.loads(mf.read_text())
        srcs[mf.name] = hashlib.sha256(mf.read_bytes()).hexdigest()[:16]
        P = PhasePolicy(tuple(PolicyBand(b["threshold"], ToomCookTable.from_json(b["table"]["name"], b["table"]))
                              for b in j["bands"]), name=j.get("name", f"optimal_n{n}"))
        pols = [("shipped optimal", P), ("const k2std", PhasePolicy.const(by["k2std"])),
                ("const k3std", PhasePolicy.const(by["k3std"]))]
        for name, Q in pols:
            raw = planner.policy_raw(Q, n, leaf="grid") if "leaf" in planner.policy_raw.__code__.co_varnames \
                else planner.policy_raw(Q, n)
            rows.append(saving_row(name, Q, n, raw.base, raw.rot, budget))
    with open(args.out / "inert_cells.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (args.out / "inert_cells.json").write_text(json.dumps(
        {"rows": rows, "manifests_sha256_16": srcs,
         "budget": {"delta": budget.delta, "rotation_fraction": budget.rotation_fraction},
         "leaf_pricing": "Qualtran CZPow per cell: 4 T (And) + 1 rotation",
         "method": "forshor_qualtran.inert_cells: exact-interval proof (sound)"}, indent=1))
    for r in rows:
        print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
