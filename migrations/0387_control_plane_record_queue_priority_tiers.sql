-- 0387_control_plane_record_queue_priority_tiers.sql
--
-- CONFIRMED PRODUCTION DEFECT, 2026-08-27: the tick dispatched
-- contact-enrichment-weekly and the job dead-lettered with "input unavailable
-- for entity-enrichment.next-40: re-verification queue must contain exactly
-- 40 rows". Two separate problems, fixed here and in
-- lib/control_plane_collectors_records.py.
--
-- (A) WRONG QUEUE. 0162 built public.v_control_plane_enrichment_queue ONLY
-- from v_expired_verification, unscoped. ops/scheduled-tasks/
-- contact-enrichment-weekly.SKILL.md ("PICK THE 40, in this priority order")
-- names FIVE bands, not one:
--   0. v_expired_verification, scoped to subject_type in
--      ('party','vendor','lead','client') -- added 2026-08-06 (loop #212),
--      scoped 2026-08-20 after an unfiltered read returned exactly one row
--      and it was a 'commit' subject (control_plane_production_readiness)
--      that this task can neither research nor enrich. That is the exact
--      defect this migration measured live: the view never carried the
--      scoping filter the SKILL has required since 2026-08-20, so a run
--      landing on a week with no contact-subject re-verifications due saw a
--      queue of ONE ineligible row and refused outright.
--   1. v_vendor_needs_type -- active vendors with no category_slug (0050).
--   2. Vendors missing party.city or party.county (0/290 populated at
--      audit time).
--   3. Vendors missing vendor.verticals (10/290 populated).
--   4. Leads and clients missing party.title, party.org_id, party.email, or
--      both party.phone and party.cell.
-- Every band excludes a subject whose party carries contact_state
-- 'do_not_contact' (0046), exactly as the SKILL's "Skip anything whose party
-- has contact_state of do_not_contact" instructs.
--
-- v_vendor_needs_type itself is reused for its documented predicate
-- (category_slug is null and disposition = 'active') and ordering
-- (is_target desc, relationship_level desc nulls last, name), but that view
-- does not expose vendor.id, so band 1 below re-derives the same predicate
-- directly against vendor/party rather than altering a view other readers
-- depend on.
--
-- Bands are GLOBALLY ordered (band 0 exhausts before band 1, etc.); within a
-- band, rows keep the SKILL's documented tiebreakers. priority is a single
-- contiguous row_number() across the whole union, so
-- lib/control_plane_collectors_records.py's "limit 40" always reads a
-- contiguous 1..N prefix no matter how large the underlying queue is.
--
-- (B) The deal-history sibling view checked the same way against
-- ops/scheduled-tasks/deal-history-research-weekly.SKILL.md ("PICK THE 25,
-- in this priority order"). public.v_control_plane_deal_history_queue (0162,
-- reshaped by 0305 for receipt-missing visibility) already implements bands
-- 1 and 3 correctly, but ranked clients and counterparties across BOTH
-- subject types by one shared newest-deal-first order instead of exhausting
-- the client bands first, and it never implemented band 2 (any client whose
-- status is active_deal or engaged and has never been verified, regardless
-- of whether a Salesforce-tagged deal exists) at all -- the same class of
-- error as (A): a real priority band from the task's own SKILL that the view
-- never reads from. Band 4 (identity anomalies) restores only the
-- objectively checkable half of what the SKILL names: a missing
-- client.roster_ref. "a NULL status" is NOT reachable here -- client.status
-- has been NOT NULL since 0001 -- so encoding it would be a predicate that
-- can never match, not a defense; if a future migration relaxes that
-- constraint, add the check then. The 15/25 receipt-bound sizing math from
-- 0305 is untouched.

begin;

create or replace view public.v_control_plane_enrichment_queue as
with reverify_scoped as (
  select q.subject_type, q.subject_id, q.reason, q.observed_at, q.expires_on,
         q.past_age_floor, q.subject_touches,
         case q.subject_type
           when 'party'  then q.subject_id
           when 'vendor' then v.party_id
           when 'lead'   then l.party_id
           when 'client' then c.party_id
         end as resolved_party_id
    from public.v_expired_verification q
    left join public.vendor v on q.subject_type = 'vendor' and v.id = q.subject_id
    left join public.lead   l on q.subject_type = 'lead'   and l.id = q.subject_id
    left join public.client c on q.subject_type = 'client' and c.id = q.subject_id
   where q.subject_type in ('party', 'vendor', 'lead', 'client')
),
reverify as (
  select 0::int as tier_rank,
         row_number() over (
           order by rs.past_age_floor desc, rs.subject_touches desc,
                    rs.observed_at asc, rs.subject_id
         ) as tier_seq,
         rs.subject_type, rs.subject_id, rs.reason as reverification_due,
         coalesce(rs.expires_on::timestamptz, rs.observed_at) as expired_at
    from reverify_scoped rs
    join public.party p on p.id = rs.resolved_party_id
   where p.contact_state <> 'do_not_contact'
),
needs_type as (
  select 1::int as tier_rank,
         row_number() over (
           order by v.is_target desc, v.relationship_level desc nulls last, p.name
         ) as tier_seq,
         'vendor'::text as subject_type, v.id as subject_id,
         'not_recorded'::text as reverification_due,
         v.created_at as expired_at
    from public.vendor v
    join public.party p on p.id = v.party_id
   where v.category_slug is null
     and v.disposition = 'active'
     and p.contact_state <> 'do_not_contact'
),
needs_location as (
  select 2::int as tier_rank,
         row_number() over (order by p.name, v.id) as tier_seq,
         'vendor'::text as subject_type, v.id as subject_id,
         'not_recorded'::text as reverification_due,
         v.created_at as expired_at
    from public.vendor v
    join public.party p on p.id = v.party_id
   where v.disposition = 'active'
     and (p.city is null or p.county is null)
     and p.contact_state <> 'do_not_contact'
     and not exists (select 1 from needs_type nt where nt.subject_id = v.id)
),
needs_verticals as (
  select 3::int as tier_rank,
         row_number() over (order by p.name, v.id) as tier_seq,
         'vendor'::text as subject_type, v.id as subject_id,
         'not_recorded'::text as reverification_due,
         v.created_at as expired_at
    from public.vendor v
    join public.party p on p.id = v.party_id
   where v.disposition = 'active'
     and (v.verticals is null or cardinality(v.verticals) = 0)
     and p.contact_state <> 'do_not_contact'
     and not exists (select 1 from needs_type nt where nt.subject_id = v.id)
     and not exists (select 1 from needs_location nl where nl.subject_id = v.id)
),
lead_client_gaps as (
  select 4::int as tier_rank,
         row_number() over (order by x.expired_at asc, x.subject_type, x.subject_id) as tier_seq,
         x.subject_type, x.subject_id,
         'not_recorded'::text as reverification_due,
         x.expired_at
    from (
      select 'lead'::text as subject_type, l.id as subject_id, l.created_at as expired_at
        from public.lead l
        join public.party p on p.id = l.party_id
       where p.contact_state <> 'do_not_contact'
         and (p.title is null or p.org_id is null or p.email is null
              or (p.phone is null and p.cell is null))
      union all
      select 'client'::text, c.id, c.created_at
        from public.client c
        join public.party p on p.id = c.party_id
       where p.contact_state <> 'do_not_contact'
         and (p.title is null or p.org_id is null or p.email is null
              or (p.phone is null and p.cell is null))
    ) x
),
combined as (
  select tier_rank, tier_seq, subject_type, subject_id, reverification_due, expired_at from reverify
  union all
  select tier_rank, tier_seq, subject_type, subject_id, reverification_due, expired_at from needs_type
  union all
  select tier_rank, tier_seq, subject_type, subject_id, reverification_due, expired_at from needs_location
  union all
  select tier_rank, tier_seq, subject_type, subject_id, reverification_due, expired_at from needs_verticals
  union all
  select tier_rank, tier_seq, subject_type, subject_id, reverification_due, expired_at from lead_client_gaps
)
select subject_type, subject_id, reverification_due,
       'not_current'::text as current_verification_status,
       row_number() over (order by tier_rank asc, tier_seq asc)::integer as priority,
       expired_at
  from combined;

comment on view public.v_control_plane_enrichment_queue is
  'Control-plane re-verification/enrichment projection, five priority bands '
  'in the order contact-enrichment-weekly.SKILL.md documents: (0) scoped '
  'expired/unstamped-volatile re-verifications, (1) active vendors with no '
  'category, (2) active vendors missing city/county, (3) active vendors '
  'missing verticals, (4) leads/clients missing title, org, email, or phone. '
  'do_not_contact parties are excluded from every band. priority is one '
  'contiguous row_number() across all bands, so a short queue is a valid '
  '1..N prefix, never a gap.';

grant select on public.v_control_plane_enrichment_queue to carr_jobs;

create or replace view public.v_control_plane_deal_history_queue as
with thursday_enrichment as (
  select j.id as job_id, j.scheduled_for, j.mode,
         (r.evidence->>'subjects_processed')::integer as subjects_processed,
         row_number() over (
           partition by j.mode,
             date_trunc('week', j.scheduled_for at time zone 'America/Chicago')
           order by j.scheduled_for desc, r.created_at desc
         ) as rn
    from ops.job_receipt r
    join ops.job j on j.id=r.job_id
   where j.definition_key='contact-enrichment-weekly'
     and r.kind='completion'
     and extract(isodow from j.scheduled_for at time zone 'America/Chicago')=4
     and jsonb_typeof(r.evidence->'subjects_processed')='number'
     and (r.evidence->>'subjects_processed')::integer >= 0
), weekly as (
  select job_id, scheduled_for, mode, subjects_processed,
         case when subjects_processed >= 30 then 15 else 25 end as slice_limit
    from thursday_enrichment where rn=1
), sizing as (
  select w.job_id, w.scheduled_for, w.mode, w.subjects_processed,
         w.slice_limit, 'receipt_bound'::text as sizing_state
    from weekly w
  union all
  select null::uuid, null::timestamptz, null::text, null::integer,
         null::integer, 'receipt_missing'::text
   where not exists (select 1 from weekly)
),
salesforce_clients as (
  select 1::int as tier_rank, 'client'::text as subject_type, c.id as subject_id,
         row_number() over (order by max(d.created_at) desc nulls last, c.id) as tier_seq
    from public.client c
    join public.party p on p.id = c.party_id
    join public.deal d on d.client_id = c.id
   where d.salesforce_id is not null
     and p.contact_state <> 'do_not_contact'
     and not exists (
       select 1 from public.record_flag f
        where f.subject_type = 'client' and f.subject_id = c.id
          and f.kind = 'verified'
          and (f.expires_on is null or f.expires_on >= current_date)
     )
   group by c.id
),
pipeline_clients as (
  select 2::int as tier_rank, 'client'::text as subject_type, c.id as subject_id,
         row_number() over (order by c.created_at desc, c.id) as tier_seq
    from public.client c
    join public.party p on p.id = c.party_id
   where c.status in ('active_deal', 'engaged')
     and p.contact_state <> 'do_not_contact'
     and not exists (select 1 from salesforce_clients sc where sc.subject_id = c.id)
     and not exists (
       select 1 from public.record_flag f
        where f.subject_type = 'client' and f.subject_id = c.id
          and f.kind = 'verified'
          and (f.expires_on is null or f.expires_on >= current_date)
     )
),
counterparties as (
  select 3::int as tier_rank, 'party'::text as subject_type, dp.party_id as subject_id,
         row_number() over (order by max(d.created_at) desc nulls last, dp.party_id) as tier_seq
    from public.deal_participant dp
    join public.deal d on d.id = dp.deal_id
    join public.party p on p.id = dp.party_id
   where d.salesforce_id is not null
     and dp.party_id is not null
     and p.contact_state <> 'do_not_contact'
     and not exists (
       select 1 from public.record_flag f
        where f.subject_type = 'party' and f.subject_id = dp.party_id
          and f.kind = 'verified'
          and (f.expires_on is null or f.expires_on >= current_date)
     )
   group by dp.party_id
),
identity_anomalies as (
  select 4::int as tier_rank, 'client'::text as subject_type, c.id as subject_id,
         row_number() over (order by c.created_at desc, c.id) as tier_seq
    from public.client c
    join public.party p on p.id = c.party_id
   where c.roster_ref is null
     and p.contact_state <> 'do_not_contact'
     and not exists (select 1 from salesforce_clients sc where sc.subject_id = c.id)
     and not exists (select 1 from pipeline_clients pc where pc.subject_id = c.id)
),
ranked as (
  select tier_rank, subject_type, subject_id,
         row_number() over (order by tier_rank asc, tier_seq asc)::integer as priority
    from (
      select tier_rank, tier_seq, subject_type, subject_id from salesforce_clients
      union all
      select tier_rank, tier_seq, subject_type, subject_id from pipeline_clients
      union all
      select tier_rank, tier_seq, subject_type, subject_id from counterparties
      union all
      select tier_rank, tier_seq, subject_type, subject_id from identity_anomalies
    ) tiers
)
select r.subject_type, r.subject_id, 'unverified'::text as verification,
       r.priority, 'canonical_counterparty'::text as source_class,
       s.slice_limit, s.subjects_processed as enrichment_subject_count,
       s.scheduled_for as enrichment_scheduled_for, s.mode as enrichment_mode,
       s.sizing_state
  from ranked r cross join sizing s
 where s.slice_limit is null or r.priority <= s.slice_limit;

comment on view public.v_control_plane_deal_history_queue is
  'Deal-history research queue, four priority bands in the order '
  'deal-history-research-weekly.SKILL.md documents: (1) clients on a '
  'Salesforce-tagged deal with no verified flag, newest-imported first, (2) '
  'any active_deal/engaged client never verified, (3) deal_participant '
  'counterparties with no verified flag, (4) clients missing roster_ref. '
  'receipt_missing keeps an unverified backlog visible when no typed '
  'Thursday completion receipt exists, but supplies no execution cap or '
  'receipt metadata. receipt_bound rows carry the exact 15/25 cap and '
  'receipt evidence required by the execution collector.';

grant select on public.v_control_plane_deal_history_queue to carr_jobs;

commit;

do $$
declare
  reverify_bands int;
  band_count int;
  deal_history_columns int;
  dnc_leaks int;
  scope_leaks int;
  deal_history_bands int;
  deal_history_dnc int;
begin
  if to_regclass('public.v_control_plane_enrichment_queue') is null then
    raise exception '0387 FAILED: missing public.v_control_plane_enrichment_queue';
  end if;
  if to_regclass('public.v_control_plane_deal_history_queue') is null then
    raise exception '0387 FAILED: missing public.v_control_plane_deal_history_queue';
  end if;

  -- The view definition must actually name all four never-verified profile
  -- bands and v_expired_verification, not just the pre-existing tier 0.
  -- pg_views.definition is the DEPARSED plan text, not the source: keywords
  -- come back upper-cased and re-parenthesized (e.g. "p.city is null or
  -- p.county is null" becomes "(p.city IS NULL) OR (p.county IS NULL)"), so
  -- every check here is case-insensitive (~*) and matches identifiers plus
  -- looser keyword spacing rather than an exact source substring. Measured
  -- against a real deparse on a disposable local Postgres before landing.
  select count(*) into band_count
    from pg_views
   where schemaname = 'public' and viewname = 'v_control_plane_enrichment_queue'
     and definition ~* 'v_expired_verification'
     and definition ~* 'category_slug\s+is\s+null'
     and definition ~* 'p\.city\s+is\s+null'
     and definition ~* 'p\.county\s+is\s+null'
     and definition ~* 'v\.verticals\s+is\s+null'
     and definition ~* 'cardinality\(v\.verticals\)';
  if band_count <> 1 then
    raise exception '0387 FAILED: enrichment queue definition is missing a required priority band';
  end if;

  select count(*) into scope_leaks
    from pg_views
   where schemaname = 'public' and viewname = 'v_control_plane_enrichment_queue'
     and (definition !~ '''party''' or definition !~ '''vendor'''
          or definition !~ '''lead''' or definition !~ '''client'''
          or definition !~* 'subject_type');
  if scope_leaks <> 0 then
    raise exception '0387 FAILED: enrichment queue lost its contact subject_type scoping';
  end if;

  select count(*) into dnc_leaks
    from pg_views
   where schemaname = 'public' and viewname = 'v_control_plane_enrichment_queue'
     and definition !~ 'do_not_contact';
  if dnc_leaks <> 0 then
    raise exception '0387 FAILED: enrichment queue lost its do_not_contact exclusion';
  end if;

  select count(*) into deal_history_bands
    from pg_views
   where schemaname = 'public' and viewname = 'v_control_plane_deal_history_queue'
     and definition ~ 'active_deal'
     and definition ~ 'engaged'
     and definition ~* 'roster_ref\s+is\s+null';
  if deal_history_bands <> 1 then
    raise exception '0387 FAILED: deal-history queue is missing the active_deal/engaged or roster_ref band';
  end if;

  select count(*) into deal_history_dnc
    from pg_views
   where schemaname = 'public' and viewname = 'v_control_plane_deal_history_queue'
     and definition !~ 'do_not_contact';
  if deal_history_dnc <> 0 then
    raise exception '0387 FAILED: deal-history queue lost its do_not_contact exclusion';
  end if;

  -- Reads must still work against a schema with no matching data (this
  -- migration is not applied to any database, but the shape check below
  -- would catch an obvious typo if it ever is): both views expose the exact
  -- column set the runtime collector query names.
  select count(*) into reverify_bands
    from information_schema.columns
   where table_schema = 'public' and table_name = 'v_control_plane_enrichment_queue'
     and column_name in ('subject_type', 'subject_id', 'reverification_due',
                          'current_verification_status', 'priority', 'expired_at');
  if reverify_bands <> 6 then
    raise exception '0387 FAILED: enrichment queue is missing a required output column';
  end if;

  select count(*) into deal_history_columns
    from information_schema.columns
   where table_schema = 'public' and table_name = 'v_control_plane_deal_history_queue'
     and column_name in ('subject_type', 'subject_id', 'verification', 'priority',
                          'source_class', 'slice_limit', 'enrichment_subject_count',
                          'enrichment_scheduled_for', 'enrichment_mode', 'sizing_state');
  if deal_history_columns <> 10 then
    raise exception '0387 FAILED: deal-history queue is missing a required output column';
  end if;

  if not has_table_privilege('carr_jobs', 'public.v_control_plane_enrichment_queue', 'select') then
    raise exception '0387 FAILED: carr_jobs cannot read the enrichment queue';
  end if;
  if not has_table_privilege('carr_jobs', 'public.v_control_plane_deal_history_queue', 'select') then
    raise exception '0387 FAILED: carr_jobs cannot read the deal-history queue';
  end if;
end $$;
