-- rollback: forward-only; restore the implementation and source declaration before restoring this binding.
-- expand-contract: remove only the stale binding to the control retired by 0944; preserve the active rule and receipts.
-- lock-review: one composite-primary-key delete; the approved-binding trigger remains enabled and unchanged.
delete from ops.rule_control_binding
 where rule_id = '5e89c211-996a-4681-b93d-d0226dbf21fa'::uuid
   and control_key = 'control-plane-workflow-manifest';
