import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { connectionRouteForTool } from '../src/mcp.js';
import { ToolError } from '../src/tool-error.ts';
import { publicError } from '../src/public-errors.ts';

test('email touch refusals preserve their public codes without source payloads',()=>{
  for(const code of ['email_touch_requires_email_occurrence','email_touch_role_mismatch',
    'email_touch_target_not_live','email_touch_already_applied','email_touch_occurrence_conflict',
    'email_touch_window_invalid','email_touch_duplicate_expectation']) {
    const error=publicError(new ToolError({error:code,source_id:'private-fixture'}),{});
    assert.equal(error.error,code); assert.equal(error.source_id,undefined);
  }
});

test('email touch permanent applications and bounded reconciliation', async t => {
  assert.equal(connectionRouteForTool(TOOLS['reconcile-email-touches']),'writer_read_only');
  const fixture = await acquireDisposablePostgres({prefix:'email-touch-',snapshot:true});
  const c = new pg.Client(fixture.connection); await c.connect();
  t.after(async()=>{ await c.end(); await fixture.close(); });
  const actor={...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
  await c.query("update actor set email='joe@example.test' where id=$1",[actor.id]);
  await c.query("insert into client_status(slug,label) values('active','Fixture') on conflict do nothing");
  const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic',$1,$1) returning id",[actor.id])).rows[0].id;
  const client=(await c.query("insert into client(party_id,status,created_by,updated_by,roster_ref) values($1,'active',$2,$2,'C-999134') returning id",[party,actor.id])).rows[0].id;
  const call=async(verb,args,role='carr_writer')=>{
    await c.query(verb==='reconcile-email-touches' ? 'begin read only' : 'begin');
    try { await c.query(`set local role ${role}`); const r=await executeRegisteredTool(c,actor,verb,args); await c.query('commit'); return r; }
    catch(e){await c.query('rollback');throw e;}
  };
  const input={idempotency_key:randomUUID(),ref:client,kind:'email_in',summary:'Synthetic',occurred_at:'2026-01-01T12:00:00Z',email_touch:{source_id:randomUUID(),source_version:'1',role:'client'}};
  let result;
  await t.test('commit before lost acknowledgement replays after ordinary receipt deletion',async()=>{
    result=await call('log-activity',input);
    await c.query("update tool_call set request_hash='obsolete-registry-receipt' where idempotency_key=$1",[input.idempotency_key]);
    assert.equal((await call('log-activity',input)).activity_id,result.activity_id);
    await c.query("update tool_call set created_at=now()-interval '31 days' where idempotency_key=$1",[input.idempotency_key]);
    assert.equal((await call('log-activity',input)).activity_id,result.activity_id);
    await c.query('delete from tool_call where idempotency_key=$1',[input.idempotency_key]);
    const replay=await call('log-activity',input);
    assert.equal(replay.activity_id,result.activity_id); assert.equal(replay.replayed,true);
    assert.equal(result.email_application.target_id,client);
    assert.equal(result.email_application.original_key,input.idempotency_key);
    assert.equal(result.email_application.role,'client');
  });
  await t.test('changed payload and removed binding refuse same permanent key',async()=>{
    for(const change of [{summary:'Changed'},{email_touch:undefined}])
      await assert.rejects(call('log-activity',{...input,...change}),e=>e.payload?.error==='key_reuse');
  });
  await t.test('per-target source version uniqueness and canonical role resolution',async()=>{
    await assert.rejects(call('log-activity',{...input,idempotency_key:randomUUID()}),e=>e.payload?.error==='email_touch_already_applied');
    await assert.rejects(call('log-activity',{...input,idempotency_key:randomUUID(),email_touch:{...input.email_touch,role:'vendor'}}),e=>e.payload?.error==='email_touch_role_mismatch');
    const revision=await call('log-activity',{...input,idempotency_key:randomUUID(),email_touch:{...input.email_touch,source_version:'2'}});
    assert.equal(revision.email_application.source_version,'2');
  });
  const expected={...result.email_application}; delete expected.activity_id;
  const read=effects=>call('reconcile-email-touches',{expected:effects,start:'2026-01-01T00:00:00Z',end:'2026-01-02T00:00:00Z'},'carr_writer');
  await t.test('counts only with exact cohort partition and bounded input',async()=>{
    const r=await read([expected,{...expected,original_key:randomUUID(),source_id:randomUUID()},{...expected,original_key:randomUUID(),source_id:randomUUID(),refused:true}]);
    assert.equal(r.counts.applied,1); assert.equal(r.counts.missing,1); assert.equal(r.counts.refused,1);
    assert.equal(r.counts.expected,3); assert.equal(r.complete,true); assert.equal(r.effects,undefined);
    assert.deepEqual(r.groups,[{target_type:'client',source_version:'1',counts:r.counts}]);
    assert.equal(Object.entries(r.counts).filter(([key])=>key!=='expected').reduce((sum,[,n])=>sum+n,0),r.counts.expected);
    await assert.rejects(read([expected,expected]));
    await assert.rejects(read(Array(501).fill(expected)));
    assert.equal((await read([{...expected,source_version:'changed'}])).counts.conflicting,1);
  });
  await t.test('one source can touch each canonical target role independently',async()=>{
    const lead=(await c.query("insert into lead(party_id,stage,created_by,updated_by,registry_ref) values($1,'new',$2,$2,'L-999134') returning id",[party,actor.id])).rows[0].id;
    const vendor=(await c.query("insert into vendor(party_id,category,created_by,updated_by,vendor_ref) values($1,'Fixture',$2,$2,'V-TEST-999134') returning id",[party,actor.id])).rows[0].id;
    for (const [id,role] of [[lead,'prospect'],[vendor,'vendor']]) {
      const a=await call('log-activity',{...input,ref:id,idempotency_key:randomUUID(),email_touch:{...input.email_touch,role}});
      assert.equal(a.email_application.target_id,id); assert.equal(a.email_application.role,role);
    }
  });
  await t.test('occurrence and recorded windows are independently half open',async()=>{
    const args={expected:[expected],start:'2026-01-01T12:00:00Z',end:'2026-01-01T12:00:01Z'};
    assert.equal((await call('reconcile-email-touches',args,'carr_writer')).counts.applied,1);
    assert.equal((await call('reconcile-email-touches',{...args,start:'2026-01-01T11:00:00Z',end:args.start},'carr_writer')).counts.out_of_window,1);
    const stamp=(await c.query('select recorded_at from event where idempotency_key=$1',[input.idempotency_key])).rows[0].recorded_at;
    const recorded_start=new Date(stamp.getTime()+1000).toISOString();
    const recorded_end=new Date(stamp.getTime()+2000).toISOString();
    assert.equal((await call('reconcile-email-touches',{...args,recorded_start,recorded_end},'carr_writer')).counts.out_of_window,1);
    await assert.rejects(call('reconcile-email-touches',{...args,recorded_start},'carr_writer'));
  });
  await t.test('immutable evidence and dangling activity are distinct from missing',async()=>{
    await assert.rejects(c.query("update event set new_value='{}' where idempotency_key=$1",[input.idempotency_key]),/email_touch_application_immutable/);
    await assert.rejects(c.query('delete from event where idempotency_key=$1',[input.idempotency_key]),/email_touch_application_immutable/);
    await c.query('delete from activity where id=$1',[result.activity_id]);
    assert.equal((await read([expected])).counts.dangling,1);
  });
  await t.test('failed transaction leaves no successful application event',async()=>{
    const bad={...input,idempotency_key:randomUUID(),email_touch:{...input.email_touch,source_version:'rollback'}};
    await c.query(`create function fixture_receipt_failure() returns trigger language plpgsql as $$
      begin
        if new.idempotency_key='${bad.idempotency_key}' then
          if exists(select 1 from event where idempotency_key=new.idempotency_key) then
            raise exception 'fixture_after_application_event';
          end if;
          raise exception 'fixture_before_application_event';
        end if;
        return new;
      end $$;
      create trigger fixture_receipt_failure before insert on tool_call for each row execute function fixture_receipt_failure()`);
    await assert.rejects(call('log-activity',bad),e=>e.message==='fixture_after_application_event' || e.payload?.message==='fixture_after_application_event');
    assert.equal((await c.query('select count(*)::int n from event where idempotency_key=$1',[bad.idempotency_key])).rows[0].n,0);
    await c.query('drop trigger fixture_receipt_failure on tool_call; drop function fixture_receipt_failure()');
  });
  await t.test('mailbox tombstones retain proof, refuse new application, allow exact replay',async()=>{
    const identity={mailbox_owner:'joe',mailbox_account:'joe@example.test',item_id:randomUUID()};
    const captured={...input,idempotency_key:randomUUID(),email_identity:identity,email_touch:{...input.email_touch,source_id:randomUUID()}};
    const applied=await call('log-activity',captured);
    const revision=await call('log-activity',{...captured,idempotency_key:randomUUID(),email_touch:{...captured.email_touch,source_version:'2'}});
    assert.equal(revision.activity_id,applied.activity_id);
    await assert.rejects(call('log-activity',{...captured,idempotency_key:randomUUID(),occurred_at:'2026-01-01T12:01:00Z',email_touch:{...captured.email_touch,source_version:'3'}}),e=>e.payload?.error==='email_touch_occurrence_conflict');
    await call('record-email-lifecycle',{idempotency_key:randomUUID(),email_identity:identity,base_version:0,state:'deleted',observation_id:randomUUID(),observed_at:'2026-01-02T12:00:00Z',method:'outlook_tombstone'});
    assert.equal((await call('log-activity',captured)).activity_id,applied.activity_id);
    await assert.rejects(call('log-activity',{...captured,idempotency_key:randomUUID(),email_touch:{...captured.email_touch,source_version:'4'}}),e=>e.payload?.error==='email_not_live');
    const effect={...applied.email_application}; delete effect.activity_id;
    assert.equal((await read([effect])).counts.tombstoned,1);
  });
  await t.test('concurrent exact replay and competing keys serialize',async()=>{
    const peers=[new pg.Client(fixture.connection),new pg.Client(fixture.connection)];
    await Promise.all(peers.map(p=>p.connect()));
    const run=async(p,args)=>{
      await p.query('begin');
      try { await p.query('set local role carr_writer'); const r=await executeRegisteredTool(p,actor,'log-activity',args); await p.query('commit'); return r; }
      catch(e){await p.query('rollback');throw e;}
    };
    try {
      const args={...input,idempotency_key:randomUUID(),email_touch:{...input.email_touch,source_id:randomUUID()}};
      const same=await Promise.all(peers.map(p=>run(p,args)));
      assert.equal(same[0].activity_id,same[1].activity_id);
      const next={...args,email_touch:{...args.email_touch,source_version:'2'}};
      const competing=await Promise.allSettled(peers.map(p=>run(p,{...next,idempotency_key:randomUUID()})));
      assert.equal(competing.filter(r=>r.status==='fulfilled').length,1);
      assert.equal(competing.find(r=>r.status==='rejected').reason.payload.error,'email_touch_already_applied');
    } finally { await Promise.all(peers.map(p=>p.end())); }
  });
  await t.test('terminal canonical targets are excluded and cannot receive new touches',async()=>{
    const args={...input,idempotency_key:randomUUID(),email_touch:{...input.email_touch,source_id:randomUUID()}};
    const applied=await call('log-activity',args);
    await c.query('update party set deleted_at=now() where id=$1',[party]);
    const effect={...applied.email_application}; delete effect.activity_id;
    assert.equal((await read([effect])).counts.tombstoned,1);
    await assert.rejects(call('log-activity',{...args,idempotency_key:randomUUID(),email_touch:{...args.email_touch,source_version:'2'}}),e=>e.payload?.error==='email_touch_target_not_live');
    assert.equal((await read([{...effect,original_key:randomUUID(),source_version:'2'}])).counts.tombstoned,1);
    await c.query('update party set deleted_at=null where id=$1',[party]);
  });
});
