-- Installing session_boot, ledger_capture and loop_guard (0874) gave each a
-- CI-run test in ops/config/rule-enforcement-map.json, which moves the map
-- digest. The eight pack-cutover contracts stay unchanged (their frozen
-- from_test_ref is the activation target's own record, not the catalog row),
-- so advance only their guarded identity from the post-0859 map to this one.
-- rollback: repin the same eight targets to v_old after verifying the exact v_new preimage
do $rule_delivery_0875$
declare
  v_old constant text := 'f1d16ff39227ab58a8fd09bde84a5af590629829c4a8bc219a12b0b5e6acc72e';
  v_new constant text := 'b7666273937390aa3f1161ab5ce223cb652e18be2d21167b554d86fd2d76b1ff';
  v_ids constant text[] := array[
    '113b3833','25fcddee','3fa17fa0','49533583',
    '557838a5','57d13061','72e06bdf','c66dc739'
  ];
  v_from_implementation_ref constant text :=
    'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js';
  v_from_test_ref constant text := 'command:python3 hooks/gate-integrity.py --selftest';
  v_to_implementation_ref constant text :=
    'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py';
  v_to_test_ref constant text :=
    'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py';
  v_count integer;
begin
  perform 1 from ops.rule_delivery_policy where singleton for update;
  lock table ops.rule_delivery_activation_target in share row exclusive mode;
  with expected_base(short_id,expected_scope,expected_pack) as (values
    ('113b3833','joe','governance-rules'),
    ('25fcddee','shared','governance-rules'),
    ('3fa17fa0','shared','client-deal'),
    ('49533583','joe','joe-comms'),
    ('557838a5','joe','joe-comms'),
    ('57d13061','joe','joe-comms'),
    ('72e06bdf','shared','client-deal'),
    ('c66dc739','joe','joe-comms')
  ), expected as (
    select short_id,expected_scope,expected_pack,
           'session_boot'::text as from_control,
           'surfacing'::text as from_enforcement_class,
           v_from_implementation_ref as from_implementation_ref,
           v_from_test_ref as from_test_ref,
           'pack_delivery'::text as to_control,
           'stop_gate'::text as to_enforcement_class,
           v_to_implementation_ref as to_implementation_ref,
           v_to_test_ref as to_test_ref,
           v_old as map_digest
      from expected_base
  ), actual as (
    select short_id,expected_scope,expected_pack,from_control,from_enforcement_class,
           from_implementation_ref,from_test_ref,to_control,to_enforcement_class,
           to_implementation_ref,to_test_ref,map_digest
      from ops.rule_delivery_activation_target
  ), differences as (
    (select * from actual except select * from expected)
    union all
    (select * from expected except select * from actual)
  )
  select count(*) into v_count from differences;
  if v_count<>0 then
    raise exception '0875 REFUSED: exact activation-target contract preimage is absent';
  end if;
  update ops.rule_delivery_activation_target set map_digest=v_new
    where short_id=any(v_ids) and map_digest=v_old;
  get diagnostics v_count=row_count;
  if v_count<>cardinality(v_ids) then
    raise exception '0875 REFUSED: changed % targets, expected %',v_count,cardinality(v_ids);
  end if;
end $rule_delivery_0875$;
