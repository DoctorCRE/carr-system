import test from 'node:test';
import assert from 'node:assert/strict';
import { sessionCheckpointTools } from '../src/session-checkpoints.js';
test('neutral v1 contract exposes checkpoint, bounded recovery and archive', () => {
 const tools=sessionCheckpointTools();
 assert.deepEqual(Object.keys(tools),['write-session-checkpoint-v1','read-session-checkpoint-v1','archive-session-checkpoint-v1']);
});

import { postgresDatabase } from './helpers/postgres-database.mjs';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import pg from 'pg';
export const binding={provider:'codex',provider_instance:'synthetic',harness:'app-server',environment:'fixture',native_session_id:'native-1',role:'t3-worker',thread_id:'thread',task_id:'task',parent_destination:'parent',card:'WR826.S3.J2',worktree:'/synthetic',repository_revision:'synthetic-revision',lease_refs:['lease']};
const checkpoint=Object.fromEntries(['acceptance','corrections','decisions','evidence_refs','active_operations','exact_jobs','destinations','watches','links','revisions','cards','leases'].map(k=>[k,[]]));
checkpoint.next_action='synthetic recovery';
const envelope={schema_version:1,binding,kind:'usage',payload:{},observed_at:1,now:1,epoch:1,epoch_evidence:'synthetic',cursor:1,expected_generation:0,model_fingerprint:'model',usage_model_fingerprint:'model',checkpoint};
test('real PostgreSQL custody, CAS, exact replay, identity isolation and terminal archive',async t=>{
 const {client:c,close}=await postgresDatabase('checkpoint-v1-'); t.after(close);
 await c.query('create schema ops');
 await c.query("create function ops.scac_current_grant_contract() returns jsonb language sql immutable as $$ select '{}'::jsonb $$");
 for(const role of ['carr_reader','carr_writer','carr_authority','carr_jobs','dot_reader']) await c.query(`create role ${role}`);
 await c.query('begin');
 await c.query(readFileSync(new URL('../../migrations/1151_session_checkpoint_authority.sql',import.meta.url),'utf8'));
 await c.query('commit');
 const tools=sessionCheckpointTools();
 const authority={sponsor:'joe',authority_session:randomUUID(),binding};
 const actor={via:'session-checkpoint-token',session_checkpoint_authority:authority};
 const call=async(verb,args,who=actor)=>{
  await c.query("select set_config('carr.acting_actor_slug',$1,false)",[(who.session_checkpoint_authority?.sponsor||'unknown')+'-local']);
  return tools[verb].handler(c,who,args);
 };
 const write=args=>call('write-session-checkpoint-v1',args);
 const read=()=>call('read-session-checkpoint-v1',{binding});
 const first={binding,idempotency_key:randomUUID(),envelope};
 const result=await write(first); assert.equal(result.record.generation,1); assert.deepEqual((await read()).record,result.record);
 assert.deepEqual(await write(first),result);
 await assert.rejects(write({...first,envelope:{...envelope,checkpoint:{...checkpoint,next_action:'changed'}}}),e=>e.payload.error==='changed_checkpoint_replay');
 await assert.rejects(write({...first,idempotency_key:randomUUID()}),e=>e.payload.error==='checkpoint_cas_conflict');
 for(const field of ['provider','provider_instance','environment','native_session_id','task_id','repository_revision','role']){
  const changed={...binding,[field]:field==='provider'?'grok':field==='role'?'reviewer':'changed',...(field==='provider'?{harness:'acp'}:{})};
  await assert.rejects(call('read-session-checkpoint-v1',{binding:changed}),e=>e.payload.error==='session_checkpoint_identity_required');
  const other={...actor,session_checkpoint_authority:{...authority,binding:changed}};
  await assert.rejects(call('read-session-checkpoint-v1',{binding:changed},other),e=>e.payload.error==='immutable_checkpoint_binding');
 }
 for(const key of ['sponsor','authority_session']) {
  const other={...actor,session_checkpoint_authority:{...authority,[key]:key==='sponsor'?'dell':randomUUID()}};
  assert.equal((await call('read-session-checkpoint-v1',{binding},other)).record,null);
 }
 await assert.rejects(call('read-session-checkpoint-v1',{binding},{}),e=>e.payload.error==='session_checkpoint_identity_required');
 await assert.rejects(call('read-session-checkpoint-v1',{binding:{...binding,task_id:null}}),e=>e.payload.error==='invalid_session_checkpoint_binding');
 await assert.rejects(write({...first,sponsor:'dell'}),e=>e.payload.error==='invalid_session_checkpoint');
 await assert.rejects(write({...first,envelope:{...envelope,checkpoint:{...checkpoint,next_action:'x'.repeat(33000)}}}),e=>e.payload.error==='invalid_session_checkpoint');
 const archive={binding,idempotency_key:randomUUID(),expected_generation:1};
 const archived=await call('archive-session-checkpoint-v1',archive); assert.equal(archived.record.archived,true);
 assert.deepEqual(await call('archive-session-checkpoint-v1',archive),archived);
 assert.deepEqual((await read()).record,archived.record);
 await assert.rejects(write({...first,idempotency_key:randomUUID(),envelope:{...envelope,expected_generation:2,cursor:2}}),e=>e.payload.error==='checkpoint_archived');
 assert.deepEqual(await write(first),result);

 await t.test('aged receipts refuse changed payloads and exact expired retries',async()=>{
  const scoped={...actor,session_checkpoint_authority:{...authority,authority_session:randomUUID()}};
  const oldest={...first,idempotency_key:randomUUID()};
  await call('write-session-checkpoint-v1',oldest,scoped);
  for(let n=1;n<=9;n++) await call('write-session-checkpoint-v1',{
   binding,idempotency_key:randomUUID(),envelope:{...envelope,cursor:n+1,expected_generation:n},
  },scoped);
  await assert.rejects(call('write-session-checkpoint-v1',oldest,scoped),e=>e.payload.error==='checkpoint_replay_expired');
  await assert.rejects(call('write-session-checkpoint-v1',{...oldest,envelope:{...envelope,checkpoint:{...checkpoint,next_action:'changed'}}},scoped),e=>e.payload.error==='changed_checkpoint_replay');
 });
 await t.test('two concurrent connections admit only one expected generation',async()=>{
  const second=new pg.Client(c.connectionParameters);
  await second.connect();
  try{
   await second.query("select set_config('carr.acting_actor_slug','joe-local',false)");
   await c.query("select set_config('carr.acting_actor_slug','joe-local',false)");
   const scoped={...actor,session_checkpoint_authority:{...authority,authority_session:randomUUID()}};
   const operation='write-session-checkpoint-v1';
   const results=await Promise.allSettled([c,second].map(connection=>tools[operation].handler(connection,scoped,{...first,idempotency_key:randomUUID()})));
   assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
   assert.equal(results.find(r=>r.status==='rejected').reason.payload.error,'checkpoint_cas_conflict');
  }finally{await second.end();}
 });
 await t.test('pinned bridge writes through the same real PG contract', {skip:!process.env.CARR_CONTINUITY_ARTIFACT_ROOT}, async()=>{
  const root=process.env.CARR_CONTINUITY_ARTIFACT_ROOT;
  const event=JSON.parse(readFileSync(root+'/tests/fixtures/session-continuity/codex-appserver.json','utf8')).envelope;
  const scoped={via:'session-checkpoint-token',session_checkpoint_authority:{sponsor:'joe',authority_session:randomUUID(),binding:event.binding}};
  const script=`import importlib.util,json,sys
s=importlib.util.spec_from_file_location('bridge','ops/session-continuity-adapter.py')
b=importlib.util.module_from_spec(s);s.loader.exec_module(b)
def call(verb,args):
 print(json.dumps(dict(verb=verb,args=args)),flush=True)
 response=json.loads(sys.stdin.readline())
 if 'error' in response: raise ValueError(response['error'])
 return response
result=b.process(json.loads(sys.argv[2]),sys.argv[1],call)
print(json.dumps(dict(final=result)),flush=True)
`;
  const child=spawn('python3',['-c',script,root,JSON.stringify(event)],{cwd:new URL('../../',import.meta.url),stdio:['pipe','pipe','pipe']});
  let final, errors=''; child.stderr.on('data',data=>errors+=data);
  const completion=new Promise(resolve=>child.on('exit',code=>resolve(code)));
  for await(const line of createInterface({input:child.stdout})){
   const message=JSON.parse(line);
   if(message.final){final=message.final;continue;}
   try{child.stdin.write(JSON.stringify(await call(message.verb,message.args,scoped))+'\n');}
   catch(e){child.stdin.write(JSON.stringify({error:e.payload?.error||e.message})+'\n');}
  }
  assert.equal(await completion,0,errors);
  assert.equal(final.status,'saved'); assert.equal(final.receipt.native_delivery,'pending');
  const direct=await call('read-session-checkpoint-v1',{binding:event.binding},scoped);
  assert.deepEqual(direct.record.envelope,event); assert.equal(direct.record.generation,final.receipt.generation);
 });

});
