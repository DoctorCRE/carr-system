import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { organizationTenantForActor } from '../src/identity.js';
import { addCalendarConfirmations } from '../src/brief-calendar.js';

let fixture, c, actor, deal, review;
const key = '0000000000000000';
const revision = 'a'.repeat(64);
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'calendar-confirm-', snapshot: true });
  c = new pg.Client(fixture.connection);
  await c.connect();
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic physician',$1,$1) returning id", [actor.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  const client = (await c.query("insert into client(party_id,status,created_by,updated_by,roster_ref) values($1,'fixture_active',$2,$2,'C-999441') returning id", [party, actor.id])).rows[0].id;
  deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic meeting deal',$1,'purchase','site_selection','joe',$2,$2) returning id", [client, actor.id])).rows[0].id;
  await c.query("insert into lead(party_id,stage,created_by,updated_by) values($1,(select slug from lead_stage order by slug limit 1),$2,$2)",[party,actor.id]);
  await c.query("insert into vendor(party_id,category,created_by,updated_by) values($1,'Synthetic',$2,$2)",[party,actor.id]);
  const end = new Date(Date.now()-3600000).toISOString();
  review = { key, title:'Synthetic completed meeting', start:new Date(Date.now()-7200000).toISOString(),end,
    source_revision:revision,canonical_event_identity:key+'b'.repeat(48),
    attendees:[{email:'attendee@example.test'}],attendee_count:1,state:'needs_review',prompt:'Confirm meeting' };
  await c.query('update party set email=$1 where id=$2',['attendee@example.test',party]);
  await c.query(`insert into activity(actor_id,kind,summary,deal_id,calendar_event_key,occurred_at)
    values($1,'meeting','Existing confirmed deal meeting',$2,$3,$4)`,[actor.id,deal,key,end]);
  await c.query(`insert into board_snapshot(organization_tenant_id,sponsoring_human_slug,board_id,snapshot_json,updated_by_actor_id)
    values($1,'joe','calendar-agenda',$2::jsonb,$3)`,[organizationTenantForActor(actor),JSON.stringify({schema:'carr-calendar-agenda.v1',observed_at:new Date().toISOString(),items:[],follow_up_reviews:[review]}),actor.id]);
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });
async function log(key, ref = deal) {
  await c.query('begin');
  try {
    const result = await executeRegisteredTool(c, actor, 'log-activity', { idempotency_key: randomUUID(), ref, kind: 'meeting', summary: 'Synthetic meeting', calendar_event_key: key, calendar_source_revision:revision });
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}
test('meeting confirmation deduplicates across request keys', async () => {
  const first = await log('0000000000000000');
  assert.equal(first.deduped,true,'existing deal meeting is reconciled while missing role touches are applied');
  const retry = await log('0000000000000000');
  assert.equal(retry.activity_id, first.activity_id);
  assert.equal(retry.deduped, true);
  assert.equal(Number((await c.query('select count(*) from activity where calendar_event_key=$1 and deal_id=$2', ['0000000000000000', deal])).rows[0].count), 1);
  await assert.rejects(log('INVALID'));
});
test('calendar reviews show confirmed keys after a reload', async () => {
  const section = { follow_up_reviews: [{ key: '0000000000000000', source_revision:revision }, { key: '1111111111111111' }] };
  await addCalendarConfirmations(c, section, actor);
  assert.deepEqual(section.follow_up_reviews.map(x => x.confirmed), [true, false]);
});

test('completed meeting creates one role touch and rejects stale evidence', async () => {
  await log(key); await log(key);
  assert.equal(Number((await c.query("select count(*) from activity where client_id=(select client_id from deal where id=$1) and kind='meeting'",[deal])).rows[0].count),1);
  const outcomes=(await c.query('select outcomes from calendar_completion')).rows[0].outcomes;
  assert.equal(outcomes.length,3);
  for(const outcome of outcomes) {
    const touch=await c.query(`select last_touch=public.carr_business_date($1::timestamptz) as correct
      from v_last_touch where subject_type=$2 and subject_id=$3`,[review.end,outcome.role,outcome.target]);
    assert.equal(touch.rows[0]?.correct,true,'confirmed attendance updates the canonical last-touch read');
  }
  await c.query(`update board_snapshot set snapshot_json=jsonb_set(snapshot_json,'{follow_up_reviews,0,source_revision}',to_jsonb($1::text)) where board_id='calendar-agenda'`,['c'.repeat(64)]);
  await assert.rejects(log(key),error=>error.payload?.error==='calendar_completion_review_required');
});

test('crash after role application rolls back and a later source revision adds no duplicate', async () => {
  const { confirmCalendarCompletion } = await import('../src/calendar-completion.js');
  const freshKey='2222222222222222';
  const changed={...review,key:freshKey,canonical_event_identity:freshKey+'b'.repeat(48)};
  await c.query(`update board_snapshot set snapshot_json=jsonb_set(snapshot_json,'{follow_up_reviews}',$1::jsonb)
    where board_id='calendar-agenda'`,[JSON.stringify([changed])]);
  const count=async()=>Number((await c.query("select count(*) from activity where kind='meeting'")).rows[0].count);
  const before=await count();
  await c.query('begin');
  await assert.rejects(confirmCalendarCompletion(c,actor,freshKey,revision,async (...args)=>{
    const result=await executeRegisteredTool(...args);
    if(args[2]==='log-activity') throw new Error('synthetic process crash');
    return result;
  }),/synthetic process crash/);
  await c.query('rollback');
  assert.equal(await count(),before);
  await c.query('begin');
  const first=await confirmCalendarCompletion(c,actor,freshKey,revision,executeRegisteredTool);
  await c.query('commit');
  assert.equal(first.outcomes.length,3);
  await c.query(`update board_snapshot set snapshot_json=jsonb_set(snapshot_json,
    '{follow_up_reviews,0,source_revision}',to_jsonb($1::text)) where board_id='calendar-agenda'`,['d'.repeat(64)]);
  await c.query('begin');
  const next=await confirmCalendarCompletion(c,actor,freshKey,'d'.repeat(64),executeRegisteredTool);
  await c.query('commit');
  assert.deepEqual(next.outcomes,first.outcomes);
  assert.equal(await count(),before+3);
});

test('existing human review action confirms role touches without a deal under carr_writer', async () => {
  const snapshot={schema:'carr-calendar-agenda.v1',observed_at:new Date().toISOString(),items:[],follow_up_reviews:[review]};
  await c.query("update board_snapshot set snapshot_json=$1::jsonb where board_id='calendar-agenda'",[JSON.stringify(snapshot)]);
  await c.query('begin');
  try {
    await c.query('set local role carr_writer');
    const result=await executeRegisteredTool(c,{...actor,via:'dealroom-cookie'},'answer-board-question',{
      idempotency_key:randomUUID(),board_id:'calendar-agenda',question_id:key,base_version:1,
      answer_text:'Confirm completed',source_revision:revision});
    assert.equal(result.completion.disposition,'confirmed-complete');
    assert.equal(result.completion.outcomes.length,3);
    await c.query('commit');
  } catch(error) { await c.query('rollback'); throw error; }
});

test('canceled and rescheduled review evidence cannot confirm attendance', async () => {
  for(const fields of [{lifecycle_status:'canceled'},{time_changed:true},{conflict:true}]) {
    const snapshot={schema:'carr-calendar-agenda.v1',observed_at:new Date().toISOString(),items:[],follow_up_reviews:[{...review,...fields}]};
    await c.query("update board_snapshot set snapshot_json=$1::jsonb where board_id='calendar-agenda'",[JSON.stringify(snapshot)]);
    await assert.rejects(log(key),error=>error.payload?.error==='calendar_completion_review_required');
  }
});
