# Link model bake-off

Run reproducible identity-integrity and graph workloads on an owned PostgreSQL 18 cluster. The runner initializes under `TMPDIR`, chooses a random port, listens only on a private Unix socket, and stops the cluster in its context manager. Stopped clusters move into the temporary parent's `_to_delete` directory. It accepts no connection string and reads no CARR database configuration.

Use Python with `psycopg` installed and PostgreSQL 18 binaries available. From the repository root:

```sh
LC_ALL=C python3 tools/link-model-bakeoff/pg18-selftest.py --pg-bin /path/to/postgresql18/bin
LC_ALL=C python3 tools/link-model-bakeoff/run.py --pg-bin /path/to/postgresql18/bin
```

The default binary path is Homebrew's `/opt/homebrew/opt/postgresql@18/bin`. `--output` chooses the report directory. The default is `out/orch/linkfork`. `--small` exercises the same SQL at reduced scale and one timing repetition; it is for integration checks, not design selection.

The canonical CI collector explicitly exempts the PG18 SQL selftest by name in `ops/ci-selftest.py`. Run that selftest before changing the harness. It runs the failure-history regressions and the integration workload. The gates pool collects `tools/test-linkfork.py` for offline namespace, monitor-failure, and collection checks.

The standalone `bakeoff.html` contains the conditional verdict, measurement tables, searchable rows, plans, and source evidence. `results.json` carries raw repetition measurements and inventory definitions. Generated `design-*.sql` and `c-to-*.sql` make the tested DDL and migration statements reviewable.

## Designs and scope

- A uses one exclusive-arc table per observed relationship family, foreign keys to domain tables, generated kind/ID columns, and a `UNION ALL` read view.
- B uses a physical registry, fixed-kind composite foreign keys from domain tables, and one edge table with composite endpoint foreign keys. The harness writes registry and domain rows in the same transaction. It deliberately does not add reverse-existence or deletion-cleanup triggers to B, so its specified guarantees can be tested.
- C projects identity-relevant constraints from migration-defined polymorphic tables. It preserves fixed-source foreign keys and their scanned deletion actions, kind checks, and observed duplicate indexes. It does not reproduce record envelopes, permissions, source revision validation, or per-owner next-action uniqueness.

`inventory.py` scans every migration's table definitions, applies scalar kind-check amendments and the candidate-pool rename, and searches all MCP JavaScript for table usage. It includes scalar pointer candidates in audit, provenance, taxonomy, notification, and lifecycle tables. Five tables named `link` have polymorphic pointers. Their measured families include doctrine citations, incident references, SIEP evidence, F01 derivatives, and lifecycle evidence pins. Supporting event, source, action, flag, and attachment pointers also participate. JSON distinguishes this broad inventory from the timed families.

This is a graph-key experiment. Native text IDs, artifact digests, bigint IDs, tenant fields, and evidence version pins require a collision-safe mapping before either proposed UUID design can replace those contracts. Synthetic UUIDs stand in for those mappings; no production mapping is claimed. Contacts and companies share the party entity kind.

## Measurement contract

The full run scales an explicitly assumed planning baseline by ten. It uses the same deterministic nodes and edges for every design. Target selection places twenty percent of edges into a one-percent hot set. Generation respects the baseline's derivative-target and evidence uniqueness constraints. Reported row counts come from the generated and loaded data, not a production census.

Depth-two context traverses incoming and outgoing links, includes the root, returns distinct vertices at minimum depth, and fetches the entire ordered result. Every timed query result must match the other designs. Timings include socket round trips, server execution, and row decoding. Each side warms up, then runs five interleaved repetitions over the same thousand refs. Psycopg automatically prepares repeated queries after five calls. The separately planned `EXPLAIN` includes `ANALYZE` and `BUFFERS`.

Write cycles insert a doctrine section and three links, update the domain payload and link targets, then remove both in committed transactions. B also inserts and deletes the registry entry. Independent writers each own their rows. The separate hotspot workload updates the same domain row and its links, with an intentional one-millisecond hold. Lock observations are ten-millisecond `pg_stat_activity` samples, not exact wait durations. The run verifies that write cycles leave the original graph unchanged.

Integrity probes use raw SQL and independent savepoints. They cover missing endpoints, wrong kinds, cross-kind UUIDs, target mutation, deletion, duplicates, and malformed arcs. A's unsupported typed columns can reject with a structural SQL error; the report distinguishes that from FK rejection. Registry-only and deletion bypasses test guarantees that same-transaction insertion cannot provide. Killing an owned backend tests rollback separately.

Migration rehearsals install transactional change capture before a repeatable-read shadow backfill. Capture retains old and new identities. After acquiring source locks with bounded `NOWAIT` retries, reconciliation removes touched shadow edges, removes obsolete domain identities, and upserts the final source domains and edges. It avoids applying obsolete intermediate references from before the snapshot. Cutover replaces capture with synchronous dual-write and switches the read view. A writer continues through backfill and cutover. Final checks compare every edge and domain row in both directions. Statement counts include client-submitted DDL and transaction control. Backfill-pass counts include every registry UNION arm. Shadow domain copies isolate the rehearsal; A can reference existing domain tables in a deployment.

These are executable rehearsals on normalized synthetic identities. Existing orphan cleanup, duplicate disposition, tenant and external-key mappings, role grants, and application writer cutover remain requirements for a production migration. No original source table is dropped. No production migration is performed.

## Files

`model.py` owns the storage contracts. `bench.py` owns data and workload checks. `cluster.py` owns local database lifecycle and serializes connection creation while clearing and restoring libpq environment defaults. `linkfork_migration.py` renders and executes the online rehearsal. `report.py` renders measured results. `run.py` composes them. `pg18-selftest.py` exercises the actual SQL, failure histories, crash rollback, concurrent writes, migration catch-up, and shutdown.

PostgreSQL primary references: [constraints](https://www.postgresql.org/docs/18/ddl-constraints.html), [ALTER TABLE](https://www.postgresql.org/docs/18/sql-altertable.html), and [CREATE INDEX](https://www.postgresql.org/docs/18/sql-createindex.html).
