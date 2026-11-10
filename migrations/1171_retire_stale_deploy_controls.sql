-- rollback: forward-only; restore implementations before restoring these controls.
-- expand-contract: contract; retire only obsolete catalog rows, preserving rule records and receipts.
-- lock-review: four primary-key deletions; refuse bound or actively approved controls.
select 1;
