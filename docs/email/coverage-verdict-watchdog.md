# Daily Outlook verdict freshness

The watchdog detects a missed daily verdict without waiting for another intake
run to set `coverage_failed`. It reads `coverage_check_day`, `coverage_run_day`,
and the mailbox timezone from the existing intake state and settings.

## Deadline

Intake starts judging coverage at 18:00. The current launchd template retries
every 30 minutes through 19:00, every calendar day. The watchdog derives the
last slot from that template and adds `scan_grace_seconds` from its existing
configuration. The current deadline is 19:05 in the mailbox timezone. The
finding becomes eligible strictly after that deadline, on the next watchdog
scan. Before the deadline, yesterday is the latest day owed a verdict.
Weekends are included. This rule differs from the existing no-email-in alarm,
which still uses business days. Mailbox wall time preserves the deadline across
both DST transitions.

`coverage_check_day` proves that an actual verdict occurred, whether it passed
or failed. `coverage_run_day` records the latest successful intake run. It provides
first-run grace only when no check day exists. A stale check remains owed even
when a later intake run resumes. A legacy state with only a check day also
establishes tracking. Neither day means a first
install has no established obligation. A first run before its first deadline
gets the remaining daily window. Invalid, noncanonical, or future days raise
the same freshness finding rather than suppressing it or aborting the scan.

## System design

Collection preserves local coverage facts even when the remote outcome request
fails or times out. The pure detector accepts the existing injected scan clock.
It emits one stable key, `job_degraded:outlook:coverage_verdict_missing`.
Reconciliation and delivery use the existing scan and inbox locks, append-only
finding history, and bounded digest. No additional scheduler is involved.

A missed verdict remains active through a lost state read or a later scan before
the next deadline. Only a valid actual check day at least as recent as the
finding's owed day permits recovery. A later verdict may independently report
`coverage_failed`; freshness recovery does not mean coverage passed.

Collection reads one local state snapshot and makes one bounded outcome request.
Cross-source transactions are not needed because freshness uses local facts
alone. Existing scan locking serializes reconciliation. Repeated scans converge
to one active key and do not append unchanged findings. There is no retry loop
or new persistent state. Each scan reads one state file and one outcome response;
ten times the scan frequency means ten times those reads, with the existing
serialized scan lock and remote outcome timeout as likely first limits. This is
an estimate, not a measured capacity. There is no cache to invalidate or evict,
no fan-out, queue, or N+1 query, and no new load distribution or scaling path. Outcome-read, coverage-failed, job-exit, and disk alarms
remain independent.

The [statechart and named transition tests](../statecharts/outlook-verdict-watchdog.md)
define the verdict lifecycle.

The table summarizes detector and reconciliation outcomes.

| Observation | Transition |
| --- | --- |
| Neither day exists and no prior miss exists | Remain unestablished. |
| Tracking began but its first deadline has not passed | Wait. |
| Valid check covers the latest day owed | Stay fresh. |
| Check is absent or stale after the deadline | Open the missed-verdict key. |
| Either day is malformed or future | Open the same key with an invalid-day reason. |
| Local facts disappear after a miss | Retain the active miss. |
| A later valid verdict covers the missed day | Clear freshness; evaluate coverage failure separately. |

## Verification and production observation

Synthetic tests execute collection, detection, reconciliation, delivery, and
digest with temporary files and mocked launchctl and outcome transport. They
cover exact deadlines, legacy state, first install, stale success, invalid days,
weekends, both DST transitions, transport failure, lost state, and recovery.

After deployment and normal runtime refresh, the coordinator reads the installed
watchdog findings and digest and compares freshness against the local coverage
days and mailbox deadline. If a deadline is naturally missed, the same active
key must remain until a later actual verdict. Do not stop intake or alter state
to manufacture evidence. No naturally missed deadline means that persistence
claim remains unobserved in production.

The proof declaration is
`proofs/requirements/outlook-missed-verdict-watchdog.json`. The registered Studio
receipt observer cannot itself attest local finding and digest semantics. Its
receipt result is only runtime evidence; the coordinator must supply the direct
readback above. A missing watchdog receipt is BLOCKED, never a passing proof.
