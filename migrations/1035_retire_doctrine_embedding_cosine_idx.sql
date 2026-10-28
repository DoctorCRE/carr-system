-- rollback: in a new migration, create index doctrine_embedding_cosine_idx on public.doctrine_embedding using hnsw (embedding public.vector_cosine_ops); about 51 MB, built from the stored rows; search results do not depend on it.
-- expand-contract: contract — removes an index nothing reads. search_doctrine_semantic scans exactly (max similarity over all chunks) and never orders by distance, so the planner cannot use an HNSW index for it; the other doctrine_embedding queries (backfill and coverage selects by revision_id and model, inserts) use doctrine_embedding_pkey or a sequential scan.
-- lock-review: the index drop takes a brief ACCESS EXCLUSIVE lock on doctrine_embedding (about 29k rows); writes to it are rare backfill inserts, so the wait is negligible.
-- Card: WR573.S2.J4 (database-audit P9 / F8). An index-served approximation measured 0.93 top-10 overlap with the exact ranking (long sections fill the candidate window), so search keeps its exact scan and the unused 51 MB index goes instead.

drop index if exists public.doctrine_embedding_cosine_idx;
