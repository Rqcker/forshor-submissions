"""The catalytic QFT construction as a circuit: the band's kept phases on each target are one controlled addition into
the shared phase-gradient register (Nam, Su & Maslov, npj Quantum Inf. 6, 26 (2020); Gidney, Quantum 2, 74 (2018)).

Two independent roots for what `qft_overlay.merged_plan` prices:

* the construction ASSEMBLED FROM THE ENGINE'S OWN BLOQS (`Hadamard`, `CtrlAddIntoCatalyst`): Qualtran's cost split
  is 8 N_cp T and 0 rotations on the real family widths and bands, and its tensor contraction on |x> |Phi_b> equals
  the banded ladder |x> times |Phi_b> (the gradient returned, the phase exact);
* an explicit GATE-LEVEL circuit (logical-AND compute 4 T, measured uncompute 0 T, CNOT, H), simulated exactly on a
  state vector with both outcomes of every measurement taken: it equals the banded ladder for every input, the
  gradient is restored, the workspace returns to |0>, the full band is the DFT, the adjoint is the inverse, and the
  gates it executes are the closed forms.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import engine, qft_overlay as qo  # noqa: E402


def ladder(n, k):
    """The banded Draper ladder (`ReferenceQFT(n, k_max=k)`, no swap) from H and CP matrices; data qubit i = bit i."""
    x = np.arange(1 << n)
    H = np.array([[1, 1], [1, -1]]) / math.sqrt(2)
    U = np.eye(1 << n, dtype=complex)
    for j in range(n):
        U = np.kron(np.kron(np.eye(1 << (n - j - 1)), H), np.eye(1 << j)) @ U
        for d in range(1, min(k, n - 1 - j) + 1):
            U = np.exp(1j * math.pi / 2 ** d * (((x >> j) & 1) & ((x >> (j + d)) & 1)))[:, None] * U
    return U


def gradient(b):
    return np.exp(-2j * np.pi * np.arange(1 << b) / (1 << b)) / math.sqrt(1 << b)


# --------------------------------------------------------------------------- root 1: the engine's own bloqs


def _assembled():
    engine.load()
    from functools import cached_property

    import attrs
    from qualtran import Bloq, BloqBuilder, QBit, QUInt, Register, Signature
    from qualtran.bloqs.basic_gates import Hadamard
    from l2q_full.catalysis.catalyst import CtrlAddIntoCatalyst

    @attrs.frozen
    class BandedMergedQFT(Bloq):
        n: int
        k: int
        b: int

        @cached_property
        def signature(self):
            return Signature([Register("q", QBit(), shape=(self.n,)), Register("catalyst", QUInt(self.b))])

        def build_composite_bloq(self, bb: BloqBuilder, q, catalyst):
            q, cat = list(q), list(bb.split(catalyst))
            for j in range(self.n):
                q[j] = bb.add(Hadamard(), q=q[j])
                m = min(self.k, self.n - 1 - j)
                if m:
                    x = bb.join(np.array(q[j + 1:j + 1 + m]), dtype=QUInt(m))       # q_{j+1} is V's MSB
                    ph = bb.join(np.array(cat[:m + 1]), dtype=QUInt(m + 1))        # the top m + 1 gradient bits
                    q[j], x, ph = bb.add(CtrlAddIntoCatalyst(m + 1, m), ctrl=q[j], x=x, phase=ph)
                    q[j + 1:j + 1 + m], cat[:m + 1] = list(bb.split(x)), list(bb.split(ph))
            return {"q": np.array(q), "catalyst": bb.join(np.array(cat), dtype=QUInt(self.b))}

        def build_call_graph(self, ssa):
            cg = {Hadamard(): self.n}
            for j in range(self.n):
                m = min(self.k, self.n - 1 - j)
                if m:
                    cg[CtrlAddIntoCatalyst(m + 1, m)] = cg.get(CtrlAddIntoCatalyst(m + 1, m), 0) + 1
            return cg

    return BandedMergedQFT


@pytest.mark.parametrize("n,k", [(4, 2), (12, 3), (2049, 38), (2347, 38), (4398, 40), (2049, 2048)])
def test_the_engines_cost_split_of_the_construction_is_what_the_overlay_prices(n, k):
    split = engine.load().fullalg.cc.split(_assembled()(n, k, k + 1))
    assert (int(split.base), int(split.rot)) == (4 * qo.merged_plan(n, k)["ands"], 0) == (4 * k * (2 * n - k - 1), 0)


@pytest.mark.parametrize("n,k,b", [(2, 1, 2), (3, 1, 3), (3, 2, 3), (4, 1, 2), (4, 2, 3), (4, 3, 4), (4, 3, 5)])
def test_the_assembled_construction_is_the_banded_ladder_with_the_gradient_returned(n, k, b):
    U = np.asarray(_assembled()(n, k, b).tensor_contract())              # index: q_0 most significant, then catalyst
    N = 1 << n
    rev = np.array([int(format(i, f"0{n}b")[::-1], 2) for i in range(N)])
    g = gradient(b)
    lhs = U @ np.kron(np.eye(N), g[:, None])
    rhs = np.kron(ladder(n, k)[np.ix_(rev, rev)], g[:, None])
    assert np.max(np.abs(lhs - rhs)) < 1e-9


# --------------------------------------------------------------------------- root 2: the gate-level circuit


class Run:
    """Data bits 0..n-1, gradient bits n..n+b-1 (g = sum 2^i g_i), workspace from n+b, carried as index bits."""

    def __init__(self, psi, nb, rng):
        self.idx, self.amp, self.nb, self.rng = np.arange(psi.shape[-1], dtype=np.uint64), psi.copy(), nb, rng
        self.ands = self.unands = self.cnots = self.live = self.peak = 0

    def bit(self, q):
        return (self.idx >> np.uint64(q)) & np.uint64(1)

    def cnot(self, c, t):
        self.idx ^= self.bit(c) << np.uint64(t)
        self.cnots += 1

    def and_(self, a, b, t):
        assert not np.any(self.bit(t))
        self.idx |= (self.bit(a) & self.bit(b)) << np.uint64(t)
        self.ands += 1
        self.live += 1
        self.peak = max(self.peak, self.live)

    def unand(self, a, b, t):
        """X-measure t (outcome r drawn), keep the (-1)^(r t) sign, fix up with CZ(a, b) when r = 1; both branches equal."""
        held = self.bit(t)
        assert np.array_equal(held, self.bit(a) & self.bit(b))
        sign = lambda r: 1.0 - 2.0 * (r * held).astype(float)
        fix = 1.0 - 2.0 * (self.bit(a) & self.bit(b)).astype(float)
        branches = [self.amp * sign(0), self.amp * sign(1) * fix]
        assert np.max(np.abs(branches[0] - branches[1])) < 1e-12
        self.amp = branches[int(self.rng.integers(2))]
        self.idx &= ~(np.uint64(1) << np.uint64(t))
        self.unands += 1
        self.live -= 1

    def lower(self):
        assert self.live == 0 and np.all(self.idx < np.uint64(1 << self.nb))
        out = np.empty_like(self.amp)
        out[..., self.idx.astype(np.int64)] = self.amp
        return out


def add_ops(a, t, c):
    """Gidney's adder t += a (mod 2^L), a one bit shorter than t, carries c: L - 1 ANDs."""
    L, ops = len(t), []
    for i in range(L - 1):
        ops += [("cnot", c[i - 1], a[i]), ("cnot", c[i - 1], t[i])] if i else []
        ops += [("and", a[i], t[i], c[i])] + ([("cnot", c[i - 1], c[i])] if i else [])
    ops.append(("cnot", c[L - 2], t[L - 1]))
    for i in range(L - 2, -1, -1):
        ops += ([("cnot", c[i - 1], c[i])] if i else []) + [("unand", a[i], t[i], c[i])]
        ops += ([("cnot", c[i - 1], a[i])] if i else []) + [("cnot", a[i], t[i])]
    return ops


def row_ops(n, b, j, k):
    m = min(k, n - 1 - j)
    if not m:
        return []
    ws = n + b
    a, c, t = [ws + i for i in range(m)], [ws + m + i for i in range(m)], [n + b - m - 1 + i for i in range(m + 1)]
    ctl = [("and", j, j + d, a[m - d]) for d in range(1, m + 1)]
    return ctl + add_ops(a, t, c) + [("unand", *o[1:]) for o in reversed(ctl)]


def run_circuit(psi, n, b, k, inverse, rng, stats):
    def apply(psi, ops):
        if not ops:
            return psi
        r = Run(psi, n + b, rng)
        for o in ops:
            {"cnot": r.cnot, "and": r.and_, "unand": r.unand}[o[0]](*o[1:])
        stats["ands"] += r.ands
        stats["cnot"] += r.cnots
        stats["workspace"] = max(stats["workspace"], r.peak)
        return r.lower()

    def h(psi, q):
        v = psi.reshape(*psi.shape[:-1], psi.shape[-1] >> (q + 1), 2, 1 << q)
        return np.stack([v[..., 0, :] + v[..., 1, :], v[..., 0, :] - v[..., 1, :]], axis=-2).reshape(psi.shape) / math.sqrt(2)

    for j in (reversed(range(n)) if inverse else range(n)):
        ops = row_ops(n, b, j, k)
        if inverse:        # the gate-by-gate adjoint: AND and measured uncompute swap roles, the order reverses
            ops = [(("unand" if o[0] == "and" else "and" if o[0] == "unand" else o[0]), *o[1:]) for o in reversed(ops)]
            psi = h(apply(psi, ops), j)
        else:
            psi = apply(h(psi, j), ops)
    return psi


@pytest.mark.parametrize("n,k,b", [(2, 1, 2), (3, 2, 3), (4, 1, 2), (4, 2, 3), (5, 2, 5), (5, 4, 5), (6, 3, 4), (6, 5, 6), (6, 5, 8)])
@pytest.mark.parametrize("inverse", [False, True], ids=["qft", "inverse"])
def test_the_gate_level_circuit_is_the_banded_ladder_for_every_input(n, k, b, inverse):
    rng = np.random.default_rng(n * 100 + k * 10 + b)
    N, g = 1 << n, gradient(b)
    stats = {"ands": 0, "cnot": 0, "workspace": 0}
    out = run_circuit(np.kron(g[None, :], np.eye(N)), n, b, k, inverse, rng, stats)
    U = ladder(n, k)
    want = np.kron(g[None, :], (U.conj().T if inverse else U).T)          # column x: (U|x>) (x) |gamma_b>
    assert np.max(np.abs(out - want)) < 1e-10                             # global phase exactly 1, gradient restored
    ms = [min(k, n - 1 - j) for j in range(n)]
    assert stats["ands"] == 2 * sum(ms) == 2 * (k * (2 * n - k - 1) // 2) == qo.merged_plan(n, k)["ands"]
    assert stats["cnot"] == sum(6 * m - 4 for m in ms if m)
    assert stats["workspace"] == 2 * k == qo.merged_plan(n, k)["workspace"]


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6])
def test_the_unbanded_construction_is_the_dft(n):
    """Catalytic exact: k = n - 1 keeps every phase, and the ladder (so the merged circuit above) is the DFT with the
    Draper bit order (input q_0 most significant, output q_j holding bit j)."""
    N = 1 << n
    xs = np.array([int(format(i, f"0{n}b")[::-1], 2) for i in range(N)])
    dft = np.exp(2j * np.pi * np.outer(np.arange(N), xs) / N) / math.sqrt(N)
    assert np.max(np.abs(ladder(n, n - 1) - dft)) < 1e-10


def test_a_gradient_narrower_than_the_band_is_a_shallower_band_not_a_free_one():
    """On b = k bits the distance-k phase has no slice (a row of m = k needs the top k + 1 bits, which would reach into
    the data register). Leaving it out is exactly the (k - 1)-band, and that is NOT the k-band: a narrower register
    must be priced as the shallower band it is (with its larger error), never as the k-band."""
    n, k = 6, 4
    first_add = row_ops(n, k, 0, k)[k]                                 # the adder's first AND: (a_0, t_0, c_0)
    assert first_add[0] == "and" and first_add[2] < n                  # t_0 would be a data qubit
    rng, N, g = np.random.default_rng(7), 1 << n, gradient(k)
    stats = {"ands": 0, "cnot": 0, "workspace": 0}
    out = run_circuit(np.kron(g[None, :], np.eye(N)), n, k, k - 1, False, rng, stats)
    assert np.max(np.abs(out - np.kron(g[None, :], ladder(n, k - 1).T))) < 1e-10
    assert np.max(np.abs(out - np.kron(g[None, :], ladder(n, k).T))) > 1e-3
