Size: one-session feature. No child agents permitted by brief.
Ground: Home sources are canonical actor-scoped rows; project_items builds versioned cards; withEnvelope provides tool_call audit. Parties and actors are global carr-internal identities. Foreign tenant context must refuse before resolving either.
Design A: add waiting fields to dispositions. Rejected because ownership and disposition versions would be coupled to an independent relationship.
Design B (chosen): separate actor/item waiting state plus durable command receipts. New read-home-inbox-v5 adds waiting_on and waiting_version; v1-v4 stay unchanged. Commands bind both current item_version and waiting base_version. SQL owns atomic compare-and-set, receipt and completion cleanup; typed adapter owns schema and envelope.
Throughput checkpoint:
- Blocking first steps: dependencies, statechart, failing public command test.
- Independent workstreams: none; one coupled migration/command/projection.
- Shared mutable state: lock actor/item waiting state; canonical completion trigger updates same row, so completion wins racing sets.
- Smallest safe decomposition: one worker with red/green vertical slices; independent preminted reviewer follows.
Test seams: registered waiting command and registered Home v5 read, disposable synthetic PostgreSQL. SQL permissions tested directly.

Version rebind (WR617.S15.J10): S14 owns v4 completion times. Waiting is v5,
composes the v4 SQL snapshot and extends its contract. Migration 1150 avoids
all occupied numbers in the fetched main and WR branches. No transition changes.
System design: Postgres owns actor/item waiting state and durable receipts;
the typed command adapter owns validation and audit, and Home owns projections.
Capacity/load/scaling: unchanged 1000-item/10000-notification read bounds;
at 10x load the existing bounded projection and database capacity break first.
Storage: one actor/item join, no per-card external lookup or N+1 query.
Caching: none; every read resolves current identity and completion.
Availability/consistency: sequential database projection; database failure
refuses the read/write, never serves stale versions or a partial write.
Async/queues/load spread: n/a; synchronous actor-scoped reads and transactions.
External calls/circuit breakers/rate limits: n/a; this slice adds none.
Writes retain UUID idempotency and both version guards. Public PostgreSQL tests
cover exact retries, conflicts, completion races, bounds and tenant isolation;
version guards prove v4 stays unchanged and v5 includes completion and waiting.
