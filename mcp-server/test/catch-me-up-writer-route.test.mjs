// catch-me-up, find-and-catch-up and prepare-conversation run on the
// read-only WRITER route (app_writer -> carr_writer) since #1476, because
// catch-me-up reads the actor-bound tool_call ledger. carr_writer had never
// been granted the reader views those handlers read (v_subject_timeline,
// v_deal_board), so release r-2026-10-03-01's golden suite failed with 42501
// on `catch-me-up V-CPA-006`. This drives the real handlers as carr_writer
// against the migrated throwaway database, so a view grant that the route
// depends on cannot go missing again unnoticed.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { connectionRouteForTool } from '../src/mcp.js';

const dsn = process.env.CARR_WRITER_READ_TEST_DATABASE_URL;
// Refuse unsafe configuration before any database test can construct a client.
if (dsn) assert.match(dsn, /^postgres(?:ql)?:\/\/[^@/]*@(?:127\.0\.0\.1|localhost):/);

const TIMELINE_VERBS = ['catch-me-up', 'find-and-catch-up', 'prepare-conversation'];

test('timeline verbs are served on the read-only writer route', () => {
  for (const verb of TIMELINE_VERBS)
    assert.equal(connectionRouteForTool(TOOLS[verb]), 'writer_read_only', verb);
});

test('real PostgreSQL: carr_writer serves a vendor catch-up through every timeline verb',
  { skip: !dsn }, async () => {
  const { Client } = (await import('pg')).default;
  const c = new Client({ connectionString: dsn });
  await c.connect();
  const actor = { id: randomUUID(), human: false };
  actor.slug = `fixture-${actor.id}`;
  const tag = actor.id.slice(0, 8);
  const name = `Synthetic Writer Route Vendor ${tag}`;
  const ref = `V-CPA-T${tag}`;
  try {
    await c.query('begin');
    await c.query("insert into actor(id,slug,kind,display_name) values($1,$2,'automation','Synthetic timeline reader')",
      [actor.id, actor.slug]);
    const { rows: [party] } = await c.query(
      "insert into party(kind,name,created_by,updated_by) values('org',$1,$2,$2) returning id", [name, actor.id]);
    const { rows: [vendor] } = await c.query(
      "insert into vendor(vendor_ref,party_id,category,created_by,updated_by) values($1,$2,'cpa',$3,$3) returning id",
      [ref, party.id, actor.id]);
    await c.query(
      "insert into activity(occurred_at,actor_id,kind,summary,vendor_id) values(now(),$1,'call','Synthetic vendor call',$2)",
      [actor.id, vendor.id]);

    await c.query('set local role carr_writer');
    const caughtUp = await executeRegisteredTool(c, actor, 'catch-me-up', { ref });
    assert.deepEqual(caughtUp.subject, { type: 'vendor', id: vendor.id });
    assert.deepEqual(caughtUp.timeline.map(row => row.summary), ['Synthetic vendor call']);
    assert.deepEqual(caughtUp.calendar_history, []);

    const found = await executeRegisteredTool(c, actor, 'find-and-catch-up', { query: name });
    assert.equal(found.state, 'completed', JSON.stringify(found));
    assert.deepEqual(found.catch_up.subject, caughtUp.subject);

    const prepared = await executeRegisteredTool(c, actor, 'prepare-conversation', { query: name });
    assert.equal(prepared.state, 'completed', JSON.stringify(prepared));
  } finally {
    await c.query('rollback');
    await c.end();
  }
});
