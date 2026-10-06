-- 0482_rule_delivery_binding_writer.sql
--
-- SIEP-12 correctly refuses an active rule without a delivery layer, but the
-- only delivery writer historically synchronized an already-active reviewed
-- map and refused proposed rules. That made the valid state unreachable: a
-- proposed rule could not receive a tag in a committed transaction, and an
-- untagged rule could not activate. The admitted projection now supplies the
-- delivery decision and approve-rule binds it in the same transaction as the
-- control bindings, receipt, and activation.

create or replace function ops.bind_rule_delivery(
  p_rule_id uuid,
  p_reason text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_actor_slug text;
  v_rule rule%rowtype;
  v_admission ops.rule_admission%rowtype;
  v_delivery jsonb;
  v_load_layer text;
  v_packs text[];
  v_scope text;
  v_why text;
  v_digest text;
  v_existing ops.rule_load_layer%rowtype;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'rule-delivery binding requires Joe authority; % cannot bind system delivery',
      v_actor_slug;
  end if;
  if btrim(coalesce(p_reason,''))='' then
    raise exception 'rule-delivery binding reason is required';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-delivery-binding:'||p_rule_id::text,0));
  select * into v_rule from rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, only proposed rules may be bound and active rules may only be verified',
      p_rule_id,v_rule.status;
  end if;

  select coalesce(owner.slug,'shared') into v_scope
    from rule r left join actor owner on owner.id=r.personal_to
   where r.id=p_rule_id;
  if v_scope not in ('shared','joe','dell') then
    raise exception 'rule % has unsupported delivery scope %',p_rule_id,v_scope;
  end if;

  -- A durable row can predate this writer (migrations and existing acceptance
  -- fixtures used the owner-only table directly). Verify that legacy prebind
  -- instead of demanding it be rewritten through a newer admission shape.
  -- Active approval replay uses the same exact verification path.
  select * into v_existing from ops.rule_load_layer where rule_id=p_rule_id;
  if found then
    if v_existing.short_id<>left(p_rule_id::text,8)
       or v_existing.scope<>v_scope
       or v_existing.load_layer not in ('layer0','control','pack')
       or (v_existing.load_layer='layer0' and
           (cardinality(v_existing.packs)<>0 or nullif(btrim(coalesce(v_existing.why,'')),'') is null))
       or (v_existing.load_layer='pack' and cardinality(v_existing.packs)=0)
       or exists (select 1 from unnest(v_existing.packs) pack where pack='' or pack='*') then
      raise exception 'rule % delivery binding no longer matches its durable identity or activation contract',
        p_rule_id;
    end if;
    return jsonb_build_object(
      'ok',true,'replayed',true,'rule_id',p_rule_id,
      'load_layer',v_existing.load_layer,'packs',v_existing.packs,'scope',v_existing.scope);
  end if;
  if v_rule.status='active' then
    if v_existing.rule_id is null then
      raise exception 'active rule % lacks its delivery binding',p_rule_id;
    end if;
  end if;

  select * into v_admission from ops.rule_admission
   where rule_id=p_rule_id and state='admitted';
  if not found then
    raise exception 'rule % cannot bind delivery: admitted rule contract is missing',p_rule_id;
  end if;
  v_delivery := v_admission.projection->'delivery';
  if v_delivery is null or jsonb_typeof(v_delivery)<>'object' then
    raise exception 'rule % delivery projection is not activation-safe: projection.delivery is required',
      p_rule_id;
  end if;
  v_load_layer := btrim(coalesce(v_delivery->>'load_layer',''));
  if v_load_layer not in ('layer0','control','pack') then
    raise exception 'rule % delivery projection has invalid load_layer %',p_rule_id,v_load_layer;
  end if;
  if jsonb_typeof(v_delivery->'packs') is distinct from 'array'
     or exists (select 1 from jsonb_array_elements(v_delivery->'packs') item
                 where jsonb_typeof(item)<>'string') then
    raise exception 'rule % delivery projection packs must be an array of names',p_rule_id;
  end if;
  select coalesce(array_agg(pack order by pack),'{}'::text[]) into v_packs
    from (select distinct btrim(value) as pack
            from jsonb_array_elements_text(v_delivery->'packs') item(value)) named;
  if exists (select 1 from unnest(v_packs) pack where pack='' or pack='*') then
    raise exception 'rule % delivery projection contains an empty or wildcard pack',p_rule_id;
  end if;
  v_why := nullif(btrim(coalesce(v_delivery->>'why','')),'');
  if v_load_layer='layer0' and (cardinality(v_packs)<>0 or v_why is null) then
    raise exception 'rule % layer0 delivery must be unconditional and explain why',p_rule_id;
  end if;
  if v_load_layer='pack' and cardinality(v_packs)=0 then
    raise exception 'rule % pack delivery names no pack',p_rule_id;
  end if;
  v_digest := encode(digest(v_delivery::text,'sha256'),'hex');

  insert into ops.rule_load_layer
    (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
  values
    (p_rule_id,left(p_rule_id::text,8),v_load_layer,v_packs,v_scope,v_why,
     'ops.bind_rule_delivery',v_digest)
  on conflict (rule_id) do update set
    short_id=excluded.short_id,
    load_layer=excluded.load_layer,
    packs=excluded.packs,
    scope=excluded.scope,
    why=excluded.why,
    source=excluded.source,
    map_digest=excluded.map_digest,
    updated_at=now();

  return jsonb_build_object(
    'ok',true,'replayed',false,'rule_id',p_rule_id,
    'load_layer',v_load_layer,'packs',v_packs,'scope',v_scope,'map_digest',v_digest);
end $$;

-- Replace only the public wrapper installed by 0479. Its private continuation
-- remains the reviewed receipt/activation implementation.
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

  perform ops.bind_rule_delivery(p_rule_id,p_reason);
  perform ops.bind_rule_controls(p_rule_id,v_bind_controls,p_reason);
  return ops.approve_rule_receipt_activation_v1(
    p_rule_id,p_policy_kind,p_control_keys,p_idempotency_key,p_reason);
end $$;

revoke all on function ops.bind_rule_delivery(uuid,text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.approve_rule(uuid,text,text[],text,text)
  from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.approve_rule(uuid,text,text[],text,text) to carr_authority;

do $$
declare
  v_definition text;
begin
  if has_function_privilege('carr_authority',
       'ops.bind_rule_delivery(uuid,text)'::regprocedure,'execute') then
    raise exception '0482 FAILED: authority role can bypass atomic delivery binding';
  end if;
  select pg_get_functiondef(
      'ops.approve_rule(uuid,text,text[],text,text)'::regprocedure)
    into v_definition;
  if v_definition not like '%ops.bind_rule_delivery%'
     or v_definition not like '%ops.bind_rule_controls%'
     or v_definition not like '%ops.approve_rule_receipt_activation_v1%'
     or strpos(v_definition,'ops.bind_rule_delivery') >= strpos(v_definition,'ops.bind_rule_controls')
     or strpos(v_definition,'ops.bind_rule_controls') >= strpos(v_definition,'ops.approve_rule_receipt_activation_v1') then
    raise exception '0482 FAILED: approval does not bind delivery, bind controls, and activate in order';
  end if;
end $$;
