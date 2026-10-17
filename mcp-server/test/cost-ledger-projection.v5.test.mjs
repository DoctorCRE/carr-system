
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";

import {
  V5_LEDGER_ENTRY_KINDS, V5_LEDGER_OPERATION_KINDS, V5_SCOPE_KINDS, V5_OVERDRAWN_REMEDIES,
  V5_SCOPE_TREE_SCHEMA_VERSION, applyOperations, compileScopeTree,
  assertLedgerConservation, ledgerStateDigest, openLedger, projectLedger,
} from "../src/hierarchical-cost-ledger.v5.js";
import {
  METERING_PATH, METERING_SCHEMA_VERSION, commitLedgerOperation, readMeteringProjection,
} from "../src/cost-ledger-projection.v5.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_COST_LEDGER_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;
const MIGRATION = new URL("../../migrations/0519_producer_cost_ledger.sql", import.meta.url);

const TREE = Object.freeze({
  schema_version: V5_SCOPE_TREE_SCHEMA_VERSION,
  tree_id: "wr111-metering",
  tree_version: 1,
  nodes: [
    { node_id: "root", parent_node_id: null, scope_kind: "portfolio", authorization_ceiling_units: 1000 },
    { node_id: "child", parent_node_id: "root", scope_kind: "child", authorization_ceiling_units: 500 },
    { node_id: "slice", parent_node_id: "child", scope_kind: "slice", authorization_ceiling_units: 100 },
  ],
});

async function skipUnlessDatabase(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof writes ledger rows and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

async function connect(pg) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  return client;
}

const wrap = client => ({ query: (text, values = []) => client.query(text, values) });

async function freshTree(client) {
  const treeRef = `wr111:${randomUUID()}`;
  const tree = compileScopeTree(TREE);
  const nodeRows = tree.node_ids.map(id => ({
    node_id: id,
    parent_node_id: tree.nodes[id].parent_node_id,
    scope_kind: tree.nodes[id].scope_kind,
    authorization_ceiling_units: tree.nodes[id].authorization_ceiling_units,
    depth: tree.ancestors[id].length,
  }));
  await client.query("select ops.initialize_cost_scope_tree($1::text,$2::jsonb,$3::text)", [
    treeRef,
    JSON.stringify({ tree_id: tree.tree_id, tree_version: String(tree.tree_version), node_rows: nodeRows }),
    ledgerStateDigest(openLedger(tree)),
  ]);
  return { treeRef, tree };
}

const reserve = (id, reservationId, units) => ({
  kind: "reserve",
  operation: { operation_id: id, node_id: "slice", reservation_id: reservationId,
    amount_units: units, requested_at: "2026-09-01T00:00:00.000Z" },
});

async function inTransaction(client, fn) {
  await client.query("begin");
  try {
    const value = await fn();
    await client.query("commit");
    return value;
  } catch (error) {
    await client.query("rollback");
    throw error;
  }
}


test("T-CONST: the 0519 enum lists equal the module's closed constants in both directions",
  () => {
    const sql = readFileSync(MIGRATION, "utf8");
    const entryKinds = sql.match(/NOT entry_kind\.[\s\S]*?\(([^)]*)'\)\)/);
    const listed = `${entryKinds[1]}'`.replace(/^[\s\S]*?\(/, "")
      .replace(/\s|'/g, "").split(",").filter(Boolean);
    assert.deepEqual(listed, [...V5_LEDGER_ENTRY_KINDS],
      "the entry-kind enum in 0519 is not V5_LEDGER_ENTRY_KINDS");
    const operationKinds = sql.match(/kind text not null check \(kind in \(([^)]*)\)\)/);
    assert.deepEqual(
      operationKinds[1].replace(/\s|'/g, "").split(",").filter(Boolean).sort(),
      [...V5_LEDGER_OPERATION_KINDS].sort(),
      "the operation-kind enum in 0519 is not V5_LEDGER_OPERATION_KINDS");
    const scopeKinds = sql.match(/scope_kind text not null check \(scope_kind in \(([^)]*)\)\)/);
    const scopes = scopeKinds[1].replace(/\s|'/g, "").split(",");
    assert.deepEqual([...scopes].sort(), [...V5_SCOPE_KINDS].sort(),
      "the scope-kind enum in 0519 is not V5_SCOPE_KINDS");
  });

test("T-CONST: 0519 stores the module's own field names, never the request's older words", () => {
  const sql = readFileSync(MIGRATION, "utf8");
  for (const frozen of ["amount_units", "vendor_reference", "authorization_ceiling_units",
    "entry_id", "conversion_id", "basis_digest", "tree_version"]) {
    assert.ok(sql.includes(frozen), `0519 does not carry the frozen module field ${frozen}`);
  }
  for (const alias of ["cost_units", "vendor_ref ", "ceiling_cost_units",
    "expected_version", "actual_version", "expected_state_digest", "actual_state_digest"]) {
    assert.ok(!sql.includes(alias), `0519 speaks a different language: ${alias.trim()}`);
  }
});

test("MTR-ROUTE-CONTRACT: the route constant and schema version are exported once", () => {
  assert.equal(METERING_PATH, "/api/v1/metering");
  assert.equal(METERING_SCHEMA_VERSION, "doctorcre-v5-metering.v1");
});

test("MTR-TREE-ROUNDTRIP: a stored ledger reads back deep-equal to the in-memory one",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { treeRef, tree } = await freshTree(c);

    const steps = [
      { kind: "record_estimate", operation: { operation_id: "op-est", node_id: "slice",
        expected_total_cost_units: 60, basis_digest: `sha256:${"a".repeat(64)}`,
        recorded_at: "2026-09-01T00:00:00.000Z" } },
      reserve("op-res", "res-1", 40),
      { kind: "post_actual", operation: { operation_id: "op-act", node_id: "child",
        amount_units: 30, vendor_reference: "VENDOR-1", incurred_at: "2026-09-02T00:00:00.000Z" } },
    ];
    for (const step of steps) {
      await inTransaction(client, () => commitLedgerOperation({ client: c, tree_ref: treeRef, step }));
    }

    const memory = applyOperations(openLedger(compileScopeTree(TREE)), steps).ledger;
    const expected = projectLedger(memory);

    const stored = await readMeteringProjection({
      client: c, actor: { slug: "joe" }, correlationId: "roundtrip", tree_ref: treeRef, period: null });
    assert.equal(stored.coverage.complete, true);
    assert.equal(stored.as_of.quantity, expected.by_node[expected.root_node_id].rolled_up.committed_units);
    assert.equal(stored.estimate.units, expected.by_node[expected.root_node_id].rolled_up.estimate_units);

    const rows = await client.query(
      `select sequence, entry_id, kind, node_id, amount_units, operation_id
         from ops.cost_ledger_entry where tree_ref = $1 order by sequence`, [treeRef]);
    assert.deepEqual(
      rows.rows.map(row => ({ sequence: Number(row.sequence), entry_id: row.entry_id,
        kind: row.kind, node_id: row.node_id, amount_units: Number(row.amount_units),
        operation_id: row.operation_id })),
      memory.entries.map(entry => ({ sequence: entry.sequence, entry_id: entry.entry_id,
        kind: entry.kind, node_id: entry.node_id, amount_units: entry.amount_units,
        operation_id: entry.operation_id })));
    const treeRow = (await client.query(
      "select tree_id, tree_version from ops.cost_scope_tree where tree_ref = $1", [treeRef])).rows[0];
    assert.equal(treeRow.tree_id, tree.tree_id);
    assert.equal(Number(treeRow.tree_version), tree.tree_version);
  });

test("MTR-CAS-CONFLICT (a): two library callers from one base land once and refuse once, by the module's own four names",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const owner = await connect(pg);
    const first = await connect(pg);
    const second = await connect(pg);
    t.after(() => Promise.all([owner, first, second].map(x => x.end().catch(() => {}))));
    const { treeRef } = await freshTree(wrap(owner));

    await first.query("begin");
    const landed = await commitLedgerOperation({
      client: wrap(first), tree_ref: treeRef, step: reserve("op-1", "res-1", 10) });
    await first.query("commit");
    assert.equal(landed.result.ok, true);

    await second.query("begin");
    const refused = (await second.query(
      `select ops.commit_cost_ledger_operation(
         $1::text,0::integer,$2::text,'op-2'::text,$3::text,'reserve'::text,
         '{"accepted":true}'::jsonb,null::text,1::integer,'digest-loser'::text,'[]'::jsonb) as result`,
      [treeRef, landed.result.state_digest === undefined ? "" : (await second.query(
        "select state_digest from ops.cost_ledger_cell where tree_ref = $1", [treeRef])).rows[0].state_digest,
       `sha256:${"b".repeat(64)}`])).rows[0].result;
    await second.query("rollback");

    assert.equal(refused.ok, false);
    assert.equal(refused.reason_id, "version_conflict");
    for (const name of ["base_version", "current_version", "base_state_digest", "current_state_digest"]) {
      assert.ok(Object.hasOwn(refused, name), `the refusal is missing the module's name ${name}`);
    }
    for (const alias of ["expected_version", "actual_version", "expected_state_digest", "actual_state_digest"]) {
      assert.ok(!Object.hasOwn(refused, alias), `the refusal speaks an alias: ${alias}`);
    }
    const rows = await owner.query(
      `select (select count(*) from ops.cost_ledger_operation where tree_ref=$1) ops,
              (select count(*) from ops.cost_ledger_entry where tree_ref=$1) entries`, [treeRef]);
    assert.equal(Number(rows.rows[0].ops), 1);
    assert.equal(Number(rows.rows[0].entries), 1);
  });

test("MTR-IDEMPOTENT-REPLAY: the same operation replays from its ORIGINAL base after the cell has moved",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { treeRef } = await freshTree(c);

    await inTransaction(client, () =>
      commitLedgerOperation({ client: c, tree_ref: treeRef, step: reserve("op-1", "res-1", 10) }));
    const afterFirst = (await client.query(
      "select ledger_version from ops.cost_ledger_cell where tree_ref=$1", [treeRef])).rows[0];
    await inTransaction(client, () =>
      commitLedgerOperation({ client: c, tree_ref: treeRef, step: reserve("op-2", "res-2", 10) }));

    const digest = (await client.query(
      "select argument_digest from ops.cost_ledger_operation where tree_ref=$1 and operation_id='op-1'",
      [treeRef])).rows[0].argument_digest;
    const replay = (await client.query(
      `select ops.commit_cost_ledger_operation($1::text,0::integer,'stale-base'::text,'op-1'::text,
         $2::text,'reserve'::text,'{"accepted":true}'::jsonb,null::text,
         1::integer,'ignored'::text,'[]'::jsonb) as result`, [treeRef, digest])).rows[0].result;
    assert.equal(replay.ok, true);
    assert.equal(replay.replayed, true);
    assert.equal(Number(replay.ledger_version), 2);
    assert.equal(Number(afterFirst.ledger_version), 1);

    const counts = (await client.query(
      `select (select count(*) from ops.cost_ledger_operation where tree_ref=$1) ops,
              (select count(*) from ops.cost_ledger_entry where tree_ref=$1) entries`,
      [treeRef])).rows[0];
    assert.equal(Number(counts.ops), 2);
    assert.equal(Number(counts.entries), 2);

    await assert.rejects(client.query(
      `select ops.commit_cost_ledger_operation($1::text,0::integer,'stale'::text,'op-1'::text,
         $2::text,'reserve'::text,'{}'::jsonb,null::text,1::integer,'x'::text,'[]'::jsonb)`,
      [treeRef, `sha256:${"c".repeat(64)}`]),
      /operation_id_reused_with_different_arguments/);
  });

test("MTR-CEILING-REFUSAL: a refusal writes ONE operation row, ZERO entries, and advances the version",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { treeRef } = await freshTree(c);

    const refused = await inTransaction(client, () => commitLedgerOperation({
      client: c, tree_ref: treeRef, step: reserve("op-over", "res-over", 500) }));
    assert.equal(refused.outcome.accepted, false);
    assert.equal(refused.outcome.reason_id, "ceiling_exceeded");

    const row = (await client.query(
      `select kind, refusal_reason_id, produced_version,
              (select count(*) from ops.cost_ledger_entry where tree_ref=$1) entries,
              (select ledger_version from ops.cost_ledger_cell where tree_ref=$1) version
         from ops.cost_ledger_operation where tree_ref=$1`, [treeRef])).rows[0];
    assert.equal(row.refusal_reason_id, "ceiling_exceeded");
    assert.equal(Number(row.entries), 0);
    assert.equal(Number(row.version), 1, "a refusal IS an applied operation");
  });

test("MTR-CEILING-REFUSAL: an overdrawn projection requires the module's own two remedies",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { treeRef } = await freshTree(c);
    await inTransaction(client, () => commitLedgerOperation({ client: c, tree_ref: treeRef,
      step: { kind: "record_late_liability", operation: { operation_id: "op-late",
        node_id: "slice", liability_id: "lia-1", amount_units: 400,
        vendor_reference: "VENDOR-LATE", incurred_at: "2026-09-03T00:00:00.000Z" } } }));
    const payload = await readMeteringProjection({
      client: c, actor: { slug: "joe" }, correlationId: "x", tree_ref: treeRef, period: null });
    assert.equal(payload.hierarchy_overdrawn, true);
    assert.deepEqual(payload.requires, [...V5_OVERDRAWN_REMEDIES],
      "an overdrawn projection publishes the module's own two remedies, not a message");
    const slice = await client.query(
      "select authorization_ceiling_units from ops.cost_scope_node where tree_ref=$1 and node_id='slice'",
      [treeRef]);
    assert.equal(Number(slice.rows[0].authorization_ceiling_units), 100);
  });

test("MTR-CONVERSION-TWO-ENTRIES: one operation row, two entry rows, one conversion id",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { treeRef } = await freshTree(c);
    await inTransaction(client, () =>
      commitLedgerOperation({ client: c, tree_ref: treeRef, step: reserve("op-1", "res-1", 40) }));
    await inTransaction(client, () => commitLedgerOperation({ client: c, tree_ref: treeRef,
      step: { kind: "convert_reservation_to_actual", operation: { operation_id: "op-2",
        conversion_id: "cv-1", reservation_id: "res-1", actual_amount_units: 55,
        vendor_reference: "VENDOR-C", settled_at: "2026-09-04T00:00:00.000Z" } } }));

    const entries = await client.query(
      `select sequence, kind, conversion_id, operation_id from ops.cost_ledger_entry
        where tree_ref=$1 and operation_id='op-2' order by sequence`, [treeRef]);
    assert.equal(entries.rows.length, 2, "a conversion emits TWO entries under ONE operation_id");
    assert.deepEqual(entries.rows.map(row => row.kind), ["reservation_release", "actual"]);
    assert.equal(new Set(entries.rows.map(row => row.conversion_id)).size, 1);
    assert.equal(Number(entries.rows[1].sequence) - Number(entries.rows[0].sequence), 1);
  });

test("MTR-DOCTORED-SUCCESSOR: a digest a direct authority caller invented is refused on the NEXT read",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { treeRef } = await freshTree(c);
    await client.query(
      `select ops.commit_cost_ledger_operation($1::text,0::integer,$2::text,'op-doctored'::text,
         $3::text,'reserve'::text,'{"accepted":true}'::jsonb,null::text,
         9::integer,'doctored-digest'::text,'[]'::jsonb)`,
      [treeRef,
       (await client.query("select state_digest from ops.cost_ledger_cell where tree_ref=$1",
         [treeRef])).rows[0].state_digest,
       `sha256:${"d".repeat(64)}`]);

    const payload = await readMeteringProjection({
      client: c, actor: { slug: "joe" }, correlationId: "x", tree_ref: treeRef, period: null });
    assert.equal(payload.coverage.complete, false);
    assert.equal(payload.coverage.sources[0].enumerable, false);
    assert.equal(payload.coverage.sources[0].reason_id, "operation_log_incomplete");
    assert.equal(payload.as_of.quantity, null, "the quantity is WITHHELD, never 0-imputed");

    await client.query("begin");
    await assert.rejects(
      commitLedgerOperation({ client: c, tree_ref: treeRef, step: reserve("op-after", "res-after", 5) }),
      error => error.code === "FRESHNESS_UNKNOWN");
    await client.query("rollback");
  });

test("MTR-CONSERVATION: the replayed ledger passes the law, and its proof numbers are the assertion",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { treeRef } = await freshTree(c);
    const steps = [
      reserve("op-1", "res-1", 40),
      { kind: "post_actual", operation: { operation_id: "op-2", node_id: "child",
        amount_units: 30, vendor_reference: "VENDOR-1", incurred_at: "2026-09-02T00:00:00.000Z" } },
    ];
    for (const step of steps) {
      await inTransaction(client, () => commitLedgerOperation({ client: c, tree_ref: treeRef, step }));
    }
    const payload = await readMeteringProjection({
      client: c, actor: { slug: "joe" }, correlationId: "x", tree_ref: treeRef, period: null });
    assert.equal(payload.coverage.complete, true);

    const stored = (await client.query(
      `select sequence, entry_id, amount_units from ops.cost_ledger_entry
        where tree_ref = $1 order by sequence`, [treeRef])).rows;
    const replayed = applyOperations(openLedger(compileScopeTree(TREE)), steps).ledger;
    assert.deepEqual(replayed.entries.map(entry => entry.entry_id), stored.map(row => row.entry_id));

    const proof = assertLedgerConservation(replayed);
    assert.deepEqual(proof, {
      entry_count: 2,
      reservation_ids: ["res-1"],
      liability_ids: [],
      open_reservation_units: 40,
      open_liability_units: 0,
      portfolio_actual_units: 30,
      portfolio_committed_units: 70,
      conversion_ids: [],
    });
    assert.equal(proof.entry_count, stored.length);
    assert.equal(proof.open_reservation_units,
      Number(stored.find(row => row.entry_id === replayed.entries[0].entry_id).amount_units));
  });

test("MTR-CONSERVATION: mutating the NAMED stored amount makes the law throw", async t => {
  const honest = applyOperations(openLedger(compileScopeTree(TREE)),
    [reserve("op-1", "res-1", 40)]).ledger;
  assert.equal(assertLedgerConservation(honest).open_reservation_units, 40);

  const namedEntryId = honest.entries[0].entry_id;
  const tampered = structuredClone(honest);
  tampered.entries[0].amount_units = 41;
  assert.equal(tampered.entries[0].entry_id, namedEntryId);
  assert.throws(() => assertLedgerConservation(tampered), error => {
    assert.equal(error.code, "conservation_violated");
    assert.equal(error.message,
      "the outstanding reservations derived from the log do not match the open reservation index");
    assert.deepEqual(error.detail, { derived: 41, index: 40 });
    return true;
  });

  assert.equal(assertLedgerConservation(honest).open_reservation_units, 40);
});

test("MTR-CONSERVATION (supporting): the stored entry log refuses an UPDATE outright",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { treeRef } = await freshTree(c);
    await inTransaction(client, () =>
      commitLedgerOperation({ client: c, tree_ref: treeRef, step: reserve("op-1", "res-1", 40) }));
    await assert.rejects(
      client.query(
        "update ops.cost_ledger_entry set amount_units = 41 where tree_ref=$1 and entry_id='entry:00000000'",
        [treeRef]),
      /immutable/);
  });

test("MTR-CAP-NOT-USAGE: a cap is never usage, and an unenumerable source withholds rather than zeroes",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { treeRef } = await freshTree(c);

    const empty = await readMeteringProjection({
      client: c, actor: { slug: "joe" }, correlationId: "x", tree_ref: treeRef, period: null });
    assert.equal(empty.as_of.quantity, 0);
    assert.ok(empty.policy.cap_units > 0, "the cap is published");
    assert.equal(empty.charge_projection.units, 0, "the cap is NOT folded into the projection");
    assert.equal(empty.coverage.complete, true);
    assert.equal(empty.coverage.sources.length, 1);
    assert.equal(empty.coverage.sources[0].enumerable, true);
  });


const estimateSlice = units => ({
  kind: "record_estimate",
  operation: { operation_id: "op-estimate", node_id: "slice", expected_total_cost_units: units,
    basis_digest: `sha256:${"a".repeat(64)}`, recorded_at: "2026-09-01T00:00:00.000Z" },
});

const actual = (id, units) => ({
  kind: "post_actual",
  operation: { operation_id: id, node_id: "slice", amount_units: units,
    vendor_reference: `INV-${id}`, incurred_at: "2026-09-01T00:00:00.000Z" },
});

const sliceSignals = signals => signals.filter(s => s.node_id === "slice").map(s => s.kind).sort();

test("A04-SIGNAL-DURABLE: a committed crossing fires once and its replay fires nothing", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const c = wrap(client);
  const { treeRef } = await freshTree(c);

  const commit = step => inTransaction(client, () => commitLedgerOperation({ client: c, tree_ref: treeRef, step }));
  assert.deepEqual((await commit(estimateSlice(100))).signals, []);
  assert.deepEqual((await commit(actual("op-a", 149))).signals, []);
  const crossed = await commit(actual("op-b", 1));
  assert.deepEqual(sliceSignals(crossed.signals), ["warning"]);
  assert.equal(crossed.signals.find(s => s.node_id === "slice").ledger_version,
    Number(crossed.result.ledger_version));
  assert.equal(crossed.signals.find(s => s.node_id === "slice").signal_id,
    `budget-signal:${treeRef}:slice:warning:v${crossed.result.ledger_version}`,
    "a stored signal is scoped by the unique tree_ref, not the shareable tree_id");

  const replay = await commit(actual("op-b", 1));
  assert.equal(replay.outcome.replayed, true);
  assert.deepEqual(replay.signals, [], "a retry of the crossing operation fires nothing");

  const ceiling = await commit(reserve("op-too-big", "res-too-big", 9999));
  assert.equal(ceiling.outcome.accepted, false);
  assert.deepEqual(ceiling.signals, [], "a refusal moves no money and fires nothing");
});

test("A04-SIGNAL-RACE: two connections racing one crossing under the row lock fire it once", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const owner = await connect(pg);
  const first = await connect(pg);
  const second = await connect(pg);
  t.after(() => Promise.all([owner, first, second].map(x => x.end().catch(() => {}))));
  const { treeRef } = await freshTree(wrap(owner));
  const o = wrap(owner);
  await inTransaction(owner, () => commitLedgerOperation({ client: o, tree_ref: treeRef, step: estimateSlice(100) }));
  await inTransaction(owner, () => commitLedgerOperation({ client: o, tree_ref: treeRef, step: actual("op-base", 130) }));

  await first.query("begin");
  const firstResult = await commitLedgerOperation({ client: wrap(first), tree_ref: treeRef, step: actual("op-first", 30) });
  await second.query("begin");
  let secondDone = false;
  const secondPromise = commitLedgerOperation({ client: wrap(second), tree_ref: treeRef, step: actual("op-second", 30) })
    .then(value => { secondDone = true; return value; });
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.equal(secondDone, false, "the second writer must be waiting on the row lock");
  await first.query("commit");
  const secondResult = await secondPromise;
  await second.query("commit");

  assert.deepEqual(sliceSignals(firstResult.signals), ["warning"]);
  assert.deepEqual(secondResult.signals, [], "190 percent is the same crossing; it does not fire again");
  assert.equal(Number(secondResult.result.ledger_version), Number(firstResult.result.ledger_version) + 1);
});

test("A04-RESERVE-RACE: two connections racing reservations past the ceiling admit one", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const owner = await connect(pg);
  const first = await connect(pg);
  const second = await connect(pg);
  t.after(() => Promise.all([owner, first, second].map(x => x.end().catch(() => {}))));
  const { treeRef } = await freshTree(wrap(owner));

  await first.query("begin");
  const firstResult = await commitLedgerOperation({ client: wrap(first), tree_ref: treeRef, step: reserve("op-r1", "res-r1", 60) });
  await second.query("begin");
  const secondPromise = commitLedgerOperation({ client: wrap(second), tree_ref: treeRef, step: reserve("op-r2", "res-r2", 60) });
  await first.query("commit");
  const secondResult = await secondPromise;
  await second.query("commit");

  assert.equal(firstResult.outcome.accepted, true);
  assert.equal(secondResult.outcome.accepted, false);
  assert.equal(secondResult.outcome.reason_id, "ceiling_exceeded");
  assert.equal(secondResult.outcome.available_units, 40);
  const open = await owner.query(
    "select reservation_id from ops.cost_ledger_entry where tree_ref=$1 and kind='reservation'", [treeRef]);
  assert.deepEqual(open.rows.map(r => r.reservation_id), ["res-r1"]);
});

test("A04-OVERDRAWN-DURABLE: an overdrawn stored hierarchy refuses a new reservation and keeps the old one open",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { treeRef } = await freshTree(c);
    const commit = step => inTransaction(client, () => commitLedgerOperation({ client: c, tree_ref: treeRef, step }));

    assert.equal((await commit(reserve("op-keep", "res-keep", 40))).outcome.accepted, true);
    await commit(actual("op-over", 200));
    const denied = await commit(reserve("op-new", "res-new", 1));
    assert.equal(denied.outcome.reason_id, "ancestor_overdrawn");
    assert.deepEqual(denied.outcome.overdrawn_node_ids, ["slice", "child", "root"]);
    assert.deepEqual(denied.outcome.overdrawn_caused_by_node_ids, ["slice"]);
    assert.match(denied.outcome.detail, /Nothing already reserved is changed/);
    const stored = await client.query(
      "select refusal_reason_id from ops.cost_ledger_operation where tree_ref=$1 and operation_id='op-new'", [treeRef]);
    assert.equal(stored.rows[0].refusal_reason_id, "ancestor_overdrawn");

    const cancelled = await commit({ kind: "cancel_reservation",
      operation: { operation_id: "op-cancel-keep", reservation_id: "res-keep",
        cancelled_at: "2026-09-01T00:00:00.000Z" } });
    assert.equal(cancelled.outcome.accepted, true,
      "the reservation that existed before the overdraw still cancels");
  });
