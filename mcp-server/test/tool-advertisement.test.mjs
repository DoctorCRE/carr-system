import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { dispatch, allowedIn } from "../src/mcp.js";
import { TOOLS } from "../src/tools.js";
import { authenticatedIdentity } from "../src/identity.js";

async function rpc(method, params, suffix = "") {
  const request = new Request(`https://synthetic.example/mcp${suffix}`, {
    method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  return (await dispatch(request, {}, {}, authenticatedIdentity.connectionForGrant({ slug: "joe" }))).json();
}

test("ordinary client registrations advertise a compact core with unchanged schemas", async () => {
  const { result } = await rpc("tools/list");
  assert.ok(result.tools.length <= 40, `startup advertised ${result.tools.length} tools`);
  const names = new Set(result.tools.map(t => t.name));
  for (const name of ["standing-context", "list-verbs", "call-verb", "find", "add-loop", "report-problem",
    "record-defect", "engineering-passport-source", "doctrine-sections", "map-architecture"])
    assert.ok(names.has(name), `${name} must remain directly reachable`);
  for (const tool of result.tools) assert.deepEqual(tool.inputSchema, TOOLS[tool.name].inputSchema);
  const settings = JSON.parse(fs.readFileSync(new URL("../../.claude/settings.json", import.meta.url)));
  for (const name of settings.permissions.allow.filter(n => n.startsWith("mcp__carr__")))
    assert.ok(names.has(name.slice("mcp__carr__".length)), `${name} keeps its existing allowlist route`);
});

test("name-specific CARR hook routes remain advertised without editing security settings", async () => {
  const names = (await rpc("tools/list")).result.tools.map(t => t.name);
  for (const file of ["hooks.json", "codex-hooks.json"]) {
    const config = JSON.parse(fs.readFileSync(new URL(`../../ops/config/${file}`, import.meta.url)));
    for (const rows of Object.values(config.hooks || config)) for (const row of rows) {
      const match = /^mcp__\.\*__(?:\(([^)]+)\)|([a-z-]+))$/.exec(row.matcher || "");
      if (!match) continue;
      for (const verb of (match[1] || match[2]).split("|"))
        if (TOOLS[verb]) assert.ok(names.includes(verb), `${file}: keep ${verb}'s direct hook route`);
    }
  }
});

test("the shared client endpoint preserves five verbs' direct and passthrough guards", async () => {
  for (const [name, args, expected] of [
    ["find", { query: "Synthetic", actor: "caller-claimed" }, "caller_authority_field_forbidden"],
    ["log-activity", { kind: "invalid-synthetic-kind" }, "value_not_in_declared_vocabulary"],
    ["standing-context", { detail: "invalid-synthetic-detail" }, "value_not_in_declared_vocabulary"],
    ["add-loop", { kind: "invalid-synthetic-kind" }, "value_not_in_declared_vocabulary"],
    ["report-problem", { situation: "Synthetic", actor: "caller-claimed" }, "caller_authority_field_forbidden"],
  ]) {
    const direct = await rpc("tools/call", { name, arguments: args });
    const delegated = await rpc("tools/call", { name: "call-verb", arguments: { verb: name, args } });
    assert.equal(direct.result.isError, true, name);
    const { correlation_id: directId, ...directError } = JSON.parse(direct.result.content[0].text);
    const { correlation_id: delegatedId, ...delegatedError } = JSON.parse(delegated.result.content[0].text);
    assert.match(directId, /^[a-f0-9-]{36}$/);
    assert.match(delegatedId, /^[a-f0-9-]{36}$/);
    assert.notEqual(directId, delegatedId, "separate requests have separate log lookup ids");
    assert.deepEqual(delegatedError, directError, name);
    assert.equal(JSON.parse(direct.result.content[0].text).error, expected, name);
  }
});

test("the complete registry stays discoverable and every ordinary verb stays authorized", async () => {
  const full = await TOOLS["list-verbs"].handler(null, {}, {});
  assert.equal(full.count, Object.keys(TOOLS).length);
  for (const verb of full.verbs) {
    assert.deepEqual(verb.inputSchema, TOOLS[verb.name].inputSchema);
    assert.equal(allowedIn("full", verb.name, TOOLS[verb.name]), true);
  }
  const filtered = await TOOLS["list-verbs"].handler(null, {}, { filter: "report-problem" });
  assert.ok(filtered.verbs.some(v => v.name === "report-problem"));
});

test("advertisement does not widen locked profiles or Doc's surface", async () => {
  const { result } = await rpc("tools/list", undefined, "?profile=read");
  assert.ok(result.tools.length > 40);
  assert.ok(result.tools.every(t => !TOOLS[t.name].write));
  const out = await rpc("tools/call", { name: "call-verb", arguments: {
    verb: "reassign-deal", args: {},
  } }, "?profile=read");
  assert.equal(JSON.parse(out.result.content[0].text).error, "not_in_profile");
});


test("retired Jev gateway verbs are absent from discovery and refuse execution", async () => {
  const advertised = new Set((await rpc("tools/list")).result.tools.map(tool => tool.name));
  const full = await TOOLS["list-verbs"].handler(null, {}, {});
  for (const name of ["ask-jev", "read-jev-call-receipts", "read-jev-call-receipt-integrity"]) {
    assert.equal(TOOLS[name], undefined, `${name} has no registered implementation`);
    assert.equal(advertised.has(name), false);
    assert.equal(full.verbs.some(verb => verb.name === name), false);
    const direct = await rpc("tools/call", { name, arguments: {} });
    assert.equal(direct.result.isError, true);
    assert.equal(JSON.parse(direct.result.content[0].text).error, "unknown_tool");
  }
});

test("retired machinery verbs are absent from discovery and refuse execution", async () => {
  const full = await TOOLS["list-verbs"].handler(null, {}, {});
  for (const name of ["cadence-status", "record-cadence-receipt", "raise-delivery-cadence-alert",
    "bot-brief", "read-execution-environment-providers", "activate-context-bundle",
    "read-context-activation", "render-context-activation", "read-attempt-reliability",
    "claude-checkpoint", "claude-read-recovery", "close-defect", "read-benchmark-manifest",
    "read-model-role", "read-journey-one-clock", "register-action-class-successor",
    "read-action-class-successors", "read-action-class-gate",
    "teach", "admit-rule", "approve-rule", "activate-rule", "accept-workflow",
    "disable-legacy-schedule", "activate-guidance-registry", "decide-guidance-import-batch",
    "deactivate-guidance-registry", "retire-rule", "amend-rule"]) {
    assert.equal(TOOLS[name], undefined, name);
    assert.equal(full.verbs.some(verb => verb.name === name), false, name);
    const direct = await rpc("tools/call", { name, arguments: {} });
    assert.equal(JSON.parse(direct.result.content[0].text).error, "unknown_tool", name);
  }
  for (const name of ["list-my-codex-sessions", "recall-memory", "engineering-passport-source", "read-resource-dashboard"])
    assert.ok(TOOLS[name], `${name} remains registered`);
});
