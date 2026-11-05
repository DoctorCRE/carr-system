import { sha256 } from './sha256.ts';
import { ToolError } from './tool-error.ts';
import { partnerAuthoritySlugForActor } from './partner-authority.js';
import { lockEmailIdentity } from './email-identity.js';

const HASH = { type: 'string', pattern: '^[a-f0-9]{64}$' };
const fail = error => { throw new ToolError({error}); };
const claimsVerification = value => value && typeof value==='object' &&
  Object.entries(value).some(([key,item]) => (key==='verified' && item!==false) || claimsVerification(item));

export async function candidateSignatureLifecycle(c, identity, args, actor, writeEvent) {
  const rows = (await c.query(`select s.*,p.status from email_candidate_signature s join candidate_pool p on p.id=s.pool_id
    where s.mailbox_owner=$1 and s.mailbox_account=$2 and $3=any(s.item_ids) order by p.id,s.id for update of p,s`,
    [identity.mailbox_owner,identity.mailbox_account,identity.item_id])).rows;
  if (!rows.length) return null;
  const observation = {observation_id:args.observation_id,state:args.state,observed_at:new Date(args.observed_at).toISOString(),method:args.method,base_version:args.base_version};
  const prior = rows.find(r=>r.lifecycle)?.lifecycle;
  if (prior) {
    if (prior.observation_id === args.observation_id) {
      if (Object.keys(observation).some(k=>prior[k]!==observation[k])) fail('email_observation_conflict');
      return {ok:true,replayed:true,state:prior.state,version:1,action_notes:[]};
    }
    fail(args.base_version !== 1 ? 'version_conflict' : 'email_lifecycle_terminal');
  }
  if (args.base_version !== 0) fail('version_conflict');
  await c.query(`update email_candidate_signature set invalidated_at=coalesce(invalidated_at,now()),
    invalid_reason=$2,lifecycle=$3 where id=any($1::uuid[])`,[rows.map(r=>r.id),args.state,JSON.stringify({...observation,actor_id:actor.id})]);
  await c.query(`update candidate_pool set source_invalid=true,evidence_version=evidence_version+1,version=version+1
    where id=any($1::uuid[])`,[[...new Set(rows.map(r=>r.pool_id))]]);
  for (const poolId of [...new Set(rows.map(r=>r.pool_id))])
    await writeEvent(c,actor,'record-email-lifecycle','candidate_pool',poolId,
      {new:{...observation,source_invalid:true},cause:'ingest_email',idempotency_key:args.idempotency_key});
  return {ok:true,state:args.state,version:1,action_notes:[]};
}

export function candidateSignatureTools({withEnvelope,writeEvent}) {
  return {
    'observe-email-candidate-signatures': {
      write:true,
      description:'Append deduplicated unverified signature evidence to an existing mailbox/address candidate. Never creates or verifies a party. Source revisions retain invalid history and hold promotion.',
      inputSchema:{type:'object',additionalProperties:false,required:['idempotency_key','address','mailbox_account','observations'],properties:{
        idempotency_key:{type:'string',format:'uuid'},address:{type:'string',format:'email',maxLength:320},
        mailbox_account:{type:'string',format:'email',maxLength:254},
        observations:{type:'array',minItems:1,maxItems:50,items:{type:'object',additionalProperties:false,
          required:['source_id','item_ids','raw_digest','extraction_version','extraction','observed_at'],properties:{
            source_id:HASH,raw_digest:HASH,item_ids:{type:'array',minItems:1,maxItems:50,uniqueItems:true,items:{type:'string',minLength:1,maxLength:500}},
            extraction_version:{type:'string',minLength:1,maxLength:100},extraction:{type:'object'},observed_at:{type:'string',format:'date-time'}
          }}}
      }},
      handler:(c,actor,args)=>withEnvelope(c,actor,'observe-email-candidate-signatures',args,async()=>{
        const owner=partnerAuthoritySlugForActor(actor) ?? fail('email_sender_identity_required');
        const address=args.address.trim().toLowerCase();
        for (const item of [...new Set(args.observations.flatMap(o=>o.item_ids))].sort())
          await lockEmailIdentity(c,owner,args.mailbox_account,item);
        await c.query(`select id from candidate_pool where source='outlook-intake' and (source_key=$1 or id in
          (select pool_id from email_candidate_signature where mailbox_owner=$2 and mailbox_account=$3 and source_id=any($4::text[])))
          order by id for update`,[`email-sha256:${owner}:${sha256(address)}`,owner,args.mailbox_account,args.observations.map(o=>o.source_id)]);
        const row=(await c.query(`select * from candidate_pool where source='outlook-intake' and source_key=$1 for update`,
          [`email-sha256:${owner}:${sha256(address)}`])).rows[0];
        if (!row) fail('email_candidate_not_found');
        if (row.status!=='pool') return {ok:true,contract:'email-candidate-signatures.v1',pool_id:row.id,status:row.status,inserted:0,candidate_version:row.version,evidence_version:row.evidence_version};
        let inserted=0, changed=false;
        for (const observation of args.observations) {
          if (JSON.stringify(observation.extraction).length>20000 || claimsVerification(observation.extraction) || Date.parse(observation.observed_at)>Date.now()+300000)
            fail('email_candidate_evidence_invalid');
          const terminal=(await c.query(`select 1 from activity where email_identity->>'mailbox_owner'=$1
            and email_identity->>'mailbox_account'=$2 and email_identity->>'item_id'=any($3::text[])
            and coalesce(email_context#>>'{lifecycle,state}','live')<>'live'
            union all select 1 from email_candidate_signature where mailbox_owner=$1 and mailbox_account=$2
              and (item_ids && $3::text[] or source_id=$4) and lifecycle is not null limit 1`,[owner,args.mailbox_account,observation.item_ids,observation.source_id])).rows.length;
          if (terminal) fail('email_not_live');
          const invalidated=await c.query(`update email_candidate_signature set invalidated_at=now(),invalid_reason='source_revision'
            where mailbox_owner=$1 and mailbox_account=$2 and source_id=$3 and (raw_digest<>$4 or pool_id<>$5)
              and invalidated_at is null returning id,pool_id`,
            [owner,args.mailbox_account,observation.source_id,observation.raw_digest,row.id]);
          const otherCandidates=[...new Set(invalidated.rows.map(r=>r.pool_id).filter(id=>id!==row.id))];
          if(otherCandidates.length) await c.query(`update candidate_pool set source_invalid=true,version=version+1,
            evidence_version=evidence_version+1 where id=any($1::uuid[])`,[otherCandidates]);
          changed ||= invalidated.rows.length>0 || (Boolean(observation.extraction.ambiguity) && !row.source_invalid);
          const existing=(await c.query(`select id,extraction from email_candidate_signature where pool_id=$1 and mailbox_account=$2
            and source_id=$3 and raw_digest=$4 and extraction_version=$5`,[row.id,args.mailbox_account,observation.source_id,observation.raw_digest,observation.extraction_version])).rows[0];
          if (existing) {
            const equal=(await c.query('select $1::jsonb=$2::jsonb as equal',[existing.extraction,observation.extraction])).rows[0].equal;
            if (!equal && !observation.extraction.ambiguity) fail('email_candidate_observation_conflict');
            if (observation.extraction.ambiguity) await c.query(`update email_candidate_signature
              set invalidated_at=coalesce(invalidated_at,now()),invalid_reason=coalesce(invalid_reason,'source_representation_conflict') where id=$1`,[existing.id]);
            await c.query(`update email_candidate_signature set item_ids=array(select distinct unnest(item_ids||$2::text[])) where id=$1`,[existing.id,observation.item_ids]);
            continue;
          }
          await c.query(`insert into email_candidate_signature(pool_id,candidate_version,mailbox_owner,mailbox_account,source_id,
            item_ids,raw_digest,extraction_version,extraction,observed_at) values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
            [row.id,row.version+1,owner,args.mailbox_account,observation.source_id,observation.item_ids,observation.raw_digest,
              observation.extraction_version,JSON.stringify(observation.extraction),observation.observed_at]);
          inserted++;
        }
        if (inserted || changed) {
          await c.query(`update candidate_pool set version=version+1,evidence_version=evidence_version+1,
            source_invalid=source_invalid or $2,updated_by=$3,updated_at=now() where id=$1`,[row.id,changed,actor.id]);
          await writeEvent(c,actor,'observe-email-candidate-signatures','candidate_pool',row.id,
            {new:{inserted,source_invalid:row.source_invalid||changed,evidence_version:row.evidence_version+1},cause:'ingest_email',idempotency_key:args.idempotency_key});
        }
        return {ok:true,contract:'email-candidate-signatures.v1',pool_id:row.id,status:row.status,inserted,
          duplicates_suppressed:args.observations.length-inserted,source_invalid:row.source_invalid||changed,
          candidate_version:row.version+Number(Boolean(inserted||changed)),evidence_version:row.evidence_version+Number(Boolean(inserted||changed))};
      })
    },
    'read-email-candidate-signatures':{
      description:'Candidate-scoped unverified evidence readback with version tokens and bounded UUID continuation. Invalid history remains visible.',
      inputSchema:{type:'object',additionalProperties:false,required:['pool_id'],properties:{pool_id:{type:'string',format:'uuid'},after:{type:'string',format:'uuid'},limit:{type:'integer',minimum:1,maximum:100}}},
      handler:async(c,actor,args)=>{
        const owner=partnerAuthoritySlugForActor(actor) ?? fail('email_sender_identity_required');
        const limit=args.limit??100;
        const row=(await c.query(`select p.id,p.version,p.evidence_version,p.source_invalid,p.status,
          coalesce((select jsonb_agg(page order by page.id) from (select * from email_candidate_signature
            where pool_id=p.id and ($3::uuid is null or id>$3) order by id limit $4) page),'[]'::jsonb) as observations
          from v_email_candidate_evidence p where p.id=$1 and p.mailbox_owner=$2`,
          [args.pool_id,owner,args.after??null,limit+1])).rows[0];
        if (!row) fail('email_candidate_not_found');
        const observations=row.observations;
        const more=observations.length>limit; if(more) observations.pop();
        return {ok:true,contract:'email-candidate-signatures.v1',pool_id:row.id,status:row.status,candidate_version:row.version,
          evidence_version:row.evidence_version,source_invalid:row.source_invalid,observations,next:more?observations.at(-1).id:null};
      }
    },
    'read-email-candidate-signature-counts':{
      description:'Counts-only mailbox signature evidence census in a half-open observation window. No identity or message content is returned. Production proof must use ordinary intake.',
      inputSchema:{type:'object',additionalProperties:false,required:['since','until'],properties:{since:{type:'string',format:'date-time'},until:{type:'string',format:'date-time'}}},
      handler:async(c,actor,args)=>{
        const owner=partnerAuthoritySlugForActor(actor) ?? fail('email_sender_identity_required');
        if(Date.parse(args.until)<=Date.parse(args.since)) fail('email_candidate_window_invalid');
        const counts=(await c.query(`with scoped as (select s.* from email_candidate_signature s
          where mailbox_owner=$1 and observed_at >= $2 and observed_at < $3), grouped as (
          select pool_id,count(*)::int as observations,count(distinct (mailbox_account,source_id))::int as sources from scoped group by pool_id)
          select (select count(*)::int from scoped) as observations,
          (select count(*)::int from grouped) as candidates,
          (select count(distinct (pool_id,mailbox_account,source_id))::int from scoped) as sources,
          (select count(*)::int from scoped where invalidated_at is not null) as invalidated_observations,
          (select count(distinct (pool_id,mailbox_account,source_id))::int from scoped where invalidated_at is not null) as invalidated_sources,
          (select count(*)::int from (select pool_id,mailbox_account,source_id,raw_digest,extraction_version
            from scoped group by 1,2,3,4,5 having count(*)>1) duplicates) as duplicate_observations,
          coalesce((select jsonb_agg(jsonb_build_object('observations',observations,'sources',sources,'candidates',candidates)) from (
            select observations,sources,count(*)::int as candidates from grouped group by observations,sources order by observations,sources) histogram),'[]'::jsonb) as observations_per_candidate`,
          [owner,args.since,args.until])).rows[0];
        return {ok:true,contract:'email-candidate-signature-counts.v1',...counts};
      }
    }
  };
}
