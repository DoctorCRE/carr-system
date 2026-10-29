-- rollback: recreate the six indexes from db/schema.sql at commit 67e98ed9585e8aa9a4b6b7b85f18437541a24864.
-- expand-contract: contract; removes six performance-only secondary indexes without changing constraints or data.
-- lock-review: exclusive table locks last until commit. Catalog removal does not scan tables; tools/migrate.py bounds lock waits to 5s. The large event table goes last. Production lock duration remains unmeasured.
-- WR573.S3.J4, audit F9/P10. The builder reported zero scans but committed no
-- query results. The PR supplies the production SELECT for the orchestrator.
-- Keep session_work_last_seen_idx: ops.session_identity_facts projects last_seen
-- as last_observed_at and orders the combined session books by that timestamp.
-- Preserve search indexes, uniqueness and foreign-key indexes, recent email
-- indexes, and indexes on tables assigned to other audit jobs.
drop index if exists public.retrieval_query_log_miss_idx;
drop index if exists public.building_addr_trgm;
drop index if exists ops.release_correlation_idx;
drop index if exists public.session_work_kind_idx;
drop index if exists public.session_work_open_loop_idx;
drop index if exists public.event_decision_text_trgm;
