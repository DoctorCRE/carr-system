// Exercise trusted production proof ingestion and immutable history on PostgreSQL.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, generateKeyPairSync, sign } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { productionProofMessage, productionProofDigest } from '../src/production-proof.js';
import plan from '../../contracts/production-proof-plan.v1.json' with { type:'json' };
import { executeRegisteredTool } from '../src/tools.js';

test('production proof trust and immutable lifecycle', async t => {
  const fixture = await acquireDisposablePostgres({ prefix:'production-proof-', snapshot:true });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  t.after(async()=>{await c.end(); await fixture.close();});
  assert.equal((await c.query('select ops.scac_mutation_catalog_v118_current() as current')).rows[0].current,true);
  const actor = {...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:false};
  const call = async (verb,args,who=actor,role='carr_writer',db=c) => {
    await db.query('begin');
    try {
      await db.query(`set local role ${role}`);
      const result = await executeRegisteredTool(db,who,verb,args);
      await db.query('commit');
      return result;
    } catch(e) { await db.query('rollback'); throw e; }
  };
  let observedClock=new Date((await c.query('select clock_timestamp() as observed')).rows[0].observed).getTime();
  const observe=()=>new Date(++observedClock).toISOString();
  const {privateKey,publicKey}=generateKeyPairSync('ed25519');
  const publicBytes=publicKey.export({format:'jwk'}).x;
  const runner='worker-release-smoke.v1';
  const sha='a'.repeat(40),digest='sha256:'+'a'.repeat(64);
  const artifact={ref:'artifact://proof/status',digest,bytes:10};
  const requirement={schema:'carr-production-proof-plan.v1',requirement_id:'synthetic-proof',targets:['worker'],
    assertions:[{id:'status-read',predicate:'Signed out status is healthy',runner,expected_result:'Healthy status observed',input_class:'synthetic',deadline_seconds:30}],
    safety:{read_only:true,no_sends:true,max_requests:10,max_records:10,max_runtime_seconds:30,max_artifact_bytes:100}};
  const binding={repo:'DoctorCRE/carr-system',merge_sha:sha,target:'worker',requirement_digest:productionProofDigest(requirement)};
  const signed=(kind,input,idempotency_key=randomUUID(),runner_id=runner)=>{
    const payload=kind==='deployment'?{...input,observed_at:observe()}:input;
    return {idempotency_key,runner_id,payload,signature:sign(null,Buffer.from(productionProofMessage(kind,payload)),privateKey).toString('base64')};
  };
  const status=()=>call('read-production-proof-status',{repo:binding.repo,merge_sha:sha,target:'worker'});
  const snapshot=async()=> (await c.query('select * from production_proof_record order by sequence')).rows;
  await t.test('builder_supplied_pass_is_refused',async()=>{
    await assert.rejects(call('record-production-proof-attempt',signed('receipt',{
      ...binding,deployment_id:randomUUID(),attempt_id:randomUUID(),assertion_id:'status-read',
      released_sha_before:sha,released_sha_after:sha,result:'pass',reason:'observed',
      started_at:observe(),finished_at:observe(),artifacts:[artifact],metrics:{requests:1,records:0},
    })),e=>e.payload?.error==='production_proof_untrusted_runner');
  });
  await c.query(`insert into production_proof_runner(id,actor_id,public_key,runner_revision,repo,target,operations)
    values($1,$2,$3,$4,$5,'worker',array['deployment','reservation','receipt'])`,
    [runner,actor.id,Buffer.from(publicBytes,'base64url').toString('base64'),plan['x-runners'][runner].revision,binding.repo]);
  assert.equal((await status()).state,'unproven');
  const deployPayload={...binding,released_sha:sha,provider_version:'fixture-v1',merge_membership:[sha],observed_at:observe(),artifact,requirement};
  const deployArgs=signed('deployment',deployPayload);
  const dep=(await call('record-production-deployment',deployArgs)).receipt;
  const acquire=async()=> (await call('acquire-production-proof-attempt',signed('reservation',{
    ...binding,deployment_id:dep.id,assertion_id:'status-read'}))).receipt;
  const attempt=await acquire();
  const receiptPayload=(a,result='pass')=>({...binding,deployment_id:dep.id,assertion_id:'status-read',attempt_id:a.id,
    released_sha_before:sha,released_sha_after:sha,result,reason:result==='pass'?'observed':result==='fail'?'assertion_failed':'missing_login',
    started_at:new Date(new Date(a.recorded_at).getTime()+1).toISOString(),finished_at:new Date(new Date(a.recorded_at).getTime()+1).toISOString(),artifacts:[artifact],metrics:{requests:1,records:0}});
  const pass=receiptPayload(attempt);
  const passArgs=signed('receipt',pass);
  const before=await snapshot();
  for (const [label,args,who,error] of [
    ['unsigned builder pass',{...passArgs,signature:'A'.repeat(88)},actor,'signature_invalid'],
    ['different actor',passArgs,{...actor,id:randomUUID()},'untrusted_runner'],
    ['human impersonation',passArgs,{...actor,human:true},'untrusted_runner'],
    ['wrong target',signed('receipt',{...pass,target:'app'}),actor,'runner_binding'],
    ['wrong requirement digest',signed('receipt',{...pass,requirement_digest:'sha256:'+'b'.repeat(64)}),actor,'deployment_binding'],
    ['wrong artifact binding',signed('receipt',{...pass,released_sha_after:'b'.repeat(40)}),actor,'artifact_binding'],
    ['tampered artifact descriptor',{...passArgs,payload:{...pass,artifacts:[{...artifact,digest:'sha256:'+'b'.repeat(64)}]}},actor,'signature_invalid'],
    ['unknown assertion',signed('receipt',{...pass,assertion_id:'unknown'}),actor,'assertion_runner_binding'],
    ['wrong attempt',signed('receipt',{...pass,attempt_id:randomUUID()}),actor,'attempt_binding'],
    ['bad result reason',signed('receipt',{...pass,reason:'missing_login'}),actor,'result_reason'],
    ['oversized artifacts',signed('receipt',{...pass,artifacts:[{...artifact,bytes:101}]}),actor,'budget_exceeded'],
    ['request budget',signed('receipt',{...pass,metrics:{requests:11,records:0}}),actor,'budget_exceeded'],
    ['pre-reservation observation',signed('receipt',{...pass,started_at:'2020-01-01T00:00:00Z'}),actor,'observation_time'],
    ['invalid result',signed('receipt',{...pass,result:'done'}),actor,'invalid_payload'],
  ]) await t.test(label,async()=>{
    await assert.rejects(call('record-production-proof-attempt',args,who),e=>e.payload?.error==='production_proof_'+error);
    assert.deepEqual(await snapshot(),before);
  });
  let receipt;
  await t.test('trusted runner pass persists with immutable provenance',async()=>{
    receipt=(await call('record-production-proof-attempt',passArgs)).receipt;
    assert.deepEqual(receipt.payload,pass);
    assert.equal(receipt.actor_id,actor.id);
    assert.equal(receipt.runner_id,runner);
    assert.equal(receipt.runner_revision,plan['x-runners'][runner].revision);
    assert.equal(receipt.signature,passArgs.signature);
    assert.equal(receipt.public_key,Buffer.from(publicBytes,'base64url').toString('base64'));
    assert.ok(receipt.recorded_at);
    assert.equal((await status()).state,'proven');
    assert.deepEqual((await status()).assertions[0].receipt.payload,pass);
  });
  await t.test('durable replay and changed-payload conflicts',async()=>{
    const before=await snapshot();
    assert.equal((await call('record-production-proof-attempt',passArgs)).receipt.id,receipt.id);
    assert.equal((await call('record-production-proof-attempt',{...passArgs,idempotency_key:randomUUID()})).receipt.id,receipt.id);
    assert.equal((await call('record-production-deployment',deployArgs)).receipt.id,dep.id);
    await assert.rejects(call('record-production-proof-attempt',signed('receipt',{...pass,result:'fail',reason:'assertion_failed'},passArgs.idempotency_key)),e=>e.payload?.error==='production_proof_key_reuse');
    await assert.rejects(call('record-production-proof-attempt',signed('receipt',{...pass,result:'fail',reason:'assertion_failed'})),e=>e.payload?.error==='production_proof_attempt_conflict');
    assert.deepEqual(await snapshot(),before);
  });
  await t.test('new reservation invalidates pass; older pass cannot hide later failure or block',async()=>{
    const old=await acquire();
    assert.equal((await status()).state,'proving');
    const newer=await acquire();
    await call('record-production-proof-attempt',signed('receipt',receiptPayload(newer,'fail')));
    assert.equal((await status()).state,'failed');
    await call('record-production-proof-attempt',signed('receipt',receiptPayload(old)));
    assert.equal((await status()).state,'failed');
    const blocked=await acquire();
    await call('record-production-proof-attempt',signed('receipt',receiptPayload(blocked,'blocked')));
    assert.equal((await status()).state,'blocked');
    for (const reason of ['awaiting_authorized_event','incompatible_release','missing_tool','artifact_unavailable']) {
      const next=await acquire();
      const stored=await call('record-production-proof-attempt',signed('receipt',{...receiptPayload(next,'blocked'),reason}));
      assert.equal(stored.receipt.payload.reason,reason);
      assert.equal((await status()).state,'blocked');
    }
    const retry=await acquire();
    await call('record-production-proof-attempt',signed('receipt',receiptPayload(retry)));
    assert.equal((await status()).state,'proven');
  });
  await t.test('declaration digest, unknown runners, duplicate assertions and target refuse',async()=>{
    await assert.rejects(call('record-production-deployment',signed('deployment',{...deployPayload,requirement_digest:digest})),e=>e.payload?.error==='production_proof_requirement_digest');
    for (const assertions of [[{...requirement.assertions[0],runner:'invented'}],[...requirement.assertions,...requirement.assertions]]) {
      const req={...requirement,assertions};
      await assert.rejects(call('record-production-deployment',signed('deployment',{...deployPayload,requirement:req,requirement_digest:productionProofDigest(req)})),e=>e.payload?.error==='production_proof_requirement_invalid');
    }
  });
  await t.test('concurrent reservation replay converges and concurrent sequences are unique',async()=>{
    const second=new pg.Client(fixture.connection);
    await second.connect();
    try {
      const args=signed('reservation',{...binding,deployment_id:dep.id,assertion_id:'status-read'});
      const first=await Promise.all([
        call('acquire-production-proof-attempt',args),
        call('acquire-production-proof-attempt',args,actor,'carr_writer',second),
      ]);
      assert.equal(first[0].receipt.id,first[1].receipt.id);
      const different=await Promise.all([
        call('acquire-production-proof-attempt',{...args,idempotency_key:randomUUID()}),
        call('acquire-production-proof-attempt',{...args,idempotency_key:randomUUID()},actor,'carr_writer',second),
      ]);
      assert.notEqual(different[0].receipt.sequence,different[1].receipt.sequence);
      assert.equal((await status()).state,'proving');
    } finally {await second.end();}
  });
  await t.test('new release and rollback invalidate proof and late observations',async()=>{
    const pending=await acquire();
    const next=(await call('record-production-deployment',signed('deployment',{...deployPayload,released_sha:'b'.repeat(40),provider_version:'fixture-v2'}))).receipt;
    assert.notEqual(next.id,dep.id);
    assert.equal((await status()).state,'deployed');
    await assert.rejects(call('record-production-proof-attempt',signed('receipt',receiptPayload(pending))),e=>e.payload?.error==='production_proof_deployment_not_current');
    await call('record-production-deployment',signed('deployment',{...deployPayload,released_sha:'c'.repeat(40),provider_version:'rollback',merge_membership:[]}));
    assert.equal((await status()).state,'unproven');
    await assert.rejects(call('record-production-deployment',{...deployArgs,idempotency_key:randomUUID()}),e=>e.payload?.error==='production_proof_deployment_order');
    await assert.rejects(acquire(),e=>e.payload?.error==='production_proof_deployment_not_current');
  });
  await t.test('one target release change invalidates older requirements and status queries stay bounded',async()=>{
    await call('record-production-deployment',signed('deployment',deployPayload));
    assert.equal((await status()).state,'deployed');
    const other={...binding,merge_sha:'d'.repeat(40)};
    const assertions=Array.from({length:100},(_,i)=>({...requirement.assertions[0],id:'status-'+i}));
    const req={...requirement,assertions};
    await call('record-production-deployment',signed('deployment',{...deployPayload,...other,requirement:req,
      requirement_digest:productionProofDigest(req),released_sha:'e'.repeat(40),provider_version:'next-release',merge_membership:[other.merge_sha]}));
    assert.equal((await status()).state,'unproven');
    await assert.rejects(acquire(),e=>e.payload?.error==='production_proof_deployment_not_current');
    const queries=[];
    const counted={query:(...args)=>{queries.push(args[0]);return c.query(...args);}};
    const result=await executeRegisteredTool(counted,actor,'read-production-proof-status',{repo:other.repo,merge_sha:other.merge_sha,target:other.target});
    assert.equal(result.assertions.length,100);
    assert.equal(queries.length,3);
  });
  await t.test('runner registry is not writable and history is immutable',async()=>{
    for (const query of [
      'update production_proof_record set signature=signature',
      'delete from production_proof_record',
    ]) {
      await assert.rejects(c.query(query),/production_proof_immutable/);
      await c.query('begin');
      await c.query('set local role carr_writer');
      await assert.rejects(c.query(query),/permission denied/);
      await c.query('rollback');
    }
    await assert.rejects(c.query('truncate production_proof_record'),/production_proof_immutable/);
    await c.query('begin');
    await c.query('set local role carr_writer');
    await assert.rejects(c.query("update production_proof_runner set enabled=true"),/permission denied/);
    await c.query('rollback');
    const verbs=await call('list-verbs',{names_only:true,filter:'production-proof'});
    assert.deepEqual(verbs.verbs.map(v=>v.name).sort(),['acquire-production-proof-attempt','read-production-proof-status','record-production-proof-attempt']);
  });
  await t.test('stored signature tampering cannot produce a proven status',async()=>{
    await c.query('begin');
    await c.query('set local role carr_writer');
    await c.query(`insert into production_proof_record
      (kind,repo,merge_sha,target,requirement_digest,payload,payload_digest,runner_id,actor_id,public_key,runner_revision,signature,idempotency_key)
      select kind,repo,merge_sha,target,requirement_digest,jsonb_set(payload,'{provider_version}','"tampered"'),$3,
        runner_id,actor_id,public_key,runner_revision,signature,$2 from production_proof_record where id=$1`,[dep.id,randomUUID(),productionProofDigest({kind:'deployment',payload:{...dep.payload,provider_version:'tampered'}})]);
    await c.query('commit');
    await assert.rejects(status(),e=>e.payload?.error==='production_proof_stored_provenance_invalid');
  });

});
