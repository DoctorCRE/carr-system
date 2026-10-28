-- rollback: recreate any index from its definition in db/schema.sql before this migration (git show HEAD~1:db/schema.sql); each is performance-only and holds no data.
-- expand-contract: contract — removes fourteen secondary indexes that production never scanned; no column, constraint, uniqueness rule or read contract changes.
-- lock-review: each drop takes a brief ACCESS EXCLUSIVE lock on its table; removal is catalog-only. tools/migrate.py owns the transaction, so concurrent drops are unavailable; the runner lock_timeout (5s) bounds any wait. public.event goes last so its lock is held the shortest time.
-- WR573.S3.J4 (database audit F9/P10). Each index had idx_scan = 0 in production on
-- 2026-10-28 over counters never reset since the database began (2026-07-30), backs no
-- constraint, has no pg_depend dependents, and no code predicate the planner could serve
-- from it: doctrine and phrase search filter a materialized set, v_precedent uses
-- word_similarity() >= 0.3, and the session_work, party_link and doctrine_edge readers are
-- served by full scans or by kept unique indexes on the same leading column.
drop index if exists public.doctrine_section_title_search_idx;
drop index if exists public.doctrine_document_title_search_idx;
drop index if exists public.retrieval_query_log_miss_idx;
drop index if exists public.retrieval_phrase_trgm_idx;
drop index if exists public.retrieval_phrase_fts_idx;
drop index if exists public.building_addr_trgm;
drop index if exists ops.release_correlation_idx;
drop index if exists public.deal_conflict_open_idx;
drop index if exists public.session_work_kind_idx;
drop index if exists public.session_work_last_seen_idx;
drop index if exists public.session_work_open_loop_idx;
drop index if exists public.party_link_from_idx;
drop index if exists public.doctrine_edge_source_idx;
drop index if exists public.event_decision_text_trgm;
