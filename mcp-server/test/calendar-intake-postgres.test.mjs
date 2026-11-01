import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
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
