-- 0479_rule_control_binding_writer.sql
--
-- A newly taught rule could name a real installed control in teach/admit-rule,
-- yet approve-rule required a separate ops.rule_control_binding row which no
-- activation path could write. Historical one-off migrations wrote those
-- rows, leaving every future rule blocked on bespoke SQL. approve-rule is
-- already the public verb for this transition, so this adds a private binding
-- primitive and invokes it inside that verb's runner-owned database transaction.

create or replace function ops.bind_rule_controls(
  p_rule_id uuid,
  p_control_keys text[],
  p_reason text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_actor_slug text;
  v_rule rule%rowtype;
  v_requested text[];
  v_available text[];
  v_missing text[];
  v_statement_hash text;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'rule-control binding requires Joe authority; % cannot bind system enforcement',
      v_actor_slug;
  end if;
  if btrim(coalesce(p_reason,''))='' then
    raise exception 'rule-control binding reason is required';
  end if;

  v_requested := array(
    select distinct btrim(u.control_key)
      from unnest(coalesce(p_control_keys,'{}'::text[])) as u(control_key)
     where btrim(u.control_key)<>''
     order by btrim(u.control_key));
  if cardinality(v_requested)=0 then
    raise exception 'at least one registered control key is required';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-control-binding:'||p_rule_id::text,0));
  select * into v_rule from rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, only proposed rules may be bound and active rules may only be verified',
      p_rule_id,v_rule.status;
  end if;
  v_statement_hash := encode(digest(v_rule.statement,'sha256'),'hex');

  select coalesce(array_agg(c.control_key order by c.control_key),'{}'::text[])
    into v_available
    from ops.enforcement_control_catalog c
   where c.control_key=any(v_requested)
     and c.installed and c.verified_at is not null
     and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema');
  v_missing := array(
    select requested.control_key
      from unnest(v_requested) as requested(control_key)
     where not (requested.control_key=any(v_available))
     order by requested.control_key);
  if cardinality(v_missing)>0 or cardinality(v_available)<>cardinality(v_requested) then
    raise exception 'rule-control binding refused: registered installed controls are missing %',v_missing;
  end if;

  -- An approved contract is immutable. Approval replay reaches this function,
  -- so an ACTIVE rule is accepted only as an exact no-write verification.
  if v_rule.status='active' then
    if exists (
      select 1 from unnest(v_requested) requested(control_key)
       where not exists (
         select 1 from ops.rule_control_binding b
          where b.rule_id=p_rule_id
            and b.control_key=requested.control_key
            and b.statement_hash=v_statement_hash)
    ) then
      raise exception 'active rule % lacks its immutable exact control binding',p_rule_id;
    end if;
    return jsonb_build_object(
      'ok',true,'replayed',true,'rule_id',p_rule_id,
      'bound_controls',v_available,'statement_hash',v_statement_hash);
  end if;

  insert into ops.rule_control_binding
    (rule_id,control_key,statement_hash,binding_contract)
  select p_rule_id,c.control_key,v_statement_hash,
         jsonb_build_object(
           'source','ops.bind_rule_controls',
           'rule_id',p_rule_id,
           'rule_version',v_rule.version,
           'statement_hash',v_statement_hash,
           'control_key',c.control_key,
           'implementation_ref',c.implementation_ref,
           'test_ref',c.test_ref,
           'binding_reason',btrim(p_reason),
           'bound_by',v_actor_slug)
    from ops.enforcement_control_catalog c
   where c.control_key=any(v_available)
  on conflict (rule_id,control_key) do update set
    statement_hash=excluded.statement_hash,
    binding_contract=excluded.binding_contract,
    bound_at=now();

  return jsonb_build_object(
    'ok',true,'replayed',false,'rule_id',p_rule_id,
    'bound_controls',v_available,'statement_hash',v_statement_hash);
end $$;

-- Preserve the reviewed receipt/activation implementation as an internal
-- continuation. The public authority function below supplies its formerly
-- missing exact-binding precondition inside the same SQL transaction.
alter function ops.approve_rule(uuid,text,text[],text,text)
  rename to approve_rule_receipt_activation_v1;

revoke all on function ops.approve_rule_receipt_activation_v1(uuid,text,text[],text,text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create or replace function ops.approve_rule(
  p_rule_id uuid,
  p_policy_kind text,
  p_control_keys text[],
  p_idempotency_key text,
  p_reason text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_bind_controls text[];
begin
  v_bind_controls := array(
    select distinct btrim(u.control_key)
      from unnest(coalesce(p_control_keys,'{}'::text[])) as u(control_key)
     where btrim(u.control_key)<>''
     order by btrim(u.control_key));
  if p_policy_kind='human_only'
     and not ('human_authority_runtime'=any(v_bind_controls)) then
    v_bind_controls := array_append(v_bind_controls,'human_authority_runtime');
  end if;

  perform ops.bind_rule_controls(p_rule_id,v_bind_controls,p_reason);
  return ops.approve_rule_receipt_activation_v1(
    p_rule_id,p_policy_kind,p_control_keys,p_idempotency_key,p_reason);
end $$;

revoke all on function ops.bind_rule_controls(uuid,text[],text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.approve_rule(uuid,text,text[],text,text)
  from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.approve_rule(uuid,text,text[],text,text) to carr_authority;

do $$
declare
  v_definition text;
begin
  if has_function_privilege('carr_writer',
       'ops.approve_rule(uuid,text,text[],text,text)'::regprocedure,'execute') then
    raise exception '0479 FAILED: routine writer can approve system rules';
  end if;
  if has_function_privilege('carr_authority',
       'ops.bind_rule_controls(uuid,text[],text)'::regprocedure,'execute')
     or has_function_privilege('carr_authority',
       'ops.approve_rule_receipt_activation_v1(uuid,text,text[],text,text)'::regprocedure,'execute') then
    raise exception '0479 FAILED: authority role can bypass atomic approval';
  end if;
  select pg_get_functiondef(
      'ops.approve_rule(uuid,text,text[],text,text)'::regprocedure)
    into v_definition;
  if v_definition not like '%ops.bind_rule_controls%'
     or v_definition not like '%ops.approve_rule_receipt_activation_v1%' then
    raise exception '0479 FAILED: approval does not bind and activate atomically';
  end if;
end $$;
