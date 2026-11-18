import assert from "node:assert/strict";
import { type TestContext, test } from "node:test";
import { Pool } from "@neondatabase/serverless";
import { publicError } from "../src/public-errors.ts";
import { NOW } from "./helpers/fixed-clock.ts";
import { executeRegisteredTool, record, ToolError } from "./helpers/legacy-test-registry.ts";

const { callTool } = record(await import(new URL("../src/mcp.js", import.meta.url).href));
const identity = record(await import(new URL("../src/identity.js", import.meta.url).href));
const { connectionForGrant } = record(identity.authenticatedIdentity);
assert.ok(typeof callTool === "function" && typeof connectionForGrant === "function");

const deal = "01000000-0000-4000-8000-000000000001";
const savedId = "01000000-0000-4000-8000-000000000002";
const key = "01000000-0000-4000-8000-000000000003";
const agent = {
  id: "01000000-0000-4000-8000-000000000004",
  slug: "codex",
  human: false,
  sponsoring_human_slug: "dell",
  native_agent_verified: true,
};
const activity = {
  idempotency_key: key,
  ref: deal,
  kind: "call",
  summary: "Reviewed the specimen",
  detail: "Follow up on the measurements.",
  occurred_at: "2026-10-10T15:13:27-05:00",
};

class LegacyReached extends Error {}

function legacy({ writes = false, subject = "deal" } = {}) {
  const changes: string[] = [];
  return {
    changes,
    async query(sql: string) {
      if (sql.includes("v_ref_index")) return { rows: [{ subject_type: subject, subject_id: deal }] };
      if (writes && sql.startsWith("select ")) return { rows: [] };
      if (writes && sql.startsWith("insert ")) {
        changes.push(sql);
        return { rows: [{ id: savedId }] };
      }
      throw new LegacyReached(sql);
    },
  };
}

function engine(t: TestContext, respond: (body: unknown) => Response, enabled = "true") {
  const calls: {
    path: string;
    headers: Headers;
    method: string | undefined;
    redirect: RequestRedirect | undefined;
    body: unknown;
  }[] = [];
  t.mock.method(globalThis, "fetch", async (url: URL, request: RequestInit) => {
    const { headers, method, redirect, body } = request;
    assert.ok(typeof body === "string");
    const parsed: unknown = JSON.parse(body);
    calls.push({ path: url.pathname, headers: new Headers(headers), method, redirect, body: parsed });
    return respond(parsed);
  });
  return {
    calls,
    env: {
      ENGINE_ASSISTANT: enabled,
      ENGINE_URL: "https://engine.example.test/",
      ENGINE_ACCESS_CLIENT_ID: "example-access-client",
      ENGINE_ACCESS_CLIENT_SECRET: "example-access-value",
    },
  };
}

test("off saves the activity through the legacy handler", async (t) => {
  const upstream = engine(t, () => assert.fail("off made an engine request"), "false");
  const c = legacy({ writes: true });
  const reply = record(await executeRegisteredTool(c, agent, "log-activity", { ...activity }, upstream.env));
  assert.equal(reply.activity_id, savedId);
  assert.ok(c.changes.some((sql) => sql.startsWith("insert into activity")));
  assert.equal(upstream.calls.length, 0);
});

test("deal activity retries a committed save without changing its time, key, or payload", async (t) => {
  const receipts = new Map<string, unknown>();
  const upstream = engine(t, (request) => {
    assert.ok(typeof request === "object" && request && "idempotency_key" in request);
    assert.ok(typeof request.idempotency_key === "string");
    if (!receipts.has(request.idempotency_key)) {
      receipts.set(request.idempotency_key, request);
      return Response.json({ error: "database_unavailable" }, { status: 503 });
    }
    assert.deepEqual(request, receipts.get(request.idempotency_key));
    return Response.json({ activity_id: savedId, replayed: true }, { status: 201 });
  });
  const c = legacy();
  for (let i = 0; i < 2; i++) {
    const reply = await executeRegisteredTool(c, agent, "log-activity", { ...activity }, upstream.env);
    assert.deepEqual(reply, { ok: true, subject: { type: "deal", id: deal }, activity_id: savedId, replayed: true });
  }
  const [request] = upstream.calls;
  assert.ok(request);
  assert.equal(upstream.calls.length, 3);
  assert.equal(receipts.size, 1);
  assert.equal(request.path, "/jobs/activities");
  assert.equal(request.method, "POST");
  assert.equal(request.redirect, "error");
  assert.equal(request.headers.get("Engine-Job"), "agent");
  assert.equal(request.headers.get("Engine-For-User"), "dell");
  assert.equal(request.headers.get("CF-Access-Client-Id"), upstream.env.ENGINE_ACCESS_CLIENT_ID);
  assert.equal(request.headers.get("CF-Access-Client-Secret"), upstream.env.ENGINE_ACCESS_CLIENT_SECRET);
  assert.deepEqual(request.body, {
    idempotency_key: key,
    kind: "call",
    happened_at: "2026-10-10T15:13:27-05:00",
    summary: "Reviewed the specimen\n\nFollow up on the measurements.",
    about: [{ deal_id: deal }],
  });
});

test("a final refusal never retries or writes to the legacy database", async (t) => {
  const upstream = engine(t, () =>
    Response.json({ error: "unknown_job", hint: "untrusted response" }, { status: 403 }),
  );
  await assert.rejects(executeRegisteredTool(legacy(), agent, "log-activity", { ...activity }, upstream.env), {
    payload: { error: "engine_assistant_refused", engine_status: 403, engine_error: "unknown_job" },
  });
  assert.equal(upstream.calls.length, 1);
});

test("sets a deal step using the atomic replacement and explicit owner contract", async (t) => {
  const upstream = engine(t, () => Response.json({ next_step_id: savedId, change_id: 7 }, { status: 201 }));
  const reply = await executeRegisteredTool(
    legacy(),
    agent,
    "set-next-action",
    {
      idempotency_key: key,
      ref: deal,
      description: "Send sample measurements",
      owner: "joe",
    },
    upstream.env,
  );
  assert.deepEqual(reply, {
    ok: true,
    next_action_id: savedId,
    subject: { type: "deal", id: deal },
    owner: "joe",
  });
  const [request] = upstream.calls;
  assert.ok(request);
  assert.equal(request.path, "/jobs/next-steps");
  assert.deepEqual(request.body, {
    idempotency_key: key,
    deal_id: deal,
    what: "Send sample measurements",
    owner: "joe",
    replaces: "current",
  });
});

test("unsupported activity does not spill into the engine", async (t) => {
  const upstream = engine(t, () => assert.fail("unmoved activity made an engine request"));
  const c = legacy({ writes: true });
  const { occurred_at, ...withoutTime } = activity;
  const reply = record(await executeRegisteredTool(c, agent, "log-activity", withoutTime, upstream.env));
  assert.equal(reply.activity_id, savedId);
  assert.ok(c.changes.some((sql) => sql.startsWith("insert into activity")));
  assert.equal(upstream.calls.length, 0);
});

test("call-verb uses the partner grant and lets the engine default the owner", async (t) => {
  const upstream = engine(t, () => Response.json({ next_step_id: savedId }, { status: 201 }));
  const c = legacy();
  t.mock.method(Pool.prototype, "connect", async () => ({
    async query(sql: string) {
      if (sql.startsWith("select id from actor")) return { rows: [{ id: agent.id }] };
      if (sql.includes("session_user::text"))
        return {
          rows: [
            {
              session_principal: "carr_writer",
              current_principal: "carr_writer",
              privilege_bundle: "carr_writer",
              member_carr_writer: true,
              member_carr_jobs: false,
              member_carr_authority: false,
              backend_pid: 9,
            },
          ],
        };
      if (/^(begin|commit|rollback)|set_config\(/.test(sql)) return { rows: [] };
      return c.query(sql);
    },
    release() {},
  }));
  t.mock.method(Pool.prototype, "end", async () => {});
  const actor: unknown = connectionForGrant({ slug: "dell" });
  assert.ok(actor);
  const env = { ...upstream.env, DATABASE_URL_WRITER: "postgres://fixture@synthetic.invalid/fixture" };
  const result = record(
    await callTool(env, actor, "call-verb", {
      verb: "set-next-action",
      args: { idempotency_key: key, ref: deal, description: "Send sample measurements", due_on: "2026-10-20" },
    }),
  );
  assert.equal(result.next_action_id, savedId);
  assert.equal(result.owner, "dell");
  const [request] = upstream.calls;
  assert.ok(request);
  assert.equal(request.headers.get("Engine-For-User"), "dell");
  assert.deepEqual(request.body, {
    idempotency_key: key,
    deal_id: deal,
    what: "Send sample measurements",
    replaces: "current",
    due_on: "2026-10-20",
  });
});

test("a future activity is still refused before either write path", async (t) => {
  const upstream = engine(t, () => assert.fail("future activity made an engine request"));
  const occurred_at = new Date(NOW + 60 * 60 * 1000).toISOString();
  await assert.rejects(
    executeRegisteredTool(legacy(), agent, "log-activity", { ...activity, occurred_at }, upstream.env),
    (error: unknown) => error instanceof ToolError && error.payload.error === "occurred_at_in_future",
  );
  assert.equal(upstream.calls.length, 0);
});

test("saves the engine cannot take yet stay on the legacy handler", async (t) => {
  const step = { idempotency_key: key, ref: deal, description: "Send sample measurements" };
  const link = { from_ref: "L-001", to_ref: "C-001", kind: "intro" };
  const assignment = { vendor_id: savedId, base_version: 1, assignment_base_version: 1 };
  const cases = [
    { name: "client activity", subject: "client", verb: "log-activity", args: activity },
    { name: "lead step", subject: "lead", verb: "set-next-action", args: step },
    { name: "human quote", verb: "log-activity", args: { ...activity, human_quote: "Send it over." } },
    { name: "links", verb: "log-activity", args: { ...activity, links: [link] } },
    { name: "vendor roles", verb: "log-activity", args: { ...activity, vendor_assignments: [assignment] } },
    { name: "conditional step", verb: "set-next-action", args: { ...step, expected_action: null } },
    { name: "agent-owned step", verb: "set-next-action", args: { ...step, owner: agent.slug } },
    { name: "unpermitted owner", verb: "set-next-action", args: { ...step, owner: "someone-else" } },
    {
      name: "non-partner caller",
      actor: { ...agent, native_agent_verified: false },
      verb: "log-activity",
      args: activity,
    },
  ];
  const upstream = engine(t, () => Response.json({ activity_id: savedId, next_step_id: savedId }, { status: 201 }));
  for (const { name, subject, actor = agent, verb, args } of cases)
    await assert.rejects(
      executeRegisteredTool(legacy({ subject }), actor, verb, { ...args }, upstream.env),
      LegacyReached,
      name,
    );
  assert.equal(upstream.calls.length, 0);
});

test("owed information stays in the activity summary", async (t) => {
  const upstream = engine(t, () => Response.json({ activity_id: savedId }, { status: 201 }));
  await executeRegisteredTool(legacy(), agent, "log-activity", { ...activity, owed: "The asking rent" }, upstream.env);
  const [request] = upstream.calls;
  assert.ok(request);
  assert.equal(
    record(request.body).summary,
    "Reviewed the specimen\n\nFollow up on the measurements.\n\nInformation still missing\nThe asking rent",
  );
});

test("an ownerless step from a sponsored agent belongs to its partner", async (t) => {
  const upstream = engine(t, () => Response.json({ next_step_id: savedId }, { status: 201 }));
  const reply = record(
    await executeRegisteredTool(
      legacy(),
      agent,
      "set-next-action",
      { idempotency_key: key, ref: deal, description: "Send sample measurements" },
      upstream.env,
    ),
  );
  assert.equal(reply.owner, "dell");
  const [request] = upstream.calls;
  assert.ok(request);
  assert.equal(request.headers.get("Engine-For-User"), "dell");
  assert.equal(Object.hasOwn(record(request.body), "owner"), false);
});

test("a success without an id is retried with the same key", async (t) => {
  let replies = 0;
  const upstream = engine(t, () =>
    Response.json(replies++ ? { activity_id: savedId, replayed: true } : {}, { status: 201 }),
  );
  const reply = record(await executeRegisteredTool(legacy(), agent, "log-activity", { ...activity }, upstream.env));
  assert.equal(reply.activity_id, savedId);
  assert.equal(upstream.calls.length, 2);
  assert.deepEqual(upstream.calls[0]?.body, upstream.calls[1]?.body);
});

test("three server failures end as unavailable without a legacy write", async (t) => {
  const upstream = engine(t, () => Response.json({ error: "database_unavailable" }, { status: 503 }));
  await assert.rejects(executeRegisteredTool(legacy(), agent, "log-activity", { ...activity }, upstream.env), {
    payload: { error: "engine_assistant_unavailable" },
  });
  assert.equal(upstream.calls.length, 3);
});

test("the caller sees the engine's refusal code", async (t) => {
  t.mock.method(console, "error", () => {});
  const refuse = (error: string) => engine(t, () => Response.json({ error }, { status: 409 })).env;
  const refusal = async (error: string) =>
    publicError(
      await executeRegisteredTool(legacy(), agent, "log-activity", { ...activity }, refuse(error)).catch(
        (thrown: unknown) => thrown,
      ),
    );
  const projected = await refusal("open_step_exists");
  assert.equal(projected.error, "engine_assistant_refused");
  assert.equal(projected.engine_error, "open_step_exists");
  assert.equal(Object.hasOwn(await refusal("Not A Slug"), "engine_error"), false);
});
