import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { buildActionLink } from '../src/action-links.js';

const owner = { id: '20000000-0000-4000-8000-000000000001', slug: 'joe', human: true, via: 'oauth-google' };
const other = { id: '20000000-0000-4000-8000-000000000002', slug: 'dell', human: true, via: 'oauth-google' };
const id = n => `30000000-0000-4000-8000-${String(n).padStart(12, '0')}`;

test('real morning producer links only owned actionable drafts, bounded and read-only', async () => {
  const db = await postgresDatabase('brief-action-links-');
  const c = db.client;
  try {
    const schema = await readFile(new URL('../../db/schema.sql', import.meta.url), 'utf8');
    for (const name of ['actor', 'party', 'lead', 'lead_contact_draft']) {
      await c.query(schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))[0]);
    }
    await c.query("insert into actor(id,slug,display_name,kind) values ($1,'joe','Joe','human'),($2,'dell','Dell','human')", [owner.id, other.id]);
    for (let n = 1; n <= 25; n++) {
      await c.query("insert into party(id,kind,name,created_by,updated_by) values ($1,'person','Synthetic practice',$2,$2)", [id(n), owner.id]);
      await c.query("insert into lead(id,party_id,stage,created_by,updated_by) values ($1,$1,'qualified',$2,$2)", [id(n), owner.id]);
      await c.query("insert into lead_contact_draft(id,lead_id,party_id,owner_id,subject,body,scheduled_for,time_zone,created_by,revision) values ($1,$1,$1,$2,'Subject','Body','2026-11-04T14:00:00Z','America/Chicago',$2,3)", [id(n), n === 2 ? other.id : owner.id]);
    }
    await c.query("update lead set stage='rejected' where id=$1", [id(3)]);
    await c.query('update lead_contact_draft set approved_at=now(),approved_by=$2 where id=$1', [id(4), owner.id]);
    await c.query('update lead_contact_draft set party_id=$2 where id=$1', [id(5), id(1)]);
    const before = (await c.query('select * from lead_contact_draft order by id')).rows;
    const reads = [];
    const reader = { query: (sql, params) => {
      assert.match(sql.trim(), /^select\b/i, 'brief and resolver execute only reads');
      reads.push(sql);
      return c.query(sql, params);
    } };
    const brief = async actor => (await executeRegisteredTool(reader, actor, 'morning-brief', {})).sections.lead_drafts;
    const section = await brief(owner);
    assert.equal(section.state, 'ready');
    assert.equal(section.items.length, 20);
    assert.deepEqual(section.items.map(row => row.id), [1, ...Array.from({ length: 19 }, (_, i) => i + 6)].map(id));
    assert.equal(reads.filter(sql => sql.includes('from lead_contact_draft')).length, 1);
    for (const row of section.items) {
      assert.deepEqual(row.action_link, buildActionLink({ kind: 'lead_draft', id: row.id }));
      const resolved = await executeRegisteredTool(reader, owner, 'resolve-action-link', { ref: row.action_link.ref });
      assert.equal(resolved.card.id, row.id);
      assert.equal(resolved.card.revision, row.revision);
      assert.equal(resolved.card.state, 'pending_approval');
      await assert.rejects(executeRegisteredTool(c, other, 'resolve-action-link', { ref: row.action_link.ref }), /action_link_not_found/);
    }
    const dell = await brief(other);
    assert.deepEqual(dell.items.map(row => row.id), [id(2)]);
    await assert.rejects(executeRegisteredTool(c, { slug: 'codex', human: false, via: 'agent-token' }, 'morning-brief', {}), /morning_brief_requires_partner_scope/);
    assert.deepEqual((await c.query('select * from lead_contact_draft order by id')).rows, before);
    const ref = section.items[0].action_link.ref;
    await c.query('update lead_contact_draft set revision=revision+1 where id=$1', [id(1)]);
    assert.equal((await executeRegisteredTool(c, owner, 'resolve-action-link', { ref })).card.revision, 4);
    await c.query('update lead_contact_draft set approved_at=now(),approved_by=$2 where id=$1', [id(1), owner.id]);
    await assert.rejects(executeRegisteredTool(c, owner, 'resolve-action-link', { ref }), /action_link_no_longer_actionable/);
    await c.query('update lead_contact_draft set approved_at=null,approved_by=null where id=$1', [id(1)]);
    await c.query("update lead set stage='rejected' where id=$1", [id(1)]);
    await assert.rejects(executeRegisteredTool(c, owner, 'resolve-action-link', { ref }), /action_link_no_longer_actionable/);
    await c.query("update lead set stage='rejected'");
    assert.deepEqual(await brief(owner), { state: 'empty', items: [] });
  } finally { await db.close(); }
});
