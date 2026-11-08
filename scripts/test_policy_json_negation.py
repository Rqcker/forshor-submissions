#!/usr/bin/env python3
"""The optional `negation` key of the policy JSON (swap-002), and the no-key regression.

Stdlib only, no Lean. Run: python3 -I scripts/test_policy_json_negation.py

* GOLDEN: SHA-256 of the Lean text the two generators produced for every `policies/*.json` BEFORE the key
  existed (PhaseProduct: recorded with the generators of 364400d; PhaseTripleProduct in this fork: recorded with this fork's generator
  before the key, `origin/main` 3a4ba21, because it is re-pointed at ForShor's `Shor.PTP`; scripts/tests/policy_json_lean_golden.json). A policy
  with no `negation`, or `"oneQubit"`, must still generate exactly those bytes.
* `"scratch"` adds `negation := .scratch` as a field of `def policy` (BandedPolicy / PTPPolicy, data part) and
  nothing else; any other value is a PolicyError. The Lean it emits is checked by building a generated module
  (see shards/B2.md).
"""
import copy
import glob
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import exact_width as ew  # noqa: E402
import policy_json_to_lean as pp  # noqa: E402
import ptp_exact_width as pw  # noqa: E402
import ptp_policy_json_to_lean as ptp  # noqa: E402

GOLDEN = json.loads((ROOT / "scripts/tests/policy_json_lean_golden.json").read_text())


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def load(path):
    doc = json.loads(Path(path).read_text())
    return doc if "bands" in doc else doc["policy"]


def variants(doc):
    """name -> Lean text, the same set the golden digests were taken over."""
    declared = pp.ladder_of(doc.get("declared_ladder"))
    v = {
        "pp_all": pp.render(doc, "M", 2048, "all", declared=declared),
        "pp_all_notarget": pp.render(doc, "M", None, "all", declared=declared),
        "pp_data": pp.render(doc, "M", None, "data", declared=declared),
        "pp_cert": pp.render(doc, "C", 2048, "cert", "D", declared=declared),
        "pp_all_bc": pp.render(doc, "M", 2048, "all", declared=declared, bands_contract=True),
    }
    if doc["mode"] == "PhaseProduct":
        lad = [(r["width"], r["k"]) for r in ew.exact_ladder(doc, 2048)]
        v["pp_exact"] = pp.render(doc, "M", 2048, "all", declared=declared, widths="exact", exact_ladder=lad)
        v["pp_exact_cert"] = pp.render(doc, "C", 2048, "cert", "D", declared=declared, widths="exact", exact_ladder=lad)
    else:
        v["ptp_data"] = ptp.render(doc, "M", None, "data")
        v["ptp_cert_scan"] = ptp.render(doc, "C", 2048, "cert", "D", "scan", [])
        v["ptp_cert_exact"] = ptp.render(doc, "C", 2048, "cert", "D", "exact", pw.exact_ladder(doc, 2048))
    return v


POLICIES = sorted(glob.glob(str(ROOT / "policies/*.json")))


class NoKeyIsByteIdentical(unittest.TestCase):
    def test_every_policy_matches_the_recorded_digests(self):
        self.assertEqual(len(POLICIES), len(GOLDEN))
        for f in POLICIES:
            name = Path(f).name
            got = {k: sha(t) for k, t in variants(load(f)).items()}
            self.assertEqual(got, GOLDEN[name], name)

    def test_explicit_default_is_the_same_as_no_key(self):
        for f in POLICIES:
            doc = load(f)
            d2 = dict(doc, negation="oneQubit")
            self.assertEqual(variants(doc), variants(d2), Path(f).name)


class ScratchKey(unittest.TestCase):
    def pp_doc(self):
        return load(ROOT / "policies/ref_pp_2048.json")

    def ptp_doc(self):
        return load(ROOT / "policies/ref_ptp_2048.json")

    def test_pp_scratch_changes_only_the_policy_and_the_comment(self):
        doc = self.pp_doc()
        base = pp.render(doc, "M", 2048, "all")
        sc = pp.render(dict(doc, negation="scratch"), "M", 2048, "all")
        self.assertEqual(sc.count("negation := .scratch"), 1)  # in `def policy : BandedPolicy`
        self.assertNotIn("negation", base)
        cut = lambda t: re.sub(r"  negation := \.scratch\n", "",
                               re.sub(r"/-- The same policy as ForShor's object.*?-/\n", "", t, flags=re.S))
        self.assertEqual(cut(base), cut(sc))

    def test_pp_cert_part_does_not_carry_it_and_data_part_does(self):
        doc = dict(self.pp_doc(), negation="scratch")
        self.assertNotIn("negation := .scratch", pp.render(doc, "C", 2048, "cert", "D"))
        self.assertIn("negation := .scratch", pp.render(doc, "D", None, "data"))

    def test_ptp_scratch_is_a_field_of_the_policy_in_the_data_part(self):
        doc = self.ptp_doc()
        base = ptp.render(doc, "D", None, "data")
        sc = ptp.render(dict(doc, negation="scratch"), "D", None, "data")
        self.assertIn("  negation := .scratch", sc)
        self.assertNotIn("negation", base)
        self.assertEqual(sc.replace("  negation := .scratch\n", ""), base)
        cert = ptp.render(doc, "C", 2048, "cert", "D", "scan", [])
        cert_sc = ptp.render(dict(doc, negation="scratch"), "C", 2048, "cert", "D", "scan", [])
        # the cert module differs only in the resource model its census statement names
        self.assertEqual(cert.replace("LowGate.resources shorGateResourceModel (G φ)", "LowGate.resources policy.resourceModel (G φ)"),
                         cert_sc)
        self.assertIn("policy.resourceModel", cert_sc)

    def test_ptp_mode_through_the_pp_generator_sets_the_field(self):
        doc = dict(self.ptp_doc(), negation="scratch")
        self.assertIn("  negation := .scratch", pp.render(doc, "M", 2048, "all"))

    def test_bad_values_are_rejected_by_both_generators(self):
        for bad in ("", "Scratch", "one_qubit", "incrementer", None, 1, True, ["scratch"]):
            for doc, render_args in ((self.pp_doc(), (pp.render, "M", 2048, "all")),
                                     (self.ptp_doc(), (ptp.render, "M", None, "data"))):
                d = dict(copy.deepcopy(doc), negation=bad)
                fn, *args = render_args
                with self.assertRaises(pp.PolicyError, msg=repr(bad)) as cm:
                    fn(d, *args)
                self.assertIn("negation", str(cm.exception))

    def test_cli_prints_a_clear_error(self):
        import io
        import contextlib
        import tempfile
        d = dict(self.pp_doc(), negation="incrementer")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as t:
            json.dump(d, t)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = pp.main([t.name, "--module", "M"])
        Path(t.name).unlink()
        self.assertEqual(rc, 2)
        self.assertIn('bad negation \'incrementer\': expected "oneQubit" (the default) or "scratch"', err.getvalue())


if __name__ == "__main__":
    unittest.main()
