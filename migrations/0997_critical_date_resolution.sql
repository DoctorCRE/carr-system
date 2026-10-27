-- rollback: forward-only; resolved rows and their history retain their terminal statuses.
-- expand-contract: expand; preserve open, passed and cleared while admitting dropped and done.
-- lock-review: constraint expansion scans critical_date without rewriting rows; lock_timeout and statement_timeout bound the exclusive lock and validation.

set local lock_timeout = '5s';
set local statement_timeout = '30s';

alter table public.critical_date
  drop constraint critical_date_status_check,
  add constraint critical_date_status_check
    check (status in ('open', 'passed', 'cleared', 'dropped', 'done'));
