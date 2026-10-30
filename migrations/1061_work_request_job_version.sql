-- rollback: forward fix only; preserve versions used by concurrent writers.
-- expand-contract: existing jobs begin at version 1; readers expose the added column.
-- lock-review: the queue job table takes an ALTER lock for the added column.
alter table ops.work_request_job add column version integer not null default 1 check (version > 0);
