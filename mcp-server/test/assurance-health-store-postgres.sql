\set ON_ERROR_STOP on

-- V5-A01 database acceptance. Every fixture row is rolled back.
--
-- The public read passes NO workflow truth (V5-F09's census store is PR #1244,
-- not on main), so it must never read green. The label predicate itself is
-- exercised as the owner through ops.assurance_health_label with an explicit
-- F09-shaped row: that function is granted to no runtime role, so this is the
-- only place such a row can be supplied.

begin;
select set_config('carr.acting_actor_slug', 'joe', true);

create or replace function pg_temp.a01_ts(p_at timestamptz)
returns text language sql as $$
  select to_char(p_at at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') $$;

create or replace function pg_temp.a01_evidence(
  p_layer text, p_status text, p_token text,
  p_observed interval default interval '-1 minute',
  p_expires interval default interval '1 day')
returns jsonb language plpgsql as $$
declare d jsonb;
begin
  d := case p_layer
    when 'artifact_assessment' then jsonb_build_object(
      'repository_commit_sha', repeat('a', 40), 'repository_tree_sha', repeat('b', 40),
      'reviewer_fact_id', 'reviewer-fact:' || p_token)
    when 'execution_assessment' then jsonb_build_object(
      'attempt_id', 'attempt:' || p_token, 'envelope_digest', 'sha256:' || repeat('c', 64),
      'plan_hash', 'sha256:' || repeat('d', 64))
    when 'controller_assessment' then jsonb_build_object(
      'controller_state', 'running', 'readback_source', 'controller:' || p_token,
      'readback_at', pg_temp.a01_ts(now() + p_observed))
    when 'candidate_outcome_oracle' then jsonb_build_object(
      'governed_data_ref', 'data:' || p_token, 'environment', 'staging',
      'expected_result_ref', 'expected:' || p_token, 'equivalence_comparator', 'exact',
      'component_versions', jsonb_build_object('app', '1'))
    when 'activation_readback' then jsonb_build_object(
      'activation_id', 'activation:' || p_token, 'readback_source', 'provider:' || p_token,
      'readback_at', pg_temp.a01_ts(now() + p_observed))
    when 'actual_business_outcome' then jsonb_build_object(
      'outcome_feedback_ref', 'feedback:' || p_token,
      'outcome_feedback_hash', 'sha256:' || repeat('e', 64),
      'acceptance_receipt_id', 'acceptance:' || p_token)
  end;
  return jsonb_build_object(
    'layer', p_layer, 'basis', ops.assurance_health_basis(p_layer),
    'status', p_status, 'subject_ref', 'release:' || p_token,
    'evidence_ref', 'evidence:' || p_layer || ':' || p_token,
    'evidence_digest', 'sha256:' || encode(digest(p_layer || ':' || p_token || ':' || p_status, 'sha256'), 'hex'),
    'observed_at', pg_temp.a01_ts(now() + p_observed),
    'expires_at', pg_temp.a01_ts(now() + p_expires),
    'detail', d, 'incident_refs', '[]'::jsonb,
    'recovery_refs', jsonb_build_array('runbook:a01'));
end $$;

-- Record one passing receipt per layer for a scope, except the layers skipped.
create or replace function pg_temp.a01_six(p_scope jsonb, p_token text,
  p_skip text[] default array[]::text[], p_observed interval default interval '-1 minute')
returns void language plpgsql as $$
declare layer text;
begin
  foreach layer in array ops.assurance_health_layers() loop
    continue when layer = any(p_skip);
    perform ops.record_assurance_health_evidence(p_scope,
      pg_temp.a01_evidence(layer, 'pass', p_token || ':' || layer, p_observed), gen_random_uuid());
  end loop;
end $$;

-- One F09-projected workflow-truth row.
create or replace function pg_temp.a01_truth(p_key text, p_version integer, p_state text)
returns jsonb language sql as $$
  select jsonb_build_object('workflow_key', p_key, 'workflow_version', p_version, 'state', p_state,
    'enabled', p_state in ('enabled_shadow_only','enabled_canary_eligible','enabled_live_eligible','operational'),
    'admissible_modes', case
      when p_state in ('enabled_live_eligible','operational') then '["shadow","canary","live"]'::jsonb
      when p_state like 'enabled_%' then '["shadow"]'::jsonb else '[]'::jsonb end) $$;

create or replace function pg_temp.a01_label(p_key text, p_version integer, p_wr text, p_state text)
returns jsonb language sql as $$
  select ops.assurance_health_label(p_key, p_version, p_wr, pg_temp.a01_truth(p_key, p_version, p_state)) $$;

-- Assert a statement is refused with exactly this SQLSTATE (and message, if given).
create or replace function pg_temp.a01_refused(p_label text, p_sql text, p_state text, p_message text default null)
returns void language plpgsql as $$
begin
  begin
    execute p_sql;
  exception when others then
    if sqlstate <> p_state or (p_message is not null and sqlerrm <> p_message) then
      raise exception 'A01 %: expected % %, got % %', p_label, p_state, coalesce(p_message, ''), sqlstate, sqlerrm;
    end if;
    return;
  end;
  raise exception 'A01 %: expected refusal % but the statement succeeded', p_label, p_state;
end $$;

create or replace function pg_temp.a01_expect(p_label text, p_ok boolean, p_detail jsonb)
returns void language plpgsql as $$
begin
  if p_ok is not true then raise exception 'A01 %: %', p_label, p_detail; end if;
end $$;

do $$
declare
  scope_a jsonb := '{"workflow_key":"doctorcre.release","workflow_version":7,"work_request_id":"WR-700"}';
  scope_b jsonb := '{"workflow_key":"doctorcre.release","workflow_version":8,"work_request_id":"WR-800"}';
  p jsonb; q jsonb; receipt jsonb; ev jsonb; key uuid; n bigint;
begin
  -- ---- attribution ---------------------------------------------------------
  receipt := ops.record_assurance_health_evidence(scope_a,
    pg_temp.a01_evidence('artifact_assessment', 'pass', 'a:artifact_assessment'), gen_random_uuid());
  perform pg_temp.a01_expect('writer derives the evaluator and a first write is not a replay',
    receipt->>'evaluator' = 'joe' and (receipt->>'replayed')::boolean is false, receipt);
  perform pg_temp.a01_six(scope_a, 'a', array['artifact_assessment']);
  perform pg_temp.a01_six(scope_b, 'b');

  -- ---- B1: no workflow truth, no green --------------------------------------
  p := ops.read_assurance_health('doctorcre.release', 7, 'WR-700');
  perform pg_temp.a01_expect('B1 six passing layers without workflow truth read unknown, unavailable, not green',
    p->>'state' = 'unknown' and p->>'capability_stage' = 'unavailable' and (p->>'green')::boolean is false
    and (p#>>'{workflow_truth,available}')::boolean is false
    and (select count(*) from jsonb_each(p->'evidence') e where e.value->>'state' = 'passing') = 6
    and (select count(distinct e.value->>'evidence_ref') from jsonb_each(p->'evidence') e) = 6, p);
  perform pg_temp.a01_six('{"workflow_key":"nonexistent.never-registered","workflow_version":999,"work_request_id":"WR-999"}', 'u');
  p := ops.read_assurance_health('nonexistent.never-registered', 999, 'WR-999');
  perform pg_temp.a01_expect('B1 an unregistered workflow with six passes never reads green through the read door',
    p->>'state' = 'unknown' and (p->>'green')::boolean is false and p->>'capability_stage' = 'unavailable', p);
  p := pg_temp.a01_label('nonexistent.never-registered', 999, 'WR-999', 'unregistered');
  perform pg_temp.a01_expect('B1 F09 unregistered truth with six passes is not green and never act',
    p->>'state' = 'not-yet-operational' and (p->>'green')::boolean is false and p->>'capability_stage' = 'read', p);
  p := pg_temp.a01_label('doctorcre.release', 7, 'WR-700', 'declared_disabled');
  perform pg_temp.a01_expect('F09 declared_disabled reads disabled',
    p->>'state' = 'disabled' and (p->>'green')::boolean is false and p->>'capability_stage' = 'read', p);
  p := pg_temp.a01_label('doctorcre.release', 7, 'WR-700', 'unknown');
  perform pg_temp.a01_expect('F09 unknown truth reads unknown',
    p->>'state' = 'unknown' and p->>'capability_stage' = 'unavailable', p);
  p := pg_temp.a01_label('doctorcre.release', 7, 'WR-700', 'enabled_shadow_only');
  perform pg_temp.a01_expect('F09 shadow-only truth caps the stage at draft and is not yet operational',
    p->>'state' = 'not-yet-operational' and p->>'capability_stage' = 'draft', p);
  p := pg_temp.a01_label('doctorcre.release', 7, 'WR-700', 'operational');
  perform pg_temp.a01_expect('six distinct current passes plus F09 live admission is the only green',
    p->>'state' = 'healthy' and (p->>'green')::boolean and p->>'capability_stage' = 'act', p);
  perform pg_temp.a01_refused('workflow truth for another workflow is refused',
    format('select ops.assurance_health_label(%L,%s,%L,%L::jsonb)', 'doctorcre.release', 7, 'WR-700',
      pg_temp.a01_truth('doctorcre.release', 8, 'operational')), '22023', 'assurance_health_workflow_truth_invalid');

  -- ---- scoped failure injection -----------------------------------------------
  q := pg_temp.a01_label('doctorcre.release', 8, 'WR-800', 'operational');
  perform ops.record_assurance_health_evidence(scope_a,
    pg_temp.a01_evidence('actual_business_outcome', 'fail', 'a:outcome-failure', interval '-30 seconds'), gen_random_uuid());
  p := pg_temp.a01_label('doctorcre.release', 7, 'WR-700', 'operational');
  perform pg_temp.a01_expect('a scoped outcome failure degrades only the affected scope to draft',
    p->>'state' = 'degraded' and p->>'capability_stage' = 'draft'
    and p#>>'{evidence,actual_business_outcome,state}' = 'failed', p);
  perform pg_temp.a01_expect('the failure does not escape its exact scope',
    pg_temp.a01_label('doctorcre.release', 8, 'WR-800', 'operational') = q and q->>'state' = 'healthy', q);

  -- ---- missing layer --------------------------------------------------------------
  perform pg_temp.a01_six('{"workflow_key":"doctorcre.release","workflow_version":9,"work_request_id":"WR-900"}',
    'm', array['activation_readback']);
  p := pg_temp.a01_label('doctorcre.release', 9, 'WR-900', 'operational');
  perform pg_temp.a01_expect('a missing activation readback blocks act and green; the outcome cannot pass without it',
    p->>'state' <> 'healthy' and p->>'capability_stage' = 'draft'
    and p#>>'{evidence,activation_readback,state}' = 'missing'
    and p#>>'{evidence,actual_business_outcome,state}' = 'conflicting', p);

  -- ---- S2: expired evidence is stale, never passing -----------------------------------
  perform pg_temp.a01_six('{"workflow_key":"a01.s2","workflow_version":1,"work_request_id":"WR-2"}', 's2',
    array['artifact_assessment']);
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.s2","workflow_version":1,"work_request_id":"WR-2"}',
    pg_temp.a01_evidence('artifact_assessment', 'pass', 's2:expired', interval '-2 hours', interval '-1 hour'), gen_random_uuid());
  p := pg_temp.a01_label('a01.s2', 1, 'WR-2', 'operational');
  perform pg_temp.a01_expect('S2 expired evidence reads stale and withdraws every stage',
    p#>>'{evidence,artifact_assessment,state}' = 'stale' and p->>'state' = 'failed'
    and (p->>'green')::boolean is false and p->>'capability_stage' = 'unavailable', p);

  -- ---- S3: one receipt can never fill two layers ----------------------------------------
  perform pg_temp.a01_six('{"workflow_key":"a01.s3","workflow_version":1,"work_request_id":"WR-3"}', 's3',
    array['execution_assessment']);
  ev := jsonb_set(pg_temp.a01_evidence('execution_assessment', 'pass', 's3:reuse'), '{evidence_digest}',
    to_jsonb(pg_temp.a01_evidence('artifact_assessment', 'pass', 's3:artifact_assessment')->>'evidence_digest'));
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.s3","workflow_version":1,"work_request_id":"WR-3"}', ev, gen_random_uuid());
  p := pg_temp.a01_label('a01.s3', 1, 'WR-3', 'operational');
  perform pg_temp.a01_expect('S3 a reused receipt digest makes both layers indistinct and blocks green',
    p#>>'{evidence,artifact_assessment,state}' = 'indistinct'
    and p#>>'{evidence,execution_assessment,state}' = 'indistinct'
    and p->>'state' = 'unknown' and (p->>'green')::boolean is false, p);
  key := gen_random_uuid();
  ev := pg_temp.a01_evidence('artifact_assessment', 'pass', 's3:idem');
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.s3","workflow_version":2}', ev, key);
  select count(*) into n from ops.assurance_health_evidence;
  receipt := ops.record_assurance_health_evidence('{"workflow_key":"a01.s3","workflow_version":2}', ev, key);
  perform pg_temp.a01_expect('S3 an identical replay returns the stored receipt and writes nothing',
    (receipt->>'replayed')::boolean and (select count(*) from ops.assurance_health_evidence) = n, receipt);
  perform pg_temp.a01_refused('S3 a reused idempotency key with a different receipt is refused',
    format('select ops.record_assurance_health_evidence(%L::jsonb,%L::jsonb,%L::uuid)',
      '{"workflow_key":"a01.s3","workflow_version":2}', jsonb_set(ev, '{status}', '"fail"'), key),
    '23505', 'assurance_health_idempotency_conflict');

  -- ---- S4 / P14: instants after the read clock are conflicting ---------------------------
  -- artifact_assessment carries no readback_at, so only observed_at can make it conflicting.
  perform pg_temp.a01_six('{"workflow_key":"a01.s4","workflow_version":1,"work_request_id":"WR-4"}', 's4',
    array['artifact_assessment']);
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.s4","workflow_version":1,"work_request_id":"WR-4"}',
    pg_temp.a01_evidence('artifact_assessment', 'pass', 's4:future', interval '2 minutes'), gen_random_uuid());
  p := pg_temp.a01_label('a01.s4', 1, 'WR-4', 'operational');
  perform pg_temp.a01_expect('S4 a future observed_at inside the skew window reads conflicting, not passing',
    p#>>'{evidence,artifact_assessment,state}' = 'conflicting' and (p->>'green')::boolean is false, p);
  perform pg_temp.a01_refused('S4 an observed_at beyond the skew window is refused at ingress',
    format('select ops.record_assurance_health_evidence(%L::jsonb,%L::jsonb,%L::uuid)',
      '{"workflow_key":"a01.s4","workflow_version":2}',
      pg_temp.a01_evidence('artifact_assessment', 'pass', 's4:far', interval '10 minutes'), gen_random_uuid()),
    '22023', 'assurance_health_evidence_time_invalid');
  perform pg_temp.a01_six('{"workflow_key":"a01.p14","workflow_version":1,"work_request_id":"WR-14"}', 'p14',
    array['activation_readback']);
  ev := pg_temp.a01_evidence('activation_readback', 'pass', 'p14:readback');
  ev := jsonb_set(ev, '{detail,readback_at}', to_jsonb(pg_temp.a01_ts(now() + interval '2 minutes')));
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.p14","workflow_version":1,"work_request_id":"WR-14"}', ev, gen_random_uuid());
  p := pg_temp.a01_label('a01.p14', 1, 'WR-14', 'operational');
  perform pg_temp.a01_expect('P14 an activation readback_at after the read clock reads conflicting',
    p#>>'{evidence,activation_readback,state}' = 'conflicting' and p->>'capability_stage' <> 'act', p);
  perform pg_temp.a01_refused('an unparseable readback_at is refused at ingress',
    format('select ops.record_assurance_health_evidence(%L::jsonb,%L::jsonb,%L::uuid)',
      '{"workflow_key":"a01.p14","workflow_version":2}',
      jsonb_set(pg_temp.a01_evidence('controller_assessment', 'pass', 'p14:bad'), '{detail,readback_at}', '"yesterday"'),
      gen_random_uuid()),
    '22023', 'assurance_health_instant_invalid');

  -- ---- S5 / S6: the Work Request is part of the exact scope, both ways ---------------------
  perform pg_temp.a01_six('{"workflow_key":"a01.s5","workflow_version":1,"work_request_id":"WR-501"}', 's5a');
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.s5","workflow_version":1,"work_request_id":"WR-502"}',
    pg_temp.a01_evidence('artifact_assessment', 'fail', 's5b:artifact', interval '-10 seconds'), gen_random_uuid());
  p := pg_temp.a01_label('a01.s5', 1, 'WR-501', 'operational');
  perform pg_temp.a01_expect('S5 a later failure under another Work Request of the same version does not reach this one',
    p->>'state' = 'healthy' and p#>>'{evidence,artifact_assessment,state}' = 'passing', p);
  p := pg_temp.a01_label('a01.s5', 1, 'WR-502', 'operational');
  perform pg_temp.a01_expect('S5 the sibling Work Request sees only its own evidence',
    p#>>'{evidence,artifact_assessment,state}' = 'failed'
    and p#>>'{evidence,execution_assessment,state}' = 'missing', p);
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.s6","workflow_version":1,"work_request_id":"WR-601"}',
    pg_temp.a01_evidence('artifact_assessment', 'fail', 's6a:artifact', interval '-3 minutes'), gen_random_uuid());
  perform pg_temp.a01_six('{"workflow_key":"a01.s6","workflow_version":1,"work_request_id":"WR-602"}', 's6b');
  p := pg_temp.a01_label('a01.s6', 1, 'WR-601', 'operational');
  perform pg_temp.a01_expect('S6 later passes under another Work Request of the same version cannot heal this one',
    p#>>'{evidence,artifact_assessment,state}' = 'failed' and p->>'state' = 'failed'
    and p#>>'{evidence,execution_assessment,state}' = 'missing', p);
  p := pg_temp.a01_label('a01.s6', 1, null, 'operational');
  perform pg_temp.a01_expect('S6 a scope with no Work Request sees none of the Work-Request-bound evidence',
    (select count(*) from jsonb_each(p->'evidence') e where (e.value->>'present')::boolean) = 0
    and p#>>'{evidence,actual_business_outcome,state}' = 'unbindable', p);
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.s6n","workflow_version":1,"work_request_id":"WR-61"}',
    pg_temp.a01_evidence('artifact_assessment', 'fail', 's6n:wr', interval '-3 minutes'), gen_random_uuid());
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.s6n","workflow_version":1}',
    pg_temp.a01_evidence('artifact_assessment', 'pass', 's6n:unbound', interval '-5 seconds'), gen_random_uuid());
  p := pg_temp.a01_label('a01.s6n', 1, 'WR-61', 'operational');
  perform pg_temp.a01_expect('S6 a later pass bound to no Work Request cannot heal a Work-Request-bound scope',
    p#>>'{evidence,artifact_assessment,state}' = 'failed', p);
  perform pg_temp.a01_refused('an outcome receipt without a Work Request is refused at ingress',
    format('select ops.record_assurance_health_evidence(%L::jsonb,%L::jsonb,%L::uuid)',
      '{"workflow_key":"a01.s6","workflow_version":1}',
      pg_temp.a01_evidence('actual_business_outcome', 'pass', 's6:no-wr'), gen_random_uuid()),
    '22023', 'assurance_health_outcome_requires_work_request');

  -- ---- S8 / P4: an outcome without activation is conflicting ----------------------------------
  perform pg_temp.a01_six('{"workflow_key":"a01.s8","workflow_version":1,"work_request_id":"WR-8"}', 's8',
    array['activation_readback']);
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.s8","workflow_version":1,"work_request_id":"WR-8"}',
    pg_temp.a01_evidence('activation_readback', 'fail', 's8:activation'), gen_random_uuid());
  p := pg_temp.a01_label('a01.s8', 1, 'WR-8', 'operational');
  perform pg_temp.a01_expect('S8 a passing business outcome with a failed activation readback is conflicting',
    p#>>'{evidence,actual_business_outcome,state}' = 'conflicting'
    and p#>>'{evidence,activation_readback,state}' = 'failed'
    and p->>'state' = 'degraded' and p->>'capability_stage' = 'draft', p);

  -- ---- P13: capability lost only to an unreadable layer is not attributed to the failure -----
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.p13","workflow_version":1,"work_request_id":"WR-13"}',
    pg_temp.a01_evidence('artifact_assessment', 'conflicting', 'p13:artifact'), gen_random_uuid());
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.p13","workflow_version":1,"work_request_id":"WR-13"}',
    pg_temp.a01_evidence('execution_assessment', 'fail', 'p13:execution'), gen_random_uuid());
  p := pg_temp.a01_label('a01.p13', 1, 'WR-13', 'operational');
  perform pg_temp.a01_expect('P13 an indeterminate artifact plus a failed execution is degraded, not failed',
    p->>'state' = 'degraded' and p->>'capability_stage' = 'unavailable'
    and p->>'capability_stage_attributable_to_findings' = 'read', p);
  perform ops.record_assurance_health_evidence('{"workflow_key":"a01.p13","workflow_version":2,"work_request_id":"WR-13"}',
    pg_temp.a01_evidence('execution_assessment', 'fail', 'p13:execution-only'), gen_random_uuid());
  p := pg_temp.a01_label('a01.p13', 2, 'WR-13', 'operational');
  perform pg_temp.a01_expect('a missing artifact (read, and absent) plus a failed execution is failed, as the contract says',
    p->>'state' = 'failed' and p->>'capability_stage_attributable_to_findings' = 'unavailable', p);

  -- ---- privileges -------------------------------------------------------------------------------
  perform pg_temp.a01_expect('no runtime role holds DML on the evidence relation',
    not has_table_privilege('carr_writer', 'ops.assurance_health_evidence', 'insert')
    and not has_table_privilege('carr_authority', 'ops.assurance_health_evidence', 'insert'), '{}');
  perform pg_temp.a01_expect('both doors are executable by carr_writer',
    has_function_privilege('carr_writer', 'ops.record_assurance_health_evidence(jsonb,jsonb,uuid)', 'execute')
    and has_function_privilege('carr_writer', 'ops.read_assurance_health(text,integer,text)', 'execute'), '{}');
  perform pg_temp.a01_expect('the label predicate, which accepts workflow truth as an argument, is granted to no runtime role',
    not has_function_privilege('carr_writer', 'ops.assurance_health_label(text,integer,text,jsonb)', 'execute')
    and not has_function_privilege('carr_authority', 'ops.assurance_health_label(text,integer,text,jsonb)', 'execute')
    and not has_function_privilege('carr_reader', 'ops.assurance_health_label(text,integer,text,jsonb)', 'execute')
    and not has_function_privilege('carr_jobs', 'ops.assurance_health_label(text,integer,text,jsonb)', 'execute'), '{}');

  -- ---- S12: append-only for every role, the owner included ---------------------------------------
  perform pg_temp.a01_refused('S12 owner UPDATE is refused',
    'update ops.assurance_health_evidence set status = ''pass''', '55000');
  perform pg_temp.a01_refused('S12 owner DELETE is refused',
    'delete from ops.assurance_health_evidence', '55000');
  perform pg_temp.a01_refused('S12 owner TRUNCATE is refused',
    'truncate ops.assurance_health_evidence', '55000');

  -- ---- S10: an evaluator can never attest its own subject -----------------------------------------
  perform pg_temp.a01_refused('S10 the door refuses a receipt whose subject is the acting evaluator',
    format('select ops.record_assurance_health_evidence(%L::jsonb,%L::jsonb,%L::uuid)',
      '{"workflow_key":"a01.s10","workflow_version":1,"work_request_id":"WR-10"}',
      jsonb_set(pg_temp.a01_evidence('artifact_assessment', 'pass', 's10:self'), '{subject_ref}', '"joe"'),
      gen_random_uuid()),
    '22023', 'assurance_health_evidence_invalid');
  perform pg_temp.a01_refused('S10 the relation refuses a self-attested row that skips the door',
    $sql$insert into ops.assurance_health_evidence(tenant,workflow_key,workflow_version,work_request_id,layer,basis,status,
      subject_ref,evaluator_actor_id,evaluator_slug,evidence_ref,evidence_digest,observed_at,expires_at,detail,
      incident_refs,recovery_refs,receipt_digest,idempotency_key)
    select 'carr-internal','a01.s10',1,'WR-10','artifact_assessment','independent_artifact_review','pass',
      'joe',a.id,'joe','evidence:s10:direct','sha256:'||repeat('1',64),now()-interval '1 minute',now()+interval '1 day',
      '{}'::jsonb,'{}','{}','sha256:'||repeat('2',64),gen_random_uuid() from public.actor a where a.slug='joe'$sql$,
    '23514');
  -- The read's own self-attestation check, proven with the relation guard
  -- lifted inside this rolled-back transaction.
  alter table ops.assurance_health_evidence drop constraint assurance_health_evidence_subject;
  perform pg_temp.a01_six('{"workflow_key":"a01.s10","workflow_version":1,"work_request_id":"WR-10"}', 's10',
    array['artifact_assessment']);
  insert into ops.assurance_health_evidence(tenant,workflow_key,workflow_version,work_request_id,layer,basis,status,
    subject_ref,evaluator_actor_id,evaluator_slug,evidence_ref,evidence_digest,observed_at,expires_at,detail,
    incident_refs,recovery_refs,receipt_digest,idempotency_key)
  select 'carr-internal','a01.s10',1,'WR-10','artifact_assessment','independent_artifact_review','pass',
    'joe',a.id,'joe','evidence:s10:lifted','sha256:'||repeat('3',64),now()-interval '1 minute',now()+interval '1 day',
    jsonb_build_object('repository_commit_sha',repeat('a',40),'repository_tree_sha',repeat('b',40),'reviewer_fact_id','rf'),
    '{}','{}','sha256:'||repeat('4',64),gen_random_uuid() from public.actor a where a.slug='joe';
  p := pg_temp.a01_label('a01.s10', 1, 'WR-10', 'operational');
  perform pg_temp.a01_expect('S10 a self-attested receipt reads self_attested and withdraws every stage',
    p#>>'{evidence,artifact_assessment,state}' = 'self_attested' and p->>'state' = 'failed', p);
end $$;

rollback;
