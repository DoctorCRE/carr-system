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
    "record-defect", "teach", "activate-rule", "engineering-passport-source", "doctrine-sections", "ask-jev", "map-architecture"])
    assert.ok(names.has(name), `${name} must remain directly reachable`);
  for (const tool of result.tools) assert.deepEqual(tool.inputSchema, TOOLS[tool.name].inputSchema);
  const settings = JSON.parse(fs.readFileSync(new URL("../../.claude/settings.json", import.meta.url)));
  for (const name of settings.permissions.allow.filter(n => n.startsWith("mcp__carr__")))
    assert.ok(names.has(name.slice("mcp__carr__".length)), `${name} keeps its existing allowlist route`);
  for (const file of ["engineering_dispatch_adapter.py"]) {
    const source = fs.readFileSync(new URL(`../../tools/room-bridge/${file}`, import.meta.url), "utf8");
    for (const match of source.matchAll(/tools\.mcp__carr__([a-z_]+)/g))
      assert.ok(names.has(match[1].replaceAll("_", "-")), `${file}: preserve the Model Room source route`);
  }
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
    assert.deepEqual(delegated.result, direct.result, name);
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
