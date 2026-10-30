-- Rollback: drop the nullable job_type column; existing jobs retain their identities.
-- Legacy jobs remain untyped and are skipped until a typed runner start adopts their type.
alter table ops.work_request_job add column job_type text;
