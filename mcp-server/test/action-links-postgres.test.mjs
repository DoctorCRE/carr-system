import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { actionLinkTools, buildActionLink } from '../src/action-links.js';

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}
const actor = { id: '20000000-0000-4000-8000-000000000001' };
const other = { id: '20000000-0000-4000-8000-000000000002' };
const draft = '20000000-0000-4000-8000-0000000000d1';
const lead = '20000000-0000-4000-8000-0000000000e1';
const party = '20000000-0000-4000-8000-0000000000a1';
const current = '20000000-0000-4000-8000-0000000000a2';

test('action links use current draft eligibility on PostgreSQL', async t => {
  const db = await postgresDatabase('action-links-');
  const c = db.client;
  try {
    const schema = await readFile(new URL('../../db/schema.sql', import.meta.url), 'utf8');
    for (const name of ['actor', 'party', 'lead', 'lead_contact_draft']) {
      const ddl = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`));
      assert.ok(ddl, `schema contains ${name}`);
      await c.query(ddl[0]);
    }
    await c.query("insert into party(id,kind,name,created_by,updated_by) values ($1,'person','Original',$3,$3),($2,'person','Current',$3,$3)", [party, current, actor.id]);
    await c.query("insert into lead(id,party_id,stage,created_by,updated_by) values ($1,$2,'qualified',$3,$3)", [lead, party, actor.id]);
    await c.query("insert into lead_contact_draft(id,lead_id,party_id,owner_id,subject,body,scheduled_for,time_zone,created_by) values ($1,$2,$3,$4,'Subject','Body','2026-11-03T14:00:00Z','America/Chicago',$4)", [draft, lead, party, actor.id]);
    const ref = buildActionLink({ kind: 'lead_draft', id: draft }).ref;
    const resolve = who => actionLinkTools({ ToolError })['resolve-action-link'].handler(c, who, { ref });
    const refused = (who, code) => assert.rejects(resolve(who), e => e.payload?.error === code);
    await t.test('owner receives a pending action with the scheduled instant', async () => {
      const result = await resolve(actor);
      assert.equal(result.card.actions[0].action, 'approve');
      assert.equal(Date.parse(result.card.scheduled_for), Date.parse('2026-11-03T14:00:00Z'));
    });
    await t.test('a different current party makes the draft ineligible', async () => {
      await c.query('update lead set party_id=$1 where id=$2', [current, lead]);
      await refused(actor, 'action_link_no_longer_actionable');
      await refused(other, 'action_link_not_found');
    });
    await t.test('a suppressed current party makes the draft ineligible', async () => {
      await c.query('update lead set party_id=$1 where id=$2', [party, lead]);
      await c.query("update party set contact_state='paused' where id=$1", [party]);
      await refused(actor, 'action_link_no_longer_actionable');
      await refused(other, 'action_link_not_found');
    });
  } finally { await db.close(); }
});
