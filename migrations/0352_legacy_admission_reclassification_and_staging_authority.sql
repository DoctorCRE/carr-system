-- 0352_legacy_admission_reclassification_and_staging_authority.sql
--
-- Two acts Joe ordered directly on 2026-08-27, both discovered live while
-- executing the WR-000019 batch sitting:
--
-- ONE. The classification parity check (0349/S10) found exactly one
-- file-vs-DB disagreement in 219 rules: rule 937252fb is
-- post_action_verification in the reviewed enforcement map (carried by the
-- completion-evidence Stop gate) but judgment_advisory in ops.rule_admission,
-- which predates that gate. admit-rule correctly refuses to rewrite an ACTIVE
-- rule's admission ("admission is a pre-activation contract"), and no other
-- path existed. This migration adds the guarded legacy reclassification:
-- Joe-authority only, legacy (receipt-less, pre-cutover) active rules only,
-- append-only receipt recording old class, new class, and the legacy fact.
-- A rule that carries a real approval receipt still refuses — its class is
-- bound to the receipt and moves only through the receipted lifecycle.
--
-- TWO. The Guidance Registry staging functions were granted to carr_writer
-- only; no writer login credential is provisioned on Joe's machine and none
-- should be minted when his existing authority login can be granted the
-- staging pair. Staging only stages: activation still requires Joe's separate
-- decide-guidance-import-batch and activate-guidance-registry verbs, both
-- authority-gated server-side. Granting his own role the stage/apply pair
-- widens nothing beyond what his decision verbs already control.

-- ── receipt table ─────────────────────────────────────────────────────────

create table ops.rule_admission_reclassification_receipt (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  rule_id uuid not null references rule(id),
  enforcement_class_before text not null,
  enforcement_class_after text not null,
  actor_id uuid not null references actor(id),
  reason text not null,
  legacy_admission text not null,
  contract_hash text not null,
  created_at timestamptz not null default now(),
  constraint reclassification_is_a_change
    check (enforcement_class_before <> enforcement_class_after),
  constraint reclassification_class_vocabulary
    check (enforcement_class_after in
           ('machine_enforceable','judgment_advisory','human_only'))
);

comment on table ops.rule_admission_reclassification_receipt is
  'Append-only ledger of legacy admission reclassifications (0352). Only an ACTIVE rule with no ops.rule_approval_receipt row that predates the receipt system (ops.legacy_rule_admission_note non-null) can be reclassified, and only by Joe authority. legacy_admission is NOT NULL by design: a receipted rule''s class is bound to its approval receipt and never moves through this path.';

create trigger refuse_rule_admission_reclassification_rewrite
  before update or delete on ops.rule_admission_reclassification_receipt
  for each row execute function ops.refuse_rule_approval_receipt_rewrite();

-- ── the guarded function ──────────────────────────────────────────────────

create function ops.reclassify_legacy_rule_admission(
  p_rule_id uuid, p_new_class text, p_idempotency_key uuid, p_reason text)
returns jsonb
language plpgsql security definer
set search_path to 'ops', 'public', 'pg_temp'
as $$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule rule%rowtype;
  v_admission ops.rule_admission%rowtype;
  v_prior ops.rule_admission_reclassification_receipt%rowtype;
  v_receipt ops.rule_admission_reclassification_receipt%rowtype;
  v_legacy_note text;
  v_contract jsonb;
  v_contract_hash text;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug<>'joe' then
    raise exception 'legacy admission reclassification requires Joe authority';
  end if;
  select id into v_actor_id from actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then raise exception 'Joe authority actor is not active'; end if;
  if btrim(coalesce(p_reason,''))='' or p_idempotency_key is null then
    raise exception 'reclassification reason and idempotency key are required';
  end if;
  if p_new_class not in ('machine_enforceable','judgment_advisory','human_only') then
    raise exception 'unknown enforcement class %',p_new_class;
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-reclassification:'||p_idempotency_key::text,0));
  select * into v_prior from ops.rule_admission_reclassification_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.enforcement_class_after is distinct from p_new_class
       or v_prior.reason is distinct from btrim(p_reason)
       or v_prior.actor_id is distinct from v_actor_id then
      raise exception 'reclassification idempotency key was reused with different input';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'rule_id',p_rule_id,
      'enforcement_class',v_prior.enforcement_class_after,
      'reclassification_receipt_id',v_prior.id);
  end if;

  select * into v_rule from rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status<>'active' then
    raise exception 'rule % is %, only an active rule''s admission is reclassified here',p_rule_id,v_rule.status;
  end if;
  if exists (select 1 from ops.rule_approval_receipt where rule_id=v_rule.id) then
    raise exception 'rule % carries an approval receipt; its class is bound to the receipt and does not move through the legacy path',p_rule_id;
  end if;
  v_legacy_note := ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at);
  if v_legacy_note is null then
    raise exception 'rule % is not a legacy admission; reclassification refused',p_rule_id;
  end if;

  select * into v_admission from ops.rule_admission where rule_id=v_rule.id for update;
  if not found then raise exception 'rule % has no admission row',p_rule_id; end if;
  if v_admission.enforcement_class=p_new_class then
    raise exception 'rule % is already classified %; a no-op reclassification is refused',p_rule_id,p_new_class;
  end if;

  v_contract := jsonb_build_object(
    'rule_id',v_rule.id,'enforcement_class_before',v_admission.enforcement_class,
    'enforcement_class_after',p_new_class,'actor_id',v_actor_id,
    'reason',btrim(p_reason),'legacy_admission',v_legacy_note);
  v_contract_hash := encode(digest(v_contract::text,'sha256'),'hex');

  insert into ops.rule_admission_reclassification_receipt
    (idempotency_key,rule_id,enforcement_class_before,enforcement_class_after,
     actor_id,reason,legacy_admission,contract_hash)
  values (p_idempotency_key,v_rule.id,v_admission.enforcement_class,p_new_class,
          v_actor_id,btrim(p_reason),v_legacy_note,v_contract_hash)
  returning * into v_receipt;

  update ops.rule_admission set enforcement_class=p_new_class
   where rule_id=v_rule.id;

  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'enforcement_class_before',v_receipt.enforcement_class_before,
    'enforcement_class',p_new_class,
    'reclassification_receipt_id',v_receipt.id,
    'legacy_admission',v_receipt.legacy_admission);
end $$;

comment on function ops.reclassify_legacy_rule_admission(uuid, text, uuid, text) is
  'Joe-authority-guarded correction of a LEGACY active rule''s admission enforcement_class (0352). Requires ops.legacy_rule_admission_note to prove the rule predates the receipt system; a receipted rule refuses (its class is bound to the receipt). Append-only receipt records before, after, and the legacy fact.';

revoke all on function ops.reclassify_legacy_rule_admission(uuid, text, uuid, text) from public;
grant execute on function ops.reclassify_legacy_rule_admission(uuid, text, uuid, text) to carr_authority;

-- ── staging pair to Joe's authority role ──────────────────────────────────

grant execute on function ops.stage_guidance_import_batch(text, text, uuid, text, text) to carr_authority;
grant execute on function ops.apply_guidance_import_batch(uuid, text, text, text) to carr_authority;

-- ── proof block ───────────────────────────────────────────────────────────

do $$
begin
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where n.nspname='ops' and p.proname='reclassify_legacy_rule_admission') then
    raise exception '0352 FAILED: ops.reclassify_legacy_rule_admission was not created';
  end if;
  if not has_function_privilege('carr_authority',
      'ops.reclassify_legacy_rule_admission(uuid, text, uuid, text)','execute') then
    raise exception '0352 FAILED: carr_authority cannot execute the reclassification';
  end if;
  if has_function_privilege('carr_writer',
      'ops.reclassify_legacy_rule_admission(uuid, text, uuid, text)','execute') then
    raise exception '0352 FAILED: routine writer may reclassify legacy admissions';
  end if;
  if not has_function_privilege('carr_authority',
      'ops.stage_guidance_import_batch(text, text, uuid, text, text)','execute') then
    raise exception '0352 FAILED: authority cannot stage a guidance import batch';
  end if;
  if not has_function_privilege('carr_authority',
      'ops.apply_guidance_import_batch(uuid, text, text, text)','execute') then
    raise exception '0352 FAILED: authority cannot apply a guidance import batch';
  end if;
  if has_function_privilege('carr_reader',
      'ops.stage_guidance_import_batch(text, text, uuid, text, text)','execute') then
    raise exception '0352 FAILED: reader may stage guidance import batches';
  end if;
end $$;
