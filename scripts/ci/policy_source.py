"""Cheap, non-executing source restrictions for complete PP/PTP policies.

Apply before compilation/reuse and again to GitHub's exact source before
publication. Lean and the pinned estimator still decide mathematical validity.
The lexer is the existing hardened verifier lexer; no submitted code is run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))
from scripts.verifier import strip_lean_comments

MAX_BYTES = 1_000_000
ALLOWED_IMPORTS = {
    'ForShor.ShorVerification.Framework.Policy',
    'ForShor.ShorVerification.Framework.ToomCookTable',
    'ForShor.ShorVerification.Implementation.PhaseTripleProduct.Statement',
    'ForShor.ShorVerification.Submission.Decide',
}
# Same additional restrictions as the existing site harness, including paths
# that the upstream lexical shell gate does not reject (notably #eval).
BANNED = re.compile(
    r"(?<![A-Za-z0-9_'])"
    r"(?:run_cmd|run_meta|run_elab|run_tac|runCmd|runTac|native_decide|ofReduceBool|trustCompiler|"
    r"sorry|sorryAx|admit|axiom|constant|opaque|partial|unsafe|implemented_by|extern|csimp|"
    r"builtin_initialize|initialize|macro_rules|macro|elab_rules|elab|syntax|notation|"
    r"attribute|instance|class|export|deriving|command_elab|term_elab|tactic_elab|"
    r"infixl|infixr|infix|prefix|postfix|declare_syntax_cat|set_option|Lean|IO|System|"
    r"unsafeBaseIO|dbgTrace|panic|prelude)"
    r"(?![A-Za-z0-9_'])"
)
HEADER_END = re.compile(r'^(?:namespace|open|section|end|def|abbrev|theorem|lemma|example|variable|universe)\b')


def validate(source):
    if len(source.encode('utf-8')) > MAX_BYTES:
        raise ValueError(f'Policy.lean exceeds {MAX_BYTES} bytes')
    text = strip_lean_comments(source)
    # #[] array literals are data; every other hash command is outside the
    # policy surface. Escaped names must not hide namespace/command tokens.
    if re.search(r'#(?!\[)', text) or '«' in text or '»' in text:
        raise ValueError('Policy.lean may not contain build-time commands or escaped identifiers')
    found = BANNED.search(text)
    if found:
        line = text.count('\n', 0, found.start()) + 1
        raise ValueError(f'Policy.lean line {line}: {found[0]} is outside the policy source rules')
    header = True
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r'import\b', line):
            modules = line.split()[1:]
            if not header or not modules or any(m not in ALLOWED_IMPORTS for m in modules):
                raise ValueError('Policy.lean imports must be the approved template modules, one import command per line')
        elif header:
            if not HEADER_END.match(line):
                raise ValueError('Policy.lean has an unsupported import/header; use the shipped template')
            header = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', type=Path)
    parser.add_argument('--stdin', action='store_true')
    parser.add_argument('--artifacts', type=Path, help='Update the trusted preflight report on a source-rule refusal')
    args = parser.parse_args()
    if args.stdin == (args.source is not None):
        parser.error('Provide one policy file or --stdin')
    try:
        validate(sys.stdin.read() if args.stdin else args.source.read_text(encoding='utf-8'))
    except ValueError as error:
        if args.artifacts:
            report = args.artifacts / 'table-generation-submission-result.json'
            result = json.loads(report.read_text())
            if result.get('kind') != 'policy' or result.get('phase') != 'preflight':
                raise RuntimeError('Expected a trusted policy preflight result')
            result['status'] = 'failure'
            result.setdefault('checks', []).append({'name': 'policy-source', 'status': 'failure', 'details': str(error)})
            report.write_text(json.dumps(result, indent=2) + '\n')
            summary = args.artifacts / 'table-generation-summary.md'
            with summary.open('a') as output:
                output.write('\n## Policy source refused\n\n' + str(error) + '\n')
        parser.exit(1, f'{error}\n')
    except OSError as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
