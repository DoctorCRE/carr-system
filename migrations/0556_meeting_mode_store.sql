-- V5-UX-B11: the non-recording shared Meeting Mode store.
--
-- WHAT THIS IS. The durable half J201 (mcp-server/src/meeting-call-mode-j201.v5.js)
-- deliberately does not have: one shared meeting identity per native source
-- identity, one leased processing owner, append-only attributed notes and note
-- revisions, a numbered action stream, and the decisions taken on it. Every
-- write door is a SECURITY DEFINER function that derives its actor from the
-- server-installed transaction context (ops.portfolio_writer_actor_id()) and its
-- tenant from carr.organization_tenant_id; no door accepts an actor, a tenant or
-- a timestamp from a caller.
--
-- WHAT THIS IS NOT.
--   * NOT A RECORDER. There is no audio, transcript, waveform or recording
--     column anywhere below, and ops.meeting.recording is CHECKed to the single
--     value 'denied'. The legacy capture_session recorder is not read, joined or
--     reused. A later D03 recording extension needs its own recording, retention
--     and activation evidence; nothing here stands in for it.
--   * NOT A SECOND TASK STORE. An action here is a proposal plus, once a
--     verified partner accepts it, a POINTER to one canonical command (an
--     existing MCP write verb and its arguments) and the idempotency key that
--     command must be called with. The effect happens only through that
--     existing verb. An action becomes executed/delegated only when
--     ops.record_meeting_action_outcome finds the committed public.tool_call row
--     the canonical verb's own envelope wrote under that key -- never on a
--     caller's say-so.
--   * NOT THE J201 PROMPT LEDGER. A meeting starts on an explicit verified
--     partner one-tap (activation_intent 'one_tap_user_activation'); no
--     detection prompt is raised or recorded here, and J201's
--     step:v5-j201-durable-prompt-ledger-owner seam stays open.
--
-- GRANTS. The eight doors are granted to carr_writer ONLY: the DoctorCRE app
-- calls them as the signed-in partner on the writer connection, and no path
-- here needs the authority bundle. No table is granted to any runtime role.
--
-- NO TRANSACTION CONTROL. This file is the first member of the reviewed
-- ATOMIC_MIGRATION_GROUP 0556/0557 declared in tools/migrate.py; 0557 seals the
-- catalog this file changes as SCAC v49.

do $b11_preflight$
begin
  if to_regprocedure('ops.portfolio_writer_actor_id()') is null then
    raise exception '0556 requires ops.portfolio_writer_actor_id()';
  end if;
  if to_regclass('public.tool_call') is null then
    raise exception '0556 requires public.tool_call, the canonical envelope ledger it reconciles against';
  end if;
  if to_regclass('ops.meeting') is not null then
    raise exception '0556 found an existing ops.meeting; this store is created exactly once';
  end if;
end $b11_preflight$;

-- -------------------------------------------------------------------------
-- 1. Relations.
-- -------------------------------------------------------------------------

-- ONE ROW PER MEETING, keyed for sharing on its NATIVE SOURCE IDENTITY -- the
-- J201 rule (Q074.D1): duplicates reconcile by meeting and native source
-- identity, never by time overlap. native_id_epoch is part of the key, so a
-- recycled native id under a new epoch is a different meeting, not a merge.
create table ops.meeting (
  id uuid primary key,
  organization_tenant_id text not null check (organization_tenant_id <> ''),
  platform text check (platform in ('teams','zoom')),
  source_system text not null check (source_system ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  native_id text not null check (native_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$'),
  native_id_epoch text not null check (native_id_epoch ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  title text not null check (char_length(title) between 1 and 200 and title !~ '[[:cntrl:]]'),
  activation_intent text not null check (activation_intent = 'one_tap_user_activation'),
  recording text not null default 'denied' check (recording = 'denied'),
  mode_state text not null default 'active_non_recording'
    check (mode_state in ('active_non_recording','ended')),
  started_by_actor uuid not null references public.actor(id),
  started_at timestamptz not null default clock_timestamp(),
  ended_by_actor uuid references public.actor(id),
  ended_at timestamptz,
  last_seq bigint not null default 0 check (last_seq >= 0),
  last_note_number integer not null default 0 check (last_note_number >= 0),
  last_action_number integer not null default 0 check (last_action_number >= 0),
  constraint meeting_end_is_coherent check (
    (mode_state = 'ended') = (ended_at is not null)
    and (ended_at is null) = (ended_by_actor is null))
);
create unique index meeting_native_identity_uniq on ops.meeting
  (organization_tenant_id, coalesce(platform, ''), source_system, native_id, native_id_epoch);

comment on table ops.meeting is
  'V5-UX-B11: one shared non-recording meeting per tenant and native source identity. recording is CHECKed to denied; there is no audio column.';

-- THE SINGLE PROCESSING OWNER. One row per meeting, so a second device can
-- only rejoin as a participant while an unexpired lease is held. lease_epoch is
-- the fence: a processing contribution must present the current epoch, so a
-- device that lost its lease cannot keep adding suggestions after a takeover.
create table ops.meeting_processing_lease (
  meeting_id uuid primary key references ops.meeting(id),
  holder_actor uuid not null references public.actor(id),
  holder_instance text not null check (holder_instance ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  lease_epoch bigint not null check (lease_epoch >= 1),
  acquired_at timestamptz not null,
  expires_at timestamptz not null,
  released_at timestamptz,
  check (expires_at > acquired_at)
);

-- Notes and every revision of a note, append-only. A revision is a new row
-- with the same note_number; nothing is overwritten.
create table ops.meeting_note (
  id uuid primary key,
  meeting_id uuid not null references ops.meeting(id),
  note_number integer not null check (note_number >= 1),
  revision integer not null check (revision >= 1),
  body text not null check (char_length(body) between 1 and 20000),
  author_actor uuid not null references public.actor(id),
  author_instance text not null check (author_instance ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  created_at timestamptz not null default clock_timestamp(),
  unique (meeting_id, note_number, revision)
);

-- The action HEAD: one row per numbered logical action. Its state moves
-- proposed -> accepted -> executed|delegated, or proposed -> declined. The
-- history of what was proposed lives in the append-only revision table and
-- the append-only stream; this row is the lockable current position.
create table ops.meeting_action (
  meeting_id uuid not null references ops.meeting(id),
  action_number integer not null check (action_number >= 1),
  dedupe_key text check (dedupe_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  state text not null check (state in ('proposed','accepted','executed','delegated','declined')),
  current_revision integer not null check (current_revision >= 1),
  decided_revision integer check (decided_revision >= 1),
  decided_by_actor uuid references public.actor(id),
  decided_at timestamptz,
  disposition text check (disposition in ('execute','delegate')),
  assignee_actor uuid references public.actor(id),
  operation_key uuid unique,
  outcome jsonb,
  primary key (meeting_id, action_number),
  unique (meeting_id, dedupe_key),
  constraint meeting_action_decision_is_coherent check (
    (state = 'proposed') = (decided_at is null)
    and (decided_at is null) = (decided_by_actor is null)
    and (decided_at is null) = (decided_revision is null)),
  constraint meeting_action_acceptance_is_coherent check (
    (state in ('accepted','executed','delegated'))
      = (operation_key is not null and disposition is not null and assignee_actor is not null)),
  constraint meeting_action_outcome_is_evidence check (
    (state in ('executed','delegated')) = (outcome is not null)
    and (state <> 'executed' or disposition = 'execute')
    and (state <> 'delegated' or disposition = 'delegate'))
);

create table ops.meeting_action_revision (
  id uuid primary key,
  meeting_id uuid not null,
  action_number integer not null,
  revision integer not null check (revision >= 1),
  summary text not null check (char_length(summary) between 1 and 2000),
  command jsonb check (command is null or (
    jsonb_typeof(command) = 'object'
    and jsonb_typeof(command->'verb') = 'string'
    and jsonb_typeof(command->'args') = 'object'
    and (command - 'verb' - 'args') = '{}'::jsonb
    and octet_length(command::text) <= 8192)),
  basis text not null check (basis in ('tentative_discussion','explicit_instruction')),
  source text not null check (source in ('participant','processing')),
  processing_epoch bigint check (processing_epoch >= 1),
  proposed_by_actor uuid not null references public.actor(id),
  proposed_by_instance text not null
    check (proposed_by_instance ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  created_at timestamptz not null default clock_timestamp(),
  unique (meeting_id, action_number, revision),
  foreign key (meeting_id, action_number) references ops.meeting_action(meeting_id, action_number),
  check ((source = 'processing') = (processing_epoch is not null))
);

-- THE NUMBERED STREAM. Every accepted write appends exactly the entries it
-- caused, under the meeting row lock, so seq is gapless per meeting and a
-- reconnecting device resumes from the last seq it saw. (idempotency_key,
-- kind) is unique: a replayed write can never append a second copy.
create table ops.meeting_stream (
  meeting_id uuid not null references ops.meeting(id),
  seq bigint not null check (seq >= 1),
  kind text not null check (kind in (
    'meeting_started','meeting_joined','processing_acquired','processing_taken_over',
    'processing_released','note_added','note_revised','action_proposed','action_revised',
    'action_accepted','action_declined','action_executed','action_delegated','meeting_ended')),
  actor_id uuid not null references public.actor(id),
  client_instance text not null check (client_instance ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  note_number integer,
  action_number integer,
  revision integer,
  lease_epoch bigint,
  idempotency_key uuid not null,
  at timestamptz not null default clock_timestamp(),
  primary key (meeting_id, seq),
  unique (idempotency_key, kind)
);

create function ops.meeting_mode_rows_immutable() returns trigger
language plpgsql set search_path = pg_catalog, ops, public
as $$
begin
  raise exception '% is append-only: % refused', tg_table_name, tg_op;
end $$;
revoke all on function ops.meeting_mode_rows_immutable() from public;

create trigger meeting_note_append_only before update or delete on ops.meeting_note
  for each row execute function ops.meeting_mode_rows_immutable();
create trigger meeting_note_no_truncate before truncate on ops.meeting_note
  for each statement execute function ops.meeting_mode_rows_immutable();
create trigger meeting_action_revision_append_only before update or delete on ops.meeting_action_revision
  for each row execute function ops.meeting_mode_rows_immutable();
create trigger meeting_action_revision_no_truncate before truncate on ops.meeting_action_revision
  for each statement execute function ops.meeting_mode_rows_immutable();
create trigger meeting_stream_append_only before update or delete on ops.meeting_stream
  for each row execute function ops.meeting_mode_rows_immutable();
create trigger meeting_stream_no_truncate before truncate on ops.meeting_stream
  for each statement execute function ops.meeting_mode_rows_immutable();

revoke all on ops.meeting, ops.meeting_processing_lease, ops.meeting_note, ops.meeting_action,
  ops.meeting_action_revision, ops.meeting_stream from public;

-- -------------------------------------------------------------------------
-- 2. Internal helpers. SECURITY INVOKER and executable by the owner only: they
--    run inside the definer doors below and are never a door themselves.
-- -------------------------------------------------------------------------

-- The acting actor, whether it is a verified partner, and the tenant. A human
-- actor is only returned by ops.portfolio_writer_actor_id() when the server
-- also installed the verified-partner context naming it, so kind='human' here
-- IS the verified-partner predicate.
create function ops.meeting_mode_actor(
  out actor_id uuid, out actor_slug text, out is_partner boolean, out tenant text)
language plpgsql stable set search_path = pg_catalog, ops, public
as $$
begin
  actor_id := ops.portfolio_writer_actor_id();
  select a.slug, a.kind = 'human' into actor_slug, is_partner from public.actor a where a.id = actor_id;
  tenant := nullif(current_setting('carr.organization_tenant_id', true), '');
end $$;
revoke all on function ops.meeting_mode_actor() from public;

-- Append one stream entry under the caller's meeting row lock. Returns the new
-- seq, or null when this (idempotency_key, kind) was already appended.
create function ops.meeting_mode_append(
  p_meeting uuid, p_kind text, p_actor uuid, p_instance text, p_note integer,
  p_action integer, p_revision integer, p_lease_epoch bigint, p_key uuid)
returns bigint language plpgsql set search_path = pg_catalog, ops, public
as $$
declare v_seq bigint;
begin
  if exists (select 1 from ops.meeting_stream where idempotency_key = p_key and kind = p_kind) then
    return null;
  end if;
  update ops.meeting set last_seq = last_seq + 1 where id = p_meeting returning last_seq into v_seq;
  insert into ops.meeting_stream(meeting_id, seq, kind, actor_id, client_instance, note_number,
    action_number, revision, lease_epoch, idempotency_key)
  values (p_meeting, v_seq, p_kind, p_actor, p_instance, p_note, p_action, p_revision,
    p_lease_epoch, p_key);
  return v_seq;
end $$;
revoke all on function ops.meeting_mode_append(uuid,text,uuid,text,integer,integer,integer,bigint,uuid) from public;

-- The server's lease length. Not a caller input: a device that could choose
-- its own lease could hold the processing role forever.
create function ops.meeting_processing_lease_ttl() returns interval
language sql immutable set search_path = pg_catalog
as $$ select interval '120 seconds' $$;
revoke all on function ops.meeting_processing_lease_ttl() from public;

-- -------------------------------------------------------------------------
-- 3. start_meeting -- start, or rejoin, the one shared meeting.
--
-- A verified partner's explicit one-tap only. Two devices starting the same
-- native meeting at once converge on ONE row: the loser's insert waits on the
-- winner's unique-index entry, does nothing, and then reads the committed row
-- as a rejoin. The row id is the winning start's idempotency key.
-- -------------------------------------------------------------------------
create function ops.start_meeting(
  p_platform text, p_source_system text, p_native_id text, p_native_id_epoch text,
  p_title text, p_activation_intent text, p_client_instance text, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path = pg_catalog, ops, public
as $$
declare v_who record; v_id uuid; v_row ops.meeting%rowtype; v_seq bigint;
begin
  select * into v_who from ops.meeting_mode_actor();
  if v_who.tenant is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_tenant_context_unavailable');
  end if;
  if not v_who.is_partner then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_start_requires_verified_partner');
  end if;
  if p_activation_intent is distinct from 'one_tap_user_activation' then
    return jsonb_build_object('ok', false, 'reason_id', 'explicit_human_activation_required');
  end if;
  if p_idempotency_key is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_idempotency_key_required');
  end if;

  insert into ops.meeting(id, organization_tenant_id, platform, source_system, native_id,
    native_id_epoch, title, activation_intent, started_by_actor)
  values (p_idempotency_key, v_who.tenant, p_platform, p_source_system, p_native_id,
    p_native_id_epoch, p_title, p_activation_intent, v_who.actor_id)
  on conflict do nothing
  returning id into v_id;

  if v_id is not null then
    perform ops.meeting_mode_append(v_id, 'meeting_started', v_who.actor_id, p_client_instance,
      null, null, null, null, p_idempotency_key);
    select * into v_row from ops.meeting where id = v_id;
    return jsonb_build_object('ok', true, 'deduplicated', false, 'joined_existing', false,
      'meeting_id', v_row.id, 'title', v_row.title, 'mode_state', v_row.mode_state,
      'started_by', v_who.actor_slug, 'last_seq', v_row.last_seq);
  end if;

  select * into v_row from ops.meeting
   where organization_tenant_id = v_who.tenant and coalesce(platform, '') = coalesce(p_platform, '')
     and source_system = p_source_system and native_id = p_native_id
     and native_id_epoch = p_native_id_epoch
   for update;
  if v_row.id is null then
    -- The conflict was on the primary key: this idempotency key already names
    -- a DIFFERENT meeting. That is a reuse, never a join.
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_idempotency_key_reuse');
  end if;
  if v_row.id = p_idempotency_key then
    if v_row.started_by_actor is distinct from v_who.actor_id or v_row.title is distinct from p_title then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_idempotency_key_reuse');
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'joined_existing', false,
      'meeting_id', v_row.id, 'title', v_row.title, 'mode_state', v_row.mode_state,
      'started_by', (select slug from public.actor where id = v_row.started_by_actor),
      'last_seq', v_row.last_seq);
  end if;
  -- An ended meeting is reported as ended and gains no join entry: it cannot
  -- be reopened by starting it again.
  if v_row.mode_state <> 'ended' then
    v_seq := ops.meeting_mode_append(v_row.id, 'meeting_joined', v_who.actor_id, p_client_instance,
      null, null, null, null, p_idempotency_key);
  end if;
  return jsonb_build_object('ok', true, 'deduplicated', v_seq is null and v_row.mode_state <> 'ended',
    'joined_existing', true,
    'meeting_id', v_row.id, 'title', v_row.title, 'mode_state', v_row.mode_state,
    'started_by', (select slug from public.actor where id = v_row.started_by_actor),
    'last_seq', (select last_seq from ops.meeting where id = v_row.id));
end $$;

-- -------------------------------------------------------------------------
-- 4. claim_meeting_processing -- the single processing owner.
-- -------------------------------------------------------------------------
create function ops.claim_meeting_processing(
  p_meeting uuid, p_client_instance text, p_release boolean, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path = pg_catalog, ops, public
as $$
declare v_who record; v_row ops.meeting%rowtype; v_lease ops.meeting_processing_lease%rowtype;
        v_now timestamptz := clock_timestamp(); v_decision text; v_holder boolean;
begin
  select * into v_who from ops.meeting_mode_actor();
  select * into v_row from ops.meeting
   where id = p_meeting and organization_tenant_id = v_who.tenant for update;
  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_not_found');
  end if;
  select * into v_lease from ops.meeting_processing_lease where meeting_id = p_meeting for update;
  v_holder := v_lease.meeting_id is not null and v_lease.holder_actor = v_who.actor_id
    and v_lease.holder_instance = p_client_instance and v_lease.released_at is null;

  if coalesce(p_release, false) then
    if not v_holder then
      v_decision := 'not_holder';
    else
      update ops.meeting_processing_lease set released_at = v_now where meeting_id = p_meeting;
      perform ops.meeting_mode_append(p_meeting, 'processing_released', v_who.actor_id,
        p_client_instance, null, null, null, v_lease.lease_epoch, p_idempotency_key);
      v_decision := 'released';
    end if;
  elsif v_row.mode_state = 'ended' then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_ended', 'meeting_id', p_meeting);
  elsif v_lease.meeting_id is null then
    insert into ops.meeting_processing_lease(meeting_id, holder_actor, holder_instance,
      lease_epoch, acquired_at, expires_at)
    values (p_meeting, v_who.actor_id, p_client_instance, 1, v_now,
      v_now + ops.meeting_processing_lease_ttl());
    perform ops.meeting_mode_append(p_meeting, 'processing_acquired', v_who.actor_id,
      p_client_instance, null, null, null, 1, p_idempotency_key);
    v_decision := 'acquired';
  elsif v_holder then
    -- Renewal by the same device. Nobody else took the lease, so the epoch
    -- does not move and nothing is appended: a heartbeat is not history.
    update ops.meeting_processing_lease
       set expires_at = v_now + ops.meeting_processing_lease_ttl()
     where meeting_id = p_meeting;
    v_decision := 'renewed';
  elsif v_lease.released_at is null and v_lease.expires_at > v_now then
    v_decision := 'held_by_other';
  else
    update ops.meeting_processing_lease
       set holder_actor = v_who.actor_id, holder_instance = p_client_instance,
           lease_epoch = lease_epoch + 1, acquired_at = v_now,
           expires_at = v_now + ops.meeting_processing_lease_ttl(), released_at = null
     where meeting_id = p_meeting;
    perform ops.meeting_mode_append(p_meeting, 'processing_taken_over', v_who.actor_id,
      p_client_instance, null, null, null, v_lease.lease_epoch + 1, p_idempotency_key);
    v_decision := 'taken_over_after_expiry';
  end if;

  select * into v_lease from ops.meeting_processing_lease where meeting_id = p_meeting;
  return jsonb_build_object('ok', true, 'meeting_id', p_meeting, 'decision', v_decision,
    'is_holder', v_lease.meeting_id is not null and v_lease.released_at is null
      and v_lease.holder_actor = v_who.actor_id and v_lease.holder_instance = p_client_instance,
    'lease', case when v_lease.meeting_id is null then null else jsonb_build_object(
      'holder', (select slug from public.actor where id = v_lease.holder_actor),
      'holder_instance', v_lease.holder_instance, 'lease_epoch', v_lease.lease_epoch,
      'expires_at', v_lease.expires_at, 'released', v_lease.released_at is not null) end);
end $$;

-- -------------------------------------------------------------------------
-- 5. add_meeting_note -- a new note, or a new revision of one.
-- -------------------------------------------------------------------------
create function ops.add_meeting_note(
  p_meeting uuid, p_body text, p_revises_note_number integer, p_base_revision integer,
  p_client_instance text, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path = pg_catalog, ops, public
as $$
declare v_who record; v_row ops.meeting%rowtype; v_prior ops.meeting_note%rowtype;
        v_number integer; v_revision integer; v_current integer;
begin
  select * into v_who from ops.meeting_mode_actor();
  select * into v_prior from ops.meeting_note where id = p_idempotency_key;
  if v_prior.id is not null then
    if v_prior.meeting_id is distinct from p_meeting or v_prior.body is distinct from p_body
       or v_prior.author_actor is distinct from v_who.actor_id then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_note_idempotency_key_reuse');
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'meeting_id', p_meeting,
      'note_number', v_prior.note_number, 'revision', v_prior.revision);
  end if;
  select * into v_row from ops.meeting
   where id = p_meeting and organization_tenant_id = v_who.tenant for update;
  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_not_found');
  end if;
  if v_row.mode_state = 'ended' then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_ended', 'meeting_id', p_meeting);
  end if;
  if p_revises_note_number is null then
    if p_base_revision is not null then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_note_base_revision_without_note');
    end if;
    v_number := v_row.last_note_number + 1;
    v_revision := 1;
    update ops.meeting set last_note_number = v_number where id = p_meeting;
  else
    select max(revision) into v_current from ops.meeting_note
     where meeting_id = p_meeting and note_number = p_revises_note_number;
    if v_current is null then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_note_not_found',
        'note_number', p_revises_note_number);
    end if;
    if p_base_revision is distinct from v_current then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_note_revision_conflict',
        'note_number', p_revises_note_number, 'current_revision', v_current);
    end if;
    v_number := p_revises_note_number;
    v_revision := v_current + 1;
  end if;
  insert into ops.meeting_note(id, meeting_id, note_number, revision, body, author_actor, author_instance)
  values (p_idempotency_key, p_meeting, v_number, v_revision, p_body, v_who.actor_id, p_client_instance);
  perform ops.meeting_mode_append(p_meeting,
    case when v_revision = 1 then 'note_added' else 'note_revised' end,
    v_who.actor_id, p_client_instance, v_number, null, v_revision, null, p_idempotency_key);
  return jsonb_build_object('ok', true, 'deduplicated', false, 'meeting_id', p_meeting,
    'note_number', v_number, 'revision', v_revision,
    'seq', (select last_seq from ops.meeting where id = p_meeting));
end $$;

-- -------------------------------------------------------------------------
-- 6. accept (internal) -- the one acceptance transition, shared by an
--    explicit partner instruction at proposal time and by decide_meeting_action.
--    The caller holds the meeting and action locks.
-- -------------------------------------------------------------------------
create function ops.meeting_mode_accept(
  p_meeting uuid, p_action integer, p_revision integer, p_actor uuid, p_disposition text,
  p_assignee uuid, p_instance text, p_key uuid)
returns uuid language plpgsql set search_path = pg_catalog, ops, public
as $$
declare v_operation uuid := gen_random_uuid();
begin
  update ops.meeting_action
     set state = 'accepted', decided_revision = p_revision, decided_by_actor = p_actor,
         decided_at = clock_timestamp(), disposition = p_disposition, assignee_actor = p_assignee,
         operation_key = v_operation
   where meeting_id = p_meeting and action_number = p_action and state = 'proposed';
  if not found then
    raise exception 'meeting action %/% is not pending acceptance', p_meeting, p_action;
  end if;
  perform ops.meeting_mode_append(p_meeting, 'action_accepted', p_actor, p_instance, null,
    p_action, p_revision, null, p_key);
  return v_operation;
end $$;
revoke all on function ops.meeting_mode_accept(uuid,integer,integer,uuid,text,uuid,text,uuid) from public;

-- The action's current position as the doors return it.
create function ops.meeting_mode_action_view(p_meeting uuid, p_action integer)
returns jsonb language sql stable set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object('action_number', a.action_number, 'state', a.state,
    'current_revision', a.current_revision, 'decided_revision', a.decided_revision,
    'decided_by', (select slug from public.actor where id = a.decided_by_actor),
    'disposition', a.disposition,
    'assignee', (select slug from public.actor where id = a.assignee_actor),
    'command', r.command,
    'dispatch', case when a.operation_key is null then null else jsonb_build_object(
      'verb', d.command->>'verb', 'args', d.command->'args',
      'idempotency_key', a.operation_key) end,
    'outcome', a.outcome)
  from ops.meeting_action a
  join ops.meeting_action_revision r
    on r.meeting_id = a.meeting_id and r.action_number = a.action_number
   and r.revision = a.current_revision
  left join ops.meeting_action_revision d
    on d.meeting_id = a.meeting_id and d.action_number = a.action_number
   and d.revision = a.decided_revision
  where a.meeting_id = p_meeting and a.action_number = p_action
$$;
revoke all on function ops.meeting_mode_action_view(uuid,integer) from public;

-- -------------------------------------------------------------------------
-- 7. propose_meeting_action -- a new numbered action, or a revision of a
--    pending one.
--
-- TENTATIVE STAYS PROPOSED. Only a verified partner's explicit instruction
-- that names a canonical command is accepted at proposal time; a processing
-- contribution never is, whatever basis it claims, because transcribed words
-- are data and not fresh authority.
--
-- NEW DISCUSSION REVISES, IT DOES NOT DUPLICATE. A dedupe_key names the
-- logical action a contribution is about. The same key with identical content
-- is a no-op; with new content it is a revision of the pending proposal; after
-- a decision it changes nothing and says so.
-- -------------------------------------------------------------------------
create function ops.propose_meeting_action(
  p_meeting uuid, p_summary text, p_command jsonb, p_basis text, p_dedupe_key text,
  p_revises_action_number integer, p_base_revision integer, p_processing_epoch bigint,
  p_client_instance text, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path = pg_catalog, ops, public
as $$
declare v_who record; v_row ops.meeting%rowtype; v_lease ops.meeting_processing_lease%rowtype;
        v_prior ops.meeting_action_revision%rowtype; v_head ops.meeting_action%rowtype;
        v_latest ops.meeting_action_revision%rowtype; v_number integer; v_revision integer;
        v_source text; v_accepted boolean := false;
begin
  select * into v_who from ops.meeting_mode_actor();
  select * into v_prior from ops.meeting_action_revision where id = p_idempotency_key;
  if v_prior.id is not null then
    if v_prior.meeting_id is distinct from p_meeting or v_prior.summary is distinct from p_summary
       or v_prior.command is distinct from p_command or v_prior.basis is distinct from p_basis
       or v_prior.proposed_by_actor is distinct from v_who.actor_id then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_idempotency_key_reuse');
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'meeting_id', p_meeting,
      'revision', v_prior.revision,
      'action', ops.meeting_mode_action_view(p_meeting, v_prior.action_number));
  end if;
  if p_basis not in ('tentative_discussion', 'explicit_instruction') then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_basis_invalid');
  end if;
  select * into v_row from ops.meeting
   where id = p_meeting and organization_tenant_id = v_who.tenant for update;
  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_not_found');
  end if;
  if v_row.mode_state = 'ended' then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_ended', 'meeting_id', p_meeting);
  end if;

  v_source := case when p_processing_epoch is null then 'participant' else 'processing' end;
  if v_source = 'processing' then
    select * into v_lease from ops.meeting_processing_lease where meeting_id = p_meeting;
    if v_lease.meeting_id is null or v_lease.released_at is not null
       or v_lease.expires_at <= clock_timestamp()
       or v_lease.holder_actor <> v_who.actor_id or v_lease.holder_instance <> p_client_instance
       or v_lease.lease_epoch <> p_processing_epoch then
      return jsonb_build_object('ok', false, 'reason_id', 'stale_processing_lease',
        'current_lease_epoch', v_lease.lease_epoch);
    end if;
  end if;

  if p_revises_action_number is not null then
    select * into v_head from ops.meeting_action
     where meeting_id = p_meeting and action_number = p_revises_action_number for update;
    if v_head.meeting_id is null then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_not_found',
        'action_number', p_revises_action_number);
    end if;
    if v_head.state <> 'proposed' then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_no_longer_pending',
        'action', ops.meeting_mode_action_view(p_meeting, v_head.action_number));
    end if;
    if p_base_revision is distinct from v_head.current_revision then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_revision_conflict',
        'action', ops.meeting_mode_action_view(p_meeting, v_head.action_number));
    end if;
  elsif p_dedupe_key is not null then
    select * into v_head from ops.meeting_action
     where meeting_id = p_meeting and dedupe_key = p_dedupe_key for update;
    if v_head.meeting_id is not null and v_head.state <> 'proposed' then
      return jsonb_build_object('ok', true, 'deduplicated', true, 'already_resolved', true,
        'meeting_id', p_meeting,
        'action', ops.meeting_mode_action_view(p_meeting, v_head.action_number));
    end if;
  elsif p_base_revision is not null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_base_revision_without_action');
  end if;

  if v_head.meeting_id is not null then
    select * into v_latest from ops.meeting_action_revision
     where meeting_id = p_meeting and action_number = v_head.action_number
       and revision = v_head.current_revision;
    if v_latest.summary = p_summary and v_latest.command is not distinct from p_command
       and v_latest.basis = p_basis then
      return jsonb_build_object('ok', true, 'deduplicated', true, 'meeting_id', p_meeting,
        'revision', v_head.current_revision,
        'action', ops.meeting_mode_action_view(p_meeting, v_head.action_number));
    end if;
    v_number := v_head.action_number;
    v_revision := v_head.current_revision + 1;
    update ops.meeting_action set current_revision = v_revision
     where meeting_id = p_meeting and action_number = v_number;
  else
    v_number := v_row.last_action_number + 1;
    v_revision := 1;
    update ops.meeting set last_action_number = v_number where id = p_meeting;
    insert into ops.meeting_action(meeting_id, action_number, dedupe_key, state, current_revision)
    values (p_meeting, v_number, p_dedupe_key, 'proposed', 1);
  end if;

  insert into ops.meeting_action_revision(id, meeting_id, action_number, revision, summary, command,
    basis, source, processing_epoch, proposed_by_actor, proposed_by_instance)
  values (p_idempotency_key, p_meeting, v_number, v_revision, p_summary, p_command, p_basis,
    v_source, p_processing_epoch, v_who.actor_id, p_client_instance);
  perform ops.meeting_mode_append(p_meeting,
    case when v_revision = 1 then 'action_proposed' else 'action_revised' end,
    v_who.actor_id, p_client_instance, null, v_number, v_revision, null, p_idempotency_key);

  if v_source = 'participant' and v_who.is_partner and p_basis = 'explicit_instruction'
     and p_command is not null then
    perform ops.meeting_mode_accept(p_meeting, v_number, v_revision, v_who.actor_id, 'execute',
      v_who.actor_id, p_client_instance, p_idempotency_key);
    v_accepted := true;
  end if;

  return jsonb_build_object('ok', true, 'deduplicated', false, 'meeting_id', p_meeting,
    'revision', v_revision, 'accepted_as_explicit_instruction', v_accepted,
    'action', ops.meeting_mode_action_view(p_meeting, v_number),
    'seq', (select last_seq from ops.meeting where id = p_meeting));
end $$;

-- -------------------------------------------------------------------------
-- 8. decide_meeting_action -- a verified partner accepts or declines.
--
-- SIMULTANEOUS ACCEPTANCE RESOLVES ONCE. The action row is locked; the first
-- acceptance moves it and mints the one operation key; every later acceptance
-- of the same revision is answered with that same decision and key.
-- -------------------------------------------------------------------------
create function ops.decide_meeting_action(
  p_meeting uuid, p_action_number integer, p_decision text, p_base_revision integer,
  p_disposition text, p_assignee_slug text, p_client_instance text, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path = pg_catalog, ops, public
as $$
declare v_who record; v_row ops.meeting%rowtype; v_head ops.meeting_action%rowtype;
        v_latest ops.meeting_action_revision%rowtype; v_assignee uuid; v_disposition text;
begin
  select * into v_who from ops.meeting_mode_actor();
  if not coalesce(v_who.is_partner, false) then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_decision_requires_verified_partner');
  end if;
  if p_decision not in ('accept', 'decline') then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_decision_invalid');
  end if;
  select * into v_row from ops.meeting
   where id = p_meeting and organization_tenant_id = v_who.tenant for update;
  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_not_found');
  end if;
  select * into v_head from ops.meeting_action
   where meeting_id = p_meeting and action_number = p_action_number for update;
  if v_head.meeting_id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_not_found',
      'action_number', p_action_number);
  end if;
  if exists (select 1 from ops.meeting_stream where idempotency_key = p_idempotency_key
               and kind in ('action_accepted', 'action_declined')) then
    return jsonb_build_object('ok', true, 'deduplicated', true, 'already', true,
      'meeting_id', p_meeting, 'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;

  if v_head.state <> 'proposed' then
    if p_decision = 'accept' and v_head.state in ('accepted', 'executed', 'delegated') then
      return jsonb_build_object('ok', true, 'deduplicated', false, 'already', true,
        'resolved_once', true, 'meeting_id', p_meeting,
        'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
    end if;
    if p_decision = 'decline' and v_head.state = 'declined' then
      return jsonb_build_object('ok', true, 'deduplicated', false, 'already', true,
        'meeting_id', p_meeting, 'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
    end if;
    return jsonb_build_object('ok', false,
      'reason_id', case when v_head.state = 'declined' then 'meeting_action_already_declined'
                        else 'meeting_action_already_accepted_requires_correction' end,
      'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;
  if p_base_revision is distinct from v_head.current_revision then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_revised_since_read',
      'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;

  if p_decision = 'decline' then
    update ops.meeting_action
       set state = 'declined', decided_revision = v_head.current_revision,
           decided_by_actor = v_who.actor_id, decided_at = clock_timestamp()
     where meeting_id = p_meeting and action_number = p_action_number;
    perform ops.meeting_mode_append(p_meeting, 'action_declined', v_who.actor_id,
      p_client_instance, null, p_action_number, v_head.current_revision, null, p_idempotency_key);
    return jsonb_build_object('ok', true, 'deduplicated', false, 'already', false,
      'meeting_id', p_meeting, 'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;

  select * into v_latest from ops.meeting_action_revision
   where meeting_id = p_meeting and action_number = p_action_number
     and revision = v_head.current_revision;
  if v_latest.command is null then
    -- A tentative item with nothing to execute stays a proposal; accepting it
    -- would make this table the task store.
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_has_no_canonical_command',
      'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;
  v_disposition := coalesce(p_disposition, 'execute');
  if v_disposition not in ('execute', 'delegate') then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_disposition_invalid');
  end if;
  if p_assignee_slug is null then
    if v_disposition = 'delegate' then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_delegation_requires_assignee');
    end if;
    v_assignee := v_who.actor_id;
  else
    select id into v_assignee from public.actor where slug = p_assignee_slug and active;
    if v_assignee is null then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_assignee_not_found');
    end if;
  end if;
  perform ops.meeting_mode_accept(p_meeting, p_action_number, v_head.current_revision,
    v_who.actor_id, v_disposition, v_assignee, p_client_instance, p_idempotency_key);
  return jsonb_build_object('ok', true, 'deduplicated', false, 'already', false,
    'resolved_once', true, 'meeting_id', p_meeting,
    'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
end $$;

-- -------------------------------------------------------------------------
-- 9. record_meeting_action_outcome -- reconcile an accepted action against
--    the canonical envelope ledger.
--
-- THE ONLY WAY AN ACTION BECOMES DONE OR DELEGATED. public.tool_call holds a
-- row for an idempotency key only when the verb that used it COMMITTED (the
-- envelope inserts it inside the verb's own transaction). Found under the
-- operation key, for the accepted command's verb and this tenant: the effect
-- happened. Absent: it is not observed, the action stays accepted, and the
-- answer names the exact call to retry WITH THE SAME KEY -- the canonical
-- envelope replays a committed call rather than writing twice.
-- -------------------------------------------------------------------------
create function ops.record_meeting_action_outcome(
  p_meeting uuid, p_action_number integer, p_client_instance text, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path = pg_catalog, ops, public
as $$
declare v_who record; v_row ops.meeting%rowtype; v_head ops.meeting_action%rowtype;
        v_decided ops.meeting_action_revision%rowtype; v_call public.tool_call%rowtype;
        v_state text; v_outcome jsonb;
begin
  select * into v_who from ops.meeting_mode_actor();
  select * into v_row from ops.meeting
   where id = p_meeting and organization_tenant_id = v_who.tenant for update;
  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_not_found');
  end if;
  select * into v_head from ops.meeting_action
   where meeting_id = p_meeting and action_number = p_action_number for update;
  if v_head.meeting_id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_not_found',
      'action_number', p_action_number);
  end if;
  if v_head.state in ('executed', 'delegated') then
    return jsonb_build_object('ok', true, 'already', true, 'reconciled', true,
      'meeting_id', p_meeting, 'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;
  if v_head.state <> 'accepted' then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_not_accepted',
      'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;
  select * into v_decided from ops.meeting_action_revision
   where meeting_id = p_meeting and action_number = p_action_number
     and revision = v_head.decided_revision;
  select * into v_call from public.tool_call where idempotency_key = v_head.operation_key::text;
  if v_call.idempotency_key is null then
    return jsonb_build_object('ok', true, 'already', false, 'reconciled', false,
      'outcome_state', 'not_observed', 'meeting_id', p_meeting,
      'retry', jsonb_build_object('verb', v_decided.command->>'verb',
        'args', v_decided.command->'args', 'idempotency_key', v_head.operation_key,
        'rule', 'retry only with this exact idempotency_key; never mint a new one'),
      'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;
  if v_call.verb is distinct from v_decided.command->>'verb'
     or v_call.organization_tenant_id is distinct from v_row.organization_tenant_id then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_operation_key_bound_elsewhere',
      'observed_verb', v_call.verb,
      'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;
  v_state := case when v_head.disposition = 'delegate' then 'delegated' else 'executed' end;
  v_outcome := jsonb_build_object('verb', v_call.verb, 'idempotency_key', v_call.idempotency_key,
    'committed_at', v_call.created_at,
    'committed_by', (select slug from public.actor where id = v_call.actor_id),
    'response_digest', 'sha256:' || encode(public.digest(convert_to(v_call.response::text, 'UTF8'), 'sha256'), 'hex'),
    'evidence', 'public.tool_call');
  update ops.meeting_action set state = v_state, outcome = v_outcome
   where meeting_id = p_meeting and action_number = p_action_number;
  perform ops.meeting_mode_append(p_meeting,
    case when v_state = 'delegated' then 'action_delegated' else 'action_executed' end,
    v_who.actor_id, p_client_instance, null, p_action_number, v_head.decided_revision, null,
    p_idempotency_key);
  return jsonb_build_object('ok', true, 'already', false, 'reconciled', true,
    'outcome_state', v_state, 'meeting_id', p_meeting,
    'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
end $$;

-- -------------------------------------------------------------------------
-- 10. end_meeting -- a verified partner ends it; the processing lease ends
--     with it. Pending actions stay decidable and reconcilable afterwards:
--     processing complete and review complete are different facts.
-- -------------------------------------------------------------------------
create function ops.end_meeting(p_meeting uuid, p_client_instance text, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path = pg_catalog, ops, public
as $$
declare v_who record; v_row ops.meeting%rowtype;
begin
  select * into v_who from ops.meeting_mode_actor();
  if not coalesce(v_who.is_partner, false) then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_end_requires_verified_partner');
  end if;
  select * into v_row from ops.meeting
   where id = p_meeting and organization_tenant_id = v_who.tenant for update;
  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_not_found');
  end if;
  if v_row.mode_state = 'ended' then
    return jsonb_build_object('ok', true, 'already', true, 'meeting_id', p_meeting,
      'ended_at', v_row.ended_at);
  end if;
  update ops.meeting set mode_state = 'ended', ended_at = clock_timestamp(),
         ended_by_actor = v_who.actor_id
   where id = p_meeting;
  update ops.meeting_processing_lease set released_at = clock_timestamp()
   where meeting_id = p_meeting and released_at is null;
  perform ops.meeting_mode_append(p_meeting, 'meeting_ended', v_who.actor_id, p_client_instance,
    null, null, null, null, p_idempotency_key);
  return jsonb_build_object('ok', true, 'already', false, 'meeting_id', p_meeting,
    'ended_at', (select ended_at from ops.meeting where id = p_meeting));
end $$;

-- -------------------------------------------------------------------------
-- 11. meeting_facts -- the one read door. Stable; for the writer connection's
--     read-only transaction, which is the path that installs the actor context.
-- -------------------------------------------------------------------------
create function ops.meeting_facts(p_meeting uuid, p_after_seq bigint, p_limit integer)
returns jsonb language plpgsql stable security definer set search_path = pg_catalog, ops, public
as $$
declare v_who record; v_row ops.meeting%rowtype; v_limit integer;
begin
  select * into v_who from ops.meeting_mode_actor();
  select * into v_row from ops.meeting where id = p_meeting and organization_tenant_id = v_who.tenant;
  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_not_found');
  end if;
  v_limit := least(greatest(coalesce(p_limit, 200), 1), 500);
  return jsonb_build_object('ok', true,
    'meeting', jsonb_build_object('id', v_row.id, 'title', v_row.title,
      'platform', v_row.platform,
      'native_identity', jsonb_build_object('source_system', v_row.source_system,
        'native_id', v_row.native_id, 'native_id_epoch', v_row.native_id_epoch),
      'mode_state', v_row.mode_state, 'recording', v_row.recording,
      'activation_intent', v_row.activation_intent,
      'started_by', (select slug from public.actor where id = v_row.started_by_actor),
      'started_at', v_row.started_at,
      'ended_by', (select slug from public.actor where id = v_row.ended_by_actor),
      'ended_at', v_row.ended_at, 'last_seq', v_row.last_seq),
    'lease', (select jsonb_build_object(
      'holder', (select slug from public.actor where id = l.holder_actor),
      'holder_instance', l.holder_instance, 'lease_epoch', l.lease_epoch,
      'expires_at', l.expires_at, 'released', l.released_at is not null,
      'live', l.released_at is null and l.expires_at > clock_timestamp())
      from ops.meeting_processing_lease l where l.meeting_id = p_meeting),
    'notes', coalesce((select jsonb_agg(jsonb_build_object('note_number', n.note_number,
      'revision', n.revision, 'body', n.body,
      'author', (select slug from public.actor where id = n.author_actor),
      'author_instance', n.author_instance, 'at', n.created_at)
      order by n.note_number, n.revision)
      from ops.meeting_note n where n.meeting_id = p_meeting), '[]'::jsonb),
    'actions', coalesce((select jsonb_agg(ops.meeting_mode_action_view(p_meeting, a.action_number)
      || jsonb_build_object('revisions', (select jsonb_agg(jsonb_build_object(
          'revision', r.revision, 'summary', r.summary, 'command', r.command, 'basis', r.basis,
          'source', r.source, 'processing_epoch', r.processing_epoch,
          'proposed_by', (select slug from public.actor where id = r.proposed_by_actor),
          'proposed_by_instance', r.proposed_by_instance, 'at', r.created_at)
          order by r.revision)
        from ops.meeting_action_revision r
        where r.meeting_id = p_meeting and r.action_number = a.action_number))
      order by a.action_number)
      from ops.meeting_action a where a.meeting_id = p_meeting), '[]'::jsonb),
    'stream', coalesce((select jsonb_agg(jsonb_build_object('seq', s.seq, 'kind', s.kind,
      'actor', (select slug from public.actor where id = s.actor_id),
      'client_instance', s.client_instance, 'note_number', s.note_number,
      'action_number', s.action_number, 'revision', s.revision, 'lease_epoch', s.lease_epoch,
      'at', s.at) order by s.seq)
      from (select * from ops.meeting_stream where meeting_id = p_meeting
              and seq > coalesce(p_after_seq, 0) order by seq limit v_limit) s), '[]'::jsonb),
    'more', exists (select 1 from ops.meeting_stream where meeting_id = p_meeting
                     and seq > coalesce(p_after_seq, 0) + v_limit));
end $$;

-- -------------------------------------------------------------------------
-- Grants. carr_writer only. Argument types are spelled in full in every
-- revoke and grant -- a stale arity revokes nothing and would leave PUBLIC
-- execute on a security definer.
-- -------------------------------------------------------------------------
revoke all on function ops.start_meeting(text,text,text,text,text,text,text,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.start_meeting(text,text,text,text,text,text,text,uuid) to carr_writer;
revoke all on function ops.claim_meeting_processing(uuid,text,boolean,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.claim_meeting_processing(uuid,text,boolean,uuid) to carr_writer;
revoke all on function ops.add_meeting_note(uuid,text,integer,integer,text,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.add_meeting_note(uuid,text,integer,integer,text,uuid) to carr_writer;
revoke all on function ops.propose_meeting_action(uuid,text,jsonb,text,text,integer,integer,bigint,text,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.propose_meeting_action(uuid,text,jsonb,text,text,integer,integer,bigint,text,uuid) to carr_writer;
revoke all on function ops.decide_meeting_action(uuid,integer,text,integer,text,text,text,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.decide_meeting_action(uuid,integer,text,integer,text,text,text,uuid) to carr_writer;
revoke all on function ops.record_meeting_action_outcome(uuid,integer,text,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.record_meeting_action_outcome(uuid,integer,text,uuid) to carr_writer;
revoke all on function ops.end_meeting(uuid,text,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.end_meeting(uuid,text,uuid) to carr_writer;
revoke all on function ops.meeting_facts(uuid,bigint,integer)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.meeting_facts(uuid,bigint,integer) to carr_writer;
