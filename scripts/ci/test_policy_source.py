import unittest
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil

from policy_source import FULL_RUN_IMPORT, validate


SAFE = '''import ForShor.ShorVerification.Framework.Policy
namespace PolicySubmission
def points := #[1, 2, 3]
end PolicySubmission
'''
FULL_RUN = SAFE.replace('def points', 'def submittedFullRunDesign : Nat := 0\ndef points')


class PolicySource(unittest.TestCase):
    def test_normal_policy_and_nested_documentation_are_data(self):
        validate(SAFE)
        validate('/- #eval IO.println "not code" /- axiom -/ -/\n' + SAFE)
        validate(SAFE.replace('PolicySubmission', 'PTPPolicySubmission'))
        validate('/- submittedFullRunDesign /- nested -/ -/\n-- submittedFullRunDesign\n' + SAFE)
        validate(SAFE.replace('points', 'submittedFullRunDesignExample'))

    def test_full_run_declaration_and_import_are_dedicated_only(self):
        with self.assertRaisesRegex(ValueError, 'submittedFullRunDesign'):
            validate(FULL_RUN)
        with self.assertRaisesRegex(ValueError, 'approved template modules'):
            validate(f'import {FULL_RUN_IMPORT}\n' + SAFE)
        validate(f'import {FULL_RUN_IMPORT}\n' + FULL_RUN, qualify_full_run=True)
        for source in (f'import {FULL_RUN_IMPORT}.Other\n' + FULL_RUN,
                       f'import {FULL_RUN_IMPORT}\n' + FULL_RUN + '#eval 1\n'):
            with self.assertRaises(ValueError):
                validate(source, qualify_full_run=True)

    def test_full_run_cli_flag_cannot_mark_ordinary_preflight_artifacts(self):
        command = [sys.executable, str(Path(__file__).with_name('policy_source.py')), '--stdin', '--qualify-full-run']
        source = f'import {FULL_RUN_IMPORT}\n' + FULL_RUN
        self.assertEqual(subprocess.run(command, input=source, text=True, capture_output=True).returncode, 0)
        result = subprocess.run([*command, '--artifacts', '.'], input=source, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('cannot be used for ordinary preflight artifacts', result.stderr)

    def test_build_time_execution_and_trust_overrides_are_refused(self):
        payloads = ['#eval IO.println "canary"', 'run_cmd pure ()',
                    'axiom unproved : False', 'unsafe def f := 1',
                    'def p := by native_decide', '@[implemented_by other] def p := 1',
                    'attribute [csimp] something', 'macro "x" : command => pure default',
                    'def p := by run_tac pure ()', 'set_option debug.skipKernelTC true',
                    'attribute [command_elab x] f', 'initialize x := pure ()']
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate(SAFE + payload + '\n')

    def test_comments_strings_and_escaped_names_cannot_hide_executable_tokens(self):
        for camouflage in ['def a := "/-"\n#eval IO.println "canary"\ndef b := "-/"',
                           'def a := \'"\'\n#eval IO.println "canary"',
                           'def a := r#"/-"#\n#eval IO.println "canary"',
                           'def «x/-» := 1\n#eval IO.println "canary"',
                           '@[\nextern "canary"\n] def x := 1']:
            with self.subTest(camouflage=camouflage), self.assertRaises(ValueError):
                validate(SAFE + camouflage)

    def test_extra_imports_and_continuation_lines_are_refused(self):
        for prefix in ['import Lean\n',
                       'import ForShor.OtherModule\n',
                       'import ForShor.ShorVerification.Framework.Policy ForShor.OtherModule\n',
                       'import ForShor.ShorVerification.Framework.Policy\nForShor.OtherModule\n',
                       'import\nForShor.ShorVerification.Framework.Policy\n']:
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                validate(prefix + SAFE)

    def test_ci_refusal_retains_identity_and_reports_a_rejection_without_running_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'Policy.lean'
            canary = root / 'must-not-exist'
            source.write_text(SAFE + f'\n#eval IO.FS.writeFile "{canary}" "executed"\n')
            report = root / 'table-generation-submission-result.json'
            report.write_text(json.dumps({'kind': 'policy', 'phase': 'preflight', 'status': 'success',
                                          'metadata': {'head_sha': 'a' * 40}, 'checks': []}))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('policy_source.py')), str(source),
                                     '--artifacts', str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(canary.exists())
            data = json.loads(report.read_text())
            self.assertEqual(data['metadata']['head_sha'], 'a' * 40)
            self.assertEqual(data['status'], 'failure')
            self.assertEqual(data['checks'][0]['name'], 'policy-source')

    def test_current_sparse_controls_work_with_an_older_scientific_verifier(self):
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            controls = workspace / 'controls'
            for relative in ['scripts/ci/policy_source.py', 'scripts/verifier.py', 'scripts/leaderboard.py', 'leaderboard/config.json']:
                target = controls / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repository / relative, target)
            old_verifier = workspace / 'verifier'
            old_verifier.mkdir()
            (old_verifier / 'old-scientific-input').write_text('pinned and unchanged')
            submission = workspace / 'submission'
            submission.mkdir()
            source = submission / 'Policy.lean'
            source.write_text(SAFE + '\n#eval IO.println "canary"\n')
            artifacts = submission / 'artifacts'
            artifacts.mkdir()
            report = artifacts / 'table-generation-submission-result.json'
            report.write_text(json.dumps({'kind': 'policy', 'phase': 'preflight', 'status': 'success', 'checks': []}))
            result = subprocess.run([sys.executable, 'scripts/ci/policy_source.py', '../submission/Policy.lean',
                                     '--artifacts', '../submission/artifacts'], cwd=controls, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('build-time commands', result.stderr)
            self.assertEqual(json.loads(report.read_text())['checks'][0]['name'], 'policy-source')
            self.assertEqual((old_verifier / 'old-scientific-input').read_text(), 'pinned and unchanged')


PRIM = 'def submittedPrimitive : Shor.PrimitiveSynthesis := .rossSelinger'
FULL = 'def submittedFullRunDesign : Shor.FullRunDesign := .reference'
DESIGN = 'def submittedDesign : Shor.SubmissionDesign := { readout := .coherent }'
FULL_IMPORT = f'import {FULL_RUN_IMPORT}\n'
POLICY_IMPORT = 'import ForShor.ShorVerification.Framework.Policy\n'


def source(*declarations, namespace='PolicySubmission', imports=''):
    """A header, the named namespace, the declarations at column 0, then a normal definition (a new command)."""
    body = ''.join(d + '\n' for d in declarations)
    return f'{POLICY_IMPORT}{imports}namespace {namespace}\n{body}def points := #[1, 2, 3]\nend {namespace}\n'


class ExplicitDefaults(unittest.TestCase):
    """Owner ruling 2026-11-24: Ross-Selinger, the reference full run and coherent readout are the defaults, and an
    explicit default token is identical to omission. Only those tokens: the grammar is not widened."""

    FULL_REFUSAL = 'submittedFullRunDesign requires dedicated nonpublishing full-run qualification'
    IMPORT_REFUSAL = 'imports must be the approved template modules'

    def test_explicit_reference_full_run_is_accepted_like_omission(self):
        # Fails if check_no_full_run_selector / the FULL_RUN_IMPORT allowance in validate refuse the default again.
        validate(source(FULL, imports=FULL_IMPORT))
        validate(source(FULL.replace('Shor.FullRunDesign', '_root_.Shor.FullRunDesign')
                        .replace(':= .reference', ':= Shor.FullRunDesign.reference'), imports=FULL_IMPORT))
        validate(source(FULL, DESIGN, PRIM, imports=FULL_IMPORT))

    def test_the_full_run_import_is_allowed_only_with_the_explicit_default(self):
        # Fails if the import is allowed unconditionally (an import with no declaration stays refused, as before).
        with self.assertRaisesRegex(ValueError, self.IMPORT_REFUSAL):
            validate(source(imports=FULL_IMPORT))
        with self.assertRaisesRegex(ValueError, self.FULL_REFUSAL):   # a non-default declaration is refused first, by the old message
            validate(source(FULL.replace('.reference', '.quotientExact'), imports=FULL_IMPORT))

    def test_non_default_and_unknown_full_run_tokens_are_refused_with_the_same_message(self):
        # Fails if the default recogniser accepts a token that is not exactly `.reference`.
        for value in ('.quotientExact', '.quotientCatalytic', '.splitStep4Kicks', '.Reference', '.referenceX', '.reference_',
                      '.referenc', 'Shor.FullRunDesign.quotientExact', '.reference |> id', 'default', '0'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, self.FULL_REFUSAL):
                validate(source(f'def submittedFullRunDesign : Shor.FullRunDesign := {value}', imports=FULL_IMPORT))

    def test_the_explicit_full_run_default_is_exactly_one_canonical_declaration(self):
        for label, text in {
                'wrong type': source(FULL.replace('Shor.FullRunDesign', 'Nat'), imports=FULL_IMPORT),
                'no type': source('def submittedFullRunDesign := Shor.FullRunDesign.reference', imports=FULL_IMPORT),
                'declared twice': source(FULL, FULL, imports=FULL_IMPORT),
                'decoy namespace': source(FULL, namespace='Decoy', imports=FULL_IMPORT),
                'nested namespace': source(FULL, namespace='PolicySubmission.Inner', imports=FULL_IMPORT),
                'ptp board': source(FULL, namespace='PTPPolicySubmission', imports=FULL_IMPORT),
                'private': source('private ' + FULL, imports=FULL_IMPORT),
                'protected': source('protected ' + FULL, imports=FULL_IMPORT),
                'abbrev': source(FULL.replace('def ', 'abbrev '), imports=FULL_IMPORT),
                'indented': source('  ' + FULL, imports=FULL_IMPORT),
                'attribute line before': source('@[reducible]', FULL, imports=FULL_IMPORT),
                'modifier line before': source('private', FULL, imports=FULL_IMPORT),
                'continued on the next line': source(FULL, '  |> fun _ => Shor.FullRunDesign.quotientExact', imports=FULL_IMPORT),
                'value on the next line': source(FULL.replace(':= .reference', ':=\n  .reference'), imports=FULL_IMPORT),
                'used elsewhere': source(FULL, 'def alias := submittedFullRunDesign', imports=FULL_IMPORT),
                'second use inside the policy': source(FULL, 'theorem t : submittedFullRunDesign = .reference := rfl', imports=FULL_IMPORT),
        }.items():
            with self.subTest(label), self.assertRaisesRegex(ValueError, self.FULL_REFUSAL):
                validate(text)

    def test_the_ci_preflight_command_passes_the_explicit_default_and_refuses_the_rest(self):
        # The workflow runs `policy_source.py <file> --artifacts ...` without --qualify-full-run.
        command = [sys.executable, str(Path(__file__).with_name('policy_source.py')), '--stdin']
        good = subprocess.run(command, input=source(FULL, imports=FULL_IMPORT), text=True, capture_output=True)
        self.assertEqual((good.returncode, good.stderr), (0, ''))
        bad = subprocess.run(command, input=source(FULL.replace('.reference', '.quotientExact'), imports=FULL_IMPORT),
                             text=True, capture_output=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn(self.FULL_REFUSAL, bad.stderr)

    def test_comments_do_not_make_a_declaration_or_hide_a_second_one(self):
        validate(source(FULL, '-- ' + FULL, '/- ' + FULL + ' -/', imports=FULL_IMPORT))
        with self.assertRaisesRegex(ValueError, self.FULL_REFUSAL):
            validate(source(FULL, '/- note -/ ' + FULL, imports=FULL_IMPORT))

    def test_the_dedicated_qualification_mode_is_unchanged(self):
        validate(source(FULL.replace('.reference', '.quotientExact'), imports=FULL_IMPORT), qualify_full_run=True)
        validate(source(FULL, imports=FULL_IMPORT), qualify_full_run=True)

    def test_default_declarations_are_consumed_only_in_the_right_namespace_and_board(self):
        import policy_source
        for text, tokens in ((source(PRIM, DESIGN, FULL, imports=FULL_IMPORT), {'submittedPrimitive', 'submittedDesign', 'submittedFullRunDesign'}),
                             (source(PRIM, namespace='PTPPolicySubmission'), {'submittedPrimitive'}),
                             (source(DESIGN, namespace='PTPPolicySubmission'), {'submittedDesign'}),
                             (source(FULL, namespace='PTPPolicySubmission', imports=FULL_IMPORT), set()),
                             (source(PRIM, namespace='Decoy'), set())):
            with self.subTest(tokens=tokens):
                remaining, consumed = policy_source.default_selectors(text)
                self.assertEqual(set(consumed), tokens)
                # a consumed declaration disappears; the rest of the file (and its line numbering) does not move
                self.assertEqual(remaining.count('\n'), text.count('\n'))
                for token in consumed:
                    self.assertNotIn(token, remaining)
                self.assertIn('def points', remaining)
        where = source('def submittedDesign : Shor.SubmissionDesign where\n  readout := .coherent')
        self.assertEqual(set(policy_source.default_selectors(where)[1]), {'submittedDesign'})
        semi = source('def submittedDesign : Shor.SubmissionDesign where\n  readout := .semiclassical')
        self.assertEqual(policy_source.default_selectors(semi)[1], {})
        self.assertIn('submittedDesign', policy_source.default_selectors(semi)[0])


if __name__ == '__main__':
    unittest.main()
