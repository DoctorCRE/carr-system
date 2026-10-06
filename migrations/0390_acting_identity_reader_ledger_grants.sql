-- 0390_acting_identity_reader_ledger_grants.sql
--
-- Second half of the 0389 repair, and the reason it needed two migrations: the
-- Work Request card is served on the READER connection, not the writer.
--
-- 0389 granted the three receipt tables to both app roles and moved the failure
-- one join further along, to 42501 "permission denied for table tool_call".
-- That error is itself the evidence — public.tool_call and public.actor were
-- already readable by carr_writer, so a card running as the writer would never
-- have reached it. app_reader -> carr_reader is the path actually serving
-- work-request-card, and carr_reader held neither table.
--
-- The assumption 0389 shipped on was that the parent table's grant pair was the
-- whole shape of the problem. It was not, and the honest fix is the role the
-- verb actually runs as rather than a wider grant to both roles on the chance
-- that one of them is right.
--
-- SELECT only, and only what ACTING_IDENTITY reads. public.tool_call is the
-- append-only verb call ledger and public.actor is the internal actor list the
-- schema snapshot already carries as non-business reference data; the reader
-- gains no write on either, matching the writer's own read-only hold on
-- public.tool_call.

grant select on table public.tool_call to carr_reader;
grant select on table public.actor to carr_reader;

do $$
declare t text;
begin
  foreach t in array array['public.tool_call','public.actor'] loop
    if not has_table_privilege('carr_reader', t, 'select') then
      raise exception '0390 FAILED: carr_reader cannot read %', t;
    end if;
    if has_table_privilege('carr_reader', t, 'insert')
       or has_table_privilege('carr_reader', t, 'update')
       or has_table_privilege('carr_reader', t, 'delete') then
      raise exception '0390 FAILED: carr_reader must stay read-only on %', t;
    end if;
  end loop;
end $$;
