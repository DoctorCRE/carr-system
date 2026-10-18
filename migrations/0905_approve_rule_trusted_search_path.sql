-- 0905_approve_rule_trusted_search_path.sql
-- rollback: alter function ops.approve_rule(uuid,text,text[],text,text) set search_path=ops,public,pg_temp and reinstall the 0902 ops.scac_current_grant_contract()
-- lock-review: metadata-only ALTER FUNCTION of one SECURITY DEFINER function plus CREATE OR REPLACE of one immutable contract function; no table is touched and no row is rewritten.
--
-- ops.approve_rule is SECURITY DEFINER and still resolved names through
-- public (search_path=ops,public,pg_temp, kept by 0904). House rule: a definer
-- pins pg_catalog, ops, pg_temp and never public. Every table and function the
-- body reaches is schema-qualified (public.rule, public.event, public.actor,
-- public.digest, ops.*); its only unqualified calls are pg_catalog builtins
-- (btrim, coalesce, nullif, cardinality, unnest, array_append,
-- jsonb_build_object, encode, now). ALTER keeps the 0904 body, owner and
-- grants unchanged.
--
-- The frozen SCAC v118 catalog digests approve_rule's proconfig, so the
-- current grant contract registers the new definition for carr_authority and
-- retires the old one; the v118 digest of every unregistered grant is unchanged.

alter function ops.approve_rule(uuid,text,text[],text,text)
  set search_path = pg_catalog, ops, pg_temp;

-- The SCAC live-catalog contract owns every SECURITY DEFINER EXECUTE grant to a runtime role;
-- this replaces the approve_rule carr_authority row with its trusted-search-path definition.
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
    }
  ],
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
