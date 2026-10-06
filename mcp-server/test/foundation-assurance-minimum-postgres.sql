-- WR95 foundation evidence: real PostgreSQL proof of resumable candidate
-- binding. Every fixture row is rolled back.
\set ON_ERROR_STOP on

begin;

do $proof$
declare
  v_service uuid;
  v_release uuid;
  v_key text := 'wr95-postgres-proof-' || gen_random_uuid()::text;
  v_source text := repeat('a', 40);
  v_final uuid := gen_random_uuid();
  v_staging uuid := gen_random_uuid();
  v_idempotency uuid := gen_random_uuid();
  v_evidence_digest text := 'sha256:' || repeat('d', 64);
  v_evidence_ref text := 'safe:wr95-evidence/' || repeat('d', 64);
  v_benchmark_digest text := 'sha256:' || repeat('e', 64);
  v_evidence jsonb;
  v_seal jsonb;
  v_result jsonb;
begin
  select id into v_service from ops.service where key='carr-mcp';
  if v_service is null then
    insert into ops.service(key,name,owner_actor)
    values('carr-mcp','WR95 rollback-only service fixture','joe')
    returning id into v_service;
  end if;

  insert into ops.release(
    release_key,service_id,environment,state,git_sha,provider,
    provider_version_id,maker_actor,rollback_ready,source_kind,source_ref)
  values(v_key,v_service,'production','candidate',v_source,'cloudflare-workers',
    v_final,'joe',true,'wrapper','mcp-server/test/foundation-assurance-minimum-postgres.sql')
  returning id into v_release;

  if (select test_evidence_ref from ops.release where id=v_release) is not null then
    raise exception 'candidate unexpectedly began with evidence';
  end if;

  v_evidence:=jsonb_build_object(
    'schema_version','doctorcre-v5-foundation-assurance-evidence.v1',
    'source_sha',v_source,'source_tree',repeat('b',40),
    'staging_provider_version',v_staging::text,
    'final_provider_version',v_final::text,
    'release',jsonb_build_object('key',v_key,'provider_version',v_final::text,
      'source_sha',v_source,'test_evidence_ref',v_evidence_ref),
    'measurements',jsonb_build_object('benchmark_payload_digest',v_benchmark_digest));
  v_seal:=jsonb_build_object(
    'schema_version','doctorcre-v5-foundation-assurance-evidence-seal.v1',
    'evidence_digest',v_evidence_digest,'evidence_ref',v_evidence_ref,
    'benchmark_payload_digest',v_benchmark_digest,'source_sha',v_source,
    'final_provider_version',v_final::text);

  perform set_config('carr.acting_actor_slug','joe',true);
  perform set_config('carr.verified_human_actor_slug','joe',true);
  perform set_config('carr.receipt_session_ref','session:wr95-postgres-proof',true);
  v_result:=ops.foundation_assurance_store_evidence(v_idempotency,'{}'::jsonb,v_evidence,v_seal);
  if v_result->>'evidence_ref'<>v_evidence_ref or v_result->>'replayed'<>'false' then
    raise exception 'first evidence store returned wrong result: %',v_result;
  end if;
  if (select test_evidence_ref from ops.release where id=v_release)<>v_evidence_ref then
    raise exception 'candidate and evidence were not bound atomically';
  end if;
  if not exists(select 1 from ops.foundation_assurance_evidence
                where evidence_digest=v_evidence_digest and release_id=v_release) then
    raise exception 'immutable evidence row is absent';
  end if;

  v_result:=ops.foundation_assurance_store_evidence(v_idempotency,'{}'::jsonb,v_evidence,v_seal);
  if v_result->>'replayed'<>'true' then
    raise exception 'same idempotency did not replay';
  end if;

  begin
    perform ops.foundation_assurance_store_evidence(gen_random_uuid(),'{}'::jsonb,
      jsonb_set(v_evidence,'{release,key}',to_jsonb(v_key || '-wrong')),v_seal);
    raise exception 'wrong release key was accepted';
  exception when others then
    if sqlerrm='wrong release key was accepted' then raise; end if;
  end;
end
$proof$;

rollback;

\echo 'PASS: WR95 candidate evidence key binding is atomic and replay-safe'
