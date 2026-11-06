// Read exact activity attribution with bounded, authorized canonical evidence.
import type { Schema } from '@cfworker/json-schema';
import { Validator } from '@cfworker/json-schema';
import { partnerAuthoritySlugForActor } from './partner-authority.js';
import { ToolError } from './tool-error.ts';
import type { Db, Actor } from './verb-contract.ts';

export const ACTIVITY_ATTRIBUTION_PATH = '/api/v1/intelligence/activity-deal-attribution';
const uuid: Schema = { type: 'string', format: 'uuid' };
const version: Schema = { type: 'integer', minimum: 1 };
const object = (properties: Record<string, Schema>): Schema => ({ type: 'object', additionalProperties: false, required: Object.keys(properties), properties });
export const activityAttributionInputSchema = object({ activity_id: uuid });
const activitySchema = object({ id: uuid, version, source_ref: { type: 'string', minLength: 1, maxLength: 500 }, source_version: { type: ['string','null'], maxLength: 200 } });
const candidateSchema = object({ deal_id: uuid, label: { type: 'string', minLength: 1, maxLength: 200 }, deal_version: version,
  evidence: { type: 'array', minItems: 1, maxItems: 101, items: object({ activity_id: uuid, activity_version: version }) } });
const responseValidator = new Validator(object({
  schema: { const: 'activity-deal-attribution.v1' },
  state: { enum: ['ready','needs_input','empty','unavailable'] },
  attribution: { enum: ['direct','source_linked','ambiguous','unresolved','unknown','unavailable'] },
  activity: { anyOf: [activitySchema, { type: 'null' }] },
  direct: { anyOf: [candidateSchema, { type: 'null' }] },
  candidates: { type: 'array', maxItems: 100, items: candidateSchema },
  credited_activity_count: { enum: [0,1] },
  coverage: { enum: ['complete','unknown','unavailable'] },
}), '2020-12', false);
const inputValidator = new Validator(activityAttributionInputSchema, '2020-12', false);
type Activity = { id: string; version: number; source_ref: string; source_version: string | null };
type Candidate = { deal_id: string; label: string; deal_version: number; evidence: { activity_id: string; activity_version: number }[] };
type Response = { schema: 'activity-deal-attribution.v1'; activity: Activity | null; candidates: Candidate[] } & (
  { state: 'ready'; attribution: 'direct'; direct: Candidate; credited_activity_count: 1; coverage: 'complete' } |
  { state: 'needs_input'; attribution: 'source_linked' | 'ambiguous' | 'unresolved'; direct: null; credited_activity_count: 0; coverage: 'complete' | 'unknown' } |
  { state: 'empty'; attribution: 'unknown'; direct: null; credited_activity_count: 0; coverage: 'unknown' } |
  { state: 'unavailable'; attribution: 'unavailable'; direct: null; credited_activity_count: 0; coverage: 'unavailable' });
export function parseActivityAttributionInput(value: unknown): { activity_id: string } {
  if (!inputValidator.validate(value).valid) throw new ToolError({ error: 'QUERY_INVALID' });
  return value as { activity_id: string };
}
export function parseActivityDealAttribution(value: unknown): Response {
  if (!responseValidator.validate(value).valid) throw new ToolError({ error: 'RESPONSE_INVALID' });
  const r = value as Response;
  const invalid = () => { throw new ToolError({ error: 'RESPONSE_INVALID' }); };
  if (r.state === 'ready') {
    if (r.attribution !== 'direct' || r.credited_activity_count !== 1 || !r.direct || !r.activity || r.coverage !== 'complete' || r.candidates.length ||
        !r.direct.evidence.some(e => e.activity_id === r.activity?.id && e.activity_version === r.activity.version)) invalid();
  } else if (r.direct !== null || r.credited_activity_count !== 0) invalid();
  if (r.state === 'needs_input') {
    if (!r.activity || !['complete','unknown'].includes(r.coverage) || r.attribution !== (r.candidates.length > 1 ? 'ambiguous' : r.candidates.length ? 'source_linked' : 'unresolved')) invalid();
  } else if (r.state !== 'ready' && (r.activity !== null || r.candidates.length || r.attribution !== (r.state === 'empty' ? 'unknown' : 'unavailable') || r.coverage !== (r.state === 'empty' ? 'unknown' : 'unavailable'))) invalid();
  if (new Set(r.candidates.map(c => c.deal_id)).size !== r.candidates.length) invalid();
  for (const c of [...r.candidates,...(r.direct ? [r.direct] : [])]) {
    if (!c.label.trim() || new Set(c.evidence.map(e => e.activity_id)).size !== c.evidence.length) invalid();
  }
  return r;
}
const liveDeal = `select d.id,d.name,d.version from public.deal d join public.client c on c.id=d.client_id
 join public.party p on p.id=c.party_id where c.merged_into is null and p.merged_into is null and p.deleted_at is null`;
const visible = (alias: string) => `coalesce(${alias}.email_context#>>'{lifecycle,state}','live')='live'
 and (${alias}.email_identity is null or ${alias}.email_identity->>'mailbox_owner'=$2)
 and (${alias}.email_source is null or ${alias}.email_source->>'mailbox_owner'=$2)
 and (${alias}.deal_id is null or exists(select 1 from live_deals d where d.id=${alias}.deal_id))
 and (${alias}.client_id is null or exists(select 1 from public.client c join public.party p on p.id=c.party_id where c.id=${alias}.client_id and c.merged_into is null and p.merged_into is null and p.deleted_at is null))
 and (${alias}.vendor_id is null or exists(select 1 from public.vendor v join public.party p on p.id=v.party_id where v.id=${alias}.vendor_id and v.merged_into is null and p.merged_into is null and p.deleted_at is null))
 and (${alias}.lead_id is null or exists(select 1 from public.lead l join public.party p on p.id=l.party_id where l.id=${alias}.lead_id and p.merged_into is null and p.deleted_at is null))`;
export const activityAttributionStatement = `with live_deals as (${liveDeal}), root as (
 select a.* from public.activity a where a.id=$1::uuid and ${visible('a')}
), linked as (
 select s.id,s.version,s.deal_id from public.activity s cross join root a
 where a.deal_id is null and s.id<>a.id and (
 s.id::text=a.email_context->>'source_activity_id' or s.email_context->>'source_activity_id'=a.id::text
 or (a.email_context->>'source_activity_id' is not null and s.email_context->>'source_activity_id'=a.email_context->>'source_activity_id'))
 and ${visible('s')} order by s.id limit 101
), candidates as (
 select d.id,d.name,d.version,jsonb_agg(jsonb_build_object('activity_id',s.id,'activity_version',s.version) order by s.id) evidence
 from linked s join live_deals d on d.id=s.deal_id group by d.id,d.name,d.version
) select jsonb_build_object('activity',jsonb_build_object('id',a.id,'version',a.version,'source_ref','activity:'||a.id,
 'source_version',a.email_context->>'source_version'),
 'direct',(select jsonb_build_object('deal_id',d.id,'label',left(d.name,200),'deal_version',d.version,
 'evidence',jsonb_build_array(jsonb_build_object('activity_id',a.id,'activity_version',a.version))) from live_deals d where d.id=a.deal_id),
 'candidates',coalesce((select jsonb_agg(jsonb_build_object('deal_id',id,'label',left(name,200),'deal_version',version,'evidence',evidence) order by id) from candidates),'[]'::jsonb),
 'overflow',(select count(*)>100 from linked),
 'source_missing',a.deal_id is null and a.email_context->>'source_activity_id' is not null and not exists(select 1 from linked s where s.id::text=a.email_context->>'source_activity_id')) snapshot from root a`;

export async function readActivityDealAttribution(c: Db, actor: Actor, value: unknown): Promise<Response> {
  const owner = partnerAuthoritySlugForActor(actor);
  if (!owner) throw new ToolError({ error: 'AUTHORIZATION_REFUSED' });
  const args = parseActivityAttributionInput(value);
  const base = { schema: 'activity-deal-attribution.v1', activity: null, direct: null, candidates: [], credited_activity_count: 0 };
  try {
    const snapshot = (await c.query(activityAttributionStatement, [args.activity_id.toLowerCase(), owner])).rows[0]?.snapshot;
    if (!snapshot) return parseActivityDealAttribution({ ...base, state: 'empty', attribution: 'unknown', coverage: 'unknown' });
    if (snapshot.overflow) throw new Error('source_limit');
    return parseActivityDealAttribution({ ...base, activity: snapshot.activity, direct: snapshot.direct,
      candidates: snapshot.direct ? [] : snapshot.candidates,
      state: snapshot.direct ? 'ready' : 'needs_input',
      attribution: snapshot.direct ? 'direct' : snapshot.candidates.length > 1 ? 'ambiguous' : snapshot.candidates.length ? 'source_linked' : 'unresolved',
      coverage: snapshot.source_missing ? 'unknown' : 'complete', credited_activity_count: snapshot.direct ? 1 : 0 });
  } catch {
    return parseActivityDealAttribution({ ...base, state: 'unavailable', attribution: 'unavailable', coverage: 'unavailable' });
  }
}
