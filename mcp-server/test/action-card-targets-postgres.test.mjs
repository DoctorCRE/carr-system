import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { Validator } from '@cfworker/json-schema';
import { executeRegisteredTool } from '../src/tools.js';
import { buildActionCardRef, parseActionCardResponse } from '../src/action-card-targets.ts';
import { buildActionLink } from '../src/action-links.js';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import pg from 'pg';
import { publicError } from '../src/public-errors.ts';

const id = n => `20000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const actors = ['joe', 'dell'].map((slug, i) => ({ id: id(i + 1), slug, human: true }));

let fixture, c;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'action-card-targets-', snapshot: true });
  c = new pg.Client(fixture.connection);
  await c.connect();
  for (const who of actors) {
    await c.query("update actor set slug=slug||'-snapshot' where slug=$1", [who.slug]);
    await c.query("insert into actor(id,slug,kind,display_name) values($1,$2,'human','Synthetic partner')", [who.id, who.slug]);
  }
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });

test('exact card lifecycle on disposable PostgreSQL', async t => {
  const call = async (who, kind, sourceId, version) => {
    await c.query('begin read only');
    try {
      await c.query('set local role carr_writer');
      const result = await executeRegisteredTool(c, who, 'resolve-action-link-v2', { ref: buildActionCardRef({ kind, id: sourceId, version }) });
      await c.query('commit');
      return result;
    } catch (error) { await c.query('rollback'); throw error; }
  };
  const version = async (who, kind, sourceId) => (await c.query('select ops.resolve_action_card($1,$2,$3) as target', [who.id, kind, sourceId])).rows[0].target.source_version;
  const refusals = [];
  const refusalErrors = [];
  const refused = promise => assert.rejects(promise, e => {
    assert.deepEqual(e.payload, { error: 'action_link_not_found' }); refusals.push(e.payload); refusalErrors.push(e); return true;
  });
  const fixtures = [];
  for (const [i, who] of actors.entries()) {
    const session = id(100 + i), event = id(200 + i), grant = id(300 + i);
    await c.query(`insert into capture_session(id,nonce,device_id,actor_id,mode,started_at,consent_announced_at,
      session_token_hash,expires_at,state,state_at,post_call) values($1,'fixture','fixture',$2,'meeting',
      '2026-11-01Z','2026-11-01Z','fixture','2026-11-02Z','done','2026-11-01Z',true)`, [session, who.id]);
    await c.query(`insert into ops.tour(id,organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version)
      values($1,'carr-internal','Synthetic tour','active',1,'fixture')`, [grant]);
    await c.query("insert into ops.tour_property(id,organization_tenant_id,property_status) values($1,'carr-internal','active')", [grant]);
    await c.query(`insert into ops.tour_property_membership(organization_tenant_id,tour_id,property_id,
      route_version,route_sequence,route_label,assertion_set_digest,selected_at)
      values('carr-internal',$1,$1,1,1,'A',$2,'2026-11-01Z')`, [grant, 'sha256:' + 'a'.repeat(64)]);
    await c.query(`insert into ops.tour_public_projection(id,organization_tenant_id,tour_id,projection_version,
      route_version,as_of,facts_only,projection_digest,status)
      values($1,'carr-internal',$1,1,1,'2026-11-01Z',true,$2,'draft')`, [grant, 'sha256:' + 'a'.repeat(64)]);
    await c.query(`insert into ops.tour_share_grant(id,organization_tenant_id,projection_id,grant_version,
      token_digest,audience,permission_scopes,created_by_actor_id,status,created_at,expires_at)
      values($1,'carr-internal',$1,1,$2,'client','["view_packet","comment"]',$3,'active','2026-11-01Z','2100-01-01Z')`, [grant, 'sha256:' + String(i + 1).repeat(64), who.id]);
    await c.query(`insert into ops.tour_share_feedback_event(id,organization_tenant_id,projection_id,
      share_grant_id,property_id,idempotency_key,action,comment,payload_digest,created_at)
      values($1,'carr-internal',$2,$2,$2,$1,'comment','Synthetic feedback',$3,'2026-11-01Z')`, [event, grant, 'sha256:' + 'a'.repeat(64)]);
    await t.test(`${who.slug} processing completion without decisions is resolved`, async () => {
      const result = await call(who,'post_call_review',session,await version(who,'post_call_review',session));
      assert.equal(result.card.state,'resolved');
      assert.deepEqual(result.card.actions,[]);
    });
    await c.query(`insert into capture_candidate(id,session_id,idempotency_key,batch_hash,item_index,kind,payload,evidence_quote,confidence)
      values($1,$2,'fixture','fixture',0,'next_step','{"action":"Synthetic pending action"}','Synthetic evidence',0.95)`, [id(600+i),session]);
    const sv = await version(who, 'post_call_review', session);
    await t.test(`${who.slug} current source resolves repeatedly without mutation`, async () => {
      const first = await call(who, 'post_call_review', session, sv);
      assert.equal(first.card.state, 'pending_review');
      assert.equal(first.card.id, session);
      assert.deepEqual(await call(who, 'post_call_review', session, sv), first);
      const feedback = await call(who, 'feedback_event', event, String(i + 1));
      assert.equal(feedback.card.state,'resolved');
      assert.equal(feedback.card.detail.event_id,event);
      const resolvedFeedback = await call(who,'feedback_event',event,'999');
      assert.equal(resolvedFeedback.card.state,'resolved');
      assert.equal(resolvedFeedback.card.source_version,String(i+1));
      assert.deepEqual(resolvedFeedback.card.actions,[]);
      fixtures.push(first, feedback);
    });
    await t.test(`${who.slug} foreign and missing refuse identically before detail`, async () => {
      for (const [kind, sourceId, v] of [['post_call_review', session, sv], ['feedback_event', event, String(i + 1)]]) {
        await refused(call(actors[1 - i], kind, sourceId, v));
        await refused(call(who, kind, id(999), v));
        assert.equal((await call(who, kind, sourceId, v)).card.id, sourceId);
      }
    });
    await t.test(`${who.slug} changed source is stale then current version restores review`, async () => {
      await c.query("update capture_session set started_at='2026-11-02Z' where id=$1", [session]);
      const stale = await call(who, 'post_call_review', session, sv);
      assert.equal(stale.card.state, 'stale');
      assert.deepEqual(stale.card.actions, []);
      assert.equal((await call(who, 'post_call_review', session, stale.card.source_version)).card.state, 'pending_review');
    });
    await t.test(`${who.slug} reviewed source resolves with no operations`, async () => {
      await c.query('update capture_session set outcome_reviewed_at=now(),outcome_reviewed_by=actor_id where id=$1', [session]);
      const result = await call(who, 'post_call_review', session, sv);
      assert.equal(result.card.state, 'resolved');
      assert.deepEqual(result.card.actions, []);
    });
    await t.test(`${who.slug} retired sources hide detail and operations`, async () => {
      await c.query('update capture_session set post_call=false where id=$1', [session]);
      await c.query(`insert into ops.tour_share_grant_revocation_receipt(organization_tenant_id,share_grant_id,
        revoked_at,actor_id,reason,receipt_digest) values('carr-internal',$1,'2026-11-02Z',$2,'Synthetic revocation',$3)`,
      [grant, who.id, 'sha256:' + 'b'.repeat(64)]);
      for (const [kind, sourceId, v] of [['post_call_review', session, sv], ['feedback_event', event, String(i + 1)]]) {
        const result = await call(who, kind, sourceId, v);
        assert.equal(result.card.state, 'retired');
        assert.equal(result.card.detail, null);
        assert.deepEqual(result.card.actions, []);
      }
    });
    await t.test(`${who.slug} deleted source becomes missing`, async () => {
      await c.query('delete from capture_candidate where session_id=$1', [session]);
      await c.query('delete from capture_session where id=$1', [session]);
      await assert.rejects(c.query('delete from ops.tour_share_feedback_event where id=$1', [event]), /append-only/);
      await c.query('truncate ops.tour_share_feedback_event');
      await refused(call(who, 'post_call_review', session, sv));
      await refused(call(who, 'feedback_event', event, String(i + 1)));
    });
  }
  await t.test('target outside first 1000 inbox sources resolves directly', async () => {
    await c.query(`insert into capture_session(id,nonce,device_id,actor_id,mode,started_at,consent_announced_at,
      session_token_hash,expires_at,state,state_at,post_call) select gen_random_uuid(),gen_random_uuid()::text,'fixture',$1,'meeting',
      now(),now(),gen_random_uuid()::text,now()+interval '1 day','done',now(),true from generate_series(1,1001)`, [actors[0].id]);
    await c.query(`insert into capture_candidate(session_id,idempotency_key,batch_hash,item_index,kind,payload,evidence_quote,confidence)
      select id,id::text,'fixture',0,'next_step','{}','Synthetic pending decision',0.95 from capture_session`);
    const oldest = (await c.query('select id from capture_session order by started_at,id offset 1000 limit 1')).rows[0].id;
    assert.equal((await call(actors[0], 'post_call_review', oldest, await version(actors[0], 'post_call_review', oldest))).card.id, oldest);
  });
  await t.test('recorded registered responses pass the real response contract', async () => {
    const contract = JSON.parse(await readFile(new URL('../contracts/action-link-resolve.v2.json', import.meta.url), 'utf8'));
    for (const result of fixtures) {
      assert.equal(new Validator(contract).validate(result).valid, true);
      assert.deepEqual(parseActionCardResponse(result), result);
    }
    const publicRefusals = {
      foreign:publicError(refusalErrors[0],{env:{CORRELATION_ID:id(1)}}),
      unknown:publicError(refusalErrors[1],{env:{CORRELATION_ID:id(2)}}),
    };
    assert.deepEqual(JSON.parse(await readFile(new URL('../contracts/fixtures/action-card-public-refusals.v2.json',import.meta.url),'utf8')),publicRefusals);
    const path = new URL('../contracts/fixtures/action-card-targets.v2.json', import.meta.url);
    if (process.env.RECORD_ACTION_CARD_FIXTURES === '1') await writeFile(path, JSON.stringify(fixtures, null, 2) + '\n');
    if (process.env.RECORD_ACTION_CARD_FIXTURES === '1') {
      const proofPath = new URL('../contracts/fixtures/action-card-proof.v2.json', import.meta.url);
      const proof = JSON.parse(await readFile(proofPath,'utf8'));
      proof.foreign = refusals[0]; proof.unknown = refusals[1];
      await writeFile(proofPath, JSON.stringify(proof,null,2)+'\n');
    }
    assert.deepEqual(JSON.parse(await readFile(path, 'utf8')), fixtures);
  });
});

test('reply exact target delegates existing registered reply hydration', async () => {
  const who = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  await c.query("update actor set email=slug||'@example.test' where slug in ('joe','dell')");
  const source = id(500), proposal = id(501);
  await c.query(`insert into activity(id,occurred_at,actor_id,kind,summary,detail,email_source)
    values($1,now(),$2,'email_in','Synthetic source','Synthetic evidence',
    '{"mailbox_owner":"joe","sender_email":"sender@example.test"}')`, [source, who.id]);
  await c.query(`insert into email_proposal(id,kind,activity_id,evidence_quote,owner,confidence,created_by,draft_subject,draft_body)
    values($1,'reply_draft',$2,'Synthetic evidence','joe',0.95,$3,'Synthetic subject','Synthetic body')`, [proposal, source, who.id]);
  await c.query("update email_proposal set created_at='2020-01-01Z' where id=$1", [proposal]);
  await c.query(`with sources as (insert into activity(id,occurred_at,actor_id,kind,summary,detail,email_source)
    select gen_random_uuid(),now(),$1,'email_in','Synthetic newer source','Synthetic evidence',
      '{"mailbox_owner":"joe","sender_email":"sender@example.test"}' from generate_series(1,1001) returning id)
    insert into email_proposal(kind,activity_id,evidence_quote,owner,confidence,created_by,draft_subject,draft_body)
    select 'reply_draft',id,'Synthetic evidence','joe',0.95,$1,'Synthetic newer draft','Synthetic body' from sources`, [who.id]);
  assert.equal((await c.query("select id from email_proposal where owner='joe' order by created_at desc limit 1000")).rows.some(r=>r.id===proposal),false);
  const ref = buildActionLink({kind:'reply_draft',id:proposal,owner:'joe',version:1}).ref;
  const resolve = async (reference = ref, actor = who) => {
    await c.query('begin read only');
    try {
      await c.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true)",[actor.slug]);
      await c.query('set local role carr_writer');
      const result = await executeRegisteredTool(c, actor, 'resolve-action-link-v2', {ref:reference});
      await c.query('commit'); return result;
    } catch (e) { await c.query('rollback'); throw e; }
  };
  const value = await resolve();
  assert.equal(value.card.id, proposal);
  assert.equal(value.card.source_version, 1);
  assert.equal(value.card.actions[0].verb, 'submit-email-draft');
  if (process.env.RECORD_ACTION_CARD_FIXTURES === '1') {
    const recorded = JSON.parse(await readFile(new URL('../contracts/fixtures/action-card-targets.v2.json', import.meta.url), 'utf8'));
    const responses = [value, recorded[0], recorded[1]];
    const errors = [];
    for (const ref of [buildActionLink({kind:'reply_draft',id:proposal,owner:'dell',version:1}).ref,
      buildActionLink({kind:'reply_draft',id:id(999),owner:'joe',version:1}).ref]) {
      try { await executeRegisteredTool(c, who, 'resolve-action-link-v2', {ref}); assert.fail('expected refusal'); }
      catch (error) { assert.deepEqual(error.payload, {error:'action_link_not_found'}); errors.push(error.payload); }
    }
    const proof = {release:{env:{value:'production'},git_sha:{value:'a'.repeat(40)}}, responses,
      targets:responses.map(r=>({ref:r.ref,kind:r.card.kind,id:r.card.id,source_version:r.card.source_version})),
      foreign:errors[0],unknown:errors[1]};
    await writeFile(new URL('../contracts/fixtures/action-card-proof.v2.json', import.meta.url), JSON.stringify(proof,null,2)+'\n');
  }
  const proof = JSON.parse(await readFile(new URL('../contracts/fixtures/action-card-proof.v2.json', import.meta.url),'utf8'));
  assert.deepEqual(proof.responses[0], value);
  for (const response of proof.responses) assert.deepEqual(parseActionCardResponse(response), response);
  await c.query('update email_proposal set version=2 where id=$1', [proposal]);
  const stale = await resolve();
  assert.equal(stale.card.state, 'stale');
  assert.equal(stale.card.source_version, 2);
  assert.deepEqual(stale.card.actions, []);
  await c.query("update email_proposal set status='dismissed',resolved_by=$2,resolved_at=now(),resolution_reason='Synthetic dismissal' where id=$1", [proposal,who.id]);
  assert.equal((await resolve()).card.state, 'resolved');
  await c.query(`update activity set email_context='{"lifecycle":{"state":"deleted"}}' where id=$1`, [source]);
  assert.equal((await resolve()).card.state, 'retired');
  await c.query('delete from email_proposal where id=$1',[proposal]);
  await assert.rejects(resolve(),e=>e.payload.error==='action_link_not_found');
  const dell = {...(await c.query("select id,slug from actor where slug='dell'")).rows[0],human:true};
  await c.query(`insert into activity(id,occurred_at,actor_id,kind,summary,detail,email_source)
    values($1,now(),$2,'email_in','Synthetic source','Synthetic evidence',
    '{"mailbox_owner":"dell","sender_email":"sender@example.test"}')`,[id(510),dell.id]);
  await c.query(`insert into email_proposal(id,kind,activity_id,evidence_quote,owner,confidence,created_by,draft_subject,draft_body)
    values($1,'reply_draft',$2,'Synthetic evidence','dell',0.95,$3,'Synthetic subject','Synthetic body')`,[id(511),id(510),dell.id]);
  const dellRef = buildActionLink({kind:'reply_draft',id:id(511),owner:'dell',version:1}).ref;
  assert.equal((await resolve(dellRef,dell)).card.id,id(511));
  await assert.rejects(resolve(dellRef,who),e=>e.payload.error==='action_link_not_found');
  await c.query('update email_proposal set version=2 where id=$1',[id(511)]);
  assert.equal((await resolve(dellRef,dell)).card.state,'stale');
  await c.query("update email_proposal set status='dismissed',resolved_by=$2,resolved_at=now(),resolution_reason='Synthetic dismissal' where id=$1",[id(511),dell.id]);
  assert.equal((await resolve(dellRef,dell)).card.state,'resolved');
  await c.query(`update activity set email_context='{"lifecycle":{"state":"deleted"}}' where id=$1`,[id(510)]);
  assert.equal((await resolve(dellRef,dell)).card.state,'retired');
  await c.query('delete from email_proposal where id=$1',[id(511)]);
  await assert.rejects(resolve(dellRef,dell),e=>e.payload.error==='action_link_not_found');
});
