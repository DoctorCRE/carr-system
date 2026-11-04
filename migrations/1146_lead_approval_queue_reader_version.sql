-- rollback: revoke select (version) on public.lead from carr_reader.
-- The approval queue returns the current lead version for proposed stage moves.
-- Keep the existing column-scoped reader contract; no table-wide access is needed.
grant select (version) on public.lead to carr_reader;
