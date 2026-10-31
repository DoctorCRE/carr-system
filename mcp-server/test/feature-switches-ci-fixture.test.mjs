// Prove shared-service feature-switch fixtures restore cluster state on success and failure.
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { postgresDatabase } from './helpers/postgres-database.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
test('feature-switches CI-service suite leaves roles and databases unchanged, including after failure', async () => {
  const service = await postgresDatabase('fs-ci-');
  const directory = await mkdtemp(path.join(tmpdir(), 'feature-switch-failure-'));
  const state = async () => ({
    roles: (await service.client.query('select rolname,rolcanlogin,rolcreatedb,rolsuper,rolconfig from pg_roles order by rolname')).rows,
    databases: (await service.client.query('select datname from pg_database order by datname')).rows,
    memberships: (await service.client.query('select roleid,member,grantor,admin_option,inherit_option,set_option from pg_auth_members order by roleid,member,grantor')).rows,
  });
  try {
    const hook = path.join(directory, 'failure.mjs');
    await writeFile(hook, `import assert from 'node:assert/strict';
const original = assert.deepEqual;
assert.deepEqual = function(actual, expected, message) {
 if (message === 'switch writes must register row and truncate guards with the existing reference monitor')
   throw new Error('intentional feature fixture failure');
 return original(actual, expected, message);
};`);
    const env = { ...process.env, CARR_CI_DATABASE_URL: `postgresql://fixture@127.0.0.1:${service.client.connectionParameters.port}/postgres` };
    delete env.NODE_TEST_CONTEXT;
    for (const [scenario, fail] of [false, true, false].entries()) {
      if (scenario === 2)
        await service.client.query('create role carr_reader login createdb');
      const before = await state();
      let error;
      try {
        await promisify(execFile)(process.execPath, [...(fail ? ['--import', pathToFileURL(hook).href] : []), path.join(root, 'test/feature-switches-postgres.test.mjs')],
          { cwd: root, env, timeout: 180000, maxBuffer: 4 * 1024 * 1024 });
      } catch (caught) { error = caught; }
      if (fail) {
        assert.equal(error?.code, 1, error?.message || 'the injected fixture failure must fail the child suite');
        assert.match(error.stdout + error.stderr, /intentional feature fixture failure/);
      } else if (error) assert.fail(error.stdout + error.stderr);
      assert.deepEqual(await state(), before, 'provided-service roles, attributes, grants and databases are unchanged');
      await service.client.query('drop role if exists carr_reader');
    }
  } finally {
    try { await service.close(); } finally { await rm(directory, { recursive: true, force: true }); }
  }
});
