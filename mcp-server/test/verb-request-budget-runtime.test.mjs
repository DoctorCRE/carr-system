import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { Miniflare } from "miniflare";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { dispatch } from "../src/mcp.js";

async function runtime(persist, alarmFixture = false) {
  const built = await build({
    entryPoints: [new URL("../src/verb-request-budget.js", import.meta.url).pathname],
    bundle: true, write: false, format: "esm", platform: "node",
  });
  const fixture = alarmFixture ? `
    export class AlarmBudget extends VerbRequestBudget {
      async fetch(request) {
        if (new URL(request.url).pathname === "/advance") {
          Date.now = () => 1800000060000;
          await this.alarm();
          return new Response(null, { status: 204 });
        }
        return super.fetch(request);
      }
    }` : "";
  const className = alarmFixture ? "AlarmBudget" : "VerbRequestBudget";
  return new Miniflare({
    cf: false,
    ...(persist ? { resourcePersistencePath: persist } : {}),
    workers: [{ config: {
      name: "budget-test",
      compatibilityDate: "2026-07-01", compatibilityFlags: ["nodejs_compat"],
      manifest: { mainModule: "budget.js", modules: { "budget.js": { type: "esm", contents: "Date.now = () => 1800000000000;\n" + built.outputFiles[0].text + fixture } } },
      env: {
        VERB_REQUEST_BUDGET: { type: "durable-object", worker: "budget-test", exportName: className },
        VERB_REQUEST_BUDGET_HUMAN: { type: "rate-limit", namespace: "56401", simple: { limit: 120, period: 60 } },
        VERB_REQUEST_BUDGET_AGENT: { type: "rate-limit", namespace: "56402", simple: { limit: 2400, period: 60 } },
      },
      exports: { [className]: { type: "durable-object", storage: "sqlite" } },
    } }],
  });
}

function request(name = "no-such-verb", query = "") {
  return new Request(`https://api.doctorcre.com/mcp${query}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name } }),
  });
}

const HUMAN = { slug: "joe", human: true, via: "oauth-google" };
const AGENT = { slug: "codex", human: false, sponsoring_human_slug: "joe" };

test("workerd enforces concurrent session budgets and persists across worker restarts", async t => {
  const directory = await mkdtemp(path.join(tmpdir(), "verb-budget-"));
  t.mock.method(console, "error", () => {});
  let mf = await runtime(directory);
  t.after(async () => { await mf.dispose(); await rm(directory, { recursive: true, force: true }); });
  let env = await mf.getBindings();
  const responses = await Promise.all(Array.from({ length: 80 }, (_, index) =>
    dispatch(request(index % 2 ? "call-verb" : "no-such-verb", index % 2 ? "?profile=full&budget=agent" : ""),
      env, {}, { ...HUMAN }, "session-a")));
  assert.equal(responses.filter(response => response.status === 200).length, 60);
  assert.equal(responses.filter(response => response.status === 429).length, 20);
  await mf.dispose();
  mf = await runtime(directory);
  env = await mf.getBindings();
  assert.equal((await dispatch(request(), env, {}, { ...HUMAN }, "session-a")).status, 429);
  assert.equal((await dispatch(request(), env, {}, { ...HUMAN }, "session-b")).status, 200);
  assert.equal((await dispatch(request(), env, {}, { ...HUMAN, slug: "dell" }, "session-a")).status, 200);
  assert.equal((await dispatch(request(), env, {}, { ...AGENT })).status, 200);
});

test("workerd agent quota admits 1200 requests and isolates authenticated principals", async t => {
  t.mock.method(console, "error", () => {});
  const directory = await mkdtemp(path.join(tmpdir(), "verb-budget-"));
  const mf = await runtime(directory);
  t.after(async () => { await mf.dispose(); await rm(directory, { recursive: true, force: true }); });
  const env = await mf.getBindings();
  const responses = await Promise.all(Array.from({ length: 1201 }, () =>
    dispatch(request(), env, {}, { ...AGENT })));
  assert.equal(responses.filter(response => response.status === 200).length, 1200);
  assert.equal(responses.filter(response => response.status === 429).length, 1);
  assert.equal((await dispatch(request(), env, {}, { ...AGENT, slug: "claude" })).status, 200);
  assert.equal((await dispatch(request(), env, {}, { ...AGENT, sponsoring_human_slug: "dell" })).status, 200);
});

test("workerd alarm cleans an expired window and the next request receives a fresh budget", async t => {
  const directory = await mkdtemp(path.join(tmpdir(), "verb-budget-"));
  const mf = await runtime(directory, true);
  t.after(async () => { await mf.dispose(); await rm(directory, { recursive: true, force: true }); });
  const binding = (await mf.getBindings()).VERB_REQUEST_BUDGET;
  const stub = binding.get(binding.idFromName("alarm-fixture"));
  const consume = () => stub.fetch("https://budget.internal/consume", {
    method: "POST", body: JSON.stringify({ kind: "human" }),
  });
  for (let index = 0; index < 60; index++) assert.equal((await (await consume()).json()).allowed, true);
  assert.equal((await (await consume()).json()).allowed, false);
  assert.equal((await stub.fetch("https://budget.internal/advance")).status, 204);
  assert.equal((await (await consume()).json()).allowed, true);
});

test("workerd admits the supplied 500 per minute OAuth connector peak", async t => {
  t.mock.method(console, "error", () => {});
  const mf = await runtime();
  t.after(() => mf.dispose());
  const env = await mf.getBindings();
  const responses = await Promise.all(Array.from({ length: 500 }, () =>
    dispatch(request(), env, {}, { ...HUMAN })));
  assert.equal(responses.filter(response => response.status === 200).length, 500);
  assert.equal(responses.filter(response => response.status === 429).length, 0);
});
