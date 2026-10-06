-- 0573: personal memories are readable and writable only by the partner they
-- belong to, enforced by the database rather than by one WHERE clause.
--
-- WHY (audit ruling 5, decisions 04101316 and 443fe82a, 2026-09-24).
-- public.memory_item holds the partners' memory: scope 'shared' rows everyone
-- may read, scope 'personal' rows owned by one human (owner_actor_id, CHECK-
-- paired with scope). Until now the only enforcement was mcp-server/src/
-- memory.js filtering every read with `scope='shared' or owner_actor_id=<the
-- verified sponsor>`. carr_reader and carr_writer hold direct SELECT (and the
-- writer INSERT/UPDATE) on the table, so any other query path, present or
-- future, saw every partner's personal rows. The survey behind 443fe82a found
-- this is the one table with direct grants whose rows are genuinely private per
-- partner; clients, deals, leads, parties, notes, activities, meetings and
-- loops are shared by design and deliberately get no sponsor policy.
--
-- HOW THE DATABASE KNOWS WHO IS ASKING. The server sets the transaction-local
-- setting carr.sponsoring_human_slug beside carr.acting_actor_slug in
-- mcp.js setWriterActorContext, from identity.js personalScopeForActor: the
-- verified human partner, or the verified sponsor of an agent session, never a
-- caller argument. Empty for shared-only machine tokens, which therefore see
-- shared rows only.
--
-- WHAT IS NOT CHANGED. The table owner (migrations) keeps bypassing RLS, so
-- existing SECURITY DEFINER functions owned by it behave exactly as before.
-- carr_backup gets a permissive read-all SELECT policy so the nightly
-- --enable-row-security dump stays complete (ops/backup-role-rls-coverage-
-- selftest.py enforces this for every RLS table). No function, grant or role
-- attribute is created or changed, so no sealed SCAC catalog digest moves: the
-- policies are inline rather than behind a helper function for that reason.

alter table public.memory_item enable row level security;

create policy memory_item_sponsor_read on public.memory_item
  for select
  using (
    scope = 'shared'
    or owner_actor_id = (
      select a.id from public.actor a
       where a.slug = nullif(current_setting('carr.sponsoring_human_slug', true), '')
         and a.kind = 'human'));

create policy memory_item_sponsor_insert on public.memory_item
  for insert
  with check (
    scope = 'shared'
    or owner_actor_id = (
      select a.id from public.actor a
       where a.slug = nullif(current_setting('carr.sponsoring_human_slug', true), '')
         and a.kind = 'human'));

create policy memory_item_sponsor_update on public.memory_item
  for update
  using (
    scope = 'shared'
    or owner_actor_id = (
      select a.id from public.actor a
       where a.slug = nullif(current_setting('carr.sponsoring_human_slug', true), '')
         and a.kind = 'human'))
  with check (
    scope = 'shared'
    or owner_actor_id = (
      select a.id from public.actor a
       where a.slug = nullif(current_setting('carr.sponsoring_human_slug', true), '')
         and a.kind = 'human'));

-- carr_backup exists in Production (0119) but is deliberately absent from
-- db/schema.sql, so the policy is created only where the role exists.
do $carr_backup_memory_item_policy$
begin
  if exists (select 1 from pg_roles where rolname = 'carr_backup') then
    create policy carr_backup_full_read_memory_item on public.memory_item for select to carr_backup using (true);
  end if;
end
$carr_backup_memory_item_policy$;
