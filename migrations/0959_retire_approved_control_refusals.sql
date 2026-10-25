-- rollback: forward-only; restore the retired guards in a new migration if policy requires them.
-- expand-contract: contract; retire approval-era vetoes on mutable control contracts, preserving receipts and grants.
-- lock-review: four short trigger drops on operational metadata tables; no row scans or data rewrites.

drop trigger active_approved_control_immutable on ops.enforcement_control_catalog;
drop trigger approved_rule_admission_immutable on ops.rule_admission;
drop trigger approved_rule_control_binding_immutable on ops.rule_control_binding;
drop trigger approved_rule_enforcement_point_immutable on ops.rule_enforcement_point;
drop function ops.refuse_live_approved_control_rewrite() restrict;
drop function ops.refuse_approved_rule_contract_rewrite() restrict;
