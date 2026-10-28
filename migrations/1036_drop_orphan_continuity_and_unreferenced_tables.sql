-- rollback: forward-only — the dropped rows have no reader; the continuity verbs' audit trail stays in event (claude-record-event, codex-record-event). Recreate a table from db/schema.sql history with a new migration if one is ever needed.
-- expand-contract: contract — no verb, view, function, policy, trigger, script or doctorcre-app code reads or writes these fourteen tables; the verbs that wrote the continuity event/revision tables are retired (ops/config/retired-verbs.v1.json).
-- lock-review: ACCESS EXCLUSIVE on fourteen tables nothing touches (largest codex_continuity_revision 25 MB, claude_continuity_event 23 MB); lock acquisition is bounded to five seconds.
--
-- WR573.S3.J1, database audit 2026-10-28 F7 / plan P8.
--
-- Kept on purpose: claude_continuity_checkpoint, claude_continuity_leaf and
-- codex_continuity_checkpoint are live (attach-memory, codex-checkpoint,
-- read-session-identity). No trigger on a checkpoint writes a revision row,
-- so dropping the revision tables does not touch the checkpoint verbs.
--
-- Left in place because a reader exists (see the PR's Unmet list):
-- doctrine_migration_batch, cadence_rule, ops.settings_change,
-- ops.staging_release_approval_receipt, ops.completion_relation,
-- neon_auth.*; ops.tour_* belongs to the tour_* retirement migration.
--
-- RESTRICT throughout: an unexpected dependent object fails the migration
-- instead of being dropped with it.
SET LOCAL lock_timeout = '5s';

DROP TABLE public.claude_continuity_event RESTRICT;
DROP TABLE public.codex_continuity_event RESTRICT;
DROP TABLE public.claude_continuity_revision RESTRICT;
DROP TABLE public.codex_continuity_revision RESTRICT;
DROP FUNCTION public.claude_continuity_revision_append_only() RESTRICT;
DROP FUNCTION public.codex_continuity_revision_append_only() RESTRICT;

DROP TABLE ops.retired_build_plan_review RESTRICT;
DROP TABLE ops.retired_build_admission_revision RESTRICT;
DROP FUNCTION ops.retired_build_receipts_are_immutable() RESTRICT;

DROP TABLE public.memory_evidence RESTRICT;
DROP TABLE public.deal_reattach_log RESTRICT;
DROP TABLE public.investigation_evidence RESTRICT;
DROP TABLE public.sensitive_blob RESTRICT;
DROP TABLE public.search_candidate RESTRICT;
DROP TABLE public.actor_profile RESTRICT;
DROP TABLE public.ammo_item RESTRICT;
DROP TABLE ops.workflow_cutover_retire_receipt_use RESTRICT;
