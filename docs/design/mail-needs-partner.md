# Mail needing the partner

System design written before implementation. Sizing is L01.

The lightest read is a single bounded Postgres query over email_proposal and activity. CARR owns authorization, ordering and the response parser. The registered verb and fixed authenticated GET adapter share that implementation. Existing proposal commands own review and mutations. Alternative considered: hydrate list-email-proposals and sort in memory. Rejected because its contact and preview fan-out is unnecessary and its pagination precedes priority ordering.

Capacity and scaling: return at most 50 rows plus one lookahead. At current and tenfold load the first constraint is the scoped join and sort on existing tables. No new indexes or migration in this slice. Each request uses one query, no per-item reads. Load stays with the existing reader pool. Request budgets protect the GET adapter.

Caching: none, so invalidation and eviction are n/a. Consistency: current database statement snapshot; pagination is a live view, not a frozen snapshot. Cursor fixes the ranking date across pages but concurrent edits can move an item. Source and proposal versions let review detect stale decisions.

Availability: validate and authorize before querying. Database failure returns unavailable, never empty. No external model, API, cache or queue is involved. Circuit breaker: one database attempt, no retry or fallback, existing request telemetry records the failure. Async producers and consumers are n/a. Fan-out and loops are capped at 51 database rows. Writes, sends and idempotency keys are n/a.

Only stored next_date is a decision date. Missing dates are unknown. PostgreSQL date columns cannot contain malformed calendar dates. Source instants missing or invalid sort last and remain unknown. Do not read or execute email instructions. Return no bodies, addresses or credentials.

Action references name the existing proposal confirmation command and exact proposal ID and version. They require the existing preview before confirmation; this read never authorizes a mutation. Coverage includes recorded inbound proposals only, not unrecorded inbox mail. Queued and sent are never equated.

## Remaining proof constraints

This draft does not claim production proof readiness. The fixed adapter uses the existing app-origin session. api.doctorcre.com is not an accepted browser origin, and sessionFor enforces origin binding. A separate authenticated contract decision is needed before the specified API-host natural proof can work. No new origin or write grant is added here.

The Python proof invokes the production TypeScript response parser. ops/proof-transport-check.py requires this exact subprocess and source hash in ops/config/proof-process-allowlist.json. That file is outside this job's writable scope. Lint correctly refuses the undeclared subprocess. Do not bypass it or claim green CI. Production mode returns insufficient_evidence until a contemporaneous canonical authenticated read transport is implemented. The CLI never accepts a hand-written canonical production receipt.

The response supplies an existing confirmation-command reference requiring review. It does not invent a browser deep link or claim the command arguments alone are sufficient to confirm.
