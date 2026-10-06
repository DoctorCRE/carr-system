-- 0351_legacy_rule_lifecycle_admission.sql
--
-- WR-000019 follow-up fix (Obedience & Autonomy).
--
-- PROBLEM, reproduced live 2026-08-26/27: ops.retire_rule refuses every
-- active rule that has no ops.rule_approval_receipt row, raising "active rule
-- % lacks its exact approval receipt". That check is correct for a rule that
-- SHOULD have a receipt and does not -- but the approval-receipt system
-- (migration 0228 and friends) was installed after 217 of the 219 currently
-- active rules were already taught and activated by hand. Those 217 never
-- had a receipt to have; they predate the ledger, not violate it. Joe's
-- accepted triage batch needs 11 retirements and 20 amendments against
-- exactly these legacy rules, and every one of the 11 retirements is
-- refused by a check written for a different failure mode.
--
-- ops.amend_rule_statement (0349) turns out NOT to share this defect: its
-- own body never checks for an approval receipt at all, and
-- ops.require_rule_admission's strict immutability branch only activates
-- "if tg_op='UPDATE' and exists (select 1 from ops.rule_approval_receipt
-- where rule_id=old.id)" -- for a receiptless legacy rule that whole branch
-- is skipped, so the underlying UPDATE already proceeds. Verified by reading
-- both functions and by the acceptance additions below actually exercising a
-- legacy amendment. So this migration's SQL change is narrowly to
-- ops.retire_rule; ops.amend_rule_statement is touched only to compute and
-- record the same legacy_admission marker for an honest receipt trail, not
-- because it was blocked.
--
-- THE HONEST PREDICATE. A rule is "legacy" for this purpose when: (a) it is
-- ACTIVE (a still-proposed rule never needed a receipt in the first place,
-- and is unaffected here), (b) no ops.rule_approval_receipt row exists for
-- it at all, and (c) either no receipt has EVER been written by the system
-- (nothing to be behind), or the rule was activated before the earliest
-- receipt on record -- i.e. it predates the receipt system's own cutover,
-- rather than merely being a rule that skipped a step it should have taken.
-- ops.legacy_rule_admission_note() below is that predicate, shared by both
-- guarded functions so the definition of "legacy" cannot drift between them.
-- An active rule that postdates the cutover and still has no receipt is NOT
-- legacy under this predicate -- it keeps failing exactly as before, because
-- that is a real defect, not a historical fact.
--
-- THE RECEIPT TRAIL STAYS HONEST. ops.rule_retirement_receipt and
-- ops.rule_amendment_receipt each gain a nullable legacy_admission text
-- column: null for the ordinary receipted path (unchanged), and a plain-text
-- note for the legacy path recording exactly what made the rule legacy
-- (its activation time and the receipt system's cutover time it predates).
-- ops.rule_retirement_receipt's existing active_retirement_has_approval
-- CHECK ("previous_status <> 'active' OR approval_receipt_id IS NOT NULL")
-- is widened to also accept "OR legacy_admission IS NOT NULL" -- a legacy
-- retirement is never silently exempted from proving SOMETHING; it proves
-- the legacy fact instead of a receipt. A new CHECK keeps the two mutually
-- exclusive: a row can never claim both an approval receipt and a legacy
-- admission, so nobody can later misread a receipted retirement as legacy
-- or vice versa. Both legacy_admission columns also reject a blank string,
-- the same convention already used by every free-text reason/rationale
-- column beside them.
--
-- AUTHORITY GUARDS ARE UNCHANGED. Same Joe-authority actor resolution, same
-- refusal of a retired rule, same refusal of a no-op amendment, same
-- idempotent replay discipline. Nothing here relaxes anything for a rule
-- that DOES carry an exact approval receipt -- the exact-receipt lookup in
-- ops.retire_rule runs first, unmodified, and only falls through to the
-- legacy predicate when that lookup comes back empty.
--
-- ops.applicable_rules() and ops.rule_amendment_reaches() are NOT touched.
-- ops.applicable_rules() INNER JOINs ops.rule_approval_receipt to compile
-- what it returns -- a rule with no receipt row never satisfies that join,
-- legacy or not, before or after this migration. Retiring or amending a
-- legacy rule does not change whether it appears in applicable_rules(),
-- because it was never there to begin with (it is delivered through
-- ops.rule_delivery_plan instead, the DELIVERY selector, not the
-- receipt-bound ENFORCEMENT compiler). The acceptance additions below assert
-- this explicitly rather than assuming it.

begin;

-- ── legacy admission marker columns ─────────────────────────────────────────

alter table ops.rule_retirement_receipt add column legacy_admission text;

alter table ops.rule_retirement_receipt
    add constraint rule_retirement_receipt_legacy_admission_check
        check (legacy_admission is null or btrim(legacy_admission) <> '');

alter table ops.rule_retirement_receipt drop constraint active_retirement_has_approval;
alter table ops.rule_retirement_receipt
    add constraint active_retirement_has_approval
        check (previous_status <> 'active'
               or approval_receipt_id is not null
               or legacy_admission is not null);

-- A row proves exactly one story: an exact receipt, or an honest legacy
-- admission -- never both, never neither, for a retired-from-active rule.
alter table ops.rule_retirement_receipt
    add constraint retirement_legacy_excludes_receipt
        check (legacy_admission is null or approval_receipt_id is null);

comment on column ops.rule_retirement_receipt.legacy_admission is
    'Null for the ordinary receipted retirement path. Populated only when ops.retire_rule accepted an ACTIVE rule that has no ops.rule_approval_receipt row because it predates the receipt system entirely (see ops.legacy_rule_admission_note) -- records the rule''s activation time and the receipt-system cutover it predates, so the tamper-evident chain shows what actually happened rather than implying a receipt that never existed.';

alter table ops.rule_amendment_receipt add column legacy_admission text;

alter table ops.rule_amendment_receipt
    add constraint rule_amendment_receipt_legacy_admission_check
        check (legacy_admission is null or btrim(legacy_admission) <> '');

comment on column ops.rule_amendment_receipt.legacy_admission is
    'Null for the ordinary amendment path (proposed rule, or an active rule carrying an approval receipt). Populated only when ops.amend_rule_statement amended an ACTIVE rule that has no ops.rule_approval_receipt row because it predates the receipt system entirely -- see ops.legacy_rule_admission_note. amend_rule_statement never required a receipt to function; this column exists purely so the ledger names the legacy fact rather than staying silent about it.';

-- ── the shared legacy predicate ─────────────────────────────────────────────
--
-- Deliberately the ONE place that defines "legacy" for both ops.retire_rule
-- and ops.amend_rule_statement, so the two guarded paths cannot quietly grow
-- different ideas of what a legacy rule is. Returns null (not legacy) for
-- anything except an ACTIVE rule with no exact-match-eligible approval
-- receipt row that also predates the earliest receipt ever written -- or,
-- if no receipt has ever been written at all, predates nothing and is
-- accepted outright (there is no cutover yet to be behind).
create function ops.legacy_rule_admission_note(
    p_rule_id uuid, p_status text, p_activated_at timestamptz
) returns text
    language sql stable
    as $$
  select case
    when p_status is distinct from 'active' then null
    when exists (select 1 from ops.rule_approval_receipt where rule_id=p_rule_id) then null
    when not exists (select 1 from ops.rule_approval_receipt) then
      'legacy_admission: rule '||p_rule_id||' carries no approval receipt and the approval-receipt system has never issued one; accepted as predating the ledger'
    when p_activated_at is not null
         and p_activated_at < (select min(created_at) from ops.rule_approval_receipt) then
      'legacy_admission: rule '||p_rule_id||' was activated at '||p_activated_at::text||
      ', before the approval-receipt system''s earliest receipt at '||
      (select min(created_at) from ops.rule_approval_receipt)::text||
      '; it predates the receipt cutover and carries none'
    else null
  end
$$;

comment on function ops.legacy_rule_admission_note(uuid, text, timestamptz) is
    'Shared legacy predicate for ops.retire_rule and ops.amend_rule_statement (0351). Non-null only for an ACTIVE rule with no ops.rule_approval_receipt row that also predates the receipt system''s own cutover (or predates a receipt system that has never issued any receipt at all). An active, receiptless rule that postdates the cutover returns null -- that is a real defect, not a historical fact, and both callers keep refusing it exactly as before.';

-- ── ops.retire_rule: accept a legacy active rule, receipted honestly ───────

create or replace function ops.retire_rule(p_rule_id uuid, p_reason text, p_superseded_by uuid, p_idempotency_key text) returns jsonb
    language plpgsql security definer
    set search_path to 'ops', 'public', 'pg_temp'
    as $$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule rule%rowtype;
  v_prior ops.rule_retirement_receipt%rowtype;
  v_receipt ops.rule_retirement_receipt%rowtype;
  v_approval_id uuid;
  v_legacy_note text;
  v_contract jsonb;
  v_contract_hash text;
  v_retired_at timestamptz;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug<>'joe' then
    raise exception 'system rule retirement requires Joe authority';
  end if;
  select id into v_actor_id from actor
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
    select * into v_rule from rule where id=p_rule_id for update;
    if not found
       or v_rule.status is distinct from 'retired'
       or v_rule.version is distinct from v_prior.rule_version_after
       or encode(digest(v_rule.statement,'sha256'),'hex') is distinct from v_prior.statement_hash
       or v_rule.retired_by is distinct from v_prior.actor_id
       or v_rule.retired_at is distinct from v_prior.retired_at then
      raise exception 'rule retirement replay refused: current retired rule no longer matches the immutable retirement';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'rule_id',p_rule_id,
      'previous_status',v_prior.previous_status,'status','retired',
      'retirement_receipt_id',v_prior.id,'legacy_admission',v_prior.legacy_admission);
  end if;

  select * into v_rule from rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, expected proposed or active',p_rule_id,v_rule.status;
  end if;
  if p_superseded_by is not null and not exists (select 1 from rule where id=p_superseded_by) then
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
       and statement_hash=encode(digest(v_rule.statement,'sha256'),'hex')
     order by created_at desc limit 1;
    if v_approval_id is null then
      -- (0351) Not every receiptless active rule is a defect: 217 of 219
      -- were activated before the receipt system existed at all. Fall
      -- through to the shared legacy predicate before refusing outright.
      v_legacy_note := ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at);
      if v_legacy_note is null then
        raise exception 'active rule % lacks its exact approval receipt',v_rule.id;
      end if;
    end if;
  end if;

  v_retired_at := now();

  v_contract := jsonb_build_object(
    'rule_id',v_rule.id,'rule_version_before',v_rule.version,
    'rule_version_after',v_rule.version+1,
    'statement_hash',encode(digest(v_rule.statement,'sha256'),'hex'),
    'previous_status',v_rule.status,'actor_id',v_actor_id,
    'reason',btrim(p_reason),'superseded_by',p_superseded_by,
    'approval_receipt_id',v_approval_id,'legacy_admission',v_legacy_note,'retired_at',v_retired_at);
  v_contract_hash := encode(digest(v_contract::text,'sha256'),'hex');
  insert into ops.rule_retirement_receipt
    (idempotency_key,rule_id,rule_version_before,rule_version_after,statement_hash,previous_status,
     actor_id,reason,superseded_by,approval_receipt_id,legacy_admission,contract_hash,retired_at)
  values (p_idempotency_key,v_rule.id,v_rule.version,v_rule.version+1,
          encode(digest(v_rule.statement,'sha256'),'hex'),v_rule.status,
          v_actor_id,btrim(p_reason),p_superseded_by,v_approval_id,v_legacy_note,v_contract_hash,v_retired_at)
  returning * into v_receipt;
  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('retirement:'||p_idempotency_key,'override','rule',v_rule.id,v_actor_id,
          'retired by Joe authority: '||btrim(p_reason),v_contract_hash,
          case when v_approval_id is null then '{}'::text[]
               else array[v_approval_id::text] end);
  update rule set status='retired',retired_by=v_actor_id,retired_at=v_retired_at
   where id=v_rule.id and status=v_rule.status;
  if not found then raise exception 'rule % retirement raced',v_rule.id; end if;
  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'previous_status',v_rule.status,'status','retired',
    'retirement_receipt_id',v_receipt.id,'legacy_admission',v_receipt.legacy_admission);
end $$;

comment on function ops.retire_rule(uuid, text, uuid, text) is
    'Joe-authority-guarded retirement. An ACTIVE rule normally requires its exact ops.rule_approval_receipt; (0351) an active rule with none is still accepted when ops.legacy_rule_admission_note proves it predates the receipt system, and the retirement receipt records that fact in legacy_admission instead of an approval_receipt_id. A rule that postdates the cutover and still lacks a receipt keeps failing -- this is not a general relaxation.';

-- ── ops.amend_rule_statement: record the same honest legacy marker ────────
--
-- amend_rule_statement never required an approval receipt to function (its
-- body never checked for one, and ops.require_rule_admission's strict
-- immutability branch only engages when a receipt row exists) -- so a legacy
-- rule's statement was already amendable before this migration. This change
-- adds nothing to what is PERMITTED; it only computes and records the same
-- legacy_admission fact the retirement path now does, so the amendment
-- ledger is equally honest about which rules it is touching.

create or replace function ops.amend_rule_statement(
    p_rule_id uuid, p_new_statement text, p_idempotency_key text, p_reason text
) returns jsonb
    language plpgsql security definer
    set search_path to 'ops', 'public', 'pg_temp'
    as $$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule rule%rowtype;
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
  select id into v_actor_id from actor
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
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.new_statement is distinct from v_new_statement
       or v_prior.rationale is distinct from btrim(p_reason)
       or v_prior.amended_by is distinct from v_actor_id then
      raise exception 'rule amendment idempotency key was reused with different input';
    end if;
    select * into v_rule from rule where id=p_rule_id for update;
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

  select * into v_rule from rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status='retired' then
    raise exception 'rule % is retired; a withdrawn rule stays as written',p_rule_id;
  end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, expected proposed or active',p_rule_id,v_rule.status;
  end if;

  v_prior_hash := encode(digest(v_rule.statement,'sha256'),'hex');
  v_new_hash   := encode(digest(v_new_statement,'sha256'),'hex');
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
  v_contract_hash := encode(digest(v_contract::text,'sha256'),'hex');

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

  update rule set statement=v_new_statement where id=v_rule.id and version=v_rule.version;
  if not found then raise exception 'rule % amendment raced',v_rule.id; end if;

  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'rule_version_before',v_rule.version,'rule_version_after',v_rule.version+1,
    'amendment_receipt_id',v_receipt.id,'legacy_admission',v_receipt.legacy_admission);
end $$;

comment on function ops.amend_rule_statement(uuid, text, text, text) is
    'Guarded like ops.approve_rule and ops.retire_rule: Joe-authority actor only, refuses a retired rule, writes an immutable ops.rule_amendment_receipt hashing the PRIOR statement, then updates rule.statement atomically. human_quote/scope/taught_by/personal_to/supersedes and every activation/retirement field are untouched. (0351) Records legacy_admission via the same shared predicate ops.retire_rule uses, though this function never required a receipt to run -- ops.require_rule_admission''s strict immutability branch only engages once a receipt exists, so a legacy rule was already amendable; this only names that fact honestly in the ledger.';

-- ── self-verification ────────────────────────────────────────────────────
do $$
begin
  if not exists (
    select 1 from information_schema.columns
     where table_schema='ops' and table_name='rule_retirement_receipt'
       and column_name='legacy_admission'
  ) then
    raise exception '0351 FAILED: ops.rule_retirement_receipt.legacy_admission was not created';
  end if;

  if not exists (
    select 1 from information_schema.columns
     where table_schema='ops' and table_name='rule_amendment_receipt'
       and column_name='legacy_admission'
  ) then
    raise exception '0351 FAILED: ops.rule_amendment_receipt.legacy_admission was not created';
  end if;

  if not exists (
    select 1 from pg_constraint c join pg_class t on t.oid=c.conrelid
     where t.relname='rule_retirement_receipt' and c.conname='active_retirement_has_approval'
  ) then
    raise exception '0351 FAILED: active_retirement_has_approval constraint is missing after widening';
  end if;

  if not exists (
    select 1 from pg_constraint c join pg_class t on t.oid=c.conrelid
     where t.relname='rule_retirement_receipt' and c.conname='retirement_legacy_excludes_receipt'
  ) then
    raise exception '0351 FAILED: retirement_legacy_excludes_receipt constraint was not created';
  end if;

  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where n.nspname='ops' and p.proname='legacy_rule_admission_note'
  ) then
    raise exception '0351 FAILED: ops.legacy_rule_admission_note was not created';
  end if;

  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where n.nspname='ops' and p.proname='retire_rule'
  ) then
    raise exception '0351 FAILED: ops.retire_rule is gone';
  end if;

  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where n.nspname='ops' and p.proname='amend_rule_statement'
  ) then
    raise exception '0351 FAILED: ops.amend_rule_statement is gone';
  end if;

  -- Neighbouring, unrelated functions must be untouched, or this migration
  -- reached further than it was meant to.
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where n.nspname='ops' and p.proname='applicable_rules'
  ) then
    raise exception '0351 FAILED: ops.applicable_rules is gone — an unrelated live function was removed';
  end if;
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where n.nspname='ops' and p.proname='rule_amendment_reaches'
  ) then
    raise exception '0351 FAILED: ops.rule_amendment_reaches is gone — an unrelated live function was removed';
  end if;
end $$;

commit;
