-- App feature configuration extends the existing versioned record/audit envelope.
-- rollback: forward-only — retain switch state and audit history; retire or restore switches through their audited verbs.
create table public.feature_switch (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  name text not null check (name ~ '^[a-z][a-z0-9-]{0,62}$'),
  description text not null check (length(btrim(description)) between 1 and 1000),
  default_enabled boolean not null,
  enabled boolean,
  audience text not null check (audience in ('joe','team','everyone')),
  owner text not null check (owner in ('joe','dell','claude')),
  created_at timestamptz not null default now(),
  expected_removal_on date not null,
  retired_at timestamptz,
  retirement_loop_id uuid,
  version integer not null default 1 check (version > 0),
  unique (organization_tenant_id,name)
);
grant select on public.feature_switch to carr_reader,carr_writer;
-- Runtime writes go through ops.write_feature_switch below; carr_writer holds no table DML here.
create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.feature_switch for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.feature_switch for each statement execute function ops.scac_reference_monitor_guard();

-- The first consumer ships hidden; activation is a later audited record act.
insert into public.feature_switch
  (organization_tenant_id,name,description,default_enabled,audience,owner,expected_removal_on)
values ('carr-internal','doc-suggestion-actions',
        'Act on or correct a suggestion from Doc',false,'joe','claude','2026-11-05');

-- The single registered writer. SECURITY DEFINER with a trusted-schema search_path (never public);
-- the table is schema-qualified in every statement. Callers keep their advisory lock and do their own
-- reads; the base_version predicate makes every revision an atomic compare-and-set.
create function ops.write_feature_switch(p_action text, p_args jsonb)
returns public.feature_switch
language plpgsql security definer set search_path=pg_catalog,ops,pg_temp as $$
declare r public.feature_switch;
begin
  if p_action='create' then
    insert into public.feature_switch
      (organization_tenant_id,name,description,default_enabled,audience,owner,expected_removal_on)
    values (p_args->>'tenant',p_args->>'name',p_args->>'description',(p_args->>'default_enabled')::boolean,
            p_args->>'audience',p_args->>'owner',(p_args->>'expected_removal_on')::date)
    returning * into r;
  elsif p_action='flip' then
    update public.feature_switch set enabled=(p_args->>'enabled')::boolean,
      audience=coalesce(p_args->>'audience',audience),version=version+1
    where id=(p_args->>'id')::uuid and version=(p_args->>'base_version')::integer returning * into r;
  elsif p_action='revise' then
    update public.feature_switch set description=p_args->>'description',
      default_enabled=(p_args->>'default_enabled')::boolean,audience=p_args->>'audience',owner=p_args->>'owner',
      expected_removal_on=(p_args->>'expected_removal_on')::date,
      retired_at=case when jsonb_typeof(p_args->'retired') is distinct from 'boolean' then retired_at
                      when (p_args->>'retired')::boolean then coalesce(retired_at,now()) else null end,
      version=version+1
    where id=(p_args->>'id')::uuid and version=(p_args->>'base_version')::integer returning * into r;
  elsif p_action='link_loop' then
    update public.feature_switch set retirement_loop_id=nullif(p_args->>'retirement_loop_id','')::uuid
    where id=(p_args->>'id')::uuid returning * into r;
  else
    raise exception 'feature_switch_action_invalid';
  end if;
  if r.id is null then raise exception 'feature_switch_version_conflict' using errcode='40001'; end if;
  return r;
end $$;
revoke all on function ops.write_feature_switch(text,jsonb) from public;
grant execute on function ops.write_feature_switch(text,jsonb) to carr_writer;

-- The SCAC live-catalog contract owns every SECURITY DEFINER EXECUTE grant to a runtime role;
-- this function adds exactly one row (carr_writer execute).
CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $fn$ SELECT $current_grants${
  "registry_version": "scac-mutation-registry.v118",
  "registry_digest": "sha256:560e16ad4c5383414b36b74da507481d9e840d8c00e45ced6eed6bce2ac3447d",
  "secdef_execute": [
    {
      "config": [
        "search_path=ops, public, pg_temp"
      ],
      "grantee": "carr_authority",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.amend_rule_scope(p_rule_id uuid, p_new_scope jsonb, p_new_statement text, p_base_version integer, p_idempotency_key text, p_reason text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.amend_rule_scope(p_rule_id uuid, p_new_scope jsonb, p_new_statement text, p_base_version integer, p_idempotency_key text, p_reason text):carr_authority:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, public, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_authority",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.transfer_studio_leader(p_source text, p_target text, p_epoch bigint, p_evidence jsonb)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.transfer_studio_leader(p_source text, p_target text, p_epoch bigint, p_evidence jsonb):carr_authority:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.write_feature_switch(p_action text, p_args jsonb)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.write_feature_switch(p_action text, p_args jsonb):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ],
  "retired_secdef_execute": [
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.classify_sourced_work_request_build(p_work_request text, p_base_version integer, p_scope_summary text, p_dependency_refs jsonb, p_caps jsonb)",
      "volatility": "s",
      "ingress_key": "db-function-acl:ops.classify_sourced_work_request_build(p_work_request text, p_base_version integer, p_scope_summary text, p_dependency_refs jsonb, p_caps jsonb):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "public",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.heavy_build_ready_plan_gate()",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.heavy_build_ready_plan_gate():public:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, public, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_heavy_build jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_heavy_build jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.record_sourced_heavy_build_admission(p_plan_id uuid, p_work_request text, p_base_version integer, p_classifier_reasons jsonb, p_contract jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.record_sourced_heavy_build_admission(p_plan_id uuid, p_work_request text, p_base_version integer, p_classifier_reasons jsonb, p_contract jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.review_sourced_heavy_build_plan(p_work_request text, p_plan_hash text, p_admission_hash text, p_reviewer_actor_id uuid, p_verdict text, p_reviewer_session_ref text, p_review_summary text, p_evidence_refs jsonb, p_gaps jsonb, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.review_sourced_heavy_build_plan(p_work_request text, p_plan_hash text, p_admission_hash text, p_reviewer_actor_id uuid, p_verdict text, p_reviewer_session_ref text, p_review_summary text, p_evidence_refs jsonb, p_gaps jsonb, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.sourced_heavy_build_review_target(p_work_request text, p_plan_hash text, p_admission_hash text)",
      "volatility": "s",
      "ingress_key": "db-function-acl:ops.sourced_heavy_build_review_target(p_work_request text, p_plan_hash text, p_admission_hash text):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ]
}$current_grants$::jsonb $fn$;
