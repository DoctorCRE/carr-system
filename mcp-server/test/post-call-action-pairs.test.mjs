import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

const pairs = JSON.parse(readFileSync(new URL('./fixtures/post-call-action-pairs.json', import.meta.url), 'utf8'));

test('labeled action pairs through read-call-outcomes', async t => {
  const fixture = await acquireDisposablePostgres({ prefix: 'call-action-pairs-', snapshot: true });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  try {
    const actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
    const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic pair physician',$1,$1) returning id", [actor.id])).rows[0].id;
    await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
    const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'fixture_active',$2,$2) returning id", [party, actor.id])).rows[0].id;
    const session = (await c.query(`insert into capture_session(nonce,device_id,actor_id,mode,started_at,consent_announced_at,
      session_token_hash,expires_at,state,state_at,post_call) values($1,'fixture',$2,'meeting',now(),now(),$1,
      now()+interval '1 day','distilling',now(),true) returning id`, [randomUUID(), actor.id])).rows[0].id;
    let falseFlags = 0, trueFlags = 0;
    for (const [index, pair] of pairs.entries()) {
      const deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values($1,$2,'lease','site_selection','joe',$3,$3) returning id", [`Synthetic ${pair.id}`, client, actor.id])).rows[0].id;
      await c.query(`insert into capture_post_call_candidate
        (session_id,idempotency_key,batch_hash,item_index,kind,deal_id,assignee_slug,action_description,evidence_quote,confidence)
        values($1,$2,$2,$3,'assigned_action',$4,'joe',$5,'Synthetic pair evidence',1)`,
      [session, randomUUID(), index, deal, pair.proposed]);
      await c.query(`insert into next_action(subject_type,subject_id,owner_id,description,status,created_by)
        values('deal',$1,$2,$3,'done',$2)`, [deal, actor.id, pair.existing]);
    }
    const result = await executeRegisteredTool(c, actor, 'read-call-outcomes', {});
    const cards = result.calls.find(call => call.session_id === session).open_cards;
    assert.equal(cards.length, pairs.length);
    for (const pair of pairs) {
      const comparison = cards.find(card => card.payload.action === pair.proposed).existing_action_comparison;
      const flagged = comparison.status === 'possible_match';
      if (flagged) { if (pair.duplicate) trueFlags++; else falseFlags++; }
      t.diagnostic(`${pair.id} gold=${pair.duplicate} observed=${comparison.status}`);
      assert.equal(comparison.exact_match, null);
    }
    t.diagnostic(`pairs=${pairs.length} falseFlags=${falseFlags} trueFlags=${trueFlags}`);
    assert.equal(falseFlags, 0);
    assert.ok(trueFlags >= 5);
  } finally { await c.end(); await fixture.close(); }
});
