'use strict';

// Trusted-main code only. Tells a submission's author how their verification ended.
//
// Accepted submissions are reported by pr_publication.js after publication. This module reports everything else that
// ends a verification: a REJECTED policy (the verifier gave a verdict), and a run that COULD NOT COMPLETE (a tool or the
// runner failed, a limit was reached, the run was cancelled: not a verdict on the policy).
//
// Trust: the pull request, its head commit and its author come from the GitHub API and the workflow_run event, never from
// the uploaded artifact. The artifact is DATA: it is parsed as JSON, only `status`, `kind` and the `checks` names and
// details are read, and every piece of its text that reaches a comment is neutralised (no mention, no cross-reference, no
// link, no HTML, no markdown) and shown in a fenced block. Only the author's login, validated against GitHub's login
// syntax, is ever mentioned.

const fs = require('node:fs');
const reporting = require('./table_generation_report.js');

const REPOSITORY = 'zapatacomputing/forshor-submissions';
const SOURCE_WORKFLOW = '.github/workflows/table-generation-submission.yml';
const BOT = 'github-actions[bot]';
const SOURCE_FILE = 'notify-source.json';
const RESULT_FILE = 'artifacts/table-generation-submission-result.json';
const MAX_RESULT_BYTES = 1000000;
const MAX_REASON_CHARS = 1500;
const MAX_REASON_LINES = 25;
// The conclusions of a verification run that this workflow reports. `success` belongs to the publisher; `skipped` and
// `action_required` are not an outcome of the submission.
const REPORTED = new Set(['failure', 'timed_out', 'cancelled', 'startup_failure']);
const LOGIN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const SHA = /^[a-f0-9]{40}$/;
const CHANGED_FILES_VERDICT = 'only TableGeneration/Submission/Policy.lean may change';

// Checks whose failure says nothing about the policy: the environment or a tool failed.
const NOT_A_VERDICT = new Set(['trees', 'verification', 'verifier exception']);
// Mirror/intake stages that are a limit or an infrastructure fault, not a mathematical rejection.
const LIMIT_STAGES = new Set(['timeout', 'harness', 'killed', 'epoch', 'publisher']);

// What each check or stage means, in plain words. Anything not listed is shown by its own (sanitised) name.
const STAGES = {
  'changed files': 'The pull request changes files other than the submission file. A submission may change only TableGeneration/Submission/Policy.lean.',
  'changed files are allowed': 'The pull request changes files other than the submission file.',
  format: 'The file is not a valid ForShor policy submission (its format, size, widths or arity).',
  lexical: "The file was refused by ForShor's lexical gate.",
  forshor: "ForShor's Lean build or policy check did not pass.",
  parse: "The policy could not be read by the STO mirror.",
  data: "The STO mirror could not build the policy's data.",
  check: "A band does not meet STO's table contract.",
  threshold: 'A threshold is too low: a band must shrink the width wherever it is used.',
  widths: 'A declared width is below the exact width.',
  cert: "The Lean kernel certificate did not build: a table, a threshold or the ladder is not valid.",
  replay: "The trusted kernel replay did not accept the certificate.",
  audit: 'The axiom or statement audit did not pass.',
  mirror: "The STO mirror's evidence does not hold for this policy.",
  'submission syntax gate': 'The file was refused by the submission syntax gate.',
  'axiom dependencies': 'The submission depends on an axiom that is not allowed.',
  'target metrics': 'The submission did not meet the metrics check.',
};

const sha7 = value => String(value).slice(0, 7);

/** The author's login as a mention, or null when it is not a plain GitHub user login (a bot, an empty or odd value). */
function mention(login) {
  return typeof login === 'string' && LOGIN.test(login) ? `@${login}` : null;
}

/** Untrusted text made safe to show inside a fenced block: no backticks to break the fence, no mention, no control or bidi characters, bounded. */
function neutralize(text, { chars = MAX_REASON_CHARS, lines = MAX_REASON_LINES } = {}) {
  let value = String(text ?? '')
    .replace(/\r\n?/g, '\n')
    .replace(/\u001b\[[0-9;?]*[ -\/]*[@-~]/g, '')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u2028\u2029\u202a-\u202e\u2066-\u2069]/g, '')
    .replace(/`/g, "'")
    .replace(/@/g, '@\u200b');
  let cut = false;
  const rows = value.split('\n');
  if (rows.length > lines) { value = rows.slice(0, lines).join('\n'); cut = true; }
  if (value.length > chars) { value = value.slice(0, chars); cut = true; }
  value = value.trim();
  return cut ? `${value}\n[shortened]` : value;
}

/** A check or stage name from the artifact, reduced to a short plain label (anything else becomes "unknown"). */
function cleanName(name) {
  return typeof name === 'string' && /^[A-Za-z0-9][A-Za-z0-9 _.:-]{0,40}$/.test(name) ? name : 'unknown';
}

/**
 * How a verification that did not succeed ended. `conclusion` comes from the workflow_run event; `result` is the parsed artifact (or null).
 * A verdict (`rejected`) needs the run to have failed AND the verifier's own result to say which check failed; everything else is `incomplete`.
 */
function classify({ conclusion, result }) {
  if (conclusion === 'cancelled') return { outcome: 'incomplete', cause: 'cancelled' };
  if (conclusion === 'timed_out') return { outcome: 'incomplete', cause: 'timed_out' };
  if (conclusion === 'startup_failure') return { outcome: 'incomplete', cause: 'startup' };
  if (!result || typeof result !== 'object' || result.status !== 'failure' || !Array.isArray(result.checks)) {
    return { outcome: 'incomplete', cause: 'no_result' };
  }
  const failed = result.checks.filter(c => c && typeof c === 'object' && c.status !== 'success');
  if (!failed.length) return { outcome: 'incomplete', cause: 'no_result' };
  const stage = cleanName(failed[0].name);
  const details = String(failed[0].details ?? '');
  if (NOT_A_VERDICT.has(stage)) return { outcome: 'incomplete', cause: 'tool', stage, reason: details };
  if (stage === 'changed files' && !details.startsWith(CHANGED_FILES_VERDICT)) return { outcome: 'incomplete', cause: 'tool', stage, reason: details };
  if (LIMIT_STAGES.has(stage)) return { outcome: 'incomplete', cause: 'limits', stage, reason: details };
  return { outcome: 'rejected', stage, reason: details };
}

const CAUSES = {
  cancelled: 'The run was cancelled before it finished.',
  timed_out: 'The run ran out of time before it finished.',
  startup: 'The run could not start on the runner.',
  no_result: 'The run ended without a result the checker can read.',
  tool: 'A tool or the runner failed while checking your policy.',
  limits: 'A time or resource limit was reached while checking your policy.',
  publication: 'Your policy passed verification, but publishing the result did not finish.',
};

const markerOf = (headSha, outcome) => `<!-- forshor-notify:${headSha}:${outcome} -->`;

/**
 * The comment for a rejected submission or a run that could not complete. Nothing here is taken from the artifact except the
 * sanitised stage label and reason (see neutralize); the run link is built from trusted identifiers.
 */
function buildComment({ outcome, login, headSha, runUrl, stage, reason, cause }) {
  const who = mention(login);
  const hello = who ? `${who}, ` : '';
  const marker = markerOf(headSha, outcome);
  const text = reason ? neutralize(reason) : '';
  const fenced = text ? `\n\`\`\`text\n${text}\n\`\`\`\n` : '';
  if (outcome === 'rejected') {
    const label = cleanName(stage);
    const what = STAGES[label] || `The check named "${neutralize(label, { chars: 60, lines: 1 })}" did not pass.`;
    return `${marker}\n### Submission not accepted\n\n`
      + `${hello}your policy did not pass verification for commit \`${sha7(headSha)}\`.\n\n`
      + `**What failed:** ${what}\n${fenced}\n`
      + 'Fix the problem and push a new commit to this pull request (a new commit starts a new check), or open a new submission pull request. '
      + '`./forshor check` runs the same checks on your machine, and `./forshor status` follows a hosted run. Published results are unchanged.\n\n'
      + `[Verification run](${runUrl})\n`;
  }
  return `${marker}\n### Verification could not complete\n\n`
    + `${hello}the automated check for commit \`${sha7(headSha)}\` did not finish. **This is not a verdict on your policy.** ${CAUSES[cause] || CAUSES.no_result}\n${fenced}\n`
    + `You do not need to change anything: the run can be re-run from [its page](${runUrl}) by a maintainer. `
    + `If you hear nothing within an hour, open an [issue](https://github.com/${REPOSITORY}/issues). Published results are unchanged.\n`;
}

/** One comment per head commit and outcome: found by its marker and updated in place, never duplicated; an identical body is left alone. */
async function upsert({ github, context, prNumber, marker, body }) {
  const comments = await github.paginate(github.rest.issues.listComments, {
    ...context.repo, issue_number: prNumber, per_page: 100,
  });
  const existing = comments.find(c => c.user?.login === BOT && typeof c.body === 'string' && c.body.startsWith(marker));
  if (existing) {
    if (existing.body !== body) await github.rest.issues.updateComment({ ...context.repo, comment_id: existing.id, body });
    return 'updated';
  }
  await github.rest.issues.createComment({ ...context.repo, issue_number: prNumber, body });
  return 'created';
}

const attemptUrl = run => `https://github.com/${REPOSITORY}/actions/runs/${run.id}/attempts/${run.run_attempt}`;

/**
 * Binds this notification to one verification attempt and one pull request, from the API only. Outputs: `process` (anything to say),
 * `download` (the verifier's result artifact is worth fetching) and `artifact` (its name). Writes notify-source.json for `post`.
 */
async function inspect({ github, context, core }) {
  const event = context.payload.workflow_run;
  if (!event || event.event !== 'pull_request' || event.path !== SOURCE_WORKFLOW
      || !Number.isSafeInteger(event.id) || !Number.isSafeInteger(event.run_attempt) || !SHA.test(event.head_sha || ''))
    throw new Error('Unexpected source verification workflow');
  core.setOutput('process', 'false');
  core.setOutput('download', 'false');
  if (!REPORTED.has(event.conclusion)) {
    core.notice(`Conclusion ${event.conclusion}: nothing for this workflow to report.`);
    return;
  }
  const { data: run } = await github.rest.actions.getWorkflowRunAttempt({
    ...context.repo, run_id: event.id, attempt_number: event.run_attempt,
  });
  if (run.id !== event.id || run.run_attempt !== event.run_attempt || run.event !== event.event
      || run.path !== event.path || run.head_sha !== event.head_sha || run.conclusion !== event.conclusion)
    throw new Error('Source workflow identity changed');
  let pr = null;
  let refusal = null;
  try {
    pr = await reporting.resolveTrustedPullRequest(github, context, run);
  } catch (error) {
    // No single current pull request for this commit (closed, merged, superseded by a push): nobody to tell.
    if (error.code === 'SUPERSEDED' || /^Expected exactly one /.test(error.message)) {
      core.notice(`No current submission pull request for this run: ${error.message}`);
      return;
    }
    // Anything else that is not a refusal of this pull request (an API failure, a malformed run) fails the job visibly: silence would hide it.
    if (!Number.isSafeInteger(error.pullNumber)) throw error;
    // The pull request changes files outside the submission surface (or nothing, or too much): a verdict computed here from the API.
    pr = { number: error.pullNumber, headSha: run.head_sha };
    refusal = error.message;
  }
  const { data: pull } = await github.rest.pulls.get({ ...context.repo, pull_number: pr.number });
  if (pull.state !== 'open' || pull.merged || pull.head?.sha !== pr.headSha
      || pull.base?.ref !== 'main' || pull.base?.repo?.full_name !== REPOSITORY) {
    core.notice('The pull request moved on or was closed; no notification.');
    return;
  }
  fs.writeFileSync(SOURCE_FILE, JSON.stringify({
    run: { id: run.id, run_attempt: run.run_attempt, conclusion: run.conclusion }, pr, author: pull.user?.login ?? null, refusal,
  }));
  core.setOutput('process', 'true');
  core.setOutput('download', refusal ? 'false' : 'true');
  core.setOutput('artifact', `table-generation-submission-${run.id}-${run.run_attempt}`);
}

function readResult(core) {
  try {
    if (!fs.existsSync(RESULT_FILE) || fs.statSync(RESULT_FILE).size > MAX_RESULT_BYTES) return null;
    return JSON.parse(fs.readFileSync(RESULT_FILE, 'utf8'));
  } catch (error) {
    core.warning?.(`Could not read the verifier result: ${error.message}`);
    return null;
  }
}

/** The comment for what `inspect` bound, built from trusted identifiers and the verifier's result as data. */
function compose({ source, result }) {
  const base = { login: source.author, headSha: source.pr.headSha, runUrl: attemptUrl(source.run) };
  if (source.refusal) return buildComment({ ...base, outcome: 'rejected', stage: 'changed files', reason: source.refusal });
  const verdict = classify({ conclusion: source.run.conclusion, result });
  return buildComment({ ...base, ...verdict });
}

async function post({ github, context, core }) {
  const source = JSON.parse(fs.readFileSync(SOURCE_FILE, 'utf8'));
  if (!Number.isSafeInteger(source.pr?.number) || !SHA.test(source.pr?.headSha || '')) throw new Error('Invalid notification source');
  const body = compose({ source, result: source.refusal ? null : readResult(core) });
  const marker = body.slice(0, body.indexOf('\n'));
  const action = await upsert({ github, context, prNumber: source.pr.number, marker, body });
  core.info(`Notification ${action} on pull request #${source.pr.number}.`);
}

/** `node scripts/ci/notify_submitters.js --dry-run --dir DIR --conclusion failure --login NAME --sha SHA --run-id ID [--attempt N]`: print the comment, post nothing. */
function main(argv) {
  const args = Object.fromEntries(argv.filter(a => a.startsWith('--')).map(a => { const [k, ...v] = a.slice(2).split('='); return [k, v.join('=') || true]; }));
  if (!args['dry-run']) throw new Error('This command only previews: pass --dry-run');
  const dir = args.dir || '.';
  const file = `${dir}/table-generation-submission-result.json`;
  let result = null;
  try { result = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { /* a missing or unreadable result is a case the comment handles */ }
  const source = {
    run: { id: Number(args['run-id'] || 1), run_attempt: Number(args.attempt || 1), conclusion: args.conclusion || 'failure' },
    pr: { number: 1, headSha: args.sha || 'a'.repeat(40) }, author: args.login || null, refusal: null,
  };
  process.stdout.write(`${compose({ source, result })}\n`);
}

if (require.main === module) main(process.argv.slice(2));

module.exports = { mention, neutralize, cleanName, classify, buildComment, markerOf, upsert, inspect, post, compose, attemptUrl, REPORTED };
