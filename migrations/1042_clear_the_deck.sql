-- rollback: forward fix only; restore an expired row with clear-the-deck-apply keep, never by deleting history.
-- lock-review: brief ACCESS EXCLUSIVE on next_action, loop_item and candidate_pool to widen one status check each and backfill confirmation (about 13k rows, touch triggers paused so versions stay put); no rewrite.
-- WR577.S1.J1: confirm-or-expire for automation-made partner obligations.
-- Partner-made: created by joe or dell, or through a shared local door
-- (joe-local, dell-local) into a partner's own queue (owner joe, dell, a door,
-- joint, or no owner). A door row owned by an agent seat such as claude is
-- automation: the retired merge queue wrote that way. Partner-made rows, and any
-- row a partner (joe, dell) later edits or keeps, are confirmed for good
-- (confirmed_by/confirmed_at). An unconfirmed next action or loop (30 days) or
-- candidate (60 days) becomes stale: it is listed on the deck and nothing else.
-- Only the stored, reversible `expired` status, written by a partner drop or the
-- one-time cleanup, takes a row off triage and Home. Renewal-radar candidates
-- are left out: their sealed daily snapshot owns that membership.

create function carr_is_partner_actor(p_actor uuid) returns boolean
  language sql stable set search_path = pg_catalog, public, pg_temp
  as $$ select exists (select 1 from public.actor a where a.id = p_actor and a.slug in ('joe', 'dell')) $$;
comment on function carr_is_partner_actor(uuid) is 'WR577: a partner-attributed actor (joe, dell). The shared local doors joe-local and dell-local are not: Call Mode and local agents both use them.';
create function carr_is_partner_made(p_created_by uuid, p_owner text) returns boolean
  language sql stable set search_path = pg_catalog, public, pg_temp
  as $$ select exists (select 1 from public.actor a where a.id = p_created_by and (a.slug in ('joe', 'dell')
          or (a.slug in ('joe-local', 'dell-local')
              and (p_owner is null or lower(p_owner) in ('joe', 'dell', 'joe-local', 'dell-local', 'joint'))))) $$;
comment on function carr_is_partner_made(uuid, text) is 'WR577: a row is partner-made when joe or dell created it, or a shared local door created it into a partner queue. A door row owned by an agent seat (claude, codex and others) is automation.';
grant execute on function carr_is_partner_actor(uuid) to carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function carr_is_partner_made(uuid, text) to carr_reader, carr_writer, carr_jobs, carr_authority;

alter table next_action add column confirmed_at timestamptz, add column confirmed_by uuid references actor(id);
alter table loop_item add column confirmed_at timestamptz, add column confirmed_by uuid references actor(id);
alter table candidate_pool add column confirmed_at timestamptz, add column confirmed_by uuid references actor(id);
comment on column next_action.confirmed_at is 'First partner confirmation (partner-made, a joe/dell edit or history event, or a deck keep). A confirmed row never goes stale.';
comment on column loop_item.confirmed_at is 'First partner confirmation (partner-made, a joe/dell edit or history event, or a deck keep). A confirmed row never goes stale.';
comment on column candidate_pool.confirmed_at is 'First partner confirmation (partner-made, a joe/dell edit or history event, or a deck keep). A confirmed row never goes stale.';

alter table next_action drop constraint next_action_status_check,
  add constraint next_action_status_check check (status = any (array['open', 'done', 'dropped', 'expired']));
alter table loop_item drop constraint loop_item_status_check,
  add constraint loop_item_status_check check (status = any (array['open', 'done', 'dropped', 'expired']));
alter table candidate_pool drop constraint candidate_pool_status_check,
  add constraint candidate_pool_status_check check (status = any (array['pool', 'promoted', 'suppressed_dup', 'declined', 'expired']));

-- Backfill from history: partner-made at creation, then a joe/dell last update,
-- then the earliest joe/dell event on the row.
alter table next_action disable trigger next_action_touch;
alter table loop_item disable trigger loop_item_touch;
alter table candidate_pool disable trigger prospect_pool_touch;
with deck_history as (
  select distinct on (e.subject_id) e.subject_id, e.actor_id, e.occurred_at
    from event e where carr_is_partner_actor(e.actor_id) order by e.subject_id, e.occurred_at
), source as (
  select n.id, carr_is_partner_made(n.created_by, o.slug) as made, carr_is_partner_actor(n.updated_by) as edited,
         h.actor_id, h.occurred_at
    from next_action n join actor o on o.id = n.owner_id left join deck_history h on h.subject_id = n.id
)
update next_action t set
  confirmed_by = case when s.made then t.created_by when s.edited then t.updated_by else s.actor_id end,
  confirmed_at = case when s.made then t.created_at when s.edited then t.updated_at else s.occurred_at end
  from source s where s.id = t.id and (s.made or s.edited or s.actor_id is not null);
with deck_history as (
  select distinct on (e.subject_id) e.subject_id, e.actor_id, e.occurred_at
    from event e where carr_is_partner_actor(e.actor_id) order by e.subject_id, e.occurred_at
), source as (
  select l.id, carr_is_partner_made(l.created_by, l.owner) as made, carr_is_partner_actor(l.updated_by) as edited,
         h.actor_id, h.occurred_at
    from loop_item l left join deck_history h on h.subject_id = l.id
)
update loop_item t set
  confirmed_by = case when s.made then t.created_by when s.edited then t.updated_by else s.actor_id end,
  confirmed_at = case when s.made then t.created_at when s.edited then t.updated_at else s.occurred_at end
  from source s where s.id = t.id and (s.made or s.edited or s.actor_id is not null);
with deck_history as (
  select distinct on (e.subject_id) e.subject_id, e.actor_id, e.occurred_at
    from event e where carr_is_partner_actor(e.actor_id) order by e.subject_id, e.occurred_at
), source as (
  select p.id, carr_is_partner_made(p.created_by, null) as made, carr_is_partner_actor(p.updated_by) as edited,
         h.actor_id, h.occurred_at
    from candidate_pool p left join deck_history h on h.subject_id = p.id
   where p.source <> 'renewal-radar'
)
update candidate_pool t set
  confirmed_by = case when s.made then t.created_by when s.edited then t.updated_by else s.actor_id end,
  confirmed_at = case when s.made then t.created_at when s.edited then t.updated_at else s.occurred_at end
  from source s where s.id = t.id and (s.made or s.edited or s.actor_id is not null);
alter table next_action enable trigger next_action_touch;
alter table loop_item enable trigger loop_item_touch;
alter table candidate_pool enable trigger prospect_pool_touch;

-- From now on a partner-made insert, or a joe/dell edit of a live row, confirms
-- it at once. A drop (status expired) is a decision, not a confirmation.
create function carr_confirm_partner_touch() returns trigger
  language plpgsql set search_path = pg_catalog, public, pg_temp
  as $$
declare
  v_owner text;
begin
  if new.confirmed_at is not null or new.status not in ('open', 'pool') then return new; end if;
  if tg_op = 'INSERT' then
    v_owner := case tg_table_name
      when 'next_action' then (select a.slug from public.actor a where a.id = (to_jsonb(new) ->> 'owner_id')::uuid)
      when 'loop_item' then to_jsonb(new) ->> 'owner' end;
    if public.carr_is_partner_made(new.created_by, v_owner) then
      new.confirmed_at := now();
      new.confirmed_by := new.created_by;
      return new;
    end if;
  end if;
  if public.carr_is_partner_actor(new.updated_by) then
    new.confirmed_at := now();
    new.confirmed_by := new.updated_by;
  end if;
  return new;
end $$;
create trigger next_action_confirm before insert or update on next_action
  for each row execute function carr_confirm_partner_touch();
create trigger loop_item_confirm before insert or update on loop_item
  for each row execute function carr_confirm_partner_touch();
create trigger candidate_pool_confirm before insert or update on candidate_pool
  for each row execute function carr_confirm_partner_touch();

create view v_deck_item as
with item as (
  select 'next_action'::text as kind, na.id, na.version, o.slug as owner, na.description as title,
         na.created_by, na.created_at, 30 as days
    from next_action na join actor o on o.id = na.owner_id
   where na.status = 'open' and na.confirmed_at is null
  union all
  select 'loop', li.id, li.version, li.owner, coalesce(nullif(li.title, ''), split_part(li.body, E'\n', 1)),
         li.created_by, li.created_at, 30
    from loop_item li
   where li.status = 'open' and li.confirmed_at is null and li.kind in ('open_loop', 'team_loop', 'action_required')
  union all
  select 'candidate', cp.id, cp.version, null, coalesce(cp.org_name, cp.name), cp.created_by, cp.created_at, 60
    from candidate_pool cp
   where cp.status = 'pool' and cp.confirmed_at is null and cp.source <> 'renewal-radar'
)
select i.kind, i.id, i.version, i.owner, i.title, creator.slug as created_by, i.created_at,
       i.created_at + make_interval(days => i.days) as expires_at,
       i.created_at + make_interval(days => i.days) <= now() as stale
  from item i join actor creator on creator.id = i.created_by;
comment on view v_deck_item is 'WR577: open, never-partner-confirmed automation-made next actions, loops and candidates with their expiry date. Stale only fills the Clear the deck list; it hides nothing elsewhere.';
grant select on v_deck_item to carr_reader, carr_writer;
