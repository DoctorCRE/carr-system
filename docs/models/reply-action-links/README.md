# Reply action links

System design before implementation. A reply reference names an immutable email_proposal UUID, mailbox owner and proposal version. A lead reference retains its existing bytes and v1 response. Reply responses use action-link-resolve.v2. The existing submit-email-draft command owns human authorization, locked version checks, recipient binding, idempotency and approval. Resolution never calls it. No generic command executor is introduced.

Alternative A routes through read-home-review and scans Home twice. Alternative B selects one proposal and reuses registered list-email-proposals hydration. B preserves reply approval authority with bounded indexed reads and avoids coupling links to Home projection state. No new database schema is needed.

Expiry is lifecycle consumption, not a clock deadline. Stable addresses are not bearer credentials. A reply version expires on edit, dismissal, approval or source retirement. A fresh reference is required after editing. Repeated resolution is read-only and does not consume the reference. The explicit returned submit-email-draft operation approves for the existing sender queue; it does not deliver mail. Same-key retries use that command's existing envelope; a new key against an approved proposal refuses. A link cannot resurrect failed replies or authorize recovery.

Each resolution hydrates at most one proposal. No cache, background work, retries, fan-out, new queue or external API call. At current and 10x load existing authenticated MCP and PostgreSQL capacity are the first limits; no throughput improvement is claimed. Rate limits for new loops and external APIs are not applicable because resolution adds neither. A client attempt performs one bounded resolve with no automatic retry. Database errors fail closed through MCP, without fallback private content. Owner and version are rechecked after hydration. Writes lock and recheck the proposal through the domain command, so concurrent approval has at most one business effect. Recipient changes between review and approval refuse through the existing sender guard.

Blocking first steps are source study, statechart and failing tests. Independent workstreams are not applicable because producer, contract and tests are coupled. Shared mutable state stays in the existing locked proposal and idempotency envelope. One worker owns this bounded slice; no delegation.

The chart and bounded TLC model cover request session state and card lifecycle. TLC checks the signed-out private-card invariant. This is not a database concurrency proof. Executable PostgreSQL tests exercise registered resolution and domain writes. Authentication and proposal lifecycle change independently; tests cross signed-out and foreign sessions with pending, stale and consumed states.

Repository impact. carr-system owns this producer contract. doctorcre-app consumes the reply response in its next slice and needs no edit in this producer PR. software-factory owns no product runtime or contract and needs no change.

Regenerate the model check in a scratch directory by copying statechart.mmd as reply-links.mmd together with ReplyLinks.tla, ReplyLinks.cfg and ReplyLinks.map.mjs, then running the statechart skill from-spec ReplyLinks.tla command with the installed TLA2TOOLS and JAVA paths. PostgreSQL tests remain the authority for locks, recipient binding and idempotency.
