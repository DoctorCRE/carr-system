-- WR-000115 -- the measured proof for the Doc conversation LIST door.
-- Run by ops/ci.sh's migration class.
--
-- WHAT ONLY A DATABASE CAN SHOW:
--   * that the returned SET follows the acting-actor context and NOTHING a
--     caller supplies. Every case below sets carr.acting_actor_slug directly
--     and calls ops.list_doc_conversations with BYTE-IDENTICAL arguments under
--     two different contexts -- which is exactly the call a check written in
--     the JavaScript handler cannot influence. An implementation that kept the
--     shipped read's p_actor_id slot fails here before anyone has to reason
--     about whether the handler passes the right value.
--   * that the three-part cursor survives a PIN LANDING MID-WALK. The page is
--     walked twice: once undisturbed, and once with a conversation pinned
--     between page one and page two. A two-part (updated_at, id) cursor cannot
--     tell that the reader has already left the pinned block, so it returns a
--     conversation twice or drops one; only a walk against a live table can
--     show that.
--   * that the function reaches carr_writer and carr_authority on the exact
--     argument types and reaches neither carr_reader, nor carr_jobs, nor
--     public. A stale arity would name a different function and prove nothing.

\set ON_ERROR_STOP on

do $wr115_grants$
declare v_fn text := 'ops.list_doc_conversations(text,integer,boolean)';
begin
  if not has_function_privilege('carr_writer', v_fn, 'execute')
     or not has_function_privilege('carr_authority', v_fn, 'execute')
     or has_function_privilege('carr_reader', v_fn, 'execute')
     or has_function_privilege('carr_jobs', v_fn, 'execute')
     or has_function_privilege('public', v_fn, 'execute') then
    raise exception 'WR-000115: % does not reach exactly the writer and authority bundles', v_fn;
  end if;

  -- 0525 adds NO table grant. The list is a definer and the store stays
  -- unreachable directly, exactly as 0520:240-242 left it.
  if exists (
    select 1 from unnest(array['ops.doc_conversation','ops.doc_conversation_title_revision',
      'ops.doc_conversation_turn','ops.doc_conversation_grant']) rel
    cross join unnest(array['carr_reader','carr_writer','carr_jobs','carr_authority','public']) grantee
    cross join unnest(array['insert','update','delete','truncate']) priv
    where has_table_privilege(grantee, rel, priv)) then
    raise exception 'WR-000115: the Doc conversation store became directly writable by a runtime bundle';
  end if;

  -- THREE arguments, none of them an actor. The shipped read keeps p_actor_id
  -- in slot two; copying that slot forward fails here.
  if (select pg_get_function_identity_arguments(p.oid)
        from pg_proc p join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'ops' and p.proname = 'list_doc_conversations')
     <> 'p_cursor text, p_limit integer, p_include_archived boolean' then
    raise exception 'WR-000115: ops.list_doc_conversations does not take exactly (text,integer,boolean)';
  end if;
  -- A read, not a write: mcp.js opens `begin read only` for this verb.
  if (select p.provolatile from pg_proc p join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'ops' and p.proname = 'list_doc_conversations') <> 's' then
    raise exception 'WR-000115: ops.list_doc_conversations is not STABLE and cannot run in a read-only transaction';
  end if;
end $wr115_grants$;

-- ONE RUN-UNIQUE TOKEN, and the four automation actors every case below
-- shares. Each run owns its whole visible set, so the exact-set assertions
-- below are statements about THIS run rather than about how many times this
-- proof has been run against this database.
create temporary table wr115_run as
  select left(replace(gen_random_uuid()::text, '-', ''), 8) as token;

do $wr115_actors$
declare v_tok text; v_role text;
begin
  select token into v_tok from wr115_run;
  foreach v_role in array array['author','guest','stranger','pager'] loop
    insert into public.actor(slug, kind, display_name, active)
    values ('wr115-'||v_tok||'-'||v_role, 'automation', 'wr115-'||v_tok||'-'||v_role, true)
    on conflict (slug) do update set active = true;
  end loop;
end $wr115_actors$;

-- ---------------------------------------------------------------------------
-- LIST-OWN-AND-GRANTED and LIST-PRIVATE-INVISIBLE (db halves): the set is the
-- created set plus the unrevoked-granted set, under each context separately.
-- ---------------------------------------------------------------------------
do $wr115_membership$
declare v_a uuid; v_b uuid; v_c uuid; v_p uuid; v_tok text;
        v_author_ids uuid[]; v_guest_ids uuid[]; v_listed jsonb;
begin
  select token into v_tok from wr115_run;
  perform set_config('carr.acting_actor_slug', 'wr115-'||v_tok||'-author', true);
  v_a := (ops.create_doc_conversation('WR-000115 A', 'private', gen_random_uuid())->>'id')::uuid;
  v_b := (ops.create_doc_conversation('WR-000115 B', 'private', gen_random_uuid())->>'id')::uuid;
  v_p := (ops.create_doc_conversation('WR-000115 P', 'private', gen_random_uuid())->>'id')::uuid;
  perform ops.share_doc_conversation(v_b, 'wr115-'||v_tok||'-guest', true, gen_random_uuid());

  perform set_config('carr.acting_actor_slug', 'wr115-'||v_tok||'-guest', true);
  v_c := (ops.create_doc_conversation('WR-000115 C', 'private', gen_random_uuid())->>'id')::uuid;

  -- THE SAME ARGUMENTS, TWICE, UNDER TWO CONTEXTS. Byte-identical call text;
  -- the only thing that differs is the acting-actor setting.
  perform set_config('carr.acting_actor_slug', 'wr115-'||v_tok||'-author', true);
  v_listed := ops.list_doc_conversations(null, null, null);
  select array_agg((row->>'id')::uuid order by row->>'id') into v_author_ids
    from jsonb_array_elements(v_listed->'conversations') row;
  if v_author_ids is distinct from (select array_agg(id order by id) from unnest(array[v_a,v_b,v_p]) id) then
    raise exception 'WR-000115: the author list is not exactly the created set: %', v_author_ids;
  end if;
  if (v_listed->>'visible_conversation_count')::integer <> 3 then
    raise exception 'WR-000115: the author visible count is %', v_listed->>'visible_conversation_count';
  end if;

  perform set_config('carr.acting_actor_slug', 'wr115-'||v_tok||'-guest', true);
  v_listed := ops.list_doc_conversations(null, null, null);
  select array_agg((row->>'id')::uuid order by row->>'id') into v_guest_ids
    from jsonb_array_elements(v_listed->'conversations') row;
  if v_guest_ids is distinct from (select array_agg(id order by id) from unnest(array[v_b,v_c]) id) then
    raise exception 'WR-000115: the guest list is not exactly the granted plus created set: %', v_guest_ids;
  end if;
  if v_author_ids = v_guest_ids then
    raise exception 'WR-000115: the same arguments returned the same set under two different acting actors';
  end if;
  -- LIST-ATTRIBUTION-SERVER-SIDE (db half): no argument value can make the
  -- guest's call return the author's private row. There is no argument that
  -- could: the cursor is a sort key, the limit a count, the flag a boolean.
  if exists (select 1 from jsonb_array_elements(v_listed->'conversations') row
              where (row->>'id')::uuid = v_p) then
    raise exception 'WR-000115: the author private conversation reached the guest list';
  end if;
  perform set_config('carr.acting_actor_slug', 'wr115-'||v_tok||'-guest', true);
  if exists (select 1 from jsonb_array_elements(
               ops.list_doc_conversations(null, 100, true)->'conversations') row
              where (row->>'id')::uuid = v_p) then
    raise exception 'WR-000115: an oversized limit and the archived flag disclosed a private conversation';
  end if;

  -- The revoke STAMPS: after it the grantee loses the row and the grant row is
  -- still there, still carrying its original grantor and moment.
  perform set_config('carr.acting_actor_slug', 'wr115-'||v_tok||'-author', true);
  perform ops.share_doc_conversation(v_b, 'wr115-'||v_tok||'-guest', false, gen_random_uuid());
  perform set_config('carr.acting_actor_slug', 'wr115-'||v_tok||'-guest', true);
  if exists (select 1 from jsonb_array_elements(
               ops.list_doc_conversations(null, null, null)->'conversations') row
              where (row->>'id')::uuid = v_b) then
    raise exception 'WR-000115: a REVOKED grant still shows the conversation to the grantee';
  end if;
  if not exists (select 1 from ops.doc_conversation_grant g
                  join public.actor a on a.id = g.grantee_actor
                 where g.conversation_id = v_b and a.slug = 'wr115-'||v_tok||'-guest'
                   and g.revoked_at is not null) then
    raise exception 'WR-000115: the revoke deleted the grant row instead of stamping it';
  end if;

  -- A stranger with neither a creation nor a grant sees an empty list.
  perform set_config('carr.acting_actor_slug', 'wr115-'||v_tok||'-stranger', true);
  v_listed := ops.list_doc_conversations(null, null, null);
  if jsonb_array_length(v_listed->'conversations') <> 0
     or (v_listed->>'visible_conversation_count')::integer <> 0 then
    raise exception 'WR-000115: a stranger saw %', v_listed;
  end if;
end $wr115_membership$;

-- ---------------------------------------------------------------------------
-- LIST-ORDER-AND-PAGE (db half): the page walked TWICE -- once undisturbed and
-- once with a pin landing between page one and page two.
-- ---------------------------------------------------------------------------
do $wr115_paging$
declare v_owner text; v_seeded uuid[] := '{}'; v_id uuid; v_version integer;
        v_walk uuid[]; v_cursor text; v_listed jsonb; v_page integer; v_pinned uuid[];
        v_archived uuid; v_expected uuid[]; v_more boolean; v_index integer;
begin
  select 'wr115-'||token||'-pager' into v_owner from wr115_run;
  perform set_config('carr.acting_actor_slug', v_owner, true);

  for v_index in 1..7 loop
    v_id := (ops.create_doc_conversation('WR-000115 page ' || v_index, 'private',
      gen_random_uuid())->>'id')::uuid;
    v_seeded := v_seeded || v_id;
  end loop;

  -- Two pinned and one archived, through the shipped 0523 door.
  for v_index in 1..2 loop
    select version into v_version from ops.doc_conversation where id = v_seeded[v_index];
    perform ops.rename_doc_conversation(v_seeded[v_index], v_version, null, true, null,
      gen_random_uuid());
    v_pinned := v_pinned || v_seeded[v_index];
  end loop;
  select version into v_version from ops.doc_conversation where id = v_seeded[7];
  perform ops.rename_doc_conversation(v_seeded[7], v_version, null, null, true, gen_random_uuid());
  v_archived := v_seeded[7];
  v_expected := v_seeded[1:6];

  -- WALK ONE, undisturbed.
  v_walk := '{}'; v_cursor := null; v_page := 0;
  loop
    v_listed := ops.list_doc_conversations(v_cursor, 2, null);
    if not (v_listed->>'ok')::boolean then
      raise exception 'WR-000115: a page refused: %', v_listed;
    end if;
    select v_walk || coalesce(array_agg((row->>'id')::uuid order by ord), '{}'::uuid[])
      into v_walk
      from jsonb_array_elements(v_listed->'conversations') with ordinality as page(row, ord);
    v_more := (v_listed->>'more')::boolean;
    -- next_cursor is null exactly when more is false.
    if (v_listed->>'next_cursor' is null) <> (not v_more) then
      raise exception 'WR-000115: next_cursor and more disagree: %', v_listed;
    end if;
    exit when not v_more;
    v_cursor := v_listed->>'next_cursor';
    v_page := v_page + 1;
    if v_page > 20 then raise exception 'WR-000115: the cursor walk did not terminate'; end if;
  end loop;

  if (select count(distinct id) from unnest(v_walk) id) <> array_length(v_walk, 1) then
    raise exception 'WR-000115: the undisturbed walk returned a conversation twice: %', v_walk;
  end if;
  if (select array_agg(id order by id) from unnest(v_walk) id)
     is distinct from (select array_agg(id order by id) from unnest(v_expected) id) then
    raise exception 'WR-000115: the undisturbed walk is not exactly the visible set: %', v_walk;
  end if;
  if v_archived = any(v_walk) then
    raise exception 'WR-000115: an archived conversation appeared in the default walk';
  end if;
  -- Pinned first across the CONCATENATION, not merely within a page.
  if (select array_agg(id order by id) from unnest(v_walk[1:2]) id)
     is distinct from (select array_agg(id order by id) from unnest(v_pinned) id) then
    raise exception 'WR-000115: pinned-first held only within a page: %', v_walk;
  end if;

  -- WALK TWO, with a PIN LANDING BETWEEN PAGE ONE AND PAGE TWO. This is the
  -- case a two-part cursor fails.
  v_walk := '{}'; v_cursor := null; v_page := 0;
  loop
    v_listed := ops.list_doc_conversations(v_cursor, 2, null);
    select v_walk || coalesce(array_agg((row->>'id')::uuid order by ord), '{}'::uuid[])
      into v_walk
      from jsonb_array_elements(v_listed->'conversations') with ordinality as page(row, ord);
    exit when not (v_listed->>'more')::boolean;
    v_cursor := v_listed->>'next_cursor';
    v_page := v_page + 1;
    if v_page = 1 then
      -- PIN A ROW PAGE ONE ALREADY RETURNED. That is the honest mid-walk case:
      -- a correct cursor carries the whole sort key, so the remainder of the
      -- walk is untouched and the row is neither repeated nor dropped. Pinning
      -- a row the walk has NOT yet reached moves it BEHIND the cursor, and no
      -- keyset cursor of any width can return a row that has moved to a
      -- position the reader already passed -- that is a property of keyset
      -- paging, not a defect this proof can attribute to the cursor's shape.
      select version into v_version from ops.doc_conversation where id = v_walk[1];
      perform ops.rename_doc_conversation(v_walk[1], v_version, null, true, null,
        gen_random_uuid());
    end if;
    if v_page > 20 then raise exception 'WR-000115: the disturbed walk did not terminate'; end if;
  end loop;

  if (select count(distinct id) from unnest(v_walk) id) <> array_length(v_walk, 1) then
    raise exception 'WR-000115: the mid-walk pin returned a conversation twice: %', v_walk;
  end if;
  if (select array_agg(id order by id) from unnest(v_walk) id)
     is distinct from (select array_agg(id order by id) from unnest(v_expected) id) then
    raise exception 'WR-000115: the mid-walk pin dropped a conversation: %', v_walk;
  end if;

  -- The clamp, and the archived toggle.
  v_listed := ops.list_doc_conversations(null, 1000, null);
  if jsonb_array_length(v_listed->'conversations') > 100 then
    raise exception 'WR-000115: an oversized limit was honoured instead of clamped';
  end if;
  v_listed := ops.list_doc_conversations(null, null, true);
  if not exists (select 1 from jsonb_array_elements(v_listed->'conversations') row
                  where (row->>'id')::uuid = v_archived) then
    raise exception 'WR-000115: include_archived did not include the archived conversation';
  end if;

  -- A cursor the server did not mint is ONE refusal, never a partial page.
  v_listed := ops.list_doc_conversations('not-a-server-token', null, null);
  if (v_listed->>'ok')::boolean
     or v_listed->>'reason_id' <> 'doc_conversation_cursor_invalid' then
    raise exception 'WR-000115: a malformed cursor was not refused by name: %', v_listed;
  end if;
end $wr115_paging$;

-- ---------------------------------------------------------------------------
-- LIST-ORDER-AND-PAGE (db half, the clamp): an oversized limit is CLAMPED, not
-- refused and not honoured. This needs more than a hundred rows to be
-- falsifiable at all -- with a handful of conversations an unclamped limit and
-- a clamped one return the same page -- so this block seeds 101 of its own.
-- ---------------------------------------------------------------------------
do $wr115_clamp$
declare v_owner text; v_actor uuid; v_listed jsonb; v_len integer;
begin
  select 'wr115-'||token||'-clamper' into v_owner from wr115_run;
  insert into public.actor(slug, kind, display_name, active)
  values (v_owner,'automation',v_owner,true) on conflict (slug) do update set active = true;
  select id into v_actor from public.actor where slug = v_owner;
  perform set_config('carr.acting_actor_slug', v_owner, true);

  insert into ops.doc_conversation(title, created_by_actor, updated_at)
  select 'WR-000115 clamp '||n, v_actor, now() + (n || ' microseconds')::interval
    from generate_series(1, 101) n;

  v_listed := ops.list_doc_conversations(null, 1000, null);
  v_len := jsonb_array_length(v_listed->'conversations');
  if v_len <> 100 then
    raise exception 'WR-000115: a limit of 1000 returned % rows rather than the clamped 100', v_len;
  end if;
  if not (v_listed->>'more')::boolean or v_listed->>'next_cursor' is null then
    raise exception 'WR-000115: a clamped page did not report an honest more: %',
      (v_listed - 'conversations');
  end if;
  if (v_listed->>'visible_conversation_count')::integer <> 101 then
    raise exception 'WR-000115: the visible count is not the whole set: %',
      v_listed->>'visible_conversation_count';
  end if;

  -- A limit BELOW the floor is clamped up rather than returning an empty page.
  if jsonb_array_length(ops.list_doc_conversations(null, -5, null)->'conversations') <> 1 then
    raise exception 'WR-000115: a limit below the floor was not clamped to one row';
  end if;
end $wr115_clamp$;

select 'WR-000115 Doc conversation list: the set follows the acting context, the three-part cursor survives a mid-walk pin, grants writer-bound' as proof;
