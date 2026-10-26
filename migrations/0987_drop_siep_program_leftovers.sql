-- rollback: forward-only; re-create the objects by replaying their source migrations (0324, 0335, 0453, 0456, 0458, 0460, 0465, 0467) on a branch; the dropped SIEP ledger and SCAC trust/token rows are retired-programme history with no runtime reader
-- expand-contract: contract — the code callers were removed in the WR-SIEP-26 code PR; nothing in mcp-server/src, ops runtime, tools, hooks or bin reads these objects
-- lock-review: drops two shadow-mode trigger pairs per guarded table (brief ACCESS EXCLUSIVE per table, one at a time) and 27 small retired tables
-- 0987_drop_siep_program_leftovers.sql
-- WR-SIEP-26, approved by Joe on 2026-10-26 with the catalog audit. Drops the
-- retired SIEP programme objects that have no runtime caller:
--   SIEP-0324 package ledger/DAG, its work_request and event guards and view
--   SIEP-06A evidence graph reader
--   SIEP-13 artifact registry, SIEP-14 root trust, SIEP-15 device enrollment
--   SIEP-17 token challenge, and the SIEP-18 reference monitor (shadow mode,
--   never enforced) with its per-table triggers.
-- Kept because live code or kept objects use them: SIEP-02 rule delivery, the
-- SIEP-11/16 mutation registry, the SIEP-12 policy epoch, the 0469 exact-effect
-- contract and trusted-principal tables with their guards, and the helpers
-- scac_reference_monitor_sha256, scac_json_has_exact_keys and
-- scac_runtime_privilege_bundle that those keep calling.
-- Every DROP is without CASCADE, so an unexpected dependent stops the migration.

-- The dropped definer functions carry 44 unregistered EXECUTE grants that
-- the v118 catalog check counts. Record them as retired grants (the 0971
-- pattern) so the catalog stays current after they are gone.
do $retire_siep_grants$
declare
  original jsonb := ops.scac_current_grant_contract();
  retired jsonb := $retired$[
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_admit_mutation(p_token_ref_digest text, p_operation_manifest jsonb, p_admission_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_admit_mutation(p_token_ref_digest text, p_operation_manifest jsonb, p_admission_idempotency_key uuid):carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_admit_mutation(p_token_ref_digest text, p_operation_manifest jsonb, p_admission_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_admit_mutation(p_token_ref_digest text, p_operation_manifest jsonb, p_admission_idempotency_key uuid):carr_jobs:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_writer",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_admit_mutation(p_token_ref_digest text, p_operation_manifest jsonb, p_admission_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_admit_mutation(p_token_ref_digest text, p_operation_manifest jsonb, p_admission_idempotency_key uuid):carr_writer:execute",
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
    "signature": "ops.scac_capability_token_status(p_token_ref_digest text)",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.scac_capability_token_status(p_token_ref_digest text):carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_capability_token_status(p_token_ref_digest text)",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.scac_capability_token_status(p_token_ref_digest text):carr_jobs:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_consume_verified_pop_challenge(p_challenge_id uuid, p_challenge_digest text, p_nonce text, p_pop_verification_digest text, p_consume_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_consume_verified_pop_challenge(p_challenge_id uuid, p_challenge_digest text, p_nonce text, p_pop_verification_digest text, p_consume_idempotency_key uuid):carr_jobs:execute",
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
    "signature": "ops.scac_device_enrollment_status(p_device_ref text)",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.scac_device_enrollment_status(p_device_ref text):carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_issue_pop_challenge(p_principal_digest text, p_device_ref text, p_workload_digest text, p_ingress_key text, p_operation_manifest_digest text, p_idempotency_digest text, p_ttl_seconds integer, p_issue_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_issue_pop_challenge(p_principal_digest text, p_device_ref text, p_workload_digest text, p_ingress_key text, p_operation_manifest_digest text, p_idempotency_digest text, p_ttl_seconds integer, p_issue_idempotency_key uuid):carr_jobs:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_record_capability_token_receipt(p_challenge_digest text, p_token_ref_digest text, p_signed_payload_digest text, p_signature_digest text, p_issuer_key_digest text, p_issuer_root_event_digest text, p_external_verification_receipt_digest text, p_issued_at timestamp with time zone, p_expires_at timestamp with time zone, p_record_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_record_capability_token_receipt(p_challenge_digest text, p_token_ref_digest text, p_signed_payload_digest text, p_signature_digest text, p_issuer_key_digest text, p_issuer_root_event_digest text, p_external_verification_receipt_digest text, p_issued_at timestamp with time zone, p_expires_at timestamp with time zone, p_record_idempotency_key uuid):carr_jobs:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_record_token_verification_binding(p_token_ref_digest text, p_token_payload jsonb, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_record_token_verification_binding(p_token_ref_digest text, p_token_payload jsonb, p_idempotency_key uuid):carr_jobs:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_reference_monitor_state()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.scac_reference_monitor_state():carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_reference_monitor_state()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.scac_reference_monitor_state():carr_jobs:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_writer",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_reference_monitor_state()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.scac_reference_monitor_state():carr_writer:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_register_operation_effect_binding(p_registry_version text, p_ingress_key text, p_privilege_bundle text, p_registry_entry_digest text, p_effect_keys text[], p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_register_operation_effect_binding(p_registry_version text, p_ingress_key text, p_privilege_bundle text, p_registry_entry_digest text, p_effect_keys text[], p_idempotency_key uuid):carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_register_token_issuer_binding(p_issuer_key_digest text, p_issuer_root_event_digest text, p_certification_receipt_digest text, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_register_token_issuer_binding(p_issuer_key_digest text, p_issuer_root_event_digest text, p_certification_receipt_digest text, p_idempotency_key uuid):carr_authority:execute",
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
    "signature": "ops.scac_revoke_token_subject(p_subject_kind text, p_subject_digest text, p_reason_digest text, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_revoke_token_subject(p_subject_kind text, p_subject_digest text, p_reason_digest text, p_idempotency_key uuid):carr_authority:execute",
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
    "signature": "ops.scac_token_control_snapshot()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.scac_token_control_snapshot():carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_token_control_snapshot()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.scac_token_control_snapshot():carr_jobs:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_transition_reference_monitor_mode(p_mode text, p_reason_digest text, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_transition_reference_monitor_mode(p_mode text, p_reason_digest text, p_idempotency_key uuid):carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, public, ops, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.scac_transition_token_kill_switch(p_action text, p_reason_digest text, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.scac_transition_token_kill_switch(p_action text, p_reason_digest text, p_idempotency_key uuid):carr_authority:execute",
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
    "signature": "ops.siep_acquire_lane_lock(p_component text, p_session_ref text, p_lease_seconds integer, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.siep_acquire_lane_lock(p_component text, p_session_ref text, p_lease_seconds integer, p_idempotency_key uuid):carr_writer:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, public, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_attach_evidence(p_component text, p_evidence_kind text, p_ledger_id uuid, p_ledger_kind text, p_evidence_digest text, p_note text, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.siep_attach_evidence(p_component text, p_evidence_kind text, p_ledger_id uuid, p_ledger_kind text, p_evidence_digest text, p_note text, p_idempotency_key uuid):carr_authority:execute",
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
    "signature": "ops.siep_attach_evidence(p_component text, p_evidence_kind text, p_ledger_id uuid, p_ledger_kind text, p_evidence_digest text, p_note text, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.siep_attach_evidence(p_component text, p_evidence_kind text, p_ledger_id uuid, p_ledger_kind text, p_evidence_digest text, p_note text, p_idempotency_key uuid):carr_writer:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, public, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_bind_evidence_job(p_component text, p_base_version integer, p_evidence_kind text, p_job_id uuid, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.siep_bind_evidence_job(p_component text, p_base_version integer, p_evidence_kind text, p_job_id uuid, p_idempotency_key uuid):carr_authority:execute",
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
    "signature": "ops.siep_claim_package(p_component text, p_session_ref text, p_lease_token uuid, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.siep_claim_package(p_component text, p_session_ref text, p_lease_token uuid, p_idempotency_key uuid):carr_writer:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, public, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_manifest_digest()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_manifest_digest():carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, public, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_manifest_digest()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_manifest_digest():carr_jobs:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, public, pg_temp"
    ],
    "grantee": "carr_reader",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_manifest_digest()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_manifest_digest():carr_reader:execute",
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
    "signature": "ops.siep_manifest_digest()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_manifest_digest():carr_writer:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, public, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_read_evidence_graph(p_component text)",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_read_evidence_graph(p_component text):carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, public, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_read_evidence_graph(p_component text)",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_read_evidence_graph(p_component text):carr_jobs:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, public, pg_temp"
    ],
    "grantee": "carr_reader",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_read_evidence_graph(p_component text)",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_read_evidence_graph(p_component text):carr_reader:execute",
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
    "signature": "ops.siep_read_evidence_graph(p_component text)",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_read_evidence_graph(p_component text):carr_writer:execute",
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
    "signature": "ops.siep_read_program()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_read_program():carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_read_program()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_read_program():carr_jobs:execute",
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
    "signature": "ops.siep_read_program()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_read_program():carr_reader:execute",
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
    "signature": "ops.siep_read_program()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_read_program():carr_writer:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, public, pg_temp"
    ],
    "grantee": "carr_authority",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_record_joe_decision(p_component text, p_gate text, p_decision text, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.siep_record_joe_decision(p_component text, p_gate text, p_decision text, p_idempotency_key uuid):carr_authority:execute",
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
    "signature": "ops.siep_release_lane_lock(p_component text, p_session_ref text, p_lease_token uuid, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.siep_release_lane_lock(p_component text, p_session_ref text, p_lease_token uuid, p_idempotency_key uuid):carr_writer:execute",
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
    "signature": "ops.siep_terminal_status()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_terminal_status():carr_authority:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  },
  {
    "config": [
      "search_path=pg_catalog, ops, pg_temp"
    ],
    "grantee": "carr_jobs",
    "parallel": "u",
    "grantable": false,
    "privilege": "execute",
    "signature": "ops.siep_terminal_status()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_terminal_status():carr_jobs:execute",
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
    "signature": "ops.siep_terminal_status()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_terminal_status():carr_reader:execute",
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
    "signature": "ops.siep_terminal_status()",
    "volatility": "s",
    "ingress_key": "db-function-acl:ops.siep_terminal_status():carr_writer:execute",
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
    "signature": "ops.siep_transition_package(p_component text, p_base_version integer, p_target_state text, p_session_ref text, p_lease_token uuid, p_idempotency_key uuid)",
    "volatility": "v",
    "ingress_key": "db-function-acl:ops.siep_transition_package(p_component text, p_base_version integer, p_target_state text, p_session_ref text, p_lease_token uuid, p_idempotency_key uuid):carr_writer:execute",
    "ingress_kind": "db_function_acl",
    "function_kind": "f",
    "security_definer": true
  }
]$retired$::jsonb;
begin
  if ops.scac_mutation_catalog_v118_current() is not true then
    raise exception '0987 FAILED: the SCAC catalog was not current before the drop';
  end if;
  if exists (select 1 from jsonb_array_elements(coalesce(original->'retired_secdef_execute','[]'::jsonb)) old,
                           jsonb_array_elements(retired) new
              where old->>'ingress_key' = new->>'ingress_key') then
    raise exception '0987 FAILED: a SIEP grant is already retired';
  end if;
  execute format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(jsonb_set(original, '{retired_secdef_execute}',
      coalesce(original->'retired_secdef_execute','[]'::jsonb) || retired)::text) || '::jsonb');
end $retire_siep_grants$;

do $drop_monitor$
declare
  r record;
  v_dropped integer := 0;
begin
  for r in
    select t.tgname, t.tgrelid::regclass as rel
      from pg_trigger t
     where t.tgfoid = 'ops.scac_reference_monitor_guard()'::regprocedure
       and not t.tgisinternal
     order by t.tgrelid::regclass::text, t.tgname
  loop
    execute format('drop trigger %I on %s', r.tgname, r.rel);
    v_dropped := v_dropped + 1;
  end loop;
  raise notice '0987 dropped % SIEP-18 reference-monitor triggers', v_dropped;
end $drop_monitor$;

drop trigger siep_program_identity_guard_before_update on ops.work_request;
drop trigger siep_program_transition_guard_before_update on ops.work_request;
drop trigger siep_joe_decision_event_guard_before_write on public.event;

-- The disabled ai-capability-builder workflow's input view reads the SIEP
-- package DAG through v_capability_program_next; nothing queries either.
drop view ops.v_control_plane_capability_candidate;
drop view ops.v_capability_program_next;

-- One statement: foreign keys between these tables need no ordering, and any
-- reference from a table outside the list still refuses the drop.
drop table
  ops.siep_job_evidence_binding, ops.siep_evidence_link, ops.siep_command_receipt,
  ops.siep_lane_lock, ops.siep_component_alias, ops.siep_program_dependency,
  ops.siep_package_contract,
  ops.scac_artifact_transparency_entry, ops.scac_artifact_signature, ops.scac_artifact_manifest,
  ops.scac_root_custodian_attestation, ops.scac_root_custodian_set_member,
  ops.scac_root_trust_event, ops.scac_root_trust_key,
  ops.scac_device_benchmark_receipt, ops.scac_device_fact_receipt, ops.scac_device_enrollment,
  ops.scac_pop_challenge_consumption, ops.scac_capability_token_receipt,
  ops.scac_token_verification_binding, ops.scac_token_issuer_binding, ops.scac_pop_challenge,
  ops.scac_token_kill_switch_event, ops.scac_token_revocation_event,
  ops.scac_operation_effect_binding, ops.scac_reference_monitor_receipt,
  ops.scac_reference_monitor_mode_event;

do $drop_functions$
declare
  r record;
  v_dropped integer := 0;
begin
  for r in
    select p.oid::regprocedure as fn
      from pg_proc p
     where p.pronamespace = 'ops'::regnamespace
       and (p.proname like '%siep%'
            or p.proname ~ '^scac_(admit_mutation|artifact_|capability_token|consume_verified|device_|issue_pop|pop_challenge|record_capability|record_token|reference_monitor|register_operation|register_token|revoke_token|root_|runtime_dml_grant_snapshot|token_|transition_)')
       and p.proname not in ('scac_siep18_append_only_guard', 'scac_siep18_truncate_guard',
                             'scac_reference_monitor_sha256')
     order by p.oid::regprocedure::text
  loop
    execute format('drop function %s', r.fn);
    v_dropped := v_dropped + 1;
  end loop;
  raise notice '0987 dropped % SIEP functions', v_dropped;
end $drop_functions$;

do $verify$
begin
  if exists (select 1 from pg_class c where c.relnamespace = 'ops'::regnamespace and c.relname like 'siep\_%') then
    raise exception '0987 FAILED: a SIEP relation survived';
  end if;
  if exists (select 1 from pg_trigger t join pg_proc p on p.oid = t.tgfoid
              where not t.tgisinternal and (p.proname like 'siep\_%' or p.proname = 'scac_reference_monitor_guard')) then
    raise exception '0987 FAILED: a SIEP trigger survived';
  end if;
  if to_regclass('ops.scac_exact_effect_contract') is null
     or to_regclass('ops.scac_trusted_principal_binding') is null
     or to_regprocedure('ops.scac_register_exact_effect_contract(text,text,text,jsonb,uuid)') is null
     or to_regprocedure('ops.scac_bind_trusted_principal(jsonb,uuid)') is null
     or to_regprocedure('ops.scac_exact_effect_union(text,text)') is null
     or to_regprocedure('ops.scac_reference_monitor_sha256(jsonb)') is null then
    raise exception '0987 FAILED: a kept SIEP-18 exact-effect object is missing';
  end if;
  if to_regclass('ops.scac_policy_epoch') is null or to_regclass('ops.scac_mutation_registry_entry') is null then
    raise exception '0987 FAILED: the kept policy epoch or mutation registry is missing';
  end if;
  if ops.scac_mutation_catalog_v118_current() is not true then
    raise exception '0987 FAILED: the drop changed an unaccounted SCAC capability';
  end if;
end $verify$;
