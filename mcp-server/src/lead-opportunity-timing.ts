export const LEAD_OPPORTUNITY_TIMING_VERSION = 'lead-opportunity-timing.v1';

// Select revisions before matching identity so a corrected record cannot revive an old match.
export const opportunityTimingCtes = `origination_latest as materialized (
  select distinct on (source,record_key) * from practice_origination_event
  order by source,record_key,id desc
), timing_identities as materialized (
  select npi,min(id::text)::uuid as party_id from party
  where npi is not null and merged_into is null and deleted_at is null
  group by npi having count(*)=1
), timing_events as materialized (
  select p.party_id,jsonb_agg(jsonb_build_object(
    'date',e.evidence_date::text,'date_type','date','date_basis',e.date_basis,
    'source',e.source,'record_key',e.record_key,'revision',e.id::text,
    'source_url',e.source_url,'published_on',e.published_on::text,'observed_at',e.recorded_at,
    'meaning',case e.kind when 'enumeration' then 'NPI enumerated'
      when 'registry_update' then 'NPPES registry updated'
      when 'pecos_correction' then 'Public PECOS evidence corrected'
      else 'Public PECOS enrollment observed' end,
    'uncertainty','Practice opening and real-estate decision unverified')
    order by e.source,e.record_key) as events
  from origination_latest e join timing_identities p on p.npi=e.npi
  join practice_origination_feed f on f.source=e.source and f.status='available'
  where e.source in ('nppes','pecos')
  group by p.party_id
)`;

export const opportunityTimingSql = `jsonb_build_object(
  'contract','${LEAD_OPPORTUNITY_TIMING_VERSION}',
  'record_added_at',w.created_at,
  'status',case when timing.events is null then 'unknown' else 'supported' end,
  'events',coalesce(timing.events,'[]'::jsonb))`;
