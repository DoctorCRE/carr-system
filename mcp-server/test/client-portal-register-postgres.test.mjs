// Tests registering a client portal for a deal, and the derived link the Deal Room reads.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { readFile } from 'node:fs/promises';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { SCAC_MUTATION_CURRENT_DB_GRANTS } from '../src/scac-mutation-registry.current.generated.js';

const actor = { id: '00000000-0000-4000-8000-000000000001' };
const migration = name => readFile(new URL(`../../migrations/${name}`, import.meta.url), 'utf8');

test('registering a client portal stores only a verifier and exposes only a derived link', async t => {
  const db = await postgresDatabase('client-portal-register-');
  const c = db.client;
  try {
    await c.query(await migration('0001_init.sql'));
    await c.query(await migration('0008_client_merged_into.sql'));
    await c.query('create schema ops; create role carr_reader; create role carr_writer; create role carr_authority; create role dot_reader; grant usage on schema ops to dot_reader; alter default privileges in schema ops grant select on tables to dot_reader; alter default privileges in schema public grant select on tables to dot_reader; create extension pgcrypto');
    for (const name of ['0975_client_portal.sql', '0976_client_portal_binding.sql', '0981_client_portal_writer_denials.sql', '1013_register_client_portal.sql'])
      await c.query(await migration(name));
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner')", [actor.id]);
    await c.query("insert into client_status(slug,label) values('active','Active'); insert into deal_phase(slug,label,sort) values('research','Research',1)");
    const deals = [];
    for (const name of ['Synthetic practice A', 'Synthetic practice B']) {
      const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person',$1,$2,$2) returning id", [name, actor.id])).rows[0].id;
      const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id", [party, actor.id])).rows[0].id;
      deals.push({ client, deal: (await c.query("insert into deal(client_id,name,deal_type,phase,created_by,updated_by) values($1,$2,'lease','research',$3,$3) returning id", [client, `${name} deal`, actor.id])).rows[0].id });
    }
    const [a, b] = deals;
    const register = async (deal, slug, password) => (await c.query('select ops.register_client_portal($1,$2,$3) as outcome', [deal, slug, password])).rows[0].outcome;
    const refused = (deal, slug, password) => assert.rejects(register(deal, slug, password));
    const link = async deal => (await c.query('select client_portal_url from v_deal_client_portal where deal_id=$1', [deal])).rows[0]?.client_portal_url ?? null;
    const password = 'synthetic-simple-password';

    await t.test('stores only the bcrypt verifier the portal reader checks', async () => {
      assert.equal(await register(a.deal, 'synthetic-a', password), 'created');
      const row = (await c.query('select * from ops.client_portal')).rows[0];
      assert.equal(row.client_id, a.client);
      assert.match(row.password_hash, /^\$2[aby]\$12\$/);
      assert.equal(JSON.stringify(row).includes(password), false);
      assert.equal((await c.query("select ops.read_client_portal('synthetic-a',$1) is not null as opens", [password])).rows[0].opens, true);
      assert.equal((await c.query("select ops.read_client_portal('synthetic-a','wrong') is null as closed")).rows[0].closed, true);
    });

    await t.test('the same slug and password for the same deal is an existing portal, not a second row', async () => {
      assert.equal(await register(a.deal, 'synthetic-a', password), 'existing');
      assert.equal((await c.query('select count(*)::int n from ops.client_portal')).rows[0].n, 1);
    });

    await t.test('a slug bound elsewhere, a wrong password, a second portal for the deal and bad input are refused by name', async () => {
      for (const [args, code] of [
        [[b.deal, 'synthetic-a', password], 'portal_slug_taken'],
        [[a.deal, 'synthetic-a', 'another-password'], 'portal_password_differs'],
        [[a.deal, 'synthetic-second', password], 'portal_deal_already_registered'],
        [[b.deal, 'Bad Slug', password], 'portal_slug_invalid'],
        [[b.deal, 'synthetic-b', ''], 'portal_password_invalid'],
        [[randomUUID(), 'synthetic-b', password], 'portal_deal_not_found'],
      ]) await assert.rejects(register(...args), e => e.message === code, code);
      assert.equal((await c.query('select count(*)::int n from ops.client_portal')).rows[0].n, 1);
    });

    await t.test('a disabled portal hides the link and frees the deal for a new one', async () => {
      assert.equal(await link(a.deal), 'https://share.doctorcre.com/synthetic-a');
      assert.equal(await link(b.deal), null);
      await c.query("update ops.client_portal set disabled_at=now() where slug='synthetic-a'");
      assert.equal(await link(a.deal), null);
      await assert.rejects(register(a.deal, 'synthetic-a', password), e => e.message === 'portal_slug_taken');
      assert.equal(await register(a.deal, 'synthetic-a2', password), 'created');
      assert.equal(await link(a.deal), 'https://share.doctorcre.com/synthetic-a2');
    });

    await t.test('concurrent registrations leave at most one active portal for a deal', async () => {
      const other = new pg.Client(c.connectionParameters);
      await other.connect();
      try {
        const results = await Promise.allSettled([
          register(b.deal, 'race-one', password),
          other.query('select ops.register_client_portal($1,$2,$3)', [b.deal, 'race-two', password]),
        ]);
        assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
        assert.equal((await c.query('select count(*)::int n from ops.client_portal where deal_id=$1 and disabled_at is null', [b.deal])).rows[0].n, 1);
        await c.query('update ops.client_portal set disabled_at=now() where deal_id=$1', [b.deal]);
      } finally { await other.end(); }
    });

    await t.test('grants: the writer registers, readers see only the link, dot_reader sees nothing', async () => {
      await c.query('set role carr_writer');
      try { assert.equal((await c.query("select ops.register_client_portal($1,'synthetic-b',$2) as outcome", [b.deal, password])).rows[0].outcome, 'created'); }
      finally { await c.query('reset role'); }
      await c.query('set role carr_reader');
      try {
        assert.equal(await link(b.deal), 'https://share.doctorcre.com/synthetic-b');
        await assert.rejects(c.query('select * from ops.client_portal'), /permission denied/);
        await assert.rejects(c.query("select ops.register_client_portal($1,'synthetic-c',$2)", [b.deal, password]), /permission denied/);
        assert.deepEqual(Object.keys((await c.query('select * from v_deal_client_portal limit 1')).rows[0]), ['deal_id', 'client_portal_url']);
      } finally { await c.query('reset role'); }
      await c.query('set role dot_reader');
      try {
        await assert.rejects(c.query('select * from v_deal_client_portal'), /permission denied/);
        await assert.rejects(c.query('select * from ops.client_portal'), /permission denied/);
      } finally { await c.query('reset role'); }
    });

    await t.test('the installed grant contract matches the runtime contract', async () => {
      const registered = contract => contract.secdef_execute.filter(grant => grant.signature.startsWith('ops.register_client_portal('));
      assert.equal(registered(SCAC_MUTATION_CURRENT_DB_GRANTS).length, 1);
      assert.deepEqual(registered((await c.query('select ops.scac_current_grant_contract() as contract')).rows[0].contract),
        registered(SCAC_MUTATION_CURRENT_DB_GRANTS));
    });
  } finally { await db.close(); }
});
