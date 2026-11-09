System design

The receipt belongs to a committed transaction and watch generation. An event is
provenance and a wake-up signal. Neither an event nor a tool_call is completion.
The generation baseline owns material, capture_tx, capture_before, capture_events
and capture_pending. First ingress freezes the generation baseline for this
transaction and accumulates event IDs. A deferred constraint trigger compares
final canonical state once, inserts one transaction/generation receipt, and moves
the baseline atomically. Later queued firings observe capture_pending=false.

PostgreSQL lets SET CONSTRAINTS IMMEDIATE flush early. Canonical writes to deal,
next_action, deal_participant, critical_date, lease and activity requeue the
generations already touched by this transaction through a capture_tx index.
The hook has no table-to-subject routing, so moving a row out of a watched
subject also invalidates an early comparison.
An early flush therefore reconciles the same transaction key using capture_before,
including deleting an intermediate receipt when the final delta is empty. A
receipt with replies also carries every changed material field. Replies deduplicate
by activity evidence across transactions. The reader removes retired mailbox
evidence from mixed material receipts too. Lifecycle updates also invalidate an early comparison. Pause and policy changes
re-evaluate final eligibility; removal and resume discard only the current
transaction's provisional receipt. Historical receipts remain. Source, baseline
and receipt all roll back together, including savepoints. No dispatcher
completion dependency remains.

Caller usage and types

Existing canonical writers still emit public.event records. They need no new call.
The private capture_subject_watch_changes(uuid) records provenance and dirty state.
flush_subject_watch_changes() is the constraint-trigger implementation.
touch_subject_watch_material() invalidates an early flush after canonical writes.
All three remain database-private. The reader and registered authority metadata
are unchanged. The baseline row is also the bounded transaction work record, so
there is no orphan staging queue or garbage collector.

Executed design comparison

1. Chosen deferred transaction capture plus canonical touch. The PostgreSQL matrix
   passes keyed and unkeyed raw ingress, multi-event registered writers, early
   immediate flush, net-zero, lifecycle fences after early flush, savepoints, long-transaction invisibility, rollback,
   concurrent partners and removal. Two operations in one transaction intentionally
   produce one receipt carrying both fields and all source events.
2. Dispatcher-end flush plus a raw event hook. The permanent alternative experiment
   flushes after raw ingress with canonical touch disabled inside a rolled-back
   transaction. A subsequent closing-date write leaves the baseline unchanged.
   Registered writers can call a dispatcher, but raw ingress cannot be required to
   do so. This loses locality and fails the executed raw post-event case.
3. Logical-decoding consumer. Rejected before prototype because asynchronous capture
   cannot atomically commit the baseline and receipt with the source transaction.

The dispatcher/completion prototype was also run against the whole registered/raw
PostgreSQL matrix, using an initially-immediate flush and a tool_call completion
hook with canonical touch disabled. Its registered two-operation coalescing and
keyed filing pass, but raw post-event material changes fail. The final alternative
run passes 18 of 26 tests; the chosen implementation passes all 357 tests in
the six-suite run, including every matrix case. Later failures can
reflect that stale baseline; they are not counted as independent defects. The
permanent one-case experiment isolates the missing-post-event-write obligation.

The original operation-marker design was also executed against the new matrix.
It drops keyed raw filing and keyed raw closing-date changes. Unkeyed evidence
before the canonical write also misses that final state. The new design removes
tool_call capture and its event-idempotency-key index entirely.

Capacity and failure behavior

There are two partners and up to four coexisting v1/v2 generations per subject.
Event ingress locks generations in ID order and records provenance in their rows.
The final read aggregates canonical dates/actions once per dirty generation at
normal commit. Repeated event wake-ups do not repeat the canonical comparison.
At 10x load, long transactions holding generation rows and large action/date
aggregations are the first expected limits. This is an inference, not a benchmark.
Early immediate mode deliberately trades repeated comparisons for correctness.
Transaction requeue uses the capture_tx index and only locks rows this
transaction already owns. Pending event arrays contain only the last transaction per generation, overwritten
on the next transaction; storage is bounded by watch count and transaction size.
No external fan-out, network calls, cache, background worker or new delivery queue
exists, so external rate limits, cache eviction and circuit breakers do not apply.
PostgreSQL lock/statement timeouts govern the caller. Failure refuses the source
transaction instead of serving stale receipts. Pending delivery remains slice 4.

State model and tests

The finite model describes one material transaction as observed from another
connection. Each never rule has its own invariant. AtomicCapture rejects a
receipt for rollback. NoLostCapture rejects a committed material change without
a receipt, including keyed raw ingress. NoIntermediateReceipt rejects visibility
before commit. The PostgreSQL matrix covers the cases intentionally abstracted
out of the model, including lifecycle, mailbox policy, multiple fields, multiple
partners, savepoints, immediate flushing, duplicate evidence and material no-ops.

Transition tests

- Registered write, Another write, Commit. two registered operations share one transaction receipt with both changes.
- Keyed raw ingress, Commit. keyed raw late evidence captures final canonical state.
- Unkeyed raw ingress, Commit. unkeyed raw late evidence captures final canonical state.
- Rollback. savepoint rollback and full rollback preserve baseline and receipts.
- Replay. existing exact action and filing replay assertions in the canonical suite.

Regenerate from a scratch copy of Receipts.tla, Receipts.cfg and Receipts.map.mjs
with statechart.mjs from-spec Receipts.tla --png. The checked-in Mermaid is the
rendered reachable chart, with one never comment per invariant. Another write and
Replay are stuttering transitions and omitted from the rendered overview.

Design and debt pass

codebase-design puts capture behind one database-owned transaction seam. Writers
know only canonical writes and source events, not dispatcher completion timing.
zero-tech-debt deletes the tool_call trigger, operation capture function and
idempotency-key event index. The added trigger machinery replaces the incomplete
operation-marker mechanism and makes early constraint flushes correct.

Pending receipt proof
The canonical next-action snapshot holds distinct owner/date pairs, not replacement
IDs, descriptions or timestamps. Changing Joe's due date while Dell's date already
sets deal.next_date captures one receipt. Equivalent replacements and exact replays
remain quiet. The existing Meaningful, Equivalent and Replay transitions cover this
fix; registered set-next-action regressions exercise all three.

The read-only CLI is `ops/verify-subject-watch-proof.py`. With a configured libpq
service, capture a visible watched deal and its generation using `capture --watched
<deal UUID> --subscription <watch UUID> --actor joe --output /tmp/watch-before.json`.
The capture runs one repeatable-read transaction with five-second statement limits,
reads at most 100 receipts through the canonical partner reader, and validates the
registered response contract. Incomplete pages or unavailable authority exit 78.
It counts the selected deal's source events to distinguish a quiet source write
from no write. It retains only this selected generation's receipt evidence.

After a containing deployment, the authorized proof operator performs one registered
meaningful change and captures `/tmp/watch-after.json`, then one registered
immaterial change and captures `/tmp/watch-immaterial.json`. Replay the exact
registered requests with their original keys and capture `/tmp/watch-replay.json`.
Keep this deal quiet otherwise. The verifier requires exactly one new pending
material receipt, zero receipt changes for the immaterial write, and zero source
events or receipts added by replay. Any concurrent source write fails the count
assertion. Do not place connection secrets in commands or artifacts.

Run `python3 ops/verify-subject-watch-proof.py verify --before /tmp/watch-before.json
--after /tmp/watch-after.json --immaterial /tmp/watch-immaterial.json --replay
/tmp/watch-replay.json --merge-sha <full merge commit>` on one command line.
It reads production `/release` using `lib/release_reader.py`, validates the production
release contract and uses git ancestry to accept equal or descendant deployments.
The local repository must contain both revisions. The total deadline is 30 seconds.
Exit 0 means all assertions passed on a containing release; 1 means assertion
failure; 78 means insufficient evidence, including older, unrelated or unknown
releases. `--release-fixture` is offline testing only and labels output accordingly.
The PostgreSQL suite records the four samples from registered writers and runs the
real CLI against equal, descendant, older, unrelated and unknown release fixtures,
plus an assertion failure. No production observation was run by this worker.
