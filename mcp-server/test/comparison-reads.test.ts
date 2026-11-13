// Prove the engine's comparison door reads as one partner and never writes.
import assert from "node:assert/strict";
import test from "node:test";
import { type ComparisonDoor, serveComparisonRead } from "../src/comparison-reads.ts";
import { neonConfig } from "../src/postgres.ts";

type Call = (...args: unknown[]) => unknown;

function record(value: unknown): Record<string, unknown> {
  assert.ok(typeof value === "object" && value !== null && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}

function callable(value: unknown): Call {
  assert.ok(typeof value === "function");
  return (...args) => Reflect.apply(value, undefined, args);
}

async function response(value: unknown): Promise<Response> {
  const settled = await value;
  assert.ok(settled instanceof Response);
  return settled;
}

const at = (value: unknown, ...path: (string | number)[]): unknown =>
  path.reduce<unknown>(
    (node, key) => (typeof node === "object" && node !== null ? Reflect.get(node, key) : undefined),
    value,
  );

// The legacy modules are loaded at their runtime seam until their own conversion.
const legacy = async (path: string) => record(await import(new URL(path, import.meta.url).href));
const identity = await legacy("../src/identity.js");
const mcp = await legacy("../src/mcp.js");
const budgeted = await legacy("./helpers/budgeted-dispatch.mjs");
const tools = Object.fromEntries(
  Object.entries(record((await legacy("../src/tools.js")).TOOLS)).map(([name, tool]) => [name, record(tool)]),
);
const actorFor = callable(identity.comparisonActorForPartner);
const browserActor = (partner: string) =>
  callable(record(identity.authenticatedIdentity).connectionForGrant)(
    callable(identity.propsForSlug)(partner, { via: "dealroom-cookie", client_id: "dealroom-pwa" }),
  );
const dispatch = callable(budgeted.dispatch);

const unreachable: ComparisonDoor<unknown> = {
  actorFor,
  tools,
  mcp: () => assert.fail("refused request reached dispatch"),
  pipeline: () => assert.fail("refused request reached the change feed"),
  business: () => assert.fail("refused request reached the business read"),
};

function rpc(method: string, params: object = {}, query = "") {
  return new Request(`https://carr-mcp.internal/mcp${query}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
}

const throughDoor = (partner: unknown, request: Request, env: object = {}, send: Call = dispatch) =>
  serveComparisonRead(partner, request, { ...unreachable, mcp: (call, actor) => response(send(call, env, {}, actor)) });

const toolResult = async (reply: Response) =>
  JSON.parse(String(at(await reply.json(), "result", "content", 0, "text")));

test("write tools, partner-in-person reads and unregistered names are refused before dispatch", async () => {
  for (const [name, args] of [
    ["log-activity", { idempotency_key: "c0de0000-0000-4000-8000-000000000001" }],
    ["confirm-merge", {}],
    ["read-home-review", {}],
    ["call-verb", { verb: "find", args: { query: "Synthetic" } }],
    ["no-such-verb", {}],
  ] as const) {
    const reply = await serveComparisonRead("joe", rpc("tools/call", { name, arguments: args }), unreachable);
    assert.equal(reply.status, 403, name);
    assert.deepEqual(await reply.json(), { error: "read_only" }, name);
  }
});

test("only the session methods and tools/call reach dispatch", async () => {
  for (const method of [
    "resources/list",
    "resources/read",
    "prompts/get",
    "completion/complete",
    "logging/setLevel",
    "notifications/cancelled",
  ]) {
    const reply = await serveComparisonRead("joe", rpc(method), unreachable);
    assert.equal(reply.status, 403, method);
    assert.deepEqual(await reply.json(), { error: "method_not_allowed" }, method);
  }
  const dispatched: unknown[] = [];
  const door: ComparisonDoor<unknown> = {
    ...unreachable,
    mcp: async (call) => {
      dispatched.push(at(await call.json(), "method"));
      return new Response("{}");
    },
  };
  const allowed = ["initialize", "notifications/initialized", "ping", "tools/list"];
  for (const method of allowed) await serveComparisonRead("joe", rpc(method), door);
  await serveComparisonRead("joe", rpc("tools/call", { name: "find", arguments: { query: "Synthetic" } }), door);
  assert.deepEqual(dispatched, [...allowed, "tools/call"]);
});

test("only GET reaches the change feed and the business read", async () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
    for (const path of ["/pipeline/changes", "/api/v1/business/clients", "/api/v1/business/relationships"]) {
      const reply = await serveComparisonRead(
        "dell",
        new Request(`https://carr-mcp.internal${path}`, { method }),
        unreachable,
      );
      assert.equal(reply.status, 405, `${method} ${path}`);
    }
  }
  const reached: unknown[] = [];
  const reach = (door: string) => async (_read: Request, actor: unknown) => {
    reached.push([door, at(actor, "slug")]);
    return new Response("{}");
  };
  const door: ComparisonDoor<unknown> = { ...unreachable, pipeline: reach("feed"), business: reach("business") };
  await serveComparisonRead("dell", new Request("https://carr-mcp.internal/pipeline/changes"), door);
  await serveComparisonRead("dell", new Request("https://carr-mcp.internal/api/v1/business/vendors"), door);
  assert.deepEqual(reached, [
    ["feed", "dell"],
    ["business", "dell"],
  ]);
});

test("anything but one of the two partners is refused", async () => {
  for (const partner of ["codex", "joe-local", "smoke-probe", "Joe", " joe", "", null, undefined, { slug: "joe" }]) {
    const reply = await serveComparisonRead(partner, rpc("tools/list"), unreachable);
    assert.equal(reply.status, 403, String(partner));
    assert.deepEqual(await reply.json(), { error: "unknown_partner" });
  }
});

test("a partner-scoped read answers through the door exactly as it does through the browser session", async (t) => {
  const previous = neonConfig.fetchFunction;
  t.after(() => {
    neonConfig.fetchFunction = previous;
  });
  const owners: unknown[] = [];
  neonConfig.fetchFunction = async (_url: string, options: { body: string }) => {
    const { query, params } = record(JSON.parse(options.body));
    const rows =
      query === "select id from public.actor where slug=$1" ? [["c0de0000-0000-4000-8000-00000000000a"]] : [];
    if (!rows.length) owners.push(at(params, 1));
    return Response.json({
      fields: [{ name: "id", dataTypeID: 25 }],
      rows,
      rowCount: rows.length,
      command: "SELECT",
      rowAsArray: true,
    });
  };
  const env = { DATABASE_URL_READER: "postgres://synthetic@synthetic.invalid/synthetic" };
  const call = () =>
    rpc("tools/call", {
      name: "read-activity-deal-attribution",
      arguments: { activity_id: "c0de0000-0000-4000-8000-000000000209" },
    });
  for (const partner of ["joe", "dell"]) {
    const viaBrowser = await (await response(dispatch(call(), env, {}, browserActor(partner)))).json();
    const viaDoor = await (await throughDoor(partner, call(), env)).json();
    assert.deepEqual(viaDoor, viaBrowser, partner);
    assert.equal(
      JSON.parse(String(at(viaDoor, "result", "content", 0, "text"))).state,
      "empty",
      "answered, not refused",
    );
  }
  assert.deepEqual(owners, ["joe", "joe", "dell", "dell"]);
});

test("request input cannot widen the door's empty write profile", async () => {
  for (const query of ["", "?profile=full", "?profile=capture", "?profile=unattended"]) {
    const listed = at(await (await throughDoor("joe", rpc("tools/list", {}, query))).json(), "result", "tools");
    assert.ok(Array.isArray(listed) && listed.length > 0);
    assert.deepEqual(
      listed.filter((tool) => at(tool, "annotations", "readOnlyHint") !== true),
      [],
      query,
    );
    const write = rpc("tools/call", { name: "log-activity", arguments: {} }, query);
    assert.equal(
      (await toolResult(await response(dispatch(write, {}, {}, actorFor("joe"))))).error,
      "not_in_profile",
      query,
    );
  }
});

test("the door spends the shared machine budget, not the browser session's 60-a-minute budget", async () => {
  const spent: string[] = [];
  const budget = (kind: string) => ({
    limit: async () => {
      spent.push(kind);
      return { success: true };
    },
  });
  const env = {
    VERB_REQUEST_BUDGET_HUMAN: budget("session"),
    VERB_REQUEST_BUDGET_AGENT: budget("machine"),
    VERB_REQUEST_BUDGET: {
      idFromName: (name: string) => name,
      get: () => ({ fetch: async () => Response.json({ allowed: false, retryAfter: 1 }) }),
    },
  };
  const find = rpc("tools/call", { name: "find", arguments: { query: "Synthetic" } });
  const reply = await throughDoor("joe", find, env, callable(mcp.dispatch));
  assert.equal(reply.status, 429);
  assert.deepEqual(spent, ["machine"]);
});
