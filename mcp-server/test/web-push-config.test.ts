import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import test from "node:test";
import { Validator } from "@cfworker/json-schema";
import { neonConfig } from "@neondatabase/serverless";
import { ToolError } from "../src/tool-error.ts";
import { webPushConfigTools } from "../src/web-push-config.ts";

const name = "read-web-push-config-v1";
const publicKey = Buffer.from(
  "046b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296" +
    "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5",
  "hex",
).toString("base64url");
const joe = { id: "20000000-0000-4000-8000-000000000001", slug: "joe", human: true, via: "oauth" };
const db = (key?: unknown) => ({
  vapidPublicKey: key,
  query: async () => {
    throw new Error("Config must not query the database");
  },
});
const local = webPushConfigTools({ ToolError })[name];
const registered: unknown = await import(new URL("../src/tools.js", import.meta.url).href);
assert.ok(typeof registered === "object" && registered !== null && "executeRegisteredTool" in registered);
const execute = registered.executeRegisteredTool;
assert.ok(typeof execute === "function");
assert.ok("TOOLS" in registered && typeof registered.TOOLS === "object" && registered.TOOLS !== null);
const registry = registered.TOOLS;
const fixtureUrl = new URL("../contracts/fixtures/web-push-config.v1.json", import.meta.url);

test("configured registered handler returns only the public applicationServerKey and v1", async () => {
  const result: unknown = await execute(db(publicKey), joe, name, {});
  assert.deepEqual(result, {
    ok: true,
    schema_version: "web-push-config.v1",
    public_key: publicKey,
    vapid_key_version: "v1",
  });
  if (process.argv.includes("--record-fixture")) await writeFile(fixtureUrl, JSON.stringify(result, null, 2) + "\n");
  const contract = new Validator(
    JSON.parse(await readFile(new URL("../contracts/web-push-config.v1.json", import.meta.url), "utf8")),
  );
  assert.equal(contract.validate(result).valid, true);
  assert.deepEqual(JSON.parse(await readFile(fixtureUrl, "utf8")), result);
  assert.deepEqual(await execute(db(publicKey), { ...joe, slug: "dell" }, name, {}), result);
});

test("absent configuration refuses with stable push_not_configured", async () => {
  for (const key of [undefined, ""])
    await assert.rejects(
      execute(db(key), joe, name, {}),
      (e: unknown) => e instanceof ToolError && e.payload.error === "push_not_configured",
    );
});

test("malformed config refuses loudly without echoing config", async () => {
  for (const key of [
    null,
    42,
    " ",
    "bad",
    publicKey + "=",
    publicKey.slice(0, -1) + "V",
    Buffer.alloc(65, 4).toString("base64url"),
    Buffer.alloc(64, 4).toString("base64url"),
    Buffer.concat([Buffer.from([3]), Buffer.alloc(64)]).toString("base64url"),
  ]) {
    await assert.rejects(
      execute(db(key), joe, name, {}),
      (e: unknown) => e instanceof ToolError && JSON.stringify(e.payload) === '{"error":"push_config_invalid"}',
    );
  }
});

test("registry authority metadata matches the local human partner guard", async () => {
  assert.ok(name in registry);
  const metadata: unknown = registry[name];
  assert.ok(
    typeof metadata === "object" && metadata !== null && "humanOnly" in metadata && "humanCallerOnly" in metadata,
  );
  assert.equal(metadata.humanOnly, true);
  assert.equal(metadata.humanCallerOnly, true);
  for (const actor of [
    { id: "unknown" },
    { ...joe, human: false },
    { ...joe, slug: "stranger" },
    {
      ...joe,
      slug: "joe-local",
      human: false,
      sponsoring_human_slug: "joe",
      native_agent_verified: true,
      via: "local-token",
    },
    { ...joe, slug: "joe-local", human: false, unattended: true, via: "unattended-token" },
  ]) {
    await assert.rejects(
      local.handler(db(publicKey), actor, {}),
      (e: unknown) => e instanceof ToolError && e.payload.error === "push_session_required",
    );
    await assert.rejects(
      execute(db(publicKey), actor, name, {}),
      (e: unknown) => e instanceof ToolError && e.payload.error === "human_only_verb_requires_verified_partner",
    );
  }
});

test("request cannot override Worker configuration", async () => {
  await assert.rejects(execute(db(publicKey), joe, name, { public_key: publicKey }));
});

test("Worker read route hands the public var to the registered handler", async (t) => {
  const mcp: unknown = await import(new URL("../src/mcp.js", import.meta.url).href);
  assert.ok(typeof mcp === "object" && mcp !== null && "callTool" in mcp && typeof mcp.callTool === "function");
  const call = mcp.callTool;
  const transport: unknown = await import(new URL("./helpers/budgeted-dispatch.mjs", import.meta.url).href);
  assert.ok(
    typeof transport === "object" &&
      transport !== null &&
      "dispatch" in transport &&
      typeof transport.dispatch === "function",
  );
  const dispatch = transport.dispatch;
  const previous = neonConfig.fetchFunction;
  t.after(() => {
    neonConfig.fetchFunction = previous;
  });
  neonConfig.fetchFunction = async () =>
    Response.json({
      fields: [{ name: "id", dataTypeID: 25 }],
      rows: [[joe.id]],
      rowCount: 1,
      command: "SELECT",
      rowAsArray: true,
    });
  const env = { DATABASE_URL_READER: "postgresql://fixture@synthetic.invalid/fixture", VAPID_PUBLIC_KEY: publicKey };
  assert.deepEqual(await call(env, { ...joe }, name, {}), JSON.parse(await readFile(fixtureUrl, "utf8")));
  await assert.rejects(
    call({ ...env, VAPID_PUBLIC_KEY: "" }, { ...joe }, name, {}),
    (e: unknown) => e instanceof ToolError && e.payload.error === "push_not_configured",
  );
  t.mock.method(console, "error", () => {});
  for (const [value, expected] of [
    ["", "push_not_configured"],
    ["bad", "push_config_invalid"],
  ]) {
    const response: unknown = await dispatch(
      new Request("https://synthetic.invalid/mcp", {
        method: "POST",
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: {} } }),
      }),
      { ...env, VAPID_PUBLIC_KEY: value },
      {},
      { ...joe },
    );
    assert.ok(response instanceof Response);
    const body: unknown = await response.json();
    assert.ok(typeof body === "object" && body !== null && "result" in body, JSON.stringify(body));
    const result = body.result;
    assert.ok(typeof result === "object" && result !== null && "isError" in result && "content" in result);
    assert.equal(result.isError, true);
    assert.ok(Array.isArray(result.content));
    const first: unknown = result.content[0];
    assert.ok(typeof first === "object" && first !== null && "text" in first && typeof first.text === "string");
    const error: unknown = JSON.parse(first.text);
    assert.ok(typeof error === "object" && error !== null && "error" in error);
    assert.equal(error.error, expected);
  }
});
