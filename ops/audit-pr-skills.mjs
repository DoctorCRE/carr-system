import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { claims, score } from './check-pr-skills.mjs';

// Input is a read-only GraphQL export of PR bodies, comments, and original commits.
const [source, carrGit, appGit, destination] = process.argv.slice(2);
if (!destination) throw new Error('Usage: node ops/audit-pr-skills.mjs SOURCE CARR_GIT APP_GIT OUTPUT');
const data = JSON.parse(readFileSync(source, 'utf8'));
if (data.errors?.length) throw new Error('GraphQL export contains errors');
const rows = [];
for (const [key, repository, cwd] of [
  ['carr_system', 'jbookout/carr-system', carrGit],
  ['doctorcre_app', 'jbookout/doctorcre-app', appGit],
]) {
  for (const pr of Object.values(data.data[key])) {
    if (pr.commits.pageInfo.hasNextPage || pr.comments.pageInfo.hasNextPage) throw new Error(`Incomplete PR ${pr.url}`);
    const commits = pr.commits.nodes.map(({ commit }) => ({
      sha: commit.oid, parents: commit.parents.nodes.map(parent => parent.oid),
      changedTests: execFileSync('git', ['diff-tree', '--root', '--no-commit-id', '--name-only', '--diff-filter=AM', '-r', commit.oid],
        { cwd, encoding: 'utf8' }).trim().split('\n').filter(Boolean),
      files: execFileSync('git', ['diff-tree', '--root', '--no-commit-id', '--name-only', '-r', commit.oid],
        { cwd, encoding: 'utf8' }).trim().split('\n').filter(Boolean),
    }));
    const errors = score({ body: pr.body, repo: repository, head: pr.headRefOid, commits,
      comments: pr.comments.nodes.map(comment => ({ ...comment, user: comment.author })) });
    const declared = claims(pr.body);
    const checks = { declaration: declared.length > 0 };
    if (checks.declaration) {
      checks.receipt = !errors.some(error => /receipt/i.test(error));
      for (const [skill, prefix] of [['tdd', 'TDD:'], ['prove-it-works', 'Prove It Works:'],
        ['grill-with-docs', 'grill-with-docs:'], ['diagnosing-bugs', 'diagnosing-bugs:']]) {
        if (declared.includes(skill)) checks[skill] = !errors.some(error => error.startsWith(prefix));
      }
    }
    rows.push({ repository, number: pr.number, url: pr.url, state: pr.state, head: pr.headRefOid,
      skills: declared, checks, passed: Object.values(checks).filter(Boolean).length,
      required: Object.keys(checks).length, verdict: errors.length ? 'FAIL' : 'PASS', errors });
  }
}
const observed = new Date().toISOString();
writeFileSync(destination, JSON.stringify({ observed, semantics: 'Artifact scores are independent of receipt availability. Missing receipts are unverified, not proof a skill was skipped.', rows }, null, 2) + '\n');
for (const row of rows) console.log(`${row.repository}#${row.number}\t${row.verdict}\t${row.passed}/${row.required}\t${Object.entries(row.checks).map(([name, ok]) => `${name}=${ok ? 'pass' : 'fail'}`).join(', ')}`);
