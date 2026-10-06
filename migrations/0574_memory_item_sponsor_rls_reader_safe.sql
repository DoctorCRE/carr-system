-- 0574: the memory_item sponsor policies stop reading actor.kind, so a
-- carr_reader query sees shared rows instead of failing.
--
-- WHY (independent review of PR 1183, 2026-09-24). 0573's policies resolved
-- the sponsor with `select a.id from public.actor a where a.slug = <setting>
-- and a.kind = 'human'`. carr_reader holds SELECT on actor (id, slug) only, so
-- any carr_reader SELECT on memory_item raised "permission denied for table
-- actor" rather than returning the shared rows it is entitled to. Nothing
-- reads memory_item as carr_reader today (the memory verbs run on the writer
-- transaction since 1183), but a reader should fail soft, not hard.
--
-- WHY DROPPING THE kind TEST IS SAFE. carr.sponsoring_human_slug is set only by
-- mcp.js setWriterActorContext, from identity.js personalScopeForActor, which
-- returns a sponsor only for a known partner (isKnownPartner); a caller can
-- never supply it. The slug therefore always names a human partner, and
-- actor.slug is unique, so the kind test added nothing but the column
-- dependency. The policies are otherwise identical to 0573. No function, grant
-- or role attribute changes, so no sealed catalog digest moves.

drop policy memory_item_sponsor_read on public.memory_item;
drop policy memory_item_sponsor_insert on public.memory_item;
drop policy memory_item_sponsor_update on public.memory_item;

create policy memory_item_sponsor_read on public.memory_item
  for select
  using (
    scope = 'shared'
    or owner_actor_id = (
      select a.id from public.actor a
       where a.slug = nullif(current_setting('carr.sponsoring_human_slug', true), '')));

create policy memory_item_sponsor_insert on public.memory_item
  for insert
  with check (
    scope = 'shared'
    or owner_actor_id = (
      select a.id from public.actor a
       where a.slug = nullif(current_setting('carr.sponsoring_human_slug', true), '')));

create policy memory_item_sponsor_update on public.memory_item
  for update
  using (
    scope = 'shared'
    or owner_actor_id = (
      select a.id from public.actor a
       where a.slug = nullif(current_setting('carr.sponsoring_human_slug', true), '')))
  with check (
    scope = 'shared'
    or owner_actor_id = (
      select a.id from public.actor a
       where a.slug = nullif(current_setting('carr.sponsoring_human_slug', true), '')));
