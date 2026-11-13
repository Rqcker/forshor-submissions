import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from repository_security import ACTION_APP_ID, apply, plan, verify_ruleset


class RepositorySecurity(unittest.TestCase):
    def test_private_repository_is_never_made_public_or_mutated(self):
        calls = []
        def api(path, method='GET', *args, **kwargs):
            calls.append((method, path))
            return {'private': True, 'visibility': 'private', 'permissions': {'admin': True}}
        with tempfile.TemporaryDirectory() as directory, patch('repository_security.api', side_effect=api):
            record = Path(directory) / 'previous.json'
            with self.assertRaisesRegex(RuntimeError, 'never changes visibility'):
                apply('github-actions', record)
            self.assertFalse(record.exists())
        self.assertEqual(calls, [('GET', 'repos/zapatacomputing/forshor-submissions')])

    def test_main_requires_checks_and_a_pr_but_no_second_maintainer(self):
        expected = plan()['rulesets'][1]
        verify_ruleset({**expected, 'id': 123, 'source_type': 'Repository'}, expected)
        for parameter, value in [('required_approving_review_count', 1), ('require_last_push_approval', True),
                                 ('require_code_owner_review', True)]:
            actual = copy.deepcopy(expected)
            actual['rules'][0]['parameters'][parameter] = value
            with self.assertRaises(RuntimeError):
                verify_ruleset(actual, expected)
        actual = copy.deepcopy(expected)
        actual['rules'].pop()
        with self.assertRaises(RuntimeError):
            verify_ruleset(actual, expected)

    def test_administrator_and_human_archive_bypasses_are_refused(self):
        for expected in plan()['rulesets']:
            for actor in [{'actor_type': 'OrganizationAdmin', 'bypass_mode': 'always'},
                          {'actor_type': 'User', 'actor_id': 123, 'bypass_mode': 'always'},
                          {'actor_type': 'RepositoryRole', 'actor_id': 5, 'bypass_mode': 'always'}]:
                actual = copy.deepcopy(expected)
                actual['bypass_actors'].append(actor)
                with self.assertRaises(RuntimeError):
                    verify_ruleset(actual, expected)

    def test_history_protection_applies_even_to_the_publisher(self):
        history, _, writer = plan()['rulesets']
        self.assertEqual(history['bypass_actors'], [])
        self.assertEqual({r['type'] for r in history['rules']}, {'deletion', 'non_fast_forward'})
        self.assertEqual(history['conditions']['ref_name']['include'], ['refs/heads/main', 'refs/heads/submission-results'])
        self.assertEqual(writer['bypass_actors'], [{'actor_id': ACTION_APP_ID, 'actor_type': 'Integration', 'bypass_mode': 'always'}])
        weakened = copy.deepcopy(history)
        weakened['bypass_actors'] = writer['bypass_actors']
        with self.assertRaises(RuntimeError):
            verify_ruleset(weakened, history)

    def test_disabled_or_wrong_branch_rules_are_refused(self):
        for expected in plan()['rulesets']:
            for key, value in [('enforcement', 'disabled'), ('conditions', {'ref_name': {'include': ['refs/heads/other'], 'exclude': []}})]:
                actual = copy.deepcopy(expected)
                actual[key] = value
                with self.assertRaises(RuntimeError):
                    verify_ruleset(actual, expected)

    def test_rejected_publisher_app_stops_without_granting_human_access(self):
        mutations = []
        saved = {}
        def api(path, method='GET', body=None, **kwargs):
            if method in ('PUT', 'POST'):
                mutations.append(body)
                if body['name'] == 'ForShor archive writer':
                    raise RuntimeError('App restriction was rejected')
                saved[1] = {**body, 'id': 1}
                return saved[1]
            if path == 'repos/zapatacomputing/forshor-submissions':
                return {'private': False, 'visibility': 'public', 'permissions': {'admin': True}}
            if path.startswith('apps/'):
                return {'id': ACTION_APP_ID}
            if '/rulesets?' in path:
                return []
            if path.endswith('/rulesets/1'):
                return saved[1]
            return {'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': False}
        with tempfile.TemporaryDirectory() as directory, patch('repository_security.api', side_effect=api):
            record = Path(directory) / 'previous.json'
            with self.assertRaisesRegex(RuntimeError, 'App restriction was rejected'):
                apply('github-actions', record)
            self.assertTrue(record.is_file(), 'Previous settings survive a failed activation')
        self.assertEqual(mutations[-1]['name'], 'ForShor archive writer')
        self.assertFalse(any(a['actor_type'] != 'Integration' for x in mutations for a in x['bypass_actors']))


if __name__ == '__main__':
    unittest.main()
