-- WR-000110 — T-CATALOG-DELTA and the F02-ADMISSION-OVERLAP ledger half, on a
-- disposable PostgreSQL. Run by the migration class of ops/ci.sh.
--
-- EVERY PROJECTION BELOW IS LIFTED BYTE FOR BYTE out of
-- migrations/0512_foundation_assurance_scac_successor.sql by a script. Retyping
-- a projection would mean asserting against an instrument this proof built for
-- itself, which is exactly how a measurement invents the answer it wanted.
--
-- The assertions are over ROWS, never over totals: a total that happens to match
-- proves nothing about which rows moved.

-- ---------------------------------------------------------------------------
-- T-CATALOG-DELTA, part one: 0517's own surface.
-- ---------------------------------------------------------------------------
do $wr110_catalog$
declare
  observed_rows text[];
  expected_rows text[];
  offending text[];
begin
    with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),
  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),
  observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select coalesce(array_agg(ingress_key order by ingress_key collate "C"), '{}')
    into observed_rows from observed
    where ingress_key like 'db-function-acl:ops.record_program_controller_fact(%';

  -- EXACTLY TWO ROWS, one per named grantee of the ONE privileged function.
  -- The category counts one row per (function, grantee) pair, so two grantees of
  -- one function is two rows -- not one row, and not two functions.
  if cardinality(observed_rows) <> 2 then
    raise exception 'WR-000110: the privileged writer has % secdef rows, expected exactly 2 (%)',
      cardinality(observed_rows), observed_rows;
  end if;
  if not (observed_rows::text like '%:carr_authority:execute%') then
    raise exception 'WR-000110: no carr_authority execute row for the privileged writer';
  end if;
  if not (observed_rows::text like '%:carr_writer:execute%') then
    raise exception 'WR-000110: no carr_writer execute row for the privileged writer';
  end if;
  -- AND NO PUBLIC ROW. acldefault() would have granted EXECUTE to public if the
  -- revoke had not preceded the grants, so this assertion is what proves the
  -- order in the migration.
  if observed_rows::text like '%:public:execute%' then
    raise exception 'WR-000110: the privileged writer is executable by public; the revoke did not precede the grants';
  end if;

    with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,acl.grantee,acl.privilege_type,acl.is_grantable from pg_class c join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select coalesce(array_agg(ingress_key order by ingress_key collate "C"), '{}')
    into offending from observed
    where ingress_key like 'db-relation-acl:ops.slice_source_lease:%'
       or ingress_key like 'db-relation-acl:ops.program_width_evidence:%'
       or ingress_key like 'db-relation-acl:ops.program_width_state:%'
       or ingress_key like 'db-relation-acl:ops.slice_checkpoint:%'
       or ingress_key like 'db-relation-acl:ops.release_receipt:%'
       or ingress_key like 'db-relation-acl:ops.program_origin_head_observation:%'
       or ingress_key like 'db-relation-acl:ops.program_controller_fact_ledger:%';
  -- The column-scoped SELECT grants create attacl entries carrying SELECT only,
  -- and neither the relation projection (INSERT/UPDATE/DELETE/TRUNCATE) nor the
  -- column projection (INSERT/UPDATE) admits SELECT. So both deltas are zero,
  -- and a SELECT grant counted as a write grant would show up right here.
  if cardinality(offending) <> 0 then
    raise exception 'WR-000110: a row-changing privilege reached a seam table: %', offending;
  end if;

    with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(a.attacl) acl where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0 and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select coalesce(array_agg(ingress_key order by ingress_key collate "C"), '{}')
    into offending from observed
    where ingress_key like 'db-column-acl:ops.slice_source_lease.%'
       or ingress_key like 'db-column-acl:ops.program_width_evidence.%'
       or ingress_key like 'db-column-acl:ops.program_width_state.%'
       or ingress_key like 'db-column-acl:ops.slice_checkpoint.%'
       or ingress_key like 'db-column-acl:ops.release_receipt.%'
       or ingress_key like 'db-column-acl:ops.program_origin_head_observation.%'
       or ingress_key like 'db-column-acl:ops.program_controller_fact_ledger.%';
  if cardinality(offending) <> 0 then
    raise exception 'WR-000110: a column write privilege reached a seam table: %', offending;
  end if;

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
  select coalesce(array_agg(ingress_key order by ingress_key collate "C"), '{}')
    into offending from observed
    where ingress_key like 'db-relation-owner:ops.slice_source_lease:%'
       or ingress_key like 'db-relation-owner:ops.program_width_evidence:%'
       or ingress_key like 'db-relation-owner:ops.program_width_state:%'
       or ingress_key like 'db-relation-owner:ops.slice_checkpoint:%'
       or ingress_key like 'db-relation-owner:ops.release_receipt:%'
       or ingress_key like 'db-relation-owner:ops.program_origin_head_observation:%'
       or ingress_key like 'db-relation-owner:ops.program_controller_fact_ledger:%'
       or ingress_key like 'db-function-owner:ops.record_program_controller_fact(%';
  -- The seam tables and the writer are owned by the migration's own owner, not
  -- by a non-login carr_* role, so they do not enter the connected closure. If
  -- this cluster happens to own them as a carr_* role the rows appear here and
  -- the measured role_authority value moves -- which is exactly why it is
  -- MEASURED rather than assumed.
  if cardinality(offending) <> 0 then
    raise exception 'WR-000110: a seam object is owned inside the carr_* closure: %', offending;
  end if;

  expected_rows := observed_rows;  -- keep the lifted variable live for the reader
end $wr110_catalog$;

-- ---------------------------------------------------------------------------
-- T-CATALOG-DELTA, part two: the v29 successor's own +4 -- ONE registration
-- function granted to FOUR roles, and ZERO rows for the seal and catalog
-- functions it also installs.
-- ---------------------------------------------------------------------------
do $wr110_successor_catalog$
declare
  registration_rows text[];
  quiet_rows text[];
begin
  if to_regprocedure('ops.scac_mutation_registration_v29(text,text)') is null then
    raise exception 'WR-000110: the v29 registration function is absent; 0518 did not load';
  end if;

    with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),
  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),
  observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select coalesce(array_agg(ingress_key order by ingress_key collate "C"), '{}')
    into registration_rows from observed
    where ingress_key like 'db-function-acl:ops.scac_mutation_registration_v29(%';
  if cardinality(registration_rows) <> 4 then
    raise exception 'WR-000110: the v29 registration function has % secdef rows, expected exactly 4 grantees (%)',
      cardinality(registration_rows), registration_rows;
  end if;

    with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),
  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),
  observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select coalesce(array_agg(ingress_key order by ingress_key collate "C"), '{}')
    into quiet_rows from observed
    where ingress_key like 'db-function-acl:ops.scac_mutation_catalog_v29%'
       or ingress_key like 'db-function-acl:ops.scac_mutation_registry_v29%';
  if cardinality(quiet_rows) <> 0 then
    raise exception 'WR-000110: a v29 seal or catalog function carries an ACL row: %', quiet_rows;
  end if;

  -- THE SEAL, NOT THE LIVE CATALOG. This asserted ops.scac_mutation_catalog_v29_current()
  -- until WR-000111/112/113 installed v30, which renames that predicate to
  -- _live_at_seal and makes it false BY DESIGN -- exactly what v29 did to v28.
  -- A proof pinned to "my generation is still the frontier" has to be rewritten
  -- by every successor; the durable claim is that 0518's own seal is intact and
  -- still validates its entry set, which stays true for every generation after.
  if not ops.scac_mutation_registry_v29_seal_available() then
    raise exception 'WR-000110: the v29 registry seal is absent or no longer validates its entry set';
  end if;
end $wr110_successor_catalog$;

-- ---------------------------------------------------------------------------
-- F02-LEASE-ROW and F02-ADMISSION-OVERLAP, the ledger half: the refusal row is
-- there AFTER the refusing call RETURNED, under that call's own key.
--
-- THE PRINCIPAL IS ESTABLISHED HERE rather than inherited. The migration class
-- runs its gates alphabetically on one shared cluster, so a proof that asserted
-- an authority privilege on whatever roles the previous gate left behind would
-- be asserting under conditions it cannot see. This creates the authority login
-- it needs, uses it, and hands the session back.
-- ---------------------------------------------------------------------------
do $wr110_principal$
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_authority_joe') then
    create role carr_authority_joe login;
  end if;
end $wr110_principal$;
grant carr_authority to carr_authority_joe;

set session authorization carr_authority_joe;

do $wr110_writes$
declare
  key uuid := '0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f';
  lease_key uuid := '0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f10';
  result jsonb;
  again jsonb;
begin
  -- Every fact is minted through the ONE privileged writer. Nothing here writes
  -- a table directly, because nothing can: no runtime role holds row-changing
  -- privilege on any of the seven.
  result := ops.record_program_controller_fact('slice_source_lease', lease_key, jsonb_build_object(
    'slice_ref','slice:wr110-a','worktree_ref','worktree:wr110-a',
    'worktree_path','/tmp/wr110-a','branch_ref','branch:wr110-a',
    'base_commit_sha', repeat('a',40), 'source_paths', jsonb_build_array('mcp-server/src/a.js'),
    'database_disposition','schema_only_fixture','reuse_disposition','extend',
    'model_roles', jsonb_build_array('author'), 'held_by_actor','joe'));
  if result->>'id' is null then raise exception 'WR-000110: the lease was not recorded'; end if;

  -- IDEMPOTENT REPLAY: the same key returns the SAME row id, never a second row.
  again := ops.record_program_controller_fact('slice_source_lease', lease_key, '{}'::jsonb);
  if again->>'id' is distinct from result->>'id' then
    raise exception 'WR-000110: a replay minted a new lease id';
  end if;

  -- A DIFFERENT KIND under the SAME key is refused.
  begin
    perform ops.record_program_controller_fact('program_width_state', lease_key, jsonb_build_object(
      'program_ref','wr:1','current_width',1,'requested_width',1));
    raise exception 'WR-000110: a reused key admitted a second fact kind';
  exception when others then
    if sqlerrm not like '%idempotency key was reused%' then raise; end if;
  end;

  -- ONE LIVE LEASE PER SLICE, enforced by the partial unique index rather than
  -- by the caller remembering to check.
  begin
    perform ops.record_program_controller_fact('slice_source_lease',
      '0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f11'::uuid, jsonb_build_object(
      'slice_ref','slice:wr110-a','worktree_ref','worktree:wr110-c',
      'worktree_path','/tmp/wr110-c','branch_ref','branch:wr110-c',
      'base_commit_sha', repeat('a',40), 'source_paths', jsonb_build_array('ops/ci.sh'),
      'database_disposition','no_database','reuse_disposition','reuse',
      'model_roles', jsonb_build_array(), 'held_by_actor','joe'));
    raise exception 'WR-000110: a slice holds two live source leases at once';
  exception when unique_violation then
    null;
  end;

  -- A release receipt whose reference is not the digest of its own body is
  -- refused, which is what makes a recorded receipt content addressed.
  begin
    perform ops.record_program_controller_fact('release_receipt',
      '0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f12'::uuid, jsonb_build_object(
      'release_ref','release:wr110','receipt_kind','head_observation',
      'receipt_ref','receipt:sha256:' || repeat('c',64),
      'body', jsonb_build_object('kind','head_observation','slice_ref','slice:wr110-a',
        'head_sha', repeat('a',40), 'observed_head_sha', repeat('a',40))));
    raise exception 'WR-000110: a forged receipt reference was accepted';
  exception when others then
    if sqlerrm not like '%not the digest of its own canonical body%' then raise; end if;
  end;

  -- The refusal the admission door records, under the refusing call's own key.
  result := ops.record_program_controller_fact('admission_refusal', key, jsonb_build_object(
    'slice_ref','slice:wr110-b','reason_id','source_path_overlap_denied',
    'blocking_check','source_path_overlap','decided_at', now()::text,
    'decision_digest','sha256:' || repeat('b',64)));
  if result->>'ok' <> 'true' then raise exception 'WR-000110: the refusal was not recorded'; end if;
end $wr110_writes$;

reset session authorization;

do $wr110_readback$
declare
  key uuid := '0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f';
  lease_key uuid := '0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f10';
  reason text;
  live integer;
  lease_id uuid;
begin
  select count(*) into live from ops.slice_source_lease where released_at is null
    and slice_ref = 'slice:wr110-a';
  if live <> 1 then
    raise exception 'WR-000110: expected exactly one live lease for the slice, found %', live;
  end if;

  -- The refusal row exists AFTER the refusing call returned, under that call's
  -- own key, carrying the evaluator's own reason identifier.
  select result->>'reason_id' into reason from ops.program_controller_fact_ledger
    where idempotency_key = key and fact_kind = 'admission_refusal';
  if reason is distinct from 'source_path_overlap_denied' then
    raise exception 'WR-000110: the ledger row does not carry the evaluator''s own reason (got %)',
      coalesce(reason, '<no row>');
  end if;

  -- Everything above is a proof, not a fixture: leave the database as found.
  select target_id into lease_id from ops.program_controller_fact_ledger
    where idempotency_key = lease_key;
  delete from ops.program_controller_fact_ledger where idempotency_key in (key, lease_key);
  delete from ops.slice_source_lease where id = lease_id;
end $wr110_readback$;
