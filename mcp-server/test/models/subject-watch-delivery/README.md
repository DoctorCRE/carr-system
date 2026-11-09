The delivery model checks one watch generation and one semantic change. Watch state, receipt disposition, inbox placement, processing health, access and the last confirmed read change independently. It models each database emission as one atomic step under the generation lock. It checks both possible orders of removal and emission. PostgreSQL tests exercise the actual locks, busy receipts, concurrent sweepers, rollback and current policy checks.

The six never rules have separate invariants in Delivery.cfg. TypeOK is additional. The model does not prove SQL isolation, calendar arithmetic, notification rendering, device delivery, arbitrary queue lengths, fairness or eventual delivery. Inbox placement and read errors are abstractions of existing consumers, not new app behavior. Repair is an explicit operator repair, not an automatic third retry.

Regenerate in a scratch directory with the installed pstack statechart skill. Copy Delivery.tla, Delivery.cfg and Delivery.map.mjs there and run `node statechart.mjs from-spec Delivery.tla --out result --png`, with TLA2TOOLS and JAVA pointing to installed TLC and Java. The resulting summary reports 96 model states. The checked chart and stories are committed beside the model. The builder inspected /tmp/j139-model-delivered/result/chart.png.

Transition coverage:

| Model action | Executable test |
| --- | --- |
| Capture, Emit, Replay | committed multi-event transaction delivers one final receipt and retry stays quiet; crossing without business mutation emits once and later days stay quiet |
| Pause, Resume | pause suppresses pending and resume takes quiet baseline |
| Unwatch | removed generation never emits; busy locks skip and later sweep catches up |
| Failure, Repair | mint rollback preserves pending and repeated failures quarantine; statement timeout preserves pending work and quarantines two failed attempts |
| AccessLost | coalescing, rate limit, busy receipts and delivery policy use current state |
| Settle | watch alerts reach named Home cards and read, settle, snooze stay independent |
| ReadFailure, ReadSuccess | registered watch transitions preserve isolation and initial baseline; registered month policy and current health pass the producer parser |

The SQL sweep is the consumer seam. It uses one network connection and indexed lookups under each acquired generation lock. There are no per-watch external requests or caches. The loop admits at most 50 watches per transaction and the Worker runs at most four transactions within its deadline. At 10x load, indexed source lookups and pending backlog are the expected limits. Synthetic 50/500-watch runs check exact emitted receipt and notification counts; they do not establish production capacity. Failed minting, including PostgreSQL statement cancellation, rolls back only emission and preserves captured clock receipts. A second failed attempt quarantines the generation and exposes needs_repair health. Stale global sweep health appears after fifteen minutes.

The competing design put queue ownership and retries in the Worker and committed notification creation separately. It was rejected because a lost acknowledgement could separate the notification from its covered receipts. PostgreSQL owns capture, coalescing and emission instead; the Worker owns scheduling and the invocation budget.

Capture abstracts the committed transaction/generation receipt from migration 1166. Events only accumulate provenance. Delivery never flushes capture, calls an operation-completion hook or interprets event IDs as receipt identity. The synthetic load test commits the source transaction before sweeping, asserts zero receipts before commit, and checks one emitted receipt per generation afterward. Early constraint flushes remain private to the producer transaction.

Transaction receipts can combine source actors and replies. Current policy checks every provenance event and retains material delivery when any event remains eligible after self-change filtering. Reply-only delivery needs at least one reference whose event passes the current self-change policy and whose reply is live and owned by the receiving mailbox; a retired sibling reference cannot veto live evidence. The mailbox predicate in this finite model abstracts the existence of eligible evidence, not the first reference or unanimity across references. `committed multi-event transaction delivers one final receipt and retry stays quiet` and `committed reply receipt retains live mailbox evidence when another reply retires` exercise these concrete reductions, including the inverse where all replies retire.

## Queue contention

Queue.tla models two due generations and a one-candidate batch, reducing a fully locked fifty-watch prefix and its unlocked successor to the same cursor transitions. Attempt advances the persisted immutable UUID cursor whether the candidate lock is busy or acquired. Wrap resets an exhausted suffix. Unlock lets the earlier candidate deliver on a later pass. FreeWorkProgresses requires every visited unlocked candidate to emit; LockedWorkWaits forbids early emission of the held candidate. These invariants supplement the existing delivery model, which still excludes queue progress.

The real PostgreSQL tests `locked generation prefix advances and unlocked receipts emit exactly once` and `locked delivery prefix advances and unlocked receipts emit exactly once` cover Attempt, Wrap and Unlock, including four bounded sweeps before unlock, recovery and retry. They use committed producer transactions and roll back their consumer sweeps to preserve other clocks. The model abstracts one candidate per transaction and omits SQL cancellation, arbitrary arrivals, lifecycle churn and fairness under a permanently unavailable scheduler. Real scans remain limited to fifty candidates per transaction, including skipped locks. The Worker still admits four batches per invocation. A nonwaiting transaction advisory lock serializes sweep cursor ownership. Cursor and notifications commit or roll back together. Ordering is now by immutable generation UUID, not the mutable next-evaluation timestamp.

Regenerate Queue.tla with the same from-spec command as Delivery.tla, in a scratch directory with Queue.cfg and Queue.map.mjs. The inspected output is /tmp/j222-model/result/chart.png. queue-summary.json records fifteen model states and twenty transitions, with nine chart states and fourteen chart transitions. TypeOK and both queue invariants hold. This finite model establishes progress on a completed bounded pass, not a production latency promise.
