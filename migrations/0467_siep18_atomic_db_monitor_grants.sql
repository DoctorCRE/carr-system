-- 0467 / SIEP-18 / SCAC-08: atomic database reference monitor and guarded DML.
--
-- Source/test implementation only.  The monitor binds one closed operation
-- manifest, the externally verified token payload, current epoch/registry,
-- PoP/challenge/revocation state, and the exact database effects in the same
-- transaction as a guarded write.  It defaults to shadow and cannot activate
-- Production.  Joe alone may enter the enforced_source_test mode.
-- Intentionally no BEGIN/COMMIT: tools/migrate.py owns the transaction.

create table ops.scac_token_issuer_binding (
  issuer_key_digest text primary key check (issuer_key_digest~'^sha256:[0-9a-f]{64}$'),
  issuer_root_event_digest text not null
    references ops.scac_root_trust_event(event_digest) on delete restrict,
  certification_receipt_digest text not null unique
    check (certification_receipt_digest~'^sha256:[0-9a-f]{64}$'),
  idempotency_key uuid not null unique,
  binding_state text not null default 'source_test_current'
    check (binding_state='source_test_current'),
  recorded_by text not null check (recorded_by='joe'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  unique (issuer_key_digest,issuer_root_event_digest)
);

create table ops.scac_token_verification_binding (
  token_ref_digest text primary key
    references ops.scac_capability_token_receipt(token_ref_digest) on delete restrict,
  token_payload jsonb not null check (jsonb_typeof(token_payload)='object'),
  token_payload_digest text not null unique check (token_payload_digest~'^sha256:[0-9a-f]{64}$'),
  challenge_digest text not null unique check (challenge_digest~'^sha256:[0-9a-f]{64}$'),
  issuer_key_digest text not null
    references ops.scac_token_issuer_binding(issuer_key_digest) on delete restrict,
  issuer_root_event_digest text not null,
  verification_binding_digest text not null unique
    check (verification_binding_digest~'^sha256:[0-9a-f]{64}$'),
  idempotency_key uuid not null unique,
  verifier_contract text not null check (verifier_contract='mcp-server/src/scac-token.js'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  foreign key (issuer_key_digest,issuer_root_event_digest)
    references ops.scac_token_issuer_binding(issuer_key_digest,issuer_root_event_digest)
    on delete restrict
);

alter table ops.scac_mutation_registry_entry
  add constraint scac_mutation_registry_entry_identity_uq
  unique (registry_version,ingress_key,entry_digest);

create table ops.scac_operation_effect_binding (
  registry_version text not null,
  ingress_key text not null,
  privilege_bundle text not null check (privilege_bundle in
    ('carr_writer','carr_jobs','carr_authority')),
  registry_entry_digest text not null check (registry_entry_digest~'^sha256:[0-9a-f]{64}$'),
  effect_keys text[] not null check (cardinality(effect_keys)>0),
  effect_set_digest text not null check (effect_set_digest~'^sha256:[0-9a-f]{64}$'),
  binding_digest text not null unique check (binding_digest~'^sha256:[0-9a-f]{64}$'),
  idempotency_key uuid not null unique,
  recorded_by text not null check (recorded_by='joe'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  primary key (registry_version,ingress_key,privilege_bundle),
  foreign key (registry_version,ingress_key,registry_entry_digest)
    references ops.scac_mutation_registry_entry(registry_version,ingress_key,entry_digest)
    on delete restrict
);

create table ops.scac_reference_monitor_mode_event (
  event_no bigint primary key check (event_no>0),
  event_digest text not null unique check (event_digest~'^sha256:[0-9a-f]{64}$'),
  previous_event_digest text unique,
  mode text not null check (mode in ('shadow','enforced_source_test')),
  reason_digest text not null check (reason_digest~'^sha256:[0-9a-f]{64}$'),
  grant_digest text not null check (grant_digest~'^sha256:[0-9a-f]{64}$'),
  idempotency_key uuid not null unique,
  recorded_by text not null check (recorded_by='joe'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  foreign key (previous_event_digest)
    references ops.scac_reference_monitor_mode_event(event_digest) on delete restrict,
  check ((event_no=1 and previous_event_digest is null) or
         (event_no>1 and previous_event_digest is not null))
);

create table ops.scac_reference_monitor_receipt (
  admission_id uuid primary key default gen_random_uuid(),
  token_ref_digest text not null
    references ops.scac_token_verification_binding(token_ref_digest) on delete restrict,
  operation_manifest_digest text not null
    check (operation_manifest_digest~'^sha256:[0-9a-f]{64}$'),
  request_payload_digest text not null check (request_payload_digest~'^sha256:[0-9a-f]{64}$'),
  idempotency_digest text not null check (idempotency_digest~'^sha256:[0-9a-f]{64}$'),
  ingress_key text not null check (ingress_key~'^[a-z][a-z0-9_-]+:' and
    ingress_key!~E'[\n\r\t]' and char_length(ingress_key)<=1000),
  effect_keys text[] not null check (cardinality(effect_keys)>0),
  effect_set_digest text not null check (effect_set_digest~'^sha256:[0-9a-f]{64}$'),
  operation_effect_binding_digest text not null
    references ops.scac_operation_effect_binding(binding_digest) on delete restrict,
  policy_epoch bigint not null,
  policy_epoch_digest text not null check (policy_epoch_digest~'^sha256:[0-9a-f]{64}$'),
  registry_version text not null,
  registry_digest text not null check (registry_digest~'^sha256:[0-9a-f]{64}$'),
  principal_digest text not null check (principal_digest~'^sha256:[0-9a-f]{64}$'),
  session_principal text not null check (session_principal in
    ('carr_writer','carr_jobs','carr_authority_joe','carr_authority_dell')),
  privilege_bundle text not null check (privilege_bundle in
    ('carr_writer','carr_jobs','carr_authority')),
  grant_digest text not null check (grant_digest~'^sha256:[0-9a-f]{64}$'),
  backend_pid integer not null check (backend_pid>0),
  transaction_id bigint not null check (transaction_id>0),
  admission_idempotency_key uuid not null unique,
  decision text not null check (decision='admit_source_test_nonproduction'),
  monitor_state text not null check (monitor_state='current'),
  direct_grant_guarded boolean not null check (direct_grant_guarded),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  recorded_at timestamptz not null default clock_timestamp(),
  foreign key (policy_epoch,policy_epoch_digest)
    references ops.scac_policy_epoch(epoch,epoch_digest) on delete restrict,
  foreign key (registry_version,ingress_key)
    references ops.scac_mutation_registry_entry(registry_version,ingress_key) on delete restrict
);

comment on table ops.scac_token_issuer_binding is
  'SIEP-18 public-digest binding from a separately verified online issuer key to the current offline-root ceremony event. The root key itself is forbidden as the online issuer.';
comment on table ops.scac_token_verification_binding is
  'SIEP-18 closed token payload bridge. It records only signed public claims/digests and never signing material or raw request payloads.';
comment on table ops.scac_operation_effect_binding is
  'SIEP-18 Joe-reviewed immutable exact operation-to-database-effect binding, separately keyed for each least-privilege runtime bundle.';
comment on table ops.scac_reference_monitor_mode_event is
  'SIEP-18 append-only Joe control. Only shadow and enforced_source_test exist; Production activation is structurally impossible here.';
comment on table ops.scac_reference_monitor_receipt is
  'SIEP-18 same-transaction admission evidence. A guarded DML trigger accepts it only on the originating backend and transaction.';

create or replace function ops.scac_json_has_exact_keys(p_value jsonb,p_keys text[])
returns boolean language sql immutable set search_path=pg_catalog as $fn$
  select jsonb_typeof(p_value)='object' and
    coalesce((select array_agg(k order by k collate "C") from jsonb_object_keys(p_value) k),'{}'::text[])
      =coalesce((select array_agg(k order by k collate "C") from unnest(p_keys) k),'{}'::text[])
$fn$;

create or replace function ops.scac_reference_monitor_sha256(p_value jsonb)
returns text language sql immutable strict set search_path=pg_catalog,public,ops as $fn$
  select 'sha256:'||encode(public.digest(
    convert_to(ops.scac_canonical_json(p_value),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_runtime_privilege_bundle()
returns text language sql stable security definer set search_path=pg_catalog as $fn$
  select case session_user
    when 'carr_writer' then 'carr_writer'
    when 'carr_jobs' then 'carr_jobs'
    when 'carr_authority_joe' then 'carr_authority'
    when 'carr_authority_dell' then 'carr_authority'
    else null end
$fn$;

create or replace function ops.scac_runtime_dml_grant_snapshot()
returns jsonb language sql stable security definer set search_path=pg_catalog,public,ops as $fn$
  with recursive connected(oid) as (
    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci'
    union
    select other.oid from connected c join pg_auth_members m
      on m.roleid=c.oid or m.member=c.oid join pg_roles other
      on other.oid=case when m.roleid=c.oid then m.member else m.roleid end
      where other.rolname<>'carr_ci' and not other.rolsuper
  ), runtime_roles as (
    select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected)
      and not r.rolsuper
  ), relation_rows as (
    select 'db-relation-acl:'||n.nspname||'.'||c.relname||':'||
      coalesce(r.rolname,'public')||':'||lower(a.privilege_type) ingress_key,
      jsonb_build_object('ingress_key','db-relation-acl:'||n.nspname||'.'||c.relname||':'||
        coalesce(r.rolname,'public')||':'||lower(a.privilege_type),
        'relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,
        'grantee',coalesce(r.rolname,'public'),'privilege',lower(a.privilege_type),
        'grantable',a.is_grantable) row
    from pg_class c join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
    left join pg_roles r on r.oid=a.grantee
    where n.nspname not in ('pg_catalog','information_schema')
      and c.relkind in ('r','p','v','m','f')
      and a.grantee <> c.relowner
      and a.privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
      and (a.grantee=0 or r.oid in(select oid from runtime_roles))
  ), column_rows as (
    select 'db-column-acl:'||n.nspname||'.'||c.relname||'.'||att.attname||':'||
      coalesce(r.rolname,'public')||':'||lower(a.privilege_type) ingress_key,
      jsonb_build_object('ingress_key','db-column-acl:'||n.nspname||'.'||c.relname||'.'||att.attname||':'||
        coalesce(r.rolname,'public')||':'||lower(a.privilege_type),
        'relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'column',att.attname,
        'grantee',coalesce(r.rolname,'public'),'privilege',lower(a.privilege_type),
        'grantable',a.is_grantable) row
    from pg_attribute att join pg_class c on c.oid=att.attrelid
    join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(att.attacl) a left join pg_roles r on r.oid=a.grantee
    where att.attnum>0 and not att.attisdropped and att.attacl is not null
      and n.nspname not in ('pg_catalog','information_schema')
      and c.relkind in ('r','p','v','m','f')
      and a.grantee <> c.relowner
      and a.privilege_type in ('INSERT','UPDATE')
      and (a.grantee=0 or r.oid in(select oid from runtime_roles))
  ), rows as (select * from relation_rows union all select * from column_rows)
  select jsonb_build_object('schema_version','scac-runtime-dml-grants.v1',
    'entry_count',count(*),'grant_digest',ops.scac_reference_monitor_sha256(
      coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb))) from rows
$fn$;

create or replace function ops.scac_reference_monitor_mode()
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$
declare r ops.scac_reference_monitor_mode_event%rowtype; expected bigint:=1;
        prior text:=null; expected_digest text; latest_mode text:='shadow'; latest_grant text:=null;
begin
  for r in select * from ops.scac_reference_monitor_mode_event order by event_no loop
    expected_digest:=ops.scac_reference_monitor_sha256(jsonb_build_object(
      'schema_version','scac-reference-monitor-mode-event.v1','event_no',r.event_no,
      'previous_event_digest',r.previous_event_digest,'mode',r.mode,
      'reason_digest',r.reason_digest,'grant_digest',r.grant_digest,
      'idempotency_key',r.idempotency_key::text,'recorded_by','joe'));
    if r.event_no<>expected or r.previous_event_digest is distinct from prior or
       r.event_digest is distinct from expected_digest or r.production_enforcement_active then
      return jsonb_build_object('mode','shadow','integrity_state','invalid_fail_closed',
        'latest_event_digest',prior,'production_enforcement_active',false);
    end if;
    prior:=r.event_digest; latest_mode:=r.mode; latest_grant:=r.grant_digest;
    expected:=expected+1;
  end loop;
  return jsonb_build_object('mode',latest_mode,
    'integrity_state',case when expected=1 then 'uninitialized_shadow' else 'valid_append_only_chain' end,
    'latest_event_digest',prior,'bound_grant_digest',latest_grant,
    'production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_reference_monitor_state()
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$
declare grant_snapshot jsonb; mode_state jsonb; epoch_chain jsonb;
        latest ops.scac_policy_epoch%rowtype;
        registry ops.scac_mutation_registry_version%rowtype; missing_guards integer;
        unsupported_writable integer;
        relation_digest text; column_digest text; grant_state text; guard_state text;
begin
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  mode_state:=ops.scac_reference_monitor_mode();
  epoch_chain:=ops.scac_policy_epoch_chain_state();
  select * into latest from ops.scac_policy_epoch order by epoch desc limit 1;
  select * into registry from ops.scac_mutation_registry_version
    where registry_version=latest.registry_version;
  relation_digest:=registry.catalog_projection#>>'{relation_dml,digest}';
  column_digest:=registry.catalog_projection#>>'{column_dml,digest}';
  grant_state:=case when registry.registry_version is not null and
    (grant_snapshot->>'grant_digest')=ops.scac_reference_monitor_sha256(jsonb_build_array(
      jsonb_build_object('relation_digest',relation_digest),
      jsonb_build_object('column_digest',column_digest)))
    then 'current' else 'measured_pending_v9_binding' end;
  -- The v9 successor replaces the temporary combined-digest comparison above
  -- with its exact catalog grant digest after 0467 is installed.
  with recursive connected(oid) as (
    select oid from pg_roles where rolname in ('carr_writer','carr_jobs','carr_authority')
  ), writable as (
    select distinct c.oid from pg_class c join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
    where c.relkind in ('r','p') and a.grantee<>c.relowner
      and (a.grantee=0 or a.grantee in(select oid from connected))
      and a.privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
    union
    select distinct c.oid from pg_attribute att join pg_class c on c.oid=att.attrelid
    cross join lateral aclexplode(att.attacl) a
    where c.relkind in ('r','p') and att.attnum>0 and not att.attisdropped
      and a.grantee<>c.relowner
      and (a.grantee=0 or a.grantee in(select oid from connected))
      and a.privilege_type in ('INSERT','UPDATE')
  )
  select count(*) into missing_guards from writable w where not exists(
    select 1 from pg_trigger t where t.tgrelid=w.oid and not t.tgisinternal
      and t.tgfoid='ops.scac_reference_monitor_guard()'::regprocedure
      and (t.tgtype & 1)=1);
  with runtime_roles as (
    select oid from pg_roles where rolname in ('carr_writer','carr_jobs','carr_authority')
  ), unsupported as (
    select distinct c.oid from pg_class c join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
    where n.nspname not in ('pg_catalog','information_schema') and
      c.relkind in ('v','m','f') and a.grantee<>c.relowner and
      (a.grantee=0 or a.grantee in(select oid from runtime_roles))
      and a.privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
    union
    select distinct c.oid from pg_attribute att join pg_class c on c.oid=att.attrelid
    join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(att.attacl) a
    where n.nspname not in ('pg_catalog','information_schema') and
      c.relkind in ('v','m','f') and att.attnum>0 and not att.attisdropped and
      a.grantee<>c.relowner and
      (a.grantee=0 or a.grantee in(select oid from runtime_roles))
      and a.privilege_type in ('INSERT','UPDATE')
  ) select count(*) into unsupported_writable from unsupported;
  guard_state:=case when unsupported_writable>0 then 'unsupported_writable_relation'
    when missing_guards=0 then 'complete' else 'incomplete' end;
  return jsonb_build_object('schema_version','scac-reference-monitor-state.v1',
    'monitor_state',case when grant_state='current' and guard_state='complete'
      and coalesce((epoch_chain->>'valid')::boolean,false)
      and (epoch_chain->>'current_epoch')::bigint=latest.epoch
      and epoch_chain->>'current_epoch_digest'=latest.epoch_digest
      and mode_state->>'integrity_state'<>'invalid_fail_closed' then 'current'
      else 'unavailable' end,
    'grant_state',grant_state,'grant_digest',grant_snapshot->>'grant_digest',
    'guard_state',guard_state,'missing_guard_count',missing_guards,
    'unsupported_writable_relation_count',unsupported_writable,
    'mode',mode_state->>'mode','mode_integrity_state',mode_state->>'integrity_state',
    'policy_epoch_state',case when coalesce((epoch_chain->>'valid')::boolean,false)
      and (epoch_chain->>'current_epoch')::bigint=latest.epoch
      and epoch_chain->>'current_epoch_digest'=latest.epoch_digest
      then 'current' else 'invalid_or_unavailable' end,
    'policy_epoch',latest.epoch,'policy_epoch_digest',latest.epoch_digest,
    'registry_version',latest.registry_version,'registry_digest',latest.registry_digest,
    'direct_database_grant_cutover',false,'production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_register_token_issuer_binding(
  p_issuer_key_digest text,p_issuer_root_event_digest text,
  p_certification_receipt_digest text,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare root_state jsonb; prior ops.scac_token_issuer_binding%rowtype;
begin
  if session_user<>'carr_authority_joe' then
    raise exception 'SIEP-18 issuer binding authority is Joe-only';
  end if;
  if coalesce(p_issuer_key_digest,'')!~'^sha256:[0-9a-f]{64}$' or
     coalesce(p_issuer_root_event_digest,'')!~'^sha256:[0-9a-f]{64}$' or
     coalesce(p_certification_receipt_digest,'')!~'^sha256:[0-9a-f]{64}$' or
     p_idempotency_key is null then raise exception 'SIEP-18 issuer binding input malformed'; end if;
  perform pg_advisory_xact_lock(hashtextextended('carr-siep18-reference-monitor',0));
  select * into prior from ops.scac_token_issuer_binding where idempotency_key=p_idempotency_key;
  if prior.issuer_key_digest is not null then
    if prior.issuer_key_digest<>p_issuer_key_digest or
       prior.issuer_root_event_digest<>p_issuer_root_event_digest or
       prior.certification_receipt_digest<>p_certification_receipt_digest then
      raise exception 'SIEP-18 issuer binding idempotency mismatch'; end if;
    return jsonb_build_object('issuer_key_digest',prior.issuer_key_digest,
      'issuer_root_event_digest',prior.issuer_root_event_digest,
      'binding_state',prior.binding_state,'production_enforcement_active',false);
  end if;
  root_state:=ops.scac_root_trust_chain_state();
  if coalesce((root_state->>'structurally_valid')::boolean,false) is not true or
     root_state->>'latest_event_digest'<>p_issuer_root_event_digest or
     root_state->>'active_key_digest' is null then
    raise exception 'scac.refusal.root_untrusted: SIEP-18 current root event unavailable';
  end if;
  if root_state->>'active_key_digest'=p_issuer_key_digest then
    raise exception 'SIEP-18 offline root key cannot be the online token issuer';
  end if;
  insert into ops.scac_token_issuer_binding
    (issuer_key_digest,issuer_root_event_digest,certification_receipt_digest,
     idempotency_key,recorded_by)
  values (p_issuer_key_digest,p_issuer_root_event_digest,p_certification_receipt_digest,
    p_idempotency_key,'joe');
  return jsonb_build_object('issuer_key_digest',p_issuer_key_digest,
    'issuer_root_event_digest',p_issuer_root_event_digest,
    'binding_state','source_test_current','production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_record_token_verification_binding(
  p_token_ref_digest text,p_token_payload jsonb,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare receipt ops.scac_capability_token_receipt%rowtype; c ops.scac_pop_challenge%rowtype;
        issuer ops.scac_token_issuer_binding%rowtype; prior ops.scac_token_verification_binding%rowtype;
        payload_digest text; binding_digest text; expected_keys constant text[]:=array[
          'challenge_digest','device_key_digest','device_ref','domain','environment','expires_at',
          'facts_digest','idempotency_digest','ingress_key','issued_at','issuer_key_digest',
          'issuer_root_event_digest','mutation_kind','operation_manifest_digest','policy_epoch',
          'policy_epoch_digest','principal_digest','registry_digest','registry_version',
          'request_digest','schema_version','target_surface','tenant_scope','token_id','workload_digest'];
begin
  if session_user<>'carr_jobs' then raise exception 'SIEP-18 token binding recorder role refused'; end if;
  if coalesce(p_token_ref_digest,'')!~'^sha256:[0-9a-f]{64}$' or p_idempotency_key is null or
     not ops.scac_json_has_exact_keys(p_token_payload,expected_keys) then
    raise exception 'SIEP-18 token verification binding malformed or open'; end if;
  payload_digest:=ops.scac_reference_monitor_sha256(p_token_payload);
  perform pg_advisory_xact_lock(hashtextextended('carr-siep18-reference-monitor',0));
  select * into prior from ops.scac_token_verification_binding where idempotency_key=p_idempotency_key;
  if prior.token_ref_digest is not null then
    if prior.token_ref_digest<>p_token_ref_digest or prior.token_payload_digest<>payload_digest then
      raise exception 'SIEP-18 token binding idempotency mismatch'; end if;
    return jsonb_build_object('token_ref_digest',prior.token_ref_digest,
      'verification_binding_digest',prior.verification_binding_digest,
      'binding_state','current_source_test','production_enforcement_active',false);
  end if;
  select * into receipt from ops.scac_capability_token_receipt
    where token_ref_digest=p_token_ref_digest for key share;
  select * into c from ops.scac_pop_challenge where challenge_id=receipt.challenge_id for key share;
  select * into issuer from ops.scac_token_issuer_binding
    where issuer_key_digest=receipt.issuer_key_digest for key share;
  if receipt.token_ref_digest is null or c.challenge_id is null or issuer.issuer_key_digest is null or
     receipt.signed_payload_digest<>payload_digest or
     p_token_payload->>'domain'<>'CARR-SCAC-TOKEN-V1' or
     p_token_payload->>'schema_version'<>'scac-capability-token.v1' or
     p_token_payload->>'tenant_scope'<>'carr-internal' or
     p_token_payload->>'environment'<>'source-test' or
     p_token_payload->>'challenge_digest'<>c.challenge_digest or
     p_token_payload->>'principal_digest'<>c.principal_digest or
     p_token_payload->>'device_ref'<>c.device_ref or
     p_token_payload->>'device_key_digest'<>c.device_key_digest or
     p_token_payload->>'facts_digest'<>c.facts_digest or
     (p_token_payload->>'workload_digest') is distinct from c.workload_digest or
     p_token_payload->>'registry_version'<>c.registry_version or
     p_token_payload->>'registry_digest'<>c.registry_digest or
     p_token_payload->>'ingress_key'<>c.ingress_key or
     p_token_payload->>'mutation_kind'<>c.mutation_kind or
     p_token_payload->>'target_surface'<>c.target_surface or
     (p_token_payload->>'policy_epoch')::bigint<>c.policy_epoch or
     p_token_payload->>'policy_epoch_digest'<>c.policy_epoch_digest or
     p_token_payload->>'operation_manifest_digest'<>c.operation_manifest_digest or
     p_token_payload->>'request_digest'<>c.request_digest or
     p_token_payload->>'idempotency_digest'<>c.idempotency_digest or
     p_token_payload->>'issuer_key_digest'<>receipt.issuer_key_digest or
     p_token_payload->>'issuer_root_event_digest'<>receipt.issuer_root_event_digest or
     (p_token_payload->>'issued_at')::timestamptz is distinct from receipt.issued_at or
     (p_token_payload->>'expires_at')::timestamptz is distinct from receipt.expires_at or
     issuer.issuer_root_event_digest<>receipt.issuer_root_event_digest then
    raise exception 'scac.refusal.token_invalid: SIEP-18 signed token binding mismatch';
  end if;
  binding_digest:=ops.scac_reference_monitor_sha256(jsonb_build_object(
    'schema_version','scac-token-verification-binding.v1','token_ref_digest',p_token_ref_digest,
    'token_payload_digest',payload_digest,'challenge_digest',c.challenge_digest,
    'issuer_key_digest',receipt.issuer_key_digest,
    'issuer_root_event_digest',receipt.issuer_root_event_digest));
  insert into ops.scac_token_verification_binding
    (token_ref_digest,token_payload,token_payload_digest,challenge_digest,
     issuer_key_digest,issuer_root_event_digest,verification_binding_digest,
     idempotency_key,verifier_contract)
  values (p_token_ref_digest,p_token_payload,payload_digest,c.challenge_digest,
    receipt.issuer_key_digest,receipt.issuer_root_event_digest,binding_digest,
    p_idempotency_key,'mcp-server/src/scac-token.js');
  return jsonb_build_object('token_ref_digest',p_token_ref_digest,
    'verification_binding_digest',binding_digest,'binding_state','current_source_test',
    'production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_register_operation_effect_binding(
  p_registry_version text,p_ingress_key text,p_privilege_bundle text,
  p_registry_entry_digest text,p_effect_keys text[],p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare re ops.scac_mutation_registry_entry%rowtype;
        prior ops.scac_operation_effect_binding%rowtype;
        effects text[]; effect_digest text; next_digest text;
begin
  if session_user<>'carr_authority_joe' then
    raise exception 'SIEP-18 operation-effect binding authority is Joe-only'; end if;
  select array_agg(value order by value collate "C") into effects from unnest(p_effect_keys) value;
  if coalesce(p_registry_version,'')!~'^scac-mutation-registry\.v[1-9][0-9]*$' or
     coalesce(p_ingress_key,'')!~'^[a-z][a-z0-9_-]+:' or p_ingress_key~E'[\n\r\t]' or
     char_length(p_ingress_key)>1000 or
     p_privilege_bundle not in ('carr_writer','carr_jobs','carr_authority') or
     coalesce(p_registry_entry_digest,'')!~'^sha256:[0-9a-f]{64}$' or
     p_idempotency_key is null or effects is null or cardinality(effects)=0 or
     cardinality(effects)<>(select count(distinct x) from unnest(effects) x) or
     exists(select 1 from unnest(effects) x where
       x!~'^db-relation-acl:[^:]+:[a-z_]+:(insert|update|delete|truncate)$'
       and x!~'^db-column-acl:[^:]+:[a-z_]+:update$') then
    raise exception 'SIEP-18 operation-effect binding malformed'; end if;
  perform pg_advisory_xact_lock(hashtextextended('carr-siep18-reference-monitor',0));
  select * into prior from ops.scac_operation_effect_binding
    where idempotency_key=p_idempotency_key;
  if prior.binding_digest is not null then
    if prior.registry_version<>p_registry_version or prior.ingress_key<>p_ingress_key or
       prior.privilege_bundle<>p_privilege_bundle or
       prior.registry_entry_digest<>p_registry_entry_digest or
       prior.effect_keys is distinct from effects then
      raise exception 'SIEP-18 operation-effect binding idempotency mismatch'; end if;
    return jsonb_build_object('binding_digest',prior.binding_digest,
      'effect_set_digest',prior.effect_set_digest,'binding_state','current_source_test',
      'production_enforcement_active',false);
  end if;
  select * into re from ops.scac_mutation_registry_entry
    where registry_version=p_registry_version and ingress_key=p_ingress_key for key share;
  if re.ingress_key is null or re.entry_digest<>p_registry_entry_digest or
     re.effect_class='read_only' or
     coalesce((re.contract->>'classification_authorizing')::boolean,true) then
    raise exception 'scac.refusal.mutation_unregistered: SIEP-18 logical operation unavailable';
  end if;
  if exists(select 1 from unnest(effects) x where not exists(
       select 1 from ops.scac_mutation_registry_entry xre
       where xre.registry_version=p_registry_version and xre.ingress_key=x
         and xre.ingress_kind in ('db_relation_acl','db_column_acl')
         and xre.contract->>'grantee'=p_privilege_bundle
         and ((xre.ingress_kind='db_relation_acl' and
               xre.contract->>'privilege' in ('insert','update','delete','truncate')) or
              (xre.ingress_kind='db_column_acl' and
               xre.contract->>'privilege'='update')))) then
    raise exception 'scac.refusal.mutation_unregistered: SIEP-18 effect is not a current exact grant';
  end if;
  effect_digest:=ops.scac_reference_monitor_sha256(to_jsonb(effects));
  next_digest:=ops.scac_reference_monitor_sha256(jsonb_build_object(
    'schema_version','scac-operation-effect-binding.v1',
    'registry_version',p_registry_version,'ingress_key',p_ingress_key,
    'privilege_bundle',p_privilege_bundle,'registry_entry_digest',p_registry_entry_digest,
    'effect_keys',to_jsonb(effects),'effect_set_digest',effect_digest,
    'idempotency_key',p_idempotency_key::text,'recorded_by','joe'));
  insert into ops.scac_operation_effect_binding
    (registry_version,ingress_key,privilege_bundle,registry_entry_digest,effect_keys,
     effect_set_digest,binding_digest,idempotency_key,recorded_by)
  values (p_registry_version,p_ingress_key,p_privilege_bundle,p_registry_entry_digest,
    effects,effect_digest,next_digest,p_idempotency_key,'joe');
  return jsonb_build_object('binding_digest',next_digest,'effect_set_digest',effect_digest,
    'binding_state','current_source_test','production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_admit_mutation(
  p_token_ref_digest text,p_operation_manifest jsonb,p_admission_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare b ops.scac_token_verification_binding%rowtype; t ops.scac_capability_token_receipt%rowtype;
        c ops.scac_pop_challenge%rowtype; e ops.scac_device_enrollment%rowtype;
        pe ops.scac_policy_epoch%rowtype; re ops.scac_mutation_registry_entry%rowtype;
        ob ops.scac_operation_effect_binding%rowtype;
        prior ops.scac_reference_monitor_receipt%rowtype; state jsonb; control jsonb;
        root_state jsonb; bundle text; manifest_digest text; effect_digest text;
        effects text[]; admission uuid; studio_benchmark_count integer;
        expected_keys constant text[]:=array[
          'device_key_digest','device_ref','domain','effect_keys','environment','facts_digest',
          'idempotency_digest','ingress_key','mutation_kind','policy_epoch','policy_epoch_digest',
          'principal_digest','registry_digest','registry_version','request_payload_digest',
          'schema_version','target_surface','tenant_scope','workload_digest'];
begin
  bundle:=ops.scac_runtime_privilege_bundle();
  if bundle is null then raise exception 'scac.refusal.identity_unverified: SIEP-18 caller role refused'; end if;
  if coalesce(p_token_ref_digest,'')!~'^sha256:[0-9a-f]{64}$' or
     p_admission_idempotency_key is null or
     not ops.scac_json_has_exact_keys(p_operation_manifest,expected_keys) or
     p_operation_manifest->>'domain'<>'CARR-SCAC-OPERATION-V1' or
     p_operation_manifest->>'schema_version'<>'scac-operation-manifest.v1' or
     p_operation_manifest->>'tenant_scope'<>'carr-internal' or
     p_operation_manifest->>'environment'<>'source-test' or
     jsonb_typeof(p_operation_manifest->'effect_keys')<>'array' then
    raise exception 'scac.refusal.request_shape_invalid: SIEP-18 manifest malformed or open';
  end if;
  select array_agg(value order by value collate "C") into effects
    from jsonb_array_elements_text(p_operation_manifest->'effect_keys');
  if effects is null or cardinality(effects)=0 or
     cardinality(effects)<>(select count(distinct x) from unnest(effects) x) or
     exists(select 1 from unnest(effects) x where
       x!~'^db-relation-acl:[^:]+:[a-z_]+:(insert|update|delete|truncate)$'
       and x!~'^db-column-acl:[^:]+:[a-z_]+:update$') then
    raise exception 'scac.refusal.request_shape_invalid: SIEP-18 effect set malformed';
  end if;
  manifest_digest:=ops.scac_reference_monitor_sha256(p_operation_manifest);
  effect_digest:=ops.scac_reference_monitor_sha256(to_jsonb(effects));
  perform pg_advisory_xact_lock(hashtextextended('carr-siep18-reference-monitor',0));
  select * into prior from ops.scac_reference_monitor_receipt
    where admission_idempotency_key=p_admission_idempotency_key;
  if prior.admission_id is not null then
    if prior.token_ref_digest<>p_token_ref_digest or
       prior.operation_manifest_digest<>manifest_digest or prior.session_principal<>session_user or
       prior.transaction_id<>txid_current() or prior.backend_pid<>pg_backend_pid() then
      raise exception 'scac.refusal.replay: SIEP-18 admission idempotency mismatch'; end if;
    perform set_config('carr.scac_admission_id',prior.admission_id::text,true);
    return jsonb_build_object('admitted',true,'admission_id',prior.admission_id::text,
      'decision',prior.decision,'monitor_state',prior.monitor_state,
      'production_enforcement_active',false);
  end if;
  select * into b from ops.scac_token_verification_binding
    where token_ref_digest=p_token_ref_digest for key share;
  select * into t from ops.scac_capability_token_receipt
    where token_ref_digest=p_token_ref_digest for key share;
  select * into c from ops.scac_pop_challenge where challenge_id=t.challenge_id for key share;
  select * into e from ops.scac_device_enrollment where device_ref=c.device_ref for key share;
  select * into pe from ops.scac_policy_epoch order by epoch desc limit 1 for key share;
  select * into re from ops.scac_mutation_registry_entry
    where registry_version=pe.registry_version and ingress_key=c.ingress_key for key share;
  select * into ob from ops.scac_operation_effect_binding
    where registry_version=pe.registry_version and ingress_key=c.ingress_key
      and privilege_bundle=bundle for key share;
  state:=ops.scac_reference_monitor_state(); control:=ops.scac_token_control_snapshot();
  root_state:=ops.scac_root_trust_chain_state();
  if b.token_ref_digest is null or t.token_ref_digest is null or c.challenge_id is null or
     e.device_ref is null or re.ingress_key is null or ob.binding_digest is null then
    raise exception 'scac.refusal.token_invalid: SIEP-18 required bound fact missing'; end if;
  if e.profile_key='studio-executor' then
    select count(*) into studio_benchmark_count
      from ops.scac_device_benchmark_receipt br
      where br.device_ref=e.device_ref and br.facts_digest=e.facts_digest and br.passed;
    if not exists(select 1 from ops.scac_device_fact_receipt fr
           where fr.device_ref=e.device_ref and fr.facts_digest=e.facts_digest)
       or studio_benchmark_count<>9 then
      raise exception 'scac.refusal.device_benchmark_incomplete: SIEP-18 Studio evidence incomplete';
    end if;
  end if;
  if state->>'monitor_state'<>'current' then
    raise exception 'scac.refusal.reference_monitor_unavailable: SIEP-18 monitor or grant state stale'; end if;
  if state->>'mode'<>'enforced_source_test' then
    raise exception 'scac.refusal.reference_monitor_unavailable: SIEP-18 shadow is non-authorizing'; end if;
  if control->>'kill_switch_state'<>'inactive' then
    raise exception 'scac.refusal.kill_switch: SIEP-18 global control engaged'; end if;
  if t.expires_at<=clock_timestamp() or c.expires_at<=clock_timestamp() then
    raise exception 'scac.refusal.token_invalid: SIEP-18 token or proof expired'; end if;
  if pe.epoch<>c.policy_epoch or pe.epoch_digest<>c.policy_epoch_digest or
     pe.registry_version<>c.registry_version or pe.registry_digest<>c.registry_digest then
    raise exception 'scac.refusal.epoch_incompatible: SIEP-18 token policy is not current'; end if;
  if re.effect_class='read_only' or coalesce((re.contract->>'classification_authorizing')::boolean,true) or
     re.contract->>'mutation_kind'<>c.mutation_kind or re.contract->>'target_surface'<>c.target_surface then
    raise exception 'scac.refusal.mutation_unregistered: SIEP-18 ingress contract unavailable'; end if;
  if ob.registry_entry_digest<>re.entry_digest or
     ob.effect_keys is distinct from effects or ob.effect_set_digest<>effect_digest then
    raise exception 'scac.refusal.scope_violation: SIEP-18 exact operation effect set mismatch'; end if;
  if manifest_digest<>c.operation_manifest_digest or
     p_operation_manifest->>'principal_digest'<>c.principal_digest or
     p_operation_manifest->>'device_ref'<>c.device_ref or
     p_operation_manifest->>'device_key_digest'<>c.device_key_digest or
     p_operation_manifest->>'facts_digest'<>c.facts_digest or
     (p_operation_manifest->>'workload_digest') is distinct from c.workload_digest or
     p_operation_manifest->>'registry_version'<>c.registry_version or
     p_operation_manifest->>'registry_digest'<>c.registry_digest or
     p_operation_manifest->>'ingress_key'<>c.ingress_key or
     p_operation_manifest->>'mutation_kind'<>c.mutation_kind or
     p_operation_manifest->>'target_surface'<>c.target_surface or
     (p_operation_manifest->>'policy_epoch')::bigint<>c.policy_epoch or
     p_operation_manifest->>'policy_epoch_digest'<>c.policy_epoch_digest or
     p_operation_manifest->>'idempotency_digest'<>c.idempotency_digest or
     coalesce(p_operation_manifest->>'request_payload_digest','')!~'^sha256:[0-9a-f]{64}$' then
    raise exception 'scac.refusal.token_invalid: SIEP-18 request/manifest/token binding mismatch'; end if;
  if coalesce((root_state->>'structurally_valid')::boolean,false) is not true or
     root_state->>'latest_event_digest'<>b.issuer_root_event_digest then
    raise exception 'scac.refusal.root_untrusted: SIEP-18 issuer root is not current'; end if;
  if exists(select 1 from ops.scac_token_revocation_event r where
       (r.subject_kind='device' and r.subject_digest=ops.scac_token_sha256_text(c.device_ref)) or
       (r.subject_kind='device_key' and r.subject_digest=c.device_key_digest) or
       (r.subject_kind='facts' and r.subject_digest=c.facts_digest) or
       (c.workload_digest is not null and r.subject_kind='workload' and r.subject_digest=c.workload_digest) or
       (r.subject_kind='challenge' and r.subject_digest=c.challenge_digest) or
       (r.subject_kind='token' and r.subject_digest=t.token_ref_digest) or
       (r.subject_kind='issuer_key' and r.subject_digest=t.issuer_key_digest) or
       (r.subject_kind='root_event' and r.subject_digest=t.issuer_root_event_digest)) then
    raise exception 'scac.refusal.revoked: SIEP-18 subject revoked'; end if;
  if exists(select 1 from unnest(effects) x where not exists(
       select 1 from ops.scac_mutation_registry_entry xre
       where xre.registry_version=pe.registry_version and xre.ingress_key=x
         and xre.ingress_kind in ('db_relation_acl','db_column_acl')
         and xre.contract->>'grantee'=bundle
         and xre.contract->>'privilege' in ('insert','update','delete','truncate'))) then
    raise exception 'scac.refusal.mutation_unregistered: SIEP-18 effect is not a current grant'; end if;
  admission:=gen_random_uuid();
  insert into ops.scac_reference_monitor_receipt
    (admission_id,token_ref_digest,operation_manifest_digest,request_payload_digest,
     idempotency_digest,ingress_key,effect_keys,effect_set_digest,
     operation_effect_binding_digest,policy_epoch,
     policy_epoch_digest,registry_version,registry_digest,principal_digest,
     session_principal,privilege_bundle,grant_digest,backend_pid,transaction_id,
     admission_idempotency_key,decision,monitor_state,direct_grant_guarded)
  values (admission,p_token_ref_digest,manifest_digest,
    p_operation_manifest->>'request_payload_digest',c.idempotency_digest,c.ingress_key,
    effects,effect_digest,ob.binding_digest,pe.epoch,pe.epoch_digest,pe.registry_version,pe.registry_digest,
    c.principal_digest,session_user,bundle,state->>'grant_digest',pg_backend_pid(),txid_current(),
    p_admission_idempotency_key,'admit_source_test_nonproduction','current',true);
  perform set_config('carr.scac_admission_id',admission::text,true);
  return jsonb_build_object('admitted',true,'admission_id',admission::text,
    'decision','admit_source_test_nonproduction','monitor_state','current',
    'grant_digest',state->>'grant_digest','production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_reference_monitor_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare mode_state jsonb; admission_text text; receipt ops.scac_reference_monitor_receipt%rowtype;
        bundle text; effect_key text; changed_ok boolean;
begin
  bundle:=ops.scac_runtime_privilege_bundle();
  mode_state:=ops.scac_reference_monitor_mode();
  if mode_state->>'mode'='shadow' and mode_state->>'integrity_state'<>'invalid_fail_closed' then
    return case when tg_op='DELETE' then old else new end;
  end if;
  if mode_state->>'mode'<>'enforced_source_test' or
     mode_state->>'integrity_state'<>'valid_append_only_chain' then
    raise exception 'scac.refusal.reference_monitor_unavailable: SIEP-18 mode control invalid';
  end if;
  if bundle is null then
    if session_user in ('neondb_owner','carr_ci') then
      return case when tg_op='DELETE' then old else new end;
    end if;
    raise exception 'scac.refusal.identity_unverified: SIEP-18 unknown DML principal';
  end if;
  admission_text:=current_setting('carr.scac_admission_id',true);
  if coalesce(admission_text,'')!~'^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then
    raise exception 'scac.refusal.reference_monitor_unavailable: SIEP-18 admission missing';
  end if;
  select * into receipt from ops.scac_reference_monitor_receipt
    where admission_id=admission_text::uuid and backend_pid=pg_backend_pid()
      and transaction_id=txid_current() and session_principal=session_user;
  if receipt.admission_id is null then
    raise exception 'scac.refusal.reference_monitor_unavailable: SIEP-18 admission not current transaction';
  end if;
  effect_key:='db-relation-acl:'||tg_table_schema||'.'||tg_table_name||':'||
    bundle||':'||lower(tg_op);
  if effect_key=any(receipt.effect_keys) then
    return case when tg_op='DELETE' then old else new end;
  end if;
  if tg_op='UPDATE' and tg_level='ROW' then
    select coalesce(bool_and(('db-column-acl:'||tg_table_schema||'.'||tg_table_name||'.'||k||':'||
      bundle||':update')=any(receipt.effect_keys)),false) into changed_ok
    from jsonb_object_keys(to_jsonb(new)) k where (to_jsonb(new)->k) is distinct from (to_jsonb(old)->k);
    if changed_ok then return new; end if;
  end if;
  raise exception 'scac.refusal.scope_violation: SIEP-18 effect % not admitted',effect_key;
end $fn$;

create or replace function ops.scac_transition_reference_monitor_mode(
  p_mode text,p_reason_digest text,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare tip ops.scac_reference_monitor_mode_event%rowtype; prior ops.scac_reference_monitor_mode_event%rowtype;
        state jsonb; next_no bigint; next_digest text;
begin
  if session_user<>'carr_authority_joe' then
    raise exception 'SIEP-18 monitor mode authority is Joe-only'; end if;
  if p_mode not in ('shadow','enforced_source_test') or
     coalesce(p_reason_digest,'')!~'^sha256:[0-9a-f]{64}$' or p_idempotency_key is null then
    raise exception 'SIEP-18 monitor mode input malformed'; end if;
  perform pg_advisory_xact_lock(hashtextextended('carr-siep18-reference-monitor',0));
  select * into prior from ops.scac_reference_monitor_mode_event where idempotency_key=p_idempotency_key;
  if prior.event_no is not null then
    if prior.mode<>p_mode or prior.reason_digest<>p_reason_digest then
      raise exception 'SIEP-18 monitor mode idempotency mismatch'; end if;
    return ops.scac_reference_monitor_mode();
  end if;
  state:=ops.scac_reference_monitor_state();
  if p_mode='enforced_source_test' and
     (state->>'grant_state'<>'current' or state->>'guard_state'<>'complete') then
    raise exception 'scac.refusal.reference_monitor_unavailable: SIEP-18 cannot enforce stale grants'; end if;
  select * into tip from ops.scac_reference_monitor_mode_event order by event_no desc limit 1;
  if coalesce(tip.mode,'shadow')=p_mode then raise exception 'SIEP-18 mode transition must change state'; end if;
  next_no:=coalesce(tip.event_no,0)+1;
  next_digest:=ops.scac_reference_monitor_sha256(jsonb_build_object(
    'schema_version','scac-reference-monitor-mode-event.v1','event_no',next_no,
    'previous_event_digest',tip.event_digest,'mode',p_mode,'reason_digest',p_reason_digest,
    'grant_digest',state->>'grant_digest','idempotency_key',p_idempotency_key::text,
    'recorded_by','joe'));
  insert into ops.scac_reference_monitor_mode_event
    (event_no,event_digest,previous_event_digest,mode,reason_digest,grant_digest,
     idempotency_key,recorded_by)
  values (next_no,next_digest,tip.event_digest,p_mode,p_reason_digest,state->>'grant_digest',
    p_idempotency_key,'joe');
  return ops.scac_reference_monitor_mode();
end $fn$;

create or replace function ops.scac_siep18_append_only_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin raise exception 'SIEP-18 issuer, token, mode, and monitor receipts are append-only'; end $fn$;
create or replace function ops.scac_siep18_truncate_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin raise exception 'SIEP-18 issuer, token, mode, and monitor receipts cannot be truncated'; end $fn$;

do $seal$
declare relation_name text;
begin
  foreach relation_name in array array['scac_token_issuer_binding','scac_token_verification_binding',
    'scac_operation_effect_binding',
    'scac_reference_monitor_mode_event','scac_reference_monitor_receipt'] loop
    execute format('create trigger %I before update or delete on ops.%I for each row execute function ops.scac_siep18_append_only_guard()',relation_name||'_immutable',relation_name);
    execute format('create trigger %I before truncate on ops.%I for each statement execute function ops.scac_siep18_truncate_guard()',relation_name||'_no_truncate',relation_name);
  end loop;
end $seal$;

-- Install the guard on every current base/partition table reachable by a
-- runtime DML bundle.  Owner/migration sessions are not runtime principals.
do $guards$
declare r record;
begin
  for r in
    with runtime_roles as (
      select oid from pg_roles where rolname in ('carr_writer','carr_jobs','carr_authority')
    ), writable as (
      select distinct c.oid,n.nspname,c.relname from pg_class c
      join pg_namespace n on n.oid=c.relnamespace
      cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
      where c.relkind in ('r','p') and a.grantee<>c.relowner and
        (a.grantee=0 or a.grantee in(select oid from runtime_roles))
        and a.privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
      union
      select distinct c.oid,n.nspname,c.relname from pg_attribute att
      join pg_class c on c.oid=att.attrelid join pg_namespace n on n.oid=c.relnamespace
      cross join lateral aclexplode(att.attacl) a
      where c.relkind in ('r','p') and att.attnum>0 and not att.attisdropped
        and a.grantee<>c.relowner
        and (a.grantee=0 or a.grantee in(select oid from runtime_roles))
        and a.privilege_type in ('INSERT','UPDATE')
    ) select * from writable order by nspname,relname
  loop
    -- A trigger created on a partitioned parent is cloned onto its partitions.
    -- Skip either trigger when an ancestor installation already supplied it;
    -- blindly recreating the fixed name on every parent and leaf is an error.
    if not exists(
      select 1 from pg_trigger t where t.tgrelid=r.oid and not t.tgisinternal
        and t.tgfoid='ops.scac_reference_monitor_guard()'::regprocedure
        and (t.tgtype & 1)=1
    ) then
      execute format('create trigger scac_reference_monitor_guard_row before insert or update or delete on %I.%I for each row execute function ops.scac_reference_monitor_guard()',r.nspname,r.relname);
    end if;
    if not exists(
      select 1 from pg_trigger t where t.tgrelid=r.oid and not t.tgisinternal
        and t.tgfoid='ops.scac_reference_monitor_guard()'::regprocedure
        and (t.tgtype & 1)=0
    ) then
      execute format('create trigger scac_reference_monitor_guard_truncate before truncate on %I.%I for each statement execute function ops.scac_reference_monitor_guard()',r.nspname,r.relname);
    end if;
  end loop;
end $guards$;

revoke all on table ops.scac_token_issuer_binding,ops.scac_token_verification_binding,
  ops.scac_operation_effect_binding,
  ops.scac_reference_monitor_mode_event,ops.scac_reference_monitor_receipt
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.scac_json_has_exact_keys(jsonb,text[]),
  ops.scac_reference_monitor_sha256(jsonb),ops.scac_runtime_privilege_bundle(),
  ops.scac_runtime_dml_grant_snapshot(),ops.scac_reference_monitor_mode(),
  ops.scac_reference_monitor_state(),
  ops.scac_register_token_issuer_binding(text,text,text,uuid),
  ops.scac_record_token_verification_binding(text,jsonb,uuid),
  ops.scac_register_operation_effect_binding(text,text,text,text,text[],uuid),
  ops.scac_admit_mutation(text,jsonb,uuid),ops.scac_reference_monitor_guard(),
  ops.scac_transition_reference_monitor_mode(text,text,uuid),
  ops.scac_siep18_append_only_guard(),ops.scac_siep18_truncate_guard()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.scac_reference_monitor_state()
  to carr_writer,carr_jobs,carr_authority;
grant execute on function ops.scac_record_token_verification_binding(text,jsonb,uuid)
  to carr_jobs;
grant execute on function ops.scac_admit_mutation(text,jsonb,uuid)
  to carr_writer,carr_jobs,carr_authority;
grant execute on function ops.scac_register_token_issuer_binding(text,text,text,uuid),
  ops.scac_register_operation_effect_binding(text,text,text,text,text[],uuid),
  ops.scac_transition_reference_monitor_mode(text,text,uuid)
  to carr_authority;

do $assert$
declare leaked text; missing integer;
begin
  select string_agg(table_name||':'||privilege_type,',' order by table_name,privilege_type)
    into leaked from information_schema.role_table_grants where table_schema='ops'
    and table_name in ('scac_token_issuer_binding','scac_token_verification_binding',
      'scac_operation_effect_binding',
      'scac_reference_monitor_mode_event','scac_reference_monitor_receipt')
    and grantee in ('PUBLIC','carr_reader','carr_writer','carr_jobs','carr_authority');
  if leaked is not null then raise exception 'SIEP-18 raw control grants leaked: %',leaked; end if;
  if has_function_privilege('carr_writer',
       'ops.scac_transition_reference_monitor_mode(text,text,uuid)'::regprocedure,'EXECUTE') or
     has_function_privilege('carr_authority',
       'ops.scac_record_token_verification_binding(text,jsonb,uuid)'::regprocedure,'EXECUTE') then
    raise exception 'SIEP-18 typed grants widened across authority/runtime roles'; end if;
  with runtime_roles as (
    select oid from pg_roles where rolname in ('carr_writer','carr_jobs','carr_authority')
  ), writable as (
    select distinct c.oid from pg_class c cross join lateral
      aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
      where c.relkind in ('r','p') and a.grantee<>c.relowner and
        (a.grantee=0 or a.grantee in(select oid from runtime_roles))
        and a.privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
    union
    select distinct c.oid from pg_attribute att join pg_class c on c.oid=att.attrelid
      cross join lateral aclexplode(att.attacl) a where c.relkind in ('r','p')
        and att.attnum>0 and not att.attisdropped and a.grantee<>c.relowner and
        (a.grantee=0 or a.grantee in(select oid from runtime_roles))
        and a.privilege_type in ('INSERT','UPDATE')
  ) select count(*) into missing from writable w where not exists(
    select 1 from pg_trigger t where t.tgrelid=w.oid and not t.tgisinternal
      and t.tgfoid='ops.scac_reference_monitor_guard()'::regprocedure
      and (t.tgtype & 1)=1);
  if missing<>0 then raise exception 'SIEP-18 current runtime DML guard coverage incomplete: %',missing; end if;
end $assert$;

-- forward-consistency patch (progressive_loop.py): re-agree ops.scac_mutation_catalog_v8_current() with this file's own catalog effect -- it is re-evaluated by every subsequent commit's deferred epoch-refresh trigger until the next writer checkpoint retires it. See RESULT.md, 'checkpoint 7 finding'.
CREATE OR REPLACE FUNCTION ops.scac_mutation_catalog_v8_current()
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
  if observed_count<>338 or observed_digest<>'sha256:ccf023867a696884b2b9e50ae6eccc7b4e2afd9d7d6dbd1a93c01d8b1ec38555' then return false; end if;
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
