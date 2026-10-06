-- V5-F05: authoritative typed rule contracts and the actor-scoped universe read.
-- No active rule can disappear: an active visible rule without an exact current
-- contract is returned in missing_rule_ids and the JS gate makes coverage partial.

create table ops.rule_f05_contract (
  binding_seq bigint generated always as identity primary key,
  idempotency_key uuid not null unique,
  rule_id uuid not null references public.rule(id),
  rule_version integer not null check (rule_version > 0),
  statement_hash text not null check (statement_hash ~ '^sha256:[0-9a-f]{64}$'),
  input_contract jsonb not null check (jsonb_typeof(input_contract) = 'object'),
  contract jsonb not null check (jsonb_typeof(contract) = 'object'),
  contract_hash text not null check (contract_hash ~ '^sha256:[0-9a-f]{64}$'),
  bound_by uuid not null references public.actor(id),
  bound_at timestamptz not null default clock_timestamp()
);

comment on table ops.rule_f05_contract is
  'V5-F05 append-only typed projections of durable rules. The latest exact rule-version/statement binding is current; history is never rewritten. Missing bindings remain explicit in ops.f05_rule_universe.';

create index rule_f05_contract_current_idx
  on ops.rule_f05_contract(rule_id, rule_version, binding_seq desc);

create or replace function ops.rule_f05_contract_append_only()
returns trigger language plpgsql set search_path=pg_catalog,ops as $$
begin
  raise exception 'rule_f05_contract is append-only; % is refused', tg_op;
end $$;

create trigger rule_f05_contract_append_only_row before update or delete
  on ops.rule_f05_contract for each row execute function ops.rule_f05_contract_append_only();
create trigger rule_f05_contract_append_only_truncate before truncate
  on ops.rule_f05_contract for each statement execute function ops.rule_f05_contract_append_only();

create or replace function ops.bind_f05_rule_contract(
  p_rule_id uuid, p_contract jsonb, p_idempotency_key uuid
) returns jsonb
language plpgsql volatile security definer
set search_path=pg_catalog,public,ops as $$
declare
  v_rule public.rule%rowtype;
  v_existing ops.rule_f05_contract%rowtype;
  v_bound_by public.actor%rowtype;
  v_teacher public.actor%rowtype;
  v_personal public.actor%rowtype;
  v_now timestamptz := clock_timestamp();
  v_statement_hash text;
  v_scope text;
  v_full jsonb;
  v_hash text;
  v_unknown text[];
  v_login text;
begin
  -- Authority comes from the LOGIN, as in 0161/0482/0542: ops.authority_actor_slug()
  -- reads session_user and raises for anything but an admitted partner
  -- authority principal. The carr.* session values are caller-settable, so they
  -- can only narrow (they must agree with the login), never establish, Joe.
  v_login := ops.authority_actor_slug();
  if v_login is distinct from 'joe' then
    raise exception 'bind_f05_rule_contract requires the Joe authority login; this authority session is %', v_login
      using errcode = '42501';
  end if;
  if nullif(current_setting('carr.acting_actor_slug',true),'') is distinct from v_login
     or nullif(current_setting('carr.verified_human_actor_slug',true),'') is distinct from v_login then
    raise exception 'bind_f05_rule_contract session actor context disagrees with the authority login'
      using errcode = '42501';
  end if;
  select * into v_bound_by from public.actor where slug='joe' and kind='human' and active;
  if v_bound_by.id is null then raise exception 'Joe authority actor is unavailable'; end if;

  if jsonb_typeof(p_contract) is distinct from 'object' then
    raise exception 'F05 input contract must be a JSON object';
  end if;
  select array_agg(k order by k collate "C") into v_unknown
    from jsonb_object_keys(p_contract) k
   where k not in ('rule_class','mandatory','trigger','control_effect','summary',
                   'code_enforcement','tests','no_machine_control_reason','retirement',
                   'relations','scoped_validity');
  if cardinality(coalesce(v_unknown,'{}'::text[])) > 0 then
    raise exception 'F05 input contract carries server-derived or unknown fields: %', v_unknown;
  end if;
  if not (p_contract ? 'rule_class' and p_contract ? 'mandatory'
          and p_contract ? 'trigger' and p_contract ? 'retirement') then
    raise exception 'F05 input contract requires rule_class, mandatory, trigger, and retirement';
  end if;

  select * into v_existing from ops.rule_f05_contract where idempotency_key=p_idempotency_key;
  if found then
    if v_existing.rule_id is distinct from p_rule_id
       or v_existing.input_contract is distinct from p_contract then
      raise exception 'F05 contract idempotency key conflicts with its prior request';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'binding_seq',v_existing.binding_seq,
      'contract_hash',v_existing.contract_hash,'contract',v_existing.contract);
  end if;

  perform pg_advisory_xact_lock(hashtextextended('f05-rule-contract:'||p_rule_id::text,0));
  select * into v_rule from public.rule where id=p_rule_id for key share;
  if v_rule.id is null then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, so it cannot receive a current F05 contract',p_rule_id,v_rule.status;
  end if;
  select * into v_teacher from public.actor where id=v_rule.taught_by and active;
  if v_teacher.id is null then raise exception 'rule % has no active teaching actor',p_rule_id; end if;
  if v_rule.personal_to is not null then
    select * into v_personal from public.actor where id=v_rule.personal_to and kind='human' and active;
    if v_personal.id is null then raise exception 'rule % has no active personal-scope owner',p_rule_id; end if;
    v_scope := v_personal.slug;
  else
    v_scope := 'shared';
  end if;

  v_statement_hash := 'sha256:'||encode(public.digest(convert_to(v_rule.statement,'UTF8'),'sha256'),'hex');
  v_full := p_contract || jsonb_build_object(
    'rule_id',v_rule.id::text,
    'version',v_rule.version,
    'scope',v_scope,
    'owner',v_teacher.slug,
    'binding_text',v_rule.statement,
    'provenance',jsonb_build_object(
      'source_record_id','rule:'||v_rule.id::text,
      'source_version',v_rule.version,
      'source_content_digest',v_statement_hash,
      'retrieved_at',to_char(v_now at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')));
  v_hash := 'sha256:'||encode(public.digest(
    convert_to(ops.scac_canonical_json(v_full),'UTF8'),'sha256'),'hex');

  insert into ops.rule_f05_contract
    (idempotency_key,rule_id,rule_version,statement_hash,input_contract,contract,
     contract_hash,bound_by,bound_at)
  values
    (p_idempotency_key,v_rule.id,v_rule.version,v_statement_hash,p_contract,v_full,
     v_hash,v_bound_by.id,v_now)
  returning * into v_existing;

  return jsonb_build_object('ok',true,'replayed',false,'binding_seq',v_existing.binding_seq,
    'contract_hash',v_existing.contract_hash,'contract',v_existing.contract);
end $$;

comment on function ops.bind_f05_rule_contract(uuid,jsonb,uuid) is
  'V5-F05 Joe-authority append-only binder. Identity, source text, version, scope, owner and provenance are derived from public.rule; only typed classification is accepted from the caller.';

create or replace function ops.f05_rule_universe(p_action text, p_resource_class text)
returns jsonb
language plpgsql stable security definer
set search_path=pg_catalog,public,ops as $$
declare
  v_tenant text := nullif(current_setting('carr.organization_tenant_id',true),'');
  v_actor text := nullif(current_setting('carr.acting_actor_slug',true),'');
  v_sponsor text := nullif(current_setting('carr.sponsoring_human_slug',true),'');
  v_now timestamptz := transaction_timestamp();
  v_result jsonb;
begin
  if v_tenant is distinct from 'carr-internal' then
    raise exception 'F05 rule universe requires the server-derived carr-internal tenant';
  end if;
  if v_actor is null or not exists(select 1 from public.actor where slug=v_actor and active) then
    raise exception 'F05 rule universe requires an active server-derived acting actor';
  end if;
  if v_sponsor is not null and not exists(
      select 1 from public.actor where slug=v_sponsor and kind='human' and active) then
    raise exception 'F05 rule universe received an invalid server-derived sponsor scope';
  end if;
  if p_action is null or btrim(p_action)='' or length(p_action)>128
     or p_resource_class is null or btrim(p_resource_class)='' or length(p_resource_class)>128 then
    raise exception 'F05 rule universe requires bounded action and resource_class facts';
  end if;

  with active as materialized (
    select r.*, personal.slug personal_slug,
           'sha256:'||encode(public.digest(convert_to(r.statement,'UTF8'),'sha256'),'hex') statement_hash
      from public.rule r
      left join public.actor personal on personal.id=r.personal_to
     where r.status='active'
       and (r.personal_to is null or personal.slug=v_sponsor)
  ), projected as materialized (
    select a.id rule_id,c.contract
      from active a
      join lateral (
        select b.contract from ops.rule_f05_contract b
         where b.rule_id=a.id and b.rule_version=a.version
           and b.statement_hash=a.statement_hash
         order by b.binding_seq desc limit 1
      ) c on true
  ), actions as (
    select p_action value
    union
    select e.value from projected p
    cross join lateral jsonb_array_elements_text(
      case when jsonb_typeof(p.contract#>'{trigger,action}')='array'
           then p.contract#>'{trigger,action}' else '[]'::jsonb end) e
  ), resources as (
    select p_resource_class value
    union
    select e.value from projected p
    cross join lateral jsonb_array_elements_text(
      case when jsonb_typeof(p.contract#>'{trigger,resource_class}')='array'
           then p.contract#>'{trigger,resource_class}' else '[]'::jsonb end) e
  ), counts as (
    select (select count(*) from active) active_count,
           (select count(*) from projected) projected_count,
           coalesce((select max(epoch) from ops.scac_policy_epoch),1) +
             coalesce((select max(binding_seq) from ops.rule_f05_contract),0) universe_version
  )
  select jsonb_build_object(
    'observed_at',to_char(v_now at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'active_rule_count',counts.active_count,
    'projected_rule_count',counts.projected_count,
    'missing_rule_ids',coalesce((select jsonb_agg(a.id::text order by a.id::text collate "C")
      from active a where not exists(select 1 from projected p where p.rule_id=a.id)),'[]'::jsonb),
    'policy',jsonb_build_object(
      'schema_version','doctorcre-v5-f05-rule-universe.v1',
      'universe_version',counts.universe_version,
      'tenant','carr-internal',
      'completeness',case when counts.active_count=counts.projected_count
        then 'complete_authoritative_universe' else 'partial_unknown_coverage' end,
      'declared_actions',(select jsonb_agg(value order by value collate "C") from actions),
      'declared_resource_classes',(select jsonb_agg(value order by value collate "C") from resources),
      'rules',coalesce((select jsonb_agg(contract order by rule_id::text collate "C") from projected),'[]'::jsonb)
    )) into v_result from counts;
  return v_result;
end $$;

comment on function ops.f05_rule_universe(text,text) is
  'V5-F05 actor/sponsor-scoped authoritative active-rule census. Every unprojectable visible rule is named in missing_rule_ids; callers cannot assert completeness.';

revoke all on table ops.rule_f05_contract from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.bind_f05_rule_contract(uuid,jsonb,uuid) from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.bind_f05_rule_contract(uuid,jsonb,uuid) to carr_authority;
revoke all on function ops.f05_rule_universe(text,text) from public,carr_reader,carr_jobs,carr_authority;
grant execute on function ops.f05_rule_universe(text,text) to carr_writer;

do $f05_install_checks$
begin
  if has_table_privilege('carr_reader','ops.rule_f05_contract','select')
     or has_table_privilege('carr_writer','ops.rule_f05_contract','insert') then
    raise exception 'F05 typed contracts must have no direct runtime table grant';
  end if;
  if not has_function_privilege('carr_writer','ops.f05_rule_universe(text,text)','execute')
     or has_function_privilege('carr_reader','ops.f05_rule_universe(text,text)','execute') then
    raise exception 'F05 universe reader grant drifted';
  end if;
  if not has_function_privilege('carr_authority','ops.bind_f05_rule_contract(uuid,jsonb,uuid)','execute')
     or has_function_privilege('carr_writer','ops.bind_f05_rule_contract(uuid,jsonb,uuid)','execute') then
    raise exception 'F05 contract binder grant drifted';
  end if;
end $f05_install_checks$;
