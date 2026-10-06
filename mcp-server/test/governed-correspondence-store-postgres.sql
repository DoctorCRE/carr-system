-- V5-J103 governed correspondence store: transaction-scoped PostgreSQL proof.
--
-- HOW TO RUN, exactly:
--   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f mcp-server/test/governed-correspondence-store-postgres.sql
-- on a database migrated through the governed correspondence store migration,
-- as the schema owner (the local-db-ci migration lane runs it that way).
--
-- EVERYTHING IS ROLLED BACK. No row survives, no role or actor is created, no
-- provider or network is touched. The seeded actors joe, dell and automation
-- (migration 0002) are used as they are.
--
-- WHAT IT PROVES, none of which reading the SQL can show:
--   * consent is read-only by CHECK: send_mail_message and every other F10 write
--     operation is refused; consent needs the mailbox partner's verified context
--     (the partner, or a sponsored agent the server gives that context); and the
--     account is a POSITIVE allowlist of (partner, digest) pairs: joe only for
--     joe.bookout@carr.us, dell only for dell.mccraney@carr.us. The other
--     partner's mailbox, either sign-in address, a shared mailbox and an unknown
--     one are all refused as account_not_partners_own
--   * a read receipt carries partner, account and native provenance copied from
--     the consent, refuses raw content and routable addresses, and admits only
--     correspondence classified as related
--   * a draft can never be dispatchable or skip the human send, can never hold a
--     routable address or a dialable number, and cannot exist without a receipt
--     whose consent is still in force and whose partner sponsors the transaction
--   * every relation is append-only and no runtime role holds DML or the
--     receipt writer
--   * no correspondence relation has a column a destination, send instruction or
--     sent status could live in

\set ON_ERROR_STOP on
begin;

-- The relation shapes: no destination or send column anywhere.
do $$
declare v_bad text;
begin
  select string_agg(table_name || '.' || column_name, ', ') into v_bad
    from information_schema.columns
   where table_schema = 'ops' and table_name like 'correspondence\_%'
     and (column_name ~ '(recipient|destination|outbound|provider_operation|to_address|reply_to|smtp|schedule)'
          or column_name ~ '(^|_)(sent|status|send_at|dispatch_at|delivered)(_|$)'
          or (column_name ~ '(send|dispatch)' and column_name not in ('requires_human_send', 'dispatchable')));
  if v_bad is not null then
    raise exception 'correspondence relations carry dispatch-shaped columns: %', v_bad;
  end if;
end $$;

-- No runtime role holds DML, and nobody holds the receipt writer.
do $$
declare r text; t text;
begin
  foreach r in array array['carr_reader', 'carr_writer', 'carr_jobs', 'carr_authority'] loop
    foreach t in array array['ops.correspondence_adapter_consent', 'ops.correspondence_adapter_consent_revocation',
                             'ops.correspondence_adapter_read_receipt', 'ops.correspondence_draft'] loop
      if has_table_privilege(r, t, 'INSERT') or has_table_privilege(r, t, 'UPDATE') or has_table_privilege(r, t, 'DELETE') then
        raise exception 'role % holds DML on %', r, t;
      end if;
    end loop;
    if has_function_privilege(r, 'ops.correspondence_record_read_receipt(uuid,text,text,integer,jsonb,uuid)', 'EXECUTE') then
      raise exception 'role % may write read receipts; no adapter seat exists to hold that', r;
    end if;
  end loop;
  if (ops.correspondence_readiness() ->> 'read_receipt_writer_granted_to_runtime')::boolean then
    raise exception 'readiness reports the receipt writer granted';
  end if;
  if not has_function_privilege('carr_writer', 'ops.correspondence_record_draft(uuid,text,text[],text,uuid)', 'EXECUTE') then
    raise exception 'carr_writer cannot reach the draft writer';
  end if;
end $$;

-- Consent. Joe's session; the fixture consent is recorded last, after the
-- refusals, because one consent per (partner, adapter, account) may be in force.
select set_config('carr.acting_actor_slug', 'joe', true),
       set_config('carr.verified_human_actor_slug', 'joe', true),
       set_config('carr.sponsoring_human_slug', 'joe', true);

create temp table j103_fixture(k text primary key, v uuid) on commit drop;

-- The allowlist refusal, and ONLY it: the error must name account_not_partners_own.
create function pg_temp.j103_refused_not_own(p_partner text, p_digest text, p_key uuid, p_label text)
returns void language plpgsql as $f$
begin
  begin
    perform ops.correspondence_record_adapter_consent(p_partner, 'v5_f10_partner_mail_calendar_adapter',
      p_digest, array['list_mail_messages'], 'q', p_key);
  exception when insufficient_privilege then
    if sqlerrm not like 'account_not_partners_own:%' then
      raise exception '% was refused for the wrong reason: %', p_label, sqlerrm;
    end if;
    return;
  end;
  raise exception '% was accepted', p_label;
end $f$;

-- A send operation cannot be consented, even for Joe's own mailbox.
do $$
begin
  begin
    perform ops.correspondence_record_adapter_consent('joe', 'v5_f10_partner_mail_calendar_adapter',
      'sha256:577047ee6425cc34f2e7a23bb904395c2bfa7b1aba3eae2e23f525812197e3f3', array['send_mail_message'], 'q', '00000000-0000-4000-8000-000000000002');
    raise exception 'send_mail_message was consented';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_adapter_consent('joe', 'v5_f10_partner_mail_calendar_adapter',
      'sha256:577047ee6425cc34f2e7a23bb904395c2bfa7b1aba3eae2e23f525812197e3f3', array['read_mail_message_metadata', 'move_mail_message'], 'q', '00000000-0000-4000-8000-000000000003');
    raise exception 'a write operation rode in beside a read one';
  exception when check_violation then null;
  end;
  -- Joe's session cannot consent AS Dell.
  begin
    perform ops.correspondence_record_adapter_consent('dell', 'v5_f10_partner_mail_calendar_adapter',
      'sha256:6632cb6fcdf5e605e667c31251acce51db90e088038855d48d7d523ff84f1834', array['list_mail_messages'], 'q', '00000000-0000-4000-8000-000000000004');
    raise exception 'one partner consented as the other';
  exception when insufficient_privilege then null;
  end;
end $$;

-- Joe himself: refused Dell's carr.us mailbox, Dell's sign-in address, his own
-- sign-in address, a shared mailbox and an unknown address.
select pg_temp.j103_refused_not_own('joe', 'sha256:6632cb6fcdf5e605e667c31251acce51db90e088038855d48d7d523ff84f1834', '00000000-0000-4000-8000-000000000111', 'Joe for dell.mccraney@carr.us');
select pg_temp.j103_refused_not_own('joe', 'sha256:7b9d432e5baf34a7ae12cc8128e9a9645645b0c51f7980e1fb7d28e8a7617f69', '00000000-0000-4000-8000-000000000112', 'Joe for Dell''s sign-in address');
select pg_temp.j103_refused_not_own('joe', 'sha256:2da48000d09255c32c966ef96d357cfe1a408fb053689706f35789af27c72963', '00000000-0000-4000-8000-000000000113', 'Joe for his sign-in address');
select pg_temp.j103_refused_not_own('joe', 'sha256:74dde74495e863411bd14b2e10cb6bb1fd26d5945278a684e9c62c61733673f7', '00000000-0000-4000-8000-000000000114', 'Joe for a shared mailbox');
select pg_temp.j103_refused_not_own('joe', 'sha256:b2d1ab7eb48cf45f70f8ed1074b4faab8b1e83735c7b8fd897c5ca22cbcc004d', '00000000-0000-4000-8000-000000000115', 'Joe for an unknown address');

-- Sponsored agents, under Joe's 2026-08-26 humanOnly ruling: an agent session
-- WITHOUT the verified-partner context is refused; a Joe-sponsored agent that the
-- server gives Joe's verified context for a humanOnly act (acting on his quoted
-- words) is held to Joe's allowlist exactly, and its consent names the agent.
do $$
declare v uuid;
begin
  perform set_config('carr.acting_actor_slug', 'automation', true);
  perform set_config('carr.verified_human_actor_slug', '', true);
  begin
    perform ops.correspondence_record_adapter_consent('joe', 'v5_f10_partner_mail_calendar_adapter',
      'sha256:577047ee6425cc34f2e7a23bb904395c2bfa7b1aba3eae2e23f525812197e3f3', array['list_mail_messages'], 'q', '00000000-0000-4000-8000-000000000005');
    raise exception 'an agent without the verified-partner context recorded consent';
  exception when insufficient_privilege then null;
  end;
  perform set_config('carr.verified_human_actor_slug', 'joe', true);
  perform pg_temp.j103_refused_not_own('joe', 'sha256:6632cb6fcdf5e605e667c31251acce51db90e088038855d48d7d523ff84f1834', '00000000-0000-4000-8000-000000000116', 'a Joe-sponsored agent for dell.mccraney@carr.us');
  perform pg_temp.j103_refused_not_own('joe', 'sha256:7b9d432e5baf34a7ae12cc8128e9a9645645b0c51f7980e1fb7d28e8a7617f69', '00000000-0000-4000-8000-000000000117', 'a Joe-sponsored agent for Dell''s sign-in address');
  perform pg_temp.j103_refused_not_own('joe', 'sha256:b2d1ab7eb48cf45f70f8ed1074b4faab8b1e83735c7b8fd897c5ca22cbcc004d', '00000000-0000-4000-8000-000000000118', 'a Joe-sponsored agent for an unknown address');
  v := ops.correspondence_record_adapter_consent('joe', 'v5_f10_partner_mail_calendar_adapter',
    'sha256:577047ee6425cc34f2e7a23bb904395c2bfa7b1aba3eae2e23f525812197e3f3', array['list_calendar_events'], 'Joe: "yes, read my calendar"',
    '00000000-0000-4000-8000-000000000006');
  if (select consented_by_actor_id from ops.correspondence_adapter_consent where id = v)
     is distinct from (select id from public.actor where slug = 'automation') then
    raise exception 'a sponsored consent was not attributed to the acting agent';
  end if;
  -- Withdrawn again so the fixture consent below can be the one in force.
  perform ops.correspondence_revoke_adapter_consent(v, 'fixture: withdraw the sponsored consent',
    '00000000-0000-4000-8000-000000000119');
  perform set_config('carr.acting_actor_slug', 'joe', true);
end $$;

-- Dell: refused Joe's carr.us mailbox, Joe's sign-in address and an unknown
-- address; accepted for his own carr.us mailbox.
do $$
begin
  perform set_config('carr.acting_actor_slug', 'dell', true);
  perform set_config('carr.verified_human_actor_slug', 'dell', true);
  perform pg_temp.j103_refused_not_own('dell', 'sha256:577047ee6425cc34f2e7a23bb904395c2bfa7b1aba3eae2e23f525812197e3f3', '00000000-0000-4000-8000-000000000120', 'Dell for joe.bookout@carr.us');
  perform pg_temp.j103_refused_not_own('dell', 'sha256:2da48000d09255c32c966ef96d357cfe1a408fb053689706f35789af27c72963', '00000000-0000-4000-8000-000000000121', 'Dell for Joe''s sign-in address');
  perform pg_temp.j103_refused_not_own('dell', 'sha256:b2d1ab7eb48cf45f70f8ed1074b4faab8b1e83735c7b8fd897c5ca22cbcc004d', '00000000-0000-4000-8000-000000000122', 'Dell for an unknown address');
  perform ops.correspondence_record_adapter_consent('dell', 'v5_f10_partner_mail_calendar_adapter',
    'sha256:6632cb6fcdf5e605e667c31251acce51db90e088038855d48d7d523ff84f1834', array['list_mail_messages'], 'Dell: "yes"', '00000000-0000-4000-8000-000000000123');
  perform set_config('carr.acting_actor_slug', 'joe', true);
  perform set_config('carr.verified_human_actor_slug', 'joe', true);
end $$;

-- Joe for his own carr.us mailbox: accepted. This is the fixture consent.
insert into j103_fixture values ('consent', ops.correspondence_record_adapter_consent(
  'joe', 'v5_f10_partner_mail_calendar_adapter',
  'sha256:577047ee6425cc34f2e7a23bb904395c2bfa7b1aba3eae2e23f525812197e3f3',
  array['read_mail_message_metadata', 'list_mail_messages'],
  'fixture quote', '00000000-0000-4000-8000-000000000001'));

-- Idempotent replay returns the same row.
do $$
begin
  if ops.correspondence_record_adapter_consent('joe', 'v5_f10_partner_mail_calendar_adapter',
       'sha256:577047ee6425cc34f2e7a23bb904395c2bfa7b1aba3eae2e23f525812197e3f3', array['list_mail_messages', 'read_mail_message_metadata'],
       'fixture quote', '00000000-0000-4000-8000-000000000001')
     is distinct from (select v from j103_fixture where k = 'consent') then
    raise exception 'consent replay returned a different row';
  end if;
end $$;

-- Read receipts (as the owner: no runtime role may call this writer).
insert into j103_fixture values ('receipt', ops.correspondence_record_read_receipt(
  (select v from j103_fixture where k = 'consent'), 'fixture-mail', 'thread-1', 0,
  jsonb_build_object('relevance_state', 'related', 'correspondence_state', 'awaiting_reply',
    'participants', jsonb_build_array(jsonb_build_object('participant_ref', 'party-fixture-2',
      'address_digest', 'sha256:' || repeat('e', 64), 'role', 'counterparty'))),
  '00000000-0000-4000-8000-000000000010'));

do $$
declare v jsonb;
begin
  v := ops.correspondence_thread_readback('fixture-mail', 'thread-1', 0);
  if jsonb_array_length(v) <> 1 then raise exception 'readback returned % rows', jsonb_array_length(v); end if;
  if v -> 0 ->> 'partner_slug' <> 'joe' or v -> 0 ->> 'account_digest' <> 'sha256:577047ee6425cc34f2e7a23bb904395c2bfa7b1aba3eae2e23f525812197e3f3'
     or v -> 0 -> 'native_identity' ->> 'native_id' <> 'thread-1'
     or (v -> 0 -> 'native_identity' ->> 'native_id_epoch')::int <> 0 then
    raise exception 'readback lost provenance: %', v;
  end if;
  if v -> 0 ->> 'metadata_digest' <> v -> 0 ->> 'recomputed_digest' then
    raise exception 'stored and recomputed digests differ';
  end if;
  -- Dell's session sees none of Joe's mail.
  perform set_config('carr.sponsoring_human_slug', 'dell', true);
  if jsonb_array_length(ops.correspondence_thread_readback('fixture-mail', 'thread-1', 0)) <> 0 then
    raise exception 'one partner read the other''s correspondence';
  end if;
  perform set_config('carr.sponsoring_human_slug', 'joe', true);
end $$;

do $$
declare c uuid := (select v from j103_fixture where k = 'consent');
begin
  begin
    perform ops.correspondence_record_read_receipt(c, 'fixture-mail', 'thread-2', 0,
      '{"relevance_state":"related","subject":"x"}'::jsonb, '00000000-0000-4000-8000-000000000011');
    raise exception 'a subject line crossed';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_read_receipt(c, 'fixture-mail', 'thread-3', 0,
      '{"relevance_state":"related","note":"someone@example.invalid.test"}'::jsonb, '00000000-0000-4000-8000-000000000012');
    raise exception 'an address crossed';
  exception when check_violation then null;
  end;
  -- An RFC Message-ID in the message refs is a message name, not a destination.
  perform ops.correspondence_record_read_receipt(c, 'fixture-mail', 'thread-5', 0,
    '{"relevance_state":"related","message_refs":[{"provider_message_id":"CAF0x1a2b.fixture@mail.example.invalid.test","provider_thread_id":"t5@mail.example.invalid.test","occurred_at":"2026-09-24T10:00:00Z"}]}'::jsonb,
    '00000000-0000-4000-8000-000000000014');
  -- ...but the same address shape in any other field of a message ref is refused.
  begin
    perform ops.correspondence_record_read_receipt(c, 'fixture-mail', 'thread-6', 0,
      '{"relevance_state":"related","message_refs":[{"provider_message_id":"m6","note":"joe@example.invalid.test"}]}'::jsonb,
      '00000000-0000-4000-8000-000000000015');
    raise exception 'an address rode in beside a message id';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_read_receipt(c, 'fixture-mail', 'thread-4', 0,
      '{"relevance_state":"ambiguous"}'::jsonb, '00000000-0000-4000-8000-000000000013');
    raise exception 'ambiguous correspondence crossed';
  exception when check_violation then null;
  end;
end $$;

-- Drafts: an agent working for Joe, in Joe's thread.
select set_config('carr.acting_actor_slug', 'automation', true),
       set_config('carr.verified_human_actor_slug', '', true),
       set_config('carr.sponsoring_human_slug', 'joe', true);

insert into j103_fixture values ('draft', ops.correspondence_record_draft(
  (select v from j103_fixture where k = 'receipt'), 'reply_in_thread', array['party-fixture-2'],
  'Thanks, the floor plan is attached in the portal.', '00000000-0000-4000-8000-000000000020'));

do $$
declare d ops.correspondence_draft%rowtype; r uuid := (select v from j103_fixture where k = 'receipt');
begin
  select * into d from ops.correspondence_draft where id = (select v from j103_fixture where k = 'draft');
  if not d.requires_human_send or d.dispatchable or d.partner_slug <> 'joe' then
    raise exception 'draft row is not human-send-only: %', row_to_json(d);
  end if;
  begin
    perform ops.correspondence_record_draft(r, 'reply_in_thread', array['party-fixture-2'], 'write to me at joe@example.invalid.test', '00000000-0000-4000-8000-000000000021');
    raise exception 'a draft carried an address';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_draft(r, 'reply_in_thread', array['party-fixture-2'], 'call me on 251 555 0100', '00000000-0000-4000-8000-000000000022');
    raise exception 'a draft carried a dialable number';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_draft(r, 'reply_in_thread', array['someone@example.invalid.test'], 'hello', '00000000-0000-4000-8000-000000000023');
    raise exception 'an address stood in for a participant reference';
  exception when check_violation then null;
  end;
  begin
    perform ops.correspondence_record_draft('00000000-0000-4000-8000-0000000000ff', 'new_message', array['party-fixture-2'], 'hello', '00000000-0000-4000-8000-000000000024');
    raise exception 'a draft existed without a read receipt';
  exception when undefined_object then null;
  end;
  -- Even the owner cannot insert a dispatchable draft or one that skips the human.
  begin
    insert into ops.correspondence_draft(tenant, partner_slug, read_receipt_id, draft_kind, intended_participant_refs,
      draft_body, draft_digest, dispatchable, recorded_by_actor_id, idempotency_key)
    values ('carr-internal', 'joe', r, 'reply_in_thread', array['party-fixture-2'], 'hello', 'sha256:' || repeat('f', 64),
      true, (select id from public.actor where slug = 'automation'), gen_random_uuid());
    raise exception 'a dispatchable draft was stored';
  exception when check_violation then null;
  end;
  begin
    insert into ops.correspondence_draft(tenant, partner_slug, read_receipt_id, draft_kind, intended_participant_refs,
      draft_body, draft_digest, requires_human_send, recorded_by_actor_id, idempotency_key)
    values ('carr-internal', 'joe', r, 'reply_in_thread', array['party-fixture-2'], 'hello', 'sha256:' || repeat('f', 64),
      false, (select id from public.actor where slug = 'automation'), gen_random_uuid());
    raise exception 'a draft that skips the human send was stored';
  exception when check_violation then null;
  end;
  -- Append-only: a stored draft cannot be edited into anything else.
  begin
    update ops.correspondence_draft set draft_body = 'changed' where id = (select v from j103_fixture where k = 'draft');
    raise exception 'a draft was edited';
  exception when insufficient_privilege then null;
  end;
  begin
    delete from ops.correspondence_draft where id = (select v from j103_fixture where k = 'draft');
    raise exception 'a draft was deleted';
  exception when insufficient_privilege then null;
  end;
  -- Dell's sponsorship cannot draft in Joe's thread.
  perform set_config('carr.sponsoring_human_slug', 'dell', true);
  begin
    perform ops.correspondence_record_draft(r, 'reply_in_thread', array['party-fixture-2'], 'hello', '00000000-0000-4000-8000-000000000025');
    raise exception 'Dell drafted in Joe''s thread';
  exception when insufficient_privilege then null;
  end;
  perform set_config('carr.sponsoring_human_slug', 'joe', true);
end $$;

-- Revocation stops new drafts and cannot be done by the other partner.
do $$
declare c uuid := (select v from j103_fixture where k = 'consent'); r uuid := (select v from j103_fixture where k = 'receipt');
begin
  perform set_config('carr.acting_actor_slug', 'dell', true);
  perform set_config('carr.verified_human_actor_slug', 'dell', true);
  begin
    perform ops.correspondence_revoke_adapter_consent(c, 'q', '00000000-0000-4000-8000-000000000030');
    raise exception 'Dell revoked Joe''s consent';
  exception when insufficient_privilege then null;
  end;
  perform set_config('carr.acting_actor_slug', 'joe', true);
  perform set_config('carr.verified_human_actor_slug', 'joe', true);
  perform ops.correspondence_revoke_adapter_consent(c, 'fixture revoke', '00000000-0000-4000-8000-000000000031');
  if ops.correspondence_consent_in_force(c) then raise exception 'revoked consent still in force'; end if;
  perform set_config('carr.acting_actor_slug', 'automation', true);
  perform set_config('carr.verified_human_actor_slug', '', true);
  begin
    perform ops.correspondence_record_draft(r, 'reply_in_thread', array['party-fixture-2'], 'hello again', '00000000-0000-4000-8000-000000000032');
    raise exception 'a draft was written after consent was revoked';
  exception when insufficient_privilege then null;
  end;
  begin
    perform ops.correspondence_record_read_receipt(c, 'fixture-mail', 'thread-9', 0, '{"relevance_state":"related"}'::jsonb, '00000000-0000-4000-8000-000000000033');
    raise exception 'a receipt was recorded after consent was revoked';
  exception when insufficient_privilege then null;
  end;
end $$;

\echo 'governed correspondence store postgres proof: all assertions passed'
rollback;
