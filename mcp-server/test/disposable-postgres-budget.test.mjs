// Test shared budget acquisition and teardown for PostgreSQL fixtures.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

async function isolatedAdapter() {
  const root = await mkdtemp(path.join(tmpdir(), 'pg-protocol-test-'));
  await mkdir(path.join(root, 'lib'));
  await mkdir(path.join(root, 'mcp-server/test/helpers'), { recursive: true });
  const source = await readFile(new URL('../../lib/disposable_pg_fixture.py', import.meta.url), 'utf8');
  await writeFile(path.join(root, 'lib/disposable_pg_fixture.py'), source.replace(
    /LOCK_PATH = .*\n/, `LOCK_PATH = Path(${JSON.stringify(path.join(root, 'budget'))})\n`));
  await writeFile(path.join(root, 'mcp-server/test/helpers/disposable-postgres.mjs'),
    await readFile(new URL('./helpers/disposable-postgres.mjs', import.meta.url)));
  const adapter = await import(pathToFileURL(path.join(root, 'mcp-server/test/helpers/disposable-postgres.mjs')));
  return { ...adapter, cleanup: () => rm(root, { recursive: true }) };
}

test('Node PostgreSQL fixtures hold the shared budget through teardown', async () => {
  const { acquirePostgresFixtureGroup, cleanup } = await isolatedAdapter();
  const first = await acquirePostgresFixtureGroup({ demand: 4 });
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
    await cleanup();
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

test('Node protocol admits two groups and reports bounded overload', async () => {
  const { acquirePostgresFixtureGroup, cleanup } = await isolatedAdapter();
  const holders = [];
  try {
    holders.push(...await Promise.all([acquirePostgresFixtureGroup(), acquirePostgresFixtureGroup()]));
    await assert.rejects(acquirePostgresFixtureGroup({ timeout: 0.1 }), /capacity_unavailable/);
    await holders.shift()();
    holders.push(await acquirePostgresFixtureGroup());
  } finally {
    await Promise.all(holders.map(close => close()));
    await cleanup();
  }
});
