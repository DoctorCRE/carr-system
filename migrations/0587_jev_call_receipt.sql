-- 0587_jev_call_receipt.sql
--
-- Server-side Jev call log: an append-only receipt of every Jev (TypeSafe)
-- call the Worker itself made, appended by the Worker's writer transaction and
-- stamped with the database clock.
--
-- WHY THIS EXISTS. Jev enforcement gates credited evidence the gated model
-- could write: the session transcript and out/jev-calls.jsonl. The model and
-- the hooks share one OS user, so a local log or a local credential is
-- forgeable. The `ask-jev` verb (mcp-server/src/jev-call-receipt.js) moves the
-- call into the Worker, which alone holds the TypeSafe key; the Worker asks Jev
-- BEFORE it opens the writer transaction and then appends here in a short one.
--
-- DETECTABLE, NOT PREVENTED. This raises the cost of forging a Jev call; it
-- does not make forgery impossible. The model's OS user can read the database
-- owner credential, and with it can insert, backdate or delete rows directly,
-- or disable the append-only trigger. What makes that detectable:
--   * ops.read_jev_call_receipts credits a receipt only when public.tool_call
--     (the envelope ledger the Worker writes in the same transaction) holds
--     the matching ask-jev row: same idempotency_key, verb 'ask-jev', same
--     actor_id, and a stored response whose receipt_id is this receipt's id.
--     A row inserted straight into this table has no such partner and is not
--     credited; forging both rows consistently is still possible for an owner
--     but is a deliberate two-table forgery, not a stray insert.
--   * ops.jev_call_receipt_integrity (read-jev-call-receipt-integrity, run by
--     ./run.sh health) counts receipts with no matching tool_call row and
--     reports whether the append-only triggers are enabled at the moment it
--     runs. A disable-then-re-enable between two polls is NOT seen.
--   * No event trigger watches ALTER TABLE on this table: CREATE EVENT TRIGGER
--     needs superuser, the disposable migration lane migrates as its own
--     superuser and so cannot show the production owner role is permitted,
--     and the production owner is not a superuser. It is not attempted.
--
-- WHAT IS STORED. Ids, digests and probabilities only: the question ids, the
-- lowercase-hex sha256 of the canonical JSON of the state, the questions and
-- the answers, the answers themselves (Jev's probabilities), usage, the
-- requested and answered model, the server-derived actor (id and slug, checked
-- against public.actor; never a caller argument), and -- for purpose
-- 'build_advisory' only -- the sha256 of the canonical JSON of
-- state.partner_request. Never the state text. recorded_at is
-- clock_timestamp() on the server; no caller supplies it.
--
-- APPEND-ONLY. No app role holds any direct privilege on the table. The one
-- write path is ops.record_jev_call_receipt (SECURITY DEFINER, EXECUTE to
-- carr_writer only); the read paths are ops.read_jev_call_receipts and
-- ops.jev_call_receipt_integrity (SECURITY DEFINER, EXECUTE to carr_reader and
-- carr_writer); triggers refuse every UPDATE, DELETE and TRUNCATE, including
-- the owner's, for as long as they stay enabled.
--
-- ATOMIC WITH 0588. The doors are new SECURITY DEFINER functions with EXECUTE
-- grants, which move the live SCAC mutation catalog; applied alone this
-- migration is refused at commit by the deferred epoch trigger. 0588 seals
-- the catalog as v69, and tools/migrate.py declares (0587, 0588) one atomic
-- group.
--
-- No explicit transaction control: from 0339 onward tools/migrate.py runs
-- each migration inside its own single transaction.

create table if not exists ops.jev_call_receipt (
  receipt_id uuid primary key default gen_random_uuid(),
  recorded_at timestamptz not null default clock_timestamp(),
  session_id text not null check (char_length(session_id) between 1 and 200),
  purpose text not null check (purpose in ('call','build_advisory')),
  question_ids text[] not null
    check (cardinality(question_ids) between 1 and 64 and array_position(question_ids, null) is null),
  facets text[] not null default '{}'
    check (facets <@ array['architecture_or_design','semantic_creation','diagnosis',
      'verification_selection','evidence_matching','next_action_priority']::text[]
      and array_position(facets, null) is null),
  model_requested text not null check (btrim(model_requested) <> ''),
  model_answered text not null check (btrim(model_answered) <> ''),
  state_sha256 text not null check (state_sha256 ~ '^[0-9a-f]{64}$'),
  questions_sha256 text not null check (questions_sha256 ~ '^[0-9a-f]{64}$'),
  answers_sha256 text not null check (answers_sha256 ~ '^[0-9a-f]{64}$'),
  prompt_sha256 text check (prompt_sha256 ~ '^[0-9a-f]{64}$'),
  answers jsonb not null check (jsonb_typeof(answers) = 'object'),
  usage jsonb check (usage is null or jsonb_typeof(usage) = 'object'),
  actor_id uuid not null references public.actor(id),
  actor_slug text not null check (btrim(actor_slug) <> ''),
  idempotency_key text not null unique check (btrim(idempotency_key) <> ''),
  -- prompt_sha256 is required exactly when purpose = 'build_advisory'.
  constraint jev_call_receipt_prompt_iff_build_advisory
    check ((purpose = 'build_advisory') = (prompt_sha256 is not null))
);

create index if not exists jev_call_receipt_session_recorded_at_idx
  on ops.jev_call_receipt (session_id, recorded_at);

comment on table ops.jev_call_receipt is
  'Append-only receipt of each Jev (TypeSafe) call the Worker made through ask-jev. recorded_at is the server clock; actor_id/actor_slug are the server-derived actor. Ids, digests and probabilities only; no state text. Tampering by the owner is detectable (tool_call cross-check, integrity audit), not prevented.';

revoke all on table ops.jev_call_receipt from public, carr_reader, carr_writer, carr_jobs, carr_authority;

create or replace function ops.jev_call_receipt_append_only()
returns trigger
language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'ops.jev_call_receipt is append-only (% refused)', tg_op;
end;
$$;

drop trigger if exists jev_call_receipt_append_only on ops.jev_call_receipt;
create trigger jev_call_receipt_append_only
before update or delete on ops.jev_call_receipt
for each row execute function ops.jev_call_receipt_append_only();

drop trigger if exists jev_call_receipt_no_truncate on ops.jev_call_receipt;
create trigger jev_call_receipt_no_truncate
before truncate on ops.jev_call_receipt
for each statement execute function ops.jev_call_receipt_append_only();

revoke all on function ops.jev_call_receipt_append_only() from public;

create or replace function ops.record_jev_call_receipt(
  p_session_id text,
  p_purpose text,
  p_question_ids text[],
  p_facets text[],
  p_model_requested text,
  p_model_answered text,
  p_state_sha256 text,
  p_questions_sha256 text,
  p_answers_sha256 text,
  p_prompt_sha256 text,
  p_answers jsonb,
  p_usage jsonb,
  p_actor_id uuid,
  p_actor_slug text,
  p_idempotency_key text
)
returns table (
  receipt_id uuid,
  recorded_at timestamptz,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_existing ops.jev_call_receipt%rowtype;
  v_row ops.jev_call_receipt%rowtype;
  v_acting text;
begin
  if p_idempotency_key is null or btrim(p_idempotency_key) = '' then
    raise exception 'idempotency_key_required';
  end if;
  -- The actor is the Worker's server-authenticated actor. It must be a real
  -- actor row, id and slug together, and -- when the writer transaction has
  -- set the acting-actor context (mcp.js setWriterActorContext) -- the same
  -- actor that context names.
  if p_actor_id is null or p_actor_slug is null or not exists (
       select 1 from public.actor a where a.id = p_actor_id and a.slug = p_actor_slug) then
    raise exception 'jev_call_receipt_actor_unresolved';
  end if;
  v_acting := nullif(current_setting('carr.acting_actor_slug', true), '');
  if v_acting is not null and v_acting <> p_actor_slug then
    raise exception 'jev_call_receipt_actor_mismatch';
  end if;
  if p_session_id is null or char_length(p_session_id) not between 1 and 200 then
    raise exception 'jev_session_id_invalid';
  end if;
  if p_purpose is null or p_purpose not in ('call','build_advisory') then
    raise exception 'jev_purpose_invalid';
  end if;
  if (p_purpose = 'build_advisory') <> (p_prompt_sha256 is not null) then
    raise exception 'jev_call_receipt_prompt_sha256_iff_build_advisory';
  end if;

  -- The Worker holds an advisory lock on the key for the whole transaction
  -- (withEnvelope), so a same-key replay reaches here only when the envelope
  -- ledger and this table disagree. Identical content replays; anything else
  -- is key reuse.
  select * into v_existing from ops.jev_call_receipt r where r.idempotency_key = p_idempotency_key;
  if found then
    if v_existing.session_id is distinct from p_session_id
       or v_existing.purpose is distinct from p_purpose
       or v_existing.state_sha256 is distinct from p_state_sha256
       or v_existing.questions_sha256 is distinct from p_questions_sha256
       or v_existing.actor_id is distinct from p_actor_id then
      raise exception 'jev_call_receipt_key_reuse';
    end if;
    return query select v_existing.receipt_id, v_existing.recorded_at, true;
    return;
  end if;

  insert into ops.jev_call_receipt (
    session_id, purpose, question_ids, facets, model_requested, model_answered,
    state_sha256, questions_sha256, answers_sha256, prompt_sha256, answers, usage,
    actor_id, actor_slug, idempotency_key
  ) values (
    p_session_id, p_purpose, p_question_ids, coalesce(p_facets, '{}'::text[]),
    p_model_requested, p_model_answered, p_state_sha256, p_questions_sha256,
    p_answers_sha256, p_prompt_sha256, p_answers, p_usage, p_actor_id, p_actor_slug,
    p_idempotency_key
  ) returning * into v_row;

  return query select v_row.receipt_id, v_row.recorded_at, false;
end;
$$;

comment on function ops.record_jev_call_receipt(text,text,text[],text[],text,text,text,text,text,text,jsonb,jsonb,uuid,text,text) is
  'Write door for ops.jev_call_receipt: append one receipt of a Jev call the Worker made, for the server-derived actor. recorded_at is the server clock. Idempotent on p_idempotency_key; no update or delete path exists.';

revoke all on function ops.record_jev_call_receipt(text,text,text[],text[],text,text,text,text,text,text,jsonb,jsonb,uuid,text,text) from public;
grant execute on function ops.record_jev_call_receipt(text,text,text[],text[],text,text,text,text,text,text,jsonb,jsonb,uuid,text,text) to carr_writer;

-- The calling actor's CREDITED receipts for one session from p_since (default
-- 24 hours before the server clock), oldest first. Credited means the
-- envelope ledger holds the matching ask-jev call (see the header). At most
-- p_lim rows -- the OLDEST from p_since -- with truncated=true when more exist.
-- answers are projected only for build_advisory receipts.
create or replace function ops.read_jev_call_receipts(
  p_session text, p_since timestamptz, p_lim int, p_actor_slug text)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor uuid;
  v_since timestamptz;
  v_rows jsonb;
  v_count int;
begin
  if p_session is null or char_length(p_session) not between 1 and 200 then
    raise exception 'jev_session_id_invalid';
  end if;
  if p_lim is null or p_lim not between 1 and 500 then
    raise exception 'jev_limit_invalid';
  end if;
  select a.id into v_actor from public.actor a where a.slug = p_actor_slug;
  if v_actor is null then
    raise exception 'jev_call_receipt_actor_unresolved';
  end if;
  v_since := coalesce(p_since, clock_timestamp() - interval '24 hours');
  select coalesce(jsonb_agg(jsonb_build_object(
           'receipt_id', credited.receipt_id,
           'recorded_at', credited.recorded_at,
           'purpose', credited.purpose,
           'question_ids', to_jsonb(credited.question_ids),
           'facets', to_jsonb(credited.facets),
           'model', credited.model_answered,
           'state_sha256', credited.state_sha256,
           'prompt_sha256', credited.prompt_sha256,
           'answers', case when credited.purpose = 'build_advisory' then credited.answers else null end
         ) order by credited.recorded_at, credited.receipt_id), '[]'::jsonb),
         count(*)
    into v_rows, v_count
    from (
      select r.*
        from ops.jev_call_receipt r
        join public.tool_call t
          on t.idempotency_key = r.idempotency_key
         and t.verb = 'ask-jev'
         and t.actor_id = r.actor_id
         and t.response->>'receipt_id' = r.receipt_id::text
       where r.session_id = p_session
         and r.actor_id = v_actor
         and r.recorded_at >= v_since
       order by r.recorded_at, r.receipt_id
       limit p_lim + 1
    ) credited;
  if v_count > p_lim then
    v_rows := v_rows - p_lim;
  end if;
  return jsonb_build_object(
    'server_now', to_jsonb(clock_timestamp()),
    'since', to_jsonb(v_since),
    'truncated', v_count > p_lim,
    'receipts', v_rows);
end;
$$;

comment on function ops.read_jev_call_receipts(text,timestamp with time zone,integer,text) is
  'Read door for ops.jev_call_receipt: the calling actor''s credited receipts (matching ask-jev tool_call row) for one session from p_since, oldest first, at most p_lim with a truncated flag, answers only for build_advisory, plus the server clock.';

revoke all on function ops.read_jev_call_receipts(text,timestamp with time zone,integer,text) from public;
grant execute on function ops.read_jev_call_receipts(text,timestamp with time zone,integer,text) to carr_reader, carr_writer;

-- The detection audit (header: detectable, not prevented).
create or replace function ops.jev_call_receipt_integrity()
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, ops
as $$
declare
  v_total bigint;
  v_orphans bigint;
  v_orphan_ids jsonb;
  v_triggers jsonb;
  v_enabled boolean;
begin
  select count(*) into v_total from ops.jev_call_receipt;
  with orphan as (
    select r.receipt_id, r.recorded_at
      from ops.jev_call_receipt r
     where not exists (
       select 1 from public.tool_call t
        where t.idempotency_key = r.idempotency_key
          and t.verb = 'ask-jev'
          and t.actor_id = r.actor_id
          and t.response->>'receipt_id' = r.receipt_id::text)
  )
  select count(*),
         coalesce((select jsonb_agg(o2.receipt_id order by o2.recorded_at desc, o2.receipt_id)
                     from (select * from orphan order by recorded_at desc, receipt_id limit 20) o2), '[]'::jsonb)
    into v_orphans, v_orphan_ids
    from orphan;
  with expected(name) as (
    values ('jev_call_receipt_append_only'), ('jev_call_receipt_no_truncate')
  )
  select jsonb_agg(jsonb_build_object(
           'name', e.name,
           'tgenabled', t.tgenabled::text,
           'enabled', coalesce(t.tgenabled in ('O','A'), false)) order by e.name),
         bool_and(coalesce(t.tgenabled in ('O','A'), false))
    into v_triggers, v_enabled
    from expected e
    left join pg_catalog.pg_trigger t
      on t.tgname = e.name
     and t.tgrelid = 'ops.jev_call_receipt'::regclass
     and not t.tgisinternal;
  return jsonb_build_object(
    'receipts_total', v_total,
    'receipts_without_tool_call', jsonb_build_object('count', v_orphans, 'receipt_ids', v_orphan_ids),
    'trigger_enabled', v_enabled,
    'triggers', v_triggers,
    'checked_at', to_jsonb(clock_timestamp()));
end;
$$;

comment on function ops.jev_call_receipt_integrity() is
  'Integrity audit for ops.jev_call_receipt: total receipts, receipts with no matching ask-jev tool_call row (count and up to 20 ids), and whether the append-only triggers are enabled now. A disable-then-re-enable between polls is not seen.';

revoke all on function ops.jev_call_receipt_integrity() from public;
grant execute on function ops.jev_call_receipt_integrity() to carr_reader, carr_writer;
