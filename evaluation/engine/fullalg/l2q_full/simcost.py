"""How long a dense simulation of a circuit will take, before starting it.

WHY THIS EXISTS
---------------
Several checks in this pipeline are exhaustive dense simulations: they are the strongest evidence
we have, and they are worth keeping. What they must not do is start a run that cannot finish.

Each of the guards they shipped with measured the WRONG QUANTITY, and each therefore failed in
the same way -- not by refusing a big job, but by accepting one:

  * `clifford_t_synthesis.verify_unitary_equivalence` is gated by callers on the ORIGINAL circuit's
    qubit count. But it simulates the CONVERTED circuit, and the whole point of the conversion is
    that it appends a phase-gradient register. Notebook 06's `Beauregard_PhiAdd_2ctrl` is 6
    original qubits -- comfortably under that notebook's limit of 6 -- and 21 converted qubits with
    2204 gates. The guard saw 6 and said yes to 3x10^11 amplitude updates.
  * Notebook 11 chose whether to simulate N = 21 on `importlib.util.find_spec("qiskit_aer")`.
    `beauregard_order_distribution(simulate=True)` calls `qiskit.quantum_info.Statevector`, and
    never touches Aer -- so that flag gated a 26-qubit run on a package the code path does not use.

The quantity that actually governs all of them is the same one, and it is known in advance:

    amplitude updates  =  (number of simulated runs) x (gate count) x 2^qubits

`2^qubits` is the state vector Qiskit walks per gate. Nothing here is subtle; it was simply never
written down, so each call site guessed with whichever number was in reach.

CALIBRATION (the benchmark machine, 2026-09-22, Qiskit Statevector, BLAS threads pinned to 1)
----------------------------------------------------------------------------------
Random Clifford+T circuits, 400 gates, `Statevector.evolve`:

    qubits      12       14        16        18        20
    amp-ops/s  5.5e7    8.6e7     2.5e7     3.3e7     2.3e7

Small cases run faster (the whole vector is in cache); past ~16 qubits it settles at 2-3e7. The
constant below takes the settled figure, which is the regime where a guard is needed at all.

Dense `Operator` construction (the `n_new == n_orig` branch of the verifier) walks a 2^n x 2^n
matrix instead, i.e. `gates * 4^n` element updates, measured at 2.7-4.0e7/s for n = 6..10.

VALIDATED AGAINST REAL RUNS, not just the microbenchmark:

    block / run                       predicted    observed
    06 Beauregard_PhiAdd_1ctrl           145 s     completed inside the 1800 s cell limit
    06 Beauregard_PhiAdd_2ctrl          11800 s    hit the 1800 s limit (this is the one that hung)
    11 N=15 order-finding, simulate     ~2400 s    hit the 1800 s limit

These are order-of-magnitude figures and are meant to be: a guard needs to tell "two minutes" from
"three hours", and for that a factor of two does not matter. It is calibrated for THIS machine --
`AMPLITUDE_OPS_PER_S` is the one number to re-measure elsewhere (`scripts/` has no runner for it;
the docstring table above is the record).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: Statevector amplitude updates per second, settled regime (>= 16 qubits). See module docstring.
AMPLITUDE_OPS_PER_S = 2.5e7

#: Dense `Operator` element updates per second. Slightly higher throughput per element, but the
#: element count is `4^n` rather than `2^n`, so it runs out far sooner.
OPERATOR_OPS_PER_S = 3.0e7

#: What a check in a notebook may spend. Chosen against the runner's 1800 s per-CELL timeout: a
#: cell usually holds several blocks, so no single check should be able to consume the whole cell.
DEFAULT_BUDGET_S = 300.0


def statevector_cost(qubits: int, gates: int, runs: int = 1) -> Dict[str, Any]:
    """Predicted cost of evolving a `qubits`-wide state vector through `gates`, `runs` times.

    `runs` is the per-basis-state loop count where there is one (the verifier evolves the converted
    circuit once per computational basis state of the ORIGINAL register, so `runs = 2^n_orig`).
    """
    ops = float(runs) * float(gates) * float(1 << int(qubits))
    return {"method": "statevector", "qubits": int(qubits), "gates": int(gates),
            "runs": int(runs), "amplitude_ops": ops, "seconds": ops / AMPLITUDE_OPS_PER_S,
            "state_bytes": 16 * (1 << int(qubits))}


def operator_cost(qubits: int, gates: int) -> Dict[str, Any]:
    """Predicted cost of building a dense `Operator` (2^n x 2^n) for a `gates`-long circuit."""
    ops = float(gates) * float(1 << (2 * int(qubits)))
    return {"method": "dense_operator", "qubits": int(qubits), "gates": int(gates), "runs": 1,
            "amplitude_ops": ops, "seconds": ops / OPERATOR_OPS_PER_S,
            "state_bytes": 16 * (1 << (2 * int(qubits)))}


def human_seconds(s: float) -> str:
    """`1.4e4` -> `'3.9 h'`. A guard's message is only useful if the number is readable.

    Past a year it switches to years, and past a thousand of those to scientific notation. That
    is not decoration: notebook 07's B1 block is 47 qubits after conversion with 2^35 basis
    states, which this printed as `20213687454260160.0 d` -- a number that reads as a formatting
    bug and so invites the reader to distrust the whole line, when the real message is simply
    "never".
    """
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.1f} min"
    if s < 172800:
        return f"{s / 3600:.1f} h"
    if s < 365.25 * 86400:
        return f"{s / 86400:.1f} d"
    years = s / (365.25 * 86400)
    if years < 1000:
        return f"{years:.1f} years"
    return f"{years:.2e} years (never)"


def human_bytes(b: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or unit == "TB":
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


#: Peak RSS of `KmyPhaseProductInplaceGeneral(n, k=3).tensor_contract()`, measured on the benchmark machine
#: 2026-09-23, keyed by 2n (the bloq's qubit count):
#:
#:     2n            6       8      10       12
#:     result       0.1 MB  1.0 MB  16.8 MB  268.4 MB      the dense operator itself
#:     peak RSS   321 MB  328 MB  2616 MB  10292 MB        what the contraction actually costs
#:     seconds      2.8     1.9     4.7      12.8
#:
#: The gap between the two rows is the whole point. `narrate.validate_phaseproduct` gated this
#: contraction on `2 * n <= 12`, which bounds the OUTPUT -- 2^12 x 2^12 complex128 is only
#: 268 MB, and looks entirely reasonable. The contraction to produce it allocates 10.3 GB of
#: intermediates, because the cost is set by the contraction path through the bloq's tensor
#: network, not by the size of the answer. Notebook 02 runs exactly this case (k=3, n=6) and it
#: is what drove that notebook to 9.9 GB.
INPLACE_TENSOR_PEAK_MB = {6: 321, 8: 328, 10: 2616, 12: 10292}

#: What one PP-8 tensor contraction may use. 3 GB admits 2n <= 10 (2.6 GB measured) and refuses
#: 2n = 12, which is the case that matters.
TENSOR_BUDGET_MB = 3000.0


def inplace_tensor_peak_mb(two_n: int) -> float:
    """Measured peak RSS for that contraction at `two_n` qubits.

    Interpolation is deliberately crude -- beyond the measured table each extra pair of qubits
    multiplies by 4, the trend from 2n = 8 -> 10 -> 12. Three noisy points do not support a
    fitted law, and a guard does not need one: it needs to tell 300 MB from 10 GB.
    """
    two_n = int(two_n)
    if two_n in INPLACE_TENSOR_PEAK_MB:
        return float(INPLACE_TENSOR_PEAK_MB[two_n])
    known = sorted(INPLACE_TENSOR_PEAK_MB)
    if two_n < known[0]:
        return float(INPLACE_TENSOR_PEAK_MB[known[0]])
    hi = known[-1]
    return float(INPLACE_TENSOR_PEAK_MB[hi]) * 4.0 ** ((two_n - hi) / 2.0)


def affordable(cost: Dict[str, Any], budget_s: Optional[float] = DEFAULT_BUDGET_S) -> bool:
    """True when `cost` fits in `budget_s`. `budget_s=None` means "run it regardless"."""
    return budget_s is None or float(cost["seconds"]) <= float(budget_s)


def explain(cost: Dict[str, Any], budget_s: Optional[float] = DEFAULT_BUDGET_S) -> str:
    """One line a reader can act on: the prediction, the budget, and WHY it is that big."""
    verdict = "fits" if affordable(cost, budget_s) else "OVER BUDGET"
    runs = f"{cost['runs']} runs x " if cost.get("runs", 1) > 1 else ""
    return (f"{verdict}: {cost['method']} on {cost['qubits']} qubits, "
            f"{runs}{cost['gates']:,} gates = {cost['amplitude_ops']:.2e} amplitude updates "
            f"~ {human_seconds(cost['seconds'])} "
            f"(budget {human_seconds(budget_s) if budget_s is not None else 'none'}; "
            f"state {human_bytes(cost['state_bytes'])})")
