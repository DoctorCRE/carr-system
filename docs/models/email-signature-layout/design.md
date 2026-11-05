Card: WR442.S2.J33
Size: one session. Scope is deterministic signature repair; real lifecycle decoding is blocked on an evidenced wire format.
Architecture: _load retains per-row HTML as private signature input. Extraction owns HTML layout restoration, current/history split, bounded contact-run recognition and sender binding. Existing review planner/journal owns proposed findings, versions and retries. No new write route.
Alternatives: changing decoder body text would change router inputs, content proofs and historical bodies. A separate signature-only HTML view restores required layout without those effects. Reuse the decoder's existing per-message html column.
Data shape: ordered current-message lines, one bounded contact run, fields with exact line evidence. Only explicitly labeled identity fields are proposed. Unlabeled title/company/name are held rather than guessed.
Capacity: linear in bounded 100k text / 1MB HTML per message; sequential local parsing at current and 10x load. No cache, fanout, external call, queue or new storage. Existing write journal, limits, refusals and idempotency remain authoritative. HTML errors refuse extraction. No real write is executed by verification.
Throughput checkpoint: decoder+research comparison, signature regressions, existing intake/review suites, strict gates/types/secret and hosted CI. No child agents, per task.
Lifecycle: J12 was minted and undispatched, not built, per current queue. Existing server contract shipped. This verified decoder exports no lifecycle fields; Deleted-folder membership and disappearance cannot supply them. Do not invent a decoder schema or resend existing contract.

J34 continuity: incorporated WR636.S1.J19 helper commit edcd3cf7938212d927f9f217727ba27fabeffc5f. The cutover patch conflicted only at the import context added by this signature fix. Verified the old agenda block against its exact base before removing it, imported the helper and bumped CALENDAR_POLICY to 4. One cancellation in any same-GOID copy suppresses upcoming display and recent outcome reviews. The eight helper tests remain single-owned. A new caller regression exercises decoded ordinary-subject cancellation, publication, old-policy invalidation and the restored unchanged-store skip. Null-ref canonical contact lookup remains a separate tools.js issue behind PR2961.
Calendar design: the pure projection owns GOID grouping and agenda/review selection; intake owns decoding/publication. No new external calls, cache, queue, database access or concurrency. O(events plus output sorting) at current and 10x load. Existing 50-attendee, 100-review and 200k-byte caps apply. Revision order remains unsupported, so cancellation conservatively suppresses every copy of the same occurrence.

J36 repair design (synthetic-only): data is a versioned evidence tuple of raw body,
raw HTML and completeness. Canonical alias proof and the pending journal binding
share this definition; raw tags matter even when flattened text is unchanged.
Source identity and acknowledged requests stay frozen. Changed input/policy holds
pending work; exact restoration permits the original request, never re-planning it.
Legacy unversioned pending bindings fail closed; existing acknowledgements remain.
The v3 signature policy invalidates the store fast path for one reevaluation.
Explicit assistant/delegate ownership in current-message prose or headings holds
before either extraction path assigns any field, including empty contact fields.
Quoted history is excluded before that check. No guessing or assistant party lookup.
Capacity/storage: linear bounded input hashing adds no query, N+1, queue, fanout,
external call or cache beyond the existing persisted journal. At 10x load parsing
and hashing remain sequential; journal retention is unchanged. Availability,
circuit breakers, limits, idempotency and writes reuse the existing journal.
Throughput checkpoint: red reviewer reproductions, focused green extraction/journal,
371 intake cases, six disposable Postgres cases, local strict classes, one hosted
read after push. No AI output changed; model eval comparison is inapplicable.
Design alternatives: bind rendered text alone (loses provenance/layout), duplicate
raw fields in each consumer (drifts), or share the versioned evidence tuple (chosen).
This chart is a transition sketch, not a model-checked proof. Executed tests cover
changed/restored HTML, changed plain text, policy change, alias proof, stable retry,
acknowledgements, assistants/delegates, quotes and multiple phone ownership.
