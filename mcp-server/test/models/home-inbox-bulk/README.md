# Reversible Home batches

Size L01: medium, one database transaction seam plus existing command adapters.

Design A adds a bulk command and a separate undo verb. Design B adds optional
`reversible: true` and `undo_of` to existing snooze/settle commands. Choose B.
It preserves deployed calls and reuses the only settlement command.
`undo_of` names the original idempotency key. Omit items to undo the whole batch,
or supply original item keys to undo a subset. No-op receipts never restore rows.
Undo refuses intervening changes rather than overwriting newer work.

Data shape: actor-owned batch receipt with per-item prior disposition row,
after version, outcome and undone marker. One actor lock serializes dispositions.
Per-item savepoints isolate failures. No new item state is introduced.

```mermaid
stateDiagram-v2
  working --> snoozed: batch snooze
  working --> set_aside: batch settle
  done --> snoozed: batch snooze
  done --> set_aside: batch settle
  auto_settled --> auto_settled: batch settle already
  auto_settled --> snoozed: batch snooze
  set_aside --> set_aside: batch settle already
  set_aside --> snoozed: batch snooze
  snoozed --> snoozed: same deadline already
  snoozed --> set_aside: batch settle
  snoozed --> undone: undo restores prior row
  set_aside --> undone: undo restores prior row
```

Undone means the exact saved disposition is restored, not a persisted item state.
Tests named `bulk statechart <state> <command> and exact undo` cover each
transition for working, done, auto-settled, set-aside, snoozed and explicit override.
Mixed receipts cover not found, forbidden and stale items. Separate tests cover
replay, whole/subset undo, concurrent changes and actor isolation.

Throughput checkpoint: first read existing commands and fail acceptance tests.
One workstream owns SQL, adapter and tests because they share a contract.
The shared mutable state is actor dispositions, protected by the existing lock.
One builder is the smallest safe decomposition; this brief forbids subagents.

The Model the Domain principle selected an actor-owned receipt ledger instead
of client-provided restoration payloads. Restoration values never cross the
public command interface. The deep module is the existing disposition command;
the receipt helper calls it and owns only exact undo bookkeeping.

Run the installed statechart script against Bulk.tla with `--view view.mjs`.
The checked model bounds the batch to two items, with the second initially
working. It models allowed/denied items and target no-ops; SQL tests cover real
visibility, optimistic version conflicts, replay and subtransaction failures.
Stay steps are hidden. The chart merges states of the second item; the model
checks invariants for both items. No new item state is persisted for undo.

Blast radius proof: the full home-inbox-postgres suite executes the actual
PostgreSQL functions and registered tools. Legacy commands retain their result
shape, and v1/v2/v3 snapshots retain their existing contracts. The new helper has
no public or writer grant and uses the original function's definer authority.
