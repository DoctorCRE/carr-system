-- rollback: forward-only — re-declaring a retired control means restoring its hook and map entry, then letting ops/sync_control_catalog.py reinsert the row; the repin is undone by the next map-digest repin.
-- PR #1801 removes these controls, not their prose rules. Keep the binding
-- foreign key and active-approval trigger intact, as in 0348 and 0881.
do $retire_reply_gates$
declare
  key text;
  bound integer;
  claimed integer;
begin
  foreach key in array array[
    'artifact_read',
    'blocker_decider',
    'chat_lint',
    'close_before_open',
    'commit_claims',
    'completion_evidence',
    'conduct_stop',
    'drift_claim',
    'escalation',
    'gate_edit',
    'ledger_capture',
    'loose_work',
    'map_architecture',
    'peer_broadcast',
    'rule_shape',
    'settings_change',
    'staged_gate_bless',
    'unfiled_bug',
    'writing_lint'
  ] loop
    select count(*) into bound
      from ops.rule_control_binding where control_key = key;
    if bound > 0 then
      raise exception '0882 REFUSED: % still has % rule binding(s)', key, bound;
    end if;

    select count(*) into claimed
      from ops.rule_approval_receipt ar
      join rule r on r.id = ar.rule_id and r.status = 'active'
     where key = any(ar.requested_control_keys);
    if claimed > 0 then
      raise exception '0882 REFUSED: % backs % active approved rule(s)', key, claimed;
    end if;

    delete from ops.enforcement_control_catalog where control_key = key;
  end loop;
end $retire_reply_gates$;

do $rule_delivery_0882$
declare
  v_old constant text := '9dff077bb048cd7813b5f50651f5e572eab2aa9c4ecf3c2f7164d3fae026c3ac';
  v_new constant text := 'd76c2fdbca05669516ed3bc48aec4d66e46ea627d9b07723c30575d08f7d9157';
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
    raise exception '0882 REFUSED: exact activation-target contract preimage is absent';
  end if;
  update ops.rule_delivery_activation_target set map_digest=v_new
    where short_id=any(v_ids) and map_digest=v_old;
  get diagnostics v_count=row_count;
  if v_count<>cardinality(v_ids) then
    raise exception '0882 REFUSED: changed % targets, expected %',v_count,cardinality(v_ids);
  end if;
end $rule_delivery_0882$;
