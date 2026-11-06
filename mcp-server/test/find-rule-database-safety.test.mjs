// Test rule lookup database URL safety and local execution.
import test from 'node:test';
import assert from 'node:assert/strict';
import { assertDisposableDatabase } from './helpers/disposable-database-url.mjs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Run the actual feature suite with an in-memory pg driver. No URL in this
// regression can reach a database, even when the safety precondition breaks.
const probe = `
  import { readFileSync } from 'node:fs';
  import vm from 'node:vm';
  import { assertDisposableDatabase } from ${JSON.stringify(new URL('./helpers/disposable-database-url.mjs', import.meta.url).href)};
  import assert from 'node:assert/strict';
  import { randomUUID } from 'node:crypto';
  const source = readFileSync(process.argv[1], 'utf8');
  const results = [];
  for (const dsn of JSON.parse(process.argv[2])) {
    const cases = [];
    const result = { dsn, constructed: 0, connected: 0, refusal: null };
    const context = vm.createContext({ process: {
      env: dsn === null ? {} : { ...JSON.parse(process.argv[3]), CARR_RULE_TEST_DATABASE_URL: dsn },
    } });
    const moduleFor = exports => new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
    }, { context });
    const imports = {
      './helpers/disposable-database-url.mjs': moduleFor({ assertDisposableDatabase: dsn => assertDisposableDatabase(dsn, { env: JSON.parse(process.argv[3]) }) }),
      'node:test': moduleFor({ default: (...args) => cases.push(args) }),
      'node:assert/strict': moduleFor({ default: assert }),
      'node:crypto': moduleFor({ randomUUID }),
      '../src/tools.js': moduleFor({ TOOLS: {}, executeRegisteredTool() {}, ToolError: Error }),
    };
    const pg = moduleFor({ default: { Client: class {
      constructor() { result.constructed++; }
      async connect() { result.connected++; throw new Error('stub: no database access'); }
    } } });
    await pg.link(() => { throw new Error('unexpected pg dependency'); });
    await pg.evaluate();
    const suite = new vm.SourceTextModule(source, { context,
      importModuleDynamically(specifier) {
        assert.equal(specifier, 'pg');
        return pg;
      },
    });
    await suite.link(specifier => {
      assert.ok(imports[specifier], 'unexpected suite dependency: ' + specifier);
      return imports[specifier];
    });
    try { await suite.evaluate(); } catch (error) { result.refusal = error.code; }
    for (const [name, options, run] of cases) {
      if (!name.startsWith('real PostgreSQL:') || options.skip) continue;
      try { await run(); } catch (error) {
        if (error.code === 'ERR_ASSERTION') result.refusal = error.code;
        else assert.equal(error.message, 'stub: no database access');
      }
    }
    results.push(result);
  }
  console.log(JSON.stringify(results));
`;

function probeSuite(dsns, env = {}) {
  const child = spawnSync(process.execPath,
    ['--experimental-vm-modules', '--input-type=module', '-e', probe,
      fileURLToPath(new URL('./find-rule-supersedes.test.mjs', import.meta.url)), JSON.stringify(dsns), JSON.stringify(env)],
    { encoding: 'utf8', timeout: 10_000 });
  assert.equal(child.status, 0, child.stderr);
  return JSON.parse(child.stdout);
}

test('rule database suite refuses unsafe URLs before any client construction or connection', () => {
  const unsafe = [
    'postgres://fixture@db.example.invalid:5432/test',
    'postgresql://fixture@localhost.example.invalid:5432/test',
    'postgres://fixture@127.0.0.1.example.invalid:5432/test',
    'not-a-database-url',
  ];
  for (const result of probeSuite(unsafe)) {
    assert.equal(result.constructed, 0, result.dsn);
    assert.equal(result.connected, 0, result.dsn);
    assert.equal(result.refusal, 'ERR_ASSERTION', result.dsn);
  }
});

test('rule database suite retains loopback execution and skips absent configuration', () => {
  const safe = ['postgres://fixture@127.0.0.1:5432/test',
    'postgresql://fixture@localhost:5432/test'];
  const results = probeSuite([...safe, null, '']);
  for (const result of results.slice(0, safe.length)) {
    assert.equal(result.constructed, 3, result.dsn);
    assert.equal(result.connected, 3, result.dsn);
    assert.equal(result.refusal, null, result.dsn);
  }
  for (const result of results.slice(safe.length)) {
    assert.equal(result.constructed, 0);
    assert.equal(result.connected, 0);
    assert.equal(result.refusal, null);
  }
});

test('rule database suite accepts the declared CI service and refuses undeclared hosts before connecting', () => {
  const dsn = 'postgresql://carr_ci@postgres:5432/rule_fixture_' + 'a'.repeat(32);
  const env = { GITHUB_ACTIONS: 'true', CARR_CI_POSTGRES_IMAGE: 'both' };
  const [accepted] = probeSuite([dsn], env);
  assert.equal(accepted.refusal, null);
  assert.equal(accepted.connected, 3);
  for (const markers of [{}, { GITHUB_ACTIONS: 'true' }, { CARR_CI_POSTGRES_IMAGE: 'both' }]) {
    const [refused] = probeSuite([dsn], markers);
    assert.equal(refused.refusal, 'ERR_ASSERTION');
    assert.equal(refused.connected, 0);
  }
  for (const unsafe of [dsn.replace('@postgres:', '@remote:'), dsn + '?host=remote',
    dsn.replace('/rule_fixture_', '/carr_ci_').replace('a'.repeat(32), 'production')]) {
    const [refused] = probeSuite([unsafe], env);
    assert.equal(refused.refusal, 'ERR_ASSERTION');
    assert.equal(refused.connected, 0);
  }
});

test('disposable URL policy bounds hosted clones and admin service separately', () => {
  const env = { GITHUB_ACTIONS: 'true', CARR_CI_POSTGRES_IMAGE: 'both' };
  const clone = 'postgres://carr_ci@postgres:5432/fixture_' + 'b'.repeat(32);
  assertDisposableDatabase(clone, { env });
  assertDisposableDatabase('postgres://carr_ci@postgres:5432/carr_ci', { env, service: true });
  assert.throws(() => assertDisposableDatabase('postgres://carr_ci@postgres:5432/carr_ci', { env }));
  for (const dsn of [clone.replace('5432', '5433'), clone.replace('carr_ci@', 'other@'),
    clone.replace('carr_ci@', 'carr_ci:pw@'), clone.replace('postgres://', 'http://'),
    clone.replace('@postgres:', '@postgres.example.invalid:'), clone + '#override',
    clone + '?hostaddr=127.0.0.1', clone + '?port=5433', 'not a URL']) {
    assert.throws(() => assertDisposableDatabase(dsn, { env, service: true }), { code: 'ERR_ASSERTION' });
  }
  for (const host of ['localhost', '127.0.0.1', '[::1]']) {
    const dsn = `postgres://fixture@${host}:55440/owned`;
    assertDisposableDatabase(dsn, { env: {} });
    assert.throws(() => assertDisposableDatabase(dsn + '?host=remote', { env }));
  }
});


test('disposable URL policy preserves the supervisor local socket contract only', () => {
  const dsn = 'postgresql://carr_ci@localhost/carr_ci?host=%2Ftmp%2Fpg-abcdefgh';
  assertDisposableDatabase(dsn, { env: {} });
  for (const unsafe of [dsn.replace('pg-abcdefgh', 'other'), dsn + '&port=5433',
    dsn.replace('localhost', 'remote'), dsn.replace('carr_ci@', 'other@'),
    dsn + '&host=remote', dsn.replace('pg-abcdefgh', 'pg-abcdefgh%2F..%2Fremote')]) {
    assert.throws(() => assertDisposableDatabase(unsafe, { env: {} }));
  }
});
