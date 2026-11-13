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


def plan(publisher_app='github-actions', publisher_app_id=ACTION_APP_ID):
    if not re.fullmatch(r'[a-z0-9-]+', publisher_app):
        raise ValueError('Expected a GitHub App slug')
    if isinstance(publisher_app_id, bool) or not isinstance(publisher_app_id, int) or publisher_app_id < 1:
        raise ValueError('Expected a positive GitHub App ID')
    if publisher_app != 'github-actions' and publisher_app_id == ACTION_APP_ID:
        raise ValueError('A custom publisher App needs its own --publisher-app-id')
    def ruleset(name, refs, rules, bypass=None):
        return {'name': name, 'target': 'branch', 'enforcement': 'active',
                'conditions': {'ref_name': {'include': refs, 'exclude': []}},
                'bypass_actors': bypass or [], 'rules': rules}
    return {
        'repository': REPOSITORY,
        'workflowDefaults': {'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': False},
        'publisherApp': {'slug': publisher_app, 'id': publisher_app_id},
        'rulesets': [
            # Separate from the writer bypass: even the publisher cannot delete
            # a branch or force-update its history. Admins have no bypass.
            ruleset('ForShor preserve history', ['refs/heads/main', 'refs/heads/submission-results'],
                    [{'type': 'deletion'}, {'type': 'non_fast_forward'}]),
            ruleset('ForShor main maintenance', ['refs/heads/main'], [
                {'type': 'pull_request', 'parameters': {
                    'required_approving_review_count': 0, 'dismiss_stale_reviews_on_push': False,
                    'require_code_owner_review': False, 'require_last_push_approval': False,
                    'required_review_thread_resolution': False}},
                {'type': 'required_status_checks', 'parameters': {
                    'strict_required_status_checks_policy': True,
                    'required_status_checks': [{'context': CHECK, 'integration_id': ACTION_APP_ID}]}}]),
            ruleset('ForShor archive writer', ['refs/heads/submission-results'],
                    [{'type': 'update', 'parameters': {'update_allows_fetch_and_merge': False}}],
                    [{'actor_id': publisher_app_id, 'actor_type': 'Integration', 'bypass_mode': 'always'}]),
        ],
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


def contains(actual, expected):
    """Compare declared settings while tolerating server-added metadata."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(contains(a, e) for a, e in zip(actual, expected)))
    return actual == expected


def verify_ruleset(actual, expected):
    if not contains(actual, expected):
        raise RuntimeError(f"{expected['name']}: GitHub did not enforce the declared rules and bypass actors")


def apply(publisher_app, record, publisher_app_id=ACTION_APP_ID):
    desired = plan(publisher_app, publisher_app_id)
    base = f'repos/{REPOSITORY}'
    repository = api(base)
    if repository.get('private') or repository.get('visibility') != 'public':
        raise RuntimeError('Repository is private. Release protections are prepared; this command never changes visibility.')
    if not repository.get('permissions', {}).get('admin'):
        raise RuntimeError('Repository administrator access is required')
    # The slug must identify an actual App; GitHub must also accept it as the
    # archive restriction. Never replace a rejected App with a human bypass.
    if api(f'apps/{publisher_app}').get('id') != publisher_app_id:
        raise RuntimeError('Publisher App slug and ID do not match')
    original = {'repository': REPOSITORY, 'capturedAt': datetime.now(timezone.utc).isoformat(),
                'workflowDefaults': api(base + '/actions/permissions/workflow'), 'rulesets': []}
    page = 1
    while True:
        items = api(f'{base}/rulesets?includes_parents=false&per_page=100&page={page}')
        original['rulesets'].extend(api(f"{base}/rulesets/{item['id']}") for item in items)
        if len(items) < 100:
            break
        page += 1
    record.parent.mkdir(parents=True, exist_ok=True)
    with record.open('x') as output:
        output.write(json.dumps(original, indent=2) + '\n')
    record.chmod(0o600)
    for ruleset in desired['rulesets']:
        existing = [r for r in original['rulesets'] if r['name'] == ruleset['name']]
        if len(existing) > 1:
            raise RuntimeError('Duplicate named rulesets require operator review')
        if existing:
            path = f"{base}/rulesets/{existing[0]['id']}"
            saved = api(path, 'PUT', ruleset)
        else:
            saved = api(base + '/rulesets', 'POST', ruleset)
        verify_ruleset(api(f"{base}/rulesets/{saved['id']}"), ruleset)
    path = base + '/actions/permissions/workflow'
    api(path, 'PUT', desired['workflowDefaults'])
    if api(path) != desired['workflowDefaults']:
        raise RuntimeError('Workflow defaults did not match after update')
    return {'status': 'verified', 'repository': REPOSITORY, 'previousSettings': str(record)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'apply'])
    parser.add_argument('--publisher-app', default='github-actions')
    parser.add_argument('--publisher-app-id', type=int, default=ACTION_APP_ID)
    parser.add_argument('--record', type=Path, default=Path('artifacts/security') /
                        ('previous-settings-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json'))
    args = parser.parse_args()
    try:
        result = (plan(args.publisher_app, args.publisher_app_id) if args.command == 'plan'
                  else apply(args.publisher_app, args.record, args.publisher_app_id))
        print(json.dumps(result, indent=2))
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
