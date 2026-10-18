-- rollback: restore the retired shadow hook, test, and map entries first, then
-- restore pack_delivery's previous implementation_ref and test_ref in a guarded migration.
-- Keep the active PreToolUse delivery control registered after shadow retirement.
do $$
declare
  changed integer;
begin
  if exists (
    select 1 from ops.enforcement_control_catalog
     where control_key = 'pack_delivery'
       and implementation_ref = 'hooks/rule-pack-preuse-reselection.py'
       and test_ref = 'ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py'
       and enforcement_class = 'stop_gate' and installed
  ) then
    return;
  end if;

  -- The active_approved_control_immutable trigger remains in force.
  update ops.enforcement_control_catalog
     set implementation_ref = 'hooks/rule-pack-preuse-reselection.py',
         test_ref = 'ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py',
         updated_at = now()
   where control_key = 'pack_delivery'
     and implementation_ref = 'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py'
     and test_ref = 'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py'
     and enforcement_class = 'stop_gate' and installed;
  get diagnostics changed = row_count;
  if changed <> 1 then
    raise exception '0884 REFUSED: expected the exact pre-retirement pack_delivery control, changed %', changed;
  end if;
end $$;
