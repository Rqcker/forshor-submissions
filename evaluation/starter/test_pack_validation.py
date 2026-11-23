#!/usr/bin/env python3
"""Exercise the unzipped pack, not imports from the development tree."""
import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", type=Path)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    def run(*parts, error=None):
        result = subprocess.run([args.python, "-I", str(args.pack / "starter.py"), *map(str, parts)],
                                capture_output=True, text=True, timeout=120)
        if error is None:
            assert result.returncode == 0, (parts, result.stderr)
        else:
            assert result.returncode == 1 and error in result.stderr, (parts, result.stderr)
            assert "Traceback" not in result.stderr and not result.stdout, (parts, result.stdout, result.stderr)
        return result.stdout

    pp = args.pack / "templates/pp_policy.json"
    ptp = args.pack / "templates/ptp_policy.json"
    compiled = args.pack / "examples/forshor_dropin.policy.json"
    for policy in (pp, ptp):
        for n in (16, 32768):
            for delta in (1e-15, math.nextafter(1.0, 0.0)):
                run("price", policy, "--n", n, "--delta", delta)
        assert run("price", policy) == run("price", policy, "--n", 2048, "--delta", 0.01)
    for n in (15, 32769):
        run("price", pp, "--n", n, error="n must be in")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "input.json"
        doc = json.loads(pp.read_text())
        path.write_text(json.dumps({**doc, "negation": "oneQubit"}))
        assert run("template", pp) == run("template", path)
        path.write_text(json.dumps({**doc, "negation": "scratch"}))
        for command in ("price", "template", "lean-input"):
            run(command, path, error="the policy asks for negation 'scratch'")
        path.write_text(json.dumps({**json.loads(compiled.read_text()), "negation": "scratch"}))
        run("forshor-doc", path, error="the policy asks for negation 'scratch'")
        for delta in ("nan", "inf", "0", "1"):
            run("price", pp, "--delta", delta, error="delta must be")
            run("forshor-doc", compiled, "--delta", delta, error="delta must be")
        path.write_bytes(b"\xff")
        run("price", path, error="file must be UTF-8")
        path.write_text('{"schema":"forshor.policy-submission/v1","bands":null}')
        run("forshor-doc", path, error="malformed or unsupported ForShor policy document")
        for count in (256, 257):
            bands = [{**doc["bands"][0], "threshold": 1000 + count - i} for i in range(count)]
            path.write_text(json.dumps({**doc, "bands": bands}))
            for command in ("template", "lean-input"):
                run(command, path, error=None if count == 256 else "at most 256 bands; found 257")
    print("starter pack validation: OK")


if __name__ == "__main__":
    main()
