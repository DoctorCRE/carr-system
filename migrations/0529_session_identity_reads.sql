-- WR-000117: the session-identity READ PAIR.
--
-- V5-UX-S02 asks that a session be findable by name or ID with its parent
-- lineage and its dispatch history, filtered to what the acting actor may see.
-- Four disjoint books already hold almost every fact it needs --
-- public.claude_continuity_leaf, public.codex_continuity_checkpoint,
-- ops.capability_agent_session and public.session_work -- and NOTHING joins
-- them, so "look this session up" has no answer however many columns exist.
-- This file adds the TWO security-definer functions that answer it, their
-- revokes and their grants, AND NOTHING ELSE.
--
-- THESE ARE READS. No relation, no column, no index, no trigger and no table
-- grant is created, amended or dropped here, and neither body contains an
-- insert, an update or a delete. NO NEW WRITER OF ANY ROW: the harvest stays
-- the writer of public.session_work, the sessions stay the writers of their own
-- continuity leaves, and the server stays the writer of
-- ops.capability_agent_session. Column ownership does not change. Adding
-- canonical_session_id / parent_initiator / native_host_id columns to
-- session_work would create a second canonical work store, which V5-UX-S02's
-- own excluded_scope forbids in as many words. This file PROJECTS; it does not
-- copy.
--
-- `stable`, not `volatile`: mcp-server/src/mcp.js opens `begin read only` for a
-- tool that declares writerConnection without write, which is the transaction
-- both of these are written for. ops.list_doc_conversations is `stable` at
-- 0525:88 for exactly that reason and both of these match it.
--
-- NO ACTOR PARAMETER, in either signature. The acting actor is derived inside
-- each body from the server-installed transaction context by
-- ops.portfolio_writer_actor_id(), exactly as the three 0523 write doors, the
-- 0520 append and the 0525 list derive it. There is no parameter for it
-- anywhere, so a caller cannot name one.
--
-- NO TRANSACTION CONTROL. This file is the first member of the reviewed
-- ATOMIC_MIGRATION_GROUP 0529/0530 declared in tools/migrate.py.

do $wr117_preflight$
begin
  if not exists (select 1 from public.schema_migrations
    where filename = '0193_session_work.sql'
      and sha256 = 'd2c4c868194cc5500bf9ee99ae749a505dac03bc607ed468b0e22f1c47c4e985') then
    raise exception '0529 requires the exact 0193 session work store';
  end if;
  -- The ONE function both bodies derive the acting actor with. Pinned by
  -- to_regprocedure rather than by a migration filename, because what 0529
  -- depends on is the callable surface and not the file that happened to
  -- install it.
  if to_regprocedure('ops.portfolio_writer_actor_id()') is null then
    raise exception '0529 requires the server-context actor deriving function';
  end if;
end $wr117_preflight$;

-- -------------------------------------------------------------------------
-- ops.session_identity_facts
--
-- ONE CROSS-SURFACE PROJECTION over four books that share no key shape.
--
-- p_query IS A SEARCH STRING AND NEVER AN IDENTITY CLAIM. It is matched, case
-- insensitively, against the canonical id, the derived display name, the
-- native-host id and the place columns of rows the actor filter has ALREADY
-- admitted. It authorises nothing: a caller who types another actor's session
-- id gets the same empty answer as a caller who types nonsense.
--
-- EVERY ROW IS LABELLED WITH THE BOOK THAT OBSERVED IT (observation_source),
-- because V5-UX-C12 clause 2 forbids inferring liveness from a name. The
-- harvest stamps public.session_work.last_seen with the time the HARVEST ran --
-- pipelines/session_crm_harvest.py says so in its own comment, and the harvest
-- is not scheduled at all -- so a harvested row can never claim a work state.
-- It reports `unknown`, and its evidence says why.
--
-- AN UNRECORDED PARENT IS NOT A ROOT. parent_known is computed PER BOOK from
-- whether that book records a parent column at all, never from
-- parent_session_id being non-null. public.codex_continuity_checkpoint has no
-- parent column of any kind, so every Codex row reports parent_known false; a
-- plain non-null test would report every Codex session as a root.
--
-- THE FRIENDLY NAME IS DERIVED. No relation anywhere stores a human-typed
-- alias, so alias_source is the constant 'derived' and no surface can mistake
-- the name for one a human chose.
--
-- PERMISSION FILTERING IS A COUNT COMPARISON, not just a predicate. The
-- function returns total_seen and total_returned and sets permission_filtered
-- when they differ, so an actor who may see nothing gets an explicitly filtered
-- empty list rather than one indistinguishable from an empty system.
--
-- THE FILTER IS PER BOOK, because the four books carry four different owner
-- columns and a union filtered once passes through the branch whose owner
-- column is absent. public.session_work HAS NO OWNER COLUMN AT ALL: it is
-- machine-shared harvest bookkeeping, so its rows carry no per-actor
-- restriction. That is stated here rather than left to be discovered, and it is
-- why those rows are labelled `harvest` and report work_state `unknown`.
--
-- THE IDLE/DISCONNECTED THRESHOLD IS COMPUTED FROM THE ROW'S OWN
-- last_observed_at AGAINST now() INSIDE THE FUNCTION. A literal instant passes
-- for months and fails on one run.
-- -------------------------------------------------------------------------

create or replace function ops.session_identity_facts(
  p_query text, p_limit integer, p_include_closed boolean)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_limit integer; v_include_closed boolean; v_query text;
        v_result jsonb;
begin
  -- The acting actor, resolved from the server-installed transaction context.
  -- A caller cannot name one: there is no parameter for it.
  v_actor := ops.portfolio_writer_actor_id();
  -- Clamped, never refused: the idiom is 0520:181's and 0525:100's.
  v_limit := least(greatest(coalesce(p_limit, 25), 1), 50);
  v_include_closed := coalesce(p_include_closed, false);
  v_query := nullif(btrim(coalesce(p_query, '')), '');

  with books as (
    -- CLAUDE. The only book of the four that records a parent column, so the
    -- only one that can report parent_known true.
    select l.session_id                                     as canonical_session_id,
           'claude'                                         as surface,
           coalesce(nullif(btrim(l.project_affinity), ''), 'claude session')
             || ' / ' || left(l.session_id, 12)             as display_name,
           l.parent_session_id                              as parent_session_id,
           true                                             as parent_known,
           l.native_agent_id                                as native_host_id,
           true                                             as native_host_supported,
           l.updated_at                                     as last_observed_at,
           'continuity_event'                               as observation_source,
           l.project_affinity                               as project_affinity,
           l.latest_cwd                                     as latest_cwd,
           l.latest_model_id                                as latest_model_id,
           (select count(*) from public.claude_continuity_leaf s
             where coalesce(s.parent_session_id, s.session_id)
                 = coalesce(l.parent_session_id, l.session_id))::integer
                                                            as attempt_count,
           (select s.session_id from public.claude_continuity_leaf s
             where coalesce(s.parent_session_id, s.session_id)
                 = coalesce(l.parent_session_id, l.session_id)
             order by s.updated_at desc, s.session_id desc limit 1)
                                                            as latest_attempt_ref,
           (l.owner_actor_id = v_actor or l.surface_principal_actor_id = v_actor)
                                                            as may_see,
           false                                            as closed,
           null::text                                       as state_hint
      from public.claude_continuity_leaf l
    union all
    -- CODEX. No parent column exists in this book, so a null parent here means
    -- UNRECORDED and parent_known is false. It records no lineage column
    -- either, so an attempt group cannot be formed and the count is the row.
    select c.native_task_id,
           'codex',
           coalesce(nullif(btrim(c.project_id), ''), 'codex session')
             || ' / ' || left(c.native_task_id, 12),
           null::text,
           false,
           c.native_task_id,
           true,
           c.updated_at,
           'checkpoint',
           c.project_id,
           c.cwd,
           null::text,
           1,
           c.native_task_id,
           (c.owner_actor_id = v_actor),
           false,
           null::text
      from public.codex_continuity_checkpoint c
    union all
    -- CAPABILITY. The server's own build-session book. It records no parent
    -- session and no native host id, so both are reported unrecorded rather
    -- than absent. Its attempt group IS recorded: every session against one
    -- work request is one attempt of it.
    select s.id::text,
           'capability',
           'capability ' || left(s.id::text, 8) || ' @ ' || left(s.source_commit_sha, 7),
           null::text,
           false,
           null::text,
           false,
           s.updated_at,
           'server_session',
           s.worktree_ref,
           s.worktree_ref,
           null::text,
           (select count(*) from ops.capability_agent_session a
             where a.work_request_id = s.work_request_id)::integer,
           (select a.id::text from ops.capability_agent_session a
             where a.work_request_id = s.work_request_id
             order by a.updated_at desc, a.id desc limit 1),
           (s.executor_actor_id = v_actor or s.created_by_actor_id = v_actor),
           (s.state in ('completed', 'cancelled')),
           s.state
      from ops.capability_agent_session s
    union all
    -- HARVESTED. public.session_work has NO owner column, so may_see is the
    -- constant true and this branch adds nothing to the withheld count. It also
    -- has no parent column and no native host id, and its last_seen is the
    -- HARVEST's clock, which is why work_state below is forced to `unknown`.
    select w.id,
           'harvested',
           w.title,
           null::text,
           false,
           null::text,
           false,
           w.last_seen,
           'harvest',
           w.kind,
           null::text,
           null::text,
           1,
           null::text,
           true,
           (not w.open_loop),
           null::text
      from public.session_work w
  ),
  -- ONE age rule for every book, computed from the row's own last_observed_at
  -- against now() inside the function.
  judged as (
    select b.*,
           now() - b.last_observed_at as observed_age,
           case
             when b.surface = 'harvested' then 'unknown'
             when b.state_hint = 'completed' then 'complete_unacknowledged'
             when b.state_hint = 'cancelled' then 'disconnected'
             when now() - b.last_observed_at <= interval '15 minutes' then 'working'
             when now() - b.last_observed_at <= interval '2 hours' then 'idle'
             else 'disconnected'
           end as work_state
      from books b
  ),
  matched as (
    select j.* from judged j
     where (v_include_closed or not j.closed)
       and (v_query is null
            or j.canonical_session_id ilike '%' || v_query || '%'
            or j.display_name ilike '%' || v_query || '%'
            or coalesce(j.native_host_id, '') ilike '%' || v_query || '%'
            or coalesce(j.project_affinity, '') ilike '%' || v_query || '%'
            or coalesce(j.latest_cwd, '') ilike '%' || v_query || '%')
  ),
  ranked as (
    select m.*,
           case when m.may_see then row_number() over (
             partition by m.may_see
             order by m.last_observed_at desc, m.canonical_session_id desc) end as rn
      from matched m
  )
  select jsonb_build_object(
    'ok', true,
    'total_seen', count(*),
    'total_returned', count(*) filter (where may_see),
    -- THE COUNT COMPARISON. Not a constant, and not "the page came back empty".
    'permission_filtered', count(*) > count(*) filter (where may_see),
    'sessions', coalesce(jsonb_agg(jsonb_build_object(
        'canonical_session_id', canonical_session_id,
        'surface', surface,
        'display_name', display_name,
        'alias_source', 'derived',
        'parent_session_id', parent_session_id,
        'parent_known', parent_known,
        'native_host_id', native_host_id,
        'native_host_supported', native_host_supported,
        'work_state', work_state,
        'work_state_evidence', observation_source || ' row observed at '
          || to_char(last_observed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
          || ', age ' || observed_age::text
          || case when surface = 'harvested'
                  then '; the harvest stamps its own run time and is not scheduled, so liveness is not claimed'
                  when state_hint is not null then '; server session state ' || state_hint
                  else '' end,
        'last_observed_at', last_observed_at,
        'observation_source', observation_source,
        'project_affinity', project_affinity,
        'latest_cwd', latest_cwd,
        'latest_model_id', latest_model_id,
        'attempt_count', attempt_count,
        'latest_attempt_ref', latest_attempt_ref)
      order by last_observed_at desc, canonical_session_id desc)
      filter (where may_see and rn <= v_limit), '[]'::jsonb))
    into v_result
    from ranked;

  return v_result;
end $$;

comment on function ops.session_identity_facts(text,integer,boolean) is
  'WR-000117: the only door that projects the four disjoint session books into one identity row shape. The actor is derived from the server-installed transaction context -- there is no parameter for it -- p_query is a search string and never an identity claim, every row names the book that observed it, an unrecorded parent is distinguishable from a root, and permission filtering is reported as total_seen against total_returned.';

-- -------------------------------------------------------------------------
-- ops.session_dispatch_history
--
-- p_session_id IS A LOOKUP KEY AND NEVER AN IDENTITY CLAIM. It selects among
-- rows this function has ALREADY filtered by the derived acting actor -- the
-- one-row case 0520:164-168 defends -- so naming another actor's session
-- returns that actor's nothing.
--
-- TWO STAGES, NOT FOUR, AND THE GAP IS NAMED. public.partner_room_turn is the
-- append-only AI-to-AI wire and carries no session id, no work-request id and
-- no acknowledgement column, so from this substrate `sent` and `received` are
-- the same fact and `acknowledged` does not exist at all. V5-UX-C13 clause 1
-- asks that the four not be CONFLATED; four inferred values would be exactly
-- that defect. This function therefore proves the two it can --
--   sent  : a room turn whose body names this session, evidenced by that row
--   acted : a state transition of the capability session itself, evidenced by
--           the timestamp column that recorded it
-- -- and returns `received` and `acknowledged` as null carrying
-- stage_unavailable_reason 'no_dispatch_spine'. An `acknowledged` inferred from
-- an adjacent room turn or an adjacent ops.attempt_receipt row is a FAILURE,
-- not a bonus: it papers over the substrate gap. V5-UX-C13 clause 1 is
-- therefore NOT closed by this Work Request; a dispatch spine relation and its
-- writer are a separate Work Request.
--
-- THE CURSOR IS AN OPAQUE SERVER-MINTED TOKEN CARRYING THE WHOLE SORT KEY,
-- for 0525:70-83's stated reason: rows arrive under an append-only clock and an
-- offset page over a moving set skips and repeats by construction.
--
-- SUPERSESSION IS RECORDED, NOT INFERRED AWAY. A later `sent` turn in the same
-- room naming the same session supersedes an earlier one, and the earlier row
-- is returned CARRYING superseded_by rather than silently dropped, so
-- V5-UX-C13 clause 3's "no stale instructions executed" is something a reader
-- can see rather than something a filter hid.
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

  -- The parent of the named session, read from the ONE book that records a
  -- parent column. Absent everywhere else, which is why the identity read
  -- reports parent_known per book.
  select l.parent_session_id into v_parent
    from public.claude_continuity_leaf l where l.session_id = v_key limit 1;

  with events as (
    -- SENT. A room turn whose body NAMES this session. The link is the named
    -- id in the message, carried in stage_evidence -- not a guess about which
    -- turn belongs to which session.
    select 'turn:' || t.id                                   as event_id,
           t.at                                              as at,
           'sent'                                            as stage,
           'public.partner_room_turn id ' || t.id
             || ' msg_id ' || t.msg_id
             || ' in room ' || t.room_id                     as stage_evidence,
           left(t.body, 500)                                 as rationale,
           t.origin_actor                                    as from_seat,
           t.seat                                            as to_seat,
           t.sponsor                                         as sponsor,
           t.room_id                                         as room_id,
           -- The NEXT turn in the room, in real time order: an instruction is
           -- superseded by the one that followed it, and the newest carries
           -- null. Ordered ASCENDING on purpose -- lead() under the DESC
           -- display order would name the instruction this one replaced.
           lead('turn:' || t.id) over (
             partition by t.room_id order by t.at asc, t.id asc)
                                                             as superseded_by,
           null::text                                        as work_request_ref,
           null::text                                        as attempt_ref,
           (t.origin_actor = v_slug or t.seat = v_slug)      as may_see
      from public.partner_room_turn t
     where t.kind = 'turn'
       and position(v_key in t.body) > 0
    union all
    -- ACTED. The capability session's OWN recorded transitions. Each carries
    -- the timestamp column that proves it; nothing is inferred from an
    -- adjacent row in another book.
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
           (s.executor_actor_id = v_actor or s.created_by_actor_id = v_actor)
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
    -- THE NAMED GAP. Null, with the reason, is the honest answer; an inferred
    -- value would be the conflation V5-UX-C13 clause 1 forbids.
    'received', null,
    'acknowledged', null,
    'stage_unavailable_reason', 'no_dispatch_spine',
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
        'work_request_ref', work_request_ref)
      order by at desc, event_id desc)
      filter (where may_see and rn <= v_limit), '[]'::jsonb))
    into v_result
    from paged;

  return v_result;
end $$;

comment on function ops.session_dispatch_history(text,text,integer) is
  'WR-000117: the dispatch history for one session, newest first. The actor is derived from the server-installed transaction context; p_session_id is a lookup key against rows already filtered by it and never an identity claim. stage carries only the two values this substrate proves -- sent and acted -- each with the row that proves it; received and acknowledged are null with stage_unavailable_reason no_dispatch_spine, because public.partner_room_turn has no session id, no work-request id and no acknowledgement column.';

-- -------------------------------------------------------------------------
-- Grants. carr_writer AND carr_authority, exactly as ops.list_doc_conversations
-- is granted at 0525:198-201. NEITHER function is authority-only: the Control
-- Room calls both as the signed-in partner, who holds no sponsor-scoped
-- authority binding, and an authority-only door is one the product cannot open
-- however correct its SQL is. The argument types are spelled in full in BOTH
-- lines of BOTH pairs -- a stale arity revokes nothing and would leave PUBLIC
-- execute on a security definer. NO new table grant of any kind.
-- -------------------------------------------------------------------------

revoke all on function ops.session_identity_facts(text,integer,boolean)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.session_identity_facts(text,integer,boolean)
  to carr_writer,carr_authority;

revoke all on function ops.session_dispatch_history(text,text,integer)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.session_dispatch_history(text,text,integer)
  to carr_writer,carr_authority;
