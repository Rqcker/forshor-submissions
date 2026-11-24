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


# A refusal has to name WHICH cause refused, because the causes need different participant actions: a
# non-default profile is a valid request on the wrong route (the dedicated nonpublishing harness), a
# mis-written default only has to be rewritten, and a bare mention has to be deleted. The leading
# sentence of each message is the anchor the rest of the repository matches on and never changes; the
# cause follows it after a colon. Classification happens only AFTER the refusal is decided, so nothing
# here changes which sources are accepted or refused.
SHIPPED_FULL_RUN_FORM = 'def submittedFullRunDesign : Shor.FullRunDesign := .reference'
FULL_RUN_REFUSAL = 'submittedFullRunDesign requires dedicated nonpublishing full-run qualification'
IMPORT_REFUSAL = 'Policy.lean imports must be the approved template modules, one import command per line'
_SHIPPED_FULL_RUN_FORM_RULE = ('one public `def` at column 0 directly inside `namespace PolicySubmission`, with the '
                               'repo type, the value on the same line, nothing continuing it on the next line, no '
                               'attribute or modifier line before it, and declared exactly once')
_DEFAULT_FULL_RUN_VALUE = re.compile(rf"(?:\.|{_SHOR}FullRunDesign\.)reference(?![\w'])")
_SELECTOR_DECLARATION = re.compile(
    r"^[ \t]*(?:@\[[^\]]*\][ \t]*|(?:private|protected|noncomputable|unsafe|partial|nonrec|scoped|local)[ \t]+)*"
    r"(?:def|abbrev|instance|structure|inductive)[ \t]+submittedFullRunDesign(?![\w'])(?P<rest>.*)$")


def _full_run_cause(residual):
    """(line number, cause) for the first `submittedFullRunDesign` left after the explicit default is consumed.

    `residual` is comment-stripped, default-stripped source; both transformations preserve the line count, so the
    number is the participant's own line. Three classes, three actions: qualify elsewhere, rewrite, or delete."""
    found = FULL_RUN_SELECTOR.search(residual)
    number = residual.count('\n', 0, found.start()) + 1
    declaration = _SELECTOR_DECLARATION.match(residual.split('\n')[number - 1])
    if declaration is None:
        return number, ('the name is used here but does not declare a selector, and an ordinary submission may not '
                        'mention it at all; remove the mention')
    rest = declaration['rest']
    value = rest.split(':=', 1)[1].strip() if ':=' in rest else ''
    if value and not _DEFAULT_FULL_RUN_VALUE.search(value):
        return number, (f'this declaration selects the non-default full-run design `{value}`, and only the dedicated '
                        'nonpublishing full-run harness qualifies a full run (it never publishes)')
    return number, (f'this declaration selects the default full run, but not in the shipped form '
                    f'`{SHIPPED_FULL_RUN_FORM}` ({_SHIPPED_FULL_RUN_FORM_RULE}); rewrite it, or omit it, and the '
                    'ordinary route accepts the policy')


def check_no_full_run_selector(text):
    """Reject the selector token in comment-stripped source, except the explicit default `.reference` declaration."""
    residual = without_default_selectors(text)
    if FULL_RUN_SELECTOR.search(residual):
        number, cause = _full_run_cause(residual)
        raise ValueError(f'{FULL_RUN_REFUSAL}: line {number}, {cause}')


def validate(source, *, qualify_full_run=False):
    if len(source.encode('utf-8')) > MAX_BYTES:
        raise ValueError(f'Policy.lean exceeds {MAX_BYTES} bytes')
    text = strip_lean_comments(source)
    # #[] array literals are data; every other hash command is outside the
    # policy surface. Escaped names must not hide namespace/command tokens.
    # The two are different edits, so they are reported as different causes.
    if (hashed := re.search(r'#(?!\[)', text)):
        raise ValueError(f'Policy.lean line {text.count(chr(10), 0, hashed.start()) + 1}: Policy.lean may not contain '
                         'build-time commands; only `#[...]` array literals may use `#`')
    if (escaped := re.search('[«»]', text)):
        raise ValueError(f'Policy.lean line {text.count(chr(10), 0, escaped.start()) + 1}: the escaped identifier '
                         f'delimiter {escaped[0]} is outside the policy source rules, because an escaped name can '
                         'hide a namespace or command token; use a plain identifier')
    found = BANNED.search(text)
    if found:
        line = text.count('\n', 0, found.start()) + 1
        raise ValueError(f'Policy.lean line {line}: {found[0]} is outside the policy source rules')
    if not qualify_full_run:
        check_no_full_run_selector(text)
    default_full_run = 'submittedFullRunDesign' in default_selectors(text)[1]
    header = True
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if re.match(r'import\b', line):
            # Same four refusal conditions as before, in the same order, each now naming its own cause: an
            # unapproved module and a misplaced or empty import are participant errors, while the full-run
            # framework is a valid request that belongs on the dedicated nonpublishing route.
            modules = line.split()[1:]
            if not header:
                raise ValueError(f'{IMPORT_REFUSAL}: line {number} is an `import` after the header; every import '
                                 "belongs in the file's header, before the first command")
            if not modules:
                raise ValueError(f'{IMPORT_REFUSAL}: line {number} is an `import` with no module name')
            allowed = ALLOWED_IMPORTS | ({FULL_RUN_IMPORT} if qualify_full_run or default_full_run else set())
            if (unapproved := [m for m in modules if m not in allowed]):
                if FULL_RUN_IMPORT in unapproved:
                    raise ValueError(f'{IMPORT_REFUSAL}: line {number} imports {FULL_RUN_IMPORT}, which an ordinary '
                                     f'submission may import only together with the explicit default '
                                     f'`{SHIPPED_FULL_RUN_FORM}`; any other full run is qualified by the dedicated '
                                     'nonpublishing harness')
                raise ValueError(f'{IMPORT_REFUSAL}: line {number} imports {", ".join(unapproved)}, which '
                                 f'{"is" if len(unapproved) == 1 else "are"} not an approved template module')
        elif header:
            if not HEADER_END.match(line):
                raise ValueError(f'Policy.lean has an unsupported import/header; use the shipped template: line '
                                 f'{number} begins with {line.split()[0]!r}, which is not one of the template\'s '
                                 'header commands')
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
