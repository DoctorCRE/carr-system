-- rollback: drop public.email_proposal after removing list-email-proposals, record-email-proposal and dismiss-email-proposal; confirmed changes stay in deal history.
-- lock-review: new empty table and index only; no existing rows rewritten.

create table public.email_proposal (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('phase_move','next_step')),
  deal_id uuid not null references public.deal(id),
  activity_id uuid not null references public.activity(id),
  evidence_quote text not null check (length(evidence_quote) between 10 and 2000),
  task_text text check (length(btrim(task_text)) between 1 and 500),
  next_date date,
  owner text check (owner in ('joe','dell')),
  confidence numeric not null check (confidence between 0 and 1),
  status text not null default 'pending' check (status in ('pending','confirmed','dismissed')),
  version integer not null default 1,
  created_by uuid not null references public.actor(id),
  created_at timestamptz not null default now(),
  resolved_by uuid references public.actor(id),
  resolved_at timestamptz,
  resolution_reason text check (length(btrim(resolution_reason)) between 1 and 500),
  unique (kind, deal_id, activity_id),
  check ((kind = 'phase_move' and task_text is null and next_date is null and owner is null)
      or (kind = 'next_step' and task_text is not null and next_date is not null and owner is not null)),
  check ((status = 'pending' and resolved_by is null and resolved_at is null and resolution_reason is null)
      or (status = 'confirmed' and resolved_by is not null and resolved_at is not null)
      or (status = 'dismissed' and resolved_by is not null and resolved_at is not null and resolution_reason is not null))
);

create index email_proposal_pending on public.email_proposal(created_at, id) where status = 'pending';

grant select on public.email_proposal to carr_reader;
grant select,insert,update on public.email_proposal to carr_writer;

create trigger scac_reference_monitor_guard_row before insert or update or delete
  on public.email_proposal for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
  on public.email_proposal for each statement execute function ops.scac_reference_monitor_guard();

-- Register exactly the two new writer grants; preserve all existing grants and retirements.
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
    },
    {
      "grantee": "carr_writer",
      "relation": "public.email_proposal",
      "grantable": false,
      "privilege": "insert",
      "ingress_key": "db-relation-acl:public.email_proposal:carr_writer:insert",
      "ingress_kind": "db_relation_acl",
      "relation_kind": "r"
    },
    {
      "grantee": "carr_writer",
      "relation": "public.email_proposal",
      "grantable": false,
      "privilege": "update",
      "ingress_key": "db-relation-acl:public.email_proposal:carr_writer:update",
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
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.read_client_portal(p_slug text, p_password text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.read_client_portal(p_slug text, p_password text):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ],
  "registry_digest": "sha256:560e16ad4c5383414b36b74da507481d9e840d8c00e45ced6eed6bce2ac3447d",
  "registry_version": "scac-mutation-registry.v118",
  "runtime_dml_grants": {
    "count": 341,
    "digest": "sha256:417178cb46966205a188bafec1ceb5d7490249d9dd924264c6f811fb31697ed1"
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
