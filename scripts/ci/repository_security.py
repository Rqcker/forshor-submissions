"""Plan/apply the ForShor repository's release protections, never its visibility.

Uses gh's existing authentication. No tokens or account credentials are read or
stored. The apply command is intentionally unavailable while this repository
remains private: GitHub Free cannot enforce its private branch protections.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

REPOSITORY = 'zapatacomputing/forshor-submissions'
CHECK = 'Maintenance checks'
ACTION_APP_ID = 15368


def plan(publisher_app='github-actions'):
    if not re.fullmatch(r'[a-z0-9-]+', publisher_app):
        raise ValueError('Expected a GitHub App slug')
    common = {'enforce_admins': True, 'required_linear_history': False,
              'allow_force_pushes': False, 'allow_deletions': False}
    return {
        'repository': REPOSITORY,
        'workflowDefaults': {'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': False},
        'main': {**common,
                 'required_status_checks': {'strict': True, 'checks': [{'context': CHECK, 'app_id': ACTION_APP_ID}]},
                 'required_pull_request_reviews': {'required_approving_review_count': 0,
                                                  'dismiss_stale_reviews': False,
                                                  'require_code_owner_reviews': False,
                                                  'require_last_push_approval': False},
                 'restrictions': None},
        'submission-results': {**common, 'required_status_checks': None,
                               'required_pull_request_reviews': None,
                               'restrictions': {'users': [], 'teams': [], 'apps': [publisher_app]}},
    }


def api(path, method='GET', body=None, missing_ok=False):
    command = ['gh', 'api', '--method', method, path]
    if body is not None:
        command += ['--input', '-']
    response = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                              capture_output=True, text=True)
    if response.returncode:
        try:
            status = json.loads(response.stdout).get('status')
        except ValueError:
            status = None
        if missing_ok and status in (404, '404'):
            return None
        raise RuntimeError(f'GitHub {method} {path} failed: {response.stderr.strip()[-800:]}')
    return json.loads(response.stdout) if response.stdout.strip() else None


def verify_branch(actual, branch, publisher_app):
    if not actual or not actual.get('enforce_admins', {}).get('enabled'):
        raise RuntimeError(f'{branch}: administrator enforcement is missing')
    for name in ('allow_force_pushes', 'allow_deletions'):
        if actual.get(name, {}).get('enabled') is not False:
            raise RuntimeError(f'{branch}: {name} must be disabled')
    if branch == 'main':
        reviews = actual.get('required_pull_request_reviews')
        if (reviews is None or reviews.get('required_approving_review_count') != 0
                or reviews.get('require_code_owner_reviews') or reviews.get('require_last_push_approval')):
            raise RuntimeError('main: require a PR with zero required approvals')
        checks = actual.get('required_status_checks') or {}
        if not checks.get('strict') or {'context': CHECK, 'app_id': ACTION_APP_ID} not in checks.get('checks', []):
            raise RuntimeError('main: the current Maintenance checks must pass')
    else:
        restrictions = actual.get('restrictions') or {}
        if (restrictions.get('users') or restrictions.get('teams')
                or [app['slug'] for app in restrictions.get('apps', [])] != [publisher_app]):
            raise RuntimeError('submission-results: only the publisher App may push')


def apply(publisher_app, record):
    desired = plan(publisher_app)
    base = f'repos/{REPOSITORY}'
    repository = api(base)
    if repository.get('private') or repository.get('visibility') != 'public':
        raise RuntimeError('Repository is private. Release protections are prepared; this command never changes visibility.')
    if not repository.get('permissions', {}).get('admin'):
        raise RuntimeError('Repository administrator access is required')
    # The slug must identify an actual App; GitHub must also accept it as the
    # archive restriction. Never replace a rejected App with a human bypass.
    api(f'apps/{publisher_app}')
    original = {'repository': REPOSITORY, 'capturedAt': datetime.now(timezone.utc).isoformat(),
                'workflowDefaults': api(base + '/actions/permissions/workflow')}
    for branch in ('main', 'submission-results'):
        original[branch] = api(f'{base}/branches/{branch}/protection', missing_ok=True)
    record.parent.mkdir(parents=True, exist_ok=True)
    with record.open('x') as output:
        output.write(json.dumps(original, indent=2) + '\n')
    record.chmod(0o600)
    for branch in ('submission-results', 'main'):
        path = f'{base}/branches/{branch}/protection'
        api(path, 'PUT', desired[branch])
        verify_branch(api(path), branch, publisher_app)
    path = base + '/actions/permissions/workflow'
    api(path, 'PUT', desired['workflowDefaults'])
    if api(path) != desired['workflowDefaults']:
        raise RuntimeError('Workflow defaults did not match after update')
    return {'status': 'verified', 'repository': REPOSITORY, 'previousSettings': str(record)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'apply'])
    parser.add_argument('--publisher-app', default='github-actions')
    parser.add_argument('--record', type=Path, default=Path('artifacts/security') /
                        ('previous-settings-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json'))
    args = parser.parse_args()
    try:
        result = plan(args.publisher_app) if args.command == 'plan' else apply(args.publisher_app, args.record)
        print(json.dumps(result, indent=2))
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
