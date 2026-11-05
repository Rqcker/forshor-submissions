"""Seam certification of the tcost bloqs: symbolic, or exact at a tiny scale (ResearchSkills Q01 and a Q02 oracle).

    python -m tcost.certify --archive <operations.json ...> --out tcost/certification/results

Nothing here simulates a large circuit. Each check is symbolic, or exact at a scale where the full unitary
is cheap (at most 10 external qubits).

  T0 width form          The closed-form nextSignedWidth (`width_form`) == the literal Lean scan, for each
                         archived table and each w in [k, 256] and 2048.
  T1 tensor action       QFT(z) . PhaseProduct . IQFT(z) has a CLASSICAL action: with phi = 2c/2^w, it is the
                         permutation z <- z + c*x mod 2^w (triple: z <- z + c*x*y). The check tensor-contracts
                         the flattened circuit (each And, ZPowGate, adder, shift and extension qubit) and
                         compares it with that permutation matrix. It does this for each leaf (pair w <= 5,
                         triple w <= 3), for forced pair Karatsuba levels and for a two-level chain. (A Toom
                         level is correct at all widths. The width must shrink only for termination. Thus
                         `ChainPhaseProduct` forces levels at tiny widths, with the same code that the ranking
                         prices symbolically.) Pair levels with k >= 3 have child widths of 14-41 bits, also at
                         w = 5. The contraction of the 7x7x7 child grids of a triple level takes about 50 s.
                         Thus T2 checks those levels.
  T1d diagonal action    The same circuits at a NON-dyadic phi = 3/7. The check contracts them directly (no QFT)
                         and compares them with diag(exp(i*pi*phi*x_s*z_s)) on SIGNED operands. This check is
                         sensitive to the sign-bit weight and to each pruned or Clifford cell that the dyadic
                         QFT form cannot see.
  T1b primitives         The ShiftL / ShiftR / NegateW / Extend decompositions == their declared classical
                         action, exhaustively at W <= 6.
  T2 level phase action  One forced level of EACH table at limb width 1 (w = k; also 2 for k <= 5) AND at the
                         first limb width where each child angle is nonzero. (At limb m, the radix 2^m is
                         frequently one of the points of the table. Then only one child is live: this is true
                         for 76/85 archived tables.) The check is an exact phased classical simulation. The
                         children act as declared (seeded inputs and edges). With the T1 leaves, this is the
                         induction over the recursion (the shape of ForShor `evalL_lowerGateRec_correct`). Thus
                         it covers all policies.
  T3 cost seam           For each table, one level at a tiny width: the SYMBOLIC cost of Qualtran (w a symbol,
                         then substituted) == the attested concrete call graph == the decomposition, in all 8
                         fields. For each leaf: symbolic == attested == decomposition. Together, these give the
                         cost induction. Also one tiny end-to-end flattening (a forced Karatsuba level at w = 4).
  T4 ranking path        The symbolic score (closed form in n, evaluated at n) == the concrete call-graph cost,
                         for each table as a constant policy at n in {64, 2048}. The composed counts of the
                         planner == the symbolic score of the policy that the planner returns.
  T5 oracle              Lean-To-Qualtran `LeanRecursivePhaseProduct` on its standard tables that Lean emits
                         (k = 2..7, n in {64, 256, 2048}). The check reproduces the ladder, And, rotation,
                         measurement and Toffoli counts exactly. The Clifford gap equals the sign-extension and
                         shift CNOTs that this construction makes physical.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
import random
import sys
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List

from . import tables as T
from .tables import PAIR, TRIPLE, Policy, Table

MAX_QUBITS = 10
EXHAUSTIVE_LIMIT = 4096


def _log(msg: str) -> None:
    print(msg, flush=True)


def _signed(u: int, w: int) -> int:
    return u - (1 << w) if u >> (w - 1) else u


# ---------------------------------------------------------------------------------------------
# T1: exact tensor action of QFT . PP . IQFT
# ---------------------------------------------------------------------------------------------

def qft_conjugated(pp, w: int):
    """Build QFT on z, the phase product, then IQFT on z. This is a classical permutation if phi = 2c/2^w."""
    from qualtran import BloqBuilder, QInt, QUInt, Register
    from qualtran.bloqs.qft import QFTTextBook
    bb = BloqBuilder()
    regs = {nm: bb.add_register(Register(nm, QInt(w))) for nm in pp.names}

    def retype(s, dt_):
        return bb.join(bb.split(s), dtype=dt_)

    z = bb.add(QFTTextBook(w), q=retype(regs["z"], QUInt(w)))
    regs["z"] = retype(z, QInt(w))
    outs = bb.add(pp, **regs)
    regs = dict(zip(pp.names, outs if isinstance(outs, tuple) else (outs,)))
    z = bb.add(QFTTextBook(w).adjoint(), q=retype(regs["z"], QUInt(w)))
    regs["z"] = retype(z, QInt(w))
    return bb.finalize(**regs)


def tensor_seam(mode: str, chain, w: int, c: int) -> Dict[str, Any]:
    import numpy as np
    from .bloqs import ChainPhaseProduct
    pp = ChainPhaseProduct(mode, tuple(chain), w, Fraction(2 * c, 1 << w))
    n_ops = len(pp.names)
    if n_ops * w > MAX_QUBITS:
        raise ValueError(f"tensor seam limited to {MAX_QUBITS} external qubits")
    t0 = time.time()
    U = qft_conjugated(pp, w).tensor_contract()           # fully flattened: all gates, all ancillas
    N = 1 << w
    P = np.zeros_like(U)
    for idx in range(N ** n_ops):
        vals = [(idx >> (w * (n_ops - 1 - i))) & (N - 1) for i in range(n_ops)]
        prod = c
        for v in vals[:-1]:
            prod *= _signed(v, w)
        out = vals[:-1] + [(vals[-1] + prod) % N]
        j = 0
        for v in out:
            j = (j << w) | v
        P[j, idx] = 1
    err = float(np.max(np.abs(U - P)))
    return {"mode": mode, "chain_k": [t.k for t in chain], "w": w, "c": c, "phi": f"{2 * c}/{N}",
            "external_qubits": n_ops * w, "declared": "z <- z + c*" + "*".join(pp.names[:-1]) + " mod 2^w",
            "max_abs_error": err, "seconds": round(time.time() - t0, 2), "ok": err < 1e-9}


def primitive_seams() -> List[Dict[str, Any]]:
    """ShiftL / ShiftR / NegateW: the classical action of the decomposition == the declared action, for each valid
    input."""
    from .bloqs import Extend, NegateW, ShiftL, ShiftR
    rows = []
    for W in range(2, 7):
        lo, hi = -(1 << (W - 1)), 1 << (W - 1)
        for b, valid in ([(ShiftL(W, n), lambda x, n=n: lo <= x << n < hi) for n in range(1, W - 1)]
                         + [(ShiftR(W, n), lambda x, n=n: x % (1 << n) == 0) for n in range(1, W - 1)]
                         + [(NegateW(W), lambda x: True)]):
            cb = b.decompose_bloq().flatten()
            bad = [x for x in range(lo, hi) if valid(x) and
                   int(cb.call_classically(x=x)[0]) != int(b.on_classical_vals(x=x)["x"])]
            rows.append({"bloq": str(b), "W": W, "bad": bad[:3], "ok": not bad})
        for wi in range(1, W):                             # Extend / un-extend: same value, clean ancillas
            for signed in (False, True):
                ext, unext = Extend(wi, W, signed), Extend(wi, W, signed, uncompute=True)
                ce, cu = ext.decompose_bloq().flatten(), unext.decompose_bloq().flatten()
                vals = range(-(1 << (wi - 1)), 1 << (wi - 1)) if signed else range(1 << wi)
                bad = [v for v in vals if int(ce.call_classically(limb=v)[0]) != v
                       or int(cu.call_classically(slot=v)[0]) != v]
                rows.append({"bloq": f"Extend({wi},{W},signed={signed}) +/- uncompute", "W": W,
                             "bad": bad[:3], "ok": not bad})
    return rows


def diagonal_seam(mode: str, chain, w: int, phi: Fraction = Fraction(3, 7)) -> Dict[str, Any]:
    """At a non-dyadic phi, the unitary of the flattened circuit must equal
    diag(exp(i*pi*phi*prod(signed operands)))."""
    import cmath
    import math
    import numpy as np
    from .bloqs import ChainPhaseProduct, fold
    pp = ChainPhaseProduct(mode, tuple(chain), w, phi)
    n_ops = len(pp.names)
    if n_ops * w > MAX_QUBITS:
        raise ValueError(f"diagonal seam limited to {MAX_QUBITS} external qubits")
    t0 = time.time()
    U = pp.tensor_contract()
    N = 1 << w
    want = np.zeros(N ** n_ops, dtype=complex)
    for idx in range(N ** n_ops):
        prod = phi
        for i in range(n_ops):
            prod *= _signed((idx >> (w * (n_ops - 1 - i))) & (N - 1), w)
        want[idx] = cmath.exp(1j * math.pi * fold(prod))
    err = float(np.max(np.abs(U - np.diag(want))))
    return {"mode": mode, "chain_k": [t.k for t in chain], "w": w, "phi": str(phi), "external_qubits": n_ops * w,
            "declared": "diag(exp(i*pi*phi*prod of signed operands))", "max_abs_error": err,
            "seconds": round(time.time() - t0, 2), "ok": err < 1e-9}


# ---------------------------------------------------------------------------------------------
# T2: exact phase action of one level (phased classical simulation, children as declared)
# ---------------------------------------------------------------------------------------------

def live_limb_width(table: Table, limit: int = 64) -> int:
    """Return the smallest limb width m at which each child angle phi * c_l(2^m) is nonzero. If 2^m is one of the
    points of the table, the interpolation puts all weight on that point. Then a level check at m exercises only
    one child."""
    for m in range(1, limit):
        if all(c != 0 for c in table.phase_coeffs(m)):
            return m
    raise ValueError(f"{table.mode} k={table.k}: no limb width < {limit} gives every child a nonzero angle")


def _t2_widths(table: Table):
    """Return limb width 1 (and 2 for k <= 5) as before, and the first width where each child is live."""
    ws = [table.k, 2 * table.k] if table.k <= 5 else [table.k]
    live = table.k * live_limb_width(table)
    return ws + ([live] if live not in ws else [])


def level_phase_seam(table: Table, w: int, n: int, seed: int) -> Dict[str, Any]:
    from qualtran.simulation.classical_sim import do_phased_classical_simulation
    from .bloqs import ChainPhaseProduct
    phi = Fraction(3, 7)                                   # non-dyadic: no accidental Clifford cells
    b = ChainPhaseProduct(table.mode, (table,), w, phi)
    cb = b.decompose_bloq()
    lo, hi = -(1 << (w - 1)), 1 << (w - 1)
    if (hi - lo) ** len(b.names) <= EXHAUSTIVE_LIMIT:
        cases = [dict(zip(b.names, v)) for v in itertools.product(range(lo, hi), repeat=len(b.names))]
        domain = "exhaustive"
    else:
        r = random.Random(seed)
        cases = [{k: r.randrange(lo, hi) for k in b.names} for _ in range(n)]
        cases += [{k: v for k in b.names} for v in (lo, hi - 1, 0, -1)]
        domain = f"random(seed={seed})+edges"
    bad, first = 0, []
    for d in cases:
        try:
            out, ph = do_phased_classical_simulation(cb, d)
            ok = all(int(out[k]) == d[k] for k in d) and abs(ph - b.basis_state_phase(**d)) < 1e-9
        except Exception as e:  # noqa: BLE001
            ok, out = False, {"error": f"{type(e).__name__}: {e}"}
        if not ok:
            bad += 1
            if len(first) < 2:
                first.append({"input": d, "out": str(out)})
    live = sum(1 for c in table.phase_coeffs(w // table.k) if c != 0)
    return {"mode": table.mode, "k": table.k, "w": w, "limb_width": w // table.k, "live_children": live,
            "children": table.q, "domain": domain,
            "cases": len(cases), "bad": bad, "first_bad": first, "ok": bad == 0}


# ---------------------------------------------------------------------------------------------
# T3 / T4: costs
# ---------------------------------------------------------------------------------------------

def cost_seam_level(table: Table, w: int) -> Dict[str, Any]:
    """Check symbolic(w).subs(w) == the attested concrete call graph == the one-level decomposition, in all 8
    fields."""
    import sympy
    from .bloqs import ChainPhaseProduct, SymbolicPhaseProduct, width_values
    from .counts import gate_counts, symbolic_gate_counts
    conc = ChainPhaseProduct(table.mode, (table,), w)
    att = gate_counts(conc)
    dec = gate_counts(conc.decompose_bloq())
    sym = symbolic_gate_counts(SymbolicPhaseProduct(table.mode, (table,)))
    sv = {k: int(sympy.sympify(v).subs(width_values((table,), w))) for k, v in sym.items()}
    ok = att == dec == sv
    return {"mode": table.mode, "k": table.k, "w": w, "ok": ok,
            **({} if ok else {"attested": att, "decomposition": dec, "symbolic": sv})}


def cost_seam_leaf(mode: str, w: int) -> Dict[str, Any]:
    """Check a leaf: symbolic(w).subs(w) == attested == decomposition (generic and phi = 3/7)."""
    import sympy
    from .bloqs import PairLeaf, TripleLeaf, width_symbol
    from .counts import gate_counts, symbolic_gate_counts
    mk = (lambda w_, phi: PairLeaf(w_, w_, phi)) if mode == PAIR else (lambda w_, phi: TripleLeaf(w_, w_, w_, phi))
    sym = symbolic_gate_counts(mk(width_symbol(0), None))
    sv = {k: int(sympy.sympify(v).subs(width_symbol(0), w)) for k, v in sym.items()}
    rows = {}
    for label, phi in (("generic", None), ("phi=3/7", Fraction(3, 7))):
        b = mk(w, phi)
        att, dec = gate_counts(b), gate_counts(b.decompose_bloq())
        rows[label] = att == dec and (phi is not None or att == sv)
    return {"mode": mode, "leaf_w": w, **rows, "ok": all(rows.values())}


def cost_seam_flat(mode: str, chain, w: int) -> Dict[str, Any]:
    from .bloqs import ChainPhaseProduct
    from .counts import gate_counts
    out = {"mode": mode, "chain_k": [t.k for t in chain], "w": w}
    for label, phi in (("generic", None), ("phi=3/7", Fraction(3, 7))):
        b = ChainPhaseProduct(mode, tuple(chain), w, phi)
        att, dec = gate_counts(b), gate_counts(b.decompose_bloq().flatten())
        out[label] = {"ok": att == dec, **({} if att == dec else {"attested": att, "decomposition": dec})}
    out["ok"] = all(out[l]["ok"] for l in ("generic", "phi=3/7"))
    return out


# ---------------------------------------------------------------------------------------------
# T5: Lean-To-Qualtran oracle
# ---------------------------------------------------------------------------------------------

def l2q_fingerprint(l2q_root: Path) -> str:
    """Return a content hash of the Lean-To-Qualtran sources and bundles that the oracle reads."""
    h = hashlib.sha256()
    for p in sorted(list((l2q_root / "forshor_qualtran").rglob("*.py")) + list((l2q_root / "fixtures" / "lean").glob("k*.forshor.json"))):
        h.update(p.relative_to(l2q_root).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def oracle_rows(l2q_root: Path, widths=(64, 256, 2048)) -> List[Dict[str, Any]]:
    if not (l2q_root / "forshor_qualtran" / "__init__.py").is_file():
        raise FileNotFoundError(f"Lean-To-Qualtran not found at {l2q_root} (set --l2q or TCOST_L2Q)")
    sys.path.insert(0, str(l2q_root))
    import forshor_qualtran  # noqa: F401  (this pins the same vendored qualtran)
    loaded = Path(forshor_qualtran.__file__).resolve()
    if l2q_root.resolve() not in loaded.parents:
        raise ImportError(f"forshor_qualtran resolved to {loaded}, not under --l2q {l2q_root}")
    from forshor_qualtran.bloqs.recursive import LeanRecursivePhaseProduct
    from qualtran.resource_counting import QECGatesCost, get_cost_value
    from .score import score_policy
    rows = []
    for k in range(2, 8):
        doc = json.loads((l2q_root / "fixtures" / "lean" / f"k{k}.forshor.json").read_text())
        sch = doc["schedule"]
        pts = [("int", q["z"]) if q["kind"] == "int" else ("frac", q["m"]) for q in sch["points"]]
        tab = Table.from_json({"mode": PAIR, "k": k, "point_order": pts, "operations": sch["ops"]})
        pol = Policy.constant(tab, name=f"lean-standard-k{k}")
        for w in widths:
            theirs = LeanRecursivePhaseProduct.from_bundle(doc, w, policy="sym")
            g = get_cost_value(theirs, QECGatesCost())
            rot = g.rotation
            th = {"and": int(g.and_bloq), "rotation": int(sum(rot.values()) if isinstance(rot, dict) else rot),
                  "measurement": int(g.measurement), "clifford": int(g.clifford), "toffoli": int(g.toffoli)}
            ours = score_policy(pol, w)["gate_counts"]                 # the symbolic ranking path
            extra = _physical_cnots(pol, w)
            same = all(ours[f] == th[f] for f in ("and", "rotation", "measurement", "toffoli"))
            ladder_ok = theirs.ladder() == [x for x, _ in pol.ladder(w)]
            rows.append({"k": k, "w": w, "theirs": th, "ours": {f: ours[f] for f in th},
                         "physical_cnots_ours_only": extra, "ladder_equal": ladder_ok,
                         "and_rotation_measurement_toffoli_equal": same,
                         "clifford_gap_explained": ours["clifford"] - th["clifford"] == extra,
                         "ok": same and ladder_ok and ours["clifford"] - th["clifford"] == extra})
    return rows


def _physical_cnots(policy: Policy, w: int) -> int:
    """Return the sign-extension and shift CNOTs of the full recursion (a closed form from the ladder)."""
    total, mult = 0, 1
    for wi, k in policy.ladder(w):
        if k is None:
            break
        t = policy.choose(wi)
        W2 = t.next_width(wi)
        node = t.arity * 2 * max(0, W2 - t.limb_widths(wi)[-1])
        for op in t.ops:
            if op[0] in ("shiftL", "shiftR"):
                node += t.arity * op[2]
            elif op[0] == "addScaled" and op[4]:
                node += t.arity * 2 * op[4]
        total += mult * node
        mult *= t.q
    return total


# ---------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tcost.certify")
    ap.add_argument("--archive", nargs="+", required=True)
    ap.add_argument("--out", default="tcost/certification/results")
    ap.add_argument("--l2q", default=os.environ.get("TCOST_L2Q", str(Path(__file__).resolve().parent.parent)),
                    help="folder holding forshor_qualtran for the T5 oracle (or TCOST_L2Q); default: this folder")
    ap.add_argument("--samples", type=int, default=6)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from . import qualtran_provenance
    from .bloqs import PolicyPhaseProduct
    from .counts import gate_counts
    from .planner import optimal_policy
    from .score import score_policy

    t_start = time.time()
    archive = T.load_archive_tables(a.archive)
    distinct: Dict[tuple, tuple] = {}
    for pid, t in archive:
        distinct.setdefault((t.mode, t.k, t.point_order, t.ops), (pid, t))
    tabs = [t for _, t in distinct.values()]
    by = {(t.mode, t.k): t for t in tabs}
    sections: Dict[str, Any] = {}

    def run(name, rows):
        sections[name] = rows
        _log(f"{name}: {sum(r['ok'] for r in rows)}/{len(rows)} ok  ({time.time() - t_start:.0f}s elapsed)")

    # T0 widths: [k, 256], 2048, and each width (+-2) that a scored ladder visits. The ranked ladders at n=2048
    # (the constant policies of each table and the archive-optimal policies) touch 695, 440, 398, ...
    ladder_widths = set()
    for t in tabs:
        ladder_widths.update(w for w, _ in Policy.constant(t).ladder(2048))
    for mode in (PAIR, TRIPLE):
        ladder_widths.update(w for w, _ in optimal_policy(tabs, mode, 2048)[0].ladder(2048))
    probe = sorted({w + d for w in ladder_widths for d in (-2, -1, 0, 1, 2)} | {2048})
    run("T0_width_form", [
        {"policy_id": pid, "mode": t.mode, "k": t.k,
         "ok": all(T.next_width_form(t, w) == T._next_width(t.ops, t.k, w)
                   for w in list(range(t.k, 257)) + [w for w in probe if w >= t.k])}
        for pid, t in distinct.values()])

    t1 = [tensor_seam(PAIR, (), w, c) for w in range(1, 6) for c in (1, 3)]
    t1 += [tensor_seam(TRIPLE, (), w, 1) for w in range(1, 4)]
    for t in tabs:                                        # forced pair Karatsuba levels (triple levels: T2)
        if t.k == 2 and t.mode == PAIR:
            for w in (4, 5):
                t1.append(tensor_seam(t.mode, (t,), w, 3))
    t1.append(tensor_seam(PAIR, (by[(PAIR, 2)],) * 2, 4, 1))     # two forced levels
    run("T1_tensor_action", t1)

    t1d = [diagonal_seam(PAIR, (), w) for w in range(1, 6)] + [diagonal_seam(TRIPLE, (), w) for w in range(1, 4)]
    t1d += [diagonal_seam(PAIR, (t,), 4) for t in tabs if t.k == 2 and t.mode == PAIR]
    run("T1d_diagonal_action", t1d)

    run("T1b_primitives", primitive_seams())

    run("T2_level_phase", [level_phase_seam(t, w, a.samples, seed=t.k * 1000 + w)
                           for t in tabs for w in _t2_widths(t)])

    t3 = [cost_seam_level(t, t.k + 2) for t in tabs]
    t3 += [cost_seam_leaf(mode, w) for mode in (PAIR, TRIPLE) for w in (1, 2, 3, 4, 6)]
    t3.append(cost_seam_flat(PAIR, (by[(PAIR, 2)],), 4))          # one tiny end-to-end flattening
    run("T3_cost", t3)

    t4 = []
    for t in tabs:
        for n in (64, 2048):
            pol = Policy.constant(t)
            sym, conc = score_policy(pol, n)["gate_counts"], gate_counts(PolicyPhaseProduct(pol, n, None))
            t4.append({"mode": t.mode, "k": t.k, "n": n, "ok": sym == conc,
                       **({} if sym == conc else {"symbolic": sym, "concrete": conc})})
    ranked = {}
    for mode in (PAIR, TRIPLE):
        pol, pred = optimal_policy(tabs, mode, 2048)
        ranked[mode] = pol
        s = score_policy(pol, 2048)
        conc = gate_counts(PolicyPhaseProduct(pol, 2048, None))
        t4.append({"policy": "archive-optimal", "mode": mode, "n": 2048, "t_count": s["t_count"],
                   "ok": pred == s["gate_counts"] == conc})
    run("T4_ranking_path", t4)

    try:
        t5 = oracle_rows(Path(a.l2q))
    except Exception as e:  # noqa: BLE001
        t5 = [{"ok": False, "error": f"{type(e).__name__}: {e}"}]
    run("T5_oracle_l2q", t5)

    ok = all(r["ok"] for sec in sections.values() for r in sec)
    here = Path(__file__).resolve().parent.parent

    def _rel(path) -> str:
        """Keep machine paths out of the witness. Record a path under this directory relative to this directory."""
        try:
            return str(Path(path).resolve().relative_to(here))
        except ValueError:
            return "<outside " + here.name + ">"

    archive_hash = hashlib.sha256()
    for path in sorted(a.archive):
        archive_hash.update(Path(path).read_bytes())
    l2q = Path(a.l2q)
    config = {"max_external_qubits": MAX_QUBITS, "exhaustive_limit": EXHAUSTIVE_LIMIT, "samples": a.samples,
              "archive": sorted(Path(p).parent.name for p in a.archive),
              "archive_sha256": archive_hash.hexdigest()[:16],
              "l2q": _rel(l2q), "l2q_fingerprint": l2q_fingerprint(l2q) if l2q.is_dir() else None}
    witness = {
        "claim": "tcost ChainPhaseProduct/PolicyPhaseProduct (pair and triple, any table, any width-banded policy): "
                 "exact quantum action of QFT.PP.IQFT at <= 10 qubits; symbolic cost == attested == decomposition; "
                 "symbolic ranking path == concrete cost; Lean-To-Qualtran counts reproduced",
        "script": "tcost/certify.py", "config": config,
        "config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16],
        "seed": "T2 rows: random.Random(1000*k + w) when not exhaustive; all other checks are deterministic",
        "lane": "qualtran " + qualtran_provenance()["version"],
        "gates": [{"name": name, "pass": all(r["ok"] for r in sec), "failed_rows": sum(not r["ok"] for r in sec),
                   "rows": len(sec)} for name, sec in sections.items()],
        "verdict": "PASS" if ok else "FAIL", "date": dt.date.today().isoformat(),
        "environment": {"qualtran": {**qualtran_provenance(), "path": _rel(qualtran_provenance().get("path", ""))},
                        "python": sys.version.split()[0]},
        "ranked_reference_policies": {m: p.to_json() for m, p in ranked.items()},
        "sections": sections, "elapsed_s": round(time.time() - t_start, 1),
    }
    (out / "tcost_seam_witness.json").write_text(json.dumps(witness, indent=1, default=str))
    _log(f"verdict {witness['verdict']} in {witness['elapsed_s']}s -> {out / 'tcost_seam_witness.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
