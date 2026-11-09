import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { randomUUID, webcrypto } from "node:crypto";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { Validator } from "@cfworker/json-schema";
import pg from "pg";
import { ToolError } from "../src/tool-error.ts";
import { webPushTools } from "../src/web-push-subscriptions.ts";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function record(value: unknown): Record<string, unknown> {
  assert.ok(isRecord(value));
  return value;
}
function records(value: unknown): Record<string, unknown>[] {
  assert.ok(Array.isArray(value));
  return value.map(record);
}
function first<T>(values: T[]): T {
  const value = values[0];
  assert.notEqual(value, undefined);
  if (value === undefined) throw new Error("Expected a row");
  return value;
}
const databaseModule = record(await import(new URL("./helpers/postgres-database.mjs", import.meta.url).href));
const databaseFactory = databaseModule.postgresDatabase;
assert.equal(typeof databaseFactory, "function");
async function postgresDatabase(prefix: string) {
  assert.ok(typeof databaseFactory === "function");
  const database = record(await databaseFactory(prefix));
  assert.ok(database.client instanceof pg.Client);
  const close = database.close;
  assert.ok(typeof close === "function");
  return {
    client: database.client,
    close: async () => {
      await close();
    },
  };
}
function connection(client: pg.Client): pg.ClientConfig {
  assert.ok("connectionParameters" in client);
  const value = record(client.connectionParameters);
  assert.ok(
    typeof value.host === "string" &&
      typeof value.port === "number" &&
      typeof value.user === "string" &&
      typeof value.database === "string",
  );
  return { host: value.host, port: value.port, user: value.user, database: value.database };
}
const registered = record(await import(new URL("../src/tools.js", import.meta.url).href));
async function executeRegisteredTool(...args: unknown[]): Promise<unknown> {
  assert.ok(typeof registered.executeRegisteredTool === "function");
  return registered.executeRegisteredTool(...args);
}
async function catalogRead(...args: unknown[]): Promise<Record<string, unknown>> {
  const catalog = record(record(registered.TOOLS)["list-verbs"]);
  assert.ok(typeof catalog.handler === "function");
  return record(await catalog.handler(...args));
}

const partner = {
  id: "20000000-0000-4000-8000-000000000001",
  human: true,
  slug: "joe",
  web_push_session: "a".repeat(64),
};
const other = {
  id: "20000000-0000-4000-8000-000000000002",
  human: true,
  slug: "dell",
  web_push_session: "b".repeat(64),
};
const tools = webPushTools({ ToolError });
const responseContract = new Validator(
  JSON.parse(await readFile(new URL("../contracts/web-push-registry.v1.json", import.meta.url), "utf8")),
);
const register = "register-web-push-subscription-v1";
const revoke = "revoke-web-push-subscription-v1";
const list = "read-web-push-subscriptions-v1";
const logout = "invalidate-web-push-session-v1";
const publicKey = await webcrypto.subtle.exportKey(
  "raw",
  (await webcrypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"])).publicKey,
);
const subscription = () => ({
  installation_id: randomUUID(),
  idempotency_key: randomUUID(),
  base_generation: 0,
  endpoint: `https://web.push.apple.com/${randomUUID()}`,
  p256dh: Buffer.from(publicKey).toString("base64url"),
  auth: Buffer.alloc(16, 1).toString("base64url"),
  vapid_key_version: "v1",
});

test("registry lifecycle, isolation, replay and logout on disposable PostgreSQL", async (t) => {
  const db = await postgresDatabase("push-registry-");
  const c = db.client;
  const invoke = async (name: keyof typeof tools, args: unknown, actor = partner, client = c) => {
    await client.query("begin");
    try {
      await client.query(
        "select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true)",
        [actor.slug],
      );
      const result = await tools[name].handler(client, actor, args);
      assert.equal(responseContract.validate(result).valid, true);
      await client.query("commit");
      return record(result);
    } catch (e) {
      await client.query("rollback");
      throw e;
    }
  };
  const refused = (promise: Promise<unknown>, code: string) =>
    assert.rejects(promise, (e) => e instanceof ToolError && e.payload.error === code);
  try {
    await c.query(
      "create schema ops; create table public.actor(id uuid primary key,slug text,active boolean,kind text)",
    );
    await c.query("insert into actor values ($1,'joe',true,'human'),($2,'dell',true,'human')", [partner.id, other.id]);
    const schema = await readFile(new URL("../../db/schema.sql", import.meta.url), "utf8");
    const identityFunction = schema.match(/CREATE FUNCTION ops\.portfolio_writer_actor_id\(\)[\s\S]*?\n\$\$;/);
    assert.ok(identityFunction);
    await c.query(identityFunction[0]);
    await c.query(
      "create function ops.scac_current_grant_contract() returns jsonb language sql as $$select '{\"secdef_execute\":[]}'::jsonb$$",
    );
    for (const role of ["carr_reader", "carr_writer", "carr_authority", "carr_jobs", "dot_reader"])
      await c.query(`create role ${role}`);
    await c.query(await readFile(new URL("../../migrations/1163_web_push_registry.sql", import.meta.url), "utf8"));
    assert.equal(
      (await c.query("select has_table_privilege('carr_writer','ops.web_push_subscription','SELECT') as allowed"))
        .rows[0].allowed,
      false,
    );
    const args = subscription();
    await t.test("Register and duplicate request preserve one confirmed generation", async () => {
      const first = await invoke(register, args);
      assert.equal(first.generation, 1);
      assert.equal((await invoke(register, args)).replayed, true);
      assert.equal(records((await invoke(list, {})).subscriptions).length, 1);
    });
    await t.test(
      "release-aware read-only proof CLI accepts recorded contracts and refuses false evidence",
      async () => {
        const root = await mkdtemp(join(tmpdir(), "push-proof-"));
        const cli = new URL("../../ops/web-push-registry-proof.py", import.meta.url).pathname;
        const git = (...args: string[]) =>
          execFileSync("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", ...args], {
            cwd: root,
            encoding: "utf8",
            input: "",
            env: { ...process.env, GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_NOSYSTEM: "1" },
          }).trim();
        try {
          git("init", "-q");
          const tree = git("mktree");
          const older = git("commit-tree", tree, "-m", "older");
          const merge = git("commit-tree", tree, "-p", older, "-m", "merge");
          const descendant = git("commit-tree", tree, "-p", merge, "-m", "descendant");
          const unrelated = git("commit-tree", tree, "-m", "unrelated");
          const catalog = await catalogRead(c, partner, { filter: "web-push" });
          const registry = await invoke(list, {});
          assert.equal(responseContract.validate(registry).valid, true);
          const bundle = {
            release: { ok: true, env: { value: "production" }, git_sha: { value: merge } },
            provenance: {
              release_sha: merge,
              actor_slug: "joe",
              transport: "signed-in-app-origin/mcp",
              app_origin: "https://app.doctorcre.com",
              queries: [
                { verb: "list-verbs", args: { filter: "web-push" } },
                { verb: list, args: {} },
              ],
            },
            catalog,
            registry,
          };
          const run = async (value: unknown, expected: number, label: string) => {
            const path = join(root, "evidence.json");
            await writeFile(path, JSON.stringify(value));
            const result = spawnSync(
              process.env.CARR_CI_PYTHON || "python3",
              [cli, "--fixture", "--evidence", path, "--merge-sha", merge, "--repository", root],
              { encoding: "utf8", timeout: 10000 },
            );
            assert.equal(result.status, expected, `${label}: ${result.stdout}${result.stderr}`);
            console.log(`proof fixture ${label} exit ${result.status}`);
          };
          for (const [label, sha, code] of [
            ["equal", merge, 0],
            ["descendant", descendant, 0],
            ["older", older, 78],
            ["unrelated", unrelated, 78],
            ["unknown", "f".repeat(40), 78],
          ] satisfies [string, string, number][]) {
            const value = structuredClone(bundle);
            value.release.git_sha.value = sha;
            value.provenance.release_sha = sha;
            await run(value, code, label);
          }
          const liveEvidence = join(root, "live.json");
          await writeFile(liveEvidence, JSON.stringify(bundle));
          const live = spawnSync(
            process.env.CARR_CI_PYTHON || "python3",
            [
              "-c",
              `
import importlib.util, json, sys, time
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('proof',sys.argv[1])
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
b=json.load(open(sys.argv[2])); calls=[]
def read(url, **kwargs):
    calls.append(url)
    return SimpleNamespace(status=200,body=json.dumps(b['release']).encode())
p.read=read
args=['--evidence',sys.argv[2],'--merge-sha',sys.argv[3],'--repository',sys.argv[4]]
assert p.main(args)==0
assert calls==['https://api.doctorcre.com/release']
def stalled(*args, **kwargs):
    time.sleep(1)
p.read=stalled
assert p.main(args+['--timeout','0.05'])==78
`,
              cli,
              liveEvidence,
              merge,
              root,
            ],
            { encoding: "utf8", timeout: 10000 },
          );
          assert.equal(live.status, 0, live.stdout + live.stderr);
          const missing = structuredClone(bundle);
          missing.catalog.verbs = records(missing.catalog.verbs).filter((v) => v.name !== register);
          await run(missing, 1, "missing registered verb");
          const malformed = structuredClone(bundle);
          first(records(malformed.registry.subscriptions)).endpoint = "secret";
          await run(malformed, 1, "response contract violation");
          const refused = structuredClone(bundle);
          refused.registry = { error: "push_session_required" };
          await run(refused, 78, "no authenticated session");
          const stale = structuredClone(bundle);
          stale.provenance.release_sha = older;
          await run(stale, 78, "read release mismatch");
        } finally {
          await rm(root, { recursive: true, force: true });
        }
      },
    );
    await t.test("concurrent duplicate requests return one receipt", async () => {
      const second = new pg.Client(connection(c));
      await second.connect();
      try {
        const replacement = { ...args, base_generation: 1, idempotency_key: randomUUID() };
        const replies = await Promise.all([
          invoke(register, replacement),
          invoke(register, replacement, partner, second),
        ]);
        assert.equal(replies[0].generation, 2);
        assert.equal(replies[1].generation, 2);
        assert.equal(replies.filter((r) => r.replayed === true).length, 1);
      } finally {
        await second.end();
      }
    });
    await t.test("changed arguments under one key refuse", async () => {
      await refused(invoke(register, { ...args, endpoint: "https://web.push.apple.com/changed" }), "push_key_reuse");
    });
    await t.test("foreign access and endpoint transfer refuse without secrets", async () => {
      assert.deepEqual(records((await invoke(list, {}, other)).subscriptions), []);
      await refused(
        invoke(
          revoke,
          { installation_id: args.installation_id, base_generation: 2, idempotency_key: randomUUID() },
          other,
        ),
        "push_subscription_unavailable",
      );
      await refused(
        invoke(register, { ...subscription(), endpoint: args.endpoint }, other),
        "push_subscription_unavailable",
      );
      const response = JSON.stringify(await invoke(list, {}));
      for (const secret of [args.endpoint, args.auth, args.p256dh]) assert.ok(!response.includes(secret));
    });
    await t.test("endpoint and encryption key validation refuses secret-free", async () => {
      for (const endpoint of [
        "http://web.push.apple.com/a",
        "https://127.0.0.1/a",
        "https://web.push.apple.com.evil/a",
        "https://" + "synthetic" + "@" + "web.push.apple.com/a",
        "https://web.push.apple.com:443/a",
        "https://web.push.apple.com/a#frag",
        `https://web.push.apple.com/${"a".repeat(2050)}`,
      ]) {
        await refused(invoke(register, { ...subscription(), endpoint }), "push_invalid_subscription");
      }
      for (const change of [
        { p256dh: "secret" },
        { auth: "secret" },
        { vapid_key_version: "wrong" },
        { p256dh: `B${"A".repeat(86)}` },
      ]) {
        await refused(invoke(register, { ...subscription(), ...change }), "push_invalid_subscription");
      }
    });
    await t.test("Replace and concurrent replacement versus revocation fence stale generation", async () => {
      const second = new pg.Client(connection(c));
      await second.connect();
      try {
        const replace = { ...args, idempotency_key: randomUUID(), base_generation: 2 };
        const remove = { installation_id: args.installation_id, idempotency_key: randomUUID(), base_generation: 2 };
        const results = await Promise.allSettled([invoke(register, replace), invoke(revoke, remove, partner, second)]);
        assert.equal(results.filter((r) => r.status === "fulfilled").length, 1);
        assert.equal(
          record(record(first(results.filter((r) => r.status === "rejected")).reason).payload).error,
          "push_generation_conflict",
        );
      } finally {
        await second.end();
      }
    });
    await t.test("Revoke and Enable advance generations and scrub revoked secrets", async () => {
      let row = first(records((await invoke(list, {})).subscriptions));
      await invoke(revoke, {
        installation_id: args.installation_id,
        base_generation: row.generation,
        idempotency_key: randomUUID(),
      });
      assert.equal(
        (await c.query("select endpoint,p256dh,auth from ops.web_push_subscription")).rows[0].endpoint,
        null,
      );
      row = first(records((await invoke(list, {})).subscriptions));
      assert.equal(
        (await invoke(register, { ...args, base_generation: row.generation, idempotency_key: randomUUID() })).state,
        "active",
      );
    });
    await t.test("logout preserves another installation and endpoint ownership survives revocation", async () => {
      const deviceActor = { ...partner, web_push_session: "c".repeat(64) };
      const device = subscription();
      await invoke(register, device, deviceActor);
      await invoke(logout, {}, deviceActor);
      assert.equal(records((await invoke(list, {})).subscriptions).filter((s) => s.state === "active").length, 1);
      await refused(
        invoke(register, { ...subscription(), endpoint: device.endpoint }, other),
        "push_subscription_unavailable",
      );
    });
    await t.test("database failures return only a fixed error", async () => {
      await assert.rejects(
        tools[list].handler(
          {
            query: async () => {
              throw new Error(args.endpoint + args.auth + args.p256dh);
            },
          },
          partner,
          {},
        ),
        (e) => e instanceof ToolError && JSON.stringify(e.payload) === '{"error":"push_registry_unavailable"}',
      );
    });
    await t.test("three active installations and twenty mutations per minute are bounded", async () => {
      for (const hash of ["d", "e", "f"])
        await invoke(register, subscription(), { ...other, web_push_session: hash.repeat(64) });
      await refused(
        invoke(register, subscription(), { ...other, web_push_session: "1".repeat(64) }),
        "push_installation_limit",
      );
      await c.query(
        "insert into ops.web_push_receipt(operation_key,actor_id,request_hash,response) select gen_random_uuid(),$1,'synthetic','{}'::jsonb from generate_series(1,20)",
        [other.id],
      );
      await refused(invoke(register, subscription(), other), "push_rate_limited");
    });
    await t.test("Logout from Active, Off and Revoked is idempotent and rejects old-session registration", async () => {
      await invoke(logout, {});
      await invoke(logout, {});
      assert.ok(records((await invoke(list, {})).subscriptions).every((s) => s.state === "revoked"));
      await refused(invoke(register, args), "push_session_closed");
      await invoke(logout, {}, other);
      await refused(invoke(register, subscription(), other), "push_session_closed");
    });
    await t.test("a new authenticated session can re-enable the same owned installation", async () => {
      const row = first(
        records((await invoke(list, {})).subscriptions).filter((s) => s.installation_id === args.installation_id),
      );
      const newSession = { ...partner, web_push_session: "9".repeat(64) };
      assert.equal(
        (
          await invoke(
            register,
            { ...args, base_generation: row.generation, idempotency_key: randomUUID() },
            newSession,
          )
        ).state,
        "active",
      );
      await invoke(logout, {}, partner);
      assert.equal(
        first(records((await invoke(list, {})).subscriptions).filter((s) => s.installation_id === args.installation_id))
          .state,
        "active",
      );
      await invoke(logout, {}, newSession);
    });
    await t.test("unauthenticated identity and caller-selected partner refuse", async () => {
      await refused(invoke(register, subscription(), { ...partner, human: false }), "push_session_required");
      await refused(invoke(register, { ...subscription(), actor_id: other.id }), "push_invalid_request");
    });
  } finally {
    await db.close();
  }
});

test("registered contract errors never echo invalid sensitive arguments", async () => {
  const secret = "private-test-marker";
  for (const change of [
    { vapid_key_version: secret },
    { endpoint: secret.repeat(300) },
    { p256dh: secret.repeat(10) },
    { auth: secret.repeat(10) },
  ]) {
    await assert.rejects(
      executeRegisteredTool(
        {
          query: async () => {
            throw new Error("validation reached database");
          },
        },
        partner,
        register,
        { ...subscription(), ...change },
      ),
      (error) => error instanceof ToolError && !JSON.stringify(error.payload).includes(secret),
    );
  }
});

test("grant collection comparison accepts all migration orders and future appends", async (t) => {
  const migrations = new Map<number, string>();
  for (const number of [1163, 1164, 1165]) {
    const directory = new URL("../../migrations/", import.meta.url);
    const name = (await readdir(directory)).find((name) => name.startsWith(`${number}_`));
    assert.ok(name);
    migrations.set(number, await readFile(new URL(name, directory), "utf8"));
  }
  const source = migrations.get(1165);
  assert.ok(source);
  const match = source.match(/do \$grant_contract\$[\s\S]*end \$grant_contract\$;/);
  assert.ok(match);
  const future = first([...match]).replaceAll(
    "ops.resolve_action_card(p_actor uuid, p_kind text, p_id uuid)",
    "ops.future_grant(p_actor uuid)",
  );
  migrations.set(1166, future);
  const references = new Map<number, { contract: Record<string, unknown>; body: string }>();
  for (const order of [
    [1163, 1164, 1165],
    [1164, 1165, 1163],
    [1165, 1164, 1163],
    [1163, 1165, 1164],
    [1164, 1163, 1165],
    [1165, 1163, 1164],
    [1163, 1164, 1165, 1166],
    [1164, 1165, 1166, 1163],
  ]) {
    await t.test(order.join(","), async () => {
      const db = await postgresDatabase("push-grant-order-");
      try {
        const c = db.client;
        await c.query(`create schema ops;
          create table public.actor(id uuid primary key,active boolean,kind text,slug text);
          create table public.attachment(id uuid primary key);
          create function ops.portfolio_writer_actor_id() returns uuid language sql as $$ select null::uuid $$;
          create function ops.scac_current_grant_contract() returns jsonb language sql as $$
            select '{"secdef_execute":[],"retired_secdef_execute":[{"signature":"sentinel","config":["a","b"]}]}'::jsonb $$;`);
        for (const role of ["carr_reader", "carr_writer", "carr_authority", "carr_jobs", "dot_reader"])
          await c.query(`create role ${role}`);
        for (const number of order) await c.query(`begin;${migrations.get(number)};commit;`);
        const result = first(
          (await c.query<Record<string, unknown>>("select ops.scac_current_grant_contract() as contract")).rows,
        );
        const row = { contract: record(result.contract), body: "" };
        assert.deepEqual(row.contract.retired_secdef_execute, [{ signature: "sentinel", config: ["a", "b"] }]);
        if (order.join(",") === "1163,1164,1165") {
          const inventoryPath = new URL("../../ops/scac_mutation_db_inventory.py", import.meta.url).pathname;
          const loadInventory = `import importlib.util, sys
spec=importlib.util.spec_from_file_location('inventory',sys.argv[1])
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
`;
          const query = execFileSync(
            process.env.CARR_CI_PYTHON || "python3",
            ["-c", `${loadInventory}print(p.SECDEF_EXECUTE_SQL)`, inventoryPath],
            { encoding: "utf8" },
          );
          const projection = (await c.query<Record<string, unknown>>(query)).rows.map((row) => Object.values(row)[0]);
          let altered: unknown[] = [];
          await c.query("begin");
          try {
            await c.query("alter function ops.web_push_registry(text,jsonb,text) security invoker");
            altered = (await c.query<Record<string, unknown>>(query)).rows.map((row) => Object.values(row)[0]);
          } finally {
            await c.query("rollback");
          }
          const validation = spawnSync(
            process.env.CARR_CI_PYTHON || "python3",
            [
              "-c",
              `${loadInventory}
import json
rows,altered,contract=json.load(sys.stdin)
contract.pop('retired_secdef_execute')
projection={'secdef_execute':rows,'relation_dml':[],'column_dml':[]}
baseline={name:{'count':0,'digest':p.digest([])} for name in projection}
p.validate_registered_grants(projection,baseline,contract)
projection['secdef_execute']=altered
try:
    p.validate_registered_grants(projection,baseline,contract)
except RuntimeError as error:
    assert 'registered grant missing or altered' in str(error)
else:
    raise AssertionError('real catalog metadata mismatch was accepted')
`,
              inventoryPath,
            ],
            { input: JSON.stringify([projection, altered, row.contract]), encoding: "utf8" },
          );
          assert.equal(validation.status, 0, validation.stdout + validation.stderr);
        }
        row.body = execFileSync(
          join(execFileSync("pg_config", ["--bindir"], { encoding: "utf8" }).trim(), "pg_dump"),
          [
            "--schema-only",
            "--no-owner",
            "--schema=ops",
            "-h",
            String(connection(c).host),
            "-p",
            String(connection(c).port),
            "-U",
            String(connection(c).user),
            "-d",
            String(connection(c).database),
          ],
          { encoding: "utf8" },
        );
        const expected = references.get(order.length) ?? row;
        references.set(order.length, expected);
        assert.deepEqual(
          records(row.contract.secdef_execute)
            .map((value) => JSON.stringify(value))
            .sort(),
          records(expected.contract.secdef_execute)
            .map((value) => JSON.stringify(value))
            .sort(),
        );
        const comparison = spawnSync(
          process.env.CARR_CI_PYTHON || "python3",
          [
            "-c",
            `
import importlib.util, json, sys
spec=importlib.util.spec_from_file_location('shadow',sys.argv[1])
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
a,b=json.load(sys.stdin)
assert not p.schema_difference(a,b), 'grant collection differs across migration order'
`,
            new URL("../../ops/migration-shadow.py", import.meta.url).pathname,
          ],
          { input: JSON.stringify([expected.body, row.body]), encoding: "utf8" },
        );
        assert.equal(comparison.status, 0, comparison.stdout + comparison.stderr);
      } finally {
        await db.close();
      }
    });
  }
});
