import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { postgresDatabase } from './helpers/postgres-database.mjs';

const id = n => `af000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const migration = name => readFileSync(new URL(`../../migrations/${name}`, import.meta.url), 'utf8');

test('Home reply drafts collapse pending conversations without changing proposals', async t => {
  const db = await postgresDatabase('home-draft-conversations-');
  const c = db.client;
  t.after(() => db.close());
  await c.query(`create schema ops;
    create table actor(id uuid primary key,slug text);
    create table activity(id uuid primary key,email_source jsonb,email_context jsonb);
    create table email_proposal(id uuid primary key,activity_id uuid,owner text,kind text,
      status text,version integer,draft_subject text,created_at timestamptz,
      submitted_at timestamptz,result_recorded_at timestamptz);
    create table next_action(id uuid,owner_id uuid,subject_type text,subject_id uuid,
      description text,status text,version integer,created_at timestamptz,updated_at timestamptz,
      hold_until date,due_on date);
    create table capture_post_call_action(id uuid,owner_id uuid,status text,completed_at timestamptz,
      updated_at timestamptz,description text,deal_id uuid,due_on date,version integer);
    create table capture_session(id uuid,actor_id uuid,state text,state_at timestamptz,started_at timestamptz);
    create table ops.notification(id uuid,recipient_actor uuid,subject_type text,subject_ref text,
      severity text,created_at timestamptz,reason text,deep_link text);
    create table ops.notification_read(notification_id uuid,recipient_actor uuid,read_at timestamptz);
    create function ops.notification_visible(uuid) returns boolean language sql as $$select true$$;
    create table deal(id uuid,name text);
    create table loop_item(id uuid,title text);
    create table lease(id uuid,client_id uuid,expiration_on date);
    create table client(id uuid,party_id uuid);
    create table party(id uuid,name text);
    insert into actor values('${id(1)}','fixture'),('${id(2)}','other');`);
  await c.query(migration('1161_home_watch_alerts.sql'));
  const add = async (n, conversation, {status='pending',owner='fixture',sender={name:'Synthetic Sender',email:'sender@example.test'}}={}) => {
    await c.query('insert into activity values($1,$2,$3)', [id(100+n),
      {mailbox_owner:owner},{conversation_id:conversation,sender,subject:'Synthetic subject'}]);
    await c.query(`insert into email_proposal values($1,$2,$3,'reply_draft',$4,1,
      'Synthetic subject','2026-11-01'::timestamptz + $5 * interval '1 minute',null,null)`,
      [id(n),id(100+n),owner,status,n]);
  };
  for (const n of [10,11,12,13]) await add(n,'thread-a');
  const read = async (actor=1) => (await c.query(
    "select * from ops.home_inbox_sources_v2($1) where source_type='email_proposal' order by source_id",[id(actor)])).rows;
  assert.equal((await read()).length,4,'baseline reproduces four cards for one conversation');
  const before=(await c.query('select * from email_proposal order by id')).rows;
  if (!process.env.HOME_DRAFT_BASELINE) await c.query(migration('1186_home_draft_conversations.sql'));
  await t.test('four pending drafts yield the newest card and all covered IDs',async()=>{
    const cards=await read();
    assert.equal(cards.length,1);
    assert.equal(cards[0].source_id,id(13));
    assert.deepEqual(cards[0].merged_source_ids,[id(13),id(12),id(11),id(10)]);
    assert.equal(cards[0].title,'Synthetic Sender · Reply needed (3 older drafts)');
    assert.equal(cards[0].context_label,'Synthetic subject');
    assert.deepEqual((await c.query('select * from email_proposal order by id')).rows,before);
  });
  await add(14,'thread-b',{sender:{email:'fallback@example.test'}});
  await t.test('two conversations yield two cards even with identical subjects',async()=>{
    assert.equal((await read()).length,2);
    assert.equal((await read()).find(r=>r.source_id===id(14)).title,'fallback@example.test · Reply needed');
  });
  await add(15,'thread-b',{status:'dismissed'});
  await t.test('resolved drafts cannot replace the pending representative',async()=>{
    assert.equal((await read()).length,2);
    assert.ok(!(await read()).some(r=>r.source_id===id(15)));
  });
  await add(16,null); await add(17,''); await add(18,'   ',{sender:{}});
  await t.test('missing conversation IDs stay separate and missing sender is honest',async()=>{
    assert.equal((await read()).length,5);
    assert.equal((await read()).find(r=>r.source_id===id(18)).title,'Unknown sender · Reply needed');
  });
  await add(19,'thread-a',{owner:'other'});
  await t.test('mailbox owners never merge or see each other',async()=>{
    assert.equal((await read()).length,5);
    assert.deepEqual((await read(2)).map(r=>r.source_id),[id(19)]);
  });
  await add(20,'thread-a',{status:'failed'});
  await t.test('failed send cards retain their independent status and identity',async()=>{
    assert.equal((await read()).length,6);
    assert.equal((await read()).find(r=>r.source_id===id(20)).source_state,'failed');
  });
});
