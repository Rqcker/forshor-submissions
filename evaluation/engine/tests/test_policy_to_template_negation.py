#!/usr/bin/env python3
"""The optional `negation` key of `harness/policy_to_template.py` (swap-002), and the no-key regression.

Stdlib only, no Lean. Run: python3 -I evaluation/engine/tests/test_policy_to_template_negation.py   (pytest collects it too)

* GOLDEN: SHA-256 of the `PolicyTemplate.lean` (or the TemplateError text) the generator produced for every policy it can read
  in this repository, for both library names, recorded BEFORE the key existed (tests/data/policy_to_template_golden.json,
  generator of 364400d / ffae2f3). A policy with no `negation`, or `"oneQubit"`, must still generate exactly those bytes.
* `"scratch"` adds the one line `  negation := .scratch` to the `policy` record and nothing else; any other value is a
  NegationError, exit code 2 on the command line.
* The shipped templates under engine/data/forshor_submitted are reproduced byte for byte where they came from the generator.
The Lean the generator emits for `scratch` is UNTESTED (needs lake and a ForShor checkout with `NegationMethod`).
"""
import contextlib
import copy
import glob
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "evaluation/harness"))
sys.path.insert(0, str(ROOT / "evaluation/engine"))
import site_engine  # noqa: E402,F401
import policy_to_template as m  # noqa: E402
from tcost import tables as tt  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "data/policy_to_template_golden.json").read_text())
FILES = sorted(glob.glob(str(ROOT / "policies/*.json")) + [str(ROOT / "evaluation/engine/data/reference_policies.json")]
               + glob.glob(str(ROOT / "evaluation/engine/data/sto_accepted/*/operations.json"))
               + glob.glob(str(ROOT / "evaluation/engine/data/forshor_submitted/*/site_policy.json")))
SCRATCH_LINE = "  negation := .scratch\n"


def digest_table(doc_for=lambda f: None):
    """The golden's table, recomputed: key `<file>#<index>#<lib>` -> sha256 / TemplateError text; `load_error` for files the
    tcost loader refuses. `doc_for(file)` may return a policy-object edit (a callable on the dict) to apply first."""
    out = {}
    for f in FILES:
        rel = str(Path(f).relative_to(ROOT))
        try:
            docs = tt.policy_docs(json.loads(Path(f).read_text()))
            edit = doc_for(f)
            if edit:
                docs = [edit(copy.deepcopy(d)) for d in docs]
            pols = tt.checked_policies(docs)
        except Exception as e:  # noqa: BLE001
            out[rel] = {"load_error": type(e).__name__}
            continue
        for i, p in enumerate(pols):
            for lib in m.LIBS:
                try:
                    v = hashlib.sha256(m.template_text(p, lib, m.negation_of(docs[i])).encode()).hexdigest()
                except m.TemplateError as e:
                    v = "TemplateError: " + str(e)
                out[f"{rel}#{i}#{lib}"] = v
    return out


def first_pp():
    return json.loads((ROOT / "policies/ref_pp_2048.json").read_text())


def pp_policy():
    d = first_pp()
    return d.get("policy", d)


class NoKeyIsByteIdentical(unittest.TestCase):
    def test_every_readable_policy_matches_the_recorded_digests(self):
        got = digest_table()
        self.assertEqual(sorted(got), sorted(GOLDEN))
        self.assertGreaterEqual(sum(1 for v in got.values() if isinstance(v, str) and len(v) == 64), 60)
        for k in GOLDEN:
            self.assertEqual(got[k], GOLDEN[k], k)

    def test_explicit_default_is_the_same_as_no_key(self):
        def add(d):
            d["negation"] = "oneQubit"
            return d
        self.assertEqual(digest_table(lambda f: add), GOLDEN)

    def test_template_text_without_the_argument_is_unchanged(self):
        P = tt.load_policies(ROOT / "policies/ref_pp_2048.json")[0]
        self.assertEqual(m.template_text(P), m.template_text(P, m.DEFAULT_LIB, None))
        self.assertEqual(m.template_text(P), m.template_text(P, m.DEFAULT_LIB, "oneQubit"))
        self.assertNotIn("negation", m.template_text(P))

    def test_shipped_templates_made_by_the_generator_are_reproduced(self):
        n = 0
        for d in sorted(glob.glob(str(ROOT / "evaluation/engine/data/forshor_submitted/*/"))):
            shipped, pol = Path(d, "PolicyTemplate.lean"), Path(d, "site_policy.json")
            if not (shipped.is_file() and pol.is_file()) or "policy_to_template.py" not in shipped.read_text():
                continue
            try:
                got = m.template_from_file(pol)
            except (m.TemplateError, tt.TableError):
                continue
            if got == shipped.read_text():
                n += 1
        self.assertGreaterEqual(n, 1)


class ScratchKey(unittest.TestCase):
    def render(self, **kw):
        d = copy.deepcopy(pp_policy())
        d.update(kw)
        p = Path(tempfile.mkdtemp()) / "p.json"
        p.write_text(json.dumps(d))
        return m.template_from_file(p)

    def test_scratch_adds_exactly_the_negation_line_after_sorted(self):
        base, sc = self.render(), self.render(negation="scratch")
        self.assertEqual(sc.count("negation"), 1)
        self.assertIn("  sorted := by decide\n" + SCRATCH_LINE + "\nend PolicySubmission\n", sc)
        self.assertEqual(sc.replace(SCRATCH_LINE, "", 1), base)
        self.assertEqual(self.render(negation="oneQubit"), base)

    def test_scratch_on_every_readable_pp_policy_differs_only_in_that_line(self):
        def add(d):
            d["negation"] = "scratch"
            return d
        base, sc = digest_table(), digest_table(lambda f: add)
        n = 0
        for f in FILES:
            try:
                docs = tt.policy_docs(json.loads(Path(f).read_text()))
                pols = tt.checked_policies([add(copy.deepcopy(d)) for d in docs])
            except Exception:  # noqa: BLE001
                continue
            for i, p in enumerate(pols):
                if p.mode != tt.PAIR:
                    continue
                t0 = m.template_text(p)
                t1 = m.template_text(p, m.DEFAULT_LIB, "scratch")
                self.assertEqual(t1.replace(SCRATCH_LINE, "", 1), t0)
                self.assertNotEqual(t1, t0)
                n += 1
        self.assertGreaterEqual(n, 30)
        self.assertEqual(set(base), set(sc))

    def test_scratch_for_the_qubitcount_library_name_too(self):
        P = tt.load_policies(ROOT / "policies/ref_pp_2048.json")[0]
        t = m.template_text(P, "ForShor", "scratch")
        self.assertTrue(t.startswith("import ForShor.ShorVerification.Framework.ToomCookTable\n"))
        self.assertEqual(t.count(SCRATCH_LINE), 1)

    def test_bad_values_are_rejected(self):
        for bad in ("", "Scratch", "one_qubit", "incrementer", None, 1, True, ["scratch"]):
            with self.assertRaises(m.NegationError, msg=repr(bad)) as cm:
                self.render(negation=bad)
            self.assertIn("negation", str(cm.exception))
        P = tt.load_policies(ROOT / "policies/ref_pp_2048.json")[0]
        with self.assertRaises(m.NegationError):
            m.template_text(P, m.DEFAULT_LIB, "incrementer")

    def test_a_triple_product_policy_is_still_refused_whatever_the_key(self):
        P = tt.load_policies(ROOT / "policies/ref_ptp_2048.json")[0]
        with self.assertRaises(m.TemplateError):
            m.template_text(P, m.DEFAULT_LIB, "scratch")

    def test_cli(self):
        def run(**kw):
            d = dict(copy.deepcopy(pp_policy()), **kw)
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as t:
                json.dump(d, t)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = m.main([t.name])
            Path(t.name).unlink()
            return rc, out.getvalue(), err.getvalue()
        rc0, o0, _ = run()
        rc1, o1, _ = run(negation="scratch")
        rc2, o2, e2 = run(negation="incrementer")
        self.assertEqual((rc0, rc1), (0, 0))
        self.assertEqual(o1.replace(SCRATCH_LINE, "", 1), o0)
        self.assertEqual((rc2, o2), (2, ""))
        self.assertIn('bad negation \'incrementer\': expected "oneQubit" (the default) or "scratch"', e2)


if __name__ == "__main__":
    unittest.main()
