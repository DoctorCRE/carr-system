import { NOW } from "./helpers/fixed-clock.ts";
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { execFileSync } from 'node:child_process';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.ts';
import { executeRegisteredTool } from '../src/tools.js';
import { addCalendarConfirmations } from '../src/brief-calendar.js';

let fixture, c, actor;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'calendar-intake-', snapshot: true });
  c = new pg.Client(fixture.connection); await c.connect();
  actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true, via: 'dealroom-cookie' };
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });
async function call(verb, args, role = 'carr_writer', identity = actor) {
  await c.query('begin');
  try {
    await c.query(`set local role ${role}`);
    const result = await executeRegisteredTool(c, identity, verb, args);
    await c.query('commit'); return result;
  } catch (error) { await c.query('rollback'); throw error; }
}
test('find resolves an exact attendee email under carr_reader without exposing contact columns', async () => {
  const p = (await c.query("insert into party(kind,name,email,created_by,updated_by) values('person','Synthetic attendee','Attendee@example.test',$1,$1) returning id", [actor.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  await c.query("insert into client(party_id,status,created_by,updated_by,roster_ref) values($1,'fixture_active',$2,$2,'C-999636')", [p,actor.id]);
  const found = await call('find', { query: 'ATTENDEE@EXAMPLE.TEST' }, 'carr_reader');
  assert.ok(found.parties.some(row => row.ref === 'C-999636'));
  assert.equal(found.parties.find(row => row.ref === 'C-999636').name, 'Synthetic attendee');
  assert.equal(JSON.stringify(found).includes('Attendee@example.test'), false);
  const partial = await call('find', { query: 'ttendee@example.test' }, 'carr_reader');
  assert.equal(partial.parties.some(row => row.ref === 'C-999636'), false);
  const name = await call('find', { query: 'Synthetic attendee' }, 'carr_reader');
  assert.ok(name.parties.some(row => row.ref === 'C-999636'));
});

test('null display refs retain canonical identities through registered lookup and intake matching', async () => {
  const ids = [];
  for (let i = 0; i < 4; i++) {
    ids.push((await c.query(`insert into party(kind,name,email,created_by,updated_by)
      values('person',$1,$2,$3,$3) returning id`,
      [`Synthetic Null Contact ${i}`, `null-${i}@example.test`, actor.id])).rows[0].id);
  }
  const roles = [];
  for (const table of ['client', 'lead', 'vendor']) {
    const p = (await c.query(`insert into party(kind,name,email,created_by,updated_by)
      values('person',$1,$2,$3,$3) returning id`,
      [`Synthetic Null ${table}`, `null-${table}@example.test`, actor.id])).rows[0].id;
    const stage = table === 'lead' ? ',stage' : table === 'vendor' ? ',category' : '';
    const value = table === 'lead' ? ',(select slug from lead_stage order by slug limit 1)' : table === 'vendor' ? ", 'Synthetic'" : '';
    roles.push((await c.query(`insert into ${table}(party_id,created_by,updated_by${stage}) values($1,$2,$2${value}) returning id`,
      [p, actor.id])).rows[0].id);
  }
  const contacts = (await call('email-match-contacts', {}, 'carr_reader')).contacts;
  const expected = [...ids, ...roles];
  const addresses = [...ids.map((_, i) => `null-${i}@example.test`),
    ...['client','lead','vendor'].map(role => `null-${role}@example.test`)];
  for (let i = 0; i < addresses.length; i++) {
    const row = contacts.find(row => row.address === addresses[i]);
    assert.equal(row?.ref, expected[i], 'lookup must return the existing canonical ID');
    assert.ok(row.contact_snapshot.party_id);
    const found = await call('find', { query: addresses[i] }, 'carr_reader');
    const refs = [...found.parties.map(row => row.ref), ...found.organizations.flatMap(row => row.refs)];
    assert.ok(refs.includes(expected[i]), 'find must return the same usable identity');
    if (i < ids.length) {
      await assert.rejects(call('get-next-action', { ref: row.ref, owner: 'joe' }),
        error => error.payload?.error === 'next_action_subject_unsupported');
    } else {
      const resolved = await call('catch-me-up', { ref: row.ref });
      assert.equal(resolved.subject.id, expected[i]);
      assert.equal(resolved.subject.type, ['client','lead','vendor'][i - ids.length]);
    }
  }
  assert.deepEqual((await call('email-match-contacts', {}, 'carr_reader')).contacts, contacts);
  const shared = (await c.query(`insert into party(kind,name,email,created_by,updated_by)
    values('person','Synthetic Shared Contact','null-0@example.test',$1,$1) returning id`, [actor.id])).rows[0].id;
  const ambiguous = (await call('email-match-contacts', {}, 'carr_reader')).contacts;
  assert.ok(ambiguous.filter(row => row.address === addresses[0]).every(row => !row.contact_snapshot));
  const proof = JSON.parse(execFileSync('python3', ['-c', `
import json, sys
from lib.outlook_intake import _contacts, Matcher, Run
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
unique, shared, addresses, ids = json.load(sys.stdin)
def matcher(rows):
    return Matcher(_contacts(lambda *_: {'contacts': rows})(), 'internal.test')
a, b = matcher(unique), matcher(shared)
for address, ident in zip(addresses, ids):
    assert a.reason(address) is None
    assert a.ref(address) == ident
assert b.reason(addresses[0]) == 'ambiguous'
assert b.ref(addresses[0]) is None
assert a.reason('foreign@unknown.test') == 'missing'
assert a.ref('foreign@unknown.test') is None
now = datetime(2026, 1, 1, tzinfo=timezone.utc)
settings = SimpleNamespace(timezone=timezone.utc, internal_domain='internal.test')
event = {'start_unix': int(now.timestamp()), 'end_unix': int(now.timestamp()) + 3600,
         'attendees': [('Synthetic', address) for address in addresses],
         'organizer_email': '', 'organizer_name': ''}
run = Run(None, {}, now, Path('.'), settings)
run.events([event], a, 0)
assert not run.state['calendar_unresolved']
run.events([event], b, 0)
assert any('ambiguous' in row['participants'].values() for row in run.state['calendar_unresolved'].values())
print(json.dumps({'matched': len(addresses), 'ambiguous': True, 'unknown': True}))
`], { cwd: new URL('../..', import.meta.url), encoding: 'utf8',
    input: JSON.stringify([contacts, ambiguous, addresses, expected]) }));
  assert.deepEqual(proof, { matched: 7, ambiguous: true, unknown: true });
  await c.query('update party set deleted_at=now() where id=$1', [ids[1]]);
  await c.query('update party set merged_into=$1 where id=$2', [ids[0], shared]);
  const retired = (await call('email-match-contacts', {}, 'carr_reader')).contacts;
  assert.equal(retired.some(row => row.ref === ids[1] || row.ref === shared), false);
  assert.equal(retired.find(row => row.address === addresses[0]).contact_snapshot.party_id, ids[0]);
});

test('calendar dismissals are durable board answers scoped to the partner and tenant', async () => {
  const key = '6360000000000001';
  const now = NOW;
  const review = { key, title: 'Synthetic review meeting', state: 'needs_review', prompt: 'Did it happen?',
    start: new Date(now - 7200000).toISOString(), end: new Date(now - 3600000).toISOString() };
  await call('publish-board-snapshot', { idempotency_key: randomUUID(), board_id: 'calendar-agenda', base_version: 0,
    snapshot: { schema: 'carr-calendar-agenda.v1', observed_at: new Date(now).toISOString(), items: [], follow_up_reviews: [review] } });
  const args = { idempotency_key: randomUUID(), board_id: 'calendar-agenda', question_id: key, base_version: 1, answer_text: 'Dismiss' };
  const receipt = await call('answer-board-question', args);
  assert.equal(receipt.answer.answer_text, 'Dismiss');
  assert.equal((await call('answer-board-question', args)).answer.id, receipt.answer.id);
  assert.equal((await call('answer-board-question', { ...args, idempotency_key: randomUUID() })).answer.id, receipt.answer.id);
  const section = { follow_up_reviews: [{ ...review }], notes: [] };
  await c.query('begin'); await c.query('set local role carr_reader');
  await addCalendarConfirmations(c, section, actor);
  await c.query('rollback');
  assert.equal(section.follow_up_reviews[0].dismissed, true);
  const dell = { ...(await c.query("select id,slug from actor where slug='dell'")).rows[0], human: true, via: 'dealroom-cookie' };
  const other = { follow_up_reviews: [{ ...review }], notes: [] };
  await addCalendarConfirmations(c, other, dell);
  assert.equal(other.follow_up_reviews[0].dismissed, false);
  await c.query(`insert into board_snapshot (organization_tenant_id,sponsoring_human_slug,board_id,version,snapshot_json,updated_by_actor_id)
    select 'synthetic-other-tenant',sponsoring_human_slug,board_id,version,snapshot_json,updated_by_actor_id
      from board_snapshot where board_id='calendar-agenda' and sponsoring_human_slug='joe'`);
  await c.query(`insert into board_question (organization_tenant_id,sponsoring_human_slug,board_id,question_id,revision,
    current,prompt,choices,allow_free_text,asker_ref,asked_by_actor_id)
    select 'synthetic-other-tenant',sponsoring_human_slug,board_id,question_id,revision,
      current,prompt,choices,allow_free_text,asker_ref,asked_by_actor_id from board_question where question_id=$1`, [key]);
  await c.query("update board_answer set organization_tenant_id='synthetic-other-tenant' where id=$1", [receipt.answer.id]);
  const outside = { follow_up_reviews: [{ ...review }], notes: [] };
  await addCalendarConfirmations(c, outside, actor);
  assert.equal(outside.follow_up_reviews[0].dismissed, false);
  await assert.rejects(call('answer-board-question', { ...args, idempotency_key: randomUUID(), question_id: '6360000000000002' }));
  await assert.rejects(call('answer-board-question', { ...args, idempotency_key: randomUUID(), answer_text: 'It happened' }));
  await assert.rejects(call('answer-board-question', { ...args, idempotency_key: randomUUID() }, 'carr_writer', { ...actor, human: false }));
});
