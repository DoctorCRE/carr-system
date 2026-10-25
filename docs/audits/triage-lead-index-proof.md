# Triage and Leads index proof

Audit [#2182](https://github.com/DoctorCRE/carr-system/pull/2182), slices Q3 then Q1.

Base snapshot `cfba29a073eff9d99b3bc601a35a5643e9d52b1f`. PostgreSQL 18 loaded `db/schema.sql`, then inserted synthetic rows. Each indexed table has 20,000 rows. Constraints, triggers, and existing indexes stayed enabled. The runner applied one forward migration.

## Reproduce

Run with PostgreSQL 18 server tools and locked Python and Node dependencies installed:

```sh
python ops/triage-lead-index-local-pg-gate.py --base cfba29a073eff9d99b3bc601a35a5643e9d52b1f --evidence /tmp/triage-lead-index-plans.json
```

The script creates and removes its own loopback cluster and accepts no database URL. `--slice Q3 --baseline` and `--slice Q1 --baseline` fail on the base snapshot. Both failures were observed before their respective indexes were added.

## Selective EXPLAIN results

These excerpts come from `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`. The script emits complete plans. Each queue starts with 100 open or new rows. No planner methods or costs were overridden. This is synthetic access-path evidence, not a production latency estimate.

| Read | Before | After |
| --- | --- | --- |
| Q3 next_action | Seq Scan `next_action` | Index Scan `next_action_open_due_idx` |
| Q3 post_call_action | Seq Scan `capture_post_call_action` | Index Only Scan `capture_post_call_action_open_due_idx` |
| Q3 critical_date | Seq Scan `critical_date` | Index Only Scan `critical_date_open_due_idx` |
| Q3 inbox | Seq Scan `ingest_inbox` | Index Only Scan `ingest_inbox_new_received_idx` |
| Q1 correspondence | Limit, Sort, Seq Scan `activity` | Limit, Sort, Bitmap Heap Scan `activity`, Bitmap Index Scan `activity_lead_timeline_idx` |
| Q1 history | Limit, Sort, Seq Scan `event` | Limit, Index Scan `event_lead_stage_mutation_idx` |
| Q1 latest | Unique, Sort, Seq Scan `event` | Unique, Index Scan `event_lead_stage_mutation_idx` |

The correspondence bitmap scan sorts only the matching lead history. PostgreSQL chose it over an ordered index scan at this cardinality. The test requires indexed access and exact output order, and allows this valid sort. The event index supplies selected-lead history order and the current set-based latest-stage order from migration `0954`.

## Matching index conditions

```text
Q3 next_action
  Index Scan next_action_open_due_idx; actual rows=97.0
    Index Cond: (due_on <= CURRENT_DATE)
    Filter: ((hold_until IS NULL) OR (hold_until <= CURRENT_DATE))
    Rows Removed by Filter: 1
Q3 post_call_action
  Index Only Scan capture_post_call_action_open_due_idx; actual rows=98.0
    Index Cond: (due_on <= CURRENT_DATE)
Q3 critical_date
  Index Only Scan critical_date_open_due_idx; actual rows=99.0
    Index Cond: (due_on <= (CURRENT_DATE + 14))
Q3 inbox
  Index Only Scan ingest_inbox_new_received_idx; actual rows=100.0
Q1 correspondence
  Bitmap Heap Scan activity; actual rows=120.0
  Bitmap Index Scan activity_lead_timeline_idx; actual rows=120.0
    Index Cond: (lead_id = 'f889d084-6a20-5d64-b250-091b18f52692'::uuid)
Q1 history
  Index Scan event_lead_stage_mutation_idx; actual rows=100.0
    Index Cond: (subject_id = 'f889d084-6a20-5d64-b250-091b18f52692'::uuid)
Q1 latest
  Index Scan event_lead_stage_mutation_idx; actual rows=240.0
```

## Whole reads and contrasting queues

The current `today-triage` handler SQL is captured by invoking its registered handler. Its full page and totals, the full triage view, and the Leads detail view return identical rows before and after. The handler retains its final rank/date/kind/id sort, owner filter, limit, and cursor predicates. Its selective plan uses the four queue indexes. Unrelated reference-name joins may still scan their small fixture relations.

In the mostly-open fixture, at least 90% of each queue is open or new. PostgreSQL chooses sequential scans for all four queue branches with and without the indexes. Complete query outputs still match. The test allows these plans because nearly every row qualifies.

Independent expected-row assertions verify future holds, holds ending today, null and future action dates, the inclusive 14-day critical-date cutoff, statuses, and 100-row history limits. Correspondence includes tied timestamps and unrelated leads. Stage events include missing and JSON-null stages, other subject types, and timestamps opposite to mutation order.

The final run passed all seven access-path queries and all ten result comparisons. Both queue ratios passed. The disposable cluster shut down before the success receipt. Migration shadow applied the migration, refreshed and restored the snapshot, and passed reviewed-seed and business-data-isolation checks.

## Decisions

- Keep the six additive indexes proposed by the audit. Query rewrites would change unrelated read contracts.
- Use ordinary index builds because the runner owns the transaction. SHARE locks and build duration remain rollout considerations at production row counts.
- Keep every existing unique index, grant, trigger, and historical migration. Add no compatibility code or runtime interface.
- Test SQL read results and selective access without fixing index names or forcing planner methods. Allow a bitmap scan to sort selected history.
- Make no production speedup claim from synthetic rows on a shared development host.
