import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { snapshotDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';

const dsn = await snapshotDatabase('subject-watches-');
test('registered watch transitions preserve isolation and initial baseline', async t => {
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
  const target = {subject_type:'deal',subject_id:deal};
  const write = (verb,args=target,who=joe,key=randomUUID()) => call(verb,{...args,idempotency_key:key},who);
  const read = (args=target,who=joe) => call('read-subject-watch-v1',args,who);
  const list = (args={},who=joe) => call('list-subject-watches-v1',args,who);
  const refuses = (promise,error) => assert.rejects(promise,e=>e.payload?.error===error);
  assert.ok(TOOLS['watch-subject-v1']);
  assert.equal((await read()).watch,null);
  const notifications = (await c.query('select count(*) from ops.notification')).rows[0].count;
  const key=randomUUID();
  const first = await write('watch-subject-v1',target,joe,key);
  assert.equal(first.schema_version,'subject-watch.v1');
  assert.equal(first.watch.baseline.version,1);
  assert.equal(first.watch.actor_id,joe.id);
  assert.equal((await write('watch-subject-v1',target,joe,key)).replayed,true);
  const concurrent = new pg.Client({connectionString:dsn});
  await concurrent.connect();
  try {
    const args={...target,idempotency_key:randomUUID()};
    const responses=await Promise.all([call('watch-subject-v1',args),call('watch-subject-v1',args,joe,concurrent)]);
    assert.equal(responses.filter(r=>r.replayed).length,1);
    assert.deepEqual(responses[0].watch,responses[1].watch);
  } finally { await concurrent.end(); }
  await refuses(write('watch-subject-v1',{subject_type:'lease',subject_id:lease},joe,key),'key_reuse');
  await refuses(write('watch-subject-v1',target,dell,key),'key_reuse');
  assert.equal((await read(target,dell)).watch,null);
  await write('watch-subject-v1',target,dell);
  await c.query("update deal set owner='dell',version=version+1 where id=$1",[deal]);
  assert.deepEqual((await read()).watch,first.watch);
  assert.deepEqual((await write('watch-subject-v1')).watch,first.watch);
  const leaseTarget={subject_type:'lease',subject_id:lease};
  await write('watch-subject-v1',leaseTarget);
  assert.equal((await list()).watches.length,2);
  const page=await list({limit:1});
  assert.equal(page.watches.length,1); assert.equal(page.has_more,true);
  const next=await list({limit:1,cursor:page.next_cursor});
  assert.equal(next.watches.length,1); assert.notEqual(next.watches[0].subject_id,page.watches[0].subject_id);
  assert.equal(next.has_more,false);
  const unwatchKey=randomUUID();
  await write('unwatch-subject-v1',target,joe,unwatchKey);
  assert.equal((await write('unwatch-subject-v1',target,joe,unwatchKey)).replayed,true);
  assert.equal((await read()).watch,null);
  assert.ok((await read(target,dell)).watch);
  assert.equal((await write('unwatch-subject-v1')).rows_changed,0);
  assert.equal((await write('watch-subject-v1')).watch.baseline.version,2);
  await c.query('update party set deleted_at=now() where id=$1',[party]);
  await refuses(read(),'watch_target_not_found');
  await refuses(write('watch-subject-v1'),'watch_target_not_found');
  await refuses(write('watch-subject-v1',target,joe,key),'watch_target_not_found');
  assert.deepEqual((await list()).watches,[]);
  await write('unwatch-subject-v1');
  await c.query('update party set deleted_at=null where id=$1',[party]);
  assert.equal((await read()).watch,null);
  assert.ok((await read(leaseTarget)).watch);
  await c.query('delete from lease where id=$1',[lease]);
  await refuses(read(leaseTarget),'watch_target_not_found');
  await write('unwatch-subject-v1',leaseTarget);
  for (const subject_type of ['deal','lease','listing'])
    await refuses(write('watch-subject-v1',{subject_type,subject_id:randomUUID()}),'watch_target_not_found');
  await assert.rejects(list({limit:101}));
  await assert.rejects(read({...target,actor_id:dell.id}));
  await c.query('begin');
  try {
    await c.query('set local role carr_reader');
    await assert.rejects(c.query('select * from ops.subject_watch'),e=>e.code==='42501');
  } finally { await c.query('rollback'); }
  await c.query('begin');
  try {
    await c.query("select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true),set_config('carr.organization_tenant_id','other-tenant',true)");
    await c.query('set local role carr_writer');
    await assert.rejects(executeRegisteredTool(c,joe,'list-subject-watches-v1',{}));
  } finally { await c.query('rollback'); }
  assert.equal((await c.query('select count(*) from ops.notification')).rows[0].count,notifications);
});
