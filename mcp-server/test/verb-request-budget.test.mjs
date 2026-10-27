import test from "node:test";
import assert from "node:assert/strict";
import { dispatch } from "../src/mcp.js";

const HUMAN = { slug: "joe", human: true, via: "oauth-google" };

function request(name = "list-verbs", args = {}, query = "") {
  return new Request(`https://api.doctorcre.com/mcp${query}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }),
  });
}

test("exhausted verb budget returns 429 before any application database or AI access", async () => {
  const environment = {
    VERB_REQUEST_BUDGET_HUMAN: { limit: async () => ({ success: true }) },
    VERB_REQUEST_BUDGET: {
      idFromName: name => name,
      get: () => ({ fetch: async () => Response.json({ allowed: false, retryAfter: 17 }) }),
    },
    get DATABASE_URL_READER() { assert.fail("refused request reached the reader database"); },
    get DATABASE_URL_WRITER() { assert.fail("refused request reached the writer database"); },
    get AI() { assert.fail("refused request reached AI"); },
  };
  const response = await dispatch(request(), environment, {}, { ...HUMAN }, "verified-session-a");
  assert.equal(response.status, 429);
  assert.equal(response.headers.get("retry-after"), "17");
  assert.equal(response.headers.get("cache-control"), "no-store");
});

test("limiter binding failures refuse without reaching application dependencies", async () => {
  for (const binding of [undefined, {
    idFromName: name => name,
    get: () => ({ fetch: async () => { throw new Error("unavailable"); } }),
  }]) {
    const response = await dispatch(request("standing-context"), {
      VERB_REQUEST_BUDGET_AGENT: { limit: async () => ({ success: true }) },
      VERB_REQUEST_BUDGET: binding,
      get DATABASE_URL_READER() { assert.fail("unavailable limiter reached database"); },
      get DATABASE_URL_WRITER() { assert.fail("unavailable limiter reached tracing database"); },
    }, {}, { ...HUMAN });
    assert.equal(response.status, 429);
    assert.equal(response.headers.get("retry-after"), "60");
    assert.equal((await response.json()).error, "verb_request_budget_unavailable");
  }
});

test("edge binding refusal makes no billed Durable Object request or application call", async () => {
  let objectRequests = 0;
  const response = await dispatch(request(), {
    VERB_REQUEST_BUDGET_AGENT: { limit: async () => ({ success: false }) },
    VERB_REQUEST_BUDGET: {
      idFromName: name => name,
      get: () => ({ fetch: async () => {
        objectRequests += 1;
        return Response.json({ allowed: true });
      } }),
    },
    get DATABASE_URL_READER() { assert.fail("edge refusal reached reader"); },
    get DATABASE_URL_WRITER() { assert.fail("edge refusal reached writer"); },
  }, {}, { ...HUMAN });
  assert.equal(response.status, 429);
  assert.equal((await response.json()).error, "verb_request_budget_exceeded");
  assert.equal(objectRequests, 0);
});
