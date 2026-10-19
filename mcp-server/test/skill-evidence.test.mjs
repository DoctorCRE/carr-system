import test from 'node:test';
import assert from 'node:assert/strict';
import { score } from '../../ops/check-pr-skills.mjs';

const sha = 'a'.repeat(40);
function evidence(body, opened = ['tdd'], commits = []) {
  return { body, repo: 'jbookout/carr-system', head: sha, commits,
    comments: [{ user: { login: 'jbookout' }, body:
      '<!-- skill-run-receipt/v1 -->\n```json\n' + JSON.stringify({
        schema: 'skill-run-receipt/v1', repo: 'jbookout/carr-system', head: sha,
        session_id: 'session', dispatch_id: 'job', skills: opened.map(skill => ({
          skill, opened: true, paths: [`/skills/${skill}/SKILL.md`],
        })),
      }) + '\n```' }],
  };
}
test('head-bound trusted receipts are required for every skill', () => {
  const value = evidence('Skills used: poteto-mode', ['poteto-mode']);
  assert.deepEqual(score(value), []);
  value.comments[0].user.login = 'someone';
  assert.match(score(value).join(), /receipt/);
  value.comments[0].user.login = 'jbookout';
  value.head = 'b'.repeat(40);
  assert.match(score(value).join(), /receipt/);
});
test('not opened and omitted claims fail', () => {
  assert.match(score(evidence('Skills used: poteto-mode', [])).join(), /poteto-mode.*not opened/);
});
test('TDD requires an ancestor test commit before the first code change', () => {
  const value = evidence('Skills used: Pocock TDD', ['tdd'], [
    { sha: 'test', parents: ['base'], files: ['test_widget.py'] },
    { sha: 'code', parents: ['test'], files: ['widget.py'] },
  ]);
  assert.deepEqual(score(value), []);
  value.commits.reverse();
  assert.deepEqual(score(value), [], 'API ordering is not chronology');
  value.commits[0].parents = ['base'];
  assert.match(score(value).join(), /TDD/);
  value.commits = [{ sha: 'mixed', parents: ['base'], files: ['test_widget.py', 'widget.py'] }];
  assert.match(score(value).join(), /TDD/);
});
test('artifact requirements reject headings and accept concrete evidence', () => {
  for (const [skill, bad, good] of [
    ['prove-it-works', '## How verified\nTests pass.',
     '## How verified\n```text\n$ python3 -m unittest\nRan 12 tests\nOK\n```'],
    ['grill-with-docs', '## Decisions\n',
     '## Decisions\nQuestion: Receipt storage? Answer: PR comment. Rejected alternative: tracked logs.'],
    ['diagnosing-bugs', '## Root cause\nA bad branch.',
     '## Root cause\n`tools/widget.py:42` accepted an invalid branch.'],
  ]) {
    assert.notDeepEqual(score(evidence(`Skills used: ${skill}\n${bad}`, [skill])), [], skill);
    assert.deepEqual(score(evidence(`Skills used: ${skill}\n${good}`, [skill])), [], skill);
  }
});
test('quoted evidence and HTML comments cannot satisfy artifacts', () => {
  assert.notDeepEqual(score(evidence('Skills used: grill-with-docs\n<!-- ## Decisions\nQuestion: X? Answer: Y. Rejected alternative: Z. -->', ['grill-with-docs'])), []);
});
