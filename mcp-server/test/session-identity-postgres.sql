-- WR-000117 -- the measured proof for the session-identity READ PAIR.
-- Run by ops/ci.sh's migration class.
--
-- WHAT ONLY A DATABASE CAN SHOW:
--   * that the permission filter holds PER BOOK. The four books carry four
--     different owner columns -- claude_continuity_leaf has two, the Codex
--     checkpoint has one, ops.capability_agent_session has two, and
--     public.session_work has NONE -- and a union filtered once passes through
--     the branch whose owner column is absent. Each branch is therefore seeded
--     for a SECOND actor and asserted separately; an assertion over the union
--     would stay green while one branch leaked.
--   * that the answer follows the ACTING-ACTOR CONTEXT and not any argument.
--     Both functions take no actor parameter at all, so the only way to prove
--     the derivation is to move the context and re-read with the same
--     arguments.
--   * that permission filtering is a COUNT COMPARISON. An actor who may see
--     nothing must get total_seen > total_returned, which is a statement about
--     rows that were withheld and cannot be read off the returned list.
--   * that an UNRECORDED PARENT is distinguishable from a ROOT.
--     public.codex_continuity_checkpoint has no parent column of any kind, so
--     its rows must report parent_known false while a Claude root reports
--     parent_known true with a null parent.
--   * that the idle/disconnected threshold is computed from the row's OWN
--     last_observed_at against now() INSIDE the function. The fixtures below
--     are stamped RELATIVE TO now(), never at a literal instant, because a
--     literal instant passes for months and fails on one run.
--   * that the dispatch answer carries the two stages the substrate proves and
--     names the two it does not, and that a stale cursor neither skips nor
--     repeats a row.
--   * that NO ROW COUNT IN ANY OF THE FOUR BOOKS MOVES ACROSS EITHER READ.
--     These are reads; a count taken across the call is the only evidence that
--     says so about the tables rather than about the return value.
--   * that both functions reach carr_writer and carr_authority on the exact
--     argument types and reach neither carr_reader, nor carr_jobs, nor public.
--     A stale arity would name a different function and prove nothing.

\set ON_ERROR_STOP on
begin;

do $wr117_shape$
declare v_identity text := 'ops.session_identity_facts(text,integer,boolean)';
        v_history text := 'ops.session_dispatch_history(text,text,integer)';
        v_fn text;
begin
  foreach v_fn in array array[v_identity, v_history] loop
    if not has_function_privilege('carr_writer', v_fn, 'execute')
       or not has_function_privilege('carr_authority', v_fn, 'execute')
       or has_function_privilege('carr_reader', v_fn, 'execute')
       or has_function_privilege('carr_jobs', v_fn, 'execute')
       or has_function_privilege('public', v_fn, 'execute') then
      raise exception 'WR-000117: % does not reach exactly the writer and authority bundles', v_fn;
    end if;
  end loop;

  -- BOTH are STABLE, so both can run inside the writer connection's
  -- `begin read only` transaction. A volatile one would fail in production.
  if (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'ops'
         and p.proname in ('session_identity_facts', 'session_dispatch_history')
         and p.provolatile = 's') <> 2 then
    raise exception 'WR-000117: a read door is not STABLE and would fail read-only in production';
  end if;

  -- NO ACTOR ARGUMENT, asserted on the identity argument list itself rather
  -- than on a count.
  if (select pg_get_function_identity_arguments(p.oid) from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'ops' and p.proname = 'session_identity_facts')
     <> 'p_query text, p_limit integer, p_include_closed boolean' then
    raise exception 'WR-000117: the identity door signature moved: %',
      (select pg_get_function_identity_arguments(p.oid) from pg_proc p
         join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'ops' and p.proname = 'session_identity_facts');
  end if;
  if (select pg_get_function_identity_arguments(p.oid) from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'ops' and p.proname = 'session_dispatch_history')
     <> 'p_session_id text, p_cursor text, p_limit integer' then
    raise exception 'WR-000117: the dispatch door signature moved: %',
      (select pg_get_function_identity_arguments(p.oid) from pg_proc p
         join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'ops' and p.proname = 'session_dispatch_history');
  end if;
end $wr117_shape$;

-- TWO ACTORS. One query shape, two different answers, is the whole point: a
-- proof that mints one actor passes under an implementation that ignores the
-- derivation entirely.
insert into public.actor (id, slug, kind, display_name) values
 ('a1170000-0000-4000-8000-000000000001','wr117-alpha','automation','WR117 alpha'),
 ('a1170000-0000-4000-8000-000000000002','wr117-beta','automation','WR117 beta');

-- TWO work requests: a unique index allows one open capability session per
-- request, so the two actors' sessions cannot share one.
insert into ops.work_request (id, ref, title, requester_actor) values
 ('a1170000-0000-4000-8000-0000000000aa','WR-000117-PROOF-A','WR117 proof request alpha','wr117-alpha'),
 ('a1170000-0000-4000-8000-0000000000ab','WR-000117-PROOF-B','WR117 proof request beta','wr117-beta');

-- FIXTURES ONLY, AND ONLY FOR THE AGE CASES. Every one of these books stamps
-- updated_at from a BEFORE trigger, so a row cannot be given an age with the
-- triggers live and the idle/disconnected rule could not be exercised at all.
-- The reads below all run with `origin` restored, so nothing the FUNCTIONS do
-- is measured under a relaxed session.
set local session_replication_role = replica;

-- EVERY FIXTURE TIMESTAMP IS RELATIVE TO now(). A hard-coded instant passes
-- for months and fails on one run.
insert into public.claude_continuity_leaf
 (organization_tenant_id, surface_principal_actor_id, owner_actor_id, session_id,
  transcript_path_digest, project_affinity, parent_session_id, native_agent_id,
  latest_cwd, latest_model_id, created_at, updated_at)
values
 ('wr117','a1170000-0000-4000-8000-000000000001','a1170000-0000-4000-8000-000000000001',
  'wr117-claude-child', repeat('a',64), 'wr117-project', 'wr117-claude-root',
  'wr117-agent-child', '/wr117', 'claude-opus-5', now() - interval '2 minutes', now() - interval '2 minutes'),
 ('wr117','a1170000-0000-4000-8000-000000000001','a1170000-0000-4000-8000-000000000001',
  'wr117-claude-root', repeat('b',64), 'wr117-project', null,
  'wr117-agent-root', '/wr117', 'claude-opus-5', now() - interval '45 minutes', now() - interval '45 minutes'),
 ('wr117','a1170000-0000-4000-8000-000000000002','a1170000-0000-4000-8000-000000000002',
  'wr117-claude-beta', repeat('c',64), 'wr117-project', null,
  'wr117-agent-beta', '/wr117', 'claude-opus-5', now() - interval '3 days', now() - interval '3 days');

insert into public.codex_continuity_checkpoint
 (organization_tenant_id, owner_actor_id, native_task_id, project_id, cwd, state, created_at, updated_at)
values
 ('wr117','a1170000-0000-4000-8000-000000000001','wr117-codex-alpha','wr117-project','/wr117','{}',
  now() - interval '5 minutes', now() - interval '5 minutes'),
 ('wr117','a1170000-0000-4000-8000-000000000002','wr117-codex-beta','wr117-project','/wr117','{}',
  now() - interval '5 minutes', now() - interval '5 minutes');

insert into ops.capability_agent_session
 (id, work_request_id, executor_actor_id, created_by_actor_id, state,
  source_commit_sha, worktree_ref, started_at, updated_at)
values
 ('a1170000-0000-4000-8000-0000000000c1','a1170000-0000-4000-8000-0000000000aa',
  'a1170000-0000-4000-8000-000000000001','a1170000-0000-4000-8000-000000000001',
  'in_progress', repeat('1',40), 'wr117-alpha-tree', now() - interval '4 minutes',
  now() - interval '4 minutes'),
 ('a1170000-0000-4000-8000-0000000000c2','a1170000-0000-4000-8000-0000000000ab',
  'a1170000-0000-4000-8000-000000000002','a1170000-0000-4000-8000-000000000002',
  'in_progress', repeat('2',40), 'wr117-beta-tree', now() - interval '4 minutes',
  now() - interval '4 minutes');

insert into public.session_work (id, kind, title, last_seen) values
 ('worktree:wr117-harvested','worktree','wr117 harvested worktree', now() - interval '1 minute');

-- The dispatch wire. Two turns naming the same session in the same room, so
-- the earlier one is superseded by the later one.
insert into public.partner_room_turn (room_id, sponsor, seat, kind, body, msg_id, origin_channel, origin_actor, at) values
 ('model-room','joe','wr117-beta','turn','dispatch one for wr117-claude-child',
  gen_random_uuid(),'mcp','wr117-alpha', now() - interval '30 minutes'),
 ('model-room','joe','wr117-beta','turn','dispatch two for wr117-claude-child, superseding the first',
  gen_random_uuid(),'mcp','wr117-alpha', now() - interval '10 minutes'),
 ('model-room','joe','wr117-gamma','turn','a turn naming wr117-claude-child that neither actor sent',
  gen_random_uuid(),'mcp','wr117-delta', now() - interval '20 minutes');

set local session_replication_role = origin;

do $wr117_reads$
declare r jsonb; s jsonb; b jsonb;
        c_leaf bigint; c_codex bigint; c_cap bigint; c_work bigint;
        c_leaf2 bigint; c_codex2 bigint; c_cap2 bigint; c_work2 bigint;
        v_cursor text; v_first text; v_second text;
begin
  select count(*) into c_leaf from public.claude_continuity_leaf;
  select count(*) into c_codex from public.codex_continuity_checkpoint;
  select count(*) into c_cap from ops.capability_agent_session;
  select count(*) into c_work from public.session_work;

  perform set_config('carr.acting_actor_slug','wr117-alpha',true);
  r := ops.session_identity_facts('wr117', 50, false);

  -- PER-BOOK FILTER, ASSERTED PER BOOK. Alpha must see its own row in each of
  -- the three owned books and NONE of beta's, and must see the unowned
  -- harvested row.
  if not exists (select 1 from jsonb_array_elements(r->'sessions') e
                  where e->>'canonical_session_id' = 'wr117-claude-child'
                    and e->>'surface' = 'claude'
                    and e->>'observation_source' = 'continuity_event') then
    raise exception 'WR-000117: the Claude book did not resolve for its own owner: %', r;
  end if;
  if exists (select 1 from jsonb_array_elements(r->'sessions') e
              where e->>'canonical_session_id' = 'wr117-claude-beta') then
    raise exception 'WR-000117: the CLAUDE branch leaked another actor''s row';
  end if;
  if not exists (select 1 from jsonb_array_elements(r->'sessions') e
                  where e->>'canonical_session_id' = 'wr117-codex-alpha'
                    and e->>'observation_source' = 'checkpoint') then
    raise exception 'WR-000117: the Codex book did not resolve for its own owner';
  end if;
  if exists (select 1 from jsonb_array_elements(r->'sessions') e
              where e->>'canonical_session_id' = 'wr117-codex-beta') then
    raise exception 'WR-000117: the CODEX branch leaked another actor''s row';
  end if;
  if not exists (select 1 from jsonb_array_elements(r->'sessions') e
                  where e->>'canonical_session_id' = 'a1170000-0000-4000-8000-0000000000c1'
                    and e->>'observation_source' = 'server_session') then
    raise exception 'WR-000117: the capability book did not resolve for its own executor';
  end if;
  if exists (select 1 from jsonb_array_elements(r->'sessions') e
              where e->>'canonical_session_id' = 'a1170000-0000-4000-8000-0000000000c2') then
    raise exception 'WR-000117: the CAPABILITY branch leaked another actor''s row';
  end if;
  select e into b from jsonb_array_elements(r->'sessions') e
   where e->>'canonical_session_id' = 'worktree:wr117-harvested';
  if b is null or b->>'observation_source' <> 'harvest' or b->>'work_state' <> 'unknown' then
    raise exception 'WR-000117: the harvested row is absent or claims a work state: %', b;
  end if;
  if b->>'work_state_evidence' not like '%not scheduled%' then
    raise exception 'WR-000117: the harvested row does not carry why it cannot claim liveness';
  end if;

  -- THE DERIVED ALIAS. Never `human`, because nothing stores one.
  if exists (select 1 from jsonb_array_elements(r->'sessions') e
              where e->>'alias_source' <> 'derived') then
    raise exception 'WR-000117: a row claimed an alias source no relation stores';
  end if;

  -- UNRECORDED PARENT vs ROOT.
  select e into s from jsonb_array_elements(r->'sessions') e
   where e->>'canonical_session_id' = 'wr117-codex-alpha';
  if (s->>'parent_known')::boolean is distinct from false or s->'parent_session_id' <> 'null'::jsonb then
    raise exception 'WR-000117: a Codex row reported an unrecorded parent as a root: %', s;
  end if;
  select e into s from jsonb_array_elements(r->'sessions') e
   where e->>'canonical_session_id' = 'wr117-claude-root';
  if (s->>'parent_known')::boolean is distinct from true or s->'parent_session_id' <> 'null'::jsonb then
    raise exception 'WR-000117: the Claude root did not report a KNOWN null parent: %', s;
  end if;
  select e into s from jsonb_array_elements(r->'sessions') e
   where e->>'canonical_session_id' = 'wr117-claude-child';
  if s->>'parent_session_id' <> 'wr117-claude-root' or (s->>'attempt_count')::integer <> 2 then
    raise exception 'WR-000117: the Claude lineage did not resolve: %', s;
  end if;

  -- THE AGE RULE, computed from each row's OWN timestamp against now().
  -- child = 2 minutes -> working; root = 45 minutes -> idle;
  -- and beta's 3-day row is another actor's, so the disconnected case is read
  -- below under beta's own context.
  if s->>'work_state' <> 'working' then
    raise exception 'WR-000117: a two-minute-old row is not working: %', s->>'work_state';
  end if;
  select e into s from jsonb_array_elements(r->'sessions') e
   where e->>'canonical_session_id' = 'wr117-claude-root';
  if s->>'work_state' <> 'idle' then
    raise exception 'WR-000117: a forty-five-minute-old row is not idle: %', s->>'work_state';
  end if;

  -- THE COUNT COMPARISON. Alpha withheld beta's rows, so the two counts differ
  -- and permission_filtered says so.
  if (r->>'permission_filtered')::boolean is distinct from true
     or (r->>'total_seen')::integer <= (r->>'total_returned')::integer then
    raise exception 'WR-000117: permission filtering is not reported as a count comparison: %',
      jsonb_build_object('seen', r->'total_seen', 'returned', r->'total_returned',
                         'filtered', r->'permission_filtered');
  end if;

  -- SAME ARGUMENTS, DIFFERENT ACTOR, DIFFERENT ANSWER.
  perform set_config('carr.acting_actor_slug','wr117-beta',true);
  s := ops.session_identity_facts('wr117', 50, false);
  if s = r then
    raise exception 'WR-000117: two actors got the same answer from the same arguments';
  end if;
  if exists (select 1 from jsonb_array_elements(s->'sessions') e
              where e->>'canonical_session_id' in
                    ('wr117-claude-child','wr117-codex-alpha',
                     'a1170000-0000-4000-8000-0000000000c1')) then
    raise exception 'WR-000117: the second actor saw the first actor''s rows';
  end if;
  select e into b from jsonb_array_elements(s->'sessions') e
   where e->>'canonical_session_id' = 'wr117-claude-beta';
  if b->>'work_state' <> 'disconnected' then
    raise exception 'WR-000117: a three-day-old row is not disconnected: %', b->>'work_state';
  end if;

  -- AN ACTOR WHO MAY SEE NOTHING gets an explicitly filtered empty list, never
  -- an answer indistinguishable from an empty system.
  s := ops.session_identity_facts('wr117-claude-child', 50, false);
  if jsonb_array_length(s->'sessions') <> 0
     or (s->>'permission_filtered')::boolean is distinct from true
     or (s->>'total_seen')::integer = 0 then
    raise exception 'WR-000117: a fully filtered answer is indistinguishable from an empty system: %', s;
  end if;

  -- THE DISPATCH ANSWER. Alpha sent both turns, so alpha sees them.
  perform set_config('carr.acting_actor_slug','wr117-alpha',true);
  r := ops.session_dispatch_history('wr117-claude-child', null, 50);
  if r->'received' <> 'null'::jsonb or r->'acknowledged' <> 'null'::jsonb
     or r->>'stage_unavailable_reason' <> 'no_dispatch_spine' then
    raise exception 'WR-000117: the two unprovable stages were not returned null with their reason: %', r;
  end if;
  if exists (select 1 from jsonb_array_elements(r->'events') e
              where e->>'stage' not in ('sent','acted')) then
    raise exception 'WR-000117: a stage this substrate cannot prove was emitted';
  end if;
  if not exists (select 1 from jsonb_array_elements(r->'events') e
                  where e->>'stage' = 'sent'
                    and e->>'stage_evidence' like 'public.partner_room_turn id %') then
    raise exception 'WR-000117: a sent event does not carry the row that proves it';
  end if;
  -- The turn neither actor sent is withheld, and the count says so.
  if exists (select 1 from jsonb_array_elements(r->'events') e
              where e->>'rationale' like '%neither actor sent%') then
    raise exception 'WR-000117: the dispatch read leaked a turn this actor is not party to';
  end if;
  if (r->>'permission_filtered')::boolean is distinct from true then
    raise exception 'WR-000117: the dispatch read withheld a row without saying so';
  end if;
  -- SUPERSESSION is marked, not hidden.
  if not exists (select 1 from jsonb_array_elements(r->'events') e
                  where e->>'rationale' like 'dispatch one%'
                    and e->>'superseded_by' is not null) then
    raise exception 'WR-000117: the superseded instruction is not marked';
  end if;
  if not exists (select 1 from jsonb_array_elements(r->'events') e
                  where e->>'rationale' like 'dispatch two%'
                    and e->'superseded_by' = 'null'::jsonb) then
    raise exception 'WR-000117: the current instruction was marked superseded';
  end if;

  -- A STALE CURSOR NEITHER SKIPS NOR REPEATS. Page one of one row, then the
  -- cursor, then page two: the two pages are disjoint and together are the set.
  r := ops.session_dispatch_history('wr117-claude-child', null, 1);
  v_first := r->'events'->0->>'event_id';
  v_cursor := r->>'next_cursor';
  if v_cursor is null then
    raise exception 'WR-000117: a page with more rows minted no cursor';
  end if;
  s := ops.session_dispatch_history('wr117-claude-child', v_cursor, 1);
  v_second := s->'events'->0->>'event_id';
  if v_second is null or v_second = v_first then
    raise exception 'WR-000117: the cursor repeated a row';
  end if;
  if (select count(*) from jsonb_array_elements(
        ops.session_dispatch_history('wr117-claude-child', null, 50)->'events') e
       where e->>'event_id' in (v_first, v_second)) <> 2 then
    raise exception 'WR-000117: the cursor skipped a row the unpaged answer returns';
  end if;
  if ops.session_dispatch_history('wr117-claude-child', 'not-a-token', 50)->>'reason_id'
     is distinct from 'dispatch_cursor_invalid' then
    raise exception 'WR-000117: an unreadable cursor did not produce one named refusal';
  end if;

  -- THE ACTED STAGE, evidenced by the column that recorded it.
  r := ops.session_dispatch_history('a1170000-0000-4000-8000-0000000000c1', null, 50);
  if not exists (select 1 from jsonb_array_elements(r->'events') e
                  where e->>'stage' = 'acted'
                    and e->>'stage_evidence' like '%column started_at%'
                    and e->>'work_request_ref' = 'a1170000-0000-4000-8000-0000000000aa') then
    raise exception 'WR-000117: the acted stage does not carry the column that proves it: %', r;
  end if;

  -- NO ROW COUNT IN ANY BOOK MOVED ACROSS EITHER READ.
  select count(*) into c_leaf2 from public.claude_continuity_leaf;
  select count(*) into c_codex2 from public.codex_continuity_checkpoint;
  select count(*) into c_cap2 from ops.capability_agent_session;
  select count(*) into c_work2 from public.session_work;
  if (c_leaf, c_codex, c_cap, c_work) is distinct from (c_leaf2, c_codex2, c_cap2, c_work2) then
    raise exception 'WR-000117: a READ moved a row count: % -> %',
      (c_leaf, c_codex, c_cap, c_work), (c_leaf2, c_codex2, c_cap2, c_work2);
  end if;
end $wr117_reads$;

rollback;

\echo 'WR-000117 session identity: the per-book permission filter, the count comparison, root against unrecorded parent, the age rule from each row own timestamp, two proven dispatch stages with the named unavailable reason, an opaque cursor that neither skips nor repeats, and no row count moved'
