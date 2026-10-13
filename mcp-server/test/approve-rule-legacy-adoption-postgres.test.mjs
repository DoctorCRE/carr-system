// approve-rule-legacy-adoption-postgres.test.mjs — WR-000262 on REAL PostgreSQL.
//
// 97 active rules were activated by hand before the approval-receipt system
// existed. approve-rule refused every one ("active rule <id> lacks its
// immutable exact control binding") because ops.bind_rule_controls treated an
// active rule only as a replay. Migration 0867 lets ops.approve_rule ADOPT such
// a legacy admission: bind its installed gate-class controls, flip its admitted
// contract to hard_enforced, and write the approval and authority receipts for
// the rule's current version without touching the rule row.
//
// This suite seeds legacy-shaped rules into a disposable template copy, calls
// the approve-rule verb exactly as mcp.js runs it on the carr_authority_joe
// connection, and asserts:
//   AC-ADOPT   a legacy rule with an installed, verified control is adopted in
//              one transaction: receipt, binding, enforcement point,
//              hard_enforced admission, unchanged rule row; it then reads in
//              ops.applicable_rules(), replays exactly, and can be retired
//              through its receipt.
//   AC-REFUSE  a held (uninstalled) control, a surfacing control, a
//              post-cutover receiptless active rule, a missing admission, a
//              class mismatch, a non-Joe activator and a Dell caller are each
//              refused by name, and a refusal writes nothing.
//   AC-A02     V5-A02 sees the adopted rule's controls as mapped and passing;
//              its only remaining gap is the fallback rung, which 0876
//              (PR 1723) lifts for hard_enforced rules on hook-only controls
//              (the gate fixture below is a hooks/*.py control).
//
// Skips in the unit class. The migration class supplies DATABASE_URL and sets
// CARR_APPROVE_RULE_ADOPTION_DB_REQUIRED=1, which turns a skip into a failure.
// It commits only into a template copy it creates and drops.
//
// EVERY RECORD IS SYNTHETIC.

import test from "node:test";
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";

import { TOOLS, ToolError } from "../src/tools.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_APPROVE_RULE_ADOPTION_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

const sha = text => createHash("sha256").update(text, "utf8").digest("hex");

const R = Object.freeze({
  cutoverAnchor:   "26200000-0000-4000-8000-000000000000",
  ready:           "26200001-0000-4000-8000-000000000000",
  held:            "26200002-0000-4000-8000-000000000000",
  surfacing:       "26200003-0000-4000-8000-000000000000",
  postCutover:     "26200004-0000-4000-8000-000000000000",
  noAdmission:     "26200005-0000-4000-8000-000000000000",
  classMismatch:   "26200006-0000-4000-8000-000000000000",
  notJoeActivated: "26200007-0000-4000-8000-000000000000",
  dellCaller:      "26200008-0000-4000-8000-000000000000",
});

const CTL = Object.freeze({
  gate: "wr262_gate_control",
  held: "wr262_held_control",
  surfacing: "wr262_surfacing_control",
});

const PACK = "wr262-adoption-proof-pack";

const JOE = { slug: "joe", display: "Joe", human: true, via: "mcp", client_id: "wr262-postgres-test" };
const DELL = { slug: "dell", display: "Dell", human: true, via: "mcp", client_id: "wr262-postgres-test" };

const ADMITTED = Object.freeze({
  binding_moment: "before a synthetic legacy action",
  applicability: { tiers: ["*"], scopes: ["shared"], surfaces: ["*"], workflows: ["*"] },
  projection: { targets: ["standing-context", "applicable-rules", "rule-enforcement-map"], category: "hard_pre_action" },
  reachability: { paths: ["record-layer", "session-boot", "CI"], exceptions: "none" },
  input_contract: { type: "object", required: ["workflow", "surface", "tier"] },
});

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
      const out = await TOOLS[verb].handler(client, full, { ...args });
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

async function refusedWithMessage(promise, message) {
  await assert.rejects(promise, e => {
    assert.ok(!(e instanceof ToolError), `expected the database refusal, got ToolError ${JSON.stringify(e.payload)}`);
    assert.equal(e.message, message, `expected named refusal ${message}, got ${e.code} ${e.message}`);
    return true;
  });
}

/** A legacy-shaped rule: active, activated in 2020 (before any receipt),
 * delivered by a control layer, admitted but blocked, with no receipt. */
async function seedLegacyRule(db, ctx, id, spec = {}) {
  const statement = `wr262 synthetic legacy rule ${id}`;
  await db.query(
    `insert into public.rule (id, statement, taught_by, status, activated_by, activated_at, version)
     values ($1, $2, $3, 'active', $4, $5, 3)`,
    [id, statement, ctx.joeId, spec.activatedBy ?? ctx.joeId,
     spec.activatedAt ?? "2020-01-01T00:00:00Z"]);
  // SIEP-12 settles a policy epoch only with at least one non-empty pack, so
  // a fixture may be pack-delivered; every other one is control-delivered.
  await db.query(
    `insert into ops.rule_load_layer (rule_id, short_id, load_layer, packs, scope, why, source, map_digest)
     values ($1::uuid, left($1::uuid::text, 8), case when $2::text is null then 'control' else 'pack' end,
             case when $2::text is null then '{}'::text[] else array[$2::text] end, 'shared',
             'wr262 synthetic delivery layer',
             'mcp-server/test/approve-rule-legacy-adoption-postgres.test.mjs', repeat('0', 64))`,
    [id, spec.pack ?? null]);
  if (spec.admission === false) return statement;
  await db.query(
    `insert into ops.rule_admission
       (rule_id, enforcement_class, binding_moment, applicability, projection, reachability,
        input_contract, fixture_refs, state, admitted_by, admitted_at, enforcement_status, coverage_detail)
     values ($1, $2, $3, $4, $5, $6, $7, array['tests/synthetic-legacy-fixture'], 'admitted', $8,
             '2020-01-01T00:00:00Z', 'blocked', '{"source":"wr262 synthetic"}'::jsonb)`,
    [id, spec.enforcementClass ?? "machine_enforceable", ADMITTED.binding_moment,
     ADMITTED.applicability, ADMITTED.projection, ADMITTED.reachability, ADMITTED.input_contract,
     ctx.joeId]);
  // The map's planned (not yet installed) point, as production carries it.
  await db.query(
    `insert into ops.rule_enforcement_point
       (rule_id, control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
     values ($1, $2, 'synthetic/impl', 'synthetic/test', 'deny_gate', false, null)`,
    [id, spec.control ?? CTL.gate]);
  return statement;
}

function approveArgs(ruleId, control, key = randomUUID()) {
  return { idempotency_key: key, rule_id: ruleId, policy_kind: "machine_enforceable",
           control_keys: [control], reason: "WR-000262 synthetic adoption under decision cbe91287" };
}

test("approve-rule adopts an active legacy rule on real PostgreSQL (WR-000262)", async t => {
  if (!DSN) {
    assert.equal(REQUIRED, false, "the WR-000262 database proof is required but DATABASE_URL is unset");
    return t.skip("the migration class supplies DATABASE_URL");
  }
  assert.match(DSN, LOOPBACK, "the WR-000262 database proof runs only against a loopback disposable database");
  const pg = (await import("pg")).default;
  const source = new URL(DSN).pathname.replace(/^\//, "");
  const admin = await clientFor(pg, "postgres");
  const copy = `wr262_adopt_${process.pid}_${randomUUID().slice(0, 8)}`;
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
      "select 1 from public.schema_migrations where filename = '0867_adopt_active_legacy_rule_approval.sql'");
    assert.equal(applied.rows.length, 1, "migration 0867 is applied as the numbered file");

    // Seed with triggers off: these rows reproduce a shape that predates the
    // admission and receipt triggers, and every other active rule is retired
    // so the coverage read below is about these rules only.
    await db.query("begin");
    await db.query("set local session_replication_role = replica");
    await db.query("update public.rule set status = 'retired' where status = 'active'");
    for (const slug of ["joe", "dell"])
      await db.query(
        `insert into public.actor (slug, kind, display_name, active) values ($1, 'human', $1, true)
         on conflict (slug) do nothing`, [slug]);
    const ctx = {
      joeId: (await db.query("select id from public.actor where slug='joe'")).rows[0].id,
      dellId: (await db.query("select id from public.actor where slug='dell'")).rows[0].id,
    };
    for (const [key, klass, installed] of [
      [CTL.gate, "deny_gate", true], [CTL.held, "deny_gate", false], [CTL.surfacing, "surfacing", true]])
      await db.query(
        `insert into ops.enforcement_control_catalog
           (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
         values ($1, 'hooks/wr262-synthetic-gate.py', 'tests/' || $1, $2, $3,
                 case when $3 then now() - interval '1 hour' end)`, [key, klass, installed]);
    // The receipt cutover: one receipt older than the post-cutover fixture and
    // newer than every legacy fixture, so ops.legacy_rule_admission_note()
    // answers both ways.
    await db.query(
      `insert into public.rule (id, statement, taught_by, status) values ($1, 'wr262 cutover anchor', $2, 'retired')`,
      [R.cutoverAnchor, ctx.joeId]);
    await db.query(
      `insert into ops.rule_approval_receipt
         (idempotency_key, rule_id, rule_version, statement_hash, actor_id, policy_kind,
          enforcement_status, requested_control_keys, installed_control_keys, reason,
          normalized_contract, contract_hash, evidence_refs, created_at)
       values ('wr262-cutover-anchor', $1, 1, $2, $3, 'machine_enforceable', 'hard_enforced',
               array[$4], array[$4], 'synthetic cutover anchor', '{}'::jsonb,
               encode(public.digest('{}'::jsonb::text, 'sha256'), 'hex'), array['synthetic'],
               now() - interval '1 day')`,
      [R.cutoverAnchor, sha("wr262 cutover anchor"), ctx.joeId, CTL.gate]);
    const statements = {};
    statements.ready = await seedLegacyRule(db, ctx, R.ready);
    await seedLegacyRule(db, ctx, R.held, { control: CTL.held });
    await seedLegacyRule(db, ctx, R.surfacing, { control: CTL.surfacing });
    await seedLegacyRule(db, ctx, R.postCutover, { activatedAt: new Date().toISOString() });
    await seedLegacyRule(db, ctx, R.noAdmission, { admission: false });
    await seedLegacyRule(db, ctx, R.classMismatch, { enforcementClass: "human_only" });
    await seedLegacyRule(db, ctx, R.notJoeActivated, { activatedBy: ctx.dellId });
    await db.query(
      `insert into ops.rule_pack (pack, title, description, triggers, source)
       values ($1, 'WR-000262 adoption proof', 'Rollback-only pack anchor for the policy epoch',
               array['wr262-adoption-proof'], 'mcp-server/test/approve-rule-legacy-adoption-postgres.test.mjs')`,
      [PACK]);
    await seedLegacyRule(db, ctx, R.dellCaller, { pack: PACK });
    await db.query("commit");

    const legacy = await db.query(
      `select r.id, ops.legacy_rule_admission_note(r.id, r.status, r.activated_at) is not null as legacy
         from public.rule r where r.id = any($1::uuid[]) order by r.id`,
      [[R.ready, R.postCutover]]);
    assert.deepEqual(legacy.rows.map(r => [r.id, r.legacy]), [[R.ready, true], [R.postCutover, false]],
      "fixture sanity: the legacy predicate separates the two fixtures");

    const ruleRow = async id => (await db.query(
      "select version, updated_at, status, activated_by, activated_at, statement, enforcement from public.rule where id=$1",
      [id])).rows[0];
    const footprint = async id => (await db.query(
      `select (select count(*)::int from ops.rule_approval_receipt where rule_id=$1) as receipts,
              (select count(*)::int from ops.rule_control_binding where rule_id=$1) as bindings,
              (select count(*)::int from ops.authority_receipt where subject_id=$1) as authority,
              (select count(*)::int from ops.rule_enforcement_point where rule_id=$1 and installed) as installed_points,
              (select enforcement_status from ops.rule_admission where rule_id=$1) as status`, [id])).rows[0];

    await t.test("AC-ADOPT: a legacy rule with an installed gate control is adopted atomically", async () => {
      const before = await ruleRow(R.ready);
      const args = approveArgs(R.ready.slice(0, 8), CTL.gate);
      const out = await callVerb(pg, copy, "carr_authority_joe", JOE, "approve-rule", args);
      assert.equal(out.policy_status, "active");
      assert.equal(out.enforcement_status, "hard_enforced");
      assert.equal(out.approval_path, "legacy_adoption");
      assert.equal(out.replayed, false);
      assert.deepEqual(out.installed_controls, [CTL.gate]);
      assert.equal(out.rule_version, before.version, "the receipt names the CURRENT version");

      assert.deepEqual(await ruleRow(R.ready), before, "the rule row is not updated at all");
      assert.deepEqual(await footprint(R.ready),
        { receipts: 1, bindings: 1, authority: 1, installed_points: 1, status: "hard_enforced" });

      const receipt = (await db.query(
        `select ar.rule_version, ar.statement_hash, ar.policy_kind, ar.requested_control_keys,
                ar.evidence_refs, ar.normalized_contract, a.fixture_refs, a.binding_moment,
                a.admitted_by = ar.actor_id as admitted_by_approver,
                b.statement_hash as binding_hash, b.binding_contract->>'approval_path' as binding_path,
                ep.verified_at = c.verified_at as point_matches_catalog
           from ops.rule_approval_receipt ar
           join ops.rule_admission a on a.rule_id = ar.rule_id
           join ops.rule_control_binding b on b.rule_id = ar.rule_id
           join ops.rule_enforcement_point ep on ep.rule_id = ar.rule_id and ep.control_key = b.control_key
           join ops.enforcement_control_catalog c on c.control_key = b.control_key
          where ar.id = $1`, [out.approval_receipt_id])).rows[0];
      assert.equal(receipt.rule_version, before.version);
      assert.equal(receipt.statement_hash, sha(statements.ready));
      assert.equal(receipt.binding_hash, sha(statements.ready));
      assert.equal(receipt.binding_path, "legacy_adoption");
      assert.equal(receipt.point_matches_catalog, true);
      assert.equal(receipt.admitted_by_approver, true);
      assert.deepEqual(receipt.requested_control_keys, [CTL.gate]);
      assert.deepEqual(receipt.evidence_refs, ["tests/synthetic-legacy-fixture", `tests/${CTL.gate}`]);
      assert.deepEqual(receipt.fixture_refs, receipt.evidence_refs);
      assert.equal(receipt.binding_moment, ADMITTED.binding_moment, "the admitted contract is kept as admitted");
      assert.equal(receipt.normalized_contract.binding_moment, ADMITTED.binding_moment);
      assert.deepEqual(receipt.normalized_contract.projection, ADMITTED.projection);
      assert.equal(receipt.normalized_contract.approval_path, "legacy_adoption");
      assert.match(receipt.normalized_contract.legacy_admission, /^legacy_admission: /);

      const applicable = await db.query(
        "select count(*)::int as n from ops.applicable_rules(null,null,null) where rule_id=$1", [R.ready]);
      assert.equal(applicable.rows[0].n, 1, "the receipt-bound policy compiler now delivers the adopted rule");
      const status = await db.query(
        "select enforcement_status, approval_receipt_id from ops.v_rule_enforcement_status where rule_id=$1",
        [R.ready]);
      assert.equal(status.rows[0].enforcement_status, "hard_enforced");
      assert.equal(status.rows[0].approval_receipt_id, out.approval_receipt_id);

      // Verb replay (tool_call) and database replay (0479 path) both hold.
      // The envelope spreads the stored response over its own replayed:true,
      // so the verb replay is proved by the same receipt and no new rows.
      const verbReplay = await callVerb(pg, copy, "carr_authority_joe", JOE, "approve-rule", args);
      assert.equal(verbReplay.approval_receipt_id, out.approval_receipt_id);
      const joe = await clientFor(pg, copy, "carr_authority_joe");
      try {
        const dbReplay = await joe.query(
          "select ops.approve_rule($1,'machine_enforceable',$2,$3,$4) as r",
          [R.ready, [CTL.gate], args.idempotency_key, args.reason]);
        assert.equal(dbReplay.rows[0].r.replayed, true);
        assert.equal(dbReplay.rows[0].r.approval_receipt_id, out.approval_receipt_id);
        // A second, different approval of an adopted rule is no longer a
        // legacy adoption: it takes the 0479 path, which refuses an active rule.
        await assert.rejects(joe.query(
          "select ops.approve_rule($1,'machine_enforceable',$2,$3,$4)",
          [R.ready, [CTL.gate], randomUUID(), args.reason]),
          e => e.message === `rule ${R.ready} is active, only a proposed rule can be approved`);
      } finally {
        await joe.end();
      }
      assert.deepEqual(await footprint(R.ready),
        { receipts: 1, bindings: 1, authority: 1, installed_points: 1, status: "hard_enforced" });
    });

    await t.test("AC-A02: the adopted rule's controls read mapped and passing", async () => {
      const record = (await db.query("select ops.v5_a02_rule_enforcement_coverage() as c")).rows[0].c;
      const gap = record.gaps.find(g => g.rule_id === R.ready)?.reason_id ?? null;
      const def = (await db.query(
        "select pg_get_functiondef('ops.v5_a02_rule_enforcement_coverage()'::regprocedure) as d")).rows[0].d;
      if (def.includes("fallback_required")) {
        assert.equal(gap, null, "with 0876 applied the adopted hard_enforced rule is covered");
      } else {
        assert.equal(gap, "active_rule_fallback_absent",
          "before 0876 the adopted rule clears every control rung and waits only on the fallback rung");
      }
      for (const id of [R.held, R.surfacing, R.postCutover])
        assert.equal(record.gaps.find(g => g.rule_id === id)?.reason_id, "active_rule_control_unmapped");
    });

    await t.test("AC-REFUSE: each unadoptable rule is refused by name and writes nothing", async () => {
      const cases = [
        [R.held, CTL.held,
         `rule-control binding refused: registered installed controls are missing {${CTL.held}}`],
        [R.surfacing, CTL.surfacing,
         `rule-control binding refused: registered installed controls are missing {${CTL.surfacing}}`],
        [R.postCutover, CTL.gate,
         `active rule ${R.postCutover} lacks its immutable exact control binding`],
        [R.noAdmission, CTL.gate,
         `active legacy rule ${R.noAdmission} has no admitted rule contract to adopt`],
        [R.classMismatch, CTL.gate,
         `active legacy rule ${R.classMismatch} is admitted as human_only, not machine_enforceable; reclassify the legacy admission first`],
        [R.notJoeActivated, CTL.gate,
         `active legacy rule ${R.notJoeActivated} was not activated by Joe; adoption cannot bind its approval to another activator`],
      ];
      for (const [id, control, message] of cases) {
        const before = await footprint(id);
        await refusedWithMessage(
          callVerb(pg, copy, "carr_authority_joe", JOE, "approve-rule", approveArgs(id, control)), message);
        assert.deepEqual(await footprint(id), before, `refusal for ${id} rolled back every write`);
        assert.equal(before.receipts, 0);
        assert.equal(before.bindings, 0);
      }
    });

    await t.test("AC-REFUSE: Dell cannot adopt, and the writer role cannot call approval at all", async () => {
      const before = await footprint(R.dellCaller);
      await assert.rejects(
        callVerb(pg, copy, "carr_authority_dell", DELL, "approve-rule", approveArgs(R.dellCaller, CTL.gate)),
        e => /requires Joe authority/.test(e.message));
      assert.deepEqual(await footprint(R.dellCaller), before);
      const writer = await clientFor(pg, copy, "carr_writer");
      try {
        await assert.rejects(writer.query(
          "select ops.adopt_legacy_active_rule_v1($1,'machine_enforceable',$2,$3,'direct')",
          [R.dellCaller, [CTL.gate], randomUUID()]), e => e.code === "42501");
      } finally {
        await writer.end();
      }
      const authority = await clientFor(pg, copy, "carr_authority_joe");
      try {
        await assert.rejects(authority.query(
          "select ops.adopt_legacy_active_rule_v1($1,'machine_enforceable',$2,$3,'direct')",
          [R.dellCaller, [CTL.gate], randomUUID()]), e => e.code === "42501",
          "the internal continuation is not callable around ops.approve_rule");
      } finally {
        await authority.end();
      }
    });

    await t.test("an adopted rule retires through its receipt, not the legacy path", async () => {
      const joe = await clientFor(pg, copy, "carr_authority_joe");
      try {
        const out = await joe.query("select ops.retire_rule($1,$2,null,$3) as r",
          [R.ready, "WR-000262 synthetic retirement after adoption", randomUUID()]);
        assert.equal(out.rows[0].r.status, "retired");
        assert.equal(out.rows[0].r.legacy_admission ?? null, null);
      } finally {
        await joe.end();
      }
      const rr = await db.query(
        `select rr.approval_receipt_id = ar.id as bound, rr.legacy_admission
           from ops.rule_retirement_receipt rr join ops.rule_approval_receipt ar on ar.rule_id = rr.rule_id
          where rr.rule_id = $1`, [R.ready]);
      assert.equal(rr.rows[0].bound, true);
      assert.equal(rr.rows[0].legacy_admission, null);
    });
  } finally {
    if (db) await db.end();
    await admin.query(`DROP DATABASE IF EXISTS ${copy} WITH (FORCE)`).catch(() => {});
    await admin.end();
  }
});
