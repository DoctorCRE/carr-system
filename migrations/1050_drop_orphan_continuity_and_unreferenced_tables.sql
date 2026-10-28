-- rollback: forward-only; recreate retired tables only with a new migration.
-- expand-contract: contract; retire twelve unused tables and account for their sealed grants.
-- lock-review: ACCESS EXCLUSIVE on twelve retired tables; lock acquisition is bounded to five seconds.
--
-- WR573.S3.J1, database audit 2026-10-28 F7 / plan P8.
--
-- Kept on purpose: claude_continuity_checkpoint, claude_continuity_leaf and
-- codex_continuity_checkpoint are live (attach-memory, codex-checkpoint,
-- read-session-identity). No trigger on a checkpoint writes a revision row,
-- so dropping the revision tables does not touch the checkpoint verbs.
--
-- Retained candidates have current readers, dependencies or provider ownership:
-- doctrine_migration_batch, cadence_rule, ops.settings_change,
-- ops.staging_release_approval_receipt, ops.completion_relation,
-- neon_auth.* remains provider-owned; ops.tour_* stays live under WR-000012.
SET LOCAL lock_timeout = '5s';

DO $retire_orphan_grants$
DECLARE
  original jsonb := ops.scac_current_grant_contract();
  relation_keys text[] := ARRAY[
    'db-relation-acl:public.actor_profile:carr_writer:insert',
    'db-relation-acl:public.actor_profile:carr_writer:update',
    'db-relation-acl:public.ammo_item:carr_writer:insert',
    'db-relation-acl:public.ammo_item:carr_writer:update',
    'db-relation-acl:public.claude_continuity_event:carr_writer:insert',
    'db-relation-acl:public.claude_continuity_revision:carr_writer:insert',
    'db-relation-acl:public.codex_continuity_event:carr_writer:insert',
    'db-relation-acl:public.codex_continuity_revision:carr_writer:insert',
    'db-relation-acl:public.investigation_evidence:carr_writer:insert',
    'db-relation-acl:public.investigation_evidence:carr_writer:update',
    'db-relation-acl:public.search_candidate:carr_writer:insert',
    'db-relation-acl:public.search_candidate:carr_writer:update',
    'db-relation-acl:public.sensitive_blob:carr_writer:insert',
    'db-relation-acl:public.sensitive_blob:carr_writer:update'];
  retired_relations jsonb;
  next_contract jsonb;
BEGIN
  SELECT jsonb_agg(contract - 'effect_class' - 'owner_package' - 'implementation_state'
                           - 'classification_authorizing' - 'source_locator' ORDER BY ingress_key)
    INTO retired_relations FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version' AND ingress_key = ANY(relation_keys);
  IF coalesce(jsonb_array_length(retired_relations),0) <> cardinality(relation_keys) THEN
    RAISE EXCEPTION 'Expected orphan grants absent from the sealed registry';
  END IF;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(original->'retired_relation_dml','[]'::jsonb)) item
      WHERE item->>'ingress_key' = ANY(relation_keys)) THEN
    RAISE EXCEPTION 'An orphan grant is already retired';
  END IF;
  next_contract := jsonb_set(original, '{retired_relation_dml}',
    coalesce(original->'retired_relation_dml','[]'::jsonb) || retired_relations);
  EXECUTE format('CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(next_contract::text) || '::jsonb');
END
$retire_orphan_grants$;

DROP TABLE IF EXISTS public.claude_continuity_event RESTRICT;
DROP TABLE IF EXISTS public.codex_continuity_event RESTRICT;
DROP TABLE IF EXISTS public.claude_continuity_revision RESTRICT;
DROP TABLE IF EXISTS public.codex_continuity_revision RESTRICT;
DROP FUNCTION IF EXISTS public.claude_continuity_revision_append_only() RESTRICT;
DROP FUNCTION IF EXISTS public.codex_continuity_revision_append_only() RESTRICT;

DROP TABLE IF EXISTS ops.retired_build_plan_review RESTRICT;
DROP TABLE IF EXISTS ops.retired_build_admission_revision RESTRICT;
DROP FUNCTION IF EXISTS ops.retired_build_receipts_are_immutable() RESTRICT;

DROP TABLE IF EXISTS public.investigation_evidence RESTRICT;
DROP TABLE IF EXISTS public.sensitive_blob RESTRICT;
DROP TABLE IF EXISTS public.search_candidate RESTRICT;
DROP TABLE IF EXISTS public.actor_profile RESTRICT;
DROP TABLE IF EXISTS public.ammo_item RESTRICT;
DROP TABLE IF EXISTS ops.workflow_cutover_retire_receipt_use RESTRICT;
