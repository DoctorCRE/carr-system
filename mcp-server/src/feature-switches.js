import { randomUUID } from 'node:crypto';
import { ToolError } from './tool-error.js';
import { organizationTenantForActor } from './identity.js';
import { partnerAuthoritySlugForActor } from './partner-authority.js';
import { isCalendarDate } from './calendar-date.js';

export const FEATURE_VERBS = Object.freeze({
  'decide-doc-suggestion':'doc-suggestion-actions',
  'propose-doc-correction':'doc-suggestion-actions',
});

export function featureEnabled(row, viewer) {
  if (!row || row.retired_at || (row.enabled ?? row.default_enabled) !== true) return false;
  return row.audience === 'everyone' || row.audience === 'team' && ['joe','dell'].includes(viewer)
    || row.audience === 'joe' && viewer === 'joe';
}

const viewer = actor => partnerAuthoritySlugForActor(actor) || actor.slug;

export async function requireFeature(c, actor, name) {
  const found = await c.query('select * from feature_switch where organization_tenant_id=$1 and name=$2',
    [organizationTenantForActor(actor), name]);
  if (!featureEnabled(found.rows[0], viewer(actor))) throw new ToolError({
    error:'feature_disabled', switch:name, message:`${name} is switched off for you.`, hint:`${name} is switched off for you.`,
  });
}

// Runtime roles hold no table DML on feature_switch; every write goes through this one registered function.
const write = async (c, action, args) => {
  try {
    return (await c.query('select * from ops.write_feature_switch($1,$2::jsonb)',[action,JSON.stringify(args)])).rows[0];
  } catch (error) {
    if (error.code === '40001' && /feature_switch_version_conflict/.test(error.message)) return null;
    throw error;
  }
};

export const FEATURE_SWITCH_WRITES = new Set(['set-feature-switch','flip-feature-switch','check-feature-switches']);
const common = {
  name:{ type:'string', pattern:'^[a-z][a-z0-9-]{0,62}$' },
  idempotency_key:{ type:'string' }, base_version:{ type:'integer', minimum:0 },
};
const audience = { type:'string', enum:['joe','team','everyone'] };
const projection = row => ({ ...row, retirement_loop_id:undefined });

export function featureSwitchTools({ withEnvelope, writeEvent, executeRegisteredTool }) {
  const change = (verb, flip) => async (c, actor, args) => withEnvelope(c, actor, verb, args, async () => {
    if (!/^[a-z][a-z0-9-]{0,62}$/.test(args.name || '') || !Number.isInteger(args.base_version) || args.base_version < 0
      || (args.audience !== undefined && !['joe','team','everyone'].includes(args.audience)))
      throw new ToolError({ error:'feature_switch_input_invalid' });
    if (flip ? typeof args.enabled !== 'boolean' :
      typeof args.default_enabled !== 'boolean' || typeof args.description !== 'string' || !args.description.trim() || args.description.trim().length > 1000
      || !['joe','dell','claude'].includes(args.owner) || !isCalendarDate(args.expected_removal_on)
      || (args.retired !== undefined && typeof args.retired !== 'boolean'))
      throw new ToolError({ error:'feature_switch_input_invalid' });
    const tenant = organizationTenantForActor(actor);
    await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',[`feature-switch:${tenant}:${args.name}`]);
    const prior = (await c.query('select * from feature_switch where organization_tenant_id=$1 and name=$2',
      [tenant,args.name])).rows[0];
    if ((prior?.version || 0) !== args.base_version || flip && !prior)
      throw new ToolError({ error:'feature_switch_version_conflict', current_version:prior?.version || 0 });
    let row;
    if (!prior) {
      if (args.retired === true) throw new ToolError({error:'feature_switch_cannot_create_retired'});
      row = await write(c,'create',{ tenant, name:args.name, description:args.description.trim(), default_enabled:args.default_enabled,
        audience:args.audience, owner:args.owner, expected_removal_on:args.expected_removal_on });
    } else if (flip) {
      if (prior.retired_at) throw new ToolError({ error:'feature_switch_retired' });
      row = await write(c,'flip',{ id:prior.id, base_version:prior.version, enabled:args.enabled, audience:args.audience ?? prior.audience });
    } else {
      row = await write(c,'revise',{ id:prior.id, base_version:prior.version, description:args.description.trim(),
        default_enabled:args.default_enabled, audience:args.audience, owner:args.owner,
        expected_removal_on:args.expected_removal_on, retired:args.retired ?? null });
    }
    if (!row) throw new ToolError({ error:'feature_switch_version_conflict', current_version:prior?.version || 0 });
    await writeEvent(c,actor,verb,'feature_switch',row.id,{
      old:prior ? projection(prior) : null, new:projection(row), idempotency_key:args.idempotency_key,
    });
    return { ok:true, switch:projection(row) };
  });
  return {
    'check-feature-switches':{
      write:true,
      description:'Check overdue feature switch removal dates. Create or update one owner retirement loop per overdue switch and clear it when retired or its reviewed date advances. Does not toggle or deploy a feature.',
      inputSchema:{type:'object',additionalProperties:false,properties:{idempotency_key:{type:'string'}},required:['idempotency_key']},
      handler:async(c,actor,args)=>withEnvelope(c,actor,'check-feature-switches',args,async()=>{
        const tenant=organizationTenantForActor(actor);
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',[`feature-switch-health:${tenant}`]);
        const rows=(await c.query(`select *,expected_removal_on::text as expected_removal_on,
          retired_at is null and expected_removal_on < (now() at time zone 'America/Chicago')::date as overdue
          from feature_switch where organization_tenant_id=$1 order by name`,[tenant])).rows;
        const overdue=[],cleared=[];
        for(const row of rows){
          let loop=row.retirement_loop_id ? (await c.query('select id,number,owner,body,status,version from loop_item where id=$1',[row.retirement_loop_id])).rows[0] : null;
          if(row.overdue){
            const body=`Retire switch ${row.name} and remove its gates after review, or record a reviewed future removal date. Verify list-feature-switches shows retired or a future date and run the consumer tests. Auto-clear when retired or no longer overdue.`;
            if(!loop || loop.status!=='open'){
              const added=await executeRegisteredTool(c,actor,'add-loop',{idempotency_key:randomUUID(),kind:'open_loop',owner:row.owner,
                title:`Retire feature switch ${row.name}`,body,domain:'system',blocker:'other_lane',
                blocker_detail:`Orchestrator review and release of the ${row.name} removal change`});
              loop={id:added.loop_id,number:added.number};
              await write(c,'link_loop',{ id:row.id, retirement_loop_id:loop.id });
            }else if(loop.owner!==row.owner || loop.body!==body){
              await executeRegisteredTool(c,actor,'update-loop',{idempotency_key:randomUUID(),loop_id:loop.id,base_version:loop.version,owner:row.owner,body});
            }
            overdue.push({name:row.name,owner:row.owner,expected_removal_on:row.expected_removal_on,loop_id:loop.id,
              line:`WARN feature switch ${row.name} overdue since ${row.expected_removal_on} · on breach: loop ${loop.number}, owner ${row.owner}; retire switch and remove gates after review or record a reviewed future date; verify list-feature-switches and consumer tests; auto-clear when retired or no longer overdue.`});
          }else if(row.retirement_loop_id){
            if(loop?.status==='open') await executeRegisteredTool(c,actor,'close-loop',{idempotency_key:randomUUID(),loop_id:loop.id,base_version:loop.version,
              outcome:`Feature switch ${row.name} is ${row.retired_at ? 'retired' : 'no longer past its reviewed removal date'}; verified by check-feature-switches.`,resolution:'done'});
            cleared.push(row.retirement_loop_id);
            await write(c,'link_loop',{ id:row.id, retirement_loop_id:null });
          }
        }
        return {ok:true,overdue,cleared,line:`${overdue.length ? 'WARN' : 'OK'} feature switches: ${overdue.length} overdue · on breach: create/update deduplicated owner loop; retire and remove gates after review or record a reviewed future date; verify list-feature-switches and consumer tests; auto-clear when retired or no longer overdue.`};
      }),
    },
    'set-feature-switch':{
      write:true,
      description:'Create or revise a feature switch with plain words, owner and removal date. Version 0 creates; a current version edits or retires. No deploy or release is performed.',
      inputSchema:{ type:'object', additionalProperties:false, properties:{ ...common,
        description:{type:'string',maxLength:1000}, default_enabled:{type:'boolean'}, audience,
        owner:{type:'string',enum:['joe','dell','claude']}, expected_removal_on:{type:'string'}, retired:{type:'boolean'},
      }, required:['idempotency_key','name','base_version','description','default_enabled','audience','owner','expected_removal_on'] },
      handler:change('set-feature-switch',false),
    },
    'flip-feature-switch':{
      write:true,
      description:'Turn one current switch on or off, optionally changing its audience. The existing audit records who, when and before/after. The worker rereads on every request; no deploy is needed.',
      inputSchema:{ type:'object', additionalProperties:false, properties:{ ...common, enabled:{type:'boolean'}, audience },
        required:['idempotency_key','name','base_version','enabled'] },
      handler:change('flip-feature-switch',true),
    },
    'list-feature-switches':{
      description:'Read feature switches and their server-evaluated availability for this authenticated actor. A named switch includes its audit history. Retired switches are unavailable.',
      inputSchema:{ type:'object', additionalProperties:false, properties:{ name:common.name } },
      handler:async (c,actor,args) => {
        const tenant=organizationTenantForActor(actor);
        const found=await c.query(`select *,expected_removal_on::text as expected_removal_on from feature_switch
          where organization_tenant_id=$1 and ($2::text is null or name=$2) order by name`,[tenant,args.name || null]);
        const history=args.name && found.rows.length ? (await c.query(`select a.slug as actor,e.recorded_at,
          e.old_value as before,e.new_value as after,e.verb from event e join actor a on a.id=e.actor_id
          where e.organization_tenant_id=$1 and e.subject_type='feature_switch' and e.subject_id=$2
          order by (e.new_value->>'version')::integer desc limit 100`,[tenant,found.rows[0].id])).rows : [];
        return {ok:true,schema:'feature-switches.v1',switches:found.rows.map(row=>({ ...projection(row),available:featureEnabled(row,viewer(actor)) })),history};
      },
    },
  };
}
