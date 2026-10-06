-- 0456 / SIEP-13 / SCAC-03: immutable artifact manifests, Ed25519 signatures, and
-- an append-only transparency chain. Source/test implementation only.
-- Root trust, signer authorization, recovery custody, and Production use are
-- deliberately absent until SIEP-14 and the later approval-gated rollout.

create table ops.scac_artifact_manifest (
  artifact_digest text primary key check (artifact_digest ~ '^sha256:[0-9a-f]{64}$'),
  manifest_digest text not null unique check (manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  artifact_kind text not null check (artifact_kind in
    ('source_bundle','container_image','vm_image','installer','binary','policy_bundle','model_bundle')),
  media_type text not null check (media_type ~ '^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]{0,126}$'),
  byte_length bigint not null check (byte_length > 0),
  source_ref text not null check (btrim(source_ref) <> '' and char_length(source_ref) <= 500),
  source_digest text not null check (source_digest ~ '^sha256:[0-9a-f]{64}$'),
  sbom_digest text check (sbom_digest is null or sbom_digest ~ '^sha256:[0-9a-f]{64}$'),
  provenance_digest text not null check (provenance_digest ~ '^sha256:[0-9a-f]{64}$'),
  policy_epoch bigint not null,
  policy_epoch_digest text not null check (policy_epoch_digest ~ '^sha256:[0-9a-f]{64}$'),
  created_at timestamptz not null default clock_timestamp(),
  root_trust_operational boolean not null default false check (not root_trust_operational),
  production_enforcement_active boolean not null default false check (not production_enforcement_active),
  foreign key (policy_epoch,policy_epoch_digest)
    references ops.scac_policy_epoch(epoch,epoch_digest) on delete restrict
);

create table ops.scac_artifact_signature (
  signature_digest text primary key check (signature_digest ~ '^sha256:[0-9a-f]{64}$'),
  manifest_digest text not null references ops.scac_artifact_manifest(manifest_digest) on delete restrict,
  algorithm text not null check (algorithm = 'ed25519'),
  signer_key_digest text not null check (signer_key_digest ~ '^sha256:[0-9a-f]{64}$'),
  public_key_bytes bytea not null check (octet_length(public_key_bytes) = 32),
  signature_bytes bytea not null check (octet_length(signature_bytes) = 64),
  signed_payload_digest text not null check (signed_payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  signature_scope text not null check (signature_scope = 'scac-artifact-manifest.v1'),
  recorded_at timestamptz not null default clock_timestamp(),
  root_trust_operational boolean not null default false check (not root_trust_operational),
  production_enforcement_active boolean not null default false check (not production_enforcement_active),
  unique (manifest_digest,signer_key_digest),
  check (signature_digest = 'sha256:' || encode(public.digest(signature_bytes,'sha256'),'hex')),
  check (signer_key_digest = 'sha256:' || encode(public.digest(public_key_bytes,'sha256'),'hex')),
  check (signed_payload_digest = manifest_digest)
);

create table ops.scac_artifact_transparency_entry (
  entry_no bigint primary key check (entry_no > 0),
  entry_digest text not null unique check (entry_digest ~ '^sha256:[0-9a-f]{64}$'),
  previous_entry_digest text unique,
  manifest_digest text not null references ops.scac_artifact_manifest(manifest_digest) on delete restrict,
  signature_digest text not null references ops.scac_artifact_signature(signature_digest) on delete restrict,
  statement_digest text not null check (statement_digest ~ '^sha256:[0-9a-f]{64}$'),
  entry_kind text not null check (entry_kind = 'artifact_inclusion'),
  included_at timestamptz not null default clock_timestamp(),
  root_trust_operational boolean not null default false check (not root_trust_operational),
  production_enforcement_active boolean not null default false check (not production_enforcement_active),
  foreign key (previous_entry_digest) references ops.scac_artifact_transparency_entry(entry_digest) on delete restrict,
  check ((entry_no = 1 and previous_entry_digest is null)
      or (entry_no > 1 and previous_entry_digest is not null))
);

comment on table ops.scac_artifact_manifest is
  'SIEP-13 immutable artifact facts. A digest or manifest row is not trust, authorization, or deploy approval.';
comment on table ops.scac_artifact_signature is
  'SIEP-13 raw Ed25519 signature material. Cryptographic validity is verified by the typed verifier; signer/root trust belongs to SIEP-14.';
comment on table ops.scac_artifact_transparency_entry is
  'SIEP-13 single append-only inclusion chain. Inclusion proves ordering only, never root trust or Production authorization.';

create or replace function ops.scac_artifact_manifest_digest(
  p_artifact_digest text,p_artifact_kind text,p_media_type text,p_byte_length bigint,
  p_source_ref text,p_source_digest text,p_sbom_digest text,p_provenance_digest text,
  p_policy_epoch bigint,p_policy_epoch_digest text
) returns text language sql immutable strict set search_path=pg_catalog,public,ops as $fn$
  select 'sha256:' || encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-artifact-manifest.v1','artifact_digest',p_artifact_digest,
    'artifact_kind',p_artifact_kind,'media_type',p_media_type,'byte_length',p_byte_length,
    'source_ref',p_source_ref,'source_digest',p_source_digest,'sbom_digest',p_sbom_digest,
    'provenance_digest',p_provenance_digest,'policy_epoch',p_policy_epoch,
    'policy_epoch_digest',p_policy_epoch_digest)),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_artifact_manifest_insert_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare expected_digest text;
begin
  expected_digest := ops.scac_artifact_manifest_digest(
    new.artifact_digest,new.artifact_kind,new.media_type,new.byte_length,new.source_ref,
    new.source_digest,coalesce(new.sbom_digest,'sha256:' || repeat('0',64)),new.provenance_digest,
    new.policy_epoch,new.policy_epoch_digest);
  if new.manifest_digest is distinct from expected_digest then
    raise exception 'SIEP-13 artifact manifest digest mismatch';
  end if;
  if not exists(select 1 from ops.scac_policy_epoch e where e.epoch=new.policy_epoch
                  and e.epoch_digest=new.policy_epoch_digest) then
    raise exception 'SIEP-13 artifact manifest policy epoch is unavailable';
  end if;
  return new;
end $fn$;

create or replace function ops.scac_artifact_signature_insert_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin
  if not exists(select 1 from ops.scac_artifact_manifest m
                 where m.manifest_digest=new.manifest_digest) then
    raise exception 'SIEP-13 signature manifest is unavailable';
  end if;
  return new;
end $fn$;

create or replace function ops.scac_artifact_transparency_entry_digest(
  p_entry_no bigint,p_previous_entry_digest text,p_manifest_digest text,
  p_signature_digest text,p_statement_digest text
) returns text language sql immutable set search_path=pg_catalog,public,ops as $fn$
  select 'sha256:' || encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-artifact-transparency.v1','entry_no',p_entry_no,
    'previous_entry_digest',p_previous_entry_digest,'manifest_digest',p_manifest_digest,
    'signature_digest',p_signature_digest,'statement_digest',p_statement_digest,
    'entry_kind','artifact_inclusion')),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_artifact_transparency_insert_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare tip ops.scac_artifact_transparency_entry%rowtype; signature_manifest text; expected_digest text;
begin
  lock table ops.scac_artifact_transparency_entry in share row exclusive mode;
  select * into tip from ops.scac_artifact_transparency_entry order by entry_no desc limit 1;
  if (tip.entry_no is null and (new.entry_no<>1 or new.previous_entry_digest is not null))
     or (tip.entry_no is not null and
         (new.entry_no<>tip.entry_no+1 or new.previous_entry_digest is distinct from tip.entry_digest)) then
    raise exception 'SIEP-13 transparency chain gap or fork';
  end if;
  select manifest_digest into signature_manifest from ops.scac_artifact_signature
   where signature_digest=new.signature_digest;
  if signature_manifest is null or signature_manifest is distinct from new.manifest_digest then
    raise exception 'SIEP-13 transparency signature does not bind the manifest';
  end if;
  expected_digest := ops.scac_artifact_transparency_entry_digest(new.entry_no,
    new.previous_entry_digest,new.manifest_digest,new.signature_digest,new.statement_digest);
  if new.entry_digest is distinct from expected_digest then
    raise exception 'SIEP-13 transparency entry digest mismatch';
  end if;
  return new;
end $fn$;

create or replace function ops.scac_artifact_append_only_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin
  raise exception 'SIEP-13 artifact registry, signatures, and transparency chain are append-only';
end $fn$;

create or replace function ops.scac_artifact_truncate_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin
  raise exception 'SIEP-13 artifact registry, signatures, and transparency chain cannot be truncated';
end $fn$;

create trigger scac_artifact_manifest_insert_exact before insert on ops.scac_artifact_manifest
for each row execute function ops.scac_artifact_manifest_insert_guard();
create trigger scac_artifact_signature_insert_exact before insert on ops.scac_artifact_signature
for each row execute function ops.scac_artifact_signature_insert_guard();
create trigger scac_artifact_transparency_insert_exact before insert on ops.scac_artifact_transparency_entry
for each row execute function ops.scac_artifact_transparency_insert_guard();
create trigger scac_artifact_manifest_append_only before update or delete on ops.scac_artifact_manifest
for each row execute function ops.scac_artifact_append_only_guard();
create trigger scac_artifact_signature_append_only before update or delete on ops.scac_artifact_signature
for each row execute function ops.scac_artifact_append_only_guard();
create trigger scac_artifact_transparency_append_only before update or delete on ops.scac_artifact_transparency_entry
for each row execute function ops.scac_artifact_append_only_guard();
create trigger scac_artifact_manifest_no_truncate before truncate on ops.scac_artifact_manifest
for each statement execute function ops.scac_artifact_truncate_guard();
create trigger scac_artifact_signature_no_truncate before truncate on ops.scac_artifact_signature
for each statement execute function ops.scac_artifact_truncate_guard();
create trigger scac_artifact_transparency_no_truncate before truncate on ops.scac_artifact_transparency_entry
for each statement execute function ops.scac_artifact_truncate_guard();

create or replace function ops.scac_artifact_integrity_state(p_artifact_digest text)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$
declare m ops.scac_artifact_manifest%rowtype; signatures integer; inclusions integer;
begin
  if p_artifact_digest is null or p_artifact_digest !~ '^sha256:[0-9a-f]{64}$' then
    return jsonb_build_object('artifact_digest',p_artifact_digest,'artifact_trust_state','untrusted',
      'signature_state','unavailable','transparency_state','unavailable','reason_id','scac.refusal.untrusted_artifact',
      'root_trust_operational',false,'production_enforcement_active',false);
  end if;
  select * into m from ops.scac_artifact_manifest where artifact_digest=p_artifact_digest;
  if m.artifact_digest is null then
    return jsonb_build_object('artifact_digest',p_artifact_digest,'artifact_trust_state','untrusted',
      'signature_state','unavailable','transparency_state','unavailable','reason_id','scac.refusal.untrusted_artifact',
      'root_trust_operational',false,'production_enforcement_active',false);
  end if;
  select count(*) into signatures from ops.scac_artifact_signature where manifest_digest=m.manifest_digest;
  select count(*) into inclusions from ops.scac_artifact_transparency_entry where manifest_digest=m.manifest_digest;
  return jsonb_build_object('artifact_digest',m.artifact_digest,'manifest_digest',m.manifest_digest,
    'artifact_trust_state','untrusted_pending_siep14','signature_state',
      case when signatures>0 then 'recorded_requires_typed_ed25519_verification' else 'unavailable' end,
    'transparency_state',case when inclusions>0 then 'included_append_only' else 'unavailable' end,
    'reason_id',case when signatures>0 and inclusions>0 then 'scac.refusal.root_untrusted'
                     else 'scac.refusal.untrusted_artifact' end,
    'root_trust_operational',false,'production_enforcement_active',false);
end $fn$;

revoke all on ops.scac_artifact_manifest,ops.scac_artifact_signature,
  ops.scac_artifact_transparency_entry from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.scac_artifact_manifest_digest(text,text,text,bigint,text,text,text,text,bigint,text),
  ops.scac_artifact_manifest_insert_guard(),ops.scac_artifact_signature_insert_guard(),
  ops.scac_artifact_transparency_entry_digest(bigint,text,text,text,text),
  ops.scac_artifact_transparency_insert_guard(),ops.scac_artifact_append_only_guard(),
  ops.scac_artifact_truncate_guard(),
  ops.scac_artifact_integrity_state(text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

do $assert$
begin
  if exists(select 1 from information_schema.role_table_grants
             where table_schema='ops' and table_name like 'scac_artifact_%'
               and grantee in ('PUBLIC','carr_reader','carr_writer','carr_jobs','carr_authority')) then
    raise exception 'SIEP-13 runtime roles unexpectedly received artifact table authority';
  end if;
end $assert$;
