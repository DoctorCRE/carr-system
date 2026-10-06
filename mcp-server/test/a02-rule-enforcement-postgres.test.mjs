// a02-rule-enforcement-postgres.test.mjs — V5-A02 rule-enforcement coverage
// and its fallback writer on REAL PostgreSQL.
//
// WHY THIS FILE EXISTS. The first version of this slice shipped a stub FakeDb
// suite plus greps of the migration text. No test ever executed
// ops.v5_a02_rule_enforcement_coverage() or ops.record_rule_enforcement_fallback,
// so a planted bug in either one survived every test. This suite seeds real
// rule, approval, enforcement-point, catalog, binding and fallback rows into a
// disposable copy of the migrated database, calls the two functions (and the
// two MCP verbs over them) as the real database principals, and asserts the
// exact per-rule reason for every leg of the guard. Each scenario below names
// the planted SQL mutant it exists to kill.
//
// Skips in the unit class. The migration class supplies DATABASE_URL and sets
// CARR_A02_RULE_COVERAGE_DB_REQUIRED=1, which turns a silent skip into a
// failure. It commits only into a template copy it creates and drops.
//
// EVERY RECORD IS SYNTHETIC.

import test from "node:test";
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";

import { TOOLS, ToolError } from "../src/tools.js";
import { readRuleEnforcementCoverage } from "../src/lifecycle-assurance.v5.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_A02_RULE_COVERAGE_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

const sha = text => createHash("sha256").update(text, "utf8").digest("hex");

// One rule per leg of the guard. Ids are fixed: the copy is fresh per run.
const R = Object.freeze({
  covered:            "a0200001-0000-4000-8000-000000000000",
  unmapped:           "a0200002-0000-4000-8000-000000000000",
  bindingStale:       "a0200003-0000-4000-8000-000000000000", // S9
  amendedVersion:     "a0200004-0000-4000-8000-000000000000", // S4, ruling 5a
  amendedStatement:   "a0200005-0000-4000-8000-000000000000", // ruling 5a
  testsMissing:       "a0200006-0000-4000-8000-000000000000", // S5
  futureDated:        "a0200007-0000-4000-8000-000000000000", // item 3
  changedSinceApproval: "a0200008-0000-4000-8000-000000000000", // correction 1
  fallbackAbsent:     "a0200009-0000-4000-8000-000000000000", // S10
  fallbackOldVersion: "a020000a-0000-4000-8000-000000000000", // S2
  fallbackOldHash:    "a020000b-0000-4000-8000-000000000000", // S3
  proposed:           "a020000c-0000-4000-8000-000000000000", // S6
  writerTarget:       "a020000d-0000-4000-8000-000000000000", // S1, S8, writer paths
  oneOfTwoInstalled:  "a020000e-0000-4000-8000-000000000000", // probe E, S20
  oneOfTwoUnverified: "a020000f-0000-4000-8000-000000000000", // N7
  oneOfTwoChanged:    "a0200010-0000-4000-8000-000000000000", // N8
  catalogMovedEarlier: "a0200011-0000-4000-8000-000000000000", // "changed" means either direction
});

const EXPECTED_GAPS = Object.freeze({
  [R.unmapped]: "active_rule_control_unmapped",
  [R.bindingStale]: "active_rule_control_unmapped",
  [R.amendedVersion]: "active_rule_amended_needs_reapproval",
  [R.amendedStatement]: "active_rule_amended_needs_reapproval",
  [R.testsMissing]: "rule_tests_not_passing",
  [R.futureDated]: "rule_test_evidence_future_dated",
  [R.changedSinceApproval]: "rule_test_evidence_changed_since_approval",
  [R.oneOfTwoInstalled]: "active_rule_approved_control_not_installed",
  [R.oneOfTwoUnverified]: "rule_tests_not_passing",
  [R.oneOfTwoChanged]: "rule_test_evidence_changed_since_approval",
  [R.catalogMovedEarlier]: "rule_test_evidence_changed_since_approval",
  [R.fallbackAbsent]: "active_rule_fallback_absent",
  [R.fallbackOldVersion]: "active_rule_fallback_absent",
  [R.fallbackOldHash]: "active_rule_fallback_absent",
  [R.writerTarget]: "active_rule_fallback_absent",
});

const JOE = { slug: "joe", display: "Joe", human: true, via: "mcp", client_id: "a02-postgres-test" };
const DELL = { slug: "dell", display: "Dell", human: true, via: "mcp", client_id: "a02-postgres-test" };

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

/** Seed one rule and whichever legs of its evidence the scenario names. */
async function seedRule(db, ctx, id, spec) {
  const statement = spec.statement ?? `a02 synthetic rule ${id}`;
  const version = spec.version ?? 1;
  await db.query(
    `insert into public.rule (id, statement, taught_by, status, activated_by, activated_at, version)
     values ($1, $2, $3, $4, case when $4 = 'active' then $3::uuid end,
             case when $4 = 'active' then now() end, $5)`,
    [id, statement, ctx.joeId, spec.status ?? "active", version]);
  if (spec.approval === false) return;
  const control = `a02_pg_ctl_${id.slice(0, 8)}`;
  const approvedStatement = spec.approvedStatement ?? statement;
  const approvedVersion = spec.approvedVersion ?? version;
  const verified = spec.verifiedAt ?? "now() - interval '1 hour'";
  await db.query(
    `insert into ops.enforcement_control_catalog
       (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
     values ($1, 'synthetic/impl', 'synthetic/test', 'deny_gate', true,
             ${spec.catalogVerifiedAt ?? verified})`,
    [control]);
  const controls = [control];
  if (spec.second) {
    // Two approved controls; the first is fully good, the second carries the
    // one defect the scenario names, so only an "every control" check sees it.
    // uninstalled: probe E. pointVerifiedNull: installed but unverified (N7).
    // catalogVerifiedAt: installed and verified, but changed since approval (N8).
    const second = `${control}_b`;
    const installed = !spec.second.uninstalled;
    controls.push(second);
    await db.query(
      `insert into ops.enforcement_control_catalog
         (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
       values ($1, 'synthetic/impl', 'synthetic/test', 'deny_gate', $2,
               ${installed ? (spec.second.catalogVerifiedAt ?? verified) : "null"})`, [second, installed]);
    await db.query(
      `insert into ops.rule_enforcement_point
         (rule_id, control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
       values ($1, $2, 'synthetic/impl', 'synthetic/test', 'deny_gate', $3,
               ${installed && !spec.second.pointVerifiedNull ? verified : "null"})`, [id, second, installed]);
    await db.query(
      `insert into ops.rule_control_binding (rule_id, control_key, statement_hash, binding_contract)
       values ($1, $2, $3, '{}'::jsonb)`, [id, second, sha(statement)]);
  }
  await db.query(
    `insert into ops.rule_enforcement_point
       (rule_id, control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
     values ($1, $2, 'synthetic/impl', 'synthetic/test', 'deny_gate', true,
             ${spec.pointVerifiedNull ? "null" : verified})`,
    [id, control]);
  await db.query(
    `insert into ops.rule_control_binding (rule_id, control_key, statement_hash, binding_contract)
     values ($1, $2, $3, '{}'::jsonb)`,
    [id, control, spec.staleBinding ? "0".repeat(64) : sha(statement)]);
  await db.query(
    `insert into ops.rule_approval_receipt
       (idempotency_key, rule_id, rule_version, statement_hash, actor_id, policy_kind,
        enforcement_status, requested_control_keys, installed_control_keys, reason,
        normalized_contract, contract_hash, evidence_refs, created_at)
     values ($1, $2, $3, $4, $5, 'machine_enforceable', 'hard_enforced', $6::text[], $6::text[],
             'synthetic approval', '{}'::jsonb,
             encode(public.digest('{}'::jsonb::text, 'sha256'), 'hex'), array['synthetic'],
             now() - interval '2 hours')`,
    [`a02-pg-approval-${id}`, id, approvedVersion, sha(approvedStatement), ctx.joeId, controls]);
  if (spec.fallback === false) return;
  const fallbackStatement = spec.fallbackStatement ?? statement;
  await db.query(
    `insert into ops.rule_enforcement_fallback_receipt
       (idempotency_key, rule_id, rule_version, statement_hash, fallback_kind,
        procedure_ref, reason, recorded_by, recorded_by_slug)
     values ($1, $2, $3, $4, 'refuse_closed', 'synthetic/procedure', 'synthetic fallback', $5, 'joe')`,
    [`a02-pg-fallback-${id}`, id, spec.fallbackVersion ?? version, sha(fallbackStatement), ctx.joeId]);
}

async function coverage(db) {
  const r = await db.query("select ops.v5_a02_rule_enforcement_coverage() as c");
  return r.rows[0].c;
}

function gapsByRule(record) {
  return Object.fromEntries(record.gaps.map(g => [g.rule_id, g.reason_id]));
}

async function refusedWithMessage(promise, message) {
  await assert.rejects(promise, e => {
    assert.equal(e.message, message, `expected named refusal ${message}, got ${e.code} ${e.message}`);
    return true;
  });
}

async function refusedWithCode(promise, code) {
  await assert.rejects(promise, e => {
    assert.equal(e.code, code, `expected SQLSTATE ${code}, got ${e.code} ${e.message}`);
    return true;
  });
}

/** One verb call exactly as mcp.js runs it: its own transaction on the
 * principal's own connection, acting actor set transaction-locally. */
async function callVerb(pg, database, role, actor, verb, args) {
  const client = await clientFor(pg, database, role);
  try {
    const a = await client.query("select id from public.actor where slug=$1", [actor.slug]);
    const full = { ...actor, id: a.rows[0].id };
    await client.query("BEGIN");
    await client.query("select set_config('carr.acting_actor_slug',$1::text,true)", [actor.slug]);
    try {
      const out = await TOOLS[verb].handler(client, full, args);
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

test("V5-A02 coverage and fallback writer on real PostgreSQL", async t => {
  if (!DSN) {
    assert.equal(REQUIRED, false, "the V5-A02 database proof is required but DATABASE_URL is unset");
    return t.skip("the migration class supplies DATABASE_URL");
  }
  assert.match(DSN, LOOPBACK, "the V5-A02 database proof runs only against a loopback disposable database");
  const pg = (await import("pg")).default;
  const source = new URL(DSN).pathname.replace(/^\//, "");
  const admin = await clientFor(pg, "postgres");
  const copy = `a02_cov_${process.pid}_${randomUUID().slice(0, 8)}`;
  let db;
  try {
    const su = await admin.query("select rolsuper from pg_roles where rolname = current_user");
    assert.equal(su.rows[0]?.rolsuper, true,
      "the proof needs a superuser to SET SESSION AUTHORIZATION to the real principals");
    for (const role of ["carr_authority_joe", "carr_authority_dell"]) {
      const exists = await admin.query("select 1 from pg_roles where rolname = $1", [role]);
      if (!exists.rows.length) await admin.query(`CREATE ROLE ${role} LOGIN`);
      await admin.query(`GRANT carr_authority TO ${role}`);
    }
    await admin.query(`CREATE DATABASE ${copy} TEMPLATE ${source}`);
    db = await clientFor(pg, copy);

    const applied = await db.query(
      "select 1 from public.schema_migrations where filename = '0721_a02_rule_enforcement_coverage.sql'");
    assert.equal(applied.rows.length, 1, "migration 0721 is applied as the numbered file");

    // Isolation: whatever active rules the snapshot carries are retired in
    // this copy, so every count below is exact.
    await db.query("begin");
    await db.query("set local session_replication_role = replica");
    await db.query("update public.rule set status = 'retired' where status = 'active'");
    for (const slug of ["joe", "dell"])
      await db.query(
        `insert into public.actor (slug, kind, display_name, active) values ($1, 'human', $1, true)
         on conflict (slug) do nothing`, [slug]);
    await db.query("commit");
    const ctx = { joeId: (await db.query("select id from public.actor where slug='joe'")).rows[0].id };

    await t.test("ruling 5b: zero active rules reads `empty`, never complete", async () => {
      const record = await coverage(db);
      assert.equal(record.active_rule_count, 0);
      assert.equal(record.coverage_state, "empty");
      assert.equal(record.coverage_complete, false);
      const reader = await clientFor(pg, copy, "carr_reader");
      try {
        const read = await TOOLS["read-v5-a02-rule-enforcement-coverage"].handler(reader, null, {});
        assert.equal(read.status, "available");
        assert.equal(read.coverage_state, "empty");
        assert.equal(read.coverage_complete, false);
      } finally {
        await reader.end();
      }
    });

    await db.query("begin");
    await db.query("set local session_replication_role = replica");
    // Correction 1: evidence verified long BEFORE the approval (what 0228's
    // copy of the catalog value produces) is current, not a gap.
    await seedRule(db, ctx, R.covered, { verifiedAt: "now() - interval '3 days'" });
    await seedRule(db, ctx, R.unmapped, { approval: false });
    await seedRule(db, ctx, R.bindingStale, { staleBinding: true });
    await seedRule(db, ctx, R.amendedVersion, { version: 2, approvedVersion: 1 });
    await seedRule(db, ctx, R.amendedStatement, {
      version: 2, statement: "a02 amended statement B", approvedVersion: 1,
      approvedStatement: "a02 original statement A" });
    await seedRule(db, ctx, R.testsMissing, { pointVerifiedNull: true });
    await seedRule(db, ctx, R.futureDated, { verifiedAt: "now() + interval '1 day'" });
    // The approval captured one verified_at on the enforcement point; the
    // catalog control now carries a different one.
    await seedRule(db, ctx, R.changedSinceApproval, {
      verifiedAt: "now() - interval '3 days'", catalogVerifiedAt: "now() - interval '1 hour'" });
    await seedRule(db, ctx, R.oneOfTwoInstalled, { second: { uninstalled: true } });
    await seedRule(db, ctx, R.oneOfTwoUnverified, { second: { pointVerifiedNull: true } });
    await seedRule(db, ctx, R.oneOfTwoChanged, {
      second: { catalogVerifiedAt: "now() - interval '10 minutes'" } });
    // The catalog now carries an EARLIER verified_at than the approval
    // captured. Every other "changed" case moves it later, so a check that
    // only asked "is the catalog newer?" would pass them all; this one is
    // the case that pins "distinct from", in either direction.
    await seedRule(db, ctx, R.catalogMovedEarlier, {
      verifiedAt: "now() - interval '1 hour'", catalogVerifiedAt: "now() - interval '5 days'" });
    await seedRule(db, ctx, R.fallbackAbsent, { fallback: false });
    await seedRule(db, ctx, R.fallbackOldVersion, { version: 2, fallbackVersion: 1 });
    await seedRule(db, ctx, R.fallbackOldHash, {
      statement: "a02 current statement B", fallbackStatement: "a02 earlier statement A" });
    await seedRule(db, ctx, R.proposed, { status: "proposed" });
    await seedRule(db, ctx, R.writerTarget, { fallback: false });
    await db.query("commit");

    await t.test("every leg of the guard reports its exact named gap (S2 S3 S4 S5 S6 S9 S10 S20 N7 N8, rulings 5a, corrections 1-2)", async () => {
      const record = await coverage(db);
      assert.equal(record.schema_version, "doctorcre-v5-a02-rule-enforcement-coverage.v2");
      assert.deepEqual(gapsByRule(record), EXPECTED_GAPS);
      // S6: the proposed rule is never counted, covered or not.
      assert.equal(record.active_rule_count, 16);
      assert.equal(record.covered_rule_count, 1);
      assert.equal(record.gap_count, 15);
      assert.equal(record.coverage_state, "gaps");
      assert.equal(record.coverage_complete, false);
      for (const gap of record.gaps) assert.ok(gap.detail.trim().length > 0);
    });

    await t.test("the JS reader validates the real record and reports it", async () => {
      const reader = await clientFor(pg, copy, "carr_reader");
      try {
        const read = await readRuleEnforcementCoverage(reader);
        assert.equal(read.status, "available", JSON.stringify(read));
        assert.equal(read.coverage_state, "gaps");
        assert.equal(read.gap_count, 15);
        assert.deepEqual(Object.fromEntries(read.gaps.map(g => [g.rule_id, g.reason_id])), EXPECTED_GAPS);
      } finally {
        await reader.end();
      }
    });

    const call = (role, key, overrides = {}) => async () => {
      const client = await clientFor(pg, copy, role);
      try {
        const r = await client.query(
          "select ops.record_rule_enforcement_fallback($1,$2,$3,$4,$5) as r",
          [overrides.rule_id ?? R.writerTarget, overrides.kind ?? "refuse_closed",
           "runbooks/synthetic-procedure.md", key, "synthetic reason"]);
        return r.rows[0].r;
      } finally {
        await client.end();
      }
    };
    const receiptRows = async ruleId =>
      (await db.query("select count(*)::int as n from ops.rule_enforcement_fallback_receipt where rule_id=$1",
        [ruleId])).rows[0].n;

    await t.test("writer and reader cannot execute the fallback writer", async () => {
      await refusedWithCode(call("carr_writer", "a02-writer")(), "42501");
      await refusedWithCode(call("carr_reader", "a02-reader")(), "42501");
      assert.equal(await receiptRows(R.writerTarget), 0);
    });

    await t.test("S1: Dell's authority connection is refused by name, before any CHECK", async () => {
      await refusedWithMessage(call("carr_authority_dell", "a02-dell")(),
        "rule_enforcement_fallback_requires_joe_authority");
      assert.equal(await receiptRows(R.writerTarget), 0);
    });

    let first;
    await t.test("Joe's authority connection records the exact current rule version", async () => {
      first = await call("carr_authority_joe", "a02-joe-1")();
      assert.equal(first.rule_id, R.writerTarget);
      assert.equal(first.rule_version, 1);
      assert.equal(first.recorded_by, "joe");
      assert.equal(first.fallback_kind, "refuse_closed");
      const row = await db.query(
        "select statement_hash from ops.rule_enforcement_fallback_receipt where id=$1", [first.receipt_id]);
      assert.equal(row.rows[0].statement_hash, sha(`a02 synthetic rule ${R.writerTarget}`));
    });

    await t.test("exact replay returns the same receipt and writes nothing", async () => {
      const again = await call("carr_authority_joe", "a02-joe-1")();
      assert.equal(again.receipt_id, first.receipt_id);
      assert.equal(await receiptRows(R.writerTarget), 1);
    });

    await t.test("S8: the same key for a different request is refused by name", async () => {
      await refusedWithMessage(call("carr_authority_joe", "a02-joe-1", { kind: "degraded_read_only" })(),
        "rule_enforcement_fallback_idempotency_conflict");
      assert.equal(await receiptRows(R.writerTarget), 1);
    });

    await t.test("a conflicting second fallback is a named refusal, not a raw unique violation", async () => {
      await refusedWithMessage(call("carr_authority_joe", "a02-joe-2", { kind: "degraded_read_only" })(),
        "rule_enforcement_fallback_already_recorded");
      assert.equal(await receiptRows(R.writerTarget), 1);
    });

    await t.test("the recorded receipt is exactly what coverage accepts", async () => {
      const record = await coverage(db);
      const expected = { ...EXPECTED_GAPS };
      delete expected[R.writerTarget];
      assert.deepEqual(gapsByRule(record), expected);
      assert.equal(record.covered_rule_count, 2);
    });

    await t.test("S7 and item 2: UPDATE, DELETE and the owner's TRUNCATE are refused", async () => {
      const owner = await db.query(
        "select pg_get_userbyid(relowner) as o from pg_class where oid='ops.rule_enforcement_fallback_receipt'::regclass");
      const asOwner = await clientFor(pg, copy, owner.rows[0].o);
      try {
        await refusedWithMessage(asOwner.query(
          "update ops.rule_enforcement_fallback_receipt set fallback_kind='degraded_read_only' where id=$1",
          [first.receipt_id]), "rule_enforcement_fallback_receipts_append_only");
        await refusedWithMessage(asOwner.query(
          "delete from ops.rule_enforcement_fallback_receipt where id=$1", [first.receipt_id]),
          "rule_enforcement_fallback_receipts_append_only");
        await refusedWithMessage(asOwner.query("truncate ops.rule_enforcement_fallback_receipt"),
          "rule_enforcement_fallback_receipts_append_only");
      } finally {
        await asOwner.end();
      }
      const kept = await db.query(
        "select fallback_kind from ops.rule_enforcement_fallback_receipt where id=$1", [first.receipt_id]);
      assert.equal(kept.rows[0].fallback_kind, "refuse_closed");
    });

    await t.test("the MCP verb: short id resolves, Dell and a second receipt are named ToolErrors", async () => {
      const verb = "record-rule-enforcement-fallback";
      const args = key => ({ idempotency_key: key, rule_id: R.fallbackAbsent.slice(0, 8),
        fallback_kind: "escalate_to_verified_partner", procedure_ref: "runbooks/synthetic.md",
        reason: "synthetic verb reason" });
      await assert.rejects(callVerb(pg, copy, "carr_authority_dell", DELL, verb, args(randomUUID())),
        e => e instanceof ToolError && e.payload.error === "rule_enforcement_fallback_requires_joe_authority");
      const key = randomUUID();
      const out = await callVerb(pg, copy, "carr_authority_joe", JOE, verb, args(key));
      assert.equal(out.rule_id, R.fallbackAbsent);
      assert.equal(out.recorded_by, "joe");
      const replay = await callVerb(pg, copy, "carr_authority_joe", JOE, verb, args(key));
      assert.equal(replay.replayed, true);
      assert.equal(replay.receipt_id, out.receipt_id);
      await assert.rejects(callVerb(pg, copy, "carr_authority_joe", JOE, verb, args(randomUUID())),
        e => e instanceof ToolError && e.payload.error === "rule_enforcement_fallback_already_recorded");
      assert.equal(await receiptRows(R.fallbackAbsent), 1);
    });
  } finally {
    if (db) await db.end();
    await admin.query(`DROP DATABASE IF EXISTS ${copy} WITH (FORCE)`).catch(() => {});
    await admin.end();
  }
});
