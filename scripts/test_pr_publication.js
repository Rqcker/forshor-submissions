'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const lifecycle = require('./ci/pr_publication.js');
const HEAD = 'a'.repeat(40), OTHER = 'b'.repeat(40), KEY = 'c'.repeat(64);
const REPO = 'zapatacomputing/forshor-submissions';

async function fixture(fn) {
  const root = process.cwd(), directory = fs.mkdtempSync(path.join(os.tmpdir(), 'forshor-pr-check-'));
  process.chdir(directory);
  const calls = [], errors = [], checks = [], comments = [], delays = [];
  const context = { repo: { owner: 'zapatacomputing', repo: 'forshor-submissions' }, runId: 456 };
  const source = { run: { id: 123, run_attempt: 1, conclusion: 'success' }, pr: { number: 12, headSha: HEAD } };
  let pr = { number: 12, user: { login: 'contributor' }, head: { sha: HEAD }, base: { ref: 'main', repo: { full_name: REPO } }, state: 'open', merged: false };
  const github = { paginate: async (method, params) => method(params), rest: {
    checks: {
      listForRef: async () => checks,
      create: async args => { calls.push(['create-check', args]); const c = { ...args, id: 7, app: { slug: 'github-actions' } }; checks.push(c); return { data: c }; },
      update: async args => { calls.push(['update-check', args]); Object.assign(checks[0], args); return { data: checks[0] }; },
      get: async () => ({ data: checks[0] }),
    },
    pulls: {
      get: async () => ({ data: structuredClone(pr) }),
      update: async args => { calls.push(['update-pr', args]); pr.state = args.state; return { data: structuredClone(pr) }; },
    },
    issues: {
      listComments: async () => comments,
      createComment: async args => { calls.push(['comment', args]); comments.push({ ...args, id: 9, user: { login: 'github-actions[bot]' } }); },
      updateComment: async args => { calls.push(['update-comment', args]); comments[0].body = args.body; },
    },
  } };
  const core = { setFailed: message => errors.push(message) };
  const write = (file, value) => fs.writeFileSync(file, JSON.stringify(value));
  write('publication-source.json', source);
  write('evaluation-metadata.json', { repository: REPO, headSha: HEAD, runId: '123', evaluationKey: KEY,
    pullRequestUrl: `https://github.com/${REPO}/pull/12` });
  write('publication-result.json', { snapshotId: 'd'.repeat(64), updatedAt: '2026-10-25T02:37:59.063Z' });
  const begin = () => lifecycle.begin({ github, context, ...source });
  const finish = (outcome = 'success') => lifecycle.finalize({ github, context, core, outcome,
    waitForRetry: async milliseconds => { delays.push(milliseconds); } });
  try { await fn({ github, context, source, checks, comments, calls, errors, delays, write, begin, finish,
    get pr() { return pr; }, set pr(value) { pr = value; } }); }
  finally { process.chdir(root); fs.rmSync(directory, { recursive: true, force: true }); }
}

test('one check on the exact source SHA; publication receipt precedes comment, close and success', () => fixture(async f => {
  await f.begin();
  assert.equal(f.checks[0].head_sha, HEAD);
  assert.equal(f.checks[0].status, 'in_progress');
  await f.finish();
  assert.deepEqual(f.errors, []);
  assert.equal(f.checks[0].conclusion, 'success');
  assert.equal(f.pr.state, 'closed');
  assert.match(f.comments[0].body, /Submission accepted and published/);
  assert.match(f.comments[0].body, /results\/snapshots\/d{64}/);
  assert.deepEqual(f.calls.slice(-3).map(c => c[0]), ['comment', 'update-pr', 'update-check']);
}));

test('a failed Lean attempt skips Qualtran and leaves the PR open', () => fixture(async f => {
  f.source.run.conclusion = 'failure';
  f.write('publication-source.json', f.source);
  await f.begin(); await f.finish();
  assert.equal(f.checks[0].conclusion, 'skipped');
  assert.equal(f.pr.state, 'open'); assert.equal(f.comments.length, 0);
}));

test('a newly created check returning 404 is retried before reporting a skipped publication', () => fixture(async f => {
  f.source.run.conclusion = 'failure';
  f.write('publication-source.json', f.source);
  await f.begin();
  const get = f.github.rest.checks.get, lookups = [];
  f.github.rest.checks.get = async args => {
    lookups.push(args);
    if (lookups.length < 3) throw Object.assign(new Error('Not Found'), {status: 404});
    return get(args);
  };
  await f.finish();
  assert.deepEqual(f.delays, [1000, 2000]);
  assert.equal(lookups.length, 3);
  assert(lookups.every(args => args.check_run_id === f.checks[0].id));
  assert.equal(f.checks[0].conclusion, 'skipped');
  assert.equal(f.pr.state, 'open'); assert.equal(f.comments.length, 0);
}));

test('a transient check lookup failure does not prevent verified publication reporting', () => fixture(async f => {
  await f.begin();
  const get = f.github.rest.checks.get;
  let attempts = 0;
  f.github.rest.checks.get = async args => {
    if (++attempts === 1) throw Object.assign(new Error('Not Found'), {status: 404});
    return get(args);
  };
  await f.finish();
  assert.equal(attempts, 2); assert.deepEqual(f.delays, [1000]);
  assert.equal(f.checks[0].conclusion, 'success'); assert.equal(f.pr.state, 'closed');
  assert.equal(f.comments.length, 1); assert.deepEqual(f.errors, []);
}));

test('permanent check lookup failure is bounded and cannot close or accept the PR', () => fixture(async f => {
  await f.begin();
  let attempts = 0;
  f.github.rest.checks.get = async () => { attempts++; throw Object.assign(new Error('Not Found'), {status: 404}); };
  await assert.rejects(f.finish(), error => error.status === 404);
  assert.equal(attempts, 4); assert.deepEqual(f.delays, [1000, 2000, 4000]);
  assert.equal(f.pr.state, 'open'); assert.equal(f.comments.length, 0);
  assert.equal(f.calls.filter(call => call[0] === 'update-check').length, 0);
}));

test('check lookup permission failures are not retried or treated as acceptance', () => fixture(async f => {
  await f.begin();
  f.github.rest.checks.get = async () => { throw Object.assign(new Error('Forbidden'), {status: 403}); };
  await assert.rejects(f.finish(), error => error.status === 403);
  assert.deepEqual(f.delays, []); assert.equal(f.pr.state, 'open'); assert.equal(f.comments.length, 0);
}));

test('readback retry preserves the guard against a newer publisher owning the check', () => fixture(async f => {
  await f.begin();
  const before = f.calls.length, get = f.github.rest.checks.get;
  let attempts = 0;
  f.github.rest.checks.get = async args => {
    if (++attempts === 1) throw Object.assign(new Error('Not Found'), {status: 404});
    f.checks[0].external_id += '-new-attempt';
    return get(args);
  };
  await f.finish();
  assert.equal(attempts, 2); assert.deepEqual(f.delays, [1000]);
  assert.equal(f.calls.length, before); assert.equal(f.pr.state, 'open'); assert.equal(f.comments.length, 0);
}));

for (const outcome of ['failure', 'cancelled']) test(`${outcome} cannot close or claim publication even if a receipt exists`, () => fixture(async f => {
  await f.begin(); await f.finish(outcome);
  assert.equal(f.checks[0].conclusion, outcome);
  assert.equal(f.pr.state, 'open');
  // A publisher failure after Lean passed tells the author it is not their policy's fault (and never claims acceptance); a cancellation is silent.
  if (outcome === 'failure') {
    assert.equal(f.comments.length, 1);
    assert.match(f.comments[0].body, /### Verification could not complete/); assert.match(f.comments[0].body, /@contributor, /);
    assert.match(f.comments[0].body, /not a verdict on your policy/); assert.doesNotMatch(f.comments[0].body, /accepted|Submission accepted and published/);
  } else assert.equal(f.comments.length, 0);
}));

test('a publisher failure notice is posted once, in place on a retry, and a failure to post never changes the check', () => fixture(async f => {
  await f.begin(); await f.finish('failure'); await f.begin(); await f.finish('failure');
  assert.equal(f.comments.length, 1); assert.equal(f.calls.filter(c => c[0] === 'update-comment').length, 0, 'an identical notice is left alone');
  assert.equal(f.checks[0].conclusion, 'failure');
  await fixture(async g => {
    g.github.rest.issues.createComment = async () => { throw new Error('GitHub is down'); };
    await g.begin(); await g.finish('failure');
    assert.equal(g.checks[0].conclusion, 'failure'); assert.deepEqual(g.errors, []); assert.equal(g.pr.state, 'open');
  });
}));

const REPEAT = { policyId: 'e'.repeat(40), login: 'contributor', pull: 41, createdAt: '2026-11-10T01:38:00Z', sameParticipant: true };
const refusal = duplicateOf => ({ refused: true, stage: 'duplicate', reason: 'x', duplicateOf });

test('a policy the author already published: a clear comment, once, no "not your policy" text, a failed check, the PR stays open', () => fixture(async f => {
  f.write('publication-result.json', refusal(REPEAT));
  await f.begin(); await f.finish('failure'); await f.begin(); await f.finish('failure');
  assert.equal(f.comments.length, 1, 'one comment, found again by its marker');
  const body = f.comments[0].body;
  assert.match(body, /^<!-- forshor-notify:a{40}:duplicate -->\n### Not published: you already submitted this policy/);
  assert.match(body, /@contributor, /); assert.match(body, /pull request #41/); assert.match(body, /2026-11-10/); assert.match(body, /eeeeeee/);
  assert.doesNotMatch(body, /not a verdict|not your policy|re-run/i);
  assert.equal(f.checks[0].conclusion, 'failure');
  assert.equal(f.checks[0].output.title, 'Not published: you already submitted this policy');
  assert.equal(f.pr.state, 'open'); assert.deepEqual(f.errors, []);
}));

test('a repeat that is not the author\'s own is described without any suggestion that the author did it twice', () => fixture(async f => {
  f.write('publication-result.json', refusal({ ...REPEAT, login: 'someone', sameParticipant: false }));
  await f.begin(); await f.finish('failure');
  assert.match(f.comments[0].body, /^<!-- forshor-notify:a{40}:duplicate -->\n### Not published: this policy is already published/);
  assert.doesNotMatch(f.comments[0].body, /you already submitted/);
}));

test('a hostile duplicate refusal puts nothing of its text in a comment and falls back to the publication-incomplete notice', () => fixture(async f => {
  for (const bad of [{ ...REPEAT, policyId: 'x' }, { ...REPEAT, pull: 1e99 }, { ...REPEAT, pull: -3 }, { ...REPEAT, createdAt: '[x](http://e) @victim' }, null, 'text']) {
    f.comments.length = 0;
    f.write('publication-result.json', refusal(bad));
    await f.begin(); await f.finish('failure');
    assert.doesNotMatch(f.comments[0]?.body ?? '', /already submitted|already published|http:\/\/e|@victim/, JSON.stringify(bad));
    assert.match(f.comments[0].body, /Verification could not complete/);
  }
}));

test('a failed publication with no duplicate refusal behaves as before', () => fixture(async f => {
  f.write('publication-result.json', { refused: true, stage: 'mirror', reason: 'x' });
  await f.begin(); await f.finish('failure');
  assert.match(f.comments[0].body, /Verification could not complete/);
  fs.rmSync('publication-result.json'); f.comments.length = 0;
  await f.begin(); await f.finish('failure');
  assert.match(f.comments[0].body, /Verification could not complete/);
}));

test('the accepted comment mentions the author, and only a plain login', () => fixture(async f => {
  await f.begin(); await f.finish();
  assert.match(f.comments[0].body, /^<!-- forshor-publication:a{40} -->\n### Submission accepted and published\n\n@contributor, Lean verification passed/);
  for (const user of [null, { login: 'dependabot[bot]' }, { login: 'x\n@victim' }]) await fixture(async g => {
    g.pr = { ...g.pr, user }; await g.begin(); await g.finish();
    assert.ok(!/@(?!\u200b)[A-Za-z]/.test(g.comments[0].body.replace(/```[\s\S]*?```/g, '')), JSON.stringify(user));   // a fenced citation holds `@misc`, which is not a mention
    assert.match(g.comments[0].body, /Submission accepted and published/);
  });
}));

for (const result of [null, {}, { snapshotId: 'd'.repeat(64) }, { alreadyPublished: true, evaluationKey: 'forged' }]) {
  test(`missing or forged publication receipt cannot close a PR: ${JSON.stringify(result)}`, () => fixture(async f => {
    if (result === null) fs.unlinkSync('publication-result.json');
    else f.write('publication-result.json', result);
    await f.begin(); await f.finish();
    assert.equal(f.checks[0].conclusion, 'failure'); assert.equal(f.pr.state, 'open');
    assert.equal(f.comments.length, 0); assert.equal(f.errors.length, 1);
  }));
}

test('publisher retry reuses its check/comment and does not reclose a closed accepted PR', () => fixture(async f => {
  await f.begin(); await f.finish();
  f.write('publication-result.json', { alreadyPublished: true, evaluationKey: KEY });
  await f.begin(); await f.finish();
  assert.equal(f.checks.length, 1); assert.equal(f.comments.length, 1);
  assert.equal(f.calls.filter(c => c[0] === 'update-pr').length, 1);
  assert.equal(f.calls.filter(c => c[0] === 'update-comment').length, 1);
  assert.deepEqual(f.errors, []); assert.equal(f.checks[0].conclusion, 'success');
}));

test('lost-acknowledgement recovery accepts the Google publisher snapshot receipt', () => fixture(async f => {
  f.write('publication-result.json', { alreadyPublished: true, snapshotId: 'd'.repeat(64), updatedAt: '2026-10-25T02:37:59.063Z' });
  await f.begin(); await f.finish();
  assert.deepEqual(f.errors, []); assert.equal(f.checks[0].conclusion, 'success');
  assert.equal(f.pr.state, 'closed');
}));

test('a failed final check update reopens a PR closed by this attempt', () => fixture(async f => {
  await f.begin();
  const update = f.github.rest.checks.update;
  f.github.rest.checks.update = async args => {
    if (args.conclusion === 'success') throw new Error('Final check reporting interrupted');
    return update(args);
  };
  await f.finish();
  assert.equal(f.pr.state, 'open'); assert.equal(f.checks[0].conclusion, 'failure');
  assert.deepEqual(f.errors, ['Final check reporting interrupted']);
}));

test('superseded source never closes a newer PR head', () => fixture(async f => {
  await f.begin(); f.pr.head.sha = OTHER; await f.finish();
  assert.equal(f.pr.state, 'open'); assert.equal(f.checks[0].conclusion, 'cancelled');
  assert.equal(f.comments.length, 0);
}));

test('a push while adding the result comment prevents PR closure', () => fixture(async f => {
  await f.begin();
  f.github.rest.issues.createComment = async () => { f.pr.head.sha = OTHER; };
  await f.finish();
  assert.equal(f.pr.state, 'open'); assert.equal(f.checks[0].conclusion, 'cancelled');
  assert.equal(f.calls.filter(c => c[0] === 'update-pr').length, 0);
}));

test('a push racing the close endpoint reopens the PR for the newer commit', () => fixture(async f => {
  await f.begin();
  const update = f.github.rest.pulls.update;
  f.github.rest.pulls.update = async args => { if (args.state === 'closed') f.pr.head.sha = OTHER; return update(args); };
  await f.finish();
  assert.equal(f.pr.state, 'open'); assert.equal(f.checks[0].conclusion, 'cancelled');
  assert.deepEqual(f.calls.filter(c => c[0] === 'update-pr').map(c => c[1].state), ['closed', 'open']);
}));

for (const endpoint of ['comment', 'close']) test(`${endpoint} API failure produces a red recoverable check and leaves PR open`, () => fixture(async f => {
  await f.begin();
  const fail = async () => { throw new Error('Transient GitHub API failure'); };
  if (endpoint === 'comment') f.github.rest.issues.createComment = fail;
  else f.github.rest.pulls.update = fail;
  await f.finish();
  assert.equal(f.pr.state, 'open'); assert.equal(f.checks[0].conclusion, 'failure');
  assert.deepEqual(f.errors, ['Transient GitHub API failure']);
}));

test('an old finalizer cannot overwrite a newer publisher retry', () => fixture(async f => {
  await f.begin(); f.checks[0].external_id += '-new-attempt';
  const before = f.calls.length; await f.finish();
  assert.equal(f.calls.length, before); assert.equal(f.pr.state, 'open');
}));

test('GitHub Actions canonicalizes details_url without changing trusted check ownership', () => fixture(async f => {
  await f.begin();
  f.checks[0].details_url = `https://github.com/${REPO}/runs/${f.checks[0].id}`;
  await f.finish();
  assert.deepEqual(f.errors, []); assert.equal(f.checks[0].conclusion, 'success');
  assert.equal(f.pr.state, 'closed');
}));

test('recovery upgrades a pending check created before publisher ownership was embedded', () => fixture(async f => {
  await f.begin(); f.checks[0].external_id = 'forshor-publication:123:1';
  f.checks[0].name = 'Qualtran estimation and publication';
  await f.begin(); await f.finish();
  assert.equal(f.checks.length, 1); assert.equal(f.checks[0].conclusion, 'success');
  assert.equal(f.checks[0].name, lifecycle.NAME);
  assert.equal(f.checks[0].external_id, `forshor-publication:123:1:456:${Number(process.env.GITHUB_RUN_ATTEMPT || 1)}`);
}));

test('mismatched trusted source or metadata cannot close the PR', () => fixture(async f => {
  await f.begin(); f.source.pr.number = 99; f.write('publication-source.json', f.source);
  await f.finish();
  assert.equal(f.pr.state, 'open'); assert.equal(f.checks[0].conclusion, 'failure');
  assert.match(f.errors[0], /identity changed/);
}));

test('no trusted check means finalization performs no API operations', () => fixture(async f => {
  await f.finish(); assert.deepEqual(f.calls, []);
}));

for (const forged of [false, true]) test(`v2 policy receipt ${forged ? 'rejects another source' : 'links the policy archive and snapshot'}`, () => fixture(async f => {
  f.write('evaluation-metadata.json', { kind: 'policy', repository: REPO, headSha: HEAD, runId: '123', evaluationKey: KEY,
    pullRequestUrl: `https://github.com/${REPO}/pull/12`, githubLogin: 'contributor', policyDigest: KEY, epoch: '7a11b2b8b6d68718' });
  f.write('publication-result.json', { schema: 'forshor.publication/v2', kind: 'policy', snapshotId: 'd'.repeat(64),
    updatedAt: '2026-11-09T00:00:00Z', name: 'contributor_sub1',
    policy: { headSha: forged ? OTHER : HEAD, login: 'contributor', pull: 12, digest: KEY } });
  await f.begin(); await f.finish();
  if (forged) {
    assert.equal(f.pr.state, 'open'); assert.equal(f.checks[0].conclusion, 'failure');
    assert.equal(f.comments.length, 0);
  } else {
    assert.equal(f.pr.state, 'closed'); assert.equal(f.checks[0].conclusion, 'success');
    assert.match(f.comments[0].body, /results\/policy-submissions\/a{40}/);
    assert.match(f.comments[0].body, /results\/v2\/e\/7a11b2b8b6d68718\/s\/d{64}\/dashboard.json/);
    // the submitter is sent the hash and a citation for their own submission, in the same comment
    assert.match(f.comments[0].body, /\n### Cite this submission\n/);
    assert.ok(f.comments[0].body.includes(`Policy digest (sha256): \`${KEY}\``));
    assert.ok(f.comments[0].body.includes(`Submission commit: \`${HEAD}\``));
    assert.match(f.comments[0].body, /```bibtex\n@misc\{forshor_[a-f0-9]{12},/);
    assert.match(f.comments[0].body, /contributor_sub1/);
  }
}));
