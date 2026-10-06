// V5-F05 durable rule-context seam against disposable loopback PostgreSQL.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_F05_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

async function database(t) {
  if (!DSN) {
    if (REQUIRED) assert.fail("CARR_F05_DB_REQUIRED requires DATABASE_URL");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN), "REFUSED: F05 SQL fixtures require disposable loopback PostgreSQL");
  return (await import("pg")).default ?? (await import("pg"));
}

async function ensureAuthorityLogins(client) {
  // Rolled back with the test transaction. The migration class may or may not
  // already carry these logins (a later db-gate commits them); establish the
  // state this test depends on rather than inheriting it.
  await client.query(`do $roles$ begin
    if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then
      create role carr_authority_joe login; end if;
    if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then
      create role carr_authority_dell login; end if;
    grant carr_authority to carr_authority_joe;
    grant carr_authority to carr_authority_dell;
  end $roles$`);
}

async function setActorContext(client, { acting, sponsor, verified }) {
  await client.query("select set_config('carr.organization_tenant_id','carr-internal',true)");
  await client.query("select set_config('carr.acting_actor_slug',$1,true)", [acting]);
  await client.query("select set_config('carr.sponsoring_human_slug',$1,true)", [sponsor]);
  await client.query("select set_config('carr.verified_human_actor_slug',$1,true)", [verified]);
}

// Each expected refusal runs inside its own savepoint so the enclosing
// fixture transaction survives it. ROLLBACK TO also undoes any
// SET SESSION AUTHORIZATION taken inside the savepoint.
async function refusedInSavepoint(client, run, pattern, message) {
  await client.query("savepoint f05_expected_refusal");
  try {
    await assert.rejects(run(), pattern, message);
  } finally {
    await client.query("rollback to savepoint f05_expected_refusal");
  }
}

// An ACTIVE rule reached only through the real admission path: proposed row,
// installed control, load-layer row, control binding, then Joe's atomic
// approve_rule under the Joe authority login.
async function activeRule(client, { statement, taughtBy, personalTo = null, scope = "shared" }) {
  const inserted = await client.query(
    `insert into public.rule(statement,human_quote,taught_by,personal_to,status)
     values ($1, 'Joe approved the F05 fixture.', $2, $3, 'proposed')
     returning id, version`, [statement, taughtBy, personalTo]);
  const ruleId = inserted.rows[0].id;
  const controlKey = `f05-fixture-${randomUUID()}`;
  await client.query(
    `insert into ops.enforcement_control_catalog
       (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
     values ($1,'migrations/0724_f05_live_rule_context.sql',
       'mcp-server/test/rule-context-runtime-postgres.test.mjs',
       'transactional_schema',true,now())`, [controlKey]);
  await client.query(
    `insert into ops.rule_load_layer
       (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
     values ($1::uuid,left(($1::uuid)::text,8),'control','{}'::text[],$2,
       'V5-F05 disposable PostgreSQL fixture control',
       'mcp-server/test/rule-context-runtime-postgres.test.mjs',repeat('0',64))`,
    [ruleId, scope]);
  await client.query(
    `insert into ops.rule_control_binding
       (rule_id,control_key,statement_hash,binding_contract)
     select id,$1,encode(digest(statement,'sha256'),'hex'),
       '{"fixture":"V5-F05 PostgreSQL integration"}'::jsonb
     from public.rule where id=$2`, [controlKey, ruleId]);
  await client.query("set session authorization carr_authority_joe");
  const approved = (await client.query(
    "select ops.approve_rule($1,'machine_enforceable',array[$2],$3,$4) result",
    [ruleId, controlKey, `f05-approve-${randomUUID()}`,
      "Joe local V5-F05 integration acceptance"])).rows[0].result;
  await client.query("reset session authorization");
  assert.equal(approved.policy_status, "active");
  assert.equal(approved.replayed, false);
  return { ruleId, insertedVersion: inserted.rows[0].version };
}

const INPUT = Object.freeze({
  rule_class: "workflow", mandatory: true,
  trigger: { action: ["fixture.act"], resource_class: ["fixture-resource"] },
  control_effect: { control_key: "fixture-control", effect: "require" },
  tests: ["check:f05-fixture"],
  retirement: { behavior: "permanent_until_superseded" },
});

async function universe(client) {
  return (await client.query(
    "select ops.f05_rule_universe('fixture.act','fixture-resource') result")).rows[0].result;
}

async function bindAsJoe(client, ruleId, key = randomUUID()) {
  await client.query("set session authorization carr_authority_joe");
  try {
    return (await client.query(
      "select ops.bind_f05_rule_contract($1::uuid,$2::jsonb,$3::uuid) result",
      [ruleId, INPUT, key])).rows[0].result;
  } finally {
    await client.query("reset session authorization");
  }
}

async function contractRows(client, ruleId) {
  return (await client.query(
    "select count(*)::int n from ops.rule_f05_contract where rule_id=$1", [ruleId])).rows[0].n;
}

async function withFixtureTransaction(t, body) {
  const pg = await database(t);
  if (!pg) return;
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("begin");
  try {
    await ensureAuthorityLogins(client);
    await setActorContext(client, { acting: "joe", sponsor: "joe", verified: "joe" });
    await body(client);
  } finally {
    await client.query("rollback");
    await client.end();
  }
}

test("typed contracts are append-only, actor-scoped, and missing rules stay explicit", async t => {
  await withFixtureTransaction(t, async client => {
    const joe = (await client.query("select id from public.actor where slug='joe' and active")).rows[0];
    assert.ok(joe?.id, "Joe fixture actor must exist");
    const { ruleId, insertedVersion } = await activeRule(client,
      { statement: "Use the F05 SQL fixture control.", taughtBy: joe.id });
    const activeVersion = (await client.query(
      "select version from public.rule where id=$1", [ruleId])).rows[0].version;
    assert.ok(activeVersion > insertedVersion,
      "atomic approval must advance the source rule version");

    const before = await universe(client);
    assert.ok(before.missing_rule_ids.includes(ruleId));
    assert.equal(before.active_rule_count - before.projected_rule_count,
      before.missing_rule_ids.length);

    const key = randomUUID();
    const bound = await bindAsJoe(client, ruleId, key);
    assert.equal(bound.ok, true);
    assert.equal(bound.replayed, false);
    assert.equal(bound.contract.rule_id, ruleId);
    assert.equal(bound.contract.version, activeVersion);
    assert.equal(bound.contract.binding_text, "Use the F05 SQL fixture control.");
    assert.equal(bound.contract.owner, "joe");
    assert.equal(bound.contract.provenance.source_record_id, `rule:${ruleId}`);

    const replay = await bindAsJoe(client, ruleId, key);
    assert.equal(replay.replayed, true);

    const after = await universe(client);
    assert.equal(after.missing_rule_ids.includes(ruleId), false);
    assert.ok(after.policy.rules.some(rule => rule.rule_id === ruleId));
    assert.ok(after.policy.declared_actions.includes("fixture.act"));
    assert.ok(after.policy.declared_resource_classes.includes("fixture-resource"));

    // Append-only in all three destructive shapes. The owner connection is
    // used on purpose: the trigger, not a missing grant, must refuse.
    await refusedInSavepoint(client,
      () => client.query("update ops.rule_f05_contract set contract=contract where rule_id=$1", [ruleId]),
      /append-only; UPDATE is refused/, "UPDATE on a bound contract must be refused");
    await refusedInSavepoint(client,
      () => client.query("delete from ops.rule_f05_contract where rule_id=$1", [ruleId]),
      /append-only; DELETE is refused/, "DELETE on a bound contract must be refused");
    await refusedInSavepoint(client,
      () => client.query("truncate ops.rule_f05_contract"),
      /append-only; TRUNCATE is refused/, "TRUNCATE on the contract store must be refused");
    assert.equal(await contractRows(client, ruleId), 1,
      "the bound contract and its idempotent replay leave exactly one row");
  });
});

test("with active rules and zero bound contracts the live read returns a partial receipt, not invalid_shape", async t => {
  // The production state right after #1305 shipped: every active rule is
  // unprojected, so the census projects an empty policy. This read used to
  // throw the kernel's policy.rules min-1 error.
  const { readActionContext } = await import("../src/rule-context-runtime.v5.js");
  const { verifyCoverageReceipt } = await import("../src/rule-applicability.v5.js");
  await withFixtureTransaction(t, async client => {
    const joe = (await client.query("select id from public.actor where slug='joe' and active")).rows[0];
    const { ruleId } = await activeRule(client,
      { statement: `F05 zero-bound fixture ${randomUUID()}`, taughtBy: joe.id });
    const census = await universe(client);
    assert.equal(census.projected_rule_count, 0,
      "precondition: the disposable database has no bound typed contract");
    assert.deepEqual(census.policy.rules, []);
    assert.ok(census.active_rule_count >= 1);

    const actor = { id: joe.id, slug: "joe", human: true };
    const facts = { action: "fixture.act", audience: "client", environment: "production",
      lifecycle_transition: "send", resource_class: "fixture-resource", risk_tier: "consequential" };
    const result = await readActionContext(client, actor, { facts });
    const receipt = result.coverage_receipt;
    assert.equal(result.consequential_action_permitted, false);
    assert.equal(receipt.consequential_action_permitted, false);
    assert.equal(receipt.coverage_complete, false);
    assert.equal(receipt.universe_completeness, "partial_unknown_coverage");
    assert.ok(receipt.blocking_reasons.includes("no_rule_contract_projected"));
    assert.deepEqual([...receipt.universe_rule_ids], []);
    assert.ok(receipt.missing_rule_ids.includes(ruleId), "the unbound active rule is named");
    assert.equal(receipt.missing_rule_ids.length, census.active_rule_count,
      "every active rule is listed as missing");
    assert.equal(receipt.missing_rule_ids.length,
      result.source.active_rule_count - result.source.projected_rule_count,
      "census consistency: missing = active - projected");
    assert.equal(verifyCoverageReceipt(receipt), true);

    // The first bind moves the read onto the kernel path; still fail-closed
    // while any other active rule stays unprojected.
    await bindAsJoe(client, ruleId);
    const after = await readActionContext(client, actor, { facts });
    assert.ok(after.coverage_receipt.universe_rule_ids.includes(ruleId));
    assert.equal(after.source.projected_rule_count, 1);
    assert.equal(after.source.missing_rule_ids.includes(ruleId), false);
    if (after.source.missing_rule_ids.length > 0) {
      assert.equal(after.consequential_action_permitted, false);
    }
  });
});

test("the binder's Joe authority comes from the login, never from settable session values", async t => {
  await withFixtureTransaction(t, async client => {
    const joe = (await client.query("select id from public.actor where slug='joe' and active")).rows[0];
    const { ruleId } = await activeRule(client,
      { statement: `F05 authority fixture ${randomUUID()}`, taughtBy: joe.id });
    const bindFrom = login => async () => {
      await client.query(`set session authorization ${login}`);
      return client.query(
        "select ops.bind_f05_rule_contract($1::uuid,$2::jsonb,$3::uuid) result",
        [ruleId, INPUT, randomUUID()]);
    };

    // (a1) Dell's own authority login with Dell's own values.
    await setActorContext(client, { acting: "dell", sponsor: "dell", verified: "dell" });
    await refusedInSavepoint(client, bindFrom("carr_authority_dell"),
      /requires the Joe authority login; this authority session is dell/,
      "Dell's authority login must not bind a contract");
    // (a2) Dell's authority login with every session value spoofed to joe --
    // the reproduction from the independent review of #1305 at 740abd3a.
    await setActorContext(client, { acting: "joe", sponsor: "joe", verified: "joe" });
    await refusedInSavepoint(client, bindFrom("carr_authority_dell"),
      /requires the Joe authority login; this authority session is dell/,
      "spoofed joe session values must not turn Dell's login into Joe");
    // (a3) Joe's login whose session values disagree with it is refused too.
    await setActorContext(client, { acting: "dell", sponsor: "joe", verified: "dell" });
    await refusedInSavepoint(client, bindFrom("carr_authority_joe"),
      /disagrees with the authority login/,
      "session values that disagree with the Joe login must be refused");
    // (a4) A login that is no partner authority principal at all.
    await setActorContext(client, { acting: "joe", sponsor: "joe", verified: "joe" });
    await refusedInSavepoint(client,
      () => client.query("select ops.bind_f05_rule_contract($1::uuid,$2::jsonb,$3::uuid)",
        [ruleId, INPUT, randomUUID()]),
      /not an admitted human authority principal/,
      "a non-authority login must be refused");
    assert.equal(await contractRows(client, ruleId), 0,
      "no refused attempt may leave a contract row");

    // The same rule binds under the real Joe login with agreeing values.
    const bound = await bindAsJoe(client, ruleId);
    assert.equal(bound.ok, true);
    assert.equal((await client.query(
      `select a.slug from ops.rule_f05_contract c join public.actor a on a.id=c.bound_by
        where c.rule_id=$1`, [ruleId])).rows[0].slug, "joe");
  });
});

test("another partner's personal rule is outside Joe's universe and inside that partner's", async t => {
  await withFixtureTransaction(t, async client => {
    const dell = (await client.query("select id from public.actor where slug='dell' and active")).rows[0];
    assert.ok(dell?.id, "Dell fixture actor must exist");
    const { ruleId } = await activeRule(client, {
      statement: `F05 Dell-personal fixture ${randomUUID()}`,
      taughtBy: dell.id, personalTo: dell.id, scope: "dell",
    });

    const joeView = await universe(client);
    assert.equal(joeView.missing_rule_ids.includes(ruleId), false,
      "Dell's personal rule must not appear in Joe's census");
    assert.equal(joeView.policy.rules.some(rule => rule.rule_id === ruleId), false);

    await setActorContext(client, { acting: "dell", sponsor: "dell", verified: "dell" });
    const dellView = await universe(client);
    assert.equal(dellView.missing_rule_ids.includes(ruleId), true,
      "the same rule is visible (and unprojected) in Dell's own census");
    assert.equal(dellView.active_rule_count, joeView.active_rule_count + 1,
      "the only difference between the two censuses is Dell's personal rule");
  });
});

test("a contract bound to an older rule version reads as missing after the rule is amended", async t => {
  await withFixtureTransaction(t, async client => {
    const joe = (await client.query("select id from public.actor where slug='joe' and active")).rows[0];
    const { ruleId } = await activeRule(client,
      { statement: `F05 amendment fixture ${randomUUID()}`, taughtBy: joe.id });
    const bound = await bindAsJoe(client, ruleId);
    const boundVersion = bound.contract.version;
    assert.equal((await universe(client)).missing_rule_ids.includes(ruleId), false,
      "freshly bound rule is projected");

    await client.query("set session authorization carr_authority_joe");
    await client.query(
      "select ops.amend_rule_statement($1,$2,$3,$4)",
      [ruleId, `F05 amended fixture ${randomUUID()}`, `f05-amend-${randomUUID()}`,
        "V5-F05 stale-contract acceptance"]);
    await client.query("reset session authorization");
    const current = (await client.query(
      "select status, version from public.rule where id=$1", [ruleId])).rows[0];
    assert.equal(current.status, "active", "amendment keeps the rule active");
    assert.ok(current.version > boundVersion, "amendment advances the rule version");

    const after = await universe(client);
    assert.equal(after.missing_rule_ids.includes(ruleId), true,
      "a contract for the superseded version and statement must not count as current");
    assert.equal(after.policy.rules.some(rule => rule.rule_id === ruleId), false);
    assert.equal(after.policy.completeness, "partial_unknown_coverage");
  });
});
