-- rollback: forward-only; preserve processing history.
-- expand-contract: expand; optional Studio processing state.
-- lock-review: nullable column without a default; metadata-only table change.
alter table phone_call_session add column processing jsonb;
