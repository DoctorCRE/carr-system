import { ToolError } from './tool-error.js';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ZERO = '00000000-0000-0000-0000-000000000000';

const statement = `with candidates as (
  select a.id as vendor_id,a.ref,a.party_id,a.company,
    p.id as contact_id,p.name,p.email,p.phone,p.cell,p.city,p.state,
    vc.slug as category,vc.label as category_label,v.territory,v.offers as reason,
    coalesce(vd.workable,false) as workable,v.out_of_market,
    f.flag_id,f.value,f.source,f.observed_at,f.expires_on,
    coalesce(f.value->'found'='true'::jsonb and not f.proposes_correction
      and f.value->>'epistemic_status'='source_backed'
      and coalesce(f.value->>'internal','false')='false'
      and f.value->>'location_type'='office'
      and jsonb_typeof(f.value->'street_address')='string' and btrim(f.value->>'street_address')<>''
      and jsonb_typeof(f.value->'city')='string' and btrim(f.value->>'city')<>''
      and jsonb_typeof(f.value->'state')='string' and btrim(f.value->>'state')<>''
      and btrim(f.source)<>'' and f.observed_at<=statement_timestamp()
      and (f.expires_on is null or f.expires_on>=current_date),false) as office_verified,
    exists(select 1 from public.party held where held.id in (a.party_id,p.id,(a.company->>'id')::uuid)
      and ((held.contact_state<>'active' and (held.contact_state_until is null or held.contact_state_until>=current_date))
        or exists(select 1 from public.vendor avoided where avoided.party_id=held.id and avoided.merged_into is null and avoided.disposition='avoid')
        or exists(select 1 from public.lead suppressed where suppressed.party_id=held.id and (coalesce(suppressed.suppressed,false) or suppressed.stage='do_not_contact')))) as restricted
  from public.v_vendor_assignment_context a
  join public.vendor v on v.id=a.id
  left join public.vendor_category vc on vc.slug=coalesce(v.category_slug,v.category)
  left join public.vendor_disposition vd on vd.slug=v.disposition
  left join lateral jsonb_array_elements(a.contacts) contact on true
  left join public.party p on p.id=(contact->>'id')::uuid
  left join lateral (
    select flag_id,value,source,observed_at,expires_on,proposes_correction
    from public.v_record_flag_subject
    where subject_type='party' and subject_id=p.id and kind='address'
    order by observed_at desc,flag_id desc limit 1
  ) f on true
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
    'contact',case when contact_id is not null then jsonb_build_object('id',contact_id,'name',name,'email',email,'phone',phone,'cell',cell) end,
    'category',category,'category_label',category_label,'service_coverage',territory,'reason',reason,'missing',missing,
    'office',case when office_verified then jsonb_build_object('street_address',value->>'street_address','city',value->>'city',
      'state',value->>'state','finding_id',flag_id,'source',source,'observed_at',observed_at,'expires_on',expires_on) end,
    'research',jsonb_build_object('status',case when flag_id is null then 'not_researched' when office_verified then 'office_verified' else 'needs_review' end,
      'finding_id',flag_id,'observed_at',observed_at,'expires_on',expires_on)
  ) order by vendor_id,coalesce(contact_id,'${ZERO}'::uuid)),'[]'::jsonb) as items from page`;

export function vendorMeetingTools() {
  return {
    'read-vendor-meetings': {
      write: false,
      description: 'Read vendor-meetings.v1 Suggested or searchable Research pages. Requires a named person and current source-backed office address finding on that person. Known email and phone are optional. Neither composes nor sends introductions.',
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
        return { schema: 'vendor-meetings.v1', view, total: result.total, items, has_more,
          next_cursor: has_more ? btoa(encodeURIComponent(JSON.stringify({ v:1,view,q,after:[last.vendor_id,last.contact?.id || ZERO] }))) : null,
          consistency: 'live_keyset; restart after record changes' };
      },
    },
  };
}
