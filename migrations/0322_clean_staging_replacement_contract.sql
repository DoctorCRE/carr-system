-- Clean staging replacement: exact, append-only project and full-tree evidence.
--
-- The prepared contract binds the immutable old and replacement Neon IDs, the
-- exact source/artifact/config/dependency identities, and every migration in
-- the repository tree.  The verifier records evidence inside the replacement
-- database itself.  It never accepts a migration prefix or caller-supplied
-- ledger readback: the recorder compares the declaration with the complete
-- live public.schema_migrations ledger in this database.
-- source_tree_sha256 is the digest of the controller's full tracked-entry
-- manifest (mode, type, object ID, path), including gitlink/submodule commits;
-- source_tree_oid separately binds the repository root tree object.  Neither
-- identity is interchangeable with the deployed artifact digest.

begin;

create table ops.staging_replacement_project_contract (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  schema_version text not null
    check (schema_version='clean-staging-replacement-contract.v1'),
  tree_mode text not null check (tree_mode='full'),
  git_sha text not null check (git_sha ~ '^[0-9a-f]{40}$'),
  source_tree_oid text not null check (source_tree_oid ~ '^[0-9a-f]{40}$'),
  source_tree_sha256 text not null
    check (source_tree_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  source_tree_entry_count integer not null check (source_tree_entry_count>0),
  artifact_sha256 text not null
    check (artifact_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  config_sha256 text not null
    check (config_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  dependency_sha256 text not null
    check (dependency_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  migration_ledger jsonb not null check (jsonb_typeof(migration_ledger)='object'),
  migration_count integer not null check (migration_count>0),
  migration_highest text not null
    check (migration_highest ~ '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$'),
  migration_ledger_sha256 text not null
    check (migration_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  prior_staging_project_id text not null check (btrim(prior_staging_project_id)<>''),
  replacement_project_id text not null check (btrim(replacement_project_id)<>''),
  replacement_branch_id text not null check (btrim(replacement_branch_id)<>''),
  replacement_endpoint_id text not null check (btrim(replacement_endpoint_id)<>''),
  expected_synthetic_data_count bigint not null check (expected_synthetic_data_count>0),
  expected_production_overlap_count bigint not null
    check (expected_production_overlap_count=0),
  writer_session_user text not null default session_user
    check (writer_session_user='carr_jobs'),
  created_at timestamptz not null default clock_timestamp()
);

create table ops.staging_replacement_project_receipt (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  contract_id uuid not null unique
    references ops.staging_replacement_project_contract(id) on delete restrict,
  schema_version text not null
    check (schema_version='clean-staging-replacement-observation.v1'),
  git_sha text not null check (git_sha ~ '^[0-9a-f]{40}$'),
  source_tree_oid text not null check (source_tree_oid ~ '^[0-9a-f]{40}$'),
  source_tree_sha256 text not null
    check (source_tree_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  source_tree_entry_count integer not null check (source_tree_entry_count>0),
  artifact_sha256 text not null
    check (artifact_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  config_sha256 text not null
    check (config_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  dependency_sha256 text not null
    check (dependency_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  prior_staging_project_id text not null,
  replacement_project_id text not null,
  replacement_branch_id text not null,
  replacement_endpoint_id text not null,
  live_migration_ledger jsonb not null check (jsonb_typeof(live_migration_ledger)='object'),
  live_migration_count integer not null check (live_migration_count>0),
  live_migration_highest text not null,
  live_migration_ledger_sha256 text not null
    check (live_migration_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  synthetic_data_count bigint not null check (synthetic_data_count>0),
  production_overlap_count bigint not null check (production_overlap_count=0),
  receipt_sha256 text not null unique
    check (receipt_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  evidence_ref text not null unique
    check (evidence_ref ~ '^ops\.staging-replacement-project:sha256:[0-9a-f]{64}$'),
  writer_session_user text not null default session_user
    check (writer_session_user='carr_program5_forward_fix_verifier'),
  observed_at timestamptz not null default clock_timestamp()
);

create trigger staging_replacement_project_contract_append_only
before update or delete on ops.staging_replacement_project_contract
for each row execute function ops.refuse_program5_evidence_mutation();

create trigger staging_replacement_project_receipt_append_only
before update or delete on ops.staging_replacement_project_receipt
for each row execute function ops.refuse_program5_evidence_mutation();

create or replace function ops.prepare_staging_replacement_project(
  p_idempotency_key uuid, p_contract jsonb
) returns jsonb
language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare
  expected_keys constant text[] := array[
    'artifact_sha256','config_sha256','dependency_sha256',
    'expected_production_overlap_count','expected_synthetic_data_count','git_sha',
    'migration_count','migration_highest','migration_ledger','migration_ledger_sha256',
    'prior_staging_project_id','replacement_branch_id','replacement_endpoint_id',
    'replacement_project_id','schema_version','source_tree_entry_count',
    'source_tree_oid','source_tree_sha256','tree_mode'
  ];
  production_project_id constant text := 'steep-field-48688294';
  supplied_keys text[]; ledger jsonb; declared_ledger_digest text;
  declared_count integer; declared_highest text; invalid_entries integer;
  existing ops.staging_replacement_project_contract%rowtype;
  receipt ops.staging_replacement_project_receipt%rowtype; contract_uuid uuid;
begin
  if session_user<>'carr_jobs' then
    raise exception 'staging replacement preparation requires the carr_jobs session';
  end if;
  if p_idempotency_key is null or jsonb_typeof(p_contract)<>'object' then
    raise exception 'invalid staging replacement preparation input';
  end if;
  select array_agg(k order by k collate "C") into supplied_keys
    from jsonb_object_keys(p_contract) as keys(k);
  if supplied_keys is distinct from expected_keys then
    raise exception 'staging replacement contract has missing or unknown keys';
  end if;
  if jsonb_typeof(p_contract->'migration_ledger')<>'object'
     or jsonb_typeof(p_contract->'migration_count')<>'number'
     or jsonb_typeof(p_contract->'source_tree_entry_count')<>'number'
     or jsonb_typeof(p_contract->'expected_synthetic_data_count')<>'number'
     or jsonb_typeof(p_contract->'expected_production_overlap_count')<>'number' then
    raise exception 'staging replacement contract has invalid typed values';
  end if;
  ledger:=p_contract->'migration_ledger';
  select count(*)::integer, max(e.key collate "C"),
         count(*) filter (where e.key !~ '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$'
                           or e.value !~ '^[0-9a-f]{64}$')::integer,
         'sha256:'||encode(public.digest(
           coalesce(string_agg(
             convert_to(e.key,'UTF8')||decode('00','hex')||
             convert_to(e.value,'UTF8')||decode('0a','hex'),
             ''::bytea order by e.key collate "C"),''::bytea),'sha256'),'hex')
    into declared_count,declared_highest,invalid_entries,declared_ledger_digest
    from jsonb_each_text(ledger) e;
  if declared_count=0 or invalid_entries<>0
     or (p_contract->>'migration_count')::integer<>declared_count
     or p_contract->>'migration_highest' is distinct from declared_highest
     or p_contract->>'migration_ledger_sha256' is distinct from declared_ledger_digest then
    raise exception 'staging replacement contract is not the exact full migration tree';
  end if;
  if p_contract->>'schema_version'<>'clean-staging-replacement-contract.v1'
     or p_contract->>'tree_mode'<>'full'
     or coalesce(p_contract->>'git_sha','') !~ '^[0-9a-f]{40}$'
     or coalesce(p_contract->>'source_tree_oid','') !~ '^[0-9a-f]{40}$'
     or coalesce(p_contract->>'source_tree_sha256','') !~ '^sha256:[0-9a-f]{64}$'
     or (p_contract->>'source_tree_entry_count')::integer<=0
     or coalesce(p_contract->>'artifact_sha256','') !~ '^sha256:[0-9a-f]{64}$'
     or coalesce(p_contract->>'config_sha256','') !~ '^sha256:[0-9a-f]{64}$'
     or coalesce(p_contract->>'dependency_sha256','') !~ '^sha256:[0-9a-f]{64}$'
     or coalesce(btrim(p_contract->>'prior_staging_project_id'),'')=''
     or coalesce(btrim(p_contract->>'replacement_project_id'),'')=''
     or coalesce(btrim(p_contract->>'replacement_branch_id'),'')=''
     or coalesce(btrim(p_contract->>'replacement_endpoint_id'),'')=''
     or p_contract->>'prior_staging_project_id'=p_contract->>'replacement_project_id'
     or p_contract->>'prior_staging_project_id'=production_project_id
     or p_contract->>'replacement_project_id'=production_project_id
     or (p_contract->>'expected_synthetic_data_count')::bigint<=0
     or (p_contract->>'expected_production_overlap_count')::bigint<>0 then
    raise exception 'staging replacement contract identity or isolation assertion is invalid';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,322));
  select * into existing from ops.staging_replacement_project_contract
   where idempotency_key=p_idempotency_key;
  if found then
    if (existing.schema_version,existing.tree_mode,existing.git_sha,
        existing.source_tree_oid,existing.source_tree_sha256,existing.source_tree_entry_count,
        existing.artifact_sha256,existing.config_sha256,existing.dependency_sha256,
        existing.migration_ledger,existing.migration_count,existing.migration_highest,
        existing.migration_ledger_sha256,existing.prior_staging_project_id,
        existing.replacement_project_id,existing.replacement_branch_id,
        existing.replacement_endpoint_id,existing.expected_synthetic_data_count,
        existing.expected_production_overlap_count) is distinct from
       (p_contract->>'schema_version',p_contract->>'tree_mode',p_contract->>'git_sha',
        p_contract->>'source_tree_oid',p_contract->>'source_tree_sha256',
        (p_contract->>'source_tree_entry_count')::integer,p_contract->>'artifact_sha256',
        p_contract->>'config_sha256',p_contract->>'dependency_sha256',ledger,
        declared_count,declared_highest,declared_ledger_digest,
        p_contract->>'prior_staging_project_id',p_contract->>'replacement_project_id',
        p_contract->>'replacement_branch_id',p_contract->>'replacement_endpoint_id',
        (p_contract->>'expected_synthetic_data_count')::bigint,0::bigint) then
      raise exception 'staging replacement idempotency key was reused with changed input';
    end if;
    select * into receipt from ops.staging_replacement_project_receipt
     where contract_id=existing.id;
    return jsonb_build_object('contract_id',existing.id,
      'state',case when receipt.id is null then 'prepared' else 'observed' end,
      'receipt_id',receipt.id,'evidence_ref',receipt.evidence_ref,'replayed',true);
  end if;

  insert into ops.staging_replacement_project_contract(
    idempotency_key,schema_version,tree_mode,git_sha,source_tree_oid,
    source_tree_sha256,source_tree_entry_count,artifact_sha256,config_sha256,
    dependency_sha256,migration_ledger,migration_count,migration_highest,
    migration_ledger_sha256,prior_staging_project_id,replacement_project_id,
    replacement_branch_id,replacement_endpoint_id,expected_synthetic_data_count,
    expected_production_overlap_count,writer_session_user)
  values(p_idempotency_key,p_contract->>'schema_version',p_contract->>'tree_mode',
    p_contract->>'git_sha',p_contract->>'source_tree_oid',p_contract->>'source_tree_sha256',
    (p_contract->>'source_tree_entry_count')::integer,p_contract->>'artifact_sha256',
    p_contract->>'config_sha256',p_contract->>'dependency_sha256',ledger,
    declared_count,declared_highest,declared_ledger_digest,
    p_contract->>'prior_staging_project_id',p_contract->>'replacement_project_id',
    p_contract->>'replacement_branch_id',p_contract->>'replacement_endpoint_id',
    (p_contract->>'expected_synthetic_data_count')::bigint,0,session_user)
  returning id into contract_uuid;
  return jsonb_build_object('contract_id',contract_uuid,'state','prepared',
    'receipt_id',null,'evidence_ref',null,'replayed',false);
end $$;

create or replace function ops.record_staging_replacement_project(
  p_idempotency_key uuid, p_observation jsonb
) returns jsonb
language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare
  expected_keys constant text[] := array[
    'artifact_sha256','config_sha256','dependency_sha256','git_sha',
    'prior_staging_project_id','production_overlap_count','replacement_branch_id',
    'replacement_endpoint_id','replacement_project_id','schema_version',
    'source_tree_entry_count','source_tree_oid','source_tree_sha256','synthetic_data_count'
  ];
  supplied_keys text[]; contract ops.staging_replacement_project_contract%rowtype;
  existing ops.staging_replacement_project_receipt%rowtype; receipt_uuid uuid;
  live_ledger jsonb; live_count integer; live_highest text; live_digest text;
  derived_synthetic_count bigint; observed_at_value timestamptz;
  projection jsonb; receipt_digest text; receipt_ref text;
begin
  if session_user<>'carr_program5_forward_fix_verifier'
     or not pg_has_role(session_user,'carr_program5_forward_fix_verifiers','member') then
    raise exception 'staging replacement recording requires the scoped verifier session';
  end if;
  if p_idempotency_key is null or jsonb_typeof(p_observation)<>'object' then
    raise exception 'invalid staging replacement observation input';
  end if;
  select array_agg(k order by k collate "C") into supplied_keys
    from jsonb_object_keys(p_observation) as keys(k);
  if supplied_keys is distinct from expected_keys
     or jsonb_typeof(p_observation->'source_tree_entry_count')<>'number'
     or jsonb_typeof(p_observation->'synthetic_data_count')<>'number'
     or jsonb_typeof(p_observation->'production_overlap_count')<>'number' then
    raise exception 'staging replacement observation has missing, unknown, or invalid typed keys';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,322));
  select * into contract from ops.staging_replacement_project_contract
   where idempotency_key=p_idempotency_key;
  if not found then raise exception 'staging replacement contract was not prepared'; end if;

  if (p_observation->>'schema_version',p_observation->>'git_sha',
      p_observation->>'source_tree_oid',p_observation->>'source_tree_sha256',
      (p_observation->>'source_tree_entry_count')::integer,
      p_observation->>'artifact_sha256',p_observation->>'config_sha256',
      p_observation->>'dependency_sha256',p_observation->>'prior_staging_project_id',
      p_observation->>'replacement_project_id',p_observation->>'replacement_branch_id',
      p_observation->>'replacement_endpoint_id') is distinct from
     ('clean-staging-replacement-observation.v1',contract.git_sha,
      contract.source_tree_oid,contract.source_tree_sha256,contract.source_tree_entry_count,
      contract.artifact_sha256,contract.config_sha256,contract.dependency_sha256,
      contract.prior_staging_project_id,contract.replacement_project_id,
      contract.replacement_branch_id,contract.replacement_endpoint_id) then
    raise exception 'staging replacement observation does not match the prepared immutable identity';
  end if;

  -- The local count is derived here from the replacement database. Production
  -- overlap is cross-project evidence: this function can only require the
  -- controller's governed G1 table-ID comparison assertion to be exactly zero.
  if to_regclass('public.party') is null or to_regclass('public.client') is null
     or to_regclass('public.deal') is null or to_regclass('public.lead') is null
     or to_regclass('public.vendor') is null then
    raise exception 'replacement database lacks a required synthetic-data table';
  end if;
  select (select count(*) from public.party)
       + (select count(*) from public.client)
       + (select count(*) from public.deal)
       + (select count(*) from public.lead)
       + (select count(*) from public.vendor)
    into derived_synthetic_count;
  if derived_synthetic_count<=0
     or (p_observation->>'synthetic_data_count')::bigint<>derived_synthetic_count
     or derived_synthetic_count<>contract.expected_synthetic_data_count
     or (p_observation->>'production_overlap_count')::bigint<>0
     or contract.expected_production_overlap_count<>0 then
    raise exception 'staging replacement synthetic-data or no-Production-overlap assertion failed';
  end if;

  select coalesce(jsonb_object_agg(filename,sha256 order by filename collate "C"),'{}'::jsonb),
         count(*)::integer,max(filename collate "C"),
         'sha256:'||encode(public.digest(
           coalesce(string_agg(convert_to(filename,'UTF8')||decode('00','hex')||
             convert_to(sha256,'UTF8')||decode('0a','hex'),''::bytea
             order by filename collate "C"),''::bytea),'sha256'),'hex')
    into live_ledger,live_count,live_highest,live_digest
    from public.schema_migrations;
  if live_ledger is distinct from contract.migration_ledger
     or live_count is distinct from contract.migration_count
     or live_highest is distinct from contract.migration_highest
     or live_digest is distinct from contract.migration_ledger_sha256 then
    raise exception 'replacement database live full migration ledger does not match the exact declared tree';
  end if;

  select * into existing from ops.staging_replacement_project_receipt
   where contract_id=contract.id;
  if found then
    if (existing.schema_version,existing.git_sha,existing.source_tree_oid,
        existing.source_tree_sha256,existing.source_tree_entry_count,
        existing.artifact_sha256,existing.config_sha256,existing.dependency_sha256,
        existing.prior_staging_project_id,existing.replacement_project_id,
        existing.replacement_branch_id,existing.replacement_endpoint_id,
        existing.live_migration_ledger,existing.live_migration_count,
        existing.live_migration_highest,existing.live_migration_ledger_sha256,
        existing.synthetic_data_count,existing.production_overlap_count) is distinct from
       (p_observation->>'schema_version',contract.git_sha,contract.source_tree_oid,
        contract.source_tree_sha256,contract.source_tree_entry_count,contract.artifact_sha256,
        contract.config_sha256,contract.dependency_sha256,contract.prior_staging_project_id,
        contract.replacement_project_id,contract.replacement_branch_id,
        contract.replacement_endpoint_id,live_ledger,live_count,live_highest,live_digest,
        derived_synthetic_count,0::bigint) then
      raise exception 'staging replacement observation replay changed recorded evidence';
    end if;
    return jsonb_build_object('contract_id',contract.id,'receipt_id',existing.id,
      'evidence_ref',existing.evidence_ref,'state','observed','replayed',true);
  end if;

  observed_at_value:=clock_timestamp();
  projection:=jsonb_build_object('contract_id',contract.id,'git_sha',contract.git_sha,
    'source_tree_oid',contract.source_tree_oid,'source_tree_sha256',contract.source_tree_sha256,
    'source_tree_entry_count',contract.source_tree_entry_count,
    'artifact_sha256',contract.artifact_sha256,'config_sha256',contract.config_sha256,
    'dependency_sha256',contract.dependency_sha256,
    'prior_staging_project_id',contract.prior_staging_project_id,
    'replacement_project_id',contract.replacement_project_id,
    'replacement_branch_id',contract.replacement_branch_id,
    'replacement_endpoint_id',contract.replacement_endpoint_id,
    'live_migration_ledger',live_ledger,'live_migration_count',live_count,
    'live_migration_highest',live_highest,'live_migration_ledger_sha256',live_digest,
    'synthetic_data_count',derived_synthetic_count,'production_overlap_count',0,
    'observed_at',observed_at_value);
  receipt_digest:='sha256:'||encode(public.digest(projection::text,'sha256'),'hex');
  receipt_ref:='ops.staging-replacement-project:'||receipt_digest;
  insert into ops.staging_replacement_project_receipt(
    idempotency_key,contract_id,schema_version,git_sha,source_tree_oid,
    source_tree_sha256,source_tree_entry_count,artifact_sha256,config_sha256,
    dependency_sha256,prior_staging_project_id,replacement_project_id,
    replacement_branch_id,replacement_endpoint_id,live_migration_ledger,
    live_migration_count,live_migration_highest,live_migration_ledger_sha256,
    synthetic_data_count,production_overlap_count,receipt_sha256,evidence_ref,
    writer_session_user,observed_at)
  values(p_idempotency_key,contract.id,p_observation->>'schema_version',contract.git_sha,
    contract.source_tree_oid,contract.source_tree_sha256,contract.source_tree_entry_count,
    contract.artifact_sha256,contract.config_sha256,contract.dependency_sha256,
    contract.prior_staging_project_id,contract.replacement_project_id,
    contract.replacement_branch_id,contract.replacement_endpoint_id,live_ledger,
    live_count,live_highest,live_digest,derived_synthetic_count,0,receipt_digest,
    receipt_ref,session_user,observed_at_value)
  returning id into receipt_uuid;
  return jsonb_build_object('contract_id',contract.id,'receipt_id',receipt_uuid,
    'evidence_ref',receipt_ref,'state','observed','replayed',false);
end $$;

create or replace function ops.read_staging_replacement_project_receipt(
  p_receipt_id uuid
) returns jsonb
language plpgsql security definer stable set search_path=pg_catalog,ops,public as $$
declare result jsonb;
begin
  if session_user<>'carr_jobs' and not (
       session_user='carr_program5_forward_fix_verifier'
       and pg_has_role(session_user,'carr_program5_forward_fix_verifiers','member')) then
    raise exception 'staging replacement receipt read requires a scoped session';
  end if;
  select jsonb_build_object(
      'contract_id',c.id,'receipt_id',r.id,'evidence_ref',r.evidence_ref,
      'receipt_sha256',r.receipt_sha256,'git_sha',r.git_sha,
      'source_tree_oid',r.source_tree_oid,'source_tree_sha256',r.source_tree_sha256,
      'source_tree_entry_count',r.source_tree_entry_count,
      'artifact_sha256',r.artifact_sha256,'config_sha256',r.config_sha256,
      'dependency_sha256',r.dependency_sha256,
      'prior_staging_project_id',r.prior_staging_project_id,
      'replacement_project_id',r.replacement_project_id,
      'replacement_branch_id',r.replacement_branch_id,
      'replacement_endpoint_id',r.replacement_endpoint_id,
      'live_migration_ledger',r.live_migration_ledger,
      'live_migration_count',r.live_migration_count,
      'live_migration_highest',r.live_migration_highest,
      'live_migration_ledger_sha256',r.live_migration_ledger_sha256,
      'synthetic_data_count',r.synthetic_data_count,
      'production_overlap_count',r.production_overlap_count,
      'observed_at',r.observed_at)
    into result
    from ops.staging_replacement_project_receipt r
    join ops.staging_replacement_project_contract c on c.id=r.contract_id
   where r.id=p_receipt_id;
  if result is null then raise exception 'staging replacement receipt not found'; end if;
  return result;
end $$;

revoke all on ops.staging_replacement_project_contract,
  ops.staging_replacement_project_receipt
  from public,carr_reader,carr_writer,carr_jobs,carr_authority,
       carr_program5_forward_fix_verifiers;
revoke all on function ops.prepare_staging_replacement_project(uuid,jsonb)
  from public,carr_reader,carr_writer,carr_authority,carr_program5_forward_fix_verifiers;
revoke all on function ops.record_staging_replacement_project(uuid,jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.read_staging_replacement_project_receipt(uuid)
  from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.prepare_staging_replacement_project(uuid,jsonb) to carr_jobs;
grant execute on function ops.record_staging_replacement_project(uuid,jsonb)
  to carr_program5_forward_fix_verifiers;
grant execute on function ops.read_staging_replacement_project_receipt(uuid)
  to carr_jobs,carr_program5_forward_fix_verifiers;

commit;

do $$
begin
  if has_table_privilege('carr_jobs','ops.staging_replacement_project_contract','insert')
     or has_table_privilege('carr_program5_forward_fix_verifiers',
                            'ops.staging_replacement_project_receipt','insert')
     or has_function_privilege('carr_jobs',
          'ops.record_staging_replacement_project(uuid,jsonb)'::regprocedure,'execute')
     or has_function_privilege('carr_writer',
          'ops.prepare_staging_replacement_project(uuid,jsonb)'::regprocedure,'execute')
     or not has_function_privilege('carr_jobs',
          'ops.prepare_staging_replacement_project(uuid,jsonb)'::regprocedure,'execute')
     or not has_function_privilege('carr_program5_forward_fix_verifiers',
          'ops.record_staging_replacement_project(uuid,jsonb)'::regprocedure,'execute')
     or not has_function_privilege('carr_jobs',
          'ops.read_staging_replacement_project_receipt(uuid)'::regprocedure,'execute') then
    raise exception '0322 FAILED: staging replacement authority boundary is wrong';
  end if;
end $$;
