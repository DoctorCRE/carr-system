-- 0354_authority_event_ledger_grant.sql
--
-- Second companion grant for the authority-connection amendment path (first:
-- 0353's rule row lock).  With the lock granted, live amend-rule advanced one
-- statement and died 42501 "permission denied for table event": the version
-- guard's conflict path SELECTs public.event to list intervening edits, and
-- every write verb's audit trail INSERTs its event row through writeEvent —
-- both on the same authority connection S10 moved the verb onto.  Observed in
-- production 2026-08-27 on the second compression attempt.
--
-- SELECT + INSERT only: the ledger is append-only for every role; authority
-- gains no update or delete.

grant select, insert on table public.event to carr_authority;

do $$
begin
  if not has_table_privilege('carr_authority','public.event','select')
     or not has_table_privilege('carr_authority','public.event','insert') then
    raise exception '0354 FAILED: authority cannot read or append the event ledger';
  end if;
  if has_table_privilege('carr_authority','public.event','update')
     or has_table_privilege('carr_authority','public.event','delete') then
    raise exception '0354 FAILED: the event ledger must stay append-only for authority';
  end if;
end $$;
