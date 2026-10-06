-- 0393_work_request_card_receipt_reads.sql
--
-- work-request-card has been DOWN in production since the loop-542 deploy.  Every
-- call returns "permission denied for table work_request_triage_receipt"
-- (sqlstate 42501), observed 2026-08-27 on a request that the same verb had read
-- successfully hours earlier, before the deploy.
--
-- #751 taught the card to say whether a human or an agent performed each
-- authority act.  Its ACTING_IDENTITY query (mcp-server/src/work-request-intake.js,
-- used only by the card at line 507) reads three receipt tables DIRECTLY:
--
--   ops.work_request_triage_receipt
--   ops.sourced_work_request_plan_acceptance_receipt
--   ops.sourced_work_request_outcome_feedback_acceptance_receipt
--
-- None of the three carries a single grant.  That was harmless for as long as it
-- was true that every reader of them went through a SECURITY DEFINER function --
-- ops.triage_sourced_work_request and its siblings run as their owner, so the
-- caller's privileges never came into it.  The first handler to read the tables
-- itself is the first to need the grant, and it did not get one.
--
-- Postgres checks table privilege when it plans the statement, not when a row is
-- returned, so this fails for every request whether or not it has any receipts.
-- The card fails closed, which is why the feature reads as "not running".
--
-- SELECT ONLY, AND ONLY TO THE READER.  The card is a read verb on the reader
-- connection; nothing here needs to write these rows, and the functions that DO
-- write them keep doing it as their definer exactly as before.  carr_writer is
-- deliberately not granted: no write path reads this query, and a grant nobody
-- needs is a grant nobody revisits.

grant select on table ops.work_request_triage_receipt to carr_reader;
grant select on table ops.sourced_work_request_plan_acceptance_receipt to carr_reader;
grant select on table ops.sourced_work_request_outcome_feedback_acceptance_receipt to carr_reader;

-- A FOURTH TABLE, and granting only the three would have left the card just as
-- broken on the next statement it plans.  The same query left-joins
-- public.tool_call to recover the authorization class and via of the call that
-- wrote each receipt.  tool_call is granted to carr_authority and carr_writer and
-- has never been granted to the reader.
--
-- COLUMN-SCOPED, following the precedent public.actor already sets for this same
-- role: the reader holds select (id, slug) there and is deliberately denied
-- display_name.  The card reads exactly four columns of tool_call, so it gets
-- exactly those four.  A full-table grant would hand the reader every recorded
-- tool argument, which is a much larger surface than this feature asked for.
grant select (idempotency_key, authorization_class, via, actor_id)
  on table public.tool_call to carr_reader;

do $$
declare
  t text;
begin
  foreach t in array array['ops.work_request_triage_receipt',
                           'ops.sourced_work_request_plan_acceptance_receipt',
                           'ops.sourced_work_request_outcome_feedback_acceptance_receipt']
  loop
    if not has_table_privilege('carr_reader', t, 'select') then
      raise exception '0393 FAILED: carr_reader still cannot select %', t;
    end if;
    -- Column privileges are not table privileges: has_table_privilege is false for
    -- a column-scoped grant, so tool_call is asserted below with
    -- has_column_privilege rather than folded into this loop.
    -- The point of the migration is the READ. If it ever hands out a write here,
    -- that is a different change and it should not arrive under this filename.
    if has_table_privilege('carr_reader', t, 'insert')
       or has_table_privilege('carr_reader', t, 'update')
       or has_table_privilege('carr_reader', t, 'delete') then
      raise exception '0393 FAILED: reader was given write access to %', t;
    end if;
  end loop;

  foreach t in array array['idempotency_key','authorization_class','via','actor_id']
  loop
    if not has_column_privilege('carr_reader', 'public.tool_call', t, 'select') then
      raise exception '0393 FAILED: carr_reader cannot select public.tool_call.%', t;
    end if;
  end loop;
  -- The scope is the point. If the reader can see a column the card never asked
  -- for, this grant has widened past the feature that needed it.
  if has_column_privilege('carr_reader', 'public.tool_call', 'response', 'select') then
    raise exception '0393 FAILED: reader can read tool_call.response; the grant widened';
  end if;
end $$;
