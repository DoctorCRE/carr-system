-- WR130 forward correction: release-manifest.py emits canonical plan:<32 hex>.
-- 0540 used the unrelated Work Request sha256 plan format in two readiness
-- predicates, so a service-filed candidate could never become ready.
-- Keep 0540 immutable and preserve its authenticated service, exact provider,
-- evidence, typed recovery, and append-only admission checks verbatim.

alter table ops.release_readiness_receipt
  drop constraint release_readiness_receipt_plan_hash_check;
alter table ops.release_readiness_receipt
  add constraint release_readiness_receipt_plan_hash_check
  check (plan_hash ~ '^plan:[0-9a-f]{32}$');

create or replace function ops.qualify_program5_release(
  p_release_key text,p_plan_hash text,p_idempotency_key uuid
) returns jsonb language plpgsql security definer
  set search_path=pg_catalog,ops,public as $$
declare rel ops.release%rowtype; prior ops.release_readiness_receipt%rowtype;
  rehearsal uuid; stamp timestamptz; digest_value text; receipt_id uuid;
begin
  if session_user<>'carr_jobs' then
    raise exception 'release readiness requires the authenticated jobs service';
  end if;
  if p_idempotency_key is null or p_plan_hash is null
     or p_plan_hash !~ '^plan:[0-9a-f]{32}$' then
    raise exception 'invalid release readiness input';
  end if;
  select * into prior from ops.release_readiness_receipt
    where idempotency_key=p_idempotency_key;
  if found then
    select * into rel from ops.release where id=prior.release_id for update;
    if not found or prior.plan_hash is distinct from p_plan_hash
       or rel.release_key is distinct from p_release_key
       or rel.readiness_receipt_id is distinct from prior.id
       or rel.git_sha is distinct from prior.git_sha
       or rel.provider is distinct from prior.provider
       or rel.provider_version_id is distinct from prior.provider_version_id
       or rel.plan_hash is distinct from prior.plan_hash then
      raise exception 'readiness idempotency key was reused with changed input';
    end if;
    if rel.state='candidate' then
      rehearsal:=ops.program5_exact_recovery_rehearsal(
        rel.id,clock_timestamp()-interval '24 hours');
      if rehearsal is null or rehearsal=prior.recovery_run_id then
        raise exception 'reopened release has no new fresh exact typed recovery rehearsal';
      end if;
      update ops.release set state='ready' where id=rel.id;
      return jsonb_build_object('readiness_receipt_id',prior.id,
        'ready_at',prior.ready_at,'replayed',true,'refreshed',true,
        'current_recovery_run_id',rehearsal);
    end if;
    return jsonb_build_object('readiness_receipt_id',prior.id,
      'ready_at',prior.ready_at,'replayed',true,'refreshed',false);
  end if;
  select * into rel from ops.release where release_key=p_release_key for update;
  if not found or rel.environment is distinct from 'production'
     or rel.state is distinct from 'candidate'
     or rel.plan_hash is distinct from p_plan_hash
     or rel.provider is distinct from 'cloudflare-workers'
     or coalesce(rel.provider_version_id,'') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     or rel.maker_session_user is distinct from 'carr_jobs'
     or rel.maker_actor is distinct from 'carr_jobs'
     or rel.maker_authority_verified is distinct from false
     or rel.approved_by_actor is not null or rel.approval_receipt_id is not null
     or rel.artifact_digest is null or rel.dependency_lock_digest is null
     or rel.test_evidence_ref is null or rel.security_evidence_ref is null
     or rel.verifier_actor is null or rel.verifier_evidence_ref is null
     or rel.verifier_actor=rel.maker_actor or rel.rollback_ready is not true
     or rel.performance_budget_ref is null or coalesce(rel.performance_budget_ms,0)<=0
     or rel.recovery_strategy is null
     or rel.recovery_strategy not in ('rollback','forward_fix')
     or rel.rollback_plan_ref is null then
    raise exception 'release is not an independently verified exact Production candidate';
  end if;
  stamp:=clock_timestamp();
  rehearsal:=ops.program5_exact_recovery_rehearsal(rel.id,stamp-interval '24 hours');
  if rehearsal is null then raise exception 'release has no fresh exact typed recovery rehearsal'; end if;
  digest_value:='sha256:'||encode(public.digest(
    jsonb_build_object('release_id',rel.id,'git_sha',rel.git_sha,
      'provider',rel.provider,'provider_version_id',rel.provider_version_id,
      'plan_hash',rel.plan_hash,'recovery_run_id',rehearsal,
      'verifier_actor',rel.verifier_actor,
      'verifier_evidence_ref',rel.verifier_evidence_ref,
      'session_login',session_user,'ready_at',stamp)::text,'sha256'),'hex');
  insert into ops.release_readiness_receipt
    (idempotency_key,release_id,git_sha,provider,provider_version_id,plan_hash,
     recovery_run_id,verifier_actor,verifier_evidence_ref,session_login,ready_at,receipt_sha256)
  values(p_idempotency_key,rel.id,rel.git_sha,rel.provider,rel.provider_version_id,
    rel.plan_hash,rehearsal,rel.verifier_actor,rel.verifier_evidence_ref,
    session_user,stamp,digest_value) returning id into receipt_id;
  update ops.release set state='ready',ready_at=stamp,
    readiness_receipt_id=receipt_id where id=rel.id;
  return jsonb_build_object('readiness_receipt_id',receipt_id,
    'ready_at',stamp,'replayed',false);
end $$;
