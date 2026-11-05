import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { runMigrationFixtures } from '../portal-parity/migration-fixtures.mjs';

function portals() {
  return JSON.parse(execFileSync('python3', ['-c', `import importlib.util,json
s=importlib.util.spec_from_file_location('r','tools/portal-migration-rehearsal.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
print(json.dumps(m.prepare(json.load(open('client-portal/fixtures/migration-rehearsal.json')))))`],
  { cwd: new URL('../../', import.meta.url), encoding: 'utf8' }));
}

test('candidate exercises real registration and names unimplemented migration steps', async () => {
  const report = await runMigrationFixtures(portals());
  assert.equal(report.status, 'pending-integration');
  assert.equal(report.steps['portal-registration'].status, 'PASS');
  assert.equal(report.steps['portal-registration'].portals.length, 4);
  for (const step of ['package-publication', 'bundle-alias-import', 'selection-import', 'selection-persistence', 'http-parity'])
    assert.equal(report.steps[step].status, 'pending', step);
});

test('mutating the real SQL registration write fails the rehearsal by name', async () => {
  await assert.rejects(runMigrationFixtures(portals(), {
    afterInstall: async c => {
      const original = (await c.query("select pg_get_functiondef('ops.register_client_portal(uuid,text,text)'::regprocedure) as source")).rows[0].source;
      const mutated = original.replace('values (p_slug,owner_client,p_deal_id,', "values (p_slug||'-wrong',owner_client,p_deal_id,");
      assert.notEqual(mutated, original, 'mutation must change the real write');
      await c.query(mutated);
    },
  }), /portal-registration:/);
});

test('mutating the real SQL password save fails the rehearsal by name', async () => {
  await assert.rejects(runMigrationFixtures(portals(), {
    afterInstall: async c => {
      const original = (await c.query("select pg_get_functiondef('ops.register_client_portal(uuid,text,text)'::regprocedure) as source")).rows[0].source;
      const mutated = original.replace("public.digest(p_password,'sha256')", "public.digest('broken-synthetic-password','sha256')");
      assert.notEqual(mutated, original, 'mutation must change the real password write');
      await c.query(mutated);
    },
  }), /portal-registration:/);
});
