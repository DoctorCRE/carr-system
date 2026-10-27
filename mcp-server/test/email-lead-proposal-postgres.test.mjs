// Test email lead proposal identity, rejection, and replay in PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

let fixture, admin, database, c, actor, connection;
before(async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let bin;
  if (ciDsn) {
    const url = new URL(ciDsn);
    assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
    assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
    assert.equal(url.search, '', 'fixture connection must not override its loopback host');
    admin = new pg.Client({ connectionString: ciDsn });
    await admin.connect();
    database = `email_lead_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${database}" template template0`);
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
    fixture = await acquireDisposablePostgres({ prefix: 'email-lead-', pgCtl: `${bin}/pg_ctl` });
    await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
    await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
    connection = { host: fixture.root, user: 'carr_ci', database: 'carr_ci' };
  }
  c = new pg.Client(connection);
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  const restore = ['-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', new URL('../../db/schema.sql', import.meta.url).pathname];
  if (ciDsn) await promisify(execFile)('psql', [connection.connectionString, ...restore], { maxBuffer: 2 * 1024 * 1024 });
  else await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', ...restore]);
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('fixture-intake','automation','Synthetic intake') returning id,slug")).rows[0], human: false };
  await c.query("insert into party(kind,name,email,created_by,updated_by) values('person','Synthetic known person','first@known.test; second@known.test',$1,$1)", [actor.id]);
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

async function propose(address, extra = {}) {
  await c.query('begin');
  try {
    await c.query('set local role carr_writer');
    const result = await executeRegisteredTool(c, actor, 'propose-email-lead', { idempotency_key: randomUUID(), address,
      display_name: 'Synthetic Sender', summary: 'Asks about leasing a small clinic suite.',
      first_seen_at: '2026-10-20T15:00:00Z', message_ref: 'abc123:1760972400', ...extra });
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}
const counts = async () => (await c.query(`select (select count(*)::int from candidate_pool) as pool,
  (select count(*)::int from lead) as leads, (select count(*)::int from party) as parties`)).rows[0];

test('an unknown sender lands once on the claim card and creates no party or lead', async () => {
  const before = await counts();
  const first = await propose('New.Person@Unknown.test');
  assert.equal(first.proposed, true);
  const card = (await c.query("select lane, display_name, has_channel, score_basis from v_claim_card where pool_id=$1", [first.pool_id])).rows[0];
  assert.deepEqual(card, { lane: 'outlook-intake', display_name: 'Synthetic Sender', has_channel: true,
    score_basis: 'Inbound email: Asks about leasing a small clinic suite.' });
  assert.equal((await c.query('select email from candidate_pool where id=$1', [first.pool_id])).rows[0].email, 'new.person@unknown.test');
  const again = await propose('new.person@unknown.test');
  assert.deepEqual([again.proposed, again.reason, again.pool_id], [false, 'already_in_queue', first.pool_id]);
  const after = await counts();
  assert.deepEqual(after, { ...before, pool: before.pool + 1 });
  const events = (await c.query("select cause from event where verb='propose-email-lead' and subject_id=$1", [first.pool_id])).rows;
  assert.deepEqual(events, [{ cause: 'ingest_email' }]);
});

test('every address a party already carries is known, never a lead candidate', async () => {
  const before = await counts();
  for (const address of ['first@known.test', 'SECOND@known.test']) {
    const result = await propose(address);
    assert.deepEqual([result.proposed, result.reason], [false, 'known_party']);
  }
  assert.deepEqual(await counts(), before);
});

test('a partner decline is final: proposing the address again leaves it declined', async () => {
  const first = await propose('declined@unknown.test');
  await c.query("update candidate_pool set status='declined', declined_at=now(), declined_by=$1, decline_reason='Synthetic vendor pitch' where id=$2", [actor.id, first.pool_id]);
  const again = await propose('declined@unknown.test');
  assert.deepEqual([again.proposed, again.reason, again.status], [false, 'already_in_queue', 'declined']);
});

test('a malformed address or a future first-seen time is refused', async () => {
  await assert.rejects(propose('not-an-address'), error => error.payload?.error === 'email_lead_input_invalid');
  await assert.rejects(propose('later@unknown.test', { first_seen_at: '2999-01-01T00:00:00Z' }),
    error => error.payload?.error === 'email_lead_input_invalid');
});

test('a replayed key returns the stored answer without a second row', async () => {
  const args = { idempotency_key: randomUUID(), address: 'replay@unknown.test', summary: 'Needs a medical office.',
    first_seen_at: '2026-10-20T15:00:00Z', message_ref: 'def456:1760972400' };
  await c.query('begin');
  const first = await executeRegisteredTool(c, actor, 'propose-email-lead', args);
  await c.query('commit');
  await c.query('begin');
  const second = await executeRegisteredTool(c, actor, 'propose-email-lead', args);
  await c.query('commit');
  assert.equal(second.replayed, true);
  assert.equal(second.pool_id, first.pool_id);
  assert.equal((await c.query("select count(*)::int as n from candidate_pool where email='replay@unknown.test'")).rows[0].n, 1);
});
