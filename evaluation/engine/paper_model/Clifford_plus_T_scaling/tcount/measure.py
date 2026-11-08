"""Measure the real T-count of this construction's rotation angles, by synthesising them.

WHAT IS SAMPLED, AND WHY THAT POPULATION
-----------------------------------------
Every non-Clifford rotation of the frozen construction is one phase `P(pi * e)` with
`e = phi * w_i * w_j`, i.e. a turn `tau = e/2 mod 1`. The census in
`notes/angle-dictionary-census.2026-09-13.md` establishes the two populations:

  * dyadic, `tau = c / 2^s` -- the U2 leaves and every QFT twiddle. Exactly representable in
    Clifford+T for small `s`; these are the cheap angles, and they are a k-independent,
    fixed-size population.
  * generic, `tau = +- r * k(c) * 2^(i+j) / (2N)` -- the U1/U5 leaves of the Toom-Cook modular
    multiply and their whole recursion. `r` is an integer Toom weight product and
    `k(c) = (c-1) mod N` or `(1 - c^-1) mod N`, so **every non-dyadic angle is an integer
    multiple of 1/(2N)**: its identity is one residue `m` in `[0, 2N)`.

This module samples the GENERIC population -- residues `m/(2N)` -- because that is the population
that (a) dominates at every n the figures plot and (b) carries whatever uncertainty exists. The
dyadic share is a counting question, not a synthesis question, and is handled in `mix.py`.

`N` is taken as a random odd n-bit modulus rather than `2^n - 1`: the census note records that
Mersenne test moduli "collapse the residues artificially and understate D", so using one here
would bias the sample toward degenerate angles and therefore toward a lower T-count.

HOW MANY SAMPLES ARE ACTUALLY NEEDED
------------------------------------
The default is 100,000 because that was asked for, but the measurement below reports the standard
error so the reader can see what it buys. Measured spread at eps = 1e-17 was about +-2% of the
mean across angles, so the standard error of the mean falls as 2%/sqrt(M): about 0.06% at
M = 1,000 and 0.006% at M = 100,000. The mean is pinned long before 100,000; what a large sample
buys is the tail, not the centre.

    python -m tcount.measure --n 2048 --rotations 2.7e10 --samples 2000 --workers 8
    python -m tcount.measure --sweep 2048 4096 8192 16384 32768 50000 --samples 100000
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# pygridsynth and numpy both spawn threads; a pool of workers each spawning threads collapses to
# zero throughput on this box (memory: pin BLAS threads in pools).
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

from .budget import RotationBudget, ross_selinger_t  # noqa: E402

__all__ = ["AngleSample", "sample_generic_turns", "measure_mean_t", "MeasuredCell"]

_PKG_ROOT = Path(__file__).resolve().parent.parent      # Clifford_plus_T_scaling/
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))


@dataclass(frozen=True)
class MeasuredCell:
    k: int
    n: int
    rotations: float
    delta: float
    rotation_fraction: float
    eps: float
    samples: int
    mean_T: float
    sd_T: float
    se_T: float
    min_T: int
    max_T: int
    generic_model_T: float
    ratio_to_model: float
    seconds: float
    engine: str

    def line(self) -> str:
        return (f"k={self.k} n={self.n:>6}  eps={self.eps:.3e}  M={self.samples:,}  "
                f"mean T = {self.mean_T:.2f} +- {self.se_T:.2f} (sd {self.sd_T:.2f}, "
                f"[{self.min_T}, {self.max_T}])  model {self.generic_model_T:.2f}  "
                f"ratio {self.ratio_to_model:.4f}  [{self.seconds:.0f}s]")


def odd_modulus(n: int, rng: random.Random) -> int:
    """A random odd n-bit modulus. NOT `2^n - 1`: Mersenne moduli collapse the residue space."""
    if n < 2:
        raise ValueError(f"n must be >= 2; got {n}")
    N = rng.randrange(1 << (n - 1), 1 << n) | 1
    return N


def sample_generic_turns(n: int, count: int, rng: random.Random,
                         N: Optional[int] = None) -> List[Fraction]:
    """`count` turns drawn from the generic population `m / (2N)`, `m` uniform in `[1, 2N)`.

    Returned as exact `Fraction`s: `ctbp.synth.synthesize_rz` takes the request as an exact
    rational and hands pygridsynth an `mpf` at working precision, never a binary64. That matters
    here -- at n = 50,000 the denominator has 50,001 bits and a float target would be meaningless.
    """
    N = N if N is not None else odd_modulus(n, rng)
    two_n = 2 * N
    out: List[Fraction] = []
    for _ in range(int(count)):
        m = rng.randrange(1, two_n)
        out.append(Fraction(m, two_n))
    return out


def _t_of(word: Any) -> int:
    for attr in ("t_count", "t", "T"):
        v = getattr(word, attr, None)
        if isinstance(v, int):
            return v
    gates = getattr(word, "gates", None)
    if gates is not None:
        return sum(1 for g in gates if str(g).strip().upper().rstrip("*") in ("T", "TDG", "TD"))
    raise TypeError(f"cannot read a T-count off {type(word).__name__}")


def _synth_chunk(args):
    """Worker: synthesise a chunk of turns, return their T-counts."""
    turns, eps_num, eps_den = args
    from ctbp.synth import SynthConfig, synthesize_rz
    cfg = SynthConfig()
    eps = Fraction(eps_num, eps_den)
    out = []
    for num, den in turns:
        out.append(_t_of(synthesize_rz(Fraction(num, den), eps, cfg)))
    return out


def measure_mean_t(turns: Sequence[Fraction], eps: Fraction, *, workers: int = 1,
                   progress: bool = False) -> Dict[str, Any]:
    """Synthesise every turn at `eps` and return the T-count statistics."""
    from ctbp.synth import engine_fingerprint

    t0 = time.time()
    pairs = [(t.numerator, t.denominator) for t in turns]
    if workers > 1 and len(pairs) > workers:
        from concurrent.futures import ProcessPoolExecutor
        chunk = max(1, len(pairs) // (workers * 4))
        chunks = [(pairs[i:i + chunk], eps.numerator, eps.denominator)
                  for i in range(0, len(pairs), chunk)]
        ts: List[int] = []
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, part in enumerate(ex.map(_synth_chunk, chunks)):
                ts.extend(part)
                if progress:
                    print(f"    {len(ts):,}/{len(pairs):,} words "
                          f"({time.time()-t0:.0f}s)", flush=True)
    else:
        ts = _synth_chunk((pairs, eps.numerator, eps.denominator))

    m = len(ts)
    mean = sum(ts) / m
    var = sum((t - mean) ** 2 for t in ts) / (m - 1) if m > 1 else 0.0
    sd = math.sqrt(var)
    return {"samples": m, "mean_T": mean, "sd_T": sd, "se_T": sd / math.sqrt(m) if m else 0.0,
            "min_T": min(ts), "max_T": max(ts), "seconds": time.time() - t0,
            "engine": engine_fingerprint()}


def measure_cell(k: int, n: int, rotations: float, budget: RotationBudget, *,
                 samples: int = 100_000, workers: int = 1, seed: int = 0,
                 progress: bool = False) -> MeasuredCell:
    """One (k, n) cell: draw `samples` generic turns and synthesise them at `delta_rot / R`."""
    rng = random.Random((seed, k, n).__hash__() & 0xFFFFFFFF)
    eps_f = budget.epsilon(rotations)
    # exact rational eps, so the request identity is exact all the way into pygridsynth
    eps = Fraction(budget.delta_rot).limit_denominator(10 ** 15) / Fraction(int(rotations))
    turns = sample_generic_turns(n, samples, rng)
    stats = measure_mean_t(turns, eps, workers=workers, progress=progress)
    model = ross_selinger_t(eps_f)
    return MeasuredCell(k=k, n=n, rotations=float(rotations), delta=budget.delta,
                        rotation_fraction=budget.rotation_fraction, eps=eps_f,
                        generic_model_T=model, ratio_to_model=stats["mean_T"] / model,
                        **{kk: stats[kk] for kk in
                           ("samples", "mean_T", "sd_T", "se_T", "min_T", "max_T",
                            "seconds", "engine")})


# --------------------------------------------------------------------------------- CLI

def _rotations_for(k: int, n: int, delta_struct: float) -> float:
    """`QECGatesCost.rotation` for full order-finding at (k, n) -- closed form, instant."""
    pipe = _PKG_ROOT.parent
    for p in (str(pipe), str(pipe / "py"), str(pipe / "notebooks")):
        if p not in sys.path:
            sys.path.insert(0, p)
    cwd = os.getcwd()
    os.chdir(pipe)
    try:
        import nb_config as C
        from forshor_qualtran import emit
        from forshor_qualtran.recursion_versions import RuleSet
        import notebook_helpers as H
        bundles = {kk: emit.emit_bundle(kk) for kk in C.KS}
        rules = RuleSet(list(bundles.values()))
        return float(H.price_model_b("SHOR", rules, k, n,
                                     precision=getattr(C, "PRECISION_POLICY", None),
                                     delta=delta_struct, what="rotation"))
    finally:
        os.chdir(cwd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--n", type=int)
    ap.add_argument("--sweep", type=int, nargs="*",
                    default=None, help="n values (default: the log ladder 2048..50000)")
    ap.add_argument("--rotations", type=float, default=None,
                    help="R; default: computed from the cost model at this (k, n)")
    ap.add_argument("--samples", type=int, default=100_000)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--delta", type=float, default=0.01)
    ap.add_argument("--rotation-fraction", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path,
                    default=_PKG_ROOT / "artifacts" / "tcount" / "measured.jsonl")
    ap.add_argument("--progress", action="store_true")
    args = ap.parse_args(argv)

    budget = RotationBudget(delta=args.delta, rotation_fraction=args.rotation_fraction)
    ns = args.sweep if args.sweep else ([args.n] if args.n else [2048, 4096, 8192, 16384, 32768, 50000])
    args.out.parent.mkdir(parents=True, exist_ok=True)

    done = set()
    if args.out.is_file():
        for ln in args.out.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
                done.add((r["k"], r["n"], r["samples"], r["delta"], r["rotation_fraction"]))
            except Exception:
                pass

    print(budget.describe())
    for n in ns:
        key = (args.k, n, args.samples, args.delta, args.rotation_fraction)
        if key in done:
            print(f"k={args.k} n={n:>6}  already measured, skipping")
            continue
        R = args.rotations if args.rotations else _rotations_for(args.k, n, budget.delta_struct)
        cell = measure_cell(args.k, n, R, budget, samples=args.samples,
                            workers=args.workers, seed=args.seed, progress=args.progress)
        print(cell.line(), flush=True)
        with args.out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(cell)) + "\n")
    print(f"\n-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
