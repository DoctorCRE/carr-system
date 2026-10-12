-- 0858_narrow_loose_work_control_after_tracker_retirement.sql
-- THE LOOSE-WORK CONTROL STOPS NAMING THE RETIRED STAGING-OBSERVATION TRACKER.
--
-- WHY THIS EXISTS. The change carrying this migration deletes
-- hooks/staging-observation-tracker.py (a hook whose ledger no gate, report or
-- verb read), and ops/config/rule-enforcement-map.json now lists only
-- hooks/loose-work-gate.py as the loose_work control's implementation.
-- ops/control-catalog-parity-gate.py and bin/schema-snapshot.sh compare
-- implementation_ref string-for-string with the map, so a catalog row that
-- still names the deleted file is a disagreement. This narrows the row the
-- same way 0348 narrowed it when hooks/staging-attribution-gate.py retired.
--
-- WHAT STILL ENFORCES THE RULE. hooks/loose-work-gate.py was and remains the
-- loose_work Stop gate; the tracker only wrote an observation ledger beside it.
-- The test_ref, enforcement_class, installed flag and verification are left as
-- they are.
--
-- WHY IT IS SAFE, checked by the database rather than taken on trust:
--   1. The update matches only the exact pre-retirement row, and refuses unless
--      exactly one row changed.
--   2. active_approved_control_immutable fires BEFORE UPDATE and raises if
--      loose_work backs an active approved rule; the guard below names that
--      reason first.
-- rollback: set loose_work's implementation_ref back to
--   'hooks/loose-work-gate.py; hooks/staging-observation-tracker.py'
--   after restoring the hook file and the map entry.

do $$
declare
  claimed integer;
  updated integer;
begin
  if exists (select 1 from ops.enforcement_control_catalog
              where control_key = 'loose_work'
                and implementation_ref = 'hooks/loose-work-gate.py') then
    raise notice '0858: loose_work already names only hooks/loose-work-gate.py';
    return;
  end if;

  select count(*) into claimed
    from ops.rule_approval_receipt ar
    join rule r on r.id = ar.rule_id and r.status = 'active'
   where 'loose_work' = any(ar.requested_control_keys);
  if claimed > 0 then
    raise exception '0858 REFUSED: loose_work backs % active approved rule(s) and is immutable', claimed;
  end if;

  update ops.enforcement_control_catalog
     set implementation_ref = 'hooks/loose-work-gate.py',
         updated_at = now()
   where control_key = 'loose_work'
     and enforcement_class = 'stop_gate'
     and implementation_ref = 'hooks/loose-work-gate.py; hooks/staging-observation-tracker.py'
     and test_ref = 'ops/loose-work-gate-selftest.py';
  get diagnostics updated = row_count;
  if updated <> 1 then
    raise exception '0858 REFUSED: expected the exact pre-retirement loose_work row, changed %', updated;
  end if;
end $$;

do $$
begin
  if exists (select 1 from ops.enforcement_control_catalog
              where implementation_ref like '%staging-observation-tracker%') then
    raise exception '0858 FAILED: a catalog row still names hooks/staging-observation-tracker.py';
  end if;
end $$;
