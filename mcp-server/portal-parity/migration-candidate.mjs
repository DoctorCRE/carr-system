// Exercise deployed registration code against a disposable synthetic database.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { clientPortalTools } from '../src/client-portal.ts';
import { postgresDatabase } from '../test/helpers/postgres-database.mjs';
import { readMigration } from '../test/migration-source.testhelper.mjs';

export const pendingSteps = {
  'client-view-publication': { status: 'pending', reason: 'explicit client-view publication is not on main', dependency: 'wr586-s4-j1 at 541e47ff; separate from market-study publication' },
  'package-publication': { status: 'pending', reason: 'publish-market-study is not implemented on main', dependency: 'WR227 S4; no supplying branch verified' },
  'bundle-alias-import': { status: 'pending', reason: 'legacy bundle and alias importer is not implemented on main', dependency: 'WR227 S21; no supplying branch verified' },
  'selection-import': { status: 'pending', reason: 'versioned Tour List importer is not implemented on main', dependency: 'WR227 S21 and selection storage; no supplying branch verified' },
  'selection-persistence': { status: 'pending', reason: 'portal Tour List compare-and-swap storage is not implemented on main', dependency: 'WR227 S4; no supplying branch verified' },
  'http-parity': { status: 'pending', reason: 'real portal HTTP candidate is not on main', dependency: 'wr227-s9-j1 at cc16e75f; requires publication and selection storage' },
};

export async function registerCandidate(portals, { afterInstall = async () => {} } = {}) {
  let db;
  const actor = { id: '00000000-0000-4000-8000-000000000001' };
  try {
    db = await postgresDatabase('portal-migration-');
    const c = db.client;
    await c.query(readMigration('0001_init.sql'));
    await c.query(readMigration('0008_client_merged_into.sql'));
    await c.query('create schema ops; create role carr_reader; create role carr_writer; create role carr_authority; create role dot_reader; create extension pgcrypto');
    for (const name of ['0975_client_portal.sql', '0976_client_portal_binding.sql', '0981_client_portal_writer_denials.sql', '1013_register_client_portal.sql'])
      await c.query(readMigration(name));
    await afterInstall(c);
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'synthetic-partner','human','Synthetic partner')", [actor.id]);
    await c.query("insert into client_status(slug,label) values('active','Active'); insert into deal_phase(slug,label,sort) values('research','Research',1)");
    const tools = clientPortalTools({
      withEnvelope: async (_c, _actor, _verb, _args, fn) => fn(),
      writeEvent: async () => {},
      resolveSubject: async (_c, ref) => ({ type: 'deal', id: ref }),
    });
    const results = [];
    const reader = new pg.Client(c.connectionParameters);
    await reader.connect();
    try {
      for (const portal of portals) {
        const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person',$1,$2,$2) returning id", [portal.deal, actor.id])).rows[0].id;
        const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id", [party, actor.id])).rows[0].id;
        const deal = (await c.query("insert into deal(client_id,name,deal_type,phase,created_by,updated_by) values($1,$2,'lease','research',$3,$3) returning id", [client, portal.deal, actor.id])).rows[0].id;
        const args = { idempotency_key: randomUUID(), deal, slug: portal.slug, password: portal.password };
        const register = () => tools['register-client-portal'].handler(c, actor, args);
        assert.deepEqual(await register(), { ok: true, deal_id: deal,
          client_portal_url: `https://share.doctorcre.com/${portal.slug}`, created: true }, 'created-binding');
        assert.equal((await register()).created, false, 'registration-replay');
        const rows = (await reader.query(`select client_id,deal_id,password_hash from ops.client_portal where slug=$1`, [portal.slug])).rows;
        assert.equal(rows.length, 1, 'persisted-binding');
        assert.equal(rows[0].deal_id, deal, 'persisted-deal');
        assert.equal(rows[0].client_id, client, 'persisted-client');
        assert.notEqual(rows[0].password_hash, portal.password, 'verifier-only');
        const opened = (await reader.query('select ops.read_client_portal($1,$2) as portal', [portal.slug, portal.password])).rows[0].portal;
        assert.equal(opened?.slug, portal.slug, 'existing-password-opens');
        assert.equal((await reader.query("select ops.read_client_portal($1,'wrong-synthetic-password') as portal", [portal.slug])).rows[0].portal, null, 'wrong-password-refused');
        results.push({ slug: portal.slug, status: 'PASS', persisted: true, replay_created: false });
      }
    } finally { await reader.end(); }
    return { status: 'PASS', implementation: 'clientPortalTools/register-client-portal + ops.register_client_portal', portals: results };
  } catch (error) {
    throw new Error(`portal-registration: ${error.message}`, { cause: error });
  } finally { await db?.close(); }
}
