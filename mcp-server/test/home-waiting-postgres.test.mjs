import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { HOME_INBOX_WAITING_CONTRACT } from '../src/home-inbox.js';
import { TOOLS } from '../src/tools.js';

test('Home waiting has a registered audited command and versioned read', () => {
  assert.equal(TOOLS['set-home-waiting']?.write, true);
  assert.equal(TOOLS['clear-home-waiting']?.write, true);
  assert.ok(TOOLS['read-home-inbox-v4']);
  assert.deepEqual(JSON.parse(readFileSync(new URL('../contracts/home-inbox.v4.json',import.meta.url),'utf8')),HOME_INBOX_WAITING_CONTRACT);
});

import { snapshotDatabase } from './helpers/postgres-database.mjs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { executeRegisteredTool } from '../src/tools.js';

const dsn = await snapshotDatabase('home-waiting-postgres-');
test('waiting transitions preserve ownership, resolve identity and audit writes on installed PostgreSQL', async t => {
  const c = new pg.Client({ connectionString: dsn });
  await c.connect();
  t.after(() => c.end());
  const actor = { id: randomUUID(), slug: 'joe', human: true };
  const other = { id: randomUUID(), slug: 'dell', human: true };
  const party = randomUUID(), survivor = randomUUID(), task = randomUUID();
  actor.id=(await c.query("select id from actor where slug='joe'")).rows[0].id;
  other.id=(await c.query("select id from actor where slug='dell'")).rows[0].id;
  await c.query("update actor set display_name='Synthetic Dell' where id=$1",[other.id]);
  await c.query(`insert into party(id,kind,name,created_by,updated_by) values
    ($1,'person','Synthetic Waiting Person',$3,$3),($2,'person','Synthetic Survivor',$3,$3)`,[party,survivor,actor.id]);
  await c.query(`insert into next_action(id,owner_id,subject_type,subject_id,description,created_by,updated_by)
    values($1,$2,'client',$3,'Synthetic waiting task',$2,$2)`,[task,actor.id,party]);
  const context = async who => c.query("select set_config('carr.acting_actor_slug',$1,false),set_config('carr.verified_human_actor_slug',$1,false),set_config('carr.organization_tenant_id','carr-internal',false)",[who.slug]);
  const read = async (who=actor) => {
    await context(who);
    return executeRegisteredTool(c,who,'read-home-inbox-v4',{});
  };
  const item = async () => (await read()).items.find(i => i.source_id===task);
  const command = async (verb, current, extra={}, who=actor, key=randomUUID()) => {
    await context(who);
    await c.query('begin');
    try {
      const result = await executeRegisteredTool(c,who,verb,{
        idempotency_key:key,item_id:current.item_id,item_version:current.base_version,
        base_version:current.waiting_version,reason:'Synthetic response owed',evidence:['synthetic:call-1'],...extra,
      });
      await c.query('commit'); return result;
    } catch(e) { await c.query('rollback'); throw e; }
  };
  const set = (current, identity={kind:'party',id:party}, who=actor, key) => command('set-home-waiting',current,{target:identity},who,key);
  const refuses = (promise,error) => assert.rejects(promise,e => e.payload?.error===error);
  const first = await item();
  assert.equal(first.waiting_on,null); assert.equal(first.waiting_version,0);
  await t.test('v4 hydrates pending reply review references after the main merge',async()=>{
    const activity=randomUUID(), proposal=randomUUID();
    await c.query(`insert into activity(id,occurred_at,actor_id,kind,summary,detail,email_source)
      values($1,now(),$2,'email_in','Synthetic reply','Synthetic evidence',
      '{"mailbox_owner":"joe","sender_email":"sender@example.test"}')`,[activity,actor.id]);
    await c.query(`insert into email_proposal(id,kind,activity_id,evidence_quote,owner,confidence,created_by,draft_subject,draft_body)
      values($1,'reply_draft',$2,'Synthetic evidence','joe',0.95,$3,'Synthetic subject','Synthetic body')`,[proposal,activity,actor.id]);
    const reply=(await read()).items.find(i=>i.source_id===proposal);
    assert.equal(reply.review_reference.kind,'reply_draft');
    assert.equal(reply.waiting_on,null);
  });
  const key=randomUUID();
  const named = await set(first,undefined,actor,key);
  assert.equal(named.waiting_on.identity.display_name,'Synthetic Waiting Person');
  assert.equal(named.waiting_version,1);
  assert.deepEqual(await set(first,undefined,actor,key),{replayed:true,...named});
  await c.query('delete from tool_call where idempotency_key=$1',[key]);
  assert.deepEqual(await set(first,undefined,actor,key),named,'durable command replay survives envelope expiry');
  const waiting=await item();
  assert.equal(waiting.base_version,first.base_version,'waiting never changes disposition version');
  assert.equal(waiting.waiting_on.reason,'Synthetic response owed');
  assert.deepEqual(waiting.waiting_on.evidence,['synthetic:call-1']);
  await refuses(set(first),'version_conflict');
  await refuses(set(waiting,{kind:'party',id:randomUUID()}),'invalid_waiting_identity');
  await refuses(set(waiting,undefined,other),'inbox_item_not_found');
  await refuses(set(waiting,{kind:'actor',id:randomUUID()}),'invalid_waiting_identity');
  const replaced=await set(waiting,{kind:'actor',id:other.id});
  assert.equal(replaced.waiting_version,2);
  assert.equal((await item()).waiting_on.identity.display_name,'Synthetic Dell');
  const clearKey=randomUUID(), beforeClear=await item();
  const cleared=await command('clear-home-waiting',beforeClear,{},actor,clearKey);
  assert.deepEqual(await command('clear-home-waiting',beforeClear,{},actor,clearKey),{replayed:true,...cleared});
  await refuses(command('clear-home-waiting',beforeClear,{reason:'Different intended clear'},actor,clearKey),'key_reuse');
  assert.equal(cleared.waiting_on,null); assert.equal(cleared.waiting_version,3);
  await set(await item());
  await c.query('update party set merged_into=$1 where id=$2',[survivor,party]);
  assert.equal((await item()).waiting_on.identity.id,survivor,'stored reference follows survivor on read');
  await refuses(set(await item()),'invalid_waiting_identity');
  await c.query('update party set deleted_at=now() where id=$1',[survivor]);
  const unavailable=(await item()).waiting_on.identity;
  assert.equal(unavailable.available,false); assert.equal(unavailable.display_name,null);
  await refuses(set(await item(),{kind:'party',id:survivor}),'invalid_waiting_identity');
  const auditBefore=(await c.query('select count(*) n from ops.home_waiting_write')).rows[0].n;
  assert.deepEqual(await item(),await item());
  assert.equal((await c.query('select count(*) n from ops.home_waiting_write')).rows[0].n,auditBefore);
  assert.equal((await c.query('select owner_id from next_action where id=$1',[task])).rows[0].owner_id,actor.id);
  await c.query("update next_action set status='done',version=version+1,updated_at=now() where id=$1",[task]);
  const done=await item();
  assert.equal(done.waiting_on,null); assert.equal(done.waiting_version,5);
  await refuses(set(done,{kind:'actor',id:other.id}),'inbox_item_completed');
  await c.query("update next_action set status='open',version=version+1 where id=$1",[task]);
  assert.equal((await item()).waiting_on,null,'completion cannot resurrect the earlier wait');
  await t.test('two writers at one version accept exactly one change',async()=>{
    const writer=new pg.Client({connectionString:dsn}); await writer.connect();
    try {
      await writer.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
      const current=await item();
      const args={command:'set',item_id:current.item_id,item_version:current.base_version,
        base_version:current.waiting_version,reason:'Concurrent synthetic wait',evidence:['synthetic:concurrency'],target:{kind:'actor',id:other.id}};
      const results=await Promise.all([c,writer].map(client=>client.query(
        'select ops.change_home_waiting($1,$2,$3) result',['carr-internal',randomUUID(),args])));
      assert.equal(results.filter(r=>r.rows[0].result.ok).length,1);
      assert.equal(results.filter(r=>r.rows[0].result.error==='version_conflict').length,1);
    } finally {await writer.end();}
  });
  await t.test('completion racing a set leaves no wait and never resurrects it',async()=>{
    const editor=new pg.Client({connectionString:dsn}); await editor.connect();
    try {
      const current=await item();
      const results=await Promise.allSettled([
        set(current,{kind:'actor',id:other.id}),
        editor.query("update next_action set status='done',version=version+1,updated_at=now() where id=$1",[task]),
      ]);
      assert.equal(results[1].status,'fulfilled');
      if(results[0].status==='rejected') assert.ok(['version_conflict','inbox_item_completed'].includes(results[0].reason.payload?.error));
      assert.equal((await item()).waiting_on,null);
      await c.query("update next_action set status='open',version=version+1 where id=$1",[task]);
      assert.equal((await item()).waiting_on,null);
    } finally {await editor.end();}
  });
  await t.test('unknown and explicitly cleared items complete without naming someone',async()=>{
    for(const initial of ['unknown','cleared']) {
      const taskId=randomUUID();
      await c.query(`insert into next_action(id,owner_id,subject_type,subject_id,description,created_by)
        values($1,$2,'client',$3,$4,$2)`,[taskId,actor.id,randomUUID(),`Synthetic ${initial} completion`]);
      const find=async()=> (await read()).items.find(i=>i.source_id===taskId);
      if(initial==='cleared') {await set(await find(),{kind:'actor',id:other.id}); await command('clear-home-waiting',await find());}
      const before=await find();
      await c.query("update next_action set status='done',version=version+1,updated_at=now() where id=$1",[taskId]);
      const after=await find();
      assert.equal(after.waiting_on,null);
      assert.equal(after.waiting_version,before.waiting_version);
      await refuses(set(after,{kind:'actor',id:other.id}),'inbox_item_completed');
    }
  });
  await t.test('call processing and grouped notification completion clear a wait',async()=>{
    const session=randomUUID();
    await c.query(`insert into capture_session(id,nonce,device_id,actor_id,mode,started_at,consent_announced_at,
      session_token_hash,expires_at,state,state_at) values($1::uuid,$1::text,'synthetic',$2,'meeting',now(),now(),'synthetic',now()+interval '1 hour','distilling',now())`,[session,actor.id]);
    const find=async()=> (await read()).items.find(i=>i.source_id===session);
    await set(await find(),{kind:'actor',id:other.id});
    await c.query("update capture_session set state='done',state_at=now() where id=$1",[session]);
    assert.equal((await find()).waiting_on,null);
    const subject=randomUUID();
    await c.query(`insert into ops.notification(recipient_actor,subject_type,subject_ref,event_ref,event_source,deep_link,dedupe_key,severity,reason)
      values($1,'email',$2,gen_random_uuid(),'event','/home',gen_random_uuid()::text,'action_required','Synthetic response requested')`,[actor.id,subject]);
    const notification=async()=> (await read()).items.find(i=>i.source_id===JSON.stringify(['email',subject]).replaceAll(',',', '));
    await set(await notification(),{kind:'actor',id:other.id});
    await c.query(`insert into ops.notification(recipient_actor,subject_type,subject_ref,event_ref,event_source,deep_link,dedupe_key,severity,reason)
      values($1,'email',$2,gen_random_uuid(),'event','/home',gen_random_uuid()::text,'completion','Synthetic response completed')`,[actor.id,subject]);
    assert.equal((await notification()).waiting_on,null);
  });
  await t.test('fact review resolution clears waiting and new evidence cannot resurrect it',async()=>{
    const client=randomUUID(),deal=randomUUID();
    await c.query(`insert into client(id,party_id,created_by,updated_by) values($1,$2,$3,$3)`,[client,party,actor.id]);
    await c.query(`insert into deal(id,client_id,name,deal_type,phase,owner,created_by,updated_by)
      select $1,$2,'Synthetic waiting review',
        (select slug from deal_type_ref order by slug limit 1),
        (select slug from deal_phase order by slug limit 1),'joe',$3,$3`,[deal,client,actor.id]);
    const record=async(label)=>{
      const result=(await c.query('select ops.record_fact_observations($1,$2,$3,$4,$5) result',
        ['carr-internal',actor.id,randomUUID(),deal,JSON.stringify([{id:label,fact_key:'base_rent_per_sf',value:'24',unit:'USD/SF/year',expense_basis:'nnn',
          source:{document_ref:'synthetic-'+label,page_start:1,page_end:1,cited_text:'Synthetic rent clause.',content_hash:'a'.repeat(64),document_family_id:label},
          method:'text_layer',extractor_version:'waiting-test.v1',confidence_tier:'human_review',extraction:{pass:label,model:'synthetic'}}])])).rows[0].result;
      assert.equal(result.ok,true);
    };
    await record('first');
    const find=async()=> (await read()).items.find(i=>i.source_type==='fact_review' && i.deal_id===deal);
    await set(await find(),{kind:'actor',id:other.id});
    const initialField=(await c.query("select ops.fact_review_field('carr-internal',$1,'base_rent_per_sf') result",[deal])).rows[0].result;
    const promoted=(await c.query("select ops.promote_fact('carr-internal',$1,$2,$3,'base_rent_per_sf','human_review',$4,$5) result",
      [actor.id,randomUUID(),deal,[initialField.observations[0].id],initialField.canonical_version])).rows[0].result;
    assert.equal(promoted.ok,true);
    assert.equal((await find()).waiting_on,null,'canonical human promotion also resolves a review');
    assert.equal((await find()).waiting_version,2);
    await record('second');
    assert.equal((await find()).waiting_on,null);
    await set(await find(),{kind:'actor',id:other.id});
    const field=(await c.query("select ops.fact_review_field('carr-internal',$1,'base_rent_per_sf') result",[deal])).rows[0].result;
    const resolved=(await c.query("select ops.resolve_fact_review('carr-internal',$1,$2) result",[actor.id,{
      idempotency_key:randomUUID(),entity_ref:deal,fact_key:'base_rent_per_sf',decision:'reject',
      base_version:field.canonical_version,review_version:field.review_version,reason:'Synthetic waiting review resolution',
    }])).rows[0].result;
    assert.equal(resolved.ok,true);
    assert.equal((await find()).waiting_on,null);
    assert.equal((await find()).waiting_version,4);
    await record('third');
    assert.equal((await find()).source_state,'needs_attention');
    assert.equal((await find()).waiting_on,null);
  });
  await c.query('update actor set active=false where id=$1',[other.id]);
  await refuses(set(await item(),{kind:'actor',id:other.id}),'invalid_waiting_identity');
  await c.query('update actor set active=true where id=$1',[other.id]);
  const staleItem=await item();
  await c.query("update next_action set description='Synthetic changed instruction',version=version+1 where id=$1",[task]);
  await refuses(set(staleItem,{kind:'actor',id:other.id}),'version_conflict');
  await refuses(command('set-home-waiting',await item(),{target:{kind:'actor',id:other.id},reason:'   '}),'invalid_waiting_command');
  assert.ok((await executeRegisteredTool(c,actor,'read-home-inbox-v3',{})).items.every(i=>!Object.hasOwn(i,'waiting_on')));
  await context(actor);
  await c.query("select set_config('carr.organization_tenant_id','foreign',false)");
  await assert.rejects(c.query('select ops.read_home_inbox_v4()'),e=>e.code==='42501');
  await assert.rejects(c.query('select ops.change_home_waiting($1,$2,$3)', ['foreign',randomUUID(),{}]),e=>e.code==='42501');
  await context(actor);
  await c.query('set role carr_reader');
  await assert.rejects(c.query('select * from ops.home_waiting'),e=>e.code==='42501');
  await assert.rejects(c.query('select ops.read_home_inbox_v4()'),e=>e.code==='42501');
  await c.query('reset role');
});
