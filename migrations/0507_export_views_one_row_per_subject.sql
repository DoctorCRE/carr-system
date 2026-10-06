-- 0507 — one export row per subject: stop the open-next-action LEFT JOIN from
-- fanning a subject into duplicate rows.
--
-- WHAT WAS BROKEN. Three views LEFT JOIN next_action on status = 'open' and read
-- a single scalar off it. A subject may legitimately have MORE THAN ONE open
-- next action, and when it does the join multiplies that subject's row by the
-- number of open actions. The views then report the same subject twice.
--
-- HOW IT SURFACED, and why it was misread for a day. On 2026-09-14 the nightly
-- chain's code-integrity step failed with "duplicate Lead ID(s): ['L-214']" and
-- BROKEN canonical lead registry. That read as a corrupt registry. It was not:
-- there is exactly ONE lead row for L-214. Dr. Luke Hyder carried two genuine
-- open next actions -- define the engagement on the Milton MOB (2026-08-10) and
-- thank him for the Beasley referral (2026-09-11) -- and BOTH are real work
-- nobody should close to make an audit pass. The data was right and the view was
-- wrong. Measured before this migration: 262 rows for 261 distinct Lead IDs.
--
-- THE FIX. LEFT JOIN LATERAL ... LIMIT 1, so the join contributes at most one
-- row per subject and the view's row count is a property of the subject table
-- alone. ORDER: soonest due_on first (NULLS LAST, an undated action is not more
-- urgent than a dated one), then earliest created_at, then id -- fully
-- deterministic, no ties, and it surfaces the most urgent action, which is what
-- a board is for. A subject with zero or one open action sees NO change.
--
-- ALL THREE ARE FIXED, not only the one that failed tonight. v_export_vendors
-- and v_calendar_prebrief_events carry the identical defect and are duplicate-
-- free today only because no vendor or calendar participant happens to hold a
-- second open action yet. Fixing only the view that broke would leave two that
-- break later for a reason nobody would remember.
--
-- NOT CHANGED: every column, its name, its order and its source expression; the
-- WHERE clauses; the hold_until predicate on the calendar view. Intentionally no
-- BEGIN/COMMIT: tools/migrate.py owns the transaction and records this file only
-- after every assertion below passes.

create or replace view public.v_export_leads as
 SELECT l.registry_ref AS "Lead ID",
    l.created_at::date AS "Date In",
    COALESCE(l.owner_label, owner.display_name) AS "Owner",
    ls.label AS "Stage",
    l.segment AS "Segment",
    p.name AS "Contact Name",
    org.name AS "Practice",
    p.specialty AS "Specialty",
    p.city AS "City/Market",
    p.county AS "County",
    p.email AS "Email",
    p.phone AS "Phone",
    l.source_type AS "Source Type",
    l.source_detail AS "Source Detail (V-ID / event / referrer)",
    COALESCE(l.report_back_due::text, l.report_back_due_raw) AS "Report-Back Due",
    l.drip_campaign AS "Drip Campaign",
    COALESCE(l.drip_added::text, l.drip_added_raw) AS "Drip Added",
    na.description AS "Next Action",
    na.due_on AS "Next Action Date",
    lt.last_touch AS "Last Touch",
    l.sf_deal AS "SF Deal",
    l.notes_path AS "Detail File",
    l.notes AS "Notes",
    COALESCE(l.est_lease_event::text, l.est_lease_event_raw) AS "Est-Lease-Event",
    l.event_source AS "Event-Source",
    l.event_confidence AS "Event-Confidence",
    l.suppressed AS _suppressed
   FROM lead l
     JOIN party p ON p.id = l.party_id
     JOIN lead_stage ls ON ls.slug = l.stage
     LEFT JOIN party org ON org.id = p.org_id
     LEFT JOIN actor owner ON owner.id = l.owner_id
     LEFT JOIN LATERAL (
       SELECT n.description, n.due_on
         FROM next_action n
        WHERE n.subject_type = 'lead'::text
          AND n.subject_id = l.id
          AND n.status = 'open'::text
        ORDER BY n.due_on ASC NULLS LAST, n.created_at ASC, n.id ASC
        LIMIT 1
     ) na ON true
     LEFT JOIN v_last_touch lt ON lt.subject_type = 'lead'::text AND lt.subject_id = l.id;

create or replace view public.v_export_vendors as
 SELECT v.vendor_ref AS "ID",
    p.name AS "Name",
    org.name AS "Company",
    COALESCE(vc.label, v.category) AS "Category",
    array_to_string(v.verticals, ', '::text) AS "Vertical",
    p.title AS "Title",
    COALESCE(v.owner_label, owner.display_name) AS "Owner",
    vs.label AS "Stage",
    lt.last_touch AS "Last Touch",
    na.description AS "Next Step",
        CASE
            WHEN v.referral_active THEN 'Yes'::text
            WHEN NOT v.referral_active THEN 'No'::text
            ELSE NULL::text
        END AS "Referral-active?",
    v.territory AS "Territory",
    p.state AS "State",
    v.offers AS "Offers",
    v.seeking AS "Seeking",
    v.links_label AS "Links",
    v.rivalry_group AS "Rivalry Group",
    v.originated AS "Originated / Referred",
    p.phone AS "Phone",
    p.email AS "Email",
    v.intro_notes AS "Notes",
        CASE
            WHEN v.enrich THEN 'Yes'::text
            WHEN NOT v.enrich THEN 'No'::text
            ELSE NULL::text
        END AS "Enrich?",
    v.out_of_market AS _out_of_market
   FROM vendor v
     JOIN party p ON p.id = v.party_id
     LEFT JOIN vendor_stage vs ON vs.slug = v.stage
     LEFT JOIN vendor_category vc ON vc.slug = v.category_slug
     LEFT JOIN party org ON org.id = p.org_id
     LEFT JOIN actor owner ON owner.id = v.owner_id
     LEFT JOIN LATERAL (
       SELECT n.description
         FROM next_action n
        WHERE n.subject_type = 'vendor'::text
          AND n.subject_id = v.id
          AND n.status = 'open'::text
        ORDER BY n.due_on ASC NULLS LAST, n.created_at ASC, n.id ASC
        LIMIT 1
     ) na ON true
     LEFT JOIN v_last_touch lt ON lt.subject_type = 'vendor'::text AND lt.subject_id = v.id
  WHERE v.merged_into IS NULL;

create or replace view public.v_calendar_prebrief_events as
 SELECT e.sponsor,
    e.occurrence_key,
    e.starts_at,
    e.ends_at,
    e.title,
    e.location,
    r.ref AS participant_ref,
    r.display_name AS participant_display_name,
    r.org_name AS participant_org_name,
    r.status AS participant_status,
    lt.last_touch AS participant_last_touch,
    action.open_owner_slug AS open_owner,
    action.description AS open_action
   FROM ops.calendar_prebrief_projection_event e
     JOIN ops.calendar_prebrief_allowed_calendar a ON a.sponsor = e.sponsor AND a.active_revision_id = e.allowlist_revision_id
     LEFT JOIN ops.calendar_prebrief_projection_participant ep ON ep.event_id = e.id
     LEFT JOIN v_ref_index r ON r.ref = ep.participant_ref AND r.subject_type = ep.subject_type AND r.subject_id = ep.subject_id AND r.party_id = ep.party_id AND NOT r.merged
     LEFT JOIN v_last_touch lt ON lt.subject_type = ep.subject_type AND lt.subject_id = ep.subject_id
     LEFT JOIN LATERAL (
       SELECT n.description, o.slug AS open_owner_slug
         FROM next_action n
         LEFT JOIN actor o ON o.id = n.owner_id
        WHERE n.subject_type = ep.subject_type
          AND n.subject_id = ep.subject_id
          AND n.status = 'open'::text
          AND (n.hold_until IS NULL OR n.hold_until <= CURRENT_DATE)
        ORDER BY n.due_on ASC NULLS LAST, n.created_at ASC, n.id ASC
        LIMIT 1
     ) action ON true;

do $verify$
declare
  v_rows          bigint;
  v_distinct      bigint;
  v_lead_rows     bigint;
  v_vendor_rows   bigint;
  v_vendor_dist   bigint;
  v_multi         bigint;
  v_def           text;
begin
  -- STRUCTURAL ASSERTIONS. These hold in every environment, including a
  -- structure-only database with no business rows, which is what the local
  -- Postgres CI lane builds. They are the ones that prove the REWRITE landed.
  foreach v_def in array array['v_export_leads', 'v_export_vendors', 'v_calendar_prebrief_events'] loop
    if position('LATERAL' in upper(pg_get_viewdef(v_def::regclass, true))) = 0 then
      raise exception '0507 FAILED: % was not converted to a lateral, so it can still fan out', v_def;
    end if;
  end loop;

  -- The calendar view keeps its hold_until predicate; losing it would widen
  -- what a pre-brief surfaces, which is a behaviour change and not a fix.
  select pg_get_viewdef('v_calendar_prebrief_events'::regclass, true) into v_def;
  if position('hold_until' in v_def) = 0 then
    raise exception '0507 FAILED: the calendar pre-brief hold_until predicate was dropped';
  end if;

  -- DATA ASSERTIONS. Skipped when the database holds no leads, because an empty
  -- database cannot demonstrate a fan-out and asserting against it would pass
  -- vacuously rather than prove anything.
  select count(*) into v_lead_rows from lead;
  if v_lead_rows > 0 then
    select count(*), count(distinct "Lead ID") into v_rows, v_distinct from v_export_leads;
    if v_rows <> v_distinct then
      raise exception '0507 FAILED: v_export_leads still fans out -- % rows for % distinct Lead IDs', v_rows, v_distinct;
    end if;
    if v_rows <> v_lead_rows then
      raise exception '0507 FAILED: v_export_leads returns % rows for % lead rows; the lateral must not drop or add a lead', v_rows, v_lead_rows;
    end if;

    -- Every lead that HAS an open action must still report one. A lateral that
    -- silently returned no row would satisfy the count checks above while
    -- blanking the column the view exists to carry.
    select count(*) into v_multi
      from lead l
     where exists (select 1 from next_action n
                    where n.subject_type = 'lead' and n.subject_id = l.id and n.status = 'open')
       and (select "Next Action" from v_export_leads x where x."Lead ID" = l.registry_ref) is null;
    if v_multi > 0 then
      raise exception '0507 FAILED: % lead(s) hold an open next action that the view no longer reports', v_multi;
    end if;
  end if;

  if (select count(*) from vendor where merged_into is null) > 0 then
    select count(*), count(distinct "ID") into v_vendor_rows, v_vendor_dist from v_export_vendors;
    if v_vendor_rows <> v_vendor_dist then
      raise exception '0507 FAILED: v_export_vendors fans out -- % rows for % distinct IDs', v_vendor_rows, v_vendor_dist;
    end if;
  end if;
end
$verify$;
