-- Rule 8400cd3d entering the reviewed enforcement map moves the map digest.
-- The eight pack-cutover contracts stay unchanged, so advance only their
-- guarded identity from the post-0837 map to the reviewed sizing-rule map.
-- rollback: repin the same eight targets to v_old after verifying the exact v_new preimage
do $rule_delivery_0851$
declare
  v_old constant text := 'b4e0d6689df3d96be24fb0cb888587f545bef8ffeba79c3ee6757b447f3fb308';
  v_new constant text := '2b217aec8841409c9a9c4aa659dad74f81da4750795b03ec3f4f490bac4a76f4';
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
    raise exception '0851 REFUSED: exact activation-target contract preimage is absent';
  end if;
  update ops.rule_delivery_activation_target set map_digest=v_new
    where short_id=any(v_ids) and map_digest=v_old;
  get diagnostics v_count=row_count;
  if v_count<>cardinality(v_ids) then
    raise exception '0851 REFUSED: changed % targets, expected %',v_count,cardinality(v_ids);
  end if;
end $rule_delivery_0851$;
