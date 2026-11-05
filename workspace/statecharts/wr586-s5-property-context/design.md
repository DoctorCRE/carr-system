# System design

Card: WR586.S5.J11

Sizing is one session unit. The existing proposal lifecycle is a dependency.
The application reads canonical property context without depending on route stops.

## Architecture and alternatives

The proposal module owns decisions and immutable cart versions. Tour owns property
identity, status, and reviewed display assertions. The existing read function owns
the hydration seam. Its new properties array contains carr_property_id, name,
address, property_status, and eligible. Existing proposal items remain unchanged.
Snapshot labels are display data and cannot authorize acceptance.

Alternative A enriches each proposal item through individual application queries.
It repeats shared IDs and misses cart-only removals. Alternative B builds one
set of IDs across the bounded page and hydrates that set in the SQL read.
B keeps deal authorization and one consistent statement snapshot in Postgres.
No new public verb or registration is needed.

## Capacity, load spread, and scaling

The read remains capped at 50 proposals, each with at most 100 proposed, original,
and current cart IDs. At today's load and 10x the page bound is unchanged.
Distinct IDs bound hydration to 15000 entries. Assertion sorting is the first
likely cost as history grows. Indexed tenant/property access and one set query
avoid application N+1 calls. No new process or load distributor is needed.

## Caching and consistency

No cache. Every read resolves current status and effective reviewed labels in
one statement. Missing or other-tenant properties return null labels/status and
eligible false. Current inactive rows retain their canonical labels for display.
No portal label is accepted. A later rename appears on the next read.
Acceptance independently checks active tenant membership using the existing
proposal rule before appending the cart. A stale selection version refuses.
A receipt replay returns its original result without another mutation.

## Availability, external calls, async, and limits

The read is one synchronous database statement. Database failure fails the read;
there is no stale eligibility fallback, queue, producer, or consumer.
No external call is added. The existing pull keeps its two-failure breaker,
60-second cooldown, 20 requests per minute, five-second timeout, and bounded body.
Existing synthetic tests exercise those rules. No new retry or fan-out loop.
Existing writes require actor-scoped idempotency keys and version checks.

## Throughput checkpoint

- Blocking first steps. Inspect main, producer, open PR paths, and migration slots.
- Independent workstreams. Tests and implementation stay with this worker.
- Shared mutable state. Tour lock and expected cart version serialize acceptance.
- Smallest safe decomposition. One owner keeps the read contract and SQL together.

## Test obligations and blast radius

Postgres tests exercise new-to-tour and cart-only labels, rename, future and
unreviewed assertions, inactive, absent, other-tenant properties, denied deals,
stale versions, partial acceptance, and receipt replay. A synthetic read test
bounds database round trips independently of property count. Read hydration
must never change proposal membership, cart versions, or stored decisions.
No AI output changes. No eval suite applies.

## Design and debt pass

Codebase-design keeps hydration behind the existing proposal read interface.
Zero-tech-debt uses one distinct property collection rather than introducing a
second property identity or repeated per-item enrichment paths.
