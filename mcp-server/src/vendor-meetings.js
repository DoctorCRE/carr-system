// Expose vendor meeting records.
import { vendorPeopleJoin, vendorPersonFields } from './vendor-people.js';
import { ToolError } from './tool-error.ts';
import { organizationTenantForActor } from './identity.js';
import { CALENDAR_AGENDA_BOARD, calendarSection } from './brief-calendar.js';
import { calendarScheduling } from './calendar-scheduling.js';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ZERO = '00000000-0000-0000-0000-000000000000';

const statement = `with candidates as (
  select a.id as vendor_id,a.ref,a.party_id,a.company,
    person.contact_id,person.name,person.email,person.phone,person.cell,person.city,person.state,
    vc.slug as category,vc.label as category_label,v.territory,v.offers as reason,
    coalesce(vd.workable,false) as workable,v.out_of_market,
    person.flag_id,person.value,person.source,person.observed_at,person.expires_on,
    coalesce(person.office_verified,false) as office_verified,
    exists(select 1 from public.party held where held.id in (a.party_id,person.contact_id,(a.company->>'id')::uuid)
      and ((held.contact_state<>'active' and (held.contact_state_until is null or held.contact_state_until>=current_date))
        or exists(select 1 from public.vendor avoided where avoided.party_id=held.id and avoided.merged_into is null and avoided.disposition='avoid')
        or exists(select 1 from public.lead suppressed where suppressed.party_id=held.id and (coalesce(suppressed.suppressed,false) or suppressed.stage='do_not_contact')))) as restricted
  from public.v_vendor_assignment_context a
  join public.vendor v on v.id=a.id
  left join public.vendor_category vc on vc.slug=coalesce(v.category_slug,v.category)
  left join public.vendor_disposition vd on vd.slug=v.disposition
  ${vendorPeopleJoin}
), assessed as (
  select *,array_remove(array[
    case when nullif(btrim(name),'') is null then 'named_contact' end,
    case when nullif(btrim(company->>'name'),'') is null then 'company' end,
    case when category is null then 'category' end,
    case when not workable then 'workable_vendor' end,
    case when out_of_market then 'in_market' end,
    case when nullif(btrim(reason),'') is null then 'meeting_reason' end,
    case when not office_verified then 'verified_office' end,
    case when restricted then 'contact_permission' end
  ],null) as missing
  from candidates
), filtered as (
  select * from assessed where
    ($1='' or strpos(lower(concat_ws(' ',ref,name,company->>'name',category,category_label,territory,city,state,
      value->>'street_address',value->>'city',value->>'state')),lower($1))>0)
    and (($2='suggested' and cardinality(missing)=0) or ($2='research' and cardinality(missing)>0))
), page as (
  select * from filtered where (vendor_id,coalesce(contact_id,'${ZERO}'::uuid))>($3::uuid,$4::uuid)
  order by vendor_id,coalesce(contact_id,'${ZERO}'::uuid) limit $5
)
select (select count(*)::int from filtered) as total,
  coalesce(jsonb_agg(jsonb_build_object(
    'vendor_id',vendor_id,'ref',ref,'party_id',party_id,'company',company,
    ${vendorPersonFields},
    'category',category,'category_label',category_label,'service_coverage',territory,'reason',reason,'missing',missing
  ) order by vendor_id,coalesce(contact_id,'${ZERO}'::uuid)),'[]'::jsonb) as items from page`;

async function vendorCalendar(c, actor, items, now) {
  for (const item of items) item.possible_meetings = [];
  const unavailable = { state: 'unavailable', reason: 'source_unavailable',
    scheduling: calendarScheduling(null, null, now) };
  if (c.inTransaction) await c.query('savepoint vendor_calendar');
  try {
    const { rows } = await c.query(`select snapshot_json from board_snapshot
      where organization_tenant_id=$1 and sponsoring_human_slug=$2 and board_id=$3`,
    [organizationTenantForActor(actor), actor.slug, CALENDAR_AGENDA_BOARD]);
    const snapshot = rows[0]?.snapshot_json;
    const section = calendarSection(snapshot, now);
    if (!section) {
      if (c.inTransaction) await c.query('release savepoint vendor_calendar');
      return unavailable;
    }
    for (const item of items) {
      const email = item.contact?.email?.trim().toLowerCase();
      if (email) item.possible_meetings = section.items.filter(meeting =>
        [meeting.organizer, ...meeting.attendees].some(person => person.email === email));
    }
    const { items: _meetings, ...calendar } = section;
    calendar.scheduling = calendarScheduling(snapshot, section, now);
    if (c.inTransaction) await c.query('release savepoint vendor_calendar');
    return calendar;
  } catch {
    if (c.inTransaction) {
      await c.query('rollback to savepoint vendor_calendar');
      await c.query('release savepoint vendor_calendar');
    }
    return unavailable;
  }
}

export function vendorMeetingTools() {
  return {
    'read-vendor-meetings': {
      write: false,
      description: 'Read vendor-meetings.v1 Suggested or searchable Research pages. Requires a named person and current source-backed office address finding on that person. Known email and phone are optional. Includes possible upcoming meetings matched to the contact email in the authenticated partner calendar, with source warnings. Neither composes nor sends introductions.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        view: { type: 'string', enum: ['suggested', 'research'] },
        q: { type: 'string', maxLength: 80 },
        limit: { type: 'integer', minimum: 1, maximum: 50 },
        cursor: { type: 'string', minLength: 1, maxLength: 2000 },
      } },
      handler: async (c, actor, args = {}) => {
        if (!['joe', 'dell'].includes(actor?.slug)) throw new ToolError({ error: 'AUTHORIZATION_REFUSED' });
        const invalid = () => { throw new ToolError({ error: 'vendor_meetings_query_invalid' }); };
        if (!args || typeof args !== 'object' || Array.isArray(args) || Object.keys(args).some(k => !['view','q','limit','cursor'].includes(k))) invalid();
        const now = new Date();
        const { view = 'suggested', q = '', limit = 25, cursor } = args;
        if (!['suggested','research'].includes(view) || typeof q !== 'string' || q.length > 80 || !Number.isInteger(limit) || limit < 1 || limit > 50) invalid();
        let after = [ZERO, ZERO];
        if (cursor !== undefined) {
          try {
            if (typeof cursor !== 'string' || !cursor.length || cursor.length > 2000) invalid();
            const token = JSON.parse(decodeURIComponent(atob(cursor)));
            if (token.v !== 1 || token.view !== view || token.q !== q || !Array.isArray(token.after) || token.after.length !== 2 || !token.after.every(id => typeof id === 'string' && UUID.test(id))) invalid();
            after = token.after;
          } catch { invalid(); }
        }
        let result;
        try { result = (await c.query(statement, [q,view,...after,limit+1])).rows[0]; }
        catch { throw new ToolError({ error: 'vendor_meetings_unavailable' }); }
        const has_more = result.items.length > limit;
        const items = result.items.slice(0,limit);
        const last = items.at(-1);
        const calendar = await vendorCalendar(c, actor, items, now);
        return { schema: 'vendor-meetings.v1', view, total: result.total, items, has_more, calendar,
          next_cursor: has_more ? btoa(encodeURIComponent(JSON.stringify({ v:1,view,q,after:[last.vendor_id,last.contact?.id || ZERO] }))) : null,
          consistency: 'live_keyset; restart after record changes' };
      },
    },
  };
}
