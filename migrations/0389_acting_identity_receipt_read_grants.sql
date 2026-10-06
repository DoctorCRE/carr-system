-- 0389_acting_identity_receipt_read_grants.sql
--
-- The Work Request card's acting_identity block shipped to production on
-- 2026-08-28 (release r-2026-08-27-worker-97a7ace-ff99c231) and broke the verb
-- outright: work-request-card returned 42501 "permission denied for table
-- work_request_triage_receipt" for every request, so the card that was supposed
-- to gain a field lost all of them.
--
-- ACTING_IDENTITY in mcp-server/src/work-request-intake.js unions three receipt
-- tables that no app role had ever been granted. Nothing about the join is
-- wrong; the query simply reads tables whose grants were never written, and the
-- work of #751 was source-only so no migration accompanied it. Its two other
-- inputs, public.tool_call and public.actor, were already granted to
-- carr_writer, which is why the failure surfaced on the receipt tables and not
-- on the ledger join.
--
-- SELECT ONLY, and to exactly the two roles that already read the parent
-- ops.work_request. These are append-only authority receipts: no role gains
-- insert, update or delete here, and the acting_identity projection only ever
-- reads them. Granting the reader alongside the writer matches the parent
-- table's own grant pair rather than inventing a narrower shape for children of
-- a table both roles can already read.

grant select on table ops.work_request_triage_receipt to carr_reader, carr_writer;
grant select on table ops.sourced_work_request_plan_acceptance_receipt to carr_reader, carr_writer;
grant select on table ops.sourced_work_request_outcome_feedback_acceptance_receipt to carr_reader, carr_writer;

do $$
declare
  t text;
  r text;
begin
  foreach t in array array[
    'ops.work_request_triage_receipt',
    'ops.sourced_work_request_plan_acceptance_receipt',
    'ops.sourced_work_request_outcome_feedback_acceptance_receipt'
  ] loop
    foreach r in array array['carr_reader','carr_writer'] loop
      if not has_table_privilege(r, t, 'select') then
        raise exception '0389 FAILED: % cannot read %', r, t;
      end if;
      if has_table_privilege(r, t, 'insert')
         or has_table_privilege(r, t, 'update')
         or has_table_privilege(r, t, 'delete') then
        raise exception '0389 FAILED: % must hold read-only on %, and holds more', r, t;
      end if;
    end loop;
  end loop;
end $$;
