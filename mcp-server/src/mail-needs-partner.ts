// Rank recorded pending decisions for the authenticated partner mailbox.
import { partnerAuthoritySlugForActor } from './partner-authority.js';
import { ToolError } from './tool-error.ts';

type Actor = Parameters<typeof partnerAuthoritySlugForActor>[0];
type Row = { id: string; version: number; kind: string; activity_id: string; source_version: number;
  next_date: string | null; occurred_at: Date | string | null; priority: number };
type Client = { query: (sql: string, values: unknown[]) => Promise<{ rows: Row[] }> };
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const date = /^\d{4}-\d{2}-\d{2}$/;
const instant = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value;
const invalid = () => { throw new ToolError({ error: 'mail_needs_partner_arguments_invalid' }); };
export const mailNeedsPartnerInputSchema = { type: 'object', additionalProperties: false, properties: {
  limit: { type: 'integer', minimum: 1, maximum: 50 }, cursor: { type: 'string', minLength: 1, maxLength: 1024 },
} };
export function parseMailNeedsPartnerQuery(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  const keys = Object.keys(value);
  if (keys.some(key => !['limit','cursor'].includes(key))) return invalid();
  const limit = 'limit' in value ? value.limit : 25;
  const cursor = 'cursor' in value ? value.cursor : null;
  if (typeof limit !== 'number' || !Number.isInteger(limit) || limit < 1 || limit > 50 ||
      (cursor !== null && (typeof cursor !== 'string' || !cursor.length || cursor.length > 1024))) return invalid();
  return { limit, cursor };
}
const actions: Record<string, string> = {
  file_to_deal: 'confirm-email-filing', possible_new_deal: 'confirm-email-filing',
  next_step: 'confirm-email-next-step', phase_move: 'confirm-email-deal-progress', reply_draft: 'submit-email-draft',
};
export function mailNeedsPartnerOwner(actor: Actor) {
  const owner = partnerAuthoritySlugForActor(actor);
  if (!owner) throw new ToolError({ error: 'mail_needs_partner_unauthorized' });
  return owner;
}
export async function readMailNeedsPartner(c: Client, actor: Actor, input: unknown, now = new Date()) {
  const args = parseMailNeedsPartnerQuery(input);
  const owner = mailNeedsPartnerOwner(actor);
  let after: string | null = null;
  let day = now.toISOString().slice(0,10);
  if (args.cursor) {
    try {
      const saved = JSON.parse(atob(args.cursor));
      if (saved.v !== 1 || saved.owner !== owner || !uuid.test(saved.id) || !date.test(saved.day) ||
          new Date(saved.day).toISOString().slice(0,10) !== saved.day || saved.day > day) return invalid();
      after = saved.id; day = saved.day;
    } catch { return invalid(); }
  }
  const coverage = { scope: 'recorded_inbound_pending_proposals', mailbox: owner, date_basis: 'recorded_next_date_utc',
    pagination: 'live', as_of_day: day, source: 'email-proposals.v1' };
  let rows: Row[];
  try {
    rows = (await c.query(`with owned as (
      select p.id,p.version,p.kind,p.activity_id,p.status,p.disposition,p.deferred_until,
        p.owner,a.kind as source_kind,case when isfinite(p.next_date) then p.next_date::text end as next_date,
        case when isfinite(a.occurred_at) then a.occurred_at end as occurred_at,a.version as source_version,
        case when isfinite(p.next_date) and p.next_date<$3::date then 0 when isfinite(p.next_date) then 1 else 2 end as priority,
        coalesce(case when isfinite(p.next_date) then p.next_date end,'infinity'::date) as sort_date,
        coalesce(case when isfinite(a.occurred_at) then a.occurred_at end,'infinity'::timestamptz) as sort_instant,
        coalesce(a.email_context#>>'{lifecycle,state}','live') as lifecycle
      from email_proposal p join activity a on a.id=p.activity_id
      where (a.email_source->>'mailbox_owner'=$1 or (a.email_source is null and a.actor_id=$2::uuid))
    ) select id,version,kind,activity_id,source_version,next_date,occurred_at,priority from owned
      where status='pending' and source_kind='email_in' and lifecycle='live'
        and coalesce(disposition,'') not in ('handled','filed')
        and (deferred_until is null or deferred_until<=now())
        and (kind<>'reply_draft' or owner=$1)
        and kind in ('file_to_deal','possible_new_deal','phase_move','next_step','reply_draft')
        and ($4::uuid is null or (priority,sort_date,sort_instant,id)>
          (select priority,sort_date,sort_instant,id from owned where id=$4::uuid))
      order by priority,sort_date,sort_instant,id limit $5`, [owner, actor.id, day, after, args.limit+1])).rows;
  } catch {
    return parseMailNeedsPartnerResponse({ schema_version: 'mail-needs-partner.v1', state: 'unavailable', coverage,
      items: [], more: false, next_cursor: null, send: false });
  }
  const more = rows.length > args.limit;
  const items = rows.slice(0,args.limit).map(row => {
    const occurred = row.occurred_at instanceof Date ? row.occurred_at.toISOString() : row.occurred_at;
    return { proposal_id: row.id, version: row.version, kind: row.kind, status: 'pending',
      decision_date: row.next_date, reason_codes: [row.priority === 0 ? 'recorded_overdue' : row.priority === 1 ? 'recorded_upcoming' : 'decision_date_unknown'],
      source: { activity_id: row.activity_id, version: row.source_version, occurred_at: instant(occurred) ? occurred : null,
        instant_status: instant(occurred) ? 'known' : 'unknown' },
      action: { verb: actions[row.kind], args: { proposal_id: row.id, base_version: row.version }, requires_existing_review: true } };
  });
  return parseMailNeedsPartnerResponse({ schema_version: 'mail-needs-partner.v1', state: items.length ? 'ready' : 'empty',
    coverage, items, more, next_cursor: more ? btoa(JSON.stringify({v:1,owner,day,id:items.at(-1)?.proposal_id})) : null, send: false });
}

export function parseMailNeedsPartnerResponse(value: unknown) {
  if (!value || typeof value !== 'object' || !('schema_version' in value) || value.schema_version !== 'mail-needs-partner.v1' ||
      !('state' in value) || !['ready','empty','unavailable'].includes(String(value.state)) ||
      !('send' in value) || value.send !== false || !('coverage' in value) || !value.coverage || typeof value.coverage !== 'object' ||
      !('mailbox' in value.coverage) || !['joe','dell'].includes(String(value.coverage.mailbox)) ||
      !('items' in value) || !Array.isArray(value.items) || value.items.length>50 ||
      !('more' in value) || typeof value.more !== 'boolean' || !('next_cursor' in value) ||
      (value.more ? typeof value.next_cursor !== 'string' : value.next_cursor !== null) ||
      (value.state === 'ready' ? value.items.length===0 : value.items.length!==0 || value.more)) throw Error('mail_needs_partner_response_invalid');
  for (const item of value.items) {
    if (!item || !uuid.test(item.proposal_id) || !Number.isInteger(item.version) || item.version<1 ||
        item.status!=='pending' || !Object.hasOwn(actions,item.kind) ||
        !(item.decision_date === null || typeof item.decision_date==='string' && date.test(item.decision_date) &&
          new Date(item.decision_date).toISOString().slice(0,10)===item.decision_date) ||
        !Array.isArray(item.reason_codes) || item.reason_codes.length!==1 ||
        !['recorded_overdue','recorded_upcoming','decision_date_unknown'].includes(item.reason_codes[0]) ||
        !item.source || !uuid.test(item.source.activity_id) || !Number.isInteger(item.source.version) || item.source.version<1 ||
        (item.source.instant_status==='unknown' ? item.source.occurred_at!==null : item.source.instant_status!=='known' || !instant(item.source.occurred_at)) ||
        item.action?.verb!==actions[item.kind] || item.action?.args?.proposal_id!==item.proposal_id ||
        item.action?.args?.base_version!==item.version || item.action?.requires_existing_review!==true) throw Error('mail_needs_partner_response_invalid');
  }
  return value;
}
