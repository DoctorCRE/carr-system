import { readMigration } from "./migration-source.testhelper.mjs";
// self-closing-records-sql.test.mjs — the SQL half of migration 0872, run on a
// real disposable PostgreSQL rather than a fake that answers marker comments.
//
// These are the predicates the technical checks stand on, and a predicate
// tested only through a fake is a predicate nobody has watched decide:
//
//   1. ops.work_request_bound_evidence_verdict: a Work Request with ONE
//      criterion unbound is not complete; every criterion bound is.
//   2. ops.work_request_evidence_receipt_kind: only a succeeded run, a complete
//      deployment, a defect resolution or a resolved incident counts, a
//      malformed reference answers null instead of raising, and an uppercase
//      uuid is the same receipt.
//   3. ops.work_request_evidence_receipt_mismatch: a receipt that resolves is
//      still refused unless it is tied to THIS request, naming the mismatch.
//   4. ops.work_request_completion_actor: only a human actor or a sponsored
//      agent binds or completes; an unsponsored runtime is refused (42501).
//   5. public.defect_resolution: one close per defect, evidence of a fixed
//      shape that the server already holds (a bare sha or an unknown PR is
//      refused by the table) AND that names the defect or a Work Request
//      naming it (a real shipped commit about something else is refused), and
//      no update, delete or truncate once written.
//   6. One receipt binds to one criterion, ever.
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

const migration = readMigration(path.join(root, "migrations/0872_self_closing_records.sql"));
const schema = readFileSync(path.join(root, "db/schema.sql"), "utf8");
const lift = (pattern, label) => {
  const found = migration.match(pattern)?.[0];
  assert.ok(found, `migration 0872 must define ${label}`);
  return found;
};
const table = (name) => {
  const found = schema.match(new RegExp(`CREATE TABLE ${name.replaceAll(".", "\\.")} \\([\\s\\S]*?\\n\\);`))?.[0];
  assert.ok(found, `current schema must define ${name}`);
  return found;
};

const uuid = (n) => `aa000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const JOE = uuid(1);
const CODEX = uuid(2);
const RUNNER = uuid(3);
const schemaFunction = (name) => {
  const found = schema.match(new RegExp(`CREATE FUNCTION ${name.replaceAll(".", "\\.")}\\([\\s\\S]*?\\n\\$\\$;`))?.[0];
  assert.ok(found, `current schema must define ${name}`);
  return found;
};

test("0872's predicates decide on a real PostgreSQL", { skip: !bin && "local PostgreSQL binaries unavailable" }, async () => {
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
    await c.query(schemaFunction("ops.authority_login_slug"));
    for (const name of ["public.actor", "public.defect", "ops.run", "ops.deployment", "ops.incident",
                        "ops.incident_link", "ops.release", "ops.release_slice_member", "ops.work_request"])
      await c.query(table(name));
    await c.query(`alter table public.actor add primary key (id); alter table public.defect add primary key (id);
                   alter table ops.work_request add primary key (id)`);
    await c.query(lift(/create table public\.defect_resolution \([\s\S]*?\n\);/, "public.defect_resolution"));
    await c.query(lift(/create function public\.defect_resolution_is_append_only\(\)[\s\S]*?end \$fn\$;/, "the append-only trigger"));
    await c.query(lift(/create function ops\.work_request_evidence_receipt_normalized\([\s\S]*?\n\$fn\$;/, "the receipt normaliser"));
    await c.query(lift(/create function ops\.defect_resolution_evidence_receipt\([\s\S]*?end \$fn\$;/, "the defect evidence resolver"));
    await c.query(lift(/create function ops\.defect_resolution_evidence_mismatch\([\s\S]*?end \$fn\$;/, "the defect relevance check"));
    await c.query(lift(/create function public\.defect_resolution_evidence_must_resolve\(\)[\s\S]*?end \$fn\$;/, "the evidence trigger"));
    await c.query(lift(/create trigger defect_resolution_evidence_resolves[\s\S]*?;/, "the evidence insert trigger"));
    await c.query(lift(/create trigger defect_resolution_append_only[\s\S]*?;/, "the row trigger"));
    await c.query(lift(/create trigger defect_resolution_no_truncate[\s\S]*?;/, "the truncate trigger"));
    await c.query(lift(/create function ops\.work_request_evidence_receipt_kind\(p_receipt text\)[\s\S]*?end \$fn\$;/, "the receipt resolver"));
    await c.query(lift(/create function ops\.work_request_evidence_receipt_mismatch\([\s\S]*?end \$fn\$;/, "the request tie"));
    await c.query(lift(/create function ops\.work_request_completion_actor\(\)[\s\S]*?end \$fn\$;/, "the actor gate"));
    await c.query(lift(/create function ops\.work_request_bound_evidence_verdict\([\s\S]*?\n\$fn\$;/, "the completion predicate"));
    await c.query(lift(/create table ops\.work_request_criterion_evidence \([\s\S]*?\n\);/, "the criterion evidence table"));
    await c.query(lift(/create unique index work_request_criterion_evidence_receipt_once[\s\S]*?;/, "the one-criterion-per-receipt index"));

    await c.query(`insert into public.actor (id, slug, display_name, kind) values
      ($1, 'joe', 'Joe', 'human'), ($2, 'codex', 'Codex', 'automation'), ($3, 'runner', 'Runner', 'system')`,
      [JOE, CODEX, RUNNER]);
    const newDefect = async (cls) => (await c.query(`insert into public.defect (defect_class, claimed, actual, detected_by, created_by)
      values ($1, 'reloads', 'cached', 'self', $2) returning id`, [cls, JOE])).rows[0].id;
    const defect = await newDefect("stale-credential-cache");
    const otherDefect = await newDefect("unrelated");
    const WR = "WR-000201";
    const wr = (await c.query(`insert into ops.work_request (ref, title, desired_outcome, acceptance_criteria, requester_actor)
      values ($1, 'Defect class stale-credential-cache: 1 unaddressed', $2, $3, 'joe') returning id`,
      [WR, `defect:${defect} is fixed`, JSON.stringify([{ id: "SHIPPED", text: "shipped" }])])).rows[0].id;

    const refused = async (sql, params, label, pattern) => {
      await c.query("savepoint s");
      try { await c.query(sql, params); } catch (err) {
        await c.query("rollback to savepoint s");
        if (pattern) assert.match(err.message, pattern, label);
        return err;
      }
      throw new Error(`${label} was accepted`);
    };

    // ── the receipt resolver ────────────────────────────────────────────────
    const kind = async (ref) => (await c.query("select ops.work_request_evidence_receipt_kind($1) as k", [ref])).rows[0].k;
    const mismatch = async (ref) => (await c.query("select ops.work_request_evidence_receipt_mismatch($1, $2) as m", [ref, wr])).rows[0].m;
    const service = uuid(50);
    const release = async (workRequestRef) => (await c.query(`insert into ops.release (release_key, service_id, environment,
        git_sha, maker_actor, source_kind, source_ref, work_request_ref)
      values ($1, $2, 'production', $3, 'claude', 'collector', 'fixture', $4) returning id`,
      [`rel-${Math.random()}`, service, "f".repeat(40), workRequestRef])).rows[0].id;
    const ownRelease = await release(WR);
    const strangerRelease = await release("WR-000188");
    const run = async (state, { runKey = "nightly", releaseId = null } = {}) => (await c.query(`insert into ops.run (kind, service_id, environment, run_key, state,
        failure_class, started_at, ended_at, source_kind, source_ref, release_id)
      values ('job', $1, 'production', $3, $2, case when $2 = 'failed' then 'exit_1' end,
        now(), now(), 'collector', 'fixture', $4)
      returning id`, [service, state, runKey, releaseId])).rows[0].id;
    const green = await run("succeeded", { releaseId: ownRelease });
    const red = await run("failed", { releaseId: ownRelease });
    const strangerGreen = await run("succeeded", { releaseId: strangerRelease });
    const namingGreen = await run("succeeded", { runKey: `ci ${WR} build` });
    const nearMiss = await run("succeeded", { runKey: `${WR}0 build` });
    assert.equal(await kind(`ops.run:${green}`), "run");
    assert.equal(await kind(`ops.run:${green.toUpperCase()}`), "run", "an uppercase uuid is the same receipt");
    assert.equal(await kind(`OPS.RUN:${green.toUpperCase()}`), "run");
    assert.equal(await kind(`ops.run:${red}`), null, "a failed run is not a receipt");
    assert.equal(await kind(`ops.run:${uuid(999)}`), null, "a run that does not exist is not a receipt");
    for (const malformed of ["ops.run:not-a-uuid", "ops.run:------------------------------------",
                             "fixed it", "", null, "INC-20260901-03", "incident:INC-1"])
      assert.equal(await kind(malformed), null, `${malformed} must answer null, not raise`);

    // ── the request tie: a resolving receipt must be evidence for THIS request ─
    assert.equal(await mismatch(`ops.run:${green}`), null, "a run of the request's own release is its evidence");
    assert.equal(await mismatch(`ops.run:${green.toUpperCase()}`), null);
    assert.equal(await mismatch(`ops.run:${namingGreen}`), null, "a run naming the request is its evidence");
    assert.match(await mismatch(`ops.run:${strangerGreen}`), /^run_not_linked_to_work_request: .*WR-000201/,
      "a green run of another request's release is not evidence for this one");
    assert.match(await mismatch(`ops.run:${nearMiss}`), /^run_not_linked_to_work_request/,
      "WR-0002010 does not name WR-000201");
    assert.match(await mismatch(`ops.run:${red}`), /^receipt_unresolved/);

    const deployment = async (releaseId) => (await c.query(`insert into ops.deployment (service_id, environment, state,
        started_at, ended_at, read_back_at, source_kind, source_ref, release_id)
      values ($1, 'production', 'complete', now(), now(), now(), 'collector', 'fixture', $2) returning id`,
      [service, releaseId])).rows[0].id;
    const member = async (releaseId, subject, { sha = "c".repeat(40), pr = null } = {}) =>
      (await c.query(`insert into ops.release_slice_member (release_id, release_git_sha, slice_id, commit_sha, pr_number,
        subject, attribution, recorded_by_actor_slug)
      values ($1, $2, 'V5-F09', $3, $4, $5, 'explicit', 'claude') returning id`,
      [releaseId, "f".repeat(40), sha, pr, subject])).rows[0].id;
    const shippingRelease = await release(null);
    const shippedMember = await member(shippingRelease, `feat: close stale credentials (${WR}) (#1711)`,
      { sha: "abcdef0123456789abcdef0123456789abcdef01", pr: 1711 });
    const quietRelease = await release(null);
    await member(quietRelease, "chore: unrelated (#1712)", { sha: "1".repeat(40), pr: 1712 });
    assert.equal(await mismatch(`ops.deployment:${await deployment(shippingRelease)}`), null,
      "a deployment shipping a slice member that names the request is its evidence");
    assert.match(await mismatch(`ops.deployment:${await deployment(quietRelease)}`),
      /^deployment_not_linked_to_work_request/, "a deployment that ships nothing naming the request is not");

    const incident = async (ref, state) => c.query(`insert into ops.incident (ref, title, severity, state, environment,
        detected_source, detected_at, source_kind, source_ref, observed_at, recovery_evidence_ref, monitoring_until, resolved_at)
      values ($1, 'fixture', 'SEV-3', $2, 'production', 'fixture', now(), 'collector', 'fixture', now(),
        case when $2 in ('resolved','reviewed') then 'ops.run:x' end,
        case when $2 in ('resolved','reviewed') then now() end,
        case when $2 in ('resolved','reviewed') then now() end) returning id`, [ref, state]);
    const linked = (await incident("INC-20260901-03", "resolved")).rows[0].id;
    await incident("INC-20260901-04", "monitoring");
    await incident("INC-20260901-05", "resolved");
    await c.query("insert into ops.incident_link (incident_id, kind, ref) values ($1, 'work_request', $2)", [linked, WR]);
    assert.equal(await kind("incident:INC-20260901-03"), "incident");
    assert.equal(await kind("incident:INC-20260901-04"), null, "an incident still being watched is not a receipt");
    assert.equal(await mismatch("incident:INC-20260901-03"), null);
    assert.match(await mismatch("incident:INC-20260901-05"), /^incident_not_linked_to_work_request/,
      "a resolved incident nobody linked to the request is not its evidence");

    // ── defect resolution: the close receipt, on evidence the server holds ──
    const close = (defectId, evidenceKind, ref, key) => c.query(`insert into public.defect_resolution
        (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, $2, $3, $4, $5) returning id`, [defectId, evidenceKind, ref, key, JOE]);
    const resolver = async (evidenceKind, ref) =>
      (await c.query("select ops.defect_resolution_evidence_receipt($1, $2) as r", [evidenceKind, ref])).rows[0].r;
    await c.query("begin");
    await refused(`insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, 'commit', 'abcdef9', 'k-bare', $2)`, [defect, JOE], "a bare sha the server holds no record of",
      /defect_resolution_evidence_unresolved/);
    await refused(`insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, 'pull_request', '999999', 'k-pr', $2)`, [defect, JOE], "a PR nobody recorded as merged",
      /defect_resolution_evidence_unresolved/);
    await refused(`insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, 'receipt', $3, 'k-red', $2)`, [defect, JOE, `ops.run:${red}`], "a failed run as fix evidence",
      /defect_resolution_evidence_unresolved/);

    // ── relevance: real shipped evidence about something else closes nothing ─
    const relevance = async (defectId, evidenceKind, ref) =>
      (await c.query("select ops.defect_resolution_evidence_mismatch($1, $2, $3) as m", [defectId, evidenceKind, ref])).rows[0].m;
    assert.equal(await resolver("commit", "1111111"), `release-slice-member:${(await c.query(
      "select id from ops.release_slice_member where pr_number = 1712")).rows[0].id}`,
      "the unrelated commit really is merged and shipped");
    await refused(`insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, 'commit', '1111111', 'k-unrelated-commit', $2)`, [defect, JOE],
      "an unrelated shipped commit as this defect's fix", /^defect_resolution_evidence_not_about_defect: commit 1111111 .*WR-000201/);
    await refused(`insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, 'pull_request', '1712', 'k-unrelated-pr', $2)`, [defect, JOE],
      "an unrelated merged pull request", /^defect_resolution_evidence_not_about_defect/);
    await refused(`insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, 'receipt', $3, 'k-unrelated-run', $2)`, [otherDefect, JOE, `ops.run:${namingGreen}`],
      "a green run naming a request that does not name this defect", /^defect_resolution_evidence_not_about_defect/);
    await refused(`insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, 'commit', 'abcdef0', 'k-other-commit', $2)`, [otherDefect, JOE],
      "a commit naming another defect's request", /^defect_resolution_evidence_not_about_defect/);
    await refused(`insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, 'receipt', $3, 'k-other-deploy', $2)`, [otherDefect, JOE, `ops.deployment:${await deployment(shippingRelease)}`],
      "a deployment shipping only another defect's fix", /^defect_resolution_evidence_not_about_defect/);
    await refused(`insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values ($1, 'receipt', 'incident:INC-20260901-05', 'k-other-incident', $2)`, [otherDefect, JOE],
      "a resolved incident linked to nothing about this defect", /^defect_resolution_evidence_not_about_defect/);
    await c.query("commit");
    assert.equal(await relevance(defect, "commit", "abcdef0"), null,
      "a shipped commit whose subject names a Work Request that names the defect is its fix");
    assert.equal(await relevance(defect, "pull_request", "1711"), null);
    assert.equal(await relevance(defect, "receipt", `ops.deployment:${await deployment(shippingRelease)}`), null,
      "a deployment shipping that commit is its fix");
    const namedByIdRelease = await release(null);
    await member(namedByIdRelease, `fix: defect ${otherDefect.toUpperCase()} (#1713)`, { sha: "2".repeat(40), pr: 1713 });
    assert.equal(await relevance(otherDefect, "commit", "2222222"), null, "a subject naming the defect id itself, in any case");
    await member(namedByIdRelease, `fix: defect ${otherDefect}0 (#1714)`, { sha: "3".repeat(40), pr: 1714 });
    assert.match(await relevance(otherDefect, "commit", "3333333"), /^defect_resolution_evidence_not_about_defect/,
      "a longer token that merely starts with the id does not name it");
    const incidentFix = (await incident("INC-20260901-06", "resolved")).rows[0].id;
    await c.query("insert into ops.incident_link (incident_id, kind, ref) values ($1, 'defect', $2)", [incidentFix, otherDefect]);
    assert.equal(await relevance(otherDefect, "receipt", "incident:INC-20260901-06"), null, "an incident linked to the defect");
    assert.equal(await relevance(defect, "receipt", "incident:INC-20260901-03"), null,
      "an incident linked to a Work Request that names the defect");
    assert.match(await relevance(defect, "commit", "abcdef9"), /^defect_resolution_evidence_unresolved/,
      "evidence that does not exist is refused as unresolved first");
    assert.equal(await resolver("commit", "abcdef0"), `release-slice-member:${shippedMember}`,
      "a merged commit the server recorded resolves, by prefix when the prefix is unique");
    assert.equal(await resolver("pull_request", "1711"), `release-slice-member:${shippedMember}`);
    assert.equal(await resolver("receipt", `ops.run:${green.toUpperCase()}`), `ops.run:${green}`);
    const resolution = (await close(defect, "commit", "abcdef0", "k-1")).rows[0].id;
    const otherFix = await run("succeeded", { runKey: `fix defect:${otherDefect}` });
    const otherResolution = (await close(otherDefect, "receipt", `ops.run:${otherFix}`, "k-o")).rows[0].id;
    assert.equal(await kind(`defect-resolution:${resolution}`), "defect_resolution");
    assert.equal(await mismatch(`defect-resolution:${resolution}`), null, "the close of a defect the request names");
    assert.match(await mismatch(`defect-resolution:${otherResolution}`), /^defect_not_linked_to_work_request/,
      "the close of a defect the request does not name is not its evidence");
    await c.query("begin");
    await refused("insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by) values ($1,'pull_request','1711','k-2',$2)",
      [defect, JOE], "a second close of the same defect");
    await refused("update public.defect_resolution set evidence_ref='def5678' where id=$1", [resolution], "rewriting a close");
    await refused("delete from public.defect_resolution where id=$1", [resolution], "deleting a close");
    await refused("truncate public.defect_resolution", [], "truncating the closes");
    const third = await newDefect("x");
    for (const [evidenceKind, ref] of [["commit", "abc"], ["pull_request", "0"], ["receipt", "fixed it"], ["memory", "abc1234"]])
      await refused("insert into public.defect_resolution (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by) values ($1,$2,$3,$4,$5)",
        [third, evidenceKind, ref, `k-${evidenceKind}-${ref}`, JOE], `${evidenceKind}:${ref}`);
    await c.query("commit");

    // ── who may bind or complete ────────────────────────────────────────────
    const actorAs = async (slug, sponsor) => {
      await c.query("begin");
      await c.query("select set_config('carr.acting_actor_slug', $1, true), set_config('carr.sponsoring_human_slug', $2, true)",
        [slug, sponsor]);
      try {
        return { slug: (await c.query("select (ops.work_request_completion_actor()).slug as s")).rows[0].s };
      } catch (err) { return { code: err.code, message: err.message }; }
      finally { await c.query("rollback"); }
    };
    assert.deepEqual(await actorAs("joe", ""), { slug: "joe" }, "a human actor");
    assert.deepEqual(await actorAs("codex", "joe"), { slug: "codex" }, "a sponsored agent");
    const unsponsored = await actorAs("codex", "");
    assert.equal(unsponsored.code, "42501", "an unsponsored agent is refused");
    assert.match(unsponsored.message, /^work_request_completion_requires_sponsored_actor/);
    const stranger = await actorAs("codex", "mallory");
    assert.equal(stranger.code, "42501", "a sponsor that is not a partner is no sponsor");
    assert.equal((await actorAs("runner", "joe")).code, "42501", "a system actor never completes");
    assert.match((await actorAs("", "joe")).message, /^work_request_completion_actor_unresolved/);

    // ── one receipt, one criterion ──────────────────────────────────────────
    await c.query(`insert into ops.work_request_criterion_evidence
      (work_request_id, criterion_id, receipt_ref, receipt_kind, bound_by_actor_id, idempotency_key)
      values ($1, 'SHIPPED', $2, 'run', $3, $4)`, [wr, `ops.run:${green}`, CODEX, uuid(60)]);
    await c.query("begin");
    await refused(`insert into ops.work_request_criterion_evidence
      (work_request_id, criterion_id, receipt_ref, receipt_kind, bound_by_actor_id, idempotency_key)
      values ($1, 'OBSERVED', $2, 'run', $3, $4)`, [wr, `ops.run:${green}`, CODEX, uuid(61)],
      "one receipt bound to a second criterion", /work_request_criterion_evidence_receipt_once/);
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
