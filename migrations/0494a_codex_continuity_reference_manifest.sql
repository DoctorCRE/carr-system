-- Candidate only: release sequencing owns the migration ordinal and execution.
-- Empty manifests preserve legacy physical revisions and their v1 digests.
alter table codex_continuity_checkpoint
  add column reference_manifest jsonb not null default '{}'::jsonb
  check (jsonb_typeof(reference_manifest) = 'object'
         and octet_length(reference_manifest::text) <= 128000);

alter table codex_continuity_revision
  add column reference_manifest jsonb not null default '{}'::jsonb
  check (jsonb_typeof(reference_manifest) = 'object'
         and octet_length(reference_manifest::text) <= 128000);

-- The approved 0494a filename is a legal migration-contract interstitial.
-- Earlier rollback/readback code still spelled the older four-digit-only
-- grammar even though the 0315 forward-fix path already accepts one optional
-- suffix letter. Keep all identities, privileges, and evidence rules intact;
-- widen only this filename grammar at the live 0494a boundary.
do $codex_continuity_0494a_compatibility$
declare
  old_grammar constant text := '^[0-9]{4}_[a-z0-9_.-]+\.sql$';
  new_grammar constant text := '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$';
  prepare_id constant regprocedure :=
    'ops.prepare_staging_deployment_attempt(uuid,uuid,text,text,uuid,text,text)'::regprocedure;
  program6_id constant regprocedure :=
    'ops.record_staging_release_readback_program6(uuid,uuid,text,integer,text,integer,bigint,boolean)'::regprocedure;
  wrapper_id constant regprocedure :=
    'ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean)'::regprocedure;
  legacy_id constant regprocedure :=
    'ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)'::regprocedure;
  restore_id constant regprocedure :=
    'ops.prepare_staging_restore_only_attempt(uuid,uuid,text,text,uuid,text)'::regprocedure;
  prepare_definition text;
  program6_definition text;
  wrapper_definition text;
  legacy_definition text;
  restore_definition text;
  changed_prepare text;
  changed_program6 text;
  changed_legacy text;
  changed_restore text;
  prepare_owner oid;
  prepare_acl aclitem[];
  prepare_secdef boolean;
  prepare_config bytea;
  prepare_volatile "char";
  prepare_parallel "char";
  program6_owner oid;
  program6_acl aclitem[];
  program6_secdef boolean;
  program6_config bytea;
  program6_volatile "char";
  program6_parallel "char";
  legacy_owner oid;
  legacy_acl aclitem[];
  legacy_secdef boolean;
  legacy_config bytea;
  legacy_volatile "char";
  legacy_parallel "char";
  restore_owner oid;
  restore_acl aclitem[];
  restore_secdef boolean;
  restore_config bytea;
  restore_volatile "char";
  restore_parallel "char";
begin
  if (select count(*)
      from pg_constraint
      where conrelid='ops.staging_deployment_attempt'::regclass
        and conname='staging_deployment_attempt_declared_schema_highest_migrat_check'
        and pg_get_constraintdef(oid) =
          'CHECK ((declared_schema_highest_migration ~ ''^[0-9]{4}_[a-z0-9_.-]+\.sql$''::text))') <> 1
     or (select count(*)
         from pg_constraint
         where conrelid='ops.staging_release_readback_receipt'::regclass
           and conname='staging_release_readback_receipt_schema_highest_migration_check'
           and pg_get_constraintdef(oid) =
             'CHECK ((schema_highest_migration ~ ''^[0-9]{4}_[a-z0-9_.-]+\.sql$''::text))') <> 1
     or (select count(*)
         from pg_constraint
         where conrelid='ops.staging_restore_only_attempt'::regclass
           and conname='staging_restore_only_attempt_declared_schema_highest_migr_check'
           and pg_get_constraintdef(oid) =
             'CHECK ((declared_schema_highest_migration ~ ''^[0-9]{4}_[a-z0-9_.-]+\.sql$''::text))') <> 1 then
    raise exception '0494a compatibility preimage has unexpected rollback/readback constraints';
  end if;

  select pg_get_functiondef(prepare_id),proowner,proacl,prosecdef,convert_to(coalesce(array_to_string(proconfig,chr(31)),''),'UTF8'),provolatile,proparallel
    into prepare_definition,prepare_owner,prepare_acl,prepare_secdef,prepare_config,prepare_volatile,prepare_parallel
    from pg_proc where oid=prepare_id;
  select pg_get_functiondef(program6_id),proowner,proacl,prosecdef,convert_to(coalesce(array_to_string(proconfig,chr(31)),''),'UTF8'),provolatile,proparallel
    into program6_definition,program6_owner,program6_acl,program6_secdef,program6_config,program6_volatile,program6_parallel
    from pg_proc where oid=program6_id;
  select pg_get_functiondef(wrapper_id) into wrapper_definition from pg_proc where oid=wrapper_id;
  select pg_get_functiondef(legacy_id),proowner,proacl,prosecdef,convert_to(coalesce(array_to_string(proconfig,chr(31)),''),'UTF8'),provolatile,proparallel
    into legacy_definition,legacy_owner,legacy_acl,legacy_secdef,legacy_config,legacy_volatile,legacy_parallel
    from pg_proc where oid=legacy_id;
  select pg_get_functiondef(restore_id),proowner,proacl,prosecdef,convert_to(coalesce(array_to_string(proconfig,chr(31)),''),'UTF8'),provolatile,proparallel
    into restore_definition,restore_owner,restore_acl,restore_secdef,restore_config,restore_volatile,restore_parallel
    from pg_proc where oid=restore_id;
  if prepare_definition is null or program6_definition is null or wrapper_definition is null or legacy_definition is null or restore_definition is null
     or length(prepare_definition)-length(replace(prepare_definition,old_grammar,'')) <> length(old_grammar)
     or length(program6_definition)-length(replace(program6_definition,old_grammar,'')) <> length(old_grammar)
     or length(legacy_definition)-length(replace(legacy_definition,old_grammar,'')) <> length(old_grammar)
     or length(restore_definition)-length(replace(restore_definition,old_grammar,'')) <> length(old_grammar)
     or position('SECURITY DEFINER' in upper(prepare_definition))=0
     or position('SECURITY DEFINER' in upper(program6_definition))=0
     or position('SECURITY DEFINER' in upper(legacy_definition))=0
     or position('SECURITY DEFINER' in upper(restore_definition))=0
     or position('record_staging_release_readback_program6' in wrapper_definition)=0
     or position('Program 6 recorder cannot replay a legacy NULL-posture receipt' in wrapper_definition)=0 then
    raise exception '0494a compatibility preimage has unexpected rollback/readback functions';
  end if;

  -- Fixed regprocedure IDs, one literal replacement each: this preserves the
  -- complete currently-installed 0202/0218/0222/0297 bodies instead of replaying history.
  changed_prepare:=replace(prepare_definition,old_grammar,new_grammar);
  changed_program6:=replace(program6_definition,old_grammar,new_grammar);
  changed_legacy:=replace(legacy_definition,old_grammar,new_grammar);
  changed_restore:=replace(restore_definition,old_grammar,new_grammar);
  execute changed_prepare;
  execute changed_program6;
  execute changed_legacy;
  execute changed_restore;

  if convert_to(pg_get_functiondef(prepare_id),'UTF8') is distinct from convert_to(changed_prepare,'UTF8')
     or convert_to(pg_get_functiondef(program6_id),'UTF8') is distinct from convert_to(changed_program6,'UTF8')
     or convert_to(pg_get_functiondef(legacy_id),'UTF8') is distinct from convert_to(changed_legacy,'UTF8')
     or convert_to(pg_get_functiondef(restore_id),'UTF8') is distinct from convert_to(changed_restore,'UTF8')
     or (select (proowner,proacl,prosecdef,convert_to(coalesce(array_to_string(proconfig,chr(31)),''),'UTF8'),provolatile,proparallel)
         from pg_proc where oid=prepare_id) is distinct from
        (prepare_owner,prepare_acl,prepare_secdef,prepare_config,prepare_volatile,prepare_parallel)
     or (select (proowner,proacl,prosecdef,convert_to(coalesce(array_to_string(proconfig,chr(31)),''),'UTF8'),provolatile,proparallel)
         from pg_proc where oid=program6_id) is distinct from
        (program6_owner,program6_acl,program6_secdef,program6_config,program6_volatile,program6_parallel)
     or (select (proowner,proacl,prosecdef,convert_to(coalesce(array_to_string(proconfig,chr(31)),''),'UTF8'),provolatile,proparallel)
         from pg_proc where oid=legacy_id) is distinct from
        (legacy_owner,legacy_acl,legacy_secdef,legacy_config,legacy_volatile,legacy_parallel)
     or (select (proowner,proacl,prosecdef,convert_to(coalesce(array_to_string(proconfig,chr(31)),''),'UTF8'),provolatile,proparallel)
         from pg_proc where oid=restore_id) is distinct from
        (restore_owner,restore_acl,restore_secdef,restore_config,restore_volatile,restore_parallel)
     or convert_to(pg_get_functiondef(wrapper_id),'UTF8') is distinct from convert_to(wrapper_definition,'UTF8') then
    raise exception '0494a compatibility replacement changed rollback/readback attributes';
  end if;
end
$codex_continuity_0494a_compatibility$;

alter table ops.staging_deployment_attempt
  drop constraint staging_deployment_attempt_declared_schema_highest_migrat_check,
  add constraint staging_deployment_attempt_declared_schema_highest_migrat_check
    check (declared_schema_highest_migration ~ '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$');

alter table ops.staging_release_readback_receipt
  drop constraint staging_release_readback_receipt_schema_highest_migration_check,
  add constraint staging_release_readback_receipt_schema_highest_migration_check
    check (schema_highest_migration ~ '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$');

alter table ops.staging_restore_only_attempt
  drop constraint staging_restore_only_attempt_declared_schema_highest_migr_check,
  add constraint staging_restore_only_attempt_declared_schema_highest_migr_check
    check (declared_schema_highest_migration ~ '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$');
