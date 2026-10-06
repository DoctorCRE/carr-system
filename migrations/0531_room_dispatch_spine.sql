-- WR-000119: the DISPATCH SPINE.
--
-- V5-UX-C13 clause 1 asks that sent/received/acknowledged/acted not be
-- conflated. 0529 could prove only two of the four and said so in as many
-- words: public.partner_room_turn is the append-only AI-to-AI wire and carries
-- no session id, no work-request id and no acknowledgement column, so from that
-- substrate `sent` and `received` are the same fact and `acknowledged` does not
-- exist at all. This file adds the substrate that was missing, and NOTHING
-- else: two append-only side relations, their indexes, their grants, the
-- rewrite of ops.session_dispatch_history at the SAME name and arity, and the
-- two security-definer write doors the three first-hand writers call.
--
-- public.partner_room_turn IS NOT TOUCHED. No alter table, no column, no
-- trigger, no row of it written here. Columns on the wire were the rejected
-- design (research spec v1 §2.3, design 1): an acknowledgement is not known
-- when the turn is appended, so closing it would need an UPDATE of a row in an
-- append-only relation that readers page over by a monotonic id -- a reader
-- already past that turn could never see the acknowledgement -- and every
-- existing row would carry a column that is null forever.
--
-- APPEND-ONLY BY GRANT, NOT BY CONVENTION. Both relations are granted insert
-- and select to carr_writer and carr_authority and update and delete to
-- NOBODY, so an acknowledgement is a NEW ROW by construction and not by a
-- handler's good manners. room_dispatch_ack's unique (dispatch_ref, stage) is
-- what refuses a second `received` for one dispatch: the database refuses it,
-- not a JavaScript branch that a direct SQL caller would walk around.
--
-- THE TWO WRITE DOORS ARE VOLATILE, unlike 0529's stable pair. They insert, so
-- mcp-server/src/mcp.js must open a WRITABLE transaction for them, which is
-- what the verbs' `write: true` declaration buys. NO ACTOR PARAMETER in either
-- signature: the acting actor is derived inside each body from the
-- server-installed transaction context by ops.portfolio_writer_actor_id(),
-- exactly as the three 0523 write doors do, so a caller cannot name one.
--
-- THE hermes-pilot RESTRICTION ON THE LINK DOOR LIVES HERE, INSIDE THE
-- DEFINER, and not in the handler. The bridge is the only writer of a link
-- row; a check in JavaScript would be bypassed entirely by a direct SQL call.
--
-- NO TRANSACTION CONTROL. This file is the first member of the reviewed
-- ATOMIC_MIGRATION_GROUP 0531/0532 declared in tools/migrate.py.

do $wr119_preflight$
begin
  -- 0529 created the function this file REPLACES. Pinned by filename and
  -- sha256 because what is being rewritten is that file's work; a drifted
  -- base would take this rewrite over something else.
  if not exists (select 1 from public.schema_migrations
    where filename = '0529_session_identity_reads.sql'
      and sha256 = '72dc678b8bd6e01813f3e34e212eec304426165d2d3878f4e8182d7ca0bb5d28') then
    raise exception '0531 requires the exact 0529 session identity read pair';
  end if;
  -- The ONE function all three bodies derive the acting actor with. Pinned by
  -- to_regprocedure rather than by a migration filename, for 0529's stated
  -- reason: what this file depends on is the callable surface.
  if to_regprocedure('ops.portfolio_writer_actor_id()') is null then
    raise exception '0531 requires the server-context actor deriving function';
  end if;
  -- The wire both relations reference. Nothing else is pinned.
  if to_regclass('public.partner_room_turn') is null then
    raise exception '0531 requires the partner room turn wire';
  end if;
end $wr119_preflight$;

-- -------------------------------------------------------------------------
-- 1. public.room_dispatch_link -- ONE row per assignment.
--
-- The EXPLICIT link the substring match at 0529:361-368 was standing in for.
-- dispatch_ref is the server-minted identity of the dispatch and is UNIQUE, so
-- a replayed mint is one row by construction; turn_id is the wire row that
-- carried it, which is what makes `sent` a proved fact instead of a body
-- match. work_request_id is NULLABLE on purpose: a dispatch that names no work
-- request is a real dispatch, and a not-null column here would push the bridge
-- into inventing one.
-- -------------------------------------------------------------------------

create table if not exists public.room_dispatch_link (
  id              bigserial primary key,
  turn_id         bigint not null references public.partner_room_turn(id),
  session_id      text not null,
  work_request_id uuid null,
  dispatch_ref    uuid not null unique,
  at              timestamptz not null default now(),
  written_by      text not null
);

create index if not exists room_dispatch_link_turn_idx
  on public.room_dispatch_link(turn_id);
create index if not exists room_dispatch_link_session_idx
  on public.room_dispatch_link(session_id, at desc);

comment on table public.room_dispatch_link is
  'WR-000119: ONE row per assignment, written first-hand by the room bridge at the moment it appends the dispatch turn. The explicit link between a wire turn and the session it was dispatched to; public.partner_room_turn carries neither, which is why sent was a substring match before this relation existed. Append-only: no update or delete grant exists.';

-- -------------------------------------------------------------------------
-- 2. public.room_dispatch_ack -- an acknowledgement is a NEW ROW.
--
-- unique (dispatch_ref, stage) is the whole point: a second `received` for one
-- dispatch is refused by the DATABASE. stage is checked to exactly the two
-- values this spine records; `sent` is the link row itself and `acted` stays
-- where 0529 left it, in ops.capability_agent_session's own timestamp columns,
-- inferred from nothing.
-- -------------------------------------------------------------------------

create table if not exists public.room_dispatch_ack (
  id           bigserial primary key,
  dispatch_ref uuid not null references public.room_dispatch_link(dispatch_ref),
  stage        text not null check (stage in ('received','acknowledged')),
  at           timestamptz not null default now(),
  by_actor     text not null,
  evidence     text null,
  unique (dispatch_ref, stage)
);

create index if not exists room_dispatch_ack_ref_idx
  on public.room_dispatch_ack(dispatch_ref, stage);

comment on table public.room_dispatch_ack is
  'WR-000119: one row per (dispatch, stage), each written first-hand by the party that observed it -- the desk when the turn landed in a window, the acting session from inside its own turn. Append-only and unique per (dispatch_ref, stage): a second acknowledgement of the same stage is refused by the database and never by a handler.';

-- -------------------------------------------------------------------------
-- 3. Relation grants. INSERT and SELECT to carr_writer and carr_authority;
-- UPDATE and DELETE to NOBODY, which is what makes these two append-only
-- against a direct SQL caller and not merely against the handler. Spelled per
-- relation and per privilege so the grant is readable as a sentence. Neither
-- relation is granted to carr_reader or carr_jobs: nothing in the read path
-- touches them except ops.session_dispatch_history, which is a definer.
-- -------------------------------------------------------------------------

revoke all on table public.room_dispatch_link
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant insert,select on table public.room_dispatch_link
  to carr_writer,carr_authority;

revoke all on table public.room_dispatch_ack
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant insert,select on table public.room_dispatch_ack
  to carr_writer,carr_authority;

-- -------------------------------------------------------------------------
-- 3b. Both new relations bound into the ATOMIC REFERENCE MONITOR.
--
-- ops.scac_reference_monitor_state() counts a relation as unguarded the moment
-- anybody but its owner holds a mutating privilege on it, and the SIEP-18 gate
-- fails closed on a non-zero count. The grants above make these two writable,
-- so the guard is not optional and not a nicety: it is the same binding
-- 0481:44-59 performs for the continuity relations, in the same shape, and a
-- new writable relation that skipped it would leave the monitor incomplete for
-- every later successor. These two guards are the ONLY triggers on either
-- relation: no business trigger, no update path and no delete.
-- -------------------------------------------------------------------------

do $wr119_monitor_guards$
declare relation_name text; relation_oid regclass;
begin
  foreach relation_name in array array['room_dispatch_link','room_dispatch_ack'] loop
    relation_oid:=to_regclass('public.'||relation_name);
    if relation_oid is null then
      raise exception 'Dispatch spine monitor relation is unavailable: %',relation_name;
    end if;
    if exists(select 1 from pg_trigger where tgrelid=relation_oid and not tgisinternal
      and tgfoid='ops.scac_reference_monitor_guard()'::regprocedure) then
      raise exception 'Dispatch spine monitor relation was already bound: %',relation_name;
    end if;
    execute format('create trigger scac_reference_monitor_guard_row before insert or update or delete on public.%I for each row execute function ops.scac_reference_monitor_guard()',relation_name);
    execute format('create trigger scac_reference_monitor_guard_truncate before truncate on public.%I for each statement execute function ops.scac_reference_monitor_guard()',relation_name);
  end loop;
end $wr119_monitor_guards$;

-- -------------------------------------------------------------------------
-- 4. ops.session_dispatch_history -- REPLACED AT THE SAME NAME AND ARITY.
--
-- create or replace, never drop and recreate: 0529's grant is attached to this
-- exact (text,text,integer) signature and dropping the function would drop the
-- grant with it. Everything 0529 wrote about the cursor, the actor filter and
-- supersession stands unchanged; what changes is that four stages now have
-- evidence instead of two.
--
-- THE SENT BRANCH IS NOW A JOIN, WITH THE OLD PATH LABELLED. A turn with a
-- room_dispatch_link row is link_source 'proved'; the 0529 substring match
-- survives ONLY for turns that have no link row at all and is labelled
-- 'body_match', so a reader can always tell an inferred dispatch from a proved
-- one rather than having to trust the substrate.
--
-- stage_unavailable_reason IS NOW PER DISPATCH, not one constant over the
-- whole answer. null once the stage is proved; 'not_acknowledged' where a link
-- exists and no ack row of that stage does; 'no_dispatch_spine' ONLY where no
-- link row exists at all, which is pre-spine history and nothing else. A link
-- with no ack borrowing the pre-spine excuse would report a silent desk as an
-- absent substrate, and rendering it as `failed` would replace one conflation
-- with another -- so it is neither: received stays null and the reason says
-- which silence it is.
-- -------------------------------------------------------------------------

create or replace function ops.session_dispatch_history(
  p_session_id text, p_cursor text, p_limit integer)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_slug text; v_limit integer; v_key text;
        v_cursor jsonb; v_cursor_at timestamptz; v_cursor_id text;
        v_parent text; v_result jsonb;
begin
  v_actor := ops.portfolio_writer_actor_id();
  select a.slug into v_slug from public.actor a where a.id = v_actor;
  v_limit := least(greatest(coalesce(p_limit, 25), 1), 100);
  v_key := nullif(btrim(coalesce(p_session_id, '')), '');
  if v_key is null then
    return jsonb_build_object('ok', false, 'reason_id', 'session_id_required');
  end if;

  if p_cursor is not null then
    begin
      v_cursor := convert_from(decode(p_cursor, 'base64'), 'utf8')::jsonb;
      v_cursor_at := (v_cursor->>'a')::timestamptz;
      v_cursor_id := v_cursor->>'e';
    exception when others then
      -- The token is the server's own; any failure to read one back is ONE
      -- refusal, never a partial page from a half-understood key.
      return jsonb_build_object('ok', false, 'reason_id', 'dispatch_cursor_invalid');
    end;
    if v_cursor_at is null or v_cursor_id is null then
      return jsonb_build_object('ok', false, 'reason_id', 'dispatch_cursor_invalid');
    end if;
  end if;

  select l.parent_session_id into v_parent
    from public.claude_continuity_leaf l where l.session_id = v_key limit 1;

  with sent_turns as (
    -- ONE set of sent turns from TWO sources, labelled. The left join is the
    -- proved path; the substring match is admitted only where no link row
    -- exists for the turn at all, which is what keeps a proved dispatch and an
    -- inferred one from ever being the same row.
    select t.id                                             as turn_id,
           t.at                                             as at,
           t.msg_id                                         as msg_id,
           t.room_id                                        as room_id,
           t.body                                           as body,
           t.origin_actor                                   as origin_actor,
           t.seat                                           as seat,
           t.sponsor                                        as sponsor,
           l.id                                             as link_id,
           l.dispatch_ref                                   as dispatch_ref,
           l.work_request_id                                as work_request_id,
           case when l.id is null then 'body_match'
                else 'proved' end                           as link_source
      from public.partner_room_turn t
      left join public.room_dispatch_link l
        on l.turn_id = t.id and l.session_id = v_key
     where t.kind = 'turn'
       and (l.id is not null
            or (position(v_key in t.body) > 0
                and not exists (select 1 from public.room_dispatch_link x
                                 where x.turn_id = t.id)))
  ),
  events as (
    -- SENT. Proved by a link row where one exists, inferred from the body
    -- where none does, and the row says which.
    select 'turn:' || s.turn_id                              as event_id,
           s.at                                              as at,
           'sent'                                            as stage,
           'public.partner_room_turn id ' || s.turn_id
             || ' msg_id ' || s.msg_id
             || ' in room ' || s.room_id
             || coalesce(' linked by public.room_dispatch_link id '
                  || s.link_id || ' dispatch_ref ' || s.dispatch_ref, '')
                                                             as stage_evidence,
           left(s.body, 500)                                 as rationale,
           s.origin_actor                                    as from_seat,
           s.seat                                            as to_seat,
           s.sponsor                                         as sponsor,
           s.room_id                                         as room_id,
           lead('turn:' || s.turn_id) over (
             partition by s.room_id order by s.at asc, s.turn_id asc)
                                                             as superseded_by,
           s.work_request_id::text                           as work_request_ref,
           null::text                                        as attempt_ref,
           (s.origin_actor = v_slug or s.seat = v_slug)      as may_see,
           s.link_source                                     as link_source,
           s.dispatch_ref::text                              as dispatch_ref,
           -- PER DISPATCH, computed on this row and nowhere else.
           case when s.link_id is null then 'no_dispatch_spine'
                when not exists (select 1 from public.room_dispatch_ack a
                                  where a.dispatch_ref = s.dispatch_ref
                                    and a.stage = 'received')
                  then 'not_acknowledged'
                else null end                                as stage_unavailable_reason
      from sent_turns s
    union all
    -- RECEIVED and ACKNOWLEDGED. One row per room_dispatch_ack row, each
    -- carrying the ack row that proves it. Nothing here is inferred from an
    -- adjacent turn or an adjacent receipt: if the desk never wrote a row,
    -- this branch returns nothing and the sent row's own reason says so.
    select 'ack:' || a.id,
           a.at,
           a.stage,
           'public.room_dispatch_ack id ' || a.id
             || ' for dispatch_ref ' || a.dispatch_ref,
           a.evidence,
           a.by_actor,
           t.seat,
           t.sponsor,
           t.room_id,
           null::text,
           l.work_request_id::text,
           null::text,
           (t.origin_actor = v_slug or t.seat = v_slug),
           'proved',
           a.dispatch_ref::text,
           null::text
      from public.room_dispatch_ack a
      join public.room_dispatch_link l on l.dispatch_ref = a.dispatch_ref
      join public.partner_room_turn t on t.id = l.turn_id
     where l.session_id = v_key
    union all
    -- ACTED. Unchanged from 0529: the capability session's OWN recorded
    -- transitions, each carrying the timestamp column that proves it.
    select 'capability:' || s.id || ':' || e.column_name,
           e.at,
           'acted',
           'ops.capability_agent_session ' || s.id || ' column ' || e.column_name
             || ', state ' || s.state,
           'capability session reached ' || e.reached,
           null::text,
           null::text,
           null::text,
           null::text,
           null::text,
           s.work_request_id::text,
           s.id::text,
           (s.executor_actor_id = v_actor or s.created_by_actor_id = v_actor),
           'proved',
           null::text,
           null::text
      from ops.capability_agent_session s
      cross join lateral (values
          ('started_at', s.started_at, 'in_progress'),
          ('prepared_at', s.prepared_at, 'verification'),
          ('completed_at', s.completed_at, 'completed'),
          ('cancelled_at', s.cancelled_at, 'cancelled')
        ) as e(column_name, at, reached)
     where e.at is not null
       and (s.id::text = v_key or position(v_key in s.worktree_ref) > 0)
  ),
  paged as (
    select e.*,
           case when e.may_see then row_number() over (
             partition by e.may_see order by e.at desc, e.event_id desc) end as rn
      from events e
     where p_cursor is null
        or row(e.at, e.event_id) < row(v_cursor_at, v_cursor_id)
  )
  select jsonb_build_object(
    'ok', true,
    'session_id', v_key,
    'parent_session_id', v_parent,
    'total_seen', count(*),
    'total_returned', count(*) filter (where may_see),
    'permission_filtered', count(*) > count(*) filter (where may_see),
    'more', count(*) filter (where may_see) > v_limit,
    'next_cursor', (
      select encode(convert_to(jsonb_build_object('a', p.at, 'e', p.event_id)::text, 'utf8'), 'base64')
        from paged p where p.may_see and p.rn = v_limit
         and (select count(*) filter (where q.may_see) from paged q) > v_limit),
    -- NOT CONSTANTS ANY MORE. Each of these three is read off the newest
    -- visible row that carries it, and the reason is the newest dispatch's own
    -- per-row value -- so an answer with one proved dispatch and one pre-spine
    -- dispatch reports each honestly in `events` and never averages them here.
    'received', (select p.at from paged p
                  where p.may_see and p.stage = 'received'
                  order by p.at desc, p.event_id desc limit 1),
    'acknowledged', (select p.at from paged p
                      where p.may_see and p.stage = 'acknowledged'
                      order by p.at desc, p.event_id desc limit 1),
    'stage_unavailable_reason', (select p.stage_unavailable_reason from paged p
                                  where p.may_see and p.stage = 'sent'
                                  order by p.at desc, p.event_id desc limit 1),
    'events', coalesce(jsonb_agg(jsonb_build_object(
        'event_id', event_id,
        'at', at,
        'stage', stage,
        'stage_evidence', stage_evidence,
        'rationale', rationale,
        'from_seat', from_seat,
        'to_seat', to_seat,
        'sponsor', sponsor,
        'room_id', room_id,
        'session_id', v_key,
        'parent_session_id', v_parent,
        'attempt_ref', attempt_ref,
        'superseded_by', superseded_by,
        'work_request_ref', work_request_ref,
        'link_source', link_source,
        'dispatch_ref', dispatch_ref,
        'stage_unavailable_reason', stage_unavailable_reason)
      order by at desc, event_id desc)
      filter (where may_see and rn <= v_limit), '[]'::jsonb))
    into v_result
    from paged;

  return v_result;
end $$;

comment on function ops.session_dispatch_history(text,text,integer) is
  'WR-000119: the dispatch history for one session, newest first. The actor is derived from the server-installed transaction context; p_session_id is a lookup key against rows already filtered by it and never an identity claim. All four stages now carry evidence: sent from public.room_dispatch_link (link_source proved) or, for pre-spine turns only, from the body match (link_source body_match); received and acknowledged from public.room_dispatch_ack rows; acted from ops.capability_agent_session timestamp columns. stage_unavailable_reason is PER DISPATCH -- null once proved, not_acknowledged where a link has no ack row of that stage, no_dispatch_spine only where no link row exists.';

revoke all on function ops.session_dispatch_history(text,text,integer)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.session_dispatch_history(text,text,integer)
  to carr_writer,carr_authority;

-- -------------------------------------------------------------------------
-- 5. ops.record_dispatch_link -- the ONLY writer of a link row.
--
-- VOLATILE, unlike 0529's pair: it inserts. NO ACTOR PARAMETER -- the caller
-- is derived here, and the hermes-pilot restriction is raised HERE, inside the
-- definer, before any write. The room bridge is the one party that observes an
-- assignment first-hand, at the moment it appends the turn; a check in the
-- JavaScript handler would be walked around by a direct SQL call, so the
-- restriction is a database fact.
--
-- dispatch_ref IS THE IDEMPOTENCY KEY and the row's own identity, so a
-- replayed mint is ONE row by construction (0523:76-94's idiom). The same key
-- offered with different content is a REUSE and is refused with a reason,
-- never silently accepted as a duplicate. AND THE ASSIGNMENT ITSELF is the
-- second idempotency key: (turn_id, session_id) already linked returns that
-- link, because one assignment is one link row however many times the bridge
-- retried the cycle that made it.
-- -------------------------------------------------------------------------

create or replace function ops.record_dispatch_link(
  p_turn_msg_id uuid, p_session_id text, p_work_request_id uuid, p_dispatch_ref uuid)
returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_slug text; v_id bigint; v_key text; v_turn_id bigint;
        v_prior public.room_dispatch_link%rowtype;
begin
  v_actor := ops.portfolio_writer_actor_id();
  select a.slug into v_slug from public.actor a where a.id = v_actor;
  -- THE RESTRICTION, INSIDE THE DEFINER. Any other derived identity -- even
  -- one that holds execute on this function -- is refused here.
  if v_slug is distinct from 'hermes-pilot' then
    return jsonb_build_object('ok', false,
      'reason_id', 'dispatch_link_hermes_pilot_only');
  end if;
  if p_dispatch_ref is null then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_ref_required');
  end if;
  v_key := nullif(btrim(coalesce(p_session_id, '')), '');
  if v_key is null then
    return jsonb_build_object('ok', false, 'reason_id', 'session_id_required');
  end if;
  -- THE CALLER NAMES THE TURN BY ITS msg_id, which is what the bridge holds in
  -- its hand at the moment it appends the turn. The bigserial id is resolved
  -- here, so no caller has to have read the row back to write the link.
  select t.id into v_turn_id
    from public.partner_room_turn t where t.msg_id = p_turn_msg_id;
  if v_turn_id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_turn_not_found');
  end if;

  -- IDEMPOTENT ON THE ASSIGNMENT ITSELF, not only on the minted key. One
  -- assignment is one link row: a bridge that retried a cycle and minted a
  -- fresh dispatch_ref for a turn it had already linked would otherwise leave
  -- two links for one assignment, and `sent` would be proved twice.
  select * into v_prior from public.room_dispatch_link
   where turn_id = v_turn_id and session_id = v_key;
  if v_prior.id is not null then
    return jsonb_build_object('ok', true, 'deduplicated', true, 'id', v_prior.id,
      'dispatch_ref', v_prior.dispatch_ref, 'turn_id', v_prior.turn_id,
      'turn_msg_id', p_turn_msg_id,
      'session_id', v_prior.session_id, 'written_by', v_prior.written_by);
  end if;

  insert into public.room_dispatch_link(
    turn_id, session_id, work_request_id, dispatch_ref, written_by)
  values (v_turn_id, v_key, p_work_request_id, p_dispatch_ref, v_slug)
  on conflict (dispatch_ref) do nothing
  returning id into v_id;

  if v_id is null then
    select * into v_prior from public.room_dispatch_link
     where dispatch_ref = p_dispatch_ref;
    if v_prior.id is null then
      return jsonb_build_object('ok', false, 'reason_id', 'dedup_row_vanished');
    end if;
    if v_prior.turn_id is distinct from v_turn_id
       or v_prior.session_id is distinct from v_key
       or v_prior.work_request_id is distinct from p_work_request_id then
      return jsonb_build_object('ok', false,
        'reason_id', 'dispatch_ref_reuse', 'dispatch_ref', p_dispatch_ref);
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'id', v_prior.id,
      'dispatch_ref', v_prior.dispatch_ref, 'turn_id', v_prior.turn_id,
      'turn_msg_id', p_turn_msg_id,
      'session_id', v_prior.session_id, 'written_by', v_prior.written_by);
  end if;

  return jsonb_build_object('ok', true, 'deduplicated', false, 'id', v_id,
    'dispatch_ref', p_dispatch_ref, 'turn_id', v_turn_id,
    'turn_msg_id', p_turn_msg_id,
    'session_id', v_key, 'written_by', v_slug);
end $$;

comment on function ops.record_dispatch_link(uuid,text,uuid,uuid) is
  'WR-000119: the ONLY writer of public.room_dispatch_link. The caller is derived from the server-installed transaction context and refused inside this body unless it is hermes-pilot, so the room bridge being the sole minter of a dispatch link is a database fact and not a handler courtesy. dispatch_ref is the idempotency key and the row identity, so a replay is one row and a reused key with different content is refused.';

-- -------------------------------------------------------------------------
-- 6. ops.acknowledge_dispatch -- an acknowledgement is an APPEND.
--
-- VOLATILE, and it takes NO ACTOR: by_actor is the DERIVED caller, so an
-- acknowledgement is always first-hand. A caller who could name the actor
-- could record an acknowledgement on another party's behalf, which is exactly
-- the inference dressed as evidence that V5-UX-C13 clause 1 forbids.
--
-- TWO WRITERS, ONE DOOR, and the STAGE is what separates them: the desk writes
-- 'received' when the turn lands in a window, the acting session writes
-- 'acknowledged' from inside its own turn. Neither may write the other's
-- stage, and neither is checked for that here, because the DATABASE cannot
-- tell a desk from a session -- what the database enforces is that the stage
-- is one of exactly two and that there is at most one row per (dispatch,
-- stage). The first-hand discipline is proved at the call sites instead.
--
-- The unique (dispatch_ref, stage) constraint does the deduplication: a second
-- ack of the same stage is not an error and not a second row, it is the same
-- fact restated, so it returns deduplicated true.
-- -------------------------------------------------------------------------

create or replace function ops.acknowledge_dispatch(
  p_dispatch_ref uuid, p_stage text, p_evidence text)
returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_slug text; v_id bigint; v_stage text;
        v_prior public.room_dispatch_ack%rowtype;
begin
  v_actor := ops.portfolio_writer_actor_id();
  select a.slug into v_slug from public.actor a where a.id = v_actor;
  if v_slug is null then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_actor_underived');
  end if;
  if p_dispatch_ref is null then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_ref_required');
  end if;
  v_stage := nullif(btrim(coalesce(p_stage, '')), '');
  -- The relation's own check would refuse this anyway; refusing here gives the
  -- caller a reason_id instead of a constraint error.
  if v_stage is null or v_stage not in ('received', 'acknowledged') then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_stage_invalid');
  end if;
  if not exists (select 1 from public.room_dispatch_link l
                  where l.dispatch_ref = p_dispatch_ref) then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_link_not_found');
  end if;

  insert into public.room_dispatch_ack(dispatch_ref, stage, by_actor, evidence)
  values (p_dispatch_ref, v_stage, v_slug, p_evidence)
  on conflict (dispatch_ref, stage) do nothing
  returning id into v_id;

  if v_id is null then
    select * into v_prior from public.room_dispatch_ack
     where dispatch_ref = p_dispatch_ref and stage = v_stage;
    if v_prior.id is null then
      return jsonb_build_object('ok', false, 'reason_id', 'dedup_row_vanished');
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'id', v_prior.id,
      'dispatch_ref', v_prior.dispatch_ref, 'stage', v_prior.stage,
      'by_actor', v_prior.by_actor);
  end if;

  return jsonb_build_object('ok', true, 'deduplicated', false, 'id', v_id,
    'dispatch_ref', p_dispatch_ref, 'stage', v_stage, 'by_actor', v_slug);
end $$;

comment on function ops.acknowledge_dispatch(uuid,text,text) is
  'WR-000119: appends one public.room_dispatch_ack row for a dispatch and a stage. by_actor is the DERIVED caller and there is no actor argument anywhere, so an acknowledgement is first-hand by construction; the unique (dispatch_ref, stage) constraint makes a restated acknowledgement one row rather than two.';

-- -------------------------------------------------------------------------
-- 7. Write-door grants. carr_writer AND carr_authority, exactly as the 0523
-- write doors and 0529's read pair are granted. NEITHER is authority-only:
-- the bridge and the desks call them as the signed-in writer. The argument
-- types are spelled in full in BOTH lines of BOTH pairs -- a stale arity
-- revokes nothing and would leave PUBLIC execute on a security definer.
-- -------------------------------------------------------------------------

revoke all on function ops.record_dispatch_link(uuid,text,uuid,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.record_dispatch_link(uuid,text,uuid,uuid)
  to carr_writer,carr_authority;

revoke all on function ops.acknowledge_dispatch(uuid,text,text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.acknowledge_dispatch(uuid,text,text)
  to carr_writer,carr_authority;
