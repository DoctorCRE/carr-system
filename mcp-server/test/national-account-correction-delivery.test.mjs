// Tests delivery and database proof of the national account correction.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const repo = resolve(import.meta.dirname, '../..');
const { NODE_TEST_CONTEXT, ...childEnv } = process.env;

test('the correction fixture leases PostgreSQL before creating a cluster under the configured temporary directory', (t) => {
  const directory = mkdtempSync(join(tmpdir(), 'carr-na-portability-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const postgres = join(directory, 'postgres');
  const initdb = join(directory, 'initdb');
  const observed = join(directory, 'initdb-args');
  writeFileSync(postgres, '');
  writeFileSync(initdb, `#!/bin/sh\nprintf '%s\n' "$@" > ${JSON.stringify(observed)}\necho portability-probe-stop >&2\nexit 1\n`, { mode: 0o755 });
  // Stop at initdb so this filesystem regression also runs without PostgreSQL.
  // The migration behavior itself is covered by the real database fixture.
  const probe = `
    import childProcess from 'node:child_process';
    import fs from 'node:fs';
    import { syncBuiltinESMExports } from 'node:module';
    const spawn = childProcess.spawn;
    childProcess.spawn = (command, args, options) => {
      const child = spawn(command, args, options);
      if (args.some(arg => arg.endsWith('/lib/disposable_pg_fixture.py')) && !args.includes('--supervise'))
        child.stdout.once('data', () => console.log('fixture-budget-acquired'));
      return child;
    };
    childProcess.spawnSync = (command, args) => {
      if (command === 'which') return { status: 0, stdout: ${JSON.stringify(postgres)} };
      throw new Error('unexpected command: ' + command);
    };
    fs.existsSync = () => true;
    syncBuiltinESMExports();
    await import(${JSON.stringify(pathToFileURL(join(repo, 'mcp-server/test/national-account-classification-correction.test.mjs')).href)});
  `;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', probe], {
    encoding: 'utf8', env: { ...childEnv, CARR_NATIONAL_ACCOUNT_TEST_REQUIRED: '0', TMPDIR: directory, TMP: directory, TEMP: directory },
  });
  assert.equal(result.status, 1, result.stderr || result.stdout);
  assert.match(result.stdout + result.stderr, /portability-probe-stop/);
  assert.match(result.stdout, /fixture-budget-acquired[\s\S]*portability-probe-stop/,
    'the fixture must acquire the shared PostgreSQL budget before initdb');
  const args = readFileSync(observed, 'utf8').trim().split('\n');
  const data = args[args.indexOf('-D') + 1];
  assert.ok(data?.startsWith(realpathSync(directory) + '/'), `fixture must honor TMPDIR; observed ${data}`);
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
