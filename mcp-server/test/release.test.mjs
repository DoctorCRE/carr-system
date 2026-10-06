// release.test.mjs — unit coverage for the /release payload builder (Phase 1,
// 2026-08-13, closing the deploy-provenance gap named in the Phase 0 audit).
//
// release.js holds no cloudflare: import (unlike index.js, which cannot be
// loaded under plain node — see its own header note), so this exercises the
// real payload-building logic with a fake env and a fake `sql` tag function.
// No live database, no deploy, no KV.
//
//   node --test mcp-server/test/release.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { buildRelease } from "../src/release.js";
import { mutationManifestIdentity } from "../src/mutation-registry.js";

const FIXED_NOW = () => new Date("2026-08-13T21:00:00.000Z");
const SCHEMA_LEDGER_SHA256 = "sha256:" + "7".repeat(64);

// A minimal stand-in for neon()'s tagged-template `sql` function. Routes each
// call by matching a substring of the query text against `responses`, so
// tests read as "when asked about X, answer Y" rather than depending on
// exact SQL formatting.
function fakeSql(responses) {
  return async (strings, ..._values) => {
    const q = strings.join(" ");
    for (const [needle, result] of responses) {
      if (q.includes(needle)) {
        if (result instanceof Error) throw result;
        if (needle === "v_schema_ledger" && Array.isArray(result)) {
          return result.map((row) => ({
            ...row,
            ledger_sha256: row.ledger_sha256 ?? SCHEMA_LEDGER_SHA256,
          }));
        }
        return result;
      }
    }
    throw new Error("release.test.mjs: unmocked query: " + q);
  };
}

test("buildRelease pins highest migration to C collation", async () => {
  let schemaQuery = "";
  const sql = async (strings) => {
    const query = strings.join(" ");
    if (query.includes("v_schema_ledger")) {
      schemaQuery = query;
      return [{ applied_count: 2, highest_applied_migration: "0494a_reference.sql" }];
    }
    if (query.includes("doctrine_meta")) return [{ generation: 1 }];
    throw new Error(`unmocked query: ${query}`);
  };
  await buildRelease({ env: { GIT_SHA: "a".repeat(40) }, sql, verbCount: 1, now: FIXED_NOW });
  assert.match(schemaQuery, /max\(filename collate "C"\) collate "default"/);
});

test("buildRelease: full shape when everything is reachable and stamped", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 101, highest_applied_migration: "0099_thing.sql" }]],
    ["doctrine_meta", [{ generation: 42 }]],
  ]);
  const out = await buildRelease({
    env: {
      GIT_SHA: "a".repeat(40),
      CF_VERSION_METADATA: {
        id: "cf-version-123",
        tag: "release-a",
        timestamp: "2026-08-16T18:00:00.000Z",
      },
    },
    sql,
    verbCount: 103,
    now: FIXED_NOW,
  });

  assert.equal(out.ok, true);
  assert.equal(out.ts, "2026-08-13T21:00:00.000Z");
  assert.equal(out.verb_count, 103);
  assert.equal(out.provider, "cloudflare-workers");
  assert.deepEqual(out.worker_version, {
    id: "cf-version-123",
    tag: "release-a",
    timestamp: "2026-08-16T18:00:00.000Z",
    reason: null,
  });
  assert.deepEqual(out.git_sha, { value: "a".repeat(40), reason: null });
  assert.equal(out.schema.highest_applied_migration, "0099_thing.sql");
  assert.equal(out.schema.applied_count, 101);
  assert.equal(out.schema.ledger_sha256, SCHEMA_LEDGER_SHA256);
  assert.equal(out.schema.reason, null);
  // The honesty note belongs on every response, success included — the field
  // is "what the tracking table claims" even when the table is reachable.
  assert.match(out.schema.note, /not ground truth/);
  assert.deepEqual(out.doctrine_generation, { value: 42, reason: null });
});

test("buildRelease: missing Cloudflare version metadata is explicit and never falls back to git SHA", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const sha = "f".repeat(40);
  const out = await buildRelease({ env: { GIT_SHA: sha }, sql, verbCount: 1, now: FIXED_NOW });

  assert.equal(out.provider, "cloudflare-workers");
  assert.deepEqual(out.worker_version, {
    id: null,
    tag: null,
    timestamp: null,
    reason: "CF_VERSION_METADATA binding is unavailable; no Cloudflare Worker version identity was observed",
  });
  assert.notEqual(out.worker_version.id, sha);
});

test("buildRelease: malformed Cloudflare version metadata never yields a partial identity", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const out = await buildRelease({
    env: { CF_VERSION_METADATA: { tag: "candidate-without-id", timestamp: "2026-08-16T18:00:00.000Z" } },
    sql, verbCount: 1, now: FIXED_NOW,
  });

  assert.deepEqual(out.worker_version, {
    id: null,
    tag: null,
    timestamp: null,
    reason: "CF_VERSION_METADATA has no version id; no Cloudflare Worker version identity was observed",
  });
});

test("buildRelease: git_sha reports null + a specific reason when GIT_SHA was never stamped", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const out = await buildRelease({ env: {}, sql, verbCount: 1, now: FIXED_NOW });

  assert.equal(out.git_sha.value, null);
  assert.equal(out.git_sha.reason, "not stamped: deployed outside bin/deploy-worker.sh");
  // An empty string is exactly the shape a broken --var wiring could produce;
  // it must fall to the same honest null, never render as a truthy sha.
});

test("buildRelease: an empty-string GIT_SHA degrades to the same not-stamped reason", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const out = await buildRelease({ env: { GIT_SHA: "" }, sql, verbCount: 1, now: FIXED_NOW });
  assert.equal(out.git_sha.value, null);
  assert.equal(out.git_sha.reason, "not stamped: deployed outside bin/deploy-worker.sh");
});

test("buildRelease: a database failure degrades schema and doctrine_generation to null + reason, never throws", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", new Error("connection terminated unexpectedly")],
    ["doctrine_meta", new Error("connection terminated unexpectedly")],
  ]);
  const out = await buildRelease({
    env: { GIT_SHA: "b".repeat(40) },
    sql,
    verbCount: 50,
    now: FIXED_NOW,
  });

  // The response is still ok:true — this is an identity endpoint, not a
  // health gate, and a database outage does not make the Worker's own
  // identity (git_sha, verb_count) any less true.
  assert.equal(out.ok, true);
  assert.equal(out.schema.highest_applied_migration, null);
  assert.equal(out.schema.applied_count, null);
  assert.match(out.schema.reason, /database unreachable/);
  assert.equal(out.doctrine_generation.value, null);
  assert.match(out.doctrine_generation.reason, /database unreachable/);
  // git_sha and verb_count have no database dependency and must survive a
  // database outage untouched.
  assert.deepEqual(out.git_sha, { value: "b".repeat(40), reason: null });
  assert.equal(out.verb_count, 50);
});

test("buildRelease: independent database readbacks overlap and fail independently", async () => {
  let active = 0;
  let maxActive = 0;
  const sql = async (strings) => {
    const query = strings.join(" ");
    active += 1;
    maxActive = Math.max(maxActive, active);
    await new Promise(resolve => setImmediate(resolve));
    active -= 1;
    if (query.includes("v_schema_ledger")) throw new Error("schema read unavailable");
    if (query.includes("doctrine_meta")) return [{ generation: 42 }];
    throw new Error(`unmocked query: ${query}`);
  };
  const out = await buildRelease({ env: { GIT_SHA: "b".repeat(40) },
    sql, verbCount: 50, now: FIXED_NOW });
  assert.equal(maxActive, 2, "schema and doctrine reads must start before either settles");
  assert.match(out.schema.reason, /schema read unavailable/);
  assert.deepEqual(out.doctrine_generation, { value: 42, reason: null },
    "one failed readback must not erase the independent successful field");
});

test("buildRelease: an empty v_schema_ledger reports zero, not an error", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 0, highest_applied_migration: null }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const out = await buildRelease({ env: { GIT_SHA: "c".repeat(40) }, sql, verbCount: 1, now: FIXED_NOW });
  assert.equal(out.schema.applied_count, 0);
  assert.equal(out.schema.highest_applied_migration, null);
  assert.equal(out.schema.reason, null);
});

test("buildRelease: doctrine_meta with no id=1 row reports null + a specific reason", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", []],
  ]);
  const out = await buildRelease({ env: { GIT_SHA: "d".repeat(40) }, sql, verbCount: 1, now: FIXED_NOW });
  assert.equal(out.doctrine_generation.value, null);
  assert.match(out.doctrine_generation.reason, /doctrine_meta has no row/);
});

test("buildRelease: never returns a secret — env is never echoed back, whatever it holds", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const env = {
    GIT_SHA: "e".repeat(40),
    DATABASE_URL_READER: "postgres://reader:supersecret@db.example/carr", // ci-secret-scan: allow — canary, db.example is RFC 2606 reserved
    DATABASE_URL_WRITER: "postgres://writer:evensecreter@db.example/carr", // ci-secret-scan: allow — canary, db.example is RFC 2606 reserved
    INGEST_TOKENS: '{"some-source":"top-secret-token"}',
    PROBE_TOKENS: '{"smoke-probe":"another-secret"}',
    REVIEW_TOKENS: '{"codex-reviewer":"yet-another-secret"}',
    AGENT_TOKENS: '{"codex":"agent-secret"}',
    LOCAL_TOKENS: '{"joe-local":"local-secret"}',
    GOOGLE_CLIENT_SECRET: "google-secret",
  };
  const out = await buildRelease({ env, sql, verbCount: 1, now: FIXED_NOW });
  const rendered = JSON.stringify(out);

  for (const secretValue of [
    "supersecret", "evensecreter", "top-secret-token", "another-secret",
    "yet-another-secret", "agent-secret", "local-secret", "google-secret",
  ]) {
    assert.ok(!rendered.includes(secretValue), `leaked secret value: ${secretValue}`);
  }
  // Only the two fields this endpoint is FOR should surface from env.
  assert.equal(out.git_sha.value, "e".repeat(40));
  assert.equal(out.env.value, "unknown"); // CARR_ENV absent above, so: unknown
});

// --- environment identity (2026-08-14) -------------------------------------
// The incident these pin: a staging Worker deployed without `routes = []`
// inherited production's three custom domains and served api.doctorcre.com for
// about two minutes. /release answered the whole time and could not say it was
// staging. Its only symptom was doctrine_generation null, which reads as a
// database fault ON PRODUCTION rather than as the wrong Worker answering.

test("buildRelease: reports the environment it IS, from CARR_ENV", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 120, highest_applied_migration: "0114_x.sql" }]],
    ["doctrine_meta", [{ generation: 359 }]],
  ]);
  for (const name of ["production", "staging"]) {
    const out = await buildRelease({
      env: { GIT_SHA: "a".repeat(40), CARR_ENV: name },
      sql, verbCount: 105, now: FIXED_NOW,
    });
    assert.deepEqual(out.env, { value: name, reason: null });
  }
});

test("buildRelease: reports the exact fail-closed Program 6 action posture", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 120, highest_applied_migration: "0114_x.sql" }]],
    ["doctrine_meta", [{ generation: 359 }]],
  ]);
  for (const [value, expected] of [
    ["false", { enabled: false, posture: "disabled", reason: null }],
    ["true", { enabled: true, posture: "enabled", reason: null }],
    [undefined, { enabled: false, posture: "misconfigured", reason: "DEALROOM_PROGRAM6_ACTIONS_ENABLED must be exactly true or false" }],
    ["TRUE", { enabled: false, posture: "misconfigured", reason: "DEALROOM_PROGRAM6_ACTIONS_ENABLED must be exactly true or false" }],
  ]) {
    const out = await buildRelease({
      env: { GIT_SHA: "a".repeat(40), CARR_ENV: "staging", DEALROOM_PROGRAM6_ACTIONS_ENABLED: value },
      sql, verbCount: 105, now: FIXED_NOW,
    });
    assert.deepEqual(out.program6_actions, expected);
  }
});

test("buildRelease: an unlabelled Worker reports unknown, and NEVER defaults to production", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 120, highest_applied_migration: "0114_x.sql" }]],
    ["doctrine_meta", [{ generation: 359 }]],
  ]);
  for (const env of [{}, { CARR_ENV: "" }, { CARR_ENV: null }]) {
    const out = await buildRelease({ env, sql, verbCount: 105, now: FIXED_NOW });
    assert.equal(out.env.value, "unknown");
    assert.notEqual(out.env.value, "production");
    assert.match(out.env.reason, /never assumed to be production/);
  }
});

test("buildRelease: env is the ONLY field that separates the environments — the incident case", async () => {
  // Staging's database is built from db/schema.sql, production's committed
  // structure INCLUDING its schema_migrations ledger. So given the same commit,
  // every other field a reader might reach for is identical. If this test ever
  // fails because some other field now differs, that difference is incidental
  // and must still not be read as identity.
  const identicalSql = () => fakeSql([
    ["v_schema_ledger", [{ applied_count: 120, highest_applied_migration: "0114_x.sql" }]],
    ["doctrine_meta", [{ generation: 359 }]],
  ]);
  const sha = "b".repeat(40);
  const prod = await buildRelease({
    env: { GIT_SHA: sha, CARR_ENV: "production" },
    sql: identicalSql(), verbCount: 105, now: FIXED_NOW,
  });
  const stage = await buildRelease({
    env: { GIT_SHA: sha, CARR_ENV: "staging" },
    sql: identicalSql(), verbCount: 105, now: FIXED_NOW,
  });

  assert.deepEqual(prod.git_sha, stage.git_sha);
  assert.deepEqual(prod.schema, stage.schema);
  assert.deepEqual(prod.doctrine_generation, stage.doctrine_generation);
  assert.equal(prod.verb_count, stage.verb_count);
  assert.notDeepEqual(prod.env, stage.env);
  assert.equal(prod.env.value, "production");
  assert.equal(stage.env.value, "staging");
});

// --- command-contract identity (V5-F07, Q058) ------------------------------
// The command-contract axis a caller needs for a compatibility test. It is the
// SAME identity the runtime dispatches against (mutation-registry.js), read
// here rather than restated, so /release cannot report a command contract this
// deploy is not running.

test("buildRelease: reports the shipped command-contract identity, not a second registry", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 120, highest_applied_migration: "0114_x.sql" }]],
    ["doctrine_meta", [{ generation: 359 }]],
  ]);
  const out = await buildRelease({
    env: { GIT_SHA: "a".repeat(40), CARR_ENV: "production" }, sql, verbCount: 105, now: FIXED_NOW,
  });

  assert.deepEqual(out.command_contract, mutationManifestIdentity());
  assert.match(out.command_contract.registry_version, /^scac-mutation-registry\.v[1-9]\d*$/);
  assert.match(out.command_contract.registry_digest, /^[0-9a-f]{64}$/);
});

test("buildRelease: command_contract is code identity — identical across environments", async () => {
  const identicalSql = () => fakeSql([
    ["v_schema_ledger", [{ applied_count: 120, highest_applied_migration: "0114_x.sql" }]],
    ["doctrine_meta", [{ generation: 359 }]],
  ]);
  const sha = "b".repeat(40);
  const prod = await buildRelease({
    env: { GIT_SHA: sha, CARR_ENV: "production" }, sql: identicalSql(), verbCount: 105, now: FIXED_NOW,
  });
  const stage = await buildRelease({
    env: { GIT_SHA: sha, CARR_ENV: "staging" }, sql: identicalSql(), verbCount: 105, now: FIXED_NOW,
  });

  // Same reasoning as the incident test above: env stays the only field that
  // separates the environments, so nobody reads this one as an identity either.
  assert.deepEqual(prod.command_contract, stage.command_contract);
});

test("buildRelease: command_contract has no database dependency and survives an outage", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", new Error("connection terminated unexpectedly")],
    ["doctrine_meta", new Error("connection terminated unexpectedly")],
  ]);
  const out = await buildRelease({ env: { GIT_SHA: "c".repeat(40) }, sql, verbCount: 105, now: FIXED_NOW });

  assert.equal(out.ok, true);
  assert.deepEqual(out.command_contract, mutationManifestIdentity());
  assert.match(out.schema.reason, /database unreachable/);
});

test("buildRelease: response is JSON-safe (no undefined, no function, round-trips clean)", async () => {
  const sql = fakeSql([
    ["v_schema_ledger", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const out = await buildRelease({ env: {}, sql, verbCount: 1, now: FIXED_NOW });
  const roundTripped = JSON.parse(JSON.stringify(out));
  assert.deepEqual(roundTripped, out);
});
