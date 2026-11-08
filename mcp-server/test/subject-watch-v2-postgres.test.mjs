import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { writeFileSync } from 'node:fs';
import { snapshotDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';

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
      await connection.query('set local role carr_writer');
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
    const deadline=Date.now()+5000;
    while(Date.now()<deadline && !blocked) {
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
