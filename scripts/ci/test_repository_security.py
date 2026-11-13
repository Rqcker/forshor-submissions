import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from repository_security import ACTION_APP_ID, CHECK, apply, plan, verify_branch


def actual(branch):
    desired = plan()[branch]
    result = copy.deepcopy(desired)
    result['enforce_admins'] = {'enabled': True}
    for name in ('allow_force_pushes', 'allow_deletions'):
        result[name] = {'enabled': False}
    if branch == 'submission-results':
        result['restrictions']['apps'] = [{'slug': 'github-actions'}]
    return result


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

    def test_main_can_be_self_merged_after_a_pr_and_checks(self):
        verify_branch(actual('main'), 'main', 'github-actions')
        branch = actual('main')
        branch['required_pull_request_reviews'] = None
        with self.assertRaisesRegex(RuntimeError, 'require a PR'):
            verify_branch(branch, 'main', 'github-actions')

    def test_missing_or_wrong_app_check_cannot_satisfy_protection(self):
        for checks in ([], [{'context': CHECK, 'app_id': 999}], [{'context': 'unrelated', 'app_id': ACTION_APP_ID}]):
            branch = actual('main')
            branch['required_status_checks']['checks'] = checks
            with self.assertRaisesRegex(RuntimeError, 'checks must pass'):
                verify_branch(branch, 'main', 'github-actions')

    def test_human_or_team_archive_bypasses_are_refused(self):
        verify_branch(actual('submission-results'), 'submission-results', 'github-actions')
        for key in ('users', 'teams', 'apps'):
            branch = actual('submission-results')
            branch['restrictions'][key].append({'slug': 'another-actor'})
            with self.assertRaisesRegex(RuntimeError, 'only the publisher'):
                verify_branch(branch, 'submission-results', 'github-actions')

    def test_force_push_delete_and_admin_bypasses_are_refused(self):
        for branch in ('main', 'submission-results'):
            for key in ('allow_force_pushes', 'allow_deletions', 'enforce_admins'):
                body = actual(branch)
                body[key]['enabled'] = key != 'enforce_admins'
                with self.assertRaises(RuntimeError):
                    verify_branch(body, branch, 'github-actions')

    def test_rejected_publisher_app_stops_without_granting_human_access(self):
        mutations = []
        def api(path, method='GET', body=None, **kwargs):
            if method == 'PUT':
                mutations.append((path, body))
                raise RuntimeError('App restriction was rejected')
            if path == 'repos/zapatacomputing/forshor-submissions':
                return {'private': False, 'visibility': 'public', 'permissions': {'admin': True}}
            return {'enabled': True}
        with tempfile.TemporaryDirectory() as directory, patch('repository_security.api', side_effect=api):
            record = Path(directory) / 'previous.json'
            with self.assertRaisesRegex(RuntimeError, 'App restriction was rejected'):
                apply('github-actions', record)
            self.assertTrue(record.is_file(), 'Previous settings survive a failed activation')
        self.assertEqual(len(mutations), 1)
        self.assertTrue(mutations[0][0].endswith('/submission-results/protection'))
        self.assertEqual(mutations[0][1]['restrictions']['users'], [])


if __name__ == '__main__':
    unittest.main()
