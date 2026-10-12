// THE UNATTENDED AUTHORITY DOOR (Joe's ruling 2026-10-12, decision
// cbe91287-10a2-48d0-bda7-3b1784b4bb93: remove his approval from everything the
// system can do itself).
//
// What this pins. A server-held UNATTENDED_TOKENS bearer resolves to the
// existing joe-local machine actor, sponsored by Joe, marked unattended, and
// that actor clears every partner-authority gate a sponsored attended session
// clears — except the verbs flagged humanCallerOnly (today: confirm-merge),
// which refuse every machine however it is sponsored.
//
// The sponsor is derived server-side from the map key. Only `joe` is accepted:
// Dell has not ruled on unattended authority for his own book, so a `dell` key
// resolves to nothing rather than to a second sponsor.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import {
  agentActorForToken, authorizationClassForActor, personalScopeForActor,
  unattendedActorForToken,
} from "../src/identity.js";
import { partnerAuthoritySlugForActor } from "../src/partner-authority.js";
import { authorityDsnForActor, profileForActor } from "../src/mcp.js";
import { LOCAL_CLIENT_PROFILE_NAMES, selectLocalClientCredential } from "../local-client-auth.mjs";

const SECRET = "unattended-secret-fixture";
const MAP = JSON.stringify({ joe: SECRET });
const BEARER = `Bearer ${SECRET}`;

const forbiddenClient = {
  query: async (text) => {
    throw new Error(`the gate let a call reach the database: ${String(text).slice(0, 80)}`);
  },
};

const unattended = () => unattendedActorForToken(BEARER, MAP);

// ---------- the accepted profile ----------

test("a Joe-keyed unattended bearer resolves to the sponsored joe-local machine actor", () => {
  const actor = unattended();
  assert.notEqual(actor, null);
  assert.equal(actor.slug, "joe-local");
  assert.equal(actor.human, false);
  assert.equal(actor.via, "unattended-token");
  assert.equal(actor.unattended, true);
  assert.equal(actor.sponsoring_human_slug, "joe");
  assert.equal(actor.human_slug, "joe");
  assert.equal(actor.native_agent_verified, true);
  assert.equal(actor.client_id, null);
  assert.equal(authorizationClassForActor(actor), "sponsored_agent");
  assert.equal(partnerAuthoritySlugForActor(actor), "joe");
  assert.deepEqual(personalScopeForActor(actor),
    { status: "personal", sponsor: "joe", source: "verified_grant_sponsor" });
});

test("the bare token (no Bearer prefix) is accepted the way every other door accepts it", () => {
  assert.equal(unattendedActorForToken(SECRET, MAP)?.slug, "joe-local");
});

test("each call returns a fresh actor object, never a shared one", () => {
  assert.notEqual(unattended(), unattended());
});

// ---------- every refusal ----------

test("a dell-keyed unattended bearer resolves to nothing", () => {
  assert.equal(unattendedActorForToken(BEARER, JSON.stringify({ dell: SECRET })), null);
  // and the Joe key in the same map is still honoured for its own bearer
  const both = JSON.stringify({ joe: "joe-only", dell: SECRET });
  assert.equal(unattendedActorForToken(BEARER, both), null);
  assert.equal(unattendedActorForToken("Bearer joe-only", both)?.sponsoring_human_slug, "joe");
});

test("unknown, probe, review, machine and agent keys resolve to nothing", () => {
  for (const key of ["joe-local", "dell-local", "smoke-probe", "codex-reviewer",
    "hermes-pilot", "codex", "claude", "grok", "Joe", "JOE", " joe", "joe ", "", "__proto__"])
    assert.equal(unattendedActorForToken(BEARER, JSON.stringify({ [key]: SECRET })), null, key);
});

test("a token held by two keys is ambiguous and resolves to nothing", () => {
  assert.equal(unattendedActorForToken(BEARER, JSON.stringify({ joe: SECRET, dell: SECRET })), null);
  assert.equal(unattendedActorForToken(BEARER,
    JSON.stringify({ joe: SECRET, "codex-reviewer": SECRET })), null);
});

test("a malformed map resolves to nothing", () => {
  for (const raw of [undefined, null, "", "not json", "{", "[]", `["${SECRET}"]`, "null",
    "42", `"${SECRET}"`, "true", "{}", JSON.stringify({ joe: 42 }),
    JSON.stringify({ joe: [SECRET] }), JSON.stringify({ joe: { token: SECRET } }),
    JSON.stringify({ joe: "" }), JSON.stringify({ joe: null })])
    assert.equal(unattendedActorForToken(BEARER, raw), null, String(raw));
});

test("a wrong, empty or near-miss bearer resolves to nothing", () => {
  for (const header of ["Bearer wrong", "", null, undefined, "Bearer ", `Bearer ${SECRET}x`,
    `Bearer ${SECRET.toUpperCase()}`, `Bearer x${SECRET}`, `Basic ${SECRET}`])
    assert.equal(unattendedActorForToken(header, MAP), null, String(header));
});

test("a caller-claimed sponsor cannot reach the door", () => {
  // Two parameters: the request's own Authorization header and the server's map.
  assert.equal(unattendedActorForToken.length, 2);
  // An extra argument naming a sponsor is ignored: Dell's bearer stays unmapped,
  // and Joe's bearer stays Joe's whatever the extra argument says.
  const dellMap = JSON.stringify({ dell: SECRET });
  assert.equal(unattendedActorForToken(BEARER, dellMap, { sponsor: "joe" }), null);
  assert.equal(unattendedActorForToken(BEARER, MAP, { sponsor: "dell", sponsoring_human_slug: "dell" })
    ?.sponsoring_human_slug, "joe");
  // A sponsor smuggled into the header text is just a wrong bearer.
  assert.equal(unattendedActorForToken(`${BEARER}; sponsor=joe`, MAP), null);
});

test("the attended local-token door does not grow an unattended marker", () => {
  const local = agentActorForToken(BEARER, JSON.stringify({ "joe-local": SECRET }), "local-token");
  assert.equal(local.via, "local-token");
  assert.equal(local.unattended, undefined);
});

// ---------- the server holds the secret, and the Worker checks the door ----------

test("UNATTENDED_TOKENS is a server credential name, so the actor brands", () => {
  const src = readFileSync(new URL("../src/identity.js", import.meta.url), "utf8");
  const names = src.match(/const SERVER_CREDENTIAL_NAMES = Object\.freeze\(\[([\s\S]*?)\]\)/);
  assert.ok(names, "SERVER_CREDENTIAL_NAMES not found");
  assert.match(names[1], /"UNATTENDED_TOKENS"/);
});

test("the Worker checks the unattended door before the local-token door", () => {
  const src = readFileSync(new URL("../src/index.js", import.meta.url), "utf8");
  assert.match(src, /import \{[^}]*\bunattendedActorForToken\b[^}]*\} from "\.\/identity\.js"/);
  assert.match(src, /unattendedActorForToken\(\s*request\.headers\.get\("authorization"\),\s*env\.UNATTENDED_TOKENS\)/);
  const unattendedAt = src.indexOf("const unattendedActor = unattendedActorFor(request, env);");
  const localAt = src.indexOf("const localActor = localActorFor(request, env);");
  assert.ok(unattendedAt > 0, "the unattended door is not wired into routeRequest");
  assert.ok(localAt > unattendedAt, "the unattended door must be checked before the local-token door");
  assert.match(src.slice(unattendedAt, localAt),
    /if \(unattendedActor\) return dispatch\(request, env, ctx, unattendedActor\);/);
});

// ---------- the humanCallerOnly flag ----------

test("confirm-merge is the one verb carrying humanCallerOnly", () => {
  assert.equal(TOOLS["confirm-merge"].humanCallerOnly, true);
  assert.equal(TOOLS["confirm-merge"].humanOnly, true);
  const flagged = Object.keys(TOOLS).filter(name => TOOLS[name].humanCallerOnly === true);
  assert.deepEqual(flagged, ["confirm-merge"]);
});

test("confirm-merge refuses the unattended actor before database access", async () => {
  const error = await executeRegisteredTool(forbiddenClient, unattended(), "confirm-merge", {
    idempotency_key: "4f0e7c1a-2b3d-4e5f-8a9b-0c1d2e3f4a5b",
    survivor_party: "20000000-0000-4000-8000-000000000001",
    merged_party: "20000000-0000-4000-8000-000000000002",
    match_basis: "matching synthetic phone and address",
  }).then(() => null, e => e);
  assert.ok(error instanceof ToolError, `expected a ToolError, got ${error}`);
  assert.equal(error.payload.error, "human_only_verb_requires_verified_partner");
  assert.equal(error.payload.verb, "confirm-merge");
  assert.equal(error.payload.actor_class, "sponsored_agent");
});

// ---------- humanOnly / authorityOnly verbs accept the unattended actor ----------

test("close-incident passes the partner-authority gate for the unattended actor", async () => {
  const error = await executeRegisteredTool(forbiddenClient, unattended(), "close-incident", {})
    .then(() => null, e => e);
  assert.notEqual(error?.payload?.error, "human_only_verb_requires_verified_partner");
});

test("every humanOnly verb without humanCallerOnly passes the gate for the unattended actor", async () => {
  const verbs = Object.keys(TOOLS).filter(name =>
    TOOLS[name].humanOnly === true && TOOLS[name].humanCallerOnly !== true);
  assert.ok(verbs.includes("close-incident") && verbs.includes("review-and-triage"));
  for (const verb of verbs) {
    const error = await executeRegisteredTool(forbiddenClient, unattended(), verb, {})
      .then(() => null, e => e);
    assert.notEqual(error?.payload?.error, "human_only_verb_requires_verified_partner", verb);
  }
});

test("authorityOnly verbs route the unattended actor to Joe's authority login", () => {
  const env = { CARR_DB_AUTHORITY_JOE_URL: "postgres://authority-joe-fixture",
                CARR_DB_AUTHORITY_DELL_URL: "postgres://authority-dell-fixture" };
  assert.equal(authorityDsnForActor(env, unattended()), "postgres://authority-joe-fixture");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "postgres://authority-fixture" },
    unattended()), "postgres://authority-fixture");
});

// ---------- the operational profile matches the attended local door ----------

test("the unattended actor runs under the same operational profile as joe-local via local-token", () => {
  const local = agentActorForToken(BEARER, JSON.stringify({ "joe-local": SECRET }), "local-token");
  for (const url of ["https://api.example.test/mcp", "https://api.example.test/mcp?profile=capture",
    "https://api.example.test/mcp?profile=away", "https://api.example.test/mcp?profile=nope"]) {
    const request = new Request(url);
    assert.equal(profileForActor(unattended(), request), profileForActor(local, request), url);
  }
  assert.equal(profileForActor(unattended(), new Request("https://api.example.test/mcp")), "full");
});

// ---------- the unattended client profile ----------

test("the unattended client profile reads only CARR_MCP_UNATTENDED_TOKEN", () => {
  assert.ok(LOCAL_CLIENT_PROFILE_NAMES.includes("unattended"));
  const fromFile = selectLocalClientCredential({ CARR_MCP_CLIENT_PROFILE: "unattended" },
    "CARR_MCP_LOCAL_TOKEN=attended-secret\nCARR_MCP_UNATTENDED_TOKEN=unattended-secret\n");
  assert.equal(fromFile.token, "unattended-secret");
  assert.equal(fromFile.tokenVariable, "CARR_MCP_UNATTENDED_TOKEN");
  assert.equal(fromFile.workerSecret, "UNATTENDED_TOKENS");
  assert.doesNotMatch(fromFile.identityNotice, /secret/);
  assert.equal(selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "unattended", CARR_MCP_UNATTENDED_TOKEN: "from-env" }, "").token,
    "from-env");
});

test("the unattended client profile never falls back to the attended local token", () => {
  assert.equal(selectLocalClientCredential({ CARR_MCP_CLIENT_PROFILE: "unattended" },
    "CARR_MCP_LOCAL_TOKEN=attended-secret\n").token, "");
  assert.equal(selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "unattended", CARR_MCP_LOCAL_TOKEN: "attended-secret" }, "").token, "");
  // and the attended profile never borrows the unattended token
  assert.equal(selectLocalClientCredential({}, "CARR_MCP_UNATTENDED_TOKEN=unattended-secret\n").token, "");
});
