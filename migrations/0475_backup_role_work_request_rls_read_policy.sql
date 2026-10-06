-- 0475: let the nightly backup read ops.work_request under row-level security
-- (WR-000044, Joe-approved 2026-09-01, decision 11376c54).
--
-- THE FAILURE. The nightly GitHub-Actions pg_dump (bin/backup-dump.sh, role
-- carr_backup) died with:
--   pg_dump: error: query would be affected by row-level security policy
--            for table "work_request"
-- pg_dump runs every data query with `row_security = off`. When RLS is off and
-- the querying role is neither the table owner nor a BYPASSRLS role, PostgreSQL
-- refuses the read rather than silently returning a subset — a fail-closed
-- guard against an incomplete dump. 0324 enabled RLS on ops.work_request (the
-- SIEP direct-writer boundary), and carr_backup is a plain SELECT-only login
-- with no BYPASSRLS, so the nightly read is refused and no backup is produced.
--
-- WHY A POLICY, NOT `ALTER ROLE carr_backup BYPASSRLS`. BYPASSRLS is a role
-- ATTRIBUTE, and role attributes (login, superuser, createdb, createrole,
-- replication, bypassrls) for every role matching '^carr_' are hashed into the
-- pinned SCAC v10 live-catalog census (ops.scac_mutation_catalog_v10_current(),
-- migration 0471). carr_backup matches '^carr_', so flipping its bypassrls bit
-- would move that sealed census digest and require a v10->v11 registry reseal
-- plus a regenerated schema snapshot. A table POLICY is a schema object, not a
-- role attribute: it is hashed by none of the four SCAC census blocks (function
-- ACL, relation ACL, column ACL, role/membership/ownership), so this migration
-- changes no sealed digest and needs no reseal. Verified empirically on a
-- disposable cluster: the census (role+membership) sub-digest is byte-identical
-- before and after this policy, while the BYPASSRLS flip moved it.
--
-- WHAT THIS BUYS, PAIRED WITH bin/backup-dump.sh. The backup command gains
-- `--enable-row-security` (row_security = on), so the dump reads under RLS and
-- this permissive SELECT policy lets carr_backup see EVERY row of work_request
-- (USING (true), no filter) — including SIEP-program rows, which the write
-- policies fence but which a backup must still capture in full. carr_backup
-- gains NOTHING else: still SELECT-only, still NOSUPERUSER / NOCREATEDB /
-- NOCREATEROLE / NOREPLICATION / NOBYPASSRLS, still in no privilege bundle.
--
-- THE COMPLETENESS OBLIGATION `--enable-row-security` CREATES. With row_security
-- on, pg_dump fails OPEN: a table whose RLS hides rows from carr_backup is
-- dumped short, silently. Today ops.work_request is the only RLS-enabled table
-- in public+ops. The invariant that every such table must carry a permissive
-- carr_backup read-all policy is enforced by
-- ops/backup-role-rls-coverage-selftest.py: its source contract runs in ci.sh
-- (a future RLS addition without a carr_backup read-all policy fails loudly),
-- and its live disposable-Postgres proof runs when CARR_LOCAL_PG_DSN is set.
--
-- CONDITIONAL ON THE ROLE, like 0473. carr_backup is a production login;
-- db/schema.sql deliberately does not create it, so on a CI rebuild the role is
-- absent and this migration is inert (a policy naming a missing role cannot be
-- created). Where the role exists the policy is created; re-running is a no-op.
-- The runner owns the transaction — no explicit begin/commit (banned since 0339).

do $backup_rls_read$
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_backup') then
    raise notice '0475: carr_backup absent (non-production database); no work_request read policy created';
    return;
  end if;
  if exists (
    select 1 from pg_policy
     where polname = 'carr_backup_full_read'
       and polrelid = 'ops.work_request'::regclass
  ) then
    raise notice '0475: carr_backup_full_read already present on ops.work_request; nothing to do';
    return;
  end if;
  create policy carr_backup_full_read on ops.work_request
    for select to carr_backup using (true);
end $backup_rls_read$;
