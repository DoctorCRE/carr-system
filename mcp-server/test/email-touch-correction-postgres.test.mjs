import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { vendorRelationshipJoin } from '../src/vendor-relationship.ts';

test('guarded email touch correction through registered transactions',async t=>{
  const fixture=await acquireDisposablePostgres({prefix:'touch-correction-',snapshot:true});
  const c=new pg.Client(fixture.connection); await c.connect();
  t.after(async()=>{await c.end();await fixture.close();});
  const actor={...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
  await c.query("update actor set email='fixture@example.test' where id=$1",[actor.id]);
  await c.query("insert into client_status(slug,label) values('active','Fixture') on conflict do nothing");
  const run=async(client,verb,args)=>{
    await client.query('begin');
    try {await client.query('set local role carr_writer');const r=await executeRegisteredTool(client,actor,verb,args);await client.query('commit');return r;}
    catch(e){await client.query('rollback');throw e;}
  };
  const call=(verb,args)=>run(c,verb,args);
  const setup=async()=>{
    const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic',$1,$1) returning id",[actor.id])).rows[0].id;
    const target=(await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id",[party,actor.id])).rows[0].id;
    await call('log-activity',{idempotency_key:randomUUID(),ref:target,kind:'call',summary:'Synthetic',occurred_at:'2026-01-01T12:00:00Z'});
    const input={idempotency_key:randomUUID(),ref:target,kind:'email_in',summary:'Synthetic',occurred_at:'2026-01-03T12:00:00Z',email_touch:{source_id:randomUUID(),source_version:'1',role:'client'}};
    const applied=await call('log-activity',input);
    const version=(await c.query('select version from client where id=$1',[target])).rows[0].version;
    const av=(await c.query('select version from activity where id=$1',[applied.activity_id])).rows[0].version;
    return {target,input,applied,args:{idempotency_key:randomUUID(),original_key:input.idempotency_key,source_version:'1',base_version:version,activity_base_version:av,source_base_version:av}};
  };
  const date=async target=>(await c.query("select last_touch::text from v_last_touch where subject_type='client' and subject_id=$1",[target])).rows[0]?.last_touch;
  await t.test('latest correction restores exact prior date and retains original',async()=>{
    const x=await setup(); assert.equal(await date(x.target),'2026-01-03');
    const r=await call('correct-email-touch',x.args);assert.equal(r.ok,true);assert.equal(await date(x.target),'2026-01-01');
    assert.equal((await c.query('select kind from activity where id=$1',[x.applied.activity_id])).rows[0].kind,'email_in');
    assert.equal((await c.query("select count(*)::int n from event where idempotency_key=$1 and new_value ? 'email_application'",[x.input.idempotency_key])).rows[0].n,1);
    await c.query('delete from tool_call where idempotency_key=$1',[x.args.idempotency_key]);
    assert.equal((await call('correct-email-touch',x.args)).replayed,true);
    await assert.rejects(call('log-activity',{...x.input,idempotency_key:x.args.idempotency_key}),e=>e.payload?.error==='key_reuse');
    await assert.rejects(call('correct-email-touch',{...x.args,source_version:'changed'}),e=>e.payload?.error==='key_reuse');
    await assert.rejects(call('correct-email-touch',{...x.args,idempotency_key:randomUUID()}),e=>e.payload?.error==='email_touch_already_corrected');
    assert.equal((await c.query("select count(*)::int n from event where verb='correct-email-touch' and subject_id=$1",[x.applied.activity_id])).rows[0].n,1);
  });
  await t.test('newer unrelated contact survives correction',async()=>{
    const x=await setup();await call('log-activity',{idempotency_key:randomUUID(),ref:x.target,kind:'call',summary:'Synthetic',occurred_at:'2026-01-05T12:00:00Z'});
    await call('correct-email-touch',x.args);assert.equal(await date(x.target),'2026-01-05');
  });
  await t.test('client correction also restores its deal date',async()=>{
    const x=await setup();
    const deal=(await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic',$1,'purchase','site_selection','joe',$2,$2) returning id",[x.target,actor.id])).rows[0].id;
    await call('correct-email-touch',x.args);
    assert.equal((await c.query("select last_touch::text from v_last_touch where subject_type='deal' and subject_id=$1",[deal])).rows[0].last_touch,'2026-01-01');
  });
  await t.test('lead and vendor correction with no prior contact clears derived date',async()=>{
    for(const [table,role] of [['lead','prospect'],['vendor','vendor']]) {
      const x=await setup();const party=(await c.query('select party_id from client where id=$1',[x.target])).rows[0].party_id;
      const target=(await c.query(table==='lead' ? "insert into lead(party_id,stage,created_by,updated_by) values($1,'new',$2,$2) returning id,version" : "insert into vendor(party_id,category,created_by,updated_by) values($1,'Fixture',$2,$2) returning id,version",[party,actor.id])).rows[0];
      const applied=await call('log-activity',{...x.input,idempotency_key:randomUUID(),ref:target.id,email_touch:{...x.input.email_touch,role}});
      const activity=(await c.query('select version from activity where id=$1',[applied.activity_id])).rows[0];
      await call('correct-email-touch',{...x.args,original_key:applied.email_application.original_key,base_version:target.version,activity_base_version:activity.version,source_base_version:activity.version});
      assert.equal((await c.query('select last_touch from v_last_touch where subject_type=$1 and subject_id=$2',[table,target.id])).rows.length,0);
      if (table==='vendor') {
        const stats=(await c.query(`select vr.payload from vendor v join party p on p.id=v.party_id ${vendorRelationshipJoin} where v.id=$1`,[target.id])).rows[0].payload;
        assert.equal(stats.last_contacted_at,null);assert.equal(stats.last_contact_note,null);
        assert.ok(stats.recent_entries.some(r=>r.id===applied.activity_id));
      }
    }
  });
  await t.test('actual intervening target edit and terminal target refuse',async()=>{
    const x=await setup();await c.query('update client set version=version+1 where id=$1',[x.target]);
    await assert.rejects(call('correct-email-touch',x.args),e=>e.payload?.error==='version_conflict');
    await c.query('update party set deleted_at=now() where id=(select party_id from client where id=$1)',[x.target]);
    await assert.rejects(call('correct-email-touch',{...x.args,base_version:x.args.base_version+1}),e=>e.payload?.error==='email_touch_target_not_live');
  });
  await t.test('newer source revision refuses even with current versions',async()=>{
    const x=await setup();await call('log-activity',{...x.input,idempotency_key:randomUUID(),email_touch:{...x.input.email_touch,source_version:'2'}});
    await assert.rejects(call('correct-email-touch',x.args),e=>e.payload?.error==='email_touch_newer_work');assert.equal(await date(x.target),'2026-01-03');
  });
  await t.test('changed target, activity, binding and source versions refuse',async()=>{
    for(const field of ['base_version','activity_base_version','source_base_version','source_version']){
      const x=await setup();await assert.rejects(call('correct-email-touch',{...x.args,[field]:field==='source_version'?'2':x.args[field]+1}),e=>['version_conflict','email_touch_source_conflict'].includes(e.payload?.error));assert.equal(await date(x.target),'2026-01-03');
    }
  });
  await t.test('source tombstone refuses correction',async()=>{
    const x=await setup();const identity={mailbox_owner:'joe',mailbox_account:'fixture@example.test',item_id:randomUUID()};
    const input={...x.input,idempotency_key:randomUUID(),email_identity:identity,email_touch:{...x.input.email_touch,source_id:randomUUID()}};
    await call('log-activity',input);
    await call('record-email-lifecycle',{idempotency_key:randomUUID(),email_identity:identity,base_version:0,state:'deleted',observation_id:randomUUID(),observed_at:'2026-01-04T12:00:00Z',method:'outlook_tombstone'});
    await assert.rejects(call('correct-email-touch',{...x.args,original_key:input.idempotency_key,activity_base_version:2,source_base_version:2}),e=>e.payload?.error==='email_not_live');
  });
  await t.test('distinct source activity version changes and deletion refuse',async()=>{
    const x=await setup();
    const source=await call('log-activity',{...x.input,idempotency_key:randomUUID(),email_touch:undefined});
    const applied=await call('log-activity',{...x.input,idempotency_key:randomUUID(),email_touch:{...x.input.email_touch,source_id:randomUUID()},
      email_context:{source_activity_id:source.activity_id,subject:'Synthetic',conversation_id:null,sender:{email:'fixture@example.test',internal:false},recipients:[]}});
    const args={...x.args,original_key:applied.email_application.original_key};
    await c.query('update activity set version=version+1 where id=$1',[source.activity_id]);
    await assert.rejects(call('correct-email-touch',args),e=>e.payload?.error==='version_conflict');
    await c.query('delete from activity where id=$1',[source.activity_id]);
    await assert.rejects(call('correct-email-touch',{...args,source_base_version:2}),e=>e.payload?.error==='email_touch_source_conflict');
  });
  await t.test('compensation and marker roll back if receipt fails',async()=>{
    const x=await setup();
    await c.query(`create function fixture_correction_failure() returns trigger language plpgsql as $$
      begin if new.verb='correct-email-touch' then raise exception 'fixture_correction_failure'; end if; return new; end $$;
      create trigger fixture_correction_failure before insert on tool_call for each row execute function fixture_correction_failure()`);
    await assert.rejects(call('correct-email-touch',x.args));
    assert.equal(await date(x.target),'2026-01-03');
    assert.equal((await c.query("select count(*)::int n from event where verb='correct-email-touch' and subject_id=$1",[x.applied.activity_id])).rows[0].n,0);
    await c.query('drop trigger fixture_correction_failure on tool_call; drop function fixture_correction_failure()');
    await call('correct-email-touch',x.args);assert.equal(await date(x.target),'2026-01-01');
    const expected={...x.applied.email_application};delete expected.activity_id;
    assert.equal((await call('reconcile-email-touches',{expected:[expected],start:'2026-01-01T00:00:00Z',end:'2026-01-06T00:00:00Z'})).counts.corrected,1);
    await assert.rejects(c.query('delete from event where idempotency_key=$1',[x.args.idempotency_key]),/email_touch_application_immutable/);
  });
  await t.test('concurrent different keys compensate once; concurrent same key replays',async()=>{
    const peers=[new pg.Client(fixture.connection),new pg.Client(fixture.connection)];await Promise.all(peers.map(p=>p.connect()));
    try {
      const x=await setup();const same=await Promise.all(peers.map(p=>run(p,'correct-email-touch',x.args)));assert.equal(same.filter(r=>r.replayed).length,1);
      const y=await setup();const r=await Promise.allSettled(peers.map(p=>run(p,'correct-email-touch',{...y.args,idempotency_key:randomUUID()})));assert.equal(r.filter(x=>x.status==='fulfilled').length,1);assert.equal(r.find(x=>x.status==='rejected').reason.payload.error,'email_touch_already_corrected');
    }finally{await Promise.all(peers.map(p=>p.end()));}
  });
  await t.test('linked-source revision and correction race without lock inversion',async()=>{
    const x=await setup();
    const source=await call('log-activity',{...x.input,idempotency_key:randomUUID(),email_touch:undefined});
    const input={...x.input,idempotency_key:randomUUID(),email_touch:{...x.input.email_touch,source_id:randomUUID()},
      email_context:{source_activity_id:source.activity_id,subject:'Synthetic',conversation_id:null,sender:{email:'fixture@example.test',internal:false},recipients:[]}};
    const applied=await call('log-activity',input);
    const peer=new pg.Client(fixture.connection);await peer.connect();
    const pid=(await peer.query('select pg_backend_pid() pid')).rows[0].pid;
    await c.query('begin');
    await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',[JSON.stringify(['email-touch-source',actor.id,input.email_touch.source_id,'client',x.target])]);
    const revision=run(peer,'log-activity',{...input,idempotency_key:randomUUID(),email_touch:{...input.email_touch,source_version:'2'}}).then(value=>({value}),error=>({error}));
    try {
      let blocked=false;
      for(let attempt=0;attempt<200 && !blocked;attempt++)
        blocked=(await c.query('select cardinality(pg_blocking_pids($1))>0 blocked',[pid])).rows[0].blocked;
      assert.equal(blocked,true,'revision reaches the held source-target lock');
      await c.query('set local role carr_writer');
      await executeRegisteredTool(c,actor,'correct-email-touch',{...x.args,original_key:applied.email_application.original_key});
      await c.query('commit');
      const result=await revision;if(result.error) throw result.error;
      assert.ok(result.value.activity_id);
    }finally{await c.query('rollback');await revision;await peer.end();}
  });
});
