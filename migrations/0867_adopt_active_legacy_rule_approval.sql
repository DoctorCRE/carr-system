-- 0867_adopt_active_legacy_rule_approval.sql
-- rollback: create or replace ops.approve_rule from migrations/0482_rule_delivery_binding_writer.sql and ops.bind_rule_controls from migrations/0856_qualify_security_definer_dependencies.sql, then drop function ops.adopt_legacy_active_rule_v1(uuid,text,text[],text,text); receipts, bindings and admissions it wrote are immutable approval history and stay
--
-- WR-000262: APPROVE-RULE CAN ADOPT AN ACTIVE LEGACY RULE INTO HARD ENFORCEMENT.
--
-- On 2026-10-12, 97 active machine_enforceable rules had no approval receipt
-- and read as blocked in ops.v_rule_enforcement_status. 58 of them carry an
-- installed, verified gate-class control (./run.sh rule-approvals, PR 1723).
-- approve-rule refused every one: "active rule <id> lacks its immutable exact
-- control binding". ops.bind_rule_controls (0479) treated an ACTIVE rule only
-- as an approval REPLAY, which verifies an existing binding and never writes
-- one, and ops.approve_rule_receipt_activation_v1 accepts only a proposed
-- rule. Both were right for the rules they were written for. Neither covered
-- a rule that was activated by hand before the receipt system existed, so
-- those rules had no path to a receipt at all. Decision
-- cbe91287-10a2-48d0-bda7-3b1784b4bb93 (Joe, 2026-10-12) removes his approval
-- from everything the system can do itself; the approval verb already runs on
-- the Joe authority connection, so this changes what it can approve, not who.
--
-- WHAT CHANGES.
--   * ops.approve_rule sends an ACTIVE rule for which
--     ops.legacy_rule_admission_note() is non-null (no approval receipt, and
--     activated before the receipt system's first receipt; 0351) to the new
--     internal ops.adopt_legacy_active_rule_v1. Every other rule takes the
--     unchanged 0479/0482 path.
--   * ops.bind_rule_controls writes the exact binding for that same legacy
--     case. For every other active rule it still only verifies, and the
--     refusal text is unchanged.
--   * ops.adopt_legacy_active_rule_v1, in the same transaction, requires Joe
--     authority, the legacy predicate, a Joe-activated rule and an admitted
--     contract whose class equals the requested policy kind, and every named
--     control installed, verified and gate-class. It then writes the
--     enforcement points, flips the admission to hard_enforced (or
--     authority_enforced for human_only), and writes the approval receipt and
--     the activation authority receipt for the rule's CURRENT version.
--
-- WHAT DOES NOT CHANGE. The rule row is not updated, so its version, status,
-- activator and statement stay exactly as they are (require_rule_admission
-- forbids updating a receipted rule anyway). The admitted contract's
-- binding_moment, applicability, projection, reachability and input_contract
-- are kept as admitted and copied into the receipt, so ops.applicable_rules()
-- and approval replay see the same contract. Historical receipts, bindings and
-- admissions of other rules are untouched; no data is written by this file.
-- A post-cutover receiptless active rule, a surfacing or judgment control, an
-- uninstalled or unverified control, a missing admission and a class mismatch
-- all still refuse, each by name. Grants are unchanged: the new function is
-- revoked from every role and is reachable only through ops.approve_rule.

create or replace function ops.bind_rule_controls(
  p_rule_id uuid,
  p_control_keys text[],
  p_reason text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $function$
declare
  v_actor_slug text;
  v_rule public.rule%rowtype;
  v_requested text[];
  v_available text[];
  v_missing text[];
  v_statement_hash text;
  v_legacy_note text;
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
  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, only proposed rules may be bound and active rules may only be verified',
      p_rule_id,v_rule.status;
  end if;
  v_statement_hash := encode(public.digest(v_rule.statement,'sha256'),'hex');

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
  -- so an ACTIVE rule is accepted only as an exact no-write verification --
  -- unless it is a legacy admission (0351): active, receiptless and older
  -- than the receipt system. That rule has never had a binding to verify, and
  -- 0867's adoption path binds it here, inside the same approval transaction.
  if v_rule.status='active' then
    v_legacy_note := ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at);
    if v_legacy_note is null then
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
         || case when v_legacy_note is null then '{}'::jsonb
                 else jsonb_build_object('approval_path','legacy_adoption',
                                         'legacy_admission',v_legacy_note) end
    from ops.enforcement_control_catalog c
   where c.control_key=any(v_available)
  on conflict (rule_id,control_key) do update set
    statement_hash=excluded.statement_hash,
    binding_contract=excluded.binding_contract,
    bound_at=now();

  return jsonb_build_object(
    'ok',true,'replayed',false,'rule_id',p_rule_id,
    'bound_controls',v_available,'statement_hash',v_statement_hash);
end $function$;

create function ops.adopt_legacy_active_rule_v1(
  p_rule_id uuid,
  p_policy_kind text,
  p_control_keys text[],
  p_idempotency_key text,
  p_reason text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $function$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule public.rule%rowtype;
  v_admission ops.rule_admission%rowtype;
  v_legacy_note text;
  v_requested text[];
  v_installed text[];
  v_missing text[];
  v_evidence text[];
  v_status text;
  v_statement_hash text;
  v_contract jsonb;
  v_contract_hash text;
  v_receipt ops.rule_approval_receipt%rowtype;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'system rule approval requires Joe authority; % may teach and participate but cannot replace Joe approval',
      v_actor_slug;
  end if;
  select id into v_actor_id from public.actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then
    raise exception 'authority actor % is not an active human',v_actor_slug;
  end if;
  if p_policy_kind='judgment_advisory' then
    raise exception 'advisory guidance is not an unbreakable rule; build a mechanical control before approval';
  end if;
  if p_policy_kind not in ('machine_enforceable','human_only') then
    raise exception 'unsupported policy kind %',p_policy_kind;
  end if;
  if btrim(coalesce(p_idempotency_key,''))='' or btrim(coalesce(p_reason,''))='' then
    raise exception 'idempotency key and approval reason are required';
  end if;

  -- Normalized exactly as approve_rule_receipt_activation_v1 does, so its
  -- replay path compares this receipt's requested keys byte for byte.
  v_requested := array(
    select distinct btrim(u.control_key)
      from unnest(coalesce(p_control_keys,'{}'::text[])) as u(control_key)
     where btrim(u.control_key)<>'' order by btrim(u.control_key));
  if p_policy_kind='human_only'
     and not ('human_authority_runtime'=any(v_requested)) then
    v_requested := array_append(v_requested,'human_authority_runtime');
    select array_agg(u.control_key order by u.control_key) into v_requested
      from unnest(v_requested) as u(control_key);
  end if;
  if cardinality(v_requested)=0 then
    raise exception 'exact registered controls must be implemented before approval';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-approval:'||p_idempotency_key,0));
  -- A true replay never reaches here: once this function writes a receipt the
  -- rule is no longer a legacy admission, so ops.approve_rule routes the
  -- replay to the 0479 path. A key that already exists here belongs to
  -- another approval.
  if exists (select 1 from ops.rule_approval_receipt where idempotency_key=p_idempotency_key) then
    raise exception 'rule approval idempotency key was reused with different input';
  end if;

  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status<>'active' then
    raise exception 'rule % is %, only an active legacy rule is adopted here',p_rule_id,v_rule.status;
  end if;
  v_legacy_note := ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at);
  if v_legacy_note is null then
    raise exception 'active rule % is not a legacy admission; it cannot be adopted',p_rule_id;
  end if;
  if v_rule.activated_by is distinct from v_actor_id then
    raise exception 'active legacy rule % was not activated by Joe; adoption cannot bind its approval to another activator',
      p_rule_id;
  end if;
  v_statement_hash := encode(public.digest(v_rule.statement,'sha256'),'hex');

  select * into v_admission from ops.rule_admission where rule_id=p_rule_id for update;
  if not found or v_admission.state<>'admitted' then
    raise exception 'active legacy rule % has no admitted rule contract to adopt',p_rule_id;
  end if;
  if v_admission.enforcement_class is distinct from p_policy_kind then
    raise exception 'active legacy rule % is admitted as %, not %; reclassify the legacy admission first',
      p_rule_id,v_admission.enforcement_class,p_policy_kind;
  end if;

  -- The same exact-control test as a fresh approval: every requested control
  -- installed, verified, gate-class and bound to this statement.
  select coalesce(array_agg(c.control_key order by c.control_key),'{}'::text[]),
         coalesce(array_agg(c.test_ref order by c.control_key),'{}'::text[])
    into v_installed,v_evidence
    from ops.enforcement_control_catalog c
    join ops.rule_control_binding b using (control_key)
   where c.installed and c.verified_at is not null
     and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')
     and b.rule_id=p_rule_id
     and b.statement_hash=v_statement_hash
     and c.control_key=any(v_requested);
  v_missing := array(
    select requested.control_key from unnest(v_requested) as requested(control_key)
     where not (requested.control_key=any(v_installed)) order by requested.control_key);
  if cardinality(v_missing)>0 or cardinality(v_installed)<>cardinality(v_requested) then
    raise exception 'rule approval refused: exact enforcement is not installed; missing %',v_missing;
  end if;
  v_status := case when p_policy_kind='human_only'
                   then 'authority_enforced' else 'hard_enforced' end;
  -- Keep the admission's own fixtures and add the controls' tests.
  v_evidence := array(
    select distinct ref
      from unnest(v_admission.fixture_refs || v_evidence) as e(ref)
     where btrim(ref)<>'' order by ref);

  -- The admitted contract is kept as admitted; the receipt records it, so
  -- ops.applicable_rules() and replay compare like with like.
  v_contract := jsonb_build_object(
    'rule_id',p_rule_id,
    'rule_version',v_rule.version,
    'statement_hash',v_statement_hash,
    'enforcement_class',p_policy_kind,
    'enforcement_status',v_status,
    'binding_moment',v_admission.binding_moment,
    'applicability',v_admission.applicability,
    'projection',v_admission.projection,
    'reachability',v_admission.reachability,
    'input_contract',v_admission.input_contract,
    'requested_controls',v_requested,
    'approval_path','legacy_adoption',
    'legacy_admission',v_legacy_note);
  v_contract_hash := encode(public.digest(v_contract::text,'sha256'),'hex');

  update ops.rule_admission set
    enforcement_status=v_status,
    fixture_refs=v_evidence,
    admitted_by=v_actor_id,
    coverage_detail=coalesce(coverage_detail,'{}'::jsonb)
      || jsonb_build_object('requested',v_requested,'installed',v_installed,
                            'missing','{}'::text[],'approval_path','legacy_adoption'),
    version=ops.rule_admission.version+1,
    updated_at=now()
   where rule_id=p_rule_id;

  update ops.rule_enforcement_point set installed=false,verified_at=null
   where rule_id=p_rule_id and not (control_key=any(v_installed));
  insert into ops.rule_enforcement_point
    (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
  select p_rule_id,control_key,implementation_ref,test_ref,enforcement_class,true,verified_at
    from ops.enforcement_control_catalog
   where control_key=any(v_installed)
  on conflict (rule_id,control_key) do update set
    implementation_ref=excluded.implementation_ref,test_ref=excluded.test_ref,
    enforcement_class=excluded.enforcement_class,installed=true,
    verified_at=excluded.verified_at;

  -- The rule row is not updated, so the receipt names the CURRENT version.
  insert into ops.rule_approval_receipt
    (idempotency_key,rule_id,rule_version,statement_hash,actor_id,policy_kind,
     enforcement_status,requested_control_keys,installed_control_keys,reason,
     normalized_contract,contract_hash,evidence_refs)
  values (p_idempotency_key,p_rule_id,v_rule.version,v_statement_hash,v_actor_id,p_policy_kind,
          v_status,v_requested,v_installed,btrim(p_reason),v_contract,v_contract_hash,v_evidence)
  returning * into v_receipt;

  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('approval:'||p_idempotency_key,'activation','rule',p_rule_id,v_actor_id,
          'active legacy rule adopted: exact controls bound and enforcement receipted atomically',
          v_contract_hash,v_evidence);

  return jsonb_build_object(
    'ok',true,'replayed',false,'rule_id',p_rule_id,'policy_status','active',
    'enforcement_status',v_status,'installed_controls',v_installed,
    'pending_controls','{}'::text[],'approval_receipt_id',v_receipt.id,
    'approval_path','legacy_adoption','rule_version',v_rule.version,
    'legacy_admission',v_legacy_note);
end $function$;

revoke all on function ops.adopt_legacy_active_rule_v1(uuid,text,text[],text,text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create or replace function ops.approve_rule(
  p_rule_id uuid,
  p_policy_kind text,
  p_control_keys text[],
  p_idempotency_key text,
  p_reason text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $function$
declare
  v_bind_controls text[];
  v_adopt boolean := false;
  v_status text;
  v_activated_at timestamptz;
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
  -- 0867: an ACTIVE legacy admission (receiptless and older than the receipt
  -- system) is adopted; every other rule takes the unchanged path. Decided
  -- before binding, because binding is what the adoption is.
  select r.status,r.activated_at into v_status,v_activated_at
    from public.rule r where r.id=p_rule_id;
  v_adopt := v_status='active'
    and ops.legacy_rule_admission_note(p_rule_id,v_status,v_activated_at) is not null;
  perform ops.bind_rule_controls(p_rule_id,v_bind_controls,p_reason);
  if v_adopt then
    return ops.adopt_legacy_active_rule_v1(
      p_rule_id,p_policy_kind,p_control_keys,p_idempotency_key,p_reason);
  end if;
  return ops.approve_rule_receipt_activation_v1(
    p_rule_id,p_policy_kind,p_control_keys,p_idempotency_key,p_reason);
end $function$;

comment on function ops.adopt_legacy_active_rule_v1(uuid,text,text[],text,text) is
  'WR-000262 (0867). Internal continuation of ops.approve_rule for an ACTIVE legacy '
  'admission (ops.legacy_rule_admission_note non-null): binds nothing itself (ops.bind_rule_controls '
  'already did), verifies every requested control is installed, verified, gate-class and bound '
  'to the current statement, flips the admitted contract to hard_enforced/authority_enforced '
  'and writes the approval and activation authority receipts for the CURRENT rule version '
  'without updating the rule. Revoked from every role.';

do $wr262_0867$
declare
  v_approve text;
  v_bind text;
begin
  v_approve := pg_get_functiondef('ops.approve_rule(uuid,text,text[],text,text)'::regprocedure);
  v_bind := pg_get_functiondef('ops.bind_rule_controls(uuid,text[],text)'::regprocedure);
  if strpos(v_approve,'ops.bind_rule_delivery') = 0
     or strpos(v_approve,'ops.bind_rule_controls') = 0
     or strpos(v_approve,'ops.adopt_legacy_active_rule_v1') = 0
     or strpos(v_approve,'ops.approve_rule_receipt_activation_v1') = 0
     or strpos(v_approve,'ops.bind_rule_delivery') >= strpos(v_approve,'ops.bind_rule_controls')
     or strpos(v_approve,'ops.bind_rule_controls') >= strpos(v_approve,'ops.adopt_legacy_active_rule_v1') then
    raise exception '0867 FAILED: approval no longer binds delivery, then controls, then receipts';
  end if;
  if strpos(v_bind,'lacks its immutable exact control binding') = 0
     or strpos(v_bind,'ops.legacy_rule_admission_note') = 0 then
    raise exception '0867 FAILED: a non-legacy active rule is no longer verify-only';
  end if;
  if has_function_privilege('carr_authority',
       'ops.adopt_legacy_active_rule_v1(uuid,text,text[],text,text)'::regprocedure,'execute')
     or has_function_privilege('carr_writer',
       'ops.adopt_legacy_active_rule_v1(uuid,text,text[],text,text)'::regprocedure,'execute')
     or has_function_privilege('carr_authority',
       'ops.bind_rule_controls(uuid,text[],text)'::regprocedure,'execute') then
    raise exception '0867 FAILED: an authority role can bypass atomic approval';
  end if;
  if not has_function_privilege('carr_authority',
       'ops.approve_rule(uuid,text,text[],text,text)'::regprocedure,'execute')
     or has_function_privilege('carr_writer',
       'ops.approve_rule(uuid,text,text[],text,text)'::regprocedure,'execute') then
    raise exception '0867 FAILED: approve_rule grants changed';
  end if;
end
$wr262_0867$;
