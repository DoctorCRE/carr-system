-- rollback: forward-only; restore this retired scheduler subsystem only in a new migration.
-- expand-contract: contract; the generic scheduler caller was removed in PR #2100.
-- lock-review: ACCESS EXCLUSIVE on one retired ledger; actual row count and lock window
-- must be checked in the implementation rehearsal, with lock_timeout bounding acquisition.
SET LOCAL lock_timeout = '5s';

DO $retire_cost_capability$
DECLARE
  original jsonb := ops.scac_current_grant_contract();
  retired jsonb;
  next_contract jsonb;
  expected_key text :=
    'db-function-acl:ops.admit_job_cost(p_job_id uuid, p_lease_token uuid, p_route_key text, p_estimated_cost_usd numeric):carr_jobs:execute';
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Current or historical SCAC seal is not valid';
  END IF;
  SELECT contract - 'effect_class' - 'owner_package' - 'implementation_state'
                  - 'classification_authorizing' - 'source_locator'
    INTO STRICT retired
    FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version'
     AND ingress_key = expected_key;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(
      coalesce(original->'retired_secdef_execute','[]'::jsonb)) item
      WHERE item->>'ingress_key' = expected_key) THEN
    RAISE EXCEPTION 'Cost-admission capability already retired';
  END IF;
  next_contract := jsonb_set(original, '{retired_secdef_execute}',
    coalesce(original->'retired_secdef_execute','[]'::jsonb)
      || jsonb_build_array(retired));
  EXECUTE format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(next_contract::text) || '::jsonb');
END
$retire_cost_capability$;

DROP FUNCTION ops.admit_job_cost(uuid,uuid,text,numeric) RESTRICT;
DROP VIEW ops.v_cost_refusal_metric RESTRICT;
DROP TABLE ops.cost_refusal RESTRICT;

DO $verify_cost_retirement$
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Cost retirement changed an unaccounted SCAC capability';
  END IF;
END
$verify_cost_retirement$;
