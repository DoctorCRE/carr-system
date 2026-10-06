-- 0458 / SIEP-14 / SCAC-04: public root descriptors, quorum ceremony facts, rotation,
-- revocation, and offline-recovery receipts. Source/test implementation only.
-- This migration stores no private material and activates no Production trust.

create table ops.scac_root_trust_key (
  key_digest text primary key check (key_digest ~ '^sha256:[0-9a-f]{64}$'),
  algorithm text not null check (algorithm='ed25519'),
  public_key_bytes bytea not null check (octet_length(public_key_bytes)=32),
  key_purpose text not null check (key_purpose='artifact_manifest_signing'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_trust_active boolean not null default false check (not production_trust_active),
  check (key_digest='sha256:'||encode(public.digest(public_key_bytes,'sha256'),'hex'))
);

create table ops.scac_root_trust_event (
  event_no bigint primary key check (event_no>0),
  event_digest text not null unique check (event_digest ~ '^sha256:[0-9a-f]{64}$'),
  previous_event_digest text unique,
  action text not null check (action in ('establish','rotate','revoke','recovery_drill')),
  subject_key_digest text not null references ops.scac_root_trust_key(key_digest) on delete restrict,
  replacement_key_digest text references ops.scac_root_trust_key(key_digest) on delete restrict,
  threshold integer not null check (threshold between 2 and 8),
  custodian_set_digest text not null check (custodian_set_digest ~ '^sha256:[0-9a-f]{64}$'),
  custodian_approval_digests text[] not null check (
    cardinality(custodian_approval_digests) between threshold and 12),
  recovery_receipt_digest text check (
    recovery_receipt_digest is null or recovery_receipt_digest ~ '^sha256:[0-9a-f]{64}$'),
  policy_epoch bigint not null,
  policy_epoch_digest text not null check (policy_epoch_digest ~ '^sha256:[0-9a-f]{64}$'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_trust_active boolean not null default false check (not production_trust_active),
  foreign key (previous_event_digest) references ops.scac_root_trust_event(event_digest) on delete restrict,
  foreign key (policy_epoch,policy_epoch_digest)
    references ops.scac_policy_epoch(epoch,epoch_digest) on delete restrict,
  check ((event_no=1 and previous_event_digest is null) or
         (event_no>1 and previous_event_digest is not null)),
  check (replacement_key_digest is null or replacement_key_digest<>subject_key_digest),
  check ((action='rotate' and replacement_key_digest is not null and recovery_receipt_digest is null) or
         (action='recovery_drill' and replacement_key_digest is null and recovery_receipt_digest is not null) or
         (action in ('establish','revoke') and replacement_key_digest is null and recovery_receipt_digest is null))
);

create table ops.scac_root_custodian_set_member (
  custodian_set_digest text not null check (custodian_set_digest ~ '^sha256:[0-9a-f]{64}$'),
  custodian_key_digest text not null check (custodian_key_digest ~ '^sha256:[0-9a-f]{64}$'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_trust_active boolean not null default false check (not production_trust_active),
  primary key (custodian_set_digest,custodian_key_digest)
);

create table ops.scac_root_custodian_attestation (
  event_digest text not null references ops.scac_root_trust_event(event_digest) on delete restrict,
  custodian_key_digest text not null check (custodian_key_digest ~ '^sha256:[0-9a-f]{64}$'),
  algorithm text not null check (algorithm='ed25519'),
  public_key_bytes bytea not null check (octet_length(public_key_bytes)=32),
  signature_bytes bytea not null check (octet_length(signature_bytes)=64),
  signature_digest text not null check (signature_digest ~ '^sha256:[0-9a-f]{64}$'),
  signed_payload_digest text not null check (signed_payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  verifier_contract text not null check (verifier_contract='mcp-server/src/root-trust.js'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_trust_active boolean not null default false check (not production_trust_active),
  primary key (event_digest,custodian_key_digest),
  unique (event_digest,signature_digest),
  check (custodian_key_digest='sha256:'||encode(public.digest(public_key_bytes,'sha256'),'hex')),
  check (signature_digest='sha256:'||encode(public.digest(signature_bytes,'sha256'),'hex'))
);

comment on table ops.scac_root_trust_key is
  'SIEP-14 public Ed25519 root descriptors only. Private or offline signing material is forbidden.';
comment on table ops.scac_root_trust_event is
  'SIEP-14 immutable quorum ceremony chain. Source-only facts never activate Production trust.';
comment on table ops.scac_root_custodian_set_member is
  'SIEP-14 immutable reviewed custodian-set membership; event attestations may be a threshold subset.';
comment on table ops.scac_root_custodian_attestation is
  'SIEP-14 public-key-only custodian signatures, externally verified before recording; no custodian identity or secret material.';

create or replace function ops.scac_root_trust_event_statement_digest(
  p_event_no bigint,p_previous_event_digest text,p_action text,p_subject_key_digest text,
  p_replacement_key_digest text,p_threshold integer,p_custodian_set_digest text,p_recovery_receipt_digest text,
  p_policy_epoch bigint,p_policy_epoch_digest text
) returns text language sql immutable set search_path=pg_catalog,public,ops as $fn$
  select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-root-trust-event.v1','event_no',p_event_no,
    'previous_event_digest',p_previous_event_digest,'action',p_action,
    'subject_key_digest',p_subject_key_digest,'replacement_key_digest',p_replacement_key_digest,
    'threshold',p_threshold,'custodian_set_digest',p_custodian_set_digest,
    'recovery_receipt_digest',p_recovery_receipt_digest,
    'policy_epoch',p_policy_epoch,'policy_epoch_digest',p_policy_epoch_digest)),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_root_trust_event_digest(
  p_event_no bigint,p_previous_event_digest text,p_action text,p_subject_key_digest text,
  p_replacement_key_digest text,p_threshold integer,p_custodian_set_digest text,p_custodian_approval_digests text[],
  p_recovery_receipt_digest text,p_policy_epoch bigint,p_policy_epoch_digest text
) returns text language sql immutable set search_path=pg_catalog,public,ops as $fn$
  select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-root-trust-event.v1','event_no',p_event_no,
    'previous_event_digest',p_previous_event_digest,'action',p_action,
    'subject_key_digest',p_subject_key_digest,'replacement_key_digest',p_replacement_key_digest,
    'threshold',p_threshold,'custodian_set_digest',p_custodian_set_digest,
    'recovery_receipt_digest',p_recovery_receipt_digest,
    'policy_epoch',p_policy_epoch,'policy_epoch_digest',p_policy_epoch_digest,
    'custodian_approval_digests',to_jsonb(p_custodian_approval_digests))),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_root_custodian_set_digest(p_custodian_key_digests text[])
returns text language sql immutable strict set search_path=pg_catalog,public,ops as $fn$
  select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-root-custodian-set.v1',
    'custodian_key_digests',to_jsonb(p_custodian_key_digests))),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_root_trust_chain_state()
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$
declare r ops.scac_root_trust_event%rowtype; expected bigint:=1; prior text:=null; active_key text:=null;
        expected_digest text; sorted_digests text[]; recorded_digests text[]; recorded_keys text[];
        reviewed_keys text[]; expected_custodian_set text:=null; reviewed_custodian_set text;
        expected_threshold integer:=null;
begin
  for r in select * from ops.scac_root_trust_event order by event_no loop
    select array_agg(v order by v) into sorted_digests from unnest(r.custodian_approval_digests) v;
    select array_agg(signature_digest order by signature_digest) into recorded_digests
      from ops.scac_root_custodian_attestation where event_digest=r.event_digest;
    select array_agg(custodian_key_digest order by custodian_key_digest) into recorded_keys
      from ops.scac_root_custodian_attestation where event_digest=r.event_digest;
    select array_agg(custodian_key_digest order by custodian_key_digest) into reviewed_keys
      from ops.scac_root_custodian_set_member where custodian_set_digest=r.custodian_set_digest;
    reviewed_custodian_set:=case when reviewed_keys is null then null else ops.scac_root_custodian_set_digest(reviewed_keys) end;
    expected_digest:=ops.scac_root_trust_event_digest(r.event_no,r.previous_event_digest,r.action,
      r.subject_key_digest,r.replacement_key_digest,r.threshold,r.custodian_set_digest,sorted_digests,
      r.recovery_receipt_digest,r.policy_epoch,r.policy_epoch_digest);
    if r.event_no<>expected or r.previous_event_digest is distinct from prior or
       r.event_digest is distinct from expected_digest or sorted_digests is distinct from r.custodian_approval_digests or
       cardinality(sorted_digests)<>(select count(distinct v) from unnest(sorted_digests) v) or
       cardinality(sorted_digests)<r.threshold or recorded_digests is distinct from sorted_digests or
       cardinality(recorded_keys)<r.threshold or reviewed_custodian_set is distinct from r.custodian_set_digest or
       exists(select 1 from unnest(recorded_keys) k where not (k=any(reviewed_keys))) or
       (expected_custodian_set is not null and r.custodian_set_digest is distinct from expected_custodian_set) or
       (expected_threshold is not null and r.threshold is distinct from expected_threshold) or
       r.production_trust_active then
      return jsonb_build_object('valid',false,'structurally_valid',false,'reason','root_chain_gap_fork_digest_or_quorum',
        'cryptographic_quorum_state','external_verification_required');
    end if;
    expected_custodian_set:=r.custodian_set_digest;
    expected_threshold:=r.threshold;
    if r.action='establish' then
      if expected<>1 or active_key is not null then return jsonb_build_object('valid',false,'structurally_valid',false,'reason','root_transition_invalid'); end if;
      active_key:=r.subject_key_digest;
    elsif r.action='rotate' then
      if active_key is distinct from r.subject_key_digest then return jsonb_build_object('valid',false,'structurally_valid',false,'reason','root_transition_invalid'); end if;
      active_key:=r.replacement_key_digest;
    elsif r.action='revoke' then
      if active_key is distinct from r.subject_key_digest then return jsonb_build_object('valid',false,'structurally_valid',false,'reason','root_transition_invalid'); end if;
      active_key:=null;
    elsif active_key is distinct from r.subject_key_digest then
      return jsonb_build_object('valid',false,'structurally_valid',false,'reason','root_transition_invalid');
    end if;
    prior:=r.event_digest; expected:=expected+1;
  end loop;
  if expected=1 then return jsonb_build_object('valid',false,'structurally_valid',false,'reason','root_chain_unavailable'); end if;
  return jsonb_build_object('valid',false,'structurally_valid',true,
    'reason','structurally_valid_external_crypto_required','cryptographic_quorum_state','external_verification_required',
    'active_key_digest',active_key,
    'latest_event_no',expected-1,'latest_event_digest',prior,
    'root_trust_operational',false,'production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_root_trust_event_insert_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare tip ops.scac_root_trust_event%rowtype; state jsonb; sorted_digests text[]; expected_digest text;
        reviewed_keys text[]; reviewed_custodian_set text;
begin
  lock table ops.scac_root_trust_event in share row exclusive mode;
  select * into tip from ops.scac_root_trust_event order by event_no desc limit 1;
  if (tip.event_no is null and (new.event_no<>1 or new.previous_event_digest is not null)) or
     (tip.event_no is not null and (new.event_no<>tip.event_no+1 or new.previous_event_digest is distinct from tip.event_digest)) then
    raise exception 'SIEP-14 root ceremony gap or fork';
  end if;
  select array_agg(v order by v) into sorted_digests from unnest(new.custodian_approval_digests) v;
  if sorted_digests is distinct from new.custodian_approval_digests or
     cardinality(sorted_digests)<>(select count(distinct v) from unnest(sorted_digests) v) then
    raise exception 'SIEP-14 custodian approvals must be distinct and sorted';
  end if;
  select array_agg(custodian_key_digest order by custodian_key_digest) into reviewed_keys
    from ops.scac_root_custodian_set_member where custodian_set_digest=new.custodian_set_digest;
  reviewed_custodian_set:=case when reviewed_keys is null then null else ops.scac_root_custodian_set_digest(reviewed_keys) end;
  if reviewed_custodian_set is distinct from new.custodian_set_digest or cardinality(reviewed_keys)<new.threshold then
    raise exception 'SIEP-14 reviewed custodian set is unavailable, drifted, or below threshold';
  end if;
  expected_digest:=ops.scac_root_trust_event_digest(new.event_no,new.previous_event_digest,new.action,
    new.subject_key_digest,new.replacement_key_digest,new.threshold,new.custodian_set_digest,sorted_digests,
    new.recovery_receipt_digest,new.policy_epoch,new.policy_epoch_digest);
  if new.event_digest is distinct from expected_digest then raise exception 'SIEP-14 root ceremony digest mismatch'; end if;
  if not exists(select 1 from ops.scac_policy_epoch where epoch=new.policy_epoch and epoch_digest=new.policy_epoch_digest)
     then raise exception 'SIEP-14 policy epoch unavailable'; end if;
  state:=ops.scac_root_trust_chain_state();
  if tip.event_no is not null and coalesce((state->>'structurally_valid')::boolean,false) is not true
     then raise exception 'SIEP-14 prior root chain invalid'; end if;
  if new.action='establish' and tip.event_no is not null then raise exception 'SIEP-14 duplicate root establishment'; end if;
  if tip.event_no is not null and new.custodian_set_digest is distinct from tip.custodian_set_digest
     then raise exception 'SIEP-14 custodian set is not the reviewed chain set'; end if;
  if tip.event_no is not null and new.threshold is distinct from tip.threshold
     then raise exception 'SIEP-14 custodian threshold is not the established chain threshold'; end if;
  if new.action<>'establish' and (state->>'active_key_digest') is distinct from new.subject_key_digest
     then raise exception 'SIEP-14 ceremony subject is not the current root'; end if;
  return new;
end $fn$;

create or replace function ops.scac_root_attestation_insert_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare e ops.scac_root_trust_event%rowtype; expected_payload text;
begin
  select * into e from ops.scac_root_trust_event where event_digest=new.event_digest;
  expected_payload:=ops.scac_root_trust_event_statement_digest(e.event_no,e.previous_event_digest,e.action,
    e.subject_key_digest,e.replacement_key_digest,e.threshold,e.custodian_set_digest,
    e.recovery_receipt_digest,e.policy_epoch,e.policy_epoch_digest);
  if e.event_digest is null or new.signed_payload_digest is distinct from expected_payload or
     not (new.signature_digest=any(e.custodian_approval_digests)) or
     not exists(select 1 from ops.scac_root_custodian_set_member m
                where m.custodian_set_digest=e.custodian_set_digest
                  and m.custodian_key_digest=new.custodian_key_digest) then
    raise exception 'SIEP-14 custodian attestation does not bind the ceremony';
  end if;
  return new;
end $fn$;

create or replace function ops.scac_root_trust_append_only_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin raise exception 'SIEP-14 root trust facts are append-only'; end $fn$;
create or replace function ops.scac_root_trust_truncate_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin raise exception 'SIEP-14 root trust facts cannot be truncated'; end $fn$;

create trigger scac_root_event_insert_exact before insert on ops.scac_root_trust_event
for each row execute function ops.scac_root_trust_event_insert_guard();
create trigger scac_root_attestation_insert_exact before insert on ops.scac_root_custodian_attestation
for each row execute function ops.scac_root_attestation_insert_guard();
create trigger scac_root_trust_key_append_only before update or delete on ops.scac_root_trust_key
for each row execute function ops.scac_root_trust_append_only_guard();
create trigger scac_root_trust_event_append_only before update or delete on ops.scac_root_trust_event
for each row execute function ops.scac_root_trust_append_only_guard();
create trigger scac_root_custodian_set_member_append_only before update or delete on ops.scac_root_custodian_set_member
for each row execute function ops.scac_root_trust_append_only_guard();
create trigger scac_root_custodian_attestation_append_only before update or delete on ops.scac_root_custodian_attestation
for each row execute function ops.scac_root_trust_append_only_guard();
create trigger scac_root_trust_key_no_truncate before truncate on ops.scac_root_trust_key
for each statement execute function ops.scac_root_trust_truncate_guard();
create trigger scac_root_trust_event_no_truncate before truncate on ops.scac_root_trust_event
for each statement execute function ops.scac_root_trust_truncate_guard();
create trigger scac_root_custodian_set_member_no_truncate before truncate on ops.scac_root_custodian_set_member
for each statement execute function ops.scac_root_trust_truncate_guard();
create trigger scac_root_custodian_attestation_no_truncate before truncate on ops.scac_root_custodian_attestation
for each statement execute function ops.scac_root_trust_truncate_guard();

create or replace function ops.scac_artifact_root_binding_state(p_artifact_digest text)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$
declare root_state jsonb; signer_count integer; active_key text;
begin
  root_state:=ops.scac_root_trust_chain_state(); active_key:=root_state->>'active_key_digest';
  select count(*) into signer_count from ops.scac_artifact_manifest m join ops.scac_artifact_signature s
    on s.manifest_digest=m.manifest_digest where m.artifact_digest=p_artifact_digest and s.signer_key_digest=active_key;
  return jsonb_build_object('artifact_digest',p_artifact_digest,'root_binding_state',
    case when coalesce((root_state->>'valid')::boolean,false) and active_key is not null and signer_count>0
      then 'current_key_recorded_requires_typed_ed25519_verification' else 'untrusted_or_revoked_root' end,
    'artifact_trust_state','untrusted_for_production','reason_id',
    case when signer_count>0 then 'scac.refusal.production_trust_inactive' else 'scac.refusal.root_untrusted' end,
    'root_trust_operational',false,'production_enforcement_active',false);
end $fn$;

revoke all on ops.scac_root_trust_key,ops.scac_root_trust_event,
  ops.scac_root_custodian_set_member,ops.scac_root_custodian_attestation
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.scac_root_trust_event_statement_digest(bigint,text,text,text,text,integer,text,text,bigint,text),
  ops.scac_root_trust_event_digest(bigint,text,text,text,text,integer,text,text[],text,bigint,text),
  ops.scac_root_custodian_set_digest(text[]),
  ops.scac_root_trust_chain_state(),ops.scac_root_trust_event_insert_guard(),
  ops.scac_root_attestation_insert_guard(),ops.scac_root_trust_append_only_guard(),
  ops.scac_root_trust_truncate_guard(),ops.scac_artifact_root_binding_state(text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

do $assert$
begin
  if exists(select 1 from information_schema.role_table_grants where table_schema='ops'
    and table_name in ('scac_root_trust_key','scac_root_trust_event','scac_root_custodian_set_member','scac_root_custodian_attestation')
    and grantee in ('PUBLIC','carr_reader','carr_writer','carr_jobs','carr_authority')) then
    raise exception 'SIEP-14 runtime roles unexpectedly received raw root trust authority';
  end if;
end $assert$;
