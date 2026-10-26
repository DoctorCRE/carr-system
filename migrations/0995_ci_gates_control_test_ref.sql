-- rollback: forward-only; restore the deleted Hermes selftest before pointing the control back at it.
-- expand-contract: metadata only; one catalog reference moves to a test CI still runs.
-- lock-review: one primary-key catalog update; no table scan or schema rewrite.
-- WR-000546 deleted ops/hermes-autonomy-check-selftest.py. ci_gates now names
-- the checker's own selftest, matching ops/config/control-enforcement-classes.v1.json.
update ops.enforcement_control_catalog
   set test_ref='ops/ci-selftest.py', updated_at=now()
 where control_key='ci_gates';
