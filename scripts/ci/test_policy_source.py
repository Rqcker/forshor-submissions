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


if __name__ == '__main__':
    unittest.main()
