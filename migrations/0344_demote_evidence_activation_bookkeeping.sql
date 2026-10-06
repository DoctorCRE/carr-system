-- 0344_demote_evidence_activation_bookkeeping.sql
-- WR-000019 slice S6 (Obedience & Autonomy heavy build), DB ALIGNMENT.
--
-- register-execution-environment-provider and transition-evaluation-case
-- (mcp-server/src/evidence-activation.js) are demoted off the JS authorityOnly
-- flag this slice: neither grants authority nor executes anything — the first
-- only quarantines a digest-pinned manifest for later conformance testing, the
-- second only advances an evaluation-case's own lifecycle bookkeeping and can
-- never promote a model, provider, or workflow (see the source comments added
-- alongside this migration). Their underlying SQL functions, though, hard-wired
-- `session_user !~ '^carr_authority_'` as an exception -- a demotion at the JS
-- layer alone would still fail every call for a sponsored non-authority agent,
-- because (a) EXECUTE was never granted to carr_writer and (b) the function
-- derives its acting actor by peeling the slug out of session_user, which only
-- ever has a per-partner shape (carr_authority_joe / carr_authority_dell) on
-- the authority connection.
--
-- This migration is additive, not a narrowing: the carr_authority_* path is
-- byte-for-byte unchanged (same regexp-derived slug, same actor lookup). It
-- adds exactly one more accepted session shape -- plain carr_writer -- whose
-- acting actor is resolved from a new session-scoped GUC,
-- carr.acting_actor_slug, set by the calling JS handler the same way tenant
-- scope is already set for every other non-authority evidence-activation
-- write (`select set_config('carr.organization_tenant_id', ...)`). Any other
-- session_user is still refused exactly as before.
--
-- transition-execution-environment-provider is NOT touched here: it is the
-- verb that can drive a provider to 'active' production capability (and is
-- the rollback path off it), so it stays authority-only end to end, JS and DB
-- alike -- this migration's survey confirms it belongs on the "leave" side.
--
-- kind IN ('human','automation') widens the earlier kind='human' restriction
-- so a sponsored Claude/Codex actor row (kind='automation', e.g. slug='codex')
-- resolves too -- exactly the identity class authorityOnly removal is meant to
-- admit. 'system' actors are still excluded: nothing in this path is meant to
-- run as an unattended system identity with no accountable sponsor.

begin;

create or replace function ops.register_execution_environment_provider(p_manifest jsonb, p_idempotency_key uuid) returns table(provider_ref text, manifest_digest text, state text, replayed boolean)
    language plpgsql security definer
    set search_path to 'ops', 'public', 'pg_temp'
    as $_$
declare actor_row actor%rowtype; existing ops.execution_environment_provider%rowtype;
  row_out ops.execution_environment_provider%rowtype; digest_value text; v_actor_slug text; allowed text[] := array[
    'schema_version','provider_key','provider_version','display_name','source_class','backend_kind',
    'implementation_ref','implementation_digest','capability_refs','operation_refs','isolation_class',
    'egress_policy_ref','secret_policy_ref','persistence_mode','resource_policy_ref','cleanup_policy_ref',
    'threat_model_ref','conformance_contract_ref','conformance_contract_digest','configuration_schema_digest',
    'package_provenance','collision_policy','contains_secrets','manifest_digest'];
begin
  if session_user ~ '^carr_authority_' then
    v_actor_slug := regexp_replace(session_user,'^carr_authority_','');
  elsif session_user = 'carr_writer' then
    v_actor_slug := nullif(btrim(current_setting('carr.acting_actor_slug', true)), '');
  else
    raise exception 'provider registration requires the authority connection or a sponsored writer session';
  end if;
  select * into actor_row from actor where slug=v_actor_slug and kind in ('human','automation') and active;
  if actor_row.id is null or jsonb_typeof(p_manifest)<>'object'
     or not (p_manifest ?& allowed)
     or exists(select 1 from jsonb_object_keys(p_manifest) k where k<>all(allowed))
     or p_manifest->>'schema_version'<>'execution-environment-provider.v1'
     or p_manifest->>'source_class'<>'plugin' or p_manifest->>'collision_policy'<>'digest_pinned'
     or p_manifest->>'contains_secrets'<>'false'
     or p_manifest->>'provider_key' !~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'
     or p_manifest->>'provider_key'=any(array['hermes-local','hermes-docker','hermes-ssh','hermes-singularity','hermes-modal','hermes-daytona','hermes-vercel-sandbox'])
     or coalesce((p_manifest->>'provider_version')::integer,0)<1
     or p_manifest->>'backend_kind' not in ('none','local','container','remote','cloud')
     or p_manifest->>'isolation_class' not in ('none','host_process','container','microvm','remote_host')
     or jsonb_typeof(p_manifest->'capability_refs')<>'array' or jsonb_array_length(p_manifest->'capability_refs')=0
     or jsonb_typeof(p_manifest->'operation_refs')<>'array'
     or not (p_manifest->'operation_refs' ?& array['operation:create','operation:exec','operation:cancel','operation:destroy','operation:health'])
     or jsonb_typeof(p_manifest->'package_provenance')<>'object'
     or not (p_manifest->'package_provenance' ?& array['package_ref','package_digest','signature_ref','sbom_ref'])
     or exists(select 1 from jsonb_object_keys(p_manifest->'package_provenance') k where k<>all(array['package_ref','package_digest','signature_ref','sbom_ref']))
     or p_manifest->>'display_name' !~ '^.{1,80}$'
     or p_manifest->>'display_name' ~ '[[:cntrl:]]'
     or p_manifest->>'implementation_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->>'implementation_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->>'conformance_contract_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->>'conformance_contract_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->>'configuration_schema_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->'package_provenance'->>'package_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->'package_provenance'->>'package_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->'package_provenance'->>'signature_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->'package_provenance'->>'sbom_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->>'persistence_mode' not in ('none','command_scoped','session_scoped','durable_workspace')
     or exists(select 1 from unnest(array['egress_policy_ref','secret_policy_ref','resource_policy_ref','cleanup_policy_ref','threat_model_ref']) field where p_manifest->>field !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$')
     or exists(select 1 from jsonb_array_elements_text(p_manifest->'operation_refs') op where op !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$')
     or (select count(*) from jsonb_array_elements_text(p_manifest->'operation_refs'))<>(select count(distinct op) from jsonb_array_elements_text(p_manifest->'operation_refs') op)
     or (select count(*) from jsonb_array_elements_text(p_manifest->'capability_refs'))<>(select count(distinct cap) from jsonb_array_elements_text(p_manifest->'capability_refs') cap)
     or exists(select 1 from jsonb_array_elements_text(p_manifest->'capability_refs') c where c not in ('environment:none','environment:exec','environment:filesystem','environment:process','environment:network-governed','environment:snapshot','environment:transfer','environment:persistent-workspace')) then
    raise exception 'execution environment plugin manifest is not closed, safe, or complete';
  end if;
  digest_value := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(p_manifest-'manifest_digest'),'sha256'),'hex');
  if p_manifest->>'manifest_digest' is distinct from digest_value then raise exception 'execution environment plugin manifest digest mismatch'; end if;
  select * into existing from ops.execution_environment_provider where idempotency_key=p_idempotency_key for share;
  if found then
    if existing.manifest is distinct from p_manifest then raise exception 'execution environment provider idempotency conflict'; end if;
    return query select 'environment-provider:'||existing.provider_key||':v'||existing.provider_version,existing.manifest_digest,ops.execution_environment_provider_current_state(existing.id),true; return;
  end if;
  if exists(select 1 from ops.execution_environment_provider p where p.provider_key=p_manifest->>'provider_key' and (p.protected_builtin or p.provider_version>=(p_manifest->>'provider_version')::integer)) then
    raise exception 'execution environment provider key/version is protected, stale, or already registered';
  end if;
  insert into ops.execution_environment_provider(provider_key,provider_version,source_class,backend_kind,manifest_digest,manifest,protected_builtin,created_by_actor_id,idempotency_key)
  values(p_manifest->>'provider_key',(p_manifest->>'provider_version')::integer,'plugin',p_manifest->>'backend_kind',digest_value,p_manifest,false,actor_row.id,p_idempotency_key) returning * into row_out;
  insert into ops.execution_environment_provider_event(provider_id,from_state,to_state,evidence_refs,ruled_by_actor_id,idempotency_key)
  values(row_out.id,null,'discovered',jsonb_build_array('evidence:human-provider-registration'),actor_row.id,p_idempotency_key);
  return query select 'environment-provider:'||row_out.provider_key||':v'||row_out.provider_version,row_out.manifest_digest,'discovered',false;
end $_$;

create or replace function ops.transition_proposed_eval_candidate(p_work_request text, p_candidate_ref text, p_next_state text, p_decision_basis jsonb, p_idempotency_key uuid) returns table(candidate_id uuid, lifecycle text, golden_member boolean)
    language plpgsql security definer
    set search_path to 'ops', 'public', 'pg_temp'
    as $$
declare tenant text := current_setting('carr.organization_tenant_id', true); candidate ops.proposed_eval_candidate%rowtype; actor_row actor%rowtype;
  current_state text; replay_event ops.proposed_eval_candidate_event%rowtype; v_actor_slug text;
begin
  if session_user ~ '^carr_authority_' then
    v_actor_slug := regexp_replace(session_user,'^carr_authority_','');
  elsif session_user = 'carr_writer' then
    v_actor_slug := nullif(btrim(current_setting('carr.acting_actor_slug', true)), '');
  else
    raise exception 'eval candidate transition requires the authority connection or a sponsored writer session';
  end if;
  select * into actor_row from actor where slug=v_actor_slug and kind in ('human','automation') and active;
  select p.* into candidate from ops.proposed_eval_candidate p join ops.attempt_receipt r on r.id=p.attempt_receipt_id join ops.work_request w on w.id=r.work_request_id
   where p.organization_tenant_id=tenant and p.candidate_ref=p_candidate_ref and w.ref=p_work_request and w.organization_tenant_id=tenant for update of p;
  if actor_row.id is null or candidate.id is null or jsonb_typeof(p_decision_basis)<>'object' then raise exception 'eval candidate transition lacks visible authority/candidate/basis'; end if;
  if ops.attempt_receipt_contains_raw_content(p_decision_basis) then raise exception 'eval candidate decision basis must be metadata-only'; end if;
  select * into replay_event from ops.proposed_eval_candidate_event where idempotency_key=p_idempotency_key for share;
  if found then
    if replay_event.candidate_id<>candidate.id or replay_event.event_kind<>p_next_state or replay_event.decision_basis is distinct from p_decision_basis then raise exception 'eval candidate transition idempotency conflict'; end if;
    select pe.event_kind into current_state from ops.proposed_eval_candidate_event pe where pe.candidate_id=candidate.id order by pe.created_at desc,pe.id desc limit 1;
    return query select candidate.id,current_state,current_state='accepted'; return;
  end if;
  select pe.event_kind into current_state from ops.proposed_eval_candidate_event pe where pe.candidate_id=candidate.id order by pe.created_at desc,pe.id desc limit 1;
  if p_next_state not in ('triaged','accepted','retired')
     or (current_state='proposed' and p_next_state<>'triaged')
     or (current_state='triaged' and p_next_state<>'accepted')
     or (current_state='accepted' and p_next_state<>'retired')
     or current_state not in ('proposed','triaged','accepted') then
    raise exception 'invalid append-only eval candidate lifecycle transition';
  end if;
  insert into ops.proposed_eval_candidate_event(candidate_id,event_kind,decided_by_actor_id,decision_basis,idempotency_key)
  values(candidate.id,p_next_state,actor_row.id,p_decision_basis,p_idempotency_key);
  if p_next_state='accepted' then
    insert into ops.accepted_eval_golden_membership(candidate_id,target_golden_set_ref,accepted_by_actor_id)
    values(candidate.id,candidate.target_golden_set_ref,actor_row.id);
  end if;
  return query select candidate.id,p_next_state,(p_next_state='accepted');
end $$;

grant execute on function ops.register_execution_environment_provider(p_manifest jsonb, p_idempotency_key uuid) to carr_writer;
grant execute on function ops.transition_proposed_eval_candidate(p_work_request text, p_candidate_ref text, p_next_state text, p_decision_basis jsonb, p_idempotency_key uuid) to carr_writer;

commit;

do $$
declare def text;
begin
  if to_regprocedure('ops.register_execution_environment_provider(jsonb,uuid)') is null then
    raise exception '0344 FAILED: ops.register_execution_environment_provider is missing';
  end if;
  select pg_get_functiondef('ops.register_execution_environment_provider(jsonb,uuid)'::regprocedure) into def;
  if def not like '%carr_writer%' or def not like '%acting_actor_slug%'
     or def not like '%requires the authority connection or a sponsored writer session%' then
    raise exception '0344 FAILED: ops.register_execution_environment_provider does not accept a sponsored writer session';
  end if;
  if def not like '%kind in (%human%,%automation%)%' then
    raise exception '0344 FAILED: ops.register_execution_environment_provider did not widen actor kind to include automation';
  end if;
  if not has_function_privilege('carr_writer','ops.register_execution_environment_provider(jsonb,uuid)'::regprocedure,'execute') then
    raise exception '0344 FAILED: carr_writer lacks execute on ops.register_execution_environment_provider';
  end if;

  if to_regprocedure('ops.transition_proposed_eval_candidate(text,text,text,jsonb,uuid)') is null then
    raise exception '0344 FAILED: ops.transition_proposed_eval_candidate is missing';
  end if;
  select pg_get_functiondef('ops.transition_proposed_eval_candidate(text,text,text,jsonb,uuid)'::regprocedure) into def;
  if def not like '%carr_writer%' or def not like '%acting_actor_slug%'
     or def not like '%requires the authority connection or a sponsored writer session%' then
    raise exception '0344 FAILED: ops.transition_proposed_eval_candidate does not accept a sponsored writer session';
  end if;
  if not has_function_privilege('carr_writer','ops.transition_proposed_eval_candidate(text,text,text,jsonb,uuid)'::regprocedure,'execute') then
    raise exception '0344 FAILED: carr_writer lacks execute on ops.transition_proposed_eval_candidate';
  end if;

  -- The verb this migration deliberately leaves alone must be untouched: no
  -- carr_writer grant, authority-only session_user check still present.
  if has_function_privilege('carr_writer','ops.transition_execution_environment_provider(text,text,text,jsonb,uuid)'::regprocedure,'execute') then
    raise exception '0344 FAILED: ops.transition_execution_environment_provider must stay authority-only (carr_writer must not gain execute)';
  end if;
  select pg_get_functiondef('ops.transition_execution_environment_provider(text,text,text,jsonb,uuid)'::regprocedure) into def;
  if def not like '%requires human authority%' then
    raise exception '0344 FAILED: ops.transition_execution_environment_provider lost its authority-only guard';
  end if;
end $$;
