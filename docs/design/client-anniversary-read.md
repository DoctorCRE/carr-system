# Client anniversary read

Joe and Dell can read upcoming yearly anniversaries of confirmed closed-won deals. The data shape is a versioned response with a state, Chicago observation date, coverage counts, cited rows, and a next offset.

## System design

The read module owns validation, one Postgres snapshot, anniversary policy, pagination, and response parsing. The family registers one read-only verb. Dr. CRE and the fixed authenticated GET adapter use that verb. Postgres owns canonical closing dates and client linkage. No app state changes.

Compared designs were SQL anniversary calculations with duplicated proof arithmetic, and bounded source rows with one shared calendar parser. The second keeps invalid-date refusal, leap-day policy, and response verification together. It reads at most 1001 rows. At 1001 rows it refuses as unavailable rather than claiming complete coverage. Today and 10x share that hard cap; the first capacity failure is the candidate cap, not unbounded memory or N+1 reads. Offset pagination is deterministic within each snapshot, ordered by anniversary, client ID, deal ID, and closing date. Concurrent source edits require a fresh read; offsets are not durable cursors.

A single query joins deal, client, and party and preserves their versions. No fan-out, cache, invalidation, eviction, async producers, queue, or background consumers exist. Query failure refuses all results with unavailable state. No stale fallback exists. The host's existing request and database budgets bound the call. The module performs no external API call, so new external circuit breakers and alerts are n/a. The HTTP adapter uses the existing request rate budget. There are no writes, so write idempotency is n/a.

Only phase closed or invoiced with outcome won qualifies. Canonical closed_on is the sole date authority. Missing and invalid dates count as skipped-invalid coverage. Future closing dates and year-zero observations never qualify. February 29 anniversaries fall on February 28 in non-leap years. Impossible dates are rejected, never normalized. Merged clients and deleted parties are inaccessible. Both authenticated partners share the existing brokerage read scope.

Unknown input fields or invalid ranges return needs_input without a query. Unauthorized actors fail before a query. Known empty, skipped-invalid coverage, and unavailable sources remain distinct. Defaults are horizon_days 30, limit 20, offset 0. Caps are 90 days, 50 returned rows, offset 999, and 1000 source rows. No required subject parameter exists for this global read; guessed subject or actor parameters are rejected.

## Throughput checkpoint

- Blocking first steps are source grounding, statechart, and a failing acceptance test.
- Independent workstreams are n/a because one worker owns this bounded slice.
- Shared mutable state is limited to additive registration and route wiring in this isolated branch.
- Smallest safe decomposition is one worker because both adapters must share one response contract.
