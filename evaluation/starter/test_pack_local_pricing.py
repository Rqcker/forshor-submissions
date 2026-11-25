#!/usr/bin/env python3
"""The unzipped pack prices a semiclassical readout, with or without a per-family QFT design, exactly as the boards do.

    python evaluation/starter/test_pack_local_pricing.py PACK_ZIP_OR_DIR [--python PY] [--cases DIR]

Run it from a checkout of this repository: the repository's own code is the reference. The pack runs as a participant runs
it: unzipped into a temporary directory, `python -I`, with an environment that names no repository path. Checks:

  1. every file `vendor/vendor.json` copies into the pack is byte-identical to its source (sha256), and every extracted
     definition is the source's own text; the rule fingerprints the pack computes equal the repository's;
  2. for each case (a ForShor semiclassical export and the Policy.lean it was built from), `starter.py forshor-doc --source`
     prints, for every board and delta, the same row T and Q as `request.build_measured_request`, and its `readout` and
     `readoutQft` blocks equal the published panel's, key for key; without `--source` the numbers are the same;
     a coherent export prices to the full-run row `pricing.price_views` gives the published request;
  3. every request hosted intake refuses is refused by the pack with the same message and no price: the `exponent`
     family, a multiplier topology, a profile, a rotation backend, a phase-product step, a design without the readout (in
     the export and in the source), a selection the source does not declare (both ways), and a standalone primitive.

The default cases are the repository's recorded exports (`evaluation/export_v2/pytests/data`): the readout alone and a
banded data QFT. `--cases DIR` adds every `<name>.measured.json` in DIR with its `<name>.Policy.lean`. Exit 1 on any failure.
"""
import argparse
import ast
import copy
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
VENDOR = json.loads((HERE / "vendor" / "vendor.json").read_text())
DATA = REPO / "evaluation" / "export_v2" / "pytests" / "data"
bad = []


def fail(msg):
    bad.append(msg)
    print("FAIL " + msg, flush=True)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def segments(text):
    """name -> source text of each top-level def, class or single-name assignment (independent of the builder's own reader)."""
    lines = text.splitlines(keepends=True)
    out = {}
    for node in ast.parse(text).body:
        name = getattr(node, "name", None)
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name is None:
            continue
        first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        out[name] = "".join(lines[first - 1:node.end_lineno]).rstrip("\n")
    return out


# ------------------------------------------------------------------------------------------ the reference worker
def reference_worker(job_path, out_path):
    """Runs in a separate process with the repository on sys.path: the published request's own numbers and refusals."""
    from types import SimpleNamespace
    sys.path[:0] = [str(REPO / "evaluation"), str(REPO / "evaluation" / "starter" / "pack" / "harness"), str(REPO)]
    import intake_v2
    import policy_options
    from export_v2 import engine, epoch, mapping, pricing, qft_overlay, readout_overlay, readout_qft_overlay, request
    from export_v2.canon import epoch_of
    job = json.loads(Path(job_path).read_text())
    eng = engine.load()
    model = epoch.model_of()
    mirror = {"harness": "check_banded_policy"}       # a test double: the pack never sees the policy check
    common = dict(model=model, store=SimpleNamespace(stomirror=lambda *a: (mirror, None)), head_sha="a" * 40, pull_number=1,
                  github_login="local", run_id="1", submitted_at="2026-11-24T00:00:00Z", evaluated_at="2026-11-24T00:00:00Z",
                  archive_commit="c" * 40, accepted_ids=["a" * 40], evaluation_key="d" * 64, base_generation="1", number=1)
    out = {"epoch": epoch_of(model), "fingerprints": {"readout": readout_overlay.rule_fingerprint(), "qft": qft_overlay.rule_fingerprint(),
                                                       "readoutQft": readout_qft_overlay.rule_fingerprint()},
           "priced": {}, "refused": {}, "coherent": {}}

    def measured(doc, source):
        policy = intake_v2.policy_from_document(eng, doc)
        return request.build_measured_request(policy.to_json(), compiled=doc, verifier_pin=readout_overlay.VERIFIER_PIN,
                                              lean_source=source.encode("utf-8"), **common)

    for name, case in job["priced"].items():
        req = measured(json.loads(Path(case["doc"]).read_text()), Path(case["source"]).read_text())
        rows = []
        for item in req["priced"]:
            row = {"board": item["board"], "delta": item["delta"], "T": item["row"]["T"], "qubits": item["row"]["qubits"]}
            run = item["panel"]["fullRun"]
            if run:
                row["readout"] = run["readout"]
                if "readoutQft" in run:
                    row["readoutQft"] = run["readoutQft"]
            rows.append(row)
        out["priced"][name] = {"rows": rows, "readoutEvidenceRule": req["submission"]["verification"]["readout"]["ruleSha256"]}
    for name, case in job["refused"].items():
        doc = json.loads(Path(case["doc"]).read_text())
        try:
            if case.get("route") == "coherent":
                policy_options.check_coherent_export(doc)
            elif case.get("route") == "primitive":
                raise ValueError(intake_v2.PRIMITIVE_EXPORT_REFUSAL)
            else:
                measured(doc, Path(case["source"]).read_text())
            out["refused"][name] = None
        except Exception as error:  # noqa: BLE001
            out["refused"][name] = str(error)
    for name, case in job["coherent"].items():
        doc = json.loads(Path(case["doc"]).read_text())
        policy = intake_v2.policy_from_document(eng, doc)
        views = pricing.price_views(policy, "Submission", "1e-2", ["pp", "full"])
        rows = {}
        for board in ("pp", "full"):
            panel = mapping.panel_of(views[board], epoch=epoch_of(model), row_id="a" * 40, board=board, delta="1e-2", verification={})
            row = mapping.row_of(panel, origin="submission", name="x", created_at="", pull_request_url=None, submission_id=None, detail="")
            rows[board] = {"T": row["T"], "qubits": row["qubits"]}
        out["coherent"][name] = rows
    Path(out_path).write_text(json.dumps(out))


# ------------------------------------------------------------------------------------------------ the checks
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pack", type=Path, help="forshor-challenge-starter.zip, or the unzipped forshor-challenge-starter/")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--cases", type=Path, help="a directory of <name>.measured.json exports with their <name>.Policy.lean")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp()).resolve()
    if a.pack.suffix == ".zip":
        zipfile.ZipFile(a.pack).extractall(tmp / "unzipped")
        pack = tmp / "unzipped" / "forshor-challenge-starter"
    else:
        pack = a.pack.resolve()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp), "PYTHONDONTWRITEBYTECODE": "1"}

    def pack_run(*args):
        return subprocess.run([a.python, "-I", str(pack / "starter.py"), *map(str, args)], cwd=tmp, env=env,
                              capture_output=True, text=True, timeout=1800)

    # 1. the vendored files are the repository's
    for item in VENDOR["files"] + VENDOR["written"]:
        if sha(pack / item["dest"]) != sha(REPO / item["source"]):
            fail(f"{item['dest']} is not byte-identical to {item['source']}")
    for item in VENDOR["extracts"]:
        text = (pack / item["dest"]).read_text()
        source_text = (REPO / item["source"]).read_text()
        got, want = segments(text), segments(source_text)
        if set(got) != set(item["names"]):
            fail(f"{item['dest']} defines {sorted(got)}, not exactly {sorted(item['names'])}")
        for name in item["names"]:
            if got.get(name) != want.get(name):
                fail(f"{item['dest']}: {name} is not the text of {item['source']}'s {name}")
        lines = set(source_text.splitlines())
        for line in text.splitlines():
            if line.startswith(("import ", "from ")) and line not in lines:
                fail(f"{item['dest']}: import line {line!r} is not in {item['source']}")
    print(f"vendored: {len(VENDOR['files']) + len(VENDOR['written'])} files and {len(VENDOR['extracts'])} extracts checked")
    probe = subprocess.run([a.python, "-I", "-c", "import json, sys; sys.path[:0] = ['.', 'engine', 'harness']; import site_engine; "
                            "from export_v2 import canon, epoch, readout_overlay as r, qft_overlay as q, readout_qft_overlay as rq; import intake_v2; "
                            "print(json.dumps({'epoch': canon.epoch_of(epoch.model_of()), 'fingerprints': {'readout': r.rule_fingerprint(), "
                            "'qft': q.rule_fingerprint(), 'readoutQft': rq.rule_fingerprint()}, "
                            "'files': [m.__file__ for m in (r, q, rq, intake_v2, epoch)]}))"],
                           cwd=pack, env=env, capture_output=True, text=True)
    if probe.returncode != 0:
        fail("the pack's rules do not import standalone: " + probe.stderr[-1500:])
        probe_out = {}
    else:
        probe_out = json.loads(probe.stdout)
        outside = [f for f in probe_out["files"] if not Path(f).resolve().is_relative_to(pack)]
        if outside:
            fail(f"the pack imported rules from outside itself: {outside}")

    # the cases
    work = tmp / "cases"
    work.mkdir()

    def write(name, doc=None, text=None):
        path = work / name
        path.write_text(json.dumps(doc) if doc is not None else text)
        return path

    cases = {}
    readout = json.loads(gzip.decompress((DATA / "readout_submitted_931bbf1.json.gz").read_bytes()))
    design = json.loads(gzip.decompress((DATA / "design_body_submitted_3a2b878.json.gz").read_bytes()))
    cases["recorded-readout"] = (write("recorded-readout.json", readout["document"]), write("recorded-readout.Policy.lean", text=readout["source"]))
    cases["recorded-design"] = (write("recorded-design.json", design["document"]), write("recorded-design.Policy.lean", text=design["source"]))
    if a.cases:
        for doc in sorted(a.cases.glob("*.measured.json")):
            name = doc.name[:-len(".measured.json")]
            cases[name] = (doc.resolve(), (a.cases / f"{name}.Policy.lean").resolve())

    # 3. the refusals, built from the recorded design export
    doc_d, src_d = cases["recorded-design"]
    doc_r, src_r = cases["recorded-readout"]
    base = json.loads(doc_d.read_text())
    selection = base["submitted_full_run"]

    def with_body(**change):
        doc = copy.deepcopy(base)
        doc["submitted_full_run"]["design"].update(change)
        return doc

    def with_family(family, choice):
        doc = copy.deepcopy(base)
        doc["submitted_full_run"]["design"]["qft"][family] = choice
        return doc

    kept, dropping = [], False
    for line in src_d.read_text().splitlines():            # the file without its readout declaration (and its indented body)
        dropping = line.startswith("def submittedDesign ") or (dropping and line[:1] in (" ", "\t") and line.strip() != "")
        if not dropping:
            kept.append(line)
    no_readout = "\n".join(kept) + "\n"
    if no_readout == src_d.read_text() or "submittedDesign" in no_readout:
        raise SystemExit("could not remove the readout declaration from the recorded design source")
    dropin = json.loads((pack / "examples" / "forshor_dropin.policy.json").read_text())
    data_choice = selection["design"]["qft"]["data"]
    refusals = {
        "exponent family": (with_family("exponent", {"backend": "ross_selinger", "target": "exact"}), src_d, "measured"),
        "multiplier topology": (with_body(modmul="quotient_stream"), src_d, "measured"),
        "profile": ({**base, "submitted_full_run": {"schema": selection["schema"], "profile": "quotient-catalytic"}}, src_d, "measured"),
        "rotation backend": (with_family("data", {**data_choice, "rotation_backend": "catalytic"}), src_d, "measured"),
        "phase-product step": (with_body(phase_product={"step4": "selectiveKick"}), src_d, "measured"),
        "design without the readout (export)": ({**base, "submitted_readout": "coherent"}, src_d, "measured"),
        "design without the readout (coherent export)": ({**dropin, "submitted_full_run": selection}, None, "coherent"),
        "design without the readout (source)": (base, write("no-readout.Policy.lean", text=no_readout), "measured"),
        "design the source does not declare": (base, src_r, "measured"),
        "source declares a design the export lacks": (json.loads(doc_r.read_text()), src_d, "measured"),
        "standalone primitive": ({**base, "submitted_primitive": {"schema": "forshor.primitive/v1"}}, src_d, "primitive"),
    }
    job = {"priced": {n: {"doc": str(d), "source": str(s)} for n, (d, s) in cases.items()},
           "refused": {}, "coherent": {"forshor_dropin": {"doc": str(pack / "examples" / "forshor_dropin.policy.json")}}}
    files = {}
    for i, (name, (doc, source, route)) in enumerate(refusals.items()):
        path = write(f"refusal-{i}.json", doc)
        files[name] = (path, source, route)
        job["refused"][name] = {"doc": str(path), "source": str(source or src_d), "route": route}
    (tmp / "job.json").write_text(json.dumps(job))
    worker = subprocess.run([sys.executable, __file__, "--reference-worker", str(tmp / "job.json"), str(tmp / "reference.json")],
                            cwd=REPO, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if worker.returncode != 0:
        print(worker.stderr[-3000:])
        raise SystemExit("the repository's reference run failed")
    ref = json.loads((tmp / "reference.json").read_text())
    if probe_out:
        if probe_out["epoch"] != ref["epoch"]:
            fail(f"engine model: pack {probe_out['epoch']}, boards {ref['epoch']}")
        if probe_out["fingerprints"] != ref["fingerprints"]:
            fail(f"rule fingerprints: pack {probe_out['fingerprints']}, repository {ref['fingerprints']}")
        print(f"engine model {probe_out['epoch']}; fingerprints " + ", ".join(f"{k} {v[:12]}" for k, v in probe_out["fingerprints"].items()))

    # 2. the prices
    for name, (doc, source) in cases.items():
        want = ref["priced"][name]
        for with_source in (True, False):
            out = tmp / f"{name}-{with_source}.out.json"
            result = pack_run("forshor-doc", doc, *(["--source", source] if with_source else []), "--json", out)
            if result.returncode != 0 or not out.is_file():
                fail(f"{name}: the pack did not price it: {result.stderr[-800:]}")
                continue
            got = json.loads(out.read_text())
            pairs = {(r["board"], r["delta"]): r for r in got["rows"]}
            for w in want["rows"]:
                g = pairs.get((w["board"], w["delta"]))
                label = f"{name} {w['board']} {w['delta']}" + ("" if with_source else " (no --source)")
                if g is None:
                    fail(f"{label}: no row")
                    continue
                if (g["T"], g["qubits"]) != (w["T"], w["qubits"]):
                    fail(f"{label}: pack T {g['T']!r} Q {g['qubits']!r}, boards T {w['T']!r} Q {w['qubits']!r}")
                for block in ("readout", "readoutQft"):
                    if g.get(block) != w.get(block):
                        fail(f"{label}: the pack's {block} block differs from the published panel's")
            if len(pairs) != len(want["rows"]):
                fail(f"{name}: the pack priced {len(pairs)} rows, the request {len(want['rows'])}")
            rq = next((w["readoutQft"] for w in want["rows"] if "readoutQft" in w), None)
            expected_rules = {"readout": want["readoutEvidenceRule"], "readoutQft": rq["ruleSha256"] if rq else None,
                              "componentRules": rq["componentRules"] if rq else None}
            if got["rules"] != expected_rules:
                fail(f"{name}: rule fingerprints {got['rules']} != the panel's {expected_rules}")
            if with_source:
                full = next(r for r in got["rows"] if (r["board"], r["delta"]) == ("full", "1e-2"))
                print(f"{name}: full 1e-2 T {full['T']!r} Q {full['qubits']} ({full['priced_by']}); "
                      f"{len(pairs)} rows equal the published request" + ("" if rq is None else f"; readout alone T {full['readout']['T']!r}"))
    for name, want in ref["coherent"].items():
        out = tmp / f"{name}.coherent.json"
        result = pack_run("forshor-doc", pack / "examples" / f"{name}.policy.json", "--json", out)
        got = json.loads(out.read_text()) if result.returncode == 0 and out.is_file() else None
        if got is None or (got["full_run"]["T"], got["full_run"]["qubits"]) != (want["full"]["T"], want["full"]["qubits"]):
            fail(f"coherent {name}: pack {got and (got['full_run']['T'], got['full_run']['qubits'])}, boards {want['full']}")
        else:
            print(f"coherent {name}: full 1e-2 T {want['full']['T']!r} Q {want['full']['qubits']} equal the published row")

    # 3. the refusals
    for name, (path, source, route) in files.items():
        message = ref["refused"][name]
        if not message:
            fail(f"{name}: the repository priced it, so it is not a refusal case")
            continue
        needs_source = source is not None and source != src_d
        needs_source = needs_source or name == "source declares a design the export lacks"
        for src in ([source] if needs_source else list(dict.fromkeys([source, None]))):
            out = tmp / "refused.out.json"
            result = pack_run("forshor-doc", path, *(["--source", src] if src else []), "--json", out)
            label = f"{name}" + (" (--source)" if src else "")
            if result.returncode != 1 or result.stdout or out.exists() or "Traceback" in result.stderr:
                fail(f"{label}: not a clean refusal: rc {result.returncode}, stdout {result.stdout[:200]!r}, {result.stderr[-400:]!r}")
            elif message not in result.stderr:
                fail(f"{label}: the pack says {result.stderr.strip()[-400:]!r}, hosted intake {message!r}")
            else:
                print(f"refused, as hosted intake refuses it: {label}: {message[:110]}")
            if out.exists():
                out.unlink()
    print(f"{len(bad)} failures")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--reference-worker":
        reference_worker(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(main())
