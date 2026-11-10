import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import pg from 'pg';
import { readFile } from 'node:fs/promises';
import { readMigration } from './migration-source.testhelper.ts';
import { postgresDatabase } from './helpers/postgres-database.mjs';

const digest = value => createHash('sha256').update(value).digest('hex');
test('slug-bound portal session statechart', async t => {
  const db = await postgresDatabase('portal-session-');
  const c = db.client;
  try {
    for (const name of ['0001_init.sql', '0008_client_merged_into.sql']) await c.query(readMigration(name));
    await c.query(`create schema ops; create role carr_reader; create role carr_writer;
      create role carr_authority; create role dot_reader; grant usage on schema ops to dot_reader;
      alter default privileges in schema ops grant select on tables to dot_reader; create extension pgcrypto`);
    for (const name of ['0975_client_portal.sql', '0976_client_portal_binding.sql', '0981_client_portal_writer_denials.sql']) await c.query(readMigration(name));
    const actor = '00000000-0000-4000-8000-000000000001';
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic')", [actor]);
    await c.query("insert into client_status(slug,label) values('active','Active'); insert into deal_phase(slug,label,sort) values('research','Research',1)");
    for (const slug of ['a', 'b']) {
      const party = (await c.query("insert into party(kind,name,notes_path,created_by,updated_by) values('person',$1,'INTERNAL_SENTINEL',$2,$2) returning id", [slug, actor])).rows[0].id;
      const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id", [party, actor])).rows[0].id;
      const deal = (await c.query("insert into deal(client_id,name,deal_type,phase,notes_path,created_by,updated_by) values($1,'INTERNAL_DEAL','lease','research','INTERNAL_SENTINEL',$2,$2) returning id", [client, actor])).rows[0].id;
      await c.query("insert into ops.client_portal(slug,client_id,deal_id,password_hash) values($1,$2,$3,crypt(encode(digest($4,'sha256'),'hex'),gen_salt('bf',12)))", [slug, client, deal, `password-${slug}`]);
      await c.query("insert into ops.client_portal_material(portal_slug,kind,title,content,shared_at) values($1,'report','Shared',$2,now()),($1,'report','Draft','INTERNAL_SENTINEL',null)", [slug, `CLIENT_${slug}_ONLY`]);
    }
    const canonical = (await c.query("select ops.read_client_portal('a','password-a') value")).rows[0].value;
    await c.query(readMigration('1152_client_portal_sessions.sql'));
    assert.deepEqual((await c.query("select ops.read_client_portal('a','password-a') value")).rows[0].value, canonical);
    const call = async (fn, args, client = c) => (await client.query(`select ops.${fn}(${args.map((_, i) => `$${i + 1}::text`).join(',')}) value`, args)).rows[0].value;
    const exchange = (slug = 'a', token = 'one', password = `password-${slug}`, client = c) => call('exchange_client_portal_session', [slug, password, digest(token)], client);
    const read = (slug = 'a', token = 'one') => call('read_client_portal_session', [slug, digest(token)]);
    const revoke = (slug = 'a', token = 'one') => call('revoke_client_portal_session', [slug, digest(token)]);
    const denied = { status: 'denied', error: 'portal_access_denied' };
    const reset = () => c.query("update ops.client_portal set failed_attempts=0,attempt_window_started_at=null where slug in ('a','b')");
    await t.test('anonymous, active, replay, cross-client and canonical private projection', async () => {
      assert.deepEqual(await read(), denied);
      const issued = await exchange();
      assert.equal(issued.status, 'success');
      assert.deepEqual(await exchange(), issued);
      assert.deepEqual(await read(), { status: 'success', schema_version: 'client-portal.v1', portal: canonical });
      assert.deepEqual(await read('b'), denied);
      assert.deepEqual(await exchange('b', 'one'), denied);
      assert.equal((await exchange('b', 'b-one')).status, 'success');
      const result = JSON.stringify(await read());
      for (const secret of ['INTERNAL', 'CLIENT_b_ONLY', 'password-a']) assert.ok(!result.includes(secret));
    });
    await t.test('revoked and expired sessions never revive on retained replay', async () => {
      assert.deepEqual(await revoke('b'), denied);
      assert.deepEqual(await revoke(), { status: 'success' });
      const timestamps = async () => (await c.query("select revoked_at::text,created_at::text,expires_at::text from ops.client_portal_session where digest=$1", [digest('one')])).rows[0];
      const first = await timestamps();
      assert.ok(first.revoked_at);
      await c.query('select pg_sleep(0.01)');
      assert.deepEqual(await revoke(), { status: 'success' });
      assert.deepEqual(await timestamps(), first, 'repeat revoke preserves every timestamp');
      assert.deepEqual(await read(), denied);
      assert.deepEqual(await exchange(), denied);
      await exchange('a', 'expired');
      await c.query("update ops.client_portal_session set created_at=now()-interval '192 hours',expires_at=now()-interval '24 hours' where digest=$1", [digest('expired')]);
      assert.deepEqual(await read('a', 'expired'), denied);
      assert.deepEqual(await exchange('a', 'expired'), denied);
    });
    await t.test('password changes atomically revoke all sessions only for that portal', async () => {
      for (const token of ['rotation-one', 'rotation-two']) assert.equal((await exchange('a', token)).status, 'success');
      const timestamps = async () => (await c.query("select digest,revoked_at::text,created_at::text,expires_at::text from ops.client_portal_session where portal_slug='a' order by digest")).rows;
      const before = await timestamps();
      const rotate = () => c.query("update ops.client_portal set password_hash=crypt(encode(digest('changed-password','sha256'),'hex'),gen_salt('bf',12)) where slug='a'");
      await c.query("update ops.client_portal set password_hash=password_hash where slug='a'");
      assert.deepEqual(await timestamps(), before, 'unchanged hash does not revoke');
      await c.query('begin');
      try {
        await rotate();
        assert.deepEqual(await read('a', 'rotation-one'), denied, 'rotation revokes inside the password transaction');
      } finally { await c.query('rollback'); }
      assert.deepEqual(await timestamps(), before, 'rollback restores password and sessions together');
      assert.equal((await read('a', 'rotation-one')).status, 'success');
      await rotate();
      const after = await timestamps();
      for (const [i, row] of after.entries()) {
        assert.ok(row.revoked_at, 'every existing session is revoked');
        assert.equal(row.created_at, before[i].created_at);
        assert.equal(row.expires_at, before[i].expires_at);
        if (before[i].revoked_at) assert.equal(row.revoked_at, before[i].revoked_at);
      }
      for (const token of ['rotation-one', 'rotation-two']) {
        assert.deepEqual(await read('a', token), denied);
        assert.deepEqual(await exchange('a', token, 'changed-password'), denied);
      }
      assert.deepEqual(await exchange('a', 'old-password'), denied);
      assert.equal((await read('b', 'b-one')).status, 'success');
      assert.equal((await exchange('a', 'changed-session', 'changed-password')).status, 'success');
      await c.query("update ops.client_portal set password_hash=crypt(encode(digest('password-a','sha256'),'hex'),gen_salt('bf',12)) where slug='a'");
      assert.deepEqual(await read('a', 'changed-session'), denied);
      assert.equal((await exchange('a', 'restored-session')).status, 'success');
    });
    await t.test('disabled portals and reassigned clients refuse live sessions', async () => {
      await exchange('a', 'live');
      await c.query("update ops.client_portal set disabled_at=now() where slug='a'");
      assert.deepEqual(await read('a', 'live'), denied);
      assert.deepEqual(await exchange('a', 'live'), denied);
      await c.query("update ops.client_portal set disabled_at=null where slug='a'");
      await c.query('begin');
      await c.query("update deal set client_id=(select client_id from ops.client_portal where slug='b') where id=(select deal_id from ops.client_portal where slug='a')");
      assert.deepEqual(await read('a', 'live'), denied);
      await c.query('rollback');
      for (const change of [
        "update party set deleted_at=now() where id=(select party_id from client where id=(select client_id from ops.client_portal where slug='a'))",
        "update client set merged_into=(select client_id from ops.client_portal where slug='b') where id=(select client_id from ops.client_portal where slug='a')",
      ]) {
        await c.query('begin');
        await c.query(change);
        assert.deepEqual(await read('a', 'live'), denied);
        await c.query('rollback');
      }
    });
    await t.test('five failures lock only the slug with retry time; old reader shares allowance', async () => {
      await reset();
      for (let i = 0; i < 4; i++) assert.deepEqual(await exchange('a', `bad-${i}`, 'wrong'), denied);
      const locked = await exchange('a', 'bad-last', 'wrong');
      assert.equal(locked.status, 'locked');
      const databaseNow = (await c.query('select clock_timestamp() value')).rows[0].value;
      assert.ok(Date.parse(locked.retry_at) > databaseNow.getTime());
      assert.deepEqual(await exchange('a', 'locked'), locked);
      assert.equal((await read('a', 'live')).status, 'success');
      assert.equal((await exchange('b', 'b-two')).status, 'success');
      await c.query("update ops.client_portal set attempt_window_started_at=now()-interval '16 minutes' where slug='a'");
      assert.equal((await exchange('a', 'unlocked')).status, 'success');
      for (let i = 0; i < 4; i++) await c.query("select ops.read_client_portal('a','wrong')");
      assert.equal((await exchange('a', 'shared-limit', 'wrong')).status, 'locked');
      await reset();
      for (let round = 0; round < 2; round++) {
        for (let i = 0; i < 4; i++) assert.deepEqual(await exchange('a', 'reset-test', 'wrong'), denied);
        assert.equal((await exchange('a', 'live')).status, 'success');
      }
    });
    await t.test('concurrent failures cannot exceed five and concurrent replay issues one row', async () => {
      const clients = Array.from({ length: 7 }, () => new pg.Client(c.connectionParameters));
      try {
        await Promise.all(clients.map(client => client.connect()));
        const results = await Promise.all(clients.map((client, i) => exchange('a', `race-${i}`, 'wrong', client)));
        assert.equal(results.filter(row => row.status === 'denied').length, 4);
        assert.equal(results.filter(row => row.status === 'locked').length, 3);
        assert.equal((await c.query("select failed_attempts from ops.client_portal where slug='a'")).rows[0].failed_attempts, 5);
        await reset();
        const replays = await Promise.all(clients.map(client => exchange('a', 'race-valid', 'password-a', client)));
        for (const row of replays) assert.deepEqual(row, replays[0]);
        assert.equal(replays[0].status, 'success');
        assert.equal((await c.query('select count(*)::int n from ops.client_portal_session where digest=$1', [digest('race-valid')])).rows[0].n, 1);
      } finally { await Promise.all(clients.map(client => client.end())); }
    });
    await t.test('bounded capacity, expiry cleanup and fixed expiry', async () => {
      await c.query("delete from ops.client_portal_session where portal_slug='a'");
      for (let i = 0; i < 10; i++) assert.equal((await exchange('a', `cap-${i}`)).status, 'success');
      assert.deepEqual(await exchange('a', 'overflow'), denied);
      assert.equal((await c.query("select count(*)::int n from ops.client_portal_session where portal_slug='a'")).rows[0].n, 10);
      await c.query("update ops.client_portal_session set created_at=now()-interval '192 hours',expires_at=now()-interval '24 hours' where portal_slug='a'");
      assert.equal((await exchange('a', 'after-cleanup')).status, 'success');
      assert.equal((await c.query("select count(*)::int n from ops.client_portal_session where portal_slug='a'")).rows[0].n, 1);
      assert.deepEqual(await exchange('a', 'cap-0'), denied);
      assert.deepEqual(await exchange('b', 'cap-0'), denied);
      assert.equal((await c.query("select bool_and(extract(epoch from expires_at-created_at)=604800) ok from ops.client_portal_session")).rows[0].ok, true);
    });
    await t.test('boundary validation, digest-only storage, private tables and function-only grants', async () => {
      for (const args of [['Bad Slug', 'password-a', digest('x')], ['a', '', digest('x')], ['a', 'password-a', 'plaintext'], ['a', null, digest('x')]]) assert.deepEqual(await call('exchange_client_portal_session', args), denied);
      for (const role of ['carr_reader', 'carr_writer', 'dot_reader']) {
        await c.query(`set role ${role}`);
        try {
          await assert.rejects(c.query('select * from ops.client_portal_session'), { code: '42501' });
          await assert.rejects(c.query('select * from ops.client_portal_session_digest'), { code: '42501' });
          await assert.rejects(c.query("select ops.client_portal_projection('a')"), { code: '42501' });
          if (role === 'carr_writer') {
            assert.equal((await read('a', 'after-cleanup')).status, 'success');
            assert.equal((await exchange('a', 'writer')).status, 'success');
            assert.equal((await revoke('a', 'writer')).status, 'success');
          }
          else await assert.rejects(read('a', 'after-cleanup'), { code: '42501' });
        } finally { await c.query('reset role'); }
      }
      const columns = (await c.query("select column_name from information_schema.columns where table_schema='ops' and table_name='client_portal_session'")).rows.map(row => row.column_name);
      assert.deepEqual(columns.sort(), ['created_at', 'digest', 'expires_at', 'portal_slug', 'revoked_at']);
      const contract = (await c.query('select ops.scac_current_grant_contract() value')).rows[0].value;
      assert.equal(contract.secdef_execute.filter(row => /ops\.(exchange|read|revoke)_client_portal_session\(/.test(row.signature)).length, 3);
    });
    await t.test('design bounds and unit B contract agree with the installed interface', async () => {
      const contract = JSON.parse(await readFile(new URL('../../workspace/contracts/client-portal-session.v1.json', import.meta.url), 'utf8'));
      assert.equal(contract.expiry_seconds, 604800);
      assert.deepEqual(Object.keys(contract.operations), ['exchange', 'read', 'revoke']);
      for (const [operation, spec] of Object.entries(contract.operations)) {
        assert.match(spec.sql, new RegExp(`ops.${operation}_client_portal_session`));
        assert.ok(spec.results.success);
        assert.deepEqual(spec.results.denied, denied);
      }
      const indexes = (await c.query("select indexdef from pg_indexes where schemaname='ops' and tablename='client_portal_session'")).rows.map(row => row.indexdef).join('\n');
      assert.match(indexes, /UNIQUE INDEX.*\(digest\)/);
      assert.match(indexes, /\(portal_slug, expires_at\)/);
      const bodies = (await c.query("select prosrc from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='ops' and (p.proname like '%client_portal_session' or p.proname='client_portal_projection')")).rows.map(row => row.prosrc).join('\n');
      assert.doesNotMatch(bodies, /\b(loop|http|dblink|pg_notify)\b/i);
      await assert.rejects(c.query("insert into ops.client_portal_session(digest,portal_slug,created_at,expires_at) values('raw-token','a',now(),now()+interval '7 days')"), { code: '23514' });
      await assert.rejects(c.query("insert into ops.client_portal_session(digest,portal_slug,created_at,expires_at) values($1,'a',now(),now()+interval '192 hours')", [digest('bad-expiry')]), { code: '23514' });
    });
    await t.test('database failure propagates without content and oversized material refuses', async () => {
      await c.query('begin');
      await c.query('alter table ops.client_portal_material rename to unavailable_material');
      await assert.rejects(read('a', 'after-cleanup'), { code: '42P01' });
      await c.query('rollback');
      await c.query("insert into ops.client_portal_material(portal_slug,kind,title,content,shared_at) select 'a','report','Shared','Synthetic',now() from generate_series(1,100)");
      assert.deepEqual(await read('a', 'after-cleanup'), denied);
    });
  } finally { await db.close(); }
});
