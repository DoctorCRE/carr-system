// V5-S01 — the door is wired at the dispatch seam, in shadow.
//
// executeRegisteredTool is the one function every door (connector read,
// connector write, local break-glass) passes. These tests check the dispatch
// order, the enforce refusal, and the arguments supplied to the door.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { runInNewContext } from "node:vm";
import { V5BoundaryDoorRefusal } from "../src/global-boundaries-door.v5.js";
import { ToolError } from "../src/tool-error.ts";

function dispatchSource() {
  const source = readFileSync(new URL("../src/tools.js", import.meta.url), "utf8");
  const start = source.indexOf("export async function executeRegisteredTool(");
  return source.slice(start, source.indexOf("\nconst TOOL_REGISTRATION_SOURCE", start));
}

test("dispatch coerces arguments, runs the seam and calendar guard, then invokes the handler", async () => {
  const calls = [];
  const record = name => () => { calls.push(name); };
  const handler = record("handler");
  const refusal = new V5BoundaryDoorRefusal({ verb: "probe", refusals: [] });
  let enforce = false;
  const dispatch = runInNewContext(`(${dispatchSource().replace(/^export /, "").trim()})`, {
    TOOLS: { probe: { handler }, "morning-brief": { handler }, "resolve-candidate": { handler, write: true } },
    assertNoCallerAuthorityFields() {},
    assertRegisteredToolInput() {},
    coerceArgsToSchema: record("coercion"),
    assertRequiredArgs: record("required arguments"),
    FEATURE_VERBS: {},
    passBoundaryDoor() {
      calls.push("seam");
      if (enforce) throw refusal;
    },
    guardCalendarReviewWrite: record("calendar guard"),
    linkFutureWork() {},
    writeEvent() {},
    V5BoundaryDoorRefusal,
    ToolError,
  });

  for (const verb of ["probe", "morning-brief", "resolve-candidate"]) {
    calls.length = 0;
    await dispatch({}, {}, verb, {});
    assert.deepEqual(calls, ["coercion", "required arguments", "seam", "calendar guard", "handler"], verb);
  }

  enforce = true;
  calls.length = 0;
  await assert.rejects(dispatch({}, {}, "probe", {}), error => {
    assert.ok(error instanceof ToolError, "the dispatcher maps an enforce refusal to ToolError");
    assert.equal(error.payload, refusal.payload);
    return true;
  });
  assert.deepEqual(calls, ["coercion", "required arguments", "seam"]);
});

// #1268 review, robustness: the seam hands the door exactly five inputs, and
// none of mode, context or log. Any extra key (`mode: args.door_mode`,
// `context: args.context`, `log: ...`) or a changed value fails here, not just
// the spellings the previous test happens to name.
test("the seam passes the door only verb, write, actor, args and now, each from the server", () => {
  const body = dispatchSource();
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
    "now: new Date" + "().toISOString()",
  ]);
});
