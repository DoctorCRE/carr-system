-- 0580_resource_observation.sql
--
-- DoctorCRE V5-UX-C02 (Resource dashboard and metering read contract) and
-- V5-UX-C06 (Local compute capacity and model-route visibility): the store
-- and the one write door those two UI slices need.
--
-- WHY THIS EXISTS. C02's included_scope asks for a "provider/account/project/
-- product/period/as-of quantity, allowance, policy, estimate and charge
-- projection" that "use[s] absent/ineligible/stale states until actual
-- collector evidence exists" -- no fabricated telemetry, ever. C06 adds one
-- more requirement on top: local-host capacity must carry MEASURED and
-- CONFIGURED as two separate fields, never collapsed into one number, and an
-- absent sensor reads back null with a reason, never a zero.
--
-- SCOPE. One append-only observation table (ops.resource_observation), one
-- SECURITY DEFINER write door (ops.record_resource_observation) for the local,
-- credential-less collector (tools/resource-collector.py, called through
-- ./run.sh call record-resource-observation -- see that script's header for
-- why it carries no credential), and one read projection
-- (ops.read_resource_dashboard) that always returns a row for every provider
-- this contract names -- neon, github, cloudflare, local_compute, model_route
-- -- even when no collector for that provider exists yet. A provider with no
-- observation reads back state='unconfigured' (external providers, C03-C05
-- not yet built) or state='collector_absent' (local providers, no observation
-- received), with an explicit reason. Nothing here fabricates a number for
-- Neon, GitHub or Cloudflare; C03-C05 each add their own collector later and
-- this door does not change when they land.
--
-- No SECURITY DEFINER on the read side: ops.read_resource_dashboard only ever
-- SELECTs ops.resource_observation, and carr_reader is granted SELECT on that
-- table directly below, so the read function needs no elevated privilege.
--
-- No explicit transaction control: from 0339 onward tools/migrate.py runs
-- each migration inside its own single transaction.

create table if not exists ops.resource_observation (
  id uuid primary key default gen_random_uuid(),
  provider text not null check (provider in ('neon','github','cloudflare','local_compute','model_route')),
  account text,
  project text,
  product text,
  period text,
  as_of timestamptz,
  quantity numeric,
  quantity_unit text,
  allowance numeric,
  policy jsonb,
  estimate numeric,
  charge numeric,
  -- C06 checkable_done: "Source contract distinguishes measured capacity from
  -- configured capacity." Two independent, independently-nullable fields --
  -- never one column collapsing both, and never a substituted zero for a
  -- sensor that did not answer.
  measured_capacity jsonb,
  configured_capacity jsonb,
  model_route jsonb,
  state text not null check (state in ('ok','partial','stale','unconfigured','collector_absent','host_offline')),
  reason text,
  source text not null check (btrim(source) <> ''),
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);

create index if not exists resource_observation_provider_observed_at_idx
  on ops.resource_observation (provider, observed_at desc);

comment on table ops.resource_observation is
  'DoctorCRE V5-UX-C02/C06: one row per collector observation. Append-only; the read door always projects the latest row per provider. No delete, no update -- a stale observation is superseded by a newer row, never edited in place.';

revoke all on table ops.resource_observation from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.resource_observation to carr_reader;

-- Private receipt: idempotency_key compare-and-swap for the write door, same
-- shape as every other receipt table in this system (e.g.
-- ops.work_request_joe_answer_receipt, 0575).
create table if not exists ops.resource_observation_receipt (
  id uuid primary key default gen_random_uuid(),
  resource_observation_id uuid not null references ops.resource_observation(id),
  idempotency_key uuid not null unique,
  request_digest text not null check (request_digest ~ '^sha256:[0-9a-f]{64}$'),
  recorded_by_actor_slug text,
  created_at timestamptz not null default now()
);

comment on table ops.resource_observation_receipt is
  'Private idempotency receipt for ops.record_resource_observation. Not a dispatch or execution record.';

revoke all on table ops.resource_observation_receipt from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.resource_observation_receipt to carr_reader;

create or replace function ops.record_resource_observation(
  p_provider text,
  p_account text,
  p_project text,
  p_product text,
  p_period text,
  p_as_of timestamptz,
  p_quantity numeric,
  p_quantity_unit text,
  p_allowance numeric,
  p_policy jsonb,
  p_estimate numeric,
  p_charge numeric,
  p_measured_capacity jsonb,
  p_configured_capacity jsonb,
  p_model_route jsonb,
  p_state text,
  p_reason text,
  p_source text,
  p_observed_at timestamptz,
  p_idempotency_key uuid,
  p_actor_slug text
)
returns table (
  id uuid,
  provider text,
  state text,
  reason text,
  observed_at timestamptz,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_digest text;
  v_existing ops.resource_observation_receipt%rowtype;
  v_row ops.resource_observation%rowtype;
begin
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  if p_provider not in ('neon','github','cloudflare','local_compute','model_route') then
    raise exception 'resource_observation_provider_invalid';
  end if;
  -- C03 (Neon), C04 (GitHub) and C05 (Cloudflare) are not built: no collector
  -- exists for those three providers yet, so this door has no real evidence
  -- to accept for them. Accepting a caller-supplied {provider:'neon',
  -- state:'ok'} today would let any carr_writer agent fabricate a healthy
  -- reading the read door then serves as fact. Refuse those three providers
  -- outright until their own collector migrations land and lift this check.
  if p_provider not in ('local_compute','model_route') then
    raise exception 'resource_observation_provider_not_yet_collectible';
  end if;
  if p_state not in ('ok','partial','stale','unconfigured','collector_absent','host_offline') then
    raise exception 'resource_observation_state_invalid';
  end if;
  if p_source is null or btrim(p_source) = '' then
    raise exception 'resource_observation_source_required';
  end if;
  if p_observed_at is null then
    raise exception 'resource_observation_observed_at_required';
  end if;
  -- Caller-supplied observed_at has no upper bound otherwise: a future
  -- timestamp would win the read door's `order by observed_at desc` forever,
  -- permanently masking every real observation behind it. RESOURCE_
  -- OBSERVATION_FUTURE_SKEW: 5 minutes, the same clock-skew tolerance already
  -- used for scheduled_for bounds in 0229 (calendar prebrief) -- generous
  -- enough for ordinary clock drift between the collector host and the
  -- database, tight enough that a fabricated future timestamp cannot buy
  -- meaningful permanence.
  if p_observed_at > now() + interval '5 minutes' then
    raise exception 'resource_observation_observed_at_in_future';
  end if;

  v_digest := 'sha256:' || encode(public.digest(
    coalesce(p_provider,'') || '|' || coalesce(p_account,'') || '|' || coalesce(p_project,'') || '|' ||
    coalesce(p_product,'') || '|' || coalesce(p_period,'') || '|' || coalesce(p_as_of::text,'') || '|' ||
    coalesce(p_quantity::text,'') || '|' || coalesce(p_quantity_unit,'') || '|' || coalesce(p_allowance::text,'') || '|' ||
    coalesce(p_policy::text,'') || '|' || coalesce(p_estimate::text,'') || '|' || coalesce(p_charge::text,'') || '|' ||
    coalesce(p_measured_capacity::text,'') || '|' || coalesce(p_configured_capacity::text,'') || '|' ||
    coalesce(p_model_route::text,'') || '|' || coalesce(p_state,'') || '|' || coalesce(p_reason,'') || '|' ||
    coalesce(p_source,'') || '|' || coalesce(p_observed_at::text,''), 'sha256'), 'hex');

  select * into v_existing from ops.resource_observation_receipt r where r.idempotency_key = p_idempotency_key;
  if found then
    if v_existing.request_digest <> v_digest then
      raise exception 'resource_observation_key_reuse';
    end if;
    select * into v_row from ops.resource_observation o where o.id = v_existing.resource_observation_id;
    return query select v_row.id, v_row.provider, v_row.state, v_row.reason, v_row.observed_at, true;
    return;
  end if;

  insert into ops.resource_observation (
    provider, account, project, product, period, as_of, quantity, quantity_unit, allowance,
    policy, estimate, charge, measured_capacity, configured_capacity, model_route, state, reason,
    source, observed_at
  ) values (
    p_provider, p_account, p_project, p_product, p_period, p_as_of, p_quantity, p_quantity_unit, p_allowance,
    p_policy, p_estimate, p_charge, p_measured_capacity, p_configured_capacity, p_model_route, p_state, p_reason,
    p_source, p_observed_at
  ) returning * into v_row;

  insert into ops.resource_observation_receipt (
    resource_observation_id, idempotency_key, request_digest, recorded_by_actor_slug
  ) values (v_row.id, p_idempotency_key, v_digest, p_actor_slug);

  return query select v_row.id, v_row.provider, v_row.state, v_row.reason, v_row.observed_at, false;
end;
$$;

comment on function ops.record_resource_observation is
  'DoctorCRE V5-UX-C02/C06 write door: append one collector observation for one provider. Idempotent on p_idempotency_key. No delete, no update -- a superseded observation is a newer row, not an edit.';

revoke all on function ops.record_resource_observation(text,text,text,text,text,timestamp with time zone,numeric,text,numeric,jsonb,numeric,numeric,jsonb,jsonb,jsonb,text,text,text,timestamp with time zone,uuid,text) from public;
grant execute on function ops.record_resource_observation(text,text,text,text,text,timestamp with time zone,numeric,text,numeric,jsonb,numeric,numeric,jsonb,jsonb,jsonb,text,text,text,timestamp with time zone,uuid,text) to carr_writer;

-- Canonical provider list this contract owns. C03-C05 add their own
-- collectors for neon/github/cloudflare without touching this list or this
-- function; a provider not yet backed by any observation always projects
-- here rather than being silently absent from the payload.
create or replace function ops.read_resource_dashboard()
returns jsonb
language sql
stable
set search_path = pg_catalog, ops
as $$
  with providers(provider) as (
    values ('neon'), ('github'), ('cloudflare'), ('local_compute'), ('model_route')
  ),
  latest as (
    select distinct on (o.provider) o.*
    from ops.resource_observation o
    order by o.provider, o.observed_at desc, o.created_at desc
  ),
  -- RESOURCE_OBSERVATION_STALE_AFTER: 15 minutes. A dead collector must not
  -- read back its last-known state forever, and observed_at is
  -- caller-supplied with only a small future-skew guard on the write side
  -- (see ops.record_resource_observation), so freshness has to be
  -- re-checked here, at read time, against the wall clock -- never trusted
  -- from the stored state alone. 15 minutes matches this repo's existing
  -- observation-staleness convention (migrations 0180/0182's scheduler
  -- receipts) and comfortably exceeds the collector's intended run cadence
  -- once ops/launchd/com.carr.resource-collector.plist is wired into
  -- ops/config/services.json with an interval well under this threshold.
  projected as (
    select
      p.provider,
      l.account, l.project, l.product, l.period, l.as_of, l.quantity, l.quantity_unit,
      l.allowance, l.policy, l.estimate, l.charge, l.measured_capacity, l.configured_capacity,
      l.model_route,
      case
        when l.id is null then
          case when p.provider in ('neon','github','cloudflare') then 'unconfigured' else 'collector_absent' end
        when l.observed_at < now() - interval '15 minutes' then 'stale'
        else l.state
      end as state,
      case
        when l.id is null then
          case when p.provider in ('neon','github','cloudflare')
            then 'no collector configured for this provider yet (V5-UX-C03/C04/C05 not built)'
            else 'no collector observation received yet' end
        when l.observed_at < now() - interval '15 minutes' then
          'observation is ' ||
          greatest(0, round(extract(epoch from (now() - l.observed_at)) / 60))::text ||
          ' minute(s) old, past the 15 minute freshness threshold'
        else l.reason
      end as reason,
      l.source,
      l.observed_at
    from providers p
    left join latest l on l.provider = p.provider
  )
  select jsonb_build_object(
    'schema', 'doctorcre-resource-dashboard.v1',
    'generated_at', now(),
    'providers', coalesce(jsonb_agg(
      jsonb_build_object(
        'provider', provider,
        'account', account,
        'project', project,
        'product', product,
        'period', period,
        'as_of', as_of,
        'quantity', quantity,
        'quantity_unit', quantity_unit,
        'allowance', allowance,
        'policy', policy,
        'estimate', estimate,
        'charge', charge,
        'measured_capacity', measured_capacity,
        'configured_capacity', configured_capacity,
        'model_route', model_route,
        'state', state,
        'reason', reason,
        'source', source,
        'observed_at', observed_at
      ) order by provider
    ), '[]'::jsonb)
  )
  from projected;
$$;

comment on function ops.read_resource_dashboard is
  'DoctorCRE V5-UX-C02/C06 read door: the full provider matrix (neon, github, cloudflare, local_compute, model_route), one row each, always present. A provider with no observation reads back unconfigured/collector_absent with an explicit reason; nothing is fabricated.';

revoke all on function ops.read_resource_dashboard() from public;
grant execute on function ops.read_resource_dashboard() to carr_reader;
