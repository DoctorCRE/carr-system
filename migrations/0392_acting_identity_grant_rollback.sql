-- 0392_acting_identity_grant_rollback.sql
--
-- UNDOES 0390, WHICH WAS THE WRONG FIX, AND CONVERGES ON THE ONE MAIN LANDED.
--
-- The card was broken because #751 taught it to read three receipt tables and
-- the call ledger with no grant behind them. 0389 granted the receipt tables.
-- The next permission error named public.tool_call, and 0390 answered it by
-- granting carr_reader TABLE-level select on public.tool_call AND public.actor.
--
-- Granting public.actor at table level crosses the boundary 0382 exists to hold,
-- and ops/guidance-registry-db-gate.py fails on it by name. The reader was never
-- meant to hold that table: it already held COLUMN select on (id, slug) and was
-- deliberately denied display_name. A table grant handed it display_name and
-- every future column besides. Hosted CI caught it; I did not.
--
-- public.tool_call was the same mistake one table over. A table grant hands the
-- reader every recorded tool argument and response. The card reads four columns.
--
-- The end state is exactly what 0393 declares, reached from a database that took
-- the wrong route to get here:
--
--   * public.actor      — column select (id, slug) only, table grant withdrawn
--   * public.tool_call   — column select on the four columns the card reads
--   * the receipt tables — carr_reader only; 0389 also gave carr_writer, which no
--                          write path reads, and a grant nobody needs is a grant
--                          nobody revisits
--
-- ops.work_request_acting_identity is dropped with them. It was a SECURITY
-- DEFINER projection written to avoid these grants entirely, and it is a
-- defensible shape — but main had already landed the column-scoped route and
-- shipped a gate for it, and two designs for one query is worse than either.
-- Nothing calls it; the handler is back to the inline joins main serves.

-- REVOKING A TABLE-LEVEL PRIVILEGE ALSO CLEARS THE COLUMN-LEVEL ONES of the same
-- type, so the (id, slug) grant the reader held on public.actor BEFORE 0390 does
-- not survive the revoke that undoes 0390. It is re-granted here explicitly.
-- Found by the migration's own assertion on a rehearsal branch, which is what
-- that assertion is for.
revoke select on table public.actor from carr_reader;
grant select (id, slug) on table public.actor to carr_reader;

revoke select on table public.tool_call from carr_reader;
grant select (idempotency_key, authorization_class, via, actor_id)
  on table public.tool_call to carr_reader;

revoke select on table ops.work_request_triage_receipt from carr_writer;
revoke select on table ops.sourced_work_request_plan_acceptance_receipt from carr_writer;
revoke select on table ops.sourced_work_request_outcome_feedback_acceptance_receipt from carr_writer;

drop function if exists ops.work_request_acting_identity(text);

do $$
declare t text;
begin
  if has_table_privilege('carr_reader','public.actor','select') then
    raise exception '0392 FAILED: carr_reader still holds TABLE-level public.actor select';
  end if;
  foreach t in array array['id','slug'] loop
    if not has_column_privilege('carr_reader','public.actor',t,'select') then
      raise exception '0392 FAILED: the reader lost public.actor.% with the table grant', t;
    end if;
  end loop;
  if has_column_privilege('carr_reader','public.actor','display_name','select') then
    raise exception '0392 FAILED: carr_reader can still read public.actor.display_name';
  end if;

  if has_table_privilege('carr_reader','public.tool_call','select') then
    raise exception '0392 FAILED: carr_reader still holds TABLE-level public.tool_call select';
  end if;
  foreach t in array array['idempotency_key','authorization_class','via','actor_id'] loop
    if not has_column_privilege('carr_reader','public.tool_call',t,'select') then
      raise exception '0392 FAILED: the card cannot read public.tool_call.%', t;
    end if;
  end loop;
  if has_column_privilege('carr_reader','public.tool_call','response','select') then
    raise exception '0392 FAILED: the reader can read tool_call.response; the grant widened';
  end if;

  foreach t in array array['ops.work_request_triage_receipt',
                           'ops.sourced_work_request_plan_acceptance_receipt',
                           'ops.sourced_work_request_outcome_feedback_acceptance_receipt'] loop
    if has_table_privilege('carr_writer', t, 'select') then
      raise exception '0392 FAILED: 0389 carr_writer grant survived on %', t;
    end if;
    if not has_table_privilege('carr_reader', t, 'select') then
      raise exception '0392 FAILED: the card lost its read on %', t;
    end if;
  end loop;

  if to_regprocedure('ops.work_request_acting_identity(text)') is not null then
    raise exception '0392 FAILED: the unused definer projection is still present';
  end if;
end $$;
