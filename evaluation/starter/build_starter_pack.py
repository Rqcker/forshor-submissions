#!/usr/bin/env python3
"""Build the ForShor Challenge starter pack from this repository.

    python evaluation/starter/build_starter_pack.py --seed PATH/epoch-input.json [--out DIR] [--no-zip]

The pack is a participant's local copy of what prices a policy on the results boards:

  * pack/ (hand-written, kept as they are): README.md, SUBMISSION_FORMAT.md, SKILL.md, requirements.txt, starter.py,
    harness/{LEAN_CHECKS.md,policy_to_template.py}, templates/, examples/{README.md,examples.json,*.policy.json};
  * engine/: `evaluation/engine` whole, the engine the exporter priced the results seed with, minus tests/, cache/ and bytecode
    (the vendored Qualtran tree is included: Qualtran imports bloqs lazily);
  * examples/forshor_dropin.policy.json and forshor_upstream_scan.policy.json are pack inputs (in pack/examples), not copied from the engine:
    ForShor `21e129a`'s own printed documents for its shipped template, `lake exe forshor_policy_submission --balanced` and the default width scan
    (printed 2026-11-08; both price to the same T as the documents the engine carries, and add the `negation` and `negate_charge` fields);
  * examples/board_snapshot.json: the rows of the results seed at delta = 0.01 (name, T, Q, Q x T per board), so `starter.py price`
    can say where a policy would rank. `--seed` is the exporter's epoch input (forshor.epoch-input/v2) the seed was made from;
  * examples/expected/*.json: `starter.py examples --write-expected`, run on the PACK's own engine copy;
  * MANIFEST.json: every file with its sha256 and origin, and what the pack was built from.

Nothing is fetched. The Lean build trees are not part of the pack. To confirm what was built, run `python starter.py examples --check`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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


def copy_engine(dest: Path) -> None:
    def keep(dirpath: str, names):
        return [n for n in names if n in EXCLUDE_DIRS or n.endswith(".pyc") or (Path(dirpath) == ENGINE and n in EXCLUDE_FILES)]
    shutil.copytree(ENGINE, dest, ignore=keep)


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=Path, required=True, help="the exporter's epoch input the results seed was made from")
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
    origins = {"pack": "evaluation/starter/pack (hand-written)", "engine": f"evaluation/engine at {git('rev-parse', '--short', 'HEAD')} (tests, caches excluded)"}

    snap = snapshot(a.seed)
    (pack / "examples" / "board_snapshot.json").write_text(json.dumps(snap, indent=1) + "\n")

    run = lambda *c: subprocess.run([a.python, str(pack / "starter.py"), *c], cwd=pack, check=True, capture_output=True, text=True)
    # derived examples and Lean inputs, written by the pack's own commands
    (pack / "examples" / "seed_pp_S707dd51bf951.PolicyTemplate.lean").write_text(run("template", "examples/seed_pp_S707dd51bf951.policy.json").stdout)
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
