-- 0473: restore the ops-schema backup default privilege after 0451 lands.
--
-- WHY. 0451 creates the four assurance tables and then self-checks that no
-- non-owner grantee exists on them. Production's ops schema carries a standing
-- ALTER DEFAULT PRIVILEGES rule (neondb_owner -> carr_backup gets SELECT on
-- every new table and sequence), so on production every fresh table is born
-- with exactly that grant and 0451's check refuses - while CI's disposable
-- database, which has no default ACLs and no carr_backup role, stays green.
-- Rather than edit the sealed, digest-pinned 0451 (any byte of drift cascades
-- through the reviewed transaction-control allowlist, the SCAC mutation
-- registry, its forward migration, the full-entry-set seals, and the schema
-- snapshot), the production apply is preceded by a one-time receipted revoke
-- of that default privilege (Joe-gated break-glass), 0451 then applies exactly
-- as reviewed, and THIS migration restores the intended end state:
--   1. the backup role's SELECT on the four assurance tables 0451 created;
--   2. the standing default privilege for future ops tables and sequences.
--
-- CONDITIONAL ON THE ROLE, deliberately: carr_backup exists on production
-- only. Creating it here would add a role row to the SCAC catalog census
-- (which pins role/membership/ownership rows exactly), so where the role is
-- absent this migration is inert - the same environments where the default
-- privilege never existed either. SELECT grants and default ACLs appear in
-- none of the sealed censuses, so where the role exists this changes no
-- sealed digest either.

do $restore_backup_priv$
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_backup') then
    raise notice '0473: carr_backup absent (non-production database); nothing to restore';
    return;
  end if;
  -- Sweep the whole schema, not only 0451's four tables: every table created
  -- by the pending migrations that apply inside the revoke window (0452-0472)
  -- was also born without the backup grant. SELECT grants are not part of any
  -- sealed census, and re-granting an existing grant is a no-op.
  grant select on all tables in schema ops to carr_backup;
  grant select on all sequences in schema ops to carr_backup;
  execute 'alter default privileges for role neondb_owner in schema ops '
          'grant select on tables to carr_backup';
  execute 'alter default privileges for role neondb_owner in schema ops '
          'grant select on sequences to carr_backup';
end $restore_backup_priv$;
