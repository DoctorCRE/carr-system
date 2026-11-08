import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { readMigration, migrationNames } from './migration-source.testhelper.mjs';
import { snapshotDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';

const dsn = await snapshotDatabase('subject-watches-');
test('v2 registered lifecycle, policy, replay, generations and isolation', async t => {
  const c = new pg.Client({ connectionString: dsn });
  await c.connect(); t.after(() => c.end());
  const joe = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  const dell = { ...(await c.query("select id,slug from actor where slug='dell'")).rows[0], human: true };
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic watch client',$1,$1) returning id",[joe.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('active','Synthetic active') on conflict do nothing");
  const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id",[party,joe.id])).rows[0].id;
  const deal = (await c.query("insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic watch deal','purchase','research','joe',$2,$2) returning id",[client,joe.id])).rows[0].id;
  const lease = (await c.query("insert into lease(deal_id,client_id,created_by,updated_by) values($1,$2,$3,$3) returning id",[deal,client,joe.id])).rows[0].id;
  const call = async (verb,args={},who=joe,connection=c) => {
    await connection.query('begin');
    try {
      await connection.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true),set_config('carr.organization_tenant_id','carr-internal',true)",[who.slug]);
      await connection.query(TOOLS[verb]?.authorityOnly ? `set local session authorization carr_authority_${who.slug}` : 'set local role carr_writer');
      const result = await executeRegisteredTool(connection,who,verb,args);
      await connection.query('commit'); return result;
    } catch(e) { await connection.query('rollback'); throw e; }
  };
  const target={subject_type:'deal',subject_id:deal};
  const policy={change_kinds:['reply_arrived','lease_threshold_crossed'],lease_threshold_days:[30,90]};
  const command=async(verb,args={},who=joe,connection=c)=>{
    const payload={...target,...args,idempotency_key:randomUUID()};
    const first=await call(verb,payload,who,connection);
    const replay=await call(verb,payload,who,connection);
    assert.equal(replay.replayed,true,verb+' replay');
    assert.deepEqual(replay.watch,first.watch);
    return first;
  };
  const read=(who=joe)=>call('read-subject-watch-v2',target,who);
  const notifications=(await c.query('select count(*) from ops.notification')).rows[0].count;
  const first=await command('watch-subject-v2',{policy});
  const {parseSubjectWatchV2}=await import('../src/subject-watches.js');
  parseSubjectWatchV2(first);
  if (process.env.J82_CAPTURE_FIXTURE) writeFileSync(process.env.J82_CAPTURE_FIXTURE,JSON.stringify(first,null,2)+'\n');
  assert.equal(first.watch.actor_id,joe.id);
  assert.equal(first.watch.policy.include_self_changes,true);
  assert.equal(first.watch.baseline.version,1);
  const expected=w=>({subscription_id:w.id,base_version:w.lifecycle_version});
  const mutate=(verb,w,args={})=>command(verb,{...expected(w),...args});
  await c.query('update deal set version=version+1 where id=$1',[deal]);
  assert.deepEqual((await command('watch-subject-v2',{policy})).watch,first.watch,'Repeat watch');
  assert.equal((await read(dell)).watch,null);
  const paused=await mutate('pause-subject-watch-v2',first.watch);
  assert.equal(paused.watch.state,'paused','Pause');
  assert.deepEqual((await command('watch-subject-v2',{policy})).watch,paused.watch,'Repeated watch does not resume');
  const replacement={change_kinds:['listing_status_changed'],include_self_changes:false};
  const changed=await mutate('replace-subject-watch-policy-v2',paused.watch,{policy:replacement});
  assert.deepEqual(changed.watch.policy,replacement,'Replace paused policy');
  assert.equal(changed.watch.state,'paused');
  const resumed=await mutate('resume-subject-watch-v2',changed.watch);
  assert.equal(resumed.watch.state,'watching','Resume');
  assert.deepEqual(resumed.watch.baseline,first.watch.baseline);
  const replaced=await mutate('replace-subject-watch-policy-v2',resumed.watch,{policy});
  assert.equal(replaced.watch.lifecycle_version,5,'Replace policy');
  assert.equal(replaced.watch.state,'watching');
  assert.deepEqual(replaced.watch.policy,{include_self_changes:true,...policy});
  await assert.rejects(mutate('unwatch-subject-v2',first.watch),e=>e.payload?.error==='watch_stale_version');
  const key=randomUUID();
  const removeArgs={...target,...expected(replaced.watch),idempotency_key:key};
  const removed=await call('unwatch-subject-v2',removeArgs);
  assert.equal(removed.watch.state,'removed','Remove replaced');
  assert.equal((await read()).watch,null);
  assert.equal((await call('unwatch-subject-v2',removeArgs)).replayed,true);
  await assert.rejects(call('unwatch-subject-v2',removeArgs,dell),e=>e.payload?.error==='key_reuse');
  const rewatched=await command('watch-subject-v2',{policy});
  assert.notEqual(rewatched.watch.id,first.watch.id,'Rewatch');
  assert.equal(rewatched.watch.baseline.version,2);
  let current=rewatched.watch;
  for (const [transition,verb,args] of [
    ['Pause rewatched','pause-subject-watch-v2',{}],
    ['Remove paused','unwatch-subject-v2',{}],
    ['Rewatch paused removal','watch-subject-v2',{policy}],
    ['Replace rewatched policy','replace-subject-watch-policy-v2',{policy:replacement}],
    ['Pause replaced','pause-subject-watch-v2',{}],
    ['Resume replaced pause','resume-subject-watch-v2',{}],
    ['Remove watching','unwatch-subject-v2',{}],
    ['Rewatch watching removal','watch-subject-v2',{policy}],
    ['Remove rewatched','unwatch-subject-v2',{}],
    ['Final rewatch','watch-subject-v2',{policy}],
  ]) await t.test(transition,async()=>{
    const previous=current;
    const response=await command(verb,{...(verb==='watch-subject-v2'?{}:expected(current)),...args});
    current=response.watch;parseSubjectWatchV2(response);
    const state=verb==='pause-subject-watch-v2'?'paused':verb==='unwatch-subject-v2'?'removed':verb==='replace-subject-watch-policy-v2'?previous.state:'watching';
    assert.equal(current.state,state,transition);
    if(verb==='watch-subject-v2') {
      assert.notEqual(current.id,previous.id);
      assert.equal(current.lifecycle_version,1);
      assert.equal(current.baseline.version,2);
      assert.deepEqual(current.policy,{include_self_changes:true,...args.policy});
    } else {
      assert.equal(current.id,previous.id);
      assert.equal(current.lifecycle_version,previous.lifecycle_version+1);
      assert.deepEqual(current.baseline,previous.baseline);
      assert.deepEqual(current.policy,verb==='replace-subject-watch-policy-v2'?{include_self_changes:true,...args.policy}:previous.policy);
    }
  });
  rewatched.watch=current;
  const other=new pg.Client({connectionString:dsn});await other.connect();
  let settled;
  try {
    const pid=(await other.query('select pg_backend_pid() as pid')).rows[0].pid;
    await c.query('begin');
    await c.query("select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true),set_config('carr.organization_tenant_id','carr-internal',true)");
    await c.query('set local role carr_writer');
    const old={...target,...expected(rewatched.watch),idempotency_key:randomUUID()};
    await executeRegisteredTool(c,joe,'unwatch-subject-v2',old);
    settled=call('unwatch-subject-v2',{...old,idempotency_key:randomUUID()},joe,other).then(value=>({value}),error=>({error}));
    const fresh=await executeRegisteredTool(c,joe,'watch-subject-v2',{...target,policy,idempotency_key:randomUUID()});
    let blocked=false;
    const deadline=performance.now()+5000;
    while(performance.now()<deadline && !blocked) {
      blocked=(await c.query("select exists(select 1 from pg_locks where pid=$1 and locktype='advisory' and not granted) as blocked",[pid])).rows[0].blocked;
      if(!blocked)await new Promise(resolve=>setTimeout(resolve,10));
    }
    assert.equal(blocked,true,'stale removal waits behind replacement transaction');
    await c.query('commit');
    assert.equal((await settled).error?.payload?.error,'watch_stale_version');
    assert.notEqual(fresh.watch.id,rewatched.watch.id);
    rewatched.watch=fresh.watch;
  } finally {
    await c.query('rollback');
    await settled;
    await other.end();
  }
  assert.equal((await call('unwatch-subject-v2',removeArgs)).watch.id,first.watch.id,'Replay cannot remove rewatch');
  assert.equal((await read()).watch.id,rewatched.watch.id);
  await assert.rejects(call('pause-subject-watch-v2',{...target,...expected(rewatched.watch),actor_id:dell.id,idempotency_key:randomUUID()}));
  await assert.rejects(command('pause-subject-watch-v2',expected(rewatched.watch),dell),e=>e.payload?.error==='watch_stale_version');
  for(const verb of ['resume-subject-watch-v2','replace-subject-watch-policy-v2','unwatch-subject-v2'])
    await assert.rejects(command(verb,{...expected(rewatched.watch),...(verb==='replace-subject-watch-policy-v2'?{policy}:{})},dell),e=>e.payload?.error==='watch_stale_version');
  assert.deepEqual((await call('list-subject-watches-v2',{},dell)).watches,[]);
  const dellWatch=await command('watch-subject-v2',{policy},dell);
  assert.equal(dellWatch.watch.actor_id,dell.id);
  assert.notEqual(dellWatch.watch.id,rewatched.watch.id);
  assert.equal((await read()).watch.id,rewatched.watch.id);
  const leaseWatch=await command('watch-subject-v2',{subject_type:'lease',subject_id:lease,policy});
  assert.equal(leaseWatch.watch.subject_type,'lease');
  const page=await call('list-subject-watches-v2',{limit:1});parseSubjectWatchV2(page);
  assert.equal(page.watches.length,1);assert.equal(page.has_more,true);
  assert.equal((await call('list-subject-watches-v2',{limit:1,cursor:page.next_cursor})).has_more,false);
  await assert.rejects(call('list-subject-watches-v2',{limit:101}));
  await assert.rejects(command('watch-subject-v2',{subject_type:'listing',subject_id:randomUUID(),policy}),e=>e.payload?.error==='watch_target_not_found');
  assert.equal((await c.query('select count(*) from ops.notification')).rows[0].count,notifications,'Commands stay quiet');
  assert.equal((await call('read-subject-watch-v1',target)).watch,null,'v1 unaffected');
  await c.query('update party set deleted_at=now() where id=$1',[party]);
  await assert.rejects(read(),e=>e.payload?.error==='watch_target_not_found');
  assert.equal((await call('list-subject-watches-v2',{})).watches.length,0);
  assert.equal((await mutate('unwatch-subject-v2',rewatched.watch)).watch.state,'removed','Removal after access loss');
});

test('meaningful canonical changes commit once with source writes and isolate mailbox evidence', async t=>{
  const c=new pg.Client({connectionString:dsn});await c.connect();t.after(()=>c.end());
  await c.query("do $$ begin if not exists(select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if; grant carr_authority to carr_authority_joe; end $$");
  const joe={...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
  const dell={...(await c.query("select id,slug from actor where slug='dell'")).rows[0],human:true};
  const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic receipt client',$1,$1) returning id",[joe.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('active','Synthetic active') on conflict do nothing");
  const client=(await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id",[party,joe.id])).rows[0].id;
  const deal=(await c.query("insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic receipt deal','purchase','research','joe',$2,$2) returning id",[client,joe.id])).rows[0].id;
  await c.query("update deal set owner='dell' where id=$1",[deal]);
  await c.query("insert into deal_participant(deal_id,actor_id,role,set_by) values($1,$2,'lead',$2)",[deal,joe.id]);
  const target={subject_type:'deal',subject_id:deal};
  const call=async(verb,args,who=joe,connection=c)=>{
    await connection.query('begin');
    try {
      await connection.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true),set_config('carr.organization_tenant_id','carr-internal',true)",[who.slug]);
      await connection.query(TOOLS[verb]?.authorityOnly ? `set local session authorization carr_authority_${who.slug}` : 'set local role carr_writer');
      const result=await executeRegisteredTool(connection,who,verb,args);await connection.query('commit');return result;
    } catch(e){await connection.query('rollback');throw e;}
  };
  const policy={change_kinds:['reply_arrived']};
  const watch=async(who=joe,other=target)=> (await call('watch-subject-v2',{...other,policy,idempotency_key:randomUUID()},who)).watch;
  const j=await watch();const d=await watch(dell);
  const changes=(w=j,who=joe,other=target)=>call('read-subject-watch-changes-v1',{...other,subscription_id:w.id},who);
  const version=async()=>Number((await c.query('select version from deal where id=$1',[deal])).rows[0].version);
  const update=async(fields,key=randomUUID())=>{
    const args={deal,fields,base_version:await version(),idempotency_key:key};
    const first=await call('update-deal',args);assert.equal((await call('update-deal',args)).replayed,true);return first;
  };
  assert.deepEqual((await changes()).changes,[]);
  await call('set-lead',{deal,new_lead:'joe',base_version:await version(),idempotency_key:randomUUID()});
  assert.equal((await changes()).changes.length,0,'repairing a stale legacy owner does not change the canonical lead');
  await update({notes_path:' Synthetic note '});await update({notes_path:'Synthetic note'});
  await update({outcome:null});
  assert.equal((await changes()).changes.length,0,'bookkeeping and equivalent selected values');
  await update({closed_on:'2027-01-01'});
  let receipts=(await changes()).changes;
  assert.equal(receipts.length,1);assert.equal(receipts[0].disposition,'pending');
  assert.deepEqual(receipts[0].changes,[{field:'closed_on',before:null,after:'2027-01-01',reason_code:'unknown_to_known'}]);
  assert.equal((await changes(d,dell)).changes.length,1);
  await update({closed_on:'2027-01-01'});assert.equal((await changes()).changes.length,1);
  await update({closed_on:null});
  assert.equal((await changes()).changes.find(x=>x.changes[0]?.reason_code==='known_to_unknown')?.changes[0].field,'closed_on');
  await c.query('begin');
  await c.query("select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true)");
  await executeRegisteredTool(c,joe,'update-deal',{deal,fields:{closed_on:'2027-03-01'},base_version:await version(),idempotency_key:randomUUID()});
  assert.equal((await executeRegisteredTool(c,joe,'read-subject-watch-changes-v1',{...target,subscription_id:j.id})).changes.length,3);
  await c.query('rollback');assert.equal((await changes()).changes.length,2,'source rollback also rolls back receipt and baseline');
  await assert.rejects(changes(j,dell),e=>e.payload?.error==='watch_target_not_found');
  const event=async(verb,value,scope='none')=>(await c.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause,personal_scope) values(now(),$1,$2,'deal',$3,$4,'automation_job',$5) returning id",[joe.id,verb,deal,value,scope])).rows[0].id;
  const mailContext={subject:'Synthetic watched reply',conversation_id:randomUUID(),
    sender:{email:'synthetic@example.test',internal:false},recipients:[{email:'broker@example.test',internal:true,kind:'to'}]};
  const inbound=async(dealId,minutes)=>(await c.query("insert into activity(kind,occurred_at,client_id,deal_id,actor_id,summary,email_source,email_context) values('email_in',now()-$4*interval '1 minute',$1,$2,$3,'Synthetic receipt reply','{\"mailbox_owner\":\"joe\",\"sender_email\":\"synthetic@example.test\"}',$5) returning id",[client,dealId,joe.id,minutes,mailContext])).rows[0].id;
  const priorMail=await inbound(deal,1),mail=await inbound(null,0);
  const filing={activity_id:mail,thread_activity_id:priorMail,idempotency_key:randomUUID()};
  await call('inherit-email-thread',filing);
  assert.equal((await call('inherit-email-thread',filing)).replayed,true,'registered filing replay captures zero');
  const e=(await c.query("select id from event where subject_id=$1 and verb='inherit-email-thread' and new_value->>'activity_id'=$2",[deal,mail])).rows[0].id;
  await event('confirm-email-filing',{activity_id:mail},'joe-personal');
  await c.query('select ops.capture_subject_watch_changes($1)',[e]);
  assert.equal((await changes()).changes.filter(x=>x.category==='reply_arrived').length,1);
  assert.equal((await changes(d,dell)).changes.length,2,'other mailbox sees no receipt or evidence');
  await event('late-evidence',{closed_on:'2026-01-01'});
  assert.equal((await changes()).changes.length,3,'late unselected evidence stays quiet');
  const current=(await call('record-executed-lease',{deal,executed_on:'2026-01-01',expiration_on:'2028-01-01',evidence_kind:'executed_lease',evidence_ref:'synthetic:first',source:'synthetic',idempotency_key:randomUUID()}));
  const lt={subject_type:'lease',subject_id:current.lease_id};const lw=await watch(joe,lt);
  await call('record-executed-lease',{deal,base_version:current.version,executed_on:'2026-01-01',expiration_on:'2029-01-01',evidence_kind:'lease_amendment',evidence_ref:'synthetic:replacement',source:'synthetic',idempotency_key:randomUUID()});
  const leaseReceipts=(await changes(lw,joe,lt)).changes;
  assert.equal(leaseReceipts.length,1,'verified lease replacement captured once');
  assert.ok(leaseReceipts[0].changes.some(x=>x.field==='replacement'));
  const beforeDates=(await changes()).changes.length;
  for(let n=0;n<2;n++) await call('add-critical-date',{deal,kind:'option_window',due_on:'2027-10-01',source:'synthetic',idempotency_key:randomUUID()});
  assert.equal((await changes()).changes.length,beforeDates+1,'equivalent critical dates do not duplicate receipts');
  await t.test('pending receipt proof CLI passes, fails assertions and refuses insufficient releases',async()=>{
    const dir=mkdtempSync(join(tmpdir(),'watch-proof-'));
    const python=process.env.CARR_CI_PYTHON || 'python3';
    const gitEnv={...process.env};
    for(const key of ['GIT_SHALLOW_FILE','GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE']) delete gitEnv[key];
    const git=args=>{
      const result=spawnSync('git',['-C',dir,...args],{encoding:'utf8',env:gitEnv});
      assert.equal(result.status,0,result.stderr);return result.stdout.trim();
    };
    git(['init','--quiet']);
    const tree=git(['mktree']);
    const commit=parents=>git(['-c','user.name=Synthetic','-c','user.email=synthetic@example.test','commit-tree',tree,...parents,'-m',randomUUID()]);
    const parent=commit([]),revision=commit(['-p',parent]),unrelated=commit([]);
    const cli=args=>spawnSync(python,[fileURLToPath(new URL('../../ops/verify-subject-watch-proof.py',import.meta.url)),...args],{
      encoding:'utf8',env:{...gitEnv,GIT_DIR:join(dir,'.git'),GIT_WORK_TREE:dir}});
    const capture=name=>{
      const file=join(dir,name+'.json');
      const result=cli(['capture','--database',dsn,'--watched',deal,'--subscription',j.id,'--actor','joe','--output',file]);
      assert.equal(result.status,0,result.stdout+result.stderr);return file;
    };
    try {
      const before=capture('before');
      const meaningful={deal,fields:{closed_on:'2027-08-01'},base_version:await version(),idempotency_key:randomUUID()};
      await call('update-deal',meaningful);
      const after=capture('after');
      const immaterial={deal,fields:{notes_path:'Synthetic proof note'},base_version:await version(),idempotency_key:randomUUID()};
      await call('update-deal',immaterial);
      const quiet=capture('immaterial');
      assert.equal((await call('update-deal',meaningful)).replayed,true);
      assert.equal((await call('update-deal',immaterial)).replayed,true);
      const replay=capture('replay');
      const release=join(dir,'release.json');
      const setRelease=sha=>writeFileSync(release,JSON.stringify({ok:true,env:{value:'production'},git_sha:{value:sha}}));
      const args=['verify','--before',before,'--after',after,'--immaterial',quiet,'--replay',replay,'--merge-sha',parent,'--release-fixture',release];
      setRelease(revision);
      let result=cli(args);assert.equal(result.status,0,result.stdout+result.stderr);
      result=cli(args.map(x=>x===parent?revision:x));assert.equal(result.status,0,result.stdout+result.stderr);
      result=cli(args.map(x=>x===after?before:x));assert.equal(result.status,1,result.stdout+result.stderr);
      setRelease(parent);
      result=cli(args.map(x=>x===parent?revision:x));assert.equal(result.status,78,result.stdout+result.stderr);
      for(const unknown of ['0'.repeat(40),null]) {
        setRelease(unknown);result=cli(args);assert.equal(result.status,78,result.stdout+result.stderr);
      }
      setRelease(unrelated);result=cli(args);assert.equal(result.status,78,result.stdout+result.stderr);
    } finally {rmSync(dir,{recursive:true,force:true});}
  });
  const beforeOwner=(await changes()).changes.length;
  await call('set-lead',{deal,new_lead:'dell',base_version:await version(),idempotency_key:randomUUID()});
  assert.equal((await changes()).changes.length,beforeOwner+1,'actual lead owner handoff');
  const beforeAction=(await changes()).changes.length;
  await call('set-next-action',{ref:deal,description:'Synthetic next move',due_on:'2027-01-01',owner:'joe',idempotency_key:randomUUID()});
  assert.ok((await changes()).changes.length>beforeAction,'next action owner and due date changes');
  const beforeEquivalentAction=(await changes()).changes.length;
  await call('set-next-action',{ref:deal,description:' Synthetic next move ',due_on:'2027-01-01',owner:'joe',idempotency_key:randomUUID()});
  assert.equal((await changes()).changes.length,beforeEquivalentAction,'equivalent action owner and due date');
  await call('set-next-action',{ref:deal,description:'Dell next move',due_on:'2027-09-01',owner:'dell',idempotency_key:randomUUID()},dell);
  const beforePartnerDue=(await changes()).changes.length;
  const partnerDue={ref:deal,description:'Synthetic next move',due_on:'2027-09-01',owner:'joe',idempotency_key:randomUUID()};
  await call('set-next-action',partnerDue);
  assert.equal((await changes()).changes.length,beforePartnerDue+1,'partner due-date change survives an unchanged shared next_date');
  assert.equal((await call('set-next-action',partnerDue)).replayed,true);
  assert.equal((await changes()).changes.length,beforePartnerDue+1,'exact action replay stays quiet');
  await call('set-next-action',{...partnerDue,idempotency_key:randomUUID()});
  assert.equal((await changes()).changes.length,beforePartnerDue+1,'equivalent per-owner action replacement stays quiet');
  const swapPrior=(await changes()).changes;
  const beforeSwap=swapPrior.length;
  await call('set-next-action',{ref:deal,description:'Dell swapped date',due_on:'2027-01-01',owner:'dell',idempotency_key:randomUUID()},dell);
  receipts=(await changes()).changes;
  assert.equal(receipts.length,beforeSwap+1,'date swap second operation adds exactly one receipt');
  const combined=receipts.find(x=>!swapPrior.some(prior=>prior.id===x.id));
  assert.deepEqual(combined.changes.map(x=>x.field),['next_actions','next_date'],
    'one action operation carries both next_actions and next_date');
  const peer=new pg.Client({connectionString:dsn});await peer.connect();
  try {
    const beforeConcurrent=(await changes()).changes.length;
    const base=await version();
    await Promise.all([
      call('update-deal',{deal,fields:{closed_on:'2027-04-01'},base_version:base,idempotency_key:randomUUID()},joe),
      call('update-deal',{deal,fields:{outcome:'lost'},base_version:base,idempotency_key:randomUUID()},dell,peer),
    ]);
    assert.equal((await changes()).changes.length,beforeConcurrent+2,'two partner writers commit distinct transitions without losing either');
    const paused=await call('pause-subject-watch-v2',{...target,subscription_id:j.id,base_version:j.lifecycle_version,idempotency_key:randomUUID()});
    const countPaused=(await changes()).changes.length;
    await update({closed_on:'2027-05-01'});
    assert.equal((await changes()).changes.length,countPaused,'paused watches capture nothing');
    await call('resume-subject-watch-v2',{...target,subscription_id:j.id,base_version:paused.watch.lifecycle_version,idempotency_key:randomUUID()});
    await event('late-evidence',{});
    assert.equal((await changes()).changes.length,countPaused,'resume quietly resets canonical baseline');
    await update({closed_on:'2027-06-01'});
    assert.equal((await changes()).changes.length,countPaused+1,'new meaningful transition after resume');
  } finally {await peer.end();}
  await call('set-next-action',{ref:deal,description:'Overdue Joe action',due_on:'2020-01-01',owner:'joe',idempotency_key:randomUUID()});
  const multiPrior=(await changes()).changes;
  const beforeMulti=multiPrior.length;
  await update({closed_on:'2027-08-02',outcome:'won'});
  receipts=(await changes()).changes;
  assert.equal(receipts.length,beforeMulti+1,'multi-field update and overdue action supersession are one operation');
  assert.ok(receipts.filter(x=>!multiPrior.some(prior=>prior.id===x.id)).some(x=>['closed_on','outcome','next_actions'].every(field=>x.changes.some(v=>v.field===field))),
    'one update receipt carries scalar edits and the action supersession');
  await c.query("update activity set email_context='{\"lifecycle\":{\"state\":\"deleted\"}}' where id=$1",[mail]);
  assert.equal((await changes()).changes.filter(x=>x.category==='reply_arrived').length,0,'retired mailbox evidence is hidden at read time');
  await c.query("update activity set email_context=null where id=$1",[mail]);
  const consumer=(await c.query("select pg_get_functiondef('ops.consume_subject_watch_event(uuid)'::regprocedure) body")).rows[0].body;
  assert.ok(!consumer.includes('deal-version:'),'the deployed consumer has no broad version wake');
  await c.query('begin');
  try {
    await c.query("select set_config('carr.acting_actor_slug','joe-local',true),set_config('carr.verified_human_actor_slug','joe',true)");
    await c.query('set local role carr_writer');
    await assert.rejects(c.query('select ops.read_subject_watch_changes($1,$2,$3,null,50)',['deal',deal,j.id]),e=>e.code==='42501');
  } finally {await c.query('rollback');}
  const response=await call('read-subject-watch-changes-v1',{...target,subscription_id:j.id,limit:1});
  assert.equal(response.changes.length,1);assert.equal(response.has_more,true);
  assert.ok((await call('read-subject-watch-changes-v1',{...target,subscription_id:j.id,cursor:response.next_cursor})).changes.every(x=>x.id>response.next_cursor));
  const {parseSubjectWatchChanges}=await import('../contracts/subject-watch-change.v1.js');
  parseSubjectWatchChanges(response);
  if(process.env.J118_CAPTURE_FIXTURE) writeFileSync(process.env.J118_CAPTURE_FIXTURE,JSON.stringify(response,null,2)+'\n');
});

test('migration initializes live watches and skips dangling targets',async t=>{
  const c=new pg.Client({connectionString:dsn});await c.connect();t.after(()=>c.end());
  await c.query('begin');
  try {
    const joe={...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
    const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic upgrade client',$1,$1) returning id",[joe.id])).rows[0].id;
    await c.query("insert into client_status(slug,label) values('active','Synthetic active') on conflict do nothing");
    const client=(await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id",[party,joe.id])).rows[0].id;
    const create=async()=> (await c.query("insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic migration deal','purchase','research','joe',$2,$2) returning id",[client,joe.id])).rows[0].id;
    const live=await create(),dangling=await create();
    await c.query("select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true)");
    const watch=async id=>{
      await c.query('set local role carr_writer');
      const response=await executeRegisteredTool(c,joe,'watch-subject-v1',{subject_type:'deal',subject_id:id,idempotency_key:randomUUID()});
      await c.query('reset role');return response.watch;
    };
    const current=await watch(live),stale=await watch(dangling);
    await c.query('delete from deal where id=$1',[dangling]);
    await c.query('delete from ops.subject_watch_material_baseline');
    const migration=readMigration(migrationNames().find(name=>name.endsWith('_subject_watch_changes.sql')));
    const start=migration.indexOf('insert into ops.subject_watch_material_baseline\nselect id');
    const end=migration.indexOf('\n\ncreate function ops.capture_subject_watch_changes',start);
    assert.ok(start>0 && end>start);
    await c.query(migration.slice(start,end));
    assert.equal((await c.query('select count(*)::integer n from ops.subject_watch_material_baseline where watch_id=$1',[current.id])).rows[0].n,1);
    assert.equal((await c.query('select count(*)::integer n from ops.subject_watch_material_baseline where watch_id=$1',[stale.id])).rows[0].n,0);
  } finally {await c.query('rollback');}
});
