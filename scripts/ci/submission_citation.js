'use strict';

// Trusted-main code only. The "Cite this submission" section of the accepted comment. It is built only from values the publisher has already
// checked against the verified pull request (digest, commit, login, pull number, snapshot) and is empty if any of them is not in its
// exact expected form: nothing a contributor wrote reaches the comment except through a pattern.

const REPOSITORY = 'zapatacomputing/forshor-submissions';
const hex = (value, length) => typeof value === 'string' && new RegExp(`^[a-f0-9]{${length}}$`).test(value);
const LOGIN = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/;
const ROW_NAME = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}_sub[1-9][0-9]*$/;
const MODES = new Set(['PhaseProduct', 'PhaseTripleProduct']);
const bibtexText = text => text.replace(/_/g, '\\_');

/**
 * The section for an accepted policy, or '' when this is not a policy or a value is missing or malformed.
 * `state.pr` is the verified pull request, `metadata` the verified evaluation's, `result` the publisher's receipt.
 */
function citation({ state, result, metadata }) {
  if (metadata?.kind !== 'policy') return '';
  const digest = metadata.policyDigest, head = state?.pr?.headSha, login = metadata.githubLogin, pull = state?.pr?.number;
  if (!hex(digest, 64) || !hex(head, 40) || !LOGIN.test(login ?? '') || !Number.isSafeInteger(pull) || pull < 1) return '';
  const mode = MODES.has(result?.policy?.mode) ? result.policy.mode : null;
  const name = ROW_NAME.test(result?.name ?? '') ? result.name : null;
  const snapshot = hex(result?.snapshotId, 64) ? result.snapshotId : null;
  // The year the submission was made (the pull request's creation), so the text never changes with a later publication; the site's Details dialog says the same.
  const made = Date.parse(metadata.submittedAt), published = Date.parse(result?.updatedAt);
  const year = new Date(Number.isFinite(made) ? made : Number.isFinite(published) ? published : Date.now()).getUTCFullYear();
  const url = `https://github.com/${REPOSITORY}/pull/${pull}`;
  const title = `ForShor challenge: ${mode ? `${mode} policy` : 'policy'}${name ? ` ${name}` : ''}`;
  // Only what never changes: the snapshot it was first listed in is a separate line, and the site's Details dialog produces this exact text without it.
  const facts = `policy digest sha256:${digest}; submission commit ${head}`;
  return [
    '### Cite this submission',
    '',
    'This submission is its own citable object. The digest identifies the policy, the commit identifies its source in the archive, and a DOI will be assigned when accepted submissions are deposited on Zenodo after the challenge closes.',
    '',
    `Policy digest (sha256): \`${digest}\``,
    `Submission commit: \`${head}\``,
    ...(snapshot ? [`Published in snapshot: \`${snapshot}\``] : []),
    '',
    '```text',
    `${login} (${year}). ${title}, pull request #${pull}. ${facts}. ${url}`,
    '```',
    '',
    '```bibtex',
    `@misc{forshor_${digest.slice(0, 12)},`,
    `  author = {${login}},`,
    `  title = {${bibtexText(title)}},`,
    `  year = {${year}},`,
    `  howpublished = {\\url{${url}}},`,
    `  note = {${facts}. DOI to be assigned at deposit after the challenge closes.}`,
    '}',
    '```',
  ].join('\n');
}

module.exports = { citation };
