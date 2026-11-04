// Store provider-neutral checkpoints under immutable credential and native session bindings.
import { Validator } from '@cfworker/json-schema';
import { ToolError } from './tool-error.ts';
import { canonicalJson } from './artifact-trust.js';
import { organizationTenantForActor } from './identity.js';

export const SESSION_CHECKPOINT_VERBS = ['write-session-checkpoint-v1', 'read-session-checkpoint-v1', 'archive-session-checkpoint-v1'];
const text = { type: 'string', minLength: 1, maxLength: 1024 };
const nullable = { anyOf: [text, { type: 'null' }] };
const bindingProperties = Object.fromEntries(['provider_instance','environment','native_session_id'].map(k => [k,text]));
Object.assign(bindingProperties, {
 provider: { enum: ['claude','codex','grok'] }, harness: { enum: ['hooks','app-server','acp'] },
 role: { enum: ['coordinator','user','builder','reviewer','verifier','t3-worker','native-subagent','remote-worker'] },
 ...Object.fromEntries(['thread_id','task_id','parent_destination','card','worktree','repository_revision'].map(k=>[k,nullable])),
 lease_refs: { type: 'array', maxItems: 32, items: text },
});
export const sessionBindingSchema = { type:'object', additionalProperties:false, properties:bindingProperties, required:Object.keys(bindingProperties) };
const list = { type:'array', maxItems:64, items:{type:'string',maxLength:32768} };
const checkpointProperties = Object.fromEntries(['acceptance','corrections','decisions','evidence_refs','active_operations','exact_jobs','destinations','watches','links','revisions','cards','leases'].map(k=>[k,list]));
checkpointProperties.next_action = { type:'string',maxLength:32768 };
const integer = {type:'integer',minimum:0,maximum:0x7ffffffe};
const envelopeProperties = {
 schema_version:{const:1}, binding:sessionBindingSchema, kind:{enum:['usage','before_compact']}, payload:{type:'object'},
 observed_at:integer, now:integer, epoch:integer, epoch_evidence:text, cursor:integer, expected_generation:integer,
 model_fingerprint:text, usage_model_fingerprint:text, auto_compact_at:integer,
 checkpoint:{type:'object',additionalProperties:false,properties:checkpointProperties,required:Object.keys(checkpointProperties)},
};
export const checkpointEnvelopeSchema = {type:'object',additionalProperties:false,properties:envelopeProperties,
 required:Object.keys(envelopeProperties).filter(k=>k!=='auto_compact_at')};
const base={binding:sessionBindingSchema};
function schema(properties) { return {type:'object',additionalProperties:false,properties,required:Object.keys(properties)}; }
const writeSchema=schema({...base,idempotency_key:{type:'string',format:'uuid'},envelope:checkpointEnvelopeSchema});
const archiveSchema=schema({...base,idempotency_key:{type:'string',format:'uuid'},expected_generation:integer});
const readSchema=schema(base);
export function sessionCheckpointTools() {
 const command=(name,inputSchema,write,operation)=>({
  description:`Provider-neutral scoped continuity ${operation}, contract session-checkpoint.v1. Native task execution and delivery remain with T3.`,
  inputSchema, ...(write?{write:true}:{}), writerConnection:true,
  handler:async(c,actor,args)=>{
   if(!new Validator(structuredClone(inputSchema)).validate(args).valid || Buffer.byteLength(JSON.stringify(args))>70000)
    throw new ToolError({error:'invalid_session_checkpoint'});
   const binding=args.binding;
   const harnesses={claude:['hooks'],codex:['hooks','app-server'],grok:['hooks','acp']};
   if(!harnesses[binding.provider].includes(binding.harness) ||
      (binding.role==='t3-worker' && ['thread_id','task_id','parent_destination','card','worktree','repository_revision'].some(k=>binding[k]===null)))
    throw new ToolError({error:'invalid_session_checkpoint_binding'});
   const authority=actor?.session_checkpoint_authority;
   if(actor?.via!=='session-checkpoint-token'||!authority || canonicalJson(authority.binding)!==canonicalJson(args.binding))
    throw new ToolError({error:'session_checkpoint_identity_required'});
   if(args.envelope && (canonicalJson(args.envelope.binding)!==canonicalJson(args.binding) || Buffer.byteLength(JSON.stringify(args.envelope.checkpoint))>32768))
    throw new ToolError({error:'session_checkpoint_binding_mismatch'});
   const result=await c.query('select ops.session_checkpoint_v1($1,$2,$3,$4,$5::jsonb) result',
    [organizationTenantForActor(actor),authority.sponsor,authority.authority_session,operation,JSON.stringify(args)]);
   const value=result.rows[0]?.result;
   if(!value?.ok) throw new ToolError({error:value?.error||'session_checkpoint_unavailable'});
   return value;
  },
 });
 return Object.fromEntries([
  [SESSION_CHECKPOINT_VERBS[0],command(SESSION_CHECKPOINT_VERBS[0],writeSchema,true,'write')],
  [SESSION_CHECKPOINT_VERBS[1],command(SESSION_CHECKPOINT_VERBS[1],readSchema,false,'read')],
  [SESSION_CHECKPOINT_VERBS[2],command(SESSION_CHECKPOINT_VERBS[2],archiveSchema,true,'archive')],
 ]);
}
