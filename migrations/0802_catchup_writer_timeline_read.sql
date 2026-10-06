-- Catch-up now uses a read-only writer transaction so it can reconcile the
-- actor-bound calendar replay ledger. Its timeline view was created after the
-- writer's original all-tables grant, so supply the one missing read privilege.
-- Preserve the reader ACL and the read-only handler route. No DML is granted.
grant select on public.v_subject_timeline to carr_writer;
