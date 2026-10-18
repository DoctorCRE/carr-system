-- rollback: comment on column public.loop_item.extra_cells is null; no row, grant or constraint is touched.
-- add-loop and update-loop now store a loop's residue (Joe 2026-10-12, decision
-- cbe91287) in extra_cells under the key blocker_residue. The column already
-- exists and carries the idea bank's cells; this records the new key where a
-- reader of the schema will find it. No schema shape, data or permission changes.
comment on column public.loop_item.extra_cells is
  'Kind-specific cells that are not columns. Idea rows: last_surfaced, status, domain (the idea bank''s own columns). Open loops whose blocker_class is human_only, ruling or capability: blocker_residue = {kind, host}, the act a provider or the OS reserves to the account holder (kind in provider_login, os_password, physical_act, money_movement, external_send, joe_only_fact) and the provider or host that reserves it, written by add-loop/update-loop since decision cbe91287. Rows filed before that decision may carry none.';
