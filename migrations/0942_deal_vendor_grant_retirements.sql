-- rollback: forward-only; retain the retired gateway grants in the current contract.
-- lock-review: replace one catalog function; no business rows or runtime grants change.
-- The unchanged vendor migration predates main's gateway retirement.
-- Restore its five retired entries after the vendor contract is installed.
do $reconcile$
declare contract jsonb;
begin
  contract := ops.scac_current_grant_contract();
  contract := jsonb_set(contract, '{retired_secdef_execute}', (
    select jsonb_agg(entry order by entry->>'ingress_key' collate "C")
    from jsonb_array_elements(contract->'retired_secdef_execute' || $retired$[
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
  }
]$retired$::jsonb) entry
  ));
  execute format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(contract::text) || '::jsonb'
  );
end $reconcile$;
