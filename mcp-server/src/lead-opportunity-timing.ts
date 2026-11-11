// Parse and project source event dates without inferring practice openings.
import { isCalendarDate } from "./calendar-date.ts";
import { ToolError } from "./tool-error.ts";

type OpportunityEvent = {
  date: string;
  date_type: "date";
  date_basis: "enumeration" | "registry_update" | "publication";
  source: "nppes" | "pecos";
  record_key: string;
  revision: string;
  source_url: string;
  published_on: string;
  observed_at: string;
  meaning: string;
  uncertainty: string;
};

export type LeadOpportunityTiming = {
  contract: "lead-opportunity-timing.v1";
  record_added_at: string;
} & ({ status: "unknown"; events: [] } | { status: "supported"; events: [OpportunityEvent, ...OpportunityEvent[]] });

function isInstant(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  return (
    !!match &&
    isCalendarDate(match[1]) &&
    Number(match[2]) < 24 &&
    Number(match[3]) < 60 &&
    Number(match[4]) < 60 &&
    Number(match[6] ?? 0) < 24 &&
    Number(match[7] ?? 0) < 60 &&
    Number.isFinite(Date.parse(value))
  );
}

function isEvent(value: unknown): value is OpportunityEvent {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const e = value as Record<string, unknown>;
  return (
    isCalendarDate(e.date) &&
    e.date_type === "date" &&
    isCalendarDate(e.published_on) &&
    isInstant(e.observed_at) &&
    ((e.source === "nppes" && (e.date_basis === "enumeration" || e.date_basis === "registry_update")) ||
      (e.source === "pecos" && e.date_basis === "publication")) &&
    [e.record_key, e.revision, e.source_url, e.meaning, e.uncertainty].every(
      (v) => typeof v === "string" && v.trim().length > 0,
    )
  );
}

// Refuse malformed reads. Unknown means absent source support, never corrupt evidence.
export function parseLeadOpportunityTiming(value: unknown): LeadOpportunityTiming {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const t = value as Record<string, unknown>;
    if (
      t.contract === LEAD_OPPORTUNITY_TIMING_VERSION &&
      isInstant(t.record_added_at) &&
      Array.isArray(t.events) &&
      ((t.status === "unknown" && t.events.length === 0) ||
        (t.status === "supported" && t.events.length > 0 && t.events.every(isEvent)))
    ) {
      return value as LeadOpportunityTiming;
    }
  }
  throw new ToolError({ error: "invalid_lead_opportunity_timing" });
}

const LEAD_OPPORTUNITY_TIMING_VERSION = "lead-opportunity-timing.v1";

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
