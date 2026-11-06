# Activity deal attribution

Joe supplies one exact activity UUID to `read-activity-deal-attribution`. Dr. CRE can explain a direct deal or show stored source-linked candidates for review. No association is inferred from a contact, name, domain, or client membership.

## System design

The activity module owns validation, the bounded Postgres snapshot, and the response parser. The registry and fixed authenticated GET adapter consume that interface. Activity, deal, client, and party remain canonical data owners. The data shape is a discriminated response with ready, needs_input, empty, and unavailable states. Direct attribution alone receives credit. Source-linked candidates never receive inferred credit.

Two designs were considered. Several independent reads simplify SQL but permit mixed versions and N+1 expansion. One snapshot with bounded source links preserves consistency and hides association rules behind one interface. The latter is chosen.

Each request reads one activity and at most 101 explicitly linked activities, returning at most 100 candidates. Overflow refuses the result. Current request volume is unmeasured. At ten times volume, Postgres source-pointer scans and connection capacity fail first. Requests spread through the existing Worker and database connection infrastructure. There is no new cache, invalidation, eviction, async producer, consumer, queue, or background work.

Authorization and validation precede one database read. Both partners share canonical business records; mailbox-bound activities remain scoped to their recorded mailbox owner. Every source subject and candidate deal must still be live. Source failure refuses fresh data, with no stale fallback. All versions come from one statement snapshot. The source pointer is traversed one hop in either direction, including siblings with the same exact source UUID. Loops and fan-out cap at 101 rows. No external model or HTTP call occurs in the runtime read. Circuit breakers and external rate caps are n/a because there is no new external dependency. Existing request budgeting protects the HTTP adapter. Writes and write idempotency are n/a.

Throughput checkpoint. Schema and source checks block implementation. Contract, route and proof remain one worker-owned vertical slice. Shared registry edits are additive in this isolated branch. One worker is the smallest safe decomposition because both entry points must enforce the identical response contract.

## Evidence coverage

A complete response covers only direct and one-hop source-activity associations. It makes no claim about all relationships. Missing source pointers produce unknown coverage. Unknown, inaccessible, or retired activities share an empty response. Database failure and overflow produce unavailable coverage. Candidates preserve activity and deal versions. Every activity ID is returned once per candidate deal, even when several source paths reach it.
