# Deal saved space searches

Candidate A embeds briefs in deal JSON. It rewrites unrelated deal data and couples search runs to deal revisions.
Candidate B, chosen, adds one deal-bound table with its own optimistic version. Three versioned verbs save, read, and run a search. They reuse canonical deal access, property search, and cart-compatible property IDs.

The brief stores canonical criteria, complete free-text requirements, client-prefill provenance, last run time, run criteria, and result property IDs. Requirements capture budget, timing, geography and space needs that canonical filters cannot execute. No MLS query or planner is introduced.

Draft, saved and archived are durable states. Stale derives from changed criteria or a result older than 24 hours. Rerun is atomic. A failed run preserves the prior snapshot. Reopen reads the exact ID. Restore saves an archived brief with its current version.

## System design

Postgres owns persistence and atomic version checks. The authenticated module installs existing deal-policy decisions through a narrowly granted candidate reader. Every call checks current deal access before replay. Brief queries use an index and cap output at 100, with explicit truncation. Runs call canonical search once, capped at 100 properties with pagination receipts. At ten times current load, canonical search and the existing all-deal authorization scan limit throughput first. There is no per-brief N+1 query. Row locks serialize only one search's writes. The existing envelope supplies idempotency and event audit.

No cache, queue, provider call, external circuit breaker, or parallel fan-out applies. Reads require current deal authority. Result snapshots may be old and visibly stale. Run failures roll back and preserve prior data. The search version prevents concurrent edits from silently overwriting one another.

The blocking steps are authorization and migration tests. One worker owns the coupled migration and verbs because nested delegation is forbidden. Shared mutable state is confined to one row per search. The app and software-factory repositories need no edits for this producer.

## Consumer interface

`save-deal-space-search-v1` creates a caller-generated search UUID at `expected_version=0`. Later saves replace the brief at its exact version. Every mutation takes a fresh idempotency UUID. An identical retry returns the original response with `replayed=true`, after checking current deal access. Reusing a search UUID never moves it to another deal.

`read-deal-space-searches-v1` reopens the exact deal and search UUID. A null search UUID lists the newest 100 briefs and reports truncation. Archived briefs remain readable. Unknown or unavailable identities return `space_search_unavailable`.

`run-deal-space-search-v1` takes the current version and only runs saved briefs. The stored canonical criteria include all property-search filters, a null cursor, and a page size up to 100. The response retains `run_criteria`, `last_run_at`, ordered `result_property_ids`, `has_more`, and `next_cursor`. This is a bounded first-page snapshot. Continue discovery with canonical property search and the returned cursor. A rerun always replaces the first-page snapshot atomically.

For T03, reopen the search, inspect its state and run criteria, rerun when stale, and pass reviewed result IDs to the existing Tour selection-cart verb. Search results do not become selected properties automatically. A deleted or unavailable deal refuses reads and writes, including idempotent replay. The optional prefill client must equal the deal's current client. Its reference and observed time describe caller-supplied provenance, not independent verification.

The response schemas in `mcp-server/contracts/deal-space-searches.v1.json` are generated from the registered canonical criteria schema. The recorded fixture in `mcp-server/test/fixtures/deal-space-searches.json` comes from the disposable registered-verb lifecycle test. Set `RECORD_SPACE_SEARCH_FIXTURE` to an explicit output path to record a fresh fixture. The consumer contract test checks both the fixture and schema generation against the real registry.
