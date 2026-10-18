-- 0794_catch_me_up_writer_read_grants.sql
--
-- Forward fix for release r-2026-10-03-01, whose postflight golden suite failed
-- `catch-me-up V-CPA-006` with 42501 "permission denied for view
-- v_subject_timeline". The fault shipped with #1476 (first red on release run
-- 20261002T093004Z), not with this release's migrations.
--
-- #1476 moved catch-me-up, find-and-catch-up and prepare-conversation onto the
-- read-only WRITER route (app_writer -> carr_writer) so catch-me-up can read
-- its actor-bound tool_call ledger. Two views those handlers read were only
-- ever granted to carr_reader: 0004 granted carr_writer "all tables" BEFORE it
-- created v_subject_timeline and v_deal_board, then granted the views to the
-- reader alone.
--   v_subject_timeline  catch-me-up's timeline read
--   v_deal_board        find's deal match, the first stage of both compositions
--
-- SELECT only. carr_writer already reads every base table under both views
-- (activity, event, actor, deal, client, party, deal_phase, deal_participant),
-- so this exposes nothing new to the role; it only lets the role read the
-- projection its own route now serves.

grant select on table public.v_subject_timeline, public.v_deal_board to carr_writer;

do $$
declare v text;
begin
  foreach v in array array['public.v_subject_timeline','public.v_deal_board'] loop
    if not has_table_privilege('carr_writer', v, 'select') then
      raise exception '0794 FAILED: carr_writer cannot read %', v;
    end if;
    if has_table_privilege('carr_writer', v, 'insert')
       or has_table_privilege('carr_writer', v, 'update')
       or has_table_privilege('carr_writer', v, 'delete') then
      raise exception '0794 FAILED: carr_writer must stay read-only on %', v;
    end if;
  end loop;
end $$;
