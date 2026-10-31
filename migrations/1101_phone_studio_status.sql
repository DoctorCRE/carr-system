-- rollback: code rollback leaves the nullable status available for later readers.
-- expand-contract: expand; independent metadata on the existing phone session.
-- lock-review: nullable column without a default; no table rewrite.
alter table phone_call_session add column studio_status jsonb;
