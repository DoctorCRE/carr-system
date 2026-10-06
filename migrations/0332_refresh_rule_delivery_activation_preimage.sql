-- Refresh the exact nine scoped-rule cutover preimages after the shadow-only
-- pre-use reselection rail joined the existing pack_delivery control. This
-- changes no policy and no installed enforcement point; a later, separately
-- authorized cutover still owns the atomic shadow/enforced transition.

begin;

do $$
declare
  v_updated bigint;
  v_catalog_updated bigint;
  v_expected_ids text[] := array[
    '25fcddee','3fa17fa0','72e06bdf','581cb3fe','113b3833',
    '57d13061','c66dc739','49533583','557838a5'
  ];
begin
  if (select mode from ops.rule_delivery_policy where singleton)
       is distinct from 'shadow' then
    raise exception 'rule delivery activation preimage refresh requires shadow mode';
  end if;

  if exists (
    select 1
      from ops.rule_approval_receipt ar
      join public.rule r on r.id = ar.rule_id and r.status = 'active'
     where 'pack_delivery' = any(ar.requested_control_keys)
  ) then
    raise exception 'pack_delivery backs an active approved rule and is immutable';
  end if;

  update ops.enforcement_control_catalog
     set implementation_ref =
           'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py',
         test_ref =
           'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py',
         updated_at = now()
   where control_key = 'pack_delivery'
     and enforcement_class = 'stop_gate'
     and installed
     and verified_at is not null
     and implementation_ref = 'hooks/rule-pack-drift-gate.py'
     and test_ref =
           'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py';

  get diagnostics v_catalog_updated = row_count;
  if v_catalog_updated <> 1 then
    raise exception
      'rule delivery activation preimage refresh expected one old pack_delivery catalog row, updated %',
      v_catalog_updated;
  end if;

  update ops.rule_delivery_activation_target t
     set map_digest =
           'c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997',
         to_implementation_ref =
           'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py',
         to_test_ref =
           'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py'
   where t.short_id = any(v_expected_ids)
     and (t.short_id, t.expected_scope, t.expected_pack) in (
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
     and t.from_control = 'session_boot'
     and t.from_enforcement_class = 'surfacing'
     and t.from_implementation_ref =
           'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js'
     and t.from_test_ref = 'command:python3 hooks/gate-integrity.py --selftest'
     and t.map_digest =
           '266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'
     and t.to_control = 'pack_delivery'
     and t.to_enforcement_class = 'stop_gate'
     and t.to_implementation_ref = 'hooks/rule-pack-drift-gate.py'
     and t.to_test_ref =
           'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py';

  get diagnostics v_updated = row_count;
  if v_updated <> cardinality(v_expected_ids) then
    raise exception
      'rule delivery activation preimage refresh expected nine old rows, updated %',
      v_updated;
  end if;

  if (select count(*) from ops.rule_delivery_activation_target)
       <> cardinality(v_expected_ids)
     or exists (
       select 1
         from ops.rule_delivery_activation_target t
        where t.short_id <> all(v_expected_ids)
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
           or t.map_digest <>
                'c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997'
           or t.to_control <> 'pack_delivery'
           or t.to_enforcement_class <> 'stop_gate'
           or t.to_implementation_ref <>
                'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py'
           or t.to_test_ref <>
                'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py'
     ) then
    raise exception
      'rule delivery activation preimage refresh did not leave the exact reviewed nine';
  end if;
end;
$$;

commit;
