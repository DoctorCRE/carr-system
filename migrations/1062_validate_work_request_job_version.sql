-- rollback: forward fix only; retain the version invariant.
-- expand-contract: validate the constraint installed by 1061 after its transaction commits.
-- lock-review: validation takes a share update exclusive lock.
alter table ops.work_request_job validate constraint work_request_job_version_positive;
