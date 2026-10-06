-- WR-000095: authenticated Foundation/Assurance evidence and minimum outcome.
--
-- This migration supplies the record-layer half of the nine zero-input Worker
-- producers. The caller-facing verbs accept only an idempotency key. Evidence,
-- identities, the accepted benchmark, the serving release, time, comparator
-- inputs and all predecessor receipts are read by the Worker and this database.
-- The dedicated LOGIN is created passwordless; staging tooling and Joe's
-- separately approved Production act provision its credential later.

do $wr95_preflight$
begin
  if not exists (select 1 from public.schema_migrations
    where filename = '0510_journey_one_clock_input_store.sql'
      and sha256 = 'bd6e8332f07ec128ef7f56a2d69165192d1b742c1b8e7ddbdd1615496de48703') then
    raise exception '0511 requires the exact 0510 Journey One clock input store';
  end if;
  if to_regprocedure('ops.benchmark_payload_preimage(uuid)') is null
     or to_regprocedure('ops.j1_clock_scope_digest(jsonb)') is null
     or to_regprocedure('ops.j1_minimum_receipt_digest(jsonb)') is null then
    raise exception '0511 requires migrations 0508 through 0510';
  end if;
end $wr95_preflight$;

-- -------------------------------------------------------------------------
-- Closed actors and the one family-level database seat.
-- -------------------------------------------------------------------------
insert into public.actor(slug, kind, display_name, active) values
  ('codex-benchmark-author', 'automation', 'Codex Benchmark Author', true),
  ('codex-benchmark-reviewer', 'automation', 'Codex Benchmark Reviewer', true),
  ('codex-fa-coverage', 'automation', 'Codex Foundation Assurance Coverage Oracle', true),
  ('codex-fa-assurance', 'automation', 'Codex Assurance Fabric Oracle', true),
  ('codex-fa-foundation', 'automation', 'Codex Foundation Control Plane Oracle', true),
  ('codex-fa-execution', 'automation', 'Codex Global Execution Oracle', true),
  ('codex-fa-phi', 'automation', 'Codex Global No-PHI Oracle', true),
  ('codex-fa-prompt', 'automation', 'Codex Global Prompt Boundary Oracle', true),
  ('codex-fa-secrets', 'automation', 'Codex Global Secrets Boundary Oracle', true),
  ('codex-fa-source', 'automation', 'Codex Global Source Authority Oracle', true),
  ('codex-fa-minimum', 'automation', 'Codex Foundation Assurance Minimum Oracle', true)
on conflict (slug) do nothing;

do $wr95_actor_shape$
declare v_slug text;
begin
  foreach v_slug in array array[
    'codex-benchmark-author','codex-benchmark-reviewer','codex-fa-coverage',
    'codex-fa-assurance','codex-fa-foundation','codex-fa-execution','codex-fa-phi',
    'codex-fa-prompt','codex-fa-secrets','codex-fa-source','codex-fa-minimum'
  ] loop
    if not exists (select 1 from public.actor
      where slug=v_slug and kind='automation' and active) then
      raise exception '0511 requires active automation actor %', v_slug;
    end if;
  end loop;
end $wr95_actor_shape$;

do $wr95_role$
begin
  if not exists (select 1 from pg_roles where rolname='carr_foundation_assurance_oracle') then
    create role carr_foundation_assurance_oracle login;
  elsif exists (select 1 from pg_roles where rolname='carr_foundation_assurance_oracle'
    and (not rolcanlogin or rolsuper or rolcreaterole or rolcreatedb or rolreplication or rolbypassrls)) then
    raise exception 'carr_foundation_assurance_oracle must be a plain LOGIN role';
  end if;
end $wr95_role$;
grant usage on schema public, ops to carr_foundation_assurance_oracle;

create or replace function ops.foundation_assurance_login_role()
returns text language sql immutable set search_path=pg_catalog
as $$ select 'carr_foundation_assurance_oracle'::text $$;

create or replace function ops.foundation_assurance_benchmark_ref()
returns text language sql immutable set search_path=pg_catalog
as $$ select 'doctorcre-v5-foundation-assurance-production'::text $$;

create or replace function ops.foundation_assurance_expected_actor(p_verb text)
returns text language sql immutable strict set search_path=pg_catalog
as $$ select case p_verb
  when 'produce-foundation-assurance-benchmark-coverage' then 'codex-fa-coverage'
  when 'produce-assurance-fabric-preactivation-receipt' then 'codex-fa-assurance'
  when 'produce-foundation-control-plane-preactivation-receipt' then 'codex-fa-foundation'
  when 'produce-global-execution-contract-receipt' then 'codex-fa-execution'
  when 'produce-global-no-phi-boundary-receipt' then 'codex-fa-phi'
  when 'produce-global-prompt-injection-boundary-receipt' then 'codex-fa-prompt'
  when 'produce-global-secrets-boundary-receipt' then 'codex-fa-secrets'
  when 'produce-global-source-authority-receipt' then 'codex-fa-source'
  when 'record-foundation-assurance-minimum-outcome' then 'codex-fa-minimum'
end $$;

create or replace function ops.foundation_assurance_expected_step(p_verb text)
returns text language sql immutable strict set search_path=pg_catalog
as $$ select case p_verb
  when 'produce-assurance-fabric-preactivation-receipt' then 'step:assurance-fabric-preactivation-contract-receipt'
  when 'produce-foundation-control-plane-preactivation-receipt' then 'step:foundation-control-plane-preactivation-contract-receipt'
  when 'produce-global-execution-contract-receipt' then 'step:global-execution-contract-independent-receipt'
  when 'produce-global-no-phi-boundary-receipt' then 'step:global-no-phi-boundary-independent-receipt'
  when 'produce-global-prompt-injection-boundary-receipt' then 'step:global-prompt-injection-boundary-independent-receipt'
  when 'produce-global-secrets-boundary-receipt' then 'step:global-secrets-boundary-independent-receipt'
  when 'produce-global-source-authority-receipt' then 'step:global-source-authority-independent-receipt'
end $$;

create or replace function ops.foundation_assurance_expected_comparator(p_verb text)
returns text language sql immutable strict set search_path=pg_catalog
as $$ select case p_verb
  when 'produce-assurance-fabric-preactivation-receipt' then 'assurance-fabric-preactivation'
  when 'produce-foundation-control-plane-preactivation-receipt' then 'foundation-control-plane-preactivation'
  when 'produce-global-execution-contract-receipt' then 'global-execution-contract'
  when 'produce-global-no-phi-boundary-receipt' then 'global-no-phi-boundary'
  when 'produce-global-prompt-injection-boundary-receipt' then 'global-prompt-injection-boundary'
  when 'produce-global-secrets-boundary-receipt' then 'global-secrets-boundary'
  when 'produce-global-source-authority-receipt' then 'global-source-authority'
end $$;

create or replace function ops.foundation_assurance_identity_actor(p_identity jsonb)
returns uuid language plpgsql stable set search_path=pg_catalog,public
as $$
declare v_slug text; v_id uuid;
begin
  if jsonb_typeof(p_identity) is distinct from 'object'
     or (select count(*) from jsonb_object_keys(p_identity)) <> 3
     or jsonb_typeof(p_identity->'actor_id') is distinct from 'string'
     or jsonb_typeof(p_identity->'session_ref') is distinct from 'string'
     or jsonb_typeof(p_identity->'authority_class') is distinct from 'string'
     or p_identity->>'authority_class' <> 'review_agent'
     or p_identity->>'session_ref' !~ '^session:[-A-Za-z0-9:._/]+$'
     or char_length(p_identity->>'session_ref') not between 17 and 300 then
    raise exception 'foundation assurance requires an exact authenticated-receipt-identity.v1 review seat';
  end if;
  v_slug := p_identity->>'actor_id';
  select id into v_id from public.actor where slug=v_slug and kind='automation' and active;
  if not found then raise exception 'foundation assurance actor % is unavailable', v_slug; end if;
  return v_id;
end $$;

create or replace function ops.foundation_assurance_require_seat(p_verb text, p_identity jsonb)
returns uuid language plpgsql stable set search_path=pg_catalog,ops,public
as $$
declare v_expected text; v_actor uuid;
begin
  if session_user is distinct from ops.foundation_assurance_login_role() then
    raise exception 'foundation assurance production requires the dedicated oracle connection';
  end if;
  v_expected := ops.foundation_assurance_expected_actor(p_verb);
  if v_expected is null or p_identity->>'actor_id' is distinct from v_expected then
    raise exception 'foundation assurance verb % requires actor %', p_verb, coalesce(v_expected,'(unknown)');
  end if;
  v_actor := ops.foundation_assurance_identity_actor(p_identity);
  return v_actor;
end $$;

-- -------------------------------------------------------------------------
-- Session provenance for the author/reviewer/acceptor chain.
-- -------------------------------------------------------------------------
alter table ops.benchmark_manifest_draft
  add column if not exists proposed_by_session_ref text;
alter table ops.benchmark_manifest_review
  add column if not exists reviewer_session_ref text;
alter table ops.benchmark_manifest_acceptance_receipt
  add column if not exists accepted_by_session_ref text;

create or replace function ops.foundation_assurance_benchmark_session_guard()
returns trigger language plpgsql set search_path=pg_catalog,ops,public
as $$
declare v_ref text; v_slug text; v_session text; v_actor uuid;
        v_author uuid; v_author_session text; v_reviewer uuid; v_reviewer_session text;
begin
  if tg_table_name='benchmark_manifest_draft' then
    v_ref:=new.benchmark_ref; v_actor:=new.proposed_by_actor_id;
  elsif tg_table_name='benchmark_manifest_review' then
    select d.benchmark_ref,d.proposed_by_actor_id,d.proposed_by_session_ref
      into v_ref,v_author,v_author_session from ops.benchmark_manifest_draft d where d.id=new.draft_id;
    v_actor:=new.reviewer_actor_id;
  else
    select d.benchmark_ref,d.proposed_by_actor_id,d.proposed_by_session_ref,
           r.reviewer_actor_id,r.reviewer_session_ref
      into v_ref,v_author,v_author_session,v_reviewer,v_reviewer_session
      from ops.benchmark_manifest_draft d join ops.benchmark_manifest_review r on r.id=new.review_id
     where d.id=new.draft_id;
    v_actor:=new.accepted_by_actor_id;
  end if;
  if v_ref is distinct from ops.foundation_assurance_benchmark_ref() then return new; end if;
  v_session:=nullif(current_setting('carr.receipt_session_ref',true),'');
  select slug into v_slug from public.actor where id=v_actor and active;
  if v_session is null then raise exception 'WR95 benchmark act requires an authenticated session reference'; end if;
  if tg_table_name='benchmark_manifest_draft' then
    if v_slug<>'codex-benchmark-author' then raise exception 'WR95 benchmark draft requires codex-benchmark-author'; end if;
    new.proposed_by_session_ref:=v_session;
  elsif tg_table_name='benchmark_manifest_review' then
    if v_slug<>'codex-benchmark-reviewer' then raise exception 'WR95 benchmark review requires codex-benchmark-reviewer'; end if;
    if v_actor=v_author or v_session=v_author_session then raise exception 'WR95 benchmark review must be independent of its author'; end if;
    new.reviewer_session_ref:=v_session;
  else
    if v_slug<>'joe' then raise exception 'WR95 benchmark acceptance is Joe-only'; end if;
    if v_actor in (v_author,v_reviewer) or v_session in (v_author_session,v_reviewer_session) then
      raise exception 'WR95 benchmark acceptance must be independent of author and reviewer';
    end if;
    new.accepted_by_session_ref:=v_session;
  end if;
  return new;
end $$;

drop trigger if exists foundation_assurance_benchmark_draft_session on ops.benchmark_manifest_draft;
create trigger foundation_assurance_benchmark_draft_session before insert on ops.benchmark_manifest_draft
for each row execute function ops.foundation_assurance_benchmark_session_guard();
drop trigger if exists foundation_assurance_benchmark_review_session on ops.benchmark_manifest_review;
create trigger foundation_assurance_benchmark_review_session before insert on ops.benchmark_manifest_review
for each row execute function ops.foundation_assurance_benchmark_session_guard();
drop trigger if exists foundation_assurance_benchmark_acceptance_session on ops.benchmark_manifest_acceptance_receipt;
create trigger foundation_assurance_benchmark_acceptance_session before insert on ops.benchmark_manifest_acceptance_receipt
for each row execute function ops.foundation_assurance_benchmark_session_guard();

-- -------------------------------------------------------------------------
-- Server-acquired evidence and immutable producer rows.
-- -------------------------------------------------------------------------
create table ops.foundation_assurance_evidence (
  evidence_digest text primary key check(evidence_digest ~ '^sha256:[0-9a-f]{64}$'),
  evidence_ref text not null unique check(evidence_ref ~ '^safe:wr95-evidence/[0-9a-f]{64}$'),
  benchmark_payload_digest text not null check(benchmark_payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  source_sha text not null check(source_sha ~ '^[0-9a-f]{40}$'),
  source_tree text not null check(source_tree ~ '^[0-9a-f]{40}$'),
  staging_provider_version uuid not null,
  final_provider_version uuid not null,
  release_id uuid not null references ops.release(id),
  config jsonb not null,
  evidence jsonb not null,
  seal jsonb not null,
  idempotency_key uuid not null unique,
  stored_by_actor_id uuid not null references public.actor(id),
  stored_by_session_ref text not null check(stored_by_session_ref ~ '^session:[-A-Za-z0-9:._/]+$'
    and char_length(stored_by_session_ref) between 17 and 300),
  stored_at timestamptz not null default now(),
  check(staging_provider_version<>final_provider_version),
  unique(final_provider_version), unique(release_id)
);

create table ops.foundation_assurance_production (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  verb text not null,
  kind text not null check(kind in ('benchmark_coverage','member_receipt','minimum_outcome')),
  evidence_digest text not null references ops.foundation_assurance_evidence(evidence_digest),
  actor_id uuid not null references public.actor(id),
  session_ref text not null check(session_ref ~ '^session:[-A-Za-z0-9:._/]+$'
    and char_length(session_ref) between 17 and 300),
  artifact jsonb not null,
  artifact_digest text not null check(artifact_digest ~ '^sha256:[0-9a-f]{64}$'),
  admission_id uuid references ops.j1_minimum_admission(id),
  event_id uuid references public.event(id),
  result jsonb not null,
  recorded_at timestamptz not null default now(),
  unique(evidence_digest,verb),
  check((kind='minimum_outcome')=(admission_id is not null and event_id is not null))
);

create or replace function ops.foundation_assurance_rows_immutable()
returns trigger language plpgsql as $$ begin
  raise exception 'foundation assurance evidence and production rows are immutable';
end $$;
create trigger foundation_assurance_evidence_immutable before update or delete
on ops.foundation_assurance_evidence for each row execute function ops.foundation_assurance_rows_immutable();
create trigger foundation_assurance_evidence_no_truncate before truncate
on ops.foundation_assurance_evidence for each statement execute function ops.foundation_assurance_rows_immutable();
create trigger foundation_assurance_production_immutable before update or delete
on ops.foundation_assurance_production for each row execute function ops.foundation_assurance_rows_immutable();
create trigger foundation_assurance_production_no_truncate before truncate
on ops.foundation_assurance_production for each statement execute function ops.foundation_assurance_rows_immutable();

create or replace function ops.foundation_assurance_store_evidence(
  p_idempotency_key uuid, p_config jsonb, p_evidence jsonb, p_seal jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_session text; v_release ops.release%rowtype; v_existing ops.foundation_assurance_evidence%rowtype;
begin
  -- EXECUTE is granted only to the authority bundle below. Evidence and its
  -- candidate binding are authority decisions, not routine writer actions.
  v_actor:=ops.portfolio_writer_actor_id();
  v_session:=nullif(current_setting('carr.receipt_session_ref',true),'');
  if v_session is null then raise exception 'foundation assurance evidence requires session provenance'; end if;
  select * into v_existing from ops.foundation_assurance_evidence where idempotency_key=p_idempotency_key;
  if found then
    if v_existing.evidence_digest is distinct from p_seal->>'evidence_digest' then
      raise exception 'foundation assurance evidence idempotency key was reused';
    end if;
    return jsonb_build_object('ok',true,'evidence_ref',v_existing.evidence_ref,'replayed',true);
  end if;
  if jsonb_typeof(p_config)<>'object' or jsonb_typeof(p_evidence)<>'object' or jsonb_typeof(p_seal)<>'object'
     or p_seal->>'schema_version'<>'doctorcre-v5-foundation-assurance-evidence-seal.v1'
     or p_evidence->>'schema_version'<>'doctorcre-v5-foundation-assurance-evidence.v1'
     or p_seal->>'evidence_ref' is distinct from p_evidence#>>'{release,test_evidence_ref}'
     or p_seal->>'source_sha' is distinct from p_evidence->>'source_sha'
     or p_seal->>'final_provider_version' is distinct from p_evidence->>'final_provider_version'
     or p_seal->>'benchmark_payload_digest' is distinct from p_evidence#>>'{measurements,benchmark_payload_digest}' then
    raise exception 'foundation assurance evidence/seal binding is invalid';
  end if;
  -- The final-upload sealer binds the caller-chosen unique key, never the row
  -- UUID that PostgreSQL generates. The wrapper files that exact candidate
  -- first, then this transaction attaches the derived evidence ref and bytes;
  -- approval can therefore name only a candidate whose evidence is complete.
  select * into v_release from ops.release where release_key=p_evidence#>>'{release,key}';
  if not found or v_release.environment<>'production' or v_release.state<>'candidate'
     or v_release.git_sha is distinct from p_evidence->>'source_sha'
     or v_release.provider is distinct from 'cloudflare-workers'
     or v_release.provider_version_id is distinct from p_evidence->>'final_provider_version'
     or (v_release.test_evidence_ref is not null
         and v_release.test_evidence_ref is distinct from p_seal->>'evidence_ref') then
    raise exception 'foundation assurance evidence is not bound to the exact production candidate';
  end if;
  -- Candidacy is deliberately filed before live acquisition so an interrupted
  -- run leaves a stable provider/release target that the sealer can resume.
  -- Bind the derived ref and insert its immutable bytes in this one transaction;
  -- approval can never observe one without the other.
  update ops.release set test_evidence_ref=p_seal->>'evidence_ref'
   where id=v_release.id and test_evidence_ref is null;
  insert into ops.foundation_assurance_evidence(evidence_digest,evidence_ref,benchmark_payload_digest,
    source_sha,source_tree,staging_provider_version,final_provider_version,release_id,config,evidence,seal,
    idempotency_key,stored_by_actor_id,stored_by_session_ref)
  values(p_seal->>'evidence_digest',p_seal->>'evidence_ref',p_seal->>'benchmark_payload_digest',
    p_evidence->>'source_sha',p_evidence->>'source_tree',(p_evidence->>'staging_provider_version')::uuid,
    (p_evidence->>'final_provider_version')::uuid,v_release.id,p_config,p_evidence,p_seal,
    p_idempotency_key,v_actor,v_session);
  return jsonb_build_object('ok',true,'evidence_ref',p_seal->>'evidence_ref','replayed',false);
end $$;

create or replace function ops.foundation_assurance_benchmark_review_material(p_draft_id uuid)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare v_payload text; v_row ops.foundation_assurance_evidence%rowtype;
begin
  select ops.benchmark_payload_digest(d.id) into v_payload from ops.benchmark_manifest_draft d
   where d.id=p_draft_id and d.benchmark_ref=ops.foundation_assurance_benchmark_ref();
  if v_payload is null then return null; end if;
  select * into v_row from ops.foundation_assurance_evidence where benchmark_payload_digest=v_payload
   order by stored_at desc limit 1;
  if not found then return null; end if;
  return jsonb_build_object('config',v_row.config,'evidence',v_row.evidence,'seal',v_row.seal);
end $$;

create or replace function ops.foundation_assurance_accepted_manifest(p_draft_id uuid)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare v_payload jsonb; v_accept ops.benchmark_manifest_acceptance_receipt%rowtype; v_slug text;
begin
  select * into v_accept from ops.benchmark_manifest_acceptance_receipt where draft_id=p_draft_id;
  if not found or v_accept.accepted_by_session_ref is null then return null; end if;
  select slug into v_slug from public.actor where id=v_accept.accepted_by_actor_id;
  v_payload:=ops.benchmark_payload_preimage(p_draft_id);
  return v_payload || jsonb_build_object('benchmark_manifest_digest',v_accept.accepted_payload_digest,
    'accepted_by_identity',jsonb_build_object('actor_id',v_slug,'session_ref',v_accept.accepted_by_session_ref,
      'authority_class','verified_partner'),'accepted_at',to_jsonb(v_accept.accepted_at),'status','accepted');
end $$;

create or replace function ops.foundation_assurance_subject_maker(p_draft_id uuid)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public
as $$ select jsonb_build_object('actor_id',a.slug,'session_ref',d.proposed_by_session_ref,
  'authority_class','review_agent') from ops.benchmark_manifest_draft d join public.actor a on a.id=d.proposed_by_actor_id
  where d.id=p_draft_id and d.proposed_by_session_ref is not null $$;

create or replace function ops.foundation_assurance_producer_material(
  p_verb text, p_identity jsonb, p_runtime_binding jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_evidence ops.foundation_assurance_evidence%rowtype; v_draft uuid;
        v_manifest jsonb; v_subject jsonb; v_comparator jsonb; v_members jsonb; v_coverage jsonb;
begin
  v_actor:=ops.foundation_assurance_require_seat(p_verb,p_identity);
  if p_runtime_binding->>'schema_version'<>'doctorcre-v5-foundation-assurance-runtime-binding.v1'
     or p_runtime_binding->>'environment'<>'production'
     or p_runtime_binding->>'provider'<>'cloudflare-workers'
     or coalesce(p_runtime_binding->>'source_sha','') !~ '^[0-9a-f]{40}$'
     or coalesce(p_runtime_binding->>'provider_version','') !~ '^[0-9a-f-]{36}$' then
    raise exception 'foundation assurance runtime binding is unavailable';
  end if;
  select * into v_evidence from ops.foundation_assurance_evidence
   where source_sha=p_runtime_binding->>'source_sha'
     and final_provider_version=(p_runtime_binding->>'provider_version')::uuid
   order by stored_at desc limit 1;
  if not found then raise exception 'foundation assurance evidence is unavailable for the serving Worker'; end if;
  select ops.benchmark_current_accepted_draft(ops.foundation_assurance_benchmark_ref()) into v_draft;
  if v_draft is null then raise exception 'foundation assurance benchmark has not been accepted'; end if;
  v_manifest:=ops.foundation_assurance_accepted_manifest(v_draft);
  v_subject:=ops.foundation_assurance_subject_maker(v_draft);
  if v_manifest is null or v_subject is null
     or v_manifest->>'benchmark_manifest_digest' is distinct from v_evidence.benchmark_payload_digest then
    raise exception 'foundation assurance accepted benchmark provenance is unavailable';
  end if;
  if p_verb='produce-foundation-assurance-benchmark-coverage' then
    return jsonb_build_object('config',v_evidence.config,'evidence',v_evidence.evidence,
      'accepted_manifest',v_manifest,'subject_maker_identity',v_subject,
      'observed_at',to_jsonb(clock_timestamp()));
  elsif ops.foundation_assurance_expected_step(p_verb) is not null then
    select value into v_comparator from jsonb_array_elements(v_evidence.evidence->'comparators')
     where value->>'id'=ops.foundation_assurance_expected_comparator(p_verb);
    if v_comparator is null then raise exception 'foundation assurance comparator is unavailable'; end if;
    return jsonb_build_object('config',v_evidence.config,'evidence',v_evidence.evidence,
      'accepted_manifest',v_manifest,'subject_maker_identity',v_subject,
      'comparator',v_comparator,'observed_at',to_jsonb(clock_timestamp()));
  elsif p_verb='record-foundation-assurance-minimum-outcome' then
    select artifact->'fact' into v_coverage from ops.foundation_assurance_production
     where evidence_digest=v_evidence.evidence_digest and kind='benchmark_coverage';
    select jsonb_agg(artifact order by verb collate "C") into v_members
      from ops.foundation_assurance_production where evidence_digest=v_evidence.evidence_digest and kind='member_receipt';
    if v_coverage is null or jsonb_array_length(coalesce(v_members,'[]'::jsonb))<>7 then
      raise exception 'foundation assurance predecessor production is incomplete';
    end if;
    return jsonb_build_object('config',v_evidence.config,'evidence',v_evidence.evidence,
      'accepted_manifest',v_manifest,'subject_maker_identity',v_subject,
      'coverage_fact',v_coverage,'member_receipts',v_members,
      'gate_zero',ops.benchmark_gate_zero_outcome(),'observed_at',to_jsonb(clock_timestamp()));
  end if;
  raise exception 'unknown foundation assurance producer verb %',p_verb;
end $$;

create or replace function ops.foundation_assurance_record_production(
  p_verb text, p_idempotency_key uuid, p_identity jsonb, p_produced jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_existing ops.foundation_assurance_production%rowtype;
        v_kind text; v_evidence text; v_digest text; v_result jsonb; v_id uuid:=gen_random_uuid();
        v_receipt jsonb; v_receipt_digest text; v_inventory uuid; v_scope jsonb; v_scope_key text;
        v_at text; v_admission uuid; v_event uuid; v_link text; v_evidence_row ops.foundation_assurance_evidence%rowtype;
begin
  v_actor:=ops.foundation_assurance_require_seat(p_verb,p_identity);
  if p_idempotency_key is null then
    raise exception 'foundation assurance production requires an idempotency key';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,0));
  select * into v_existing from ops.foundation_assurance_production where idempotency_key=p_idempotency_key;
  if found then
    if v_existing.verb is distinct from p_verb or v_existing.actor_id is distinct from v_actor
       or (p_produced is not null and v_existing.artifact_digest is distinct from
         'sha256:'||encode(public.digest(convert_to(ops.portfolio_canonical_json(p_produced),'UTF8'),'sha256'),'hex')) then
      raise exception 'foundation assurance idempotency key was reused for different production';
    end if;
    return v_existing.result||jsonb_build_object('replayed',true);
  end if;
  if jsonb_typeof(p_produced)<>'object' then return null; end if;
  v_digest:='sha256:'||encode(public.digest(convert_to(ops.portfolio_canonical_json(p_produced),'UTF8'),'sha256'),'hex');
  if p_verb='produce-foundation-assurance-benchmark-coverage' then
    v_kind:='benchmark_coverage'; v_evidence:=p_produced->>'evidence_digest';
    if p_produced#>>'{fact,status}'<>'pass' or p_produced#>>'{fact,evaluator_identity,actor_id}'<>'codex-fa-coverage' then
      raise exception 'foundation assurance benchmark coverage is not a passing staffed fact';
    end if;
  elsif ops.foundation_assurance_expected_step(p_verb) is not null then
    v_kind:='member_receipt'; v_evidence:=p_produced->>'environment_manifest_digest';
    if p_produced->>'status'<>'pass'
       or p_produced->>'receipt_producer_step_ref' is distinct from ops.foundation_assurance_expected_step(p_verb)
       or p_produced#>>'{producer_identity,actor_id}' is distinct from ops.foundation_assurance_expected_actor(p_verb) then
      raise exception 'foundation assurance member receipt does not match its staffed producer';
    end if;
  elsif p_verb='record-foundation-assurance-minimum-outcome' then
    v_kind:='minimum_outcome'; v_receipt:=p_produced->'proposed_receipt';
    v_evidence:=v_receipt->>'environment_manifest_digest';
    if p_produced->>'admissible'<>'true' or p_produced->>'issued'<>'false'
       or v_receipt->>'status'<>'pass'
       or v_receipt#>>'{producer_identity,actor_id}'<>'codex-fa-minimum' then
      raise exception 'foundation assurance minimum was not an admissible unissued proposal';
    end if;
  else raise exception 'unknown foundation assurance producer verb %',p_verb;
  end if;
  select * into v_evidence_row from ops.foundation_assurance_evidence where evidence_digest=v_evidence;
  if not found then raise exception 'foundation assurance production names unknown sealed evidence'; end if;

  if v_kind='minimum_outcome' then
    if exists(select 1 from ops.foundation_assurance_production where evidence_digest=v_evidence and kind='minimum_outcome') then
      raise exception 'foundation assurance minimum already exists for this evidence';
    end if;
    if (select count(*) from ops.foundation_assurance_production
         where evidence_digest=v_evidence and kind in ('benchmark_coverage','member_receipt'))<>8 then
      raise exception 'foundation assurance minimum requires exactly eight stored predecessors';
    end if;
    v_scope:=jsonb_build_object('tenant','carr-internal','scope_ref',
      'safe:wr95-foundation-assurance/'||substr(v_evidence,8),
      'clock_origin_gate_id','foundation-assurance-minimum-accepted',
      'clock_terminus_gate_id','journey-one-kernel-production-accepted',
      'benchmark_subject_digest',v_receipt->>'subject_digest',
      'benchmark_candidate_digest',v_receipt->>'candidate_digest',
      'benchmark_policy_digest',v_receipt->>'policy_digest');
    v_scope_key:=ops.j1_clock_scope_digest(v_scope);
    insert into ops.j1_minimum_inventory(clock_scope_key,clock_scope,clock_scope_ref,tenant,
      minimum_receipt_ttl_policy_ms,minimum_environment_manifest_digest,opened_by_actor_id)
    values(v_scope_key,v_scope,v_scope->>'scope_ref','carr-internal',
      (v_evidence_row.config->>'minimum_receipt_ttl_ms')::bigint,v_evidence,v_actor)
    on conflict(clock_scope_key) do nothing;
    select id into v_inventory from ops.j1_minimum_inventory where clock_scope_key=v_scope_key;
    if exists(select 1 from ops.j1_minimum_admission where inventory_id=v_inventory) then
      raise exception 'foundation assurance minimum inventory already has an admission';
    end if;
    v_at:=ops.j1_minimum_admission_instant();
    v_receipt_digest:=ops.j1_minimum_receipt_digest(v_receipt);
    v_link:=ops.j1_minimum_admission_digest(v_at,v_scope_key,v_evidence,
      (v_evidence_row.config->>'minimum_receipt_ttl_ms')::bigint,null,v_receipt_digest,'carr-internal');
    insert into ops.j1_minimum_admission(inventory_id,admission_ordinal,idempotency_key,
      prior_admission_digest,admission_digest,receipt,receipt_digest,admitted_at,gate_id,
      receipt_producer_step_ref,observed_at,ttl_expires_at,status,
      minimum_receipt_ttl_policy_ms,minimum_environment_manifest_digest,receipt_schema_ref,
      source_ref,input_authority,written_by_actor_id)
    values(v_inventory,0,p_idempotency_key,null,v_link,v_receipt,v_receipt_digest,v_at,
      v_receipt->>'gate_id',v_receipt->>'receipt_producer_step_ref',v_receipt->>'observed_at',
      v_receipt->>'ttl_expires_at',v_receipt->>'status',
      (v_evidence_row.config->>'minimum_receipt_ttl_ms')::bigint,v_evidence,
      'consumer-gate-receipt.v1',v_evidence_row.evidence_ref,
      'trusted_admission_not_independently_verified_by_this_record_layer',v_actor)
    returning id into v_admission;
    insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,field,new_value,cause,
      agent_rationale,idempotency_key)
    values(now(),v_actor,p_verb,'foundation_assurance_minimum',v_id,'minimum_outcome',
      jsonb_build_object('receipt_digest',v_receipt_digest,'evidence_digest',v_evidence,
        'admission_id',v_admission),'automation_job',
      'WR-000095 exact minimum join admitted after all nine independent inputs passed',p_idempotency_key::text)
    returning id into v_event;
    v_result:=jsonb_build_object('ok',true,'outcome_id',v_id,'receipt_digest',v_receipt_digest,
      'evidence_digest',v_evidence,'admission_id',v_admission,'event_id',v_event,'replayed',false);
  else
    v_result:=jsonb_build_object('ok',true,'production_id',v_id,'verb',p_verb,
      'artifact_digest',v_digest,'evidence_digest',v_evidence,'replayed',false);
  end if;
  insert into ops.foundation_assurance_production(id,idempotency_key,verb,kind,evidence_digest,
    actor_id,session_ref,artifact,artifact_digest,admission_id,event_id,result)
  values(v_id,p_idempotency_key,p_verb,v_kind,v_evidence,v_actor,p_identity->>'session_ref',
    p_produced,v_digest,v_admission,v_event,v_result);
  return v_result;
end $$;

-- Ordinary readers can verify immutable results. Only the dedicated role can
-- obtain producer material or record a production. Raw table writes are held by
-- no runtime role.
grant select on ops.foundation_assurance_evidence,ops.foundation_assurance_production
  to carr_reader,carr_writer,carr_authority;
revoke insert,update,delete,truncate on ops.foundation_assurance_evidence,ops.foundation_assurance_production
  from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_foundation_assurance_oracle;

revoke all on function ops.foundation_assurance_store_evidence(uuid,jsonb,jsonb,jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_foundation_assurance_oracle;
grant execute on function ops.foundation_assurance_store_evidence(uuid,jsonb,jsonb,jsonb)
  to carr_authority;

revoke all on function ops.foundation_assurance_producer_material(text,jsonb,jsonb),
  ops.foundation_assurance_record_production(text,uuid,jsonb,jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_foundation_assurance_oracle;
grant execute on function ops.foundation_assurance_producer_material(text,jsonb,jsonb),
  ops.foundation_assurance_record_production(text,uuid,jsonb,jsonb)
  to carr_foundation_assurance_oracle;

revoke all on function ops.foundation_assurance_benchmark_review_material(uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_foundation_assurance_oracle;
grant execute on function ops.foundation_assurance_benchmark_review_material(uuid)
  to carr_writer,carr_authority;

-- The generic 0510 append path may no longer admit this minimum. 0511's
-- family-seat writer is the sole atomic outcome/admission/event route.
revoke execute on function ops.j1_minimum_open_inventory(jsonb,bigint,text),
  ops.j1_minimum_append_admission(text,text,uuid,text,text,text,jsonb,jsonb)
  from carr_writer,carr_authority;
