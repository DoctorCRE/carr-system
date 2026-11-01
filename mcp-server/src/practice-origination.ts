// Store dated public-source practice evidence and expose review-only contact reasons.
import sources from './origination-sources.json' with { type: 'json' };
import { Validator, type Schema } from '@cfworker/json-schema';
import { canonicalJson } from './artifact-trust.js';
import { sha256 } from './sha256.ts';
import { isCalendarDate } from './calendar-date.ts';
import { ToolError } from './tool-error.ts';
import type { Actor, Db, VerbInputs, WithEnvelope } from './verb-contract.ts';

const florida = sources.find(source=>source.source==='formation-fl');
const healthcareName = new RegExp(florida?.healthcare_name_pattern ?? '(?!)', 'i');

const text = (maxLength: number): Schema => ({ type: 'string', minLength: 1, maxLength });
const recordProperties = {
  record_key: text(150),
  name: text(300), city: {type:'string', maxLength:200}, state: {type:'string',enum:['AL','FL']},
  evidence_date: {type:'string',format:'date'},
  date_basis: {type:'string',enum:['enumeration','registry_update','publication','filing']},
  source_url: {type:'string',format:'uri',maxLength:1000},
  facts: {type:'object',additionalProperties:false,properties:{
    address: {type:'string',maxLength:500}, specialty:{type:'string',maxLength:2000},
    mailing_only:{type:'boolean'}, enrollment_id:text(150), provider_type:text(200),
    registered_agent_address:{type:'string',maxLength:500}, healthcare_name_match:{type:'boolean'},
    postal_code:{type:'string',pattern:'^[0-9]{5}(-[0-9]{4})?$'},
  }},
} satisfies Record<string, Schema>;
const recordRequired = ['record_key','name','city','state','evidence_date','date_basis','source_url','facts'];
const recordSchema: Schema = {anyOf:[
  {type:'object',additionalProperties:false,properties:{...recordProperties,
    npi:{type:'string',pattern:'^[0-9]{10}$'}},required:[...recordRequired,'npi']},
  {type:'object',additionalProperties:false,properties:{...recordProperties,
    document_number:{type:'string',pattern:'^[A-Z0-9]{12}$'},filing_on:{type:'string',format:'date'}},
    required:[...recordRequired,'document_number','filing_on']},
]};
const importSchema: Schema = {type:'object',additionalProperties:false,properties:{
  idempotency_key:text(200), source:{type:'string',enum:['nppes','pecos','formation-fl']},
  expected_checkpoint:{type:['string','null'],maxLength:200}, checkpoint:text(200),
  published_on:{type:'string',format:'date'}, status:{type:'string',enum:['available','blocked','unavailable']},
  detail:text(500), records:{type:'array',maxItems:100,items:recordSchema},
},required:['idempotency_key','source','expected_checkpoint','checkpoint','published_on','status','detail','records']};
const readSchema: Schema = {type:'object',additionalProperties:false,properties:{
  limit:{type:'integer',minimum:1,maximum:100}, before_id:{type:'string',pattern:'^[1-9][0-9]{0,18}$'},
}};
const importValidator = new Validator(importSchema);
const readValidator = new Validator(readSchema);
const fail = (error: string): never => {throw new ToolError({error});};

type Import = VerbInputs['record-practice-origination-feed'];
type Read = VerbInputs['list-practice-origination'];
export type OriginationRecord = Import['records'][number];

function validateImport(args: Import) {
  if (!importValidator.validate(args).valid || !isCalendarDate(args.published_on) ||
      args.published_on > new Date().toISOString().slice(0,10) ||
      (args.status !== 'available' && args.records.length)) fail('origination_input_invalid');
  if (!sources.find(source=>source.source===args.source)?.enabled) fail('origination_source_disabled');
  const keys = new Set<string>();
  for (const row of args.records) {
    if (!isCalendarDate(row.evidence_date) || row.evidence_date > args.published_on ||
        !row.name.trim() || !row.record_key.trim() || keys.has(row.record_key)) fail('origination_input_invalid');
    keys.add(row.record_key);
    const url = new URL(row.source_url);
    if (url.protocol !== 'https:' || url.username || url.password) fail('origination_input_invalid');
    if (args.source === 'formation-fl') {
      if (!('document_number' in row) || !row.document_number || row.record_key !== row.document_number ||
          !row.filing_on || !isCalendarDate(row.filing_on) || row.evidence_date !== row.filing_on ||
          row.date_basis !== 'filing' || row.state !== 'FL' || !row.facts.healthcare_name_match ||
          !florida?.territory_zip_prefixes?.includes(row.facts.postal_code?.slice(0,3) ?? '') || !healthcareName.test(row.name) ||
          url.href !== 'https://dos.fl.gov/sunbiz/other-services/data-downloads/daily-data/') fail('origination_input_invalid');
    } else if (!('npi' in row) || !row.npi ||
        (args.source === 'nppes' ? url.hostname !== 'npiregistry.cms.hhs.gov' ||
           url.pathname !== `/provider-view/${row.npi}` || row.record_key !== row.npi ||
           !['enumeration','registry_update'].includes(row.date_basis) :
           url.hostname !== 'data.cms.gov' || row.date_basis !== 'publication' ||
           row.evidence_date !== args.published_on || !row.facts.enrollment_id ||
           row.record_key !== `${row.npi}|${row.facts.enrollment_id}`)) fail('origination_input_invalid');
  }
}

export function practiceOriginationTools({withEnvelope}: {withEnvelope:WithEnvelope}) {
  return {
    'record-practice-origination-feed': {
      write:true,
      description:'Import at most 100 prepared NPPES, PECOS or enabled Florida corporate-file observations with an atomic checkpoint. Echo expected_checkpoint from list-practice-origination coverage. Changed PECOS content is a correction dated by publication, never enrollment inferred from an ID. Same source record/content produces no second event or card. Report schema drift as blocked with no records; unsuccessful imports preserve the last good checkpoint. Only evidence is written. Never sends mail, creates or merges a party, or promotes a lead. Florida imports remain disabled until the declared field layout is verified; recorder imports remain disabled. Healthcare names are research candidates, never verified practice openings.',
      inputSchema:importSchema,
      handler:(c:Db,actor:Actor,args:Import) => withEnvelope(c,actor,'record-practice-origination-feed',args,async () => {
        validateImport(args);
        await c.query('insert into practice_origination_feed(source) values($1) on conflict do nothing',[args.source]);
        const feed = (await c.query('select checkpoint,published_on::text from practice_origination_feed where source=$1 for update',[args.source])).rows[0];
        if (!feed || feed.checkpoint !== args.expected_checkpoint) fail('origination_checkpoint_conflict');
        if (feed.published_on && args.published_on < String(feed.published_on).slice(0,10)) fail('origination_stale_publication');
        let inserted = 0;
        for (const row of args.records) {
          // Publication vintage and links may change while the underlying evidence stays identical.
          const {source_url, ...content} = row;
          const fingerprint = sha256(canonicalJson({...content,
            ...(args.source === 'pecos' ? {evidence_date:null} : {})}));
          const previous = (await c.query(`select fingerprint from practice_origination_event
            where source=$1 and record_key=$2 order by id desc limit 1`,[args.source,row.record_key])).rows[0];
          if (previous?.fingerprint === fingerprint) continue;
          const kind = args.source === 'formation-fl' ? 'formation_candidate' :
            args.source === 'nppes' ? row.date_basis : previous ? 'pecos_correction' : 'pecos_observation';
          const result = await c.query(`insert into practice_origination_event
            (source,record_key,fingerprint,npi,name,city,state,kind,evidence_date,date_basis,source_url,facts,published_on,recorded_by,document_number,filing_on)
            values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14,$15,$16)
            on conflict(source,record_key,published_on,fingerprint) do nothing returning id`,
            [args.source,row.record_key,fingerprint,'npi' in row ? row.npi : null,row.name,row.city,row.state,kind,row.evidence_date,
              row.date_basis,source_url,JSON.stringify(row.facts),args.published_on,actor.id,'document_number' in row ? row.document_number : null,
              'filing_on' in row ? row.filing_on : null]);
          inserted += result.rows.length;
        }
        await c.query(`update practice_origination_feed set status=$2,detail=$3,updated_at=now(),
          checkpoint=case when $2='available' then $4 else checkpoint end,
          published_on=case when $2='available' then $5::date else published_on end where source=$1`,
          [args.source,args.status,args.detail,args.checkpoint,args.published_on]);
        return {ok:true,contract:'practice-origination.v1',inserted,
          checkpoint:args.status==='available' ? args.checkpoint : feed.checkpoint,status:args.status};
      }),
    },
    'list-practice-origination': {
      write:false,
      description:'Read dated public-source reasons to contact a practice and explicit source coverage. One card per source record, latest evidence first; before_id pages the latest-card projection. Exact unique NPI/client matches route to relationship_follow_up. Possible name/location or ambiguous matches require identity_review. Routes are current read-time relationship evidence, never authority to create or merge a party or send outreach. Publication/registry dates do not prove practice opening. Disabled/unimported sources report unavailable; other counties uncovered.',
      inputSchema:readSchema,
      handler:async (c:Db,_actor:Actor,args:Read) => {
        if (!readValidator.validate(args).valid) fail('origination_input_invalid');
        const limit = args.limit ?? 25;
        const feeds = (await c.query('select source,checkpoint,published_on::text,status,detail,updated_at from practice_origination_feed')).rows;
        const rows = (await c.query(`with latest as (
            select distinct on (source,record_key) * from practice_origination_event order by source,record_key,id desc
          ), page as materialized (
            select * from latest where ($1::bigint is null or id<$1::bigint) order by id desc limit $2
          ) select e.id::text,e.source,e.record_key,e.npi,e.name,e.city,e.state,e.kind,
            e.document_number,e.filing_on::text,e.evidence_date::text,e.date_basis,e.source_url,e.facts,e.published_on::text,e.recorded_at,
            case when e.source='formation-fl' and (
              coalesce(e.facts->>'registered_agent_address','')<>'' and (
                practice_address_key(e.facts->>'registered_agent_address')=practice_address_key(e.facts->>'address') or exists (
                  select 1 from latest other where other.source='formation-fl' and other.record_key<>e.record_key
                    and practice_address_key(other.facts->>'registered_agent_address')=
                      practice_address_key(e.facts->>'registered_agent_address'))))
              then 'identity_review' when m.parties=1 and m.exact=1 and m.clients=1 and not coalesce((e.facts->>'mailing_only')::boolean,false)
              then 'relationship_follow_up' when m.parties>0 then 'identity_review' else 'research' end as route,
            m.client_refs,m.client_ids,
            case e.kind when 'formation_candidate' then 'Healthcare-name research candidate filed'
              when 'enumeration' then 'NPI enumerated'
              when 'registry_update' then 'NPPES registry updated'
              when 'pecos_correction' then 'Public PECOS evidence corrected'
              else 'Public PECOS enrollment observed' end || ' on ' || e.evidence_date::text || case when e.source='formation-fl'
                then '; practice identity and opening unverified' else '' end as why_now
          from page e left join lateral (
            select count(distinct p.id)::int parties,
              count(distinct p.id) filter(where p.npi=e.npi)::int exact,
              count(distinct cl.id)::int clients,
              coalesce(array_agg(distinct cl.roster_ref) filter(where cl.roster_ref is not null),'{}') client_refs,
              coalesce(array_agg(distinct cl.id) filter(where cl.id is not null),'{}') client_ids
            from party p left join client cl on cl.party_id=p.id and cl.merged_into is null
            where p.merged_into is null and p.deleted_at is null and
              (p.npi=e.npi or (lower(p.name)=lower(e.name) and lower(coalesce(p.city,''))=lower(e.city)
                and p.state=e.state) or (length(coalesce(e.facts->>'address',''))>0 and p.state=e.state
                and practice_address_key((select a.street_address from v_practice_origination_party_address a
                  where a.party_id=p.id))=practice_address_key(e.facts->>'address')))
          ) m on true order by e.id desc`,[args.before_id ?? null,limit+1])).rows;
        const cards = rows.slice(0,limit);
        return {ok:true,contract:'practice-origination.v1',cards,
          next_cursor:rows.length>limit ? cards.at(-1)?.id : null,
          coverage:sources.map(source => ({...source,
            ...(source.enabled ? feeds.find(f=>f.source===source.source) ?? {status:'unavailable',checkpoint:null} :
              {status:source.source==='formation-fl' ? 'unavailable' : source.coverage,checkpoint:null})})),outreach_started:false};
      },
    },
  };
}
