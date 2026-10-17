-- PR #1800 removes eval receipt enforcement while retaining eval runners and
-- baselines. Retire its catalog claim using the guarded deletion pattern from
-- 0348. The binding foreign key and active-approval trigger remain in force.
-- rollback: re-insert the eval_receipt row into ops.enforcement_control_catalog with its prior values from db/schema snapshot history; nothing else is changed here
do $retire_eval_receipt$
declare
  bound integer;
  claimed integer;
begin
  select count(*) into bound
    from ops.rule_control_binding
   where control_key = 'eval_receipt';
  if bound > 0 then
    raise exception '0881 REFUSED: eval_receipt still has % rule binding(s)', bound;
  end if;

  select count(*) into claimed
    from ops.rule_approval_receipt ar
    join rule r on r.id = ar.rule_id and r.status = 'active'
   where 'eval_receipt' = any(ar.requested_control_keys);
  if claimed > 0 then
    raise exception '0881 REFUSED: eval_receipt backs % active approved rule(s)', claimed;
  end if;

  delete from ops.enforcement_control_catalog where control_key = 'eval_receipt';
end $retire_eval_receipt$;
