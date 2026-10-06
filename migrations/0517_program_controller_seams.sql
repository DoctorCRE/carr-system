-- WR-000110: the program-controller seams -- a live source-path lease census, an
-- earned-width evidence ledger, a durable slice checkpoint, an authoritative
-- release receipt store, an authoritative origin/main head observation and one
-- idempotency ledger. Together they hold the facts
-- mcp-server/src/engineering-program-controller.v5.js refuses to let a caller
-- invent: every field of every closed request shape its four evaluators enforce
-- is a column here or a stated invariant in
-- mcp-server/src/program-controller-census.v5.js.
--
-- NO TRANSACTION CONTROL. tools/migrate.py refuses a 0339_-or-later migration
-- carrying top-level transaction control, and this file is the first half of a
-- reviewed ATOMIC_MIGRATION_GROUP with 0518: the authority surface below must
-- not reach the DEFERRABLE ops.scac_policy_epoch_refresh() constraint trigger on
-- public.schema_migrations without the v29 seal inside the same runner-owned
-- transaction.
--
-- Every enum and range list below is GENERATED from the exported controller
-- constant it mirrors. mcp-server/test/program-controller-census.v5.test.mjs
-- (T-CONST) asserts equality in BOTH directions, so a vocabulary that moves in
-- the module and not here is a red test rather than a silent divergence.

-- PostgreSQL refuses a subquery inside a check constraint, so "every element is
-- a typed reference" needs a function. It is IMMUTABLE and deliberately NOT
-- security definer: a plain function adds no row to the secdef_execute
-- projection and its owner is the migration's own superuser, outside the carr_*
-- closure the role_authority projection walks.
create or replace function ops.program_controller_typed_refs(p_values text[])
returns boolean
language sql immutable
as $$ select p_values is not null and not exists (
  select 1 from unnest(p_values) entry where entry !~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$') $$;

create table if not exists ops.slice_source_lease (
  id uuid primary key default gen_random_uuid(),
  slice_ref text not null check (slice_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  worktree_ref text not null check (worktree_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  worktree_path text not null check (length(btrim(worktree_path)) > 0),
  branch_ref text not null check (branch_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  base_commit_sha text not null check (base_commit_sha ~ '^[0-9a-f]{40}$'),
  source_paths text[] not null check (cardinality(source_paths) > 0),
  -- The refused disposition is STORABLE on purpose: the evaluator accepts the
  -- member and refuses it BY NAME, so a table that could not hold it would make
  -- that refusal unprovable.
  database_disposition text not null
    check (database_disposition in ('approved_sanitized_parent','equal_trust_branch','hermetic_fixture','no_database','schema_only_fixture','raw_production_fork')),
  database_resources text[] not null default '{}'
    check (ops.program_controller_typed_refs(database_resources)),
  serialized_surfaces text[] not null default '{}'
    check (serialized_surfaces <@ array['deployment_activation_retirement_controller','generated_schema_or_seal','migration_number_frontier','scac_mutation_registry','shared_interface_or_command_contract','shared_source_inventory_fixture']::text[]),
  repository_actions text[] not null default '{}'
    check (repository_actions <@ array['repository:create-worktree','repository:create-branch','repository:write-declared-scope','repository:run-checks','repository:commit','repository:push-branch','repository:open-pr']::text[]),
  reuse_disposition text not null check (reuse_disposition in ('extend','replace','reuse')),
  model_roles text[] not null default '{}'
    check (model_roles <@ array['author','observer','reviewer']::text[]),
  held_by_actor text not null check (length(btrim(held_by_actor)) > 0),
  opened_at timestamptz not null default now(),
  released_at timestamptz,
  correlation_id text
);
create unique index if not exists slice_source_lease_one_live
  on ops.slice_source_lease (slice_ref) where released_at is null;

create table if not exists ops.program_width_evidence (
  id uuid primary key default gen_random_uuid(),
  program_ref text not null check (program_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  evidence_class text not null check (evidence_class in ('failure_containment','isolation','merge_serialization','race','stale_base','wip_recovery')),
  state text not null check (state in ('accepted','failed','pending')),
  bound_base_sha text not null check (bound_base_sha ~ '^[0-9a-f]{40}$'),
  evidence_ref text not null check (evidence_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  observed_at timestamptz not null,
  accepted_at timestamptz,
  recorded_at timestamptz not null default now()
);
create unique index if not exists program_width_evidence_current
  on ops.program_width_evidence (program_ref, evidence_class, bound_base_sha);

create table if not exists ops.program_width_state (
  id uuid primary key default gen_random_uuid(),
  program_ref text not null check (program_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  current_width integer not null check (current_width in (1, 2, 3)),
  requested_width integer not null check (requested_width in (1, 2, 3)),
  set_at timestamptz not null default now()
);
create unique index if not exists program_width_state_one_live
  on ops.program_width_state (program_ref);

create table if not exists ops.slice_checkpoint (
  id uuid primary key default gen_random_uuid(),
  slice_ref text not null check (slice_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  checkpoint_ref text not null check (checkpoint_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  checkpoint_digest text not null check (checkpoint_digest ~ '^sha256:[0-9a-f]{64}$'),
  base_commit_sha text not null check (base_commit_sha ~ '^[0-9a-f]{40}$'),
  worktree_ref text not null check (worktree_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  lease_id uuid not null references ops.slice_source_lease(id),
  next_step_ref text not null check (next_step_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  completed_step_refs text[] not null default '{}'
    check (ops.program_controller_typed_refs(completed_step_refs)),
  record_evidence_refs text[] not null default '{}'
    check (ops.program_controller_typed_refs(record_evidence_refs)),
  reconstruction_source text not null check (reconstruction_source in ('durable_records','inherited_transcript')),
  inherited_transcript_used boolean not null default false,
  recorded_at timestamptz not null,
  written_at timestamptz not null default now()
);

-- check_conclusion / merge_slot_state / readback_state are deliberately NOT
-- columns: they live inside the typed body, whose per-kind shape the definer
-- function validates. Two truths for one fact is the defect that avoids.
create table if not exists ops.release_receipt (
  id uuid primary key default gen_random_uuid(),
  release_ref text not null check (release_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  slice_ref text not null check (slice_ref ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'),
  head_sha text not null check (head_sha ~ '^[0-9a-f]{40}$'),
  receipt_ref text not null check (receipt_ref ~ '^receipt:sha256:[0-9a-f]{64}$'),
  receipt_kind text not null check (receipt_kind in ('head_observation','merge_slot','readback','required_checks','revalidation','review')),
  body jsonb not null,
  recorded_at timestamptz not null default now(),
  unique (release_ref, receipt_kind)
);

-- The AUTHORITATIVE origin/main head. Never a caller argument and never a
-- lease's own base: a per-slice base would let the gated party move the base
-- its own evidence is judged against.
create table if not exists ops.program_origin_head_observation (
  id uuid primary key default gen_random_uuid(),
  repository_root text not null check (length(btrim(repository_root)) > 0),
  origin_main_sha text not null check (origin_main_sha ~ '^[0-9a-f]{40}$'),
  observed_at timestamptz not null,
  observed_by text not null check (length(btrim(observed_by)) > 0),
  recorded_at timestamptz not null default now()
);

-- The ONE place p_idempotency_key is persisted. A replay returns the stored
-- result, so a lease release replays the id the first call produced instead of
-- minting a second row.
create table if not exists ops.program_controller_fact_ledger (
  idempotency_key uuid primary key,
  fact_kind text not null check (fact_kind in ('slice_source_lease','slice_lease_release','program_width_evidence','program_width_state','slice_checkpoint','release_receipt','origin_head_observation','admission_refusal')),
  recorded_by text not null,
  target_table text,
  target_id uuid,
  result jsonb not null,
  recorded_at timestamptz not null default now()
);

-- ONE privileged writer. Every fact above is minted here and nowhere else.
--
-- TWO-TIER AUTHORITY, gated on the fact kind FIRST. admission_refusal is the one
-- kind the routine writer bundle may record, because the admission door in
-- mcp-server/src/engineering-runtime.js runs on DATABASE_URL_WRITER as
-- carr_writer and a refusal it cannot record is a refusal that is not evidence.
-- Every other kind requires the acting human authority.
create or replace function ops.record_program_controller_fact(
  p_fact_kind text, p_idempotency_key uuid, p_body jsonb
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_prior ops.program_controller_fact_ledger%rowtype;
  v_result jsonb;
  v_id uuid;
  v_target text;
  v_actor text;
  v_kind text;
  v_expected text[];
  v_digest text;
begin
  if p_fact_kind is null or p_fact_kind not in ('slice_source_lease','slice_lease_release','program_width_evidence','program_width_state','slice_checkpoint','release_receipt','origin_head_observation','admission_refusal') then
    raise exception 'program controller fact kind % is not a kind this writer records', p_fact_kind;
  end if;
  if p_idempotency_key is null or jsonb_typeof(p_body) <> 'object' then
    raise exception 'a program controller fact requires an idempotency key and a json object body';
  end if;

  if p_fact_kind = 'admission_refusal' then
    if not pg_has_role(session_user, 'carr_writer', 'member')
       and not pg_has_role(session_user, 'carr_authority', 'member') then
      raise exception using errcode='42501',
        message='recording an admission refusal requires the writer or authority capability';
    end if;
  else
    -- The membership test comes FIRST so a routine writer is refused with 42501
    -- rather than with ops.authority_actor_slug()'s own unadmitted-principal
    -- exception, which carries no privilege errcode.
    if not pg_has_role(session_user, 'carr_authority', 'member') then
      raise exception using errcode='42501',
        message='recording this program controller fact requires the authority capability';
    end if;
    if ops.authority_actor_slug() <> 'joe' then
      raise exception using errcode='42501',
        message='recording this program controller fact requires Joe authority';
    end if;
  end if;

  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text, 517));

  select * into v_prior from ops.program_controller_fact_ledger
    where idempotency_key = p_idempotency_key;
  if found then
    if v_prior.fact_kind <> p_fact_kind then
      raise exception 'program controller idempotency key was reused under fact kind % after %',
        p_fact_kind, v_prior.fact_kind;
    end if;
    return v_prior.result;
  end if;

  v_actor := session_user::text;

  if p_fact_kind = 'slice_source_lease' then
    insert into ops.slice_source_lease (
      slice_ref, worktree_ref, worktree_path, branch_ref, base_commit_sha, source_paths,
      database_disposition, database_resources, serialized_surfaces, repository_actions,
      reuse_disposition, model_roles, held_by_actor, correlation_id)
    values (
      p_body->>'slice_ref', p_body->>'worktree_ref', p_body->>'worktree_path',
      p_body->>'branch_ref', p_body->>'base_commit_sha', coalesce((select array_agg(value order by ordinality) from jsonb_array_elements_text(coalesce(p_body->'source_paths','[]'::jsonb)) with ordinality t(value, ordinality)), '{}'),
      p_body->>'database_disposition', coalesce((select array_agg(value order by ordinality) from jsonb_array_elements_text(coalesce(p_body->'database_resources','[]'::jsonb)) with ordinality t(value, ordinality)), '{}'),
      coalesce((select array_agg(value order by ordinality) from jsonb_array_elements_text(coalesce(p_body->'serialized_surfaces','[]'::jsonb)) with ordinality t(value, ordinality)), '{}'), coalesce((select array_agg(value order by ordinality) from jsonb_array_elements_text(coalesce(p_body->'repository_actions','[]'::jsonb)) with ordinality t(value, ordinality)), '{}'),
      p_body->>'reuse_disposition', coalesce((select array_agg(value order by ordinality) from jsonb_array_elements_text(coalesce(p_body->'model_roles','[]'::jsonb)) with ordinality t(value, ordinality)), '{}'),
      coalesce(p_body->>'held_by_actor', v_actor), p_body->>'correlation_id')
    returning id into v_id;
    v_target := 'ops.slice_source_lease';
    v_result := jsonb_build_object('ok', true, 'fact_kind', p_fact_kind, 'id', v_id);

  elsif p_fact_kind = 'slice_lease_release' then
    update ops.slice_source_lease set released_at = now()
      where slice_ref = p_body->>'slice_ref' and released_at is null
      returning id into v_id;
    if v_id is null then
      raise exception 'no live source lease for %', p_body->>'slice_ref';
    end if;
    v_target := 'ops.slice_source_lease';
    v_result := jsonb_build_object('ok', true, 'fact_kind', p_fact_kind, 'id', v_id);

  elsif p_fact_kind = 'program_width_evidence' then
    insert into ops.program_width_evidence (
      program_ref, evidence_class, state, bound_base_sha, evidence_ref, observed_at, accepted_at)
    values (
      p_body->>'program_ref', p_body->>'evidence_class', p_body->>'state',
      p_body->>'bound_base_sha', p_body->>'evidence_ref',
      (p_body->>'observed_at')::timestamptz, (p_body->>'accepted_at')::timestamptz)
    on conflict (program_ref, evidence_class, bound_base_sha) do update
      set state = excluded.state, evidence_ref = excluded.evidence_ref,
          observed_at = excluded.observed_at, accepted_at = excluded.accepted_at
    returning id into v_id;
    v_target := 'ops.program_width_evidence';
    v_result := jsonb_build_object('ok', true, 'fact_kind', p_fact_kind, 'id', v_id);

  elsif p_fact_kind = 'program_width_state' then
    insert into ops.program_width_state (program_ref, current_width, requested_width)
    values (p_body->>'program_ref', (p_body->>'current_width')::integer,
            (p_body->>'requested_width')::integer)
    on conflict (program_ref) do update
      set current_width = excluded.current_width,
          requested_width = excluded.requested_width, set_at = now()
    returning id into v_id;
    v_target := 'ops.program_width_state';
    v_result := jsonb_build_object('ok', true, 'fact_kind', p_fact_kind, 'id', v_id);

  elsif p_fact_kind = 'slice_checkpoint' then
    insert into ops.slice_checkpoint (
      slice_ref, checkpoint_ref, checkpoint_digest, base_commit_sha, worktree_ref, lease_id,
      next_step_ref, completed_step_refs, record_evidence_refs, reconstruction_source,
      inherited_transcript_used, recorded_at)
    values (
      p_body->>'slice_ref', p_body->>'checkpoint_ref', p_body->>'checkpoint_digest',
      p_body->>'base_commit_sha', p_body->>'worktree_ref', (p_body->>'lease_id')::uuid,
      p_body->>'next_step_ref', coalesce((select array_agg(value order by ordinality) from jsonb_array_elements_text(coalesce(p_body->'completed_step_refs','[]'::jsonb)) with ordinality t(value, ordinality)), '{}'),
      coalesce((select array_agg(value order by ordinality) from jsonb_array_elements_text(coalesce(p_body->'record_evidence_refs','[]'::jsonb)) with ordinality t(value, ordinality)), '{}'), p_body->>'reconstruction_source',
      coalesce((p_body->>'inherited_transcript_used')::boolean, false),
      (p_body->>'recorded_at')::timestamptz)
    returning id into v_id;
    v_target := 'ops.slice_checkpoint';
    v_result := jsonb_build_object('ok', true, 'fact_kind', p_fact_kind, 'id', v_id);

  elsif p_fact_kind = 'release_receipt' then
    -- A receipt is minted ONLY here. The reference must be the sha256 of the
    -- canonical body -- the controller's own digest check, mirrored -- and the
    -- body keys must be exactly the kind's declared shape.
    v_kind := p_body#>>'{body,kind}';
    if v_kind is null or v_kind is distinct from p_body->>'receipt_kind' then
      raise exception 'release receipt body kind % does not match receipt_kind %',
        v_kind, p_body->>'receipt_kind';
    end if;
    v_expected := case v_kind
      when 'head_observation' then array['head_sha','kind','observed_head_sha','slice_ref']
      when 'merge_slot' then array['head_sha','held_by_slice_ref','kind','slice_ref','state']
      when 'readback' then array['delivered_source_digest','expected_source_digest','head_sha','kind','main_sha','slice_ref','state']
      when 'required_checks' then array['checks','head_sha','kind','slice_ref']
      when 'revalidation' then array['current_main_sha','head_sha','kind','required','revalidated_against_sha','slice_ref']
      when 'review' then array['head_sha','kind','maker_actor_id','reviewed_head_sha','reviewer_actor_id','slice_ref','state']
    end;
    if v_expected is null then
      raise exception 'release receipt kind % has no declared body shape', v_kind;
    end if;
    if (select array_agg(key order by key) from jsonb_object_keys(p_body->'body') key)
       is distinct from (select array_agg(key order by key) from unnest(v_expected) key) then
      raise exception 'release receipt body for kind % is not exactly the declared shape', v_kind;
    end if;
    v_digest := 'receipt:sha256:' || encode(public.digest(
      convert_to(ops.scac_canonical_json(p_body->'body'), 'UTF8'), 'sha256'), 'hex');
    if p_body->>'receipt_ref' is distinct from v_digest then
      raise exception 'release receipt reference is not the digest of its own canonical body';
    end if;
    insert into ops.release_receipt (release_ref, slice_ref, head_sha, receipt_ref, receipt_kind, body)
    values (p_body->>'release_ref', p_body#>>'{body,slice_ref}', p_body#>>'{body,head_sha}',
            p_body->>'receipt_ref', v_kind, p_body->'body')
    returning id into v_id;
    v_target := 'ops.release_receipt';
    v_result := jsonb_build_object('ok', true, 'fact_kind', p_fact_kind, 'id', v_id,
      'receipt_ref', p_body->>'receipt_ref');

  elsif p_fact_kind = 'origin_head_observation' then
    insert into ops.program_origin_head_observation (
      repository_root, origin_main_sha, observed_at, observed_by)
    values (p_body->>'repository_root', p_body->>'origin_main_sha',
            (p_body->>'observed_at')::timestamptz, coalesce(p_body->>'observed_by', v_actor))
    returning id into v_id;
    v_target := 'ops.program_origin_head_observation';
    v_result := jsonb_build_object('ok', true, 'fact_kind', p_fact_kind, 'id', v_id);

  else
    -- admission_refusal: the decided outcome this Work Request requires to be
    -- persisted. It names no other store, so the writer cannot mint itself a
    -- lease, a width row or a receipt.
    if p_body->>'slice_ref' is null or p_body->>'reason_id' is null then
      raise exception 'an admission refusal records the slice it refused and the reason it refused it';
    end if;
    v_target := 'ops.program_controller_fact_ledger';
    v_result := jsonb_build_object('ok', true, 'fact_kind', p_fact_kind,
      'slice_ref', p_body->>'slice_ref', 'reason_id', p_body->>'reason_id',
      'blocking_check', p_body->>'blocking_check', 'decided_at', p_body->>'decided_at',
      'decision_digest', p_body->>'decision_digest');
  end if;

  insert into ops.program_controller_fact_ledger
    (idempotency_key, fact_kind, recorded_by, target_table, target_id, result)
  values (p_idempotency_key, p_fact_kind, v_actor, v_target, v_id, v_result);
  return v_result;
end $$;

comment on function ops.record_program_controller_fact(text,uuid,jsonb) is
  'WR-000110: the single privileged writer for the V5-F02 program-controller seams. Gated on fact kind first; the routine writer bundle may record only an admission refusal.';

-- Column-scoped SELECT to BOTH bundles. carr_writer needs it because the
-- admission door executes every census read as carr_writer.
grant select (id, slice_ref, worktree_ref, worktree_path, branch_ref, base_commit_sha, source_paths, database_disposition, database_resources, serialized_surfaces, repository_actions, reuse_disposition, model_roles, held_by_actor, opened_at, released_at, correlation_id) on table ops.slice_source_lease to carr_reader, carr_writer;
grant select (id, program_ref, evidence_class, state, bound_base_sha, evidence_ref, observed_at, accepted_at, recorded_at) on table ops.program_width_evidence to carr_reader, carr_writer;
grant select (id, program_ref, current_width, requested_width, set_at) on table ops.program_width_state to carr_reader, carr_writer;
grant select (id, slice_ref, checkpoint_ref, checkpoint_digest, base_commit_sha, worktree_ref, lease_id, next_step_ref, completed_step_refs, record_evidence_refs, reconstruction_source, inherited_transcript_used, recorded_at, written_at) on table ops.slice_checkpoint to carr_reader, carr_writer;
grant select (id, release_ref, slice_ref, head_sha, receipt_ref, receipt_kind, body, recorded_at) on table ops.release_receipt to carr_reader, carr_writer;
grant select (id, repository_root, origin_main_sha, observed_at, observed_by, recorded_at) on table ops.program_origin_head_observation to carr_reader, carr_writer;
grant select (idempotency_key, fact_kind, recorded_by, target_table, target_id, result, recorded_at) on table ops.program_controller_fact_ledger to carr_reader, carr_writer;

-- Row-changing privilege is held by no runtime role; the definer function above
-- is the only write path. The four privileges are the exact list the relation
-- projection admits, lifted byte-for-byte from 0511.
revoke insert,update,delete,truncate on ops.slice_source_lease,ops.program_width_evidence,ops.program_width_state,ops.slice_checkpoint,ops.release_receipt,ops.program_origin_head_observation,ops.program_controller_fact_ledger
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

-- The revoke PRECEDES the grants so acldefault()'s public EXECUTE row is
-- suppressed and the secdef projection sees exactly the two named grantees.
revoke all on function ops.record_program_controller_fact(text,uuid,jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.record_program_controller_fact(text,uuid,jsonb) to carr_authority;
grant execute on function ops.record_program_controller_fact(text,uuid,jsonb) to carr_writer;
