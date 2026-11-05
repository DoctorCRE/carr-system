// Compensate one immutable email application without deleting contact history.
import { organizationTenantForActor } from './identity.js';
import { lockEmailIdentity } from './email-identity.js';

export function emailTouchCorrection({ToolError,withEnvelope,versionGuard,writeEvent}) {
  const properties={idempotency_key:{type:'string',format:'uuid'},original_key:{type:'string',format:'uuid'},
    source_version:{type:'string',minLength:1,maxLength:200},base_version:{type:'integer',minimum:1},
    activity_base_version:{type:'integer',minimum:1},source_base_version:{type:'integer',minimum:1}};
  return {
    write:true,
    description:'Correct one applied email contact by its original application key. Retains original history and records a compensation. Requires immutable source_version and current target, activity and source activity versions. Refuses terminal sources, newer conflicting applications and repeated correction with another key. Exact correction-key retries replay permanently.',
    inputSchema:{type:'object',additionalProperties:false,required:Object.keys(properties),properties},
    handler:(c,actor,args)=>withEnvelope(c,actor,'correct-email-touch',args,async request_hash=>{
      const application=(await c.query(`select id,recorded_at,new_value from event where verb='log-activity'
        and idempotency_key=$1 and actor_id=$2 and organization_tenant_id=$3
        and new_value ? 'email_application'`,[args.original_key,actor.id,organizationTenantForActor(actor)])).rows[0];
      if (!application) throw new ToolError({error:'email_touch_application_not_found'});
      const binding=application.new_value.email_application.binding;
      if (binding.source_version!==args.source_version) throw new ToolError({error:'email_touch_source_conflict'});
      const table=binding.target_type;
      if (!['client','lead','vendor'].includes(table)) throw new ToolError({error:'email_touch_source_conflict'});
      await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',
        [JSON.stringify(['email-touch-source',actor.id,binding.source_id,table,binding.target_id])]);
      const id=application.new_value.activity;
      const initial=(await c.query('select email_identity,email_context from activity where id=$1',[id])).rows[0];
      if (!initial) throw new ToolError({error:'email_touch_source_conflict'});
      const sourceId=initial.email_context?.source_activity_id || id;
      const initialSource=(await c.query('select email_identity from activity where id=$1',[sourceId])).rows[0];
      const identities=[initial.email_identity,initialSource?.email_identity].filter(Boolean);
      for (const identity of identities.sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b))))
        await lockEmailIdentity(c,identity.mailbox_owner,identity.mailbox_account,identity.item_id);
      await versionGuard(c,table,binding.target_id,args.base_version);
      const live=(await c.query(`select 1 from v_ref_index r join party p on p.id=r.party_id
        where r.subject_type=$1 and r.subject_id=$2 and not r.merged and p.deleted_at is null for update of p`,[table,binding.target_id])).rows.length;
      if (!live) throw new ToolError({error:'email_touch_target_not_live'});
      const rows=(await c.query('select * from activity where id=any($1::uuid[]) order by id for update',[[id,sourceId]])).rows;
      const activity=rows.find(r=>r.id===id),source=rows.find(r=>r.id===sourceId);
      if (!activity || !source || (activity.email_context?.source_activity_id || id)!==sourceId)
        throw new ToolError({error:'email_touch_source_conflict'});
      if (activity.touch_correction_key) throw new ToolError({error:'email_touch_already_corrected'});
      if ([activity,source].some(r=>r.email_context?.lifecycle?.state && r.email_context.lifecycle.state!=='live'))
        throw new ToolError({error:'email_not_live'});
      if (activity.version!==args.activity_base_version || source.version!==args.source_base_version)
        throw new ToolError({error:'version_conflict'});
      if (activity[`${table}_id`]!==binding.target_id ||
          ['deal','client','lead','vendor'].some(type=>type!==table && activity[`${type}_id`]!=null) ||
          !['email_in','email_out'].includes(activity.kind) ||
          new Date(activity.occurred_at).toISOString()!==binding.occurred_at)
        throw new ToolError({error:'email_touch_source_conflict'});
      const conflict=(await c.query(`select 1 from event where verb='log-activity' and new_value ? 'email_application'
        and id<>$1 and (new_value->>'activity'=$2 or
          (actor_id=$3 and new_value#>>'{email_application,binding,source_id}'=$4
          and new_value#>>'{email_application,binding,target_type}'=$5
          and new_value#>>'{email_application,binding,target_id}'=$6 and recorded_at >= $7)) limit 1`,
        [application.id,id,actor.id,binding.source_id,table,binding.target_id,application.recorded_at])).rows.length;
      if (conflict) throw new ToolError({error:'email_touch_newer_work'});
      await c.query(`update activity set touch_correction_key=$2,version=version+1,updated_by=$3,updated_at=now() where id=$1`,[id,args.idempotency_key,actor.id]);
      const last=(await c.query('select last_touch from v_last_touch where subject_type=$1 and subject_id=$2',[table,binding.target_id])).rows[0]?.last_touch;
      const response={ok:true,activity_id:id,original_key:args.original_key,correction_key:args.idempotency_key,
        activity_version:activity.version+1,last_touch:last || null};
      await writeEvent(c,actor,'correct-email-touch','activity',id,{new:{email_correction:{binding,
        original_event_id:application.id,source_activity_id:sourceId,request_hash,response}},
        idempotency_key:args.idempotency_key,cause:'human_correction'});
      return response;
    }),
  };
}
