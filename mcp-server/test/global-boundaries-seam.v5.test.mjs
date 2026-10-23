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
