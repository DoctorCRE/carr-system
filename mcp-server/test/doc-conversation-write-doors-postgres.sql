-- WR-000114 -- the measured proof for the three Doc conversation write doors.
-- Run by ops/ci.sh's migration class.
--
-- WHAT ONLY A DATABASE CAN SHOW:
--   * that the creator rule is raised INSIDE the security-definer body. Every
--     creator case below calls the function DIRECTLY under a second acting-actor
--     context -- which is exactly the call that bypasses a check written in the
--     JavaScript handler. A handler-only implementation passes the node suite
--     and fails here.
--   * that a withdrawal STAMPS revoked_at and never deletes: after a refused
--     attempt the row is still there, still unrevoked, with its original
--     grantor and its original moment. Under deletion the only available
--     assertion is that a row is absent -- which is also what a successful
--     revoke looks like and what a grant that never happened looks like.
--   * that the three functions reach carr_writer and carr_authority and reach
--     neither carr_reader nor public, on the exact argument types.

\set ON_ERROR_STOP on

do $wr114_grants$
declare v_bad text;
begin
  -- The three write doors: carr_writer AND carr_authority, and nobody else.
  -- The argument types are spelled in full: a stale arity would name a
  -- different function and quietly prove nothing.
  for v_bad in
    select fn from unnest(array[
      'ops.create_doc_conversation(text,text,uuid)',
      'ops.share_doc_conversation(uuid,text,boolean,uuid)',
      'ops.rename_doc_conversation(uuid,integer,text,boolean,boolean,uuid)']) fn
    where not has_function_privilege('carr_writer', fn, 'execute')
       or not has_function_privilege('carr_authority', fn, 'execute')
       or has_function_privilege('carr_reader', fn, 'execute')
       or has_function_privilege('public', fn, 'execute')
  loop
    raise exception 'WR-000114: % does not reach exactly the writer and authority bundles', v_bad;
  end loop;

  -- 0523 adds NO table grant. No runtime bundle may change a row directly:
  -- the three functions are definers and are the only doors.
  for v_bad in
    select rel from unnest(array['ops.doc_conversation','ops.doc_conversation_title_revision',
      'ops.doc_conversation_turn','ops.doc_conversation_grant']) rel
    cross join unnest(array['carr_reader','carr_writer','carr_jobs','carr_authority','public']) grantee
    cross join unnest(array['insert','update','delete','truncate']) priv
    where has_table_privilege(grantee, rel, priv)
  loop
    raise exception 'WR-000114: % became directly writable by a runtime bundle', v_bad;
  end loop;
end $wr114_grants$;

-- The two automation actors every case below shares.
do $wr114_actors$
begin
  insert into public.actor(slug, kind, display_name, active)
  values ('wr114-author','automation','wr114-author',true)
  on conflict (slug) do update set active = true;
  insert into public.actor(slug, kind, display_name, active)
  values ('wr114-guest','automation','wr114-guest',true)
  on conflict (slug) do update set active = true;
  insert into public.actor(slug, kind, display_name, active)
  values ('wr114-stranger','automation','wr114-stranger',true)
  on conflict (slug) do update set active = true;
end $wr114_actors$;

-- ---------------------------------------------------------------------------
-- DOC-CREATE-READBACK (db half): the row id IS the idempotency key, so a
-- replayed create is ONE row counted in the STORE, not in any envelope.
-- ---------------------------------------------------------------------------
do $wr114_create$
declare v_key uuid := gen_random_uuid(); v_first jsonb; v_replay jsonb; v_reuse jsonb;
        v_rows integer; v_actor uuid;
        v_title text := 'WR-000114: a title, with punctuation -- and a tail';
begin
  perform set_config('carr.acting_actor_slug', 'wr114-author', true);
  select id into v_actor from public.actor where slug = 'wr114-author';

  v_first := ops.create_doc_conversation(v_title, 'private', v_key);
  if not (v_first->>'ok')::boolean or (v_first->>'deduplicated')::boolean then
    raise exception 'WR-000114: the first create did not land: %', v_first;
  end if;
  if (v_first->>'id')::uuid <> v_key then
    raise exception 'WR-000114: the row id is not the idempotency key: %', v_first;
  end if;
  if v_first->>'title' <> v_title then
    raise exception 'WR-000114: the title did not read back byte-identical: %', v_first;
  end if;
  if (v_first->>'created_by')::uuid <> v_actor then
    raise exception 'WR-000114: the creator is not the derived actor: %', v_first;
  end if;

  -- The same key again. There is no envelope here at all, so this is the store
  -- path and nothing else.
  v_replay := ops.create_doc_conversation(v_title, 'private', v_key);
  if not (v_replay->>'ok')::boolean or not (v_replay->>'deduplicated')::boolean then
    raise exception 'WR-000114: a replayed key was not deduplicated: %', v_replay;
  end if;
  select count(*) into v_rows from ops.doc_conversation where id = v_key;
  if v_rows <> 1 then
    raise exception 'WR-000114: % rows stored for one replayed key', v_rows;
  end if;

  -- The same key with a different title is a REUSE, not a duplicate.
  v_reuse := ops.create_doc_conversation('a different title', 'private', v_key);
  if (v_reuse->>'ok')::boolean
     or v_reuse->>'reason_id' <> 'doc_conversation_idempotency_key_reuse' then
    raise exception 'WR-000114: a changed title under a reused key was accepted: %', v_reuse;
  end if;
end $wr114_create$;

-- ---------------------------------------------------------------------------
-- DOC-SHARE-TOGGLE (db half): a grant lands with the STATEMENT clock, a revoke
-- STAMPS, and a grant -> revoke -> re-grant sequence inside ONE transaction
-- does not collide on the primary key. now() is constant for a whole
-- transaction, so the column default would collide here.
-- ---------------------------------------------------------------------------
do $wr114_share$
declare v_conversation uuid := gen_random_uuid(); v_guest uuid; v_grants integer;
        v_unrevoked integer; v_visibility text; v_result jsonb;
begin
  perform set_config('carr.acting_actor_slug', 'wr114-author', true);
  select id into v_guest from public.actor where slug = 'wr114-guest';
  perform ops.create_doc_conversation('WR-000114 share proof', 'private', v_conversation);

  v_result := ops.share_doc_conversation(v_conversation, 'wr114-guest', true, gen_random_uuid());
  if not (v_result->>'ok')::boolean then
    raise exception 'WR-000114: the grant was refused: %', v_result;
  end if;
  select visibility into v_visibility from ops.doc_conversation where id = v_conversation;
  if v_visibility <> 'shared' then
    raise exception 'WR-000114: the header visibility did not move to shared: %', v_visibility;
  end if;

  v_result := ops.share_doc_conversation(v_conversation, 'wr114-guest', false, gen_random_uuid());
  if not (v_result->>'ok')::boolean then
    raise exception 'WR-000114: the revoke was refused: %', v_result;
  end if;
  -- STAMPED, NOT DELETED. The row survives with its grantor and its moment.
  select count(*) into v_grants from ops.doc_conversation_grant
   where conversation_id = v_conversation and grantee_actor = v_guest;
  if v_grants <> 1 then
    raise exception 'WR-000114: a revoke deleted the grant row instead of stamping it';
  end if;
  if not exists (select 1 from ops.doc_conversation_grant
                  where conversation_id = v_conversation and grantee_actor = v_guest
                    and revoked_at is not null and granted_by_actor is not null) then
    raise exception 'WR-000114: the revoked grant lost its stamp or its grantor';
  end if;
  select visibility into v_visibility from ops.doc_conversation where id = v_conversation;
  if v_visibility <> 'private' then
    raise exception 'WR-000114: the last revoke did not return the header to private: %', v_visibility;
  end if;

  -- The RE-GRANT, in the SAME transaction. With the column default this insert
  -- collides on (conversation_id, grantee_actor, granted_at).
  v_result := ops.share_doc_conversation(v_conversation, 'wr114-guest', true, gen_random_uuid());
  if not (v_result->>'ok')::boolean then
    raise exception 'WR-000114: the re-grant collided on the primary key: %', v_result;
  end if;
  select count(*) into v_grants from ops.doc_conversation_grant
   where conversation_id = v_conversation and grantee_actor = v_guest;
  select count(*) into v_unrevoked from ops.doc_conversation_grant
   where conversation_id = v_conversation and grantee_actor = v_guest and revoked_at is null;
  if v_grants <> 2 or v_unrevoked <> 1 then
    raise exception 'WR-000114: a re-grant left % rows, % unrevoked', v_grants, v_unrevoked;
  end if;
end $wr114_share$;

-- ---------------------------------------------------------------------------
-- DOC-CREATOR-ONLY -- FIVE SEPARATELY NAMED CASES.
--
-- Each one is called DIRECTLY against the function under a SECOND acting-actor
-- context, asserts the NAMED refusal, and asserts the row unchanged afterwards
-- INCLUDING ITS VERSION. A creator check written into share and omitted from
-- rename, pin or archive fails a case that names it; none of these four is
-- implied by another.
-- ---------------------------------------------------------------------------

-- DOC-CREATOR-ONLY/share
do $wr114_creator_share$
declare v_conversation uuid := gen_random_uuid(); v_result jsonb;
        v_version_before integer; v_version_after integer; v_grants_before integer;
        v_grants_after integer;
begin
  perform set_config('carr.acting_actor_slug', 'wr114-author', true);
  perform ops.create_doc_conversation('WR-000114 creator-only share', 'private', v_conversation);
  perform ops.share_doc_conversation(v_conversation, 'wr114-guest', true, gen_random_uuid());
  select version into v_version_before from ops.doc_conversation where id = v_conversation;
  select count(*) into v_grants_before from ops.doc_conversation_grant
   where conversation_id = v_conversation;

  -- The GUEST is a member, so it learns the conversation exists -- and is still
  -- refused, by name.
  perform set_config('carr.acting_actor_slug', 'wr114-guest', true);
  v_result := ops.share_doc_conversation(v_conversation, 'wr114-stranger', true, gen_random_uuid());
  if (v_result->>'ok')::boolean or v_result->>'reason_id' <> 'doc_conversation_creator_only' then
    raise exception 'DOC-CREATOR-ONLY/share: a non-creator grant was not refused by name: %', v_result;
  end if;

  -- A STRANGER gets the absent-conversation answer instead, and learns nothing.
  perform set_config('carr.acting_actor_slug', 'wr114-stranger', true);
  v_result := ops.share_doc_conversation(v_conversation, 'wr114-guest', true, gen_random_uuid());
  if (v_result->>'ok')::boolean or v_result->>'reason_id' <> 'doc_conversation_not_found' then
    raise exception 'DOC-CREATOR-ONLY/share: a stranger was answered distinguishably: %', v_result;
  end if;

  select version into v_version_after from ops.doc_conversation where id = v_conversation;
  select count(*) into v_grants_after from ops.doc_conversation_grant
   where conversation_id = v_conversation;
  if v_version_after <> v_version_before or v_grants_after <> v_grants_before then
    raise exception 'DOC-CREATOR-ONLY/share: the refused attempts moved the row (version %->%, grants %->%)',
      v_version_before, v_version_after, v_grants_before, v_grants_after;
  end if;
end $wr114_creator_share$;

-- DOC-CREATOR-ONLY/revoke
do $wr114_creator_revoke$
declare v_conversation uuid := gen_random_uuid(); v_result jsonb; v_guest uuid;
        v_version_before integer; v_version_after integer;
        v_granted_at_before timestamptz; v_granted_at_after timestamptz;
        v_grantor_before uuid; v_grantor_after uuid; v_revoked timestamptz;
begin
  perform set_config('carr.acting_actor_slug', 'wr114-author', true);
  select id into v_guest from public.actor where slug = 'wr114-guest';
  perform ops.create_doc_conversation('WR-000114 creator-only revoke', 'private', v_conversation);
  perform ops.share_doc_conversation(v_conversation, 'wr114-guest', true, gen_random_uuid());
  select version into v_version_before from ops.doc_conversation where id = v_conversation;
  select granted_at, granted_by_actor into v_granted_at_before, v_grantor_before
    from ops.doc_conversation_grant
   where conversation_id = v_conversation and grantee_actor = v_guest;

  -- The grantee tries to withdraw its OWN grant. Refused, by name.
  perform set_config('carr.acting_actor_slug', 'wr114-guest', true);
  v_result := ops.share_doc_conversation(v_conversation, 'wr114-guest', false, gen_random_uuid());
  if (v_result->>'ok')::boolean or v_result->>'reason_id' <> 'doc_conversation_creator_only' then
    raise exception 'DOC-CREATOR-ONLY/revoke: a non-creator revoke was not refused by name: %', v_result;
  end if;

  select granted_at, granted_by_actor, revoked_at
    into v_granted_at_after, v_grantor_after, v_revoked
    from ops.doc_conversation_grant
   where conversation_id = v_conversation and grantee_actor = v_guest;
  select version into v_version_after from ops.doc_conversation where id = v_conversation;
  -- STILL UNREVOKED, SAME GRANTOR, SAME MOMENT. This is the assertion deletion
  -- could not make: an absent row would be indistinguishable from success.
  if v_revoked is not null then
    raise exception 'DOC-CREATOR-ONLY/revoke: the refused revoke stamped the row anyway';
  end if;
  if v_granted_at_after <> v_granted_at_before or v_grantor_after <> v_grantor_before then
    raise exception 'DOC-CREATOR-ONLY/revoke: the grant row moved under a refused attempt';
  end if;
  if v_version_after <> v_version_before then
    raise exception 'DOC-CREATOR-ONLY/revoke: the header version moved from % to %',
      v_version_before, v_version_after;
  end if;
end $wr114_creator_revoke$;

-- DOC-CREATOR-ONLY/rename
do $wr114_creator_rename$
declare v_conversation uuid := gen_random_uuid(); v_result jsonb;
        v_version_before integer; v_version_after integer;
        v_title_before text; v_title_after text; v_revisions integer;
begin
  perform set_config('carr.acting_actor_slug', 'wr114-author', true);
  perform ops.create_doc_conversation('WR-000114 creator-only rename', 'private', v_conversation);
  perform ops.share_doc_conversation(v_conversation, 'wr114-guest', true, gen_random_uuid());
  select version, title into v_version_before, v_title_before
    from ops.doc_conversation where id = v_conversation;

  -- A CORRECT base version, so the refusal cannot be an artifact of staleness.
  perform set_config('carr.acting_actor_slug', 'wr114-guest', true);
  v_result := ops.rename_doc_conversation(v_conversation, v_version_before,
    'renamed by a non-creator', null, null, gen_random_uuid());
  if (v_result->>'ok')::boolean or v_result->>'reason_id' <> 'doc_conversation_creator_only' then
    raise exception 'DOC-CREATOR-ONLY/rename: a non-creator rename was not refused by name: %', v_result;
  end if;

  select version, title into v_version_after, v_title_after
    from ops.doc_conversation where id = v_conversation;
  select count(*) into v_revisions from ops.doc_conversation_title_revision
   where conversation_id = v_conversation;
  if v_version_after <> v_version_before or v_title_after <> v_title_before then
    raise exception 'DOC-CREATOR-ONLY/rename: the header moved (version %->%, title %->%)',
      v_version_before, v_version_after, v_title_before, v_title_after;
  end if;
  if v_revisions <> 0 then
    raise exception 'DOC-CREATOR-ONLY/rename: % title revisions were appended for a refused rename', v_revisions;
  end if;
end $wr114_creator_rename$;

-- DOC-CREATOR-ONLY/pin
do $wr114_creator_pin$
declare v_conversation uuid := gen_random_uuid(); v_result jsonb;
        v_version_before integer; v_version_after integer;
        v_pinned_before timestamptz; v_pinned_after timestamptz;
begin
  perform set_config('carr.acting_actor_slug', 'wr114-author', true);
  perform ops.create_doc_conversation('WR-000114 creator-only pin', 'private', v_conversation);
  perform ops.share_doc_conversation(v_conversation, 'wr114-guest', true, gen_random_uuid());
  select version, pinned_at into v_version_before, v_pinned_before
    from ops.doc_conversation where id = v_conversation;

  perform set_config('carr.acting_actor_slug', 'wr114-guest', true);
  v_result := ops.rename_doc_conversation(v_conversation, v_version_before,
    null, true, null, gen_random_uuid());
  if (v_result->>'ok')::boolean or v_result->>'reason_id' <> 'doc_conversation_creator_only' then
    raise exception 'DOC-CREATOR-ONLY/pin: a non-creator pin was not refused by name: %', v_result;
  end if;

  select version, pinned_at into v_version_after, v_pinned_after
    from ops.doc_conversation where id = v_conversation;
  if v_version_after <> v_version_before or v_pinned_after is distinct from v_pinned_before then
    raise exception 'DOC-CREATOR-ONLY/pin: the pin field or the version moved under a refused attempt';
  end if;
end $wr114_creator_pin$;

-- DOC-CREATOR-ONLY/archive
do $wr114_creator_archive$
declare v_conversation uuid := gen_random_uuid(); v_result jsonb;
        v_version_before integer; v_version_after integer;
        v_archived_before timestamptz; v_archived_after timestamptz;
begin
  perform set_config('carr.acting_actor_slug', 'wr114-author', true);
  perform ops.create_doc_conversation('WR-000114 creator-only archive', 'private', v_conversation);
  perform ops.share_doc_conversation(v_conversation, 'wr114-guest', true, gen_random_uuid());
  select version, archived_at into v_version_before, v_archived_before
    from ops.doc_conversation where id = v_conversation;

  perform set_config('carr.acting_actor_slug', 'wr114-guest', true);
  v_result := ops.rename_doc_conversation(v_conversation, v_version_before,
    null, null, true, gen_random_uuid());
  if (v_result->>'ok')::boolean or v_result->>'reason_id' <> 'doc_conversation_creator_only' then
    raise exception 'DOC-CREATOR-ONLY/archive: a non-creator archive was not refused by name: %', v_result;
  end if;

  select version, archived_at into v_version_after, v_archived_after
    from ops.doc_conversation where id = v_conversation;
  if v_version_after <> v_version_before or v_archived_after is distinct from v_archived_before then
    raise exception 'DOC-CREATOR-ONLY/archive: the archive field or the version moved under a refused attempt';
  end if;
end $wr114_creator_archive$;

-- ---------------------------------------------------------------------------
-- DOC-RENAME-RETAINS (db half): the step-2 version guard. A refused rename must
-- leave NO orphan title revision -- the revision table is immutable, so the
-- mistake could never be cleaned up afterwards.
-- ---------------------------------------------------------------------------
do $wr114_rename_guard$
declare v_conversation uuid := gen_random_uuid(); v_result jsonb;
        v_base integer; v_revisions integer;
begin
  perform set_config('carr.acting_actor_slug', 'wr114-author', true);
  perform ops.create_doc_conversation('before the rename', 'private', v_conversation);
  select version into v_base from ops.doc_conversation where id = v_conversation;

  v_result := ops.rename_doc_conversation(v_conversation, v_base, 'after the rename',
    null, null, gen_random_uuid());
  if not (v_result->>'ok')::boolean or (v_result->>'version')::integer <> v_base + 1 then
    raise exception 'WR-000114: the rename did not land: %', v_result;
  end if;
  select count(*) into v_revisions from ops.doc_conversation_title_revision
   where conversation_id = v_conversation;
  if v_revisions <> 1 then
    raise exception 'WR-000114: % title revisions after one rename', v_revisions;
  end if;
  if not exists (select 1 from ops.doc_conversation_title_revision
                  where conversation_id = v_conversation and title = 'before the rename') then
    raise exception 'WR-000114: the retained revision does not carry the PRIOR title';
  end if;

  -- The NOW-STALE base version. The refusal must append nothing.
  v_result := ops.rename_doc_conversation(v_conversation, v_base, 'a third title',
    null, null, gen_random_uuid());
  if (v_result->>'ok')::boolean or v_result->>'reason_id' <> 'version_conflict' then
    raise exception 'WR-000114: a stale base version was not refused: %', v_result;
  end if;
  if (v_result->>'current_version')::integer <> v_base + 1 then
    raise exception 'WR-000114: the refusal did not name the current version: %', v_result;
  end if;
  select count(*) into v_revisions from ops.doc_conversation_title_revision
   where conversation_id = v_conversation;
  if v_revisions <> 1 then
    raise exception 'WR-000114: a REFUSED rename appended an orphan revision (% rows)', v_revisions;
  end if;
end $wr114_rename_guard$;

-- ---------------------------------------------------------------------------
-- WR-000114 review B1 -- DOC-FLAG-ONLY: FOUR SEPARATELY NAMED POSITIVE CASES.
--
-- The DOC-CREATOR-ONLY/pin and /archive cases above are NEGATIVE: they prove a
-- non-creator is refused. These four prove the ACT itself, one case per act,
-- against the version/flag pair only a database can read back: the intended
-- stamp moved, the version rose by exactly one, the OTHER stamp and the title
-- are byte-identical, no title revision was appended, and a stale base version
-- is refused without moving the row. The two `else null` arms of the
-- compare-and-swap -- the arms that perform an unpin and an unarchive -- are
-- executed only here.
-- ---------------------------------------------------------------------------

do $wr114_flag_only$
declare
  r record;
  v_conversation uuid; v_result jsonb; v_base integer;
  v_title_before text; v_pinned_before timestamptz; v_archived_before timestamptz;
  v_version_after integer; v_title_after text;
  v_pinned_after timestamptz; v_archived_after timestamptz;
  v_revisions integer;
begin
  perform set_config('carr.acting_actor_slug', 'wr114-author', true);
  for r in
    select * from (values
      -- name,             setup pin, setup archive, act pin, act archive,
      -- expect pin stamp, expect archive stamp,     the field this act owns
      ('DOC-PIN-ONLY',       null::boolean, null::boolean, true::boolean,  null::boolean, true,  false, 'pinned_at'),
      -- unpin runs against an ARCHIVED conversation, so an unpin that reached
      -- archived_at instead of its own field cannot hide behind a null.
      ('DOC-UNPIN-ONLY',     true,          true,          false,          null,          false, true,  'pinned_at'),
      ('DOC-ARCHIVE-ONLY',   null,          null,          null,           true,          false, true,  'archived_at'),
      ('DOC-UNARCHIVE-ONLY', true,          true,          null,           false,         true,  false, 'archived_at')
    ) as t(name, setup_pinned, setup_archived, act_pinned, act_archived,
           expect_pinned, expect_archived, owns)
  loop
    v_conversation := gen_random_uuid();
    perform ops.create_doc_conversation(r.name || ' fixture', 'private', v_conversation);

    -- Setup. Each step is its own compare-and-swap, so the act below starts
    -- from a row that is genuinely pinned and/or archived.
    if r.setup_pinned is not null then
      select version into v_base from ops.doc_conversation where id = v_conversation;
      v_result := ops.rename_doc_conversation(v_conversation, v_base, null,
        r.setup_pinned, null, gen_random_uuid());
      if not (v_result->>'ok')::boolean then
        raise exception '%: the pin setup was refused: %', r.name, v_result;
      end if;
    end if;
    if r.setup_archived is not null then
      select version into v_base from ops.doc_conversation where id = v_conversation;
      v_result := ops.rename_doc_conversation(v_conversation, v_base, null,
        null, r.setup_archived, gen_random_uuid());
      if not (v_result->>'ok')::boolean then
        raise exception '%: the archive setup was refused: %', r.name, v_result;
      end if;
    end if;

    select version, title, pinned_at, archived_at
      into v_base, v_title_before, v_pinned_before, v_archived_before
      from ops.doc_conversation where id = v_conversation;

    -- THE ACT.
    v_result := ops.rename_doc_conversation(v_conversation, v_base, null,
      r.act_pinned, r.act_archived, gen_random_uuid());
    if not (v_result->>'ok')::boolean then
      raise exception '%: the act was refused: %', r.name, v_result;
    end if;

    select version, title, pinned_at, archived_at
      into v_version_after, v_title_after, v_pinned_after, v_archived_after
      from ops.doc_conversation where id = v_conversation;

    -- 1. The intended flag moved, in the direction asked for.
    if (v_pinned_after is not null) <> r.expect_pinned
       or (v_archived_after is not null) <> r.expect_archived then
      raise exception '%: the flags read back wrong (pinned_at %, archived_at %)',
        r.name, v_pinned_after, v_archived_after;
    end if;
    if (v_result->>'pinned')::boolean <> r.expect_pinned
       or (v_result->>'archived')::boolean <> r.expect_archived then
      raise exception '%: the answer disagrees with the stored row: %', r.name, v_result;
    end if;
    if r.owns = 'pinned_at' and v_pinned_after is not distinct from v_pinned_before then
      raise exception '%: pinned_at did not move at all', r.name;
    end if;
    if r.owns = 'archived_at' and v_archived_after is not distinct from v_archived_before then
      raise exception '%: archived_at did not move at all', r.name;
    end if;

    -- 2. The version rose by exactly one.
    if v_version_after <> v_base + 1 or (v_result->>'version')::integer <> v_base + 1 then
      raise exception '%: the version went % -> % (answer %)',
        r.name, v_base, v_version_after, v_result->>'version';
    end if;

    -- 3. The OTHER flag and the title are byte-identical, and no revision landed.
    if r.owns = 'pinned_at' and v_archived_after is distinct from v_archived_before then
      raise exception '%: archived_at moved (% -> %), and this act does not own it',
        r.name, v_archived_before, v_archived_after;
    end if;
    if r.owns = 'archived_at' and v_pinned_after is distinct from v_pinned_before then
      raise exception '%: pinned_at moved (% -> %), and this act does not own it',
        r.name, v_pinned_before, v_pinned_after;
    end if;
    if v_title_after is distinct from v_title_before or v_result->>'title' is distinct from v_title_before then
      raise exception '%: the title moved (% -> %)', r.name, v_title_before, v_title_after;
    end if;
    select count(*) into v_revisions from ops.doc_conversation_title_revision
     where conversation_id = v_conversation;
    if v_revisions <> 0 then
      raise exception '%: a flag write appended % title revisions', r.name, v_revisions;
    end if;

    -- 4. The now-stale base version is refused, and moves nothing.
    v_result := ops.rename_doc_conversation(v_conversation, v_base, null,
      r.act_pinned, r.act_archived, gen_random_uuid());
    if (v_result->>'ok')::boolean or v_result->>'reason_id' <> 'version_conflict' then
      raise exception '%: a stale base version was not refused: %', r.name, v_result;
    end if;
    if exists (select 1 from ops.doc_conversation
                where id = v_conversation
                  and (version <> v_version_after
                       or pinned_at is distinct from v_pinned_after
                       or archived_at is distinct from v_archived_after)) then
      raise exception '%: a REFUSED flag write moved the row', r.name;
    end if;
  end loop;
end $wr114_flag_only$;

select 'WR-000114 Doc conversation write doors: creator-only inside the definer, revoke stamps, grants writer-bound' as proof;
