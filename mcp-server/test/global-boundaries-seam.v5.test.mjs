// V5-S01 — the door is wired at the dispatch seam, in shadow.
//
// executeRegisteredTool is the one function every door (connector read,
// connector write, local break-glass) passes. These tests go through it, not
// around it: the door must evaluate every dispatch there, record a refusal
// verdict, and in shadow let the handler run exactly as before.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { TOOLS, executeRegisteredTool } from "../src/tools.js";
import { v5BoundaryPolicyDigest } from "../src/global-boundaries.v5.js";
import {
  V5_BOUNDARY_DOOR_MODE,
  doorObservationSnapshot,
  resetDoorObservationForTest,
} from "../src/global-boundaries-door.v5.js";

const DELL = { slug: "dell", human: true, via: "oauth-google", sponsoring_human_slug: null };
const SENTINEL = "handler-reached-the-database";
const recordingClient = () => {
  const queries = [];
  return {
    queries,
    query: async text => { queries.push(text); throw new Error(SENTINEL); },
  };
};

test("the read projection is registered as a read verb and served through the seam", async () => {
  const tool = TOOLS["read-global-boundaries"];
  assert.ok(tool, "read-global-boundaries is registered");
  assert.notEqual(tool.write, true);
  assert.notEqual(tool.humanOnly, true);
  assert.notEqual(tool.authorityOnly, true);
  assert.equal(tool.registrySource, "mcp-server/src/global-boundaries-door.v5.js");
  resetDoorObservationForTest();
  const client = recordingClient();
  const result = await executeRegisteredTool(client, DELL, "read-global-boundaries", {});
  assert.equal(result.projection.policy_digest, v5BoundaryPolicyDigest());
  assert.equal(result.door.mode, "shadow");
  assert.equal(client.queries.length, 0, "the projection reads no database");
  assert.equal(doorObservationSnapshot().evaluated, 1, "the seam evaluated this dispatch");
});

test("shadow at the seam: Dell's approve-rule is recorded as refused and still reaches the handler", async () => {
  assert.equal(V5_BOUNDARY_DOOR_MODE, "shadow");
  resetDoorObservationForTest();
  const warned = [];
  const original = console.warn;
  console.warn = line => warned.push(line);
  try {
    await assert.rejects(executeRegisteredTool(recordingClient(), DELL, "approve-rule", {
      idempotency_key: "00000000-0000-4000-8000-000000000001", rule_id: "00000000-0000-4000-8000-000000000002",
      policy_kind: "machine_enforceable", control_keys: [], reason: "seam test",
    }), error => {
      // The refusal a caller sees today is the database's (reached via the
      // handler), never the door's: shadow does not block.
      assert.notEqual(error?.payload?.error, "v5_boundary_refused");
      return true;
    });
  } finally {
    console.warn = original;
  }
  const snapshot = doorObservationSnapshot();
  assert.equal(snapshot.evaluated, 1);
  assert.equal(snapshot.boundary_refused, 1);
  assert.equal(snapshot.enforced, 0);
  assert.equal(snapshot.by_reason["actor_authority:system_authority_reserved_to_joe"], 1);
  const line = warned.map(l => JSON.parse(l)).find(l => l.event === "v5_boundary_door");
  assert.equal(line.verb, "approve-rule");
  assert.ok(!warned.join("").includes("seam test"), "argument values never reach the log");
});

// #1268 review, surviving mutant 1: a seam that took its mode (or context)
// from the caller's arguments. accept-workflow legitimately carries a `mode`
// argument ("canary"), so a seam reading args.mode would hand the door an
// unregistered mode: the dispatch would show up as a door_error instead of a
// recorded refusal. And a caller must never be able to pick "enforce" or
// "shadow" for itself.
test("the seam's mode and context are the server's, never the caller's arguments", async () => {
  resetDoorObservationForTest();
  const original = console.warn;
  console.warn = () => {};
  const args = {
    idempotency_key: "00000000-0000-4000-8000-000000000003", workflow_key: "wf",
    mode: "canary", receipt_ref: "r1",
  };
  const before = JSON.stringify(args);
  try {
    await assert.rejects(executeRegisteredTool(recordingClient(), DELL, "accept-workflow", args),
      error => error?.payload?.error !== "v5_boundary_refused");
  } finally {
    console.warn = original;
  }
  const snapshot = doorObservationSnapshot();
  assert.equal(snapshot.door_errors, 0, "the door was handed the server's mode, not args.mode");
  assert.equal(snapshot.boundary_refused, 1);
  assert.equal(snapshot.by_reason["actor_authority:system_authority_reserved_to_joe"], 1);
  assert.equal(snapshot.recent_refused[0].mode, "shadow");
  // Surviving mutant 2, at the seam: the door leaves the arguments exactly as sent.
  assert.equal(JSON.stringify(args), before);
});

test("the seam call sits after coercion and before the handler, and maps an enforce refusal by name", () => {
  const source = readFileSync(new URL("../src/tools.js", import.meta.url), "utf8");
  const start = source.indexOf("export async function executeRegisteredTool(");
  const body = source.slice(start, source.indexOf("\nconst TOOL_REGISTRATION_SOURCE", start));
  const required = body.indexOf("assertRequiredArgs(tool.inputSchema, args);");
  const doorCall = body.indexOf("passBoundaryDoor({ verb: name, write: tool.write === true, actor, args,");
  const handler = body.indexOf("return await tool.handler(client, actor, args);");
  assert.ok(required > 0 && doorCall > required && handler > doorCall,
    "the door runs after the required-argument check and before the handler");
  assert.match(body, /if \(error instanceof V5BoundaryDoorRefusal\) throw new ToolError\(error\.payload\);/);
  // The door context is never taken from the caller.
  assert.doesNotMatch(body.slice(doorCall, doorCall + 200), /args\.(connectivity|context|now)/);
});

// #1268 review, robustness: the seam hands the door exactly five inputs, and
// none of mode, context or log. Any extra key (`mode: args.door_mode`,
// `context: args.context`, `log: ...`) or a changed value fails here, not just
// the spellings the previous test happens to name.
test("the seam passes the door only verb, write, actor, args and now, each from the server", () => {
  const source = readFileSync(new URL("../src/tools.js", import.meta.url), "utf8");
  const start = source.indexOf("export async function executeRegisteredTool(");
  const body = source.slice(start, source.indexOf("\nconst TOOL_REGISTRATION_SOURCE", start));
  const calls = body.split("passBoundaryDoor(").length - 1;
  assert.equal(calls, 1, "exactly one door call in the dispatch seam");
  const open = body.indexOf("passBoundaryDoor({") + "passBoundaryDoor({".length;
  let depth = 1;
  let end = open;
  for (; end < body.length && depth > 0; end += 1) {
    if (body[end] === "{") depth += 1;
    else if (body[end] === "}") depth -= 1;
  }
  const literal = body.slice(open, end - 1).replace(/\s+/g, " ").trim();
  assert.equal(body.slice(end, end + 2), ");", "the door call takes one object literal and nothing else");
  assert.doesNotMatch(literal, /\.\.\./, "no spread can smuggle caller keys into the door input");
  const entries = literal.split(/,(?![^()]*\))/).map(part => part.trim()).filter(Boolean);
  const keys = entries.map(entry => entry.split(":")[0].trim());
  assert.deepEqual(keys, ["verb", "write", "actor", "args", "now"]);
  assert.deepEqual(entries, [
    "verb: name",
    "write: tool.write === true",
    "actor",
    "args",
    "now: new Date().toISOString()",
  ]);
});
