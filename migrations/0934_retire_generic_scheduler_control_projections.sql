-- rollback: forward-only; restore source controls before marking them installed again.
-- expand-contract: metadata only; retain every scheduler table, receipt and rule definition.
-- lock-review: three primary-key catalog updates; existing approved-control guards stay active.
update ops.enforcement_control_catalog
   set installed=false, verified_at=null, updated_at=now()
 where control_key in ('control-plane-workflow-manifest', 'verification_status', 'weekly_social_route');
