#!/usr/bin/env python3
"""Build the ForShor Challenge starter pack from this repository.

    python evaluation/starter/build_starter_pack.py [--seed PATH/epoch-input.json] [--out DIR] [--no-zip]

The pack is a participant's local copy of what prices a policy on the results boards. It starts from the Kahanamoku-Meyer and Yao (2024) tables
and nothing else: NO archive table, DP-library table, seeded planner policy or STO reference policy is in it.

  * pack/ (hand-written, kept as they are): README.md, SUBMISSION_FORMAT.md, SKILL.md, requirements.txt, starter.py,
    harness/{LEAN_CHECKS.md,policy_to_template.py}, templates/{Policy.lean,LICENSE.forshor}, examples/{README.md,examples.json,summary.tex,forshor_*.policy.json}
    (examples/summary.tex is the template of the optional TableGeneration/Submission/summary.tex: standard LaTeX, never compiled by anything in the pack);
  * engine/: `evaluation/engine` minus tests/, cache/, bytecode AND minus every archive table (`data/` keeps only `kmy_tables/`; the library's
    loaders find no archive, and `personal_tables/tables.json` is an empty list, so they return nothing). The repository's own engine is untouched.
    The vendored Qualtran tree is included: Qualtran imports bloqs lazily;
  * engine_wording.json: exact passages of the pack's engine copy that the published pack words differently (no repository or project name; "policy check" for "STO mirror"), applied after
    the scrubs; the build stops if one is missing, because the engine's text moved;
  * the starting policies, written from the pack's own engine (the planner's optimum over exactly the tables in the “Parallel sequences” appendix of Kahanamoku-Meyer and Yao (2024) at n = 2048, delta = 0.01, canonical):
    examples/starting_pp.policy.json, starting_ptp.policy.json, declared_ladder_ignored.policy.json, templates/pp_policy.json and ptp_policy.json (with their `*.lean-input.json`), and the
    submission files examples/starting_pp.Policy.lean and starting_ptp.Policy.lean (`starter.py template`) and templates/ptp.Policy.lean (the same PhaseTripleProduct file under the
    explanatory header in ptp_template_header.txt);
  * examples/forshor_dropin.policy.json and forshor_upstream_scan.policy.json are pack inputs (in pack/examples), not copied from the engine:
    ForShor `21e129a`'s own printed documents for its shipped template, `lake exe forshor_policy_submission --balanced` and the default width scan
    (printed 2026-11-08; both price to the same T as the documents the engine carries, and add the `negation` and `negate_charge` fields);
  * examples/board_snapshot.json: the rows of the boards at delta = 0.01 (name, T, Q, Q x T per board), so `starter.py price` can say where a policy would
    rank. By default the rows are the starting policies priced by the pack's own engine. `--seed` (the exporter's epoch input, forshor.epoch-input/v2) takes them from a
    results seed instead: pass only a seed that lists just the starting policies (the exporter with `--kmy-only`), or the snapshot would show the scores of the policies the challenge removed;
  * examples/expected/*.json: `starter.py examples --write-expected`, run on the PACK's own engine copy;
  * export_v2/, harness/intake_v2.py, scripts/: the boards' readout and QFT design rules, copied as vendor/vendor.json lists (byte for byte, or verbatim
    definitions), with export_v2/epoch.py (the pack's own) and export_v2/model.json (the boards' engine model and the pack engine's fingerprint), so
    `starter.py forshor-doc` prices a semiclassical export with or without a per-family QFT design; test_pack_local_pricing.py checks them;
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


# Not read by any code of the pack. certification/ names archive submissions; dp2048.policy.json is a printed document of a policy built from DP-library and archive tables
# (found by comparing operation sequences, not ids: test_pack_kmy_only.py does the same).
ARCHIVE_ENGINE_DIRS = {ENGINE / "tcost" / "certification", ENGINE / "fullalg" / "data" / "dp2048.policy.json"}


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


WORDING = Path(__file__).resolve().parent / "engine_wording.json"


def reword_engine(pack: Path) -> int:
    """Apply `engine_wording.json` to the pack's engine copy: the published pack names no repository or project and calls the Lean policy check "policy check", while
    `evaluation/engine` (epoch-locked, so not edited) still carries the older wording. Each passage must be present; a missing one stops the build, because it means the
    engine's text moved and the wording file must be updated, not that the wording can be skipped. Returns the number of passages applied."""
    passages = json.loads(WORDING.read_text(encoding="utf-8"))["rewordings"]
    missing = []
    for p in passages:
        f = pack / "engine" / p["file"]
        text = f.read_text(encoding="utf-8")
        if p["old"] not in text:
            missing.append(f"{p['file']}: {p['old'][:80]!r}")
            continue
        f.write_text(text.replace(p["old"], p["new"]), encoding="utf-8")
    if missing:
        raise SystemExit("evaluation/starter/engine_wording.json no longer matches the engine's text; update it (passages not found):\n  " + "\n  ".join(missing))
    return len(passages)


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


VENDOR = HERE / "vendor" / "vendor.json"


def top_level_segments(source: str) -> dict:
    """name -> the exact source lines of each top-level def, class or single-name assignment of a module."""
    import ast
    lines = source.splitlines(keepends=True)
    out = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            names = [node.targets[0].id]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        for name in names:
            out[name] = "".join(lines[start - 1:node.end_lineno])
    return out


def extract_module(spec: dict) -> str:
    """A module of top-level definitions copied verbatim, in source order, from `spec["source"]`."""
    source = (REPO / spec["source"]).read_text(encoding="utf-8")
    lines = set(source.splitlines())
    missing = [h for h in spec["header"] if h not in lines]
    if missing:
        raise SystemExit(f"{spec['source']}: header lines not in the source: {missing}")
    segments = top_level_segments(source)
    absent = [n for n in spec["names"] if n not in segments]
    if absent:
        raise SystemExit(f"{spec['source']}: no top-level definition named {absent}")
    ordered = sorted(spec["names"], key=lambda n: source.index(segments[n]))
    return ('"""' + spec["doc"] + '"""\n' + "\n".join(spec["header"]) + "\n\n\n"
            + "\n\n".join(segments[n].rstrip("\n") + "\n" for n in ordered))


def vendor_rules(pack: Path, python: str) -> dict:
    """Copy the published pricing rules into the pack (vendor/vendor.json), and record the boards' engine model beside them.

    Runs after the scrubs, which must not touch these files: the readout and QFT rules fingerprint their own bytes."""
    spec = json.loads(VENDOR.read_text(encoding="utf-8"))
    copied = {}
    for item in spec["files"] + spec["written"]:
        dest = pack / item["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / item["source"], dest)
        copied[item["dest"]] = item["source"]
    for item in spec["extracts"]:
        dest = pack / item["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(extract_module(item), encoding="utf-8")
        copied[item["dest"]] = item["source"] + " (extract)"
    for dest in copied:
        text = (pack / dest).read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern, _ in PATH_SCRUBS):
            raise SystemExit(f"{dest}: a vendored rule names a local path, and its bytes may not be changed")
    model = json.loads(subprocess.run([python, "-c", "import json; from export_v2 import epoch; print(json.dumps(epoch.model_of()))"],
                                      cwd=REPO / "evaluation", check=True, capture_output=True, text=True).stdout)
    fingerprint = subprocess.run([python, "-c", "import sys; sys.path.insert(0, 'engine'); import site_engine; from site_engine import full_frontier; "
                                  "print(full_frontier.fingerprint())"], cwd=pack, check=True, capture_output=True, text=True).stdout.strip()
    (pack / "export_v2" / "model.json").write_text(json.dumps({"model": model, "packEngineFingerprint": fingerprint}, indent=1) + "\n")
    check = subprocess.run([python, "-I", "-c", "import sys; sys.path[:0] = ['.', 'engine']; import site_engine; "
                            "from export_v2 import canon, epoch, readout_overlay, qft_overlay, readout_qft_overlay; "
                            "e = canon.epoch_of(epoch.model_of()); "
                            "assert e == readout_overlay.EPOCH == qft_overlay.EPOCH == readout_qft_overlay.EPOCH, e; print(e)"],
                           cwd=pack, capture_output=True, text=True)
    if check.returncode != 0:
        raise SystemExit("the pack's pricing rules do not accept the boards' engine model:\n" + check.stderr[-2000:])
    for p in pack.rglob("__pycache__"):
        shutil.rmtree(p)
    return {"epoch": check.stdout.strip(), "files": copied}


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


GEN_STARTING = r"""
import json, sys
sys.path.insert(0, "engine")
import site_engine  # noqa: F401
from site_engine import library as lib
from site_engine import frontier as fr
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
    Q = canonical(P, "Starting policy " + mode)
    out[key] = {"policy": Q.to_json(), "scan_ladder": [[w, k] for w, k in Q.with_widths("scan").ladder(2048)], "ladder": fr.ladder_str(fr._ladder(Q.with_widths("exact"), 2048))}
print(json.dumps(out))
"""

HOW = {"PhaseProduct": "A PhaseProduct banded policy: bands widest first, thresholds strictly decreasing; a band serves every width w >= its threshold down to the next band's threshold; below the last threshold the leaf. Different bands may use different tables of the same k. point_order is the order the program consumes the points ('0', '1', '-1', '2', '1/2', 'inf', ...); operations are [\"addScaled\", dst, src, sign(+1/-1), shift], [\"negate\", i], [\"shiftL\", i, n], [\"shiftR\", i, n], [\"phaseProduct\", i]. Do not declare widths: the board derives the exact ladder. This starting policy is the planner's optimum over the table of Kahanamoku-Meyer and Yao (2024) (one band); replace the table and thresholds with yours, and add bands with other tables. This file prices as it stands (python starter.py price).",
       "PhaseTripleProduct": "A PhaseTripleProduct banded policy (a table evaluates at 3k - 2 points): bands widest first, thresholds strictly decreasing; a band serves every width w >= its threshold down to the next band's threshold; below the last threshold the leaf. Different bands may use different tables of the same k. point_order is the order the program consumes the points ('0', '1', '-1', '2', '1/2', 'inf', ...); operations are [\"addScaled\", dst, src, sign(+1/-1), shift], [\"negate\", i], [\"shiftL\", i, n], [\"shiftR\", i, n], [\"phaseProduct\", i]. Do not declare widths: the board derives the exact ladder. This starting policy is the planner's optimum over the two tables of Kahanamoku-Meyer and Yao (2024) (one band); replace the tables and thresholds with yours, and add bands with other tables. This file prices as it stands (python starter.py price)."}


def write_starting_inputs(pack: Path, python: str) -> dict:
    """The starting policies (the planner's optimum over the Kahanamoku-Meyer and Yao tables), from the pack's own engine, and the files that start from them. Returns the raw generator output."""
    r = subprocess.run([python, "-c", GEN_STARTING], cwd=pack, check=True, capture_output=True, text=True)
    gen = json.loads(r.stdout.strip().splitlines()[-1])
    dump = lambda path, doc: path.write_text(json.dumps(doc, indent=1) + "\n")
    for key, stem in (("pp", "starting_pp"), ("ptp", "starting_ptp")):
        pol = gen[key]["policy"]
        dump(pack / "examples" / f"{stem}.policy.json", pol)
        dump(pack / "templates" / f"{key}_policy.json", {"_how": HOW[pol["mode"]], **{**pol, "name": f"my {key.upper()} policy"}})
    ladder = {"declared_ladder": gen["pp"]["scan_ladder"]}
    dump(pack / "examples" / "declared_ladder_ignored.policy.json",
         {"name": "Starting policy PhaseProduct, with a declared ladder", "mode": gen["pp"]["policy"]["mode"], **ladder, "bands": gen["pp"]["policy"]["bands"]})
    return gen


EDIT_ME = ("""/-- **Edit me.** The policy: bands widest first, each a threshold and a table. The default negation is one
qubit; add `negation := .scratch` to ask for the other construction (not priced on the boards). -/
""")


def write_ptp_template(pack: Path, gen: dict) -> None:
    """templates/ptp.Policy.lean: the PhaseTripleProduct submission file for the starting policy, under the template's own explanatory header (ptp_template_header.txt)."""
    body = (pack / "examples" / "starting_ptp.Policy.lean").read_text()
    pol = gen["ptp"]["policy"]
    header = (HERE / "ptp_template_header.txt").read_text().rstrip("\n").replace("@@LADDER@@", gen["ptp"]["ladder"]).replace("@@OPS@@", str(len(pol["bands"][0]["operations"])))
    i = body.index("/-!")
    j = body.index("-/", i) + 2
    text = body[:i] + header + body[j:]
    assert "def policy : PTP.PTPPolicy where" in text
    text = text.replace("def policy : PTP.PTPPolicy where", EDIT_ME + "def policy : PTP.PTPPolicy where", 1)
    (pack / "templates" / "ptp.Policy.lean").write_text(text)


def forshor_pin() -> str:
    for pkg in json.loads((REPO / "lake-manifest.json").read_text())["packages"]:
        if pkg.get("name") == "Fast_multiplication":
            return pkg["rev"]
    raise SystemExit("lake-manifest.json has no Fast_multiplication (ForShor) package")


def starting_snapshot(pack: Path, python: str) -> dict:
    """The boards' rows as the pack's own engine prices the starting policies (PhaseProduct on the PP and full boards, PhaseTripleProduct on its board)."""
    rows = {}
    for board, stem, extra in (("pp", "starting_pp", ["--no-full"]), ("full", "starting_pp", []), ("ptp", "starting_ptp", ["--no-full"])):
        out = pack / f".price-{board}.json"
        subprocess.run([python, str(pack / "starter.py"), "price", str(pack / "examples" / f"{stem}.policy.json"), "--json", str(out), *extra],
                       cwd=pack, check=True, capture_output=True, text=True)
        got = json.loads(out.read_text())["policies"][0]
        out.unlink()
        pr = got["full_run"] if board == "full" else got["board"]
        rows[board] = [{"name": got["name"], "rowId": hashlib.sha256(json.dumps(got["bands"]).encode()).hexdigest()[:12], "T": pr["T"], "qubits": pr["qubits"],
                        "score": pr["T"] * pr["qubits"], "stoMirror": "none"}]
    return {"built": date.today().isoformat(), "delta": "1e-2", "n": 2048, "source": "the starting policies priced by the pack's own engine",
            "forshorPin": forshor_pin(), "negation": "incrementer", "updatedAt": None,
            "note": "The starting rows of the boards at delta = 0.01 when the pack was built: policies built from the tables in the “Parallel sequences” appendix of Kahanamoku-Meyer and Yao (2024). The page shows the live boards; score = Q x T, lower is better.",
            "boards": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=Path, help="a results seed that lists only the starting policies (the exporter's epoch input, `--kmy-only`) to take the snapshot rows from; default: the starting policies priced by the pack's own engine")
    ap.add_argument("--out", type=Path, default=REPO / "dist-starter", help="output directory (default: dist-starter/ at the repo root)")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true", help="build from uncommitted source (a trial; the manifest's commit will not describe it)")
    ap.add_argument("--python", default=sys.executable, help="python with the pack's requirements, to pin the example outputs")
    a = ap.parse_args()

    vendored = sorted({x["source"] for k in ("files", "extracts") for x in json.loads(VENDOR.read_text())[k]})
    dirty = git("status", "--porcelain", "--", "evaluation/starter", "evaluation/engine", *vendored)
    if dirty and not a.allow_dirty:
        raise SystemExit("the pack's source is not committed, so its manifest could not name the commit it was built from:\n" + dirty + "\n(commit it, or pass --allow-dirty for a trial build)")
    pack = a.out / "forshor-challenge-starter"
    if pack.exists():
        shutil.rmtree(pack)
    shutil.copytree(SRC, pack)
    copy_engine(pack / "engine")
    print(f"local paths scrubbed in {scrub_paths(pack)} files; archive ids scrubbed from {scrub_archive_ids(pack)} engine source files")
    print(f"engine wording: {reword_engine(pack)} passages reworded")
    rules = vendor_rules(pack, a.python)
    print(f"pricing rules: {len(rules['files'])} files from the repository, engine model {rules['epoch']}")
    origins = {"pack": "evaluation/starter/pack (hand-written)", "engine": f"evaluation/engine at {git('rev-parse', '--short', 'HEAD')} (tests, caches excluded)",
               "rules": {"epoch": rules["epoch"], "files": rules["files"]}}

    run = lambda *c: subprocess.run([a.python, str(pack / "starter.py"), *c], cwd=pack, check=True, capture_output=True, text=True)
    gen = write_starting_inputs(pack, a.python)
    snap = snapshot(a.seed) if a.seed else starting_snapshot(pack, a.python)
    (pack / "examples" / "board_snapshot.json").write_text(json.dumps(snap, indent=1) + "\n")
    # derived examples and Lean inputs, written by the pack's own commands
    (pack / "examples" / "starting_pp.Policy.lean").write_text(run("template", "examples/starting_pp.policy.json").stdout)
    (pack / "examples" / "starting_ptp.Policy.lean").write_text(run("template", "examples/starting_ptp.policy.json").stdout)
    write_ptp_template(pack, gen)
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
