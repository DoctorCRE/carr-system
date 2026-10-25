-- rollback: forward-only; re-add only with a real producer and consumer.
-- expand-contract: contract; no retained writer or consumer uses cache evidence_refs.
-- lock-review: metadata-only DROP COLUMN; ACCESS EXCLUSIVE acquisition bounded to 5s.
SET LOCAL lock_timeout = '5s';
ALTER TABLE ops.cognition_result_cache DROP COLUMN evidence_refs RESTRICT;
