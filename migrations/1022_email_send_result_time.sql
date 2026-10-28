-- rollback: forward fix only; retain recorded delivery result times.
-- expand-contract: nullable result time leaves historical outcomes unknown.
-- lock-review: brief catalog lock; no data rewrite.
alter table public.email_proposal add column result_recorded_at timestamptz;
