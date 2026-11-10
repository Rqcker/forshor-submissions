#!/usr/bin/env python3
"""Build the ForShor Challenge starter pack from this repository.

    python evaluation/starter/build_starter_pack.py [--seed PATH/epoch-input.json] [--out DIR] [--no-zip]

The pack is a participant's local copy of what prices a policy on the results boards. It starts from the Kahanamoku-Meyer and Yao (2024) tables
and nothing else: NO archive table, DP-library table, seeded planner policy or STO reference policy is in it.

  * pack/ (hand-written, kept as they are): README.md, SUBMISSION_FORMAT.md, SKILL.md, requirements.txt, starter.py,
    harness/{LEAN_CHECKS.md,policy_to_template.py}, templates/{Policy.lean,ptp.Policy.lean,LICENSE.forshor}, examples/{README.md,examples.json,forshor_*.policy.json};
  * engine/: `evaluation/engine` minus tests/, cache/, bytecode AND minus every archive table (`data/` keeps only `kmy_tables/`; the library's
    loaders find no archive, and `personal_tables/tables.json` is an empty list, so they return nothing). The repository's own engine is untouched.
    The vendored Qualtran tree is included: Qualtran imports bloqs lazily;
  * the KMY policies, written from the pack's own engine (the planner's optimum over KMY's tables at n = 2048, delta = 0.01, canonical): examples/kmy_pp.policy.json,
    kmy_ptp.policy.json, declared_ladder_ignored.policy.json, templates/pp_policy.json and ptp_policy.json (with their `*.lean-input.json`), and the
    submission files examples/kmy_pp.Policy.lean and kmy_ptp.Policy.lean (`starter.py template`);
  * examples/forshor_dropin.policy.json and forshor_upstream_scan.policy.json are pack inputs (in pack/examples), not copied from the engine:
    ForShor `21e129a`'s own printed documents for its shipped template, `lake exe forshor_policy_submission --balanced` and the default width scan
    (printed 2026-11-08; both price to the same T as the documents the engine carries, and add the `negation` and `negate_charge` fields);
  * examples/board_snapshot.json: the rows of the boards at delta = 0.01 (name, T, Q, Q x T per board), so `starter.py price` can say where a policy would
    rank. By default the rows are the KMY policies priced by the pack's own engine. `--seed` (the exporter's epoch input, forshor.epoch-input/v2) takes them from a
    results seed instead: pass only a KMY-only seed, or the snapshot would show the scores of the policies the challenge removed;
  * examples/expected/*.json: `starter.py examples --write-expected`, run on the PACK's own engine copy;
  * MANIFEST.json: every file with its sha256 and origin, and what the pack was built from.

Nothing is fetched. The Lean build trees are not part of the pack. To confirm what was built, run `python starter.py examples --check`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ENGINE = REPO / "evaluation" / "engine"
SRC = HERE / "pack"
EXCLUDE_DIRS = {"tests", "cache", "__pycache__", ".pytest_cache"}
EXCLUDE_FILES = {"requirements.txt", "README.md"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


# The archive's data: every table, seeded policy and pinned run the engine's library loaders read, except KMY's three tables. None of it goes into the pack.
ARCHIVE_DATA_DIRS = {"sto_archive", "sto_accepted", "sto_submitted", "forshor_submitted", "table_sto_mirror", "personal_tables"}
ARCHIVE_DATA_FILES = {"BestKnown.STO-main.lean", "reference_policies.json", "sto_replay.json", "table_sto_mirror_runs.json"}
PACK_DATA_README = """# Data in this pack

Only the three explicit tables of Kahanamoku-Meyer and Yao (2024), arXiv:2403.18006v4, Appendix "Parallel sequences" (PhaseProduct k = 3;
PhaseTripleProduct k = 3 and k = 4): `kmy_tables/operations.json`.

The challenge starts from these tables. The engine's library loaders look for other table collections (an archive, a DP library, seeded policies);
there are none here, and `personal_tables/tables.json` is deliberately an empty list so those loaders return nothing.
"""


# Not read by any code of the pack, and they name archive submissions (certification results of archive tables).
ARCHIVE_ENGINE_DIRS = {ENGINE / "tcost" / "certification"}


def copy_engine(dest: Path) -> None:
    def keep(dirpath: str, names):
        drop = [n for n in names if n in EXCLUDE_DIRS or n.endswith(".pyc") or (Path(dirpath) == ENGINE and n in EXCLUDE_FILES)]
        drop += [n for n in names if Path(dirpath, n) in ARCHIVE_ENGINE_DIRS]
        if Path(dirpath) == ENGINE / "data":
            drop += [n for n in names if n in ARCHIVE_DATA_DIRS or n in ARCHIVE_DATA_FILES]
        return drop
    shutil.copytree(ENGINE, dest, ignore=keep)
    (dest / "data" / "personal_tables").mkdir()
    (dest / "data" / "personal_tables" / "tables.json").write_text("[]\n")
    (dest / "data" / "README.md").write_text(PACK_DATA_README)


# Absolute paths of the machine that produced the engine's pinned run records, replaced in the PACK's copy only (the repository's `evaluation/engine/` is untouched:
# its records are inside the engine's cache fingerprint, and a commit there changes the epoch). Nothing in the pack reads these as paths.
PATH_SCRUBS = [(re.compile(r"/Users/[A-Za-z0-9._-]+/Desktop/github_packages"), "$TREES"), (re.compile(r"~/Desktop/github_packages"), "$TREES"),
               (re.compile(r"~/Desktop"), "$TREES"), (re.compile(r"/Users/[A-Za-z0-9._-]+/Desktop"), "$TREES"), (re.compile(r"/Users/[A-Za-z0-9._-]+"), "$HOME")]
TEXT_SUFFIXES = {".json", ".md", ".py", ".txt", ".lean", ".yaml", ".yml", ".toml", ".cfg", ".lock", ".csv", ".sh", ""}


def archive_ids() -> list:
    """Every identifier the repository's engine data gives the archive and the seeded planner policies: archive submission shas (and their 12-hex ids),
    accepted-copy directory names, seeded policy ids. Longest first, so a sha is replaced whole."""
    ids = set()
    for kind in ("sto_archive", "sto_accepted", "sto_submitted", "forshor_submitted"):
        for d in (ENGINE / "data" / kind).glob("*"):
            m = re.fullmatch(r"[PS]?([0-9a-f]{12,64})", d.name)            # hex ids only: never a directory like `seed_exact`
            if d.is_dir() and m:
                h = m.group(1)
                ids |= {d.name, h, h[:12], "P" + h[:12], "S" + h[:12]}
    return sorted(ids, key=len, reverse=True)


def scrub_archive_ids(pack: Path) -> int:
    """Replace archive and seed identifiers that survive in the pack's engine SOURCE and notes (docstrings and comments that cite a submission) with a neutral
    word. Only .py and .md files of the engine's code are touched, never its data; `starter.py examples --check` runs afterwards on the result."""
    pattern = re.compile("|".join(re.escape(i) for i in archive_ids()))
    changed = 0
    for f in (pack / "engine").rglob("*"):
        if not f.is_file() or f.suffix not in {".py", ".md"} or "data" in f.relative_to(pack / "engine").parts[:1]:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new = pattern.sub("an archive submission", text)
        if new != text:
            f.write_text(new, encoding="utf-8")
            changed += 1
    return changed


def scrub_paths(pack: Path) -> int:
    """Replace the author's local paths in the pack's text files with `$TREES` / `$HOME`. Returns the number of files changed."""
    changed = 0
    for f in pack.rglob("*"):
        if not f.is_file() or f.suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new = text
        for pattern, repl in PATH_SCRUBS:
            new = pattern.sub(repl, new)
        if new != text:
            f.write_text(new, encoding="utf-8")
            changed += 1
    return changed


def snapshot(seed_path: Path) -> dict:
    seed = json.loads(seed_path.read_text())
    if seed.get("schema") != "forshor.epoch-input/v2":
        raise SystemExit(f"{seed_path}: not an exporter epoch input")
    boards = {}
    for key, rows in seed["rows"].items():
        board, delta = key.split("/")
        if delta != "1e-2":
            continue
        boards[board] = sorted(({"name": r["name"], "rowId": r["rowId"], "T": r["T"], "qubits": r["qubits"], "score": r["T"] * r["qubits"],
                                 "stoMirror": r["trust"]["stoMirror"]} for r in rows), key=lambda r: (r["score"], r["name"]))
    model = seed["model"]
    return {"built": date.today().isoformat(), "delta": "1e-2", "n": 2048, "source": "forshor.epoch-input/v2 (the results seed)",
            "forshorPin": model["forshor"]["pin"], "negation": model.get("negation"), "updatedAt": seed["updatedAt"],
            "note": "Rows of the results boards at delta = 0.01 when the pack was built. The page shows the live boards; score = Q x T, lower is better.",
            "boards": boards}


GEN_KMY = r"""
import json, sys
sys.path.insert(0, "engine")
import site_engine  # noqa: F401
from site_engine import library as lib
from tcost import planner as tp, tables as tt


def canonical(P, name):
    bands = []
    for b in P.bands:
        if bands and bands[-1].table.digest == b.table.digest:
            bands[-1] = tt.Band(b.threshold, b.table)
        else:
            bands.append(b)
    return tt.Policy(mode=P.mode, bands=tuple(bands), name=name, widths=P.widths)


out = {}
for key, mode in (("pp", tt.PAIR), ("ptp", tt.TRIPLE)):
    P, _ = tp.optimal_policy(lib.milestone_tables("kmy", mode), mode, 2048, 0.01)
    Q = canonical(P, "KMY (2024) " + mode)
    out[key] = {"policy": Q.to_json(), "scan_ladder": [[w, k] for w, k in Q.with_widths("scan").ladder(2048)]}
print(json.dumps(out))
"""

HOW = {"PhaseProduct": "A PhaseProduct banded policy: bands widest first, thresholds strictly decreasing; a band serves every width w >= its threshold down to the next band's threshold; below the last threshold the leaf. One table per k. point_order is the order the program consumes the points ('0', '1', '-1', '2', '1/2', 'inf', ...); operations are [\"addScaled\", dst, src, sign(+1/-1), shift], [\"negate\", i], [\"shiftL\", i, n], [\"shiftR\", i, n], [\"phaseProduct\", i]. Do not declare widths: the board derives the exact ladder. This starting point is the planner's optimum over KMY's table (one band); replace the table and thresholds with yours, and add bands with other tables. This file prices as it stands (python starter.py price).",
       "PhaseTripleProduct": "A PhaseTripleProduct banded policy (a table evaluates at 3k - 2 points): bands widest first, thresholds strictly decreasing; a band serves every width w >= its threshold down to the next band's threshold; below the last threshold the leaf. One table per k. point_order is the order the program consumes the points ('0', '1', '-1', '2', '1/2', 'inf', ...); operations are [\"addScaled\", dst, src, sign(+1/-1), shift], [\"negate\", i], [\"shiftL\", i, n], [\"shiftR\", i, n], [\"phaseProduct\", i]. Do not declare widths: the board derives the exact ladder. This starting point is the planner's optimum over KMY's two tables (one band); replace the tables and thresholds with yours, and add bands with other tables. This file prices as it stands (python starter.py price)."}


def write_kmy_inputs(pack: Path, python: str) -> dict:
    """The KMY policies, from the pack's own engine, and the files that start from them. Returns the raw generator output."""
    r = subprocess.run([python, "-c", GEN_KMY], cwd=pack, check=True, capture_output=True, text=True)
    gen = json.loads(r.stdout.strip().splitlines()[-1])
    dump = lambda path, doc: path.write_text(json.dumps(doc, indent=1) + "\n")
    for key, stem in (("pp", "kmy_pp"), ("ptp", "kmy_ptp")):
        pol = gen[key]["policy"]
        dump(pack / "examples" / f"{stem}.policy.json", pol)
        dump(pack / "templates" / f"{key}_policy.json", {"_how": HOW[pol["mode"]], **{**pol, "name": f"my {key.upper()} policy"}})
    ladder = {"declared_ladder": gen["pp"]["scan_ladder"]}
    dump(pack / "examples" / "declared_ladder_ignored.policy.json",
         {"name": "KMY (2024) PhaseProduct, with a declared ladder", "mode": gen["pp"]["policy"]["mode"], **ladder, "bands": gen["pp"]["policy"]["bands"]})
    return gen


def forshor_pin() -> str:
    for pkg in json.loads((REPO / "lake-manifest.json").read_text())["packages"]:
        if pkg.get("name") == "Fast_multiplication":
            return pkg["rev"]
    raise SystemExit("lake-manifest.json has no Fast_multiplication (ForShor) package")


def kmy_snapshot(pack: Path, python: str) -> dict:
    """The boards' rows as the pack's own engine prices the KMY policies (PhaseProduct on the PP and full boards, PhaseTripleProduct on its board)."""
    rows = {}
    for board, stem, extra in (("pp", "kmy_pp", ["--no-full"]), ("full", "kmy_pp", []), ("ptp", "kmy_ptp", ["--no-full"])):
        out = pack / f".price-{board}.json"
        subprocess.run([python, str(pack / "starter.py"), "price", str(pack / "examples" / f"{stem}.policy.json"), "--json", str(out), *extra],
                       cwd=pack, check=True, capture_output=True, text=True)
        got = json.loads(out.read_text())["policies"][0]
        out.unlink()
        pr = got["full_run"] if board == "full" else got["board"]
        rows[board] = [{"name": got["name"], "rowId": hashlib.sha256(json.dumps(got["bands"]).encode()).hexdigest()[:12], "T": pr["T"], "qubits": pr["qubits"],
                        "score": pr["T"] * pr["qubits"], "stoMirror": "none"}]
    return {"built": date.today().isoformat(), "delta": "1e-2", "n": 2048, "source": "the KMY (2024) policies priced by the pack's own engine",
            "forshorPin": forshor_pin(), "negation": "incrementer", "updatedAt": None,
            "note": "The starting rows of the boards at delta = 0.01 when the pack was built: policies built from the tables of Kahanamoku-Meyer and Yao (2024). The page shows the live boards; score = Q x T, lower is better.",
            "boards": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=Path, help="a KMY-only results seed (the exporter's epoch input) to take the snapshot rows from; default: the KMY policies priced by the pack's own engine")
    ap.add_argument("--out", type=Path, default=REPO / "dist-starter", help="output directory (default: dist-starter/ at the repo root)")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true", help="build from uncommitted source (a trial; the manifest's commit will not describe it)")
    ap.add_argument("--python", default=sys.executable, help="python with the pack's requirements, to pin the example outputs")
    a = ap.parse_args()

    dirty = git("status", "--porcelain", "--", "evaluation/starter", "evaluation/engine")
    if dirty and not a.allow_dirty:
        raise SystemExit("the pack's source is not committed, so its manifest could not name the commit it was built from:\n" + dirty + "\n(commit it, or pass --allow-dirty for a trial build)")
    pack = a.out / "forshor-challenge-starter"
    if pack.exists():
        shutil.rmtree(pack)
    shutil.copytree(SRC, pack)
    copy_engine(pack / "engine")
    print(f"local paths scrubbed in {scrub_paths(pack)} files; archive ids scrubbed from {scrub_archive_ids(pack)} engine source files")
    origins = {"pack": "evaluation/starter/pack (hand-written)", "engine": f"evaluation/engine at {git('rev-parse', '--short', 'HEAD')} (tests, caches excluded)"}

    run = lambda *c: subprocess.run([a.python, str(pack / "starter.py"), *c], cwd=pack, check=True, capture_output=True, text=True)
    write_kmy_inputs(pack, a.python)
    snap = snapshot(a.seed) if a.seed else kmy_snapshot(pack, a.python)
    (pack / "examples" / "board_snapshot.json").write_text(json.dumps(snap, indent=1) + "\n")
    # derived examples and Lean inputs, written by the pack's own commands
    (pack / "examples" / "kmy_pp.Policy.lean").write_text(run("template", "examples/kmy_pp.policy.json").stdout)
    (pack / "examples" / "kmy_ptp.Policy.lean").write_text(run("template", "examples/kmy_ptp.policy.json").stdout)
    for name, policy in (("pp_policy", "templates/pp_policy.json"), ("ptp_policy", "templates/ptp_policy.json")):
        (pack / "templates" / f"{name}.lean-input.json").write_text(run("lean-input", policy).stdout)
    run("examples", "--write-expected")
    check = subprocess.run([a.python, str(pack / "starter.py"), "examples", "--check"], cwd=pack, capture_output=True, text=True)
    if check.returncode != 0:
        print(check.stdout[-2000:], check.stderr[-2000:])
        raise SystemExit("the pack's own examples do not match the outputs it just pinned")
    for p in pack.rglob("__pycache__"):
        shutil.rmtree(p)

    files = sorted(p for p in pack.rglob("*") if p.is_file() and p.name != "MANIFEST.json")
    manifest = {"schema": "forshor-starter.manifest/1", "built": snap["built"], "forshorPin": snap["forshorPin"],
                "epoch_input_updatedAt": snap["updatedAt"], "exporter_commit": git("rev-parse", "HEAD"),
                "engine_revision": git("log", "-1", "--format=%H", "--", "evaluation/engine"), "origins": origins,
                "archive": "none: the pack's engine/data holds only the KMY (2024) tables",
                "files": [{"path": str(p.relative_to(pack)), "sha256": sha256(p), "bytes": p.stat().st_size} for p in files]}
    (pack / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    shutil.copy(pack / "SKILL.md", a.out / "forshor-skill.md")
    print(f"pack: {pack} ({len(files) + 1} files)")
    if not a.no_zip:
        z = a.out / "forshor-challenge-starter.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(pack.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(a.out))
        print(f"zip: {z} ({z.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
