-- WR-000119 -- the measured proof for the DISPATCH SPINE.
-- Run by ops/ci.sh's migration class, beside the WR-000117 session-identity block.
--
-- WHAT ONLY A DATABASE CAN SHOW, and what is therefore here rather than in node:
--   * that a SECOND ack of the same (dispatch_ref, stage) is refused by the
--     UNIQUE CONSTRAINT and not by a handler branch. A node case against a fake
--     would pass whether the constraint existed or not.
--   * that an UPDATE of either relation is refused because NO UPDATE GRANT
--     EXISTS. Append-only is a grant here, not a convention, and a missing
--     grant is invisible from JavaScript.
--   * that an ack whose dispatch_ref has no link is refused BY THE REFERENCE.
--   * that the hermes-pilot restriction on record-dispatch-link lives INSIDE
--     the definer: the same call under a different derived identity is refused
--     with its reason, and the refusal is proved by moving the acting-actor
--     context rather than by passing a different argument -- there is no actor
--     argument to pass.
--   * that stage_unavailable_reason is computed PER ROW inside the function
--     rather than once over the result: one answer carries a proved dispatch, a
--     linked-but-unacknowledged dispatch and a pre-spine dispatch at the same
--     time, and each row carries its own value.
--   * that public.partner_room_turn's row count NEVER MOVES. The rejected
--     design wrote the wire; a count taken across every call in this file is
--     the only evidence that says so about the table rather than about a return
--     value.
--
-- EVERY FIXTURE TIMESTAMP IS RELATIVE TO now(). The transaction clock is
-- constant inside a statement, so a hard-coded instant proves nothing about
-- ordering and fails on one run months from now.

\set ON_ERROR_STOP on
begin;

-- ---------------------------------------------------------------------------
-- 1. THE GRANT SHAPE. insert and select to the writer and the authority
-- bundle; update and delete to NOBODY, on BOTH relations. A stale arity or a
-- missing relation would name nothing and prove nothing, so each is spelled.
-- ---------------------------------------------------------------------------
do $wr119_grants$
declare v_rel text; v_priv text;
begin
  foreach v_rel in array array['public.room_dispatch_link','public.room_dispatch_ack'] loop
    foreach v_priv in array array['insert','select'] loop
      if not has_table_privilege('carr_writer', v_rel, v_priv)
         or not has_table_privilege('carr_authority', v_rel, v_priv) then
        raise exception 'WR-000119: % does not reach the writer and authority bundle for %', v_rel, v_priv;
      end if;
    end loop;
    foreach v_priv in array array['update','delete'] loop
      if has_table_privilege('carr_writer', v_rel, v_priv)
         or has_table_privilege('carr_authority', v_rel, v_priv)
         or has_table_privilege('carr_reader', v_rel, v_priv)
         or has_table_privilege('carr_jobs', v_rel, v_priv)
         or has_table_privilege('public', v_rel, v_priv) then
        raise exception 'WR-000119: % is MUTABLE -- % is granted to somebody', v_rel, v_priv;
      end if;
    end loop;
  end loop;

  -- The two new write doors reach the writer and the authority bundle on their
  -- exact argument types, and reach nobody else.
  foreach v_rel in array array['ops.record_dispatch_link(uuid,text,uuid,uuid)',
                               'ops.acknowledge_dispatch(uuid,text,text)'] loop
    if not has_function_privilege('carr_writer', v_rel, 'execute')
       or not has_function_privilege('carr_authority', v_rel, 'execute')
       or has_function_privilege('carr_reader', v_rel, 'execute')
       or has_function_privilege('carr_jobs', v_rel, 'execute')
       or has_function_privilege('public', v_rel, 'execute') then
      raise exception 'WR-000119: % has the wrong execute grant shape', v_rel;
    end if;
  end loop;
end $wr119_grants$;

-- ---------------------------------------------------------------------------
-- 2. THE FIXTURE. TWO actors, because every identity assertion here is about a
-- derivation and a single-actor fixture passes under an implementation that
-- ignores the derivation entirely. Three dispatches to ONE session so the
-- per-row reason has something to be wrong about:
--   alpha  linked AND acknowledged -- all four stages
--   beta   linked, NO ack row      -- received null, reason not_acknowledged
--   gamma  NO link at all          -- pre-spine, reason no_dispatch_spine
-- ---------------------------------------------------------------------------
do $wr119_spine$
declare v_session text := 'wr119-session-' || substr(md5(random()::text), 1, 8);
        v_alpha uuid := gen_random_uuid();
        v_beta  uuid := gen_random_uuid();
        v_turn_alpha uuid := gen_random_uuid();
        v_turn_beta  uuid := gen_random_uuid();
        v_turn_gamma uuid := gen_random_uuid();
        v_turn_orphan uuid := gen_random_uuid();
        v_ref_alpha uuid := gen_random_uuid();
        v_ref_beta  uuid := gen_random_uuid();
        v_ref_absent uuid := gen_random_uuid();
        -- The optional work_request_id the beta link carries. The column has no
        -- foreign key by design, so this proves the OPTIONAL argument end to end
        -- without ordering this block after the work_request seed below.
        v_link_wr uuid := gen_random_uuid();
        c_turns bigint; c_turns2 bigint; r jsonb; e jsonb; v_count integer;
        v_wr uuid; v_hermes uuid;
begin
  insert into public.actor(slug, kind, display_name, active)
  values ('wr119-intruder', 'automation', 'WR119 intruder', true)
  on conflict (slug) do nothing;

  -- The acting identity is the SERVER CONTEXT and nothing else. hermes-pilot
  -- already exists (0169); the intruder above is the second actor every
  -- identity assertion below needs.
  perform set_config('carr.acting_actor_slug', 'hermes-pilot', true);

  select count(*) into c_turns from public.partner_room_turn;

  insert into public.partner_room_turn(room_id, sponsor, seat, kind, body, msg_id,
                                       origin_channel, origin_actor)
  values ('partner-line','joe','hermes','turn',
          'dispatch alpha for ' || v_session, v_turn_alpha, 'mcp', 'hermes-pilot'),
         ('partner-line','joe','hermes','turn',
          'dispatch beta for ' || v_session, v_turn_beta, 'mcp', 'hermes-pilot'),
         ('partner-line','joe','hermes','turn',
          'dispatch gamma for ' || v_session, v_turn_gamma, 'mcp', 'hermes-pilot'),
         ('partner-line','joe','hermes','turn',
          'a turn naming nobody', v_turn_orphan, 'mcp', 'hermes-pilot');

  -- ONE LINK PER ASSIGNMENT, and none for a turn that was never assigned.
  r := ops.record_dispatch_link(v_turn_alpha, v_session, null, v_ref_alpha);
  if r->>'ok' <> 'true' or r->>'deduplicated' <> 'false' then
    raise exception 'WR-000119: the first link was not minted: %', r;
  end if;
  -- THE OPTIONAL work_request_id, PRESENT. alpha was minted with null above;
  -- beta names one, so the two rows differ in exactly that column.
  r := ops.record_dispatch_link(v_turn_beta, v_session, v_link_wr, v_ref_beta);
  if r->>'ok' <> 'true' then
    raise exception 'WR-000119: the beta link was not minted: %', r;
  end if;
  if not exists (select 1 from public.room_dispatch_link
                  where dispatch_ref = v_ref_beta and work_request_id = v_link_wr) then
    raise exception 'WR-000119: the optional work_request_id was not stored on the beta link';
  end if;
  if exists (select 1 from public.room_dispatch_link
              where dispatch_ref = v_ref_alpha and work_request_id is not null) then
    raise exception 'WR-000119: an omitted work_request_id was invented on the alpha link';
  end if;

  -- IDEMPOTENT ON THE ASSIGNMENT. A retried cycle that mints a FRESH
  -- dispatch_ref for a turn already linked must not leave two links for one
  -- assignment; `sent` would then be proved twice.
  r := ops.record_dispatch_link(v_turn_alpha, v_session, null, gen_random_uuid());
  if r->>'deduplicated' <> 'true' or (r->>'dispatch_ref')::uuid <> v_ref_alpha then
    raise exception 'WR-000119: a retried assignment minted a second link: %', r;
  end if;
  select count(*) into v_count from public.room_dispatch_link
   where session_id = v_session;
  if v_count <> 2 then
    raise exception 'WR-000119: expected exactly two links for this session, found %', v_count;
  end if;
  select count(*) into v_count from public.room_dispatch_link l
   where l.turn_id = (select id from public.partner_room_turn where msg_id = v_turn_orphan);
  if v_count <> 0 then
    raise exception 'WR-000119: a turn that was never assigned carries a link row';
  end if;

  -- THE hermes-pilot RESTRICTION IS INSIDE THE DEFINER. Same call, same
  -- arguments, different DERIVED identity -- and there is no actor argument
  -- that could have carried the difference.
  perform set_config('carr.acting_actor_slug', 'wr119-intruder', true);
  r := ops.record_dispatch_link(v_turn_gamma, v_session, null, gen_random_uuid());
  if r->>'ok' <> 'false' or r->>'reason_id' <> 'dispatch_link_hermes_pilot_only' then
    raise exception 'WR-000119: a non-hermes-pilot identity minted a link: %', r;
  end if;
  select count(*) into v_count from public.room_dispatch_link where session_id = v_session;
  if v_count <> 2 then
    raise exception 'WR-000119: the refused mint still wrote a row: %', v_count;
  end if;
  perform set_config('carr.acting_actor_slug', 'hermes-pilot', true);

  -- AN ACK WHOSE dispatch_ref HAS NO LINK IS REFUSED, and by the reference
  -- rather than by a handler: the door refuses it with a reason first, and a
  -- direct insert of the same row is refused by the foreign key second.
  r := ops.acknowledge_dispatch(v_ref_absent, 'received', 'nothing to acknowledge');
  if r->>'ok' <> 'false' or r->>'reason_id' <> 'dispatch_link_not_found' then
    raise exception 'WR-000119: an ack for an absent link was accepted: %', r;
  end if;
  begin
    insert into public.room_dispatch_ack(dispatch_ref, stage, by_actor)
    values (v_ref_absent, 'received', 'hermes-pilot');
    raise exception 'WR-000119: the ack reference does not exist -- a dangling ack was stored';
  exception when foreign_key_violation then
    null;
  end;

  -- A SECOND ACK OF THE SAME STAGE IS THE SAME FACT RESTATED, and it is the
  -- UNIQUE CONSTRAINT that says so: the door returns deduplicated, and a direct
  -- insert bypassing the door is refused by the database itself.
  r := ops.acknowledge_dispatch(v_ref_alpha, 'received', 'desk alpha log offset 4096');
  if r->>'ok' <> 'true' or r->>'deduplicated' <> 'false' then
    raise exception 'WR-000119: the received ack was not appended: %', r;
  end if;
  r := ops.acknowledge_dispatch(v_ref_alpha, 'received', 'desk alpha log offset 4096');
  if r->>'deduplicated' <> 'true' then
    raise exception 'WR-000119: a restated received ack was not deduplicated: %', r;
  end if;
  begin
    insert into public.room_dispatch_ack(dispatch_ref, stage, by_actor)
    values (v_ref_alpha, 'received', 'hermes-pilot');
    raise exception 'WR-000119: a second received row for one dispatch was ACCEPTED';
  exception when unique_violation then
    null;
  end;
  -- The stage vocabulary is the relation's own check, not a handler's opinion.
  begin
    insert into public.room_dispatch_ack(dispatch_ref, stage, by_actor)
    values (v_ref_alpha, 'delivered', 'hermes-pilot');
    raise exception 'WR-000119: an unknown stage was stored';
  exception when check_violation then
    null;
  end;
  r := ops.acknowledge_dispatch(v_ref_alpha, 'delivered', null);
  if r->>'ok' <> 'false' or r->>'reason_id' <> 'dispatch_stage_invalid' then
    raise exception 'WR-000119: an unknown stage reached the relation: %', r;
  end if;

  -- The acting session's own acknowledgement, from a DIFFERENT derived
  -- identity, which is what makes by_actor first-hand rather than decorative.
  perform set_config('carr.acting_actor_slug', 'wr119-intruder', true);
  r := ops.acknowledge_dispatch(v_ref_alpha, 'acknowledged', 'took the turn up');
  if r->>'ok' <> 'true' or r->>'by_actor' <> 'wr119-intruder' then
    raise exception 'WR-000119: the acknowledged ack did not stamp its own caller: %', r;
  end if;
  perform set_config('carr.acting_actor_slug', 'hermes-pilot', true);

  -- THE ACTED STAGE, unchanged from 0529 and seeded here only so the four
  -- stages can be asserted together in ONE answer.
  -- The capability book stamps state through BEFORE triggers that refuse a row
  -- created in any state but claimed. The fixture is seeded with the session's
  -- replication role relaxed and restored IMMEDIATELY after, so nothing the
  -- SPINE does below is measured under a relaxed session -- every refusal this
  -- file proves is proved with triggers and references fully armed.
  set local session_replication_role = replica;
  select id into v_hermes from public.actor where slug = 'hermes-pilot';
  insert into ops.work_request(ref, title, requester_actor)
  values ('WR-119-' || substr(md5(random()::text), 1, 6), 'WR119 spine proof', v_hermes)
  returning id into v_wr;
  insert into ops.capability_agent_session
    (id, work_request_id, executor_actor_id, created_by_actor_id, state,
     source_commit_sha, worktree_ref, started_at, updated_at)
  values (gen_random_uuid(), v_wr, v_hermes, v_hermes,
          'in_progress', repeat('9', 40), 'tree-for-' || v_session,
          now() - interval '4 minutes', now() - interval '4 minutes');
  set local session_replication_role = origin;

  -- ---------------------------------------------------------------------
  -- 3. THE READ. ONE answer carrying a proved-and-acknowledged dispatch, a
  -- linked-but-unacknowledged dispatch and a pre-spine dispatch at once. A
  -- reason computed once over the result cannot be right about all three,
  -- which is exactly why they are asserted in the same answer.
  -- ---------------------------------------------------------------------
  r := ops.session_dispatch_history(v_session, null, 50);
  if r->>'ok' <> 'true' then
    raise exception 'WR-000119: the dispatch history refused: %', r;
  end if;

  -- ALL FOUR STAGES HAVE EVIDENCE.
  foreach e in array array['"sent"'::jsonb, '"received"', '"acknowledged"', '"acted"'] loop
    if not exists (select 1 from jsonb_array_elements(r->'events') x
                    where x->'stage' = e
                      and coalesce(x->>'stage_evidence', '') <> '') then
      raise exception 'WR-000119: stage % is missing or carries no evidence: %', e, r;
    end if;
  end loop;

  -- The ack stages carry the ACK ROW that proves them, by id.
  if not exists (select 1 from jsonb_array_elements(r->'events') x
                  where x->>'stage' = 'received'
                    and x->>'stage_evidence' like 'public.room_dispatch_ack id %'
                    and x->>'stage_evidence' like ('%for dispatch_ref ' || v_ref_alpha || '%')) then
    raise exception 'WR-000119: the received stage does not carry its ack row: %', r;
  end if;

  -- THE PROVED DISPATCH: link_source proved, and its reason is NULL because
  -- the stage it would have explained is proved.
  if not exists (select 1 from jsonb_array_elements(r->'events') x
                  where x->>'stage' = 'sent'
                    and (x->>'dispatch_ref')::uuid = v_ref_alpha
                    and x->>'link_source' = 'proved'
                    and x->'stage_unavailable_reason' = 'null'::jsonb) then
    raise exception 'WR-000119: the proved dispatch is not labelled proved with a null reason: %', r;
  end if;

  -- THE LINKED, UNACKNOWLEDGED DISPATCH: received is NOT returned for it at
  -- all, it is NEVER rendered as failed, and its reason is not_acknowledged.
  if exists (select 1 from jsonb_array_elements(r->'events') x
              where (x->>'dispatch_ref')::uuid = v_ref_beta
                and x->>'stage' in ('received', 'acknowledged', 'failed')) then
    raise exception 'WR-000119: a silent desk was rendered as received, acknowledged or failed: %', r;
  end if;
  if not exists (select 1 from jsonb_array_elements(r->'events') x
                  where x->>'stage' = 'sent'
                    and (x->>'dispatch_ref')::uuid = v_ref_beta
                    and x->>'link_source' = 'proved'
                    and x->>'stage_unavailable_reason' = 'not_acknowledged') then
    raise exception 'WR-000119: a link with no ack does not read not_acknowledged: %', r;
  end if;

  -- THE PRE-SPINE DISPATCH: no link row at all, so the substring path survives,
  -- it is labelled body_match, and ONLY this one may borrow no_dispatch_spine.
  if not exists (select 1 from jsonb_array_elements(r->'events') x
                  where x->>'stage' = 'sent'
                    and x->>'link_source' = 'body_match'
                    and x->'dispatch_ref' = 'null'::jsonb
                    and x->>'stage_unavailable_reason' = 'no_dispatch_spine') then
    raise exception 'WR-000119: the pre-spine dispatch is not labelled body_match: %', r;
  end if;

  -- THE TWO NULLS MUST NEVER COLLAPSE. Both reasons are present in the SAME
  -- answer and they are different values; a constant could only be one of them.
  if (select count(distinct x->>'stage_unavailable_reason')
        from jsonb_array_elements(r->'events') x
       where x->>'stage' = 'sent' and x->'stage_unavailable_reason' <> 'null'::jsonb) <> 2 then
    raise exception 'WR-000119: not_acknowledged and no_dispatch_spine collapsed into one value: %', r;
  end if;

  -- ---------------------------------------------------------------------
  -- 4. APPEND-ONLY, PROVED BY ATTEMPT. The privilege assertions above say the
  -- grant is absent; this says what happens when the role that holds insert
  -- and select tries anyway.
  -- ---------------------------------------------------------------------
  begin
    set local role carr_writer;
    begin
      update public.room_dispatch_link set session_id = 'rewritten'
       where dispatch_ref = v_ref_alpha;
      raise exception 'WR-000119: carr_writer UPDATED a link row';
    exception when insufficient_privilege then
      null;
    end;
    begin
      update public.room_dispatch_ack set stage = 'acknowledged'
       where dispatch_ref = v_ref_alpha;
      raise exception 'WR-000119: carr_writer UPDATED an ack row';
    exception when insufficient_privilege then
      null;
    end;
    begin
      delete from public.room_dispatch_ack where dispatch_ref = v_ref_alpha;
      raise exception 'WR-000119: carr_writer DELETED an ack row';
    exception when insufficient_privilege then
      null;
    end;
    reset role;
  end;

  -- ---------------------------------------------------------------------
  -- 5. THE WIRE NEVER MOVED. public.partner_room_turn gained exactly the four
  -- turns this fixture appended and nothing else: no branch of the spine
  -- writes it, which is the whole of why design 1 was rejected.
  -- ---------------------------------------------------------------------
  select count(*) into c_turns2 from public.partner_room_turn;
  if c_turns2 <> c_turns + 4 then
    raise exception 'WR-000119: the wire row count moved by % and not by the four seeded turns',
      c_turns2 - c_turns;
  end if;
  if exists (select 1 from public.partner_room_turn
              where msg_id in (v_turn_alpha, v_turn_beta, v_turn_gamma, v_turn_orphan)
                and body not like 'dispatch %' and body <> 'a turn naming nobody') then
    raise exception 'WR-000119: a seeded wire row was rewritten by the spine';
  end if;
end $wr119_spine$;

rollback;

\echo 'WR-000119 dispatch spine: append-only by grant, one link per assignment and none without one, the hermes-pilot restriction inside the definer, a dangling ack refused by the reference, a second ack of one stage refused by the unique constraint, all four stages evidenced in one answer, proved against body_match, not_acknowledged against no_dispatch_spine per row, and the wire never written'
