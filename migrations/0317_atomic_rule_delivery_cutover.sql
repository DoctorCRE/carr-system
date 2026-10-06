-- One transaction owns the delivery policy flip and the exact nine boot-control
-- changes.  A policy-only UPDATE is refused: it would cut the boot payload while
-- leaving the controls that detect undeclared work on the old session rail.

begin;

create table ops.rule_delivery_activation_target (
  short_id text primary key check (short_id ~ '^[0-9a-f]{8}$'),
  expected_scope text not null check (expected_scope in ('shared','joe','dell')),
  -- Packs are configuration rows installed after migrations on a fresh store;
  -- the cutover function verifies the live row before it can act.
  expected_pack text not null,
  from_control text not null,
  from_enforcement_class text not null,
  from_implementation_ref text not null,
  from_test_ref text not null,
  to_control text not null,
  to_enforcement_class text not null,
  to_implementation_ref text not null,
  to_test_ref text not null,
  map_digest text not null check (map_digest ~ '^[0-9a-f]{64}$')
);

insert into ops.rule_delivery_activation_target
  (short_id,expected_scope,expected_pack,
   from_control,from_enforcement_class,from_implementation_ref,from_test_ref,
   to_control,to_enforcement_class,to_implementation_ref,to_test_ref,map_digest)
values
 ('25fcddee','shared','governance-rules','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('3fa17fa0','shared','client-deal','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('72e06bdf','shared','client-deal','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('581cb3fe','shared','delegation-council','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('113b3833','joe','governance-rules','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('57d13061','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('c66dc739','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('49533583','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('557838a5','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a');

create table ops.rule_delivery_activation_receipt (
  id uuid primary key default gen_random_uuid(),
  from_mode text not null,
  to_mode text not null,
  changed_by text not null check (btrim(changed_by) <> ''),
  reason text not null check (btrim(reason) <> ''),
  map_digest text not null,
  target_short_ids text[] not null check (cardinality(target_short_ids)=9),
  created_at timestamptz not null default now()
);

create or replace function ops.refuse_rule_delivery_activation_receipt_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'rule-delivery activation receipts are append-only';
end $$;
create trigger rule_delivery_activation_receipt_append_only
  before update or delete on ops.rule_delivery_activation_receipt
  for each row execute function ops.refuse_rule_delivery_activation_receipt_rewrite();

create or replace function ops.refuse_direct_rule_delivery_policy_update()
returns trigger language plpgsql as $$
begin
  if current_setting('carr.rule_delivery_cutover',true) is distinct from 'on' then
    raise exception 'direct rule-delivery policy update refused; use ops.set_rule_delivery_mode';
  end if;
  return new;
end $$;
-- db/schema.sql reconstructs structure, not seed rows.  Preserve 0291's safe
-- default when this migration is the first post-snapshot writer.
insert into ops.rule_delivery_policy(singleton,mode,changed_by,reason)
values (true,'shadow','migration:0317',
        'Fail-safe seed for a rebuilt store; activation still requires the guarded cutover.')
on conflict(singleton) do nothing;
create trigger rule_delivery_policy_cutover_only
  before update on ops.rule_delivery_policy
  for each row execute function ops.refuse_direct_rule_delivery_policy_update();

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
  if (select count(*) from ops.rule_delivery_activation_target) <> 9 then
    raise exception 'activation target set is not exactly nine';
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
  if v_count<>9 then raise exception 'active target preimage count is %, expected 9',v_count; end if;

  perform l.rule_id from ops.rule_load_layer l
    join ops.rule_delivery_activation_target t on t.short_id=l.short_id
   where l.scope=t.expected_scope and l.packs=array[t.expected_pack]
     and l.load_layer='pack' and l.map_digest=t.map_digest
   for update of l;
  get diagnostics v_count=row_count;
  if v_count<>9 then raise exception 'delivery target tag preimage count is %, expected 9',v_count; end if;

  perform a.rule_id from ops.rule_admission a
    join public.rule r on r.id=a.rule_id
    join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id
   where a.state='admitted'
     and a.reason='Backfilled from the reviewed active rule enforcement map'
     and a.enforcement_status='blocked'
     and not exists (select 1 from ops.rule_approval_receipt ar where ar.rule_id=r.id)
   for update of a;
  get diagnostics v_count=row_count;
  if v_count<>9 then raise exception 'admission target preimage count is %, expected 9',v_count; end if;

  select count(*) into v_count
    from ops.rule_enforcement_point ep
    join public.rule r on r.id=ep.rule_id
    join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id
   where ep.control_key=v_expected_control
     and ep.enforcement_class=v_expected_class and ep.installed;
  if v_count<>9 or (select count(*) from ops.rule_enforcement_point ep
      join public.rule r on r.id=ep.rule_id
      join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id)<>9 then
    raise exception 'enforcement-point preimage is not the exact nine %/% rows',
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
  return query select p_mode,9::bigint,v_receipt;
end $$;

revoke all on function ops.set_rule_delivery_mode(text,text,text,text) from public;
grant select on ops.rule_delivery_activation_target,ops.rule_delivery_activation_receipt
  to carr_reader,carr_writer;
do $$ begin
  if exists(select 1 from pg_roles where rolname='carr_authority') then
    execute 'grant execute on function ops.set_rule_delivery_mode(text,text,text,text) '
            'to carr_authority';
  end if;
end $$;

commit;

do $$
begin
  if (select count(*) from ops.rule_delivery_activation_target)<>9 then
    raise exception '0317 FAILED: exact target inventory is absent';
  end if;
  begin
    update ops.rule_delivery_policy set changed_at=changed_at where singleton;
    raise exception '0317 FAILED: direct policy update was accepted';
  exception when raise_exception then
    if sqlerrm like '0317 FAILED:%' then raise; end if;
  end;
end $$;
