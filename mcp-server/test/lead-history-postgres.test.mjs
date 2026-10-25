import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';

test('F7 registered Leads history reaches every captured entry with exact counts and stable ties', async () => {
  const db = await postgresDatabase('lead-history-');
  const c = db.client;
  try {
    await c.query('create role neondb_owner');
    execFileSync('psql', [c.connectionParameters.connectionString || `postgresql://fixture@127.0.0.1:${c.connectionParameters.port}/postgres`, '-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', new URL('../../db/schema.sql', import.meta.url).pathname], { stdio: ['ignore','pipe','pipe'] });
    const actor = (await c.query("insert into actor(slug,kind,display_name) values('history-fixture','automation','Synthetic history') returning id,slug")).rows[0];
    const read = (lead, extra = {}) => executeRegisteredTool(c, { ...actor, human: false }, 'lead-board', { workspace: 'leads', lead_id: lead, ...extra });
    for (const count of [0, 100, 101]) {
      const party = randomUUID(), lead = randomUUID();
      await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'person','Synthetic history',$2,$2)", [party, actor.id]);
      await c.query("insert into lead(id,party_id,stage,created_by,updated_by) values($1,$2,'new',$3,$3)", [lead, party, actor.id]);
      await c.query("insert into activity(id,occurred_at,actor_id,kind,summary,lead_id) select md5($1||n::text)::uuid,'2026-01-01T10:00:00.123456Z'::timestamptz,$2,'call','Synthetic captured call',$1::uuid from generate_series(1,$3::int) n", [lead, actor.id, count]);
      await c.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,field,new_value,cause,idempotency_key) select '2026-01-01T10:00:00Z',$2,'update-lead','lead',$1,'stage','{\"stage\":\"qualified\"}'::jsonb,'automation_job',$1||n::text from generate_series(1,$3::int) n", [lead, actor.id, count]);
      await c.query('set role carr_reader');
      let detail = (await read(lead)).detail;
      assert.equal(detail.correspondence_coverage, 'captured_entries_only');
      const correspondence = [...detail.correspondence], stages = [...detail.stage_history];
      assert.deepEqual(detail.correspondence_page && { total: detail.correspondence_page.total, has_more: detail.correspondence_page.has_more }, { total: count, has_more: count > 100 });
      assert.equal(detail.stage_history_page.total, count);
      if (count > 100) {
        assert.match(detail.correspondence_page.next_cursor.occurred_at, /123456/);
        assert.equal(typeof detail.stage_history_page.next_cursor, 'string');
        detail = (await read(lead, { correspondence_cursor: detail.correspondence_page.next_cursor, stage_history_cursor: detail.stage_history_page.next_cursor })).detail;
        correspondence.push(...detail.correspondence); stages.push(...detail.stage_history);
        assert.equal(detail.correspondence_page.has_more, false);
        assert.equal(detail.stage_history_page.has_more, false);
      }
      assert.equal(correspondence.length, count);
      assert.equal(new Set(correspondence.map(row => row.id)).size, count);
      assert.equal(new Set(stages.map(row => row.event_id)).size, count);
      await c.query('reset role');
      await c.query('update lead set suppressed=true where id=$1', [lead]);
      assert.equal((await read(lead)).detail, null);
    }
  } finally { await db.close(); }
});
