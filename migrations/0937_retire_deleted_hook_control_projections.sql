-- rollback: forward-only; restore the referenced implementations before marking these controls installed.
-- expand-contract: metadata only; preserve rule definitions, bindings, receipts and historical control references.
-- lock-review: four primary-key catalog updates; existing approved-control guards stay active.
-- The surviving guard and record-home hook remain installed. These legacy bundles
-- also require deleted scripts, so their old installed claim no longer holds.
update ops.enforcement_control_catalog
   set installed=false, verified_at=null, updated_at=now()
 where control_key in ('draft_export_read', 'github-burst-guard', 'one_repo', 'record_home');
