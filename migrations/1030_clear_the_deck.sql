-- rollback: forward fix only; restore an expired row with clear-the-deck-apply keep, never by deleting history.
-- lock-review: brief ACCESS EXCLUSIVE on next_action, loop_item and candidate_pool to widen one status check each and backfill confirmation (about 13k rows, touch triggers paused so versions stay put); no rewrite.
-- WR577.S1.J1: confirm-or-expire for automation-made partner obligations.
-- A partner is a human partner or a partner door (joe-local, dell-local). Any
-- partner create, edit, history event or Clear the deck keep confirms a row for
-- good (confirmed_by/confirmed_at). An unconfirmed automation-made next action
-- or loop (30 days) or candidate (60 days) becomes stale: it is listed on the
-- deck and nothing else. Only the stored, reversible `expired` status, written
-- by a partner drop or the one-time cleanup, takes a row off triage and Home.
-- Renewal-radar candidates are left out: their sealed daily snapshot owns that membership.

create function carr_is_partner_actor(p_actor uuid) returns boolean
  language sql stable set search_path = pg_catalog, public, pg_temp
  as $$ select exists (select 1 from public.actor a where a.id = p_actor
          and a.slug in ('joe', 'dell', 'joe-local', 'dell-local')) $$;
grant execute on function carr_is_partner_actor(uuid) to carr_reader, carr_writer, carr_jobs, carr_authority;
comment on function carr_is_partner_actor(uuid) is 'WR577: the one partner rule for Clear the deck. The partners (joe, dell) and their call-mode doors (joe-local, dell-local) are partners; system, merge-queue and intake actors are automation. Reads only actor id and slug, which every runtime role may read.';

alter table next_action add column confirmed_at timestamptz, add column confirmed_by uuid references actor(id);
alter table loop_item add column confirmed_at timestamptz, add column confirmed_by uuid references actor(id);
alter table candidate_pool add column confirmed_at timestamptz, add column confirmed_by uuid references actor(id);
comment on column next_action.confirmed_at is 'First partner confirmation (create, edit, history event or deck keep). A confirmed row never goes stale.';
comment on column loop_item.confirmed_at is 'First partner confirmation (create, edit, history event or deck keep). A confirmed row never goes stale.';
comment on column candidate_pool.confirmed_at is 'First partner confirmation (create, edit, history event or deck keep). A confirmed row never goes stale.';

alter table next_action drop constraint next_action_status_check,
  add constraint next_action_status_check check (status = any (array['open', 'done', 'dropped', 'expired']));
alter table loop_item drop constraint loop_item_status_check,
  add constraint loop_item_status_check check (status = any (array['open', 'done', 'dropped', 'expired']));
alter table candidate_pool drop constraint candidate_pool_status_check,
  add constraint candidate_pool_status_check check (status = any (array['pool', 'promoted', 'suppressed_dup', 'declined', 'expired']));

-- Backfill from history: the creator, the last updater, then the earliest partner event on the row.
alter table next_action disable trigger next_action_touch;
alter table loop_item disable trigger loop_item_touch;
alter table candidate_pool disable trigger prospect_pool_touch;
with first_event as (
  select distinct on (e.subject_id) e.subject_id, e.actor_id, e.occurred_at
    from event e where carr_is_partner_actor(e.actor_id) order by e.subject_id, e.occurred_at
)
update next_action t set
  confirmed_by = case when carr_is_partner_actor(t.created_by) then t.created_by
                      when carr_is_partner_actor(t.updated_by) then t.updated_by else fe.actor_id end,
  confirmed_at = case when carr_is_partner_actor(t.created_by) then t.created_at
                      when carr_is_partner_actor(t.updated_by) then t.updated_at else fe.occurred_at end
  from (select n.id, fe.actor_id, fe.occurred_at from next_action n left join first_event fe on fe.subject_id = n.id) fe
 where fe.id = t.id and (carr_is_partner_actor(t.created_by) or carr_is_partner_actor(t.updated_by) or fe.actor_id is not null);
with first_event as (
  select distinct on (e.subject_id) e.subject_id, e.actor_id, e.occurred_at
    from event e where carr_is_partner_actor(e.actor_id) order by e.subject_id, e.occurred_at
)
update loop_item t set
  confirmed_by = case when carr_is_partner_actor(t.created_by) then t.created_by
                      when carr_is_partner_actor(t.updated_by) then t.updated_by else fe.actor_id end,
  confirmed_at = case when carr_is_partner_actor(t.created_by) then t.created_at
                      when carr_is_partner_actor(t.updated_by) then t.updated_at else fe.occurred_at end
  from (select l.id, fe.actor_id, fe.occurred_at from loop_item l left join first_event fe on fe.subject_id = l.id) fe
 where fe.id = t.id and (carr_is_partner_actor(t.created_by) or carr_is_partner_actor(t.updated_by) or fe.actor_id is not null);
with first_event as (
  select distinct on (e.subject_id) e.subject_id, e.actor_id, e.occurred_at
    from event e where carr_is_partner_actor(e.actor_id) order by e.subject_id, e.occurred_at
)
update candidate_pool t set
  confirmed_by = case when carr_is_partner_actor(t.created_by) then t.created_by
                      when carr_is_partner_actor(t.updated_by) then t.updated_by else fe.actor_id end,
  confirmed_at = case when carr_is_partner_actor(t.created_by) then t.created_at
                      when carr_is_partner_actor(t.updated_by) then t.updated_at else fe.occurred_at end
  from (select p.id, fe.actor_id, fe.occurred_at from candidate_pool p left join first_event fe on fe.subject_id = p.id) fe
 where fe.id = t.id and t.source <> 'renewal-radar'
   and (carr_is_partner_actor(t.created_by) or carr_is_partner_actor(t.updated_by) or fe.actor_id is not null);
alter table next_action enable trigger next_action_touch;
alter table loop_item enable trigger loop_item_touch;
alter table candidate_pool enable trigger prospect_pool_touch;

-- From now on a partner create or edit of a live row confirms it at once. A drop
-- (status expired) is a decision, not a confirmation.
create function carr_confirm_partner_touch() returns trigger
  language plpgsql set search_path = pg_catalog, public, pg_temp
  as $$
declare
  v_actor uuid := case when tg_op = 'INSERT' then new.created_by else new.updated_by end;
begin
  if new.confirmed_at is null and new.status in ('open', 'pool') and public.carr_is_partner_actor(v_actor) then
    new.confirmed_at := now();
    new.confirmed_by := v_actor;
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
  from item i join actor creator on creator.id = i.created_by
 where not carr_is_partner_actor(i.created_by);
comment on view v_deck_item is 'WR577: open, never-partner-confirmed automation-made next actions, loops and candidates with their expiry date. Stale only fills the Clear the deck list; it hides nothing elsewhere.';
grant select on v_deck_item to carr_reader, carr_writer;
