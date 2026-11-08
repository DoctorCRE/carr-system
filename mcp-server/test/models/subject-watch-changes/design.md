System design
Canonical snapshots, keyed by watch generation, hold only meaningful selected deal/lease values. The registered operation completion trigger on public.tool_call compares that baseline to final canonical state, then commits the receipt and new baseline in the source transaction. Keyed intermediate events wait for that completion. Unkeyed source events still capture on insert. Material semantic identity is the operation idempotency key, or the unkeyed event UUID, plus material category. It never includes policy version. A pure SQL classifier returns changed field names and unknown/known reason codes. Reply identity is the inbound activity UUID and mailbox owner.
Alternative considered: classify event payloads alone. Rejected because lease replacement events omit prior values and late evidence must compare selected canonical state.
Two partners and the coexisting v1/v2 generations bound each subject to four subscriptions. Indexed subject lookup, per-generation row locks and one bounded read page avoid N+1 external work. At 10x, related-date aggregation is the first expected cost; no measured capacity claim. No cache or external call, hence no circuit breaker. Source and receipt refuse together on failure. Pending receipt delivery belongs to slice 4. Read authority is the verified partner actor, and inaccessible records refuse. Unique generation/semantic key handles replay. Paused/resumed generations reset their snapshot quietly.

The finite chart models one semantic source commit and its receipt only. PostgreSQL tests cover replay, rollback, lifecycle, mailbox isolation and partner concurrency beyond this two-state abstraction. Equivalent commit, Rollback and Replay are stuttering transitions in the model. The diagram and generated spec live beside this note; regenerate from a scratch copy with the installed statechart from-chart command.


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

Operation completion regression
The Meaningful commit transition means a complete registered operation, including
every intermediate event and canonical write. The date-swap regression asserts
one receipt for each partner operation and both next_actions and next_date in
the second receipt. The multi-field update regression combines scalar edits with
overdue action supersession and asserts one receipt with every changed field.
Equivalent commit and Replay remain quiet; Rollback still restores the baseline
and receipts. The migration initializer remains a quiet selected-state baseline.

Writer audit
update-deal emits per-field events before optional overdue-action supersession;
the completion trigger handles both together. set-next-action emits action and
shared-date events around its canonical writes; completion now handles both.
set-lead and add-critical-date each emit one event after all writes.
record-executed-lease performs its canonical replacement before its single event.
Inbound inherit-email-thread and confirm-email-filing emit per-activity filing
evidence; reply keys remain activity UUIDs with mailbox isolation. Existing
registered regressions exercise each writer, lease replacement, equivalent dates,
filing replay, rollback and concurrent partners.

Design and debt pass
codebase-design keeps classification behind one SQL operation-completion seam.
zero-tech-debt replaces per-event material identity rather than adding merge or
receipt-repair logic. No external call, queue or cache is introduced. Indexed
event-key lookup limits each completion scan to that operation's events.
