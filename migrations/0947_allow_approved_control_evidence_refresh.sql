-- rollback: restore ops.refuse_live_approved_control_rewrite from 0228 in a new migration.
-- expand-contract: migrate the approved-control guard to permit evidence refreshes while preserving enforcement identity.
-- lock-review: replace one existing trigger function; retain its trigger, signature, owner and grants.
-- Decision 1facbf00-60d9-4cde-bfab-9798f1b6e307 permits source delivery to repair old approval-semantics gates.
create or replace function ops.refuse_live_approved_control_rewrite()
returns trigger language plpgsql as $$
begin
  if exists (
    select 1 from ops.rule_approval_receipt ar
     join public.rule r on r.id=ar.rule_id and r.status='active'
    where old.control_key=any(ar.requested_control_keys)
  ) then
    if tg_op='UPDATE'
       and (to_jsonb(new)-array['implementation_ref','test_ref','verified_at','updated_at'])
           is not distinct from
           (to_jsonb(old)-array['implementation_ref','test_ref','verified_at','updated_at']) then
      return new;
    end if;
    raise exception 'control % backs an active approved rule and is immutable',old.control_key;
  end if;
  return case when tg_op='DELETE' then old else new end;
end $$;
