// Synthetic fixtures only, using the production read role.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { snapshotDatabase } from './helpers/postgres-database.mjs';
import { leadAutomationTools } from '../src/lead-automation.js';

const dsn = await snapshotDatabase('lead-approval-reader-');
const tools = leadAutomationTools({});

test('reader lists draft approval receipts and proposed move base versions', async () => {
  const c = new pg.Client({ connectionString: dsn });
  await c.connect();
  try {
    await c.query('begin');
    const actor = randomUUID(), party = randomUUID(), lead = randomUUID();
    await c.query("insert into actor(id,slug,kind,display_name) values($1,$2,'human','Synthetic reviewer')", [actor, `fixture-${actor}`]);
    await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'person','Synthetic contact',$2,$2)", [party, actor]);
    const { rows: [row] } = await c.query("insert into lead(id,party_id,stage,created_by,updated_by) values($1,$2,'qualified',$3,$3) returning version", [lead, party, actor]);
    const { rows: [draft] } = await c.query(`insert into lead_contact_draft
      (lead_id,party_id,subject,body,scheduled_for,time_zone,created_by,approved_by,approved_at,revision)
      values($1,$2,'Synthetic subject','Synthetic body',now(),'America/Chicago',$3,$3,now(),2)
      returning id,approved_at`, [lead, party, actor]);
    const { rows: [activity] } = await c.query("insert into activity(occurred_at,actor_id,kind,summary,lead_id) values(now(),$1,'call','Synthetic activity',$2) returning id", [actor, lead]);
    const { rows: [move] } = await c.query(`insert into lead_stage_move
      (lead_id,from_stage,to_stage,activity_id,evidence_ref,strength,status,created_by,reason)
      values($1,'qualified','outreach_active',$2,'synthetic:queue','weak','proposed',$3,'Synthetic reason') returning id`, [lead, activity.id, actor]);
    await c.query('set local role carr_reader');
    const queue = await tools['lead-approval-queue'].handler(c);
    const receipt = queue.drafts.find(d => d.id === draft.id);
    assert.equal(receipt.revision, 2);
    assert.equal(receipt.approved_at.toISOString(), draft.approved_at.toISOString());
    assert.equal(queue.moves.find(m => m.id === move.id).base_version, row.version);
    assert.equal((await c.query("select has_column_privilege(current_user,'lead','score_reason','SELECT') as allowed")).rows[0].allowed, false);
  } finally {
    await c.query('rollback');
    await c.end();
  }
});
