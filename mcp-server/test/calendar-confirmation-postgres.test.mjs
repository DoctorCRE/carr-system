import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { addCalendarConfirmations } from '../src/brief-calendar.js';

let fixture, c, actor, deal;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'calendar-confirm-', snapshot: true });
  c = new pg.Client(fixture.connection);
  await c.connect();
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic physician',$1,$1) returning id", [actor.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  const client = (await c.query("insert into client(party_id,status,created_by,updated_by,roster_ref) values($1,'fixture_active',$2,$2,'C-999441') returning id", [party, actor.id])).rows[0].id;
  deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic meeting deal',$1,'purchase','site_selection','joe',$2,$2) returning id", [client, actor.id])).rows[0].id;
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });
async function log(key, ref = deal) {
  await c.query('begin');
  try {
    const result = await executeRegisteredTool(c, actor, 'log-activity', { idempotency_key: randomUUID(), ref, kind: 'meeting', summary: 'Synthetic meeting', calendar_event_key: key });
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}
test('meeting confirmation deduplicates across request keys', async () => {
  const first = await log('0000000000000000');
  const retry = await log('0000000000000000');
  assert.equal(retry.activity_id, first.activity_id);
  assert.equal(retry.deduped, true);
  assert.equal(Number((await c.query('select count(*) from activity where calendar_event_key=$1 and deal_id=$2', ['0000000000000000', deal])).rows[0].count), 1);
  await assert.rejects(log('INVALID'));
});
test('calendar reviews show confirmed keys after a reload', async () => {
  const section = { follow_up_reviews: [{ key: '0000000000000000' }, { key: '1111111111111111' }] };
  await addCalendarConfirmations(c, section, actor);
  assert.deepEqual(section.follow_up_reviews.map(x => x.confirmed), [true, false]);
});
