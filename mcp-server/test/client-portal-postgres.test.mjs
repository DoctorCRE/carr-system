import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';

const actor = { id: '00000000-0000-4000-8000-000000000001', slug: 'joe', human: true };

test('a password opens only its client portal and the live deal roadmap', async t => {
  const db = await postgresDatabase('client-portal-');
  const c = db.client;
  try {
    await c.query(await readFile(new URL('../../migrations/0001_init.sql', import.meta.url), 'utf8'));
    await c.query(await readFile(new URL('../../migrations/0008_client_merged_into.sql', import.meta.url), 'utf8'));
    await c.query('create schema ops; create role carr_reader; create role carr_writer; create role carr_authority; create extension pgcrypto');
    await c.query(await readFile(new URL('../../migrations/0974_client_portal.sql', import.meta.url), 'utf8'));
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner')", [actor.id]);
    await c.query("insert into client_status(slug,label) values('active','Active'); insert into deal_phase(slug,label,sort) values('research','Research',1),('negotiation','Negotiation',2)");
    const party = (await c.query("insert into party(kind,name,notes_path,created_by,updated_by) values('person','Synthetic client A','INTERNAL_A',$1,$1) returning id", [actor.id])).rows[0].id;
    const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id", [party, actor.id])).rows[0].id;
    const deal = (await c.query("insert into deal(client_id,name,deal_type,phase,notes_path,created_by,updated_by) values($1,'INTERNAL_DEAL_NAME','lease','research','INTERNAL_DEAL_NOTE',$2,$2) returning id", [client, actor.id])).rows[0].id;
    await c.query("insert into ops.client_portal(slug,client_id,deal_id,password_hash) values('synthetic-a',$1,$2,crypt(encode(digest('synthetic-password-a','sha256'),'hex'),gen_salt('bf',12)))", [client, deal]);
    await c.query("insert into ops.client_portal_material(portal_slug,kind,title,content,shared_at) values('synthetic-a','report','Shared A report','Synthetic A report content',now())");
    const read = async (slug = 'synthetic-a', password = 'synthetic-password-a', extra = {}) => {
      await c.query('set role carr_reader');
      try { return await executeRegisteredTool(c, actor, 'read-client-portal', { slug, password, ...extra }); }
      finally { await c.query('reset role'); }
    };
    const expected = {
      ok: true, schema_version: 'client-portal.v1',
      portal: { slug: 'synthetic-a', roadmap: { current_phase: 'research', phases: [{ slug: 'research', label: 'Research' }, { slug: 'negotiation', label: 'Negotiation' }] }, materials: [{ kind: 'report', title: 'Shared A report', content: 'Synthetic A report content' }] },
    };
    assert.deepEqual(await read(), expected);
    const denied = error => { assert.deepEqual(error.payload, { error: 'portal_access_denied' }); return true; };

    await t.test('other-client credentials never return either client material', async () => {
      const partyB = (await c.query("insert into party(kind,name,notes_path,created_by,updated_by) values('person','Synthetic client B','INTERNAL_B',$1,$1) returning id", [actor.id])).rows[0].id;
      const clientB = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id", [partyB, actor.id])).rows[0].id;
      const dealB = (await c.query("insert into deal(client_id,name,deal_type,phase,created_by,updated_by) values($1,'INTERNAL_B_DEAL','lease','negotiation',$2,$2) returning id", [clientB, actor.id])).rows[0].id;
      await c.query("insert into ops.client_portal(slug,client_id,deal_id,password_hash) values('synthetic-b',$1,$2,crypt(encode(digest('synthetic-password-b','sha256'),'hex'),gen_salt('bf',12)))", [clientB, dealB]);
      await c.query("insert into ops.client_portal_material(portal_slug,kind,title,content,shared_at) values('synthetic-b','tour','Shared B tour','Synthetic B tour content',now())");
      assert.deepEqual(await read('synthetic-b', 'synthetic-password-b'), {
        ...expected, portal: { slug: 'synthetic-b', roadmap: { ...expected.portal.roadmap, current_phase: 'negotiation' }, materials: [{ kind: 'tour', title: 'Shared B tour', content: 'Synthetic B tour content' }] },
      });
      await assert.rejects(read('synthetic-a', 'synthetic-password-b'), denied);
      await assert.rejects(read('synthetic-b', 'synthetic-password-a'), denied);
      await assert.rejects(read('missing', 'synthetic-password-a'), denied);
      assert.deepEqual(await read(), expected);
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
      await assert.rejects(read(), denied);
      await c.query("update ops.client_portal set disabled_at=null where slug='synthetic-a'");
      await c.query('update party set deleted_at=now() where id=$1', [party]);
      await assert.rejects(read(), denied);
      await c.query('update party set deleted_at=null,merged_into=(select id from party where id<>$1 limit 1) where id=$1', [party]);
      await assert.rejects(read(), denied);
      await c.query('update party set merged_into=null where id=$1', [party]);
      assert.deepEqual(await read(), expected);
    });

    await t.test('a merged client cannot keep reading through its previous portal', async () => {
      await c.query('update client set merged_into=(select id from client where id<>$1 limit 1) where id=$1', [client]);
      await assert.rejects(read(), denied);
      await c.query('update client set merged_into=null where id=$1', [client]);
      assert.deepEqual(await read(), expected);
    });

    await t.test('reassigning the deal cannot move its new client into the old portal', async () => {
      await c.query('update deal set client_id=(select id from client where id<>$1 limit 1) where id=$2', [client, deal]);
      await assert.rejects(read(), denied);
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
      await assert.rejects(read('synthetic-a', prefix + 'b'), denied);
      await c.query("update ops.client_portal set password_hash=crypt(encode(digest('synthetic-password-a','sha256'),'hex'),gen_salt('bf',12)) where slug='synthetic-a'");
    });

    await t.test('the reader can use the contract without accessing raw material or verifiers', async () => {
      await c.query('set role carr_reader');
      try {
        for (const table of ['ops.client_portal', 'ops.client_portal_material', 'public.deal', 'public.party'])
          await assert.rejects(c.query(`select * from ${table}`), { code: '42501' });
      } finally { await c.query('reset role'); }
      await c.query('create role portal_untrusted; grant usage on schema ops to portal_untrusted; set role portal_untrusted');
      try { await assert.rejects(c.query("select ops.read_client_portal('synthetic-a','synthetic-password-a')"), { code: '42501' }); }
      finally { await c.query('reset role'); }
      assert.deepEqual(await read(), expected);
    });

    await t.test('an oversized shared library refuses instead of silently hiding material', async () => {
      await c.query("insert into ops.client_portal_material(portal_slug,kind,title,content,shared_at) select 'synthetic-a','report','Synthetic report '||n,'Synthetic content',now() from generate_series(1,100) n");
      await assert.rejects(read(), denied);
    });
  } finally { await db.close(); }
});
