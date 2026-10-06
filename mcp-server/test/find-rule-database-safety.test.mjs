import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// Run the actual feature suite with an in-memory pg driver. No URL in this
// regression can reach a database, even when the safety precondition breaks.
const probe = `
  import { readFileSync } from 'node:fs';
  import vm from 'node:vm';
  import assert from 'node:assert/strict';
  import { randomUUID } from 'node:crypto';
  const source = readFileSync(process.argv[1], 'utf8');
  const results = [];
  for (const dsn of JSON.parse(process.argv[2])) {
    const cases = [];
    const result = { dsn, constructed: 0, connected: 0, refusal: null };
    const context = vm.createContext({ process: {
      env: dsn === null ? {} : { CARR_RULE_TEST_DATABASE_URL: dsn },
    } });
    const moduleFor = exports => new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
    }, { context });
    const imports = {
      'node:test': moduleFor({ default: (...args) => cases.push(args) }),
      'node:assert/strict': moduleFor({ default: assert }),
      'node:crypto': moduleFor({ randomUUID }),
      '../src/tools.js': moduleFor({ TOOLS: {}, executeRegisteredTool() {}, ToolError: Error }),
      '../src/mcp.js': moduleFor({ requiresAuthorityConnection() {} }),
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

function probeSuite(dsns) {
  const child = spawnSync(process.execPath,
    ['--experimental-vm-modules', '--input-type=module', '-e', probe,
      fileURLToPath(new URL('./find-rule-supersedes.test.mjs', import.meta.url)), JSON.stringify(dsns)],
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
    assert.equal(result.constructed, 4, result.dsn);
    assert.equal(result.connected, 4, result.dsn);
    assert.equal(result.refusal, null, result.dsn);
  }
  for (const result of results.slice(safe.length)) {
    assert.equal(result.constructed, 0);
    assert.equal(result.connected, 0);
    assert.equal(result.refusal, null);
  }
});
