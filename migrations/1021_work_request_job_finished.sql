-- rollback: alter table ops.work_request_job drop column finished; the board then re-derives every stage from GitHub alone.
-- expand-contract: expand — adds one nullable column beside the existing job facts.
-- lock-review: ops.work_request_job is the S1 queue table from 1017 with a handful of rows; a nullable column with no default needs no rewrite.
-- A job's first observed merged or closed state and its PR links, saved once by the work-queue read so the
-- GitHub search window only has to cover unfinished jobs. Table-level grants from 1017 already cover it.
alter table ops.work_request_job add column finished jsonb
  check (finished is null or (jsonb_typeof(finished) = 'object' and finished->>'stage' in ('merged','closed')
    and jsonb_typeof(finished->'prs') = 'array'));
