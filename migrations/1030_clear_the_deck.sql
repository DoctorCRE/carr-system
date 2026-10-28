-- rollback: forward fix only; restore an expired row with clear-the-deck-apply keep, never by deleting history.
-- lock-review: brief ACCESS EXCLUSIVE on next_action, loop_item and candidate_pool to widen one status check each (validated scan of about 13k rows); no rewrite.
-- WR577.S1.J1: confirm-or-expire for automation-created partner obligations.
-- A next action or loop (30 days) or candidate (60 days) whose creating actor is
-- not a human partner goes stale unless a partner touches it. Stale is a read
-- rule (v_deck_item); `expired` is the stored, reversible outcome a partner or
-- the one-time cleanup writes through clear-the-deck-apply. Renewal-radar
-- candidates are left out: their daily sealed snapshot owns that membership.

alter table next_action add column confirmed_at timestamptz;
alter table loop_item add column confirmed_at timestamptz;
alter table candidate_pool add column confirmed_at timestamptz;
comment on column next_action.confirmed_at is 'Last Clear the deck keep by a partner; restarts the automation expiry clock.';
comment on column loop_item.confirmed_at is 'Last Clear the deck keep by a partner; restarts the automation expiry clock.';
comment on column candidate_pool.confirmed_at is 'Last Clear the deck keep by a partner; restarts the automation expiry clock.';

alter table next_action drop constraint next_action_status_check,
  add constraint next_action_status_check check (status = any (array['open', 'done', 'dropped', 'expired']));
alter table loop_item drop constraint loop_item_status_check,
  add constraint loop_item_status_check check (status = any (array['open', 'done', 'dropped', 'expired']));
alter table candidate_pool drop constraint candidate_pool_status_check,
  add constraint candidate_pool_status_check check (status = any (array['pool', 'promoted', 'suppressed_dup', 'declined', 'expired']));

create view v_deck_item as
with item as (
  select 'next_action'::text as kind, na.id, na.version, o.slug as owner, na.description as title,
         na.created_by, na.created_at, na.confirmed_at, na.updated_by, na.updated_at, 30 as days
    from next_action na join actor o on o.id = na.owner_id
   where na.status = 'open'
  union all
  select 'loop', li.id, li.version, li.owner,
         coalesce(nullif(li.title, ''), split_part(li.body, E'\n', 1)),
         li.created_by, li.created_at, li.confirmed_at, li.updated_by, li.updated_at, 30
    from loop_item li
   where li.status = 'open' and li.kind in ('open_loop', 'team_loop', 'action_required')
  union all
  select 'candidate', cp.id, cp.version, null, coalesce(cp.org_name, cp.name),
         cp.created_by, cp.created_at, cp.confirmed_at, cp.updated_by, cp.updated_at, 60
    from candidate_pool cp
   where cp.status = 'pool' and cp.source <> 'renewal-radar'
), clocked as (
  select i.kind, i.id, i.version, i.owner, i.title, creator.slug as created_by, i.created_at,
         greatest(i.created_at, i.confirmed_at,
           case when toucher.kind = 'human' then i.updated_at end) as clock_at, i.days
    from item i
    join actor creator on creator.id = i.created_by and creator.kind <> 'human'
    left join actor toucher on toucher.id = i.updated_by
)
select kind, id, version, owner, title, created_by, created_at, clock_at,
       clock_at + make_interval(days => days) as expires_at,
       clock_at + make_interval(days => days) <= now() as stale
  from clocked;
comment on view v_deck_item is 'WR577: open automation-created next actions, loops and candidates with their confirm-or-expire clock. Partner-created rows never appear. stale rows are hidden from triage and Home until a partner keeps or drops them.';
grant select on v_deck_item to carr_reader, carr_writer;

create or replace view v_today_triage as
 select 'next_action'::text as item_kind,
    na.id,
    na.subject_type,
    na.subject_id,
    owner.slug as owner,
    na.description as what,
    na.due_on,
    coalesce(r.display_name, r.org_name) as subject_name,
    r.ref as subject_ref,
    carr_business_days(na.due_on, carr_business_date()) as business_days_overdue
   from next_action na
     join actor owner on owner.id = na.owner_id
     left join v_ref_index r on r.subject_id = na.subject_id and r.subject_type = na.subject_type
  where na.status = 'open' and na.due_on is not null and na.due_on <= carr_business_date()
    and (na.hold_until is null or na.hold_until <= carr_business_date())
    and not exists (select 1 from v_deck_item d where d.kind = 'next_action' and d.id = na.id and d.stale)
union all
 select 'post_call_action'::text,
    pca.id,
    'deal'::text,
    pca.deal_id,
    owner.slug,
    pca.description,
    pca.due_on,
    coalesce(r.display_name, r.org_name),
    r.ref,
    carr_business_days(pca.due_on, carr_business_date())
   from capture_post_call_action pca
     join actor owner on owner.id = pca.owner_id
     left join v_ref_index r on r.subject_id = pca.deal_id and r.subject_type = 'deal'
  where pca.status = 'open' and pca.due_on is not null and pca.due_on <= carr_business_date()
union all
 select 'critical_date'::text,
    cd.id,
    'deal'::text,
    cd.deal_id,
    null::text,
    cd.kind || coalesce(': ' || cd.note, ''),
    cd.due_on,
    coalesce(r.display_name, r.org_name),
    r.ref,
    carr_business_days(cd.due_on, carr_business_date())
   from critical_date cd
     left join v_ref_index r on r.subject_id = cd.deal_id and r.subject_type = 'deal'
  where cd.status = 'open' and cd.due_on <= carr_business_date() + 14
union all
 select 'ingest'::text,
    i.id,
    'inbox'::text,
    i.id,
    null::text,
    coalesce(nullif(trim(both from i.payload ->> 'summary'), ''), nullif(trim(both from i.payload ->> 'title'), ''),
      nullif(trim(both from i.payload ->> 'subject'), ''), i.source || ' item awaiting triage'),
    carr_business_date(i.received_at),
    nullif(trim(both from i.payload ->> 'organizer'), ''),
    null::text,
    carr_business_days(carr_business_date(i.received_at), carr_business_date())
   from ingest_inbox i
  where i.status = 'new';
