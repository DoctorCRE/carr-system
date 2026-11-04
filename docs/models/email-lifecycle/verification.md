# Verification contract

The invariant is that no new action can commit from terminal mail. Registered
mutations lock the source before their handler runs. Lifecycle changes lock the
same source rows. Database triggers also refuse proposal and summary writes. The lifecycle mutation
is exercised under carr_writer and reads only a narrow audit view, not raw
event history or current account addresses.
Terminal observations annotate all captures of the mailbox identity atomically.

The PostgreSQL suite executes each live-to-terminal transition, same-key replay,
new-key observation replay, stale versions, changed terminal observations,
recapture refusal, prior-action notes, owner/account refusal, caller tenant
selection refusal, unchanged other-mailbox captures and concurrent replay.
It independently exercises terminal link/summary queues and follow-up previews.
Home v1/v2/v3 and proposal readers lose cards that were present while live.

TLC checks TerminalNotes, Versioned and NoResurrection. The round-2 model has
separate inbound-source and outbound-destination states. ConfirmSent can arrive
before or after Observe. A terminal destination refuses the delayed confirmation
under the identity lock. A completed receipt can replay without touching the
outbound row. A terminal source still refuses receipts, including replay.
A scratch model of the old receipt overwrite violates NoResurrection after
Observe then ConfirmSent. The fixed model preserves that rule.
TLC generated 355 states with 96 distinct reachable states and no violation.

Run from the repository root, with the TLA+ tools jar installed at the given path:

```
java -cp /tmp/j58-tla2tools.jar tlc2.TLC -workers 1 -metadir /tmp/j8-tlc-state -config docs/models/email-lifecycle/Mail.cfg docs/models/email-lifecycle/Mail.tla
node --test mcp-server/test/email-lifecycle-postgres.test.mjs mcp-server/test/email-deal-progress-postgres.test.mjs mcp-server/test/tool-error-classification.test.mjs
```

Every transition has an executable named test:

- Capture and Act use `email identity survives new retry keys and filing to a deal`
  and `reply drafts keep captured mailbox ownership and submit the exact partner text once`
  in email-deal-progress-postgres.test.mjs.
- Observe, Replay and RefuseStaleOrForeign use `deleted`, `recalled`, and
  `superseded` in email-lifecycle-postgres.test.mjs. These assert version,
  changed-terminal, owner and account refusals and exact observation replay.
- ConfirmSent, ReplaySent, Capture refusal and both destination orderings use
  `<state>: sent confirmation before observation preserves terminal capture` and
  `<state>: delayed sent confirmation after observation preserves terminal capture`
  for each of the three terminal states in that suite. Two fresh receipt keys
  and capture keys verify unchanged full activity rows, lifecycle versions and
  action notes. The proposal remains approved when confirmation refuses.
- ObserveSource uses the final source observation in those same six tests. The
  independent source becomes terminal and receipt calls refuse, including
  previously completed receipt replay. Existing source guards stay in force.

Round-3 guard coverage supplements the atomic model with executable refusals.
`<state>: same observation rejects changed <field>` covers independent changes to
state, method, observed_at and base_version under the original observation ID.
Each call uses a fresh envelope key and carr_writer, expects
email_observation_conflict, and compares full activity, proposal and audit rows.
`<state>: identical observation replay preserves rows and events` checks the
original receipt and unchanged stored rows with another fresh key.
These tests refine Replay and RefuseStaleOrForeign; payload comparison and
observation IDs are outside the TLC state space. No model transition changed.
The terminal-state tests still exercise stale-version refusal and terminal
recapture refusal; the six sent-confirmation tests cover ConfirmSent and
ReplaySent in both observation orderings.

The model treats transactions as atomic. It leaves authentication, identity
selection, PostgreSQL advisory and row locks to the integration tests. It does
not model Outlook detection, real mail contents, lock timing, deadlocks, or
performance. RefuseStaleOrForeign is an abstract stutter; only the integration
tests validate its guards. The chart shows observable states and omits stutters.

Round-2 system design. The existing activity row owns lifecycle metadata. The
registered receipt interface now refuses a terminal destination before replacing
context, inside the existing identity lock and transaction. Rejecting the write
was chosen over merging context because the terminal contract forbids a new
source action. There is no new store, cache, process, queue, external call,
fan-out, rate limit or circuit breaker. Capacity and 10x behavior remain
unmeasured. The existing indexed identity lookup and serialization do not change.
A refused receipt rolls back and retains its approved proposal for reconciliation.
Fresh idempotency keys do not bypass terminal authority. Earlier successful
receipts replay without rewriting history. No source detection is delivered.

Design and debt pass:

- codebase-design keeps one lifecycle mutation behind the established identity,
  transaction envelope and event seams; no second identity store is introduced.
- zero-tech-debt retains canonical earlier actions with explicit notes, removes
  terminal sources at existing reader predicates, and adds no restoration or
  compatibility mode without a caller.

PR two owns lib/outlook_intake.py, ops/outlook-intake-selftest.py with synthetic
tombstone/revision fixtures in that self-test. It must serialize
behind any open PR holding those paths, including PR 2941.
