The delivery model checks one watch generation and one semantic change. Watch state, receipt disposition, inbox placement, processing health, access and the last confirmed read change independently. It models each database emission as one atomic step under the generation lock. It checks both possible orders of removal and emission. PostgreSQL tests exercise the actual locks, busy receipts, concurrent sweepers, rollback and current policy checks.

The six never rules have separate invariants in Delivery.cfg. TypeOK is additional. The model does not prove SQL isolation, calendar arithmetic, notification rendering, device delivery, arbitrary queue lengths, fairness or eventual delivery. Inbox placement and read errors are abstractions of existing consumers, not new app behavior. Repair is an explicit operator repair, not an automatic third retry.

Regenerate in a scratch directory with the installed pstack statechart skill. Copy Delivery.tla, Delivery.cfg and Delivery.map.mjs there and run `node statechart.mjs from-spec Delivery.tla --out result --png`, with TLA2TOOLS and JAVA pointing to installed TLC and Java. The resulting summary reports 96 model states. The checked chart and stories are committed beside the model. The builder inspected /tmp/j139-model-delivered/result/chart.png.

Transition coverage:

| Model action | Executable test |
| --- | --- |
| Capture, Emit, Replay | crossing without business mutation emits once and later days stay quiet |
| Pause, Resume | pause suppresses pending and resume takes quiet baseline |
| Unwatch | removed generation never emits; busy locks skip and later sweep catches up |
| Failure, Repair | mint rollback preserves pending and repeated failures quarantine |
| AccessLost | coalescing, rate limit, busy receipts and delivery policy use current state |
| Settle | watch alerts reach named Home cards and read, settle, snooze stay independent |
| ReadFailure, ReadSuccess | registered watch transitions preserve isolation and initial baseline; registered month policy and current health pass the producer parser |

The SQL sweep is the consumer seam. It uses one network connection and indexed lookups under each acquired generation lock. There are no per-watch external requests or caches. The loop admits at most 50 watches per transaction and the Worker runs at most four transactions within its deadline. At 10x load, indexed source lookups and pending backlog are the expected limits. Synthetic 50/500-watch runs check exact emitted receipt and notification counts; they do not establish production capacity. Failed minting rolls back only emission, preserving captured clock receipts. A second failed attempt quarantines the generation and exposes needs_repair health. Stale global sweep health appears after fifteen minutes.

The competing design put queue ownership and retries in the Worker and committed notification creation separately. It was rejected because a lost acknowledgement could separate the notification from its covered receipts. PostgreSQL owns capture, coalescing and emission instead; the Worker owns scheduling and the invocation budget.
