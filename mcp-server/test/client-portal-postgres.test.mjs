// Tests client portal authentication and deal access against PostgreSQL.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import pg from 'pg';
import { Pool, neonConfig } from '@neondatabase/serverless';
import { callTool } from '../src/mcp.js';
import { authenticatedIdentity } from '../src/identity.js';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { SCAC_MUTATION_CURRENT_DB_GRANTS } from '../src/scac-mutation-registry.current.generated.js';

const actor = { id: '00000000-0000-4000-8000-000000000001', slug: 'joe', human: true };

test('a password opens only its client portal and the live deal roadmap', async t => {
  const db = await postgresDatabase('client-portal-');
  const c = db.client;
  try {
    await c.query(await readFile(new URL('../../migrations/0001_init.sql', import.meta.url), 'utf8'));
    await c.query(await readFile(new URL('../../migrations/0008_client_merged_into.sql', import.meta.url), 'utf8'));
    await c.query('create schema ops; create role carr_reader; create role carr_writer; create role carr_authority; create role dot_reader; grant usage on schema ops to dot_reader; alter default privileges in schema ops grant select on tables to dot_reader; create extension pgcrypto');
    await c.query(await readFile(new URL('../../migrations/0975_client_portal.sql', import.meta.url), 'utf8'));
    await c.query(await readFile(new URL('../../migrations/0976_client_portal_binding.sql', import.meta.url), 'utf8'));
    await c.query(await readFile(new URL('../../migrations/0981_client_portal_writer_denials.sql', import.meta.url), 'utf8'));
    await c.query(await readFile(new URL('../../migrations/1016_list_client_portals.sql', import.meta.url), 'utf8'));
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner')", [actor.id]);
    await c.query("insert into client_status(slug,label) values('active','Active'); insert into deal_phase(slug,label,sort) values('research','Research',1),('negotiation','Negotiation',2)");
    const party = (await c.query("insert into party(kind,name,notes_path,created_by,updated_by) values('person','Synthetic client A','INTERNAL_A',$1,$1) returning id", [actor.id])).rows[0].id;
    const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id", [party, actor.id])).rows[0].id;
    const deal = (await c.query("insert into deal(client_id,name,deal_type,phase,notes_path,created_by,updated_by) values($1,'INTERNAL_DEAL_NAME','lease','research','INTERNAL_DEAL_NOTE',$2,$2) returning id", [client, actor.id])).rows[0].id;
    await c.query("insert into ops.client_portal(slug,client_id,deal_id,password_hash) values('synthetic-a',$1,$2,crypt(encode(digest('synthetic-password-a','sha256'),'hex'),gen_salt('bf',12)))", [client, deal]);
    await c.query("insert into ops.client_portal_material(portal_slug,kind,title,content,shared_at) values('synthetic-a','report','Shared A report','Synthetic A report content',now())");
    await t.test('partners can list registered portals without credentials', async () => {
      await c.query('set role carr_reader');
      try {
        const result = await executeRegisteredTool(c, actor, 'list-client-portals', {});
        assert.equal(result.portals.length, 1);
        assert.equal(result.portals[0].slug, 'synthetic-a');
        assert.equal(result.portals[0].client_name, 'Synthetic client A');
        assert.equal(result.portals[0].deal_id, deal);
        assert.equal(result.portals[0].deal_name, 'INTERNAL_DEAL_NAME');
        assert.equal(result.portals[0].client_portal_url, 'https://share.doctorcre.com/synthetic-a');
        assert.equal(result.portals[0].status, 'active');
        assert.equal(result.portals[0].locked, false);
        assert.ok(result.portals[0].registered_at);
        assert.ok(result.portals[0].updated_at);
        assert.equal('password_hash' in result.portals[0], false);
        assert.equal('password' in result.portals[0], false);
      } finally { await c.query('reset role'); }
      await c.query("update ops.client_portal set disabled_at=now(), failed_attempts=5, attempt_window_started_at=now() where slug='synthetic-a'");
      await c.query('set role carr_reader');
      try {
        const row = (await executeRegisteredTool(c, actor, 'list-client-portals', {})).portals[0];
        assert.equal(row.status, 'disabled');
        assert.equal(row.locked, true);
        assert.ok(row.updated_at);
      } finally { await c.query('reset role'); }
      await c.query("update ops.client_portal set disabled_at=null, failed_attempts=0, attempt_window_started_at=null where slug='synthetic-a'");
      await c.query('set role dot_reader');
      try { await assert.rejects(c.query('select * from v_client_portal_list'), /permission denied/); }
      finally { await c.query('reset role'); }
    });
    const read = async (slug = 'synthetic-a', password = 'synthetic-password-a', extra = {}) => {
      await c.query('set role carr_writer');
      try { return await executeRegisteredTool(c, actor, 'read-client-portal', { slug, password, ...extra }); }
      finally { await c.query('reset role'); }
    };
    const expected = {
      ok: true, schema_version: 'client-portal.v1',
      portal: { slug: 'synthetic-a', roadmap: { current_phase: 'research', phases: [{ slug: 'research', label: 'Research' }, { slug: 'negotiation', label: 'Negotiation' }] }, materials: [{ kind: 'report', title: 'Shared A report', content: 'Synthetic A report content' }] },
    };
    assert.deepEqual(await read(), expected);
    const denied = { ok: false, error: 'portal_access_denied' };

    await t.test('portal update time ignores password attempts but tracks portal changes', async () => {
      const updatedAt = async () => (await executeRegisteredTool(c, actor, 'list-client-portals', {})).portals[0].updated_at;
      const before = await updatedAt();
      assert.deepEqual(await read('synthetic-a', 'wrong-password'), denied);
      assert.deepEqual(await updatedAt(), before);
      assert.deepEqual(await read(), expected);
      assert.deepEqual(await updatedAt(), before);
      await c.query("update ops.client_portal set disabled_at=clock_timestamp() where slug='synthetic-a'");
      assert.ok((await updatedAt()) > before);
      await c.query("update ops.client_portal set disabled_at=null where slug='synthetic-a'");
    });

    await t.test('the real writer route commits denials and locks the portal after five failures', async t => {
      await c.query('create role carr_jobs; alter role carr_writer login; grant select on actor to carr_writer');
      const writer = new pg.Client({ ...c.connectionParameters, user: 'carr_writer' });
      await writer.connect();
      t.mock.method(Pool.prototype, 'connect', async () => ({ query: writer.query.bind(writer), release() {} }));
      t.mock.method(Pool.prototype, 'end', async () => {});
      const previousFetch = neonConfig.fetchFunction;
      neonConfig.fetchFunction = async () => { throw new Error('portal authentication must use the writer route'); };
      t.after(() => { neonConfig.fetchFunction = previousFetch; });
      const call = password => callTool({ DATABASE_URL_READER: 'postgresql://carr_reader@fixture.invalid/postgres', DATABASE_URL_WRITER: 'postgresql://carr_writer@fixture.invalid/postgres' },
        authenticatedIdentity.connectionForGrant({ slug: 'joe' }), 'read-client-portal', { slug: 'synthetic-a', password });
      try {
        assert.deepEqual(await call('synthetic-password-a'), expected);
        for (let attempt = 0; attempt < 5; attempt++)
          assert.deepEqual(await call('wrong-password'), { ok: false, error: 'portal_access_denied' });
        assert.deepEqual(await call('synthetic-password-a'), { ok: false, error: 'portal_access_denied' });
        assert.equal((await c.query("select failed_attempts from ops.client_portal where slug='synthetic-a'")).rows[0].failed_attempts, 5);
      } finally {
        await writer.end();
        await c.query("update ops.client_portal set failed_attempts=0,attempt_window_started_at=null where slug='synthetic-a'");
      }
    });

    await t.test('locked and unknown slugs both pay for one bcrypt verification', async () => {
      await c.query('begin');
      try {
        await c.query("set local track_functions='all'");
        await c.query("update ops.client_portal set failed_attempts=5,attempt_window_started_at=clock_timestamp() where slug='synthetic-a'");
        const hashes = async () => Number((await c.query("select coalesce(sum(calls),0) as calls from pg_stat_xact_user_functions where funcid='public.crypt(text,text)'::regprocedure")).rows[0].calls);
        for (const slug of ['missing', 'synthetic-a']) {
          const before = await hashes();
          assert.equal((await c.query('select ops.read_client_portal($1,$2) as portal', [slug, 'wrong-password'])).rows[0].portal, null);
          assert.equal(await hashes() - before, 1, `${slug} denial must pay the bcrypt cost`);
        }
      } finally { await c.query('rollback'); }
    });

    await t.test('real and unknown slugs verify concurrently and password changes invalidate in-flight guesses', async () => {
      const callers = Array.from({ length: 3 }, () => new pg.Client(c.connectionParameters));
      const barrier = 2321;
      let pending = [];
      await c.query(`alter function public.crypt(text,text) rename to portal_fixture_crypt;
        create function public.crypt(text,text) returns text language plpgsql as $body$
        begin
          perform pg_advisory_xact_lock_shared(${barrier});
          return public.portal_fixture_crypt($1,$2);
        end $body$`);
      try {
        await Promise.all(callers.map(caller => caller.connect()));
        const pids = await Promise.all(callers.map(async caller =>
          (await caller.query('select pg_backend_pid() as pid')).rows[0].pid));
        const allVerifying = async slug => {
          const deadline = performance.now() + 5000;
          let verifying = 0;
          while (performance.now() < deadline) {
            verifying = Number((await c.query(`select count(*) as n from pg_locks
              where pid=any($1::int[]) and locktype='advisory' and not granted`, [pids])).rows[0].n);
            if (verifying === callers.length) break;
            await new Promise(resolve => setTimeout(resolve, 10));
          }
          assert.equal(verifying, callers.length, `${slug} must let every caller reach bcrypt concurrently`);
        };
        for (const slug of ['missing', 'synthetic-a']) {
          await c.query('select pg_advisory_lock($1)', [barrier]);
          pending = callers.map(caller => caller.query('select ops.read_client_portal($1,$2) as portal', [slug, 'wrong-password']));
          await allVerifying(slug);
          await c.query('select pg_advisory_unlock($1)', [barrier]);
          for (const result of await Promise.all(pending)) assert.equal(result.rows[0].portal, null);
          pending = [];
        }
        await c.query("update ops.client_portal set failed_attempts=0,attempt_window_started_at=null where slug='synthetic-a'");
        await c.query('select pg_advisory_lock($1)', [barrier]);
        pending = callers.map(caller => caller.query('select ops.read_client_portal($1,$2) as portal', ['synthetic-a', 'synthetic-password-a']));
        await allVerifying('synthetic-a');
        await c.query("update ops.client_portal set password_hash=public.portal_fixture_crypt(encode(digest('changed-password','sha256'),'hex'),gen_salt('bf',12)) where slug='synthetic-a'");
        await c.query('select pg_advisory_unlock($1)', [barrier]);
        for (const result of await Promise.all(pending)) assert.equal(result.rows[0].portal, null);
        pending = [];
        assert.equal((await c.query("select failed_attempts from ops.client_portal where slug='synthetic-a'")).rows[0].failed_attempts, 0);
      } finally {
        await c.query('select pg_advisory_unlock_all()');
        await Promise.allSettled(pending);
        await Promise.all(callers.map(caller => caller.end()));
        await c.query(`drop function public.crypt(text,text);
          alter function public.portal_fixture_crypt(text,text) rename to crypt;
          update ops.client_portal set password_hash=crypt(encode(digest('synthetic-password-a','sha256'),'hex'),gen_salt('bf',12)),
            failed_attempts=0,attempt_window_started_at=null where slug='synthetic-a'`);
      }
    });

    await t.test('the installed portal grant contract matches the runtime contract', async () => {
      const portalGrants = contract => contract.secdef_execute.filter(grant => grant.signature.startsWith('ops.read_client_portal('));
      assert.deepEqual(portalGrants((await c.query('select ops.scac_current_grant_contract() as contract')).rows[0].contract),
        portalGrants(SCAC_MUTATION_CURRENT_DB_GRANTS));
    });

    await t.test('the portal definer follows the canonical trusted search path policy', async () => {
      const policy = (await readFile(new URL('./security-definer-search-path-postgres.sql', import.meta.url), 'utf8'))
        .split('-- Negatives exercise')[0].replace(/^\\set.*$/gm, '');
      try { await c.query(policy); }
      finally { await c.query('rollback'); }
    });

    await t.test('other-client credentials never return either client material', async () => {
      const partyB = (await c.query("insert into party(kind,name,notes_path,created_by,updated_by) values('person','Synthetic client B','INTERNAL_B',$1,$1) returning id", [actor.id])).rows[0].id;
      const clientB = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id", [partyB, actor.id])).rows[0].id;
      const dealB = (await c.query("insert into deal(client_id,name,deal_type,phase,created_by,updated_by) values($1,'INTERNAL_B_DEAL','lease','negotiation',$2,$2) returning id", [clientB, actor.id])).rows[0].id;
      await c.query("insert into ops.client_portal(slug,client_id,deal_id,password_hash) values('synthetic-b',$1,$2,crypt(encode(digest('synthetic-password-b','sha256'),'hex'),gen_salt('bf',12)))", [clientB, dealB]);
      await c.query("insert into ops.client_portal_material(portal_slug,kind,title,content,shared_at) values('synthetic-b','tour','Shared B tour','Synthetic B tour content',now())");
      assert.deepEqual(await read('synthetic-b', 'synthetic-password-b'), {
        ...expected, portal: { slug: 'synthetic-b', roadmap: { ...expected.portal.roadmap, current_phase: 'negotiation' }, materials: [{ kind: 'tour', title: 'Shared B tour', content: 'Synthetic B tour content' }] },
      });
      assert.deepEqual(await read('synthetic-a', 'synthetic-password-b'), denied);
      assert.deepEqual(await read('synthetic-b', 'synthetic-password-a'), denied);
      assert.deepEqual(await read('missing', 'synthetic-password-a'), denied);
      assert.deepEqual(await read(), expected);
    });

    await t.test('five failed passwords lock only that slug even with its correct password', async () => {
      for (let attempt = 0; attempt < 5; attempt++)
        assert.deepEqual(await read('synthetic-a', 'wrong-password'), denied);
      assert.deepEqual(await read(), denied);
      assert.equal((await read('synthetic-b', 'synthetic-password-b')).portal.slug, 'synthetic-b');
      await c.query("update ops.client_portal set attempt_window_started_at=clock_timestamp()-interval '16 minutes' where slug='synthetic-a'");
      assert.deepEqual(await read(), expected);
    });

    await t.test('a correct password resets the failed password allowance before lockout', async () => {
      for (let round = 0; round < 2; round++) {
        for (let attempt = 0; attempt < 4; attempt++)
          assert.deepEqual(await read('synthetic-a', 'wrong-password'), denied);
        assert.deepEqual(await read(), expected);
      }
    });

    await t.test('concurrent failed passwords share one attempt allowance', async () => {
      const callers = Array.from({ length: 5 }, () => new pg.Client(c.connectionParameters));
      try {
        await Promise.all(callers.map(async caller => {
          await caller.connect();
          await caller.query('set role carr_writer');
          assert.deepEqual(await executeRegisteredTool(caller, actor, 'read-client-portal', {
            slug: 'synthetic-a', password: 'wrong-password',
          }), denied);
        }));
        assert.deepEqual(await read(), denied);
      } finally {
        await Promise.all(callers.map(caller => caller.end()));
        await c.query("update ops.client_portal set attempt_window_started_at=clock_timestamp()-interval '16 minutes' where slug='synthetic-a'");
      }
      assert.deepEqual(await read(), expected);
    });

    await t.test('portal ownership cannot be changed after creation', async () => {
      for (const assignment of [
        "client_id=(select client_id from ops.client_portal where slug='synthetic-b')",
        "deal_id=(select deal_id from ops.client_portal where slug='synthetic-b')",
        "client_id=(select client_id from ops.client_portal where slug='synthetic-b'),deal_id=(select deal_id from ops.client_portal where slug='synthetic-b')",
      ]) {
        await c.query('begin');
        try { await assert.rejects(c.query(`update ops.client_portal set ${assignment} where slug='synthetic-a'`), { code: '23514' }); }
        finally { await c.query('rollback'); }
        assert.deepEqual(await read(), expected);
      }
      await c.query("update ops.client_portal set client_id=client_id,deal_id=deal_id where slug='synthetic-a'");
    });

    await t.test('Dot cannot read portal verifiers or raw material', async () => {
      await c.query('set role dot_reader');
      try {
        for (const table of ['ops.client_portal', 'ops.client_portal_material'])
          await assert.rejects(c.query(`select * from ${table}`), { code: '42501' });
      } finally { await c.query('reset role'); }
    });

    await t.test('unpublished future and withdrawn material stays private', async () => {
      await c.query("insert into ops.client_portal_material(portal_slug,kind,title,content,shared_at,withdrawn_at) values('synthetic-a','document','Draft','INTERNAL_DRAFT',null,null),('synthetic-a','document','Withdrawn','INTERNAL_WITHDRAWN',now(),now()),('synthetic-a','document','Future','INTERNAL_FUTURE',now()+interval '1 day',null)");
      assert.deepEqual(await read(), expected);
    });

    await t.test('current phase and phase labels come from canonical records', async () => {
      await c.query("update deal set phase='negotiation' where id=$1", [deal]);
      await c.query("update deal_phase set label='Active negotiation' where slug='negotiation'");
      assert.deepEqual((await read()).portal.roadmap, { current_phase: 'negotiation', phases: [{ slug: 'research', label: 'Research' }, { slug: 'negotiation', label: 'Active negotiation' }] });
      await c.query("update deal set phase='research' where id=$1", [deal]);
      await c.query("update deal_phase set label='Negotiation' where slug='negotiation'");
    });

    await t.test('disabled portals and tombstoned clients use the same refusal', async () => {
      await c.query("update ops.client_portal set disabled_at=now() where slug='synthetic-a'");
      assert.deepEqual(await read(), denied);
      await c.query("update ops.client_portal set disabled_at=null where slug='synthetic-a'");
      await c.query('update party set deleted_at=now() where id=$1', [party]);
      assert.deepEqual(await read(), denied);
      await c.query('update party set deleted_at=null,merged_into=(select id from party where id<>$1 limit 1) where id=$1', [party]);
      assert.deepEqual(await read(), denied);
      await c.query('update party set merged_into=null where id=$1', [party]);
      assert.deepEqual(await read(), expected);
    });

    await t.test('a merged client cannot keep reading through its previous portal', async () => {
      await c.query('update client set merged_into=(select id from client where id<>$1 limit 1) where id=$1', [client]);
      assert.deepEqual(await read(), denied);
      await c.query('update client set merged_into=null where id=$1', [client]);
      assert.deepEqual(await read(), expected);
    });

    await t.test('reassigning the deal cannot move its new client into the old portal', async () => {
      await c.query('update deal set client_id=(select id from client where id<>$1 limit 1) where id=$2', [client, deal]);
      assert.deepEqual(await read(), denied);
      await c.query('update deal set client_id=$1 where id=$2', [client, deal]);
      assert.deepEqual(await read(), expected);
    });

    await t.test('client and deal selectors and malformed input never bypass scope or expose the password', async () => {
      for (const extra of [{ client_id: client }, { deal_id: deal }, { material_id: 1 }]) {
        await assert.rejects(read('synthetic-a', 'synthetic-password-a', extra), error => {
          assert.equal(error.payload.error, 'unregistered_operation_fields');
          assert.ok(!JSON.stringify(error.payload).includes('synthetic-password-a'));
          return true;
        });
      }
      for (const [slug, password] of [["synthetic-a' or true --", 'synthetic-password-a'], ['synthetic-a', 'x'.repeat(1025)]]) {
        await assert.rejects(read(slug, password), error => {
          assert.equal(error.payload.error, 'invalid_portal_request');
          assert.deepEqual(Object.keys(error.payload), ['error']);
          return true;
        });
      }
      await assert.rejects(read('synthetic-a', ''), error => {
        assert.equal(error.payload.error, 'missing_required');
        assert.ok(!Object.hasOwn(error.payload, 'received'));
        return true;
      });
    });

    await t.test('passwords differing after 72 UTF-8 bytes remain distinct', async () => {
      const prefix = 'é'.repeat(40);
      await c.query("update ops.client_portal set password_hash=crypt(encode(digest($1,'sha256'),'hex'),gen_salt('bf',12)) where slug='synthetic-a'", [prefix + 'a']);
      assert.deepEqual(await read('synthetic-a', prefix + 'a'), expected);
      assert.deepEqual(await read('synthetic-a', prefix + 'b'), denied);
      await c.query("update ops.client_portal set password_hash=crypt(encode(digest('synthetic-password-a','sha256'),'hex'),gen_salt('bf',12)) where slug='synthetic-a'");
    });

    await t.test('the writer can use the contract without accessing raw material or verifiers', async () => {
      await c.query('set role carr_writer');
      try {
        for (const table of ['ops.client_portal', 'ops.client_portal_material', 'public.deal', 'public.party'])
          await assert.rejects(c.query(`select * from ${table}`), { code: '42501' });
        await assert.rejects(c.query("update ops.client_portal set failed_attempts=0,attempt_window_started_at=null where slug='synthetic-a'"), { code: '42501' });
      } finally { await c.query('reset role'); }
      await c.query('create role portal_untrusted; grant usage on schema ops to portal_untrusted; set role portal_untrusted');
      try { await assert.rejects(c.query("select ops.read_client_portal('synthetic-a','synthetic-password-a')"), { code: '42501' }); }
      finally { await c.query('reset role'); }
      assert.deepEqual(await read(), expected);
    });

    await t.test('an oversized shared library refuses instead of silently hiding material', async () => {
      await c.query("insert into ops.client_portal_material(portal_slug,kind,title,content,shared_at) select 'synthetic-a','report','Synthetic report '||n,'Synthetic content',now() from generate_series(1,100) n");
      assert.deepEqual(await read(), denied);
    });
  } finally { await db.close(); }
});
