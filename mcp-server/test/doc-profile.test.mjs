import test from "node:test";
import assert from "node:assert/strict";
import { dispatch, allowedIn, profileForActor, mcpApiHandler } from "../src/mcp.js";
import { TOOLS } from "../src/tools.js";
import { authenticatedIdentity } from "../src/identity.js";
import { Pool } from "@neondatabase/serverless";

const READS = ["catch-me-up", "today-triage", "find", "find-and-catch-up",
  "deal-board", "get-deal-room", "who-do-we-know", "counterparty-history",
  "lead-board", "schedule-board", "search-tour-properties", "read-doctrine",
  "search-doctrine", "recall-memory"];
const WRITES = ["add-deal-note", "log-activity", "set-next-step", "add-critical-date", "log-capture"];
const ACKNOWLEDGEMENTS = ['whats-new'];
const actor = () => authenticatedIdentity.connectionForGrant({ slug: "joe" });
async function rpc(path, method, params) {
  const request = new Request(`https://synthetic.example${path}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  return (await dispatch(request, {}, {}, actor())).json();
}

test("Doc HTTP discovery exposes exactly the curated brokerage tools", async () => {
  const { result } = await rpc("/doc/mcp?profile=full", "tools/list");
  assert.deepEqual(result.tools.map(t => t.name).sort(), [...READS, ...WRITES, ...ACKNOWLEDGEMENTS].sort());
  for (const t of result.tools) {
    assert.equal(t.annotations.readOnlyHint, READS.includes(t.name), t.name);
    assert.equal(t.annotations.readOnlyHint, !TOOLS[t.name].write,
      `${t.name}: a registry write must never be advertised as read-only`);
    assert.equal(t.annotations.destructiveHint, false, t.name);
    assert.equal(t.annotations.openWorldHint, false, t.name);
    assert.deepEqual(t.inputSchema, TOOLS[t.name].inputSchema, "original concurrency/idempotency schema");
    if (WRITES.includes(t.name)) assert.ok(t.inputSchema.required.includes("idempotency_key"));
  }
});

test("Doc refuses guessed reads, destructive writes and passthroughs before any database access", async () => {
  for (const [name, args] of [["list-verbs", {}], ["read-profiles", {}],
    ["confirm-merge", {}], ["reassign-deal", {}],
    ["call-verb", { verb: "find", args: { query: "Synthetic" } }]]) {
    const { result } = await rpc("/doc/mcp?profile=full", "tools/call", { name, arguments: args });
    assert.equal(result.isError, true, name);
    assert.equal(JSON.parse(result.content[0].text).error, "not_in_profile", name);
  }
  for (const suffix of ["", "?profile=full", "?profile=capture", "?profile=unknown"]) {
    assert.equal(profileForActor(actor(), new Request(`https://synthetic.example/doc/mcp${suffix}`)), "doc");
  }
  assert.equal(allowedIn("doc", "read-profiles", TOOLS["read-profiles"]), false);
});

test("Doc retains the narrow activity payload guard", async () => {
  const { result } = await rpc("/doc/mcp", "tools/call", { name: "log-activity", arguments: {
    idempotency_key: "d170d000-0000-4000-8000-000000000003", ref: "SYNTHETIC",
    kind: "note", summary: "Synthetic fixture", links: [{ from_ref: "SYNTHETIC-A", to_ref: "SYNTHETIC-B", kind: "knows" }],
  } });
  assert.equal(JSON.parse(result.content[0].text).error, "not_in_profile");
});

test("Doc cannot override capture dedup with caller-asserted confirmation", async () => {
  const { result } = await rpc("/doc/mcp", "tools/call", { name: "log-capture", arguments: {
    idempotency_key: "d170d000-0000-4000-8000-000000000004", session: "Synthetic source",
    status: "queued", force_new: true,
  } });
  assert.equal(JSON.parse(result.content[0].text).error, "not_in_profile");
});

test("Doc refuses every accepted true encoding before capture database access", async () => {
  const connect = Pool.prototype.connect;
  let connections = 0;
  Pool.prototype.connect = async () => {
    connections++;
    throw new Error("synthetic_database_boundary_reached");
  };
  try {
    for (const force_new of [true, "true", "TRUE", " True ", "\ttrue\n"]) {
      const { result } = await rpc("/doc/mcp", "tools/call", { name: "log-capture", arguments: {
        idempotency_key: "d170d000-0000-4000-8000-000000000008", session: "Synthetic source",
        status: "queued", force_new,
      } });
      assert.equal(JSON.parse(result.content[0].text).error, "not_in_profile", JSON.stringify(force_new));
      assert.equal(connections, 0, "a refused override must never open the writer connection");
    }
  } finally {
    Pool.prototype.connect = connect;
  }
});

test("Doc refuses array and serialized activity relationships before database access", async () => {
  const connect = Pool.prototype.connect;
  let connections = 0;
  Pool.prototype.connect = async () => {
    connections++;
    throw new Error("synthetic_database_boundary_reached");
  };
  try {
    const relationships = [{ from_ref: "SYNTHETIC-A", to_ref: "SYNTHETIC-B", kind: "knows" }];
    for (const links of [relationships, JSON.stringify(relationships), ` \n${JSON.stringify(relationships)}\t`]) {
      const { result } = await rpc("/doc/mcp", "tools/call", { name: "log-activity", arguments: {
        idempotency_key: "d170d000-0000-4000-8000-000000000009", ref: "SYNTHETIC",
        kind: "note", summary: "Synthetic fixture", links,
      } });
      assert.equal(JSON.parse(result.content[0].text).error, "not_in_profile", JSON.stringify(links));
      assert.equal(connections, 0, "a refused relationship must never open the writer connection");
    }
  } finally {
    Pool.prototype.connect = connect;
  }
});

test("Doc still admits false capture overrides and empty activity links", async () => {
  const connect = Pool.prototype.connect;
  let connections = 0;
  Pool.prototype.connect = async () => {
    connections++;
    throw new Error("synthetic_database_boundary_reached");
  };
  try {
    for (const force_new of [false, "false", " FALSE "]) {
      const { result } = await rpc("/doc/mcp", "tools/call", { name: "log-capture", arguments: {
        idempotency_key: "d170d000-0000-4000-8000-000000000010", session: "Synthetic source",
        status: "queued", force_new,
      } });
      assert.equal(result.isError, true);
      assert.equal(JSON.parse(result.content[0].text).error, "unhandled_verb_failure");
    }
    for (const links of [[], "[]", " \n[]\t"]) {
      const { result } = await rpc("/doc/mcp", "tools/call", { name: "log-activity", arguments: {
        idempotency_key: "d170d000-0000-4000-8000-000000000011", ref: "SYNTHETIC",
        kind: "note", summary: "Synthetic fixture", links,
      } });
      assert.equal(result.isError, true);
      assert.equal(JSON.parse(result.content[0].text).error, "unhandled_verb_failure");
    }
    assert.equal(connections, 6);
  } finally {
    Pool.prototype.connect = connect;
  }
});

test("ordinary /mcp keeps its tool contracts and boot instructions behind compact discovery", async () => {
  const { result } = await rpc("/mcp", "tools/list");
  assert.ok(result.tools.length < Object.keys(TOOLS).length);
  for (const tool of result.tools) {
    assert.equal(tool.description, TOOLS[tool.name].description);
    assert.deepEqual(tool.inputSchema, TOOLS[tool.name].inputSchema);
    assert.deepEqual(tool.annotations, {
      readOnlyHint: !TOOLS[tool.name].write, destructiveHint: TOOLS[tool.name].destructiveHint ?? Boolean(TOOLS[tool.name].write),
      idempotentHint: true, openWorldHint: false,
    });
  }
  const initialized = (await rpc("/mcp", "initialize")).result;
  assert.match(initialized.instructions, /call standing-context FIRST/);
  assert.match(initialized.instructions, /list-verbs with a filter.*call-verb/);
  assert.match(initialized.instructions, /idempotency_key.*base_version/);
  assert.equal(initialized.serverInfo.name, "carr-record-layer");
  assert.deepEqual((await rpc("/mcp", "ping")).result, {});
});

test("Doc initialization describes brokerage usage without the engineering briefing", async () => {
  const { result } = await rpc("/doc/mcp", "initialize");
  assert.match(result.instructions, /brokerage colleague/i);
  assert.match(result.instructions, /^When .*what is new.*what they missed.*cold.*whats-new first/i);
  assert.match(result.instructions, /idempotency_key/);
  assert.doesNotMatch(result.instructions, /DELEGATION LATCH|standing-context FIRST/);
});

test("the Doc OAuth adapter requires a resolved partner grant, never a purpose-bound machine grant", async () => {
  const request = () => new Request("https://api.doctorcre.com/doc/mcp?profile=full", {
    method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
  });
  const partner = await mcpApiHandler.fetch(request(), {}, { props: { slug: "joe" } });
  assert.equal(partner.status, 200);
  assert.deepEqual((await partner.json()).result.tools.map(t => t.name).sort(), [...READS, ...WRITES, ...ACKNOWLEDGEMENTS].sort());
  assert.equal((await mcpApiHandler.fetch(request(), {}, { props: { slug: "codex" } })).status, 403);
  assert.equal((await mcpApiHandler.fetch(request(), {}, { props: { slug: "codex", human: false } })).status, 403);
  assert.equal((await mcpApiHandler.fetch(request(), {}, { props: { slug: "synthetic-unknown" } })).status, 401);
});
