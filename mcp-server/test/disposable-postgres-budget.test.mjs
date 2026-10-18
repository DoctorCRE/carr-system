import test from 'node:test';
import assert from 'node:assert/strict';
import { acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';

test('Node PostgreSQL fixtures hold the shared budget through teardown', async () => {
  const first = await acquirePostgresFixtureGroup();
  let entered = false, second;
  const waiting = acquirePostgresFixtureGroup().then(release => { entered = true; second = release; });
  try {
    await new Promise(resolve => setTimeout(resolve, 200));
    assert.equal(entered, false, 'another fixture must wait while the first cluster exists');
    await first();
    await waiting;
    assert.equal(entered, true);
  } finally {
    await first();
    await waiting;
    await second?.();
  }
});

// A fixture may be imported indirectly from an eval helper. Keep every real
// Node initdb caller in this budget, rather than only scanning test filenames.
test('all Node cluster constructors acquire the shared fixture budget', async () => {
  const { readdir, readFile } = await import('node:fs/promises');
  const root = new URL('../../', import.meta.url);
  async function scan(directory) {
    const matches = [];
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (['node_modules', 'out', '.git', '.claude'].includes(entry.name)) continue;
      const url = new URL(entry.name + (entry.isDirectory() ? '/' : ''), directory);
      if (entry.isDirectory()) matches.push(...await scan(url));
      else if (/\.(?:mjs|js)$/.test(entry.name)) {
        const source = await readFile(url, 'utf8');
        if (/(?:binary|path\.join)\([^\n]*['"]initdb['"]/.test(source) || /run\(['"]initdb['"]/.test(source))
          matches.push({ url, source });
      }
    }
    return matches;
  }
  const constructors = await scan(root);
  assert.ok(constructors.length > 0);
  for (const { url, source } of constructors) {
    assert.match(source, /await acquirePostgresFixtureGroup\(/, url.pathname);
    assert.match(source, /await acquireDisposablePostgres\(/, url.pathname);
    assert.doesNotMatch(source, /execFileSync\(\s*(?:binary|path\.join)\([^\n]*['"](?:initdb|pg_ctl)['"]/,
      `${url.pathname}: cluster launch must execute inside its supervisor`);
  }
});
