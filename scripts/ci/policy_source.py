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
FULL_RUN_IMPORT = 'ForShor.ShorVerification.Framework.FullRunDesign'
ALLOWED_IMPORTS = {
    'ForShor.ShorVerification.Framework.Policy',
    'ForShor.ShorVerification.Framework.PrimitiveSynthesis',
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
FULL_RUN_SELECTOR = re.compile(r"(?<![\w'])submittedFullRunDesign(?![\w'])")


# Explicit default selections. Owner ruling 2026-11-24: Ross-Selinger, the reference full run and coherent readout are
# the defaults, and an explicit default token is identical to omission (Lean's resolvers return the default when the
# declaration is absent, and attest the document unchanged when it is the default). Only these exact declarations are
# recognised: one `def` at column 0 directly inside the board's namespace, annotated with the repo's type, with the
# default value and nothing else on the statement. Recognition is lexical only (nothing is run) and Lean still
# decides validity; anything else that mentions a selector name is refused exactly as before.
_SHOR = r'(?:_root_\.)?Shor\.'
_BOARDS = ('PolicySubmission', 'PTPPolicySubmission')
_DEFAULT_FORMS = {   # name: (declaration after the name, boards where Lean reads it)
    'submittedPrimitive': (rf':[ \t]*{_SHOR}PrimitiveSynthesis[ \t]*:=[ \t]*(?:\.|{_SHOR}PrimitiveSynthesis\.)rossSelinger',
                           _BOARDS),
    # PTPPolicyCheck refuses any submittedFullRunDesign, `.reference` included, so only the PhaseProduct board has the default.
    'submittedFullRunDesign': (rf':[ \t]*{_SHOR}FullRunDesign[ \t]*:=[ \t]*(?:\.|{_SHOR}FullRunDesign\.)reference',
                               ('PolicySubmission',)),
    'submittedDesign': (rf':[ \t]*{_SHOR}SubmissionDesign[ \t]*(?::=[ \t]*\{{[ \t]*readout[ \t]*:=[ \t]*\.coherent[ \t]*\}}'
                        rf'|where[ \t]*\n[ \t]+readout[ \t]*:=[ \t]*\.coherent)', _BOARDS),
}
_DEFAULT_DECL = {name: re.compile(rf'def[ \t]+{name}[ \t]*{form}[ \t]*(?:\n|\Z)') for name, (form, _) in _DEFAULT_FORMS.items()}
_COMMAND_START = re.compile(r'(?:end|def|abbrev|theorem|lemma|example|namespace|open|section|variable|universe|structure|inductive)\b')
_MODIFIER_LINE = re.compile(r'(?:@\[|private\b|protected\b|noncomputable\b|unsafe\b|partial\b|nonrec\b|scoped\b|local\b)')
_NAMESPACE = re.compile(r'namespace[ \t]+([A-Za-z_][\w.]*)[ \t]*$')
_SECTION = re.compile(r'section\b')
_END = re.compile(r'end\b')


def default_selectors(text):
    """(`text` with each explicit default declaration blanked, {selector: board namespace}) for comment-stripped source.

    Line count is preserved. A declaration is consumed only once per selector, only at column 0 directly inside the
    board's namespace, and only when the next non-blank line starts a new command at column 0 (so no continuation
    line can change the value) and the previous one is not a modifier or attribute line. The rest of the file,
    including any second declaration or any other mention of the selector, is left for the refusal checks."""
    lines = text.split('\n')
    consumed = {}
    scopes = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (m := _NAMESPACE.fullmatch(stripped)):
            scopes.append(m[1])
        elif _SECTION.match(stripped):
            scopes.append('')
        elif _END.match(stripped) and scopes:
            scopes.pop()
        namespace = '.'.join(part for part in scopes if part)
        found = re.match(r'def[ \t]+(submitted\w+)', line)       # column 0 only
        if not found or found[1] not in _DEFAULT_DECL or found[1] in consumed or namespace not in _DEFAULT_FORMS[found[1]][1]:
            continue
        rest = '\n'.join(lines[i:i + 2]) + '\n'
        match = _DEFAULT_DECL[found[1]].match(rest)
        if not match:
            continue
        span = match.group(0).count('\n')                      # lines the declaration occupies
        end = i + (span if span else 1)
        following = next((l for l in lines[end:] if l.strip()), None)
        previous = next((l.strip() for l in reversed(lines[:i]) if l.strip()), '')
        if (following is not None and not _COMMAND_START.match(following)) or _MODIFIER_LINE.match(previous):
            continue
        consumed[found[1]] = namespace
        for j in range(i, end):
            lines[j] = ''
    return '\n'.join(lines), consumed


def without_default_selectors(text):
    """Comment-stripped source with the explicit default selector declarations removed (see `default_selectors`)."""
    return default_selectors(text)[0]


def check_no_full_run_selector(text):
    """Reject the selector token in comment-stripped source, except the explicit default `.reference` declaration."""
    if FULL_RUN_SELECTOR.search(without_default_selectors(text)):
        raise ValueError('submittedFullRunDesign requires dedicated nonpublishing full-run qualification')


def validate(source, *, qualify_full_run=False):
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
    if not qualify_full_run:
        check_no_full_run_selector(text)
    default_full_run = 'submittedFullRunDesign' in default_selectors(text)[1]
    header = True
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r'import\b', line):
            modules = line.split()[1:]
            if not header or not modules or any(m not in ALLOWED_IMPORTS and
                                                not ((qualify_full_run or default_full_run) and m == FULL_RUN_IMPORT) for m in modules):
                raise ValueError('Policy.lean imports must be the approved template modules, one import command per line')
        elif header:
            if not HEADER_END.match(line):
                raise ValueError('Policy.lean has an unsupported import/header; use the shipped template')
            header = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', type=Path)
    parser.add_argument('--stdin', action='store_true')
    parser.add_argument('--qualify-full-run', action='store_true', help='Use only in the dedicated nonpublishing qualification harness')
    parser.add_argument('--artifacts', type=Path, help='Update the trusted preflight report on a source-rule refusal')
    args = parser.parse_args()
    if args.stdin == (args.source is not None):
        parser.error('Provide one policy file or --stdin')
    if args.qualify_full_run and args.artifacts is not None:
        parser.error('--qualify-full-run cannot be used for ordinary preflight artifacts')
    try:
        validate(sys.stdin.read() if args.stdin else args.source.read_text(encoding='utf-8'),
                 qualify_full_run=args.qualify_full_run)
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
