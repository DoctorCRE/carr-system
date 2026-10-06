-- 0431_completion_register_schema.sql
-- doctrine: runbook#completion-activation-register-v1
-- Additive completion evidence core. Source ledgers remain authoritative and
-- no stored column or write path can assert the derived operational state.

begin;

create or replace function ops.completion_runtime_tenant()
returns text
language plpgsql
stable
set search_path = pg_catalog, ops
as $$
declare
  tenant text := nullif(btrim(current_setting('carr.organization_tenant_id', true)), '');
begin
  if tenant is null then
    raise exception 'completion register requires a server-derived tenant';
  end if;
  return tenant;
end;
$$;

create or replace function ops.completion_stamp_tenant()
returns trigger
language plpgsql
set search_path = pg_catalog, ops
as $$
declare
  tenant text := ops.completion_runtime_tenant();
begin
  if new.organization_tenant_id is not null
     and new.organization_tenant_id is distinct from tenant then
    raise exception 'completion register tenant is server-derived';
  end if;
  new.organization_tenant_id := tenant;
  return new;
end;
$$;

create or replace function ops.completion_reject_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception '% is append-only', tg_table_name;
end;
$$;

create or replace function ops.completion_json_is_redacted(p_value jsonb)
returns boolean
language sql
immutable
strict
set search_path = pg_catalog
as $$
  with recursive nodes(path, key_name, value) as (
    select array[]::text[], null::text, p_value
    union all
    select n.path || child.key_name, child.key_name, child.value
      from nodes n
      cross join lateral (
        select e.key as key_name, e.value
          from jsonb_each(case when jsonb_typeof(n.value) = 'object' then n.value else '{}'::jsonb end) e
        union all
        select a.ordinality::text, a.value
          from jsonb_array_elements(case when jsonb_typeof(n.value) = 'array' then n.value else '[]'::jsonb end)
               with ordinality a(value, ordinality)
      ) child
  )
  select octet_length(p_value::text) <= 16384
     and not exists (
       select 1
         from nodes n
        where lower(coalesce(n.key_name, '')) = any (array[
                'secret','password','token','credential','authorization',
                'cookie','email','phone','address','environment','env',
                'raw','raw_environment','transcript',
                'raw_transcript','raw_body','body','diff','patch',
                'client_data','deal_data','derived_state','operational_status'
              ]::text[])
           or lower(coalesce(n.key_name, '')) ~ '(^|_)(secret|password|token|credential)($|_)'
           or (jsonb_typeof(n.value) = 'string'
               and lower(trim(both '"' from n.value::text)) = 'operational')
           or (jsonb_typeof(n.value) = 'string'
               and trim(both '"' from n.value::text) ~* '^[a-z][a-z0-9+.-]*://')
           or octet_length(n.value::text) > 4096
     );
$$;

create or replace function ops.completion_dimensions_valid(p_dimensions text[])
returns boolean
language sql
immutable
strict
set search_path = pg_catalog
as $$
  select cardinality(p_dimensions) > 0
     and p_dimensions <@ array[
       'canonical_owner','intended_consumer','workflow_trigger',
       'retrieval_admission','enforcement_closure','operator_surface',
       'telemetry','canonical_implementation','activation',
       'live_readback','rollback'
     ]::text[]
     and cardinality(p_dimensions) = (
       select count(distinct dimension)::integer from unnest(p_dimensions) dimension
     );
$$;

create or replace function ops.completion_precedence_valid(p_states text[])
returns boolean
language sql
immutable
strict
set search_path = pg_catalog
as $$
  select p_states = array[
    'conflicting','canceled','superseded','unknown_stale','blocked','planned',
    'built_unmerged','merged_unactivated','active_unproven',
    'partially_built','operational'
  ]::text[];
$$;

create table ops.completion_policy (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  policy_key text not null check (policy_key ~ '^[a-z][a-z0-9_-]{0,79}$'),
  policy_version integer not null check (policy_version > 0),
  capability_class text not null check (capability_class ~ '^[a-z][a-z0-9_-]{0,79}$'),
  required_dimensions text[] not null check (ops.completion_dimensions_valid(required_dimensions)),
  default_freshness interval not null check (default_freshness > interval '0'),
  state_precedence text[] not null check (ops.completion_precedence_valid(state_precedence)),
  effective_at timestamptz not null,
  policy_digest text not null check (policy_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, id),
  unique (organization_tenant_id, policy_key, policy_version)
);

create table ops.completion_receipt (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  receipt_ref text not null check (length(btrim(receipt_ref)) between 1 and 240),
  collector_name text not null check (collector_name ~ '^[a-z][a-z0-9_.-]{0,79}$'),
  collector_version text not null check (length(btrim(collector_version)) between 1 and 80),
  source_kind text not null check (source_kind ~ '^[a-z][a-z0-9_.-]{0,79}$'),
  source_ref text not null check (length(btrim(source_ref)) between 1 and 500),
  source_cursor jsonb not null check (jsonb_typeof(source_cursor) = 'object' and ops.completion_json_is_redacted(source_cursor)),
  rows_observed integer not null check (rows_observed >= 0),
  rows_changed integer not null check (rows_changed between 0 and rows_observed),
  outcome text not null check (outcome in ('succeeded','failed','unavailable')),
  failure_class text,
  started_at timestamptz not null,
  finished_at timestamptz not null check (finished_at >= started_at),
  expires_at timestamptz not null check (expires_at > finished_at),
  evidence_digest text not null check (evidence_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, id),
  unique (organization_tenant_id, receipt_ref),
  check ((outcome = 'succeeded' and failure_class is null)
      or (outcome <> 'succeeded' and length(btrim(failure_class)) > 0))
);

create table ops.completion_subject (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  stable_key text not null check (stable_key ~ '^[a-z0-9][a-z0-9:._/-]{0,239}$'),
  human_label text not null check (length(btrim(human_label)) between 1 and 240),
  capability_class text not null check (capability_class ~ '^[a-z][a-z0-9_-]{0,79}$'),
  canonical_source_kind text check (canonical_source_kind ~ '^[a-z][a-z0-9_.-]{0,79}$'),
  canonical_source_ref text check (length(btrim(canonical_source_ref)) between 1 and 500),
  created_provenance jsonb not null check (jsonb_typeof(created_provenance) = 'object' and ops.completion_json_is_redacted(created_provenance)),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, id),
  unique (organization_tenant_id, stable_key),
  check ((canonical_source_kind is null) = (canonical_source_ref is null))
);

create table ops.completion_observation (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  subject_id uuid not null,
  receipt_id uuid not null,
  source_kind text not null check (source_kind ~ '^[a-z][a-z0-9_.-]{0,79}$'),
  source_ref text not null check (length(btrim(source_ref)) between 1 and 500),
  source_revision text not null check (length(btrim(source_revision)) between 1 and 240),
  coherent_revision text not null check (length(btrim(coherent_revision)) between 1 and 240),
  observation_kind text not null check (observation_kind in (
    'canonical_owner','intended_consumer','workflow_trigger',
    'retrieval_admission','enforcement_closure','operator_surface',
    'telemetry','canonical_implementation','activation','live_readback',
    'rollback','intent','implementation_artifact','blocker'
  )),
  authority_class text not null check (authority_class in ('authoritative','supporting')),
  content_digest text not null check (content_digest ~ '^sha256:[a-f0-9]{64}$'),
  value_digest text not null check (value_digest ~ '^sha256:[a-f0-9]{64}$'),
  redacted_value jsonb not null check (ops.completion_json_is_redacted(redacted_value)),
  evidence_locator text not null check (length(btrim(evidence_locator)) between 1 and 500),
  observed_at timestamptz not null,
  expires_at timestamptz not null check (expires_at > observed_at),
  collector_name text not null check (collector_name ~ '^[a-z][a-z0-9_.-]{0,79}$'),
  collector_version text not null check (length(btrim(collector_version)) between 1 and 80),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, id),
  unique (organization_tenant_id, source_kind, source_ref, source_revision, observation_kind, content_digest),
  foreign key (organization_tenant_id, subject_id)
    references ops.completion_subject (organization_tenant_id, id),
  foreign key (organization_tenant_id, receipt_id)
    references ops.completion_receipt (organization_tenant_id, id)
);

create table ops.completion_relation (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  from_subject_id uuid not null,
  to_subject_id uuid not null,
  relation_kind text not null check (relation_kind in (
    'implements','evidenced_by','duplicates','supersedes','blocked_by','owned_by'
  )),
  authority_class text not null check (authority_class in ('exact_source','human_decision')),
  source_kind text not null check (source_kind ~ '^[a-z][a-z0-9_.-]{0,79}$'),
  source_ref text not null check (length(btrim(source_ref)) between 1 and 500),
  source_revision text not null check (length(btrim(source_revision)) between 1 and 240),
  relation_digest text not null check (relation_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, id),
  unique (organization_tenant_id, from_subject_id, to_subject_id, relation_kind, source_kind, source_ref, source_revision),
  foreign key (organization_tenant_id, from_subject_id)
    references ops.completion_subject (organization_tenant_id, id),
  foreign key (organization_tenant_id, to_subject_id)
    references ops.completion_subject (organization_tenant_id, id),
  check (from_subject_id <> to_subject_id)
);

create table ops.completion_disposition (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  subject_id uuid not null,
  disposition text not null check (disposition in ('canceled','superseded')),
  replacement_subject_id uuid,
  decision_ref text not null check (length(btrim(decision_ref)) between 1 and 240),
  rationale text not null check (length(btrim(rationale)) between 1 and 2000),
  decided_by_actor_id uuid not null references public.actor(id),
  decided_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, id),
  foreign key (organization_tenant_id, subject_id)
    references ops.completion_subject (organization_tenant_id, id),
  foreign key (organization_tenant_id, replacement_subject_id)
    references ops.completion_subject (organization_tenant_id, id),
  check ((disposition = 'superseded' and replacement_subject_id is not null)
      or (disposition = 'canceled' and replacement_subject_id is null)),
  check (replacement_subject_id is distinct from subject_id)
);

create index completion_observation_subject_kind_latest_idx
  on ops.completion_observation
  (organization_tenant_id, subject_id, observation_kind, observed_at desc, id desc);
create index completion_observation_expiry_idx
  on ops.completion_observation (organization_tenant_id, expires_at);
create index completion_disposition_subject_latest_idx
  on ops.completion_disposition
  (organization_tenant_id, subject_id, decided_at desc, id desc);
create index completion_relation_to_subject_idx
  on ops.completion_relation (organization_tenant_id, to_subject_id, relation_kind);
create index completion_policy_class_latest_idx
  on ops.completion_policy
  (organization_tenant_id, capability_class, effective_at desc, policy_version desc);

create or replace function ops.completion_policy_before_insert()
returns trigger
language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  tenant text := ops.completion_runtime_tenant();
  computed_digest text;
begin
  if new.organization_tenant_id is not null
     and new.organization_tenant_id is distinct from tenant then
    raise exception 'completion register tenant is server-derived';
  end if;
  new.organization_tenant_id := tenant;
  computed_digest := 'sha256:' || encode(digest(convert_to(jsonb_build_object(
    'organization_tenant_id', new.organization_tenant_id,
    'policy_key', new.policy_key,
    'policy_version', new.policy_version,
    'capability_class', new.capability_class,
    'required_dimensions', to_jsonb(new.required_dimensions),
    'default_freshness', new.default_freshness::text,
    'state_precedence', to_jsonb(new.state_precedence),
    'effective_at', to_char(new.effective_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
  )::text, 'UTF8'), 'sha256'), 'hex');
  if new.policy_digest is not null and new.policy_digest is distinct from computed_digest then
    raise exception 'completion policy digest does not match its immutable content';
  end if;
  new.policy_digest := computed_digest;
  return new;
end;
$$;

create or replace function ops.completion_observation_before_insert()
returns trigger
language plpgsql
set search_path = pg_catalog, ops
as $$
declare
  tenant text := ops.completion_runtime_tenant();
  receipt ops.completion_receipt%rowtype;
begin
  if new.organization_tenant_id is not null
     and new.organization_tenant_id is distinct from tenant then
    raise exception 'completion register tenant is server-derived';
  end if;
  new.organization_tenant_id := tenant;
  select * into receipt
    from ops.completion_receipt
   where organization_tenant_id = tenant and id = new.receipt_id;
  if not found
     or receipt.outcome <> 'succeeded'
     or receipt.source_kind is distinct from new.source_kind
     or receipt.source_ref is distinct from new.source_ref
     or receipt.collector_name is distinct from new.collector_name
     or receipt.collector_version is distinct from new.collector_version
     or receipt.finished_at > new.observed_at
     or new.observed_at > receipt.expires_at
     or new.expires_at > receipt.expires_at then
    raise exception 'completion observation requires its matching successful receipt';
  end if;
  return new;
end;
$$;

create or replace function ops.completion_disposition_before_insert()
returns trigger
language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  tenant text := ops.completion_runtime_tenant();
begin
  if new.organization_tenant_id is not null
     and new.organization_tenant_id is distinct from tenant then
    raise exception 'completion register tenant is server-derived';
  end if;
  new.organization_tenant_id := tenant;
  if not exists (
    select 1 from ops.completion_subject s
     where s.organization_tenant_id = tenant and s.id = new.subject_id
       and s.canonical_source_kind is null and s.canonical_source_ref is null
  ) then
    raise exception 'completion disposition is only for source-less subjects';
  end if;
  if not exists (
    select 1 from public.actor a
     where a.id = new.decided_by_actor_id and a.kind = 'human' and a.active
  ) then
    raise exception 'completion disposition requires an active human actor';
  end if;
  return new;
end;
$$;

create trigger completion_policy_insert_guard
before insert on ops.completion_policy
for each row execute function ops.completion_policy_before_insert();
create trigger completion_observation_insert_guard
before insert on ops.completion_observation
for each row execute function ops.completion_observation_before_insert();
create trigger completion_disposition_insert_guard
before insert on ops.completion_disposition
for each row execute function ops.completion_disposition_before_insert();

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'completion_policy','completion_receipt','completion_subject',
    'completion_observation','completion_relation','completion_disposition'
  ] loop
    if table_name not in ('completion_policy','completion_observation','completion_disposition') then
      execute format(
        'create trigger %I before insert on ops.%I for each row execute function ops.completion_stamp_tenant()',
        table_name || '_tenant_guard', table_name
      );
    end if;
    execute format(
      'create trigger %I before update or delete on ops.%I for each row execute function ops.completion_reject_mutation()',
      table_name || '_append_only', table_name
    );
  end loop;
end;
$$;

select set_config('carr.organization_tenant_id', 'carr-internal', true);
insert into ops.completion_policy (
  id, policy_key, policy_version, capability_class, required_dimensions,
  default_freshness, state_precedence, effective_at, policy_digest, created_at
) values (
  '00000000-0000-4000-8000-000000000431'::uuid,
  'completion-default', 1, 'default',
  array[
    'canonical_owner','intended_consumer','workflow_trigger',
    'retrieval_admission','enforcement_closure','operator_surface',
    'telemetry','canonical_implementation','activation','live_readback','rollback'
  ]::text[],
  interval '24 hours',
  array[
    'conflicting','canceled','superseded','unknown_stale','blocked','planned',
    'built_unmerged','merged_unactivated','active_unproven',
    'partially_built','operational'
  ]::text[],
  '2026-08-25T00:00:00Z'::timestamptz,
  null,
  '2026-08-25T00:00:00Z'::timestamptz
);

create view ops.completion_current_observation
with (security_barrier = true)
as
select distinct on (
    o.organization_tenant_id, o.subject_id, o.observation_kind,
    o.source_kind, o.source_ref
  )
  o.id, o.organization_tenant_id, o.subject_id, o.receipt_id,
  o.source_kind, o.source_ref, o.source_revision, o.coherent_revision,
  o.observation_kind, o.authority_class, o.content_digest, o.value_digest,
  o.redacted_value, o.evidence_locator, o.observed_at, o.expires_at,
  o.collector_name, o.collector_version
from ops.completion_observation o
where o.organization_tenant_id = ops.completion_runtime_tenant()
order by o.organization_tenant_id, o.subject_id, o.observation_kind,
         o.source_kind, o.source_ref, o.observed_at desc, o.id desc;

create view ops.completion_dimension_matrix
with (security_barrier = true)
as
with subject_policy as (
  select s.id as subject_id, s.organization_tenant_id, s.stable_key,
         p.policy_key, p.policy_version, p.policy_digest,
         p.required_dimensions, p.default_freshness, p.state_precedence
    from ops.completion_subject s
    cross join lateral (
      select candidate.*
        from ops.completion_policy candidate
       where candidate.organization_tenant_id = s.organization_tenant_id
         and candidate.capability_class in (s.capability_class, 'default')
         and candidate.effective_at <= now()
       order by (candidate.capability_class = s.capability_class) desc,
                candidate.effective_at desc, candidate.policy_version desc
       limit 1
    ) p
   where s.organization_tenant_id = ops.completion_runtime_tenant()
), required as (
  select sp.*, dimension.dimension, dimension.ordinality::integer as dimension_ordinal
    from subject_policy sp
    cross join lateral unnest(sp.required_dimensions)
      with ordinality as dimension(dimension, ordinality)
)
select r.organization_tenant_id, r.subject_id, r.stable_key,
       r.policy_key, r.policy_version, r.policy_digest,
       r.default_freshness, r.state_precedence, r.dimension, r.dimension_ordinal,
       case
         when count(distinct o.value_digest) filter (
                where o.authority_class = 'authoritative'
                  and least(o.expires_at, o.observed_at + r.default_freshness) > now()
              ) > 1 then 'conflicting'
         when count(*) filter (
                where o.authority_class = 'authoritative'
                  and least(o.expires_at, o.observed_at + r.default_freshness) > now()
              ) > 0 then 'present'
         when count(o.id) filter (where o.authority_class = 'authoritative') > 0 then 'stale'
         else 'missing'
       end as dimension_state,
       min(o.observed_at) filter (
         where o.authority_class = 'authoritative'
           and least(o.expires_at, o.observed_at + r.default_freshness) > now()
       ) as oldest_fresh_observed_at,
       min(least(o.expires_at, o.observed_at + r.default_freshness)) filter (
         where o.authority_class = 'authoritative'
           and least(o.expires_at, o.observed_at + r.default_freshness) > now()
       ) as earliest_fresh_expiry,
       coalesce(
         jsonb_agg(jsonb_build_object(
           'observation_id', 'observation:' || o.id::text,
           'receipt_ref', receipt.receipt_ref,
           'source_kind', o.source_kind,
           'source_ref', o.source_ref,
           'source_revision', o.source_revision,
           'coherent_revision', o.coherent_revision,
           'observed_at', o.observed_at,
           'source_expires_at', o.expires_at,
           'expires_at', least(o.expires_at, o.observed_at + r.default_freshness),
           'evidence_locator', o.evidence_locator
         ) order by o.observed_at desc, o.id desc) filter (where o.id is not null),
         '[]'::jsonb
       ) as evidence
  from required r
  left join ops.completion_current_observation o
    on o.organization_tenant_id = r.organization_tenant_id
   and o.subject_id = r.subject_id
   and o.observation_kind = r.dimension
  left join ops.completion_receipt receipt
    on receipt.organization_tenant_id = o.organization_tenant_id
   and receipt.id = o.receipt_id
 group by r.organization_tenant_id, r.subject_id, r.stable_key,
          r.policy_key, r.policy_version, r.policy_digest,
          r.default_freshness, r.state_precedence, r.dimension, r.dimension_ordinal;

create view ops.completion_projection
with (security_barrier = true)
as
with matrix_rollup as (
  select m.organization_tenant_id, m.subject_id, m.stable_key,
         m.policy_key, m.policy_version, m.policy_digest, m.default_freshness,
         m.state_precedence,
         bool_or(m.dimension_state = 'conflicting') as has_conflict,
         bool_or(m.dimension_state = 'stale') as has_stale,
         bool_and(m.dimension_state = 'present') as every_required_present,
         min(m.dimension_ordinal) filter (where m.dimension_state <> 'present') as first_unmet_ordinal,
         jsonb_object_agg(m.dimension, jsonb_build_object(
           'state', m.dimension_state,
           'ordinal', m.dimension_ordinal,
           'oldest_fresh_observed_at', m.oldest_fresh_observed_at,
           'earliest_fresh_expiry', m.earliest_fresh_expiry,
           'evidence', m.evidence
         ) order by m.dimension_ordinal) as evidence_matrix
    from ops.completion_dimension_matrix m
   group by m.organization_tenant_id, m.subject_id, m.stable_key,
            m.policy_key, m.policy_version, m.policy_digest, m.default_freshness,
            m.state_precedence
), subject_state as (
  select r.*, s.human_label, s.capability_class,
         s.canonical_source_kind, s.canonical_source_ref,
         unmet.dimension as next_required_dimension,
         disposition.disposition,
         disposition.decision_ref as disposition_decision_ref,
         disposition.replacement_subject_id,
         coalesce(flags.has_intent, false) as has_intent,
         coalesce(flags.has_artifact, false) as has_artifact,
         coalesce(flags.has_canonical, false) as has_canonical,
         coalesce(flags.has_activation, false) as has_activation,
         coalesce(flags.has_readback, false) as has_readback,
         coalesce(flags.has_telemetry, false) as has_telemetry,
         coalesce(flags.has_blocker, false) as has_blocker,
         coalesce(coherence.coherent_revision_count, 0) as coherent_revision_count,
         coherence.coherent_revision
    from matrix_rollup r
    join ops.completion_subject s
      on s.organization_tenant_id = r.organization_tenant_id and s.id = r.subject_id
    left join ops.completion_dimension_matrix unmet
      on unmet.organization_tenant_id = r.organization_tenant_id
     and unmet.subject_id = r.subject_id
     and unmet.dimension_ordinal = r.first_unmet_ordinal
    left join lateral (
      select d.disposition, d.decision_ref, d.replacement_subject_id
        from ops.completion_disposition d
       where d.organization_tenant_id = r.organization_tenant_id
         and d.subject_id = r.subject_id
       order by d.decided_at desc, d.id desc
       limit 1
    ) disposition on true
    left join lateral (
      select bool_or(o.authority_class = 'authoritative' and o.observation_kind = 'intent' and least(o.expires_at, o.observed_at + r.default_freshness) > now()) as has_intent,
             bool_or(o.authority_class = 'authoritative' and o.observation_kind = 'implementation_artifact' and least(o.expires_at, o.observed_at + r.default_freshness) > now()) as has_artifact,
             bool_or(o.authority_class = 'authoritative' and o.observation_kind = 'canonical_implementation' and least(o.expires_at, o.observed_at + r.default_freshness) > now()) as has_canonical,
             bool_or(o.authority_class = 'authoritative' and o.observation_kind = 'activation' and least(o.expires_at, o.observed_at + r.default_freshness) > now()) as has_activation,
             bool_or(o.authority_class = 'authoritative' and o.observation_kind = 'live_readback' and least(o.expires_at, o.observed_at + r.default_freshness) > now()) as has_readback,
             bool_or(o.authority_class = 'authoritative' and o.observation_kind = 'telemetry' and least(o.expires_at, o.observed_at + r.default_freshness) > now()) as has_telemetry,
             bool_or(o.authority_class = 'authoritative' and o.observation_kind = 'blocker' and least(o.expires_at, o.observed_at + r.default_freshness) > now()) as has_blocker
        from ops.completion_current_observation o
       where o.organization_tenant_id = r.organization_tenant_id
         and o.subject_id = r.subject_id
    ) flags on true
    left join lateral (
      select count(distinct o.coherent_revision) filter (where o.coherent_revision is not null) as coherent_revision_count,
             min(o.coherent_revision) filter (where o.coherent_revision is not null) as coherent_revision
        from ops.completion_current_observation o
       where o.organization_tenant_id = r.organization_tenant_id
         and o.subject_id = r.subject_id
         and o.observation_kind in (
           select m.dimension
             from ops.completion_dimension_matrix m
            where m.organization_tenant_id = r.organization_tenant_id
              and m.subject_id = r.subject_id
         )
         and o.authority_class = 'authoritative'
         and least(o.expires_at, o.observed_at + r.default_freshness) > now()
    ) coherence on true
), candidate as (
  select s.*,
         chosen.lifecycle_state,
         array_position(s.state_precedence, chosen.lifecycle_state) as precedence_rank
    from subject_state s
    cross join lateral (
      select possibility.lifecycle_state
        from (values
          ('conflicting'::text, s.has_conflict),
          ('canceled', s.disposition = 'canceled'),
          ('superseded', s.disposition = 'superseded'),
          ('unknown_stale', s.has_stale),
          ('blocked', s.has_blocker),
          ('planned', s.has_intent and not s.has_artifact and not s.has_canonical),
          ('built_unmerged', s.has_artifact and not s.has_canonical),
          ('merged_unactivated', s.has_canonical and not s.has_activation),
          ('active_unproven', s.has_activation and (not s.has_readback or not s.has_telemetry)),
          ('partially_built', not (s.every_required_present and s.coherent_revision_count = 1)),
          ('operational', s.every_required_present and s.coherent_revision_count = 1)
        ) possibility(lifecycle_state, applies)
       where possibility.applies
       order by array_position(s.state_precedence, possibility.lifecycle_state)
       limit 1
    ) chosen
)
select organization_tenant_id, subject_id, stable_key, human_label,
       capability_class, canonical_source_kind, canonical_source_ref,
       lifecycle_state, precedence_rank, state_precedence,
       policy_key, policy_version, policy_digest, default_freshness,
       coherent_revision,
       evidence_matrix, next_required_dimension,
       case
         when lifecycle_state = 'conflicting' then 'resolve authoritative evidence conflict'
         when lifecycle_state in ('canceled','superseded') then null
         when lifecycle_state = 'unknown_stale' then 'refresh ' || coalesce(next_required_dimension, 'expired evidence')
         when lifecycle_state = 'blocked' then 'clear canonical blocker'
         when next_required_dimension is not null then 'supply ' || next_required_dimension
         else null
       end as next_action,
       disposition_decision_ref,
       case when replacement_subject_id is null then null else 'subject:' || replacement_subject_id::text end as replacement_subject_ref
  from candidate;

comment on table ops.completion_subject is
  'Immutable tenant-derived capability identities. No execution or lifecycle status is stored.';
comment on table ops.completion_observation is
  'Append-only, receipt-bound, redacted source evidence. Operational is forbidden as authored evidence and derived only in ops.completion_projection.';
comment on table ops.completion_relation is
  'Append-only exact or human-decided relations between completion subjects.';
comment on table ops.completion_disposition is
  'Append-only human disposition for source-less artifacts only; never an operational assertion.';
comment on table ops.completion_policy is
  'Append-only versioned required-evidence, freshness, and conservative precedence policy.';
comment on table ops.completion_receipt is
  'Append-only collector receipt metadata; failures cannot support observations.';
comment on view ops.completion_projection is
  'Tenant-derived deterministic lifecycle projection. Conflicts and stale evidence outrank positive completion evidence.';

revoke all on ops.completion_policy, ops.completion_receipt,
  ops.completion_subject, ops.completion_observation,
  ops.completion_relation, ops.completion_disposition from public;
revoke all on function ops.completion_runtime_tenant(),
  ops.completion_json_is_redacted(jsonb),
  ops.completion_dimensions_valid(text[]),
  ops.completion_precedence_valid(text[]) from public;
grant execute on function ops.completion_runtime_tenant()
  to carr_reader, carr_writer, carr_jobs;
grant select on ops.completion_current_observation,
  ops.completion_dimension_matrix, ops.completion_projection
  to carr_reader, carr_writer, carr_jobs;

commit;

-- Rollback is forward-only: stop future collectors and replace views/policies
-- additively. Historical evidence and applied migrations are never deleted.
