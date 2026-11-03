# Delivery failure attribution

The rejected premise is that completing a paginated traversal proves that all source filings agreed at one instant. The correction-between-pages test disproves it before the implementation change.

```mermaid
stateDiagram-v2
  state Bounce {
    [*] --> Received
    Received --> Loading: Read first filing page
    Loading --> Loading: Read another page
    Loading --> TraversalChanged: A previously read source is corrected or unfiled
    TraversalChanged --> Retry: Write rejects stale source evidence
    Loading --> Retry: Source is missing or deals disagree
    Loading --> Attributed: Transaction locks and validates every source version and deal
    Retry --> Loading: Another admitted attempt within budget
    Retry --> TerminalUnresolved: 32 attempts or 30 days elapsed
    Attributed --> Attributed: Retry before receipt expiry
    Attributed --> ReceiptExpired: Receipt reaches 30 days or is pruned
    ReceiptExpired --> Attributed: Retry returns the original finding
    Attributed --> Attributed: Concurrent sweeps return the original finding
    TerminalUnresolved --> TerminalUnresolved: Source arrives after exhaustion
  }
  --
  state FilingFeed {
    [*] --> PageReady
    PageReady --> PageReady: Read next page with activity versions
    PageReady --> Corrected: Correct or undo a filing
    Corrected --> PageReady: Later page sees current rows
    PageReady --> Locked: Finding locks all candidate activity rows in UUID order
    Locked --> PageReady: Finding commits or validation rolls back
  }
```

Every attribution requires all known source activities, one deal, and the exact activity versions read from the feed. Validation runs inside record-finding's transaction. A correction that wins the row lock rejects the finding; a correction after the finding commits is a later historical change. A missing version fails closed, including state saved by older clients. A proven stale refusal discards the unsent frozen request and spends the existing bounded correlation budget. An unknown transport outcome keeps its payload and key for replay.

The two design candidates were an immutable server snapshot spanning runs and write-time validation of versioned candidate rows. The latter keeps the existing feed and write interfaces and does not need snapshot retention or expiry.

Transition coverage derives from the chart. The correction-between-pages regression covers Loading to TraversalChanged to Retry. Random correction interleavings cover stale, missing, conflicting and agreeing sources and retry to attribution. Existing pagination tests cover loading, missing evidence and final-page attribution. Existing bounded-retry and retention tests cover terminal outcomes and late source arrival. Lost-response tests cover frozen replay. Server transaction tests cover row locking, stale validation, refusal without a receipt and valid replay. No chart transition is intentionally untested. The chart omits parser grammar, ordinary link filing and mailbox decoding, which retain their existing tests.

A bounce report is identified by its existing provenance source (hashed mailbox account and report Message-ID, or fallback mail identity), scoped to the authenticated actor. Deal, source activity versions, transport UUID and receipt age are not part of that identity. The committed finding is the durable result. Retries return it before revalidating current filings. New reports still require the unchanged attribution checks.

Expiry transition tests are `frozen bounce retry converges before, at and long after receipt expiry`, `expired bounce replay survives later filing corrections`, and `concurrent bounce sweeps converge after receipt pruning`. They use real PostgreSQL transactions and the real handler. The intake test `test_delivery_failure_replays_frozen_request_long_after_receipt_expiry` covers the producer retaining and resending the identical frozen payload after 31 days. The existing source-lock test covers rollback and commit. This chart models receipt lifetime and finding identity; it does not model the parser grammar or transport internals.
