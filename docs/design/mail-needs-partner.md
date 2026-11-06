# Mail needing the partner

System design written before implementation. Sizing is L01.

The lightest read is a single bounded Postgres query over email_proposal and activity. CARR owns authorization, ordering and the response parser. The registered verb and fixed authenticated GET adapter share that implementation. Existing proposal commands own review and mutations. Alternative considered: hydrate list-email-proposals and sort in memory. Rejected because its contact and preview fan-out is unnecessary and its pagination precedes priority ordering.

Capacity and scaling: return at most 50 rows plus one lookahead. At current and tenfold load the first constraint is the scoped join and sort on existing tables. No new indexes or migration in this slice. Each request uses one query, no per-item reads. Load stays with the existing reader pool. Request budgets protect the GET adapter.

Caching: none, so invalidation and eviction are n/a. Consistency: current database statement snapshot; pagination is a live view, not a frozen snapshot. Cursor fixes the ranking date across pages but concurrent edits can move an item. Source and proposal versions let review detect stale decisions.

Availability: validate and authorize before querying. Database failure returns unavailable, never empty. No external model, API, cache or queue is involved. Circuit breaker: one database attempt, no retry or fallback, existing request telemetry records the failure. Async producers and consumers are n/a. Fan-out and loops are capped at 51 database rows. Writes, sends and idempotency keys are n/a.

Only stored next_date is a decision date. Missing dates are unknown. PostgreSQL date columns cannot contain malformed calendar dates. Source instants missing or invalid sort last and remain unknown. Do not read or execute email instructions. Return no bodies, addresses or credentials.

Action references name the existing proposal confirmation command and exact proposal ID and version. They require the existing preview before confirmation; this read never authorizes a mutation. Coverage includes recorded inbound proposals only, not unrecorded inbox mail. Queued and sent are never equated.

## Production proof transport

The natural proof reuses the existing local-actor call wrapper used by prove-prospect-research-future-evidence.py and prove-mail-policy-multiple-recorded-addresses.py. run.sh call reaches the deployed Worker /mcp door; the server derives mailbox authority from its existing local token. No actor argument, new credential store, cookie transfer or origin grant is introduced. The independent app's shared scripts/app-release-reader.mjs supplies read-only HTTP and release headers, not partner authentication. lib/carr_session_scope.py classifies transcript ownership and does not authenticate production reads.

The primary wrapper defaults to this checkout's run.sh. The proof worker supplies an existing second-partner authenticated wrapper using --other-call-wrapper. Both wrappers must implement the existing call verb JSON convention. Same-mailbox credentials fail closed. Release HTTP uses lib/release_reader.py. The fixed browser GET remains an adapter tested against the same registered read contract; the proof observes the deployed registered verb through the permitted existing actor transport.

Read the top natural pending item, read the second partner mailbox, then contemporaneously page list-email-proposals using the primary wrapper. Canonical reads include non-deal items and are capped at twenty pages of fifty proposals. Missing witnesses, incomplete second-mailbox coverage, stale versions, resolved proposals, source failures, absent second-partner transport or absent merge identity return insufficient_evidence. There are at most twenty-two wrapper reads, a sixty-second wrapper deadline and a ten-second per-call timeout. Every acquired wrapper process group is killed on success, failure or timeout. The parser subprocess is bounded to fifteen seconds and has no child acquisition.

The allowlist pins the complete proof source and audits the fixed Node parser, selected existing read wrappers and owned-process-group cleanup. Its checker is unchanged. Production remains unverified in this builder job.

The response supplies an existing confirmation-command reference requiring review. It does not invent a browser deep link or claim the command arguments alone are sufficient to confirm.
