-- rollback: forward-only; completed work retains its evidence and the repair retains its audit event
-- lock-review: ops.work_request size is unmeasured; replacing its CHECK takes ACCESS EXCLUSIVE and scans all rows.
set local lock_timeout = '5s';
set local statement_timeout = '60s';
alter table ops.work_request drop constraint work_request_sourced_capture_shape;
alter table ops.work_request add constraint work_request_sourced_capture_shape CHECK ((((capture_idempotency_key IS NULL) AND (organization_tenant_id IS NULL) AND (doctrine_section_id IS NULL) AND (doctrine_revision_id IS NULL) AND (sourced_capture_sequence IS NULL) AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL) AND (program_key IS NULL) AND (program_ordinal IS NULL)) OR ((capture_idempotency_key IS NULL) AND (NOT (organization_tenant_id IS DISTINCT FROM 'carr-internal'::text)) AND (doctrine_section_id IS NULL) AND (doctrine_revision_id IS NULL) AND (sourced_capture_sequence IS NULL) AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL) AND (program_key = ANY (ARRAY['carr-ai-engineering-suite-v1'::text, 'carr-system-integrity-elimination-v1'::text])) AND (program_ordinal IS NOT NULL) AND (program_ordinal > 0) AND (NOT (requester_actor IS DISTINCT FROM 'joe'::text)) AND (NOT (owner_actor IS DISTINCT FROM 'joe'::text))) OR ((capture_idempotency_key IS NOT NULL) AND (NOT (organization_tenant_id IS DISTINCT FROM 'carr-internal'::text)) AND (doctrine_section_id IS NOT NULL) AND (doctrine_revision_id IS NOT NULL) AND (sourced_capture_sequence IS NOT NULL) AND (program_key IS NULL) AND (program_ordinal IS NULL) AND (origin_ref IS NOT NULL) AND (origin_ref ~ '^doctrine:[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$'::text) AND (((state = ANY (ARRAY['captured'::text,'confirmed_closed'::text])) AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL)) OR ((state = ANY (ARRAY['triaged'::text, 'ready'::text,'verification'::text,'confirmed_closed'::text])) AND (triage_classification = ANY (ARRAY['operational'::text, 'needs_judgment'::text, 'safety_review'::text])) AND (triaged_by_actor_id IS NOT NULL) AND (triaged_at IS NOT NULL)) OR ((state = ANY (ARRAY['declined'::text, 'superseded'::text])) AND (((triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL)) OR ((state = ANY (ARRAY['declined'::text, 'superseded'::text])) AND (triage_classification = ANY (ARRAY['operational'::text, 'needs_judgment'::text, 'safety_review'::text])) AND (triaged_by_actor_id IS NOT NULL) AND (triaged_at IS NOT NULL))) AND (exit_reason IS NOT NULL) AND (btrim(exit_reason) <> ''::text) AND (closed_at IS NOT NULL) AND (((state = 'declined'::text) AND (superseded_by IS NULL)) OR ((state = 'superseded'::text) AND (superseded_by IS NOT NULL))))))));

do $lifecycle$
declare definition text; marker text := '  if old.state = ''captured''';
begin
  definition := pg_get_functiondef('ops.sourced_work_request_is_immutable()'::regprocedure);
  if position('-- built completion' in definition) = 0 then
    if position(marker in definition) = 0 then raise exception 'Sourced lifecycle insertion point drifted'; end if;
    execute regexp_replace(definition, marker, $arm$  -- built completion delegates evidence rules to enforce_completion_capsule.
  if old.state in ('captured','triaged','ready','verification') and new.state='confirmed_closed'
     and new.completion_kind='built' and new.version=old.version+1
     and (to_jsonb(new)-array['state','completion_kind','completion_evidence','verification_accepted_at','closed_at','version','updated_at'])
       is not distinct from
       (to_jsonb(old)-array['state','completion_kind','completion_evidence','verification_accepted_at','closed_at','version','updated_at']) then
    return new;
  end if;
$arm$ || marker);
  end if;
end $lifecycle$;

do $repair$
declare repaired record;
begin
  alter table ops.work_request disable trigger sourced_work_request_is_immutable;
  for repaired in
    with candidates as materialized (
      select id,ref,state,version,exit_reason from ops.work_request
      where ref in ('WR-000414','WR-000436','WR-000499','WR-000500','WR-000501','WR-000507','WR-000510','WR-000520','WR-000542','WR-000545','WR-000546','WR-000551','WR-000552','WR-000553')
        and state='declined' and (exit_reason like 'Done on main%' or exit_reason like 'Done:%') for update
    ), changed as (
      update ops.work_request w set state='captured',closed_at=null,exit_reason=null,version=w.version+1,updated_at=now()
      from candidates x where w.id=x.id returning w.id,w.ref,w.version,x.version as old_version,x.exit_reason as old_reason
    ) select * from changed
  loop
    insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause)
    values(now(),(select id from public.actor where slug='joe'),'repair-built-work-request-decline','ops_work_request',repaired.id,'state',
      jsonb_build_object('state','declined','version',repaired.old_version,'exit_reason',repaired.old_reason),
      jsonb_build_object('state','captured','version',repaired.version,'reason','Built work was incorrectly withdrawn. Reopened for evidence-backed complete-work-request.','migration','1006_complete_work_request'),'import_migration');
  end loop;
  alter table ops.work_request enable trigger sourced_work_request_is_immutable;
end $repair$;
