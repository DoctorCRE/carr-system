-- DoctorCRE v5 slice V5-J103: the governed correspondence store.
--
-- Paired with 0701 (the SCAC v79 successor, chained from main's v78 at 0629) as
-- one atomic group in tools/migrate.py: this file's SECURITY DEFINER writers move
-- the live catalog, so it may never commit without its seal.
--
-- WHAT THIS INSTALLS, AND THE ONE THING IT DELIBERATELY CANNOT DO.
--
-- Q133.D1 says DoctorCRE STORES provenance-linked correspondence threads and the
-- drafts it writes, while the mailbox stays truth. The kernels
-- (governed-correspondence.v5.js, governed-correspondence-journey.v5.js) decide;
-- this file is the durable half, in four append-only relations:
--
--   ops.correspondence_adapter_consent            a partner's own consent for one
--                                                 adapter to READ one mailbox
--   ops.correspondence_adapter_consent_revocation the withdrawal of that consent
--   ops.correspondence_adapter_read_receipt       one thread the adapter read,
--                                                 with account and native provenance
--   ops.correspondence_draft                      a draft CARR wrote against a
--                                                 read thread, for a human to send
--
-- THERE IS NO SEND, STRUCTURALLY, AT THIS LAYER TOO:
--   * no relation has a recipient-address, destination, provider-operation,
--     scheduled-send or status column. A draft row cannot be "sent" because
--     nothing here can say so; Joe sends from the client that holds the thread.
--   * draft rows carry requires_human_send = true and dispatchable = false as
--     CHECKed constants, and a CHECK refuses a body carrying a routable address,
--     a mailto/tel-style URI or a dialable number.
--   * intended participants are CARR references whose pattern cannot hold "@".
--   * consent can name only the F10 READ operations; send_mail_message and every
--     other F10 write operation are refused by the CHECK, so no consent recorded
--     here could ever authorise an effect.
--
-- WHAT IS NOT ACTIVATED, said plainly. The read-receipt writer is granted to NO
-- runtime role: the F10 provider client that would read a mailbox does not
-- exist, and no seat is registered to hold that write. Since a draft requires a
-- read receipt (NOT NULL foreign key), no draft row can exist either until
-- (1) the adapter lands with its own seat and a forward migration grants this
-- writer to it, and (2) the partner has recorded consent here. Consent is the
-- human step; it is recorded through a humanOnly verb whose writer below checks
-- the verified-partner context names the mailbox's own partner.
--
-- IT INSTALLS FRESH, OR IT REFUSES, following the model-role store's rule: no
-- ALTER, no backfill, no adoption of an existing ops.correspondence_* object.

-- ---------------------------------------------------------------------------
-- 0. Preconditions and fresh-install refusal, before the first CREATE.
-- ---------------------------------------------------------------------------

do $preconditions$
declare v_missing text[] := array[]::text[]; v_name text;
begin
  if to_regnamespace('ops') is null then
    raise exception 'correspondence_store_blocked: schema ops does not exist' using errcode = '42704';
  end if;
  foreach v_name in array array[
    'ops.portfolio_writer_actor_id()',
    'ops.portfolio_canonical_json(jsonb)',
    'public.digest(bytea,text)'
  ] loop
    if to_regprocedure(v_name) is null then v_missing := v_missing || v_name; end if;
  end loop;
  if to_regclass('public.actor') is null then v_missing := v_missing || 'public.actor'; end if;
  foreach v_name in array array['carr_authority', 'carr_jobs', 'carr_reader', 'carr_writer'] loop
    if not exists (select 1 from pg_roles where rolname = v_name) then
      v_missing := v_missing || ('role ' || v_name);
    end if;
  end loop;
  if cardinality(v_missing) > 0 then
    raise exception 'correspondence_store_blocked: this database is missing %. Nothing was created.',
      array_to_string(v_missing, ', ') using errcode = '42704';
  end if;
end;
$preconditions$;

do $fresh_install_only$
declare v_found text[] := array[]::text[]; v_name text;
begin
  foreach v_name in array array[
    'ops.correspondence_adapter_consent', 'ops.correspondence_adapter_consent_revocation',
    'ops.correspondence_adapter_read_receipt', 'ops.correspondence_draft'
  ] loop
    if to_regclass(v_name) is not null then v_found := v_found || ('relation ' || v_name); end if;
  end loop;
  for v_name in
    select 'function ops.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')'
      from pg_proc p join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'ops' and p.proname like 'correspondence\_%'
     order by 1
  loop
    v_found := v_found || v_name;
  end loop;
  if cardinality(v_found) > 0 then
    raise exception 'correspondence_store_blocked: this database already carries % of these objects, the first being %. This file installs fresh and will not adopt an existing installation. Nothing was created.',
      cardinality(v_found), v_found[1] using errcode = '42P07';
  end if;
end;
$fresh_install_only$;

-- ---------------------------------------------------------------------------
-- 1. Constants. Each restates a JavaScript constant and names its source.
-- ---------------------------------------------------------------------------

-- identity.js ORGANIZATION_TENANT_ID.
create function ops.correspondence_tenant()
returns text language sql immutable set search_path = pg_catalog
as $$ select 'carr-internal'::text $$;

-- identity.js PARTNER_SLUGS. One binding names one partner; there is no
-- combined-coverage slot anywhere in this file (Q134: never claim both).
create function ops.correspondence_partners()
returns text[] language sql immutable set search_path = pg_catalog
as $$ select array['dell', 'joe']::text[] $$;

-- governed-correspondence.v5.js V5_J103_ADAPTER_KINDS (bound there to F10's
-- V5_F10_ADAPTER_KIND by import).
create function ops.correspondence_adapter_kinds()
returns text[] language sql immutable set search_path = pg_catalog
as $$ select array['v5_f10_partner_mail_calendar_adapter']::text[] $$;

-- partner-mail-calendar.v5.js V5_F10_READ_OPERATIONS. The ONLY things a consent
-- may name. F10's write operations — send_mail_message among them — are absent
-- on purpose, so the CHECK below refuses them by construction.
create function ops.correspondence_read_operations()
returns text[] language sql immutable set search_path = pg_catalog
as $$ select array['list_calendar_events', 'list_mail_messages',
                   'read_calendar_event_metadata', 'read_mail_message_metadata']::text[] $$;

-- governed-correspondence.v5.js V5_J103_DRAFT_KINDS.
create function ops.correspondence_draft_kinds()
returns text[] language sql immutable set search_path = pg_catalog
as $$ select array['new_message', 'reply_in_thread']::text[] $$;

create function ops.correspondence_is_sha256_ref(p text)
returns boolean language sql immutable set search_path = pg_catalog
as $$ select p is not null and p ~ '^sha256:[0-9a-f]{64}$' $$;

-- governed-correspondence.v5.js INTERNAL_REF: a CARR reference cannot hold "@".
create function ops.correspondence_is_ref(p text)
returns boolean language sql immutable set search_path = pg_catalog
as $$ select p is not null and p ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$' $$;

create function ops.correspondence_refs_valid(p text[])
returns boolean language sql immutable set search_path = pg_catalog, ops
as $$
  select p is not null and cardinality(p) between 1 and 64
     and not exists (select 1 from unnest(p) r where not ops.correspondence_is_ref(r))
     and cardinality(p) = (select count(distinct r) from unnest(p) r)
$$;

-- governed-correspondence.v5.js ROUTABLE_ADDRESS, as a PostgreSQL ARE.
create function ops.correspondence_has_routable_address(p text)
returns boolean language sql immutable set search_path = pg_catalog
as $$
  select p is not null and (
    p ~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
    or p ~* '(^|[^A-Za-z])(mailto|smtp|sms|tel|callto|skype):')
$$;

-- governed-correspondence.v5.js DIALABLE_NUMBER, draft prose only.
create function ops.correspondence_has_dialable_number(p text)
returns boolean language sql immutable set search_path = pg_catalog
as $$ select p is not null and p ~ '(^|[^0-9])\+?[0-9][0-9 ().-]{7,}[0-9]($|[^0-9])' $$;

-- governed-correspondence-store.v5.js V5_J103_PARTNER_MAILBOXES: the ONLY
-- mailbox each partner may consent for, as the digests correspondenceAccountDigest
-- computes (trim, lower-case, sha256):
--   joe  -> joe.bookout@carr.us
--   dell -> dell.mccraney@carr.us
-- A POSITIVE allowlist of (partner, digest) PAIRS: an address on nobody's list is
-- refused, and so is an address on the other partner's list. A delegated or
-- shared mailbox is therefore refused by construction. The store test recomputes
-- each pair from the JS map and asserts this function holds exactly those pairs.
create function ops.correspondence_partner_owns_account(p_partner_slug text, p_account_digest text)
returns boolean language sql immutable set search_path = pg_catalog
as $$
  select coalesce((p_partner_slug, p_account_digest) in (
    ('joe', 'sha256:577047ee6425cc34f2e7a23bb904395c2bfa7b1aba3eae2e23f525812197e3f3'),
    ('dell', 'sha256:6632cb6fcdf5e605e667c31251acce51db90e088038855d48d7d523ff84f1834')
  ), false)
$$;

-- Thread metadata with the native message and thread identifiers removed. An RFC
-- 5322 Message-ID is shaped like local@host and names a MESSAGE, not a
-- destination, so those exact fields are exempt from the address scan; every
-- other value in the metadata is still scanned.
create function ops.correspondence_metadata_without_message_ids(p jsonb)
returns jsonb language sql immutable set search_path = pg_catalog
as $$
  select case
    when jsonb_typeof(p -> 'message_refs') = 'array' then
      jsonb_set(p, '{message_refs}', coalesce((
        select jsonb_agg(case when jsonb_typeof(m) = 'object'
                              then m - 'provider_message_id' - 'provider_thread_id' else m end
                         order by o)
          from jsonb_array_elements(p -> 'message_refs') with ordinality as e(m, o)), '[]'::jsonb))
    else p
  end
$$;

create function ops.correspondence_server_instant()
returns text language sql stable set search_path = pg_catalog
as $$ select to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') $$;

create function ops.correspondence_digest(p jsonb)
returns text language sql immutable set search_path = pg_catalog, ops, public
as $$ select 'sha256:' || encode(public.digest(convert_to(ops.portfolio_canonical_json(p), 'UTF8'), 'sha256'), 'hex') $$;

-- ---------------------------------------------------------------------------
-- 2. The relations. Plain CREATE TABLE; every constraint named.
-- ---------------------------------------------------------------------------

create table ops.correspondence_adapter_consent (
  id                     uuid not null default gen_random_uuid(),
  tenant                 text not null,
  partner_slug           text not null,
  adapter_kind           text not null,
  account_digest         text not null,
  read_operations        text[] not null,
  human_quote            text not null,
  consented_by_actor_id  uuid not null,
  recorded_at            timestamptz not null default now(),
  idempotency_key        uuid not null,
  constraint correspondence_consent_pk primary key (id),
  constraint correspondence_consent_idem unique (idempotency_key),
  constraint correspondence_consent_actor_fk foreign key (consented_by_actor_id) references public.actor(id),
  constraint correspondence_consent_tenant check (tenant = ops.correspondence_tenant()),
  constraint correspondence_consent_partner check (partner_slug = any (ops.correspondence_partners())),
  constraint correspondence_consent_adapter check (adapter_kind = any (ops.correspondence_adapter_kinds())),
  constraint correspondence_consent_account check (ops.correspondence_is_sha256_ref(account_digest)),
  constraint correspondence_consent_read_only check (
    cardinality(read_operations) between 1 and 4
    and read_operations <@ ops.correspondence_read_operations()),
  constraint correspondence_consent_quote check (length(human_quote) between 1 and 2000)
);

comment on table ops.correspondence_adapter_consent is
  'A partner''s own consent for one adapter to READ one mailbox account, named by digest. Read operations only; no consent recorded here can name a send or any other provider write. Consent alone reads nothing: the adapter must also issue read receipts, and its writer is granted to no runtime role yet.';

create table ops.correspondence_adapter_consent_revocation (
  id                   uuid not null default gen_random_uuid(),
  consent_id           uuid not null,
  revoked_by_actor_id  uuid not null,
  human_quote          text not null,
  recorded_at          timestamptz not null default now(),
  idempotency_key      uuid not null,
  constraint correspondence_revocation_pk primary key (id),
  constraint correspondence_revocation_once unique (consent_id),
  constraint correspondence_revocation_idem unique (idempotency_key),
  constraint correspondence_revocation_consent_fk foreign key (consent_id) references ops.correspondence_adapter_consent(id),
  constraint correspondence_revocation_actor_fk foreign key (revoked_by_actor_id) references public.actor(id),
  constraint correspondence_revocation_quote check (length(human_quote) between 1 and 2000)
);

create table ops.correspondence_adapter_read_receipt (
  id                    uuid not null default gen_random_uuid(),
  tenant                text not null,
  consent_id            uuid not null,
  partner_slug          text not null,
  adapter_kind          text not null,
  account_digest        text not null,
  source_system         text not null,
  native_id             text not null,
  native_id_epoch       integer not null,
  thread_metadata       jsonb not null,
  metadata_digest       text not null,
  recorded_by_actor_id  uuid not null,
  recorded_at           timestamptz not null default now(),
  idempotency_key       uuid not null,
  constraint correspondence_receipt_pk primary key (id),
  constraint correspondence_receipt_idem unique (idempotency_key),
  constraint correspondence_receipt_native unique (tenant, partner_slug, source_system, native_id, native_id_epoch, metadata_digest),
  constraint correspondence_receipt_consent_fk foreign key (consent_id) references ops.correspondence_adapter_consent(id),
  constraint correspondence_receipt_actor_fk foreign key (recorded_by_actor_id) references public.actor(id),
  constraint correspondence_receipt_tenant check (tenant = ops.correspondence_tenant()),
  constraint correspondence_receipt_partner check (partner_slug = any (ops.correspondence_partners())),
  constraint correspondence_receipt_adapter check (adapter_kind = any (ops.correspondence_adapter_kinds())),
  constraint correspondence_receipt_account check (ops.correspondence_is_sha256_ref(account_digest)),
  constraint correspondence_receipt_source check (source_system ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$'),
  constraint correspondence_receipt_native_id check (native_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$'),
  constraint correspondence_receipt_epoch check (native_id_epoch between 0 and 1000000),
  constraint correspondence_receipt_metadata_object check (jsonb_typeof(thread_metadata) = 'object'),
  -- Only correspondence the kernel classified as related crosses; unrelated is
  -- excluded and ambiguous is withheld private upstream of this row (Q134).
  constraint correspondence_receipt_related check (thread_metadata ->> 'relevance_state' = 'related'),
  -- The mailbox stays truth: typed metadata only. No subject, body, preview,
  -- snippet or attachment bytes, and no routable address anywhere in it.
  constraint correspondence_receipt_no_content check (
    not (thread_metadata ?| array['subject', 'body', 'preview', 'snippet', 'html', 'raw', 'attachment_bytes', 'message_text'])),
  constraint correspondence_receipt_no_address check (
    not ops.correspondence_has_routable_address(ops.correspondence_metadata_without_message_ids(thread_metadata)::text)),
  constraint correspondence_receipt_digest check (ops.correspondence_is_sha256_ref(metadata_digest))
);

comment on table ops.correspondence_adapter_read_receipt is
  'One thread an authorized adapter read, carrying the partner, the mailbox account digest and the full (source_system, native_id, native_id_epoch) identity. Its writer is granted to no runtime role until the F10 adapter and its seat exist.';

create table ops.correspondence_draft (
  id                         uuid not null default gen_random_uuid(),
  tenant                     text not null,
  partner_slug               text not null,
  read_receipt_id            uuid not null,
  draft_kind                 text not null,
  intended_participant_refs  text[] not null,
  draft_body                 text not null,
  draft_digest               text not null,
  requires_human_send        boolean not null default true,
  dispatchable               boolean not null default false,
  recorded_by_actor_id       uuid not null,
  recorded_at                timestamptz not null default now(),
  idempotency_key            uuid not null,
  constraint correspondence_draft_pk primary key (id),
  constraint correspondence_draft_idem unique (idempotency_key),
  constraint correspondence_draft_receipt_fk foreign key (read_receipt_id) references ops.correspondence_adapter_read_receipt(id),
  constraint correspondence_draft_actor_fk foreign key (recorded_by_actor_id) references public.actor(id),
  constraint correspondence_draft_tenant check (tenant = ops.correspondence_tenant()),
  constraint correspondence_draft_partner check (partner_slug = any (ops.correspondence_partners())),
  constraint correspondence_draft_kind check (draft_kind = any (ops.correspondence_draft_kinds())),
  constraint correspondence_draft_participants check (ops.correspondence_refs_valid(intended_participant_refs)),
  constraint correspondence_draft_body_length check (length(draft_body) between 1 and 20000),
  constraint correspondence_draft_body_no_address check (not ops.correspondence_has_routable_address(draft_body)),
  constraint correspondence_draft_body_no_number check (not ops.correspondence_has_dialable_number(draft_body)),
  constraint correspondence_draft_digest check (ops.correspondence_is_sha256_ref(draft_digest)),
  constraint correspondence_draft_human_sends check (requires_human_send),
  constraint correspondence_draft_never_dispatchable check (not dispatchable)
);

comment on table ops.correspondence_draft is
  'A draft CARR wrote against a thread an adapter read. It has no recipient address, no provider operation and no status: nothing in CARR can mark it sent, and nothing can send it. requires_human_send is true and dispatchable is false by CHECK.';

-- 2b. Assert what was just created carries every named constraint, from the catalog.
do $shape$
declare v_missing text[] := array[]::text[]; v_name text;
begin
  foreach v_name in array array[
    'correspondence_consent_read_only', 'correspondence_consent_partner',
    'correspondence_revocation_once', 'correspondence_receipt_related',
    'correspondence_receipt_no_content', 'correspondence_receipt_no_address',
    'correspondence_draft_body_no_address', 'correspondence_draft_body_no_number',
    'correspondence_draft_human_sends', 'correspondence_draft_never_dispatchable',
    'correspondence_draft_participants', 'correspondence_draft_receipt_fk'
  ] loop
    if not exists (select 1 from pg_constraint c join pg_namespace n on n.oid = c.connamespace
                    where n.nspname = 'ops' and c.conname = v_name) then
      v_missing := v_missing || v_name;
    end if;
  end loop;
  if cardinality(v_missing) > 0 then
    raise exception 'correspondence_store_blocked: created relations lack constraints %', v_missing using errcode = '42704';
  end if;
end;
$shape$;

-- ---------------------------------------------------------------------------
-- 3. Append-only.
-- ---------------------------------------------------------------------------

create function ops.correspondence_rows_immutable()
returns trigger language plpgsql set search_path = pg_catalog, ops
as $$
begin
  raise exception 'DoctorCRE v5 correspondence rows are append-only; % on %.% is refused',
    tg_op, tg_table_schema, tg_table_name using errcode = '42501';
end;
$$;

do $append_only$
declare t text;
begin
  foreach t in array array[
    'correspondence_adapter_consent', 'correspondence_adapter_consent_revocation',
    'correspondence_adapter_read_receipt', 'correspondence_draft'
  ] loop
    execute format('create trigger %I before update or delete on ops.%I for each row execute function ops.correspondence_rows_immutable()', t || '_append_only', t);
    execute format('create trigger %I before truncate on ops.%I for each statement execute function ops.correspondence_rows_immutable()', t || '_no_truncate', t);
  end loop;
end;
$append_only$;

-- ---------------------------------------------------------------------------
-- 4. Writers. Every actor, partner, account and instant is DERIVED here.
-- ---------------------------------------------------------------------------

-- Is this consent in force? Present and not revoked.
create function ops.correspondence_consent_in_force(p_consent_id uuid)
returns boolean language sql stable security definer set search_path = pg_catalog, ops
as $$
  select exists (select 1 from ops.correspondence_adapter_consent c where c.id = p_consent_id)
     and not exists (select 1 from ops.correspondence_adapter_consent_revocation r where r.consent_id = p_consent_id)
$$;

-- THE HUMAN STEP. A partner consents for their OWN mailbox: the verified-partner
-- context the server sets only for a humanOnly act must name the partner whose
-- mailbox this is. Under Joe's 2026-08-26 humanOnly ruling that context is set
-- for the partner AND for an agent the partner sponsors acting on the partner's
-- quoted words, so such an agent may record the consent (human_quote carries the
-- words); an agent session WITHOUT that context is refused.
--
-- WHAT IS CHECKED ABOUT THE MAILBOX, exactly: the partner is derived, and the
-- (partner, account_digest) pair must be on ops.correspondence_partner_owns_account's
-- allowlist; anything else is refused as account_not_partners_own.
create function ops.correspondence_record_adapter_consent(
  p_partner_slug text, p_adapter_kind text, p_account_digest text,
  p_read_operations text[], p_human_quote text, p_idempotency_key uuid)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_actor uuid; v_verified text; v_existing ops.correspondence_adapter_consent%rowtype; v_id uuid;
begin
  v_actor := ops.portfolio_writer_actor_id();
  v_verified := nullif(current_setting('carr.verified_human_actor_slug', true), '');
  if v_verified is distinct from p_partner_slug then
    raise exception 'correspondence consent for % mailbox needs that partner''s own verified context; this transaction names %',
      p_partner_slug, coalesce(v_verified, '(none)') using errcode = '42501';
  end if;
  if not ops.correspondence_partner_owns_account(p_partner_slug, p_account_digest) then
    raise exception 'account_not_partners_own: % may consent only for their own carr.us mailbox; any other account, including the other partner''s and any delegated or shared mailbox, is refused',
      p_partner_slug using errcode = '42501';
  end if;
  select * into v_existing from ops.correspondence_adapter_consent where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.partner_slug is distinct from p_partner_slug or v_existing.adapter_kind is distinct from p_adapter_kind
       or v_existing.account_digest is distinct from p_account_digest
       or v_existing.read_operations is distinct from p_read_operations
       or v_existing.consented_by_actor_id is distinct from v_actor then
      raise exception 'correspondence consent idempotency key % was already used for a different consent', p_idempotency_key using errcode = '23505';
    end if;
    return v_existing.id;
  end if;
  if exists (select 1 from ops.correspondence_adapter_consent c
              where c.partner_slug = p_partner_slug and c.adapter_kind = p_adapter_kind
                and c.account_digest = p_account_digest and ops.correspondence_consent_in_force(c.id)) then
    raise exception 'a consent for this partner, adapter and account is already in force; revoke it first' using errcode = '23505';
  end if;
  insert into ops.correspondence_adapter_consent(tenant, partner_slug, adapter_kind, account_digest,
    read_operations, human_quote, consented_by_actor_id, idempotency_key)
  values (ops.correspondence_tenant(), p_partner_slug, p_adapter_kind, p_account_digest,
    (select array_agg(distinct o order by o) from unnest(p_read_operations) o), p_human_quote, v_actor, p_idempotency_key)
  returning id into v_id;
  return v_id;
end;
$$;

create function ops.correspondence_revoke_adapter_consent(
  p_consent_id uuid, p_human_quote text, p_idempotency_key uuid)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_actor uuid; v_verified text; v_partner text; v_existing ops.correspondence_adapter_consent_revocation%rowtype; v_id uuid;
begin
  v_actor := ops.portfolio_writer_actor_id();
  select partner_slug into v_partner from ops.correspondence_adapter_consent where id = p_consent_id;
  if not found then
    raise exception 'correspondence consent % does not exist', p_consent_id using errcode = '42704';
  end if;
  v_verified := nullif(current_setting('carr.verified_human_actor_slug', true), '');
  if v_verified is distinct from v_partner then
    raise exception 'revoking % mailbox consent needs that partner''s own verified context; this transaction names %',
      v_partner, coalesce(v_verified, '(none)') using errcode = '42501';
  end if;
  select * into v_existing from ops.correspondence_adapter_consent_revocation where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.consent_id is distinct from p_consent_id or v_existing.revoked_by_actor_id is distinct from v_actor then
      raise exception 'correspondence revocation idempotency key % was already used for a different revocation', p_idempotency_key using errcode = '23505';
    end if;
    return v_existing.id;
  end if;
  insert into ops.correspondence_adapter_consent_revocation(consent_id, revoked_by_actor_id, human_quote, idempotency_key)
  values (p_consent_id, v_actor, p_human_quote, p_idempotency_key)
  returning id into v_id;
  return v_id;
end;
$$;

-- THE ADAPTER'S WRITER. Granted to nobody in this file (see section 6). The
-- partner, adapter and account come from the consent row, never a parameter, so
-- a receipt cannot be attributed to a mailbox other than the one consented.
-- OWED WITH THE F10 INSTALLATION BINDING: before this writer is granted to the
-- adapter seat, it must also match the installation's own account against the
-- consent's account_digest, which is what proves the consenting partner owns
-- the mailbox being read.
create function ops.correspondence_record_read_receipt(
  p_consent_id uuid, p_source_system text, p_native_id text, p_native_id_epoch integer,
  p_thread_metadata jsonb, p_idempotency_key uuid)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_actor uuid; v_consent ops.correspondence_adapter_consent%rowtype; v_digest text; v_id uuid;
begin
  v_actor := ops.portfolio_writer_actor_id();
  select * into v_consent from ops.correspondence_adapter_consent where id = p_consent_id;
  if not found or not ops.correspondence_consent_in_force(p_consent_id) then
    raise exception 'no correspondence consent in force for %; nothing is read without one', p_consent_id using errcode = '42501';
  end if;
  v_digest := ops.correspondence_digest(p_thread_metadata);
  select id into v_id from ops.correspondence_adapter_read_receipt where idempotency_key = p_idempotency_key;
  if found then return v_id; end if;
  insert into ops.correspondence_adapter_read_receipt(tenant, consent_id, partner_slug, adapter_kind, account_digest,
    source_system, native_id, native_id_epoch, thread_metadata, metadata_digest, recorded_by_actor_id, idempotency_key)
  values (ops.correspondence_tenant(), v_consent.id, v_consent.partner_slug, v_consent.adapter_kind, v_consent.account_digest,
    p_source_system, p_native_id, p_native_id_epoch, p_thread_metadata, v_digest, v_actor, p_idempotency_key)
  returning id into v_id;
  return v_id;
end;
$$;

-- THE DRAFT WRITER. Needs a read receipt whose consent is still in force, and
-- the transaction's sponsoring partner must be the mailbox's partner: an agent
-- working for Joe does not draft in Dell's thread. The partner is copied from
-- the receipt, never taken from a parameter. There is no parameter through
-- which a destination, a send instruction or a status could arrive.
create function ops.correspondence_record_draft(
  p_read_receipt_id uuid, p_draft_kind text, p_intended_participant_refs text[],
  p_draft_body text, p_idempotency_key uuid)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_actor uuid; v_receipt ops.correspondence_adapter_read_receipt%rowtype; v_sponsor text;
        v_digest text; v_existing ops.correspondence_draft%rowtype; v_id uuid;
begin
  v_actor := ops.portfolio_writer_actor_id();
  select * into v_receipt from ops.correspondence_adapter_read_receipt where id = p_read_receipt_id;
  if not found then
    raise exception 'no read receipt %; a draft is written against correspondence that was actually read', p_read_receipt_id using errcode = '42704';
  end if;
  if not ops.correspondence_consent_in_force(v_receipt.consent_id) then
    raise exception 'the consent behind read receipt % has been revoked; no new draft is written against it', p_read_receipt_id using errcode = '42501';
  end if;
  v_sponsor := nullif(current_setting('carr.sponsoring_human_slug', true), '');
  if v_sponsor is distinct from v_receipt.partner_slug then
    raise exception 'drafting in % correspondence needs that partner as this transaction''s sponsor; it names %',
      v_receipt.partner_slug, coalesce(v_sponsor, '(none)') using errcode = '42501';
  end if;
  v_digest := ops.correspondence_digest(jsonb_build_object(
    'read_receipt_id', p_read_receipt_id::text, 'draft_kind', p_draft_kind,
    'intended_participant_refs', to_jsonb(p_intended_participant_refs), 'draft_body', p_draft_body));
  select * into v_existing from ops.correspondence_draft where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.draft_digest is distinct from v_digest or v_existing.recorded_by_actor_id is distinct from v_actor then
      raise exception 'correspondence draft idempotency key % was already used for a different draft', p_idempotency_key using errcode = '23505';
    end if;
    return v_existing.id;
  end if;
  insert into ops.correspondence_draft(tenant, partner_slug, read_receipt_id, draft_kind,
    intended_participant_refs, draft_body, draft_digest, recorded_by_actor_id, idempotency_key)
  values (ops.correspondence_tenant(), v_receipt.partner_slug, p_read_receipt_id, p_draft_kind,
    p_intended_participant_refs, p_draft_body, v_digest, v_actor, p_idempotency_key)
  returning id into v_id;
  return v_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- 5. Readers. Partner-fenced by the server-derived sponsor.
-- ---------------------------------------------------------------------------

-- Readiness: the facts a caller needs to see what is owed, and nothing private.
create function ops.correspondence_readiness()
returns jsonb language sql stable security definer set search_path = pg_catalog, ops
as $$
  select jsonb_build_object(
    'server_instant', ops.correspondence_server_instant(),
    'partners', (
      select jsonb_agg(jsonb_build_object(
        'partner_slug', p,
        'consents_in_force', (select count(*) from ops.correspondence_adapter_consent c
                               where c.partner_slug = p and ops.correspondence_consent_in_force(c.id)),
        'consents_revoked', (select count(*) from ops.correspondence_adapter_consent c
                              join ops.correspondence_adapter_consent_revocation r on r.consent_id = c.id
                             where c.partner_slug = p),
        'read_receipts', (select count(*) from ops.correspondence_adapter_read_receipt t where t.partner_slug = p),
        'drafts', (select count(*) from ops.correspondence_draft d where d.partner_slug = p))
        order by p)
      from unnest(ops.correspondence_partners()) p),
    'read_receipt_writer_granted_to_runtime', exists (
      select 1 from unnest(array['carr_reader', 'carr_writer', 'carr_jobs', 'carr_authority']) r
       where has_function_privilege(r, 'ops.correspondence_record_read_receipt(uuid,text,text,integer,jsonb,uuid)', 'EXECUTE')))
$$;

-- The provenance-preserving read: every stored receipt for one native identity,
-- in the sponsor's own mailbox only.
create function ops.correspondence_thread_readback(p_source_system text, p_native_id text, p_native_id_epoch integer)
returns jsonb language sql stable security definer set search_path = pg_catalog, ops
as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'read_receipt_id', t.id::text,
      'partner_slug', t.partner_slug,
      'adapter_kind', t.adapter_kind,
      'account_digest', t.account_digest,
      'native_identity', jsonb_build_object('source_system', t.source_system, 'native_id', t.native_id,
                                            'native_id_epoch', t.native_id_epoch),
      'thread_metadata', t.thread_metadata,
      'metadata_digest', t.metadata_digest,
      'recomputed_digest', ops.correspondence_digest(t.thread_metadata),
      'consent_in_force', ops.correspondence_consent_in_force(t.consent_id),
      'recorded_at', to_char(t.recorded_at at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'))
      order by t.recorded_at, t.id), '[]'::jsonb)
    from ops.correspondence_adapter_read_receipt t
   where t.tenant = ops.correspondence_tenant()
     and t.partner_slug = nullif(current_setting('carr.sponsoring_human_slug', true), '')
     and t.source_system = p_source_system and t.native_id = p_native_id and t.native_id_epoch = p_native_id_epoch
$$;

-- ---------------------------------------------------------------------------
-- 6. Grants. No role holds direct DML. The receipt writer is granted to NOBODY.
-- ---------------------------------------------------------------------------

revoke all on ops.correspondence_adapter_consent, ops.correspondence_adapter_consent_revocation,
  ops.correspondence_adapter_read_receipt, ops.correspondence_draft
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function
  ops.correspondence_tenant(), ops.correspondence_partners(), ops.correspondence_adapter_kinds(),
  ops.correspondence_read_operations(), ops.correspondence_draft_kinds(),
  ops.correspondence_is_sha256_ref(text), ops.correspondence_is_ref(text), ops.correspondence_refs_valid(text[]),
  ops.correspondence_has_routable_address(text), ops.correspondence_has_dialable_number(text),
  ops.correspondence_server_instant(), ops.correspondence_digest(jsonb),
  ops.correspondence_partner_owns_account(text,text), ops.correspondence_metadata_without_message_ids(jsonb),
  ops.correspondence_rows_immutable(), ops.correspondence_consent_in_force(uuid),
  ops.correspondence_record_adapter_consent(text,text,text,text[],text,uuid),
  ops.correspondence_revoke_adapter_consent(uuid,text,uuid),
  ops.correspondence_record_read_receipt(uuid,text,text,integer,jsonb,uuid),
  ops.correspondence_record_draft(uuid,text,text[],text,uuid),
  ops.correspondence_readiness(),
  ops.correspondence_thread_readback(text,text,integer)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

grant execute on function
  ops.correspondence_tenant(), ops.correspondence_partners(), ops.correspondence_adapter_kinds(),
  ops.correspondence_read_operations(), ops.correspondence_draft_kinds(),
  ops.correspondence_server_instant(), ops.correspondence_readiness(),
  ops.correspondence_thread_readback(text,text,integer)
  to carr_reader, carr_writer, carr_authority;

-- Consent and revocation are humanOnly verbs; mcp.js runs them under the writer
-- bundle with the verified-partner context set, and the writers re-check it.
grant execute on function
  ops.correspondence_record_adapter_consent(text,text,text,text[],text,uuid),
  ops.correspondence_revoke_adapter_consent(uuid,text,uuid)
  to carr_writer;

grant execute on function ops.correspondence_record_draft(uuid,text,text[],text,uuid) to carr_writer;

-- ops.correspondence_record_read_receipt: deliberately NO grant. The adapter
-- that may call it and the seat that would hold it do not exist yet; granting it
-- is a reviewed forward migration in the F10 adapter slice.
