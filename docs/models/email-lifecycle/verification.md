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

TLC checks TerminalNotes and Versioned. It generated 33 states, with 12 distinct
reachable states and no invariant violation. Run from a scratch directory:

```
java -cp /tmp/WR442-tla2tools.jar tlc2.TLC -workers 1 -config /tmp/wt-wr442-s4-j5/docs/models/email-lifecycle/Mail.cfg /tmp/wt-wr442-s4-j5/docs/models/email-lifecycle/Mail.tla
```

The rendering wrapper failed with `Cannot read properties of undefined (reading
'tag')`; direct TLC completed successfully. The chart in README is authored from
the checked transitions. Replay and rejected stale/foreign actions are omitted
from chart arrows that do not change the displayed state. The model treats
actions as atomic and leaves authentication and SQL lock implementation to the
PostgreSQL tests. It does not model Outlook detection or real mailbox contents.

Design and debt pass:

- codebase-design keeps one lifecycle mutation behind the established identity,
  transaction envelope and event seams; no second identity store is introduced.
- zero-tech-debt retains canonical earlier actions with explicit notes, removes
  terminal sources at existing reader predicates, and adds no restoration or
  compatibility mode without a caller.

PR two owns lib/outlook_intake.py, ops/outlook-intake-selftest.py with synthetic
tombstone/revision fixtures in that self-test. It must serialize
behind any open PR holding those paths, including PR 2941.
