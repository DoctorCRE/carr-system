-- rollback: forward-only; keep truthful missing-grant receipts and restore behavior only through a successor migration.
-- lock-review: nullable column and checks lock only the retirement receipt ledger; no rule rows are rewritten.
-- Retirement removes authority even when its original grant cannot be proved.
-- Preserve the legacy explanation and record post-cutover gaps separately.
alter table ops.rule_retirement_receipt add column missing_approval_receipt text
  check (missing_approval_receipt is null or btrim(missing_approval_receipt) <> '');
alter table ops.rule_retirement_receipt drop constraint active_retirement_has_approval;
alter table ops.rule_retirement_receipt add constraint active_retirement_has_approval
  check (previous_status <> 'active' or approval_receipt_id is not null
    or legacy_admission is not null or missing_approval_receipt is not null);
alter table ops.rule_retirement_receipt add constraint retirement_missing_approval_exclusive
  check (missing_approval_receipt is null or
    (previous_status = 'active' and approval_receipt_id is null and legacy_admission is null));
comment on column ops.rule_retirement_receipt.missing_approval_receipt is
  'Explicit missing-approval-receipt marker for retirement of an active rule without an exact grant or legacy admission. Included in the retirement contract hash; never grants authority.';

CREATE OR REPLACE FUNCTION ops.retire_rule(p_rule_id uuid, p_reason text, p_superseded_by uuid, p_idempotency_key text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule public.rule%rowtype;
  v_prior ops.rule_retirement_receipt%rowtype;
  v_receipt ops.rule_retirement_receipt%rowtype;
  v_approval_id uuid;
  v_legacy_note text;
  v_missing_note text;
  v_contract jsonb;
  v_contract_hash text;
  v_retired_at timestamptz;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug<>'joe' then
    raise exception 'system rule retirement requires Joe authority';
  end if;
  select id into v_actor_id from public.actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then raise exception 'Joe authority actor is not active'; end if;
  if btrim(coalesce(p_reason,''))='' or btrim(coalesce(p_idempotency_key,''))='' then
    raise exception 'retirement reason and idempotency key are required';
  end if;
  if p_superseded_by is not null and p_superseded_by=p_rule_id then
    raise exception 'a rule cannot supersede itself';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-retirement:'||p_idempotency_key,0));
  select * into v_prior from ops.rule_retirement_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.reason is distinct from btrim(p_reason)
       or v_prior.superseded_by is distinct from p_superseded_by
       or v_prior.actor_id is distinct from v_actor_id then
      raise exception 'rule retirement idempotency key was reused with different input';
    end if;
    select * into v_rule from public.rule where id=p_rule_id for update;
    if not found
       or v_rule.status is distinct from 'retired'
       or v_rule.version is distinct from v_prior.rule_version_after
       or encode(public.digest(v_rule.statement,'sha256'),'hex') is distinct from v_prior.statement_hash
       or v_rule.retired_by is distinct from v_prior.actor_id
       or v_rule.retired_at is distinct from v_prior.retired_at then
      raise exception 'rule retirement replay refused: current retired rule no longer matches the immutable retirement';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'rule_id',p_rule_id,
      'previous_status',v_prior.previous_status,'status','retired',
      'retirement_receipt_id',v_prior.id,'legacy_admission',v_prior.legacy_admission,
      'missing_approval_receipt',v_prior.missing_approval_receipt);
  end if;

  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, expected proposed or active',p_rule_id,v_rule.status;
  end if;
  if p_superseded_by is not null and not exists (select 1 from public.rule where id=p_superseded_by) then
    raise exception 'superseding rule % does not exist',p_superseded_by;
  end if;
  if v_rule.status='active' then
    select id into v_approval_id from ops.rule_approval_receipt
     where rule_id=v_rule.id
       and (rule_version=v_rule.version or exists (
         select 1 from ops.rule_approval_lifecycle_anchor legacy
          where legacy.approval_receipt_id=ops.rule_approval_receipt.id
            and legacy.rule_id=v_rule.id and legacy.rule_version_after=v_rule.version
            and legacy.statement_hash=ops.rule_approval_receipt.statement_hash))
       and statement_hash=encode(public.digest(v_rule.statement,'sha256'),'hex')
     order by created_at desc limit 1;
    if v_approval_id is null then
      v_legacy_note := ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at);
      if v_legacy_note is null then
        v_missing_note := 'missing_approval_receipt: active rule '||v_rule.id||
          ' at version '||v_rule.version||' has no exact approval receipt; retirement removes authority';
      end if;
    end if;
  end if;

  v_retired_at := now();

  v_contract := jsonb_build_object(
    'rule_id',v_rule.id,'rule_version_before',v_rule.version,
    'rule_version_after',v_rule.version+1,
    'statement_hash',encode(public.digest(v_rule.statement,'sha256'),'hex'),
    'previous_status',v_rule.status,'actor_id',v_actor_id,
    'reason',btrim(p_reason),'superseded_by',p_superseded_by,
    'approval_receipt_id',v_approval_id,'legacy_admission',v_legacy_note,'missing_approval_receipt',v_missing_note,'retired_at',v_retired_at);
  v_contract_hash := encode(public.digest(v_contract::text,'sha256'),'hex');
  insert into ops.rule_retirement_receipt
    (idempotency_key,rule_id,rule_version_before,rule_version_after,statement_hash,previous_status,
     actor_id,reason,superseded_by,approval_receipt_id,legacy_admission,missing_approval_receipt,contract_hash,retired_at)
  values (p_idempotency_key,v_rule.id,v_rule.version,v_rule.version+1,
          encode(public.digest(v_rule.statement,'sha256'),'hex'),v_rule.status,
          v_actor_id,btrim(p_reason),p_superseded_by,v_approval_id,v_legacy_note,v_missing_note,v_contract_hash,v_retired_at)
  returning * into v_receipt;
  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('retirement:'||p_idempotency_key,'override','rule',v_rule.id,v_actor_id,
          'retired by Joe authority: '||btrim(p_reason),v_contract_hash,
          case when v_approval_id is null then '{}'::text[]
               else array[v_approval_id::text] end);
  update public.rule set status='retired',retired_by=v_actor_id,retired_at=v_retired_at
   where id=v_rule.id and status=v_rule.status;
  if not found then raise exception 'rule % retirement raced',v_rule.id; end if;
  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'previous_status',v_rule.status,'status','retired',
    'retirement_receipt_id',v_receipt.id,'legacy_admission',v_receipt.legacy_admission,
    'missing_approval_receipt',v_receipt.missing_approval_receipt);
end $function$;
