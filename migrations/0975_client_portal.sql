-- rollback: remove the portal consumer before dropping these empty-by-default records and read function.
-- lock-review: adds portal-only tables and a reader function; existing business records are untouched.

create table ops.client_portal (
  slug text primary key check (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
  client_id uuid not null references public.client(id),
  deal_id uuid not null references public.deal(id),
  password_hash text not null check (password_hash ~ '^\$2[aby]\$12\$[./A-Za-z0-9]{53}$'),
  disabled_at timestamptz,
  created_at timestamptz not null default now()
);

create table ops.client_portal_material (
  id bigint generated always as identity primary key,
  portal_slug text not null references ops.client_portal(slug),
  kind text not null check (kind in ('document','tour','report')),
  title text not null check (length(btrim(title)) between 1 and 200),
  content text not null check (length(btrim(content)) between 1 and 10000),
  shared_at timestamptz,
  withdrawn_at timestamptz,
  created_at timestamptz not null default now()
);
create index client_portal_material_shared on ops.client_portal_material(portal_slug,shared_at,id)
  where shared_at is not null and withdrawn_at is null;

revoke all on ops.client_portal,ops.client_portal_material from public,carr_reader,carr_writer,carr_authority;
revoke all on sequence ops.client_portal_material_id_seq from public,carr_reader,carr_writer,carr_authority;

-- Verifiers use bcrypt(SHA-256(password)), so passwords differing beyond bcrypt's 72-byte limit remain distinct.
create function ops.read_client_portal(p_slug text,p_password text) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare
  portal ops.client_portal%rowtype;
  supplied_hash text;
  result jsonb;
begin
  if p_slug is null or p_slug !~ '^[a-z0-9][a-z0-9-]{0,62}$'
     or p_password is null or length(p_password) not between 1 and 1024 then
    return null;
  end if;
  select p.* into portal from ops.client_portal p where p.slug=p_slug;
  -- A non-secret dummy verifier keeps an unknown slug on the same bcrypt work path.
  supplied_hash := public.crypt(encode(public.digest(p_password,'sha256'),'hex'),
    coalesce(portal.password_hash,'$2a$12$......................0000000000000000000000000000000'));
  if portal.slug is null or portal.password_hash is distinct from supplied_hash or portal.disabled_at is not null then
    return null;
  end if;
  if (select count(*) from ops.client_portal_material m where m.portal_slug=portal.slug
      and m.shared_at<=now() and m.withdrawn_at is null)>100 then
    return null;
  end if;
  select jsonb_build_object(
    'slug',portal.slug,
    'roadmap',jsonb_build_object('current_phase',d.phase,'phases',
      (select coalesce(jsonb_agg(jsonb_build_object('slug',ph.slug,'label',ph.label) order by ph.sort,ph.slug),'[]'::jsonb) from public.deal_phase ph)),
    'materials',(select coalesce(jsonb_agg(jsonb_build_object('kind',m.kind,'title',m.title,'content',m.content) order by m.shared_at,m.id),'[]'::jsonb)
      from ops.client_portal_material m where m.portal_slug=portal.slug and m.shared_at<=now() and m.withdrawn_at is null)
  ) into result
  from public.deal d join public.client cl on cl.id=d.client_id join public.party p on p.id=cl.party_id
  where d.id=portal.deal_id and cl.id=portal.client_id and cl.merged_into is null
    and p.merged_into is null and p.deleted_at is null;
  return result;
end $$;
revoke all on function ops.read_client_portal(text,text) from public;
grant usage on schema ops to carr_reader;
grant execute on function ops.read_client_portal(text,text) to carr_reader;

CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $fn$ SELECT $current_grants${
  "relation_dml": [
    {
      "grantee": "carr_writer",
      "relation": "public.deal_vendor_assignment",
      "grantable": false,
      "privilege": "insert",
      "ingress_key": "db-relation-acl:public.deal_vendor_assignment:carr_writer:insert",
      "ingress_kind": "db_relation_acl",
      "relation_kind": "r"
    },
    {
      "grantee": "carr_writer",
      "relation": "public.deal_vendor_assignment",
      "grantable": false,
      "privilege": "update",
      "ingress_key": "db-relation-acl:public.deal_vendor_assignment:carr_writer:update",
      "ingress_kind": "db_relation_acl",
      "relation_kind": "r"
    },
    {
      "grantee": "carr_writer",
      "relation": "public.doctrine_embedding",
      "grantable": false,
      "privilege": "insert",
      "ingress_key": "db-relation-acl:public.doctrine_embedding:carr_writer:insert",
      "ingress_kind": "db_relation_acl",
      "relation_kind": "r"
    }
  ],
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
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_authority",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.approve_rule(p_rule_id uuid, p_policy_kind text, p_control_keys text[], p_idempotency_key text, p_reason text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.approve_rule(p_rule_id uuid, p_policy_kind text, p_control_keys text[], p_idempotency_key text, p_reason text):carr_authority:execute",
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
    },
    {
      "config": [
        "search_path=pg_catalog, pg_temp"
      ],
      "grantee": "carr_reader",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "public.search_doctrine_semantic(p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer, p_policy_id text, p_embeddings vector[])",
      "volatility": "s",
      "ingress_key": "db-function-acl:public.search_doctrine_semantic(p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer, p_policy_id text, p_embeddings vector[]):carr_reader:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "public.search_doctrine_semantic(p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer, p_policy_id text, p_embeddings vector[])",
      "volatility": "s",
      "ingress_key": "db-function-acl:public.search_doctrine_semantic(p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer, p_policy_id text, p_embeddings vector[]):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, pg_temp"
      ],
      "grantee": "carr_reader",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.read_client_portal(p_slug text, p_password text)",
      "volatility": "s",
      "ingress_key": "db-function-acl:ops.read_client_portal(p_slug text, p_password text):carr_reader:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ],
  "registry_digest": "sha256:560e16ad4c5383414b36b74da507481d9e840d8c00e45ced6eed6bce2ac3447d",
  "registry_version": "scac-mutation-registry.v118",
  "runtime_dml_grants": {
    "count": 339,
    "digest": "sha256:a210dbda8843ef57aecf1985cb0b97f7ab6fac5e1e99df0926542eb2945254bc"
  },
  "retired_secdef_execute": [
    {
      "config": [
        "search_path=ops, public, pg_temp"
      ],
      "grantee": "carr_authority",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.approve_rule(p_rule_id uuid, p_policy_kind text, p_control_keys text[], p_idempotency_key text, p_reason text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.approve_rule(p_rule_id uuid, p_policy_kind text, p_control_keys text[], p_idempotency_key text, p_reason text):carr_authority:execute",
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
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_reader",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.jev_call_receipt_integrity()",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.jev_call_receipt_integrity():carr_reader:execute",
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
      "signature": "ops.jev_call_receipt_integrity()",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.jev_call_receipt_integrity():carr_writer:execute",
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
      "grantee": "carr_reader",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.read_jev_call_receipts(p_session text, p_since timestamp with time zone, p_lim integer, p_actor_slug text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.read_jev_call_receipts(p_session text, p_since timestamp with time zone, p_lim integer, p_actor_slug text):carr_reader:execute",
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
      "signature": "ops.read_jev_call_receipts(p_session text, p_since timestamp with time zone, p_lim integer, p_actor_slug text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.read_jev_call_receipts(p_session text, p_since timestamp with time zone, p_lim integer, p_actor_slug text):carr_writer:execute",
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
      "signature": "ops.record_jev_call_receipt(p_session_id text, p_purpose text, p_question_ids text[], p_facets text[], p_model_requested text, p_model_answered text, p_state_sha256 text, p_questions_sha256 text, p_answers_sha256 text, p_prompt_sha256 text, p_answers jsonb, p_usage jsonb, p_actor_id uuid, p_actor_slug text, p_idempotency_key text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.record_jev_call_receipt(p_session_id text, p_purpose text, p_question_ids text[], p_facets text[], p_model_requested text, p_model_answered text, p_state_sha256 text, p_questions_sha256 text, p_answers_sha256 text, p_prompt_sha256 text, p_answers jsonb, p_usage jsonb, p_actor_id uuid, p_actor_slug text, p_idempotency_key text):carr_writer:execute",
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
    },
    {
      "config": [
        "search_path=ops, public, pg_temp"
      ],
      "grantee": "carr_jobs",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.admit_job_cost(p_job_id uuid, p_lease_token uuid, p_route_key text, p_estimated_cost_usd numeric)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.admit_job_cost(p_job_id uuid, p_lease_token uuid, p_route_key text, p_estimated_cost_usd numeric):carr_jobs:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=ops, public, pg_temp"
      ],
      "grantee": "carr_jobs",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.get_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.get_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text):carr_jobs:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=ops, public, pg_temp"
      ],
      "grantee": "carr_jobs",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.invalidate_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_dependency_ref text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.invalidate_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_dependency_ref text):carr_jobs:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=ops, public, pg_temp"
      ],
      "grantee": "carr_jobs",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.put_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text, p_cognition_key text, p_cognition_version integer, p_output_schema_version integer, p_proposal jsonb, p_dependency_refs text[], p_ttl_seconds integer)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.put_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text, p_cognition_key text, p_cognition_version integer, p_output_schema_version integer, p_proposal jsonb, p_dependency_refs text[], p_ttl_seconds integer):carr_jobs:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ]
}$current_grants$::jsonb $fn$;
