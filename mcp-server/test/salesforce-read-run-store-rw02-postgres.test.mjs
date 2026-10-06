// salesforce-read-run-store-rw02-postgres.test.mjs — migration 0733 on REAL
// PostgreSQL: the attended-run ledger and its reset, the clean-after-stop
// refusal, the consent record read and its revocation, the loop-episode read,
// and the invoiced marker in the reconciliation absence scope.
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
import {
  createDellConsentSource, createLoopEpisodeSource, createServerClock, evaluateDellConsent,
  readCarrDealsForReconciliation,
} from "../src/salesforce-browser-read-rw02.v5.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_RW02_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;
const JOE = { slug: "joe", display: "Joe", human: true, via: "mcp", client_id: "rw02-run-store-pg-test" };
const KIND = "presence_membership_reconciliation";
const hex24 = () => randomUUID().replace(/-/g, "").slice(0, 24);

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

/** One unit of work as mcp.js runs a verb: its own transaction as carr_writer. */
async function asWriter(pg, database, fn, { partner = null, acting = JOE.slug } = {}) {
  const client = await clientFor(pg, database, "carr_writer");
  try {
    const a = await client.query("select id from public.actor where slug=$1", [JOE.slug]);
    const actor = { ...JOE, id: a.rows[0].id };
    await client.query("BEGIN");
    await client.query("select set_config('carr.acting_actor_slug',$1::text,true)", [acting]);
    if (partner) await client.query("select set_config('carr.verified_human_actor_slug',$1::text,true)", [partner]);
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

const verb = (pg, database, name, args, opts) =>
  asWriter(pg, database, (client, actor) => TOOLS[name].handler(client, actor, args), opts);

test("V5-RW02 run ledger, consent record, loop episodes and invoiced scope on real PostgreSQL", async t => {
  if (!DSN) {
    assert.equal(REQUIRED, false, "the V5-RW02 database proof is required but DATABASE_URL is unset");
    return t.skip("the migration class supplies DATABASE_URL");
  }
  assert.match(DSN, LOOPBACK, "the V5-RW02 database proof runs only against a loopback disposable database");
  const pg = (await import("pg")).default;
  const source = new URL(DSN).pathname.replace(/^\//, "");
  const admin = await clientFor(pg, "postgres");
  const copy = `rw02_run_pg_${process.pid}_${randomUUID().slice(0, 8)}`;
  let db;
  try {
    await admin.query(`CREATE DATABASE ${copy} TEMPLATE ${source}`);
    db = await clientFor(pg, copy);
    const applied = await db.query(
      "select 1 from public.schema_migrations where filename = '0733_salesforce_rw02_safe_stop_run_store.sql'");
    assert.equal(applied.rows.length, 1, "migration 0733 is applied as the numbered file");
    await db.query(
      `insert into public.actor (slug, kind, display_name, active) values ('joe', 'human', 'joe', true)
       on conflict (slug) do nothing`);

    await t.test("five clean runs meet the threshold; one stop resets to zero; the ledger is append-only", async () => {
      const record = (outcome, extra = {}) => {
        const run_ref = hex24();
        return verb(pg, copy, "record-salesforce-run-outcome", { idempotency_key: `rw02-run-outcome:${run_ref}`,
          run_ref, action_kind: KIND, outcome, ...extra });
      };
      let last;
      for (let i = 0; i < 4; i++) last = await record("clean");
      assert.equal(last.counter.consecutive_clean, 4);
      assert.equal(last.counter.threshold_met, false);
      last = await record("clean");
      assert.equal(last.counter.consecutive_clean, 5);
      assert.equal(last.counter.threshold_met, true);
      const stop = await record("stopped", { stop_class: "ui_drift", reason_id: "ui_selector_missing" });
      assert.equal(stop.counter.consecutive_clean, 0);
      const read = await verb(pg, copy, "read-salesforce-autonomy-counter", { action_kind: KIND });
      assert.equal(read.consecutive_clean, 0);
      assert.equal(read.threshold_met, false);
      assert.equal(read.runs_recorded, 6);
      // The Worker runs a read verb on its READER login; the counter answers there too.
      const reader = await clientFor(pg, copy, "carr_reader");
      try {
        const actorId = (await reader.query("select id from public.actor where slug=$1", [JOE.slug])).rows[0].id;
        const onReader = await TOOLS["read-salesforce-autonomy-counter"].handler(reader, { ...JOE, id: actorId },
          { action_kind: KIND });
        assert.equal(onReader.consecutive_clean, 0);
        assert.equal(onReader.runs_recorded, 6);
      } finally { await reader.end(); }
      // Another kind neither counts nor resets.
      const other = await verb(pg, copy, "read-salesforce-autonomy-counter", { action_kind: "opportunity_create" });
      assert.equal(other.consecutive_clean, 0);
      assert.equal(other.runs_recorded, 0);
      for (const sql of ["update ops.rw02_attended_run set outcome = 'clean'",
        "delete from ops.rw02_attended_run", "truncate ops.rw02_attended_run"]) {
        await assert.rejects(db.query(sql), /rw02_safe_stop_append_only/, sql);
      }
    });

    await t.test("two concurrent first recordings of one run: one row, and a typed conflict or replay", async () => {
      const run_ref = hex24();
      const args = outcome => ({ idempotency_key: `rw02-run-outcome:${run_ref}:${outcome}`, run_ref,
        action_kind: KIND, outcome, ...(outcome === "stopped" ? { stop_class: "ui_drift", reason_id: "ui_drift" } : {}) });
      const settled = await Promise.allSettled([
        verb(pg, copy, "record-salesforce-run-outcome", args("clean")),
        verb(pg, copy, "record-salesforce-run-outcome", args("stopped"))]);
      const rejected = settled.filter(r => r.status === "rejected");
      assert.equal(rejected.length, 1, "exactly one of two different facts for one run lands");
      assert.ok(rejected[0].reason instanceof ToolError, String(rejected[0].reason));
      assert.equal(rejected[0].reason.payload.error, "run_outcome_conflict");
      assert.equal((await db.query("select count(*)::int as n from ops.rw02_attended_run where run_ref=$1",
        [run_ref])).rows[0].n, 1);
    });

    await t.test("a run that recorded a page stop cannot be recorded clean", async () => {
      const run_ref = hex24();
      await verb(pg, copy, "record-salesforce-page-stop", { idempotency_key: `rw02-read:${run_ref}:page-stop:0`,
        page: { execution_mode: "attended",
          binding: { expected_origin: "https://synthetic-org.invalid", expected_org_id: "00Dsynthetic0001",
            expected_account_ref: "seat", expected_ui_contract_digest: `sha256:${"1".repeat(64)}` },
          observation: { origin: "https://synthetic-org.invalid", org_id: "00Dsynthetic0001",
            signed_in_account_ref: "seat", ui_contract_digest: `sha256:${"1".repeat(64)}`,
            challenge: "captcha", result_consistency: "consistent" } } });
      await assert.rejects(verb(pg, copy, "record-salesforce-run-outcome", {
        idempotency_key: `rw02-run-outcome:${run_ref}`, run_ref, action_kind: KIND, outcome: "clean" }),
      e => e instanceof ToolError && e.payload.error === "clean_run_contradicted");
      const stopped = await verb(pg, copy, "record-salesforce-run-outcome", {
        idempotency_key: `rw02-run-outcome:${run_ref}-s`, run_ref, action_kind: KIND, outcome: "stopped",
        stop_class: "auth_challenge", reason_id: "captcha" });
      assert.equal(stopped.run.outcome, "stopped");
    });

    await t.test("consent: the decision record is read, and a partner's revocation refuses the next run", async () => {
      const decision = randomUUID();
      const actor = (await db.query("select id from public.actor where slug='joe'")).rows[0].id;
      const ev = (await db.query(
        `insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id, new_value, cause,
           human_quote, sponsoring_human_slug)
         values (now(), $1, 'log-decision', 'decision', $2, '{"title":"synthetic consent"}', 'human_stated',
           'synthetic partner words', 'joe') returning id`, [actor, decision])).rows[0].id;
      await db.query(`insert into public.record_source (entity_type, entity_id, source_system, external_key)
        values ('event', $1, 'decision-history', $2)`, [ev, `live#pg#${ev}`]);
      const readConsent = () => asWriter(pg, copy, async client => {
        const r = await client.query("select ops.rw02_consent_record($1::uuid) as consent", [decision]);
        return r.rows[0].consent;
      });
      const before = await readConsent();
      assert.deepEqual(before, { record: { decision_id: decision, sponsoring_human_slug: "joe",
        human_quote_present: true }, revoked: false });
      const missing = await asWriter(pg, copy, async client =>
        (await client.query("select ops.rw02_consent_record($1::uuid) as c", [randomUUID()])).rows[0].c);
      assert.deepEqual(missing, { record: null, revoked: false });
      assert.equal(evaluateDellConsent(missing).reason_id, "dell_consent_record_missing");
      // The production source asks for the pinned decision; on this database it does not exist.
      const pinned = await asWriter(pg, copy, client => createDellConsentSource(client).read());
      assert.equal(evaluateDellConsent(pinned).decision, "refused");
      // No verified partner: refused.
      await assert.rejects(verb(pg, copy, "revoke-salesforce-read-consent", {
        idempotency_key: randomUUID(), decision_id: decision, human_quote: "withdrawn" }),
      e => e instanceof ToolError && e.payload.error === "verified_partner_required");
      // A verified-partner setting that names a different human than the acting
      // human actor is refused: the setting alone cannot forge a revocation.
      await assert.rejects(verb(pg, copy, "revoke-salesforce-read-consent", {
        idempotency_key: randomUUID(), decision_id: decision, human_quote: "withdrawn" }, { partner: "dell" }),
      e => e instanceof ToolError && e.payload.error === "verified_partner_required");
      assert.equal((await readConsent()).revoked, false);
      const revoked = await verb(pg, copy, "revoke-salesforce-read-consent", {
        idempotency_key: randomUUID(), decision_id: decision, human_quote: "withdrawn" },
      { partner: "dell", acting: "dell" });
      assert.equal(revoked.revoked_by, "dell");
      const dellId = (await db.query("select id from public.actor where slug='dell'")).rows[0].id;
      assert.equal((await db.query("select revoked_by_actor_id from ops.rw02_consent_revocation where decision_id=$1",
        [decision])).rows[0].revoked_by_actor_id, dellId, "the revocation names the actor that made it");
      assert.equal((await readConsent()).revoked, true);
      await assert.rejects(verb(pg, copy, "revoke-salesforce-read-consent", {
        idempotency_key: randomUUID(), decision_id: randomUUID(), human_quote: "withdrawn" }, { partner: "joe" }),
      e => e instanceof ToolError && e.payload.error === "consent_decision_unknown");
    });

    await t.test("loop episodes: the read answers each filing's loop status", async () => {
      const base = `rw02-missing-joe:${randomUUID().replace(/-/g, "")}`;
      const loopA = randomUUID();
      const loopB = randomUUID();
      const actor = (await db.query("select id from public.actor where slug='joe'")).rows[0].id;
      await db.query("set session_replication_role = replica");
      try {
        for (const [id, status] of [[loopA, "done"], [loopB, "open"]]) {
          await db.query(
            `insert into public.loop_item (id, kind, number, block_id, render_seq, tier, title, owner, status,
               close_outcome, closed_at, created_by, updated_by)
             values ($1, 'team_loop', $6, gen_random_uuid(), 1, 'shared', 'synthetic', 'dell', $2, $3, $4, $5, $5)`,
            [id, status, status === "open" ? null : "synthetic close", status === "open" ? null : new Date(), actor,
              `T-rw02-${id.slice(0, 8)}`]);
        }
      } finally { await db.query("set session_replication_role = origin"); }
      for (const [key, loop] of [[base, loopA], [`${base}:e2`, loopB], [`${base}:e3`, randomUUID()],
        [`${base}:eX`, randomUUID()], [`${base}:e0`, randomUUID()], [`${base}:e1`, randomUUID()],
        [`${base}:e2x`, randomUUID()]]) {
        await db.query(`insert into public.tool_call (idempotency_key, verb, actor_id, request_hash, response)
          values ($1, 'add-loop', $2, 'synthetic', $3)`, [key, actor, JSON.stringify({ ok: true, loop_id: loop })]);
      }
      const rows = await asWriter(pg, copy, client => createLoopEpisodeSource(client).loopEpisodes(base));
      assert.deepEqual(rows.map(r => [r.idempotency_key, r.status]).sort(),
        [[base, "done"], [`${base}:e2`, "open"], [`${base}:e3`, null]],
        "malformed look-alike keys (:eX, :e0, :e1, :e2x) are not episodes and are not answered");
      const foreign = await asWriter(pg, copy, client => createLoopEpisodeSource(client).loopEpisodes("add-loop-anything"));
      assert.deepEqual(foreign, [], "only RW02 finding keys are answered");
    });

    await t.test("the invoiced marker: open and won-not-invoiced deals are in scope; invoiced, lost and paused are not", async () => {
      const actor = (await db.query("select id from public.actor where slug='joe'")).rows[0].id;
      const ids = {};
      await db.query("set session_replication_role = replica");
      try {
        for (const [label, outcome, closed_on, invoiced_on] of [
          ["open", null, null, null], ["won_not_invoiced", "won", "2026-09-01", null],
          ["won_invoiced", "won", "2026-08-01", "2026-09-15"], ["lost", "lost", "2026-07-01", null],
          ["paused", "paused", null, null], ["closed_no_outcome", null, "2026-09-02", null]]) {
          ids[label] = (await db.query(
            `insert into public.deal (client_id, name, deal_type, phase, outcome, closed_on, invoiced_on,
               created_by, updated_by)
             values (gen_random_uuid(), $1, 'synthetic', 'synthetic', $2, $3, $4, $5, $5) returning id`,
            [`Synthetic ${label}`, outcome, closed_on, invoiced_on, actor])).rows[0].id;
        }
      } finally { await db.query("set session_replication_role = origin"); }
      const reader = await clientFor(pg, copy, "carr_reader");
      try {
        const deals = await readCarrDealsForReconciliation(reader);
        const inScope = new Set(deals.map(d => d.deal_id));
        for (const label of ["open", "won_not_invoiced", "closed_no_outcome"])
          assert.ok(inScope.has(ids[label]), `${label} is in the absence scope`);
        for (const label of ["won_invoiced", "lost", "paused"])
          assert.ok(!inScope.has(ids[label]), `${label} is out of the absence scope`);
      } finally { await reader.end(); }
      // update-deal refuses an impossible invoice date before it reaches the
      // database, instead of failing later as a raw cast error.
      for (const invoiced_on of ["2026-13-45", "2026-02-30", "2026-9-1", 20260901]) {
        await assert.rejects(verb(pg, copy, "update-deal", { idempotency_key: randomUUID(), deal: ids.open,
          base_version: 1, fields: { invoiced_on } }),
        e => e instanceof ToolError && e.payload.error === "invalid_invoiced_on", String(invoiced_on));
      }
    });

    await t.test("the server clock is the database clock", async () => {
      const clock = await asWriter(pg, copy, async client => {
        const r = await client.query("select (floor(extract(epoch from clock_timestamp()) * 1000))::bigint::text as now_ms");
        return Number(r.rows[0].now_ms);
      });
      assert.ok(Math.abs(clock - Date.now()) < 60_000);
      assert.ok(createServerClock(db));
    });
  } finally {
    if (db) await db.end();
    await admin.query(`DROP DATABASE IF EXISTS ${copy} WITH (FORCE)`).catch(() => {});
    await admin.end();
  }
});
