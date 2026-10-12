// self-closing-records-sql.test.mjs — the SQL half of migration 0862, run on a
// real disposable PostgreSQL rather than a fake that answers marker comments.
//
// Three things are proven here because they are the predicates, and a predicate
// tested only through a fake is a predicate nobody has watched decide:
//
//   1. ops.work_request_bound_evidence_verdict: a Work Request with ONE
//      criterion unbound is not complete; every criterion bound is.
//   2. ops.work_request_evidence_receipt_kind: only a succeeded run, a complete
//      deployment, a defect resolution or a resolved incident counts, and a
//      malformed reference answers null instead of raising.
//   3. public.defect_resolution: one close per defect, evidence of a fixed
//      shape, and no update, delete or truncate once written.
//
// The tables are the current db/schema.sql definitions; the objects under test
// are lifted from the migration file itself, never retyped.
//
// Run with: node --test mcp-server/test/self-closing-records-sql.test.mjs

import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from "./helpers/disposable-postgres.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import pg from "pg";

const root = fileURLToPath(new URL("../../", import.meta.url));
let bin = null;
for (const pgConfig of ["pg_config", "/opt/homebrew/opt/postgresql@17/bin/pg_config", "/usr/lib/postgresql/17/bin/pg_config"]) {
  try {
    const dir = execFileSync(pgConfig, ["--bindir"], { encoding: "utf8" }).trim();
    if (existsSync(path.join(dir, "postgres"))) { bin = dir; break; }
  } catch { /* try the next candidate */ }
}

const migration = readFileSync(path.join(root, "migrations/0862_self_closing_records.sql"), "utf8");
const schema = readFileSync(path.join(root, "db/schema.sql"), "utf8");
const lift = (pattern, label) => {
  const found = migration.match(pattern)?.[0];
  assert.ok(found, `migration 0862 must define ${label}`);
  return found;
};
const table = (name) => {
  const found = schema.match(new RegExp(`CREATE TABLE ${name.replaceAll(".", "\\.")} \\([\\s\\S]*?\\n\\);`))?.[0];
  assert.ok(found, `current schema must define ${name}`);
  return found;
};

const uuid = (n) => `aa000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const JOE = uuid(1);

test("0862's predicates decide on a real PostgreSQL", { skip: !bin && "local PostgreSQL binaries unavailable" }, async () => {
  let fixture;
  let c;
  const releaseBudget = await acquirePostgresFixtureGroup();
  try {
    fixture = await acquireDisposablePostgres({ prefix: "self-closing-", pgCtl: path.join(bin, "pg_ctl"), dataName: "." });
    const dir = fixture.root;
    await fixture.run(path.join(bin, "initdb"), ["-D", dir, "-U", "fixture", "--auth=trust", "--no-locale"]);
    await fixture.run(path.join(bin, "pg_ctl"), ["-D", dir, "-l", path.join(dir, "server.log"),
      "-o", `-k ${dir} -h '' -c timezone=UTC`, "-w", "start"]);
    c = new pg.Client({ host: dir, user: "fixture", database: "postgres" });
    await c.connect();

    await c.query("create schema ops");
    for (const name of ["public.actor", "public.defect", "ops.run", "ops.deployment", "ops.incident"])
      await c.query(table(name));
    await c.query("alter table public.actor add primary key (id); alter table public.defect add primary key (id)");
    await c.query(lift(/create table public\.defect_resolution \([\s\S]*?\n\);/, "public.defect_resolution"));
    await c.query(lift(/create function public\.defect_resolution_is_append_only\(\)[\s\S]*?end \$fn\$;/, "the append-only trigger"));
    await c.query(lift(/create trigger defect_resolution_append_only[\s\S]*?;/, "the row trigger"));
    await c.query(lift(/create trigger defect_resolution_no_truncate[\s\S]*?;/, "the truncate trigger"));
    await c.query(lift(/create function ops\.work_request_evidence_receipt_kind\(p_ref text\)[\s\S]*?end \$fn\$;/, "the receipt resolver"));
    await c.query(lift(/create function ops\.work_request_bound_evidence_verdict\([\s\S]*?\n\$fn\$;/, "the completion predicate"));

    await c.query("insert into public.actor (id, slug, display_name, kind) values ($1, 'joe', 'Joe', 'human')", [JOE]);
    const defect = (await c.query(`insert into public.defect (defect_class, claimed, actual, detected_by, created_by)
      values ('stale-credential-cache', 'reloads', 'cached', 'self', $1) returning id`, [JOE])).rows[0].id;

    // ── the receipt resolver ────────────────────────────────────────────────
    const kind = async (ref) => (await c.query("select ops.work_request_evidence_receipt_kind($1) as k", [ref])).rows[0].k;
    const service = uuid(50);
    const run = async (state) => (await c.query(`insert into ops.run (kind, service_id, environment, run_key, state,
        failure_class, started_at, ended_at, source_kind, source_ref)
      values ('job', $1, 'production', 'nightly', $2, case when $2 = 'failed' then 'exit_1' end,
        now(), now(), 'collector', 'fixture')
      returning id`, [service, state])).rows[0].id;
    const green = await run("succeeded");
    const red = await run("failed");
    assert.equal(await kind(`ops.run:${green}`), "run");
    assert.equal(await kind(`ops.run:${red}`), null, "a failed run is not a receipt");
    assert.equal(await kind(`ops.run:${uuid(999)}`), null, "a run that does not exist is not a receipt");
    for (const malformed of ["ops.run:not-a-uuid", "ops.run:------------------------------------",
                             "fixed it", "", null, "INC-20260901-03", "incident:INC-1"])
      assert.equal(await kind(malformed), null, `${malformed} must answer null, not raise`);

    const incident = async (ref, state) => c.query(`insert into ops.incident (ref, title, severity, state, environment,
        detected_source, detected_at, source_kind, source_ref, observed_at, recovery_evidence_ref, monitoring_until, resolved_at)
      values ($1, 'fixture', 'SEV-3', $2, 'production', 'fixture', now(), 'collector', 'fixture', now(),
        case when $2 in ('resolved','reviewed') then 'ops.run:x' end,
        case when $2 in ('resolved','reviewed') then now() end,
        case when $2 in ('resolved','reviewed') then now() end)`, [ref, state]);
    await incident("INC-20260901-03", "resolved");
    await incident("INC-20260901-04", "monitoring");
    assert.equal(await kind("incident:INC-20260901-03"), "incident");
    assert.equal(await kind("incident:INC-20260901-04"), null, "an incident still being watched is not a receipt");

    // ── defect resolution: the close receipt ────────────────────────────────
    const resolution = (await c.query(`insert into public.defect_resolution
        (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, 'commit', 'abc1234', 'k-1', $2) returning id`, [defect, JOE])).rows[0].id;
    assert.equal(await kind(`defect-resolution:${resolution}`), "defect_resolution");
    const refused = async (sql, params, label) => {
      await c.query("savepoint s");
      try { await c.query(sql, params); } catch { await c.query("rollback to savepoint s"); return; }
      throw new Error(`${label} was accepted`);
    };
    await c.query("begin");
    await refused("insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by) values ($1,'commit','def5678','k-2',$2)",
      [defect, JOE], "a second close of the same defect");
    await refused("update public.defect_resolution set evidence_ref='def5678' where id=$1", [resolution], "rewriting a close");
    await refused("delete from public.defect_resolution where id=$1", [resolution], "deleting a close");
    await refused("truncate public.defect_resolution", [], "truncating the closes");
    const other = (await c.query(`insert into public.defect (defect_class, claimed, actual, detected_by)
      values ('x', 'a', 'b', 'self') returning id`)).rows[0].id;
    for (const [evidenceKind, ref] of [["commit", "abc"], ["pull_request", "0"], ["receipt", "fixed it"], ["memory", "abc1234"]])
      await refused("insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by) values ($1,$2,$3,$4,$5)",
        [other, evidenceKind, ref, `k-${evidenceKind}-${ref}`, JOE], `${evidenceKind}:${ref}`);
    await c.query("commit");

    // ── the completion predicate ────────────────────────────────────────────
    const verdict = async (criteria, bindings) =>
      (await c.query("select ops.work_request_bound_evidence_verdict($1::jsonb, $2::jsonb) as v",
        [JSON.stringify(criteria), JSON.stringify(bindings)])).rows[0].v;
    const criteria = [{ id: "OBSERVED", text: "seen" }, { id: "SHIPPED", text: "shipped" }];
    const shipped = { criterion_id: "SHIPPED", receipt_ref: `ops.run:${green}`, receipt_kind: "run", binding_id: uuid(7) };
    const observed = { criterion_id: "OBSERVED", receipt_ref: "incident:INC-20260901-03", receipt_kind: "incident", binding_id: uuid(8) };

    const oneUnbound = await verdict(criteria, [shipped]);
    assert.equal(oneUnbound.complete, false, "completion must not fire with one criterion unbound");
    assert.deepEqual(oneUnbound.missing, ["OBSERVED"]);

    const stale = await verdict(criteria, [shipped, { ...observed, receipt_kind: null }]);
    assert.equal(stale.complete, false, "a receipt that no longer resolves does not count");
    assert.deepEqual(stale.missing, ["OBSERVED"]);

    const all = await verdict(criteria, [shipped, observed]);
    assert.equal(all.complete, true);
    assert.deepEqual(all.missing, []);
    assert.deepEqual(all.receipts.map(r => r.receipt_ref), ["incident:INC-20260901-03", `ops.run:${green}`]);

    const stray = await verdict(criteria, [shipped, observed, { ...shipped, criterion_id: "NOT-A-CRITERION" }]);
    assert.equal(stray.receipts.length, 2, "a binding for a criterion the request does not have is not a receipt for it");

    assert.equal((await verdict([], [])).complete, false, "a request with no criteria never completes on evidence");
    assert.equal((await verdict(null, null)).complete, false);
  } finally {
    await c?.end().catch(() => {});
    try { await fixture?.close(); } finally { await releaseBudget(); }
  }
});
