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

let deals;
async function fixtures() {
  if (deals) return deals;
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic Client Person',$1,$1) returning id", [actor.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  const client = (await c.query("insert into client(party_id,status,created_by,updated_by,roster_ref) values($1,'fixture_active',$2,$2,'C-999451') returning id", [party, actor.id])).rows[0].id;
  const deal = async (name, phase) => (await c.query(`insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by)
    values($1,$2,'purchase',$3,'joe',$4,$4) returning id`, [name, client, phase, actor.id])).rows[0].id;
  deals = { open: await deal('Synthetic bayside suite', 'site_selection'), closed: await deal('Synthetic closed suite', 'closed') };
  return deals;
}

test('a read names what the email applies to on the queued row, validated against open records', async () => {
  const { open, closed } = await fixtures();
  const deal = await propose('assistant@landlord-reads.test', { applies_to: 'existing_deal', deal: open,
    evidence_quote: 'Tour times for the bayside suite', confidence: 0.8 });
  assert.equal(deal.proposed, true);
  const row = (await c.query('select score_basis, source_row from candidate_pool where id=$1', [deal.pool_id])).rows[0];
  assert.equal(row.score_basis, 'Inbound email: Asks about leasing a small clinic suite. Applies to open deal Synthetic bayside suite.');
  assert.deepEqual(row.source_row.reading, { applies_to: 'existing_deal', deal_id: open, party_ref: null,
    evidence_quote: 'Tour times for the bayside suite', confidence: 0.8 });
  const party = await propose('personal@reads.test', { applies_to: 'existing_party', party: 'C-999451', confidence: 0.7 });
  assert.match((await c.query('select score_basis from candidate_pool where id=$1', [party.pool_id])).rows[0].score_basis,
    /Likely a new address for Synthetic Client Person \(C-999451\)\.$/);
  const matter = await propose('newmatter@reads.test', { applies_to: 'new_matter', confidence: 0.6 });
  assert.match((await c.query('select score_basis from candidate_pool where id=$1', [matter.pool_id])).rows[0].score_basis,
    /Possible new lead or matter\.$/);
  await assert.rejects(propose('closed@reads.test', { applies_to: 'existing_deal', deal: closed, confidence: 0.9 }),
    error => error.payload?.error === 'email_lead_deal_invalid');
  await assert.rejects(propose('noref@reads.test', { applies_to: 'existing_party', party: 'C-000000', confidence: 0.9 }),
    error => error.payload?.error === 'email_lead_party_invalid');
  for (const extra of [{ applies_to: 'existing_deal', confidence: 0.9 }, { applies_to: 'new_matter', deal: open, confidence: 0.9 },
    { deal: open }, { applies_to: 'new_matter' }])
    await assert.rejects(propose('shape@reads.test', extra), error => error.payload?.error === 'email_lead_input_invalid', JSON.stringify(extra));
});

test('a read adds its reasoning once to a row queued before reads existed, never to a judged row', async () => {
  const { open } = await fixtures();
  const first = await propose('legacy@reads.test');
  const reasoned = await propose('legacy@reads.test', { applies_to: 'existing_deal', deal: open, confidence: 0.8 });
  assert.deepEqual([reasoned.proposed, reasoned.reason, reasoned.reasoned, reasoned.pool_id], [false, 'already_in_queue', true, first.pool_id]);
  const row = (await c.query('select score_basis, source_row, version from candidate_pool where id=$1', [first.pool_id])).rows[0];
  assert.match(row.score_basis, /Applies to open deal Synthetic bayside suite\.$/);
  assert.equal(row.source_row.message_ref, 'abc123:1760972400', 'the original provenance stays');
  assert.equal(row.version, 2);
  const again = await propose('legacy@reads.test', { applies_to: 'new_matter', confidence: 0.5 });
  assert.equal(again.reasoned, false, 'a reasoned row keeps its first reading');
  const declined = await propose('judged@reads.test');
  await c.query("update candidate_pool set status='declined', declined_at=now(), declined_by=$1, decline_reason='Synthetic no' where id=$2", [actor.id, declined.pool_id]);
  const late = await propose('judged@reads.test', { applies_to: 'new_matter', confidence: 0.9 });
  assert.deepEqual([late.reasoned, late.status], [false, 'declined']);
});
