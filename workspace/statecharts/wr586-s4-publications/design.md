# Publication design before implementation

One PR-sized CARR contract unit. App tab, hosting and byte delivery belong to later jobs.

The data shape is a publication with an immutable portal binding, kind, explicit partner-written title/content or a shared finalized deliverable reference, revision and published flag. Milestones are text a partner explicitly publishes. No live deal phase, notes, financials, object keys or credentials enter the projection.

Considered copied snapshots and live allowlisted projection. Snapshots duplicate file privacy and become stale on unsharing. Choose the projection, checking current file visibility and the published file version on every read. Partner preview and authenticated portal reading call the same projection function. Existing explicitly shared portal materials remain visible.

## System design

Architecture: Postgres owns publications and projection; MCP owns partner authorization, envelope idempotency and audit. Existing portal authentication retains password limits. No public mutation route.
Capacity: 100 active items and 1000 total records per portal. At 10x portals indexed reads stay bounded; first limit is per-portal authoring capacity, refused rather than truncated. A single aggregate with an indexed file join avoids N+1.
Load spread/scaling: different portals operate independently; writes lock one portal and compare item revisions. Postgres is the storage bottleneck.
Caching: none; file revocations must be current. Eviction/invalidation n/a.
Availability: sequential authorization and transactional database work, fail closed without partial publication.
Consistency: current publication and file privacy; resharing a file does not revive an old publication because its version changed.
Async: n/a; no queue or sends.
Circuit breakers: n/a; no new external calls. Hosted delivery is a later unit.
Rate bounds: 100 active and 1000 total publications, no fan-out or retry loop. Existing password reader retains five-failure lockout.
Idempotency: envelope keys on writes and SQL revision CAS. Reads never change publication state.

## Throughput checkpoint

Blocking first steps: scope, canonical design, existing delivery and migration heads before code.
Independent workstreams: n/a; one coupled migration/contract needs one worker, no delegates authorized.
Shared mutable state: per-portal row lock and item revision; portals are independent.
Smallest safe decomposition: publications here; hosting, downloads and app tab are separate preminted jobs.

## Verification

Synthetic PostgreSQL tests exercise hidden read, publish, replace, unpublish, private revocation, stale CAS, roles, disabled/cross-client isolation and caps. The chart models one item, omitting authentication, capacity and concurrency, which executable tests cover. The statechart command could not run TLC because TLA2TOOLS was unavailable; no formal proof is claimed.

## Model limitation

Regenerate with `node /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/statechart/scripts/statechart.mjs from-chart /tmp/wr586-s4-chart/agreed.mmd --module Agreed`. The command generated TLA+ then exited 2 because TLC was unavailable. No PNG or formal verification was produced. The chart records intended behavior and the SQL tests are the executed evidence.
