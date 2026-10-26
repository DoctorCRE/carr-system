# Control-plane scheduler removal

The generic tick, scheduler, ledger CLI, dispatcher, collectors, provider calls,
watchdog overlap check, deployment definition, and their tests are removed.
Applied migrations and captured audit evidence remain unchanged.

Forward migrations retire the unused cost-refusal and cache-observation ledgers.
`0999_retire_scheduler_contracts.sql` retires the remaining provider, cognition,
cache, and cost-reservation tables, their exclusive functions, and the three
writerless legacy schedule contracts. Native schedulers remain unchanged.

`ops.job_definition`, `ops.job`, `ops.job_attempt`, and `ops.job_receipt` remain
shared with on-demand engineering claims and receipts. `ops.workflow_acceptance`
and the historical legacy schedule observation and disable receipts remain
inputs to schedule-board and health readers. Their append-only controls remain.
Staging approval receipts also remain as historical evidence. Staging release
records no longer require a separate typed approval.

`tools/workflow-registry.py sync` retains only the contract bootstrap needed by
those shared consumers and disposable database reconstruction. It cannot enqueue,
claim, execute, retry, or promote jobs. Native schedulers own recurrence.

The three deterministic recurring workflows retain their direct launchd paths.

| Workflow | Existing launchd job | Command |
| --- | --- | --- |
| Calendar fetch | `com.carr.calendar-eventkit` | `bin/calendar-eventkit-capture.sh` |
| Nightly record layer | `com.carr.nightly-record-layer` | `bin/nightly.sh` |
| Notes sweep | `com.carr.notes-sweep` | `bin/notes-sweep-post.sh` |

The AI tasks retain their existing native scheduler definitions. The five paused
capability and prebrief definitions and the disabled renewal-source definition
are not activated. `engineering-slice` remains on demand through its dedicated
MCP and room-bridge worker. No job requires the tick.
