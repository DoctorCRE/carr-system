// Project vendor people with sourced offices and research status.
export const vendorPeopleJoin = `left join lateral (
  select p.id as contact_id,p.name,p.email,p.phone,p.cell,p.city,p.state,
    f.flag_id,f.value,f.source,f.observed_at,f.expires_on,
    coalesce(f.value->'found'='true'::jsonb and not f.proposes_correction
      and f.value->>'epistemic_status'='source_backed'
      and coalesce(f.value->>'internal','false')='false'
      and f.value->>'location_type'='office'
      and jsonb_typeof(f.value->'street_address')='string' and btrim(f.value->>'street_address')<>''
      and jsonb_typeof(f.value->'city')='string' and btrim(f.value->>'city')<>''
      and jsonb_typeof(f.value->'state')='string' and btrim(f.value->>'state')<>''
      and btrim(f.source)<>'' and f.observed_at<=statement_timestamp()
      and (f.expires_on is null or f.expires_on>=current_date),false) as office_verified
  from jsonb_array_elements(a.contacts) contact
  join public.party p on p.id=(contact->>'id')::uuid
  left join lateral (
    select flag_id,value,source,observed_at,expires_on,proposes_correction
    from public.v_record_flag_subject
    where subject_type='party' and subject_id=p.id and kind='address'
    order by observed_at desc,flag_id desc limit 1
  ) f on true
) person on true`;

export const vendorPersonFields = `'contact',case when contact_id is not null then jsonb_build_object('id',contact_id,'name',name,'email',email,'phone',phone,'cell',cell) end,
    'office',case when office_verified then jsonb_build_object('street_address',value->>'street_address','city',value->>'city',
      'state',value->>'state','finding_id',flag_id,'source',source,'observed_at',observed_at,'expires_on',expires_on) end,
    'research',jsonb_build_object('status',case when flag_id is null then 'not_researched' when office_verified then 'office_verified' else 'needs_review' end,
      'finding_id',flag_id,'observed_at',observed_at,'expires_on',expires_on)`;

export function vendorDirectoryPayload(rowAlias) {
  return `${rowAlias}.row_payload::jsonb || jsonb_build_object('people', (
    select coalesce(jsonb_agg(jsonb_build_object('company',company,${vendorPersonFields}) order by contact_id),'[]'::jsonb)
    from public.v_vendor_assignment_context a
    ${vendorPeopleJoin}
    where a.id=${rowAlias}.id and nullif(btrim(name),'') is not null
  ))`;
}
