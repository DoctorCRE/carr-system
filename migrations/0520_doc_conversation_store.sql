-- WR-000112: the Doc conversation store.
--
-- The shape is lifted from public.partner_room_turn (db/schema.sql:46775-46793):
-- the same body bounds, the same msg_id uniqueness, the same origin slug regex
-- and the same closed origin-channel enum. What partner-room.js enforces in
-- JavaScript ("room provenance must be server-derived", partner-room.js:179-180)
-- is enforced STRUCTURALLY here instead: ops.append_doc_conversation_turn takes
-- no channel and no actor parameter at all, so there is nothing for a caller to
-- supply.
--
-- NO TRANSACTION CONTROL. This file is the second member of the reviewed
-- ATOMIC_MIGRATION_GROUP 0519/0520/0521/0522 declared in tools/migrate.py.

do $wr112_preflight$
begin
  if not exists (select 1 from public.schema_migrations
    where filename = '0519_producer_cost_ledger.sql'
      and sha256 = 'ca2ac9aa9bb9184ca223fdfb8e99b074bf7fcbbd852d7b52943c4c19ac9eef05') then
    raise exception '0520 requires the exact 0519 producer cost ledger';
  end if;
  if to_regprocedure('ops.commit_cost_ledger_operation(text,integer,text,text,text,text,jsonb,text,integer,text,jsonb)') is null then
    raise exception '0520 requires the 0519 cost ledger admission point';
  end if;
end $wr112_preflight$;

create table ops.doc_conversation (
  id uuid primary key default gen_random_uuid(),
  title text not null check (btrim(title) <> '' and length(title) <= 200),
  created_by_actor uuid not null references public.actor(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  pinned_at timestamptz,
  archived_at timestamptz,
  visibility text not null default 'private' check (visibility in ('private','shared')),
  version integer not null default 1 check (version >= 1)
);

create table ops.doc_conversation_title_revision (
  conversation_id uuid not null references ops.doc_conversation(id),
  title text not null check (btrim(title) <> '' and length(title) <= 200),
  at timestamptz not null default now(),
  by_actor uuid not null references public.actor(id),
  primary key (conversation_id, at, by_actor)
);

create table ops.doc_conversation_turn (
  id bigint generated always as identity primary key,
  conversation_id uuid not null references ops.doc_conversation(id),
  sequence integer not null check (sequence >= 0),
  role text not null check (role in ('human','assistant','system')),
  body text not null,
  msg_id uuid not null unique,
  origin_channel text not null default 'mcp' check (origin_channel in ('mcp','browser-human')),
  origin_actor text not null check (origin_actor ~ '^[a-z0-9][a-z0-9-]{0,31}$'),
  at timestamptz not null default now(),
  constraint doc_conversation_turn_body_bounds check (btrim(body) <> '' and length(body) <= 20000),
  unique (conversation_id, sequence)
);
create index doc_conversation_turn_cursor on ops.doc_conversation_turn (conversation_id, sequence);

create table ops.doc_conversation_grant (
  conversation_id uuid not null references ops.doc_conversation(id),
  grantee_actor uuid not null references public.actor(id),
  granted_at timestamptz not null default now(),
  granted_by_actor uuid not null references public.actor(id),
  revoked_at timestamptz,
  primary key (conversation_id, grantee_actor, granted_at)
);

-- The turn log and the title history are append-only; the conversation header
-- and the access list are not (a rename moves the header, a revocation stamps
-- the grant row).
create or replace function ops.doc_conversation_rows_immutable()
returns trigger language plpgsql as $$ begin
  raise exception 'doc conversation turns and title revisions are immutable';
end $$;

create trigger doc_conversation_turn_immutable before update or delete
on ops.doc_conversation_turn for each row execute function ops.doc_conversation_rows_immutable();
create trigger doc_conversation_turn_no_truncate before truncate
on ops.doc_conversation_turn for each statement execute function ops.doc_conversation_rows_immutable();
create trigger doc_conversation_title_revision_immutable before update or delete
on ops.doc_conversation_title_revision for each row execute function ops.doc_conversation_rows_immutable();
create trigger doc_conversation_title_revision_no_truncate before truncate
on ops.doc_conversation_title_revision for each statement execute function ops.doc_conversation_rows_immutable();

-- SIEP-18 reference-monitor guards on all four relations.
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.doc_conversation for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.doc_conversation for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.doc_conversation_title_revision for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.doc_conversation_title_revision for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.doc_conversation_turn for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.doc_conversation_turn for each statement execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.doc_conversation_grant for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.doc_conversation_grant for each statement execute function ops.scac_reference_monitor_guard();

-- -------------------------------------------------------------------------
-- Two definer functions. The append takes NO origin_actor, NO origin_channel,
-- NO sequence and NO timestamp: every one of those is derived here, which is
-- the structural version of partner-room.js's runtime guard.
-- -------------------------------------------------------------------------

create or replace function ops.append_doc_conversation_turn(
  p_conversation uuid, p_role text, p_body text, p_msg_id uuid, p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_slug text; v_id bigint; v_sequence integer; v_prior ops.doc_conversation_turn%rowtype;
begin
  if not exists (select 1 from ops.doc_conversation where id = p_conversation) then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_not_found');
  end if;
  -- The acting actor, resolved from the server-installed transaction context.
  -- A caller cannot name one: there is no parameter for it.
  v_actor := ops.portfolio_writer_actor_id();
  select slug into v_slug from public.actor where id = v_actor;
  if v_slug is null then
    raise exception 'doc conversation append could not resolve the acting actor';
  end if;

  insert into ops.doc_conversation_turn(
    conversation_id, sequence, role, body, msg_id, origin_channel, origin_actor)
  select p_conversation,
         coalesce((select max(sequence) + 1 from ops.doc_conversation_turn
                    where conversation_id = p_conversation), 0),
         p_role, p_body, p_msg_id, 'mcp', v_slug
  on conflict (msg_id) do nothing
  returning id, sequence into v_id, v_sequence;

  if v_id is null then
    select * into v_prior from ops.doc_conversation_turn where msg_id = p_msg_id;
    if v_prior.id is null then
      return jsonb_build_object('ok', false, 'reason_id', 'dedup_row_vanished');
    end if;
    -- The record-signal full-field replay comparison: the same msg_id offered
    -- with different content is a REUSE, not a duplicate.
    if v_prior.conversation_id is distinct from p_conversation
       or v_prior.role is distinct from p_role
       or v_prior.body is distinct from p_body then
      return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_msg_id_reuse',
        'msg_id', p_msg_id);
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'id', v_prior.id,
      'sequence', v_prior.sequence, 'msg_id', p_msg_id, 'origin_actor', v_prior.origin_actor,
      'idempotency_key', p_idempotency_key);
  end if;

  update ops.doc_conversation set updated_at = now() where id = p_conversation;
  return jsonb_build_object('ok', true, 'deduplicated', false, 'id', v_id,
    'sequence', v_sequence, 'msg_id', p_msg_id, 'origin_actor', v_slug,
    'origin_channel', 'mcp', 'idempotency_key', p_idempotency_key);
end $$;

comment on function ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid) is
  'WR-000112: the only writer for ops.doc_conversation_turn. Origin channel, origin actor and sequence are derived here; the function takes no parameter for any of them.';

-- FOUR arguments, the acting actor in slot two -- exactly as
-- ops.read_tour_sharing_library takes a.id in its own slot. The argument is a
-- LOOKUP KEY, not an authorisation claim: the access list below still refuses a
-- non-member. Search counts are computed INSIDE this function so an
-- unauthorized caller cannot learn a private conversation exists from a count.
create or replace function ops.doc_conversation_facts(
  p_conversation_id uuid, p_actor_id text, p_after_sequence integer, p_limit integer)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_row ops.doc_conversation%rowtype; v_limit integer; v_after integer;
        v_turns jsonb; v_latest integer; v_more boolean; v_grants jsonb; v_visible integer;
begin
  begin
    v_actor := p_actor_id::uuid;
  exception when others then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_not_found');
  end;
  v_limit := least(greatest(coalesce(p_limit, 200), 1), 200);
  v_after := greatest(coalesce(p_after_sequence, 0), 0);

  select * into v_row from ops.doc_conversation where id = p_conversation_id;
  if v_row.id is null
     or not (v_row.created_by_actor = v_actor
             or exists (select 1 from ops.doc_conversation_grant g
                         where g.conversation_id = v_row.id and g.grantee_actor = v_actor
                           and g.revoked_at is null)) then
    -- One answer for absent and for not-permitted. A distinguishable refusal
    -- would itself disclose that a private conversation exists.
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_not_found');
  end if;

  select coalesce(jsonb_agg(to_jsonb(t) order by t.sequence), '[]'::jsonb) into v_turns
    from (select sequence, role, body, msg_id, origin_channel, origin_actor, at
            from ops.doc_conversation_turn
           where conversation_id = v_row.id and sequence >= v_after
           order by sequence limit v_limit) t;
  select coalesce(max(sequence), -1) into v_latest
    from ops.doc_conversation_turn where conversation_id = v_row.id;
  v_more := exists (select 1 from ops.doc_conversation_turn
                     where conversation_id = v_row.id
                       and sequence >= v_after + jsonb_array_length(v_turns));
  select coalesce(jsonb_agg(to_jsonb(g) order by g.granted_at), '[]'::jsonb) into v_grants
    from (select grantee_actor, granted_at, granted_by_actor
            from ops.doc_conversation_grant
           where conversation_id = v_row.id and revoked_at is null) g;
  -- The count this actor is entitled to see, computed here and nowhere else.
  select count(*) into v_visible from ops.doc_conversation c
   where c.created_by_actor = v_actor
      or exists (select 1 from ops.doc_conversation_grant g
                  where g.conversation_id = c.id and g.grantee_actor = v_actor
                    and g.revoked_at is null);

  return jsonb_build_object('ok', true,
    'identity', jsonb_build_object('id', v_row.id, 'title', v_row.title,
      'visibility', v_row.visibility, 'pinned_at', v_row.pinned_at,
      'archived_at', v_row.archived_at, 'version', v_row.version,
      'created_by', v_row.created_by_actor),
    'turns', v_turns, 'latest_sequence', v_latest, 'more', v_more,
    'effective_grants', v_grants, 'visible_conversation_count', v_visible);
end $$;

comment on function ops.doc_conversation_facts(uuid,text,integer,integer) is
  'WR-000112: the only read door for the Doc conversation store. Enforces the access list itself and computes the visible-conversation count inside, so a non-member learns nothing from a count.';

-- -------------------------------------------------------------------------
-- Grants. NO table grants to carr_reader: ops.doc_conversation_facts is the
-- only door, exactly as public.v_partner_room_turn is for the partner room
-- (db/schema.sql:51522). The append is authority-only; the read runs on the
-- writer connection, which is the only one that installs the acting-actor
-- context the append's sibling needs.
-- -------------------------------------------------------------------------

grant select on ops.doc_conversation,ops.doc_conversation_title_revision,
                ops.doc_conversation_turn,ops.doc_conversation_grant
  to carr_writer,carr_authority;

revoke insert,update,delete,truncate on ops.doc_conversation,ops.doc_conversation_title_revision,
  ops.doc_conversation_turn,ops.doc_conversation_grant
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

revoke all on function ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)
  to carr_authority;

revoke all on function ops.doc_conversation_facts(uuid,text,integer,integer)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.doc_conversation_facts(uuid,text,integer,integer)
  to carr_writer,carr_authority;
