-- rollback: forward-only; completed work retains its evidence and the repair retains its audit event
-- lock-review: ops.work_request size is unmeasured; replacing its CHECK takes ACCESS EXCLUSIVE and scans all rows.
-- The existing bounded operational queue remains in place; fail after 5s acquiring a lock or 60s running rather than block writers indefinitely.
set local lock_timeout = '5s';
set local statement_timeout = '60s';
create table if not exists ops.work_request_completion_receipt (
  idempotency_key uuid primary key,
  work_request_id uuid not null references ops.work_request(id),
  base_version integer not null,
  result_version integer not null,
  evidence jsonb not null,
  attestor_actor_id uuid not null references public.actor(id),
  completed_at timestamptz not null default now(),
  unique(work_request_id)
);
revoke all on ops.work_request_completion_receipt from public;

create or replace function ops.work_request_completion_receipt_append_only() returns trigger
language plpgsql as $fn$ begin raise exception 'Work Request completion receipts are append-only'; end $fn$;
drop trigger if exists completion_receipt_append_only on ops.work_request_completion_receipt;
create trigger completion_receipt_append_only before update or delete on ops.work_request_completion_receipt
for each row execute function ops.work_request_completion_receipt_append_only();

create or replace function ops.complete_work_request(
  p_ref text, p_base_version integer, p_changes jsonb, p_criteria jsonb,
  p_user_facing boolean, p_journey text, p_actor_slug text, p_key uuid)
returns table(work_request_id uuid, ref text, state text, version integer, previous_state text, closed_at timestamptz)
language plpgsql security definer set search_path to 'pg_catalog','ops','public','pg_temp' as $fn$
declare w ops.work_request%rowtype; a public.actor%rowtype; capsule jsonb; predicate jsonb;
begin
  select * into a from public.actor where slug=p_actor_slug;
  if not coalesce(a.active and (a.kind='human' or (a.kind='automation'
    and nullif(btrim(current_setting('carr.sponsoring_human_slug',true)),'') in ('joe','dell'))), false) then
    raise exception 'sponsored_actor_required: an active partner or sponsored agent must attest';
  end if;
  select * into w from ops.work_request where ops.work_request.ref=p_ref for update;
  if not found then raise exception 'work_request_not_found: %',p_ref; end if;
  if w.state in ('confirmed_closed','declined','superseded','failed') then
    raise exception 'work_request_terminal_state: % is %',p_ref,w.state;
  end if;
  if w.version is distinct from p_base_version then raise exception 'work_request_version_conflict: %',p_ref; end if;
  if w.state not in ('captured','triaged','ready','verification') then
    raise exception 'work_request_not_completable: % is %',p_ref,w.state;
  end if;
  if jsonb_typeof(p_changes) is distinct from 'array' then raise exception 'missing_change_ref: supply merged PR references'; end if;
  if jsonb_array_length(p_changes)=0 or exists(select 1 from jsonb_array_elements(p_changes) x
    where jsonb_typeof(x)<>'object' or (select count(*) from jsonb_object_keys(case when jsonb_typeof(x)='object' then x else '{}'::jsonb end))<>2
      or jsonb_typeof(x->'pr_url') is distinct from 'string' or jsonb_typeof(x->'merge_commit_sha') is distinct from 'string'
      or coalesce(x->>'pr_url','') !~ '^https://github[.]com/(DoctorCRE/carr-system|jbookout/doctorcre-app)/pull/[1-9][0-9]*$'
      or coalesce(x->>'merge_commit_sha','') !~ '^[0-9a-fA-F]{40}$') then
    raise exception 'missing_change_ref: supply a merged PR URL and its full merge commit SHA';
  end if;
  if jsonb_typeof(p_criteria) is distinct from 'array' then raise exception 'missing_criterion_evidence: supply evidence for every criterion id'; end if;
  if jsonb_array_length(w.acceptance_criteria)=0 or jsonb_array_length(p_criteria)<>jsonb_array_length(w.acceptance_criteria)
     or exists(select 1 from jsonb_array_elements(w.acceptance_criteria) criterion
       where nullif(btrim(criterion->>'id'),'') is null
          or (select count(*) from jsonb_array_elements(p_criteria) line
               where line->>'id'=criterion->>'id' and jsonb_typeof(line)='object'
                 and jsonb_typeof(line->'evidence')='string' and jsonb_typeof(line->'id')='string'
                 and nullif(btrim(line->>'evidence'),'') is not null
                 and (select count(*) from jsonb_object_keys(case when jsonb_typeof(line)='object' then line else '{}'::jsonb end))=2)<>1) then
    raise exception 'missing_criterion_evidence: supply exactly one nonempty evidence line per criterion id';
  end if;
  if p_user_facing is null then raise exception 'completion_capsule_refused: user_facing must be explicit'; end if;
  if p_user_facing and nullif(btrim(p_journey),'') is null then raise exception 'missing_user_journey_ref: user-facing work requires an exercised journey'; end if;
  select jsonb_agg(to_jsonb((line->>'id') || ': ' || (line->>'evidence'))) into predicate from jsonb_array_elements(p_criteria) line;
  capsule:=jsonb_build_object('acceptance_predicates',predicate,'change_ref',p_changes::text,
    'change_refs',p_changes,'criterion_evidence',p_criteria,'user_facing',p_user_facing,'user_journey_ref',p_journey,'attested_by',a.id::text);
  insert into ops.work_request_completion_receipt(idempotency_key,work_request_id,base_version,result_version,evidence,attestor_actor_id)
  values(p_key,w.id,w.version,w.version+1,capsule,a.id);
  begin
    update ops.work_request set state='confirmed_closed', completion_kind='built', completion_evidence=capsule,
      verification_accepted_at=now(), closed_at=now(),version=w.version+1,updated_at=now() where id=w.id;
  exception when check_violation then
    raise exception 'completion_capsule_refused: %',sqlerrm using errcode='23514';
  end;
  return query select x.id,x.ref,x.state,x.version,w.state,x.closed_at from ops.work_request x where x.id=w.id;
end $fn$;
revoke all on function ops.complete_work_request(text,integer,jsonb,jsonb,boolean,text,text,uuid) from public;
grant execute on function ops.complete_work_request(text,integer,jsonb,jsonb,boolean,text,text,uuid) to carr_writer;

alter table ops.work_request drop constraint work_request_sourced_capture_shape;
alter table ops.work_request add constraint work_request_sourced_capture_shape CHECK ((((capture_idempotency_key IS NULL) AND (organization_tenant_id IS NULL) AND (doctrine_section_id IS NULL) AND (doctrine_revision_id IS NULL) AND (sourced_capture_sequence IS NULL) AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL) AND (program_key IS NULL) AND (program_ordinal IS NULL)) OR ((capture_idempotency_key IS NULL) AND (NOT (organization_tenant_id IS DISTINCT FROM 'carr-internal'::text)) AND (doctrine_section_id IS NULL) AND (doctrine_revision_id IS NULL) AND (sourced_capture_sequence IS NULL) AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL) AND (program_key = ANY (ARRAY['carr-ai-engineering-suite-v1'::text, 'carr-system-integrity-elimination-v1'::text])) AND (program_ordinal IS NOT NULL) AND (program_ordinal > 0) AND (NOT (requester_actor IS DISTINCT FROM 'joe'::text)) AND (NOT (owner_actor IS DISTINCT FROM 'joe'::text))) OR ((capture_idempotency_key IS NOT NULL) AND (NOT (organization_tenant_id IS DISTINCT FROM 'carr-internal'::text)) AND (doctrine_section_id IS NOT NULL) AND (doctrine_revision_id IS NOT NULL) AND (sourced_capture_sequence IS NOT NULL) AND (program_key IS NULL) AND (program_ordinal IS NULL) AND (origin_ref IS NOT NULL) AND (origin_ref ~ '^doctrine:[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$'::text) AND (((state = ANY (ARRAY['captured'::text,'confirmed_closed'::text])) AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL)) OR ((state = ANY (ARRAY['triaged'::text, 'ready'::text,'verification'::text,'confirmed_closed'::text])) AND (triage_classification = ANY (ARRAY['operational'::text, 'needs_judgment'::text, 'safety_review'::text])) AND (triaged_by_actor_id IS NOT NULL) AND (triaged_at IS NOT NULL)) OR ((state = ANY (ARRAY['declined'::text, 'superseded'::text])) AND (((triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL)) OR ((state = ANY (ARRAY['declined'::text, 'superseded'::text])) AND (triage_classification = ANY (ARRAY['operational'::text, 'needs_judgment'::text, 'safety_review'::text])) AND (triaged_by_actor_id IS NOT NULL) AND (triaged_at IS NOT NULL))) AND (exit_reason IS NOT NULL) AND (btrim(exit_reason) <> ''::text) AND (closed_at IS NOT NULL) AND (((state = 'declined'::text) AND (superseded_by IS NULL)) OR ((state = 'superseded'::text) AND (superseded_by IS NOT NULL))))))));

CREATE OR REPLACE FUNCTION ops.sourced_work_request_is_immutable() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'pg_temp'
    AS $$
begin
  -- INSERT WAS NEVER COVERED. This trigger was BEFORE UPDATE only, so a sourced
  -- row could be INSERTed already terminal: no receipt, no version history, no
  -- pointer check, nothing. Every other INSERT trigger on this table returns
  -- early for these states, so the constraint alone stood between a fabricated
  -- withdrawal and the table. A sourced request is born captured or not at all.
  if tg_op = 'INSERT' then
    if new.capture_idempotency_key is not null and new.state <> 'captured' then
      raise exception 'a sourced Work Request must be INSERTed in state captured, not %', new.state;
    end if;
    return new;
  end if;

  if old.capture_idempotency_key is null then
    return new;
  end if;

  if old.state in ('captured','triaged','ready','verification') and new.state='confirmed_closed'
     and new.completion_kind='built' and new.version=old.version+1
     and (to_jsonb(new)-array['state','completion_kind','completion_evidence','verification_accepted_at','closed_at','version','updated_at'])
       is not distinct from
       (to_jsonb(old)-array['state','completion_kind','completion_evidence','verification_accepted_at','closed_at','version','updated_at'])
     and exists(select 1 from ops.work_request_completion_receipt r where r.work_request_id=old.id
       and r.base_version=old.version and r.result_version=new.version and r.evidence=new.completion_evidence) then
    return new;
  end if;

  if old.state = 'captured'
     and new.state = 'triaged'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','triage_classification','triaged_by_actor_id','triaged_at','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['state','triage_classification','triaged_by_actor_id','triaged_at','version','updated_at'])
     and exists (
       select 1 from ops.work_request_triage_receipt r
        where r.work_request_id = old.id and r.base_version = old.version
          and r.result_version = new.version and r.classification = new.triage_classification
          and r.triaged_by_actor_id = new.triaged_by_actor_id and r.triaged_at = new.triaged_at
     ) then
    return new;
  end if;

  if old.state = 'triaged'
     and new.state = 'triaged'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at','version','updated_at'])
     and exists (
       select 1 from ops.effective_sourced_work_request_shape_disposition(new) e
        where e.base_version = old.version and e.result_version = new.version
     ) then
    return new;
  end if;

  if old.state = 'triaged'
     and new.state = 'ready'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','version','updated_at','shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at'])
           is not distinct from
         (to_jsonb(old) - array['state','version','updated_at','shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at'])
     and exists (
       select 1
         from ops.sourced_work_request_plan_acceptance_receipt ar
         join ops.sourced_work_request_plan p on p.id = ar.plan_id
         left join ops.sourced_work_request_plan_shape_binding_receipt b
           on b.plan_acceptance_receipt_id = ar.id
        where ar.work_request_id = old.id and ar.base_version = old.version
          and ar.result_version = new.version and ar.plan_hash = p.plan_hash
          and (
            (b.id is null
             and new.shape_disposition = 'not_required'
             and new.shape_fixed_surface_ref = ar.shape_fixed_surface_ref
             and new.shape_fixed_surface_ref = ('sourced-plan:' || p.plan_ref || '#' || p.plan_hash)
             and new.shape_rationale = ar.shape_rationale
             and new.shape_rationale = ('Accepted immutable plan ' || p.plan_ref || ' for ' || p.runbook_ref || ' at sha256:' || p.runbook_content_hash)
             and new.shape_decided_by_actor_id = ar.accepted_by_actor_id
             and new.shape_decided_at = ar.accepted_at)
            or
            (b.id is not null
             and b.work_request_id = old.id
             and (new.shape_disposition,new.shape_fixed_surface_ref,new.shape_rationale,new.shape_decided_by_actor_id,new.shape_decided_at)
                 is not distinct from
                 (b.disposition,b.fixed_surface_ref,b.rationale,b.decided_by_actor_id,b.decided_at))
          )
     ) then
    return new;
  end if;


  -- A ready plan may move only to an independently accepted successor on the
  -- same Work Request.  No business field changes; the version bump makes all
  -- predecessor envelopes, slice plans, leases, and feedback bindings stale.
  if old.state = 'ready'
     and new.state = 'ready'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['version','updated_at']) is not distinct from
         (to_jsonb(old) - array['version','updated_at'])
     and exists (
       select 1 from ops.ready_plan_amendment_acceptance_receipt a
       join ops.sourced_work_request_plan_acceptance_receipt ar
         on ar.id = a.plan_acceptance_receipt_id
        where a.work_request_id = old.id and a.base_version = old.version
          and a.result_version = new.version and ar.plan_id = a.successor_plan_id
          and ar.base_version = old.version and ar.result_version = new.version
     ) then
    return new;
  end if;

  -- CAPTURED -> DECLINED or SUPERSEDED, receipt-backed like every other arm.
  -- Only from captured: a triaged row carries a triage receipt and may carry a
  -- shape disposition, and withdrawing it would strand both.
  if old.state = 'captured'
     and new.state in ('declined','superseded')
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','exit_reason','closed_at','superseded_by','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['state','exit_reason','closed_at','superseded_by','version','updated_at'])
     and exists (
       select 1 from ops.work_request_withdrawal_receipt r
        where r.work_request_id = old.id and r.base_version = old.version
          and r.result_version = new.version and r.final_state = new.state
          and r.exit_reason = new.exit_reason
          and r.superseded_by is not distinct from new.superseded_by
     ) then
    return new;
  end if;

  -- TRIAGED -> SUPERSEDED (0888). The triage fields stay on the row and the
  -- triage receipt stays attached; nothing else may name the request.
  if old.state = 'triaged'
     and new.state = 'superseded'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','exit_reason','closed_at','superseded_by','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['state','exit_reason','closed_at','superseded_by','version','updated_at'])
     and exists (
       select 1 from ops.work_request_withdrawal_receipt r
        where r.work_request_id = old.id and r.base_version = old.version
          and r.result_version = new.version and r.final_state = 'superseded'
          and r.exit_reason = new.exit_reason
          and r.superseded_by is not distinct from new.superseded_by
     )
     and current_setting('transaction_isolation') = 'read committed'
     and cardinality(ops.work_request_supersession_strands(old.id)) = 0 then
    return new;
  end if;

  if old.state = 'ready' and new.state = 'declined' and (
       old.claimed_at is not null or old.executor_actor is not null or old.started_at is not null
       or exists (select 1 from ops.work_request_execution_assignment a where a.work_request_id = old.id)
       or current_setting('transaction_isolation') <> 'read committed'
     ) then
    raise exception 'work_request_not_declinable: % is ready with downstream execution or outside read committed', old.ref;
  end if;

  if old.state in ('triaged','ready')
     and new.state = 'declined'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','exit_reason','closed_at','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['state','exit_reason','closed_at','version','updated_at'])
     and exists (
       select 1 from ops.work_request_withdrawal_receipt r
        where r.work_request_id = old.id and r.base_version = old.version
          and r.result_version = new.version and r.final_state = 'declined'
          and r.exit_reason = new.exit_reason and r.superseded_by is null
     ) then
    return new;
  end if;

  raise exception 'sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, triaged-to-ready, captured-to-withdrawn, or triaged-to-withdrawn or ready-to-declined transitions';
end;
$$;


do $repair$
declare definition text; repaired record;
begin
  definition := pg_get_functiondef('ops.sourced_work_request_is_immutable()'::regprocedure);
  execute replace(definition, 'begin', $arm$begin
  if tg_op='UPDATE' and old.ref in ('WR-000414','WR-000436','WR-000499','WR-000500','WR-000501','WR-000507','WR-000510','WR-000520','WR-000542','WR-000545','WR-000546','WR-000551','WR-000552','WR-000553')
    and old.state='declined' and (old.exit_reason like 'Done on main%' or old.exit_reason like 'Done:%')
    and new.state='captured' and new.closed_at is null and new.exit_reason is null
    and new.version=old.version+1
    and (to_jsonb(new)-array['state','closed_at','exit_reason','version','updated_at']) is not distinct from
        (to_jsonb(old)-array['state','closed_at','exit_reason','version','updated_at']) then return new; end if;
$arm$);
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
      jsonb_build_object('state','captured','version',repaired.version,'reason','Built work was incorrectly withdrawn. Reopened for evidence-backed complete-work-request.','migration','1005_complete_work_request'),'import_migration');
  end loop;
  execute definition;
end $repair$;
