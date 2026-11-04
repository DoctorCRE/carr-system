// Validate signed production observations and derive current target proof status.
import { Validator } from '@cfworker/json-schema';
import plan from '../../contracts/production-proof-plan.v1.json' with { type: 'json' };
import { sha256 } from './sha256.ts';

const object = properties => ({type:'object',additionalProperties:false,properties,required:Object.keys(properties)});
const text = {type:'string',minLength:1,maxLength:500};
const digest = {type:'string',pattern:'^sha256:[0-9a-f]{64}$'};
const revision = {type:'string',pattern:'^[0-9a-f]{40}$'};
const uuid = {type:'string',format:'uuid'};
const target = {enum:['worker','app','studio']};
const repo = {enum:['DoctorCRE/carr-system','jbookout/doctorcre-app']};
const artifact = object({ref:{type:'string',pattern:'^artifact://[A-Za-z0-9/._-]+$',maxLength:500},digest,
  bytes:{type:'integer',minimum:1,maximum:1048576}});
const binding = {repo,merge_sha:revision,target,requirement_digest:digest};
const planSchema=Object.fromEntries(Object.entries(plan).filter(([key])=>!key.startsWith('$') && !key.startsWith('x-')));
const deployment = object({...binding,released_sha:revision,provider_version:text,
  merge_membership:{type:'array',minItems:0,maxItems:1000,uniqueItems:true,items:revision},
  observed_at:{type:'string',format:'date-time'},artifact,
  requirement:planSchema});
const reservation = object({...binding,deployment_id:uuid,assertion_id:text});
const receipt = object({...reservation.properties,attempt_id:uuid,released_sha_before:revision,released_sha_after:revision,
  result:{enum:['pass','fail','blocked']},reason:{enum:['observed','assertion_failed','missing_login','missing_runtime','dependency_unavailable','budget_exceeded']},
  started_at:{type:'string',format:'date-time'},finished_at:{type:'string',format:'date-time'},
  artifacts:{type:'array',minItems:1,maxItems:10,items:artifact},
  metrics:object({requests:{type:'integer',minimum:0,maximum:100},records:{type:'integer',minimum:0,maximum:1000}})});
const schemas = {deployment,reservation,receipt};
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value==='object'
  ? Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])) : value;
export const productionProofMessage = (kind,payload) => JSON.stringify(canonical({schema:'carr-production-proof-signature.v1',kind,payload}));
export const productionProofDigest = value => 'sha256:'+sha256(JSON.stringify(canonical(value)));
const bytes = value => Uint8Array.from(atob(value),c=>c.charCodeAt(0));
const validators = Object.fromEntries(Object.entries(schemas).map(([key,schema])=>[key,new Validator(schema)]));

async function validStored(record) {
  if (!record || !validators[record.kind]?.validate(record.payload).valid ||
    record.payload_digest!==productionProofDigest({kind:record.kind,payload:record.payload})) return false;
  try {
    const key=await crypto.subtle.importKey('raw',bytes(record.public_key),{name:'Ed25519'},false,['verify']);
    return await crypto.subtle.verify('Ed25519',key,bytes(record.signature),new TextEncoder().encode(productionProofMessage(record.kind,record.payload)));
  } catch {return false;}
}

export function productionProofTools({ToolError}) {
  const refuse = reason => {throw new ToolError({error:'production_proof_'+reason});};
  const lock = async (c,key) => c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',['production-proof:'+key]);
  const latest = async (c,p) => (await c.query(`select * from production_proof_record
    where kind='deployment' and repo=$1 and merge_sha=$2 and target=$3 order by sequence desc limit 1`,
    [p.repo,p.merge_sha,p.target])).rows[0];
  const currentRelease = async (c,p) => (await c.query(`select payload->>'released_sha' as sha,payload->>'provider_version' as version,payload->>'observed_at' as observed_at
    from production_proof_record where kind='deployment' and repo=$1 and target=$2 order by sequence desc limit 1`,[p.repo,p.target])).rows[0];
  const matchesRelease = (d,r) => d.payload.released_sha===r?.sha && d.payload.provider_version===r?.version;
  const row = async (c,id,kind) => (await c.query('select * from production_proof_record where id=$1 and kind=$2',[id,kind])).rows[0];
  const bindingKey = p => JSON.stringify([p.repo,p.target]);
  const sameBinding = (a,b) => Object.keys(binding).every(key=>a[key]===b[key]);
  async function authenticate(c,actor,args,kind) {
    const runner=(await c.query(`select * from production_proof_runner where id=$1 and actor_id=$2 and enabled`,[args.runner_id,actor.id])).rows[0];
    if (!runner || actor.human===true || !runner.operations.includes(kind)) refuse('untrusted_runner');
    if (!validators[kind].validate(args.payload).valid || new TextEncoder().encode(JSON.stringify(args.payload)).length>131072) refuse('invalid_payload');
    const p=args.payload;
    if (runner.repo!==p.repo || runner.target!==p.target) refuse('runner_binding');
    try {
      const key=await crypto.subtle.importKey('raw',bytes(runner.public_key),{name:'Ed25519'},false,['verify']);
      if (!await crypto.subtle.verify('Ed25519',key,bytes(args.signature),new TextEncoder().encode(productionProofMessage(kind,p)))) refuse('signature_invalid');
    } catch {refuse('signature_invalid');}
    return runner;
  }
  async function store(c,actor,args,kind,runner,attempt=null) {
    const p=args.payload;
    const result=(await c.query(`insert into production_proof_record
      (kind,repo,merge_sha,target,requirement_digest,deployment_id,attempt_id,assertion_id,payload,payload_digest,
       runner_id,actor_id,public_key,runner_revision,signature,idempotency_key)
      values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) returning *`,
    [kind,p.repo,p.merge_sha,p.target,p.requirement_digest,p.deployment_id??null,attempt,p.assertion_id??null,
      p,productionProofDigest({kind,payload:p}),runner.id,actor.id,runner.public_key,runner.runner_revision,args.signature,args.idempotency_key])).rows[0];
    return {ok:true,receipt:result};
  }
  async function write(c,actor,args,kind) {
    const runner=await authenticate(c,actor,args,kind);
    const p=args.payload;
    await lock(c,actor.id+args.idempotency_key);
    const prior=(await c.query('select * from production_proof_record where actor_id=$1 and idempotency_key=$2',[actor.id,args.idempotency_key])).rows[0];
    if (prior) {
      if (prior.payload_digest!==productionProofDigest({kind,payload:p}) || prior.runner_id!==runner.id || prior.signature!==args.signature) refuse('key_reuse');
      return {ok:true,replayed:true,receipt:prior};
    }
    await lock(c,bindingKey(p));
    if (kind==='deployment') {
      if (p.requirement_digest!==productionProofDigest(p.requirement)) refuse('requirement_digest');
      if (!p.requirement.targets.includes(p.target)) refuse('target_mismatch');
      const ids=new Set();
      for (const assertion of p.requirement.assertions) {
        if (ids.has(assertion.id) || !plan['x-runners'][assertion.runner]) refuse('requirement_invalid');
        ids.add(assertion.id);
      }
      if (Date.parse(p.observed_at)>Date.now()+60000) refuse('observation_time');
      const current=await currentRelease(c,p);
      if (current && (Date.parse(p.observed_at)<Date.parse(current.observed_at) ||
        (!matchesRelease({payload:p},current) && Date.parse(p.observed_at)===Date.parse(current.observed_at)))) refuse('deployment_order');
      return store(c,actor,args,kind,runner);
    }
    const deployed=await row(c,p.deployment_id,'deployment');
    if (!deployed || !sameBinding(p,deployed)) refuse('deployment_binding');
    if ((await latest(c,p))?.id!==deployed.id || !matchesRelease(deployed,await currentRelease(c,p)) || !deployed.payload.merge_membership.includes(p.merge_sha)) refuse('deployment_not_current');
    const requirement=deployed.payload.requirement;
    const assertion=requirement.assertions.find(a=>a.id===p.assertion_id);
    const registered=assertion && plan['x-runners'][assertion.runner];
    if (!assertion || runner.id!==assertion.runner || runner.runner_revision!==registered?.revision || !registered.targets.includes(p.target)) refuse('assertion_runner_binding');
    if (kind==='reservation') return store(c,actor,args,kind,runner);
    const attempt=await row(c,p.attempt_id,'reservation');
    if (!attempt || !sameBinding(attempt,p) || attempt.deployment_id!==p.deployment_id || attempt.assertion_id!==p.assertion_id || attempt.runner_id!==runner.id || attempt.actor_id!==actor.id || attempt.public_key!==runner.public_key) refuse('attempt_binding');
    const previous=(await c.query('select * from production_proof_record where attempt_id=$1',[attempt.id])).rows[0];
    if (previous) {
      if (previous.payload_digest!==productionProofDigest({kind,payload:p}) || previous.signature!==args.signature) refuse('attempt_conflict');
      return {ok:true,replayed:true,receipt:previous};
    }
    if (p.released_sha_before!==deployed.payload.released_sha || p.released_sha_after!==deployed.payload.released_sha) refuse('artifact_binding');
    const start=Date.parse(p.started_at),end=Date.parse(p.finished_at);
    if (start<new Date(attempt.recorded_at).getTime()-1000 || start<Date.parse(deployed.payload.observed_at) || end<start || end>Date.now()+60000) refuse('observation_time');
    if ((p.result==='pass' && p.reason!=='observed') || (p.result==='fail' && p.reason!=='assertion_failed') ||
      (p.result==='blocked' && ['observed','assertion_failed'].includes(p.reason))) refuse('result_reason');
    const safety=requirement.safety;
    if (end-start>Math.min(assertion.deadline_seconds,safety.max_runtime_seconds)*1000 ||
      p.metrics.requests>safety.max_requests || p.metrics.records>safety.max_records ||
      p.artifacts.reduce((n,a)=>n+a.bytes,0)>safety.max_artifact_bytes) refuse('budget_exceeded');
    return store(c,actor,args,kind,runner,attempt.id);
  }
  const tools={};
  for (const [verb,kind] of Object.entries({'record-production-deployment':'deployment','acquire-production-proof-attempt':'reservation','record-production-proof-attempt':'receipt'})) {
    tools[verb]={write:true,description:`Append a signed ${kind} for a provisioned production proof runner. Builders cannot self-attest; preserves immutable provenance.`,
      inputSchema:object({idempotency_key:uuid,runner_id:text,signature:{type:'string',maxLength:88,minLength:88},payload:schemas[kind]}),
      handler:(c,actor,args)=>write(c,actor,args,kind)};
  }
  tools['read-production-proof-status']={description:'Read current production evidence and exact immutable receipts. A newer reservation or release invalidates older proven status; does not authorize closure.',
    inputSchema:object({repo,merge_sha:revision,target}),handler:async(c,actor,args)=>{
      const deployed=await latest(c,args);
      if (!deployed) return {ok:true,state:'unproven',deployment:null,assertions:[]};
      if (!await validStored(deployed) || deployed.requirement_digest!==productionProofDigest(deployed.payload.requirement)) refuse('stored_provenance_invalid');
      const requirement=deployed.payload.requirement;
      const assertions=requirement.assertions.filter(a=>plan['x-runners'][a.runner]?.targets.includes(args.target));
      const rows=(await c.query(`select distinct on (a.assertion_id) a.assertion_id,a.id as attempt_id,a.sequence,a.recorded_at,r.payload,r.id as receipt_id,
        to_jsonb(a) as attempt,to_jsonb(r) as receipt from production_proof_record a left join production_proof_record r on r.attempt_id=a.id
        where a.kind='reservation' and a.deployment_id=$1 order by a.assertion_id,a.sequence desc`,[deployed.id])).rows;
      const coverage=[];
      for (const assertion of assertions) {
        const found=rows.find(r=>r.assertion_id===assertion.id);
        if (found && (!await validStored(found.attempt) || (found.receipt && (!await validStored(found.receipt) ||
          found.receipt.payload.attempt_id!==found.attempt.id || !sameBinding(found.receipt.payload,deployed) ||
          found.receipt.payload.deployment_id!==deployed.id || found.receipt.payload.assertion_id!==assertion.id)))) refuse('stored_provenance_invalid');
        coverage.push({assertion_id:assertion.id,result:found?.payload?.result??'unproven',attempt_id:found?.attempt_id??null,receipt:found?.receipt??null});
      }
      const contains=deployed.payload.merge_membership.includes(args.merge_sha) && matchesRelease(deployed,await currentRelease(c,args));
      const state=!contains || !coverage.length ? 'unproven' : coverage.some(a=>a.result==='fail') ? 'failed'
        : coverage.some(a=>a.result==='blocked') ? 'blocked' : coverage.every(a=>a.result==='pass') ? 'proven' : coverage.some(a=>a.attempt_id) ? 'proving' : 'deployed';
      return {ok:true,state,deployment:deployed,assertions:coverage};
    }};
  return tools;
}
