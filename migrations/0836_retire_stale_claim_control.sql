-- 0836_retire_stale_claim_control.sql
-- THE STALE-CLAIM STOP GATE LEAVES THE ENFORCEMENT CATALOG WITH ITS SOURCE.
--
-- WHY THIS EXISTS. The change carrying this migration deletes
-- hooks/stale-claim-gate.py, its paid judge (ops/stale_claim_judge.py) and its
-- self-tests, and ops/config/rule-enforcement-map.json no longer declares the
-- stale_claim control. Migration 0274 seeded that control as installed, and
-- ops/control-catalog-parity-gate.py fails on any catalog row the map no
-- longer declares, because the gate will not delete a row that may still be
-- enforcing a live rule. This is the deliberate retirement it asks for, in the
-- same guarded shape as 0290 (ledger_boundary) and 0348 (git_writer,
-- canonical_edit).
--
-- WHAT STILL ENFORCES THE RULE IT BACKED. stale_claim was only ever the
-- second_control on d5dcfe26 (a dated memo is not self-updating); that rule's
-- primary control, drift_claim, stays in the map and in this catalog.
--
-- WHY IT IS SAFE, checked by the database rather than taken on trust:
--   1. rule_controls in ops/config/rule-enforcement-map.json names stale_claim
--      nowhere.
--   2. ops.rule_control_binding references control_key ON DELETE RESTRICT, so
--      a live binding aborts this migration instead of silently un-enforcing a
--      rule; the guard below names that reason before the constraint does.
--   3. active_approved_control_immutable fires BEFORE DELETE and raises if the
--      key appears in any rule_approval_receipt for an active rule.

do $$
declare
  bound   integer;
  claimed integer;
begin
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key = 'stale_claim') then
    raise notice '0836: stale_claim is already absent from the catalog; nothing to retire';
    return;
  end if;

  select count(*) into bound
    from ops.rule_control_binding
   where control_key = 'stale_claim';
  if bound > 0 then
    raise exception '0836 REFUSED: stale_claim still has % rule binding(s); '
                    'it is enforcing something and must not be retired', bound;
  end if;

  select count(*) into claimed
    from ops.rule_approval_receipt ar
    join rule r on r.id = ar.rule_id and r.status = 'active'
   where 'stale_claim' = any(ar.requested_control_keys);
  if claimed > 0 then
    raise exception '0836 REFUSED: stale_claim backs % active approved rule(s)', claimed;
  end if;

  delete from ops.enforcement_control_catalog where control_key = 'stale_claim';
end $$;

do $$
begin
  if exists (select 1 from ops.enforcement_control_catalog
              where control_key = 'stale_claim') then
    raise exception '0836 FAILED: stale_claim is still in the catalog after the delete';
  end if;

  -- The rule's surviving control must be untouched, or this retired the wrong
  -- row and the parity gate would have agreed with it.
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key = 'drift_claim') then
    raise exception '0836 FAILED: drift_claim is gone — d5dcfe26 lost its primary control';
  end if;
end $$;
