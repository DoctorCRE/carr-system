// Review stored email document replies through registered verbs on disposable Postgres.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { CHECKLIST_SOURCES, readDealChecklist } from '../src/deal-checklist.ts';
import { applyFutureWorkSchema, restoreEventIdentity } from './helpers/snapshot-schema.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');
const actor = { id: randomUUID(), slug: 'joe', human: true };
const deal = randomUUID(), other = randomUUID();

test('a participant document reply proposes its open request without fulfilling it', async t => {
  const fixture = await postgresDatabase('requirement-replies-');
  const c = fixture.client;
  try {
    const schema = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
    for (const name of ['actor', 'attachment', 'event', 'tool_call'])
      await c.query(schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))[0]);
    await restoreEventIdentity(c, schema);
    await c.query(`alter table actor add primary key(id); alter table attachment add primary key(id);
      alter table tool_call add primary key(idempotency_key);
      create role carr_reader; create role carr_writer; create role carr_authority; create schema ops;
      create table deal(id uuid primary key, client_id uuid, deal_type text, owner text);
      create table v_ref_index(subject_type text, subject_id uuid, ref text, display_name text);
      create table doctrine_section(id uuid, status text, body_hash text);
      create table v_deal_room_commission(deal_id uuid, commission_id uuid, status text, received_on date);
      create function ops.scac_current_grant_contract() returns jsonb language sql as $$select '{"relation_dml":[]}'::jsonb$$;`);
    await applyFutureWorkSchema(c);
    await c.query("insert into actor(id,slug,kind,display_name,active) values($1,'joe','human','Synthetic partner',true)", [actor.id]);
    await c.query("insert into deal values($1,$3,'purchase','joe'),($2,$3,'purchase','joe')", [deal, other, randomUUID()]);
    await c.query("insert into v_ref_index values('deal',$1,'D-1','Synthetic closing'),('deal',$2,'D-2','Synthetic other')", [deal, other]);
    for (const s of CHECKLIST_SOURCES) await c.query("insert into doctrine_section values($1,'active',$2)", [s.section_id, s.content_hash]);
    await c.query(readFileSync(new URL('../../migrations/1105_requirement_requests.sql', import.meta.url), 'utf8'));
    const call = async (name, args) => {
      await c.query('begin');
      try {
        const result = await executeRegisteredTool(c, actor, name, { ...(name.startsWith('list-') ? {} : { idempotency_key: randomUUID() }), ...args });
        await c.query('commit'); return result;
      } catch (error) { await c.query('rollback'); throw error; }
    };
    const activity = randomUUID();
    await c.query(`create table activity(id uuid primary key,client_id uuid,deal_id uuid,kind text,email_context jsonb,detail text,version integer default 1,occurred_at timestamptz default now());
      create table party(id uuid primary key,email text,deleted_at timestamptz,merged_into uuid);
      create table deal_participant(deal_id uuid,party_id uuid,to_at timestamptz);`);
    await c.query(readFileSync(new URL('../../migrations/1116_requirement_document_replies.sql', import.meta.url),'utf8'));
    for (const name of ['notification', 'notification_delivery'])
      await c.query(schema.match(new RegExp(`CREATE TABLE ops\\.${name} \\([\\s\\S]*?\\n\\);`))[0]);
    await c.query(readFileSync(new URL('../../migrations/1114_home_inbox.sql', import.meta.url), 'utf8').split('create or replace function ops.notification_feed_facts')[0]);
    await c.query(readFileSync(new URL('../../migrations/1132_checklist_fulfilled_reminders.sql', import.meta.url), 'utf8'));
    await c.query(readFileSync(new URL('../../migrations/1133_checklist_receipt_filename.sql', import.meta.url), 'utf8'));
    const requested = await call('request-checklist-document', { deal, row_key: 'title_commitment', owner: 'joe', due_on: '2020-01-01' });
    const checklist = () => readDealChecklist(c, { id: deal, type: 'purchase', owner: 'joe' }, new Date('2026-10-20T17:00:00Z'));
    assert.equal(requested.request.document_type, 'title_commitment');
    assert.equal(requested.request.source.section_id, CHECKLIST_SOURCES[2].section_id);
    const person = randomUUID();
    await c.query("insert into party(id,email) values($1,'participant@example.test')", [person]);
    await c.query('insert into deal_participant values($1,$2,null)', [deal, person]);
    await c.query("insert into activity(id,deal_id,kind,email_context,detail) values($1,$2,'email_in',$3,'Attached title commitment')",
      [activity, deal, JSON.stringify({sender:{email:'participant@example.test'}, subject:'Title commitment',conversation_id:'synthetic-thread'})]);
    const sha = hash('synthetic document');
    const attached = await call('attach-deal-file', {deal,activity_id:activity,filename:'Title.pdf',mime:'application/pdf',
      sha256:sha,bytes:18,r2_key:`documents/C-1/${sha.slice(0,16)}/Title.pdf`});
    const proposals = await call('list-requirement-reply-proposals', {deal});
    assert.equal(proposals.proposals.length, 1);
    assert.equal(proposals.proposals[0].candidates[0].request_id, requested.request.id);
    assert.equal(proposals.proposals[0].attachment_id, attached.attachment_id);
    assert.equal((await call('list-requirement-requests',{deal})).requests[0].status,'open');
    const pending = await checklist();
    assert.equal(pending.due_items.find(row => row.id === 'title_commitment').status, 'overdue');
    await t.test('confirm fulfills with the stored attachment, while satisfaction remains separate', async () => {
      const queued = randomUUID();
      await c.query(`insert into ops.notification(id,subject_type,subject_ref,event_ref,event_source,recipient_actor,reason,severity,deep_link,dedupe_key)
        values($1,'deal',$2,$3,'event',$4,'Synthetic due document','action_required','/notifications',$5)`,
      [queued,deal,randomUUID(),actor.id,`deal-checklist:${deal}:title_commitment:2020-01-01`]);
      await c.query(`insert into ops.notification_delivery(notification_id,channel,state,held_until)
        values($1,'in_app','held_until_morning',now()+interval '1 hour'),($1,'device','pending',null)`,[queued]);
      const controls = [];
      for (const [subject, key] of [[deal,`deal-checklist:${deal}:appraisal:2020-01-01`],
        [other,`deal-checklist:${other}:title_commitment:2020-01-01`]]) {
        const id = randomUUID(); controls.push(id);
        await c.query(`insert into ops.notification(id,subject_type,subject_ref,event_ref,event_source,recipient_actor,reason,severity,deep_link,dedupe_key)
          values($1,'deal',$2,$3,'event',$4,'Other due document','action_required','/notifications',$5)`,
        [id,subject,randomUUID(),actor.id,key]);
        await c.query("insert into ops.notification_delivery(notification_id,channel,state) values($1,'device','pending')",[id]);
      }
      const proposal = proposals.proposals[0];
      const result = await call('confirm-requirement-reply',{proposal_id:proposal.id,base_version:proposal.version,
        proposal_digest:proposal.proposal_digest,request_id:requested.request.id});
      assert.equal(result.request_status,'fulfilled');
      assert.deepEqual((await c.query('select state from ops.notification_delivery where notification_id=$1',[queued])).rows.map(r=>r.state),
        ['suppressed_fulfilled','suppressed_fulfilled']);
      assert.equal((await c.query('select ops.notification_visible($1) as visible',[queued])).rows[0].visible,false);
      assert.deepEqual((await c.query('select state from ops.notification_delivery where notification_id=any($1::uuid[])',[controls])).rows.map(r=>r.state),
        ['pending','pending']);
      const row = (await call('list-requirement-requests',{deal})).requests[0];
      assert.equal(row.status,'fulfilled');
      assert.equal(row.evidence_attachment_id,attached.attachment_id);
      assert.equal(row.satisfied,false);
      const received = await checklist();
      const item = received.phases.flatMap(phase => phase.items).find(item => item.id === 'title_commitment');
      assert.equal(item.status, 'received');
      assert.equal(item.request.status, 'fulfilled');
      assert.equal(item.request.satisfied, false);
      assert.ok(item.request.fulfilled_at);
      assert.equal(item.request.fulfilled_by, actor.id);
      assert.equal(item.request.evidence_attachment_id, attached.attachment_id);
      assert.equal(item.evidence_ref, 'Title.pdf');
      assert.equal(received.due_items.some(item => item.id === 'title_commitment'), false);
      assert.equal((await call('list-requirement-reply-proposals',{deal})).proposals.length,0);
    });
    await t.test('participant thread reply lists two candidates and rejection leaves both open', async () => {
      const thread = randomUUID();
      await c.query("insert into activity(id,deal_id,kind,email_context,occurred_at) values($1,$2,'email_out',$3,now()-interval '1 day')",
        [thread,deal,JSON.stringify({conversation_id:'requested-documents'})]);
      const requests = [];
      for (const row_key of ['appraisal','survey']) requests.push((await call('request-checklist-document',
        {deal,row_key,owner:'joe',due_on:'2026-11-15',thread_activity_id:thread})).request);
      const reply = randomUUID();
      await c.query("insert into activity(id,deal_id,kind,email_context,detail) values($1,$2,'email_in',$3,'Here is the document')",
        [reply,deal,JSON.stringify({sender:{email:'participant@example.test'},subject:'Re: Closing',conversation_id:'requested-documents'})]);
      const sha = hash('synthetic thread reply');
      const args = {deal,activity_id:reply,filename:'Closing.pdf',mime:'application/pdf',sha256:sha,bytes:22,
        r2_key:`documents/C-1/${sha.slice(0,16)}/Closing.pdf`};
      await call('attach-deal-file',args);
      const [proposal] = (await call('list-requirement-reply-proposals',{deal})).proposals;
      assert.deepEqual(proposal.candidates.map(r=>r.request_id).sort(),requests.map(r=>r.id).sort());
      await call('reject-requirement-reply',{proposal_id:proposal.id,base_version:proposal.version,reason:'Wrong attachment'});
      await call('attach-deal-file',args);
      assert.equal((await call('list-requirement-reply-proposals',{deal})).proposals.length,0);
      const listed = (await call('list-requirement-requests',{deal})).requests;
      assert.ok(requests.every(r=>listed.find(x=>x.id===r.id).status==='open'));
      const rejected = (await checklist()).phases.flatMap(phase => phase.items);
      assert.ok(requests.every(r => rejected.find(item => item.id === r.row_key).status === 'upcoming'));
    });
    await t.test('non-participant sender and unrelated document names propose nothing', async () => {
      for (const [sender,body] of [['outsider@example.test','Attached appraisal'],['participant@example.test','Appraisals are unrelated']]) {
        const id = randomUUID(),sha=hash(id);
        await c.query("insert into activity(id,deal_id,kind,email_context,detail) values($1,$2,'email_in',$3,$4)",
          [id,deal,JSON.stringify({sender:{email:sender},subject:'Closing',conversation_id:sender==='outsider@example.test'?'requested-documents':'unrelated-thread'}),body]);
        await call('attach-deal-file',{deal,activity_id:id,filename:'Evidence.pdf',mime:'application/pdf',sha256:sha,
          bytes:12,r2_key:`documents/C-1/${sha.slice(0,16)}/Evidence.pdf`});
      }
      assert.equal((await call('list-requirement-reply-proposals',{deal})).proposals.length,0);
    });
    await t.test('a named recipient can propose a request without deal participation', async () => {
      const request = (await call('request-checklist-document',{deal,row_key:'environmental',owner:'joe',due_on:'2026-11-15',
        recipient_email:'Recipient@example.test'})).request;
      const id=randomUUID(),sha=hash(id);
      await c.query(`insert into activity(id,client_id,kind,email_context,detail)
        select $1,client_id,'email_in',$3,'Environmental document attached' from deal where id=$2`,
        [id,deal,JSON.stringify({sender:{email:'recipient@example.test'},subject:'Environmental'})]);
      await call('attach-deal-file',{deal,activity_id:id,filename:'Environmental.pdf',mime:'application/pdf',sha256:sha,
        bytes:12,r2_key:`documents/C-1/${sha.slice(0,16)}/Environmental.pdf`});
      const [proposal]=(await call('list-requirement-reply-proposals',{deal})).proposals;
      assert.deepEqual(proposal.candidates.map(r=>r.request_id),[request.id]);
      await assert.rejects(call('confirm-requirement-reply',{proposal_id:proposal.id,base_version:99,
        request_id:request.id,proposal_digest:proposal.proposal_digest}),/version_conflict/);
      await c.query("update activity set detail='Different document',version=version+1 where id=$1",[id]);
      await assert.rejects(call('confirm-requirement-reply',{proposal_id:proposal.id,base_version:proposal.version,
        request_id:request.id,proposal_digest:proposal.proposal_digest}),/requirement_reply_stale/);
      await call('reject-requirement-reply',{proposal_id:proposal.id,base_version:proposal.version,reason:'Stale evidence'});
    });
    await t.test('tenant claims cannot widen scope and cross-deal attachments are filed', async () => {
      await assert.rejects(call('list-requirement-reply-proposals',{deal,organization_tenant_id:'other-tenant'}),/caller_authority_field_forbidden/);
      const result = await call('attach-deal-file',{deal:other,activity_id:activity,filename:'Other.pdf',mime:'application/pdf',
        sha256:sha,bytes:18,r2_key:`documents/C-2/${sha.slice(0,16)}/Other.pdf`});
      assert.equal(result.existing,false);
      assert.equal(result.reply_proposal_skipped.source_mismatch,1);
      await assert.rejects(c.query(`insert into ops.requirement_reply_proposal(organization_tenant_id,deal_id,activity_id,attachment_id,created_by)
        values('other-tenant',$1,$2,$3,$4)`,[deal,activity,attached.attachment_id,actor.id]),/check constraint/);
      assert.equal((await call('list-requirement-reply-proposals',{deal:other})).proposals.length,0);
      const machine={...actor,human:false,slug:'outlook-intake'};
      await assert.rejects(executeRegisteredTool(c,machine,'confirm-requirement-reply',{idempotency_key:randomUUID(),
        proposal_id:proposals.proposals[0].id,base_version:1,request_id:requested.request.id,
        proposal_digest:proposals.proposals[0].proposal_digest}),/human_only_verb_requires_verified_partner/);
    });
    await t.test('an email moved to another deal still files its attachment', async () => {
      const id=randomUUID(), digest=hash(id);
      await c.query("insert into activity(id,deal_id,kind,email_context,detail) values($1,$2,'email_in',$3,'Title commitment')",
        [id,deal,JSON.stringify({sender:{email:'participant@example.test'},subject:'Title commitment'})]);
      await c.query('update activity set deal_id=$2 where id=$1',[id,other]);
      const result=await call('attach-deal-file',{deal,activity_id:id,filename:'Moved.pdf',mime:'application/pdf',sha256:digest,bytes:12,
        r2_key:`documents/C-1/${digest.slice(0,16)}/Moved.pdf`});
      assert.equal(result.existing,false);
      assert.equal(result.reply_proposal_skipped.source_mismatch,1);
      assert.equal((await c.query('select subject_id from attachment where id=$1',[result.attachment_id])).rows[0].subject_id,deal);
      assert.equal((await c.query('select count(*)::int as n from ops.requirement_reply_proposal where activity_id=$1',[id])).rows[0].n,0);
    });
    await t.test('a participant reply in the requested thread selects only that request', async () => {
      const thread=randomUUID(),id=randomUUID(),sha=hash(id);
      await c.query('insert into deal_participant values($1,$2,null)',[other,person]);
      await c.query("insert into activity(id,deal_id,kind,email_context,occurred_at) values($1,$2,'email_out',$3,now()-interval '1 hour')",
        [thread,other,JSON.stringify({conversation_id:'single-request-thread'})]);
      const requested=(await call('request-checklist-document',{deal:other,row_key:'title_commitment',owner:'joe',due_on:'2026-11-15',thread_activity_id:thread})).request;
      await call('request-checklist-document',{deal:other,row_key:'appraisal',owner:'joe',due_on:'2026-11-15'});
      await c.query("insert into activity(id,deal_id,kind,email_context,detail) values($1,$2,'email_in',$3,'Here it is')",
        [id,other,JSON.stringify({sender:{email:'participant@example.test'},subject:'Re: Requested document',conversation_id:'single-request-thread'})]);
      await call('attach-deal-file',{deal:other,activity_id:id,filename:'Document.pdf',mime:'application/pdf',sha256:sha,bytes:12,
        r2_key:`documents/C-2/${sha.slice(0,16)}/Document.pdf`});
      const [proposal]=(await call('list-requirement-reply-proposals',{deal:other})).proposals;
      assert.deepEqual(proposal.candidates.map(r=>r.request_id),[requested.id]);
      const args={idempotency_key:randomUUID(),proposal_id:proposal.id,base_version:proposal.version,
        request_id:requested.id,proposal_digest:proposal.proposal_digest};
      await call('confirm-requirement-reply',args);
      assert.equal((await call('confirm-requirement-reply',args)).replayed,true);
      const listed=(await call('list-requirement-requests',{deal:other})).requests;
      assert.equal(listed.find(r=>r.row_key==='appraisal').status,'open');
    });
  } finally { await fixture.close(); }
});
