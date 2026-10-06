-- 0579: personal memories are readable by both partners; writing one stays
-- limited to its owner.
--
-- WHY (decision 9c06bf1e, Joe 2026-09-24): "I'm not sure we need to hide personal doctrine from
-- each other though", then "Yea handle both that way" for doctrine and
-- memories. 'personal' scopes which partner a memory APPLIES to, not who may
-- see it. recall-memory, the path that applies memory to a session, keeps its
-- own query filter (shared, or personal owned by the caller's sponsor), so
-- opening the read fence changes what a partner can look at, not what is
-- applied to them.
--
-- WHAT STAYS. memory_item_sponsor_insert and memory_item_sponsor_update from
-- 0574 are unchanged: a session still cannot create, edit, promote, correct or
-- forget a personal memory owned by the other partner. Row security stays on.
-- No function, grant or role attribute changes.

drop policy memory_item_sponsor_read on public.memory_item;

create policy memory_item_partners_read on public.memory_item
  for select
  using (true);
