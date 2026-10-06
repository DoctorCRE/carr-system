-- 0732_f01_login_bundle_membership.sql
--
-- Let the Worker's real logins reach the F01/J102 record doors, by bundle
-- MEMBERSHIP instead of by literal role name, without admitting anyone else.
--
-- WHY (2026-09-26, live outcome audit). ops.f01_context_actor_slug(), added in
-- 0626, accepted a non-authority session only when session_user was LITERALLY
-- 'carr_writer' or 'carr_reader'. Those are NOLOGIN bundle roles: nothing ever
-- authenticates as them. The deployed Worker authenticates as app_writer (a
-- carr_writer member) and app_reader (a carr_reader member), so every call from
-- them was refused with "f01_principal_refused: app_writer" (SQLSTATE 42501).
-- That blocked every function that resolves its actor through this helper:
-- the CRE transaction lifecycle (0704 j102_*), record source authority and
-- document identity (0626 f01_*), and the Salesforce reconciliation store
-- (0726 rw02_*). The database fixtures never saw it because they reach the
-- bundles with SET SESSION AUTHORIZATION carr_writer, which makes session_user
-- the bundle itself. 0571 fixed the same bug for Tours.
--
-- THE SAME DEFECT, ELSEWHERE. The live catalog was searched for session_user or
-- current_user compared with a bundle name. Two more writers refuse app_writer
-- in the same way: ops.register_execution_environment_provider and
-- ops.transition_proposed_eval_candidate (0344). Two F01 writers carry a
-- literal reader DENY (IF session_user = 'carr_reader' THEN refuse):
-- ops.f01_record_document and ops.f01_register_derivative_link. That deny never
-- fires for app_reader. Today app_reader cannot run either function anyway (no
-- EXECUTE for carr_reader). The deny is switched too, so it covers the real
-- reader login. Left alone on purpose: the carr_jobs checks, because carr_jobs
-- is itself a LOGIN role. ops.tour_server_actor_id, because 0571 already checks
-- membership. ops.scac_runtime_privilege_bundle, because the SIEP-18 monitor is
-- a shadow path and belongs to the sealed SCAC surface, not to this change.
--
-- WHY NOT A PLAIN pg_has_role() TEST. Membership alone would widen access.
-- The schema owner (neondb_owner) is a member of carr_writer, carr_reader and
-- carr_authority. A superuser is a member of everything. carr_exporter is a
-- member of carr_reader. Each of those was refused before and a bare membership
-- test would admit them. 0626 says F01 "never falls back to the schema owner".
-- So the classification is one helper, ops.login_bundle_principal(login):
--
--   * null for an unknown role or a superuser;
--   * null for any member of carr_authority, carr_jobs or carr_exporter
--     (authority logins are still matched by name first, exactly as before);
--   * 'carr_writer' for any other member of carr_writer;
--   * 'carr_reader' for any other member of carr_reader;
--   * null otherwise.
--
-- A bundle is a member of itself, so the fixtures that use SET SESSION
-- AUTHORIZATION carr_writer/carr_reader classify exactly as before.
--
-- WHAT DOES NOT CHANGE. No EXECUTE grant is added, revoked or moved. The helper
-- is SECURITY INVOKER with no EXECUTE for PUBLIC or any carr_ role, so only the
-- owner's definer functions can call it. It adds no secdef_execute row to the
-- SCAC catalog projection. The four in-place patches preserve owner, ACL,
-- SECURITY DEFINER, proconfig, signature and volatility, and this block proves
-- it. A reader still cannot write. The writers are not granted to carr_reader,
-- and the two reader denies now catch app_reader as well. The actor must still
-- be a bounded slug from carr.acting_actor_slug, which the Worker sets
-- server-side.

create or replace function ops.login_bundle_principal(p_login name)
returns text language sql stable security invoker
set search_path = pg_catalog, pg_temp
as $$
  select case
    when r.oid is null or r.rolsuper then null
    when pg_has_role(r.oid, 'carr_authority', 'MEMBER')
      or pg_has_role(r.oid, 'carr_jobs', 'MEMBER')
      or pg_has_role(r.oid, 'carr_exporter', 'MEMBER') then null
    when pg_has_role(r.oid, 'carr_writer', 'MEMBER') then 'carr_writer'
    when pg_has_role(r.oid, 'carr_reader', 'MEMBER') then 'carr_reader'
    else null end
  from (select 1) one
  left join pg_roles r on r.rolname = p_login
$$;

revoke all on function ops.login_bundle_principal(name) from public, carr_reader, carr_writer, carr_jobs, carr_authority, carr_exporter;

comment on function ops.login_bundle_principal(name) is
  'Classifies a login as the carr_writer or carr_reader runtime bundle by membership (0732). Null for superusers, the schema owner, authority/jobs/exporter members and non-members. Owner-only; never an EXECUTE grant.';

CREATE OR REPLACE FUNCTION ops.f01_context_actor_slug()
RETURNS text LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, ops, public
AS $$
DECLARE v_slug text; v_bundle text;
BEGIN
  IF session_user IN ('carr_authority_joe', 'carr_authority_dell') THEN
    IF to_regprocedure('ops.authority_actor_slug()') IS NULL THEN
      RAISE EXCEPTION 'f01_authority_actor_slug_missing: ops.authority_actor_slug() is not installed; F01 never falls back to the schema owner'
        USING ERRCODE = '42883';
    END IF;
    v_slug := ops.authority_actor_slug();
  ELSE
    -- 0732: the bundle by membership (app_writer, app_reader), never by the
    -- NOLOGIN bundle's literal name; see ops.login_bundle_principal.
    v_bundle := ops.login_bundle_principal(session_user);
    IF v_bundle = 'carr_writer' THEN
      v_slug := nullif(current_setting('carr.acting_actor_slug', true), '');
    ELSIF v_bundle = 'carr_reader' THEN
      v_slug := 'carr-reader';
    ELSE
      RAISE EXCEPTION 'f01_principal_refused: %', session_user USING ERRCODE = '42501';
    END IF;
  END IF;
  IF v_slug IS NULL OR v_slug !~ '^[a-z][a-z0-9-]{1,62}$' THEN
    RAISE EXCEPTION 'f01_no_authenticated_actor' USING ERRCODE = '28000';
  END IF;
  RETURN v_slug;
END;
$$;

-- The four in-place patches: 0537's pattern. Each old clause must occur exactly
-- once in the live definition, and the function's authority must be unchanged
-- afterwards.
do $f01_login_bundle_patch$
declare
  target record;
  before_proc pg_catalog.pg_proc%rowtype;
  after_proc pg_catalog.pg_proc%rowtype;
  definition text;
begin
  for target in
    select * from (values
      ('ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)',
       'IF session_user = ''carr_reader'' THEN',
       'IF ops.login_bundle_principal(session_user) = ''carr_reader'' THEN'),
      ('ops.f01_register_derivative_link(jsonb,text,text)',
       'IF session_user = ''carr_reader'' THEN',
       'IF ops.login_bundle_principal(session_user) = ''carr_reader'' THEN'),
      ('ops.register_execution_environment_provider(jsonb,uuid)',
       'elsif session_user = ''carr_writer'' then',
       'elsif ops.login_bundle_principal(session_user) = ''carr_writer'' then'),
      ('ops.transition_proposed_eval_candidate(text,text,text,jsonb,uuid)',
       'elsif session_user = ''carr_writer'' then',
       'elsif ops.login_bundle_principal(session_user) = ''carr_writer'' then')
    ) v(signature, old_clause, new_clause)
  loop
    if to_regprocedure(target.signature) is null then
      raise exception '0732: predecessor % is absent', target.signature;
    end if;
    select * into strict before_proc from pg_catalog.pg_proc
     where oid = to_regprocedure(target.signature)::oid;
    definition := pg_catalog.pg_get_functiondef(before_proc.oid);
    if before_proc.prosecdef is not true
       or (length(definition) - length(replace(definition, target.old_clause, '')))
          / length(target.old_clause) <> 1 then
      raise exception '0732: % body or security contract drifted', target.signature;
    end if;
    execute replace(definition, target.old_clause, target.new_clause);
    select * into strict after_proc from pg_catalog.pg_proc where oid = before_proc.oid;
    if (after_proc.proowner, after_proc.proacl, after_proc.prosecdef, after_proc.proconfig,
        after_proc.proargtypes, after_proc.prorettype, after_proc.provolatile, after_proc.proparallel)
       is distinct from
       (before_proc.proowner, before_proc.proacl, before_proc.prosecdef, before_proc.proconfig,
        before_proc.proargtypes, before_proc.prorettype, before_proc.provolatile, before_proc.proparallel) then
      raise exception '0732: % replacement changed function authority or signature', target.signature;
    end if;
  end loop;
end
$f01_login_bundle_patch$;

-- Proof, run wherever this applies (production included): the classification
-- admits exactly the runtime bundles and their plain login members, and no
-- patched function still compares session_user with a bundle's literal name.
do $f01_login_bundle_proof$
declare fn text;
begin
  if ops.login_bundle_principal('carr_writer') is distinct from 'carr_writer'
     or ops.login_bundle_principal('carr_reader') is distinct from 'carr_reader' then
    raise exception '0732 proof: the bundles themselves no longer classify';
  end if;
  if ops.login_bundle_principal('carr_jobs') is not null
     or ops.login_bundle_principal('carr_exporter') is not null
     or ops.login_bundle_principal('carr_authority') is not null
     or ops.login_bundle_principal('carr_0732_absent_role') is not null
     or ops.login_bundle_principal(current_user) is not null then
    raise exception '0732 proof: a jobs, exporter, authority, unknown or installing role classified as a runtime bundle';
  end if;
  if exists (select 1 from pg_roles where rolname = 'neondb_owner')
     and ops.login_bundle_principal('neondb_owner') is not null then
    raise exception '0732 proof: the schema owner classified as a runtime bundle';
  end if;
  if exists (select 1 from pg_roles where rolname in ('carr_authority_joe', 'carr_authority_dell')
              and ops.login_bundle_principal(rolname) is not null) then
    raise exception '0732 proof: an authority login classified as a runtime bundle';
  end if;
  if exists (select 1 from pg_roles where rolname = 'app_writer')
     and ops.login_bundle_principal('app_writer') is distinct from 'carr_writer' then
    raise exception '0732 proof: app_writer does not classify as carr_writer on this database';
  end if;
  if exists (select 1 from pg_roles where rolname = 'app_reader')
     and ops.login_bundle_principal('app_reader') is distinct from 'carr_reader' then
    raise exception '0732 proof: app_reader does not classify as carr_reader on this database';
  end if;
  if exists (select 1 from pg_proc where oid = 'ops.login_bundle_principal(name)'::regprocedure and prosecdef)
     or has_function_privilege('public', 'ops.login_bundle_principal(name)', 'EXECUTE')
     or exists (select 1 from pg_proc p cross join lateral aclexplode(p.proacl) a
                 join pg_roles g on g.oid = a.grantee
                where p.oid = 'ops.login_bundle_principal(name)'::regprocedure
                  and a.grantee <> p.proowner) then
    raise exception '0732 proof: the classifier is a definer or carries an EXECUTE grant';
  end if;
  foreach fn in array array[
    'ops.f01_context_actor_slug()',
    'ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)',
    'ops.f01_register_derivative_link(jsonb,text,text)',
    'ops.register_execution_environment_provider(jsonb,uuid)',
    'ops.transition_proposed_eval_candidate(text,text,text,jsonb,uuid)'
  ] loop
    if pg_get_functiondef(fn::regprocedure) ~* 'session_user\s*=\s*''carr_(writer|reader)'''
       or pg_get_functiondef(fn::regprocedure) !~ 'ops\.login_bundle_principal\(session_user\)' then
      raise exception '0732 proof: % still names a bundle literally or does not classify by membership', fn;
    end if;
  end loop;
end
$f01_login_bundle_proof$;
