// salesforce-reconciliation-rw02-postgres.test.mjs — the V5-RW02 evidence
// store's replay and write-time refusal paths on REAL PostgreSQL.
//
// WHY THIS FILE EXISTS. The store's FakeDb answered every replay lookup with
// null, so no test ever reached the replay branch: on real PostgreSQL a replay
// of record-salesforce-page-stop came back with decision undefined, and a key
// reused over a different request surfaced as a raw P0001. And no test ever
// filed a malformed readback as the database principal: one was accepted, and
// because a row cannot be removed, every later evidence read of that action
// threw. This suite runs the MCP verb and the store as carr_writer against a
// disposable template copy of the migrated database and pins both.
//
// Skips in the unit class. The migration class supplies DATABASE_URL and sets
// CARR_RW02_DB_REQUIRED=1, which turns a silent skip into a failure. It
// commits only into a template copy it creates and removes.
//
// EVERY RECORD IS SYNTHETIC.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { TOOLS, ToolError } from "../src/tools.js";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5RW02StoreError,
  createSalesforceReconciliationStore,
} from "../src/salesforce-reconciliation-store-rw02.v5.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_RW02_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;
const JOE = { slug: "joe", display: "Joe", human: true, via: "mcp", client_id: "rw02-postgres-test" };
const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;

function dsnFor(database) {
  const url = new URL(DSN);
  url.pathname = `/${database}`;
  return url.toString();
}

async function clientFor(pg, database, role = null) {
  const client = new pg.Client({ connectionString: dsnFor(database) });
  await client.connect();
  if (role) await client.query(`SET SESSION AUTHORIZATION ${role}`);
  return client;
}

/** One unit of work exactly as mcp.js runs a verb: its own transaction on the
 * writer's connection, acting actor set transaction-locally. */
async function asWriter(pg, database, fn) {
  const client = await clientFor(pg, database, "carr_writer");
  try {
    const a = await client.query("select id from public.actor where slug=$1", [JOE.slug]);
    const actor = { ...JOE, id: a.rows[0].id };
    await client.query("BEGIN");
    await client.query("select set_config('carr.acting_actor_slug',$1::text,true)", [JOE.slug]);
    try {
      const out = await fn(client, actor);
      await client.query("COMMIT");
      return out;
    } catch (e) {
      await client.query("ROLLBACK").catch(() => {});
      throw e;
    }
  } finally {
    await client.end();
  }
}

const verb = (pg, database, name, args) =>
  asWriter(pg, database, (client, actor) => TOOLS[name].handler(client, actor, args));

function storeOn(client, evaluators = {}) {
  return createSalesforceReconciliationStore({ evaluators,
    db: { query: (...a) => client.query(...a), transaction: fn => fn(client) } });
}

const page = (challenge = "mfa_challenge") => ({
  execution_mode: "attended",
  binding: { expected_origin: "https://synthetic-org.invalid", expected_org_id: "00Dsynthetic0001",
    expected_account_ref: "sf-seat-synthetic-partner", expected_ui_contract_digest: D(1) },
  observation: { origin: "https://synthetic-org.invalid", org_id: "00Dsynthetic0001",
    signed_in_account_ref: "sf-seat-synthetic-partner", ui_contract_digest: D(1),
    challenge, result_consistency: "consistent" },
});

function sealed(over = {}) {
  const fields = { schema_version: "doctorcre-v5-rw02-action-evidence.v1",
    tenant: ORGANIZATION_TENANT_ID, action_kind: "opportunity_create",
    step_key: "rw02-pg-step-1", preview_digest: D(2), envelope_digest: D(3),
    evidence_class: "fixture", observed_at: "2026-09-26T04:59:00Z", outcome: "exact_match",
    readback_digest: D(4), ...over };
  return { ...fields, evidence_digest: digest({ kind: "rw02-evidence.v1", ...fields }) };
}

const readbackEvaluator = evidence => ({
  evaluateWriteReadback: () => ({ decision: "confirmed", reason_id: "exact_readback",
    action_kind: evidence.action_kind, step_key: evidence.step_key, evidence,
    outward_effect_granted: false, autonomy_active: false }),
});

test("V5-RW02 evidence store replay and write-time refusals on real PostgreSQL", async t => {
  if (!DSN) {
    assert.equal(REQUIRED, false, "the V5-RW02 database proof is required but DATABASE_URL is unset");
    return t.skip("the migration class supplies DATABASE_URL");
  }
  assert.match(DSN, LOOPBACK, "the V5-RW02 database proof runs only against a loopback disposable database");
  const pg = (await import("pg")).default;
  const source = new URL(DSN).pathname.replace(/^\//, "");
  const admin = await clientFor(pg, "postgres");
  const copy = `rw02_pg_${process.pid}_${randomUUID().slice(0, 8)}`;
  let db;
  try {
    await admin.query(`CREATE DATABASE ${copy} TEMPLATE ${source}`);
    db = await clientFor(pg, copy);
    const applied = await db.query(
      "select 1 from public.schema_migrations where filename = '0726_salesforce_reconciliation_rw02_store.sql'");
    assert.equal(applied.rows.length, 1, "migration 0726 is applied as the numbered file");
    await db.query(
      `insert into public.actor (slug, kind, display_name, active) values ('joe', 'human', 'joe', true)
       on conflict (slug) do nothing`);
    const rows = async () => Number((await db.query(
      "select count(*) from ops.rw02_runtime_record")).rows[0].count);

    await t.test("a store replay answers exactly what the first call answered", async () => {
      // The store's own replay branch: reached whenever the store runs outside
      // the MCP envelope's tool_call replay (and on a replay the envelope has
      // no row for). It used to return the raw stored row, decision undefined.
      const args = { idempotency_key: `rw02-pg-page-${randomUUID()}`, page: page() };
      const call = () => asWriter(pg, copy, (client, actor) =>
        storeOn(client).recordPageStop(args, { actor }));
      const first = await call();
      assert.equal(first.decision, "recorded");
      assert.equal(first.reason_id, "runtime_observation_recorded");
      assert.equal(first.evaluation.decision, "stop");
      assert.equal(first.evaluation.reason_id, "authentication_challenge");
      assert.match(first.record_digest, /^sha256:[0-9a-f]{64}$/);
      assert.equal(first.replayed, false);
      const before = await rows();
      const replay = await call();
      assert.equal(await rows(), before, "a replay writes nothing");
      assert.equal(replay.replayed, true);
      assert.equal(replay.effects.database_writes, 0);
      const strip = ({ replayed: _r, effects: _e, ...rest }) => rest;
      assert.deepEqual(strip(replay), strip(first));
      assert.deepEqual(Object.keys(replay).sort(), Object.keys(first).sort());
      assert.deepEqual({ ...replay.effects, database_writes: first.effects.database_writes },
        first.effects);
    });

    await t.test("through the MCP verb a replay returns the first response byte for byte", async () => {
      const args = { idempotency_key: `rw02-pg-verb-${randomUUID()}`, page: page() };
      const first = await verb(pg, copy, "record-salesforce-page-stop", args);
      assert.equal(first.decision, "recorded");
      const before = await rows();
      const replay = await verb(pg, copy, "record-salesforce-page-stop", args);
      assert.equal(await rows(), before);
      assert.deepEqual(replay, first);
    });

    await t.test("a key reused over a different request is a typed conflict", async () => {
      const key = `rw02-pg-conflict-${randomUUID()}`;
      await asWriter(pg, copy, (client, actor) => storeOn(client).recordPageStop(
        { idempotency_key: key, page: page("mfa_challenge") }, { actor }));
      const before = await rows();
      await assert.rejects(
        asWriter(pg, copy, (client, actor) => storeOn(client).recordPageStop(
          { idempotency_key: key, page: page("captcha") }, { actor })),
        e => e instanceof V5RW02StoreError && e.code === "idempotency_conflict");
      assert.equal(await rows(), before);
      // Through the MCP verb the envelope refuses first, with its own typed code.
      const verbKey = `rw02-pg-verb-conflict-${randomUUID()}`;
      await verb(pg, copy, "record-salesforce-page-stop", { idempotency_key: verbKey, page: page("mfa_challenge") });
      await assert.rejects(
        verb(pg, copy, "record-salesforce-page-stop", { idempotency_key: verbKey, page: page("captcha") }),
        e => e instanceof ToolError && e.payload.error === "key_reuse");
    });

    await t.test("a malformed readback is refused at write time and the read keeps working", async () => {
      const good = sealed();
      const recorded = await asWriter(pg, copy, (client, actor) =>
        storeOn(client, readbackEvaluator(good)).recordWriteReadback(
          { idempotency_key: `rw02-pg-readback-${randomUUID()}`, observation: { synthetic: true } },
          { actor }));
      assert.equal(recorded.decision, "recorded");
      const before = await rows();
      for (const [label, evidence] of [
        ["unsealed", { ...good, evidence_digest: D(9) }],
        ["field edited after sealing", { ...good, outcome: "mismatch" }],
        ["unknown key", { ...sealed(), note: "x" }],
        ["no such day", sealed({ observed_at: "2026-02-30T05:00:00Z" })],
      ]) {
        await assert.rejects(
          asWriter(pg, copy, (client, actor) =>
            storeOn(client, readbackEvaluator(evidence)).recordWriteReadback(
              { idempotency_key: `rw02-pg-poison-${randomUUID()}`, observation: { synthetic: label } },
              { actor })),
          e => e instanceof V5RW02StoreError && e.code === "evidence_invalid", label);
      }
      // The same sample under a new key would make every later window refuse.
      await assert.rejects(
        asWriter(pg, copy, (client, actor) =>
          storeOn(client, readbackEvaluator(good)).recordWriteReadback(
            { idempotency_key: `rw02-pg-again-${randomUUID()}`, observation: { synthetic: "again" } },
            { actor })),
        e => e instanceof V5RW02StoreError && e.code === "evidence_counted_twice");
      assert.equal(await rows(), before, "no refused readback landed");
      const read = await verb(pg, copy, "read-salesforce-action-evidence", { action_kind: "opportunity_create" });
      assert.equal(read.decision, "window_read");
      assert.equal(read.trust_scope, "per_action");
      assert.equal(read.evidence_total, 1);
      assert.equal(read.evidence_authenticated, false);
      // Live 2026-09-27: the Worker runs a read verb on its READER login, where
      // the database principal is 'carr-reader' and the handler actor is the
      // partner. The read must answer there, exactly as it does on the writer.
      const reader = await clientFor(pg, copy, "carr_reader");
      try {
        const actorId = (await reader.query("select id from public.actor where slug=$1", [JOE.slug])).rows[0].id;
        const onReader = await TOOLS["read-salesforce-action-evidence"].handler(reader, { ...JOE, id: actorId },
          { action_kind: "opportunity_create" });
        assert.equal(onReader.decision, "window_read");
        assert.equal(onReader.evidence_total, 1);
        assert.equal(onReader.evidence_authenticated, false);
      } finally { await reader.end(); }
    });
  } finally {
    if (db) await db.end();
    await admin.query(`DROP DATABASE IF EXISTS ${copy} WITH (FORCE)`).catch(() => {});
    await admin.end();
  }
});
