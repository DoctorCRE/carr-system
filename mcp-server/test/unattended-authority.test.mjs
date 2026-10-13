// THE UNATTENDED AUTHORITY DOOR (Joe's ruling 2026-10-12, decision
// cbe91287-10a2-48d0-bda7-3b1784b4bb93: remove his approval from everything the
// system can do itself).
//
// What this pins. A server-held UNATTENDED_TOKENS bearer resolves to the
// existing joe-local machine actor, sponsored by Joe, marked unattended, and
// that actor holds partner authority for exactly the acts tools.js's
// UNATTENDED_AUTHORITY_VERBS lists, under a server-locked `unattended` profile.
// Every other humanOnly/authorityOnly verb refuses it, and the verbs flagged
// humanCallerOnly (today: confirm-merge) refuse every machine however it is
// sponsored. Each runner holds its own token, and the runner's name reaches the
// ledger as client_id.
//
// The sponsor is derived server-side from the map key. Only `joe` is accepted:
// Dell has not ruled on unattended authority for his own book, so a `dell` key
// resolves to nothing rather than to a second sponsor.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { TOOLS, ToolError, UNATTENDED_AUTHORITY_VERBS, executeRegisteredTool } from "../src/tools.js";
import {
  agentActorForToken, authorizationClassForActor, personalScopeForActor,
  unattendedActorForToken,
} from "../src/identity.js";
import { partnerAuthoritySlugForActor } from "../src/partner-authority.js";
import { PROFILES, allowedIn, authorityDsnForActor, profileForActor, readCallInsertSQL } from "../src/mcp.js";
import { a05SeatForActor } from "../src/delivery-cadence-a05-tools.js";
import { LOCAL_CLIENT_PROFILE_NAMES, selectLocalClientCredential } from "../local-client-auth.mjs";
import { mcpInventory } from "../../ops/scac-mutation-inventory.mjs";

const SECRET = "unattended-secret-fixture";
const RUNNER = "nightly-mac";
const runnerMap = (sponsor, token = SECRET, runner = RUNNER) =>
  JSON.stringify({ [sponsor]: { [runner]: token } });
const MAP = runnerMap("joe");
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
  assert.equal(actor.client_id, "unattended-runner:nightly-mac");
  assert.equal(actor.unattended_runner, "nightly-mac");
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
  assert.equal(unattendedActorForToken(BEARER, runnerMap("dell")), null);
  // and the Joe key in the same map is still honoured for its own bearer
  const both = JSON.stringify({ joe: { [RUNNER]: "joe-only" }, dell: { [RUNNER]: SECRET } });
  assert.equal(unattendedActorForToken(BEARER, both), null);
  assert.equal(unattendedActorForToken("Bearer joe-only", both)?.sponsoring_human_slug, "joe");
});

test("unknown, probe, review, machine and agent keys resolve to nothing", () => {
  for (const key of ["joe-local", "dell-local", "smoke-probe", "codex-reviewer",
    "hermes-pilot", "codex", "claude", "grok", "Joe", "JOE", " joe", "joe ", "", "__proto__"])
    assert.equal(unattendedActorForToken(BEARER, runnerMap(key)), null, key);
});

test("a token held by two keys or two runners is ambiguous and resolves to nothing", () => {
  assert.equal(unattendedActorForToken(BEARER,
    JSON.stringify({ joe: { [RUNNER]: SECRET }, dell: { [RUNNER]: SECRET } })), null);
  assert.equal(unattendedActorForToken(BEARER,
    JSON.stringify({ joe: { [RUNNER]: SECRET }, "codex-reviewer": { [RUNNER]: SECRET } })), null);
  assert.equal(unattendedActorForToken(BEARER,
    JSON.stringify({ joe: { [RUNNER]: SECRET, "model-room": SECRET } })), null);
});

test("each runner holds its own token, and the ledger names the runner that acted", () => {
  const map = JSON.stringify({ joe: { [RUNNER]: SECRET, "model-room": "model-room-secret" } });
  assert.equal(unattendedActorForToken(BEARER, map)?.client_id, "unattended-runner:nightly-mac");
  const desk = unattendedActorForToken("Bearer model-room-secret", map);
  assert.equal(desk?.client_id, "unattended-runner:model-room");
  // Revoking one runner is deleting its entry; the other keeps working.
  const revoked = JSON.stringify({ joe: { "model-room": "model-room-secret" } });
  assert.equal(unattendedActorForToken(BEARER, revoked), null);
  assert.equal(unattendedActorForToken("Bearer model-room-secret", revoked)?.slug, "joe-local");
  // The runner label rides into the read and write ledgers beside via.
  const { text, params } = readCallInsertSQL(unattended(), "standing-context", true, null);
  assert.match(text, /\bvia, client_id\b/);
  const via = params.indexOf("unattended-token");
  assert.ok(via > 0, "via missing from the ledger row");
  assert.equal(params[via + 1], "unattended-runner:nightly-mac");
});

test("a runner name outside the server's vocabulary resolves to nothing", () => {
  for (const runner of ["", "Nightly", "-x", ".x", "a b", "a/b", "x".repeat(49), "__proto__"])
    assert.equal(unattendedActorForToken(BEARER, runnerMap("joe", SECRET, runner)), null, runner);
  assert.equal(unattendedActorForToken(BEARER, runnerMap("joe", SECRET, "x".repeat(48)))?.slug,
    "joe-local");
});

test("a malformed map resolves to nothing", () => {
  for (const raw of [undefined, null, "", "not json", "{", "[]", `["${SECRET}"]`, "null",
    "42", `"${SECRET}"`, "true", "{}", JSON.stringify({ joe: 42 }),
    // the flat sponsor -> token shape is not accepted: every token names its runner
    JSON.stringify({ joe: SECRET }),
    JSON.stringify({ joe: [SECRET] }), JSON.stringify({ joe: { [RUNNER]: [SECRET] } }),
    JSON.stringify({ joe: { [RUNNER]: 42 } }), JSON.stringify({ joe: { [RUNNER]: "" } }),
    JSON.stringify({ joe: { [RUNNER]: null } }), JSON.stringify({ joe: [[RUNNER, SECRET]] }),
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
  const dellMap = runnerMap("dell");
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

test("the live SCAC inventory reads the human-caller principal mode off the flag", () => {
  const rows = new Map(mcpInventory(TOOLS).map(row => [row.operation, row]));
  assert.equal(rows.get("confirm-merge").principal_mode, "server_verified_human");
  assert.equal(rows.get("close-incident").principal_mode, "server_verified_human_or_native_agent");
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

// ---------- partner authority: exactly the allowlist ----------

const gateError = (actor, verb) =>
  executeRegisteredTool(forbiddenClient, actor, verb, {}).then(() => null, e => e);
const authorityVerbs = () => Object.keys(TOOLS).filter(name =>
  TOOLS[name].humanOnly === true || TOOLS[name].authorityOnly === true);

test("the allowlist names only real humanOnly/authorityOnly verbs, none human-caller-only", () => {
  assert.deepEqual([...UNATTENDED_AUTHORITY_VERBS].sort(), [
    "accept-ready-plan", "accept-ready-plan-amendment", "adjudicate-incident",
    "approve-lead-draft", "approve-lead-move", "close-incident", "review-and-triage",
    "undo-lead-move"]);
  assert.ok(Object.isFrozen(UNATTENDED_AUTHORITY_VERBS));
  for (const verb of UNATTENDED_AUTHORITY_VERBS) {
    assert.ok(TOOLS[verb], verb);
    assert.ok(TOOLS[verb].humanOnly === true || TOOLS[verb].authorityOnly === true, verb);
    assert.notEqual(TOOLS[verb].humanCallerOnly, true, verb);
  }
});

test("allowlisted authority verbs pass every partner-authority gate for the unattended actor", async () => {
  for (const verb of UNATTENDED_AUTHORITY_VERBS) {
    const error = await gateError(unattended(), verb);
    assert.notEqual(error?.payload?.error, "human_only_verb_requires_verified_partner", verb);
    assert.notEqual(error?.payload?.error, "unattended_runner_authority_not_admitted", verb);
  }
});

test("rule, schedule and deal-fact verbs refuse the unattended actor before database access", async () => {
  for (const verb of ["retire-rule", "amend-rule", "approve-rule", "bind-rule-context-contract",
    "disable-legacy-schedule", "record-executed-lease"]) {
    const error = await gateError(unattended(), verb);
    assert.ok(error instanceof ToolError, `${verb}: expected a ToolError, got ${error}`);
    assert.equal(error.payload.error, "unattended_runner_authority_not_admitted", verb);
    assert.equal(error.payload.verb, verb);
  }
});

test("every humanOnly/authorityOnly verb off the allowlist refuses the unattended actor", async () => {
  const off = authorityVerbs().filter(name =>
    !UNATTENDED_AUTHORITY_VERBS.includes(name) && TOOLS[name].humanCallerOnly !== true);
  assert.ok(off.length > 50, `only ${off.length} verbs off the allowlist`);
  for (const verb of off)
    assert.equal((await gateError(unattended(), verb))?.payload?.error,
      "unattended_runner_authority_not_admitted", verb);
});

test("the allowlist does not narrow the attended local door", async () => {
  const local = agentActorForToken(BEARER, JSON.stringify({ "joe-local": SECRET }), "local-token");
  for (const verb of ["retire-rule", "close-incident", "record-executed-lease"])
    assert.notEqual((await gateError(local, verb))?.payload?.error,
      "unattended_runner_authority_not_admitted", verb);
});

test("authorityOnly verbs route the unattended actor to Joe's authority login", () => {
  const env = { CARR_DB_AUTHORITY_JOE_URL: "postgres://authority-joe-fixture",
                CARR_DB_AUTHORITY_DELL_URL: "postgres://authority-dell-fixture" };
  assert.equal(authorityDsnForActor(env, unattended()), "postgres://authority-joe-fixture");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "postgres://authority-fixture" },
    unattended()), "postgres://authority-fixture");
});

// ---------- the server-locked unattended profile (rule b42e217e) ----------

test("the unattended actor is locked to the unattended profile whatever ?profile= asks", () => {
  for (const url of ["https://api.example.test/mcp", "https://api.example.test/mcp?profile=full",
    "https://api.example.test/mcp?profile=capture", "https://api.example.test/mcp?profile=away",
    "https://api.example.test/mcp?profile=nope"])
    assert.equal(profileForActor(unattended(), new Request(url)), "unattended", url);
  // the attended local door keeps its operational default
  const local = agentActorForToken(BEARER, JSON.stringify({ "joe-local": SECRET }), "local-token");
  assert.equal(profileForActor(local, new Request("https://api.example.test/mcp")), "full");
});

test("the unattended profile holds capture, close-loop, the cadence doors and the allowlist only", () => {
  const profile = PROFILES.unattended;
  assert.ok(profile instanceof Set);
  for (const verb of profile) {
    assert.ok(TOOLS[verb], `${verb} is not a registered verb`);
    assert.equal(TOOLS[verb].write, true, verb);
    assert.equal(allowedIn("unattended", verb, TOOLS[verb]), true, verb);
  }
  for (const verb of UNATTENDED_AUTHORITY_VERBS) assert.ok(profile.has(verb), verb);
  for (const verb of ["close-loop", "record-cadence-receipt", "raise-delivery-cadence-alert"])
    assert.ok(profile.has(verb), verb);
  for (const verb of profile)
    if (TOOLS[verb].humanOnly === true || TOOLS[verb].authorityOnly === true)
      assert.ok(UNATTENDED_AUTHORITY_VERBS.includes(verb), `${verb} is authority but off the allowlist`);
  for (const verb of ["confirm-merge", "retire-rule", "amend-rule", "approve-rule", "teach",
    "disable-legacy-schedule", "record-executed-lease", "reassign-deal", "update-deal", "add-party"])
    assert.equal(allowedIn("unattended", verb, TOOLS[verb]), false, verb);
});

// ---------- the V5-A05 seat ----------

test("the unattended runner holds the V5-A05 system seat, as the attended local door does", () => {
  assert.equal(a05SeatForActor(unattended()), "system");
  const local = agentActorForToken(BEARER, JSON.stringify({ "joe-local": SECRET }), "local-token");
  assert.equal(a05SeatForActor(local), "system");
  assert.equal(a05SeatForActor({ ...unattended(), native_agent_verified: false }), "other");
  assert.equal(a05SeatForActor({ ...unattended(), via: "unattended-tokens" }), "other");
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
