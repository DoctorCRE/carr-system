import test from "node:test";
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { createLeadBoardClient } from "../../dealroom/js/leads-client.js";
import { createLiveClient } from "../../dealroom/js/live-client.js";
import { createSystemWorkClient } from "../../dealroom/js/system-work-client.js";

const fixture = new URL("./fixtures/web-client-contract.json", import.meta.url);
const envelope = (payload, isError = false) => ({ result: {
  content: [{ type: "text", text: JSON.stringify(payload) }], isError,
} });
const cases = {
  success: { body: envelope({ ok: true, value: "synthetic" }) },
  conflict: { body: envelope({ ok: false, conflict: { id: "synthetic-conflict" } }) },
  payloadError: { body: envelope({ error: "version_conflict", hint: "Changed elsewhere." }) },
  toolError: { body: envelope({ error: "refused", message: "No change.", hint: "Hint." }, true) },
  toolErrorWithoutPayload: { body: { result: { isError: true } } },
  rpcError: { body: { error: { code: "rpc_fault", message: "RPC refused." } } },
  secondText: { body: { result: { content: [{ type: "image", data: "synthetic" }, { type: "text", text: '{"ok":true}' }] } } },
  emptyText: { body: { result: { content: [{ type: "text", text: "" }] } } },
  missingResult: { body: {} },
  nullEnvelope: { body: null },
  invalidPayload: { body: { result: { content: [{ type: "text", text: "invalid" }] } } },
  invalidJson: { raw: "invalid" },
  denied: { status: 403, body: { error: "denied", message: "Sign in.", hint: "Hint." } },
  httpFault: { status: 502, raw: "synthetic server failure ".repeat(30) },
  network: { network: true },
};

function response(spec) {
  if (spec.network) throw new TypeError("synthetic connection dropped");
  return new Response(spec.raw ?? JSON.stringify(spec.body), { status: spec.status || 200 });
}

async function observe(operation, spec) {
  const requests = [];
  const fetchImpl = async (path, init) => {
    requests.push({ path, ...init, ...(init.body ? { body: JSON.parse(init.body) } : {}) });
    return response(spec);
  };
  let outcome;
  try {
    outcome = { value: await operation(fetchImpl) };
  } catch (error) {
    outcome = { error: { name: error.name, message: error.message } };
    for (const key of ["code", "status", "body", "payload"]) {
      if (key in error) outcome.error[key] = error[key];
    }
    if (error.cause) outcome.error.cause = { name: error.cause.name, message: error.cause.message };
  }
  return { requests, outcome };
}

async function contract() {
  const observations = {};
  for (const [name, spec] of Object.entries(cases)) {
    observations[`leads/${name}`] = await observe(async (fetchImpl) => {
      const client = createLeadBoardClient({ fetchImpl, uuid: () => "synthetic-key" });
      return client.moveLeadStage({ registry_ref: "L-000", base_version: 7 }, "contacted");
    }, spec);
    observations[`live/${name}`] = await observe(async (fetchImpl) => {
      const client = createLiveClient({ fetchImpl });
      return client.addDealNote({ deal: "synthetic-deal", text: "synthetic note", idempotency_key: "synthetic-key" });
    }, spec);
    observations[`system/${name}`] = await observe(async (fetchImpl) => {
      const client = createSystemWorkClient({ fetchImpl });
      return client.read("WR-000123");
    }, spec);
  }
  observations.rpcIds = await observe(async (fetchImpl) => {
    const lead = createLeadBoardClient({ fetchImpl });
    const live = createLiveClient({ fetchImpl });
    await lead.getLeadBoard();
    await live.getHealth({});
    await lead.getLeadBoard();
    return live.getHealth({});
  }, cases.success);
  observations.systemSession = await observe(async (fetchImpl) => {
    const client = createSystemWorkClient({ fetchImpl, uuid: () => "synthetic-key" });
    await client.bootstrap();
    await client.acceptPlan("WR-000123", { base_version: 3, plan_hash: "synthetic-hash" });
    await client.acceptOutcome("WR-000123", { base_version: 4, feedback_hash: "synthetic-hash" });
    return client.session;
  }, { body: { csrf_token: "synthetic-csrf", data: { challenge: "synthetic-challenge" } } });
  observations.systemNotReady = await observe(async (fetchImpl) => {
    return createSystemWorkClient({ fetchImpl }).report({});
  }, cases.success);
  return observations;
}

if (process.argv.includes("--capture")) {
  await writeFile(fixture, JSON.stringify(await contract(), null, 2) + "\n");
} else {
  test("web clients preserve the captured requests, payloads, and errors", async () => {
    const expected = JSON.parse(await readFile(fixture, "utf8"));
    assert.deepEqual(await contract(), expected);
  });
}
