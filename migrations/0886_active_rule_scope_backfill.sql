-- rollback: forward-only. Restore the previous admission guard to close scope writes;
-- retain amendment receipts and their scope preimages as immutable history.
-- expand-contract: contract. Replace the statement-only no-op constraint with a
-- constraint accepting scope changes; all existing receipt rows remain valid.
-- lock-review: rule_amendment_receipt is an append-only metadata ledger. Nullable
-- columns avoid a rewrite; constraint replacement takes a brief table lock.
create function ops.rule_scope_is_additive(p_prior jsonb, p_next jsonb)
returns boolean language sql immutable
set search_path = pg_catalog
as $$
  select case when jsonb_typeof(p_prior)='object' and jsonb_typeof(p_next)='object'
    then not exists (
      select 1 from jsonb_each(p_prior) prior
       where not (p_next ? prior.key) or p_next->prior.key is distinct from prior.value
    ) else false end
$$;

alter table ops.rule_amendment_receipt
  add column prior_scope jsonb,
  add column new_scope jsonb,
  drop constraint rule_amendment_receipt_not_noop,
  add constraint rule_amendment_receipt_scope_pair check (
    (prior_scope is null and new_scope is null) or
    (prior_scope is not null and new_scope is not null
     and ops.rule_scope_is_additive(prior_scope,new_scope)
     and prior_scope is distinct from new_scope)),
  add constraint rule_amendment_receipt_not_noop check (
    prior_statement_hash <> new_statement_hash or
    (prior_scope is not null and new_scope is not null and prior_scope <> new_scope));

comment on table ops.rule_amendment_receipt is
  'Append-only authorized statement amendments and additive scope backfills. Nullable prior_scope/new_scope identify scope changes without rewriting historical statement receipts.';

create function ops.amend_rule_scope(
  p_rule_id uuid, p_new_scope jsonb, p_new_statement text, p_base_version integer,
  p_idempotency_key text, p_reason text
) returns jsonb language plpgsql security definer
set search_path = ops, public, pg_temp
as $$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule public.rule%rowtype;
  v_prior ops.rule_amendment_receipt%rowtype;
  v_receipt ops.rule_amendment_receipt%rowtype;
  v_new_statement text := btrim(coalesce(p_new_statement,''));
  v_prior_hash text;
  v_new_hash text;
  v_legacy_note text;
  v_contract jsonb;
  v_contract_hash text;
  v_amended_at timestamptz := now();
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'scope amendment requires Joe authority';
  end if;
  select id into v_actor_id from public.actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then raise exception 'authority actor is not an active human'; end if;
  if btrim(coalesce(p_idempotency_key,''))='' or btrim(coalesce(p_reason,''))='' then
    raise exception 'amendment idempotency key and rationale are required';
  end if;
  if v_new_statement='' then raise exception 'a rule cannot be amended to empty text'; end if;
  if p_new_scope is null or jsonb_typeof(p_new_scope)<>'object' then
    raise exception 'invalid_object: scope must be a JSON object';
  end if;
  if p_base_version is null then raise exception 'version_conflict: fresh base_version required'; end if;

  -- Share the statement writer's lock and receipt namespace.
  perform pg_advisory_xact_lock(hashtextextended('rule-amendment:'||p_idempotency_key,0));
  select * into v_prior from ops.rule_amendment_receipt where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.new_scope is distinct from p_new_scope
       or v_prior.new_statement is distinct from v_new_statement
       or v_prior.rule_version_before is distinct from p_base_version
       or v_prior.rationale is distinct from btrim(p_reason)
       or v_prior.amended_by is distinct from v_actor_id then
      raise exception 'rule amendment idempotency key was reused with different input';
    end if;
    select * into v_rule from public.rule where id=p_rule_id for update;
    if not found or v_rule.status<>'active'
       or v_rule.version is distinct from v_prior.rule_version_after
       or v_rule.statement is distinct from v_prior.new_statement
       or v_rule.scope is distinct from v_prior.new_scope then
      raise exception 'rule amendment replay refused: current rule no longer matches the immutable amendment';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'rule_id',p_rule_id,
      'rule_version_before',v_prior.rule_version_before,'rule_version_after',v_prior.rule_version_after,
      'amendment_receipt_id',v_prior.id);
  end if;

  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule not found'; end if;
  if v_rule.status<>'active' then raise exception 'scope backfill requires an active rule'; end if;
  if v_rule.version is distinct from p_base_version then
    raise exception 'version_conflict: fresh base_version required';
  end if;
  if not ops.rule_scope_is_additive(v_rule.scope,p_new_scope) then
    raise exception 'active_rule_scope_frozen: preserve every stored scope key and value';
  end if;
  if v_rule.scope=p_new_scope then raise exception 'scope amendment is a no-op'; end if;

  v_prior_hash := encode(public.digest(v_rule.statement,'sha256'),'hex');
  v_new_hash := encode(public.digest(v_new_statement,'sha256'),'hex');
  v_legacy_note := ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at);
  v_contract := jsonb_build_object(
    'rule_id',v_rule.id,'rule_version_before',v_rule.version,'rule_version_after',v_rule.version+1,
    'prior_statement_hash',v_prior_hash,'new_statement_hash',v_new_hash,
    'prior_scope',v_rule.scope,'new_scope',p_new_scope,
    'actor_id',v_actor_id,'rationale',btrim(p_reason),'legacy_admission',v_legacy_note,'amended_at',v_amended_at);
  v_contract_hash := encode(public.digest(v_contract::text,'sha256'),'hex');
  insert into ops.rule_amendment_receipt
    (idempotency_key,rule_id,rule_version_before,rule_version_after,prior_statement_hash,
     new_statement,new_statement_hash,prior_scope,new_scope,amended_by,rationale,
     legacy_admission,contract_hash,amended_at)
  values (p_idempotency_key,v_rule.id,v_rule.version,v_rule.version+1,v_prior_hash,
    v_new_statement,v_new_hash,v_rule.scope,p_new_scope,v_actor_id,btrim(p_reason),
    v_legacy_note,v_contract_hash,v_amended_at)
  returning * into v_receipt;
  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('amendment:'||p_idempotency_key,'amendment','rule',v_rule.id,v_actor_id,
    'additive scope backfill: '||btrim(p_reason),v_contract_hash,'{}'::text[]);
  update public.rule set statement=v_new_statement,scope=p_new_scope
   where id=v_rule.id and version=p_base_version;
  if not found then raise exception 'version_conflict: rule amendment raced'; end if;
  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'rule_version_before',v_rule.version,'rule_version_after',v_rule.version+1,
    'amendment_receipt_id',v_receipt.id);
end $$;
revoke all on function ops.amend_rule_scope(uuid,jsonb,text,integer,text,text) from public;
grant execute on function ops.amend_rule_scope(uuid,jsonb,text,integer,text,text) to carr_authority;

CREATE OR REPLACE FUNCTION ops.require_rule_admission() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
declare
  a ops.rule_admission%rowtype;
  v_approval ops.rule_approval_receipt%rowtype;
begin
  if tg_op='UPDATE' and old.status='retired' then
    raise exception 'retired rule % is immutable',old.id;
  end if;
  if tg_op='UPDATE' and old.status='active' and new.scope is distinct from old.scope then
    if new.status<>'active'
       or (to_jsonb(new)-array['scope','statement']) is distinct from
          (to_jsonb(old)-array['scope','statement'])
       or not ops.rule_scope_is_additive(old.scope,new.scope)
       or not exists (
         select 1 from ops.rule_amendment_receipt ar
          where ar.rule_id=old.id and ar.rule_version_before=old.version
            and ar.rule_version_after=old.version+1
            and ar.prior_statement_hash=encode(public.digest(old.statement,'sha256'),'hex')
            and ar.new_statement=new.statement
            and ar.prior_scope=old.scope and ar.new_scope=new.scope
       ) then
      raise exception 'active_rule_scope_frozen: additive scope change requires an exact authority receipt';
    end if;
    return new;
  end if;
  -- Once the approval receipt exists, the entire rule row is immutable except
  -- for the three exact authority transitions owned below: proposed -> active
  -- in ops.approve_rule, proposed/active -> retired in ops.retire_rule, and
  -- (new, 0349) active -> active with ONLY the statement changed, in
  -- ops.amend_rule_statement. This also blocks no-op UPDATEs that would
  -- otherwise bump the optimistic version and silently make an active receipt
  -- stale through trg_touch_row.
  if tg_op='UPDATE'
     and exists (select 1 from ops.rule_approval_receipt where rule_id=old.id) then
    if old.status='proposed' and new.status='active'
       and new.id is not distinct from old.id
       and new.statement is not distinct from old.statement
       and new.human_quote is not distinct from old.human_quote
       and new.taught_by is not distinct from old.taught_by
       and new.scope is not distinct from old.scope
       and new.personal_to is not distinct from old.personal_to
       and new.supersedes is not distinct from old.supersedes
       and new.created_at is not distinct from old.created_at
       and new.version is not distinct from old.version
       and new.updated_at is not distinct from old.updated_at
       and new.retired_by is not distinct from old.retired_by
       and new.retired_at is not distinct from old.retired_at then
      null; -- exact activation fields are validated below
    elsif old.status in ('proposed','active') and new.status='retired'
       and new.id is not distinct from old.id
       and new.statement is not distinct from old.statement
       and new.human_quote is not distinct from old.human_quote
       and new.taught_by is not distinct from old.taught_by
       and new.scope is not distinct from old.scope
       and new.personal_to is not distinct from old.personal_to
       and new.activated_by is not distinct from old.activated_by
       and new.activated_at is not distinct from old.activated_at
       and new.enforcement is not distinct from old.enforcement
       and new.supersedes is not distinct from old.supersedes
       and new.created_at is not distinct from old.created_at
       and new.version is not distinct from old.version
       and new.updated_at is not distinct from old.updated_at then
      null; -- exact retirement actor/receipt is validated below
    elsif old.status='active' and new.status='active'
       and new.id is not distinct from old.id
       and new.statement is distinct from old.statement
       and new.human_quote is not distinct from old.human_quote
       and new.taught_by is not distinct from old.taught_by
       and new.scope is not distinct from old.scope
       and new.personal_to is not distinct from old.personal_to
       and new.activated_by is not distinct from old.activated_by
       and new.activated_at is not distinct from old.activated_at
       and new.enforcement is not distinct from old.enforcement
       and new.supersedes is not distinct from old.supersedes
       and new.created_at is not distinct from old.created_at
       and new.version is not distinct from old.version
       and new.updated_at is not distinct from old.updated_at
       and new.retired_by is not distinct from old.retired_by
       and new.retired_at is not distinct from old.retired_at
       and exists (
         select 1 from ops.rule_amendment_receipt ar
          where ar.rule_id=old.id
            and ar.rule_version_before=old.version
            and ar.prior_statement_hash=encode(digest(old.statement,'sha256'),'hex')
            and ar.new_statement=new.statement
       ) then
      null; -- exact amendment receipt is validated below
    else
      raise exception 'approved rule % is immutable except through exact Joe approval, retirement or amendment',new.id;
    end if;
  end if;
  if tg_op='UPDATE' and old.status is distinct from 'retired' and new.status='retired' then
    if new.retired_by is null or new.retired_at is null or not exists (
      select 1 from ops.rule_retirement_receipt rr
       where rr.rule_id=old.id and rr.actor_id=new.retired_by
         and rr.rule_version_before=old.version
         and rr.statement_hash=encode(digest(old.statement,'sha256'),'hex')
         and rr.previous_status=old.status
    ) then
      raise exception 'rule % cannot retire without an exact Joe authority receipt',new.id;
    end if;
  end if;
  if not (new.status='active' and
          (tg_op='INSERT' or old.status is distinct from 'active')) then
    return new;
  end if;
  if new.activated_by is null then
    raise exception 'rule % cannot activate without a human activator',new.id;
  end if;
  select * into a from ops.rule_admission where rule_id=new.id;
  if not found or a.state<>'admitted' then
    raise exception 'rule % cannot activate: admitted rule contract is missing',new.id;
  end if;
  if a.enforcement_status not in ('hard_enforced','authority_enforced') then
    raise exception 'rule % cannot activate: active requires installed enforcement, got %',
      new.id,a.enforcement_status;
  end if;
  select * into v_approval from ops.rule_approval_receipt
   where rule_id=new.id and actor_id=new.activated_by
     and enforcement_status=a.enforcement_status
     and statement_hash=encode(digest(new.statement,'sha256'),'hex')
   order by created_at desc limit 1;
  if not found then
    raise exception 'rule % cannot activate: immutable enforced approval receipt is missing',new.id;
  end if;
  if new.enforcement is distinct from
       (case when v_approval.enforcement_status='hard_enforced' then 'gate' else 'constraint' end) then
    raise exception 'rule % cannot activate: enforcement label does not match approval',new.id;
  end if;
  if exists (
    select 1 from unnest(v_approval.requested_control_keys) as requested(control_key)
     where not exists (
       select 1
         from ops.rule_enforcement_point ep
         join ops.enforcement_control_catalog c using (control_key)
         join ops.rule_control_binding b
           on b.rule_id=ep.rule_id and b.control_key=ep.control_key
        where ep.rule_id=new.id and ep.control_key=requested.control_key
          and ep.installed and c.installed and c.verified_at is not null
          and b.statement_hash=encode(digest(new.statement,'sha256'),'hex')
          and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')
     )
  ) then
    raise exception 'rule % cannot activate: exact requested enforcement is incomplete',new.id;
  end if;
  return new;
end $$;

CREATE OR REPLACE FUNCTION ops.amend_rule_statement(p_rule_id uuid, p_new_statement text, p_idempotency_key text, p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule public.rule%rowtype;
  v_prior ops.rule_amendment_receipt%rowtype;
  v_receipt ops.rule_amendment_receipt%rowtype;
  v_prior_hash text;
  v_new_hash text;
  v_new_statement text;
  v_legacy_note text;
  v_contract jsonb;
  v_contract_hash text;
  v_amended_at timestamptz;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'system rule amendment requires Joe authority; % may teach and participate but cannot replace Joe approval',
      v_actor_slug;
  end if;
  select id into v_actor_id from public.actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then
    raise exception 'authority actor % is not an active human',v_actor_slug;
  end if;
  if btrim(coalesce(p_idempotency_key,''))='' or btrim(coalesce(p_reason,''))='' then
    raise exception 'amendment idempotency key and rationale are required';
  end if;
  v_new_statement := btrim(coalesce(p_new_statement,''));
  if v_new_statement='' then
    raise exception 'a rule cannot be amended to empty text';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-amendment:'||p_idempotency_key,0));
  select * into v_prior from ops.rule_amendment_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.new_scope is not null
       or v_prior.rule_id is distinct from p_rule_id
       or v_prior.new_statement is distinct from v_new_statement
       or v_prior.rationale is distinct from btrim(p_reason)
       or v_prior.amended_by is distinct from v_actor_id then
      raise exception 'rule amendment idempotency key was reused with different input';
    end if;
    select * into v_rule from public.rule where id=p_rule_id for update;
    if not found
       or v_rule.version is distinct from v_prior.rule_version_after
       or v_rule.statement is distinct from v_prior.new_statement then
      raise exception 'rule amendment replay refused: current rule no longer matches the immutable amendment';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'rule_id',p_rule_id,
      'rule_version_before',v_prior.rule_version_before,
      'rule_version_after',v_prior.rule_version_after,
      'amendment_receipt_id',v_prior.id,'legacy_admission',v_prior.legacy_admission);
  end if;

  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status='retired' then
    raise exception 'rule % is retired; a withdrawn rule stays as written',p_rule_id;
  end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, expected proposed or active',p_rule_id,v_rule.status;
  end if;

  v_prior_hash := encode(public.digest(v_rule.statement,'sha256'),'hex');
  v_new_hash   := encode(public.digest(v_new_statement,'sha256'),'hex');
  if v_prior_hash = v_new_hash then
    raise exception 'rule % amendment is a no-op: the new statement hashes identically to the current one',p_rule_id;
  end if;

  -- (0351) Purely descriptive here: unlike ops.retire_rule, nothing below
  -- branches on v_legacy_note -- it is recorded whenever it applies, never
  -- required.
  v_legacy_note := ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at);

  v_amended_at := now();
  v_contract := jsonb_build_object(
    'rule_id',v_rule.id,'rule_version_before',v_rule.version,'rule_version_after',v_rule.version+1,
    'prior_statement_hash',v_prior_hash,'new_statement_hash',v_new_hash,
    'actor_id',v_actor_id,'rationale',btrim(p_reason),'legacy_admission',v_legacy_note,'amended_at',v_amended_at);
  v_contract_hash := encode(public.digest(v_contract::text,'sha256'),'hex');

  insert into ops.rule_amendment_receipt
    (idempotency_key,rule_id,rule_version_before,rule_version_after,prior_statement_hash,
     new_statement,new_statement_hash,amended_by,rationale,legacy_admission,contract_hash,amended_at)
  values (p_idempotency_key,v_rule.id,v_rule.version,v_rule.version+1,v_prior_hash,
          v_new_statement,v_new_hash,v_actor_id,btrim(p_reason),v_legacy_note,v_contract_hash,v_amended_at)
  returning * into v_receipt;

  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('amendment:'||p_idempotency_key,'amendment','rule',v_rule.id,v_actor_id,
          'statement amended by Joe authority: '||btrim(p_reason),v_contract_hash,'{}'::text[]);

  update public.rule set statement=v_new_statement where id=v_rule.id and version=v_rule.version;
  if not found then raise exception 'rule % amendment raced',v_rule.id; end if;

  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'rule_version_before',v_rule.version,'rule_version_after',v_rule.version+1,
    'amendment_receipt_id',v_receipt.id,'legacy_admission',v_receipt.legacy_admission);
end $function$;

-- The current registry extends the live catalog without rewriting historical seals.
create function ops.scac_current_grant_contract() returns jsonb
language sql immutable set search_path = pg_catalog
as $$ select $current_grants${
  "registry_version": "scac-mutation-registry.v118",
  "registry_digest": "sha256:560e16ad4c5383414b36b74da507481d9e840d8c00e45ced6eed6bce2ac3447d",
  "secdef_execute": [
    {
      "config": [
        "search_path=ops, public, pg_temp"
      ],
      "grantee": "carr_authority",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.amend_rule_scope(p_rule_id uuid, p_new_scope jsonb, p_new_statement text, p_base_version integer, p_idempotency_key text, p_reason text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.amend_rule_scope(p_rule_id uuid, p_new_scope jsonb, p_new_statement text, p_base_version integer, p_idempotency_key text, p_reason text):carr_authority:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ]
}$current_grants$::jsonb $$;
revoke all on function ops.scac_current_grant_contract() from public;

create or replace function ops.scac_mutation_catalog_v118_current()
returns boolean language plpgsql stable security definer set search_path=pg_catalog,public,ops,pg_temp as $fn$
declare observed_count integer; observed_digest text; registered_count integer;
  current_grants jsonb := ops.scac_current_grant_contract();
begin
  if not exists (
    select 1 from ops.scac_mutation_registry_version
     where registry_version=current_grants->>'registry_version'
       and registry_digest=current_grants->>'registry_digest'
  ) then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),
  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),
  observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*) filter (where row not in (select value from jsonb_array_elements(current_grants->'secdef_execute'))),
    'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(
      jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C")
        filter (where row not in (select value from jsonb_array_elements(current_grants->'secdef_execute'))),
      '[]'::jsonb)),'UTF8'),'sha256'),'hex'),
    count(*) filter (where row in (select value from jsonb_array_elements(current_grants->'secdef_execute')))
    into observed_count,observed_digest,registered_count from observed;
  if registered_count<>jsonb_array_length(current_grants->'secdef_execute') then return false; end if;
  if observed_count<>1267 or observed_digest<>'sha256:587e762e80c6691d4a07c5b35e48a259c7b8bdf885d8595ffd0c14928c657efa' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,acl.grantee,acl.privilege_type,acl.is_grantable from pg_class c join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>321 or observed_digest<>'sha256:2ece3984f1389d78749dd3055ecf72516f02f97e33d260192e66daff062292e0' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(a.attacl) acl where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0 and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>15 or observed_digest<>'sha256:f7a33d14ce431ba588cc07dff07fc9fd91a0dcdb41112de6fecd390fb42dc098' then return false; end if;
  with recursive connected(oid) as (
    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' and not rolcanlogin and not rolsuper union
    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname~'^carr_' and other.rolname<>'carr_ci' and not other.rolcanlogin and not other.rolsuper
  ), role_rows as (
    select 'db-role:'||r.rolname ingress_key,jsonb_build_object('ingress_key','db-role:'||r.rolname,'row_kind','role','role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,'superuser',r.rolsuper,'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row from pg_roles r where r.oid in(select oid from connected)
  ), membership_rows as (
    select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,'row_kind','membership','role',role.rolname,'member',member.rolname,'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row from pg_auth_members m join pg_roles role on role.oid=m.roleid join pg_roles member on member.oid=m.member where m.roleid in(select oid from connected) and m.member in(select oid from connected)
  ), ownership_rows as (
    select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner' union all
    select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname) row from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
  ), observed as (select * from role_rows union all select * from membership_rows union all select * from ownership_rows)
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if exists (select 1 from pg_auth_members m join pg_roles g on g.oid=m.roleid join pg_roles mem on mem.oid=m.member where mem.rolname~'^carr_' and (g.rolsuper or g.rolname~'^(neon_|pg_)')) then return false; end if;
  return observed_count=13 and observed_digest='sha256:93724fe71ed216afac9b4bf48eee500e693de057cae0b59f59ff8e68cc6a2bc0';
end $fn$;
