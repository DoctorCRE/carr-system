-- rollback: forward-only; restore source controls before marking them installed again.
-- expand-contract: metadata only; retain every scheduler table and receipt.
-- lock-review: three primary-key catalog updates; existing approved-control guards stay active.
update ops.enforcement_control_catalog as c
   set implementation_ref=v.implementation_ref,
       test_ref=v.test_ref,
       enforcement_class=v.enforcement_class,
       installed=v.installed,
       verified_at=case when v.installed then now() else null end,
       updated_at=now()
  from (values
    ('control-plane-workflow-manifest', 'ops/config/control-plane-workflows.v1.json', 'ops/control-plane-selftest.py; ops/control-plane-db-gate.py', 'deny_gate', false),
    ('verification_status', 'migrations/0104_reverify_queue_supersession.sql; tools/control-plane.py; lib/control_plane_inputs.py', 'ops/control-plane-inputs-selftest.py; ops/control-plane-db-gate.py', 'surfacing', false),
    ('weekly_social_route', 'tools/social-batch-candidates.py', 'ops/client-asset-controls-selftest.py', 'judgment_ambient', true)
  ) as v(control_key,implementation_ref,test_ref,enforcement_class,installed)
 where c.control_key=v.control_key;
