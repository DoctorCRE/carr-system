-- rollback: forward fix only; retain historical repository names on existing jobs.
-- lock-review: replacing the CHECK takes a brief table lock, without rewriting rows.
alter table ops.work_request_job drop constraint if exists work_request_job_repository_check;
alter table ops.work_request_job add constraint work_request_job_repository_check
  check (repository in ('DoctorCRE/carr-system','DoctorCRE/doctorcre-app','jbookout/doctorcre-app','jbookout/software-factory'));
