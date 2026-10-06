// call-verb passthrough tests.
//
// WHY THIS FILE EXISTS. call-verb is the deploy-gap passthrough: it grants a
// session reach to a verb the connector has not cached yet. It shipped with no
// test at all, and on 2026-08-13 it was found to be delivering EMPTY ARGUMENTS
// to every verb reached through it. The old dispatch line was
//
//     callTool(env, actor, inner, (args && args.args) || {}, profile)
//
// and when a client serialized the nested object as a JSON string, the string
// was truthy, so it sailed through `||` and reached the inner verb intact-as-a-
// string. The inner verb then read each field off a string, got undefined, and
// answered with its own "missing_<field>" error — so a passthrough bug wore the
// mask of a caller mistake. A session trying to file a defect spent four
// attempts rearranging idempotency_key before probing with a known-good id and
// realising the payload never arrived at all. The defect log was unreachable
// the whole time, which is the one record the system keeps about its own
// failures.
//
// CHOOSING A PROBE THAT ACTUALLY PROVES FORWARDING. The first version of this
// file used read-loop and asserted "the inner verb did not say its argument was
// missing". That was worthless: read-loop opens a database connection BEFORE it
// validates, so with no database every call fails identically and the assertion
// passes whether or not the payload arrived. The probe used here instead is the
// payload-aware profile guard in callTool, which reads args.ownership for
// add-premises under the away profile purely in memory, before any client is
// created. It therefore reports on the forwarded object itself. Under the old
// code a stringified payload has no .ownership, slips past that guard, and ends
// at a database error; under the fix it is caught. The test fails on the bug
// and passes on the fix, which is the only property that makes it worth having.

import test from "node:test";
import assert from "node:assert/strict";
import { callTool, foundationAssuranceRuntimeBinding } from "../src/mcp.js";

const ACTOR = { slug: "joe", human: true, via: "oauth-google" };

// The in-memory guard fires only for this exact combination.
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
  // This is the exact shape that was silently broken on 2026-08-13.
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
  // With nothing to inspect the guard cannot fire, so this proceeds past the
  // passthrough entirely. What matters is that the passthrough did not invent a
  // complaint of its own about a payload the caller simply did not send.
  assert.notEqual(out?.error, "unparseable_args");
  assert.notEqual(out?.error, "args_not_an_object");
});

test("the verb name is still required, and recursion is still refused", async () => {
  assert.equal((await callVerb({ args: PROBE_PAYLOAD }))?.error, "missing_verb");
  assert.equal((await callVerb({ verb: "call-verb" }))?.error, "no_recursion");
  assert.equal((await callVerb({ verb: "list-verbs" }))?.error, "no_recursion");
});

test("foundation assurance runtime binding comes only from deploy/provider metadata", () => {
  assert.deepEqual(foundationAssuranceRuntimeBinding({
    CARR_ENV: "production",
    GIT_SHA: "a".repeat(40),
    CF_VERSION_METADATA: { id: "00000000-0000-4000-8000-000000000095" },
    DATABASE_URL_FOUNDATION_ASSURANCE_WRITER: "postgresql://must-not-leak",
  }), {
    schema_version: "doctorcre-v5-foundation-assurance-runtime-binding.v1",
    environment: "production",
    source_sha: "a".repeat(40),
    provider: "cloudflare-workers",
    provider_version: "00000000-0000-4000-8000-000000000095",
  });
  assert.equal(foundationAssuranceRuntimeBinding({}).provider_version, null);
});
