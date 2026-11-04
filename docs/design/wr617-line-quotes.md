# Deal line quotes

This first delivery unit stores and reads exact-version line quotes. Ask attachment,
sending, text extraction integration, and the selection UI are separate follow-ups.
AC-QUOTE is not complete until those follow-ups run together.

## Existing flow

Attachments own the document UUID, deal UUID and byte hash. They do not contain a
text layer. The app currently lists documents and fact citations, but has no lease
text selection viewer. The queued-asks outbox freezes a conversation and body;
`doc-request.v1` deliberately accepts no deal or quote fields. PR 2942 owns that
contract and the conversation module. This unit must not silently overload it.

## Data shape and interface choice

A text snapshot owns immutable extracted text, attachment UUID, byte hash, text
hash, and a monotonically numbered version. A quote owns a snapshot UUID, zero-based
Unicode code-point range with an exclusive end, exact selected text, comment,
partner identity and write key. A detached quote retains its original selection.

Considered storing caller-supplied excerpts against a byte hash alone. Rejected
because the server could not verify the range or distinguish forged excerpts.
Chosen interface registers a text snapshot through an authority-only door and
creates quotes only by slicing that snapshot. The future extractor must verify
source bytes before registering the text. This unit does not claim byte extraction.

## Statechart

```mermaid
stateDiagram-v2
    [*] --> Drafting: Select text and write a comment
    Drafting --> Saved: Save against the displayed version
    Saved --> Saved: Retry the same write key
    Saved --> Detached: Remove from the next ask
    Saved --> VersionChanged: A newer document version arrives
    Detached --> VersionChanged: A newer document version arrives
    VersionChanged --> VersionChanged: Reopen the quote from version N
    Saved --> AttachedToAsk: Freeze the next ask [follow-up]
    VersionChanged --> AttachedToAsk: Explicitly include the old version [follow-up]
    AttachedToAsk --> Sent: Ask accepted with the frozen quote [follow-up]
    AttachedToAsk --> Saved: Ask cancelled before acceptance [follow-up]
```

The storage state is saved or detached. VersionChanged is a read projection,
independent of that state. AttachedToAsk and Sent are not implemented here.
Never change a quote's snapshot or range. Never replay a write without rechecking
current access. Never accept a changed request under an existing key.

## System design

- Architecture. PostgreSQL owns immutable snapshots and quote receipts. The
  existing deal-files tool registry exposes registration, saving, detaching, and
  a bounded list. The existing business.read_deal decision installer and its
  revision-bound SQL guard own deal access. No second access policy.
- Capacity and scaling. Text is capped at 200,000 code points and a quote at 4,000.
  Each deal list returns at most 100 quotes with a continuation cursor. Writes serialize on one attachment or
  one actor/key. At 10x, the existing all-deal authorization installer is the first
  scaling constraint. No new loop or external fan-out is introduced.
- Storage. Indexed attachment/hash and deal/quote lookups. The list joins snapshots
  and current attachments in one query. No per-quote fetch or N+1.
- Load spread. Independent deals and write keys proceed independently. Version
  numbering serializes on the attachment row.
- Cache. None. Reads recheck authorization and attachment state every time.
- Availability. Database calls are sequential in the existing transaction. A
  failure rolls back the write. No background retry or partial acceptance.
- Consistency. Immutable text and excerpts can be historical. Current access must
  pass. A changed attachment hash or newer snapshot yields `version_changed` and
  `from version N`; it never changes the old range or text.
- Async. n/a in this unit. The next unit freezes quotes into the queued ask.
- External calls and circuit breakers. n/a. No network provider, extraction call,
  or send is introduced. PostgreSQL failure propagates without a retry loop.
- Rate limits. Registration is bounded by text size. Saves and lists have fixed
  bounds. No polling or fan-out. Existing ingress limits apply.
- Idempotency. Every write requires a UUID key. Snapshot and quote receipts bind
  the full request and actor. Changed arguments refuse; concurrent retries return
  one original result. Detach uses a separate immutable receipt.

## Throughput checkpoint

- Blocking first steps. Resolve the text source and authorization before code.
- Independent workstreams. n/a. This worker owns one coupled store and tool diff.
- Shared mutable state. Serialize snapshot versions on their attachment and writes
  on actor/key. Do not share quote state with the queued-ask outbox.
- Smallest safe decomposition. Store first; queued-ask integration second; app
  viewer and selection third. Only the store is this delivery unit.

## Verification

Disposable PostgreSQL exercises tool-facing storage functions with synthetic
attachments and partners. It checks exact ranges including Unicode, immutable
replay, changed-key arguments, current deal access, version changes, detach replay,
size limits, and concurrent writes. New grants must not expose tables directly.
Hosted strict CI and a reviewer remain required before merge. UI, axe, and the
counterparty-facing ask are explicitly unverified in this first unit.

## Remaining delivery units

1. CARR ask attachment. Extend `doc-request.v1`, the submission definer,
   `mcp-server/src/doc-conversation.js`, its projections, and its PostgreSQL tests.
   Bind an ask to a deal, freeze selected saved quotes atomically, preserve retry
   receipts, and include the exact clause and comment in the ask body. Sending
   must consume only those frozen quotes, with cancellation and concurrent asks
   tested against the statechart. Do not change existing frozen outbox payloads.
2. Text source and app selection. Add a byte-verified PDF text-layer producer that
   calls `register-deal-quote-text`. Add a TypeScript document viewer under
   `doctorcre-app/src/ui/` and connect it to the existing deal document list and
   queued-ask composer. Count code points rather than DOM offsets. Verify phone
   selection, keyboard selection, comment retry, stale versions, and access errors
   at 390, 1024, and 1440 pixels in both themes with axe.

The storage tools are internal CARR verbs. No browser route or document-byte
extractor is shipped in this first unit. No app source changed.

## Unit 2 System design

- Architecture. The submission definer owns the atomic accepted turn and receipt.
  Optional deal_id and ordered quote_ids select at most 20 saved quotes. The turn
  owns frozen quote IDs, clause text and comments. Read projection exposes those facts.
- Capacity. Current and 10x requests use one bounded indexed quote query, never
  N+1. The existing all-deal access installer remains the first scaling constraint.
- Load spread. Independent asks own independent snapshots. Only actor/key replay
  and conversation sequence allocation serialize; no shared per-deal pending set.
- Cache. n/a: no cache. Each submit, replay and read installs current deal access.
- Availability. One transaction freezes and accepts. Any failure or cancellation
  before commit rolls everything back. There is no external send in this unit.
- Scaling. Indexed deal/id lookups and a 20-quote cap bound per-request storage.
- Storage. Immutable turn JSON carries ordered snapshots. Receipt request JSON
  binds selection and deal; existing turn and receipt immutability applies.
- Consistency. Historical clauses remain exact. Current deal and conversation
  access must pass even for replay and reads; revoked access refuses.
- Async. n/a: the client owns the queued draft. Submission and acceptance are one
  atomic server transition. Consumers read only accepted turn snapshots.
- Circuit breakers. n/a: no external API calls. PostgreSQL errors propagate and
  rollback; there is no retry loop or fallback to live quotes.
- Rate limits. At most 20 unique quote IDs, 4,000 characters each for text and
  comment and a 20,000-character rendered body; existing ingress limits apply. No fan-out or polling is introduced.
- Idempotency. Existing UUID key binds body, conversation, deal and ordered IDs.
  Same request replays the original receipt and frozen quotes. Changed input refuses.

### Unit 2 lifecycle and test mapping

```mermaid
stateDiagram-v2
  [*] --> Draft
  Draft --> Cancelled: discard without submission
  Draft --> Submitted: select saved quote IDs
  Submitted --> Cancelled: rollback before commit
  Submitted --> Accepted: atomic freeze and commit
  Accepted --> Replay: identical key and input, current access
  Replay --> Accepted: return original frozen receipt
  Accepted --> Accepted: later quote changes never change the ask
  Accepted --> Refused: changed key input or revoked access
```

Draft cancellation is client-owned and performs no server write. A submitted
transaction may roll back. Once committed the ask is accepted and immutable.
Tests exercise freeze/read, later edits and detach, changed input, replay,
revocation, bounded invalid selections, concurrent asks, and rollback/retry.
No counterparty send or production claim belongs to this unit.

Unit 2 tests map the design to observable behavior.

| Rule or transition | PostgreSQL test |
| --- | --- |
| Draft discard and submitted rollback | cancelled submission rolls back frozen quotes and retry accepts once |
| Freeze, commit, ordered read, one-query selection | quote ask freezes ordered exact clauses and comments in acceptance and read |
| Immutable snapshots and replay | quote ask immutable replay never consumes later edits saves or detaches |
| Current visibility, no stale cache | quote ask reads submissions and replays refuse revoked deal access |
| Independent asks, no shared deal state | concurrent asks on one deal own independent frozen selections |
| Bounded selection, deal binding | quote selections refuse foreign detached missing duplicate and oversized IDs |
| Rendered storage bound | combined ask and frozen quote body refuses overflow without partial writes |
| SQL ingress checks, no fallback without decisions | direct SQL quote entry validates selection shape and requires current deal decision |
| Atomic failure recovery | receipt insert failure rolls back ask and receipt together |

The no-queue, no-cache and no-external-call answers introduce no separate runtime
transition. The acceptance, replay, current-access and rollback tests cover their
observable consequences. The capacity statements describe query bounds, not a
measured throughput claim. External delivery and post-acceptance cancellation
remain outside this store's lifecycle.
