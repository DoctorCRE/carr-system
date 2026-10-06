\set ON_ERROR_STOP on
-- V5-RW02 durable evidence SQL on the disposable CI database. Everything runs
-- inside one transaction that is rolled back. Every record is synthetic.
begin;

do $$
begin
  if to_regclass('ops.rw02_runtime_record') is null then
    raise exception 'rw02_runtime_record missing';
  end if;
  if to_regprocedure('ops.rw02_record(text,text,text,text,text,jsonb)') is null then
    raise exception 'rw02_record writer missing';
  end if;
  if to_regprocedure('ops.rw02_replay(text,text,text)') is null then
    raise exception 'rw02_replay reader missing';
  end if;
  if to_regprocedure('ops.rw02_action_evidence(text)') is null then
    raise exception 'rw02_action_evidence reader missing';
  end if;
end $$;

-- One correctly sealed evidence record: evidence_digest is the kernel's
-- digest({kind:'rw02-evidence.v1', ...record}) over these exact fields.
create temporary table rw02_fixture_evidence (e jsonb) on commit drop;
insert into rw02_fixture_evidence values (jsonb_build_object(
  'schema_version', 'doctorcre-v5-rw02-action-evidence.v1',
  'tenant', 'carr-internal',
  'action_kind', 'opportunity_create',
  'step_key', 'rw02-step-postgres-1',
  'preview_digest', 'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  'envelope_digest', 'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
  'evidence_class', 'fixture',
  'observed_at', '2026-09-26T05:00:00Z',
  'outcome', 'exact_match',
  'readback_digest', 'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
  'evidence_digest', 'sha256:7faf9722f4dd6fa56caa695780a7040cf3aaecb60092dbbc0b42cf33e716c1c1'));
grant select on rw02_fixture_evidence to carr_writer;

set session authorization carr_writer;
select set_config('carr.acting_actor_slug', 'synthetic-rw02-writer', true);
select set_config('carr.sponsoring_human_slug', 'joe', true);

select ops.rw02_record(
  'record-salesforce-write-readback',
  'rw02-postgres-fixture-0001',
  'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  'opportunity_create',
  'rw02-step-postgres-1',
  jsonb_build_object('evaluation', jsonb_build_object(
    'decision', 'confirmed', 'action_kind', 'opportunity_create',
    'step_key', 'rw02-step-postgres-1', 'evidence', (select e from rw02_fixture_evidence))));

-- A duplicate check for the SAME action kind is durable too, but it is not
-- readback evidence: the per-action evidence read must not count it.
select ops.rw02_record(
  'record-salesforce-duplicate-check',
  'rw02-postgres-fixture-0002',
  'sha256:1111111111111111111111111111111111111111111111111111111111111111',
  'opportunity_create',
  'rw02-step-postgres-dup-1',
  jsonb_build_object('evaluation', jsonb_build_object('decision', 'link',
    'action_kind', 'opportunity_create', 'step_key', 'rw02-step-postgres-dup-1'))
);

-- Readback evidence sealed for one action cannot be filed under another.
do $$
begin
  perform ops.rw02_record(
    'record-salesforce-write-readback',
    'rw02-postgres-fixture-0003',
    'sha256:2222222222222222222222222222222222222222222222222222222222222222',
    'opportunity_phase_update',
    'rw02-step-postgres-1',
    jsonb_build_object('evaluation', jsonb_build_object(
      'evidence', (select e from rw02_fixture_evidence))));
  raise exception 'rw02 fixture: cross-action readback was accepted';
exception when others then
  if position('rw02_evidence_binding_mismatch' in sqlerrm) = 0 then raise; end if;
end $$;

-- POISON ROWS. Each of these would make evaluateActionTrustWindow throw (or
-- refuse) on every later read of the action, and a row cannot be removed, so
-- each is refused at write time. The label names the planted defect.
do $$
declare
  v_good jsonb := (select e from rw02_fixture_evidence);
  v_case record;
begin
  for v_case in select * from (values
    ('unsealed', v_good || jsonb_build_object('evidence_digest',
       'sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'), 'evidence_seal_broken'),
    ('resealed field edit', v_good || jsonb_build_object('outcome', 'mismatch'), 'evidence_seal_broken'),
    ('extra key', v_good || jsonb_build_object('note', 'x'), 'evidence_keys_not_exact'),
    ('missing key', v_good - 'preview_digest', 'evidence_keys_not_exact'),
    ('numeric value', v_good || jsonb_build_object('preview_digest', 1), 'evidence_value_not_string'),
    ('wrong schema', v_good || jsonb_build_object('schema_version', 'x'), 'evidence_schema_version'),
    ('wrong tenant', v_good || jsonb_build_object('tenant', 'other'), 'evidence_tenant'),
    ('unknown class', v_good || jsonb_build_object('evidence_class', 'x'), 'evidence_class'),
    ('unknown outcome', v_good || jsonb_build_object('outcome', 'x'), 'evidence_outcome'),
    ('no such day', v_good || jsonb_build_object('observed_at', '2026-02-30T05:00:00Z'), 'evidence_observed_at'),
    ('no offset', v_good || jsonb_build_object('observed_at', '2026-09-26T05:00:00'), 'evidence_observed_at'),
    ('not an object', to_jsonb('x'::text), 'evidence_not_object')
  ) as t(label, evidence, reason) loop
    begin
      perform ops.rw02_record(
        'record-salesforce-write-readback',
        'rw02-postgres-poison-' || md5(v_case.label),
        'sha256:' || md5(v_case.label) || md5(v_case.label),
        'opportunity_create', 'rw02-step-postgres-1',
        jsonb_build_object('evaluation', jsonb_build_object('evidence', v_case.evidence)));
      raise exception 'rw02 fixture: poison row accepted (%)', v_case.label;
    exception when others then
      if position('rw02_evidence_invalid: ' || v_case.reason in sqlerrm) = 0 then
        raise exception 'rw02 fixture: poison row % refused for the wrong reason: %', v_case.label, sqlerrm;
      end if;
    end;
  end loop;
end $$;

-- The same sample, or a second success for the same step and envelope under
-- a new key, would make the window refuse as counted twice for good.
do $$
declare v_good jsonb := (select e from rw02_fixture_evidence);
begin
  begin
    perform ops.rw02_record('record-salesforce-write-readback', 'rw02-postgres-fixture-0004',
      'sha256:4444444444444444444444444444444444444444444444444444444444444444',
      'opportunity_create', 'rw02-step-postgres-1',
      jsonb_build_object('evaluation', jsonb_build_object('evidence', v_good)));
    raise exception 'rw02 fixture: the same evidence was counted twice';
  exception when others then
    if position('rw02_evidence_counted_twice' in sqlerrm) = 0 then raise; end if;
  end;
end $$;

do $$
declare v_replay jsonb; v_first jsonb; v_count integer; v_evidence jsonb;
begin
  select ops.rw02_replay(
    'record-salesforce-write-readback', 'rw02-postgres-fixture-0001',
    'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa') into v_replay;
  if v_replay is null or v_replay->>'action_kind' <> 'opportunity_create'
     or v_replay #>> '{evaluation,decision}' <> 'confirmed'
     or v_replay->>'record_digest' !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'exact replay missing or changed: %', v_replay;
  end if;
  -- The writer's own same-key call answers exactly what the replay does.
  select ops.rw02_record('record-salesforce-write-readback', 'rw02-postgres-fixture-0001',
    'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    'opportunity_create', 'rw02-step-postgres-1', '{}'::jsonb) into v_first;
  if v_first is distinct from v_replay then
    raise exception 'replay and same-key record answer differently: % vs %', v_first, v_replay;
  end if;
  select count(*) into v_count from ops.rw02_action_evidence('opportunity_create');
  select record into v_evidence from ops.rw02_action_evidence('opportunity_create') limit 1;
  if v_count <> 1 or v_evidence->>'action_kind' <> 'opportunity_create' then
    raise exception 'per-action evidence read failed: count %, record %', v_count, v_evidence;
  end if;
  if exists (select 1 from ops.rw02_action_evidence('opportunity_phase_update')) then
    raise exception 'evidence leaked across action kinds';
  end if;
end $$;

-- A reused key over a different request is a typed conflict, not an error.
do $$
declare v_replay jsonb;
begin
  select ops.rw02_replay(
    'record-salesforce-write-readback', 'rw02-postgres-fixture-0001',
    'sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff') into v_replay;
  if v_replay is distinct from '{"conflict": true}'::jsonb then
    raise exception 'rw02 fixture: changed-digest replay was not a conflict: %', v_replay;
  end if;
end $$;

do $$
begin
  perform ops.rw02_record('record-salesforce-write-readback', 'rw02-postgres-fixture-0001',
    'sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
    'opportunity_create', 'rw02-step-postgres-1', '{}'::jsonb);
  -- The sentinel must NOT contain the expected error text, or the handler
  -- below would accept this raise as the refusal it is looking for.
  raise exception 'rw02 fixture: record accepted a changed request digest';
exception when others then
  if position('rw02_idempotency_conflict' in sqlerrm) = 0 then raise; end if;
end $$;

do $$
begin
  if has_table_privilege('carr_writer', 'ops.rw02_runtime_record', 'INSERT')
     or has_table_privilege('carr_writer', 'ops.rw02_runtime_record', 'UPDATE')
     or has_table_privilege('carr_writer', 'ops.rw02_runtime_record', 'DELETE')
     or has_table_privilege('carr_writer', 'ops.rw02_runtime_record', 'TRUNCATE') then
    raise exception 'carr_writer has direct DML on append-only RW02 evidence';
  end if;
end $$;

reset session authorization;

-- APPEND-ONLY holds for the table OWNER too, not only for the runtime roles
-- a revoke can reach. Each statement is attempted as the owner and must be
-- refused by the table's own trigger.
do $$
declare
  v_owner text := (select tableowner from pg_tables
                    where schemaname = 'ops' and tablename = 'rw02_runtime_record');
  v_stmt text;
begin
  execute format('set local role %I', v_owner);
  foreach v_stmt in array array[
    'update ops.rw02_runtime_record set step_key = step_key',
    'delete from ops.rw02_runtime_record',
    'truncate ops.rw02_runtime_record'
  ] loop
    begin
      execute v_stmt;
      raise exception 'rw02 fixture: the owner rewrote RW02 evidence (%)', v_stmt;
    exception when others then
      if position('rw02_runtime_record_append_only' in sqlerrm) = 0 then
        raise exception 'rw02 fixture: owner % not refused by the append-only guard: %', v_stmt, sqlerrm;
      end if;
    end;
  end loop;
  reset role;
  if (select count(*) from ops.rw02_runtime_record
       where idempotency_key like 'rw02-postgres-fixture-%') <> 2 then
    raise exception 'rw02 fixture: evidence rows changed under the append-only guard';
  end if;
end $$;

rollback;
