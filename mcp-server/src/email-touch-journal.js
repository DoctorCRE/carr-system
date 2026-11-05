// Exact expectation bytes are committed before any selected email application.
import { canonicalJson } from './artifact-trust.js';
import { mailboxOwnerForActor } from './partner-authority.js';
const ROLES={client:'client',lead:'prospect',vendor:'vendor'};
const uuid={type:'string',format:'uuid'};
const hash={type:'string',pattern:'^[0-9a-f]{64}$'};
const properties={idempotency_key:uuid,principal_slug:{enum:['joe','dell']},producer_sha:{type:'string',pattern:'^[0-9a-f]{40}$'},
 mailbox_scope_hash:hash,execution_id:uuid,source_id:{type:'string',pattern:'^outlook-intake:v1:[0-9a-f]{64}$'},
 source_version:{type:'string',pattern:'^sha256:[0-9a-f]{64}$'},projection_digest:hash,message_hash:hash,
 proposals:{type:'array',minItems:1,maxItems:500,items:{type:'object',additionalProperties:false,
  required:['original_key','ref','kind','occurred_at'],properties:{original_key:uuid,ref:{type:'string',minLength:1,maxLength:500},
  kind:{enum:['email_in','email_out']},occurred_at:{type:'string',format:'date-time'}}}}};

export function emailTouchJournalTools({withEnvelope,ToolError,release=()=>process.env.GIT_SHA,principal=mailboxOwnerForActor}) {
 const require=(ok,error)=>{if(!ok)throw new ToolError({error});};
 return {
  'capture-email-touch-journal':{
   write:true,description:'Freeze all intended supported applications for one natural email before filing. Requires installed producer and Worker SHA agreement. Identical capture UUID replays immutable original bytes; changed intent refuses. No email text is accepted.',
   inputSchema:{type:'object',additionalProperties:false,properties,required:Object.keys(properties)},
   handler:async(c,actor,args)=>{
    require(principal(actor)===args.principal_slug,'email_journal_principal_mismatch');
    require(release()===args.producer_sha,'email_journal_release_mismatch');
    require(args.source_version==='sha256:'+args.projection_digest,'email_journal_projection_mismatch');
    return withEnvelope(c,actor,'capture-email-touch-journal',args,async()=>{
     const read=await c.query('select ops.capture_email_touch_journal($1::jsonb,null) as result',[JSON.stringify(args)]);
     if(read.rows[0].result)return read.rows[0].result;
     const resolved=(await c.query(`with proposals as (select value q from jsonb_array_elements($1::jsonb))
      select q->>'original_key' as original_key,r.subject_type as target_type,r.subject_id::text as target_id
      from proposals join v_ref_index r on (r.ref=q->>'ref' or r.subject_id::text=q->>'ref')
      join party p on p.id=r.party_id where not r.merged and p.deleted_at is null
      and r.subject_type in ('client','lead','vendor')`,[JSON.stringify(args.proposals)])).rows;
     require(resolved.length===args.proposals.length && new Set(resolved.map(r=>r.original_key)).size===resolved.length,'email_journal_target_mismatch');
     const start=args.proposals[0].occurred_at;
     require(new Date(start).toISOString()===start && args.proposals.every(p=>p.occurred_at===start),'email_journal_window_invalid');
     const expected=args.proposals.map(p=>{
      const r=resolved.find(r=>r.original_key===p.original_key);
      return {source_id:args.source_id,source_version:args.source_version,original_key:p.original_key,
       role:ROLES[r.target_type],target_type:r.target_type,target_id:r.target_id,occurred_at:start};
     }).sort((a,b)=>a.original_key.localeCompare(b.original_key));
     const raw=Buffer.from(canonicalJson({schema:'email-touch-expectations.v1',principal_slug:args.principal_slug,
      actor_id:actor.id,actor_slug:actor.slug,tenant:'carr-internal',release_sha:release(),start,
      end:new Date(Date.parse(start)+1).toISOString(),expected_count:expected.length,expected}));
     require(raw.length<=1048576,'email_journal_size');
     return (await c.query('select ops.capture_email_touch_journal($1::jsonb,$2::bytea) as result',[JSON.stringify(args),raw])).rows[0].result;
    });
   },
  },
  'record-email-touch-journal-outcome':{
   write:true,description:'Append bounded producer observations to one frozen journal. Observations do not change expectations or establish canonical application success. Reusing an observation UUID with changed bytes refuses.',
   inputSchema:{type:'object',additionalProperties:false,required:['idempotency_key','reference','observations'],properties:{
    idempotency_key:uuid,reference:{type:'string',pattern:'^sha256:[0-9a-f]{64}$'},
    observations:{type:'array',minItems:1,maxItems:500,items:{type:'object',additionalProperties:false,
     required:['observation_id','original_key','status','activity_id','error_code'],properties:{observation_id:uuid,original_key:uuid,
      status:{enum:['acknowledged','uncertain','refused']},error_code:{type:['string','null'],pattern:'^[a-z][a-z0-9_]{0,63}$'},activity_id:{type:['string','null'],format:'uuid'}}}}}},
   handler:(c,actor,args)=>withEnvelope(c,actor,'record-email-touch-journal-outcome',args,async()=>
    (await c.query('select ops.record_email_touch_journal_outcome($1,$2::jsonb,$3) as result',[args.reference,JSON.stringify(args.observations),release()])).rows[0].result),
  },
 };
}
