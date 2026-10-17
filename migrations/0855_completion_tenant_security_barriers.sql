-- 0756 replaced these tenant-scoped views without retaining their reloptions.
-- Preserve the existing predicates and grants while preventing caller
-- expressions from running ahead of the tenant filter.
-- rollback: forward-only — retain tenant barriers; correct view definitions in a later migration.
alter view ops.completion_current_observation set (security_barrier=true);
alter view ops.completion_dimension_matrix set (security_barrier=true);
