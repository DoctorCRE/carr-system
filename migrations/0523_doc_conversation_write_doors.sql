-- WR-000114: the three Doc conversation write doors.
--
-- 0520 shipped the store with exactly two doors: an authority-only append and a
-- writer-bound read. Nothing could START a conversation, hand another partner
-- access to one, or rename it -- every one of those acts was performed by hand
-- against the relations. This file adds the three SECURITY DEFINER functions
-- that perform them, and their grants, AND NOTHING ELSE.
--
-- NO TRIGGER IS ADDED, AMENDED OR DROPPED. ops.doc_conversation_rows_immutable()
-- is attached by four triggers at 0520:78-85 and they name exactly two
-- relations, ops.doc_conversation_turn and ops.doc_conversation_title_revision.
-- The two relations this file writes -- the header and the access list -- carry
-- none of them, which 0520:70-72 says in its own words ("a rename moves the
-- header, a revocation stamps the grant row") and which 0520:155 has been
-- proving in production since the v30 release, because the shipped append
-- updates the header on every non-deduplicated turn. An amended trigger here
-- would be a widening of the append-only guarantee, not a fix, and would break
-- 0520's own header update.
--
-- NO NEW TABLE GRANT OF ANY KIND, and no change to the 0520:240-242 revoke: the
-- three functions are definers, so the runtime bundles stay unable to touch a
-- row directly.
--
-- NO TRANSACTION CONTROL. This file is the first member of the reviewed
-- ATOMIC_MIGRATION_GROUP 0523/0524 declared in tools/migrate.py.

do $wr114_preflight$
begin
  if not exists (select 1 from public.schema_migrations
    where filename = '0520_doc_conversation_store.sql'
      and sha256 = 'cdf0120133e4f575a5e661d447ba3d2a58c93dfc64019edd2c90fd2bff11e54f') then
    raise exception '0523 requires the exact 0520 Doc conversation store';
  end if;
  if to_regprocedure('ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)') is null then
    raise exception '0523 requires the 0520 Doc conversation append door';
  end if;
  if to_regprocedure('ops.doc_conversation_facts(uuid,text,integer,integer)') is null then
    raise exception '0523 requires the 0520 Doc conversation read door';
  end if;
end $wr114_preflight$;

-- -------------------------------------------------------------------------
-- 1. create_doc_conversation
--
-- THE ROW'S id IS THE IDEMPOTENCY KEY. The store's own precedent is a
-- caller-relayed unique natural key: the shipped append lets a caller supply
-- msg_id, dedupes with "on conflict (msg_id) do nothing", and refuses a
-- differing reuse (0520:128-149). The conversation's primary key is the same
-- kind of key one level up, so a replayed create is ONE row by construction.
--
-- There is NO creator check: creating one is the act that makes you the
-- creator. There is no parameter for an actor either -- it is derived from the
-- server-installed transaction context by ops.portfolio_writer_actor_id(),
-- exactly as the shipped append derives it at 0520:122.
-- -------------------------------------------------------------------------

create or replace function ops.create_doc_conversation(
  p_title text, p_visibility text, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_id uuid; v_version integer; v_visibility text;
        v_prior ops.doc_conversation%rowtype;
begin
  v_actor := ops.portfolio_writer_actor_id();
  if p_idempotency_key is null then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_idempotency_key_required');
  end if;
  v_visibility := coalesce(p_visibility, 'private');
  -- The table's own check at 0520:34 would refuse this anyway; refusing here
  -- gives the caller a reason_id instead of a constraint error.
  if v_visibility not in ('private', 'shared') then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_visibility_invalid');
  end if;

  insert into ops.doc_conversation(id, title, created_by_actor, visibility)
  values (p_idempotency_key, p_title, v_actor, v_visibility)
  on conflict (id) do nothing
  returning id, version into v_id, v_version;

  if v_id is null then
    select * into v_prior from ops.doc_conversation where id = p_idempotency_key;
    if v_prior.id is null then
      return jsonb_build_object('ok', false, 'reason_id', 'dedup_row_vanished');
    end if;
    -- The append's full-field replay comparison (0520:137-149): the same key
    -- offered with different content is a REUSE, not a duplicate.
    if v_prior.title is distinct from p_title
       or v_prior.visibility is distinct from v_visibility
       or v_prior.created_by_actor is distinct from v_actor then
      return jsonb_build_object('ok', false,
        'reason_id', 'doc_conversation_idempotency_key_reuse', 'id', p_idempotency_key);
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'id', v_prior.id,
      'version', v_prior.version, 'title', v_prior.title, 'visibility', v_prior.visibility,
      'created_by', v_prior.created_by_actor);
  end if;

  return jsonb_build_object('ok', true, 'deduplicated', false, 'id', v_id,
    'version', v_version, 'title', p_title, 'visibility', v_visibility,
    'created_by', v_actor);
end $$;

comment on function ops.create_doc_conversation(text,text,uuid) is
  'WR-000114: the only door that starts a Doc conversation. The creator is derived from the transaction context and the row id IS the idempotency key, so a replayed create is one row by construction.';

-- -------------------------------------------------------------------------
-- 2. share_doc_conversation -- grant and revoke, one function.
--
-- TWO REFUSAL SHAPES, DELIBERATELY. An actor who cannot see the conversation
-- at all gets 'doc_conversation_not_found', byte-identical to the read door's
-- single answer at 0520:192, so a stranger learns nothing. An actor who CAN
-- see it but is not the creator gets 'doc_conversation_creator_only' -- a
-- grantee already knows the conversation exists, so the distinct refusal
-- discloses nothing and is the honest answer the UI needs.
--
-- THE CREATOR CHECK IS RAISED HERE, INSIDE THE DEFINER, BEFORE ANY WRITE, for
-- grant and for revoke alike. A check in the JavaScript handler would be
-- bypassed entirely by a direct SQL call.
--
-- A REVOKE STAMPS revoked_at AND NEVER DELETES. 0520:189/:208/:214 filter
-- revoked_at three times, so a stamped row is already invisible to the read;
-- what stamping buys is a refusal that can be PROVED (the row is still there,
-- still unrevoked, after a refused attempt) and an audit that can still name
-- the grantor and the moment.
-- -------------------------------------------------------------------------

create or replace function ops.share_doc_conversation(
  p_conversation uuid, p_grantee_slug text, p_granted boolean, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_row ops.doc_conversation%rowtype; v_grantee uuid;
        v_existing boolean; v_remaining integer;
begin
  v_actor := ops.portfolio_writer_actor_id();

  select * into v_row from ops.doc_conversation where id = p_conversation;
  -- Absent, or invisible to this actor: one answer for both.
  if v_row.id is null
     or not (v_row.created_by_actor = v_actor
             or exists (select 1 from ops.doc_conversation_grant g
                         where g.conversation_id = v_row.id and g.grantee_actor = v_actor
                           and g.revoked_at is null)) then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_not_found');
  end if;
  -- Visible but not the creator. BEFORE ANY WRITE.
  if v_row.created_by_actor <> v_actor then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_creator_only');
  end if;

  select id into v_grantee from public.actor
   where slug = p_grantee_slug and active = true;
  if v_grantee is null then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_grantee_not_found');
  end if;
  -- The creator is already visible by 0520:186; a self-grant would put a
  -- meaningless row in effective_grants.
  if v_grantee = v_actor then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_grantee_is_creator');
  end if;

  v_existing := exists (select 1 from ops.doc_conversation_grant
                         where conversation_id = p_conversation and grantee_actor = v_grantee
                           and revoked_at is null);

  if p_granted then
    if v_existing then
      return jsonb_build_object('ok', true, 'already', true, 'granted', true,
        'conversation_id', p_conversation, 'grantee_slug', p_grantee_slug);
    end if;
    -- clock_timestamp(), NOT the column default. The primary key is
    -- (conversation_id, grantee_actor, granted_at) (0520:67) and now() is
    -- constant for a whole transaction, so grant -> revoke -> re-grant inside
    -- one transaction would collide on the key with the default.
    insert into ops.doc_conversation_grant(
      conversation_id, grantee_actor, granted_at, granted_by_actor)
    values (p_conversation, v_grantee, clock_timestamp(), v_actor);
    -- The header field the read door already returns (0520:218). Leaving it
    -- stale would make the app show "private" for a shared conversation. No
    -- version bump: sharing is not a rename, the caller never read a version to
    -- swap against, and bumping it would invalidate a rename in flight.
    update ops.doc_conversation
       set visibility = 'shared', updated_at = now()
     where id = p_conversation;
    return jsonb_build_object('ok', true, 'already', false, 'granted', true,
      'conversation_id', p_conversation, 'grantee_slug', p_grantee_slug);
  end if;

  update ops.doc_conversation_grant set revoked_at = now()
   where conversation_id = p_conversation and grantee_actor = v_grantee
     and revoked_at is null;
  if not found then
    -- Revoking what is not granted is a no-op, not an error.
    return jsonb_build_object('ok', true, 'already', true, 'granted', false,
      'conversation_id', p_conversation, 'grantee_slug', p_grantee_slug);
  end if;
  select count(*) into v_remaining from ops.doc_conversation_grant
   where conversation_id = p_conversation and revoked_at is null;
  update ops.doc_conversation
     set visibility = case when v_remaining = 0 then 'private' else 'shared' end,
         updated_at = now()
   where id = p_conversation;
  return jsonb_build_object('ok', true, 'already', false, 'granted', false,
    'conversation_id', p_conversation, 'grantee_slug', p_grantee_slug);
end $$;

comment on function ops.share_doc_conversation(uuid,text,boolean,uuid) is
  'WR-000114: the only door that widens or withdraws access to a Doc conversation. Creator-only, raised inside this body before any write; a withdrawal STAMPS revoked_at and never deletes, so the grantor and the moment survive for the audit.';

-- -------------------------------------------------------------------------
-- 3. rename_doc_conversation -- rename, pin/unpin and archive/unarchive.
--
-- ONE function for all three, because they are the same row, the same creator
-- check and the same compare-and-swap; three functions would cost three
-- ingresses and three ACL pairs for one act.
--
-- THE CREATOR CHECK IS RAISED HERE, INSIDE THE DEFINER, BEFORE ANY VERSION
-- COMPARISON AND BEFORE ANY WRITE, for rename, pin and archive alike -- so a
-- refused non-creator attempt leaves the row and its VERSION untouched, and the
-- refusal can never be an artifact of a stale version.
--
-- THE STEP-2 VERSION GUARD IS THE SUBTLE ONE. The title revision is appended
-- BEFORE the update, so it carries the PRIOR title. But the insert and the
-- update run in one runner-owned transaction, so a refused compare-and-swap
-- would otherwise leave an orphan revision -- in an IMMUTABLE table, which no
-- later statement can clean up. The insert therefore carries the SAME version
-- predicate the update carries.
-- -------------------------------------------------------------------------

create or replace function ops.rename_doc_conversation(
  p_conversation uuid, p_base_version integer, p_title text,
  p_pinned boolean, p_archived boolean, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_row ops.doc_conversation%rowtype; v_version integer;
begin
  v_actor := ops.portfolio_writer_actor_id();

  select * into v_row from ops.doc_conversation where id = p_conversation;
  if v_row.id is null
     or not (v_row.created_by_actor = v_actor
             or exists (select 1 from ops.doc_conversation_grant g
                         where g.conversation_id = v_row.id and g.grantee_actor = v_actor
                           and g.revoked_at is null)) then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_not_found');
  end if;
  -- BEFORE the version comparison and BEFORE any write.
  if v_row.created_by_actor <> v_actor then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_creator_only');
  end if;

  if p_title is null and p_pinned is null and p_archived is null then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_no_change_requested');
  end if;

  -- The PRIOR title, appended only when the title actually moves, and only
  -- under the same version predicate the update below carries.
  -- clock_timestamp() for the same primary-key reason as the grant (0520:43).
  insert into ops.doc_conversation_title_revision(conversation_id, title, at, by_actor)
  select id, title, clock_timestamp(), v_actor from ops.doc_conversation
   where id = p_conversation and version = p_base_version
     and p_title is not null and p_title is distinct from title;

  -- The compare-and-swap is a predicated update and NOT FOUND *is* the refusal.
  update ops.doc_conversation
     set title       = coalesce(p_title, title),
         pinned_at   = case when p_pinned   is null then pinned_at
                            when p_pinned   then coalesce(pinned_at, now()) else null end,
         archived_at = case when p_archived is null then archived_at
                            when p_archived then coalesce(archived_at, now()) else null end,
         version     = version + 1,
         updated_at  = now()
   where id = p_conversation and version = p_base_version
  returning version into v_version;
  if not found then
    return jsonb_build_object('ok', false, 'reason_id', 'version_conflict',
      'current_version', (select version from ops.doc_conversation where id = p_conversation));
  end if;

  select * into v_row from ops.doc_conversation where id = p_conversation;
  return jsonb_build_object('ok', true, 'id', p_conversation, 'version', v_version,
    'title', v_row.title, 'pinned', v_row.pinned_at is not null,
    'archived', v_row.archived_at is not null);
end $$;

comment on function ops.rename_doc_conversation(uuid,integer,text,boolean,boolean,uuid) is
  'WR-000114: the only door that renames, pins, unpins, archives or unarchives a Doc conversation. Creator-only, raised inside this body before any version comparison and before any write; the prior title is appended under the same version predicate the compare-and-swap carries, so a refused rename leaves no orphan revision.';

-- -------------------------------------------------------------------------
-- Grants. carr_writer AND carr_authority, exactly as ops.doc_conversation_facts
-- is granted at 0520:249-252: the app must call these as the signed-in partner,
-- which arrives on the writer connection. The argument types are spelled in
-- full in every revoke and every grant -- a stale arity revokes nothing and
-- would leave PUBLIC execute on a security definer.
-- -------------------------------------------------------------------------

revoke all on function ops.create_doc_conversation(text,text,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.create_doc_conversation(text,text,uuid)
  to carr_writer,carr_authority;

revoke all on function ops.share_doc_conversation(uuid,text,boolean,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.share_doc_conversation(uuid,text,boolean,uuid)
  to carr_writer,carr_authority;

revoke all on function ops.rename_doc_conversation(uuid,integer,text,boolean,boolean,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.rename_doc_conversation(uuid,integer,text,boolean,boolean,uuid)
  to carr_writer,carr_authority;
