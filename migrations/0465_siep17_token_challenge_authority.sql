-- 0465 / SIEP-17 / SCAC-07: single-use PoP challenges, signed capability-token
-- receipts, permanent revocation, and Joe-only global kill-switch events.
--
-- Source/test implementation only. These facts are necessary evidence but
-- never authorize a business write. SIEP-18 remains the sole atomic database
-- reference monitor. Applying this migration to Production remains Joe-gated.
-- Intentionally no BEGIN/COMMIT: tools/migrate.py owns the transaction.

create table ops.scac_token_kill_switch_event (
  event_no bigint primary key check (event_no>0),
  event_digest text not null unique check (event_digest~'^sha256:[0-9a-f]{64}$'),
  previous_event_digest text unique,
  action text not null check (action in ('engage','release')),
  reason_digest text not null check (reason_digest~'^sha256:[0-9a-f]{64}$'),
  idempotency_key uuid not null unique,
  recorded_by text not null check (recorded_by='joe'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  foreign key (previous_event_digest)
    references ops.scac_token_kill_switch_event(event_digest) on delete restrict,
  check ((event_no=1 and previous_event_digest is null) or
         (event_no>1 and previous_event_digest is not null))
);

create table ops.scac_token_revocation_event (
  event_id uuid primary key default gen_random_uuid(),
  subject_kind text not null check (subject_kind in
    ('device','device_key','facts','workload','challenge','token','issuer_key','root_event')),
  subject_digest text not null check (subject_digest~'^sha256:[0-9a-f]{64}$'),
  reason_digest text not null check (reason_digest~'^sha256:[0-9a-f]{64}$'),
  idempotency_key uuid not null unique,
  recorded_by text not null check (recorded_by='joe'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  unique (subject_kind,subject_digest)
);

create table ops.scac_pop_challenge (
  challenge_id uuid primary key default gen_random_uuid(),
  schema_version text not null check (schema_version='scac-pop-challenge.v1'),
  tenant_scope text not null check (tenant_scope='carr-internal'),
  environment text not null check (environment='source-test'),
  principal_digest text not null check (principal_digest~'^sha256:[0-9a-f]{64}$'),
  device_ref text not null references ops.scac_device_enrollment(device_ref) on delete restrict,
  device_key_digest text not null check (device_key_digest~'^sha256:[0-9a-f]{64}$'),
  facts_digest text not null check (facts_digest~'^sha256:[0-9a-f]{64}$'),
  workload_digest text check (workload_digest is null or workload_digest~'^sha256:[0-9a-f]{64}$'),
  policy_epoch bigint not null,
  policy_epoch_digest text not null check (policy_epoch_digest~'^sha256:[0-9a-f]{64}$'),
  registry_version text not null,
  registry_digest text not null check (registry_digest~'^sha256:[0-9a-f]{64}$'),
  ingress_key text not null check (ingress_key~'^[a-z][a-z0-9_-]+:' and
    ingress_key!~E'[\n\r\t]' and char_length(ingress_key)<=1000),
  mutation_kind text not null check (mutation_kind~'^scac\.mutation\.[a-z_]+$'),
  target_surface text not null check (target_surface~'^scac\.surface\.[a-z_]+$'),
  operation_manifest_digest text not null
    check (operation_manifest_digest~'^sha256:[0-9a-f]{64}$'),
  request_digest text not null check (request_digest~'^sha256:[0-9a-f]{64}$'),
  idempotency_digest text not null check (idempotency_digest~'^sha256:[0-9a-f]{64}$'),
  nonce_bytes bytea not null check (octet_length(nonce_bytes)=32),
  nonce_digest text not null unique check (nonce_digest~'^sha256:[0-9a-f]{64}$'),
  issued_at timestamptz not null,
  expires_at timestamptz not null,
  challenge_digest text not null unique check (challenge_digest~'^sha256:[0-9a-f]{64}$'),
  request_fingerprint text not null check (request_fingerprint~'^sha256:[0-9a-f]{64}$'),
  issue_idempotency_key uuid not null unique,
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  foreign key (policy_epoch,policy_epoch_digest)
    references ops.scac_policy_epoch(epoch,epoch_digest) on delete restrict,
  foreign key (registry_version,ingress_key)
    references ops.scac_mutation_registry_entry(registry_version,ingress_key) on delete restrict,
  check (request_digest=operation_manifest_digest),
  check (expires_at>issued_at and expires_at<=issued_at+interval '5 minutes'),
  check (nonce_digest='sha256:'||encode(public.digest(nonce_bytes,'sha256'),'hex'))
);

create table ops.scac_pop_challenge_consumption (
  challenge_id uuid primary key references ops.scac_pop_challenge(challenge_id) on delete restrict,
  challenge_digest text not null unique check (challenge_digest~'^sha256:[0-9a-f]{64}$'),
  pop_verification_digest text not null unique
    check (pop_verification_digest~'^sha256:[0-9a-f]{64}$'),
  consumption_digest text not null unique check (consumption_digest~'^sha256:[0-9a-f]{64}$'),
  consume_idempotency_key uuid not null unique,
  consumed_at timestamptz not null default clock_timestamp(),
  token_intent_state text not null default 'eligible_for_external_signing_non_authorizing'
    check (token_intent_state='eligible_for_external_signing_non_authorizing'),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active)
);

create table ops.scac_capability_token_receipt (
  token_ref_digest text primary key check (token_ref_digest~'^sha256:[0-9a-f]{64}$'),
  challenge_id uuid not null unique
    references ops.scac_pop_challenge_consumption(challenge_id) on delete restrict,
  challenge_digest text not null unique check (challenge_digest~'^sha256:[0-9a-f]{64}$'),
  signed_payload_digest text not null unique check (signed_payload_digest~'^sha256:[0-9a-f]{64}$'),
  signature_digest text not null unique check (signature_digest~'^sha256:[0-9a-f]{64}$'),
  issuer_key_digest text not null check (issuer_key_digest~'^sha256:[0-9a-f]{64}$'),
  issuer_root_event_digest text not null check (issuer_root_event_digest~'^sha256:[0-9a-f]{64}$'),
  external_verification_receipt_digest text not null unique
    check (external_verification_receipt_digest~'^sha256:[0-9a-f]{64}$'),
  issued_at timestamptz not null,
  expires_at timestamptz not null,
  record_idempotency_key uuid not null unique,
  cryptographic_state text not null default 'external_verification_recorded_non_authorizing'
    check (cryptographic_state='external_verification_recorded_non_authorizing'),
  admission_state text not null default 'ineligible_pending_siep18'
    check (admission_state='ineligible_pending_siep18'),
  routing_eligible boolean not null default false check (not routing_eligible),
  privileges_active boolean not null default false check (not privileges_active),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  recorded_at timestamptz not null default clock_timestamp(),
  check (expires_at>issued_at and expires_at<=issued_at+interval '5 minutes')
);

comment on table ops.scac_pop_challenge is
  'SIEP-17 private immutable challenge ledger. Raw nonce access is denied; issuance is typed, DB-clocked, exact-request bound, and non-authorizing.';
comment on table ops.scac_pop_challenge_consumption is
  'SIEP-17 atomic single-use PoP consumption. A receipt permits external token signing only and grants no business mutation authority.';
comment on table ops.scac_capability_token_receipt is
  'SIEP-17 digest-only external signature verification receipt. SIEP-18 must verify current control state inside the governed write transaction.';
comment on table ops.scac_token_revocation_event is
  'SIEP-17 permanent Joe-authorized subject revocation facts. Dell cannot freeze Joe or central core.';
comment on table ops.scac_token_kill_switch_event is
  'SIEP-17 append-only Joe-authorized global control. No events means active fail-closed; release is a new event, never an update.';

create or replace function ops.scac_token_sha256_text(p_value text)
returns text language sql immutable strict set search_path=pg_catalog,public as $fn$
  select 'sha256:'||encode(public.digest(convert_to(p_value,'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_pop_challenge_digest(
  p_challenge_id uuid,p_device_ref text,p_device_key_digest text,p_facts_digest text,
  p_policy_epoch bigint,p_policy_epoch_digest text,p_operation_manifest_digest text,
  p_nonce text,p_issued_at text,p_expires_at text
) returns text language sql immutable strict set search_path=pg_catalog,public,ops as $fn$
  select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'domain','CARR-SCAC-POP-V1','schema_version','scac-pop-challenge.v1',
    'challenge_id',p_challenge_id::text,'device_ref',p_device_ref,
    'device_key_digest',p_device_key_digest,'facts_digest',p_facts_digest,
    'policy_epoch',p_policy_epoch,'policy_epoch_digest',p_policy_epoch_digest,
    'operation_manifest_digest',p_operation_manifest_digest,'nonce',p_nonce,
    'issued_at',p_issued_at,'expires_at',p_expires_at)),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_token_kill_event_digest(
  p_event_no bigint,p_previous_event_digest text,p_action text,p_reason_digest text,
  p_idempotency_key uuid
) returns text language sql immutable set search_path=pg_catalog,public,ops as $fn$
  select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-token-kill-switch-event.v1','event_no',p_event_no,
    'previous_event_digest',p_previous_event_digest,'action',p_action,
    'reason_digest',p_reason_digest,'idempotency_key',p_idempotency_key::text,
    'recorded_by','joe')),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_token_control_snapshot()
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops as $fn$
declare e ops.scac_token_kill_switch_event%rowtype; expected_no bigint:=1; prior text:=null;
        expected_digest text; latest_action text:=null;
begin
  for e in select * from ops.scac_token_kill_switch_event order by event_no loop
    expected_digest:=ops.scac_token_kill_event_digest(e.event_no,e.previous_event_digest,
      e.action,e.reason_digest,e.idempotency_key);
    if e.event_no<>expected_no or e.previous_event_digest is distinct from prior or
       e.event_digest is distinct from expected_digest or e.production_enforcement_active then
      return jsonb_build_object('schema_version','siep17-token-control-snapshot.v1',
        'kill_switch_state','active','reason_id','scac.refusal.kill_switch',
        'control_integrity_state','invalid_fail_closed','latest_event_digest',prior,
        'admission_state','ineligible_pending_siep18','production_enforcement_active',false);
    end if;
    prior:=e.event_digest; latest_action:=e.action; expected_no:=expected_no+1;
  end loop;
  if expected_no=1 then
    return jsonb_build_object('schema_version','siep17-token-control-snapshot.v1',
      'kill_switch_state','active','reason_id','scac.refusal.kill_switch',
      'control_integrity_state','uninitialized_fail_closed','latest_event_digest',null,
      'admission_state','ineligible_pending_siep18','production_enforcement_active',false);
  end if;
  return jsonb_build_object('schema_version','siep17-token-control-snapshot.v1',
    'kill_switch_state',case when latest_action='release' then 'inactive' else 'active' end,
    'reason_id',case when latest_action='release' then null else 'scac.refusal.kill_switch' end,
    'control_integrity_state','valid_append_only_chain','latest_event_digest',prior,
    'admission_state','ineligible_pending_siep18','production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_transition_token_kill_switch(
  p_action text,p_reason_digest text,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare tip ops.scac_token_kill_switch_event%rowtype; prior ops.scac_token_kill_switch_event%rowtype;
        next_no bigint; next_digest text;
begin
  if session_user<>'carr_authority_joe' then
    raise exception 'SIEP-17 global kill-switch authority is Joe-only';
  end if;
  if p_action not in ('engage','release') or coalesce(p_reason_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or p_idempotency_key is null then raise exception 'SIEP-17 kill-switch input malformed'; end if;
  perform pg_advisory_xact_lock(hashtextextended('carr-siep17-token-control',0));
  select * into prior from ops.scac_token_kill_switch_event where idempotency_key=p_idempotency_key;
  if prior.event_no is not null then
    if prior.action is distinct from p_action or prior.reason_digest is distinct from p_reason_digest then
      raise exception 'SIEP-17 kill-switch idempotency binding mismatch';
    end if;
    return ops.scac_token_control_snapshot();
  end if;
  select * into tip from ops.scac_token_kill_switch_event order by event_no desc limit 1;
  if tip.event_no is not null and
     ((tip.action='release' and p_action='release') or (tip.action='engage' and p_action='engage')) then
    raise exception 'SIEP-17 kill-switch transition must change current state';
  end if;
  next_no:=coalesce(tip.event_no,0)+1;
  next_digest:=ops.scac_token_kill_event_digest(next_no,tip.event_digest,p_action,
    p_reason_digest,p_idempotency_key);
  insert into ops.scac_token_kill_switch_event
    (event_no,event_digest,previous_event_digest,action,reason_digest,idempotency_key,recorded_by)
  values (next_no,next_digest,tip.event_digest,p_action,p_reason_digest,p_idempotency_key,'joe');
  return ops.scac_token_control_snapshot();
end $fn$;

create or replace function ops.scac_revoke_token_subject(
  p_subject_kind text,p_subject_digest text,p_reason_digest text,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,ops as $fn$
declare prior ops.scac_token_revocation_event%rowtype; inserted_id uuid;
begin
  if session_user<>'carr_authority_joe' then
    raise exception 'SIEP-17 revocation authority is Joe-only';
  end if;
  if p_subject_kind not in ('device','device_key','facts','workload','challenge','token','issuer_key','root_event')
     or coalesce(p_subject_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_reason_digest,'')!~'^sha256:[0-9a-f]{64}$' or p_idempotency_key is null then
    raise exception 'SIEP-17 revocation input malformed';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('carr-siep17-token-control',0));
  select * into prior from ops.scac_token_revocation_event where idempotency_key=p_idempotency_key;
  if prior.event_id is not null then
    if prior.subject_kind is distinct from p_subject_kind or prior.subject_digest is distinct from p_subject_digest
       or prior.reason_digest is distinct from p_reason_digest then
      raise exception 'SIEP-17 revocation idempotency binding mismatch';
    end if;
    return jsonb_build_object('revocation_state','revoked','subject_kind',prior.subject_kind,
      'subject_digest',prior.subject_digest,'reason_id','scac.refusal.revoked',
      'admission_state','ineligible_pending_siep18','production_enforcement_active',false);
  end if;
  insert into ops.scac_token_revocation_event
    (subject_kind,subject_digest,reason_digest,idempotency_key,recorded_by)
  values (p_subject_kind,p_subject_digest,p_reason_digest,p_idempotency_key,'joe')
  on conflict (subject_kind,subject_digest) do nothing returning event_id into inserted_id;
  if inserted_id is null then
    raise exception 'SIEP-17 revocation subject already bound to a different idempotency key';
  end if;
  return jsonb_build_object('revocation_state','revoked','subject_kind',p_subject_kind,
    'subject_digest',p_subject_digest,'reason_id','scac.refusal.revoked',
    'admission_state','ineligible_pending_siep18','production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_issue_pop_challenge(
  p_principal_digest text,p_device_ref text,p_workload_digest text,p_ingress_key text,
  p_operation_manifest_digest text,p_idempotency_digest text,p_ttl_seconds integer,
  p_issue_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare e ops.scac_device_enrollment%rowtype; pe ops.scac_policy_epoch%rowtype;
        re ops.scac_mutation_registry_entry%rowtype; prior ops.scac_pop_challenge%rowtype;
        control jsonb; fingerprint text; challenge_uuid uuid; nonce bytea;
        v_issued timestamptz; v_expires timestamptz; issued_text text; expires_text text;
        nonce_text text; challenge_hash text;
begin
  if session_user<>'carr_jobs' then raise exception 'SIEP-17 challenge issuer role refused'; end if;
  if coalesce(p_principal_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_operation_manifest_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_idempotency_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or (p_workload_digest is not null and p_workload_digest!~'^sha256:[0-9a-f]{64}$')
     or coalesce(p_device_ref,'')!~'^[a-z0-9][a-z0-9._-]{2,127}$'
     or coalesce(p_ingress_key,'')!~'^[a-z][a-z0-9_-]+:' or p_ingress_key~E'[\n\r\t]'
     or char_length(p_ingress_key)>1000 or p_ttl_seconds not between 1 and 300
     or p_issue_idempotency_key is null then raise exception 'SIEP-17 challenge input malformed'; end if;
  fingerprint:=ops.scac_token_sha256_text(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-pop-challenge-request.v1','principal_digest',p_principal_digest,
    'device_ref',p_device_ref,'workload_digest',p_workload_digest,'ingress_key',p_ingress_key,
    'operation_manifest_digest',p_operation_manifest_digest,
    'idempotency_digest',p_idempotency_digest,'ttl_seconds',p_ttl_seconds)));
  perform pg_advisory_xact_lock(hashtextextended('carr-siep17-token-control',0));
  control:=ops.scac_token_control_snapshot();
  if control->>'kill_switch_state'<>'inactive' then
    raise exception 'scac.refusal.kill_switch: SIEP-17 challenge issuance unavailable';
  end if;
  select * into prior from ops.scac_pop_challenge where issue_idempotency_key=p_issue_idempotency_key;
  if prior.challenge_id is not null then
    if prior.request_fingerprint is distinct from fingerprint then
      raise exception 'SIEP-17 challenge idempotency binding mismatch';
    end if;
  end if;
  select * into e from ops.scac_device_enrollment where device_ref=p_device_ref for key share;
  if e.device_ref is null or e.lifecycle_state<>'registered_pending_siep16_pop' or
     e.routing_eligible or e.privileges_active or e.production_enforcement_active then
    raise exception 'scac.refusal.revoked: SIEP-17 enrolled device unavailable';
  end if;
  select * into pe from ops.scac_policy_epoch order by epoch desc limit 1 for key share;
  if pe.epoch is null or pe.epoch<>e.policy_epoch or pe.epoch_digest<>e.policy_epoch_digest then
    raise exception 'scac.refusal.token_invalid: SIEP-17 current policy epoch mismatch';
  end if;
  select * into re from ops.scac_mutation_registry_entry
    where registry_version=pe.registry_version and ingress_key=p_ingress_key for key share;
  if re.ingress_key is null or re.effect_class='read_only' or
     coalesce((re.contract->>'classification_authorizing')::boolean,true) then
    raise exception 'scac.refusal.token_invalid: SIEP-17 registered mutation ingress unavailable';
  end if;
  if exists(select 1 from ops.scac_token_revocation_event r where
       (r.subject_kind='device' and r.subject_digest=ops.scac_token_sha256_text(e.device_ref)) or
       (r.subject_kind='device_key' and r.subject_digest=e.device_key_digest) or
       (r.subject_kind='facts' and r.subject_digest=e.facts_digest) or
       (p_workload_digest is not null and r.subject_kind='workload' and r.subject_digest=p_workload_digest)) then
    raise exception 'scac.refusal.revoked: SIEP-17 challenge subject revoked';
  end if;
  if prior.challenge_id is not null then
    if prior.device_key_digest is distinct from e.device_key_digest
       or prior.facts_digest is distinct from e.facts_digest
       or prior.policy_epoch is distinct from pe.epoch
       or prior.policy_epoch_digest is distinct from pe.epoch_digest
       or prior.registry_version is distinct from pe.registry_version
       or prior.registry_digest is distinct from pe.registry_digest
       or prior.expires_at<=clock_timestamp()
       or exists(select 1 from ops.scac_pop_challenge_consumption x where x.challenge_id=prior.challenge_id)
       or exists(select 1 from ops.scac_token_revocation_event r
            where r.subject_kind='challenge' and r.subject_digest=prior.challenge_digest) then
      raise exception 'scac.refusal.token_invalid: SIEP-17 prior challenge is no longer issuable';
    end if;
    return jsonb_build_object('schema_version',prior.schema_version,'challenge_id',prior.challenge_id::text,
      'device_ref',prior.device_ref,'device_key_digest',prior.device_key_digest,
      'facts_digest',prior.facts_digest,'policy_epoch',prior.policy_epoch,
      'policy_epoch_digest',prior.policy_epoch_digest,
      'operation_manifest_digest',prior.operation_manifest_digest,'nonce',encode(prior.nonce_bytes,'base64'),
      'issued_at',to_char(prior.issued_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'expires_at',to_char(prior.expires_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'));
  end if;
  challenge_uuid:=gen_random_uuid(); nonce:=gen_random_bytes(32); v_issued:=clock_timestamp();
  v_expires:=v_issued+make_interval(secs=>p_ttl_seconds);
  issued_text:=to_char(v_issued at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"');
  expires_text:=to_char(v_expires at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"');
  nonce_text:=encode(nonce,'base64');
  challenge_hash:=ops.scac_pop_challenge_digest(challenge_uuid,e.device_ref,e.device_key_digest,
    e.facts_digest,pe.epoch,pe.epoch_digest,p_operation_manifest_digest,nonce_text,issued_text,expires_text);
  insert into ops.scac_pop_challenge
    (challenge_id,schema_version,tenant_scope,environment,principal_digest,device_ref,
     device_key_digest,facts_digest,workload_digest,policy_epoch,policy_epoch_digest,
     registry_version,registry_digest,ingress_key,mutation_kind,target_surface,
     operation_manifest_digest,request_digest,idempotency_digest,nonce_bytes,nonce_digest,
     issued_at,expires_at,challenge_digest,request_fingerprint,issue_idempotency_key)
  values (challenge_uuid,'scac-pop-challenge.v1','carr-internal','source-test',p_principal_digest,
    e.device_ref,e.device_key_digest,e.facts_digest,p_workload_digest,pe.epoch,pe.epoch_digest,
    pe.registry_version,pe.registry_digest,re.ingress_key,re.contract->>'mutation_kind',
    re.contract->>'target_surface',p_operation_manifest_digest,p_operation_manifest_digest,
    p_idempotency_digest,nonce,'sha256:'||encode(public.digest(nonce,'sha256'),'hex'),v_issued,v_expires,
    challenge_hash,fingerprint,p_issue_idempotency_key);
  return jsonb_build_object('schema_version','scac-pop-challenge.v1',
    'challenge_id',challenge_uuid::text,'device_ref',e.device_ref,
    'device_key_digest',e.device_key_digest,'facts_digest',e.facts_digest,
    'policy_epoch',pe.epoch,'policy_epoch_digest',pe.epoch_digest,
    'operation_manifest_digest',p_operation_manifest_digest,'nonce',nonce_text,
    'issued_at',issued_text,'expires_at',expires_text);
end $fn$;

create or replace function ops.scac_consume_verified_pop_challenge(
  p_challenge_id uuid,p_challenge_digest text,p_nonce text,p_pop_verification_digest text,
  p_consume_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare c ops.scac_pop_challenge%rowtype; prior ops.scac_pop_challenge_consumption%rowtype;
        control jsonb; v_now timestamptz; consumption_hash text; inserted_id uuid;
begin
  if session_user<>'carr_jobs' then raise exception 'SIEP-17 challenge consumer role refused'; end if;
  if p_challenge_id is null or coalesce(p_challenge_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_pop_verification_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or p_consume_idempotency_key is null or p_nonce is null then
    raise exception 'SIEP-17 challenge consumption input malformed'; end if;
  perform pg_advisory_xact_lock(hashtextextended('carr-siep17-token-control',0));
  select * into c from ops.scac_pop_challenge where challenge_id=p_challenge_id for update;
  if c.challenge_id is null then raise exception 'scac.refusal.token_invalid: SIEP-17 challenge missing'; end if;
  control:=ops.scac_token_control_snapshot();
  if control->>'kill_switch_state'<>'inactive' then
    raise exception 'scac.refusal.kill_switch: SIEP-17 challenge consumption unavailable'; end if;
  v_now:=clock_timestamp();
  if c.expires_at<=v_now then raise exception 'scac.refusal.token_invalid: SIEP-17 challenge expired'; end if;
  if c.challenge_digest<>p_challenge_digest or encode(c.nonce_bytes,'base64')<>p_nonce or
     encode(decode(p_nonce,'base64'),'base64')<>p_nonce or
     c.nonce_digest<>'sha256:'||encode(public.digest(decode(p_nonce,'base64'),'sha256'),'hex') then
    raise exception 'scac.refusal.token_invalid: SIEP-17 challenge binding mismatch'; end if;
  if not exists(select 1 from ops.scac_policy_epoch pe where pe.epoch=c.policy_epoch
      and pe.epoch_digest=c.policy_epoch_digest and pe.epoch=(select max(epoch) from ops.scac_policy_epoch))
     or not exists(select 1 from ops.scac_device_enrollment e where e.device_ref=c.device_ref
      and e.device_key_digest=c.device_key_digest and e.facts_digest=c.facts_digest
      and e.policy_epoch=c.policy_epoch and e.policy_epoch_digest=c.policy_epoch_digest) then
    raise exception 'scac.refusal.token_invalid: SIEP-17 challenge policy or device is stale'; end if;
  if exists(select 1 from ops.scac_token_revocation_event r where
       (r.subject_kind='device' and r.subject_digest=ops.scac_token_sha256_text(c.device_ref)) or
       (r.subject_kind='device_key' and r.subject_digest=c.device_key_digest) or
       (r.subject_kind='facts' and r.subject_digest=c.facts_digest) or
       (c.workload_digest is not null and r.subject_kind='workload' and r.subject_digest=c.workload_digest) or
       (r.subject_kind='challenge' and r.subject_digest=c.challenge_digest)) then
    raise exception 'scac.refusal.revoked: SIEP-17 challenge subject revoked'; end if;
  select * into prior from ops.scac_pop_challenge_consumption where challenge_id=c.challenge_id;
  if prior.challenge_id is not null then
    if prior.consume_idempotency_key=p_consume_idempotency_key and
       prior.pop_verification_digest=p_pop_verification_digest and
       prior.challenge_digest=p_challenge_digest then
      return jsonb_build_object('challenge_digest',prior.challenge_digest,
        'consumption_digest',prior.consumption_digest,'token_intent_state',prior.token_intent_state,
        'token_state','missing','revocation_state','clear','kill_switch_state','inactive',
        'admission_state','ineligible_pending_siep18','routing_eligible',false,
        'privileges_active',false,'production_enforcement_active',false);
    end if;
    raise exception 'scac.refusal.token_invalid: SIEP-17 challenge replayed';
  end if;
  consumption_hash:=ops.scac_token_sha256_text(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-pop-challenge-consumption.v1','challenge_digest',c.challenge_digest,
    'pop_verification_digest',p_pop_verification_digest,
    'consume_idempotency_key',p_consume_idempotency_key::text)));
  insert into ops.scac_pop_challenge_consumption
    (challenge_id,challenge_digest,pop_verification_digest,consumption_digest,consume_idempotency_key)
  values (c.challenge_id,c.challenge_digest,p_pop_verification_digest,consumption_hash,p_consume_idempotency_key)
  on conflict (challenge_id) do nothing returning challenge_id into inserted_id;
  if inserted_id is null then raise exception 'scac.refusal.token_invalid: SIEP-17 challenge replayed'; end if;
  return jsonb_build_object('challenge_digest',c.challenge_digest,'consumption_digest',consumption_hash,
    'token_intent_state','eligible_for_external_signing_non_authorizing',
    'token_state','missing','revocation_state','clear','kill_switch_state','inactive',
    'admission_state','ineligible_pending_siep18','routing_eligible',false,
    'privileges_active',false,'production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_record_capability_token_receipt(
  p_challenge_digest text,p_token_ref_digest text,p_signed_payload_digest text,
  p_signature_digest text,p_issuer_key_digest text,p_issuer_root_event_digest text,
  p_external_verification_receipt_digest text,p_issued_at timestamptz,p_expires_at timestamptz,
  p_record_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,ops as $fn$
declare c ops.scac_pop_challenge%rowtype; prior ops.scac_capability_token_receipt%rowtype;
        control jsonb;
begin
  if session_user<>'carr_jobs' then raise exception 'SIEP-17 token receipt recorder role refused'; end if;
  if coalesce(p_challenge_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_token_ref_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_signed_payload_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_signature_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_issuer_key_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_issuer_root_event_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_external_verification_receipt_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or p_record_idempotency_key is null then raise exception 'SIEP-17 token receipt input malformed'; end if;
  perform pg_advisory_xact_lock(hashtextextended('carr-siep17-token-control',0));
  control:=ops.scac_token_control_snapshot();
  if control->>'kill_switch_state'<>'inactive' then
    raise exception 'scac.refusal.kill_switch: SIEP-17 token receipt unavailable'; end if;
  select * into prior from ops.scac_capability_token_receipt
    where record_idempotency_key=p_record_idempotency_key;
  if prior.token_ref_digest is not null then
    if prior.token_ref_digest<>p_token_ref_digest or prior.challenge_digest<>p_challenge_digest
       or prior.signed_payload_digest<>p_signed_payload_digest
       or prior.signature_digest<>p_signature_digest
       or prior.issuer_key_digest<>p_issuer_key_digest
       or prior.issuer_root_event_digest<>p_issuer_root_event_digest
       or prior.external_verification_receipt_digest<>p_external_verification_receipt_digest
       or prior.issued_at is distinct from p_issued_at
       or prior.expires_at is distinct from p_expires_at then
      raise exception 'SIEP-17 token receipt idempotency binding mismatch'; end if;
    return ops.scac_capability_token_status(prior.token_ref_digest);
  end if;
  select ch.* into c from ops.scac_pop_challenge ch join ops.scac_pop_challenge_consumption x
    on x.challenge_id=ch.challenge_id where ch.challenge_digest=p_challenge_digest for key share of ch;
  if c.challenge_id is null or p_issued_at<c.issued_at or p_issued_at>clock_timestamp()
     or p_expires_at<=clock_timestamp() or p_expires_at>p_issued_at+interval '5 minutes'
     or p_expires_at>c.expires_at then raise exception 'scac.refusal.token_invalid: SIEP-17 token time or challenge invalid'; end if;
  if exists(select 1 from ops.scac_token_revocation_event r where
       (r.subject_kind='device' and r.subject_digest=ops.scac_token_sha256_text(c.device_ref)) or
       (r.subject_kind='device_key' and r.subject_digest=c.device_key_digest) or
       (r.subject_kind='facts' and r.subject_digest=c.facts_digest) or
       (c.workload_digest is not null and r.subject_kind='workload' and r.subject_digest=c.workload_digest) or
       (r.subject_kind='challenge' and r.subject_digest=c.challenge_digest) or
       (r.subject_kind='token' and r.subject_digest=p_token_ref_digest) or
       (r.subject_kind='issuer_key' and r.subject_digest=p_issuer_key_digest) or
       (r.subject_kind='root_event' and r.subject_digest=p_issuer_root_event_digest)) then
    raise exception 'scac.refusal.revoked: SIEP-17 token receipt subject revoked'; end if;
  insert into ops.scac_capability_token_receipt
    (token_ref_digest,challenge_id,challenge_digest,signed_payload_digest,signature_digest,
     issuer_key_digest,issuer_root_event_digest,external_verification_receipt_digest,
     issued_at,expires_at,record_idempotency_key)
  values (p_token_ref_digest,c.challenge_id,p_challenge_digest,p_signed_payload_digest,p_signature_digest,
    p_issuer_key_digest,p_issuer_root_event_digest,p_external_verification_receipt_digest,
    p_issued_at,p_expires_at,p_record_idempotency_key);
  return ops.scac_capability_token_status(p_token_ref_digest);
end $fn$;

create or replace function ops.scac_capability_token_status(p_token_ref_digest text)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops as $fn$
declare t ops.scac_capability_token_receipt%rowtype; c ops.scac_pop_challenge%rowtype;
        control jsonb; revoked boolean:=false; token_state text; reason text;
begin
  control:=ops.scac_token_control_snapshot();
  select * into t from ops.scac_capability_token_receipt where token_ref_digest=p_token_ref_digest;
  if t.token_ref_digest is null then
    return jsonb_build_object('found',false,'token_state','missing','reason_id','scac.refusal.token_invalid',
      'kill_switch_state',control->>'kill_switch_state','revocation_state','clear',
      'admission_state','ineligible_pending_siep18','routing_eligible',false,
      'privileges_active',false,'production_enforcement_active',false);
  end if;
  select * into c from ops.scac_pop_challenge where challenge_id=t.challenge_id;
  select exists(select 1 from ops.scac_token_revocation_event r where
    (r.subject_kind='token' and r.subject_digest=t.token_ref_digest) or
    (r.subject_kind='challenge' and r.subject_digest=t.challenge_digest) or
    (r.subject_kind='device' and r.subject_digest=ops.scac_token_sha256_text(c.device_ref)) or
    (r.subject_kind='device_key' and r.subject_digest=c.device_key_digest) or
    (r.subject_kind='facts' and r.subject_digest=c.facts_digest) or
    (c.workload_digest is not null and r.subject_kind='workload' and r.subject_digest=c.workload_digest) or
    (r.subject_kind='issuer_key' and r.subject_digest=t.issuer_key_digest) or
    (r.subject_kind='root_event' and r.subject_digest=t.issuer_root_event_digest)) into revoked;
  if revoked then token_state:='revoked';
  elsif t.expires_at<=clock_timestamp() then token_state:='expired';
  else token_state:='valid'; end if;
  if control->>'kill_switch_state'='active' then reason:='scac.refusal.kill_switch';
  elsif revoked then reason:='scac.refusal.revoked';
  elsif token_state='expired' then reason:='scac.refusal.token_invalid';
  else reason:=null; end if;
  return jsonb_build_object('found',true,'token_ref_digest',t.token_ref_digest,
    'challenge_digest',t.challenge_digest,'token_state',token_state,'reason_id',reason,
    'kill_switch_state',control->>'kill_switch_state',
    'revocation_state',case when revoked then 'revoked' else 'clear' end,
    'cryptographic_state',t.cryptographic_state,
    'issuer_trust_state','unverified_pending_siep18_transaction_bridge',
    'admission_state','ineligible_pending_siep18','routing_eligible',false,
    'privileges_active',false,'production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_siep17_append_only_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin raise exception 'SIEP-17 token, challenge, revocation, and control facts are append-only'; end $fn$;
create or replace function ops.scac_siep17_truncate_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin raise exception 'SIEP-17 token, challenge, revocation, and control facts cannot be truncated'; end $fn$;

do $triggers$
declare relation_name text;
begin
  foreach relation_name in array array['scac_token_kill_switch_event','scac_token_revocation_event',
    'scac_pop_challenge','scac_pop_challenge_consumption','scac_capability_token_receipt'] loop
    execute format('create trigger %I before update or delete on ops.%I for each row execute function ops.scac_siep17_append_only_guard()',relation_name||'_immutable',relation_name);
    execute format('create trigger %I before truncate on ops.%I for each statement execute function ops.scac_siep17_truncate_guard()',relation_name||'_no_truncate',relation_name);
  end loop;
end $triggers$;

revoke all on table ops.scac_token_kill_switch_event,ops.scac_token_revocation_event,
  ops.scac_pop_challenge,ops.scac_pop_challenge_consumption,ops.scac_capability_token_receipt
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.scac_token_sha256_text(text),
  ops.scac_pop_challenge_digest(uuid,text,text,text,bigint,text,text,text,text,text),
  ops.scac_token_kill_event_digest(bigint,text,text,text,uuid),
  ops.scac_token_control_snapshot(),
  ops.scac_transition_token_kill_switch(text,text,uuid),
  ops.scac_revoke_token_subject(text,text,text,uuid),
  ops.scac_issue_pop_challenge(text,text,text,text,text,text,integer,uuid),
  ops.scac_consume_verified_pop_challenge(uuid,text,text,text,uuid),
  ops.scac_record_capability_token_receipt(text,text,text,text,text,text,text,timestamptz,timestamptz,uuid),
  ops.scac_capability_token_status(text),ops.scac_siep17_append_only_guard(),
  ops.scac_siep17_truncate_guard()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.scac_token_control_snapshot(),ops.scac_capability_token_status(text)
  to carr_jobs,carr_authority;
grant execute on function ops.scac_issue_pop_challenge(text,text,text,text,text,text,integer,uuid),
  ops.scac_consume_verified_pop_challenge(uuid,text,text,text,uuid),
  ops.scac_record_capability_token_receipt(text,text,text,text,text,text,text,timestamptz,timestamptz,uuid)
  to carr_jobs;
grant execute on function ops.scac_transition_token_kill_switch(text,text,uuid),
  ops.scac_revoke_token_subject(text,text,text,uuid)
  to carr_authority;

do $assert$
begin
  if exists(select 1 from information_schema.role_table_grants where table_schema='ops'
    and table_name in ('scac_token_kill_switch_event','scac_token_revocation_event',
      'scac_pop_challenge','scac_pop_challenge_consumption','scac_capability_token_receipt')
    and grantee in ('PUBLIC','carr_reader','carr_writer','carr_jobs','carr_authority')) then
    raise exception 'SIEP-17 runtime roles unexpectedly received raw token authority';
  end if;
  if has_function_privilege('carr_authority',
       'ops.scac_issue_pop_challenge(text,text,text,text,text,text,integer,uuid)'::regprocedure,'EXECUTE')
     or has_function_privilege('carr_jobs',
       'ops.scac_transition_token_kill_switch(text,text,uuid)'::regprocedure,'EXECUTE') then
    raise exception 'SIEP-17 typed least-privilege grants drifted';
  end if;
end $assert$;

-- forward-consistency patch (progressive_loop.py): re-agree ops.scac_mutation_catalog_v7_current() with this file's own catalog effect -- it is re-evaluated by every subsequent commit's deferred epoch-refresh trigger until the next writer checkpoint retires it. See RESULT.md, 'checkpoint 7 finding'.
CREATE OR REPLACE FUNCTION ops.scac_mutation_catalog_v7_current()
 RETURNS boolean
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'ops'
AS $function$
declare observed_count integer; observed_digest text;
begin
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),
  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),
  observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>324 or observed_digest<>'sha256:24741cd86e09f3bf968526711049f5af92c235f44b868786050366cc9c97279d' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,acl.grantee,acl.privilege_type,acl.is_grantable from pg_class c join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>285 or observed_digest<>'sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(a.attacl) acl where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0 and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>12 or observed_digest<>'sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f' then return false; end if;
  with recursive connected(oid) as (
    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' and not rolcanlogin and not rolsuper union
    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname~'^carr_' and other.rolname<>'carr_ci' and not other.rolcanlogin and not other.rolsuper
  ), role_rows as (
    select 'db-role:'||r.rolname ingress_key,jsonb_build_object('ingress_key','db-role:'||r.rolname,'row_kind','role','role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,'superuser',r.rolsuper,'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row from pg_roles r where r.oid in(select oid from connected)
  ), membership_rows as (
    select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,'row_kind','membership','role',role.rolname,'member',member.rolname,'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row from pg_auth_members m join pg_roles role on role.oid=m.roleid join pg_roles member on member.oid=m.member where m.roleid in(select oid from connected) and m.member in(select oid from connected)
  ), ownership_rows as (
    select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner' union all
    select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname) row from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
  ), observed as (select * from role_rows union all select * from membership_rows union all select * from ownership_rows)
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if exists (select 1 from pg_auth_members m join pg_roles g on g.oid=m.roleid join pg_roles mem on mem.oid=m.member where mem.rolname~'^carr_' and (g.rolsuper or g.rolname~'^(neon_|pg_)')) then return false; end if;
  return observed_count=12 and observed_digest='sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648';
end $function$;
