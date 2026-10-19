import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const policy = JSON.parse(readFileSync(new URL('./skill-evidence-policy.json', import.meta.url)));
export function canonical(value) {
  value = value.replace(/\([^)]*\)/g, '').trim().replace(/^[`/ ]+|[`/ ]+$/g, '')
    .split(/\s+for\s+/)[0].split(':').at(-1).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return policy.aliases[value] ?? value;
}
const visible = body => (body ?? '').replace(/<!--[\s\S]*?(?:-->|$)/g, '');
export function claims(body) {
  const line = visible(body).match(/^Skills used:[ \t]*(.+)$/im)?.[1];
  return line ? [...new Set(line.split(/\.\s+(?=[A-Z])/)[0].split(/[,;]/).map(canonical).filter(Boolean))] : [];
}
function section(body, title) {
  return body.match(new RegExp(`^#{1,6} ${title}[^\\n]*\\n([\\s\\S]*?)(?=^#{1,6} |$(?![\\s\\S]))`, 'im'))?.[1]?.trim() ?? '';
}
const isTest = file => /(^|\/)(tests?|__tests__|testdata|fixtures)(\/|\.)|(^|\/)(test[_-]|.*[_-]test[_.-])|\.(test|spec)\./i.test(file);
const isCode = file => !isTest(file) && /\.(py|[cm]?js|jsx|tsx?|sh|swift|rs|go|java|c|cpp|rb)$/.test(file);
function tddEvidence(commits) {
  const bySha = new Map(commits.map(commit => [commit.sha, commit]));
  function ancestors(commit, seen = new Set()) {
    for (const parent of commit.parents ?? []) {
      if (seen.has(parent)) continue;
      seen.add(parent);
      if (bySha.has(parent)) ancestors(bySha.get(parent), seen);
    }
    return seen;
  }
  const tests = commits.filter(commit => (commit.changedTests ?? commit.files).some(isTest) && !commit.files.some(isCode));
  const code = commits.filter(commit => commit.files.some(isCode));
  return tests.length > 0 && code.every(commit => tests.some(test => ancestors(commit).has(test.sha)));
}

export function score({ body, repo, head, comments = [], commits = [] }) {
  body = visible(body);
  const skills = claims(body), errors = [];
  if (!skills.length) return ['PR body requires a non-empty Skills used: line.'];
  const owner = repo.split('/')[0];
  const receipts = comments.flatMap(comment => {
    if (![owner, 'github-actions[bot]'].includes(comment.user?.login)) return [];
    if (!comment.body?.startsWith('<!-- skill-run-receipt/v1 -->')) return [];
    try {
      const receipt = JSON.parse(comment.body.match(/```json\s*\n([\s\S]*?)\n```/)?.[1]);
      return receipt.schema === 'skill-run-receipt/v1' && receipt.repo === repo && receipt.head === head
        && receipt.session_id && receipt.dispatch_id && Array.isArray(receipt.skills) ? [receipt] : [];
    } catch { return []; }
  });
  if (!receipts.length) errors.push('Missing trusted skill-run receipt for this repository and head SHA; dispatcher must publish it after the job.');
  for (const skill of skills) {
    if (receipts.length && !receipts.some(receipt => receipt.skills.some(item => item && item.skill === skill
      && item.opened === true && Array.isArray(item.paths)
      && item.paths.some(path => typeof path === 'string' && path.endsWith('/SKILL.md')
        && canonical(path.split('/').at(-2)) === skill)))) {
      errors.push(`${skill}: not opened in the job's session receipt. Open its SKILL.md and republish the receipt.`);
    }
  }
  if (skills.includes('tdd') && !tddEvidence(commits)) {
    errors.push('TDD: require a test-only commit that adds or changes a test and is an ancestor of each code-changing commit; mixed test/code commits do not prove test-first work.');
  }
  if (skills.includes('prove-it-works')) {
    const output = [...body.matchAll(/```(?:text|console|shell|bash|sh)?\n([\s\S]*?)```/g)].some(match =>
      /(?:\$ |python\d? |npm |node |pytest|ops\/ci\.sh)/.test(match[1])
      && /(?:Ran \d+ tests?|\b\d+ passed\b|# pass \d+|ℹ pass \d+|Tests:\s*\d+ passed|\bPASS\b)/.test(match[1]));
    const reproduction = section(body, 'Reproduction');
    if (!output && !(/expected/i.test(reproduction) && /actual/i.test(reproduction) && /```/.test(reproduction))) {
      errors.push('Prove It Works: include real command and test output in a fenced block, or a Reproduction section with command, expected and actual results.');
    }
  }
  if (skills.includes('grill-with-docs')) {
    const decisions = section(body, 'Decisions');
    if (!/question:|\bQ\d+\b/i.test(decisions) || !/answer:/i.test(decisions) || !/alternative:|rejected:/i.test(decisions)) {
      errors.push('grill-with-docs: add a Decisions section recording each question, recommended answer, and rejected alternative.');
    }
  }
  if (skills.includes('diagnosing-bugs') && !/\S+\.[a-z]+:\d+\b/i.test(section(body, 'Root cause'))) {
    errors.push('diagnosing-bugs: add a Root cause section identifying the cause with a file:line reference.');
  }
  return errors;
}

export function gitCommits(base, head, cwd = process.cwd()) {
  if (![base, head].every(value => /^[a-f0-9]{40}$/.test(value ?? ''))) throw new Error('Full base and head SHAs required');
  const git = args => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
  return git(['rev-list', '--reverse', `${base}..${head}`]).split('\n').filter(Boolean).map(sha => ({
    sha, parents: git(['show', '-s', '--format=%P', sha]).split(' ').filter(Boolean),
    files: git(['diff-tree', '--root', '--no-commit-id', '--name-only', '-r', sha]).split('\n').filter(Boolean),
    changedTests: git(['diff-tree', '--root', '--no-commit-id', '--name-only', '--diff-filter=AM', '-r', sha]).split('\n').filter(isTest),
  }));
}
export function exempt(event) {
  const pr = event.pull_request, body = visible(pr.body);
  return ['dependabot[bot]', 'renovate[bot]', 'renovate'].includes(pr.user?.login)
    || ['jbookout', 'github-actions[bot]'].includes(pr.user?.login)
    && pr.head?.repo?.full_name === event.repository?.full_name
    && /^release\/schema-snapshot-[0-9a-f]{8}$/.test(pr.head?.ref ?? '')
    && body.startsWith('Opened by ops/release-pipeline.py:');
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, 'utf8'));
  if (!event.pull_request) throw new Error('PR event required');
  if (!exempt(event)) {
    const pr = event.pull_request;
    const comments = process.env.SKILL_RECEIPT_COMMENTS
      ? JSON.parse(readFileSync(process.env.SKILL_RECEIPT_COMMENTS, 'utf8')).flat() : [];
    const errors = score({ body: pr.body, repo: event.repository.full_name, head: pr.head?.sha, comments,
      commits: claims(pr.body).includes('tdd') && pr.base?.sha && pr.head?.sha ? gitCommits(pr.base.sha, pr.head.sha) : [] });
    for (const error of errors) console.error(error);
    if (errors.length) process.exitCode = 1;
  }
}
