-- rollback: forward fix only; preserve runner starts attached to minted jobs.
alter table ops.work_request_job
  add column repository text check (repository in ('DoctorCRE/carr-system','jbookout/doctorcre-app','jbookout/software-factory')),
  add column runner_job_id text check (length(btrim(runner_job_id)) between 1 and 100),
  add column started_at timestamptz,
  add constraint work_request_job_start_pair check ((runner_job_id is null) = (started_at is null));
