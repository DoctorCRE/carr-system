import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createDealroomHandler } from "../src/dealroom-web.js";
import { DELIVERABLE_PREFIX } from "../src/deal-deliverables.js";
import { TOOLS, assertRegisteredToolInput } from "../src/tools.js";
const origin = "https://app.example.test";
const path = `${DELIVERABLE_PREFIX}/10000000-0000-4000-8000-000000000001`;
const opaque = "synthetic-session",
  csrf = "synthetic-csrf";
const key =
  "dealroom_session:" + createHash("sha256").update(opaque).digest("hex");
const now = 1_800_000_000_000;
const session = {
  origin,
  createdAt: now,
  expiresAt: now + 7200000,
  csrfToken: csrf,
  props: {},
};
function fixture() {
  let active = true,
    calls = 0;
  const env = {
    APP_HOST: "app.example.test",
    WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true",
    OAUTH_KV: {
      async get(k) {
        return active && k === key ? session : null;
      },
      async put() {},
      async delete() {
        active = false;
      },
    },
  };
  const handler = createDealroomHandler({
    now: () => now,
    actorFromPropsFn: () => ({ slug: "joe", human: true }),
    deliverablesHandler: async (_r, _e, actor, stillActive) => {
      calls++;
      assert.equal(actor.slug, "joe");
      assert.equal(await stillActive(), true);
      active = false;
      assert.equal(await stillActive(), false);
      return new Response("{}");
    },
  });
  const request = (method = "GET", headers = {}) =>
    new Request(origin + path, {
      method,
      headers: { cookie: `__Host-dealroom_session=${opaque}`, ...headers },
    });
  return {
    env,
    handler,
    request,
    count: () => calls,
    revoke: () => {
      active = false;
    },
  };
}
test("real Worker session gate refuses missing and revoked cookies", async () => {
  const f = fixture();
  let response = await f.handler.fetch(new Request(origin + path), f.env, {});
  assert.equal(response.status, 401);
  f.revoke();
  response = await f.handler.fetch(f.request(), f.env, {});
  assert.equal(response.status, 401);
  assert.equal(f.count(), 0);
});

test("unsettled storage calls remain capped after their deadlines", async () => {
  const { createDealDeliverablesHandler } =
    await import("../src/deal-deliverables-web.js");
  const id = "20000000-0000-4000-8000-000000000002";
  const row = {
    id,
    deal_id: path.split("/").at(-1),
    object_key: `deliverables/carr-internal/${path.split("/").at(-1)}/${id}`,
    state: "finalized",
  };
  let calls = 0,
    release;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  const bucket = {
    get: () => {
      calls++;
      return pending;
    },
  };
  const transaction = async (fn) =>
    fn({
      query: async (sql) => ({
        rows: sql.includes("select d.owner") ? [{ owner: "joe" }] : [row],
      }),
    });
  const handler = createDealDeliverablesHandler({
    transaction,
    bucket,
    operationTimeoutMs: 10,
  });
  const results = await Promise.all(
    Array.from({ length: 8 }, () =>
      handler(
        new Request(origin + path + `/${id}/download`),
        { slug: "joe", human: true },
        async () => true,
      ),
    ),
  );
  assert.equal(calls, 4);
  assert.ok(results.every((r) => r.status === 503));
  release(null);
});
test("real Worker gate refuses cross-origin and missing CSRF mutations", async () => {
  for (const headers of [
    {},
    { origin, "sec-fetch-site": "same-origin" },
    {
      origin: "https://attacker.test",
      "sec-fetch-site": "same-origin",
      "x-carr-csrf": csrf,
    },
  ]) {
    const f = fixture();
    const response = await f.handler.fetch(
      f.request("POST", headers),
      f.env,
      {},
    );
    assert.equal(response.status, 403);
    assert.equal(f.count(), 0);
  }
});
test("real Worker gate provides the partner and a live revocation recheck", async () => {
  for (const method of ["GET", "POST", "PUT"]) {
    const f = fixture();
    const response = await f.handler.fetch(
      f.request(method, {
        origin,
        "sec-fetch-site": "same-origin",
        "x-carr-csrf": csrf,
      }),
      f.env,
      {},
    );
    assert.equal(response.status, 200);
    assert.equal(f.count(), 1);
  }
});
test("deliverable verbs are registered with closed schemas and partner-only writes", () => {
  for (const verb of [
    "list-deal-deliverables",
    "start-deal-deliverable",
    "finalize-deal-deliverable",
    "set-deal-deliverable-visibility",
  ]) {
    const tool = TOOLS[verb];
    assert.ok(tool);
    assert.equal(tool.inputSchema.additionalProperties, false);
    assert.equal("r2_key" in tool.inputSchema.properties, false);
    if (tool.write) {
      assert.equal(tool.humanOnly, true);
      assert.equal(tool.humanCallerOnly, true);
    }
  }
});
test("registered upload rejects a caller-selected object key before database access", async () => {
  await assert.rejects(
    assertRegisteredToolInput(
      "start-deal-deliverable",
      TOOLS["start-deal-deliverable"],
      { r2_key: "documents/forged" },
    ),
    (e) => e.payload?.error === "unregistered_operation_fields",
  );
});

test("storage circuit and rate admission fail closed", async (t) => {
  t.mock.method(Date, "now", () => now);
  const { createDealDeliverablesHandler } =
    await import("../src/deal-deliverables-web.js");
  const id = "20000000-0000-4000-8000-000000000002";
  let calls = 0,
    healthy = false;
  const bytes = new TextEncoder().encode("%PDF-1.7\nSynthetic");
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const row = {
    id,
    deal_id: path.split("/").at(-1),
    object_key: `deliverables/carr-internal/${path.split("/").at(-1)}/${id}`,
    state: "finalized",
    bytes: bytes.length,
    mime: "application/pdf",
    sha256,
    filename: "synthetic.pdf",
  };
  const bucket = {
    async get() {
      calls++;
      if (healthy)
        return { size: bytes.length, body: bytes, customMetadata: { sha256 } };
      throw new Error("offline");
    },
  };
  const transaction = async (fn) =>
    fn({
      query: async (sql) => ({
        rows: sql.includes("select d.owner") ? [{ owner: "joe" }] : [row],
      }),
    });
  const handler = createDealDeliverablesHandler({
    transaction,
    bucket,
    operationTimeoutMs: 10,
  });
  const get = () =>
    handler(
      new Request(origin + path + `/${id}/download`),
      { slug: "joe", human: true },
      async () => true,
    );
  for (let i = 0; i < 4; i++) assert.equal((await get()).status, 503);
  assert.equal(calls, 3, "fourth call must not reach open dependency");
  for (let i = 4; i < 60; i++) await get();
  assert.equal((await get()).status, 429);
  const future = now + 61_000;
  t.mock.method(Date, "now", () => future);
  healthy = true;
  assert.equal((await get()).status, 200, "half-open probe restores service");
  assert.equal((await get()).status, 200);
  assert.equal(calls, 5);
});
