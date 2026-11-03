import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { TOOLS } from '../src/tools.js';

let fixture, c, connection, actor, deal, ids;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'email-delivery-', snapshot: true });
  connection = fixture.connection;
  c = new pg.Client(connection);
  await c.connect();
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic physician',$1,$1) returning id", [actor.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  const client = (await c.query("insert into client(party_id,status,created_by,updated_by,roster_ref) values($1,'fixture_active',$2,$2,'C-999442') returning id", [party, actor.id])).rows[0].id;
  deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic delivery purchase',$1,'purchase','site_selection','joe',$2,$2) returning id", [client, actor.id])).rows[0].id;
  ids = [];
  for (let i = 0; i < 2; i++) ids.push((await c.query(`insert into activity(client_id,deal_id,kind,summary,actor_id,updated_by,occurred_at)
    values($1,$2,'email_out','Synthetic outbound',$3,$3,now()) returning id`, [client, deal, actor.id])).rows[0].id);
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });

async function evidence() {
  const rows = (await c.query('select id,version from activity where id=any($1::uuid[])', [ids])).rows;
  return { idempotency_key: randomUUID(), subject: deal, kind: 'email_delivery_failure',
    source: 'outlook-delivery-report/synthetic/report', value: { source_activity_ids: ids,
      source_activity_versions: Object.fromEntries(rows.map(row => [row.id, row.version])) } };
}
async function finding(args, client = c) {
  await client.query('begin');
  try {
    const result = await TOOLS['record-finding'].handler(client, actor, args);
    await client.query('commit');
    return result;
  } catch (error) { await client.query('rollback'); throw error; }
}
const changed = error => error.payload?.error === 'email_delivery_filing_changed';

test('stale or absent sources refuse without a finding or receipt and can retry', async () => {
  const args = await evidence();
  await c.query('update activity set deal_id=null,version=version+1 where id=$1', [ids[0]]);
  await assert.rejects(finding(args), changed);
  assert.equal((await c.query('select count(*) from tool_call where idempotency_key=$1', [args.idempotency_key])).rows[0].count, '0');
  assert.equal((await c.query("select count(*) from record_flag where kind='email_delivery_failure'")).rows[0].count, '0');
  await c.query('update activity set deal_id=$2,version=version+1 where id=$1', [ids[0], deal]);
  await assert.rejects(finding(args), changed);
  const current = await evidence();
  const first = await finding(current);
  await c.query('update activity set deal_id=null,version=version+1 where id=$1', [ids[0]]);
  assert.equal((await finding(current)).flag_id, first.flag_id);
  await assert.rejects(finding({ ...await evidence(), value: { source_activity_ids: ids } }), changed);
  const missing = await evidence();
  missing.value.source_activity_ids = [...ids, randomUUID()];
  missing.value.source_activity_versions[missing.value.source_activity_ids.at(-1)] = 1;
  await assert.rejects(finding(missing), changed);
  await c.query('update activity set deal_id=$2,version=version+1 where id=$1', [ids[0], deal]);
});

test('a correction committed before validation rejects the source version', async () => {
  const args = await evidence();
  const other = new pg.Client(connection);
  await other.connect();
  try {
    await other.query('begin');
    await other.query('update activity set version=version+1 where id=$1', [ids[0]]);
    await other.query('commit');
    await assert.rejects(finding(args), changed);
  } finally { await other.end(); }
});

test('source locks prevent corrections between validation and finding commit', async () => {
  const args = await evidence();
  const other = new pg.Client(connection);
  await other.connect();
  await c.query('begin');
  try {
    await TOOLS['record-finding'].handler(c, actor, args);
    await other.query('begin');
    await other.query("set local lock_timeout='100ms'");
    await assert.rejects(other.query('update activity set version=version+1 where id=$1', [ids[0]]),
      error => error.code === '55P03');
    await other.query('rollback');
    await c.query('commit');
    await other.query('update activity set version=version+1 where id=$1', [ids[0]]);
    assert.equal((await finding(args)).replayed, true);
  } finally {
    await c.query('rollback');
    await other.end();
  }
});
