-- rollback: forward-only; restore lease-bound cache observations in a new migration.
-- expand-contract: contract; generic scheduler callers were removed in PR #2100.
-- lock-review: ACCESS EXCLUSIVE on one retired ledger; acquisition is bounded to 5s.
-- Synthetic rows rehearse deletion; no retained product reader consumes this ledger.
SET LOCAL lock_timeout = '5s';

DO $retire_cache_capabilities$
DECLARE
  original jsonb := ops.scac_current_grant_contract();
  expected_keys text[] := ARRAY[
    'db-function-acl:ops.get_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text):carr_jobs:execute',
    'db-function-acl:ops.put_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text, p_cognition_key text, p_cognition_version integer, p_output_schema_version integer, p_proposal jsonb, p_dependency_refs text[], p_ttl_seconds integer):carr_jobs:execute',
    'db-function-acl:ops.invalidate_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_dependency_ref text):carr_jobs:execute'
  ];
  retired jsonb;
  next_contract jsonb;
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Current or historical SCAC seal is not valid';
  END IF;
  SELECT jsonb_agg(contract - 'effect_class' - 'owner_package' - 'implementation_state'
                           - 'classification_authorizing' - 'source_locator'
                  ORDER BY ingress_key)
    INTO retired
    FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version'
     AND ingress_key = ANY(expected_keys);
  IF coalesce(jsonb_array_length(retired),0) <> cardinality(expected_keys) THEN
    RAISE EXCEPTION 'Expected cache capabilities absent from the sealed registry';
  END IF;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(
      coalesce(original->'retired_secdef_execute','[]'::jsonb)) item
      WHERE item->>'ingress_key' = ANY(expected_keys)) THEN
    RAISE EXCEPTION 'A cache capability is already retired';
  END IF;
  next_contract := jsonb_set(original, '{retired_secdef_execute}',
    coalesce(original->'retired_secdef_execute','[]'::jsonb) || retired);
  EXECUTE format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(next_contract::text) || '::jsonb');
END
$retire_cache_capabilities$;

DROP FUNCTION ops.get_cognition_cache_for_job(uuid,uuid,text) RESTRICT;
DROP FUNCTION ops.put_cognition_cache_for_job(
  uuid,uuid,text,text,integer,integer,jsonb,text[],integer) RESTRICT;
DROP FUNCTION ops.invalidate_cognition_cache_for_job(uuid,uuid,text) RESTRICT;
DROP TABLE ops.cognition_cache_observation RESTRICT;

DO $verify_cache_retirement$
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Cache retirement changed an unaccounted SCAC capability';
  END IF;
END
$verify_cache_retirement$;
