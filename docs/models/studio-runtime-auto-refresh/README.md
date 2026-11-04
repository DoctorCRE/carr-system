# Studio runtime auto refresh

Statechart for WR634.S1.J80, corrected before the WR634.S1.J87 fix.

```mermaid
stateDiagram-v2
    [*] --> Recover
    Recover --> Restore: interrupted transaction, even if SHA matches
    Restore --> Observe: rollback artifacts and restore prior jobs under runtime lock
    Restore --> Refuse: recovery fails, retain journal
    Recover --> Refuse: another transaction holds runtime lock
    Recover --> Observe: no interrupted transaction
    Observe --> Current: verified release matches, artifacts valid and jobs enabled
    Observe --> Refuse: matching SHA with missing artifacts or disabled jobs
    Observe --> Wait: checked less than five minutes ago
    Observe --> Refuse: release identity or successful main deploy unavailable
    Observe --> Open: two attempts failed for this SHA
    Observe --> Cooldown: last attempt less than ten minutes ago
    Observe --> Attempt: persist attempt before any refresh effects
    Attempt --> Current: wrapper succeeds, SHA and artifacts match, jobs enabled
    Attempt --> Restore: process dies after checkout before job restoration
    Attempt --> RetryLater: first failure or crash
    RetryLater --> Attempt: same release after ten minutes
    Attempt --> Open: second failure or crash
    Open --> Open: same SHA / alert and no retry
    Open --> Observe: different verified release
```

Never refresh an unverified release. Never attempt a SHA a third time after two
failures. Never attempt twice within ten minutes, even across release changes or
process crashes. Never interrupt a send. Same installed SHA is a no-op only after recovery and artifact/job verification.
A persisted attempt counts as a failure until verified success. A crash after checkout can leave the released SHA installed while jobs remain
disabled and artifacts incomplete. Every tick first acquires the existing runtime
transaction lock and rolls back a pending journal through the existing wrapper.
Recovery runs before discovery throttling, network refusal, cooldown and breaker
checks and consumes no new attempt. Only verified artifacts and loaded, enabled
jobs allow a matching SHA to report current. Otherwise the tick refuses success.

## System design

One launchd calendar job wakes every five minutes. Python reads a dated local
ledger before each run, reads production `/release` (`git_sha.value`), and checks
for a successful `deploy.yml` run on main at that exact SHA through GitHub CLI. The workflow run name binds the actual deployed SHA,
which can differ from the workflow_run event’s own default-branch head SHA.
The existing drained wrapper owns send locks, runtime installation and recovery.
A temporary local source clone at the released commit supplies the installers;
shared main is never checked out or pulled by this job. The fetched remote-main
SHA is copied into the clone before verification, independent of local main. It is deleted on exit.
The alternative of running from current main fails when main is ahead of release.
A T3 task adds an agent session where a bounded deterministic job suffices.

State is an atomic JSON file under `out/studio-runtime-auto-refresh`; append-only
UTC receipts record observations and attempts. A per-job flock serializes ledger
writes. The SHA is the operation key. Attempt reservations precede side effects.
Two failures for one SHA open its breaker permanently; a new release gets its own
budget. Network and Git commands have timeouts, with no internal retries. The outer
wrapper invocation has no kill timeout; the existing drain and subprocess bounds
own lifecycle failure handling, so this caller cannot kill it during restoration.
Release discovery failures refuse effects and are limited to one check per five
minutes. Refresh attempts have a global ten-minute cooldown. Existing watchdog
findings and orchestrator inbox carry an open-breaker alert; nonzero job exit also
lets the watchdog observe it. No email or external message is sent.

Capacity is one Studio and one runtime today and at 10x release frequency. Newer
releases supersede older targets. Slow builds/drains break the fifteen-minute
objective first. Calls run sequentially; a failed identity/deploy read keeps the
installed runtime. There is no database access, N+1 query, fan-out, or queue.
Caching is limited to the persisted cooldown and per-SHA failures; release truth
is fetched anew, and stale release data never authorizes a refresh. Local receipts
are durable; production proof remains separate from unit tests. No generic
scheduler, retry service, or dependency is added.

## Production proof

installed runtime SHA equals the production /release SHA within 15 minutes of the next main release, with a refresh receipt and no dropped send

The builder does not merge or invoke production. After merge, install only this
job through `./run.sh config install-launchd com.carr.studio-runtime-auto-refresh --apply`.
Watch its first run and the next main release. Do not run a full config install.

## Transition tests

`ops/studio-runtime-auto-refresh-selftest.py` keeps named executable transitions.

| Transition | Test |
| --- | --- |
| Recover / Restore before Current, Wait, Refuse, Cooldown and Open | `test_crash_recovery_precedes_every_short_circuit` |
| Attempt interrupted after checkout to Restore | `test_crash_recovery_precedes_every_short_circuit` |
| Restore failure to Refuse with journal retained | `test_recovery_failure_keeps_journal_and_never_reports_current` |
| Concurrent runtime transaction to Refuse | `test_recovery_respects_runtime_transaction_lock` |
| Matching SHA with incomplete artifacts or disabled/unloaded jobs to Refuse | `test_current_requires_installed_artifacts_and_enabled_jobs` |
| Observe / Attempt to Current | `test_success_then_same_sha_noop`, `test_success_requires_runtime_readback` |
| Observe to Wait / Refuse | `test_unknown_release_never_refreshes_and_discovery_is_bounded` |
| Observe to Cooldown | `test_rate_limit_survives_release_change`, `test_slow_discovery_does_not_shorten_attempt_cooldown` |
| Attempt to RetryLater / Open and Open to Open | `test_two_failures_open_breaker_no_third_attempt`, `test_attempt_reservation_survives_crash` |
| Open to Observe on a different release | `test_release_change_gets_new_budget_without_resetting_old_breaker` |

The combined recovery tests use the wrapper's filesystem snapshot, drain locks,
swap and restoration with synthetic launchctl/installer ports. They reproduce
process death by releasing only the fixture's held locks. No live mailbox or
launchd state is touched. No formal proof is claimed.
