-- Reader sessions use a view, as standing-context does. Keep table write
-- privileges closed; the handler bounds output and derives personal scope.
create view public.v_rule_lookup as
  select r.id,r.status,r.version,r.created_at,r.scope,r.statement,a.slug as personal_to
    from public.rule r left join public.actor a on a.id=r.personal_to;
grant select on public.v_rule_lookup to carr_reader,carr_writer,carr_authority;

-- Human replacement capture uses the existing authority credential. It needs
-- the same capture inserts as writer; activation and retirement still use
-- their separate definer doors and approval proofs.
grant insert on public.rule,ops.guidance_intake to carr_authority;

-- Tuple xmin changes on UPDATE. Keep INSERT provenance outside the mutable
-- rule row, with no runtime DML or callable writer door to forge it. The
-- top-level xid remains the same across teach's savepoint subtransactions.
create table ops.rule_insert_provenance (
  rule_id uuid primary key references public.rule(id) on delete cascade,
  taught_by uuid not null references public.actor(id),
  transaction_id xid8 not null
);
revoke all on ops.rule_insert_provenance from public,carr_reader,carr_writer,carr_authority,carr_jobs;
create function ops.record_rule_insert_provenance()
returns trigger language plpgsql security definer
set search_path=pg_catalog,ops,public as $$
begin
  insert into ops.rule_insert_provenance(rule_id,taught_by,transaction_id)
    values(new.id,new.taught_by,pg_current_xact_id());
  return new;
end $$;
revoke all on function ops.record_rule_insert_provenance()
  from public,carr_reader,carr_writer,carr_authority,carr_jobs;
create trigger rule_insert_provenance after insert on public.rule
  for each row execute function ops.record_rule_insert_provenance();

-- A replacement captures new proposed guidance and retires its predecessor in
-- one transaction. No rule is activated. Active retirement keeps the existing
-- Joe authority connection, exact approval proof, and immutable receipts.
create function ops.retire_superseded_rule(p_replacement uuid,p_idempotency_key text)
returns jsonb language plpgsql security definer
set search_path=pg_catalog,ops,public as $$
declare
  v_actor uuid;
  v_replacement public.rule%rowtype;
  v_rule public.rule%rowtype;
  v_reason text;
  v_at timestamptz:=now();
  v_contract jsonb;
  v_hash text;
  v_receipt uuid;
begin
  v_actor:=ops.portfolio_writer_actor_id();
  select * into v_replacement from public.rule where id=p_replacement for update;
  if not found or v_replacement.status<>'proposed'
     or v_replacement.taught_by is distinct from v_actor
     or v_replacement.supersedes is null
     or not exists(
       select 1 from ops.rule_insert_provenance
        where rule_id=p_replacement and taught_by=v_actor
          and transaction_id=pg_current_xact_id())
     or btrim(coalesce(p_idempotency_key,''))='' then
    raise exception 'supersession requires this transaction''s newly taught proposed replacement';
  end if;
  if v_replacement.supersedes=p_replacement then
    raise exception 'a rule cannot supersede itself';
  end if;
  select * into v_rule from public.rule where id=v_replacement.supersedes for update;
  if not found or v_rule.status not in ('proposed','active') then
    raise exception 'superseded rule must be proposed or active';
  end if;
  v_reason:='superseded by '||p_replacement::text;
  if v_rule.status='active' then
    -- A sponsored machine's authority connection is insufficient here.
    if not exists(select 1 from public.actor where id=v_actor and kind='human' and active)
       or ops.authority_actor_slug()<>'joe' then
      raise exception 'active rule supersession requires human Joe authority';
    end if;
    return ops.retire_rule(v_rule.id,v_reason,p_replacement,p_idempotency_key);
  end if;
  v_contract:=jsonb_build_object(
    'rule_id',v_rule.id,'rule_version_before',v_rule.version,
    'rule_version_after',v_rule.version+1,
    'statement_hash',encode(public.digest(v_rule.statement,'sha256'),'hex'),
    'previous_status',v_rule.status,'actor_id',v_actor,
    'reason',v_reason,'superseded_by',p_replacement,
    'approval_receipt_id',null,'legacy_admission',null,'retired_at',v_at);
  v_hash:=encode(public.digest(v_contract::text,'sha256'),'hex');
  insert into ops.rule_retirement_receipt
    (idempotency_key,rule_id,rule_version_before,rule_version_after,statement_hash,
     previous_status,actor_id,reason,superseded_by,contract_hash,retired_at)
  values(p_idempotency_key,v_rule.id,v_rule.version,v_rule.version+1,
    encode(public.digest(v_rule.statement,'sha256'),'hex'),v_rule.status,v_actor,
    v_reason,p_replacement,v_hash,v_at) returning id into v_receipt;
  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values('retirement:'||p_idempotency_key,'override','rule',v_rule.id,v_actor,
    'proposed rule withdrawn by atomic teach: '||v_reason,v_hash,'{}'::text[]);
  update public.rule set status='retired',retired_by=v_actor,retired_at=v_at
    where id=v_rule.id and status='proposed';
  if not found then raise exception 'proposed rule supersession raced'; end if;
  return jsonb_build_object('ok',true,'rule_id',v_rule.id,'previous_status','proposed',
    'status','retired','reason',v_reason,'superseded_by',p_replacement,
    'retirement_receipt_id',v_receipt);
end $$;
revoke all on function ops.retire_superseded_rule(uuid,text)
  from public,carr_reader,carr_jobs;
grant execute on function ops.retire_superseded_rule(uuid,text) to carr_writer,carr_authority;
