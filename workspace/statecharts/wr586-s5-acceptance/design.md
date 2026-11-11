# Partner acceptance of client Tour List choices

The partner reviews one publication revision, client source version and immutable cart base. The read/accept interface owns canonical comparison and SQL authorization. The client save remains isolated in the existing proposal history. No route, packet, grant or publication operation is called by acceptance.

## System design

One strict TypeScript module owns the versioned interface, parser and existing envelope/event integration. One SQL function acquires portal, publication and Tour locks in that order, rechecks current client/deal binding and retirement, then delegates the cart append to the existing business operation. A runtime client cannot acquire the private table locks itself. Neither a second source read nor an application-only advisory lock replaces the portal-row lock.

At current and tenfold load each call reads one indexed proposal head, one indexed cart head and at most 100 canonical property IDs. Calls for different portals do not share locks. One busy portal serializes; consumers must not hold transactions open while the partner thinks. There is no cache or N+1 network query. The pending event read uses the existing source audit; event growth is the first read-cost limit. No new event index or push service is justified by this bounded interface. Rank 7 owns listing and rendering through existing bounded publication reads.

Review data can become stale immediately after its read. Acceptance refuses unless both source and cart bases still match inside the transaction. A failed append, event write or receipt insert rolls back together in the existing dispatcher transaction. Unknown outcomes retain the exact request and key. Replay uses the existing 30-day tool_call retention, with live authorization and publication checks before returning a receipt. No external calls, queues, retries, provider circuits or new storage are introduced.

Keep mine validates both bases and records the decision without a cart append. Use theirs sends the exact selected array after explicitly excluding retired identities. Per-property choices send an exact subset of the reviewed union. Notes are private inert client text and never become property facts. Canonical UUIDs are the only identity join.

## Design and debt pass

Codebase-design compared a separate lock acquisition interface with one review/accept SQL interface. The latter keeps lock order, retirement and source validation local. The existing adapter and publication interface remain required by client saves.

Zero-tech-debt moved J15's reproduction into the existing PostgreSQL suite instead of keeping a duplicated suite. No receipt table, notification kind, compatibility path or additional mutation framework is needed. Existing tool_call and event rows own retry and decision history.

## Statechart verification

Acceptance.tla abstracts atomic database calls, one client change, one competing partner write and one intended acceptance. It omits payload sizes, SQL authorization, publication membership and database failure details; the real PostgreSQL tests cover those. Unknown means the caller does not know whether dispatch committed. Retry after commit is a stuttering transition.

The named test `guarded acceptance, uncertain replay, second-tab conflict and notification dedup` maps Review to the initial exact read, Change to source-six, OtherPartner to the same-source second-tab attempt after the winning append, Accept/Commit to the successful append, Unknown/Retry to the discarded response followed by original-key replay, Conflict to source/cart refusal, Keep to the kept receipt without another version, Notify to repeated identical signals and disappearance after decision, and Retire to refusal after publication withdrawal. `partner read cannot fence a client source update` preserves J15's pre-function race and SQLSTATE 42501 lock denials. `publication changes retire identities without rewriting their notes` covers source tombstones. `canonical records and private storage permissions remain unchanged` checks portal isolation. `registered proposal review contract and recorded SQL response stay pinned` validates the producer recording through the production parser and rejects a malformed response.

Run TLC with JAVA=/opt/homebrew/opt/openjdk@21/bin/java and tla2tools.jar against Acceptance.tla and Acceptance.cfg. All four invariants must pass. The Mermaid chart describes the same partner-visible states. No production call is part of this build.

## Production proof

After a containing release, independently compare an existing client proposal and current same-deal cart with the server difference. Read an existing authorized acceptance receipt and compare its source/publication/cart versions and exact selected IDs with the immutable resulting cart. No qualifying receipt means review-only evidence and exit 78 for acceptance persistence. The build does not create production proposals or acceptance receipts.

CARR owns the runtime and migration. doctorcre-app rank 7 consumes the pinned contract and owns presentation; packet/grant PR 836 remains untouched. software-factory owns no runtime or product data here.
