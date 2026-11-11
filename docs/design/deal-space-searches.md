# Deal saved space searches

Candidate A embeds briefs in deal JSON. It rewrites unrelated deal data and couples search runs to deal revisions.
Candidate B, chosen, adds one deal-bound table with its own optimistic version. Three versioned verbs save, read, and run a search. They reuse canonical deal access, property search, and cart-compatible property IDs.

The brief stores canonical criteria, complete free-text requirements, client-prefill provenance, last run time, run criteria, and result property IDs. Requirements capture budget, timing, geography and space needs that canonical filters cannot execute. No MLS query or planner is introduced.

Draft, saved and archived are durable states. Stale derives from changed criteria or a result older than 24 hours. Rerun is atomic. A failed run preserves the prior snapshot. Reopen reads the exact ID. Restore saves an archived brief with its current version.

## System design

Postgres owns persistence and atomic version checks. The authenticated module installs existing deal-policy decisions. Reads use one indexed query capped at 100 briefs, with explicit truncation. Runs call canonical search once, capped at 100 properties with pagination receipts. At ten times current load, canonical search and the existing all-deal authorization scan limit throughput first. There is no per-brief N+1 query. Row locks serialize only one search's writes. The existing envelope supplies idempotency and event audit.

No cache, queue, provider call, external circuit breaker, or parallel fan-out applies. Reads require current deal authority. Result snapshots may be old and visibly stale. Run failures roll back and preserve prior data. The search version prevents concurrent edits from silently overwriting one another.

The blocking steps are authorization and migration tests. One worker owns the coupled migration and verbs because nested delegation is forbidden. Shared mutable state is confined to one row per search. The app and software-factory repositories need no edits for this producer.
