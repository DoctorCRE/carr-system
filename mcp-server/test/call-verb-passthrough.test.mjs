
import test from "node:test";
import assert from "node:assert/strict";
import { callTool } from "../src/mcp.js";

const ACTOR = { slug: "joe", human: true, via: "oauth-google" };

const PROBE_VERB = "add-premises";
const PROBE_PAYLOAD = { ownership: [{ new_party: { name: "Probe Party" } }] };
const GUARD_HIT = "add-premises (new_party)";

async function callVerb(args, profile = "away") {
  try {
    await callTool({}, ACTOR, "call-verb", args, profile);
    return null;
  } catch (e) {
    return e && e.payload ? e.payload : { error: e && e.message };
  }
}

test("a nested args OBJECT reaches the inner verb's argument inspection", async () => {
  const out = await callVerb({ verb: PROBE_VERB, args: PROBE_PAYLOAD });
  assert.equal(out?.error, "not_in_profile");
  assert.equal(out?.verb, GUARD_HIT,
    "the guard did not see ownership[], so the payload never reached the inner verb");
});

test("a nested args JSON STRING is parsed and forwarded identically", async () => {
  const out = await callVerb({ verb: PROBE_VERB, args: JSON.stringify(PROBE_PAYLOAD) });
  assert.equal(out?.error, "not_in_profile");
  assert.equal(out?.verb, GUARD_HIT,
    "a stringified args payload was not parsed — the 2026-08-13 regression is back");
});

test("object form and string form are indistinguishable downstream", async () => {
  const asObject = await callVerb({ verb: PROBE_VERB, args: PROBE_PAYLOAD });
  const asString = await callVerb({ verb: PROBE_VERB, args: JSON.stringify(PROBE_PAYLOAD) });
  assert.deepEqual(asString, asObject);
});

test("a string that is not JSON fails as ITSELF, not as the inner verb's problem", async () => {
  const out = await callVerb({ verb: PROBE_VERB, args: "ownership=new_party" });
  assert.equal(out?.error, "unparseable_args");
});

test("a non-object, non-string args is refused rather than forwarded", async () => {
  const out = await callVerb({ verb: PROBE_VERB, args: ["ownership"] });
  assert.equal(out?.error, "args_not_an_object");
});

test("omitted args is an empty object, and is never mistaken for a bad payload", async () => {
  const out = await callVerb({ verb: PROBE_VERB });
  assert.notEqual(out?.error, "unparseable_args");
  assert.notEqual(out?.error, "args_not_an_object");
});

test("the verb name is still required, and recursion is still refused", async () => {
  assert.equal((await callVerb({ args: PROBE_PAYLOAD }))?.error, "missing_verb");
  assert.equal((await callVerb({ verb: "call-verb" }))?.error, "no_recursion");
  assert.equal((await callVerb({ verb: "list-verbs" }))?.error, "no_recursion");
});
