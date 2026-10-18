import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const repo = resolve(import.meta.dirname, '../..');
const { NODE_TEST_CONTEXT, ...childEnv } = process.env;

test('the correction fixture leases PostgreSQL before creating a cluster under the configured temporary directory', (t) => {
  const directory = mkdtempSync(join(tmpdir(), 'carr-na-portability-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  // Stop at initdb so this filesystem regression also runs without PostgreSQL.
  // The migration behavior itself is covered by the real database fixture.
  const probe = `
    import childProcess from 'node:child_process';
    import fs from 'node:fs';
    import { basename } from 'node:path';
    import { syncBuiltinESMExports } from 'node:module';
    const spawn = childProcess.spawn;
    childProcess.spawn = (command, args, options) => {
      if (args.some(arg => arg.endsWith('/lib/disposable_pg_fixture.py')))
        console.log('fixture-budget-requested');
      return spawn(command, args, options);
    };
    childProcess.spawnSync = (command, args) => {
      if (command === 'which') return { status: 0, stdout: process.execPath };
      if (basename(command) === 'initdb') {
        console.log('fixture-data=' + args[args.indexOf('-D') + 1]);
        return { status: 1, stderr: 'portability probe stops before initdb' };
      }
      throw new Error('unexpected command: ' + command);
    };
    fs.existsSync = () => true;
    syncBuiltinESMExports();
    await import(${JSON.stringify(pathToFileURL(join(repo, 'mcp-server/test/national-account-classification-correction.test.mjs')).href)});
  `;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', probe], {
    encoding: 'utf8', env: { ...childEnv, CARR_NATIONAL_ACCOUNT_TEST_REQUIRED: '0', TMPDIR: directory, TMP: directory, TEMP: directory },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /fixture-budget-requested[\s\S]*fixture-data=/,
    'the fixture must acquire the shared PostgreSQL budget before initdb');
  const data = result.stdout.match(/^fixture-data=(.+)$/m)?.[1];
  assert.ok(data?.startsWith(directory + '/'), `fixture must honor TMPDIR; observed ${data}`);
});

test('the pending correction sorts after the committed schema migration ledger', () => {
  const names = readdirSync(join(repo, 'migrations')).filter(name =>
    /^\d{4}_national_account_classification_correction\.sql$/.test(name));
  assert.equal(names.length, 1, 'exactly one correction migration must exist');
  const schema = readFileSync(join(repo, 'db/schema.sql'), 'utf8');
  const ledger = schema.match(/^COPY public\.schema_migrations .*\n([\s\S]*?)^\\\.$/m)?.[1];
  assert.ok(ledger, 'the committed schema must carry its migration ledger');
  const applied = ledger.trim().split('\n').map(row => row.split('\t')[0]);
  if (applied.includes(names[0])) return;
  const highest = applied.sort().at(-1);
  assert.ok(names[0] > highest, `pending correction ${names[0]} must follow ${highest}`);
});

test('the committed schema migration ledger contains unique loadable records', () => {
  const schema = readFileSync(join(repo, 'db/schema.sql'), 'utf8');
  const ledger = schema.match(/^COPY public\.schema_migrations .*\n([\s\S]*?)^\\\.$/m)?.[1];
  assert.ok(ledger, 'the committed schema must carry its migration ledger');
  const filenames = new Set();
  for (const row of ledger.trim().split('\n')) {
    const fields = row.split('\t');
    assert.equal(fields.length, 3, `migration ledger row must have three fields: ${row}`);
    const [filename, checksum, appliedAt] = fields;
    assert.match(filename, /^\d{4}[a-z]?_[a-z0-9_]+\.sql$/);
    assert.match(checksum, /^[a-f0-9]{64}$/);
    assert.ok(Number.isFinite(Date.parse(appliedAt)), `invalid migration timestamp: ${appliedAt}`);
    assert.ok(!filenames.has(filename), `duplicate migration record: ${filename}`);
    filenames.add(filename);
  }
});

test('the required database proof fails instead of skipping when PostgreSQL is missing', () => {
  const probe = `
    import childProcess from 'node:child_process';
    import { syncBuiltinESMExports } from 'node:module';
    childProcess.spawnSync = () => ({ status: 1, stdout: '' });
    syncBuiltinESMExports();
    await import(${JSON.stringify(pathToFileURL(join(repo, 'mcp-server/test/national-account-classification-correction.test.mjs')).href)});
  `;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', probe], {
    encoding: 'utf8', env: { ...childEnv, CARR_NATIONAL_ACCOUNT_TEST_REQUIRED: '1' },
  });
  assert.equal(result.status, 1, result.stderr || result.stdout);
  assert.match(result.stdout + result.stderr, /PostgreSQL server unavailable/);
});
