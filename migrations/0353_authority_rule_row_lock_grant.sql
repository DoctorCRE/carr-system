-- 0353_authority_rule_row_lock_grant.sql
--
-- WR-000019 slice S10 moved amend-rule to the authority connection (the verb
-- is authorityOnly; Joe's own login runs it).  Its handler performs the
-- standard optimistic version guard — "select version from rule where id=$1
-- for update" — which requires UPDATE privilege on the table to take the row
-- lock.  carr_authority could already SELECT rule (the resolve path worked
-- live) but not lock it, so every live amendment died with
-- "permission denied for table rule" (sqlstate 42501, observed in production
-- 2026-08-27 on the first post-release compression batch).  The proposed-rule
-- amendment path also updates rule directly under the same connection.
--
-- The grant is UPDATE on public.rule to carr_authority: the same principal
-- already executes the SECURITY DEFINER governance functions that mutate the
-- same rows; this only lets its own optimistic guard hold the lock.

grant update on table public.rule to carr_authority;

-- Companion reads: rule's UPDATE triggers (ops.require_rule_admission and the
-- approval-preimage guards) read these two tables while firing as the writer's
-- role; without SELECT the trigger itself dies mid-write.  Read-only, both.
grant select on table ops.rule_admission to carr_authority;
grant select on table ops.rule_enforcement_point to carr_authority;

do $$
begin
  if not has_table_privilege('carr_authority','public.rule','update') then
    raise exception '0353 FAILED: carr_authority cannot lock/update rule rows';
  end if;
  if has_table_privilege('carr_reader','public.rule','update') then
    raise exception '0353 FAILED: reader may update rule rows';
  end if;
  if not has_table_privilege('carr_authority','ops.rule_admission','select')
     or not has_table_privilege('carr_authority','ops.rule_enforcement_point','select') then
    raise exception '0353 FAILED: authority lacks the trigger companion reads';
  end if;
end $$;
