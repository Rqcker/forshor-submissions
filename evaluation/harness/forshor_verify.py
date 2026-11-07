#!/usr/bin/env python3
"""The ForShor Lean harness of the site (PLAN_v2 Stage 3.2): a banded policy goes through ForShor's Lean checks, then Python.

    python harness/forshor_verify.py verify <template-or-policy> [--forshor DIR] [--out DIR] [--install]
    python harness/forshor_verify.py verify site-policy.json --from-policy      # write the template from a site policy first

A submission is ForShor's `ShorPolicySubmission`: a list of `(threshold, table)` bands, widest first, each table with its
four conditions (C1 to C4) proved by the Lean kernel, plus `sorted` (the thresholds strictly decrease). The one file the
submitter owns is `FastMultiplication/ShorVerification/Submission/PolicyTemplate.lean`.

Stages (each fails closed and names itself):

  0 intake     Exactly one file, the template (a path, or a directory that holds it). Bounded size, UTF-8, no links.
               The site's own import rule: only the three modules the shipped template imports.
  1 scan       The site's own scan, before any Lean runs: no `#eval` (arbitrary IO at build time: ForShor's gate does not ban it,
               and a template that used it wrote a file during the build), no `run_cmd`, `axiom`, `native_decide`, `sorry`,
               `set_option maxHeartbeats`, and the other tokens in `SITE_BANNED`. Stricter than ForShor, on purpose.
  1b lexical   ForShor's gate, `scripts/check_template_lexical.sh`, from the trusted tree: no `implemented_by`/`extern`/`csimp`,
               `unsafe`, `run_cmd`, `initialize`, `elab`/`macro`/`syntax`/`notation`, `import Lean`, `set_option debug`.
               These can make the kernel-checked table and the printed table different objects, and add no axiom.
  2 stage      A copy-on-write clone of the trusted ForShor build tree, with only the template replaced.
  3 lean       `lake build PolicySubmission` (C1-C4 per band, `sorted`, `#assert_axioms`, the IR pinned against the compiled
               circuit at sampled widths) and `lake exe forshor_policy_submission` (prints `policy.json`). Time and peak memory
               are measured.
  4 python     `policy.json` is data, not trusted. The schema, the organiser-fixed constants (bits, trial count, eta, m) against
               the document the trusted tree's own built `forshor_policy_submission` emits (Stage 15: read from the build, never
               a fixed number; the tree's checked-in `policy.json` only when no binary is built), thresholds strictly decreasing, each band's table replayed (`tcost`) and its
               Toom-Cook identity checked (the same checks as an STO table).
  4b negation  (swap 002) With `--from-policy`: the `negation` key of the policy JSON ("oneQubit", the default, or "scratch") must equal
               the `negation` of the document Lean printed, else REJECTED. The price (stage 5) uses the printed method; the record
               carries `negation` {requested, printed, priced_as}. A printed document without the key means a build tree older than
               ForShor eb00c5a (no NegationMethod): REJECTED (`REQUIRE_PRINTED_NEGATION`).
  5 price      The policy as a fixed banded policy in the paper model (the headline: single-run T of the full order-finding
               run at n = 2048), and, as a conversion check, the submitted document priced by ForShor's native pricer: the
               document and the converted policy must give the same number.
  6 record     `<out>/F<id>/` and, with `--install`, `engine/data/forshor_submitted/<id>/record.json` (what the full-view
               board lists).

What this proves and what it does not. Lean proves each band's table and the policy's soundness (`policy_submission_correct`
is generic in the policy). The IR that the price reads is checked against the compiled circuit at sampled widths, not at every
width (ForShor says so). The price is Qualtran's count in Python, not Lean. Running this executes the submitter's Lean on this
machine after the lexical gate: use a disposable VM or container for code you do not trust.

Build tree (Stage 15, 2026-10-28): `.cache/forshor_latest_build`, ForShor 10fe620 (Lean v4.36.0-rc1, Mathlib 4dc43842), a
copy-on-write copy of a clean worktree. STALE since swap 002: the pin is ForShor eb00c5a, whose `forshor_policy_submission` prints
`negation`; a tree older than that is refused at stage 4b (`REQUIRE_PRINTED_NEGATION`), so the copy must be re-made at eb00c5a; `$FORSHOR_BUILD` or `--forshor` overrides it. `.cache/forshor_main_build`
(ForShor-main, Lean v4.28.0) is kept as a fallback. Every record names the ForShor commit and toolchain (`forshor`).
Resources: before Lean starts the harness waits while three or more `lean` processes run (the limit was 2 until the coordinator raised it on 2026-10-28); each stage runs in its own process
group (killed with its descendants on a timeout or at the memory cap, orphans swept after it returns,
`harness/pgroup_stage.py`); memory cap 8 GB on the stage's process tree (`top` mem, compressed included).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = ROOT / "engine"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(HERE))

import site_engine  # noqa: E402,F401  (binds the vendored Qualtran before anything else)
from forshor_qualtran.policy import PolicyError  # noqa: E402
from sto_verify import ENV_ALLOW, HarnessError, _clone, _env, python_verify  # noqa: E402
from tcost import tables as tt  # noqa: E402
import pgroup_stage as pg  # noqa: E402

#: Stage 15 (2026-10-28): the latest ForShor (10fe620, Lean v4.36.0-rc1). ForShor-main (v4.28.0) is the fallback.
DEFAULT_FORSHOR = ROOT / ".cache" / "forshor_latest_build"
FALLBACK_FORSHOR = ROOT / ".cache" / "forshor_main_build"
PROVENANCE_REL = ".sto_site_forshor.json"          # written by the site when it made the copy (commit, toolchain)
REFERENCE_CACHE = ROOT / ".cache" / "forshor_reference"
MEM_CAP_BYTES = 8 << 30                            # per stage, the stage's whole process tree (top mem, compressed included)
LEAN_SLOT_LIMIT = 3                                # wait while this many `lean` processes run (coordinator, 2026-10-28: was 2)
MEM_POLL_S = 2.0
DEFAULT_OUT = ROOT / ".cache" / "harness_runs"
INSTALL_DIR = ENGINE / "data" / "forshor_submitted"
TEMPLATE_REL = "FastMultiplication/ShorVerification/Submission/PolicyTemplate.lean"
LEXICAL_REL = "scripts/check_template_lexical.sh"
#: Tokens the site refuses in a template (outside comments), over and above ForShor's lexical gate. A template is three
#: definitions per band and one record; none of these is needed to write one.
SITE_BANNED = (
    (r"#eval", "compile-time execution of arbitrary IO (ForShor's lexical gate does not ban it)"),
    (r"#reduce|#synth|#print|#check|#guard|#exit|#where", "a command that runs or reports at build time"),
    (r"run_cmd|run_meta|run_elab|runCmd", "compile-time execution"),
    (r"native_decide|decide\s*\+\s*native|ofReduceBool|trustCompiler", "non-kernel computation"),
    (r"sorry|sorryAx|admit", "an unproved goal"),
    (r"axiom|constant|opaque", "a declaration that adds or hides trust"),
    (r"unsafe|implemented_by|extern|csimp|builtin_initialize|initialize|@\[export", "compiled code that can differ from the checked term"),
    (r"macro_rules|macro|elab_rules|elab|syntax|notation|infixl|infixr|infix|prefix|postfix|declare_syntax_cat", "metaprogramming"),
    (r"set_option", "an option change (heartbeats, debug, kernel switches)"),
    (r"import\s+Lean|import\s+Mathlib|open\s+Lean|open\s+IO|open\s+System", "a wider import or namespace than a template needs"),
    (r"IO\.|System\.|unsafeBaseIO|dbgTrace|panic!?", "an effect or a debug hook"),
)
ALLOWED_IMPORTS = ("FastMultiplication.ShorVerification.Framework.ToomCookTable",
                   "FastMultiplication.ShorVerification.Framework.Policy",
                   "FastMultiplication.ShorVerification.Submission.Decide")
MAX_BYTES = 1_000_000
LEXICAL_TIMEOUT = 60
BUILD_TIMEOUT = 3600
PRINT_TIMEOUT = 1800
HERO_N = 2048
SCHEMA = "forshor.policy-submission/v1"
RECORD_SCHEMA = "sto-site-forshor-record/1"


# ---------------------------------------------------------------------------------------------- stage 0
def collect_template(src: Path) -> bytes:
    src = Path(src)
    if not src.exists():
        raise HarnessError("intake", f"{src} does not exist")
    if src.is_symlink():
        raise HarnessError("intake", f"{src} is a symbolic link")
    if src.is_dir():
        files = [p for p in sorted(src.rglob("*")) if p.is_file() or p.is_symlink()]
        for p in files:
            if p.is_symlink():
                raise HarnessError("intake", f"{p.relative_to(src)} is a symbolic link")
        rels = [p.relative_to(src).as_posix() for p in files]
        stray = [r for r in rels if r != TEMPLATE_REL and r != "PolicyTemplate.lean"]
        if stray:
            raise HarnessError("intake", f"only PolicyTemplate.lean may be submitted (the diff rule); found {', '.join(stray[:5])}")
        if len(rels) != 1:
            raise HarnessError("intake", f"expected exactly one file, the template; found {len(rels)}")
        src = files[0]
    elif src.suffix != ".lean":
        raise HarnessError("intake", "the submission must be a .lean file (PolicyTemplate.lean)")
    data = src.read_bytes()
    if len(data) > MAX_BYTES:
        raise HarnessError("intake", f"the template is larger than {MAX_BYTES} bytes")
    if b"\x00" in data:
        raise HarnessError("intake", "the template contains a NUL byte")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HarnessError("intake", "the template is not valid UTF-8")
    check_imports(text)
    site_scan(text)
    return data


def strip_comments(text: str) -> str:
    """Remove `--` line comments and (nested) `/- ... -/` block comments, keeping line structure."""
    out, i, depth = [], 0, 0
    while i < len(text):
        two = text[i:i + 2]
        if two == "/-":
            depth += 1
            i += 2
        elif two == "-/" and depth:
            depth -= 1
            i += 2
        elif depth:
            out.append("\n" if text[i] == "\n" else " ")
            i += 1
        elif two == "--":
            while i < len(text) and text[i] != "\n":
                i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _site_regex(pat: str) -> "re.Pattern[str]":
    """The token must start at an identifier boundary, and (unless it ends in `.` or `!`) end at one."""
    tail = "" if pat.endswith(("\\.", "!?")) else r"(?![A-Za-z0-9_'])"
    return re.compile(r"(?<![A-Za-z0-9_'])(?:" + pat + r")" + tail)


_SITE_RE = [(_site_regex(pat), why) for pat, why in SITE_BANNED]


def site_scan(text: str) -> None:
    """Refuse the tokens of `SITE_BANNED` anywhere outside comments, strings included (conservative on purpose)."""
    for lineno, line in enumerate(strip_comments(text).splitlines(), start=1):
        for rx, why in _SITE_RE:
            m = rx.search(line)
            if m:
                raise HarnessError("scan", f"line {lineno}: `{m.group(0).strip()}`: {why}")


def check_imports(text: str) -> None:
    """The shipped template imports three modules and nothing else; so does a submission (the site's rule: the
    lexical gate does not cover `import`, and the template needs no more)."""
    imports = re.findall(r"^import[ \t]+(\S+)", text, flags=re.M)
    if not imports:
        raise HarnessError("intake", "the template has no imports")
    extra = [m for m in imports if m not in ALLOWED_IMPORTS]
    if extra:
        raise HarnessError("intake", f"imports outside the allowed three: {', '.join(extra)}")


def content_id(data: bytes) -> str:
    return hashlib.sha256(f"{TEMPLATE_REL}\t{hashlib.sha256(data).hexdigest()}\n".encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- running
def _mem_bytes(tok: str) -> int:
    tok = tok.strip().rstrip("+-")
    mult = {"B": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}
    try:
        return int(float(tok[:-1]) * mult[tok[-1]]) if tok and tok[-1] in mult else int(tok)
    except ValueError:
        return 0


def tree_mem_bytes(pid: int) -> int:
    """Memory of `pid` and its descendants as `top` reports it (`-stats mem`: resident + compressed; RSS hides the latter)."""
    pids = {pid} | pg.descendants(pid)
    cmd = ["top", "-l", "1", "-stats", "pid,mem"] + [a for q in sorted(pids) for a in ("-pid", str(q))]
    try:
        lines = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.splitlines()
    except Exception:  # noqa: BLE001
        return 0
    total = 0
    for line in lines:
        f = line.split()
        if len(f) == 2 and f[0].isdigit() and int(f[0]) in pids:
            total += _mem_bytes(f[1])
    return total


def lean_count() -> int:
    return len(subprocess.run(["pgrep", "-x", "lean"], capture_output=True, text=True).stdout.split())


def wait_lean_slot(limit: int = LEAN_SLOT_LIMIT, poll: float = 15.0, max_wait: Optional[float] = None) -> float:
    """The machine rule (shared with other Lean agents): do not start while `limit` or more `lean` processes run.
    Returns the seconds waited."""
    t0 = time.monotonic()
    while lean_count() >= limit:
        if max_wait is not None and time.monotonic() - t0 > max_wait:
            break
        time.sleep(poll)
    return round(time.monotonic() - t0, 1)


def _run_rss(cmd: List[str], cwd: Path, timeout: int, log: Path, stdout_to: Optional[Path] = None,
             info: Optional[Dict[str, Any]] = None, mem_cap: Optional[int] = None) -> Tuple[int, float]:
    """Run one stage in its own process group; return (exit code, peak resident set in MB of the process tree that it
    waited for). The stage's tree is polled (`top` mem) and killed with its descendants at the timeout (exit 124,
    "TIMEOUT" in the log) or at the memory cap (exit 137, "MEMCAP" in the log); orphans left in the group after the stage
    returns are swept (`pgroup_stage`). `info`, when given, receives `peak_tree_mb`, `killed` and `seconds`."""
    cap = MEM_CAP_BYTES if mem_cap is None else mem_cap
    log.parent.mkdir(parents=True, exist_ok=True)
    out = open(stdout_to, "wb") if stdout_to else None
    err = open(log, "wb")
    why, peak = None, 0
    t0 = time.monotonic()
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=_env(), stdout=out if out else err, stderr=err, start_new_session=True)
        next_poll = 0.0
        while True:
            pid, status, ru = os.wait4(p.pid, os.WNOHANG)
            if pid:
                break
            now = time.monotonic()
            if now - t0 > timeout:
                why = "timeout"
            elif cap and now >= next_poll:
                m = tree_mem_bytes(p.pid)
                peak = max(peak, m)
                next_poll = time.monotonic() + MEM_POLL_S
                if m >= cap:
                    why = "memcap"
            if why:
                pg.kill_group(p.pid, pg.descendants(p.pid) | {p.pid},
                              why=f"stage {why} ({timeout} s / {cap >> 30} GB): {' '.join(map(str, cmd))[:200]}")
                _, status, ru = os.wait4(p.pid, 0)
                break
            time.sleep(0.2)
        p.returncode = os.waitstatus_to_exitcode(status)
        left = pg.pgid_members(p.pid) - {p.pid}
        if left:
            pg.kill_group(p.pid, left, why="sweep after the stage returned")
    finally:
        err.close()
        if out:
            out.close()
    if info is not None:
        info.update({"peak_tree_mb": round(peak / (1 << 20), 1), "killed": why, "seconds": round(time.monotonic() - t0, 1),
                     "mem_cap_gb": cap / (1 << 30) if cap else None})
    if why == "timeout":
        with open(log, "ab") as f:
            f.write(f"\nTIMEOUT after {timeout} s\n".encode())
        return 124, 0.0
    if why == "memcap":
        with open(log, "ab") as f:
            f.write(f"\nMEMCAP: the stage's process tree reached {peak / (1 << 30):.2f} GB (cap {cap / (1 << 30):g} GB)\n".encode())
        return 137, 0.0
    rss = ru.ru_maxrss / (1 << 20 if sys.platform == "darwin" else 1 << 10)
    return p.returncode, round(rss, 1)


# ---------------------------------------------------------------------------------------------- the trusted tree
def exe_args(forshor: Path) -> List[str]:
    """Arguments the tree's `forshor_policy_submission` runs with, from its `.sto_site_forshor.json` (`exe_args`). The
    drop-in tree (Stage 18) runs `--balanced --probe-scan` (user decision 2026-10-28); stock ForShor runs none."""
    f = Path(forshor) / PROVENANCE_REL
    try:
        return [str(a) for a in json.loads(f.read_text()).get("exe_args", [])] if f.is_file() else []
    except json.JSONDecodeError:
        return []


def forshor_provenance(forshor: Path) -> Dict[str, Any]:
    """The ForShor commit and toolchain of a build tree, for every record. The site writes `.sto_site_forshor.json` when
    it makes a copy (a copied worktree has no usable git metadata); a tree with its own `.git` directory is asked with a
    read-only `git rev-parse`."""
    forshor = Path(forshor)
    prov: Dict[str, Any] = {}
    f = forshor / PROVENANCE_REL
    if f.is_file():
        try:
            prov = json.loads(f.read_text())
        except json.JSONDecodeError:
            prov = {}
    commit = prov.get("forshor_commit")
    if commit is None and (forshor / ".git").is_dir():
        commit = _version(["git", "-C", str(forshor), "rev-parse", "HEAD"], forshor) or None
    tc = (forshor / "lean-toolchain").read_text().strip() if (forshor / "lean-toolchain").is_file() else None
    try:
        man = json.loads((forshor / "lake-manifest.json").read_text())
        mathlib = next((p["rev"] for p in man.get("packages", []) if p.get("name") == "mathlib"), None)
    except (OSError, json.JSONDecodeError):
        mathlib = None
    return {"tree": str(forshor), "commit": commit, "commit_short": prov.get("forshor_commit_short") or (commit[:7] if commit else None),
            "label": prov.get("label"), "lean_toolchain": tc, "mathlib": mathlib, "source": prov.get("source"),
            "default_tree": forshor.resolve() == DEFAULT_FORSHOR.resolve()}


REFERENCE_FIXED = {"benchmark": ("bits", "trial_count"), "precision": ("eta", "m")}


def reference_document(forshor: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The organiser-fixed document of the trusted tree: what ITS OWN built `forshor_policy_submission` prints (the
    shipped template), cached by the binary's sha256. Stage 15: this replaces comparing with a fixed value, so a document
    printed by any ForShor revision is compared with that revision's own constants (10fe620: trial count 386; ForShor-main:
    2,216,900,437,333,460). Without a built binary, the tree's checked-in `policy.json`."""
    forshor = Path(forshor)
    exe = forshor / ".lake" / "build" / "bin" / "forshor_policy_submission"
    checked_in = forshor / "policy.json"
    info: Dict[str, Any] = {}
    if exe.is_file():
        sha = hashlib.sha256(exe.read_bytes()).hexdigest()
        cache = REFERENCE_CACHE / f"{sha[:16]}.json"
        if cache.is_file():
            text = cache.read_text()
        else:
            REFERENCE_CACHE.mkdir(parents=True, exist_ok=True)
            tmp = REFERENCE_CACHE / f"{sha[:16]}.tmp"
            # `lake exe` in the tree, as the submission itself runs: the drop-in (checkpoint A+B) extracts the IR at run
            # time, so the exe needs the tree's toolchain and its .olean search path; stock ForShor is unaffected
            code, _ = _run_rss(["lake", "exe", "forshor_policy_submission", *exe_args(forshor)], forshor.resolve(), PRINT_TIMEOUT,
                               REFERENCE_CACHE / f"{sha[:16]}.log", stdout_to=tmp)
            if code != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
                raise HarnessError("python", f"the trusted tree's own forshor_policy_submission failed (exit {code}); "
                                             "cannot read the organiser-fixed constants")
            text = tmp.read_text()
            tmp.replace(cache)
        info = {"source": "emitted by the trusted tree's built forshor_policy_submission", "binary_sha256": sha}
    elif checked_in.is_file():
        text = checked_in.read_text()
        info = {"source": "the trusted tree's checked-in policy.json (no built forshor_policy_submission)"}
    else:
        raise HarnessError("python", f"{forshor} has neither a built forshor_policy_submission nor a policy.json")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise HarnessError("python", f"the trusted tree's reference document is not JSON: {e}")
    info["sha256"] = hashlib.sha256(text.encode()).hexdigest()
    info["fixed"] = {key: {f: (doc.get(key) or {}).get(f) for f in fs} for key, fs in REFERENCE_FIXED.items()}
    if checked_in.is_file() and info["source"].startswith("emitted"):
        try:
            ci = json.loads(checked_in.read_text())
            info["checked_in_agrees"] = all((ci.get(k) or {}).get(f) == (doc.get(k) or {}).get(f)
                                            for k, fs in REFERENCE_FIXED.items() for f in fs)
        except json.JSONDecodeError:
            info["checked_in_agrees"] = False
    return doc, info


# ---------------------------------------------------------------------------------------------- stages 1 and 2
def lexical_gate(forshor: Path, work: Path) -> None:
    script = Path(forshor) / LEXICAL_REL
    if not script.is_file():
        raise HarnessError("lexical", f"{LEXICAL_REL} is missing from the trusted tree")
    try:
        r = pg.run_in_group(["bash", str(script), str(work / TEMPLATE_REL)], capture_output=True, text=True, env=_env(),
                            timeout=LEXICAL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise HarnessError("lexical", f"ForShor's lexical gate did not finish in {LEXICAL_TIMEOUT} s")
    if r.returncode != 0:
        raise HarnessError("lexical", (r.stdout + r.stderr).strip()[-600:] or f"exit {r.returncode}")


CHECK_REL = "FastMultiplication/ShorVerification/Submission/PolicyCheck.lean"
STOCK_HEARTBEATS = 4000000          # the one `set_option maxHeartbeats` in ForShor's PolicyCheck.lean
MAX_HEARTBEATS_LIMIT = 1000 * STOCK_HEARTBEATS


def override_heartbeats(work: Path, n: int) -> None:
    """Site override: raise the one heartbeat cap in the CLONED trusted PolicyCheck.lean (never the submitter's file).
    Recorded in the provenance of every run that uses it. PLAN_v2 3.2: the stock cap refuses any k >= 5 band and any
    three-band policy; the work is deterministic, so the cap is a resource limit, not a soundness condition."""
    if not isinstance(n, int) or isinstance(n, bool) or not STOCK_HEARTBEATS <= n <= MAX_HEARTBEATS_LIMIT:
        raise HarnessError("stage", f"max heartbeats must be an integer in [{STOCK_HEARTBEATS}, {MAX_HEARTBEATS_LIMIT}]")
    f = work / CHECK_REL
    text = f.read_text()
    line = f"set_option maxHeartbeats {STOCK_HEARTBEATS} in\nextract_policy_ir_doc"
    if text.count(line) != 1:
        raise HarnessError("stage", "PolicyCheck.lean does not have the expected single heartbeat cap; "
                                    "the override needs review for this ForShor revision")
    f.write_text(text.replace(line, f"set_option maxHeartbeats {n} in\nextract_policy_ir_doc"))


def stage(forshor: Path, data: bytes, work: Path, max_heartbeats: Optional[int] = None) -> Path:
    if not (Path(forshor) / TEMPLATE_REL).is_file():
        raise HarnessError("stage", f"{forshor} is not a ForShor checkout ({TEMPLATE_REL} missing)")
    if work.exists():
        shutil.rmtree(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    _clone(Path(forshor), work)
    (work / TEMPLATE_REL).write_bytes(data)
    if max_heartbeats is not None and max_heartbeats != STOCK_HEARTBEATS:
        override_heartbeats(work, max_heartbeats)
    return work


# ---------------------------------------------------------------------------------------------- stage 3
def lean_build(work: Path, out: Path, args: Optional[List[str]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """`lake build PolicySubmission`, then `lake exe forshor_policy_submission`. Return (policy.json, run stats)."""
    t0 = time.time()
    b_info: Dict[str, Any] = {}
    code, rss1 = _run_rss(["lake", "build", "PolicySubmission"], work, BUILD_TIMEOUT, out / "logs" / "lake-build.log", info=b_info)
    t1 = time.time()
    if code != 0:
        tail = (out / "logs" / "lake-build.log").read_text(errors="replace")[-1500:]
        raise HarnessError("lean", f"lake build PolicySubmission failed (exit {code}; build {round(t1 - t0, 1)} s, peak tree "
                                   f"{b_info.get('peak_tree_mb')} MB):\n{tail}")
    pj = out / "policy.json"
    p_info: Dict[str, Any] = {}
    code, rss2 = _run_rss(["lake", "exe", "forshor_policy_submission", *(args or [])], work, PRINT_TIMEOUT, out / "logs" / "forshor_policy_submission.log",
                          stdout_to=pj, info=p_info)
    t2 = time.time()
    if code != 0 or not pj.is_file() or pj.stat().st_size == 0:
        tail = (out / "logs" / "forshor_policy_submission.log").read_text(errors="replace")[-1500:]
        raise HarnessError("lean", f"lake exe forshor_policy_submission failed (exit {code}); it prints nothing when the IR "
                                   f"check fails:\n{tail}")
    try:
        doc = json.loads(pj.read_text())
    except json.JSONDecodeError as e:
        raise HarnessError("lean", f"policy.json is not JSON: {e}")
    return doc, {"build_s": round(t1 - t0, 1), "print_s": round(t2 - t1, 1), "build_peak_rss_mb": rss1, "print_peak_rss_mb": rss2,
                 "build_peak_tree_mb": b_info.get("peak_tree_mb"), "print_peak_tree_mb": p_info.get("peak_tree_mb"),
                 "mem_cap_gb": b_info.get("mem_cap_gb")}


# ---------------------------------------------------------------------------------------------- stage 4
#: ForShor's `Shor.NegationMethod` (swap 002). A ForShor tree at eb00c5a or later (the pin) prints `"negation"` in every
#: `forshor_policy_submission` document. An older tree (6d55ad9, 10fe620) has no such key and its `negateResources` is the Cuccaro
#: charge (`scratch`): that is a tree this harness no longer accepts. The price follows the printed document.
NEGATIONS = ("oneQubit", "scratch")
#: True since the pin is eb00c5a: a printed document without the key comes from a tree that is too old, which is a verification
#: failure (not a guess at the method).
REQUIRE_PRINTED_NEGATION = True


def check_negation(doc: Dict[str, Any], requested: Optional[str] = None) -> Dict[str, Any]:
    """The policy's negation method: what Lean printed against what was submitted. Returns the record entry.

    ``doc`` is the printed policy.json (data, not trusted); ``requested`` is the `negation` key of the submitted policy JSON
    (absent = "oneQubit"), or None when the submission is a template file (nothing to compare: the template is the source
    and the printed document says what it built). A printed value that is not a method, or that differs from the requested
    one, is a verification failure: otherwise Lean would build one method and Python price another."""
    printed = doc.get("negation")
    if "negation" in doc and (not isinstance(printed, str) or printed not in NEGATIONS):
        raise HarnessError("python", f"policy.json negation {printed!r} is not one of {NEGATIONS}")
    if printed is None:
        if requested == "scratch":
            raise HarnessError("python", "the submitted policy asks for negation 'scratch' but the policy.json Lean printed has no "
                                         "`negation` key: the build tree predates ForShor's NegationMethod (eb00c5a)")
        if REQUIRE_PRINTED_NEGATION:
            raise HarnessError("python", "policy.json has no `negation` key: the build tree is older than ForShor eb00c5a "
                                         "(NegationMethod), whose `forshor_policy_submission` always prints it; refusing to guess the method")
    elif requested is not None and printed != requested:
        raise HarnessError("python", f"negation mismatch: the submitted policy JSON says {requested!r} (no key = 'oneQubit') but "
                                     f"the policy ForShor built and printed says {printed!r}")
    from site_engine import fullalg as fa
    return {"requested": requested, "printed": printed, "priced_as": fa.cc.negate_impl(printed),
            **({"note": "no `negation` key (REQUIRE_PRINTED_NEGATION is off): priced as the default"} if printed is None else {})}


def _pt(p: Dict[str, Any]) -> str:
    """ForShor point dict -> tcost spelling. frac 0 is infinity; frac m is the point 1/m."""
    if p["kind"] == "int":
        return str(int(p["z"]))
    m = int(p["m"])
    return "inf" if m == 0 else f"{1 if m > 0 else -1}/{abs(m)}"


def policy_from_doc(doc: Dict[str, Any], name: str = "forshor-policy") -> tt.Policy:
    try:
        bands = [tt.Band(int(b["threshold"]), tt.Table.from_json({"mode": tt.PAIR, "k": int(b["k"]),
                                                                  "point_order": [_pt(p) for p in b["points"]],
                                                                  "operations": b["ops"]})) for b in doc["bands"]]
        return tt.Policy(mode=tt.PAIR, bands=tuple(bands), name=name)
    except (tt.TableError, KeyError, TypeError, ValueError) as e:
        raise HarnessError("python", f"policy.json does not describe a valid policy: {e}")


def python_stage(doc: Dict[str, Any], forshor: Path, name: str) -> Tuple[tt.Policy, List[Dict[str, Any]]]:
    ref, _ = reference_document(forshor)
    if doc.get("schema") != SCHEMA:
        raise HarnessError("python", f"schema {doc.get('schema')!r} != {SCHEMA!r}")
    for key, fixed in REFERENCE_FIXED.items():
        mine, theirs = doc.get(key), ref.get(key) or {}
        if not isinstance(mine, dict) or any(mine.get(f) != theirs.get(f) for f in fixed):
            raise HarnessError("python", f"{key} differs from the organiser's fixed values: "
                                         f"{ {f: mine.get(f) if isinstance(mine, dict) else None for f in fixed} } != "
                                         f"{ {f: theirs.get(f) for f in fixed} }")
    bands = doc.get("bands")
    if not isinstance(bands, list) or not bands:
        raise HarnessError("python", "policy.json has no bands")
    th = [b.get("threshold") for b in bands]
    if not all(isinstance(t, int) for t in th) or any(a <= b for a, b in zip(th, th[1:])):
        raise HarnessError("python", f"thresholds must strictly decrease, widest first; got {th}")
    P = policy_from_doc(doc, name)
    checks = []
    for i, (b, band) in enumerate(zip(P.bands, bands)):
        if not 2 <= b.table.k <= 6:
            raise HarnessError("python", f"band {i}: k = {b.table.k}, ForShor policies take 1 < k <= 6")
        try:
            python_verify(b.table)
        except (PolicyError, tt.TableError) as e:
            raise HarnessError("python", f"band {i}: {e}")
        checks.append({"band": i, "threshold": b.threshold, "k": b.table.k, "digest": b.table.digest, "shrinks": band.get("shrinks"),
                       "tcost_replay": True, "toom_cook_identity": True})
    return P, checks


# ---------------------------------------------------------------------------------------------- stage 5
def price(doc: Dict[str, Any], P: tt.Policy, delta: float, n: int = HERO_N) -> Dict[str, Any]:
    """Stage 5: the site's one full-run pricer (`site_engine.fullalg`, PLAN_v2 Stage 11) on the policy, and the
    conversion check: ForShor's printed document and the converted site policy must price alike under it."""
    from site_engine import fullalg as fa
    # Stage 18: the document's own width rule picks the IR it is checked on. A drop-in document ("exact", balanced IR) is
    # priced on the board's IR (fullalg.REFERENCE); a scan document (stock ForShor tree) on upstream ForShor's IR at
    # scanned widths (fullalg.REFERENCE_SCAN), the board's labelled cross-check. Comparing a scan document with the
    # exact board price would fail the conversion check for a faithful conversion.
    rule = fa.doc_width_rule(doc)
    negation = doc.get("negation")        # the printed document's own method (swap 002); absent: the repo default (legacy)
    r = fa.price_full(P, n, delta, detail=False, rule=rule, negation=negation)
    out = {"model": r["model"], "tier": r["tier"], "width_rule": rule, "n": n, "delta": delta, "single_run_T": float(r["T"]),
           "qubits": int(r["qubits"]),
           "rotations": int(r["rotations"]), "toffoli": r.get("toffoli"), "cnot": r.get("cnot"), "ladder_str": r.get("ladder_str"),
           "lean_grid_leaf_T": r["lean_grid_leaf_T"]}
    t_doc = float(fa.price_document(doc, n, delta)["T"])
    t_conv = float(r["T"])
    out["negation"] = {"printed": negation, "priced_as": fa.cc.negate_impl(negation)}
    out["native"] = {"tier": r["tier"], "width_rule": rule, "T_document": t_doc, "T_converted_policy": t_conv,
                     "conversion_agrees": t_doc == t_conv}
    if t_doc != t_conv:
        raise HarnessError("price", f"the document and the converted policy price differently under the full-run pricer "
                                    f"({t_doc} vs {t_conv}): the conversion is not faithful")
    return out


# ---------------------------------------------------------------------------------------------- stage 6
def _version(cmd: List[str], cwd: Path) -> str:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""


def _tree_digest(forshor: Path) -> str:
    h = hashlib.sha256()
    for rel in ("lake-manifest.json", "lean-toolchain", "policy.json", LEXICAL_REL,
                "FastMultiplication/ShorVerification/Submission/PolicyCheck.lean",
                "FastMultiplication/ShorVerification/Submission/PolicyMain.lean",
                "FastMultiplication/ShorVerification/Submission/Audit.lean",
                "FastMultiplication/ShorVerification/Submission/PolicyDecide.lean"):
        h.update(rel.encode() + (Path(forshor) / rel).read_bytes())
    return h.hexdigest()


def record(out: Path, data: bytes, cid: str, forshor: Path, doc: Dict[str, Any], P: tt.Policy, checks: List[Dict[str, Any]],
           priced: Dict[str, Any], stats: Dict[str, Any], seconds: Dict[str, float],
           max_heartbeats: Optional[int] = None, negation: Optional[Dict[str, Any]] = None) -> Path:
    rid = f"F{cid[:12]}"
    d = out / rid
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    manifest = json.loads((Path(forshor) / "lake-manifest.json").read_text())
    rec = {
        "schema": RECORD_SCHEMA, "id": rid, "content_sha256": cid, "created_at": now, "lean_verified": True,
        "bands": [{"threshold": b.threshold, "k": b.table.k, "digest": b.table.digest, "shrinks": c["shrinks"],
                   "table": b.table.to_json()} for b, c in zip(P.bands, checks)],
        "negation": negation,       # {"requested", "printed", "priced_as"[, "note"]}: Lean's printed method and how it was priced
        "single_run_T": priced["single_run_T"], "qubits": priced["qubits"], "rotations": priced["rotations"],
        "n": priced["n"], "delta": priced["delta"], "model": priced["model"], "native": priced["native"],
        "benchmark": doc["benchmark"], "precision": {k: doc["precision"][k] for k in ("eta", "m")},
        "trust": {"lean": "ForShor ShorPolicySubmission: C1-C4 per band and `sorted` by the kernel, axioms audited",
                  "ir": "pinned against the compiled circuit at sampled widths, not at every width",
                  "cost": "counted in Python (Qualtran), not proved in Lean", "python_verified": True},
        "forshor_tree_sha256": _tree_digest(forshor), "lean": _version(["lean", "--version"], Path(forshor)),
        "forshor": {**forshor_provenance(forshor), "reference_document": reference_document(forshor)[1]},
        "lake_manifest": {p["name"]: p["rev"] for p in manifest.get("packages", [])},
        "run": {**stats, "seconds": seconds}, "checks": checks,
        "template_sha256": hashlib.sha256(data).hexdigest(),
        "max_heartbeats": max_heartbeats or STOCK_HEARTBEATS,
        "site_override": ({"max_heartbeats": max_heartbeats, "stock": STOCK_HEARTBEATS,
                           "note": "site override of ForShor's resource cap in PolicyCheck.lean; not ForShor's stock check"}
                          if max_heartbeats and max_heartbeats != STOCK_HEARTBEATS else None),
    }
    (d / "record.json").write_text(json.dumps(rec, indent=1))
    shutil.copy2(out / "policy.json", d / "policy.json")
    (d / "source").mkdir(exist_ok=True)
    (d / "source" / "PolicyTemplate.lean").write_bytes(data)
    if (out / "logs").is_dir():
        shutil.rmtree(d / "logs", ignore_errors=True)
        shutil.copytree(out / "logs", d / "logs")
    return d


def install(d: Path, dest: Path = INSTALL_DIR) -> Path:
    target = dest / d.name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for f in ("record.json", "policy.json"):
        shutil.copy2(d / f, target / f)
    return target


# ---------------------------------------------------------------------------------------------- driver
def run(submission: Path, forshor: Path = DEFAULT_FORSHOR, out_root: Path = DEFAULT_OUT, delta: float = 0.01,
        keep_work: bool = False, lean_only: bool = False, from_policy: bool = False,
        max_heartbeats: Optional[int] = None, wait_slot: bool = True) -> Dict[str, Any]:
    t0 = time.time()
    secs: Dict[str, float] = {}
    requested: Optional[str] = None      # the submitted policy JSON's `negation` (absent = "oneQubit"); None for a template file
    if from_policy:
        from policy_to_template import TemplateError, requested_negation_from_file, template_from_file
        try:
            requested = requested_negation_from_file(submission)
            data = template_from_file(submission).encode()
        except TemplateError as e:
            raise HarnessError("intake", f"cannot write a ForShor policy from this policy: {e}")
        check_imports(data.decode())
        site_scan(data.decode())
    else:
        data = collect_template(submission)
    cid = content_id(data)
    out = out_root / f"forshor-run-{cid[:12]}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out_root / f"forshor-work-{cid[:12]}"
    try:
        stage(forshor, data, work, max_heartbeats)
        lexical_gate(forshor, work)
        secs["stage"] = round(time.time() - t0, 1)
        secs["waited_for_lean_slot"] = wait_lean_slot() if wait_slot else 0.0
        t = time.time()
        doc, stats = lean_build(work, out, exe_args(forshor))
        stats["exe_args"] = exe_args(forshor)
        secs["lean"] = round(time.time() - t, 1)
        t = time.time()
        neg = check_negation(doc, requested)
        P, checks = python_stage(doc, forshor, f"forshor-{cid[:8]}")
        secs["python"] = round(time.time() - t, 1)
        if lean_only:
            return {"id": f"F{cid[:12]}", "status": "lean-ok", "negation": neg, "bands": [(b.threshold, b.table.k) for b in P.bands], "stats": stats,
                    "seconds": secs, "policy_json": str(out / "policy.json"),
                    "forshor": {**forshor_provenance(forshor), "reference_document": reference_document(forshor)[1]}}
        t = time.time()
        priced = price(doc, P, delta)
        secs["price"] = round(time.time() - t, 1)
        d = record(out, data, cid, forshor, doc, P, checks, priced, stats, secs, max_heartbeats, neg)
        return {"id": d.name, "status": "verified", "dir": str(d), "negation": neg, "bands": [(b.threshold, b.table.k) for b in P.bands],
                "single_run_T": priced["single_run_T"], "native": priced["native"], "stats": stats, "seconds": secs,
                "forshor": {k: v for k, v in forshor_provenance(forshor).items() if k in ("commit", "lean_toolchain", "mathlib", "tree")}}
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("verify")
    p.add_argument("submission", type=Path)
    p.add_argument("--forshor", type=Path, default=Path(os.environ.get("FORSHOR_BUILD", DEFAULT_FORSHOR)))
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--delta", type=float, default=0.01)
    p.add_argument("--from-policy", action="store_true", help="the argument is a site policy JSON: write the template from it")
    p.add_argument("--install", action="store_true", help="copy the record to engine/data/forshor_submitted/")
    p.add_argument("--max-heartbeats", type=int, default=None,
                   help=f"SITE OVERRIDE of ForShor's cap (stock {STOCK_HEARTBEATS}); recorded in the provenance")
    p.add_argument("--keep-work", action="store_true")
    p.add_argument("--lean-only", action="store_true", help="stop after the Lean build and print (no pricing)")
    a = ap.parse_args(argv)
    try:
        s = run(a.submission, a.forshor, a.out, a.delta, a.keep_work, a.lean_only, a.from_policy, a.max_heartbeats)
    except HarnessError as e:
        print(f"REJECTED at stage '{e.stage}': {e.detail}", file=sys.stderr)
        return 1
    print(json.dumps(s, indent=1))
    if a.install and s["status"] == "verified":
        print(f"installed -> {install(Path(s['dir']))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
