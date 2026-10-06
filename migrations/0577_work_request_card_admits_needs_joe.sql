-- 0577_work_request_card_admits_needs_joe.sql
--
-- The gap this closes: 0575/0576 added answer-work-request-for-joe, the sole
-- verb that resolves a needs_joe Work Request -- but ops.work_request_card
-- (latest full body: migration 0493) refused to READ one. Its state filter
-- named only ('captured','triaged','ready','declined','superseded'), so the
-- app's Model Room card for the exact row Joe is about to answer came back
-- work_request_not_found, and the answer verb's own base_version and
-- acceptance_criteria -- the two things scope_confirmed is supposed to
-- revalidate -- had no read path at all. "authorized human decision and
-- evidence recorded; scope and acceptance criteria revalidated" cannot be
-- honoured by a human who cannot see the criteria being revalidated.
--
-- Widening the state list alone would have been dead code. needs_joe is
-- reachable ONLY by a general or program row (ops.work_request_sourced_
-- capture_shape, 0426, never admits needs_joe for a sourced row -- see
-- 0575's own header), and every existing INNER JOIN and equality filter in
-- this function assumed the opposite: a sourced or program row, always
-- carrying organization_tenant_id='carr-internal' and a doctrine_section_id
-- pointing at a real, shared doctrine_section/doctrine_document pair. A
-- general row's organization_tenant_id and doctrine_section_id are NULL by
-- the same CHECK constraint's first branch.
--
-- ONLY THE GENERAL needs_joe ROW SHAPE IS WIDENED, not "any row with no
-- doctrine section, in any state". An earlier draft of this migration
-- relaxed the tenant and visibility checks with plain OR-widenings
-- (organization_tenant_id IS NULL, d.visibility IS NULL) independently of
-- state, which admitted a general row in ANY of the five pre-existing
-- states too -- a general captured row, for instance, which review-and-
-- triage refuses outright because it is sourced-only. The final admission
-- predicate is instead one exclusive disjunction with no independent OR
-- anywhere else in the WHERE clause:
--
--   (w.doctrine_section_id is not null and d.visibility='shared')
--   or (w.state='needs_joe' and w.doctrine_section_id is null)
--
-- The first arm is EXACTLY the original sourced/program-row admission
-- (doctrine_section_id populated, document shared) with the join widened
-- from INNER to LEFT so it still evaluates rather than eliminating the row
-- outright; the second arm admits ONLY a general row (no doctrine section)
-- and ONLY in needs_joe. No other combination of doctrine-section-presence
-- and state passes. Two supporting changes travel with it:
--
--   * the doctrine_section / doctrine_document joins become LEFT joins, so
--     the first arm above can evaluate d.visibility without eliminating a
--     general row before the WHERE clause is reached;
--   * source_current is coalesced to false rather than left to evaluate
--     against a null doctrine_section, so a needs_joe card with no source
--     reads as "not current" rather than an ambiguous null.
--
-- THE FIRST ARM ALSO EXCLUDES needs_joe EXPLICITLY, not only implicitly
-- through the second arm's shape. Without "and w.state<>'needs_joe'" on the
-- first arm, a REAL sourced row illegally forced into needs_joe -- which
-- program6-human-triage-gate.py and program6-sourced-routine-gate.py both
-- manufacture on purpose, by disabling the sourced-row CHECK and immutability
-- trigger and setting state='needs_joe' directly, then asserting the card
-- still refuses it -- would still satisfy "doctrine_section_id is not null
-- and d.visibility='shared'" on its own, since that manufactured row keeps
-- its real doctrine linkage and the outer state list already admits
-- needs_joe. A first cut of this predicate that omitted the extra state
-- exclusion passed every ordinary case but reopened exactly that hole, and
-- both gates caught it on the next disposable-PostgreSQL run. With the
-- exclusion, the two arms partition the six admitted states cleanly: the
-- first is "one of the five original states, with real shared doctrine,
-- never needs_joe"; the second is "needs_joe, with no doctrine at all". A
-- row can satisfy at most one arm, and the manufactured bypass row satisfies
-- neither.
--
-- Every existing sourced or program row's projection is completely
-- unchanged: for those rows doctrine_section_id is always populated, so the
-- first arm is exactly the original admission condition, and source_current's
-- coalesce is a no-op (the expression it wraps was already boolean, never
-- null, when s exists).
--
-- The row-level tenant check (further down, unchanged in shape from the
-- earlier draft: "organization_tenant_id is null or organization_tenant_id
-- ='carr-internal'") stays permissive on the null side, because a general
-- row's organization_tenant_id is NULL by the same CHECK constraint's first
-- branch. It no longer does any of the actual scoping work, though: the
-- doctrine-section/state predicate above is what refuses every general row
-- except needs_joe, so this check merely admits the tenant value every row
-- that already passed that predicate necessarily carries.
--
-- Signature and grants are unchanged: create or replace is used rather than
-- drop-then-create because the return table's columns are identical to
-- 0493's, and PostgreSQL preserves the function's existing grants across a
-- create-or-replace that does not touch the signature.
--
-- No explicit transaction control: from 0339 onward tools/migrate.py runs
-- each migration inside its own single transaction.

create or replace function ops.work_request_card(p_work_request text, p_organization_tenant_id text) RETURNS TABLE(ref text, title text, state text, version integer, origin_ref text, desired_outcome text, acceptance_criteria jsonb, doctrine_section_id uuid, doctrine_revision_id uuid, doctrine_source_label text, source_current boolean, triage_classification text, triaged_by_actor_slug text, triaged_at timestamp with time zone, plan_ref text, plan_hash text, scope_summary text, runbook_ref text, runbook_revision_id uuid, runbook_content_hash text, plan_caps jsonb, dependency_refs jsonb, recovery_ref text, observability_ref text, accepted_by_actor_slug text, accepted_at timestamp with time zone, shape_disposition text, shape_fixed_surface_ref text, outcome_feedback jsonb, outcome_feedback_history jsonb, accepted_feedback_count bigint, exit_reason text, closed_at timestamp with time zone, superseded_by_ref text, incident_evidence jsonb)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops'
    AS $$
  select w.ref,w.title,w.state,w.version,w.origin_ref,w.desired_outcome,
         w.acceptance_criteria,w.doctrine_section_id,w.doctrine_revision_id,
         coalesce(s.title,s.section_key),
         coalesce(s.status='active' and s.current_revision_id=w.doctrine_revision_id, false),
         w.triage_classification,ta.slug,w.triaged_at,
         p.plan_ref,p.plan_hash,p.scope_summary,p.runbook_ref,p.runbook_revision_id,
         case when p.runbook_content_hash is null then null else 'sha256:' || p.runbook_content_hash end,
         p.caps,p.dependency_refs,p.recovery_ref,p.observability_ref,aa.slug,ar.accepted_at,
         w.shape_disposition,w.shape_fixed_surface_ref,
         latest.feedback,coalesce(history.feedback_history,'[]'::jsonb),coalesce(counted.accepted_count,0),
         w.exit_reason,w.closed_at,succ.ref,coalesce(incident_evidence.items,'[]'::jsonb)
    from ops.work_request w
    left join public.doctrine_section s on s.id=w.doctrine_section_id
    left join public.doctrine_document d on d.id=s.document_id
    left join public.actor ta on ta.id=w.triaged_by_actor_id
    left join ops.work_request succ on succ.id=w.superseded_by
    left join lateral (
      select x.* from ops.sourced_work_request_plan x
      left join ops.sourced_work_request_plan_acceptance_receipt accepted on accepted.plan_id=x.id
       where x.work_request_id=w.id
       order by (accepted.id is not null) desc,x.plan_version desc limit 1
    ) p on true
    left join ops.sourced_work_request_plan_acceptance_receipt ar on ar.plan_id=p.id
    left join public.actor aa on aa.id=ar.accepted_by_actor_id
    left join lateral (
      select jsonb_build_object(
        'feedback_ref',f.feedback_ref,'feedback_hash',f.feedback_hash,'outcome',f.outcome,
        'criterion_results',f.criterion_results,'evidence_refs',f.evidence_refs,
        'blocker_code',f.blocker_code,'result_summary',f.result_summary,
        'observed_minutes',f.observed_minutes,'interaction_surface',f.interaction_surface,
        'heavy_session_used',f.heavy_session_used,'manual_context_transfers',f.manual_context_transfers,
        'accepted_by_actor_slug',fa.slug,'accepted_at',fr.accepted_at) as feedback
        from ops.sourced_work_request_outcome_feedback f
        join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
        join public.actor fa on fa.id=fr.accepted_by_actor_id
       where f.work_request_id=w.id
       order by fr.accepted_at desc,fr.id desc limit 1
    ) latest on true
    left join lateral (
      select jsonb_agg(h.feedback order by h.accepted_at,h.acceptance_id) as feedback_history
        from (
          select fr.accepted_at,fr.id as acceptance_id,jsonb_build_object(
            'feedback_ref',f.feedback_ref,'feedback_hash',f.feedback_hash,'outcome',f.outcome,
            'criterion_results',f.criterion_results,'evidence_refs',f.evidence_refs,
            'blocker_code',f.blocker_code,'result_summary',f.result_summary,
            'observed_minutes',f.observed_minutes,'interaction_surface',f.interaction_surface,
            'heavy_session_used',f.heavy_session_used,'manual_context_transfers',f.manual_context_transfers,
            'accepted_by_actor_slug',fa.slug,'accepted_at',fr.accepted_at) as feedback
            from ops.sourced_work_request_outcome_feedback f
            join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
            join public.actor fa on fa.id=fr.accepted_by_actor_id
           where f.work_request_id=w.id
           order by fr.accepted_at desc,fr.id desc limit 20
        ) h
    ) history on true
    left join lateral (
      select count(*)::bigint as accepted_count
        from ops.sourced_work_request_outcome_feedback f
        join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
       where f.work_request_id=w.id
    ) counted on true
    left join lateral (
      select jsonb_agg(to_jsonb(projected) order by projected.detected_at,projected.incident_ref) as items
        from (
          select i.ref as incident_ref,i.title,i.state,i.severity,i.environment,
                 to_jsonb(i.detected_at)#>>'{}' as detected_at,
                 to_jsonb(i.observed_at)#>>'{}' as observed_at,
                 to_jsonb(i.resolved_at)#>>'{}' as resolved_at,
                 occurrence.occurrences,occurrence.occurrence_evidence_status,
                 occurrence.legacy_overlap_unknown,occurrence.unresolved_occurrence_edge_count,
                 jsonb_build_object('kind','work_request','ref',w.ref) as association,
                 coalesce(evidence.items,'[]'::jsonb) as evidence
            from ops.incident_link anchor
            join ops.incident i on i.id=anchor.incident_id
            left join lateral (
              with occurrence_links as (
                select l.kind,l.ref,
                       case when l.ref ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                            then l.ref::uuid else null::uuid end as target_uuid
                  from ops.incident_link l
                 where l.incident_id=i.id and l.kind in ('run','deployment')
              ), resolved_links as (
                select x.kind,x.ref,
                       case when x.kind='run' then
                              (select r.correlation_id::text from ops.run r where r.id=x.target_uuid)
                            when x.kind='deployment' then
                              (select dep.correlation_id::text from ops.deployment dep where dep.id=x.target_uuid)
                       end as resolved_correlation
                  from occurrence_links x
              ), correlation_suffixes as (
                select distinct substring(f.source_ref from 13) as correlation_suffix
                  from ops.incident_fact f
                 where f.incident_id=i.id and f.source_ref like 'correlation:%'
              ), occurrence_counts as (
                select (select count(*) from resolved_links)::int as link_count,
                       (select count(*) from correlation_suffixes)::int as correlation_count,
                       (select count(*) from resolved_links where resolved_correlation is null)::int as unresolved_count,
                       (select count(*) from correlation_suffixes c where not exists (
                          select 1 from resolved_links r where r.resolved_correlation=c.correlation_suffix
                       ))::int as unpaired_correlation_count
              )
              select case when unresolved_count=0
                          then greatest(1,link_count+unpaired_correlation_count)
                          else greatest(1,link_count,correlation_count) end::int as occurrences,
                     case when unresolved_count>0 then 'legacy_overlap_unknown' else 'complete' end as occurrence_evidence_status,
                     (unresolved_count>0) as legacy_overlap_unknown,
                     unresolved_count as unresolved_occurrence_edge_count
                from occurrence_counts
            ) occurrence on true
            left join lateral (
              select jsonb_agg(item order by item->>'occurred_at' nulls last,item->>'kind',
                                            coalesce(item->>'ref',item->>'source_ref')) as items
                from (
                  select jsonb_build_object('evidence_type','link','kind',l.kind,'ref',l.ref,
                                            'occurred_at',null) as item
                    from ops.incident_link l
                   where l.incident_id=i.id and l.kind in ('run','deployment')
                  union all
                  select jsonb_build_object('evidence_type','fact','kind','fact','text',f.text,
                                            'source_ref',f.source_ref,
                                            'recorded_at',to_jsonb(f.recorded_at)#>>'{}',
                                            'occurred_at',to_jsonb(f.recorded_at)#>>'{}') as item
                    from ops.incident_fact f
                   where f.incident_id=i.id and f.source_ref is not null
                  union all
                  select jsonb_build_object('evidence_type','trace','kind',t.kind,'ref',t.ref,
                                            'correlation_id',t.correlation_id,'state',t.state,
                                            'environment',t.environment,'service_key',t.service_key,
                                            'failure_class',t.failure_class,'detail',t.detail,
                                            'source_kind',t.source_kind,'source_ref',t.source_ref,
                                            'freshness_state',t.freshness_state,
                                            'occurred_at',to_jsonb(t.occurred_at)#>>'{}') as item
                    from ops.v_trace t
                   where t.correlation_id=i.correlation_id
                      or t.correlation_id in (
                        select substring(f.source_ref from 13)::uuid
                          from ops.incident_fact f
                         where f.incident_id=i.id
                           and f.source_ref ~ '^correlation:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                      )
                ) evidence_rows
            ) evidence on true
           where anchor.kind='work_request' and anchor.ref=w.ref
        ) projected
    ) incident_evidence on true
   where p_organization_tenant_id='carr-internal'
     and (w.organization_tenant_id is null or w.organization_tenant_id='carr-internal') and w.ref=p_work_request
     and w.state in ('captured','triaged','ready','needs_joe','declined','superseded')
     and ((w.doctrine_section_id is not null and d.visibility='shared' and w.state<>'needs_joe')
          or (w.state='needs_joe' and w.doctrine_section_id is null));
$$;

grant execute on function ops.work_request_card(text,text) to carr_reader,carr_writer;
