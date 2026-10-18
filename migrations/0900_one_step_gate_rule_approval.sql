-- 0900_one_step_gate_rule_approval.sql
-- rollback: create or replace ops.approve_rule from migrations/0867_adopt_active_legacy_rule_approval.sql; admissions and receipts it wrote are immutable approval history and stay
-- lock-review: CREATE OR REPLACE of one existing SECURITY DEFINER function; signature, owner and grants are unchanged and no row is rewritten.
--
-- ONE STEP: JOE'S APPROVE MAKES A TAUGHT GATE RULE LIVE (supersedes PR #1536).
--
-- approve-rule already verifies enforcement, writes the receipt and activates
-- in one transaction (0194/0479/0482; activate-rule is retired). What still
-- took a second act was admission: ops.bind_rule_delivery reads the delivery
-- decision from an admitted ops.rule_admission row, so a rule taught with
-- enforcement_home 'gate' and an installed carrying_control was refused with
-- "admitted rule contract is missing" until someone hand-wrote admit-rule.
-- Every field admit-rule would carry for that rule is already in the teach
-- record: the class (machine_enforceable), the one control, and control-layer
-- delivery.
--
-- WHAT CHANGES. For a PROPOSED rule with no admission and no delivery binding
-- whose first teach event names enforcement_home 'gate', approval writes that
-- admission (and its admission authority receipt) itself, then takes the
-- unchanged 0867 path. A missing policy_kind defaults to machine_enforceable
-- and missing control_keys to the carrying_control, so replay with the same
-- omitted arguments resolves to the same receipt.
--
-- WHAT DOES NOT CHANGE. Only Joe authority may approve. The carrying control
-- must still be installed, verified and gate-class or approval refuses and the
-- whole transaction (including the derived admission) rolls back. core, jit
-- and judgment_advisory rules have no installed deny control to carry an
-- approval receipt, so they keep needing an explicit admit-rule contract.
-- Explicit admissions, legacy adoption (0867) and replay are untouched.

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
  v_policy_kind text := p_policy_kind;
  v_control_keys text[] := p_control_keys;
  v_bind_controls text[];
  v_adopt boolean := false;
  v_rule public.rule%rowtype;
  v_teach jsonb;
  v_control text;
  v_joe uuid;
  v_intake uuid;
  v_contract jsonb;
begin
  select * into v_rule from public.rule r where r.id=p_rule_id;
  select e.new_value into v_teach from public.event e
   where e.verb='teach' and e.subject_type='rule' and e.subject_id=p_rule_id
   order by e.recorded_at,e.id limit 1;
  v_control := nullif(btrim(coalesce(v_teach->>'carrying_control','')),'');
  if v_teach->>'enforcement_home'='gate' and v_control is not null then
    v_policy_kind := coalesce(v_policy_kind,'machine_enforceable');
    if cardinality(coalesce(v_control_keys,'{}'::text[]))=0 then
      v_control_keys := array[v_control];
    end if;
    if v_rule.status='proposed'
       and not exists (select 1 from ops.rule_admission a where a.rule_id=p_rule_id)
       and not exists (select 1 from ops.rule_load_layer l where l.rule_id=p_rule_id) then
      if ops.authority_actor_slug() is distinct from 'joe' then
        raise exception 'system rule approval requires Joe authority';
      end if;
      if v_policy_kind<>'machine_enforceable' or not (v_control=any(v_control_keys)) then
        raise exception 'gate rule % must be approved as machine_enforceable with its carrying_control %',
          p_rule_id,v_control;
      end if;
      select id into v_joe from public.actor where slug='joe' and kind='human' and active;
      select id into v_intake from ops.guidance_intake
       where lane='rule' and source_ref='rule:'||p_rule_id::text order by captured_at limit 1;
      v_contract := jsonb_build_object(
        'enforcement_class','machine_enforceable',
        'binding_moment','when the taught rule applies',
        'applicability',case when v_rule.scope='{}'::jsonb
          then '{"workflows":["*"],"surfaces":["*"],"tiers":["*"]}'::jsonb else v_rule.scope end,
        'projection',jsonb_build_object('delivery',jsonb_build_object(
          'load_layer','control','packs','[]'::jsonb,'why',v_rule.statement)),
        'reachability','{"paths":["record-layer","session-boot","registered-controls"]}'::jsonb,
        'input_contract','{"type":"object"}'::jsonb,
        'teach',v_teach);
      insert into ops.rule_admission
        (rule_id,guidance_intake_id,enforcement_class,binding_moment,applicability,projection,
         reachability,input_contract,fixture_refs,state,admitted_by,admitted_at,reason)
      values
        (p_rule_id,v_intake,'machine_enforceable',v_contract->>'binding_moment',
         v_contract->'applicability',v_contract->'projection',v_contract->'reachability',
         v_contract->'input_contract',array['rule:'||p_rule_id::text],'admitted',v_joe,now(),
         btrim(p_reason));
      insert into ops.authority_receipt
        (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
      values
        ('admission:'||p_idempotency_key,'admission','rule',p_rule_id,v_joe,btrim(p_reason),
         encode(public.digest(v_contract::text,'sha256'),'hex'),array['rule:'||p_rule_id::text]);
    end if;
  end if;

  v_bind_controls := array(
    select distinct btrim(u.control_key)
      from unnest(coalesce(v_control_keys,'{}'::text[])) as u(control_key)
     where btrim(u.control_key)<>''
     order by btrim(u.control_key));
  if v_policy_kind='human_only'
     and not ('human_authority_runtime'=any(v_bind_controls)) then
    v_bind_controls := array_append(v_bind_controls,'human_authority_runtime');
  end if;

  perform ops.bind_rule_delivery(p_rule_id,p_reason);
  -- 0867: an ACTIVE legacy admission (receiptless and older than the receipt
  -- system) is adopted; every other rule takes the unchanged path. Decided
  -- before binding, because binding is what the adoption is.
  v_adopt := v_rule.status='active'
    and ops.legacy_rule_admission_note(p_rule_id,v_rule.status,v_rule.activated_at) is not null;
  perform ops.bind_rule_controls(p_rule_id,v_bind_controls,p_reason);
  if v_adopt then
    return ops.adopt_legacy_active_rule_v1(
      p_rule_id,v_policy_kind,v_control_keys,p_idempotency_key,p_reason);
  end if;
  return ops.approve_rule_receipt_activation_v1(
    p_rule_id,v_policy_kind,v_control_keys,p_idempotency_key,p_reason);
end $function$;
