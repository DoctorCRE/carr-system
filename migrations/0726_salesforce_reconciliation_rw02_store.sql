-- V5-RW02: append-only attended Salesforce reconciliation runtime evidence.
-- No function here contacts Salesforce or grants an external effect.

create table ops.rw02_runtime_record (
  id uuid primary key default gen_random_uuid(),
  tenant text not null check (tenant = 'carr-internal'),
  operation text not null check (operation in (
    'record-salesforce-page-stop',
    'record-salesforce-duplicate-check',
    'record-salesforce-write-readback'
  )),
  idempotency_key text not null check (length(idempotency_key) between 8 and 200),
  request_digest text not null check (request_digest ~ '^sha256:[0-9a-f]{64}$'),
  action_kind text,
  step_key text,
  outcome jsonb not null,
  record_digest text not null check (record_digest ~ '^sha256:[0-9a-f]{64}$'),
  recorded_by text not null,
  recorded_at timestamptz not null default clock_timestamp(),
  unique (operation, recorded_by, idempotency_key),
  check ((operation in ('record-salesforce-duplicate-check','record-salesforce-write-readback')) =
    (action_kind is not null and step_key is not null))
);

comment on table ops.rw02_runtime_record is
  'Append-only V5-RW02 page-stop, duplicate, and provider-readback evidence. No provider effect authority.';

revoke all on ops.rw02_runtime_record from public, carr_reader, carr_writer, carr_jobs, carr_authority;

-- One evidence record, and one successful effect per step and envelope, per
-- action kind. The per-action trust window refuses a window that counts the
-- same sample twice, so a duplicate that landed would refuse every later read
-- of that action; it is refused here at write time instead. The pre-check in
-- ops.rw02_record names the refusal; these indexes are the race backstop.
create unique index rw02_runtime_record_evidence_once
  on ops.rw02_runtime_record (action_kind, (outcome #>> '{evaluation,evidence,evidence_digest}'))
  where operation = 'record-salesforce-write-readback';
create unique index rw02_runtime_record_success_effect_once
  on ops.rw02_runtime_record (action_kind, step_key, (outcome #>> '{evaluation,evidence,envelope_digest}'))
  where operation = 'record-salesforce-write-readback'
    and outcome #>> '{evaluation,evidence,outcome}' in ('exact_match', 'unknown_resolved_by_readback');

-- Append-only is a property of the TABLE (the 0700/0704/0706/0717/0719/0721
-- idiom): UPDATE and DELETE are refused per row and TRUNCATE per statement,
-- for every role including the owner, independent of any later grant.
create or replace function ops.refuse_rw02_runtime_record_rewrite()
returns trigger language plpgsql as $fn$
begin
  raise exception using
    message = 'rw02_runtime_record_append_only',
    detail = format('%s on ops.rw02_runtime_record is refused; RW02 evidence is append-only', tg_op);
end
$fn$;

create trigger rw02_runtime_record_append_only
  before update or delete on ops.rw02_runtime_record
  for each row execute function ops.refuse_rw02_runtime_record_rewrite();

create trigger rw02_runtime_record_no_truncate
  before truncate on ops.rw02_runtime_record
  for each statement execute function ops.refuse_rw02_runtime_record_rewrite();

-- The kernel's per-action trust window (evaluateActionTrustWindow) reads every
-- stored evidence record and throws on any malformed one. A row cannot be
-- removed, so a malformed row would break that action's evidence read for
-- good. This is the same shape and seal check, run BEFORE the insert: closed
-- and complete keys, string-or-null values, schema version, tenant, action and
-- step binding, evidence class, outcome, a real calendar instant, and the
-- evidence_digest recomputed over the kernel's canonical JSON
-- (sorted keys, no whitespace, kind 'rw02-evidence.v1').
create or replace function ops.rw02_evidence_refusal(
  p_evidence jsonb, p_action_kind text, p_step_key text
) returns text
language plpgsql immutable
set search_path = pg_catalog
as $$
declare
  v_keys text[] := array['action_kind','envelope_digest','evidence_class','evidence_digest',
    'observed_at','outcome','preview_digest','readback_digest','schema_version','step_key','tenant'];
  v_parts text[];
  v_canonical text;
begin
  if p_evidence is null or jsonb_typeof(p_evidence) <> 'object' then return 'evidence_not_object'; end if;
  if (select array_agg(k order by k collate "C") from jsonb_object_keys(p_evidence) k)
     is distinct from v_keys then
    return 'evidence_keys_not_exact';
  end if;
  if exists (select 1 from jsonb_each(p_evidence) e where jsonb_typeof(e.value) not in ('string','null')) then
    return 'evidence_value_not_string';
  end if;
  if exists (select 1 from unnest(array['schema_version','tenant','action_kind','step_key',
                'evidence_class','observed_at','outcome','evidence_digest']) k
              where jsonb_typeof(p_evidence->k) is distinct from 'string') then
    return 'evidence_required_value_missing';
  end if;
  if p_evidence->>'schema_version' <> 'doctorcre-v5-rw02-action-evidence.v1' then
    return 'evidence_schema_version';
  end if;
  if p_evidence->>'tenant' <> 'carr-internal' then return 'evidence_tenant'; end if;
  if p_evidence->>'action_kind' is distinct from p_action_kind
     or p_evidence->>'step_key' is distinct from p_step_key then
    return 'evidence_binding_mismatch';
  end if;
  if p_evidence->>'evidence_class' not in ('fixture','supervised_production_sample','recovery_exercise') then
    return 'evidence_class';
  end if;
  if p_evidence->>'outcome' not in ('exact_match','mismatch','stopped','unknown_resolved_by_readback') then
    return 'evidence_outcome';
  end if;
  v_parts := regexp_match(p_evidence->>'observed_at',
    '^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|[+-](\d{2}):(\d{2}))$');
  if v_parts is null
     or v_parts[2]::int not between 1 and 12
     or v_parts[3]::int < 1
     or v_parts[3]::int > extract(day from (make_date(v_parts[1]::int, v_parts[2]::int, 1)
                                            + interval '1 month' - interval '1 day'))::int
     or v_parts[4]::int > 23 or v_parts[5]::int > 59 or v_parts[6]::int > 59
     or coalesce(v_parts[7]::int, 0) > 23 or coalesce(v_parts[8]::int, 0) > 59 then
    return 'evidence_observed_at';
  end if;
  select '{' || string_agg(to_jsonb(e.key)::text || ':' || e.value::text, ',' order by e.key collate "C") || '}'
    into v_canonical
    from jsonb_each((p_evidence - 'evidence_digest') || jsonb_build_object('kind', 'rw02-evidence.v1')) e;
  if 'sha256:' || encode(public.digest(convert_to(v_canonical, 'UTF8'), 'sha256'), 'hex')
     <> p_evidence->>'evidence_digest' then
    return 'evidence_seal_broken';
  end if;
  return null;
end;
$$;

-- A replay answers with exactly what the first call stored. A reused key
-- over a different request is not an error inside the transaction: it comes
-- back as {"conflict": true} so the handler can refuse it with a typed code.
create or replace function ops.rw02_replay(
  p_operation text, p_idempotency_key text, p_request_digest text
) returns jsonb
language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
declare v_actor text; v_row ops.rw02_runtime_record%rowtype;
begin
  v_actor := ops.f01_context_actor_slug();
  select * into v_row from ops.rw02_runtime_record
   where operation=p_operation and recorded_by=v_actor and idempotency_key=p_idempotency_key;
  if not found then return null; end if;
  if v_row.request_digest <> p_request_digest then
    return jsonb_build_object('conflict', true);
  end if;
  return v_row.outcome || jsonb_build_object(
    'record_digest', v_row.record_digest,
    'action_kind', v_row.action_kind,
    'step_key', v_row.step_key);
end;
$$;

create or replace function ops.rw02_record(
  p_operation text, p_idempotency_key text, p_request_digest text,
  p_action_kind text, p_step_key text, p_outcome jsonb
) returns jsonb
language plpgsql volatile security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor text;
  v_now timestamptz := clock_timestamp();
  v_outcome jsonb;
  v_digest text;
  v_row ops.rw02_runtime_record%rowtype;
  v_evidence jsonb;
  v_refusal text;
begin
  if p_operation not in ('record-salesforce-page-stop','record-salesforce-duplicate-check',
                          'record-salesforce-write-readback') then
    raise exception 'rw02_operation_unregistered';
  end if;
  if p_request_digest !~ '^sha256:[0-9a-f]{64}$' then raise exception 'rw02_request_digest_invalid'; end if;
  if length(coalesce(p_idempotency_key,'')) not between 8 and 200 then
    raise exception 'rw02_idempotency_key_invalid';
  end if;
  v_actor := ops.f01_context_actor_slug();
  select * into v_row from ops.rw02_runtime_record
   where operation=p_operation and recorded_by=v_actor and idempotency_key=p_idempotency_key
   for update;
  if found then
    if v_row.request_digest <> p_request_digest then raise exception 'rw02_idempotency_conflict'; end if;
    return v_row.outcome || jsonb_build_object('record_digest',v_row.record_digest,
      'action_kind',v_row.action_kind,'step_key',v_row.step_key);
  end if;

  v_outcome := coalesce(p_outcome,'{}'::jsonb) || jsonb_build_object(
    'schema_version','doctorcre-v5-rw02-runtime-store.v1',
    'operation',p_operation,'tenant','carr-internal','actor_slug',v_actor,
    'recorded_at',to_char(v_now at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'));

  if p_operation = 'record-salesforce-write-readback' then
    v_evidence := v_outcome #> '{evaluation,evidence}';
    v_refusal := ops.rw02_evidence_refusal(v_evidence, p_action_kind, p_step_key);
    if v_refusal = 'evidence_binding_mismatch' then
      raise exception 'rw02_evidence_binding_mismatch';
    elsif v_refusal is not null then
      raise exception 'rw02_evidence_invalid: %', v_refusal;
    end if;
    if exists (select 1 from ops.rw02_runtime_record r
                where r.operation='record-salesforce-write-readback'
                  and r.action_kind=p_action_kind
                  and (r.outcome #>> '{evaluation,evidence,evidence_digest}' = v_evidence->>'evidence_digest'
                       or (v_evidence->>'outcome' in ('exact_match','unknown_resolved_by_readback')
                           and r.step_key = p_step_key
                           and r.outcome #>> '{evaluation,evidence,outcome}'
                               in ('exact_match','unknown_resolved_by_readback')
                           and r.outcome #>> '{evaluation,evidence,envelope_digest}'
                               is not distinct from v_evidence->>'envelope_digest'))) then
      raise exception 'rw02_evidence_counted_twice';
    end if;
  elsif p_operation = 'record-salesforce-duplicate-check' then
    if p_action_kind is distinct from 'opportunity_create'
       or p_step_key is null or length(p_step_key) = 0 then
      raise exception 'rw02_duplicate_binding_invalid';
    end if;
  elsif p_action_kind is not null or p_step_key is not null then
    raise exception 'rw02_unexpected_action_binding';
  end if;

  v_digest := 'sha256:' || encode(public.digest(convert_to(
    jsonb_build_object('kind','rw02-runtime-record.v1','operation',p_operation,
      'request_digest',p_request_digest,'action_kind',p_action_kind,'step_key',p_step_key,
      'outcome',v_outcome,'recorded_by',v_actor)::text, 'UTF8'), 'sha256'), 'hex');

  insert into ops.rw02_runtime_record
    (tenant,operation,idempotency_key,request_digest,action_kind,step_key,outcome,
     record_digest,recorded_by,recorded_at)
  values ('carr-internal',p_operation,p_idempotency_key,p_request_digest,p_action_kind,p_step_key,
          v_outcome,v_digest,v_actor,v_now)
  returning * into v_row;
  return v_outcome || jsonb_build_object('record_digest',v_digest,
    'action_kind',p_action_kind,'step_key',p_step_key);
end;
$$;

create or replace function ops.rw02_action_evidence(p_action_kind text)
returns table(record jsonb)
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select r.outcome #> '{evaluation,evidence}'
    from ops.rw02_runtime_record r
   where r.tenant='carr-internal'
     and r.operation='record-salesforce-write-readback'
     and r.action_kind=p_action_kind
   order by r.recorded_at, r.id
$$;

revoke all on function ops.refuse_rw02_runtime_record_rewrite() from public;
revoke all on function ops.rw02_evidence_refusal(jsonb,text,text) from public;
revoke all on function ops.rw02_replay(text,text,text) from public;
revoke all on function ops.rw02_record(text,text,text,text,text,jsonb) from public;
revoke all on function ops.rw02_action_evidence(text) from public;
grant execute on function ops.rw02_replay(text,text,text) to carr_writer, carr_authority;
grant execute on function ops.rw02_record(text,text,text,text,text,jsonb) to carr_writer, carr_authority;
grant execute on function ops.rw02_action_evidence(text) to carr_reader, carr_writer, carr_authority;

do $rw02_0726$
begin
  if (select count(*) from pg_trigger
       where tgrelid = 'ops.rw02_runtime_record'::regclass
         and tgname in ('rw02_runtime_record_append_only', 'rw02_runtime_record_no_truncate')
         and not tgisinternal) <> 2 then
    raise exception '0726 FAILED: RW02 append-only or truncate guard missing';
  end if;
  if has_table_privilege('carr_writer', 'ops.rw02_runtime_record', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_table_privilege('carr_reader', 'ops.rw02_runtime_record', 'SELECT') then
    raise exception '0726 FAILED: RW02 evidence table is directly reachable by a runtime role';
  end if;
end
$rw02_0726$;
