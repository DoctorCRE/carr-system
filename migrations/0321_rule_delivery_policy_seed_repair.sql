-- 0321_rule_delivery_policy_seed_repair.sql
-- pg_dump's committed structure intentionally carries no table data, while its
-- migration ledger says 0291/0317 already ran. A rebuilt database therefore had
-- the delivery policy table but no singleton row, yielding mode:null and making
-- the shadow week impossible to measure. Preserve any live policy exactly; seed
-- only the absent row and prove the singleton exists before committing.

begin;

insert into ops.rule_delivery_policy(singleton,mode,changed_by,reason)
values (true,'shadow','migration-0321','Restore the missing singleton on schema-snapshot rebuilds; existing live policy is preserved')
on conflict (singleton) do nothing;

-- The exact nine cutover targets are configuration data, not business data,
-- and schema-only snapshots omit them for the same reason they omit the policy
-- singleton. Re-install the reviewed 0317 preimage. Existing rows are corrected
-- to that immutable map; an unexpected tenth row refuses the transaction below.
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
 ('557838a5','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a')
on conflict (short_id) do update set
  expected_scope=excluded.expected_scope,
  expected_pack=excluded.expected_pack,
  from_control=excluded.from_control,
  from_enforcement_class=excluded.from_enforcement_class,
  from_implementation_ref=excluded.from_implementation_ref,
  from_test_ref=excluded.from_test_ref,
  to_control=excluded.to_control,
  to_enforcement_class=excluded.to_enforcement_class,
  to_implementation_ref=excluded.to_implementation_ref,
  to_test_ref=excluded.to_test_ref,
  map_digest=excluded.map_digest;

do $$
begin
  if (select count(*) from ops.rule_delivery_policy where singleton) <> 1 then
    raise exception 'rule delivery policy singleton repair did not leave exactly one row';
  end if;
  if (select count(*) from ops.rule_delivery_activation_target) <> 9
     or exists (select 1 from ops.rule_delivery_activation_target
                 where map_digest <> '266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a') then
    raise exception 'rule delivery activation-target repair did not leave the exact reviewed nine';
  end if;
end;
$$;

commit;
