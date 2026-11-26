# Client commitment register

Status: partial, source acceptance contract unresolved. Card WR648.S5.J1.
Base inspected: `d2c47c2aa2cc861d3fff5ea1b896a9142e52a1dd`.
No runtime read, route, Doc exposure, or proof runner is implemented by this change.

## What the partner needs

Dr. CRE must answer what the team explicitly accepted as a promise to an exact
client. Each answer needs its acceptance reference, existing action ID, owner,
due date, current version, and evidence for any fulfilled status. A request or
suggestion must remain separate. Reading cannot change the action lifecycle.

## Source evidence

The following observations concern the checked-in contracts, not production data.

| Source | Stored evidence | Limit for this read |
| --- | --- | --- |
| `mcp-server/src/future-work.js`, `records` and `linkFutureWork` | Links successful future-work writes to Work Requests with their source verb and key. | The module explicitly captures writes without a prose classifier. A link does not establish a client promise or acceptance. |
| `mcp-server/src/deal-items.ts`, `validateDealItem` and `record` | Stores an action with owner, due date, source reference, and typed bullets. The creation event includes the item ID and bullets. | The `promised` bullet discriminator has no explicit acceptance reference or provenance field. `splitDealNextStep` also generates it from generic prose. |
| `mcp-server/src/deal-items.ts`, `backfill-deal-items` | Records a distinct backfill creation verb, so those events can be excluded. | Excluding backfill avoids one inference path. It does not establish that a direct `record-deal-item` caller separately accepted a promise. |
| `db/schema.sql`, `capture_post_call_action` | Stores `accepted_by`, `accepted_at`, owner, due date, and source candidate. | Acceptance is of a Call Mode action. There is no promise versus request discriminator on that accepted action. |
| `mcp-server/src/action-closure.ts`, `complete-one-action` | Returns a completion event ID and action version. Reopening references that exact completion event. | This can support terminal evidence once promise eligibility is established. Completion cannot establish acceptance of a promise. |
| `mcp-server/src/requirement-requests.js` | Stores a document request with owner, due date, and checklist source. | Receipt is explicitly distinct from satisfaction. It does not accept a promise to the client. |
| `mcp-server/src/governed-correspondence-journey.v5.js`, c7 | Separates `promised_commitment`, `requested_task`, and `suggestion`, with source and completion evidence in the proposal contract. | `classifyCorrespondenceCommitments` returns proposals. Its `record_commitment` action is a proposed action, not a persisted acceptance receipt. |

A local probe executes the real parser. `splitDealNextStep('Send the market
package.')` produces a `promised` bullet without an acceptance input.
`validateDealItem` accepts a caller-supplied `promised` bullet whose text is
`Requested on the call.` The current contract does not validate that distinction.
The existing deal-items PostgreSQL test uses that same combination.
These observations prove that the label alone is insufficient evidence of the
separate acceptance predicate required by this brief. They do not prove that
natural accepted promises do not exist.

## Missing contract

The unresolved fact is whether a direct partner-authorized `record-deal-item`
creation event with a caller-supplied `promised` bullet is itself the canonical
acceptance receipt for a client promise. The current writer describes adding a
sourced checklist item, and has no separate acceptance operation or reference.
This worker does not equate action creation with promise acceptance.

A narrow candidate design would admit only those direct creation events and
exclude backfill, generic actions, and requests. It is technically feasible using
existing fields. It remains conditional on that acceptance interpretation being
part of the owning contract. Do not describe this candidate as impossible or
claim that a migration is necessarily required.

Without that interpretation, the owning lifecycle needs a persisted association
between the exact action ID, explicit promise kind, client or deal ID, and the
partner's acceptance evidence. Owner, due date, action version, and terminal
receipts can remain in their existing stores. Categories require explicit source
values if the reader is to distinguish market packages, lender introductions,
attorney introductions, and engagement agreements without classifying prose.
Missing categories must be reported as gaps. No new lifecycle is proposed here.

## System design

Compare two designs before implementation.

1. A query over all accepted actions and promised bullets. Reject it because
   generic action acceptance and backfilled prose labels would manufacture
   promise eligibility.
2. A query over exact action IDs joined to authoritative promise acceptance
   evidence, restricted to exact client and deal IDs. Prefer it once the
   acceptance receipt contract is established. Reuse action closure for status.

The intended interface is registered read-only `read-client-commitments` with a
required exact client ref, limit 1 through 50, and a subject-bound opaque cursor.
A fixed authenticated GET route and the Doc allowlist call the same reader and
response parser. They cannot accept actor or tenant query arguments.

Data ownership stays in the action, acceptance, and event records. One bounded
Postgres read should return current rows and versions without N+1 queries.
At current load and ten times that load, the first likely limit is the
acceptance join and sort, which must be measured against the chosen source
before adding an index. No load measurements have been taken for an unbuilt
query. Return at most 50 rows and one lookahead. No fan-out or API loop is needed.

There is no cache, invalidation, eviction, async producer, consumer, or queue.
Read each field from one database snapshot. Refuse source failure rather than
serve stale acceptance, owner, status, or evidence. A missing receipt gives
unknown fulfilment, never fulfilled. Unknown client, known empty source, and
unavailable source remain distinct outcomes. The implementation must disclose
partial source coverage even when it returns eligible rows.

There are no new external calls in the reader. External circuit-breaker
thresholds, fallbacks, and alerts are n/a for that reason. Use the existing
request budget for the authenticated adapter. No writes or sends occur, so
write idempotency is n/a. Horizontal runtime scaling retains database ownership.
A database failure must return unavailable without exposing its error text.

## Planned statechart and verification

`docs/statecharts/client-commitment-register.mmd` describes intended behavior,
not implemented behavior. No transition is claimed passing. These are the
required test names for a future implementation.

| Transition | Required behavioral test |
| --- | --- |
| Start to validation | `request enters validation` |
| Validation to needs input | `missing exact client and malformed limit or cursor refuse before reading` |
| Validation to unauthorized | `unknown actor refuses before reading` |
| Validation to reading | `Joe and Dell use the same authorized reader` |
| Reading to needs input | `unknown client remains distinct from known empty client` |
| Reading to unavailable | `database failure and missing promise acceptance contract remain unavailable` |
| Reading to empty | `known client with an available source and no eligible promises is empty` |
| Reading to ready | `only explicit accepted promises appear with exact source and action IDs` |
| Ready to validation | `next cursor stays bound to the exact client and bounded page` |

Before building, add a failing registered-read acceptance test. Then exercise
all transitions, exact links, source versions, category gaps, terminal receipts,
reopen behavior, no mutations, malformed route queries, and authenticated Doc
discovery. Use the repository disposable PostgreSQL mechanism and the production
response parser. Run the proof CLI with both passing and failing fixtures and
repeat focused tests in a fresh depth-1 checkout of the pushed branch.

## Production proof remains unimplemented

A separate post-deploy worker must read the deployed release and naturally
eligible records through the shared release reader and private session transport.
It must compare a returned promise's exact action, owner, acceptance source, and
status with the owning records. A fulfilled promise also requires the exact
terminal receipt or file reference. No natural records, missing merge identity,
source failure, or failed authentication means insufficient evidence with a
nonzero exit. No synthetic production rows or release-only pass is acceptable.
This partial documentation change supplies no executable proof declaration.

PR-time production-proof admission is retired on this base, as documented in
`proofs/requirements/README.md`. Do not restore it.

## Scope and follow-up

This partial result changes documentation only. It preserves generic lifecycle
behavior and shared sibling registration files. It is not review-ready as a
completed implementation of WR648.S5 and must not be marked live-proven.
The coordinator must resolve the acceptance contract or schedule its smallest
owning-contract extension before this read can satisfy the brief.

DoctorCRE/carr-system owns this runtime read, authorization, Doc contract, and proof.
jbookout/doctorcre-app needs no source change for the existing conversation client.
jbookout/software-factory needs no source change for this product runtime behavior.
