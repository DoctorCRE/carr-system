# Control-plane scheduler removal

The generic tick, scheduler, ledger CLI, dispatcher, collectors, provider calls,
watchdog overlap check, deployment definition, and their tests are removed.
Applied migrations and captured audit evidence remain unchanged. No tables are
dropped and no production changes are performed by this PR.

The following tables have no remaining runtime consumer in the repository.
Historical SQL functions and schema tests still reference them. A later migration
must review those dependencies before dropping anything.

- `ops.provider_route`
- `ops.provider_observation`
- `ops.cognition_job`
- `ops.cognition_result_cache`
- `ops.cognition_cache_observation`
- `ops.cost_reservation`
- `ops.cost_refusal`

`ops.job_definition`, `ops.job`, `ops.job_attempt`, and `ops.job_receipt` remain
shared with on-demand engineering claims and receipts. `ops.workflow_acceptance`
and the `ops.legacy_schedule_*` tables remain inputs to native scheduler
observations and assurance checks. They are not unused tables.

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
