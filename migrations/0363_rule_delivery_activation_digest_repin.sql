-- 0363_rule_delivery_activation_digest_repin.sql
--
-- WR-000019 retired rule 581cb3fe and repinned the reviewed activation
-- overlay. Require the exact prior nine-row preimage, remove only that exact
-- retired row, and repin the unchanged eight. Any same-ID field drift refuses
-- before mutation; the transaction leaves no partial delete or digest update.

begin;

do $$
declare
  v_expected constant text := 'f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904';
  v_prior constant text := '4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218';
  v_ids constant text[] := array[
    '25fcddee','3fa17fa0','72e06bdf','113b3833',
    '57d13061','c66dc739','49533583','557838a5'
  ];
  v_prior_ids constant text[] := array[
    '25fcddee','3fa17fa0','72e06bdf','581cb3fe','113b3833',
    '57d13061','c66dc739','49533583','557838a5'
  ];
  v_updated bigint;
  v_policy_mode text;
  v_retired_rule_count bigint;
begin
  select p.mode into v_policy_mode
    from ops.rule_delivery_policy p
   where p.singleton
   for update;
  if v_policy_mode is distinct from 'shadow' then
    raise exception '0363 REFUSED: activation digest repin requires shadow mode';
  end if;

  -- Serialize in the same policy -> rule order as the cutover, then accept
  -- either a sanitized rebuild with no rule row or the exact immutable
  -- retirement receipt that superseded 581cb3fe with aa411351.
  perform r.id
    from public.rule r
   where left(r.id::text,8)='581cb3fe'
   for update;
  get diagnostics v_retired_rule_count = row_count;
  if v_retired_rule_count > 1 then
    raise exception '0363 REFUSED: retired short id 581cb3fe resolves to % rules',
      v_retired_rule_count;
  end if;
  if v_retired_rule_count = 1 and not exists (
    select 1
      from public.rule r
      join ops.rule_retirement_receipt rr on rr.rule_id=r.id
      join public.actor retirement_actor on retirement_actor.id=rr.actor_id
      join public.rule successor on successor.id=rr.superseded_by
      join ops.authority_receipt ar
        on ar.idempotency_key='retirement:'||rr.idempotency_key
     where left(r.id::text,8)='581cb3fe'
       and r.status='retired'
       and r.retired_by=rr.actor_id
       and r.retired_at=rr.retired_at
       and r.version=rr.rule_version_after
       and rr.rule_version_before+1=rr.rule_version_after
       and rr.statement_hash=encode(public.digest(r.statement,'sha256'),'hex')
       and rr.previous_status='active'
       and retirement_actor.slug='joe'
       and retirement_actor.kind='human'
       and retirement_actor.active
       and rr.reason=btrim(rr.reason)
       and rr.created_at=rr.retired_at
       and (
         (rr.approval_receipt_id is not null
          and rr.legacy_admission is null
          and exists (
            select 1
              from ops.rule_approval_receipt approval
             where approval.id=rr.approval_receipt_id
               and approval.rule_id=r.id
               and approval.statement_hash=rr.statement_hash
               and (
                 approval.rule_version=rr.rule_version_before
                 or exists (
                   select 1
                     from ops.rule_approval_lifecycle_anchor anchor
                    where anchor.approval_receipt_id=approval.id
                      and anchor.rule_id=r.id
                      and anchor.rule_version_after=rr.rule_version_before
                      and anchor.statement_hash=approval.statement_hash
                 )
               )
          ))
         or
         (rr.approval_receipt_id is null
          and rr.legacy_admission is not null
          and rr.legacy_admission=
                ops.legacy_rule_admission_note(r.id,'active',r.activated_at))
       )
       and rr.contract_hash=encode(public.digest(jsonb_build_object(
             'rule_id',r.id,
             'rule_version_before',rr.rule_version_before,
             'rule_version_after',rr.rule_version_after,
             'statement_hash',rr.statement_hash,
             'previous_status',rr.previous_status,
             'actor_id',rr.actor_id,
             'reason',rr.reason,
             'superseded_by',rr.superseded_by,
             'approval_receipt_id',rr.approval_receipt_id,
             'legacy_admission',rr.legacy_admission,
             'retired_at',rr.retired_at
           )::text,'sha256'),'hex')
       and left(successor.id::text,8)='aa411351'
       and (select count(*) from public.rule named_successor
             where left(named_successor.id::text,8)='aa411351')=1
       and ar.kind='override'
       and ar.subject_type='rule'
       and ar.subject_id=r.id
       and ar.actor_id=rr.actor_id
       and ar.contract_hash=rr.contract_hash
       and ar.decision='retired by Joe authority: '||rr.reason
       and ar.evidence_refs=case
             when rr.approval_receipt_id is null then '{}'::text[]
             else array[rr.approval_receipt_id::text]
           end
       and ar.created_at=rr.retired_at
       and (select count(*) from ops.rule_retirement_receipt all_rr
             where all_rr.rule_id=r.id)=1
  ) then
    raise exception
      '0363 REFUSED: rule 581cb3fe is not absent or exactly retired to aa411351';
  end if;

  if (select count(*) from ops.rule_delivery_activation_target)
       <> cardinality(v_prior_ids)
     or exists (
       select 1
         from ops.rule_delivery_activation_target t
        where t.short_id <> all(v_prior_ids)
           or (t.short_id, t.expected_scope, t.expected_pack) not in (
                values
                  ('25fcddee','shared','governance-rules'),
                  ('3fa17fa0','shared','client-deal'),
                  ('72e06bdf','shared','client-deal'),
                  ('581cb3fe','shared','delegation-council'),
                  ('113b3833','joe','governance-rules'),
                  ('57d13061','joe','joe-comms'),
                  ('c66dc739','joe','joe-comms'),
                  ('49533583','joe','joe-comms'),
                  ('557838a5','joe','joe-comms')
              )
           or t.from_control <> 'session_boot'
           or t.from_enforcement_class <> 'surfacing'
           or t.from_implementation_ref <>
                'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js'
           or t.from_test_ref <>
                'command:python3 hooks/gate-integrity.py --selftest'
           or t.map_digest <> v_prior
           or t.to_control <> 'pack_delivery'
           or t.to_enforcement_class <> 'stop_gate'
           or t.to_implementation_ref <>
                'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py'
           or t.to_test_ref <>
                'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py'
     ) then
    raise exception '0363 REFUSED: expected the exact reviewed nine-row activation preimage';
  end if;

  delete from ops.rule_delivery_activation_target t
   where t.short_id = '581cb3fe'
     and t.expected_scope = 'shared'
     and t.expected_pack = 'delegation-council'
     and t.from_control = 'session_boot'
     and t.from_enforcement_class = 'surfacing'
     and t.from_implementation_ref =
           'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js'
     and t.from_test_ref = 'command:python3 hooks/gate-integrity.py --selftest'
     and t.map_digest = v_prior
     and t.to_control = 'pack_delivery'
     and t.to_enforcement_class = 'stop_gate'
     and t.to_implementation_ref =
           'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py'
     and t.to_test_ref =
           'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py';

  get diagnostics v_updated = row_count;
  if v_updated <> 1 then
    raise exception '0363 REFUSED: exact retired activation target delete changed % rows',
      v_updated;
  end if;

  update ops.rule_delivery_activation_target t
     set map_digest = v_expected
   where t.short_id = any(v_ids)
     and (t.short_id, t.expected_scope, t.expected_pack) in (
           values
             ('25fcddee','shared','governance-rules'),
             ('3fa17fa0','shared','client-deal'),
             ('72e06bdf','shared','client-deal'),
             ('113b3833','joe','governance-rules'),
             ('57d13061','joe','joe-comms'),
             ('c66dc739','joe','joe-comms'),
             ('49533583','joe','joe-comms'),
             ('557838a5','joe','joe-comms')
         )
     and t.from_control = 'session_boot'
     and t.from_enforcement_class = 'surfacing'
     and t.from_implementation_ref =
           'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js'
     and t.from_test_ref = 'command:python3 hooks/gate-integrity.py --selftest'
     and t.map_digest = v_prior
     and t.to_control = 'pack_delivery'
     and t.to_enforcement_class = 'stop_gate'
     and t.to_implementation_ref =
           'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py'
     and t.to_test_ref =
           'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py';

  get diagnostics v_updated = row_count;
  if v_updated <> cardinality(v_ids) then
    raise exception
      '0363 REFUSED: expected eight exact reviewed activation rows, updated %',
      v_updated;
  end if;

  if (select count(*) from ops.rule_delivery_activation_target)
       <> cardinality(v_ids)
     or exists (
       select 1
         from ops.rule_delivery_activation_target t
        where t.short_id <> all(v_ids)
           or (t.short_id, t.expected_scope, t.expected_pack) not in (
                values
                  ('25fcddee','shared','governance-rules'),
                  ('3fa17fa0','shared','client-deal'),
                  ('72e06bdf','shared','client-deal'),
                  ('113b3833','joe','governance-rules'),
                  ('57d13061','joe','joe-comms'),
                  ('c66dc739','joe','joe-comms'),
                  ('49533583','joe','joe-comms'),
                  ('557838a5','joe','joe-comms')
              )
           or t.from_control <> 'session_boot'
           or t.from_enforcement_class <> 'surfacing'
           or t.from_implementation_ref <>
                'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js'
           or t.from_test_ref <>
                'command:python3 hooks/gate-integrity.py --selftest'
           or t.map_digest <> v_expected
           or t.to_control <> 'pack_delivery'
           or t.to_enforcement_class <> 'stop_gate'
           or t.to_implementation_ref <>
                'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py'
           or t.to_test_ref <>
                'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py'
     ) then
    raise exception '0363 FAILED: activation transition did not leave the exact reviewed eight';
  end if;
end $$;


do $$
begin
  if not exists (
    select 1
      from pg_constraint c
     where c.conrelid = 'ops.rule_delivery_activation_receipt'::regclass
       and c.conname = 'rule_delivery_activation_receipt_target_short_ids_check'
       and pg_get_constraintdef(c.oid) like '%cardinality(target_short_ids) = 9%'
  ) then
    raise exception '0363 REFUSED: historical activation receipt cardinality constraint differs';
  end if;
end $$;

alter table ops.rule_delivery_activation_receipt
  drop constraint rule_delivery_activation_receipt_target_short_ids_check,
  add constraint rule_delivery_activation_receipt_target_short_ids_check
    check (cardinality(target_short_ids) in (8,9));

create or replace function ops.set_rule_delivery_mode(
  p_mode text,
  p_changed_by text,
  p_reason text,
  p_expected_map_digest text)
returns table(mode text, changed_controls bigint, receipt_id uuid)
language plpgsql security definer
set search_path = pg_catalog, public, ops
as $$
declare
  v_from_mode text;
  v_expected_control text;
  v_expected_class text;
  v_next_control text;
  v_next_class text;
  v_count bigint;
  v_receipt uuid;
  v_health record;
begin
  if p_mode not in ('shadow','enforced') then
    raise exception 'unknown rule-delivery mode %',p_mode;
  end if;
  if coalesce(btrim(p_changed_by),'')='' or coalesce(btrim(p_reason),'')='' then
    raise exception 'changed_by and reason are required';
  end if;
  if (select count(*) from ops.rule_delivery_activation_target) <> 8 then
    raise exception 'activation target set is not exactly eight';
  end if;
  if exists (select 1 from ops.rule_delivery_activation_target
              where map_digest<>p_expected_map_digest) then
    raise exception 'activation map digest preimage differs';
  end if;

  select p.mode into v_from_mode
    from ops.rule_delivery_policy p where p.singleton for update;
  if v_from_mode is null then raise exception 'delivery policy singleton is absent'; end if;
  if v_from_mode=p_mode then
    raise exception 'delivery policy already %; refusing a receipt-free no-op',p_mode;
  end if;
  if v_from_mode='shadow' and p_mode='enforced' then
    v_expected_control:='session_boot'; v_expected_class:='surfacing';
    v_next_control:='pack_delivery'; v_next_class:='stop_gate';
  elsif v_from_mode='enforced' and p_mode='shadow' then
    v_expected_control:='pack_delivery'; v_expected_class:='stop_gate';
    v_next_control:='session_boot'; v_next_class:='surfacing';
  else
    raise exception 'unsupported delivery transition % -> %',v_from_mode,p_mode;
  end if;

  select * into v_health from ops.rule_delivery_audit_counts(35);
  if v_health.total=0 or v_health.untagged<>0 or v_health.orphaned<>0
     or v_health.wildcarded<>0 or v_health.packless<>0 or v_health.emptypack<>0
     or v_health.scope_mismatch<>0 then
    raise exception 'delivery coverage is not activation-safe: %',row_to_json(v_health);
  end if;

  perform r.id from public.rule r
    join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id
   where r.status='active' for update of r;
  get diagnostics v_count=row_count;
  if v_count<>8 then raise exception 'active target preimage count is %, expected 8',v_count; end if;

  perform l.rule_id from ops.rule_load_layer l
    join ops.rule_delivery_activation_target t on t.short_id=l.short_id
   where l.scope=t.expected_scope and l.packs=array[t.expected_pack]
     and l.load_layer='pack' and l.map_digest=t.map_digest
   for update of l;
  get diagnostics v_count=row_count;
  if v_count<>8 then raise exception 'delivery target tag preimage count is %, expected 8',v_count; end if;

  perform a.rule_id from ops.rule_admission a
    join public.rule r on r.id=a.rule_id
    join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id
   where a.state='admitted'
     and a.reason='Backfilled from the reviewed active rule enforcement map'
     and a.enforcement_status='blocked'
     and not exists (select 1 from ops.rule_approval_receipt ar where ar.rule_id=r.id)
   for update of a;
  get diagnostics v_count=row_count;
  if v_count<>8 then raise exception 'admission target preimage count is %, expected 8',v_count; end if;

  select count(*) into v_count
    from ops.rule_enforcement_point ep
    join public.rule r on r.id=ep.rule_id
    join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id
   where ep.control_key=v_expected_control
     and ep.enforcement_class=v_expected_class and ep.installed;
  if v_count<>8 or (select count(*) from ops.rule_enforcement_point ep
      join public.rule r on r.id=ep.rule_id
      join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id)<>8 then
    raise exception 'enforcement-point preimage is not the exact eight %/% rows',
      v_expected_control,v_expected_class;
  end if;
  if p_mode='enforced' and not exists (
      select 1 from ops.enforcement_control_catalog c
       where c.control_key='pack_delivery' and c.enforcement_class='stop_gate'
         and c.installed and c.verified_at is not null) then
    raise exception 'pack_delivery is not an installed, verified stop gate';
  end if;

  delete from ops.rule_enforcement_point ep using public.rule r,
      ops.rule_delivery_activation_target t
   where ep.rule_id=r.id and left(r.id::text,8)=t.short_id;
  insert into ops.rule_enforcement_point
    (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
  select r.id,
         case when p_mode='enforced' then t.to_control else t.from_control end,
         case when p_mode='enforced' then t.to_implementation_ref else t.from_implementation_ref end,
         case when p_mode='enforced' then t.to_test_ref else t.from_test_ref end,
         case when p_mode='enforced' then t.to_enforcement_class else t.from_enforcement_class end,
         true,now()
    from public.rule r join ops.rule_delivery_activation_target t
      on left(r.id::text,8)=t.short_id where r.status='active';

  update ops.rule_admission a
     set coverage_detail=jsonb_set(a.coverage_detail,'{rule_delivery}',
           jsonb_build_object('mode',p_mode,'control',v_next_control,
                              'changed_by',p_changed_by,'changed_at',now()),true),
         version=a.version+1,updated_at=now()
    from public.rule r join ops.rule_delivery_activation_target t
      on left(r.id::text,8)=t.short_id
   where a.rule_id=r.id;

  perform set_config('carr.rule_delivery_cutover','on',true);
  update ops.rule_delivery_policy
     set mode=p_mode,changed_by=p_changed_by,reason=p_reason,changed_at=now()
   where singleton;

  insert into ops.rule_delivery_activation_receipt
    (from_mode,to_mode,changed_by,reason,map_digest,target_short_ids)
  select v_from_mode,p_mode,p_changed_by,p_reason,p_expected_map_digest,
         array_agg(t.short_id order by t.short_id)
    from ops.rule_delivery_activation_target t returning id into v_receipt;
  return query select p_mode,8::bigint,v_receipt;
end $$;

revoke all on function ops.set_rule_delivery_mode(text,text,text,text) from public;
do $$ begin
  if exists(select 1 from pg_roles where rolname='carr_authority') then
    execute 'grant execute on function ops.set_rule_delivery_mode(text,text,text,text) '
            'to carr_authority';
  end if;
end $$;

commit;
